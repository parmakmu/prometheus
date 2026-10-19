#!/usr/bin/env python3
"""Change an agent's plan and params through checked operations (`--help` lists the options).

While a run is active the plan changes only here (the agent extension blocks direct edits), and
commits go to the run's branch run/<id>, which the user reviews when the run ends. With no run
active, the plan changes on a change branch (/agent-revise) by direct edits and `critique`.

resolve    (an active run, no wave running) applies {stages, remove, findings, questions, answers,
          note}: full stage definitions replace or add stages (one given without
          `resolves_after` becomes concrete); `remove` drops pending stages; findings and answers fill unknowns and
          questions. Locked: stages done or running in the run, and the request, goal, done_when,
          params, inputs, outputs and agents (a change for /agent-revise); a side effect stays
          one. Bumps the revision, sets draft, clears the critique, commits.
critique  records a plan-critic result {run_id, verdict, passes?, findings} with a digest of the
          plan it is for, marks the plan ready if it validates (needs_answers with open questions),
          commits. The run id must be a completed critic child in pi-subagents' records; refused
          while a wave runs and, once a run exists, on main.
params    sets {name: value} in .env for params the plan declares, except secrets.
card      what a caller of the agent's API needs (the web app's run API): what it does, the params it
          takes, its outputs, and where a run may stop to ask (approvals, reviews).
network-show  the hosts the plan asks to reach (`network:`), for the web app to offer you to allow.
params-show   the plan's params and their values for a form (the web app): a secret only as set or not.
begin-change  (no active run) opens or continues a change branch, revise/<date>-<n> or improve/<date>-<n>
          (--kind improve; needs a run first); before the first run, planning edits main directly.
review    the checked-out branch against main: its commits and files, and the branches still unreviewed.
merge     ends the review (--action merge | keep | discard): merge squashes a ready, valid plan's branch into one
          commit on main; keep switches back to main and leaves the branch; discard deletes it. The git work is
          agent_repo.py's (a library below the state machine); these commands check agent_state.py first.

Prints one JSON object (resolve, critique and params: with the validation result).
"""
import datetime
import json
import re
from pathlib import Path
from typing import Annotated

import agent_params
import agent_repo
import agent_state
import pi_records
import typer
from plan_validate import plan_digest, validate
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from run_folders import active_run
from utils import (
    PLAN_FILE,
    emit,
    read_plan,
    refuse,
    stage_kind,
    write_plan,
)

CRITIC_PASSES = 2  # each time a run resolves stages: a plan fixed after a finding gets a new revision, not a new budget
LOCKED = ("request", "goal", "done_when", "params", "inputs", "outputs", "agents")
RESOLVE_KEYS = {"stages", "remove", "findings", "questions", "answers", "note"}
app = typer.Typer(add_completion=False)


def json_arg(arg):
    """A JSON value given as a file path or as the text itself (refused when it is neither)."""
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


def back_to_run_branch(agent_dir, run):
    """Make sure an active run's own branch is checked out before its plan is read: someone may have switched
    branches. Not while a wave runs (the call is refused then anyway)."""
    if run is None or run.wave_running:
        return
    try:
        agent_repo.back_to_run_branch(agent_dir, run.branch)
    except RuntimeError as exc:
        refuse(f"run {run.id}: {exc}. Tell the user; do not change branches or files yourself")


def commit(agent_dir, message, run):
    """Commit the agent (on the run's branch during a run); (commit, note). A git failure is
    reported, not fatal."""
    try:
        return agent_repo.commit(agent_dir, message, to=run.branch if run else None)
    except RuntimeError as exc:
        return None, str(exc)


def report(agent_dir, plan):
    errors, warnings, summary = validate(plan, None, None, agent_dir)
    return {
        "valid": not errors, "status": plan.get("status"), "revision": plan.get("revision", 1),
        "errors": errors, "warnings": warnings, "run": summary.get("run"), "next_wave": summary.get("next_wave"),
        "waves": summary.get("waves"),
        "provisional": summary.get("provisional"), "open_questions": summary.get("open_questions"),
        "missing_params": (summary.get("agent") or {}).get("missing_params", []),
        "missing_secrets": (summary.get("agent") or {}).get("missing_secrets", []),
    }


