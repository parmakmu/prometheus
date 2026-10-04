#!/usr/bin/env python3
"""Start runs and record wave launches and results in an agent (from the agent root).

Each execution of the plan is a run, runs/<id>/ (layout in runs.py); the plan holds no run state,
so the agent can run again on new input. Commands (`--help` lists their options):

new       starts a run (id <date>-<n>). Refuses while another run is active, a branch other than
          main is checked out, or the plan cannot run (validate_plan.check_ready). Commits the plan:
          before the first run on main (that is planning), after it on run/<id>, since main changes
          only by a reviewed merge.
stop      marks the active run stopped, commits what its waves left on run/<id>, and returns the
          branch for review and the call that closes its pi-subagents mission.
start     records a launch of the active run's next wave (the plan must be ready): its stages run.
          Without --mission-id it looks up the mission that lists the launch (one recorded late).
finish    records how the wave's launch ended, read from pi-subagents' status.json unless
          --result is given. `done` marks its stages done (and the run, after the last wave); any
          other status puts them back to pending for a relaunch. Saves the return value to
          .compiled/<wave>-result.json, commits files the wave wrote on run/<id>, and returns what
          the parent needs next (ends_with, next_wave with what its compile would ask first; at the
          end the review, mission_close and done_when).
decision  writes the user's answer for a decision stage where compile_wave.py looks for it.

Files are round-tripped, so comments and formatting survive. Prints one JSON object.
"""
import datetime
import json
from enum import Enum
from pathlib import Path
from typing import Annotated

import agent_git
import agent_params
import typer
from compile_wave import Compiler
from runs import RUN_ID_RE, RUNS, Run, active_run, list_runs
from utils import (
    emit,
    mission_of,
    process_alive,
    read_json,
    read_yaml,
    refuse,
    report_state,
    subagents_runs_root,
)
from validate_plan import check_ready, validate

PLAN = Path("plan.yaml")
STILL_RUNNING = {"queued", "pending", "scheduled", "started", "running"}
app = typer.Typer(add_completion=False)


class Status(str, Enum):
    done = "done"
    failed = "failed"
    blocked = "blocked"
    stopped = "stopped"


def now():
    return datetime.datetime.now(datetime.UTC).astimezone().isoformat(timespec="seconds")


@app.callback()
def main():
    report_state(Path.cwd())  # every result carries `next` (once there is a plan here)


def open_agent(need_run=True) -> tuple[dict, Run | None]:
    """(plan, the active run or None)."""
    if not PLAN.is_file():
        refuse("run from the agent root (no plan.yaml here)", 2)
    run = active_run(Path.cwd())
    if need_run and run is None:
        refuse("no active run: start one with agent_new_run (run_log.py new)")
    return read_yaml(PLAN, {}), run


def subagents_outcome(run_id):
    """(returned value or None, status.json) of a finished async run; refuses while it is missing or
    still running."""
    path = subagents_runs_root() / run_id / "status.json"
    st = read_json(path)
    if not isinstance(st, dict):
        refuse(f"cannot read pi-subagents' record of run {run_id} ({path}); "
               "pass the agent's return value with --result and --status")
    if st.get("state") in STILL_RUNNING:
        if process_alive(st.get("pid")) is False:
            refuse(f"run {run_id} is still {st.get('state')} in pi-subagents, but its process ({st.get('pid')}) is gone: "
                   "it ended without recording how. Record it with agent_log_finish passing status failed "
                   "(stopped if it was stopped) and result {}")
        refuse(f"run {run_id} is still {st.get('state')} in pi-subagents: wait for its completion notice. Only if you "
               "know it ended (Pi crashed, or it was stopped), record it with agent_log_finish passing status "
               "failed (or stopped) and result {}")
    return (st.get("workflow") or {}).get("value"), st


def close_mission(run, status, summary):
    """The subagent call that closes the run's mission (one mission per run)."""
    mid = next((r.get("mission_id") for r in reversed(run.state.get("launches") or []) if r.get("mission_id")), None)
    return {"action": "mission.close", "missionId": mid, "missionStatus": status, "summary": summary} if mid else None


