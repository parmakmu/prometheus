#!/usr/bin/env python3
"""Compile the next wave of an agent's active run into the exact
pi-subagents call that runs it.

Usage:
  run_compile.py [--agent DIR] [--approve key,key] [--budget USD] [--reviewed] [--out FILE] [--no-commit] [--check]
  run_compile.py --frozen RUN_DIR [--params JSON] [--approve key,key]

The wave runs in the active run's folder (runs/<id>/, see plan_validate.py):
children work in runs/<id>/work/, and relative param values resolve against
runs/<id>/. A launchable wave is saved in runs/<id>/.compiled/ (run_folders.py says how).
Gate commands name their scripts as ../gates/<script>; the compiler points
that at the agent's gates/ folder.

It refuses a wave already launched but not recorded (see unrecorded_launch).
A model stage an earlier run finished on the same inputs is not launched again:
its outputs are copied from that run (reuse_earlier; fingerprints in the
`inputs` of runs/<id>/.compiled/<wave>.json). A merged map stage that can't be
reused whole maps only the items no earlier run stored a record for (reuse_items).
Before compiling, it commits pending changes to the agent's plan
(agent_repo.py) and returns the commit, so the launch is reproducible; that is
the only change it makes to the agent. Git is required.

Waves are not written in the plan: plan_validate.derive_waves groups the stages
that are not done, each as early as its `after` allows, and starts a new wave
wherever the parent must act (a decision or approval, a side effect's report,
a user review, resolving provisional stages).

--agent      agent root (default: current directory)
--approve    side-effect stage ids the user approved for this launch
--budget     raise the run's cost ceiling (USD) to this, after the user said to go on
--reviewed   the user has seen the last wave's outputs and said to go on (it ended with review)
--out        also write the result JSON to FILE
--no-commit  report only (e.g. for a status view): switch, commit and save nothing
--check      planning check: compile every concrete pending wave (no run needed)
--frozen     a released agent's run folder (agent_release.py), with --params and --approve from
             the caller and no active run, commit or .env: the next wave after the stages its
             run.yaml records as done, or the whole plan as one (`all`) when nothing in it asks
             anyone; a decision or an unapproved side effect is in "preflight", for the caller
             to answer; {"ok": true, "done": true} when no stage is left
--params     with --frozen: the caller's param values, a JSON object (no secrets)

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

The compiler never launches anything. It also says what comes after a launch (after_launch: the next wave and what its
compile will ask, or at the end the review, the deliverables and the report), for run_steps.py finish-wave and for a
wave it records without a launch, so run_steps.py imports this module and never the reverse.
"""
import hashlib
import json
import re
import shlex
import shutil
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Annotated

import agent_params
import agent_repo
import agent_state
import pi_records
import typer
from plan_validate import agent_tools, check_ready, project_subagents, validate
from ruamel.yaml.error import YAMLError
from run_folders import (
    RUNS,
    active_run,
    list_runs,
    now,
    read_run,
    read_wave,
    read_wave_spec,
    record_reused,
    stages_done,
    write_wave,
)
from utils import (
    agent_name,
    emit,
    read_json,
    read_plan,
    refuse,
    stage_kind,
)

HERE = Path(__file__).resolve().parent
PARAM_RE = re.compile(r"\$\{([^}]*)\}")
GATES_RE = re.compile(r"(?<![\w./-])\.\./gates/")
BATCHER = "batcher"
BATCH_REQUEST = "BATCH_TOOL_REQUEST "  # the line run_batcher.py reads its request from
SINGLE = {"script", "worker", "scout", "reviewer"}
RUNNER = HERE / "wave_runner.js"  # appended to every compiled wave


def unrecorded_launch(run_dir, state, wave_id):
    """(pi-subagents run id, its state) of a launch of the wave's compiled script that run.yaml does
    not record, or None. The wave is launched between compile and agent_start_wave (which needs
    the launch's id): a session that ends in between leaves a launch nobody recorded, which a
    recompile would launch again. pi-subagents keeps the sha256 of each workflow run's script
    (status.json `workflow.scriptDigest`); the compiled file holds the script that was passed."""
    script = Path(run_dir) / ".compiled" / f"{wave_id}.js"
    if not script.is_file():
        return None
    return pi_records.unrecorded_launch(script, {r.get("subagent_run_id") for r in state.get("launches") or []})


def canonical(value):
    """A stable text form of JSON-like data, to compare items across launches."""
    return json.dumps(value, sort_keys=True, default=str)


