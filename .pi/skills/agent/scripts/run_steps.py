#!/usr/bin/env python3
"""Start runs and record wave launches and results in an agent (`--agent DIR`, default the current folder).

Each execution of the plan is a run, runs/<id>/ (layout in run_folders.py); the plan holds no run state,
so the agent can run again on new input. Commands (`--help` lists their options):

start-run   starts a run (id <date>-<n>) from main. Refuses as agent_state.start_run's guards say: another run
          active, a branch other than main or one to review checked out, or main's plan cannot run.
          Commits the plan: before the first run on main (that is planning), after it on run/<id>,
          since main changes only by a reviewed merge.
stop        marks the active run stopped, commits what its waves left on run/<id>, and returns the
          branch for review and the call that closes its pi-subagents mission.
start-wave  records a launch of the active run's next wave (the plan must be ready): its stages run.
          Without --mission-id it looks up the mission that lists the launch (one recorded late).
finish-wave records how the wave's launch ended, read from pi-subagents' status.json unless
          --result is given. `done` marks its stages done (and the run, after the last wave); any
          other status puts them back to pending for a relaunch. Saves the return value to
          .compiled/<wave>.json (`result`), commits files the wave wrote on run/<id>, and returns what
          the parent needs next (ends_with, next_wave with what its compile would ask first; at the
          end the review, mission_close and done_when).
decision    writes the user's answer for a decision stage where run_compile.py looks for it.

Files are round-tripped, so comments and formatting survive. Prints one JSON object.
"""
import datetime
import json
from enum import Enum
from pathlib import Path
from typing import Annotated

import agent_params
import agent_repo
import agent_state
import pi_records
import typer
from plan_validate import validate
from run_compile import Compiler
from run_folders import (
    RUN_ID_RE,
    RUNS,
    Run,
    active_run,
    end_launch,
    list_runs,
    read_run,
    read_wave,
    record_wave_result,
)
from utils import (
    PLAN_FILE,
    emit,
    process_alive,
    read_plan,
    refuse,
    report_state,
    stage_kind,
)

app = typer.Typer(add_completion=False)


class Status(str, Enum):
    done = "done"
    failed = "failed"
    blocked = "blocked"
    stopped = "stopped"


def now():
    return datetime.datetime.now(datetime.UTC).astimezone().isoformat(timespec="seconds")


@app.callback()
def main(ctx: typer.Context, agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = "."):
    ctx.obj = Path(agent_dir).resolve()  # the agent every command works on: passed on, never the current folder
    report_state(ctx.obj)  # every result carries `next` (once there is a plan here)


def open_agent(agent_dir, need_run=True) -> tuple[dict, Run | None]:
    """(plan, the active run or None)."""
    if not (agent_dir / PLAN_FILE).is_file():
        refuse(f"{agent_dir} is not an agent (no plan.yaml)", 2)
    run = active_run(agent_dir)
    if need_run and run is None:
        refuse("no active run: start one with agent_start_run (run_steps.py start-run)")
    return read_plan(agent_dir, {}), run


def subagents_outcome(run_id):
    """How a finished launch ended (pi_records.outcome); refuses while its record is missing or it still runs."""
    st = pi_records.read_status(run_id)
    if st is None:
        refuse(f"cannot read pi-subagents' record of run {run_id} ({pi_records.runs_root() / run_id}); "
               "pass the agent's return value with --result and --status")
    if st.get("state") in pi_records.STILL_RUNNING:
        started = st["startedAt"] / 1000 if isinstance(st.get("startedAt"), (int, float)) else None  # ms
        if process_alive(st.get("pid"), started) is False:
            refuse(f"run {run_id} is still {st.get('state')} in pi-subagents, but its process ({st.get('pid')}) is gone: "
                   "it ended without recording how. Record it with agent_finish_wave passing status failed "
                   "(stopped if it was stopped) and result {}")
        refuse(f"run {run_id} is still {st.get('state')} in pi-subagents: wait for its completion notice. Only if you "
               "know it ended (Pi crashed, or it was stopped), record it with agent_finish_wave passing status "
               "failed (or stopped) and result {}")
    return pi_records.outcome(st)


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


def commit(agent_dir, message, run):
    """Commit the agent on the run's branch; the note, or why it was not committed (never fatal)."""
    try:
        return agent_repo.commit(agent_dir, message, to=run.branch)[1]
    except RuntimeError as exc:
        return f"not committed: {exc}"


