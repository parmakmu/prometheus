#!/usr/bin/env python3
"""Start runs and record wave launches and results in a workflow.

Each execution of the plan is a run: a folder runs/<id>/ with its state in
runs/<id>/run.yaml (layout in validate_plan.py, "runs"). The plan itself holds
no run state, so the workflow can run again on new input.

Usage (from the workflow root):
  run_log.py new      [--note TEXT]
  run_log.py stop     [--note TEXT]
  run_log.py start    --wave W --subagent-run-id ID [--mission-id M] [--approved a,b] [--decisions JSON]
  run_log.py finish   --wave W [--status done|failed|blocked|stopped] [--result FILE_OR_JSON] [--note TEXT]
  run_log.py decision --stage KEY --answer JSON

new       starts a run (id <date>-<n>) and makes it the active one. Refuses while
          another run is active, or while a branch other than main is checked
          out (review it first: workflow_git.py merge). Commits the plan on
          main and records the commit; the run's own changes go to run/<id>.
stop      marks the active run stopped (kept in runs/), commits what its waves
          left in the workflow (on run/<id>) and returns its branch for review,
          if it has one, and the call that closes its pi-subagents mission.
start     the wave must be the active run's next wave (waves are derived from
          the stages, see compile_wave.py); sets its stages running and appends
          a launch entry with the stage keys, the wave's ends_with and the
          plan's commit (made by compile_wave.py just before the launch).
finish    updates the wave's running launch. Without --result it reads the
          wave's outcome from pi-subagents (status.json of the launch's async
          run: the workflow script's return value, and the cost), so the model
          passes only the wave. `done` marks its stages done, and the run done
          once every stage is. Any other status puts the stages back to
          pending, so the wave can be fixed and relaunched (a relaunch runs the
          whole wave again). The return value is saved to
          runs/<id>/.compiled/<wave>-result.json and its `children` map is
          stored as run ids per key. Files the wave wrote into the workflow (a
          stage script, say) are committed on run/<id>, before the run can be
          marked done, so they reach the review. Returns the launch's ends_with
          (what to do next) and, once the run is done, its branch for review
          and the call that closes its pi-subagents mission.
decision  writes the user's answer for a decision stage where compile_wave.py
          looks for it (in the active run's work/).

Files are loaded and written in round-trip mode, so comments and formatting
survive. Prints one JSON object.
"""
import datetime
import importlib.util
import io
import json
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
import workflow_git
import workflow_params
from ruamel.yaml.error import YAMLError
from utils import emit
from validate_plan import RUN_ID_RE, RUNS, active_run, list_runs, rt_yaml, validate

HERE = Path(__file__).resolve().parent
PLAN = Path("plan.yaml")
HEADER = "# Run state written by the run protocol (run_log.py). Do not edit by hand.\n"
app = typer.Typer(add_completion=False)


class Status(str, Enum):
    done = "done"
    failed = "failed"
    blocked = "blocked"
    stopped = "stopped"


def now():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def load(path, default):
    """Round-trip load; a repeated key is an error, since this script writes the file back."""
    try:
        data = rt_yaml().load(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return default
    except YAMLError as exc:
        emit({"ok": False, "error": f"cannot read {path}: {exc}"}, 2)
    return default if data is None else data


def save(path, data, header=""):
    buf = io.StringIO()
    rt_yaml().dump(data, buf)
    text = buf.getvalue()
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text if text.startswith("#") else header + text, encoding="utf-8")
    tmp.replace(path)


def open_workflow(need_run=True):
    """(plan, (id, dir, state) of the active run or None)."""
    if not Path(PLAN).is_file():
        emit({"ok": False, "error": "run from the workflow root (no plan.yaml here)"}, 2)
    act = active_run(Path.cwd())
    if need_run and act is None:
        emit({"ok": False, "error": "no active run: start one with workflow_new_run (run_log.py new)"}, 1)
    return load(PLAN, {}), act


