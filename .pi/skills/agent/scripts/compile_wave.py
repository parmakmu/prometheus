#!/usr/bin/env python3
"""Compile the next wave of an agent's active run into the exact
pi-subagents call that runs it.

Usage:
  compile_wave.py [--agent DIR] [--approve key,key] [--out FILE] [--check]

The wave runs in the active run's folder (runs/<id>/, see validate_plan.py):
children work in runs/<id>/work/, relative param values resolve against
runs/<id>/, and the result is also saved to runs/<id>/.compiled/<wave>.json.
Gate commands name their scripts as ../gates/<script>; the compiler points
that at the agent's gates/ folder.

It refuses a wave already launched but not recorded (see unrecorded_launch).
Before compiling, it commits pending changes to the agent's plan
(agent_git.py) and returns the commit, so the launch is reproducible; that is
the only change it makes to the agent. Git is required.

Waves are not written in the plan: validate_plan.derive_waves groups the stages
that are not done, each as early as its needs allow, and starts a new wave
wherever the parent must act (a decision or approval, a side effect's report,
a user review, a replan).

--agent  agent root (default: current directory)
--approve   side-effect stage keys the user approved for this launch
--out       also write the result JSON to FILE
--check     planning check: compile every concrete pending wave (no run needed)

Prints one JSON object. When the wave can launch:

  {"ok": true, "wave": "w1", "stages": [...], "ends_with": "review",
   "call": {"async": true, "mission"|"missionId": …, "workflow": "…/.compiled/w1.js"},
   "children": [...], "notes": [...]}

The generated script is the wave as data (`const W = {...};`) followed by
wave_runner.js unchanged, so the compiler writes no code and every wave runs the
same runner. It is saved to runs/<id>/.compiled/<wave>.js (read it there to
review it; `--check` and a directly-run Compiler still return it inline as
`workflowScript`, since neither launches). Pass `call` to the subagent tool
exactly as given: passing a path, not the script text, is the point. When something must happen
first, "ok" is false and "preflight" lists it: decisions to collect (with the
file to write each answer to), approvals to get, params to set (missing_params),
environment variables the user must set for secrets (missing_secrets), or plan errors.

The compiler never launches anything.
"""
import hashlib
import json
import re
import shlex
import shutil
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Annotated

import agent_git
import agent_params
import typer
from ruamel.yaml.error import YAMLError
from runs import RUNS, active_run
from utils import emit, load_yaml, read_json, refuse, report_state, subagent_records
from validate_plan import check_ready, project_subagents, validate

HERE = Path(__file__).resolve().parent
PARAM_RE = re.compile(r"\$\{([^}]*)\}")
GATES_RE = re.compile(r"(?<![\w./-])\.\./gates/")
BATCHER = "batcher"
SINGLE = {"script", "worker", "scout", "reviewer"}
RUNNER = HERE / "wave_runner.js"  # appended to every compiled wave


def unrecorded_launch(run_dir, state, wave_id):
    """(pi-subagents run id, its state) of a launch of the wave's compiled script that run.yaml does
    not record, or None. The wave is launched between compile and agent_log_start (which needs
    the launch's id): a session that ends in between leaves a launch nobody recorded, which a
    recompile would launch again. pi-subagents keeps the sha256 of each workflow run's script
    (status.json `workflow.scriptDigest`); the compiled file holds the script that was passed."""
    script = Path(run_dir) / ".compiled" / f"{wave_id}.js"
    if not script.is_file():
        return None
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    recorded = {r.get("subagent_run_id") for r in state.get("launches") or []}
    # a launch of the script is newer than it: older records are skipped unread
    for rid, st in subagent_records(newer_than=script.stat().st_mtime):
        if rid not in recorded and (st.get("workflow") or {}).get("scriptDigest") == digest:
            return st.get("runId") or rid, st.get("state")
    return None


def canonical(value):
    """A stable text form of JSON-like data, to compare items across launches."""
    return json.dumps(value, sort_keys=True, default=str)