@app.command()
def start_run(
    ctx: typer.Context,
    note: Annotated[str | None, typer.Option(help="what this run is for")] = None,
    params: Annotated[str | None, typer.Option(help="{name: value} for this run only, over .env (not secrets)")] = None,
    approve: Annotated[str, typer.Option(help="side-effect stage ids approved for the whole run, comma-separated")] = "",
    max_cost_usd: Annotated[float | None, typer.Option(help="this run's cost ceiling (USD), instead of the plan's")] = None,
):
    """Start a run. --params, --approve and --max-cost-usd: what a caller (the web app's API) gives up front."""
    agent_dir = ctx.obj
    plan, run = open_agent(agent_dir, need_run=False)
    try:
        given = json.loads(params) if params else {}
    except ValueError as exc:
        refuse(f"--params is not JSON: {exc}")
    if not isinstance(given, dict) or not all(isinstance(v, str | int | float | bool) for v in given.values()):
        refuse("--params must be a JSON object {name: value} of plain values")
    if problems := agent_params.problems_with(plan.get("params"), given):
        refuse("; ".join(problems))
    approved = [a.strip() for a in approve.split(",") if a.strip()]
    if unknown := [a for a in approved if a not in {s.get("id") for s in plan.get("stages") or [] if isinstance(s, dict)}]:
        refuse(f"--approve names no stage: {', '.join(unknown)}")
    agent_state.require(agent_dir, agent_state.start_run)
    left = agent_repo.branch(agent_dir)
    if agent_repo.is_review_branch(left):  # a run starts from main: the branch (a change or a finished run's) stays for review
        agent_repo.leave_branch(agent_dir)
        plan, _ = open_agent(agent_dir, need_run=False)
    first = not agent_repo.has_run(agent_dir)
    today = datetime.datetime.now().astimezone().date().isoformat()  # local date
    runs = list_runs(agent_dir)
    # a run/<id> branch outlives its folder (runs/ is not in git): skip its id too
    ids = [rid for rid, _ in runs] + [b.removeprefix("run/") for b in agent_repo.run_branches(agent_dir)]
    taken = [int(m.group(2)) for rid in ids if (m := RUN_ID_RE.match(rid)) and m.group(1) == today]
    rid = f"{today}-{max(taken, default=0) + 1}"
    run = Run(rid, agent_dir / RUNS / rid, {})
    try:  # before the first run, uncommitted work is planning, on main; after it, it is the run's
        commit_sha = agent_repo.commit(agent_dir, f"r{plan.get('revision', 1)}: plan for run {rid}",
                                         to=None if first else run.branch)[0]
    except RuntimeError as exc:
        refuse(f"cannot commit the plan: {exc}")
    run.state.update({"id": rid, "status": "active", "revision": plan.get("revision", 1), "commit": commit_sha,
                      "started_at": now(), **({"note": note} if note else {}), "stages": {}, "launches": [],
                      # a relative path resolves inside this run, as a relative value in .env does (inputs/ a caller sent)
                      **({"given_params": {k: agent_params.resolve(k, (plan.get("params") or {})[k], v, run.dir)
                                           for k, v in given.items()}} if given else {}),
                      **({"preapproved": approved} if approved else {}),
                      **({"max_cost_usd": max_cost_usd} if max_cost_usd is not None else {})})
    (run.dir / "work").mkdir(parents=True)
    run.save()
    emit({"ok": True, "run": rid, "path": str(run.dir.resolve()), "commit": commit_sha,
          **({"left_branch": left, "note": f"switched to main for this run; {left} is kept for review"} if agent_repo.is_review_branch(left) else {})})


@app.command()
def stop(ctx: typer.Context, note: Annotated[str | None, typer.Option(help="why the run stopped")] = None):
    """Stop the active run; its branch (if any) waits for review."""
    agent_dir = ctx.obj
    agent_state.require(agent_dir, agent_state.stop_run)
    _plan, run = open_agent(agent_dir)
    git_note = commit(agent_dir, f"run {run.id}: files its waves left in the agent", run)  # stopping must work even if this fails
    run.state.update({"status": "stopped", "finished_at": now(), **({"note": note} if note else {})})
    run.save()
    emit({"ok": True, "run": run.id, "run_status": "stopped", "git_note": git_note, "review": agent_repo.review_info(agent_dir),
          "mission_close": close_mission(run, "cancelled", f"Agent run {run.id} stopped" + (f": {note}" if note else ""))})


