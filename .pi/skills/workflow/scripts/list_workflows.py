#!/usr/bin/env python3
"""List workflows and where each one stands.

Usage: list_workflows.py [--root workflows] [--workflow DIR]

Prints a JSON list, one object per workflow: slug, status, revision, goal,
state (workflow_state's name for where it stands), runs (count),
last_run {id, status, started_at, finished_at, last_wave}, recent_runs (the last 5, newest
first: id, status, started_at, finished_at, note, cost_usd summed over its launches),
active_run, next_wave and stages_done/stages_total (of the active run),
provisional, open_questions, custom_agents, prometheus_version (the skill copy's; stale when
Prometheus here has another), branch (checked out) and unmerged
(run/, revise/ and improve/ branches not merged into main). Waves and stage status come from
validate_plan (waves are derived; status lives in runs/<id>/run.yaml).
Nothing is changed.
"""
import json
from pathlib import Path
from typing import Annotated

import prometheus_version
import typer
import workflow_git
import workflow_state
from runs import last_run, list_runs, read_run
from utils import read_yaml as load
from utils import refuse
from validate_plan import validate


def recent_runs(runs, n=5):
    """The last n runs, newest first, with their cost summed over launches."""
    out = []
    for rid, d in reversed(runs[-n:]):
        s = read_run(d)
        cost = sum(x.get("cost_usd") or 0 for x in s.get("launches") or [] if isinstance(x, dict))
        out.append({"id": rid, "status": s.get("status"), "started_at": s.get("started_at"),
                    "finished_at": s.get("finished_at"), "note": s.get("note"), "cost_usd": round(cost, 6)})
    return out


def summarise(root, slug, current):
    """One workflow's summary; `current` is this Prometheus's version (prometheus_version.current(), the same for all)."""
    plan = load(Path(root) / slug / "plan.yaml", {})
    if not isinstance(plan, dict):
        return {"slug": slug, "error": "plan.yaml is not a mapping"}
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict)]
    summary = validate(plan, None, None, Path(root) / slug)[2]
    active = (summary.get("run") or {}).get("id")
    questions = [q for q in plan.get("questions") or [] if isinstance(q, dict)]
    runs = list_runs(Path(root) / slug)
    last = last_run(Path(root) / slug)
    agents_dir = Path(root) / slug / ".pi" / "agents"
    custom = sorted(p.stem for p in agents_dir.iterdir() if p.suffix == ".md") if agents_dir.is_dir() else []
    goal = plan.get("goal") or ""
    installed = prometheus_version.installed(Path(root) / slug)
    return {
        "slug": slug,
        "status": plan.get("status"),
        "state": workflow_state.state(Path(root) / slug)["state"],
        "revision": plan.get("revision", 1),
        "goal": goal if len(goal) <= 120 else goal[:117] + "...",
        "runs": len(runs),
        "active_run": active,
        "next_wave": summary.get("next_wave") if active else None,
        "stages_done": len(summary.get("done") or []),
        "stages_total": len(stages),
        "provisional": [s.get("key") for s in stages if s.get("provisional") is True],
        "open_questions": [q.get("id") for q in questions if q.get("answer") in (None, "")],
        "last_run": last,
        "recent_runs": recent_runs(runs),
        "custom_agents": [a for a in custom if a not in ("plan-critic", "workflow-batcher")],
        "prometheus_version": installed,
        "stale": bool(current and installed != current),
        "branch": workflow_git.branch(Path(root) / slug),
        "unmerged": workflow_git.unmerged_branches(Path(root) / slug),
    }


def main(
    root: Annotated[str, typer.Option(help="folder that holds workflows")] = "workflows",
    workflow: Annotated[str | None, typer.Option(help="summarise only this workflow folder")] = None,
):
    """List workflows."""
    if workflow:
        b = Path(workflow).resolve()
        if not (b / "plan.yaml").is_file():
            refuse(f"{b} is not a workflow")
        print(json.dumps(summarise(b.parent, b.name, prometheus_version.current()), indent=2, default=str))
        return
    root_path = Path(root)
    current = prometheus_version.current()  # git calls and, when dirty, a hash of the skill: once
    workflows = [
        summarise(root, entry.name, current)
        for entry in (sorted(root_path.iterdir()) if root_path.is_dir() else [])
        if (entry / "plan.yaml").is_file()
    ]
    print(json.dumps(workflows, indent=2, default=str))


if __name__ == "__main__":
    typer.run(main)
