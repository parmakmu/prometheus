#!/usr/bin/env python3
"""Compile the next wave of a workflow's active run into the exact
pi-subagents call that runs it.

Usage:
  compile_wave.py [--workflow DIR] [--approve key,key] [--out FILE] [--check]

The wave runs in the active run's folder (runs/<id>/, see validate_plan.py):
children work in runs/<id>/work/, relative param values resolve against
runs/<id>/, and the result is also saved to runs/<id>/.compiled/<wave>.json.
Gate commands name their scripts as ../gates/<script>; the compiler points
that at the workflow's gates/ folder.

Before compiling, it commits pending changes to the workflow's plan
(workflow_git.py) and returns the commit, so the launch is reproducible; that is
the only change it makes to the workflow. Git is required.

Waves are not written in the plan: validate_plan.derive_waves groups the stages
that are not done, each as early as its needs allow, and starts a new wave
wherever the parent must act (a decision or approval, a side effect's report,
a user review, a replan).

--workflow  workflow root (default: current directory)
--approve   side-effect stage keys the user approved for this launch
--out       also write the result JSON to FILE
--check     planning check: compile every concrete pending wave (no run needed)

Prints one JSON object. When the wave can launch:

  {"ok": true, "wave": "w1", "stages": [...], "ends_with": "review",
   "call": {"async": true, "mission"|"missionId": …, "workflowScriptPath": "…/.compiled/w1.js"},
   "children": [...], "notes": [...]}

The generated script is saved to runs/<id>/.compiled/<wave>.js (read it there to
review it; `--check` and a directly-run Compiler still return it inline as
`workflowScript`, since neither launches). Pass `call` to the subagent tool
exactly as given: passing a path, not the script text, is the point. When something must happen
first, "ok" is false and "preflight" lists it: decisions to collect (with the
file to write each answer to), approvals to get, params to set (missing_params),
environment variables the user must set for secrets (missing_secrets), or plan errors.

The compiler never launches anything.
"""
import importlib.util
import json
import re
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Annotated

import typer
import workflow_git
import workflow_params
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

