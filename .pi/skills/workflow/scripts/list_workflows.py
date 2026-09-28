#!/usr/bin/env python3
"""List workflows and where each one stands.

Usage: list_workflows.py [--root workflows] [--workflow DIR]

Prints a JSON list, one object per workflow: slug, status, revision, goal,
runs (count), last_run {id, status, started_at, finished_at, last_wave},
active_run, next_wave and stages_done/stages_total (of the active run),
provisional, open_questions, custom_agents, branch (checked out) and unmerged
(run/ and change/ branches not merged into main). Waves and stage status come from
validate_plan (waves are derived; status lives in runs/<id>/run.yaml).
Nothing is changed.
"""
import json
from pathlib import Path
from typing import Annotated

import typer
import workflow_git
from ruamel.yaml.error import YAMLError
from utils import load_yaml_safe as load
from validate_plan import list_runs, read_run, validate


def summarise(root, slug):
    plan = load(Path(root) / slug / "plan.yaml", {})
    if not isinstance(plan, dict):
        return {"slug": slug, "error": "plan.yaml is not a mapping"}
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict)]
    summary = validate(plan, None, None, Path(root) / slug)[2]
    active = (summary.get("run") or {}).get("id")
    questions = [q for q in plan.get("questions") or [] if isinstance(q, dict)]
    runs = list_runs(Path(root) / slug)
    last = None
    if runs:
        try:
            state = read_run(runs[-1][1])
        except (OSError, YAMLError):
            state = {}
        launch = (state.get("launches") or [None])[-1]
        last = {"id": runs[-1][0], **{k: state.get(k) for k in ("status", "started_at", "finished_at")},
                "last_wave": {k: launch.get(k) for k in ("wave", "status")} if isinstance(launch, dict) else None}
    agents_dir = Path(root) / slug / ".pi" / "agents"
    custom = sorted(p.stem for p in agents_dir.iterdir() if p.suffix == ".md") if agents_dir.is_dir() else []
    goal = plan.get("goal") or ""
    return {
        "slug": slug,
        "status": plan.get("status"),
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
        "custom_agents": [a for a in custom if a not in ("plan-critic", "workflow-batcher")],
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
            print(json.dumps({"error": f"{b} is not a workflow"}))
            raise typer.Exit(1)
        print(json.dumps(summarise(b.parent, b.name), indent=2, default=str))
        return
    root_path = Path(root)
    workflows = [
        summarise(root, entry.name)
        for entry in (sorted(root_path.iterdir()) if root_path.is_dir() else [])
        if (entry / "plan.yaml").is_file()
    ]
    print(json.dumps(workflows, indent=2, default=str))


if __name__ == "__main__":
    typer.run(main)
