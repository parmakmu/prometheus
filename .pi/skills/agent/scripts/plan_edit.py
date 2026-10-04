#!/usr/bin/env python3
"""Change an agent's plan and params through checked operations (`--help` lists the options).

While a run is active the plan changes only here (the agent extension blocks direct edits), and
commits go to the run's branch run/<id>, which the user reviews when the run ends. With no run
active, the plan changes on a change branch (/agent-revise) by direct edits and `critique`.

replan    (an active run, no wave running) applies {stages, remove, findings, questions, answers,
          note}: full stage definitions replace or add stages (one given without `provisional`
          becomes concrete); `remove` drops pending stages; findings and answers fill unknowns and
          questions. Locked: stages done or running in the run, and the request, goal, done_when,
          params, inputs, outputs and agents (a change for /agent-revise); a side effect stays
          one. Bumps the revision, sets draft, clears the critique, commits.
critique  records a plan-critic result {run_id, verdict, passes?, findings} with a digest of the
          plan it is for, marks the plan ready if it validates (needs_answers with open questions),
          commits. The run id must be a completed critic child in pi-subagents' records; refused
          while a wave runs and, once a run exists, on main.
params    sets {name: value} in .env for params the plan declares, except secrets.

Prints one JSON object with the validation result.
"""
import json
import os
from pathlib import Path
from typing import Annotated

import agent_git
import agent_params
import typer
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from runs import active_run, list_runs
from utils import emit, read_yaml, refuse, report_state, subagent_child, write_yaml
from validate_plan import plan_digest, validate

PLAN = Path("plan.yaml")
CRITIC_PASSES = 2  # per replan: a plan fixed after a finding gets a new revision, not a new budget
LOCKED = ("request", "goal", "done_when", "params", "inputs", "outputs", "agents")
REPLAN_KEYS = {"stages", "remove", "findings", "questions", "answers", "note"}
app = typer.Typer(add_completion=False)


def read_json(arg):
    try:
        arg_path = Path(arg)
        return json.loads(arg_path.read_text(encoding="utf-8")) if arg_path.is_file() else json.loads(arg)
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


def commit(message, run):
    """Commit the agent (on the run's branch during a run); (commit, note). A git failure is
    reported, not fatal."""
    try:
        return agent_git.commit(Path.cwd(), message, to=run.branch if run else None)
    except RuntimeError as exc:
        return None, str(exc)


def report(plan):
    errors, warnings, summary = validate(plan, None, None, Path.cwd())
    return {
        "valid": not errors, "status": plan.get("status"), "revision": plan.get("revision", 1),
        "errors": errors, "warnings": warnings, "run": summary.get("run"), "next_wave": summary.get("next_wave"),
        "waves": summary.get("waves"),
        "provisional": summary.get("provisional"), "open_questions": summary.get("open_questions"),
        "missing_params": (summary.get("agent") or {}).get("missing_params", []),
        "missing_secrets": (summary.get("agent") or {}).get("missing_secrets", []),
    }


@app.callback()
def main(agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = "."):
    os.chdir(agent_dir)
    if not PLAN.is_file():
        refuse(f"{Path(agent_dir).resolve()} is not an agent (no plan.yaml)")
    report_state(Path.cwd())


@app.command()
def replan(change: Annotated[str, typer.Option(help="the change as a JSON object, or a file holding it")]):
    """Change pending stages after discovery."""
    plan, c = read_yaml(PLAN, {}), read_json(change)
    if not isinstance(c, dict):
        refuse("the change must be a JSON object")
    extra = sorted(set(c) - REPLAN_KEYS)
    if "waves" in extra:
        refuse("waves are not part of the plan: they follow from the stages' needs, kinds and `pause_after`")
    if extra:
        refuse(f"a replan cannot change {extra}; it changes {sorted(REPLAN_KEYS - {'note'})}. "
               f"{', '.join(LOCKED)} are locked: change them with /agent-revise once no run is active")

    stages = plan.get("stages") or []
    run = active_run(Path.cwd())
    if run is None:
        refuse("no run is active: the replan tools work only during a run. Before the first run (on main) or on a "
               "change branch, edit plan.yaml directly; after a run, start a change with /agent-revise")
    if conflict := agent_git.branch_conflict(Path.cwd(), run.branch):
        refuse(conflict)
    if run.wave_running:
        refuse(f"a wave of run {run.id} is running: replan after agent_log_finish")
    frozen = set(run.state.get("stages") or {})
    by_key = {s.get("key"): s for s in stages}
    unknowns = {u.get("id"): u for u in plan.get("unknowns") or []}
    questions = {q.get("id"): q for q in plan.get("questions") or []}

    problems = []
    for key in c.get("remove") or []:
        if key in frozen:
            problems.append(f"stage '{key}' is done or running in run {run.id}; it is history")
        elif key not in by_key:
            problems.append(f"no stage '{key}' to remove")
        elif by_key[key].get("kind") == "side_effect":
            problems.append(f"stage '{key}' is a side effect the user asked for; removing it is a change for /agent-revise once no run is active")
    for st in c.get("stages") or []:
        key = st.get("key") if isinstance(st, dict) else None
        if not key:
            problems.append("each stage needs a key"); continue
        if key in frozen:
            problems.append(f"stage '{key}' is done or running in run {run.id}; it is history")
        elif "status" in st:
            problems.append(f"stage '{key}': status belongs to the run, not the plan")
        old = by_key.get(key)
        if old is not None and old.get("kind") == "side_effect" and (st.get("kind") != "side_effect" or st.get("approval") != "required"):
            problems.append(f"stage '{key}' is a side effect; it must stay kind: side_effect with approval: required")
    problems += [f"no unknown '{u}'" for u in c.get("findings") or {} if u not in unknowns]
    problems += [f"no question '{q}'" for q in c.get("answers") or {} if q not in questions]
    problems += [f"question '{q.get('id')}' already exists" for q in c.get("questions") or [] if q.get("id") in questions]
    if problems:
        refuse("; ".join(problems))

    rev = plan.get("revision", 1)
    previous = agent_git.head(Path.cwd())

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
    write_yaml(PLAN, plan)
    new, git_note = commit(f"r{rev + 1} draft: replan" + (f": {c['note']}" if c.get("note") else ""), run)
    emit({"ok": True, "previous_commit": previous, "commit": new, "git_note": git_note, "note": c.get("note"), **report(plan)})