@app.command()
def start_wave(
    ctx: typer.Context,
    wave: Annotated[str, typer.Option()],
    subagent_run_id: Annotated[str, typer.Option(help="the id the subagent launch returned")],
    mission_id: Annotated[str | None, typer.Option()] = None,
    approved: Annotated[str, typer.Option(help="approved side-effect stage ids, comma-separated")] = "",
    decisions: Annotated[str, typer.Option(help="decision answers as JSON")] = "{}",
):
    """Record a wave launch."""
    agent_dir = ctx.obj
    agent_state.require(agent_dir, agent_state.start_wave)
    plan, run = open_agent(agent_dir)
    nxt = (validate(plan, None, None, agent_dir, run_state=run.state)[2].get("waves") or [None])[0]
    if nxt is None or nxt["id"] != wave:
        refuse(f"wave {wave} is not the next wave of run {run.id} (next: {nxt['id'] if nxt else 'none'})")
    try:
        answers = json.loads(decisions)
    except ValueError as exc:
        refuse(f"--decisions is not JSON: {exc}")
    add_launch(agent_dir, plan, run, nxt, subagent_run_id, mission_id, [a.strip() for a in approved.split(",") if a.strip()], answers)
    emit({"ok": True, "run": run.id, "wave": wave})  # the entry is in run.yaml; the model supplied what it holds


def add_launch(agent_dir, plan, run, nxt, subagent_run_id, mission_id, approved, answers):
    """Append a launch of wave `nxt` ({id, stages, ends_with}) to run.yaml, its stages running; returns the entry."""
    entry = {
        "wave": nxt["id"],
        "stages": nxt["stages"],
        "revision": plan.get("revision", 1),
        "ends_with": nxt["ends_with"],
        "branch": agent_repo.branch(agent_dir),
        "commit": agent_repo.head(agent_dir),
        "status": "running",
        "subagent_run_id": subagent_run_id,
        "mission_id": mission_id or pi_records.mission_of(subagent_run_id),  # a launch recorded late: its mission lists it
        "started_at": now(),
        "approvals": approved,
        "decisions": answers,
        "params": agent_params.record(plan.get("params"), agent_params.load(agent_dir, plan.get("params"))),
    }
    run.state.setdefault("launches", []).append(entry)
    set_status(run.state, nxt["stages"], "running")
    run.save()
    return entry


def record_without_launch(agent_dir, plan, run, nxt, returned, approved):
    """A wave every stage of which is reused from an earlier run (compile_wave: nothing_to_launch), recorded as
    launched and finished at once, with no launch: what agent_finish_wave would return."""
    entry = add_launch(agent_dir, plan, run, nxt, None, None, approved, {})
    entry["launched"] = False
    return close_launch(agent_dir, plan, run, entry, Status.done, returned, {}, None)


def count_items(path: Path):
    """How many items a deliverable holds, where that can be read without a model: a JSON array's entries, a JSON
    lines file's lines, a CSV's rows (header not counted), a folder's files; else None."""
    try:
        if path.is_dir():
            return sum(1 for f in path.rglob("*") if f.is_file())
        suffix = path.suffix.lower()
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else None
        if suffix in (".jsonl", ".ndjson"):
            return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        if suffix == ".csv":
            return max(0, sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) - 1)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def list_deliverables(agent_dir, plan, run):
    """The plan's outputs as this run has them: {id, path (relative to the agent when inside it), exists, items (how
    many, when count_items can tell)}, resolved as the compiler resolves them (params, and relative paths inside the
    run): the final report checks a count from here instead of reading the file."""
    compiler = Compiler(agent_dir, plan, agent_params.load(agent_dir, plan.get("params")), run.dir, run.state, set())
    found = []
    for o in plan.get("outputs") or []:
        if not isinstance(o, dict) or not o.get("id") or not (path := compiler.out_path(o)):
            continue
        path = Path(path)
        shown = path.relative_to(agent_dir) if path.is_relative_to(agent_dir) else path
        entry = {"id": o["id"], "path": str(shown), "exists": path.exists()}
        if path.exists() and (items := count_items(path)) is not None:
            entry["items"] = items
        found.append(entry)
    return found


