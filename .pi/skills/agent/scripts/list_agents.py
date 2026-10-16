#!/usr/bin/env python3
"""List agents and where each one stands.

Usage: list_agents.py [--root agents] [--agent DIR]

Prints a JSON list, one object per agent: slug, status, revision, goal,
state (agent_state's name for where it stands), runs (count),
last_run {id, status, started_at, finished_at, last_wave}, recent_runs (the last 5, newest
first: id, status, started_at, finished_at, note, cost_usd: its subagents' cost summed over its launches,
agent_cost_usd: the agent's own model's, from its sessions, session_digest.agent_costs),
active_run, next_wave (its id) and next_stages (its stage ids), waiting (what the active run is paused on after its last
wave: approval | review | resolve, else null), stages_done/stages_total (of the active run) and stages
(every plan stage: key, title, status: done | running | null, null when no run is active; children {done, failed, total, items: each child's key, agent, state, run_id}
while its wave runs, from pi-subagents' record of the launch),
provisional, open_questions, custom_subagents, prometheus_version (the skill copy's; stale when
Prometheus here would copy something else, prometheus_version.up_to_date), branch (checked out), unmerged
(run/, revise/ and improve/ branches not merged into main) and off_branch (the active run's branch isn't checked
out: what is, and what to do; else null). Waves and stage status come from
validate_plan (waves are derived; status lives in runs/<id>/run.yaml).
Nothing is changed.
"""
import json
from pathlib import Path
from typing import Annotated

import agent_git
import agent_state
import prometheus_version
import typer
from runs import last_run, list_runs, read_run
from session_digest import agent_costs, default_sessions_dir
from utils import read_json, read_plan, refuse, subagents_runs_root
from validate_plan import validate


def recent_runs(agent_dir, runs, n=5):
    """The last n runs, newest first, with their subagents' cost (summed over launches) and the agent's own."""
    states = [(rid, read_run(d)) for rid, d in reversed(runs[-n:])]
    own = agent_costs(default_sessions_dir(agent_dir), since=states[-1][1].get("started_at")) if states else {}
    out = []
    for rid, s in states:
        cost = sum(x.get("cost_usd") or 0 for x in s.get("launches") or [] if isinstance(x, dict))
        out.append({"id": rid, "status": s.get("status"), "started_at": s.get("started_at"),
                    "finished_at": s.get("finished_at"), "note": s.get("note"), "cost_usd": round(cost, 6),
                    "agent_cost_usd": own.get(rid, 0)})
    return out


DONE_STATES = ("complete", "completed", "done", "succeeded")
FAILED_STATES = ("failed", "error", "cancelled", "canceled")


def child_progress(run_state, stage_keys):
    """{stage id: {done, failed, total, items: [{key, agent, state, run_id}]}} for the children the running wave has launched so far, from
    pi-subagents' status.json for that launch. A child belongs to the longest stage id its own key starts
    with (`<stage>`, `<stage>-review`, `<stage>-3`, `<stage>-fix`). Empty when no wave runs or the record is unreadable."""
    launch = next(iter(reversed([x for x in run_state.get("launches") or [] if isinstance(x, dict)])), None)
    if not launch or launch.get("status") != "running" or not launch.get("subagent_run_id"):
        return {}
    status = read_json(subagents_runs_root() / str(launch["subagent_run_id"]) / "status.json")
    children = ((status or {}).get("workflowChildren") or {}).get("children") if isinstance(status, dict) else None
    out = {}
    for c in children or []:
        key = str(c.get("childId") or "") if isinstance(c, dict) else ""
        owner = max((k for k in stage_keys if key == k or key.startswith(f"{k}-")), key=len, default=None)
        if owner is None:
            continue
        p = out.setdefault(owner, {"done": 0, "failed": 0, "total": 0, "items": []})
        p["total"] += 1
        p["items"].append({"key": key, "agent": c.get("agent"), "state": c.get("state"), "run_id": c.get("runId")})
        p["done"] += c.get("state") in DONE_STATES
        p["failed"] += c.get("state") in FAILED_STATES
    return out