HERE = Path(__file__).resolve().parent
PARAM_RE = re.compile(r"\$\{([^}]*)\}")
GATES_RE = re.compile(r"(?<![\w./-])\.\./gates/")
BATCHER = "workflow-batcher"
SINGLE = {"script", "worker", "scout", "reviewer"}


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_plan", HERE / "validate_plan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def js(value):
    """A JavaScript literal for a JSON-compatible Python value."""
    return json.dumps(value, ensure_ascii=False)


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
    def __init__(self, workflow, plan, params_values, run_dir, run_state, approvals):
        """`params_values`: {name: value} from workflow_params.load."""
        self.workflow = Path(workflow)
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
        self.external = {a["name"] for a in agents if isinstance(a, dict) and a.get("runner") in ("external-cli", "external-job")}
        self.writers = {"worker"} | self.project_writers()
        self.missing_params = set()
        self.missing_secrets = set()
        self.errors = []
        self.notes = []

    # --- paths ---------------------------------------------------------------
    def resolve_text(self, text):
        """${name} -> the param's value by its type (workflow_params.resolve); a secret
        becomes ${ENV_NAME}, which the child's or gate's shell expands."""
        def sub(m):
            name = m.group(1)
            spec = self.params.get(name) or {}
            if self.values.get(name) is None:
                if workflow_params.kind(spec) == "secret":
                    self.missing_secrets.add(workflow_params.env_name(name, spec))
                else:
                    self.missing_params.add(name)
                return m.group(0)
            return workflow_params.resolve(name, spec, self.values[name], self.run_dir)
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
        """(text, js_expr_or_None) describing one consumed id."""
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
            expr = f"JSON.stringify(R[{js(pkey)}].structuredOutput ?? R[{js(pkey)}].output)"
            return f"- {cid} (result of stage {pkey}): ", expr
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

    def task_expr(self, key, st, in_wave, extra_lines=()):
        """A JS expression building the child's task text."""
        slug = self.workflow.name
        parts = []  # list of ("lit", str) | ("expr", str)

        def lit(s):
            parts.append(("lit", s))

        lit(f"Workflow \"{slug}\", stage \"{key}\": {st.get('title', key)}.\n")
        for field in ("intent", "notes"):
            if st.get(field):
                lit(f"{st[field]}\n")
        consumes = st.get("consumes") or []
        if consumes:
            lit("\nInputs:\n")
            for cid in consumes:
                text, expr = self.input_line(cid, in_wave)
                lit(text)
                if expr:
                    parts.append(("expr", expr))
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
            lit(line)
        kind = st.get("kind")
        script = self.script_path(st)
        if script and Path(script).is_file():
            lit(f"\nThis stage's program is {script}, reviewed and kept with the workflow. Run it (its docstring "
                "says how) to write the outputs; do not rewrite it or process items by hand. Change it only if it "
                "fails or its output would not meet \"Done when\"; keep the change small and say what you changed and why.\n")
        elif script:
            lit(f"\nWrite this stage's program at {script}, then run it to write the outputs; do not process items "
                "by hand. Later runs reuse it: take every input and output location as a command-line argument "
                "(nothing of this run hard-coded), use only Python's standard library or packages already "
                "installed, and say how to run it in its docstring.\n")
        elif kind == "deterministic":
            lit("\nThis is mechanical work: write a program that does it, run it, and keep the program "
                f"in {self.work / 'scripts'}/. Do not process items by hand.\n")
        if kind == "side_effect":
            lit("\nThis stage changes something outside the workflow. The user approved it before launch. "
                "Do exactly what is described, nothing more, and report what you did (ids, links, counts).\n")
        v = st.get("verify") or {}
        if v.get("check"):
            lit(f"\nDone when: {v['check']}\n")
        lit("\nRules: use only the listed inputs; write only the listed outputs" + (" and the program" if script else "")
            + "; do not contact other agents "
            "or spawn subagents. If you cannot continue without a decision, use contact_supervisor with reason "
            "need_decision instead of guessing.")
        out = []
        for kind_, val in parts:
            if kind_ == "lit":
                if out and out[-1][0] == "lit":
                    out[-1] = ("lit", out[-1][1] + val)
                else:
                    out.append(("lit", val))
            else:
                out.append(("expr", val))
        return " + ".join(js(v) if k == "lit" else f"({v})" for k, v in out) or '""'

    # --- items -----------------------------------------------------------------
    def item_fields(self, key, st, in_wave, task_expr, output=None, returned_schema=True):
        fields = [("key", js(key)), ("label", js(st.get("title", key))), ("cwd", js(str(self.work)))]
        resumes = st.get("resumes")
        if resumes:
            if resumes in in_wave:
                fields.append(("resume", f"R[{js(resumes)}].runId"))
            else:
                subagent_run_id = next((r.get("subagent_run_id") for r in reversed(self.launches)
                               if r.get("status") == "done" and resumes in (r.get("children") or {})), None)
                if not subagent_run_id:
                    self.errors.append(f"stage '{key}' resumes '{resumes}' but run {self.run_id} has no finished launch of stage '{resumes}'")
                fields.append(("resume", js({"workflowRunId": subagent_run_id, "key": resumes, "latest": True})))
        else:
            fields.append(("agent", js(st.get("agent"))))
        fields.append(("task", task_expr))
        if output:
            fields.append(("output", js(output)))
        if st.get("context"):
            fields.append(("context", js(st["context"])))
        agent = st.get("agent")
        returned = [o for o in st.get("out") or [] if isinstance(o, dict) and not o.get("path") and o.get("schema")]
        if returned and returned_schema:
            if agent in self.external:
                self.notes.append(f"{key}: external runner; schema requested as JSON text only")
            else:
                fields.append(("outputSchema", js(expand_schema(returned[0]["schema"]))))
        acceptance = None if resumes else self.acceptance(key, agent, (st.get("verify") or {}).get("gate"))
        if acceptance:
            fields.append(("acceptance", js(acceptance)))
        return "{ " + ", ".join(f"{k}: {v}" for k, v in fields) + " }"

    def project_writers(self):
        """Project agents whose frontmatter declares acceptanceRole: writer."""
        names = set()
        agents_dir = self.workflow / ".pi" / "agents"
        for path in sorted(agents_dir.iterdir()) if agents_dir.is_dir() else []:
            text = path.read_text(encoding="utf-8")
            end = text.find("\n---", 3) if path.suffix == ".md" and text.startswith("---") else -1
            meta = YAML(typ="safe").load(text[3:end]) if end != -1 else None
            if isinstance(meta, dict) and meta.get("acceptanceRole") == "writer":
                names.add(meta.get("name") or path.stem)
        return names

    def script_path(self, st):
        """The stage's program in the workflow's scripts/ (kept across runs), or None."""
        return str(self.workflow / "scripts" / st["script"]) if st.get("script") else None

    def gate_command(self, command):
        """The gate with params resolved and ../gates/ pointing at the workflow's gates/."""
        return GATES_RE.sub(lambda m: str(self.workflow / "gates") + "/", self.resolve_text(command))

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

    # --- script pieces -------------------------------------------------------------
    def run_layer(self, items_js, keys):
        lines = [f"items = [\n    {(',' + chr(10) + '    ').join(items_js)}\n  ];",
                 "for (let i = 0; i < items.length; i += P) {",
                 "    const part = await runs.all(items.slice(i, i + P));",
                 "    part.forEach((r, j) => { R[items[i + j].key] = r; });",
                 "  }",
                 f"  bad = {js(keys)}.filter(k => failed(R[k]));",
                 "  if (bad.length) return stop(\"failed\", bad);"]
        return "\n  ".join(lines)

    def map_block(self, key, st, in_wave):
        pattern = st.get("pattern")
        items_id = st.get("items")
        if items_id in self.inputs:
            inp = self.inputs[items_id]
            source = self.resolve_text(inp.get("location", ""))
        else:
            source = self.out_path(self.producer[items_id][1]) if items_id in self.producer else None
        if not source:
            self.errors.append(f"stage '{key}': cannot resolve the items source '{items_id}'")
            source = ""
        final = next((o for o in st.get("out") or [] if isinstance(o, dict) and o.get("path")), None)
        final_path = self.out_path(final) if final else str(self.work / key / "result.json")
        ext = Path(final_path).suffix or ".json"
        outdir = str(self.work / key)
        split_req = {"op": "split", "source": source, "batch": st.get("batch"),
                     "outdir": str(self.work / "batches" / key)}
        if st.get("items_glob"):
            split_req["glob"] = st["items_glob"]
        base_task = self.task_expr(key, st, in_wave, extra_lines=(
            ("\nThis is one batch of a larger set. Work only on the items in your batch file; "
             "the output path above is replaced by your batch output path below.\n"),))
        batch_task = (f"{base_task} + \"\\n\\nYour batch file (one item per line): \" + b.file + "
                      f"\"\\nWrite your batch result to: \" + {js(outdir + '/')} + b.index + {js(ext)}")
        child = {"key": f"{js(key + '-')} + b.index", "label": f"{js(st.get('title', key) + ' — batch ')} + b.index",
                 "agent": js(st.get("agent")), "cwd": js(str(self.work)), "task": batch_task,
                 "output": f"{js(outdir + '/')} + b.index + {js(ext)}"}
        if st.get("context"):
            child["context"] = js(st["context"])
        child_js = "{ " + ", ".join(f"{k}: {v}" for k, v in child.items()) + " }"
        batch_outputs = f"S.batches.map(b => {js(outdir + '/')} + b.index + {js(ext)})"
        lines = [
            f"// {pattern} stage {key}",
            f"R[{js(key + '-split')}] = await runs.run({js(key + '-split')}, {{ agent: {js(BATCHER)}, label: {js('Split items: ' + key)}, cwd: {js(str(self.work))}, task: {js('BATCH_TOOL_REQUEST ' + json.dumps(split_req))} }});",
            f"if (failed(R[{js(key + '-split')}])) return stop(\"failed\", [{js(key + '-split')}]);",
            f"S = JSON.parse(R[{js(key + '-split')}].output);",
            f"items = S.batches.map(b => ({child_js}));",
            "for (let i = 0; i < items.length; i += P) {",
            "  const part = await runs.all(items.slice(i, i + P));",
            "  part.forEach((r, j) => { R[items[i + j].key] = r; });",
            "}",
            "bad = items.map(it => it.key).filter(k => failed(R[k]));",
            "if (bad.length) return stop(\"failed\", bad);",
        ]
        if pattern == "map":
            merge_req_expr = (f"\"BATCH_TOOL_REQUEST \" + JSON.stringify({{ op: \"merge\", inputs: {batch_outputs}, "
                              f"output: {js(final_path)} }})")
            lines += [
                f"R[{js(key + '-merge')}] = await runs.run({js(key + '-merge')}, {{ agent: {js(BATCHER)}, label: {js('Merge batches: ' + key)}, cwd: {js(str(self.work))}, task: {merge_req_expr} }});",
                f"if (failed(R[{js(key + '-merge')}])) return stop(\"failed\", [{js(key + '-merge')}]);",
            ]
        else:  # map_reduce
            reduce_task = (f"{self.task_expr(key, st, in_wave)} + \"\\n\\nThis is the reduce step. Combine these batch results into the output above: \" + "
                           f"{batch_outputs}.join(\", \")")
            reduce_item = {"key": js(key + "-reduce"), "label": js(st.get("title", key) + " — reduce"),
                           "agent": js(st.get("agent")), "cwd": js(str(self.work)), "task": reduce_task,
                           "output": js(final_path)}
            lines += [
                f"R[{js(key + '-reduce')}] = await runs.run({js(key + '-reduce')}, {{ {', '.join(f'{k}: {v}' for k, v in reduce_item.items())} }});",
                f"if (failed(R[{js(key + '-reduce')}])) return stop(\"failed\", [{js(key + '-reduce')}]);",
            ]
        return "\n  ".join(lines)

    def review_block(self, key, st):
        agent = (st.get("verify") or {}).get("review")
        outs = [self.out_path(o) for o in st.get("out") or [] if isinstance(o, dict) and o.get("path")]
        check = (st.get("verify") or {}).get("check") or "the output is complete and correct for the stage's purpose"
        used_next = self.used_next_by(key)
        task = (f"Review the output of workflow stage \"{key}\" ({st.get('title', key)}). Read-only: do not edit anything.\n"
                f"Outputs: {', '.join(outs) if outs else 'the stage result below'}\n"
                f"Criterion: {check}\n"
                + ("Used next by:\n" + "".join(used_next) if used_next else "")
                + "Set blocking to true only if the output is wrong or incomplete in a way the next stages would build on.")
        schema = {"type": "object", "required": ["blocking", "findings"],
                  "properties": {"blocking": {"type": "boolean"}, "findings": {"type": "array", "items": {"type": "string"}}}}
        rk = key + "-review"
        task_js = js(task) if outs else f"{js(task + chr(10))} + JSON.stringify(R[{js(key)}].structuredOutput ?? R[{js(key)}].output)"
        return "\n  ".join([
            f"R[{js(rk)}] = await runs.run({js(rk)}, {{ agent: {js(agent)}, label: {js('Review: ' + key)}, cwd: {js(str(self.work))}, context: \"fresh\", task: {task_js}, outputSchema: {js(schema)} }});",
            f"if (failed(R[{js(rk)}])) return stop(\"failed\", [{js(rk)}]);",
            f"if (R[{js(rk)}].structuredOutput && R[{js(rk)}].structuredOutput.blocking) return stop(\"blocked\", [{js(rk)}]);",
        ])

    # --- wave ------------------------------------------------------------------------
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
            if st.get("pattern") in ("lanes", "council"):
                self.errors.append(f"stage '{s}': pattern {st.get('pattern')} is not compiled automatically; run it as described in run-protocol.md")

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
        body = []
        children = []
        in_wave_done = set()
        for layer in layers:
            singles = [s for s in layer if self.stages[s].get("pattern") not in ("map", "map_reduce")]
            maps = [s for s in layer if s not in singles]
            if singles:
                items_js = []
                for s in singles:
                    st = self.stages[s]
                    first_path = next((self.out_path(o) for o in st.get("out") or [] if isinstance(o, dict) and o.get("path")), None)
                    items_js.append(self.item_fields(s, st, in_wave_done, self.task_expr(s, st, in_wave_done), output=first_path))
                    children.append(s)
                body.append(f"// layer: {', '.join(singles)}\n  " + self.run_layer(items_js, singles))
            for s in maps:
                body.append(self.map_block(s, self.stages[s], in_wave_done))
                children += [f"{s}-split", f"{s}-<n>", f"{s}-merge" if self.stages[s].get("pattern") == "map" else f"{s}-reduce"]
            for s in layer:
                if (self.stages[s].get("verify") or {}).get("review"):
                    body.append(self.review_block(s, self.stages[s]))
                    children.append(f"{s}-review")
            in_wave_done |= set(layer)

        preflight["missing_params"] = sorted(self.missing_params)
        preflight["missing_secrets"] = sorted(self.missing_secrets)
        blocked = any(preflight[k] for k in preflight) or self.errors
        if blocked:
            return {"ok": False, "wave": wave.get("id"), "stages": members, "preflight": preflight, "errors": self.errors}

        wid = wave.get("id")
        revision = self.plan.get("revision", 1)
        script = "\n".join([
            f"// Compiled from .pi/plan.yaml revision {revision}, run {self.run_id}, wave {wid}. Do not edit; recompile instead.",
            f"const P = {int(max_parallel)};",
            "const R = {};",
            "let items, bad, S;",
            # a child result has ok, not a status: runs.all returns failed children as {ok: false},
            # runs.run throws (caught below); outcome() is pi-subagents' own naming of the result
            "function failed(r) { return !r || r.ok !== true; }",
            "function outcome(r) { return r.ok ? \"completed\" : r.stopped ? \"stopped\" : r.detached ? \"paused\" : \"failed\"; }",
            "function summary() { const o = {}; for (const k of Object.keys(R)) { const r = R[k] || {}; o[k] = { runId: r.runId ?? null, status: outcome(r), outputReference: r.outputReference ?? null, structuredOutput: r.structuredOutput ?? null }; } return o; }",
            f"function stop(status, keys) {{ return {{ wave: {js(wid)}, revision: {revision}, status: status, at: keys, children: summary() }}; }}",
            "try {",
            "  " + "\n  ".join(body),
            "} catch (e) {",
            "  return { wave: " + js(wid) + ", revision: " + str(revision) + ", status: \"failed\", error: String((e && e.message) || e), children: summary() };",
            "}",
            f"return {{ wave: {js(wid)}, revision: {revision}, status: \"done\", children: summary() }};",
        ])

        call = {"async": True}
        mission_id = next((r.get("mission_id") for r in reversed(self.launches) if r.get("mission_id")), None)
        if mission_id:
            call["missionId"] = mission_id
        else:
            goal = (self.plan.get("goal") or "").strip()
            title = ((goal[:67] + "...") if len(goal) > 70 else goal) or self.workflow.name
            call["mission"] = {"title": f"{title} (run {self.run_id})", "objective": goal}
        call["workflowScript"] = script
        return {"ok": True, "run": self.run_id, "wave": wid, "stages": members, "ends_with": wave.get("ends_with"),
                "revision": revision, "call": call, "children": children,
                "approved": sorted(self.approvals), "notes": self.notes}