def set_status(state, keys, status):
    """Stage status in the run; pending is the default, so it removes the key."""
    stages = state.setdefault("stages", {})
    for k in keys:
        if status == "pending":
            stages.pop(k, None)
        else:
            stages[k] = status


def commit(message, run):
    """Commit the agent on the run's branch; the note, or why it was not committed (never fatal)."""
    try:
        return agent_git.commit(Path.cwd(), message, to=run.branch)[1]
    except RuntimeError as exc:
        return f"not committed: {exc}"


@app.command()
def new(note: Annotated[str | None, typer.Option(help="what this run is for")] = None):
    """Start a run."""
    plan, run = open_agent(need_run=False)
    if run is not None:
        refuse(f"run {run.id} is active: continue it, or stop it first (agent_stop_run)")
    left = agent_git.branch(Path.cwd())
    if agent_git.is_review_branch(left):  # a run starts from main: the branch (a change or a finished run's) stays for review
        if why := check_ready(agent_git.main_plan(Path.cwd()) or {}):
            refuse(f"the plan on main is not ready to run ({why}); {left} is still checked out")
        agent_git.leave_branch(Path.cwd())
        plan, _ = open_agent(need_run=False)
    elif left not in (None, agent_git.MAIN):
        refuse(f"branch {left} is checked out and is not a branch to review: switch to main first")
    if why := check_ready(plan):
        refuse(why)
    today = datetime.datetime.now().astimezone().date().isoformat()  # local date
    runs = list_runs(Path.cwd())
    # a run/<id> branch outlives its folder (runs/ is not in git): skip its id too
    ids = [rid for rid, _ in runs] + [b.removeprefix("run/") for b in agent_git.run_branches(Path.cwd())]
    taken = [int(m.group(2)) for rid in ids if (m := RUN_ID_RE.match(rid)) and m.group(1) == today]
    rid = f"{today}-{max(taken, default=0) + 1}"
    run = Run(rid, Path(RUNS) / rid, {})
    try:  # before the first run, uncommitted work is planning, on main; after it, it is the run's
        commit_sha = agent_git.commit(Path.cwd(), f"r{plan.get('revision', 1)}: plan for run {rid}",
                                         to=run.branch if runs else None)[0]
    except RuntimeError as exc:
        refuse(f"cannot commit the plan: {exc}")
    run.state.update({"id": rid, "status": "active", "revision": plan.get("revision", 1), "commit": commit_sha,
                      "started_at": now(), **({"note": note} if note else {}), "stages": {}, "launches": []})
    (run.dir / "work").mkdir(parents=True)
    run.save()
    emit({"ok": True, "run": rid, "path": str(run.dir.resolve()), "commit": commit_sha,
          **({"left_branch": left, "note": f"switched to main for this run; {left} is kept for review"} if agent_git.is_review_branch(left) else {})})


@app.command()
def stop(note: Annotated[str | None, typer.Option(help="why the run stopped")] = None):
    """Stop the active run; its branch (if any) waits for review."""
    _plan, run = open_agent()
    if run.wave_running:
        refuse(f"a wave of run {run.id} is running: let it finish, or stop it in pi-subagents first, then record it with agent_log_finish")
    git_note = commit(f"run {run.id}: files its waves left in the agent", run)  # stopping must work even if this fails
    run.state.update({"status": "stopped", "finished_at": now(), **({"note": note} if note else {})})
    run.save()
    emit({"ok": True, "run": run.id, "run_status": "stopped", "git_note": git_note, "review": agent_git.review_info(Path.cwd()),
          "mission_close": close_mission(run, "cancelled", f"Agent run {run.id} stopped" + (f": {note}" if note else ""))})


