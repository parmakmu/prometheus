"""What Prometheus reads of pi-subagents' own files: its async runs' records (status.json, workflow-children.jsonl), its
missions and its package. The only module that knows where pi-subagents keeps them or what they hold; everything else
reads a launch as run.yaml records it (run_folders.end_launch: status, cost_usd, tokens, children, error), from
outcome() here or, for a released run, from prometheus/launcher.py."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from utils import pi_agent_dir, read_json

STILL_RUNNING = {"queued", "pending", "scheduled", "started", "running"}  # a run's states before it ends
RUN_ID_RE = re.compile(r"^[\w-]+$")  # pi-subagents' run ids; read from files, so nothing like ../..


def runs_root():
    """Where pi-subagents keeps its async runs, one folder per run id (its src/shared/types.js, ASYNC_DIR)."""
    root = os.environ.get("PI_SUBAGENTS_TEMP_ROOT", "").strip()
    if not root:
        scope = f"uid-{os.getuid()}" if hasattr(os, "getuid") else f"user-{os.environ.get('USERNAME', 'shared')}"
        root = Path(tempfile.gettempdir()) / f"pi-subagents-{scope}"
    return Path(root).resolve() / "async-subagent-runs"


def read_status(run_id):
    """The status.json of async run `run_id`, or None."""
    if not RUN_ID_RE.match(str(run_id or "")):
        return None
    status = read_json(runs_root() / str(run_id) / "status.json")
    return status if isinstance(status, dict) else None


def outcome(status):
    """A launch's status.json as Prometheus records it: {state, value (what the wave's script returned), cost_usd,
    tokens, error, failure_kind (why it ended before returning: a script error, a timeout, a validation failure)}."""
    cost, workflow = status.get("totalCost") or {}, status.get("workflow") or {}
    priced = cost.get("costUsd") is not None
    return {"state": status.get("state"), "value": workflow.get("value"),
            "cost_usd": round(float(cost["costUsd"]), 6) if priced else None,
            "tokens": {"input": cost.get("inputTokens"), "output": cost.get("outputTokens")} if priced else None,
            "error": str(status.get("error") or "")[:500] or None, "failure_kind": workflow.get("failureKind")}


def records(newer_than=None):
    """(run id, status.json) of each async run pi-subagents keeps; with `newer_than` (a timestamp),
    only records written since, skipped by their file time without being read."""
    root = runs_root()
    for path in root.glob("*/status.json") if root.is_dir() else []:
        try:
            if newer_than is not None and path.stat().st_mtime < newer_than:
                continue
        except OSError:
            continue
        if isinstance(status := read_json(path), dict):
            yield path.parent.name, status


def unrecorded_launch(script, recorded):
    """(run id, its state) of a launch of the compiled wave `script` (a path) whose id is not in `recorded`, or None.
    pi-subagents keeps the sha256 of each workflow run's script (status.json `workflow.scriptDigest`)."""
    digest = hashlib.sha256(Path(script).read_bytes()).hexdigest()
    # a launch of the script is newer than it: older records are skipped unread
    for rid, st in records(newer_than=Path(script).stat().st_mtime):
        if rid not in recorded and (st.get("workflow") or {}).get("scriptDigest") == digest:
            return st.get("runId") or rid, st.get("state")
    return None


def launch_children(run_id):
    """The children launch `run_id` has started so far: [{key, agent, state, run_id}]; [] when its record is unreadable."""
    children = ((read_status(run_id) or {}).get("workflowChildren") or {}).get("children")
    return [{"key": str(c.get("childId") or ""), "agent": c.get("agent"), "state": c.get("state"), "run_id": c.get("runId")}
            for c in children or [] if isinstance(c, dict)]


def child(run_id):
    """The child entry ({childId, state, runId, agent}) of a pi-subagents child run, from its
    workflow run's status.json `workflowChildren.children`, or None."""
    for _rid, status in records():
        for c in (status.get("workflowChildren") or {}).get("children") or []:
            if isinstance(c, dict) and c.get("runId") == run_id:
                return c
    return None


def launch_records(run_id):
    """What pi-subagents keeps of one launch (`run_id`, a wave's async run), as {"folders", "files"}: its record folder
    and its children's (named by `runId` or as folders), and the files they name (sessions, transcripts, outputs).
    Only paths inside pi-subagents' own temporary folders: they are read from its records, and get deleted (prune)."""
    root, tmp = runs_root(), Path(tempfile.gettempdir()).resolve()

    def ours(text):
        try:
            path = Path(text).resolve()
        except (OSError, ValueError, RuntimeError):
            return None
        inside_tmp = path.is_relative_to(tmp) and len(path.relative_to(tmp).parts) > 1
        if path.is_relative_to(root.parent) or inside_tmp and path.relative_to(tmp).parts[0].startswith("pi-subagent-session-"):
            return path
        return None

    folders, files, todo = [], [], [run_id]

    def visit(value, key=None):
        if isinstance(value, dict):
            for k, v in value.items():
                visit(v, k)
        elif isinstance(value, list):
            for v in value:
                visit(v)
        elif isinstance(value, str):
            if key == "runId":
                todo.append(value)
            elif value.startswith("/") and (path := ours(value)):
                if path.parent == root and path.is_dir():
                    todo.append(path.name)
                elif path.is_file() and not path.is_relative_to(root) and path not in files:
                    files.append(path)

    while todo:
        rid = todo.pop()
        folder = root / rid
        if not RUN_ID_RE.match(rid) or folder in folders or not folder.is_dir():
            continue
        folders.append(folder)
        visit(read_json(folder / "status.json"))
        try:
            lines = (folder / "workflow-children.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                visit(json.loads(line))
            except ValueError:
                continue
    return {"folders": folders, "files": files}


def bundle_name(path: Path) -> str:
    """Where a file pi-subagents named goes in a bundle: under subagents/ (its own folder) or sessions/."""
    root, tmp = runs_root().parent, Path(tempfile.gettempdir()).resolve()
    return f"subagents/{path.relative_to(root)}" if path.is_relative_to(root) else f"sessions/{path.relative_to(tmp)}"


def package(agent_dir):
    """pi-subagents' package.json (its version), project install first, else Pi's; [] when not found."""
    for root in (Path(agent_dir) / ".pi" / "npm", pi_agent_dir() / "npm"):
        if (f := root / "node_modules" / "pi-subagents" / "package.json").is_file():
            return [f]
    return []


def missions():
    """(path, mission) of each pi-subagents mission, kept per project under <Pi's agent dir>/missions/projects/<hash>/
    <mission id>.json, each listing its runs (pi-subagents' missions/store.js)."""
    for path in (pi_agent_dir() / "missions" / "projects").glob("*/*.json"):
        if isinstance(mission := read_json(path), dict):
            yield path, mission


def mission_of(run_id):
    """The id of the mission that lists async run `run_id`, or None."""
    for path, mission in missions():
        if any(isinstance(r, dict) and r.get("runId") == run_id for r in mission.get("runs") or []):
            return mission.get("id") or path.stem
    return None


def prune_missions(gone, kept, dry_run):
    """How many mission files (one per launch that started a mission) all of whose launches were pruned went (or would)."""
    removed = 0
    for path, mission in missions():
        runs = {str(r.get("runId")) for r in mission.get("runs") or [] if isinstance(r, dict)}
        if runs and runs <= gone and not runs & kept:
            removed += 1
            if not dry_run:
                path.unlink(missing_ok=True)
    return removed