def main(
    workflow: Annotated[str, typer.Option(help="workflow root")] = ".",
    approve: Annotated[str, typer.Option(help="side-effect stage keys the user approved, comma-separated")] = "",
    out: Annotated[str | None, typer.Option(help="also write the result JSON to this file")] = None,
    no_commit: Annotated[bool, typer.Option("--no-commit", help="report only (e.g. for a status view): do not commit the plan")] = False,
    check: Annotated[bool, typer.Option("--check", help="planning check: compile every concrete pending wave, ignore status and preflight, report errors only")] = False,
):
    """Compile the next wave into a pi-subagents call."""
    workflow = Path(workflow).resolve()
    plan_path = workflow / ".pi" / "plan.yaml"
    if not plan_path.is_file():
        print(json.dumps({"ok": False, "error": f"{workflow} is not a workflow"}))
        raise typer.Exit(2)
    validator = load_validator()
    try:
        plan = validator.load_yaml(plan_path.read_text(encoding="utf-8")) or {}
    except YAMLError as exc:
        print(json.dumps({"ok": False, "error": f"cannot read plan: {exc}"}))
        raise typer.Exit(2)
    errors, _warnings, summary = validator.validate(plan, None, None, workflow)

    params = workflow_params.load(workflow, plan.get("params") or {})
    act = validator.active_run(workflow)
    if check:
        results = {}
        provisional = set(summary.get("provisional") or [])
        run_dir, state = (act[1], act[2]) if act else (workflow / validator.RUNS / "(next)", {})
        for w in summary.get("waves") or []:
            if any(s in provisional for s in w["stages"]):
                continue
            c = Compiler(workflow, plan, params, run_dir, state, set())
            c.compile(w)
            # a resume across waves cannot be resolved before the earlier wave has run
            results[w["id"]] = [e for e in c.errors if "has no finished run" not in e]
        compile_errors = {k: v for k, v in results.items() if v}
        print(json.dumps({"ok": not errors and not compile_errors, "plan_errors": errors,
                          "compile_errors": compile_errors, "waves_checked": sorted(results)}, indent=2))
        raise typer.Exit(0 if not errors and not compile_errors else 1)
    if plan.get("status") != "ready":
        errors = [f"plan status is {plan.get('status')}, not ready"] + errors
    if errors:
        result = {"ok": False, "preflight": {"plan_errors": errors}}
        print(json.dumps(result, indent=2))
        raise typer.Exit(1)

    if act is None:
        print(json.dumps({"ok": False, "error": "no active run: start one with workflow_new_run (run_log.py new)"}))
        raise typer.Exit(1)
    if summary.get("running"):
        print(json.dumps({"ok": False, "error": f"a wave is running (stages {summary['running']}); check its run before launching again"}))
        raise typer.Exit(1)
    waves = summary.get("waves") or []
    if not waves:
        print(json.dumps({"ok": False, "error": "no wave to run: every stage is done"}))
        raise typer.Exit(1)
    wave = waves[0]
    if not workflow_git.available():
        print(json.dumps({"ok": False, "error": "git is required: pi-subagents checks each writer child's staged index with git, and the workflow's plan is versioned in its own repo. Ask the user to install git."}))
        raise typer.Exit(1)
    try:
        commit, _note = (workflow_git.head(workflow), None) if no_commit else workflow_git.commit(workflow, f"r{plan.get('revision', 1)}: plan for run {act[0]}, wave {wave['id']}")
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": f"cannot commit the plan: {exc}"}))
        raise typer.Exit(1)

    compiler = Compiler(workflow, plan, params, act[1], act[2], {a.strip() for a in approve.split(",") if a.strip()})
    result = compiler.compile(wave)
    result["commit"] = commit
    if result.get("ok") and "workflowScript" in (result.get("call") or {}):
        # a path, not the text: the model would otherwise have to re-emit the whole script
        # as output tokens just to pass it back to the subagent tool (pi-subagents reads
        # workflowScriptPath itself: src/runs/foreground/subagent-executor.js, loadWorkflowScriptPath)
        script_path = Path(act[1]) / ".compiled" / f"{wave['id']}.js"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(result["call"].pop("workflowScript"), encoding="utf-8")
        result["call"]["workflowScriptPath"] = str(script_path)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    for out_path in filter(None, (Path(act[1]) / ".compiled" / f"{wave['id']}.json", Path(out) if out else None)):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    print(text)
    raise typer.Exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    typer.run(main)