def subagents_run_dir(run_id):
    """Where pi-subagents keeps an async run (its src/shared/types.js, ASYNC_DIR)."""
    root = os.environ.get("PI_SUBAGENTS_TEMP_ROOT", "").strip()
    if not root:
        scope = f"uid-{os.getuid()}" if hasattr(os, "getuid") else f"user-{os.environ.get('USERNAME', 'shared')}"
        root = Path(tempfile.gettempdir()) / f"pi-subagents-{scope}"
    return Path(root).resolve() / "async-subagent-runs" / run_id


STILL_RUNNING = {"queued", "pending", "scheduled", "started", "running"}


def subagents_outcome(run_id):
    """(returned value or None, status.json) of a finished async run; exits if it is
    missing or still running."""
    path = subagents_run_dir(run_id) / "status.json"
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        emit({"ok": False, "error": f"cannot read pi-subagents' record of run {run_id} ({path}: {exc}); "
             "pass the workflow's return value with --result and --status"}, 1)
    if st.get("state") in STILL_RUNNING:
        emit({"ok": False, "error": f"run {run_id} is still {st.get('state')} in pi-subagents: wait for its completion notice"}, 1)
    return (st.get("workflow") or {}).get("value"), st


def close_mission(state, rid, status, summary):
    """The subagent call that closes the run's mission (one mission per run)."""
    mid = next((r.get("mission_id") for r in reversed(state.get("launches") or []) if r.get("mission_id")), None)
    return {"action": "mission.close", "missionId": mid, "missionStatus": status,
            "summary": summary} if mid else None


def state_file(run_dir):
    return Path(run_dir) / "run.yaml"


def set_status(state, keys, status):
    """Stage status in the run; pending is the default, so it removes the key."""
    stages = state.setdefault("stages", {})
    for k in keys:
        if status == "pending":
            stages.pop(k, None)
        else:
            stages[k] = status


@app.command()
def new(
    note: Annotated[str | None, typer.Option(help="what this run is for")] = None,
):
    """Start a run."""
    plan, act = open_workflow(need_run=False)
    if act is not None:
        emit({"ok": False, "error": f"run {act[0]} is active: continue it, or stop it first (workflow_stop_run)"}, 1)
    b = workflow_git.branch(Path.cwd())
    if b not in (None, workflow_git.MAIN):
        emit({"ok": False, "error": f"branch {b} is checked out and not reviewed: review it first (workflow_merge: merge, keep or discard)"}, 1)
    today = datetime.datetime.now().astimezone().date().isoformat()  # local date
    # a run/<id> branch outlives its folder (runs/ is not in git): skip its id too
    ids = [rid for rid, _ in list_runs(Path.cwd())] + [b.removeprefix("run/") for b in workflow_git.run_branches(Path.cwd())]
    taken = [int(m.group(2)) for rid in ids if (m := RUN_ID_RE.match(rid)) and m.group(1) == today]
    rid = f"{today}-{max(taken, default=0) + 1}"
    run_dir = Path(RUNS) / rid
    try:
        commit, _note = workflow_git.commit(Path.cwd(), f"r{plan.get('revision', 1)}: plan for run {rid}")
    except RuntimeError as exc:
        emit({"ok": False, "error": f"cannot commit the plan: {exc}"}, 1)
    (run_dir / "work").mkdir(parents=True)
    state = {"id": rid, "status": "active", "revision": plan.get("revision", 1), "commit": commit, "started_at": now()}
    if note:
        state["note"] = note
    state.update({"stages": {}, "launches": []})
    save(state_file(run_dir), state, HEADER)
    emit({"ok": True, "run": rid, "path": str(run_dir.resolve()), "commit": commit})


@app.command()
def stop(note: Annotated[str | None, typer.Option(help="why the run stopped")] = None):
    """Stop the active run; its branch (if any) waits for review."""
    _plan, (rid, run_dir, state) = open_workflow()
    if "running" in (state.get("stages") or {}).values():
        emit({"ok": False, "error": f"a wave of run {rid} is running: let it finish, or stop it in pi-subagents first, then record it with workflow_log_finish"}, 1)
    try:  # on run/<id>, while still active
        _commit, git_note = workflow_git.commit(Path.cwd(), f"run {rid}: files its waves left in the workflow")
    except RuntimeError as exc:
        git_note = f"not committed: {exc}"  # stopping must still work
    state["status"] = "stopped"
    state["finished_at"] = now()
    if note:
        state["note"] = note
    save(state_file(run_dir), state, HEADER)
    emit({"ok": True, "run": rid, "run_status": "stopped", "git_note": git_note, "review": workflow_git.review_info(Path.cwd()),
         "mission_close": close_mission(state, rid, "cancelled", f"Workflow run {rid} stopped" + (f": {note}" if note else ""))})