@app.command()
def start(
    wave: Annotated[str, typer.Option()],
    subagent_run_id: Annotated[str, typer.Option(help="the id the subagent launch returned")],
    mission_id: Annotated[str | None, typer.Option()] = None,
    approved: Annotated[str, typer.Option(help="approved side-effect stage keys, comma-separated")] = "",
    decisions: Annotated[str, typer.Option(help="decision answers as JSON")] = "{}",
):
    """Record a wave launch."""
    plan, run = open_agent()
    if run.wave_running:
        refuse(f"a wave of run {run.id} is running; finish it first")
    if why := check_ready(plan):
        refuse(f"{why}. No wave was compiled from this plan, so none can have launched")
    nxt = (validate(plan, None, None, Path.cwd(), run_state=run.state)[2].get("waves") or [None])[0]
    if nxt is None or nxt["id"] != wave:
        refuse(f"wave {wave} is not the next wave of run {run.id} (next: {nxt['id'] if nxt else 'none'})")
    try:
        answers = json.loads(decisions)
    except ValueError as exc:
        refuse(f"--decisions is not JSON: {exc}")
    run.state.setdefault("launches", []).append({
        "wave": wave,
        "stages": nxt["stages"],
        "revision": plan.get("revision", 1),
        "ends_with": nxt["ends_with"],
        "branch": agent_git.branch(Path.cwd()),
        "commit": agent_git.head(Path.cwd()),
        "status": "running",
        "subagent_run_id": subagent_run_id,
        "mission_id": mission_id or mission_of(subagent_run_id),  # a launch recorded late: its mission lists it
        "started_at": now(),
        "approvals": [a.strip() for a in approved.split(",") if a.strip()],
        "decisions": answers,
        "params": agent_params.record(plan.get("params"), agent_params.load(Path.cwd(), plan.get("params"))),
    })
    set_status(run.state, nxt["stages"], "running")
    run.save()
    emit({"ok": True, "run": run.id, "wave": wave})  # the entry is in run.yaml; the model supplied what it holds


def next_preflight(plan, run, wave):
    """What the next wave's compile would ask for first (decisions, approvals, params, secrets), so the
    parent collects it in the same turn instead of compiling once just to hear it."""
    try:
        compiled = Compiler(Path.cwd(), plan, agent_params.load(Path.cwd(), plan.get("params")),
                            run.dir, run.state, set()).compile(wave)
    except (OSError, ValueError, KeyError, TypeError):  # only a hint: the compile itself reports what went wrong
        return None
    return {k: v for k, v in (compiled.get("preflight") or {}).items() if v} or None