@app.command()
def critique(result: Annotated[str, typer.Option(help="the critic's result as JSON {run_id, verdict, passes?, findings}, or a file")]):
    """Record a plan-critic result and mark the plan ready if it validates."""
    plan, c = read_yaml(PLAN, {}), read_json(result)
    if not isinstance(c, dict) or not c.get("run_id") or not c.get("verdict"):
        refuse("the result needs run_id and verdict")
    if not isinstance(c.get("passes", 1), int) or not 1 <= c.get("passes", 1) <= CRITIC_PASSES:
        refuse(f"passes is 1 or 2: at most {CRITIC_PASSES} critic passes per revision, and per replan during a run. "
               "Do not launch another; record the last pass with each remaining finding rejected and a note why")
    run = active_run(Path.cwd())
    if run and (conflict := agent_git.branch_conflict(Path.cwd(), run.branch)):
        refuse(conflict)
    if run and run.wave_running:
        refuse(f"a wave of run {run.id} is running: the critic reviews a replan, which waits for agent_log_finish")
    if run is None and agent_git.branch(Path.cwd()) == agent_git.MAIN and list_runs(Path.cwd()):
        refuse("main changes only by a reviewed merge once a run exists: change the plan with /agent-revise "
               "(a branch), and record the critic there")
    child = subagent_child(c["run_id"])
    if child is None:
        refuse(f"pi-subagents has no record of a critic run {c['run_id']}: record the run id of the plan-critic child "
               "that reviewed this plan (critic-contract.md), after it completed")
    if child.get("state") not in ("completed", "complete") or (child.get("agent") or "plan-critic") != c.get("agent", "plan-critic"):
        refuse(f"run {c['run_id']} is a {child.get('agent')} child in state {child.get('state')}, not a completed "
               f"{c.get('agent', 'plan-critic')} run: record the critic's result only once it has completed")
    if run:  # a replan: count the critic results recorded since the last wave launched
        waves = len(run.state.get("launches") or [])
        seen = [e.get("run_id") for e in run.state.get("critiques") or [] if e.get("after_waves") == waves]
        if c["run_id"] not in seen and len(seen) >= CRITIC_PASSES:
            refuse(f"this replan already had {CRITIC_PASSES} critic passes ({', '.join(seen)}); do not launch another. "
                   f"Record the last one again (run_id {seen[-1]}) with each remaining finding rejected and a note why, "
                   "tell the user about any blocking one, and continue")
        if c["run_id"] not in seen:
            run.state.setdefault("critiques", []).append({"run_id": c["run_id"], "after_waves": waves})
            run.save()
    if run is None and agent_git.is_change_branch(agent_git.branch(Path.cwd()) or ""):
        # a change's first critique of an edited plan makes it a new revision (begin-change leaves the plan as is)
        base = agent_git.main_plan(Path.cwd()) or {}
        if plan.get("revision", 1) == base.get("revision", 1) and plan_digest(plan) != plan_digest(base):
            plan["revision"] = plan.get("revision", 1) + 1
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
    # the plan the critic saw: an edit after this record makes it unready (validate_plan.check_ready)
    plan["critique"]["plan_digest"] = plan_digest(plan)
    write_yaml(PLAN, plan)
    new, git_note = commit(f"r{plan.get('revision', 1)} {plan['status']}: critic {c['verdict']}", run)
    emit({"ok": True, "ready": plan["status"] == "ready", "commit": new, "git_note": git_note, **r})


@app.command()
def params(values: Annotated[str, typer.Option(help="{name: value} as JSON, or a file")]):
    """Set values for params that are not secrets in .env (agent_params.py)."""
    plan, v = read_yaml(PLAN, {}), read_json(values)
    if not isinstance(v, dict):
        refuse("values must be a JSON object {name: value}")
    problems = agent_params.set_values(Path.cwd(), plan.get("params") or {}, v)
    if problems:
        refuse("; ".join(problems))
    emit({"ok": True, **report(plan)})


if __name__ == "__main__":
    app()