@app.command()
def start(
    wave: Annotated[str, typer.Option()],
    subagent_run_id: Annotated[str, typer.Option(help="the id the subagent launch returned")],
    mission_id: Annotated[str | None, typer.Option()] = None,
    approved: Annotated[str, typer.Option(help="approved side-effect stage keys, comma-separated")] = "",
    decisions: Annotated[str, typer.Option(help="decision answers as JSON")] = "{}",
):
    """Record a wave launch."""
    plan, (rid, run_dir, state) = open_workflow()
    _errors, _warnings, summary = validate(plan, None, None, Path.cwd(), run_state=state)
    if summary.get("running"):
        emit({"ok": False, "error": f"a wave is running (stages {summary['running']}); finish it first"}, 1)
    nxt = (summary.get("waves") or [None])[0]
    if nxt is None or nxt["id"] != wave:
        emit({"ok": False, "error": f"wave {wave} is not the next wave of run {rid} (next: {nxt['id'] if nxt else 'none'})"}, 1)
    try:
        answers = json.loads(decisions)
    except ValueError as exc:
        emit({"ok": False, "error": f"--decisions is not JSON: {exc}"}, 1)
    entry = {
        "wave": wave,
        "stages": nxt["stages"],
        "revision": plan.get("revision", 1),
        "ends_with": nxt["ends_with"],
        "branch": workflow_git.branch(Path.cwd()),
        "commit": workflow_git.head(Path.cwd()),
        "status": "running",
        "subagent_run_id": subagent_run_id,
        "mission_id": mission_id,
        "started_at": now(),
        "approvals": [a.strip() for a in approved.split(",") if a.strip()],
        "decisions": answers,
        "params": workflow_params.record(plan.get("params"), workflow_params.load(Path.cwd(), plan.get("params"))),
    }
    state.setdefault("launches", []).append(entry)
    set_status(state, nxt["stages"], "running")
    save(state_file(run_dir), state, HEADER)
    emit({"ok": True, "run": rid, "entry": entry})