def digest_tree(path, h):
    """Feed a file's or folder's contents (relative names and bytes) into hash `h`; False if missing."""
    path = Path(path)
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else None
    if files is None:
        return False
    for f in files:
        h.update(f"\0{f.relative_to(path) if f != path else f.name}\0".encode())
        h.update(f.read_bytes())
    return True


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
        self.models = plan.get("models") or {}
        self.timeout = (plan.get("limits") or {}).get("command_timeout_s")
        # the run's own ceiling (raised with --budget when the user says go on), else the plan's
        self.budget = (run_state.get("max_cost_usd") if isinstance(run_state, dict) else None) or (plan.get("limits") or {}).get("max_cost_usd")
        self.stages = {s["id"]: s for s in plan.get("stages") or [] if isinstance(s, dict) and s.get("id")}
        self.inputs = {i["id"]: i for i in plan.get("inputs") or [] if isinstance(i, dict) and i.get("id")}
        self.producer = {}
        for key, st in self.stages.items():
            for o in st.get("writes") or []:
                if isinstance(o, dict) and o.get("id"):
                    self.producer[o["id"]] = (key, o)
        agents = (plan.get("agents") or {}).get("available") or []
        self.external = {a["name"] for a in agents if isinstance(a, dict) and a.get("runner") == "external-cli"}
        self.writers = {"worker"} | self.project_writers()
        self.missing_params = set()
        self.missing_secrets = set()
        self.uses_uv = False  # a program or gate runs with `uv run` (the agent's environment)
        self.errors = []
        self.unresolved = set()  # errors only a run can clear (a resume of an earlier wave's stage)
        self.notes = []
        self.spliced = set()  # keys whose result a later child's task splices in (reuse_finished)
        self.fingerprints = {}  # stage -> fingerprint, saved as the wave record's `inputs` (reuse_earlier)
        self.reused_from = {}  # stage -> the earlier run its outputs are copied from

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
        for o in st.get("writes") or []:
            if isinstance(o, dict) and o.get("path"):
                return self.out_path(o)
        return str(self.work / "decisions" / f"{key}.json")

    # --- task text -------------------------------------------------------------
    def input_line(self, cid, in_wave_results):
        """(text, key) describing one id a stage reads; key names a stage earlier in this wave whose
        returned result follows the text, else None."""
        if cid in self.inputs:
            inp = self.inputs[cid]
            loc = self.resolve_text(inp.get("path", "")) if inp.get("path") else "(given in this task)"
            fmt = f", {inp['format']}" if inp.get("format") else ""
            note = f" — {inp['note']}" if inp.get("note") else ""
            return f"- {cid} ({inp.get('kind')}{fmt}): {loc}{note}", None
        pkey, o = self.producer[cid]
        pst = self.stages[pkey]
        if stage_kind(pst) == "decision" or pst.get("pattern") == "approval":
            return f"- {cid}: the user's decision, in {self.decision_path(pkey)}", None
        path = self.out_path(o)
        if path:
            fmt = f", {o['format']}" if o.get("format") else ""
            return f"- {cid} (from stage {pkey}{fmt}): {path}", None
        if pkey in in_wave_results:
            return f"- {cid} (result of stage {pkey}): ", pkey
        self.errors.append(f"stage reads '{cid}', a returned result of stage '{pkey}' from an earlier wave; give that output a path")
        return f"- {cid}: (unavailable)", None

    def used_next_by(self, key):
        """Lines naming the stages that read `key`'s outputs, with what they must achieve.
        The producer and its reviewer both get them, so they judge the output by the same
        standard: a report told only "list the available fields" once left out the one field
        (Cc) a later stage extracts, and its reviewer, reading the plan, blocked it."""
        outs = {o["id"] for o in self.stages[key].get("writes") or [] if isinstance(o, dict) and o.get("id")}
        lines = []
        for ckey, cst in self.stages.items():
            used = [c for c in cst.get("reads") or [] if c in outs]
            if ckey == key or not used:
                continue
            check = cst.get("done_when")
            lines.append(f"- {ckey} ({cst.get('title', ckey)}) reads {', '.join(used)}"
                         + (f"; it is done when: {check}" if check else "") + "\n")
        return lines

    def task(self, key, st, in_wave, extra_lines=()):
        """The child's task: text, or parts [{text} | {result: key}] when it includes the result
        of a stage earlier in this wave (wave_runner.js splices it in)."""
        slug = agent_name(self.agent_dir)
        parts = []

        def lit(s):
            if parts and "text" in parts[-1]:
                parts[-1]["text"] += s
            else:
                parts.append({"text": s})

        lit(f"Agent \"{slug}\", stage \"{key}\": {st.get('title', key)}.\n")
        for field in ("instructions", "intent"):
            if st.get(field):
                lit(f"{st[field]}\n")
        reads = st.get("reads") or []
        if reads:
            lit("\nInputs:\n")
            for cid in reads:
                text, result = self.input_line(cid, in_wave)
                lit(text)
                if result:
                    parts.append({"result": result})
                    self.spliced.add(result)
                lit("\n")
        writes = [o for o in st.get("writes") or [] if isinstance(o, dict) and o.get("path")]
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
        kind = stage_kind(st)
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
        if st.get("done_when"):
            lit(f"\nDone when: {st['done_when']}\n")
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
                    err = f"stage '{key}' resumes '{resumes}' but run {self.run_id} has no finished launch of stage '{resumes}'"
                    self.errors.append(err)
                    self.unresolved.add(err)  # --check skips it: the earlier wave hasn't run yet
                c["resume"] = {"workflowRunId": subagent_run_id, "key": resumes, "latest": True}
        else:
            c["agent"] = st.get("agent")
            if model := self.model(st):
                c["model"] = model
        c["task"] = task
        if output:
            c["output"] = output
        if st.get("context"):
            c["context"] = st["context"]
        agent = st.get("agent")
        returned = [o for o in st.get("writes") or [] if isinstance(o, dict) and not o.get("path") and o.get("schema")]
        if returned:
            if agent in self.external:
                self.notes.append(f"{key}: external runner; schema requested as JSON text only")
            else:
                c["outputSchema"] = expand_schema(returned[0]["schema"])
        acceptance = None if resumes else self.acceptance(key, agent, st.get("gate"))
        if acceptance:
            c["acceptance"] = acceptance
        return c

    def model(self, st, role="default"):
        """The model a stage's children use: its own, else the plan's for the role, else None (the subagent's)."""
        if st.get("agent") in self.external | {BATCHER}:
            return None
        return (st.get("model") if role == "default" else None) or self.models.get(role) or self.models.get("default")

    def project_writers(self):
        """Subagents whose frontmatter declares acceptanceRole: writer."""
        return {name for name, meta in project_subagents(self.agent_dir).items() if meta.get("acceptanceRole") == "writer"}

    def script_path(self, st):
        """The stage's program in the agent's scripts/ (kept across runs), or None."""
        program = st.get("program") or {}
        return str(self.agent_dir / "scripts" / program["file"]) if program.get("file") else None

    def program_command(self, st):
        """The shell command that runs the stage's program with its args, or None."""
        program = st.get("program") or {}
        if not program.get("file") or program.get("args") is None:
            return None
        self.uses_uv = True
        return f"uv run {shlex.quote(self.script_path(st))} {self.resolve_command(program['args'])}".strip()

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
        """A stage whose program exists and has args: batcher runs the program and its
        gate (no model). `fix` is the child the runner launches only if that fails: it is shown the
        failure, repairs the program and runs it again, checked by the usual acceptance gate."""
        command = self.program_command(st)
        req = {"op": "run", "command": command, **self.batcher_gate(st)}
        if self.timeout:
            req["timeout"] = self.timeout
        req.update(self.secrets_for(req))
        fix_task = self.task(key, st, in_wave, extra_lines=(
            f"\nThe program failed when this wave ran it as a command:\n  {command}\n",
            {"failure": key},
            ("\nFind the cause, fix the program (keep the change small; say what you changed and why) and run it "
             "again the same way. Do not work around the program by writing the outputs yourself.\n")))
        fix = self.item(key, st, in_wave, fix_task, output=output)
        fix["key"], fix["label"] = f"{key}-fix", f"{st.get('title', key)} (fix the program)"
        return {"key": key, "label": st.get("title", key), "agent": BATCHER, "cwd": str(self.work),
                "task": BATCH_REQUEST + json.dumps(req), "fix": fix}

    def consumed_path(self, cid):
        """Where an id a stage reads is: an input's path, or the path of the stage output that produces it."""
        if cid in self.inputs:
            return self.resolve_text(str(self.inputs[cid].get("path") or ""))
        if cid in self.producer:
            return self.out_path(self.producer[cid][1])
        return None

    def a2a_item(self, key, st, output):
        """A stage that calls another agent over A2A: batcher sends the message (the files it reads, the params, the
        side effects approved), waits for the task to end and downloads its artifacts into the stage's writes path."""
        call = st.get("a2a") or {}
        req = {"op": "a2a", "url": self.resolve_text(str(call.get("url") or "")), "out": output,
               "files": {remote: self.consumed_path(cid) for remote, cid in (call.get("files") or {}).items()},
               "params": {k: self.resolve_text(v) if isinstance(v, str) else v for k, v in (call.get("params") or {}).items()},
               "approve": list(call.get("approve") or []), **self.batcher_gate(st)}
        if call.get("token"):
            req["token"] = self.resolve_text(str(call["token"]))  # "${ENV}": batcher reads it from its environment
        if timeout := call.get("timeout_s") or self.timeout:
            req["timeout"] = timeout
        req.update(self.secrets_for(req))
        return {"key": key, "label": st.get("title", key), "agent": BATCHER, "cwd": str(self.work),
                "task": BATCH_REQUEST + json.dumps(req)}

    def batcher_gate(self, st):
        """{gate, gate_json} for batcher, which runs the stage's gate itself (no acceptance on a command runner)."""
        gate = st.get("gate")
        if gate is None:
            return {}
        typed = isinstance(gate, dict)
        return {"gate": self.gate_command(gate["command"] if typed else gate), "gate_json": typed}

    def secrets_for(self, req):
        """{secrets, env_file} for a batcher request whose command or gate names secrets (`"${ENV}"`): the batcher adds
        just those to that command's environment, read from .env then. Pi's own environment holds none, so no model
        child sees one (plan_validate allows secrets only here)."""
        secret_envs = {agent_params.env_name(n, s) for n, s in self.params.items() if agent_params.kind(s) == "secret"}
        named = sorted(set(re.findall(r"\$\{(\w+)\}", f"{req.get('command') or ''} {req.get('gate') or ''} {req.get('token') or ''}")) & secret_envs)
        return {"secrets": named, "env_file": str(self.agent_dir / agent_params.ENV_FILE)} if named else {}

    def gate_command(self, command):
        """The gate with params resolved and ../gates/ pointing at the agent's gates/."""
        if "uv run" in command:
            self.uses_uv = True
        # values quoted, as in a program's args (a value with `;` would otherwise run as a command); secrets stay "${ENV}"
        return GATES_RE.sub(lambda m: str(self.agent_dir / "gates") + "/", self.resolve_command(command))

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
        spec = st.get("map") or {}
        items_id = spec.get("over")
        if items_id in self.inputs:
            source = self.resolve_text(self.inputs[items_id].get("path", ""))
        else:
            source = self.out_path(self.producer[items_id][1]) if items_id in self.producer else None
        if not source:
            self.errors.append(f"stage '{key}': cannot resolve the items source '{items_id}'")
            source = ""
        final = next((o for o in st.get("writes") or [] if isinstance(o, dict) and o.get("path")), None)
        final_path = self.out_path(final) if final else str(self.work / key / "result.json")
        split_req = {"op": "split", "source": source, "batch": spec.get("batch"), "outdir": str(self.work / "batches" / key)}
        if spec.get("glob"):
            split_req["glob"] = spec["glob"]
        m = {"key": key, "pattern": pattern, "title": st.get("title", key), "agent": st.get("agent"),
             "cwd": str(self.work), "outdir": str(self.work / key), "ext": Path(final_path).suffix or ".json",
             "final": final_path,
             "task": self.task(key, st, in_wave, extra_lines=(
                 ("\nThis is one batch of a larger set. Work only on the items in your batch file; "
                  "the output path above is replaced by your batch output path below.\n"),)),
             "split": {"key": f"{key}-split", "agent": BATCHER, "label": f"Split items: {key}", "cwd": str(self.work),
                       "task": BATCH_REQUEST + json.dumps(split_req)}}
        if st.get("context"):
            m["context"] = st["context"]
        if model := self.model(st):
            m["model"] = model
        gate = st.get("gate")
        if pattern == "map_reduce":
            m["reduce_task"] = self.task(key, st, in_wave)
            if acceptance := self.acceptance(key, st.get("agent"), gate):
                m["reduce_acceptance"] = acceptance  # the gate checks the reduced output
        else:
            m["merge"] = {**self.batcher_gate(st), **({"timeout": self.timeout} if self.timeout else {})}
            m["merge"].update(self.secrets_for(m["merge"]))
        return {"map": m}

    def review_step(self, key, st):
        """A fresh reviewer of a stage's output; a blocking finding stops the wave."""
        outs = [self.out_path(o) for o in st.get("writes") or [] if isinstance(o, dict) and o.get("path")]
        check = st.get("done_when") or "the output is complete and correct for the stage's purpose"
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
        review = {"key": f"{key}-review", "agent": st.get("review"), "label": f"Review: {key}",
                  "cwd": str(self.work), "context": "fresh",
                  "task": task if outs else [{"text": task + "\n"}, {"result": key}], "outputSchema": schema}
        if model := self.model({"agent": review["agent"]}, "review"):
            review["model"] = model
        return {"review": review}

    # --- wave ------------------------------------------------------------------------
    def fingerprint(self, key, item, review, in_wave, skip=None):
        """What a model stage's output depends on, hashed: its child and review items (this run's
        folder masked), the contents of what it reads, its subagent's file, program and gates.
        None when that can't be known: it reads an app or url, a returned result, or the output of
        a stage in this wave that has no fingerprint (a program). A map stage's item is its map step: its
        batch size, instructions, subagent and model; what it maps over is among its reads. `skip`: a read
        left out (what a map stage maps over, for the fingerprint its items are stored under)."""
        text = canonical([item, review]).replace(str(self.run_dir), "<run>")
        # an input's place is masked: its contents are hashed below, so the same files moved elsewhere (run
        # 2026-10-05-1: scratch/emails to uploads/emails) still reuse
        for cid in self.stages[key].get("reads") or []:
            inp = self.inputs.get(cid) or {}
            if inp.get("kind") in ("file", "folder") and inp.get("path"):
                where = self.resolve_text(inp["path"])
                for form in {where, str(Path(where).expanduser())}:
                    text = text.replace(form, f"<input {cid}>")
        h = hashlib.sha256(text.encode())
        for cid in self.stages[key].get("reads") or []:
            if cid == skip:
                continue
            if cid in self.inputs:
                inp = self.inputs[cid]
                if inp.get("kind") == "text":
                    continue  # the text is in the task
                if inp.get("kind") not in ("file", "folder") or not inp.get("path"):
                    return None
                path = Path(self.resolve_text(inp["path"])).expanduser()
            else:
                pkey, o = self.producer[cid]
                decision = stage_kind(self.stages[pkey]) == "decision" or self.stages[pkey].get("pattern") == "approval"
                if pkey in in_wave:
                    # its output doesn't exist yet: it is what that stage makes from its own fingerprint,
                    # in the run it comes from (this one, or the one it is reused from)
                    if pkey not in self.fingerprints:
                        return None
                    h.update(f"\0{pkey} {self.fingerprints[pkey]}@{self.reused_from.get(pkey, self.run_id)}".encode())
                    continue
                path = self.decision_path(pkey) if decision else self.out_path(o)
                if not path:
                    return None
            if not digest_tree(path, h):
                return None
        st = self.stages[key]
        # the subagent's file, and what picks a builtin's model and prompt: the agent's settings, pi-subagents' version
        files = [self.agent_dir / ".pi" / "agents" / f"{st.get('agent')}.md", Path(self.script_path(st) or ""),
                 self.agent_dir / ".pi" / "settings.json", *pi_records.package(self.agent_dir)]
        files += [self.agent_dir / "gates" / g for g in re.findall(r"\.\./gates/(\S+)", canonical(st.get("gate")))]
        for f in files:
            if f.is_file():
                digest_tree(f, h)
        return h.hexdigest()

    def can_reuse(self, key):
        """Whether a stage's work may be taken from an earlier run: a model stage with no side effect, not resumed,
        not `reuse: false`, its outputs inside the run (work/, or outputs/ by a relative param: an earlier run has
        them at the same place to copy from; one written elsewhere, a path the user chose, is overwritten by each
        run), and no subagent with connector tools, which may act on or read what no file shows."""
        st = self.stages[key]
        outs = [self.out_path(o) for o in st.get("writes") or [] if isinstance(o, dict)]
        tools = agent_tools(project_subagents(self.agent_dir).get(st.get("agent")) or {})
        return not (stage_kind(st) not in ("judgment", "deterministic") or st.get("resumes") or st.get("reuse") is False
                    or not outs or not all(o and Path(o).is_relative_to(self.run_dir) for o in outs)
                    or any(t.startswith("mcp:") for t in tools))

    def reuse_earlier(self, key, item, review, in_wave):
        """Mark a model stage with no side effect `reuse` when an earlier run finished it with the same
        fingerprint: the same judgment on the same inputs is not made again. compile_wave's main copies
        its outputs from that run's work/. A map stage (`item`: its map step) counts as finished there when
        its merge or reduce completed, or it was reused itself. Returns that run's id, or None."""
        st = self.stages[key]
        outs = [self.out_path(o) for o in st.get("writes") or [] if isinstance(o, dict)]
        if not self.can_reuse(key):
            return None
        fp = self.fingerprint(key, item, review, in_wave)
        if not fp:
            return None
        self.fingerprints[key] = fp
        # an agent's runs, or a release's (prometheus/run_release.py): the same judgment, made once across calls too
        for rid, d in reversed(list_runs(self.agent_dir)):
            if rid == self.run_id or key not in stages_done(d):
                continue
            for path in (d / ".compiled").glob("*.json"):
                record = read_json(path) or {}
                children = (record.get("result") or {}).get("children") or {}
                done = children.get(key) or children.get(f"{key}-merge") or children.get(f"{key}-reduce") or {}
                if ((record.get("inputs") or {}).get(key) == fp and done.get("status") == "completed"
                        and all((d / Path(o).relative_to(self.run_dir)).exists() for o in outs)):
                    item["reuse"] = {**{k: done.get(k) for k in ("runId", "outputReference", "structuredOutput")}, "run": rid}
                    self.reused_from[key] = rid
                    return rid
        return None

    def item_store(self, key):
        """Where a map stage's merge stores each item's record in this run, one file per item (run_batcher.py merge)."""
        return self.work / key / "items"

    def item_fingerprint(self, key, m, review, in_wave):
        """What each item's record of a merged map stage depends on besides the item itself: the stage's fingerprint
        without what it maps over, and without its split (the batch size: an item's record is its own). Recorded as
        `<key>/items` among the wave's fingerprints, so a later run finds the records this run stores. None when the
        stage's work can't be reused, or it is a map_reduce (its batch results are combined, not one per item)."""
        st = self.stages[key]
        if st.get("pattern") != "map" or not self.can_reuse(key):
            return None
        fp = self.fingerprint(key, {k: v for k, v in m.items() if k != "split"}, review, in_wave,
                              skip=(st.get("map") or {}).get("over"))
        if fp:
            self.fingerprints[f"{key}/items"] = fp
        return fp

    def reuse_items(self, key, m, fp):
        """Hand the map step's split the item stores of earlier runs that finished the stage with item fingerprint
        `fp`, newest first (each holds every item its run covered: a merge stores the reused records too), so it leaves
        the items they hold out of the batches; and its merge the split's list of items and this run's store. Returns
        how many stores it found."""
        stores = []
        for rid, d in reversed(list_runs(self.agent_dir)):
            store = d / self.item_store(key).relative_to(self.run_dir)
            if rid == self.run_id or key not in stages_done(d) or not store.is_dir():
                continue
            if any(((read_json(p) or {}).get("inputs") or {}).get(f"{key}/items") == fp for p in (d / ".compiled").glob("*.json")):
                stores.append(str(store))
        items_file = str(self.work / key / "items.json")
        split = json.loads(m["split"]["task"].removeprefix(BATCH_REQUEST))
        m["split"]["task"] = BATCH_REQUEST + json.dumps({**split, "items_file": items_file, "stores": stores})
        m["merge"].update({"items_file": items_file, "store": str(self.item_store(key))})
        return len(stores)

    def reuse_finished(self, wid, steps):
        """Mark the layer children that completed in the wave's last (failed or blocked) launch with
        `reuse`, so a relaunch runs only the rest; returns their keys. A child is reused only when its
        item is unchanged since that launch (the wave's data in .compiled/<wave>.js), every stage it runs after in this
        wave is reused too (else its input may change), and, when a later child splices in its result,
        it has a structured output (a child's text output is not kept between launches). A map stage
        whose spec is unchanged and whose input stages are reused keeps the batches that completed (their
        output files still there) and, when all did, its reduce; the split and merge run again (no model)."""
        last = next((r for r in reversed(self.launches) if r.get("wave") == wid), None)
        before = read_wave_spec(self.run_dir, wid)
        outcome = read_wave(self.run_dir, wid).get("result")
        if not (last and last.get("status") in ("failed", "blocked", "stopped") and isinstance(before, dict) and isinstance(outcome, dict)):
            return []
        previous = {it["key"]: canonical({k: v for k, v in it.items() if k != "reuse"})
                    for step in before.get("steps") or [] for it in step.get("layer") or []}
        previous_maps = {s["map"]["key"]: canonical({k: v for k, v in s["map"].items() if k != "reuse"})
                         for s in before.get("steps") or [] if "map" in s}
        children = outcome.get("children") or {}
        reused, rerun = [], set()
        for step in steps:
            if "map" in step:
                m = step["map"]
                key = m["key"]
                done = {k: v for k, v in children.items() if re.fullmatch(rf"{re.escape(key)}-\d+", k)
                        and v.get("status") == "completed" and Path(m["outdir"], k[len(key) + 1:] + m["ext"]).is_file()}
                if (previous_maps.get(key) != canonical(m) or set((self.stages.get(key) or {}).get("after") or []) & rerun
                        or not done):
                    rerun.add(key)
                    continue
                m["reuse"] = {k: {f: v.get(f) for f in ("runId", "outputReference", "structuredOutput")} for k, v in done.items()}
                # every batch the split wrote (work/batches/<key>/) completed: what follows sees the same result
                complete = len(done) == len(list((self.work / "batches" / key).glob("*")))
                reduce = children.get(f"{key}-reduce") or {}
                if complete and m["pattern"] == "map_reduce" and reduce.get("status") == "completed":
                    m["reuse"][f"{key}-reduce"] = {f: reduce.get(f) for f in ("runId", "outputReference", "structuredOutput")}
                elif not complete or m["pattern"] == "map_reduce":
                    rerun.add(key)  # some batch or the reduce runs again: what follows may change
                reused.extend(sorted(m["reuse"]))
            for it in step.get("layer") or []:
                key, done = it["key"], children.get(it["key"]) or {}
                if "reuse" in it:
                    continue  # reused from an earlier run
                if (previous.get(key) == canonical(it) and done.get("status") == "completed"
                        and not set((self.stages.get(key) or {}).get("after") or []) & rerun
                        and (key not in self.spliced or done.get("structuredOutput") is not None)):
                    it["reuse"] = {k: done.get(k) for k in ("runId", "outputReference", "structuredOutput")}
                    reused.append(key)
                else:
                    rerun.add(key)
        return reused

    def compile(self, wave, reuse=False):
        """`reuse`: let unchanged model stages take an earlier run's outputs (reuse_earlier); only for a launch."""
        members = [s for s in wave.get("stages") or [] if s in self.stages]
        decisions = [s for s in members if stage_kind(self.stages[s]) == "decision" or self.stages[s].get("pattern") == "approval"]
        runnable = [s for s in members if s not in decisions]
        preflight = {"decisions_needed": [], "approvals_needed": [], "missing_params": [], "missing_secrets": [], "budget": None}
        spent = round(sum(r.get("cost_usd") or 0 for r in self.launches), 4)
        if self.budget and spent >= self.budget:
            preflight["budget"] = {"spent_usd": spent, "max_cost_usd": self.budget,
                                   "do": "tell the user what the run spent; to go on, compile with a higher budget they give"}

        for d in decisions:
            path = self.decision_path(d)
            if not Path(path).is_file():
                st = self.stages[d]
                preflight["decisions_needed"].append({
                    "stage": d, "title": st.get("title"), "write_answer_to": path,
                    "show_user": [self.input_line(c, set())[0] for c in st.get("reads") or []],
                })
        for s in runnable:
            st = self.stages[s]
            if stage_kind(st) == "side_effect" and s not in self.approvals:
                # what the user approves, so the parent need not read the plan to describe it
                preflight["approvals_needed"].append({
                    "stage": s, "title": st.get("title"), "why": (st.get("kind") or {}).get("reason"),
                    "reads": [self.input_line(c, set())[0] for c in st.get("reads") or []],
                    "writes": [self.out_path(o) for o in st.get("writes") or [] if isinstance(o, dict) and o.get("path")],
                    "done_when": st.get("done_when"),
                    "program": self.script_path(st),
                })

        # layers of runnable stages by in-wave `after`
        layers = []
        order = TopologicalSorter({s: [d for d in self.stages[s].get("after") or [] if d in runnable] for s in runnable})
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
            reviews = {s: self.review_step(s, self.stages[s]) for s in layer if self.stages[s].get("review")}
            if singles:
                items = []
                for s in singles:
                    st = self.stages[s]
                    first_path = next((self.out_path(o) for o in st.get("writes") or [] if isinstance(o, dict) and o.get("path")), None)
                    program = self.script_path(st)
                    if st.get("pattern") == "a2a":
                        items.append(self.a2a_item(s, st, first_path))
                    elif self.program_command(st) and Path(program).is_file() and not st.get("resumes"):
                        items.append(self.command_item(s, st, in_wave_done, first_path))
                    else:
                        items.append(self.item(s, st, in_wave_done, self.task(s, st, in_wave_done), output=first_path))
                        if reuse and (rid := self.reuse_earlier(s, items[-1], reviews.get(s), in_wave_done)):
                            reviews.pop(s, None)  # it passed its review in that run
                            self.notes.append(f"{s}: reused from run {rid} (same inputs and stage): its outputs are copied, not run again")
                    children.append(s)
                steps.append({"layer": items})
            for s in maps:
                step = self.map_step(s, self.stages[s], in_wave_done)
                # before the whole stage's: recorded even when the stage is reused whole, whose store is copied too
                item_fp = self.item_fingerprint(s, step["map"], reviews.get(s), in_wave_done) if reuse else None
                if reuse and (rid := self.reuse_earlier(s, step["map"], reviews.get(s), in_wave_done)):
                    # the same items and stage as an earlier run: its merged (or reduced) output is copied, and the
                    # stage stands in the wave as one reused item, so its split, batches and merge aren't launched
                    steps.append({"layer": [{"key": s, "reuse": step["map"].pop("reuse")}]})
                    reviews.pop(s, None)
                    self.notes.append(f"{s}: reused from run {rid} (same items and stage): its output is copied, not mapped again")
                    children.append(s)
                    continue
                if item_fp and (n := self.reuse_items(s, step["map"], item_fp)):
                    self.notes.append(f"{s}: items {'an earlier run' if n == 1 else f'{n} earlier runs'} already mapped "
                                      "with this stage are left out of its batches; their results are merged back in")
                steps.append(step)
                children += [f"{s}-split", f"{s}-<n>", f"{s}-merge" if self.stages[s].get("pattern") == "map" else f"{s}-reduce"]
            for s, review in reviews.items():
                steps.append(review)
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
            title = ((goal[:67] + "...") if len(goal) > 70 else goal) or agent_name(self.agent_dir)
            call["mission"] = {"title": f"{title} (run {self.run_id})", "objective": goal}
        call["workflowScript"] = script
        result = {"ok": True, "run": self.run_id, "wave": wid, "stages": members, "ends_with": wave.get("ends_with"),
                  "revision": revision, "call": call, "children": children,
                  "approved": sorted(self.approvals), "notes": self.notes, "spec": spec}
        reused_items = [it for step in steps for it in step.get("layer") or []]
        if steps and all("layer" in step for step in steps) and all("reuse" in it for it in reused_items):
            # every stage is reused: nothing to launch. What the runner would have returned, so the wave is recorded
            # without one (run_folders.record_reused, from run_steps.py or run_release.Run)
            result["nothing_to_launch"] = True
            result["outcome"] = {"wave": wid, "revision": revision, "status": "done", "children": {
                it["key"]: {**{k: it["reuse"].get(k) for k in ("runId", "outputReference", "structuredOutput")},
                            "status": "completed", "reused": True} for it in reused_items}}
        return result

    def copy_reused(self):
        """Copy each reused stage's outputs from the earlier run into this one, at the same place in the run."""
        for key, rid in self.reused_from.items():
            src = self.run_dir.parent / rid
            rels = [Path(self.out_path(o)).relative_to(self.run_dir) for o in self.stages[key].get("writes") or []]
            # a map stage's item store, so a later run with some items changed still finds the rest here
            if (src / (store := self.item_store(key).relative_to(self.run_dir))).is_dir():
                rels.append(store)
            for rel in rels:
                (self.run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                if (src / rel).is_dir():
                    shutil.copytree(src / rel, self.run_dir / rel, dirs_exist_ok=True)
                else:
                    shutil.copy2(src / rel, self.run_dir / rel)


FROZEN_WAVE = "all"


def frozen_values(agent_dir, specs, given):
    """Param values for a frozen run: the caller's (not secrets), else the environment, else the plan's default.
    A released checkout has no .env (it is not in git); problems: undeclared or secret names given."""
    if problems := agent_params.problems_with(specs, given):
        return None, problems
    values = agent_params.load(agent_dir, specs)
    values.update({n: str(v) for n, v in given.items() if v is not None})
    return values, []


def asks_someone(st, approvals):
    """Whether a stage needs an answer from the caller first: a decision, or a side effect not approved."""
    return (stage_kind(st) == "decision" or st.get("pattern") == "approval"
            or (stage_kind(st) == "side_effect" and st.get("id") not in approvals))


def compile_frozen(agent_dir, plan, run_dir, values, approvals, state=None, reuse=False):
    """The next wave of a released agent's run, run by code with no model deciding anything.

    `state` (the run's run.yaml) names the stages done and the waves launched. A first wave of a plan in which
    nothing asks the caller anything is the whole plan (`all`): the runner orders every stage by `after` and stops
    at the first failure, in one launch. Otherwise the waves are derived as in an agent's own run, so a decision or an
    unapproved side effect starts a wave whose compile returns a preflight: the caller answers, and the run goes on.
    `reuse`: model stages take an earlier run's result when nothing changed (reuse_earlier, across the release's
    runs); the result then carries the compiler as "_compiler", for save_wave to save fingerprints and copy outputs.
    Returns compile()'s result, or {"ok": True, "done": True} when no stage is left."""
    state = state or {}
    done = {k for k, v in (state.get("stages") or {}).items() if v == "done"}
    launches = state.get("launches") or []
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict) and s.get("id")]
    if not done and not launches and not any(asks_someone(s, approvals) for s in stages):
        wave = {"id": FROZEN_WAVE, "stages": [s["id"] for s in stages], "ends_with": "none"}
        compiler = Compiler(agent_dir, plan, values, run_dir, {}, approvals)
        return {**compiler.compile(wave, reuse=reuse), **({"_compiler": compiler} if reuse else {})}
    run_state = {"id": Path(run_dir).name, "stages": {k: "done" for k in done},
                 "launches": [{"status": "done", "cost_usd": r.get("cost_usd")} for r in launches]}
    _errors, _warnings, summary = validate(plan, None, None, agent_dir, run_state)
    waves = summary.get("waves") or []
    if not waves:
        return {"ok": True, "done": True}
    compiler = Compiler(agent_dir, plan, values, run_dir, run_state, approvals)
    return {**compiler.compile(waves[0], reuse=reuse), **({"_compiler": compiler} if reuse else {})}