def expand_schema(s):
    """Plan shorthand ({field: type}, [type]) to JSON Schema."""
    if isinstance(s, dict):
        if "type" in s and isinstance(s.get("type"), str) and s["type"] in ("object", "array") and ("properties" in s or "items" in s):
            return s  # already JSON Schema
        return {"type": "object", "required": list(s), "properties": {k: expand_schema(v) for k, v in s.items()}}
    if isinstance(s, list):
        return {"type": "array", "items": expand_schema(s[0]) if s else {}}
    if s in ("string", "number", "integer", "boolean", "object", "array"):
        return {"type": s}
    return {"type": "string", "description": str(s)}


class Compiler:
    def __init__(self, agent_dir, plan, params_values, run_dir, run_state, approvals):
        """`params_values`: {name: value} from agent_params.load."""
        self.agent_dir = Path(agent_dir)
        self.run_dir = Path(run_dir)
        self.run_id = self.run_dir.name
        self.work = self.run_dir / "work"
        self.plan = plan
        self.values = params_values if isinstance(params_values, dict) else {}
        launches = run_state.get("launches") if isinstance(run_state, dict) else None
        self.launches = launches if isinstance(launches, list) else []
        self.approvals = approvals
        self.params = plan.get("params") or {}
        self.stages = {s["key"]: s for s in plan.get("stages") or [] if isinstance(s, dict) and s.get("key")}
        self.inputs = {i["id"]: i for i in plan.get("inputs") or [] if isinstance(i, dict) and i.get("id")}
        self.producer = {}
        for key, st in self.stages.items():
            for o in st.get("out") or []:
                if isinstance(o, dict) and o.get("id"):
                    self.producer[o["id"]] = (key, o)
        agents = (plan.get("agents") or {}).get("available") or []
        self.external = {a["name"] for a in agents if isinstance(a, dict) and a.get("runner") == "external-cli"}
        self.writers = {"worker"} | self.project_writers()
        self.missing_params = set()
        self.missing_secrets = set()
        self.uses_uv = False  # a program or gate runs with `uv run` (the agent's environment)
        self.errors = []
        self.notes = []
        self.spliced = set()  # keys whose result a later child's task splices in (reuse_finished)

    # --- paths ---------------------------------------------------------------
    def resolve_text(self, text):
        """${name} -> the param's value by its type (agent_params.resolve); a secret
        becomes ${ENV_NAME}, which the child's or gate's shell expands."""
        def sub(m):
            name = m.group(1)
            spec = self.params.get(name) or {}
            if self.values.get(name) is None:
                if agent_params.kind(spec) == "secret":
                    self.missing_secrets.add(agent_params.env_name(name, spec))
                else:
                    self.missing_params.add(name)
                return m.group(0)
            return agent_params.resolve(name, spec, self.values[name], self.run_dir)
        return PARAM_RE.sub(sub, str(text))

    def out_path(self, o):
        """Absolute path for an out entry's path; relative paths live in work/."""
        raw = o.get("path")
        if not raw:
            return None
        path = Path(self.resolve_text(raw)).expanduser()
        return str(path if path.is_absolute() else self.work / path)

    def decision_path(self, key):
        st = self.stages[key]
        for o in st.get("out") or []:
            if isinstance(o, dict) and o.get("path"):
                return self.out_path(o)
        return str(self.work / "decisions" / f"{key}.json")

    # --- task text -------------------------------------------------------------
    def input_line(self, cid, in_wave_results):
        """(text, key) describing one consumed id; key names a stage earlier in this wave whose
        returned result follows the text, else None."""
        if cid in self.inputs:
            inp = self.inputs[cid]
            loc = self.resolve_text(inp.get("location", "")) if inp.get("location") else "(given in this task)"
            fmt = f", {inp['format']}" if inp.get("format") else ""
            note = f" — {inp['note']}" if inp.get("note") else ""
            return f"- {cid} ({inp.get('kind')}{fmt}): {loc}{note}", None
        pkey, o = self.producer[cid]
        pst = self.stages[pkey]
        if pst.get("kind") == "decision" or pst.get("pattern") == "approval":
            return f"- {cid}: the user's decision, in {self.decision_path(pkey)}", None
        path = self.out_path(o)
        if path:
            fmt = f", {o['format']}" if o.get("format") else ""
            return f"- {cid} (from stage {pkey}{fmt}): {path}", None
        if pkey in in_wave_results:
            return f"- {cid} (result of stage {pkey}): ", pkey
        self.errors.append(f"stage consumes '{cid}', a returned result of stage '{pkey}' from an earlier wave; give that output a path")
        return f"- {cid}: (unavailable)", None

    def used_next_by(self, key):
        """Lines naming the stages that consume `key`'s outputs, with what they must achieve.
        The producer and its reviewer both get them, so they judge the output by the same
        standard: a report told only "list the available fields" once left out the one field
        (Cc) a later stage extracts, and its reviewer, reading the plan, blocked it."""
        outs = {o["id"] for o in self.stages[key].get("out") or [] if isinstance(o, dict) and o.get("id")}
        lines = []
        for ckey, cst in self.stages.items():
            used = [c for c in cst.get("consumes") or [] if c in outs]
            if ckey == key or not used:
                continue
            check = (cst.get("verify") or {}).get("check")
            lines.append(f"- {ckey} ({cst.get('title', ckey)}) reads {', '.join(used)}"
                         + (f"; it is done when: {check}" if check else "") + "\n")
        return lines

    def task(self, key, st, in_wave, extra_lines=()):
        """The child's task: text, or parts [{text} | {result: key}] when it includes the result
        of a stage earlier in this wave (wave_runner.js splices it in)."""
        slug = self.agent_dir.name
        parts = []

        def lit(s):
            if parts and "text" in parts[-1]:
                parts[-1]["text"] += s
            else:
                parts.append({"text": s})

        lit(f"Agent \"{slug}\", stage \"{key}\": {st.get('title', key)}.\n")
        for field in ("intent", "notes"):
            if st.get(field):
                lit(f"{st[field]}\n")
        consumes = st.get("consumes") or []
        if consumes:
            lit("\nInputs:\n")
            for cid in consumes:
                text, result = self.input_line(cid, in_wave)
                lit(text)
                if result:
                    parts.append({"result": result})
                    self.spliced.add(result)
                lit("\n")
        writes = [o for o in st.get("out") or [] if isinstance(o, dict) and o.get("path")]
        if writes:
            lit("\nWrite:\n")
            for o in writes:
                fmt = f" ({o['format']})" if o.get("format") else ""
                schema = f"; each record: {json.dumps(o['schema'])}" if o.get("schema") else ""
                note = f" — {o['notes']}" if o.get("notes") else ""
                lit(f"- {o['id']}: {self.out_path(o)}{fmt}{schema}{note}\n")
        used_next = self.used_next_by(key)
        if used_next:
            lit("\nUsed next by (make the outputs complete for what these stages need):\n" + "".join(used_next))
        for line in extra_lines:
            lit(line) if isinstance(line, str) else parts.append(line)
        kind = st.get("kind")
        script = self.script_path(st)
        command = self.program_command(st)
        run_as = f" as `{command}` (from {self.work})" if command else " (its docstring says how)"
        if script and Path(script).is_file():
            lit(f"\nThis stage's program is {script}, reviewed and kept with the agent. Run it{run_as} "
                "to write the outputs; do not rewrite it or process items by hand. Change it only if it "
                "fails or its output would not meet \"Done when\"; keep the change small and say what you changed and why.\n")
        elif script:
            lit(f"\nWrite this stage's program at {script}, then run it{run_as} to write the outputs; do not process "
                "items by hand. Later runs reuse it" + (", running it with exactly these arguments without a model"
                if command else "") + ": take every input and output location as a command-line argument "
                f"(nothing of this run hard-coded), use the packages {self.agent_dir / 'pyproject.toml'} lists (if it "
                "needs one the file lacks, do not add it yourself: the user must approve a new package, and you cannot ask. "
                "Say which package and why in your answer, or fail the stage; never a `# /// script` block), "
                "and say how to run it in its docstring.\n")
        elif kind == "deterministic":
            lit("\nThis is mechanical work: write a program that does it, run it, and keep the program "
                f"in {self.work / 'scripts'}/. Do not process items by hand.\n")
        if kind == "side_effect":
            lit("\nThis stage changes something outside the agent. The user approved it before launch. "
                "Do exactly what is described, nothing more, and report what you did (ids, links, counts).\n")
        v = st.get("verify") or {}
        if v.get("check"):
            lit(f"\nDone when: {v['check']}\n")
        lit("\nRules: use only the listed inputs; write only the listed outputs" + (" and the program" if script else "")
            + "; do not contact other agents "
            "or spawn subagents. If you cannot continue without a decision, use contact_supervisor with reason "
            "need_decision instead of guessing.")
        return parts[0]["text"] if len(parts) == 1 else parts

    # --- items -----------------------------------------------------------------
    def item(self, key, st, in_wave, task, output=None):
        """A pi-subagents child for a single-child stage."""
        c = {"key": key, "label": st.get("title", key), "cwd": str(self.work)}
        resumes = st.get("resumes")
        if resumes:
            if resumes in in_wave:
                c["resumeFrom"] = resumes  # the runner puts in that child's run id
            else:
                subagent_run_id = next((r.get("subagent_run_id") for r in reversed(self.launches)
                               if r.get("status") == "done" and resumes in (r.get("children") or {})), None)
                if not subagent_run_id:
                    self.errors.append(f"stage '{key}' resumes '{resumes}' but run {self.run_id} has no finished launch of stage '{resumes}'")
                c["resume"] = {"workflowRunId": subagent_run_id, "key": resumes, "latest": True}
        else:
            c["agent"] = st.get("agent")
        c["task"] = task
        if output:
            c["output"] = output
        if st.get("context"):
            c["context"] = st["context"]
        agent = st.get("agent")
        returned = [o for o in st.get("out") or [] if isinstance(o, dict) and not o.get("path") and o.get("schema")]
        if returned:
            if agent in self.external:
                self.notes.append(f"{key}: external runner; schema requested as JSON text only")
            else:
                c["outputSchema"] = expand_schema(returned[0]["schema"])
        acceptance = None if resumes else self.acceptance(key, agent, (st.get("verify") or {}).get("gate"))
        if acceptance:
            c["acceptance"] = acceptance
        return c

    def project_writers(self):
        """Subagents whose frontmatter declares acceptanceRole: writer."""
        return {name for name, meta in project_subagents(self.agent_dir).items() if meta.get("acceptanceRole") == "writer"}

    def script_path(self, st):
        """The stage's program in the agent's scripts/ (kept across runs), or None."""
        return str(self.agent_dir / "scripts" / st["script"]) if st.get("script") else None

    def program_command(self, st):
        """The shell command that runs the stage's program with its `run:` arguments, or None."""
        if not st.get("script") or st.get("run") is None:
            return None
        self.uses_uv = True
        return f"uv run {shlex.quote(self.script_path(st))} {self.resolve_command(st['run'])}".strip()

    def resolve_command(self, text):
        """resolve_text for a shell command: values are quoted (a path may have spaces); a secret
        stays "${ENV_NAME}", which the shell expands."""
        def sub(m):
            name = m.group(1)
            resolved = self.resolve_text(m.group(0))
            spec = self.params.get(name) or {}
            if resolved == m.group(0) or agent_params.kind(spec) == "secret":
                return f'"{resolved}"'
            return shlex.quote(resolved)
        return PARAM_RE.sub(sub, str(text))

    def command_item(self, key, st, in_wave, output):
        """A stage whose program exists and has `run:`: batcher runs the program and its
        gate (no model). `fix` is the child the runner launches only if that fails: it is shown the
        failure, repairs the program and runs it again, checked by the usual acceptance gate."""
        command = self.program_command(st)
        req = {"op": "run", "command": command}
        gate = (st.get("verify") or {}).get("gate")
        if gate is not None:
            typed = isinstance(gate, dict)
            req["gate"] = self.gate_command(gate["command"] if typed else gate)
            req["gate_json"] = typed
        fix_task = self.task(key, st, in_wave, extra_lines=(
            f"\nThe program failed when this wave ran it as a command:\n  {command}\n",
            {"failure": key},
            ("\nFind the cause, fix the program (keep the change small; say what you changed and why) and run it "
             "again the same way. Do not work around the program by writing the outputs yourself.\n")))
        fix = self.item(key, st, in_wave, fix_task, output=output)
        fix["key"], fix["label"] = f"{key}-fix", f"{st.get('title', key)} (fix the program)"
        return {"key": key, "label": st.get("title", key), "agent": BATCHER, "cwd": str(self.work),
                "task": "BATCH_TOOL_REQUEST " + json.dumps(req), "fix": fix}

    def gate_command(self, command):
        """The gate with params resolved and ../gates/ pointing at the agent's gates/."""
        if "uv run" in command:
            self.uses_uv = True
        return GATES_RE.sub(lambda m: str(self.agent_dir / "gates") + "/", self.resolve_text(command))

    def acceptance(self, key, agent, gate):
        """Explicit acceptance for children that write or have a gate. Left to infer it,
        pi-subagents gives an async writer git evidence (which fails whenever the user's
        repo has staged files) and a required independent review; the plan's gate
        scripts and <key>-review children do those jobs instead."""
        if agent in self.external:
            if gate is not None:
                self.notes.append(f"{key}: external runner; gate not supported, not compiled")
            return None
        verify = []
        if isinstance(gate, dict):
            v = {"id": "gate", "command": self.gate_command(gate["command"]), "output": "json"}
            if gate.get("schema"):
                v["schema"] = expand_schema(gate["schema"])
            if gate.get("timeout_ms"):
                v["timeoutMs"] = gate["timeout_ms"]
            verify.append(v)
        elif gate is not None:
            verify.append({"id": "gate", "command": self.gate_command(gate)})
        if not verify and agent not in self.writers:
            return None  # read-only children keep pi-subagents' default
        acc = {"level": "verified" if verify else "checked", "preserveStagedIndex": True, "review": {"required": False}}
        if verify:
            acc["verify"] = verify
        return acc

    # --- steps (wave_runner.js runs them) ------------------------------------------
    def map_step(self, key, st, in_wave):
        """A map / map_reduce stage: split by the command runner, a child per batch, then
        merge (command runner) or reduce (a child)."""
        pattern = st.get("pattern")
        items_id = st.get("items")
        if items_id in self.inputs:
            source = self.resolve_text(self.inputs[items_id].get("location", ""))
        else:
            source = self.out_path(self.producer[items_id][1]) if items_id in self.producer else None
        if not source:
            self.errors.append(f"stage '{key}': cannot resolve the items source '{items_id}'")
            source = ""
        final = next((o for o in st.get("out") or [] if isinstance(o, dict) and o.get("path")), None)
        final_path = self.out_path(final) if final else str(self.work / key / "result.json")
        split_req = {"op": "split", "source": source, "batch": st.get("batch"), "outdir": str(self.work / "batches" / key)}
        if st.get("items_glob"):
            split_req["glob"] = st["items_glob"]
        m = {"key": key, "pattern": pattern, "title": st.get("title", key), "agent": st.get("agent"),
             "cwd": str(self.work), "outdir": str(self.work / key), "ext": Path(final_path).suffix or ".json",
             "final": final_path,
             "task": self.task(key, st, in_wave, extra_lines=(
                 ("\nThis is one batch of a larger set. Work only on the items in your batch file; "
                  "the output path above is replaced by your batch output path below.\n"),)),
             "split": {"key": f"{key}-split", "agent": BATCHER, "label": f"Split items: {key}", "cwd": str(self.work),
                       "task": "BATCH_TOOL_REQUEST " + json.dumps(split_req)}}
        if st.get("context"):
            m["context"] = st["context"]
        if pattern == "map_reduce":
            m["reduce_task"] = self.task(key, st, in_wave)
        return {"map": m}

    def review_step(self, key, st):
        """A fresh reviewer of a stage's output; a blocking finding stops the wave."""
        outs = [self.out_path(o) for o in st.get("out") or [] if isinstance(o, dict) and o.get("path")]
        check = (st.get("verify") or {}).get("check") or "the output is complete and correct for the stage's purpose"
        used_next = self.used_next_by(key)
        task = (f"Review the output of agent stage \"{key}\" ({st.get('title', key)}). Read-only: do not edit anything.\n"
                f"Outputs: {', '.join(outs) if outs else 'the stage result below'}\n"
                f"Criterion: {check}\n"
                + ("Used next by:\n" + "".join(used_next) if used_next else "")
                + "Set blocking to true only if the output is wrong or incomplete in a way the next stages would build on.")
        schema = {"type": "object", "required": ["blocking", "findings"],
                  "properties": {"blocking": {"type": "boolean"}, "findings": {"type": "array", "items": {"type": "string"}}}}
        if not outs:
            self.spliced.add(key)  # the reviewer reads the returned result
        return {"review": {"key": f"{key}-review", "agent": (st.get("verify") or {}).get("review"), "label": f"Review: {key}",
                           "cwd": str(self.work), "context": "fresh",
                           "task": task if outs else [{"text": task + "\n"}, {"result": key}], "outputSchema": schema}}

    # --- wave ------------------------------------------------------------------------
    def reuse_finished(self, wid, steps):
        """Mark the layer children that completed in the wave's last (failed or blocked) launch with
        `reuse`, so a relaunch runs only the rest; returns their keys. A child is reused only when its
        item is unchanged since that launch (.compiled/<wave>.wave.json), every stage it needs in this
        wave is reused too (else its input may change), and, when a later child splices in its result,
        it has a structured output (a child's text output is not kept between launches)."""
        last = next((r for r in reversed(self.launches) if r.get("wave") == wid), None)
        before = read_json(self.run_dir / ".compiled" / f"{wid}.wave.json")
        outcome = read_json(self.run_dir / ".compiled" / f"{wid}-result.json")
        if not (last and last.get("status") in ("failed", "blocked", "stopped") and isinstance(before, dict) and isinstance(outcome, dict)):
            return []
        previous = {it["key"]: canonical({k: v for k, v in it.items() if k != "reuse"})
                    for step in before.get("steps") or [] for it in step.get("layer") or []}
        children = outcome.get("children") or {}
        reused, rerun = [], set()
        for step in steps:
            if "map" in step:
                rerun.add(step["map"]["key"])  # a map stage always runs again whole
            for it in step.get("layer") or []:
                key, done = it["key"], children.get(it["key"]) or {}
                if (previous.get(key) == canonical(it) and done.get("status") == "completed"
                        and not set((self.stages.get(key) or {}).get("needs") or []) & rerun
                        and (key not in self.spliced or done.get("structuredOutput") is not None)):
                    it["reuse"] = {k: done.get(k) for k in ("runId", "outputReference", "structuredOutput")}
                    reused.append(key)
                else:
                    rerun.add(key)
        return reused

    def compile(self, wave):
        members = [s for s in wave.get("stages") or [] if s in self.stages]
        decisions = [s for s in members if self.stages[s].get("kind") == "decision" or self.stages[s].get("pattern") == "approval"]
        runnable = [s for s in members if s not in decisions]
        preflight = {"decisions_needed": [], "approvals_needed": [], "missing_params": [], "missing_secrets": []}

        for d in decisions:
            path = self.decision_path(d)
            if not Path(path).is_file():
                st = self.stages[d]
                preflight["decisions_needed"].append({
                    "stage": d, "title": st.get("title"), "write_answer_to": path,
                    "show_user": [self.input_line(c, set())[0] for c in st.get("consumes") or []],
                })
        for s in runnable:
            st = self.stages[s]
            needs_approval = st.get("kind") == "side_effect" or st.get("approval") == "required"
            if needs_approval and s not in self.approvals:
                # what the user approves, so the parent need not read the plan to describe it
                preflight["approvals_needed"].append({
                    "stage": s, "title": st.get("title"), "why": st.get("kind_reason"),
                    "consumes": [self.input_line(c, set())[0] for c in st.get("consumes") or []],
                    "writes": [self.out_path(o) for o in st.get("out") or [] if isinstance(o, dict) and o.get("path")],
                    "done_when": (st.get("verify") or {}).get("check"),
                    "program": self.script_path(st) if st.get("script") else None,
                })

        # layers of runnable stages by in-wave needs
        layers = []
        order = TopologicalSorter({s: [d for d in self.stages[s].get("needs") or [] if d in runnable] for s in runnable})
        try:
            order.prepare()
            while order.is_active():
                layer = sorted(order.get_ready(), key=runnable.index)  # plan order within a layer
                layers.append(layer)
                order.done(*layer)
        except CycleError as exc:
            self.errors.append(f"cannot order stages: cycle {exc.args[1]}")

        limits = self.plan.get("limits") or {}
        max_parallel = limits.get("max_parallel") or 4
        steps = []
        children = []
        in_wave_done = set()
        for layer in layers:
            singles = [s for s in layer if self.stages[s].get("pattern") not in ("map", "map_reduce")]
            maps = [s for s in layer if s not in singles]
            if singles:
                items = []
                for s in singles:
                    st = self.stages[s]
                    first_path = next((self.out_path(o) for o in st.get("out") or [] if isinstance(o, dict) and o.get("path")), None)
                    program = self.script_path(st)
                    if self.program_command(st) and Path(program).is_file() and not st.get("resumes"):
                        items.append(self.command_item(s, st, in_wave_done, first_path))
                    else:
                        items.append(self.item(s, st, in_wave_done, self.task(s, st, in_wave_done), output=first_path))
                    children.append(s)
                steps.append({"layer": items})
            for s in maps:
                steps.append(self.map_step(s, self.stages[s], in_wave_done))
                children += [f"{s}-split", f"{s}-<n>", f"{s}-merge" if self.stages[s].get("pattern") == "map" else f"{s}-reduce"]
            for s in layer:
                if (self.stages[s].get("verify") or {}).get("review"):
                    steps.append(self.review_step(s, self.stages[s]))
                    children.append(f"{s}-review")
            in_wave_done |= set(layer)

        if self.uses_uv and shutil.which("uv") is None:
            self.errors.append("this wave runs stage programs or gates with `uv run`, but uv is not installed: "
                               "ask the user to install it (https://docs.astral.sh/uv/)")
        preflight["missing_params"] = sorted(self.missing_params)
        preflight["missing_secrets"] = sorted(self.missing_secrets)
        blocked = any(preflight[k] for k in preflight) or self.errors
        if blocked:
            return {"ok": False, "wave": wave.get("id"), "stages": members, "preflight": preflight, "errors": self.errors}

        wid = wave.get("id")
        revision = self.plan.get("revision", 1)
        if reused := self.reuse_finished(wid, steps):
            self.notes.append(f"relaunch: {', '.join(reused)} completed in the last launch and are unchanged: not run again")
        # the wave as data, then the fixed runner (JSON is a valid JS literal)
        spec = {"wave": wid, "revision": revision, "max_parallel": int(max_parallel), "steps": steps}
        script = (f"// Compiled from plan.yaml revision {revision}, run {self.run_id}, wave {wid}. Do not edit; recompile instead.\n"
                  f"const W = {json.dumps(spec, indent=1, ensure_ascii=False)};\n\n" + RUNNER.read_text(encoding="utf-8"))

        call = {"async": True}
        mission_id = next((r.get("mission_id") for r in reversed(self.launches) if r.get("mission_id")), None)
        if mission_id:
            call["missionId"] = mission_id
        else:
            goal = (self.plan.get("goal") or "").strip()
            title = ((goal[:67] + "...") if len(goal) > 70 else goal) or self.agent_dir.name
            call["mission"] = {"title": f"{title} (run {self.run_id})", "objective": goal}
        call["workflowScript"] = script
        return {"ok": True, "run": self.run_id, "wave": wid, "stages": members, "ends_with": wave.get("ends_with"),
                "revision": revision, "call": call, "children": children,
                "approved": sorted(self.approvals), "notes": self.notes, "spec": spec}