def next_preflight(agent_dir, plan, run, wave):
    """What the next wave's compile would ask for first (decisions, approvals, params, secrets), so the
    parent collects it in the same turn instead of compiling once just to hear it."""
    try:
        compiled = Compiler(agent_dir, plan, agent_params.load(agent_dir, plan.get("params")),
                            run.dir, run.state, set(run.state.get("preapproved") or [])).compile(wave)
    except (OSError, ValueError, KeyError, TypeError):  # only a hint: the compile itself reports what went wrong
        return None
    return {k: v for k, v in (compiled.get("preflight") or {}).items() if v} or None


@app.command()
def finish_wave(
    ctx: typer.Context,
    wave: Annotated[str, typer.Option()],
    status: Annotated[Status | None, typer.Option(help="default: the status the workflow script returned")] = None,
    result: Annotated[str | None, typer.Option(help="the workflow script's return value (a file or a JSON string); default: read from pi-subagents")] = None,
    note: Annotated[str | None, typer.Option()] = None,
):
    """Record a wave result."""
    agent_dir = ctx.obj
    plan, run = open_agent(agent_dir, need_run=False)
    if run is None and (last := (list_runs(agent_dir) or [None])[-1]):
        run = Run(last[0], last[1], read_run(last[1]))  # a wave of a run that has ended: say it is recorded (a completion notice after /agent-auto)
    state = (run.state if run else {})
    entry = next((r for r in reversed(state.get("launches") or []) if r.get("wave") == wave and r.get("status") == "running"), None)
    if entry is None:  # a running launch is recorded even after its run ended (a late completion notice)
        if recorded := next((r for r in reversed(state.get("launches") or []) if r.get("wave") == wave), None):
            refuse(f"wave {wave} of run {run.id} is already recorded ({recorded.get('status')}; the run is {state.get('status')}): "
                   "nothing to record again, and nothing to relaunch")
        agent_state.require(agent_dir, agent_state.finish_wave)
        refuse(f"run {run.id} has no running launch of wave {wave}")
    returned, ended = None, {}
    if result:
        try:
            result_path = Path(result)
            returned = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else json.loads(result)
        except ValueError as exc:
            refuse(f"--result is not JSON: {exc}")
    elif entry.get("subagent_run_id"):
        ended = subagents_outcome(entry["subagent_run_id"])
        returned = ended["value"]
    if status is None and returned is None and ended.get("state") in ("failed", "stopped", "paused"):
        # the agent ended before its script returned (a script error, a timeout, a validation failure)
        status = Status.stopped if ended["state"] == "stopped" else Status.failed
        entry["error"], entry["failure_kind"] = ended.get("error"), ended.get("failure_kind")
    if status is None:
        given = returned.get("status") if isinstance(returned, dict) else None
        if given not in {s.value for s in Status}:
            refuse(f"cannot tell how wave {wave} ended (pi-subagents state {ended.get('state')!r}, returned status {given!r}); pass --status")
        status = Status(given)
    emit(close_launch(agent_dir, plan, run, entry, status, returned, ended, note))