@app.command()
def finish(
    wave: Annotated[str, typer.Option()],
    status: Annotated[Status | None, typer.Option(help="default: the status the workflow script returned")] = None,
    result: Annotated[str | None, typer.Option(help="the workflow script's return value (a file or a JSON string); default: read from pi-subagents")] = None,
    note: Annotated[str | None, typer.Option()] = None,
):
    """Record a wave result."""
    plan, (rid, run_dir, state) = open_workflow()
    entry = next((r for r in reversed(state.get("launches") or [])
                  if r.get("wave") == wave and r.get("status") == "running"), None)
    if entry is None:
        emit({"ok": False, "error": f"run {rid} has no running launch of wave {wave}"}, 1)
    returned, record = None, {}
    if result:
        try:
            result_path = Path(result)
            returned = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else json.loads(result)
        except ValueError as exc:
            emit({"ok": False, "error": f"--result is not JSON: {exc}"}, 1)
    elif entry.get("subagent_run_id"):
        returned, record = subagents_outcome(entry["subagent_run_id"])
    if status is None and returned is None and record.get("state") in ("failed", "stopped", "paused"):
        # the workflow ended before its script returned (a script error, a timeout, a
        # validation failure): pi-subagents >= 0.73 says which in workflow.failureKind
        status = Status.stopped if record.get("state") == "stopped" else Status.failed
        entry["error"] = str(record.get("error") or "")[:500] or None
        entry["failure_kind"] = (record.get("workflow") or {}).get("failureKind")
    if status is None:
        given = returned.get("status") if isinstance(returned, dict) else None
        if given not in {s.value for s in Status}:
            emit({"ok": False, "error": f"cannot tell how wave {wave} ended (pi-subagents state {record.get('state')!r}, "
                 f"returned status {given!r}); pass --status"}, 1)
        status = Status(given)
    if returned is not None:
        result_dir = Path(run_dir) / ".compiled"
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / f"{wave}-result.json").write_text(json.dumps(returned, indent=2, ensure_ascii=False), encoding="utf-8")
    cost = record.get("totalCost") or {}
    if cost.get("costUsd") is not None:
        entry["cost_usd"] = round(float(cost["costUsd"]), 6)
        entry["tokens"] = {"input": cost.get("inputTokens"), "output": cost.get("outputTokens")}
    entry["status"] = status.value
    entry["finished_at"] = now()
    if isinstance(returned, dict):
        children = returned.get("children") or {}
        entry["children"] = {k: (v or {}).get("runId") for k, v in children.items()}
        if returned.get("at"):
            entry["stopped_at"] = returned["at"]
        if returned.get("error"):
            entry["error"] = returned["error"]
    if note:
        entry["note"] = note
    wave_status = "done" if status is Status.done else "pending"
    set_status(state, entry.get("stages") or [], wave_status)
    save(state_file(run_dir), state, HEADER)
    # files the wave wrote into the workflow go to run/<id> while the run is still active
    try:
        commit, git_note = workflow_git.commit(Path.cwd(), f"run {rid}, wave {wave}: files the wave wrote")
    except RuntimeError as exc:
        commit, git_note = None, f"not committed: {exc}"
    keys = [s.get("key") for s in plan.get("stages") or []]
    done = bool(keys) and all(state["stages"].get(k) == "done" for k in keys)
    if done:
        state["status"] = "done"
        state["finished_at"] = now()
        save(state_file(run_dir), state, HEADER)
    total = sum(r.get("cost_usd") or 0 for r in state.get("launches") or [])
    emit({"ok": True, "run": rid, "run_status": state["status"], "wave_status": wave_status,
         "ends_with": entry.get("ends_with") if status is Status.done else None,
         # a summary only: outputs are files the gates checked; read one only when something needs a look
         "children": {k: {"status": (v or {}).get("status"), "output": (v or {}).get("outputReference")}
                      for k, v in ((returned or {}).get("children") or {}).items()} if isinstance(returned, dict) else None,
         "error": entry.get("error"), "failure_kind": entry.get("failure_kind"),
         "branch": workflow_git.branch(Path.cwd()), "commit": commit, "git_note": git_note,
         "review": workflow_git.review_info(Path.cwd()) if done else None,
         "mission_close": close_mission(state, rid, "completed", f"Workflow run {rid} completed ({len(keys)} stages"
                                        + (f", ${total:.4f} in children" if total else "") + ").") if done else None,
         # the criteria the final report goes through, so the parent need not read the plan for them
         "done_when": plan.get("done_when") if done else None,
         "entry": entry})


@app.command()
def decision(
    stage: Annotated[str, typer.Option()],
    answer: Annotated[str, typer.Option(help="the user's answer as JSON")],
):
    """Write the user's answer where compile_wave.py looks for it."""
    plan, (rid, run_dir, state) = open_workflow()
    st = next((s for s in plan.get("stages") or [] if isinstance(s, dict) and s.get("key") == stage), None)
    if st is None:
        emit({"ok": False, "error": f"no stage {stage}"}, 1)
    if st.get("kind") != "decision" and st.get("pattern") != "approval":
        emit({"ok": False, "error": f"stage {stage} is not a decision stage"}, 1)
    try:
        value = json.loads(answer)
    except ValueError as exc:
        emit({"ok": False, "error": f"--answer is not JSON: {exc}"}, 1)
    spec = importlib.util.spec_from_file_location("compile_wave", HERE / "compile_wave.py")
    cw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cw)
    compiler = cw.Compiler(Path.cwd(), plan, workflow_params.load(Path.cwd(), plan.get("params")), run_dir, state, set())
    path = Path(compiler.decision_path(stage))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stage": stage, "answer": value, "recorded_at": now()}, indent=2, ensure_ascii=False), encoding="utf-8")
    emit({"ok": True, "run": rid, "stage": stage, "path": str(path)})


if __name__ == "__main__":
    app()
