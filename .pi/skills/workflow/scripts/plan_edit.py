#!/usr/bin/env python3
"""Change a workflow's plan and params through checked operations.

While a run is active, the run protocol changes .pi/plan.yaml only through
`replan` and `critique`, and the workflow extension blocks direct edits. Commits
go to the run's branch run/<id> (workflow_git.py), which the user reviews and
merges (or not) when the run ends. With no run active, the plan changes
on a change branch (/workflow-revise, workflow_git.py begin-change), by direct
edits and `critique`.

Usage:
  plan_edit.py [--workflow DIR] replan   --change FILE_OR_JSON
  plan_edit.py [--workflow DIR] critique --result FILE_OR_JSON
  plan_edit.py [--workflow DIR] params   --values FILE_OR_JSON

replan    (an active run is required) applies
          {stages, remove, findings, questions, answers, note}:
          - stages: full stage definitions; replaces the stage with the same key
            or adds a new one. A provisional stage becomes concrete when given
            without `provisional`. Waves follow from the stages (compile_wave.py).
          - remove: keys of pending stages to drop.
          - findings: {unknown id: finding}; questions: new questions;
            answers: {question id: answer}.
          Locked: stages that are done or running in the active run, and request, goal,
          done_when, params, inputs, outputs and agents (a change there is a
          change for /workflow-revise once no run is active). A
          side_effect stage stays side_effect with approval: required and
          cannot be removed. Bumps revision, sets status draft, clears the
          critique (re-run the critic) and commits on the run's branch.
critique  records a plan-critic result {run_id, verdict, passes?, findings} for
          the current revision, then marks the plan ready if it validates (or
          needs_answers when questions are open), and commits.
params    sets {name: value} in .env for params the plan declares, except secrets
          (per machine, not versioned; see workflow_params.py).

Prints one JSON object with the validation result.
"""
import io
import json
import os
from typing import Annotated

import typer
import workflow_git
import workflow_params
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from validate_plan import active_run, rt_yaml, validate

PLAN = os.path.join(".pi", "plan.yaml")
LOCKED = ("request", "goal", "done_when", "params", "inputs", "outputs", "agents")
REPLAN_KEYS = {"stages", "remove", "findings", "questions", "answers", "note"}
app = typer.Typer(add_completion=False)


def out(obj, code=0):
    print(json.dumps(obj, indent=2, default=str))
    raise typer.Exit(code)


def refuse(errors):
    out({"ok": False, "errors": errors if isinstance(errors, list) else [errors]}, 1)


def read_json(arg):
    try:
        if os.path.isfile(arg):
            with open(arg, encoding="utf-8") as fh:
                return json.load(fh)
        return json.loads(arg)
    except ValueError as exc:
        refuse(f"not JSON: {exc}")


def styled(value, key=None):
    """JSON from the model in the plans' style: short lists and schemas on one line."""
    scalar = lambda x: not isinstance(x, (dict, list))
    if isinstance(value, dict):
        m = CommentedMap((k, styled(v, k)) for k, v in value.items())
        if key == "schema" and all(scalar(v) for v in value.values()):
            m.fa.set_flow_style()
        return m
    if isinstance(value, list):
        s = CommentedSeq(styled(v) for v in value)
        if value and all(scalar(v) for v in value) and sum(len(str(v)) for v in value) < 80:
            s.fa.set_flow_style()
        return s
    return value


def load(path):
    with open(path, encoding="utf-8") as fh:
        return rt_yaml().load(fh)


def save(path, data):
    buf = io.StringIO()
    rt_yaml().dump(data, buf)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, path)


def commit(message):
    """Commit the workflow (the cwd; on the active run's branch during a run); (commit, note).
    A git failure is reported, not fatal."""
    try:
        return workflow_git.commit(os.getcwd(), message)
    except RuntimeError as exc:
        return None, str(exc)


def report(plan):
    errors, warnings, summary = validate(plan, None, None, os.getcwd())
    return {
        "valid": not errors, "status": plan.get("status"), "revision": plan.get("revision", 1),
        "errors": errors, "warnings": warnings, "run": summary.get("run"), "next_wave": summary.get("next_wave"),
        "waves": summary.get("waves"),
        "provisional": summary.get("provisional"), "open_questions": summary.get("open_questions"),
        "missing_params": (summary.get("workflow") or {}).get("missing_params", []),
        "missing_secrets": (summary.get("workflow") or {}).get("missing_secrets", []),
    }


@app.callback()
def main(workflow: Annotated[str, typer.Option(help="workflow root")] = "."):
    os.chdir(workflow)
    if not os.path.isfile(PLAN):
        refuse(f"{os.path.abspath(workflow)} is not a workflow (no .pi/plan.yaml)")