def save_wave(run_dir, result, compiler):
    """Save a launchable compile in the run folder: the script as <wave>.js, which the call then names by path (the
    model would otherwise re-emit the whole script as output tokens to pass it back; pi-subagents reads a
    `workflow` value with "/" as a file), and the result in <wave>.json with the fingerprints later runs reuse by
    (`inputs`). Copies the outputs reused now. The wave's data is in the script only (runs.read_wave_spec)."""
    wave = result["wave"]
    result.pop("spec")
    script = Path(run_dir) / ".compiled" / f"{wave}.js"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(result["call"].pop("workflowScript"), encoding="utf-8")
    result["call"]["workflow"] = str(script.resolve())
    write_wave(run_dir, wave, {**result, "inputs": compiler.fingerprints})
    compiler.copy_reused()
    return result


# --- after a launch: what agent_finish_wave returns (run_steps.py finish-wave), and a wave recorded without one ----

def close_mission(run, status, summary):
    """The subagent call that closes the run's mission (one mission per run)."""
    mid = next((r.get("mission_id") for r in reversed(run.state.get("launches") or []) if r.get("mission_id")), None)
    return {"action": "mission.close", "missionId": mid, "missionStatus": status, "summary": summary} if mid else None


def commit(agent_dir, message, run):
    """Commit the agent on the run's branch; the note, or why it was not committed (never fatal)."""
    try:
        return agent_repo.commit(agent_dir, message, to=run.branch)[1]
    except RuntimeError as exc:
        return f"not committed: {exc}"