def close_launch(agent_dir, plan, run, entry, status, returned, ended, note):
    """Record how a launch ended (its stages done or pending again, its cost and children), commit what the wave wrote,
    and say what comes next: agent_finish_wave's result. `ended`: pi_records.outcome, or {} when nothing launched."""
    state, wave = run.state, entry["wave"]
    if returned is not None:
        record_wave_result(run.dir, wave, returned)
    end_launch(entry, status.value, returned, ended)
    entry["finished_at"] = now()
    if note:
        entry["note"] = note
    wave_status = "done" if status is Status.done else "pending"
    set_status(state, entry.get("stages") or [], wave_status)
    run.save()
    git_note = commit(agent_dir, f"run {run.id}, wave {wave}: files the wave wrote", run)  # while the run is still active
    keys = [s.get("id") for s in plan.get("stages") or []]
    done = bool(keys) and all(state["stages"].get(k) == "done" for k in keys)
    if done:
        state.update({"status": "done", "finished_at": now()})
        run.save()
    total = sum(r.get("cost_usd") or 0 for r in state.get("launches") or [])
    after = validate(plan, None, None, agent_dir, run_state=state)[2]
    nxt = None if done else (after.get("waves") or [None])[0]  # after a failed wave: that wave again
    if nxt:
        nxt = {k: nxt[k] for k in ("id", "stages", "ends_with")}
        if preflight := next_preflight(agent_dir, plan, run, nxt):
            nxt["preflight"] = preflight
    return {"ok": True, "run": run.id, "run_status": state["status"], "wave_status": wave_status,
          "ends_with": entry.get("ends_with") if status is Status.done else None,
          # a summary only: outputs are files the gates checked; read one only when something needs a look
          "children": {k: {"status": (v or {}).get("status"), "output": (v or {}).get("outputReference")}
                       for k, v in ((returned or {}).get("children") or {}).items()} if isinstance(returned, dict) else None,
          "error": entry.get("error"), "failure_kind": entry.get("failure_kind"), "cost_usd": entry.get("cost_usd"),
          "stopped_at": entry.get("stopped_at"), "git_note": git_note,
          # what to compile next, so the parent goes straight to agent_compile_wave (no check in between)
          "next_wave": nxt,
          "review": agent_repo.review_info(agent_dir) if done else None,
          "mission_close": close_mission(run, "completed", f"Agent run {run.id} completed ({len(keys)} stages"
                                         + (f", ${total:.4f} in children" if total else "") + ").") if done else None,
          # the criteria the final report goes through, so the parent need not read the plan for them
          "done_when": plan.get("done_when") if done else None,
          # where the deliverables are in this run: the report names them, nothing to work out
          "outputs": list_deliverables(agent_dir, plan, run) if done else None,
          "report": write_report(agent_dir, plan, run) if done else None}


def write_report(agent_dir, plan, run):
    """The final report, settled by code where it can be: each deliverable with how its stage's output was checked
    (gate, review, or reused from a run where they passed), and what is left for the model: the criteria, when some
    deliverable no check covers. `do` says which."""
    stages = {s.get("id"): s for s in plan.get("stages") or [] if isinstance(s, dict)}
    by_output = {o.get("id"): k for k, s in stages.items() for o in s.get("writes") or [] if isinstance(o, dict)}
    children = {}
    for launch in run.state.get("launches") or []:
        children.update(((read_wave(run.dir, launch.get("wave")).get("result") or {}).get("children")) or {})
    lines, unchecked = [], []
    for d in list_deliverables(agent_dir, plan, run):
        key = by_output.get(d["id"])
        st, child = stages.get(key) or {}, children.get(key) or {}
        how = ("reused from an earlier run, checked there" if child.get("reused")
               else ", ".join(c for c, ok in (("its gate passed", st.get("gate") not in (None, "", "true")),
                                              ("its review passed", st.get("review"))) if ok))
        if not how:
            unchecked.append(d["path"])
        items = f", {d['items']} items" if "items" in d else ""
        lines.append(f"{d['path']} ({'present' if d['exists'] else 'MISSING'}{items}): {how or 'no check covers it'}")
    return {"deliverables": lines, "criteria": plan.get("done_when") or [],
            "do": ("Report the deliverables and criteria as they stand: every deliverable was checked. Open nothing."
                   if not unchecked else
                   f"Report the deliverables as they stand. Only these no check covers: {', '.join(unchecked)}. Judge "
                   "the criteria about them from their items count; open one only if that can't settle it, once.")}


@app.command()
def decision(
    ctx: typer.Context,
    stage: Annotated[str, typer.Option()],
    answer: Annotated[str, typer.Option(help="the user's answer as JSON")],
):
    """Write the user's answer where run_compile.py looks for it."""
    agent_dir = ctx.obj
    agent_state.require(agent_dir, agent_state.record_decision)
    plan, run = open_agent(agent_dir)
    st = next((s for s in plan.get("stages") or [] if isinstance(s, dict) and s.get("id") == stage), None)
    if st is None:
        refuse(f"no stage {stage}")
    if stage_kind(st) != "decision" and st.get("pattern") != "approval":
        refuse(f"stage {stage} is not a decision stage")
    try:
        value = json.loads(answer)
    except ValueError as exc:
        refuse(f"--answer is not JSON: {exc}")
    compiler = Compiler(agent_dir, plan, agent_params.load(agent_dir, plan.get("params")), run.dir, run.state, set())
    path = Path(compiler.decision_path(stage))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stage": stage, "answer": value, "recorded_at": now()}, indent=2, ensure_ascii=False), encoding="utf-8")
    emit({"ok": True, "run": run.id, "stage": stage, "path": str(path)})


if __name__ == "__main__":
    app()