@app.command()
def replan(change: Annotated[str, typer.Option(help="the change as a JSON object, or a file holding it")]):
    """Change pending stages after discovery."""
    plan, c = load(PLAN), read_json(change)
    if not isinstance(c, dict):
        refuse("the change must be a JSON object")
    extra = sorted(set(c) - REPLAN_KEYS)
    if "waves" in extra:
        refuse("waves are not part of the plan: they follow from the stages' needs, kinds and `pause_after`")
    if extra:
        refuse(f"a replan cannot change {extra}; it changes {sorted(REPLAN_KEYS - {'note'})}. "
               f"{', '.join(LOCKED)} are locked: change them with /workflow-revise once no run is active")

    stages = plan.get("stages") or []
    act = active_run(os.getcwd())
    if act is None:
        refuse("no run is active: change the plan with /workflow-revise (a change branch, reviewed before it reaches main)")
    frozen = set(act[2].get("stages") or {})
    by_key = {s.get("key"): s for s in stages}
    unknowns = {u.get("id"): u for u in plan.get("unknowns") or []}
    questions = {q.get("id"): q for q in plan.get("questions") or []}

    problems = []
    for key in c.get("remove") or []:
        if key in frozen:
            problems.append(f"stage '{key}' is done or running in run {act[0]}; it is history")
        elif key not in by_key:
            problems.append(f"no stage '{key}' to remove")
        elif by_key[key].get("kind") == "side_effect":
            problems.append(f"stage '{key}' is a side effect the user asked for; removing it is a change for /workflow-revise once no run is active")
    for st in c.get("stages") or []:
        key = st.get("key") if isinstance(st, dict) else None
        if not key:
            problems.append("each stage needs a key"); continue
        if key in frozen:
            problems.append(f"stage '{key}' is done or running in run {act[0]}; it is history")
        elif "status" in st:
            problems.append(f"stage '{key}': status belongs to the run, not the plan")
        old = by_key.get(key)
        if old is not None and old.get("kind") == "side_effect" and (st.get("kind") != "side_effect" or st.get("approval") != "required"):
            problems.append(f"stage '{key}' is a side effect; it must stay kind: side_effect with approval: required")
    problems += [f"no unknown '{u}'" for u in c.get("findings") or {} if u not in unknowns]
    problems += [f"no question '{q}'" for q in c.get("answers") or {} if q not in questions]
    problems += [f"question '{q.get('id')}' already exists" for q in c.get("questions") or [] if q.get("id") in questions]
    if problems:
        refuse(problems)

    rev = plan.get("revision", 1)
    previous = workflow_git.head(os.getcwd())

    for key in c.get("remove") or []:
        stages.remove(by_key.pop(key))
    for st in map(styled, c.get("stages") or []):
        if st["key"] in by_key:
            stages[stages.index(by_key[st["key"]])] = st
        else:
            stages.append(st)
    for uid, finding in (c.get("findings") or {}).items():
        unknowns[uid]["finding"] = finding
    if c.get("questions"):
        plan.setdefault("questions", []).extend(map(styled, c["questions"]))
    for qid, answer in (c.get("answers") or {}).items():
        questions[qid]["answer"] = answer
    plan["revision"] = rev + 1
    plan["status"] = "draft"
    plan.pop("critique", None)
    save(PLAN, plan)
    new, git_note = commit(f"r{rev + 1} draft: replan" + (f": {c['note']}" if c.get("note") else ""))
    out({"ok": True, "previous_commit": previous, "commit": new, "git_note": git_note, "note": c.get("note"), **report(plan)})


@app.command()
def critique(result: Annotated[str, typer.Option(help="the critic's result as JSON {run_id, verdict, passes?, findings}, or a file")]):
    """Record a plan-critic result and mark the plan ready if it validates."""
    plan, c = load(PLAN), read_json(result)
    if not isinstance(c, dict) or not c.get("run_id") or not c.get("verdict"):
        refuse("the result needs run_id and verdict")
    plan["critique"] = {
        "agent": c.get("agent", "plan-critic"),
        "run_id": c["run_id"],
        "revision": plan.get("revision", 1),
        "passes": c.get("passes", 1),
        "verdict": c["verdict"],
        "findings": styled(c.get("findings") or []),
    }
    plan["status"] = "ready"
    r = report(plan)
    if not r["valid"]:
        plan["status"] = "needs_answers" if r["open_questions"] else "draft"
        r = report(plan)
    save(PLAN, plan)
    new, git_note = commit(f"r{plan.get('revision', 1)} {plan['status']}: critic {c['verdict']}")
    out({"ok": True, "ready": plan["status"] == "ready", "commit": new, "git_note": git_note, **r})


@app.command()
def params(values: Annotated[str, typer.Option(help="{name: value} as JSON, or a file")]):
    """Set values for params that are not secrets in .env (workflow_params.py)."""
    plan, v = load(PLAN), read_json(values)
    if not isinstance(v, dict):
        refuse("values must be a JSON object {name: value}")
    problems = workflow_params.set_values(os.getcwd(), plan.get("params") or {}, v)
    if problems:
        refuse("; ".join(problems))
    out({"ok": True, **report(plan)})


if __name__ == "__main__":
    app()