def launch_fields(agent_dir, plan, nxt, approved, answers):
    """What an agent's run keeps on a launch, beside what every run does: the plan's revision, what follows the wave,
    the branch and commit it ran from, and the approvals, decisions and param values it ran with."""
    return {"revision": plan.get("revision", 1), "ends_with": nxt["ends_with"], "branch": agent_repo.branch(agent_dir),
            "commit": agent_repo.head(agent_dir), "approvals": approved, "decisions": answers,
            "params": agent_params.record(plan.get("params"), agent_params.load(agent_dir, plan.get("params")))}


def record_without_launch(agent_dir, plan, run, nxt, returned, approved):
    """A wave every stage of which is reused from an earlier run (compile_wave: nothing_to_launch), recorded as
    launched and finished at once, with no launch (run_folders.record_reused): what agent_finish_wave would return."""
    entry = record_reused(run.dir, run.state, nxt["id"], nxt["stages"], returned,
                          **launch_fields(agent_dir, plan, nxt, approved, {}))
    run.save()
    return after_launch(agent_dir, plan, run, entry, "done", returned)


def count_items(path: Path):
    """How many items a deliverable holds, where that can be read without a model: a JSON array's entries, a JSON
    lines file's lines, a CSV's rows (header not counted), a folder's files; else None."""
    try:
        if path.is_dir():
            return sum(1 for f in path.rglob("*") if f.is_file())
        suffix = path.suffix.lower()
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else None
        if suffix in (".jsonl", ".ndjson"):
            return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        if suffix == ".csv":
            return max(0, sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) - 1)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def list_deliverables(agent_dir, plan, run):
    """The plan's outputs as this run has them: {id, path (relative to the agent when inside it), exists, items (how
    many, when count_items can tell)}, resolved as the compiler resolves them (params, and relative paths inside the
    run): the final report checks a count from here instead of reading the file."""
    compiler = Compiler(agent_dir, plan, agent_params.load(agent_dir, plan.get("params")), run.dir, run.state, set())
    found = []
    for o in plan.get("outputs") or []:
        if not isinstance(o, dict) or not o.get("id") or not (path := compiler.out_path(o)):
            continue
        path = Path(path)
        shown = path.relative_to(agent_dir) if path.is_relative_to(agent_dir) else path
        entry = {"id": o["id"], "path": str(shown), "exists": path.exists()}
        if path.exists() and (items := count_items(path)) is not None:
            entry["items"] = items
        found.append(entry)
    return found