@app.callback()
def main(ctx: typer.Context, agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = "."):
    ctx.obj = agent_dir = Path(agent_dir).resolve()  # the agent every command works on: passed on, never the current folder
    if not (agent_dir / PLAN_FILE).is_file():
        refuse(f"{agent_dir} is not an agent (no plan.yaml)")
    agent_state.report_state(agent_dir)


@app.command()
def resolve(ctx: typer.Context, change: Annotated[str, typer.Option(help="the change as a JSON object, or a file holding it")]):
    """Resolve provisional stages after their discovery, or change pending ones the user asked about."""
    agent_dir = ctx.obj
    agent_state.require(agent_dir, agent_state.resolve)
    back_to_run_branch(agent_dir, active_run(agent_dir))  # before the plan is read
    plan, c = read_plan(agent_dir, {}), json_arg(change)
    if not isinstance(c, dict):
        refuse("the change must be a JSON object")
    extra = sorted(set(c) - RESOLVE_KEYS)
    if "waves" in extra:
        refuse("waves are not part of the plan: they follow from the stages' `after`, kinds and `pauses_after`")
    if extra:
        refuse(f"resolving cannot change {extra}; it changes {sorted(RESOLVE_KEYS - {'note'})}. "
               f"{', '.join(LOCKED)} are locked: change them with /agent-revise once no run is active")

    stages = plan.get("stages") or []
    run = active_run(agent_dir)
    frozen = set(run.state.get("stages") or {})
    by_key = {s.get("id"): s for s in stages}
    unknowns = {u.get("id"): u for u in plan.get("unknowns") or []}
    questions = {q.get("id"): q for q in plan.get("questions") or []}

    problems = []
    for key in c.get("remove") or []:
        if key in frozen:
            problems.append(f"stage '{key}' is done or running in run {run.id}; it is history")
        elif key not in by_key:
            problems.append(f"no stage '{key}' to remove")
        elif stage_kind(by_key[key]) == "side_effect":
            problems.append(f"stage '{key}' is a side effect the user asked for; removing it is a change for /agent-revise once no run is active")
    for st in c.get("stages") or []:
        key = st.get("id") if isinstance(st, dict) else None
        if not key:
            problems.append("each stage needs an id"); continue
        if key in frozen:
            problems.append(f"stage '{key}' is done or running in run {run.id}; it is history")
        elif "status" in st:
            problems.append(f"stage '{key}': status belongs to the run, not the plan")
        old = by_key.get(key)
        if old is not None and stage_kind(old) == "side_effect" and stage_kind(st) != "side_effect":
            problems.append(f"stage '{key}' is a side effect; its kind must stay side_effect")
    problems += [f"no unknown '{u}'" for u in c.get("findings") or {} if u not in unknowns]
    problems += [f"no question '{q}'" for q in c.get("answers") or {} if q not in questions]
    problems += [f"question '{q.get('id')}' already exists" for q in c.get("questions") or [] if q.get("id") in questions]
    if problems:
        refuse("; ".join(problems))

    rev = plan.get("revision", 1)
    previous = agent_repo.head(agent_dir)

    for key in c.get("remove") or []:
        stages.remove(by_key.pop(key))
    for st in map(styled, c.get("stages") or []):
        if st["id"] in by_key:
            stages[stages.index(by_key[st["id"]])] = st
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
    write_plan(agent_dir, plan)
    new, git_note = commit(agent_dir, f"r{rev + 1} draft: resolve" + (f": {c['note']}" if c.get("note") else ""), run)
    emit({"ok": True, "previous_commit": previous, "commit": new, "git_note": git_note, "note": c.get("note"), **report(agent_dir, plan)})