WAITS = {"approval": "approval", "review": "review", "resolve": "resolve"}  # a wave's ends_with → what the run waits for


def waiting_for(run_state, next_wave):
    """What an active run is paused on after its last wave finished: approval | review | resolve, else None."""
    last = next(iter(reversed([x for x in run_state.get("launches") or [] if isinstance(x, dict)])), None)
    if not next_wave or not last or last.get("status") != "done":
        return None
    return WAITS.get(last.get("ends_with"))


def summarise(root, slug, current):
    """One agent's summary; `current` is this Prometheus's version (prometheus_version.current(), the same for all)."""
    plan = read_plan(Path(root) / slug, {})
    if not isinstance(plan, dict):
        return {"slug": slug, "error": "plan.yaml is not a mapping"}
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict)]
    summary = validate(plan, None, None, Path(root) / slug)[2]
    active = (summary.get("run") or {}).get("id")
    questions = [q for q in plan.get("questions") or [] if isinstance(q, dict)]
    runs = list_runs(Path(root) / slug)
    run_dir = dict(runs).get(active)
    run_state = read_run(run_dir) if run_dir else {}
    stage_status = run_state.get("stages") or {}
    progress = child_progress(run_state, [s.get("id") for s in stages])
    last = last_run(Path(root) / slug)
    subagents_dir = Path(root) / slug / ".pi" / "agents"
    custom = sorted(p.stem for p in subagents_dir.iterdir() if p.suffix == ".md") if subagents_dir.is_dir() else []
    goal = plan.get("goal") or ""
    installed = prometheus_version.installed(Path(root) / slug)
    where = agent_state.state(Path(root) / slug)
    return {
        "slug": slug,
        "status": plan.get("status"),
        "state": where["state"],
        "revision": plan.get("revision", 1),
        "goal": goal if len(goal) <= 120 else goal[:117] + "...",
        "runs": len(runs),
        "active_run": active,
        "next_wave": summary.get("next_wave") if active else None,
        "next_stages": next((w.get("stages") for w in summary.get("waves") or [] if w.get("id") == summary.get("next_wave")), []) if active else [],
        "waiting": waiting_for(run_state, summary.get("next_wave")) if active else None,
        "stages_done": len(summary.get("done") or []),
        "stages_total": len(stages),
        "stages": [{"key": s.get("id"), "title": s.get("title"), "status": stage_status.get(s.get("id")),
                    **({"children": progress[s.get("id")]} if s.get("id") in progress else {})} for s in stages],
        "provisional": [s.get("id") for s in stages if s.get("resolves_after")],
        "open_questions": [q.get("id") for q in questions if q.get("answer") in (None, "")],
        "last_run": last,
        "recent_runs": recent_runs(Path(root) / slug, runs),
        "custom_subagents": [a for a in custom if a not in ("plan-critic", "batcher")],
        "prometheus_version": installed,
        "stale": bool(current) and not prometheus_version.up_to_date(installed, current),
        "branch": agent_git.branch(Path(root) / slug),
        "unmerged": agent_git.unmerged_branches(Path(root) / slug),
        "off_branch": where.get("off_branch"),
    }


def main(
    root: Annotated[str, typer.Option(help="folder that holds agents")] = "agents",
    agent_dir: Annotated[str | None, typer.Option("--agent", help="summarise only this agent folder")] = None,
):
    """List agents."""
    if agent_dir:
        b = Path(agent_dir).resolve()
        if not (b / "plan.yaml").is_file():
            refuse(f"{b} is not an agent")
        print(json.dumps(summarise(b.parent, b.name, prometheus_version.current()), indent=2, default=str))
        return
    root_path = Path(root)
    current = prometheus_version.current()  # git calls and a hash of the skill and templates: once
    agent_dirs = [
        summarise(root, entry.name, current)
        for entry in (sorted(root_path.iterdir()) if root_path.is_dir() else [])
        if (entry / "plan.yaml").is_file()
    ]
    print(json.dumps(agent_dirs, indent=2, default=str))


if __name__ == "__main__":
    typer.run(main)