def next_preflight(agent_dir, plan, run, wave):
    """What the next wave's compile would ask for first (decisions, approvals, params, secrets), so the
    parent collects it in the same turn instead of compiling once just to hear it."""
    try:
        compiled = Compiler(agent_dir, plan, agent_params.load(agent_dir, plan.get("params")),
                            run.dir, run.state, set(run.state.get("preapproved") or [])).compile(wave)
    except (OSError, ValueError, KeyError, TypeError):  # only a hint: the compile itself reports what went wrong
        return None
    return {k: v for k, v in (compiled.get("preflight") or {}).items() if v} or None


def after_launch(agent_dir, plan, run, entry, status, returned):
    # status: how the launch ended (run_folders.STATUSES)
    """Once a launch is recorded: commit what the wave wrote, end the run after its last wave, and say what comes next:
    agent_finish_wave's result."""
    state, wave = run.state, entry["wave"]
    wave_status = "done" if status == "done" else "pending"
    git_note = commit(agent_dir, f"run {run.id}, wave {wave}: files the wave wrote", run)  # while the run is still active
    keys = [s.get("id") for s in plan.get("stages") or []]
    done = bool(keys) and all(state["stages"].get(k) == "done" for k in keys)
    if done:
        state.update({"status": "done", "finished_at": now(), "built_from": agent_repo.built_from(agent_dir)})
        run.save()
    total = sum(r.get("cost_usd") or 0 for r in state.get("launches") or [])
    after = validate(plan, None, None, agent_dir, run_state=state)[2]
    nxt = None if done else (after.get("waves") or [None])[0]  # after a failed wave: that wave again
    if nxt:
        nxt = {k: nxt[k] for k in ("id", "stages", "ends_with")}
        if preflight := next_preflight(agent_dir, plan, run, nxt):
            nxt["preflight"] = preflight
    return {"ok": True, "run": run.id, "run_status": state["status"], "wave_status": wave_status,
          "ends_with": entry.get("ends_with") if status == "done" else None,
          # a summary only: outputs are files the gates checked; read one only when something needs a look
          "children": {k: {"status": (v or {}).get("status"), "output": (v or {}).get("outputReference")}
                       for k, v in ((returned or {}).get("children") or {}).items()} if isinstance(returned, dict) else None,
          "error": entry.get("error"), "failure_kind": entry.get("failure_kind"), "cost_usd": entry.get("cost_usd"),
          "stopped_at": entry.get("stopped_at"), "git_note": git_note,
          # what to compile next, so the parent goes straight to agent_compile_wave (no check in between)
          "next_wave": nxt,
          "review": agent_repo.review_info(agent_dir) if done else None,
          "mission_close": close_mission(run, "completed", f"Agent run {run.id} completed ({len(keys)} stages"
                                         + (f", ${total:.4f} in children" if total else "") + ").") if done else None,
          # the criteria the final report goes through, so the parent need not read the plan for them
          "done_when": plan.get("done_when") if done else None,
          # where the deliverables are in this run: the report names them, nothing to work out
          "outputs": list_deliverables(agent_dir, plan, run) if done else None,
          "report": write_report(agent_dir, plan, run) if done else None}