@app.command()
def critique(ctx: typer.Context, result: Annotated[str, typer.Option(help="the critic's result as JSON {run_id, verdict, passes?, findings}, or a file")]):
    """Record a plan-critic result and mark the plan ready if it validates."""
    agent_dir = ctx.obj
    agent_state.require(agent_dir, agent_state.record_critique)
    back_to_run_branch(agent_dir, active_run(agent_dir))  # before the plan is read
    plan, c = read_plan(agent_dir, {}), json_arg(result)
    if not isinstance(c, dict) or not c.get("run_id") or not c.get("verdict"):
        refuse("the result needs run_id and verdict")
    if not isinstance(c.get("passes", 1), int) or not 1 <= c.get("passes", 1) <= CRITIC_PASSES:
        refuse(f"passes is 1 or 2: at most {CRITIC_PASSES} critic passes per revision, and each time a run resolves stages. "
               "Do not launch another; record the last pass with each remaining finding rejected and a note why")
    run = active_run(agent_dir)
    child = pi_records.child(c["run_id"])
    if child is None:
        refuse(f"pi-subagents has no record of a critic run {c['run_id']}: record the run id of the plan-critic child "
               "that reviewed this plan (critic-contract.md), after it completed")
    if child.get("state") not in ("completed", "complete") or (child.get("agent") or "plan-critic") != c.get("agent", "plan-critic"):
        refuse(f"run {c['run_id']} is a {child.get('agent')} child in state {child.get('state')}, not a completed "
               f"{c.get('agent', 'plan-critic')} run: record the critic's result only once it has completed")
    if run:  # resolving: count the critic results recorded since the last wave launched
        waves = len(run.state.get("launches") or [])
        seen = [e.get("run_id") for e in run.state.get("critiques") or [] if e.get("after_waves") == waves]
        if c["run_id"] not in seen and len(seen) >= CRITIC_PASSES:
            refuse(f"resolving these stages already had {CRITIC_PASSES} critic passes ({', '.join(seen)}); do not launch another. "
                   f"Record the last one again (run_id {seen[-1]}) with each remaining finding rejected and a note why, "
                   "tell the user about any blocking one, and continue")
        if c["run_id"] not in seen:
            run.state.setdefault("critiques", []).append({"run_id": c["run_id"], "after_waves": waves})
            run.save()
    if run is None and agent_repo.is_change_branch(agent_repo.branch(agent_dir) or ""):
        # a change's first critique of an edited plan makes it a new revision (begin-change leaves the plan as is)
        base = agent_repo.main_plan(agent_dir) or {}
        if plan.get("revision", 1) == base.get("revision", 1) and plan_digest(plan) != plan_digest(base):
            plan["revision"] = plan.get("revision", 1) + 1
    plan["critique"] = {
        "agent": c.get("agent", "plan-critic"),
        "run_id": c["run_id"],
        "revision": plan.get("revision", 1),
        "passes": c.get("passes", 1),
        "verdict": c["verdict"],
        "findings": styled(c.get("findings") or []),
        # the plan the critic saw (status, revision and critique aside): an edit after this record makes it
        # unready (plan_validate.check_ready), and only this command writes it
        "plan_digest": plan_digest(plan),
    }
    plan["status"] = "ready"
    r = report(agent_dir, plan)
    if not r["valid"]:
        plan["status"] = "needs_answers" if r["open_questions"] else "draft"
        r = report(agent_dir, plan)
    write_plan(agent_dir, plan)
    new, git_note = commit(agent_dir, f"r{plan.get('revision', 1)} {plan['status']}: critic {c['verdict']}", run)
    emit({"ok": True, "ready": plan["status"] == "ready", "commit": new, "git_note": git_note, **r})


