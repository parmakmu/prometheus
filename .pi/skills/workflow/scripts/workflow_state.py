#!/usr/bin/env python3
"""Where a workflow stands: its state and the moves that are legal from it (docs/state-machine.md).

A state follows from four facts: whether the plan can run (validate_plan.check_ready), the active
run, whether a wave of it is running, and the checked-out branch (with whether any run exists yet).
MOVES lists, per state, the tools and commands the scripts accept; the scripts refuse the rest
(tests/test_state_machine.py walks them). Workflow tools put `state()` in their results as `next`;
`workflow_check` and /workflow-status show it too.

Usage: workflow_state.py [--workflow DIR]   prints {"ok", "state", "moves", ...}
"""
from pathlib import Path
from typing import Annotated

import typer
import workflow_git
from runs import active_run, list_runs
from utils import emit, read_yaml, refuse

MOVES = {
    "planning": ["edit plan.yaml", "workflow_record_critique", "/workflow-revise"],
    "planned": ["workflow_new_run", "edit plan.yaml", "workflow_record_critique", "/workflow-revise"],
    "ready": ["workflow_new_run", "/workflow-revise", "/workflow-improve"],
    "between_waves": ["workflow_compile", "workflow_log_start", "workflow_record_decision", "workflow_replan", "workflow_stop_run"],
    "wave_running": ["workflow_log_finish"],
    "replan_draft": ["workflow_record_critique", "workflow_replan", "workflow_record_decision", "workflow_stop_run"],
    "branch_review": ["workflow_new_run", "workflow_merge", "workflow_record_critique"],
    "change_draft": ["workflow_new_run", "edit plan.yaml", "workflow_record_critique", "workflow_merge", "/workflow-revise", "/workflow-improve"],
    "change_ready": ["workflow_new_run", "workflow_merge", "edit plan.yaml", "workflow_record_critique", "/workflow-revise", "/workflow-improve"],
    "main_draft": ["/workflow-revise", "/workflow-improve"],
}
HINTS = {
    "planning": "plan: edit plan.yaml, run the critic, record it",
    "planned": "the plan is ready: start the first run, or keep planning",
    "ready": "start a run, or change the plan on a branch",
    "between_waves": "compile the next wave (it lists what must happen first)",
    "wave_running": "wait for the completion notice, then workflow_log_finish",
    "replan_draft": "run the critic on the replan and record it",
    "branch_review": "review the run's branch with the user: merge, keep or discard",
    "change_draft": "finish the change (edit, critic, record), or keep or discard it",
    "change_ready": "the plan is ready (unchanged, or the critic passed): make the change, or review it with the user: merge, keep or discard",
    "main_draft": "the plan on main is not ready: change it with /workflow-revise",
}


def name_state(ready, run, branch, has_runs):
    if run:
        return "wave_running" if run.wave_running else "between_waves" if ready else "replan_draft"
    if branch.startswith("run/"):
        return "branch_review"
    if workflow_git.is_change_branch(branch):
        return "change_ready" if ready else "change_draft"
    if not has_runs:
        return "planned" if ready else "planning"
    return "ready" if ready else "main_draft"


def state(workflow):
    """{state, moves, hint, merge?, run?, last_launch?, branch?}: where the workflow stands and what may
    happen next; `last_launch` is the active run's latest launch (wave, status, subagent_run_id)."""
    workflow = Path(workflow)
    plan = read_yaml(workflow / "plan.yaml", {})
    ready = isinstance(plan, dict) and workflow_git.why_not_ready(workflow, plan) is None
    run = active_run(workflow)
    branch = workflow_git.branch(workflow) or workflow_git.MAIN
    name = name_state(ready, run, branch, bool(list_runs(workflow)))
    out = {"state": name, "moves": MOVES[name], "hint": HINTS[name]}
    if "workflow_merge" in out["moves"]:
        out["merge"] = ["merge", "keep", "discard"] if ready else ["keep", "discard"]
    if run:
        out["run"] = run.id
        launch = (run.state.get("launches") or [None])[-1]
        if isinstance(launch, dict):  # so a new session needs no read of run.yaml to know how the last wave went
            out["last_launch"] = {k: launch.get(k) for k in ("wave", "status", "subagent_run_id")}
    if branch != workflow_git.MAIN:
        out["branch"] = branch
    return out


def main(workflow: Annotated[str, typer.Option(help="workflow root")] = "."):
    root = Path(workflow)
    if not (root / "plan.yaml").is_file():
        refuse(f"{root.resolve()} is not a workflow (no plan.yaml)", 2)
    emit({"ok": True, **state(root)})


if __name__ == "__main__":
    typer.run(main)