def write_report(agent_dir, plan, run):
    """The final report, settled by code where it can be: each deliverable with how its stage's output was checked
    (gate, review, or reused from a run where they passed), and what is left for the model: the criteria, when some
    deliverable no check covers. `do` says which."""
    stages = {s.get("id"): s for s in plan.get("stages") or [] if isinstance(s, dict)}
    by_output = {o.get("id"): k for k, s in stages.items() for o in s.get("writes") or [] if isinstance(o, dict)}
    children = {}
    for launch in run.state.get("launches") or []:
        children.update(((read_wave(run.dir, launch.get("wave")).get("result") or {}).get("children")) or {})
    lines, unchecked = [], []
    for d in list_deliverables(agent_dir, plan, run):
        key = by_output.get(d["id"])
        st, child = stages.get(key) or {}, children.get(key) or {}
        how = ("reused from an earlier run, checked there" if child.get("reused")
               else ", ".join(c for c, ok in (("its gate passed", st.get("gate") not in (None, "", "true")),
                                              ("its review passed", st.get("review"))) if ok))
        if not how:
            unchecked.append(d["path"])
        items = f", {d['items']} items" if "items" in d else ""
        lines.append(f"{d['path']} ({'present' if d['exists'] else 'MISSING'}{items}): {how or 'no check covers it'}")
    return {"deliverables": lines, "criteria": plan.get("done_when") or [],
            "do": ("Report the deliverables and criteria as they stand: every deliverable was checked. Open nothing."
                   if not unchecked else
                   f"Report the deliverables as they stand. Only these no check covers: {', '.join(unchecked)}. Judge "
                   "the criteria about them from their items count; open one only if that can't settle it, once.")}