@app.command()
def params(ctx: typer.Context, values: Annotated[str, typer.Option(help="{name: value} as JSON, or a file")]):
    """Set values for params that are not secrets in .env (agent_params.py)."""
    agent_dir = ctx.obj
    plan, v = read_plan(agent_dir, {}), json_arg(values)
    if not isinstance(v, dict):
        refuse("values must be a JSON object {name: value}")
    problems = agent_params.set_values(agent_dir, plan.get("params") or {}, v)
    if problems:
        refuse("; ".join(problems))
    emit({"ok": True, **report(agent_dir, plan)})



@app.command("network-show")
def network_show(ctx: typer.Context):
    """The hosts the plan asks to reach (`network:`), for the web app to offer you to allow."""
    agent_dir = ctx.obj
    hosts = read_plan(agent_dir, {}).get("network") or []
    emit({"ok": True, "requested": [str(h) for h in hosts if isinstance(h, str)]})


@app.command("params-show")
def params_show(ctx: typer.Context):
    """The plan's params and their values for a form (the web app): a secret only as set or not."""
    agent_dir = ctx.obj
    specs = read_plan(agent_dir, {}).get("params") or {}
    values = agent_params.load(agent_dir, specs)
    emit({"ok": True, "params": [{
        "name": n, "description": agent_params.spec_of(s).get("description"), "type": agent_params.kind(s),
        "required": agent_params.spec_of(s).get("required", True), "env": agent_params.env_name(n, s),
        "set": values.get(n) is not None,
        "value": None if agent_params.kind(s) == "secret" or values.get(n) is None else str(values[n]),
    } for n, s in specs.items()]})


@app.command()
def card(ctx: typer.Context):
    """The agent as a caller sees it: no values (a secret, or a path on this machine, stays here)."""
    agent_dir = ctx.obj
    plan = read_plan(agent_dir, {})
    specs = plan.get("params") or {}
    values = agent_params.load(agent_dir, specs)
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict)]
    emit({"ok": True, "ready": plan.get("status") == "ready", "request": plan.get("request"), "goal": plan.get("goal"),
          "done_when": plan.get("done_when") or [],
          "params": [{"name": n, "type": agent_params.kind(s), "description": agent_params.spec_of(s).get("description"),
                      "required": agent_params.spec_of(s).get("required", True), "set_here": values.get(n) is not None}
                     for n, s in specs.items()],
          "inputs": [{k: i.get(k) for k in ("id", "kind", "format", "path") if i.get(k) is not None}
                     for i in plan.get("inputs") or [] if isinstance(i, dict)],
          "outputs": [{k: o.get(k) for k in ("id", "kind", "format", "path") if o.get(k) is not None}
                      for o in plan.get("outputs") or [] if isinstance(o, dict)],
          "approvals": [{"stage": s["id"], "title": s.get("title")} for s in stages
                        if stage_kind(s) == "side_effect" or s.get("pattern") == "approval"],
          "reviews": [{"stage": s["id"], "title": s.get("title")} for s in stages if s.get("pauses_after")]})


# --- a change to the plan between runs, and the review of a branch (git: agent_repo.py) ----------------------

def require_git():
    if not agent_repo.available():
        refuse("git is required; ask the user to install it", 2)