@app.command()
def finish(
    wave: Annotated[str, typer.Option()],
    status: Annotated[Status | None, typer.Option(help="default: the status the workflow script returned")] = None,
    result: Annotated[str | None, typer.Option(help="the workflow script's return value (a file or a JSON string); default: read from pi-subagents")] = None,
    note: Annotated[str | None, typer.Option()] = None,
):
    """Record a wave result."""
    plan, run = open_agent()
    state = run.state
    entry = next((r for r in reversed(state.get("launches") or []) if r.get("wave") == wave and r.get("status") == "running"), None)
    if entry is None:
        refuse(f"run {run.id} has no running launch of wave {wave}")
    returned, record = None, {}
    if result:
        try:
            result_path = Path(result)
            returned = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else json.loads(result)
        except ValueError as exc:
            refuse(f"--result is not JSON: {exc}")
    elif entry.get("subagent_run_id"):
        returned, record = subagents_outcome(entry["subagent_run_id"])
    if status is None and returned is None and record.get("state") in ("failed", "stopped", "paused"):
        # the agent ended before its script returned (a script error, a timeout, a
        # validation failure): pi-subagents >= 0.73 says which in workflow.failureKind
        status = Status.stopped if record.get("state") == "stopped" else Status.failed
        entry["error"] = str(record.get("error") or "")[:500] or None
        entry["failure_kind"] = (record.get("workflow") or {}).get("failureKind")
    if status is None:
        given = returned.get("status") if isinstance(returned, dict) else None
        if given not in {s.value for s in Status}:
            refuse(f"cannot tell how wave {wave} ended (pi-subagents state {record.get('state')!r}, returned status {given!r}); pass --status")
        status = Status(given)
    if returned is not None:
        (run.dir / ".compiled").mkdir(parents=True, exist_ok=True)
        (run.dir / ".compiled" / f"{wave}-result.json").write_text(json.dumps(returned, indent=2, ensure_ascii=False), encoding="utf-8")
    cost = record.get("totalCost") or {}
    if cost.get("costUsd") is not None:
        entry["cost_usd"] = round(float(cost["costUsd"]), 6)
        entry["tokens"] = {"input": cost.get("inputTokens"), "output": cost.get("outputTokens")}
    entry["status"] = status.value
    entry["finished_at"] = now()
    if isinstance(returned, dict):
        entry["children"] = {k: (v or {}).get("runId") for k, v in (returned.get("children") or {}).items()}
        if returned.get("at"):
            entry["stopped_at"] = returned["at"]
        if returned.get("error"):
            entry["error"] = returned["error"]
    if note:
        entry["note"] = note
    wave_status = "done" if status is Status.done else "pending"
    set_status(state, entry.get("stages") or [], wave_status)
    run.save()
    git_note = commit(f"run {run.id}, wave {wave}: files the wave wrote", run)  # while the run is still active
    keys = [s.get("key") for s in plan.get("stages") or []]
    done = bool(keys) and all(state["stages"].get(k) == "done" for k in keys)
    if done:
        state.update({"status": "done", "finished_at": now()})
        run.save()
    total = sum(r.get("cost_usd") or 0 for r in state.get("launches") or [])
    after = validate(plan, None, None, Path.cwd(), run_state=state)[2]
    nxt = None if done else (after.get("waves") or [None])[0]  # after a failed wave: that wave again
    if nxt:
        nxt = {k: nxt[k] for k in ("id", "stages", "ends_with")}
        if preflight := next_preflight(plan, run, nxt):
            nxt["preflight"] = preflight
    emit({"ok": True, "run": run.id, "run_status": state["status"], "wave_status": wave_status,
          "ends_with": entry.get("ends_with") if status is Status.done else None,
          # a summary only: outputs are files the gates checked; read one only when something needs a look
          "children": {k: {"status": (v or {}).get("status"), "output": (v or {}).get("outputReference")}
                       for k, v in ((returned or {}).get("children") or {}).items()} if isinstance(returned, dict) else None,
          "error": entry.get("error"), "failure_kind": entry.get("failure_kind"),
          "stopped_at": entry.get("stopped_at"), "git_note": git_note,
          # what to compile next, so the parent goes straight to agent_compile (no check in between)
          "next_wave": nxt,
          "review": agent_git.review_info(Path.cwd()) if done else None,
          "mission_close": close_mission(run, "completed", f"Agent run {run.id} completed ({len(keys)} stages"
                                         + (f", ${total:.4f} in children" if total else "") + ").") if done else None,
          # the criteria the final report goes through, so the parent need not read the plan for them
          "done_when": plan.get("done_when") if done else None})


@app.command()
def decision(
    stage: Annotated[str, typer.Option()],
    answer: Annotated[str, typer.Option(help="the user's answer as JSON")],
):
    """Write the user's answer where compile_wave.py looks for it."""
    plan, run = open_agent()
    if run.wave_running:
        refuse(f"a wave of run {run.id} is running: record decisions for the next wave after agent_log_finish")
    st = next((s for s in plan.get("stages") or [] if isinstance(s, dict) and s.get("key") == stage), None)
    if st is None:
        refuse(f"no stage {stage}")
    if st.get("kind") != "decision" and st.get("pattern") != "approval":
        refuse(f"stage {stage} is not a decision stage")
    try:
        value = json.loads(answer)
    except ValueError as exc:
        refuse(f"--answer is not JSON: {exc}")
    compiler = Compiler(Path.cwd(), plan, agent_params.load(Path.cwd(), plan.get("params")), run.dir, run.state, set())
    path = Path(compiler.decision_path(stage))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stage": stage, "answer": value, "recorded_at": now()}, indent=2, ensure_ascii=False), encoding="utf-8")
    emit({"ok": True, "run": run.id, "stage": stage, "path": str(path)})


if __name__ == "__main__":
    app()