def record_reused_wave(agent_dir, plan, run, result):
    """A wave every stage of which is reused, recorded as it is compiled (record_without_launch): nothing is
    launched, and the result is what agent_finish_wave would have returned, with `launched: false`."""
    nxt = {"id": result["wave"], "stages": result["stages"], "ends_with": result["ends_with"]}
    recorded = record_without_launch(agent_dir, plan, run, nxt, result["outcome"], result.get("approved") or [])
    # first, and in words: a model that saw only a finished wave's result called agent_finish_wave too (run 2026-10-16-7)
    return {"ok": True, "launched": False,
            "recorded": f"wave {result['wave']} is already recorded: every stage was reused, nothing was launched. Do not "
                        "launch it or call agent_start_wave or agent_finish_wave: go on from this result as from agent_finish_wave's",
            **recorded, "wave": result["wave"], "stages": result["stages"], "notes": result.get("notes"), "commit": result.get("commit")}


def main(
    agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = ".",
    approve: Annotated[str, typer.Option(help="side-effect stage ids the user approved, comma-separated")] = "",
    budget: Annotated[float | None, typer.Option(help="raise the run's cost ceiling (USD) to this, after the user said to go on")] = None,
    reviewed: Annotated[bool, typer.Option("--reviewed", help="the user has seen the last wave's outputs and said to go on (it ended with review)")] = False,
    out: Annotated[str | None, typer.Option(help="also write the result JSON to this file")] = None,
    no_commit: Annotated[bool, typer.Option("--no-commit", help="report only (e.g. for a status view): switch, commit and save nothing")] = False,
    check: Annotated[bool, typer.Option("--check", help="planning check: compile every concrete pending wave, ignore status and preflight, report errors only")] = False,
    frozen: Annotated[str | None, typer.Option(help="a released agent's run folder: compile its next wave there (the whole plan as one when nothing asks the caller)")] = None,
    params_json: Annotated[str, typer.Option("--params", help="with --frozen: the caller's param values, a JSON object (no secrets)")] = "{}",
):
    """Compile the next wave into a pi-subagents call."""
    agent_dir = Path(agent_dir).resolve()
    plan_path = agent_dir / "plan.yaml"
    if not plan_path.is_file():
        refuse(f"{agent_dir} is not an agent", 2)
    if frozen:
        plan = read_plan(agent_dir, strict=True) or {}
        try:
            given = json.loads(params_json)
        except ValueError as exc:
            refuse(f"--params is not JSON: {exc}", 2)
        values, problems = frozen_values(agent_dir, plan.get("params") or {}, given if isinstance(given, dict) else {})
        if problems:
            refuse("; ".join(problems), 2)
        result = compile_frozen(agent_dir, plan, Path(frozen).resolve(), values, {a.strip() for a in approve.split(",") if a.strip()},
                                read_run(frozen), reuse=True)
        compiler = result.pop("_compiler", None)
        launchable = result.get("ok") and not result.get("done")
        emit(save_wave(frozen, result, compiler) if launchable else result, 0 if result.get("ok") else 1)
    agent_state.report_state(agent_dir)
    if not check:
        agent_state.require(agent_dir, agent_state.compile_wave)
    if not check and (run := active_run(agent_dir)):
        # the plan, gates and programs are read from what is checked out: the run's own branch
        if no_commit and (off := agent_repo.find_off_branch(agent_dir, run.branch)):
            refuse(f"run {run.id}: {agent_repo.describe_off_branch(off)}")  # a report switches nothing
        try:
            agent_repo.back_to_run_branch(agent_dir, run.branch)
        except RuntimeError as exc:
            refuse(f"run {run.id}: {exc}. Tell the user; do not change branches or files yourself")
    try:
        plan = read_plan(agent_dir, strict=True) or {}
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
            results[w["id"]] = [e for e in c.errors if e not in c.unresolved]
        compile_errors = {k: v for k, v in results.items() if v}
        emit({"ok": not errors and not compile_errors, "plan_errors": errors,
              "compile_errors": compile_errors, "waves_checked": sorted(results)}, 0 if not errors and not compile_errors else 1)
    if (why := check_ready(plan)) and why not in errors:
        errors.insert(0, why)
    if errors:
        emit({"ok": False, "preflight": {"plan_errors": errors}}, 1)

    waves = summary.get("waves") or []
    if not waves:
        refuse("no wave to run: every stage is done")
    wave = waves[0]
    if launched := unrecorded_launch(run.dir, run.state, wave["id"]):
        refuse(f"wave {wave['id']} was already launched as pi-subagents run {launched[0]} ({launched[1]}) and not "
               f"recorded: do not launch it again. Record it with agent_start_wave (subagent_run_id {launched[0]}), "
               "then wait for it or, if it has ended, agent_finish_wave")
    last = (run.state.get("launches") or [None])[-1]
    if isinstance(last, dict) and last.get("status") == "done" and last.get("ends_with") == "review" and not reviewed:
        # the plan put a user review here: the next wave waits for the user's go, recorded by --reviewed
        emit({"ok": False, "wave": wave["id"], "preflight": {"review_needed": {
            "wave": last.get("wave"), "stages": last.get("stages"),
            "do": "summarise this wave's outputs for the user (paths, counts), ask whether to go on, then compile with reviewed"}}}, 1)
    if budget is not None:
        run.state["max_cost_usd"] = budget
        run.save()
    if not agent_repo.available():
        refuse("git is required: pi-subagents checks each writer child's staged index with git, and the agent's plan is versioned in its own repo. Ask the user to install git.")

    # approved now, or for the whole run when it started (an API call's `approve`)
    approvals = {a.strip() for a in approve.split(",") if a.strip()} | set(run.state.get("preapproved") or [])
    compiler = Compiler(agent_dir, plan, params, run.dir, run.state, approvals)
    result = compiler.compile(wave, reuse=True)
    if result.get("ok"):
        # committed only for a launch: a refused compile (params, approvals) leaves the branch alone
        try:
            result["commit"] = (agent_repo.head(agent_dir) if no_commit else
                                agent_repo.commit(agent_dir, f"r{plan.get('revision', 1)}: plan for run {run.id}, wave {wave['id']}", to=run.branch)[0])
        except RuntimeError as exc:
            refuse(f"cannot commit the plan: {exc}")
        if not no_commit:  # a report writes nothing
            save_wave(run.dir, result, compiler)
            if result.get("nothing_to_launch"):
                result = record_reused_wave(agent_dir, plan, run, result)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    emit(result, 0 if result.get("ok") else 1)


if __name__ == "__main__":
    typer.run(main)
