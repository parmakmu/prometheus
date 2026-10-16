#!/usr/bin/env python3
"""Where an agent stands: its state and the moves that are legal from it (docs/state-machine.md).

A state follows from four facts: whether the plan can run (validate_plan.check_ready), the active
run, whether a wave of it is running, and the checked-out branch (with whether any run exists yet).
MOVES lists, per state, the tools and commands the scripts accept; the scripts refuse the rest
(tests/test_state_machine.py walks them). Agent tools put `state()` in their results as `next`;
`agent_check` and /agent-status show it too.

Usage: agent_state.py [--agent DIR]   prints {"ok", "state", "moves", ...}
"""
from pathlib import Path
from typing import Annotated

import agent_git
import typer
from runs import active_run, list_runs
from utils import emit, read_plan, refuse

MOVES = {
    "planning": ["edit plan.yaml", "agent_record_critique", "/agent-revise"],
    "planned": ["agent_new_run", "edit plan.yaml", "agent_record_critique", "/agent-revise"],
    "ready": ["agent_new_run", "/agent-revise", "/agent-improve"],
    "between_waves": ["agent_compile", "agent_log_start", "agent_record_decision", "agent_resolve", "agent_stop_run"],
    "wave_running": ["agent_log_finish"],
    "resolve_draft": ["agent_record_critique", "agent_resolve", "agent_record_decision", "agent_stop_run"],
    "branch_review": ["agent_new_run", "agent_merge", "agent_record_critique"],
    "change_draft": ["agent_new_run", "edit plan.yaml", "agent_record_critique", "agent_merge", "/agent-revise", "/agent-improve"],
    "change_ready": ["agent_new_run", "agent_merge", "edit plan.yaml", "agent_record_critique", "/agent-revise", "/agent-improve"],
    "main_draft": ["/agent-revise", "/agent-improve"],
}
HINTS = {
    "planning": "plan: edit plan.yaml, run the critic, record it",
    "planned": "the plan is ready: start the first run, or keep planning",
    "ready": "start a run, or change the plan on a branch",
    "between_waves": "compile the next wave (it lists what must happen first)",
    "wave_running": "wait for the completion notice, then agent_log_finish",
    "resolve_draft": "run the critic on the resolved stages and record it",
    "branch_review": "review the run's branch with the user: merge, keep or discard",
    "change_draft": "finish the change (edit, critic, record), or keep or discard it",
    "change_ready": "the plan is ready (unchanged, or the critic passed): make the change, or review it with the user: merge, keep or discard",
    "main_draft": "the plan on main is not ready: change it with /agent-revise",
}


def name_state(ready, run, branch, has_runs):
    if run:
        return "wave_running" if run.wave_running else "between_waves" if ready else "resolve_draft"
    if branch.startswith("run/"):
        return "branch_review"
    if agent_git.is_change_branch(branch):
        return "change_ready" if ready else "change_draft"
    if not has_runs:
        return "planned" if ready else "planning"
    return "ready" if ready else "main_draft"


def state(agent_dir):
    """{state, moves, hint, merge?, run?, last_launch?, branch?, off_branch?}: where the agent stands and what may
    happen next; `last_launch` is the active run's latest launch (wave, status, subagent_run_id); `off_branch` says
    that the run's branch isn't checked out, and what to do (agent_git.describe_off_branch)."""
    agent_dir = Path(agent_dir)
    plan = read_plan(agent_dir, {})
    ready = isinstance(plan, dict) and agent_git.why_not_ready(agent_dir, plan) is None
    run = active_run(agent_dir)
    branch = agent_git.branch(agent_dir) or agent_git.MAIN
    name = name_state(ready, run, branch, bool(list_runs(agent_dir)))
    out = {"state": name, "moves": MOVES[name], "hint": HINTS[name]}
    if "agent_merge" in out["moves"]:
        out["merge"] = ["merge", "keep", "discard"] if ready else ["keep", "discard"]
    if run:
        out["run"] = run.id
        launch = (run.state.get("launches") or [None])[-1]
        if isinstance(launch, dict):  # so a new session needs no read of run.yaml to know how the last wave went
            out["last_launch"] = {k: launch.get(k) for k in ("wave", "status", "subagent_run_id")}
    if branch != agent_git.MAIN:
        out["branch"] = branch
    if run and not run.wave_running and (off := agent_git.find_off_branch(agent_dir, run.branch)):
        out["off_branch"] = agent_git.describe_off_branch(off)  # someone checked out another branch mid-run
    return out


def main(agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = "."):
    root = Path(agent_dir)
    if not (root / "plan.yaml").is_file():
        refuse(f"{root.resolve()} is not an agent (no plan.yaml)", 2)
    emit({"ok": True, **state(root)})


if __name__ == "__main__":
    typer.run(main)