@app.command("begin-change")
def begin_change(ctx: typer.Context, kind: Annotated[str, typer.Option(help="revise (/agent-revise) | improve (/agent-improve)")] = "revise"):
    """Start (or continue) a branch for changing the plan: revise/<date>-<n> or improve/<date>-<n>."""
    agent_dir = ctx.obj
    require_git()
    if kind not in agent_repo.CHANGE_KINDS:
        refuse(f"kind must be one of {', '.join(agent_repo.CHANGE_KINDS)}")
    agent_state.require(agent_dir, agent_state.revise if kind == "revise" else agent_state.improve)
    if not agent_repo.has_run(agent_dir):
        emit({"ok": True, "branch": agent_repo.branch(agent_dir), "mode": "planning", "note": "no run yet: planning edits the plan directly"})
    b = agent_repo.branch(agent_dir)
    if agent_repo.is_change_branch(b):
        open_kind = b.split("/")[0]
        if open_kind != kind:
            refuse(f"{b} is an open /agent-{open_kind} change: finish it first (review: merge, keep "
                   f"or discard), then start /agent-{kind}")
        emit({"ok": True, "branch": b, "mode": open_kind, "continued": True})
    today = datetime.datetime.now().astimezone().date().isoformat()  # local date
    taken = [int(m.group(1)) for x in agent_repo.git(agent_dir, "branch", "--list", f"{kind}/{today}-*", "--format=%(refname:short)").stdout.split()
             if (m := re.match(rf"^{kind}/{today}-(\d+)$", x))]
    new = f"{kind}/{today}-{max(taken, default=0) + 1}"
    agent_repo.git(agent_dir, "switch", "-q", "-c", new)  # uncommitted changes come along: main changes only by a merge
    # the plan is left as it is: an edit to it unreadies it (critique.plan_digest) until the critic is
    # recorded, which also raises the revision; a change to gates, scripts or lessons alone needs no critic
    sha, _ = agent_repo.commit(agent_dir, f"{kind} started: changes left uncommitted on main")
    emit({"ok": True, "branch": new, "mode": kind, "revision": agent_repo.plan_revision(agent_dir)[0], "commit": sha})


@app.command()
def review(ctx: typer.Context):
    """Show the checked-out branch against main."""
    require_git()
    emit({"ok": True, "review": agent_repo.review_info(ctx.obj), "unmerged": agent_repo.unmerged_branches(ctx.obj)})


@app.command()
def merge(ctx: typer.Context, action: Annotated[str, typer.Option(help="merge | keep | discard")],
          summary: Annotated[str, typer.Option(help="merge: what the branch changes, in words, for the commit message")] = ""):
    """Finish the review of the checked-out branch and switch back to main."""
    agent_dir = ctx.obj
    require_git()
    if action not in ("merge", "keep", "discard"):
        refuse("action must be merge, keep or discard")
    b = agent_repo.branch(agent_dir)
    agent_state.require(agent_dir, agent_state.merge, action)
    if action == "merge":
        plan = read_plan(agent_dir, {})
        errors = validate(plan, None, None, agent_dir)[0]
        if errors or agent_repo.why_not_ready(agent_dir, plan):
            refuse(f"the plan on {b} is {plan.get('status')}" + (f" with errors {errors[:3]}" if errors else "")
                   + ": only a ready, valid plan can reach main. Finish the change (critic, agent_record_critique), or keep or discard the branch.")
    agent_repo.leave_branch(agent_dir)
    git, main = agent_repo.git, agent_repo.MAIN
    if action == "merge":
        # one commit on main per merged branch (a squash); the branch's commits were its working steps
        steps = [s for s in git(agent_dir, "log", "--reverse", "--format=%s", f"{main}..{b}").stdout.splitlines() if s != agent_repo.AT_REVIEW]
        if git(agent_dir, "merge", "-q", "--squash", b, check=False).returncode != 0:
            git(agent_dir, "reset", "-q", "--merge", check=False)
            git(agent_dir, "switch", "-q", b)
            refuse(f"main has moved and {b} conflicts with it; the branch is checked out again. Resolve by hand, or discard it.")
        if git(agent_dir, "diff", "--cached", "--quiet", check=False).returncode != 0:
            git(agent_dir, *agent_repo.identity(agent_dir), "commit", "-q", "-m", agent_repo.merge_message(agent_dir, b, summary, steps))
        agent_repo.tag_ready(agent_dir)
    if action in ("merge", "discard"):
        git(agent_dir, "branch", "-q", "-D", b)  # -D: a squashed branch is not an ancestor of main
    rev, status = agent_repo.plan_revision(agent_dir)
    emit({"ok": True, "action": action, "branch": b, "main": agent_repo.head(agent_dir), "revision": rev, "status": status,
          "unmerged": agent_repo.unmerged_branches(agent_dir)})


if __name__ == "__main__":
    app()

