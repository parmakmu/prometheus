#!/usr/bin/env python3
"""An agent's runs side by side, and pruning old ones.

Usage:
  run_history.py show  [--agent DIR] [--last N]
  run_history.py prune [--agent DIR] [--keep N] [--dry-run]

show   per run (newest first): id, status, revision, started_at, cost_usd, and per stage the
       launches that ran it: status (completed | failed | reused | ...), the wave, and the
       launch's cost. A `stages` summary counts, per stage, how often it completed, failed
       or was reused, so a stage that keeps failing (or keeps costing) stands out.
prune  deletes the work/ folders of finished runs beyond the newest N (default 10), and
       pi-subagents' records of their launches, and its mission files once none of their
       launches is kept. run.yaml and .compiled/ stay, so the history stays; an earlier run
       whose work/ is gone is no longer reused from (compile_wave, reuse_earlier). The active
       run is never touched. On a release's folder (prometheus/frozen.py) it prunes its runs the
       same way, with their inputs/ and outputs/ (state.json and events.jsonl stay); a run that
       waits for an answer is not finished.
"""
import re
import shutil
from pathlib import Path
from typing import Annotated

import typer
from runs import list_released_runs, list_runs, read_run
from utils import emit, pi_agent_dir, read_json, refuse, subagents_runs_root

app = typer.Typer(add_completion=False)
AGENT = Annotated[str, typer.Option("--agent", help="agent root")]
RUN_ID_RE = re.compile(r"^[\w-]+$")  # pi-subagents' run ids; the model records them, so nothing like ../..


def agent_root(agent_dir):
    root = Path(agent_dir).resolve()
    if not (root / "plan.yaml").is_file():
        refuse(f"{root} is not an agent", 2)
    return root


def run_entry(rid, d):
    state = read_run(d)
    stages = {}
    for launch in state.get("launches") or []:
        outcome = (read_json(d / ".compiled" / f"{launch.get('wave')}-result.json") or {}).get("children") or {}
        for key in launch.get("stages") or []:
            child = outcome.get(key) or {}
            status = "reused" if child.get("reused") else child.get("status") or launch.get("status")
            stages.setdefault(key, []).append({"wave": launch.get("wave"), "status": status})
    cost = sum(r.get("cost_usd") or 0 for r in state.get("launches") or [])
    return {"id": rid, "status": state.get("status"), "revision": state.get("revision"),
            "started_at": state.get("started_at"), "cost_usd": round(cost, 6), "stages": stages}


@app.command()
def show(agent_dir: AGENT = ".", last: Annotated[int, typer.Option(help="how many runs, newest first")] = 10):
    """Recent runs and how each stage went in them."""
    root = agent_root(agent_dir)
    runs = [run_entry(rid, d) for rid, d in reversed(list_runs(root))][:last]
    summary = {}
    for r in runs:
        for key, launches in r["stages"].items():
            counts = summary.setdefault(key, {})
            for launch in launches:
                counts[launch["status"]] = counts.get(launch["status"], 0) + 1
    emit({"ok": True, "runs": runs, "stages": summary,
          "total_cost_usd": round(sum(r["cost_usd"] for r in runs), 6)})


@app.command()
def prune(agent_dir: AGENT = ".", keep: Annotated[int, typer.Option(help="finished runs to keep whole")] = 10,
          dry_run: Annotated[bool, typer.Option("--dry-run", help="say what would go; delete nothing")] = False):
    """Delete the work/ of old finished runs and pi-subagents' records of their launches."""
    root = agent_root(agent_dir)
    finished = [(rid, d, read_run(d).get("launches") or [], ["work"]) for rid, d in list_runs(root)
                if read_run(d).get("status") in ("done", "stopped")]
    for rid, d in list_released_runs(root):  # a release's runs: state.json (prometheus/frozen.py)
        state = read_json(d / "state.json") or {}
        if state.get("state") in ("complete", "failed", "stopped", "error"):
            finished.append((rid, d, state.get("launches") or [], ["work", "inputs", "outputs"]))
    old = finished[:-keep] if keep > 0 else finished
    records = subagents_runs_root()
    pruned, freed, gone_launches = [], 0, set()
    for rid, d, launches, folders in old:
        ids = [i for r in launches if RUN_ID_RE.match(i := str(r.get("subagent_run_id") or ""))]
        targets = [d / f for f in folders if (d / f).is_dir()] + [records / i for i in ids if (records / i).is_dir()]
        gone_launches.update(ids)
        if not targets:
            continue
        freed += sum(f.stat().st_size for t in targets for f in t.rglob("*") if f.is_file())
        if not dry_run:
            for t in targets:
                shutil.rmtree(t, ignore_errors=True)
        pruned.append(rid)
    missions = prune_missions(gone_launches, {i for _, _, launches, _ in finished[len(old):] for r in launches
                                              if (i := str(r.get("subagent_run_id") or ""))}, dry_run)
    emit({"ok": True, "pruned": pruned, "freed_mb": round(freed / 1e6, 2), "dry_run": dry_run, "missions_removed": missions,
          "kept": [rid for rid, *_ in finished[len(old):]]})


def prune_missions(gone, kept, dry_run):
    """pi-subagents' mission files (one per launch that started a mission) all of whose launches were pruned."""
    removed = 0
    for path in (pi_agent_dir() / "missions" / "projects").glob("*/*.json"):
        runs = {str(r.get("runId")) for r in (read_json(path) or {}).get("runs") or [] if isinstance(r, dict)}
        if runs and runs <= gone and not runs & kept:
            removed += 1
            if not dry_run:
                path.unlink(missing_ok=True)
    return removed


if __name__ == "__main__":
    app()