def main(
    agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = ".",
    approve: Annotated[str, typer.Option(help="side-effect stage keys the user approved, comma-separated")] = "",
    reviewed: Annotated[bool, typer.Option("--reviewed", help="the user has seen the last wave's outputs and said to go on (it ended with review)")] = False,
    out: Annotated[str | None, typer.Option(help="also write the result JSON to this file")] = None,
    no_commit: Annotated[bool, typer.Option("--no-commit", help="report only (e.g. for a status view): do not commit the plan")] = False,
    check: Annotated[bool, typer.Option("--check", help="planning check: compile every concrete pending wave, ignore status and preflight, report errors only")] = False,
):
    """Compile the next wave into a pi-subagents call."""
    agent_dir = Path(agent_dir).resolve()
    plan_path = agent_dir / "plan.yaml"
    if not plan_path.is_file():
        refuse(f"{agent_dir} is not an agent", 2)
    report_state(agent_dir)
    try:
        plan = load_yaml(plan_path.read_text(encoding="utf-8")) or {}
    except YAMLError as exc:
        refuse(f"cannot read plan: {exc}", 2)
    errors, _warnings, summary = validate(plan, None, None, agent_dir)

    params = agent_params.load(agent_dir, plan.get("params") or {})
    run = active_run(agent_dir)
    if check:
        results = {}
        provisional = set(summary.get("provisional") or [])
        run_dir, state = (run.dir, run.state) if run else (agent_dir / RUNS / "(next)", {})
        for w in summary.get("waves") or []:
            if any(s in provisional for s in w["stages"]):
                continue
            c = Compiler(agent_dir, plan, params, run_dir, state, set())
            c.compile(w)
            # a resume across waves cannot be resolved before the earlier wave has run
            results[w["id"]] = [e for e in c.errors if "has no finished run" not in e]
        compile_errors = {k: v for k, v in results.items() if v}
        emit({"ok": not errors and not compile_errors, "plan_errors": errors,
              "compile_errors": compile_errors, "waves_checked": sorted(results)}, 0 if not errors and not compile_errors else 1)
    if (why := check_ready(plan)) and why not in errors:
        errors.insert(0, why)
    if errors:
        emit({"ok": False, "preflight": {"plan_errors": errors}}, 1)

    if run is None:
        refuse("no active run: start one with agent_new_run (run_log.py new)")
    if run.wave_running:
        refuse(f"a wave is running (stages {summary['running']}); check its run before launching again")
    waves = summary.get("waves") or []
    if not waves:
        refuse("no wave to run: every stage is done")
    wave = waves[0]
    if launched := unrecorded_launch(run.dir, run.state, wave["id"]):
        refuse(f"wave {wave['id']} was already launched as pi-subagents run {launched[0]} ({launched[1]}) and not "
               f"recorded: do not launch it again. Record it with agent_log_start (subagent_run_id {launched[0]}), "
               "then wait for it or, if it has ended, agent_log_finish")
    last = (run.state.get("launches") or [None])[-1]
    if isinstance(last, dict) and last.get("status") == "done" and last.get("ends_with") == "review" and not reviewed:
        # the plan put a user review here: the next wave waits for the user's go, recorded by --reviewed
        emit({"ok": False, "wave": wave["id"], "preflight": {"review_needed": {
            "wave": last.get("wave"), "stages": last.get("stages"),
            "do": "summarise this wave's outputs for the user (paths, counts), ask whether to go on, then compile with reviewed"}}}, 1)
    if not agent_git.available():
        refuse("git is required: pi-subagents checks each writer child's staged index with git, and the agent's plan is versioned in its own repo. Ask the user to install git.")

    compiler = Compiler(agent_dir, plan, params, run.dir, run.state, {a.strip() for a in approve.split(",") if a.strip()})
    result = compiler.compile(wave)
    if result.get("ok"):
        # committed only for a launch: a refused compile (params, approvals) leaves the branch alone
        try:
            result["commit"] = (agent_git.head(agent_dir) if no_commit else
                                agent_git.commit(agent_dir, f"r{plan.get('revision', 1)}: plan for run {run.id}, wave {wave['id']}", to=run.branch)[0])
        except RuntimeError as exc:
            refuse(f"cannot commit the plan: {exc}")
        # a path, not the text: the model would otherwise have to re-emit the whole script as output
        # tokens just to pass it back (pi-subagents >= 0.74 reads a `workflow` value with "/" as a file)
        compiled = run.dir / ".compiled"
        compiled.mkdir(parents=True, exist_ok=True)
        script_path = compiled / f"{wave['id']}.js"
        script_path.write_text(result["call"].pop("workflowScript"), encoding="utf-8")
        (compiled / f"{wave['id']}.wave.json").write_text(canonical(result.pop("spec")), encoding="utf-8")  # for reuse_finished
        result["call"]["workflow"] = str(script_path.resolve())
    text = json.dumps(result, indent=2, ensure_ascii=False)
    for out_path in filter(None, (run.dir / ".compiled" / f"{wave['id']}.json", Path(out) if out else None)):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    emit(result, 0 if result.get("ok") else 1)


if __name__ == "__main__":
    typer.run(main)
