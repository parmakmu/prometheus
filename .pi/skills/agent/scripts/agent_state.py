#!/usr/bin/env python3
"""Where an agent stands, and the moves it allows: a state machine in the style of a Quint spec (docs/state-machine.md).

`State` is the agent's state, read once per call (`read_state`). Each move is an action: its guards, then the states it
can lead to, as Quint's `all { guard, …, agent' = … }`. A guard that fails refuses the move with its reason, so
every script calls `require(agent_dir, action)` before anything else, and `next.moves` lists the actions whose
guards pass: one definition for both. Checks about a move's own arguments (which wave, which stage, locked keys)
stay in the scripts. tests/test_state_machine.py model-checks the actions from a new agent, with the invariants
below, and runs every real script to check it lands in a state its action said.

report_state(agent_dir) makes every result a script prints carry `next` (utils.emit calls back here: utils imports
nothing above it).

Usage: agent_state.py [--agent DIR]   prints {"ok", "state", "moves", "agent", ...}
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import agent_repo
import typer
from plan_validate import check_ready
from pydantic import BaseModel, ConfigDict
from run_folders import active_run
from utils import carry_next, emit, read_plan, refuse


class Branch(StrEnum):
    MAIN = "main"
    RUN = "run"  # run/<id>
    CHANGE = "change"  # revise/… or improve/…
    OTHER = "other"  # one the agent didn't make


class State(BaseModel):
    """The agent's state, the spec's variables. The first fields are what the moves depend on; the rest are for people."""

    model_config = ConfigDict(frozen=True)

    # what the moves depend on
    run: str | None = None  # id of the active run; None when no run is active
    wave: bool = False  # True while the active run has a wave launched and not yet recorded as finished
    plan_error: str | None = (
        None  # why plan.yaml as checked out cannot run (check_ready); None when it can
    )
    main_plan_error: str | None = (
        None  # why plan.yaml on main cannot run; None when it can (a new run starts from it)
    )
    has_run: bool = False  # True once the agent has had any run (agent_repo.has_run)
    branch: Branch = Branch.MAIN  # which kind of branch is checked out
    stuck: bool = False  # True when the active run's branch is not checked out and files are left uncommitted
    # for people
    branch_name: str = (
        "main"  # the checked-out branch's full name, e.g. revise/2026-10-17-1
    )
    off_branch: str | None = (
        None  # what to do when the active run's branch is not checked out
    )
    last_launch: dict | None = (
        None  # the active run's latest launch: wave, status, subagent_run_id
    )

    def update(self, **changes):
        """The state after `changes`, as read_state would read it: on main, main's plan is the plan; only a run
        between waves can be off its branch."""
        s = self.model_copy(update=changes)
        return s.model_copy(
            update={
                "main_plan_error": s.plan_error
                if s.branch == Branch.MAIN
                else s.main_plan_error,
                "stuck": s.stuck and bool(s.run) and not s.wave,
            }
        )


def read_state(agent_dir):
    agent_dir = Path(agent_dir)
    plan = read_plan(agent_dir, {})
    plan = plan if isinstance(plan, dict) else {}
    run = active_run(agent_dir)
    name = agent_repo.branch(agent_dir) or (
        "a detached HEAD" if agent_repo.is_repo(agent_dir) else agent_repo.MAIN
    )
    if name == agent_repo.MAIN:
        branch = Branch.MAIN
    else:
        branch = (
            Branch.RUN
            if name.startswith("run/")
            else Branch.CHANGE
            if agent_repo.is_change_branch(name)
            else Branch.OTHER
        )
    main_plan = plan if branch == Branch.MAIN else agent_repo.main_plan(agent_dir) or {}
    plan_error = check_ready(plan, None if branch == Branch.MAIN else main_plan)
    wave = bool(run and run.wave_running)
    off = (
        agent_repo.find_off_branch(agent_dir, run.branch) if run and not wave else None
    )
    launch = (run.state.get("launches") or [None])[-1] if run else None
    return State(
        run=run.id if run else None,
        wave=wave,
        plan_error=plan_error,
        main_plan_error=plan_error if branch == Branch.MAIN else check_ready(main_plan),
        has_run=agent_repo.has_run(agent_dir),
        branch=branch,
        stuck=bool(off and off["uncommitted"]),
        branch_name=name,
        off_branch=agent_repo.describe_off_branch(off) if off else None,
        last_launch={k: launch.get(k) for k in ("wave", "status", "subagent_run_id")}
        if isinstance(launch, dict)
        else None,
    )


# --- guards -----------------------------------------------------------------------------------------


class Refused(Exception):
    pass


def guard(holds, why):
    """Quint's guard: the action happens only if `holds`; else it is refused with `why`."""
    if not holds:
        raise Refused(why)


def guard_no_run(a):
    guard(
        not a.run,
        f"run {a.run} is active: continue it (a change to its plan is resolved on its branch: "
        "agent_resolve), or stop it first (agent_stop_run)",
    )


def guard_between_waves(a):
    guard(
        a.run,
        "no active run: start one with agent_start_run (run_steps.py start-run). Outside a run the plan is edited "
        "directly: on main before the first run, else on a change branch (/agent-revise)",
    )
    guard(
        not a.wave,
        f"a wave of run {a.run} is running: let it finish (or stop it in pi-subagents), then record "
        "it with agent_finish_wave",
    )


def guard_on_run_branch(a):
    """The run's branch is checked out, or can be again: the scripts switch back when nothing is left uncommitted."""
    guard(
        not a.stuck,
        f"run {a.run}: {a.off_branch}. Tell the user; do not change branches or files yourself",
    )


def guard_known_branch(a):
    guard(
        a.branch != Branch.OTHER,
        f"{a.branch_name} is checked out, which is neither main nor a run or change "
        "branch: switch back with `git switch main` (or to the branch you were working on)",
    )


def guard_reviewed(a):
    guard(
        a.branch != Branch.RUN,
        f"branch {a.branch_name} is checked out and not reviewed: review it first "
        "(agent_merge: merge, keep or discard)",
    )


ERROR = "not ready"  # stands for a plan error in a predicted state, whose words the spec can't know


def back_on_run_branch(a):
    """The states that can follow when a run's script first checks out the run's branch again (run/<id>, or main
    before the run's first commit creates it), as it does when someone switched branches and left nothing
    uncommitted. Which of the two, and whether its plan can run, the spec can't know."""
    if not a.run or a.branch in (Branch.MAIN, Branch.RUN):
        return [a]
    return [
        a.update(branch=b, plan_error=e, off_branch=None)
        for b in (Branch.MAIN, Branch.RUN)
        for e in (None, ERROR)
    ]


def committed(states):
    """`states`, plus each one on run/<id>: a run's first commit creates that branch and checks it out
    (agent_repo.commit with `to`). main then still has the plan the run started from, which could run."""
    return states + [
        s.update(branch=Branch.RUN, main_plan_error=None)
        for s in states
        if s.branch == Branch.MAIN
    ]


# --- actions: guards, then the states the move can lead to ------------------------------------------


def start_run(a):
    guard_no_run(a)
    guard_known_branch(a)
    guard(
        not a.main_plan_error,
        f"the plan on main, which a run starts from, can't run: {a.main_plan_error}",
    )
    started = State(
        run="new", plan_error=None, main_plan_error=None, has_run=True
    )  # on main; a branch left is kept for review
    return committed([started]) if a.has_run else [started]


def compile_wave(a):
    guard_between_waves(a)
    guard_on_run_branch(
        a
    )  # a plan that can't run is not refused: compile reports it in its preflight (plan_errors)
    return committed(back_on_run_branch(a))


def start_wave(a):
    # it records a launch the subagent tool already made (it needs the launch's id), so a guard here can't stop one:
    # no guard_on_run_branch, which would only leave a running launch unrecorded. The entry keeps its branch and commit.
    guard_between_waves(a)
    guard(
        not a.plan_error,
        f"{a.plan_error}. No wave compiles from this plan, so none can have launched from it",
    )
    return [a.update(wave=True)]


def finish_wave(a):
    guard(a.run, "no active run: there is nothing to record")
    guard(a.wave, "no wave is running: there is nothing to record")
    # the run goes on (more waves) or ends (that was the last); its commit checks out the run's branch first
    # a commit that can't switch back (files left uncommitted) is skipped, not refused: the agent stays where it is
    moved = back_on_run_branch(a.update(wave=False)) + [a.update(wave=False)]
    return committed([s.update(run=r) for s in moved for r in (a.run, None)])


def record_decision(a):
    guard_between_waves(a)
    return [a]


def resolve(a):
    guard_between_waves(a)
    guard_on_run_branch(a)
    return committed(
        [
            s.update(plan_error=e)
            for s in back_on_run_branch(a)
            for e in (s.plan_error, ERROR)
        ]
    )


def record_critique(a):
    guard(
        not a.wave,
        f"a wave of run {a.run} is running: the critic reviews resolved stages after agent_finish_wave",
    )
    guard_known_branch(a)
    # a critic's result needs a plan to apply to: a run's resolved stages, a run or change branch, or planning
    guard(
        a.run or a.branch != Branch.MAIN or not a.has_run,
        "main changes only by a reviewed merge once a run exists: change the plan with /agent-revise (a branch), "
        "and record the critic there",
    )
    guard_on_run_branch(a)
    after = [
        s.update(plan_error=e)
        for s in back_on_run_branch(a)
        for e in (s.plan_error, None)
    ]
    return committed(after) if a.run else after


def stop_run(a):
    guard_between_waves(a)
    # a commit that can't switch back (files left uncommitted) is skipped, not refused: the agent stays where it is
    return committed([s.update(run=None) for s in back_on_run_branch(a) + [a]])


def merge(a, action):
    guard_no_run(a)
    guard_known_branch(a)
    guard(a.branch != Branch.MAIN, "main is checked out: nothing to review")
    if action == "merge":  # keep and discard need no plan that can run; merge does
        guard(
            not a.plan_error,
            f"the plan on {a.branch_name} can't run ({a.plan_error}): only a ready, valid plan can "
            "reach main. Finish the change (critic, agent_record_critique), or keep or discard the branch",
        )
        return [a.update(branch=Branch.MAIN, branch_name="main")]
    return [
        a.update(branch=Branch.MAIN, branch_name="main", plan_error=a.main_plan_error)
    ]


def revise(a):
    guard_no_run(a)
    guard_known_branch(a)
    guard_reviewed(a)
    if not a.has_run or a.branch == Branch.CHANGE:
        return [a]  # planning edits the plan on main; an open change continues
    # uncommitted changes on main move to the branch: main's committed plan may be the one that can run
    return [
        a.update(branch=Branch.CHANGE, branch_name="revise/…", main_plan_error=e)
        for e in (a.plan_error, None)
    ]


def improve(a):
    guard(
        a.has_run,
        "no run yet, so nothing to improve from: run the agent first (/agent-run), or change the plan "
        "directly while planning (/agent-revise)",
    )
    return revise(a)


def edit_plan(a):
    """Editing plan.yaml by hand: the extension's tool_call hook allows it only where this guard passes (index.ts)."""
    guard_no_run(a)
    guard(
        a.branch == Branch.CHANGE or (a.branch == Branch.MAIN and not a.has_run),
        "the plan changes by hand only on a change branch (/agent-revise), or on main before the first run",
    )
    return [a, a.update(plan_error=ERROR)]


MOVES = {  # the tools' and commands' names
    "agent_start_run": start_run,
    "agent_compile_wave": compile_wave,
    "agent_start_wave": start_wave,
    "agent_finish_wave": finish_wave,
    "agent_record_decision": record_decision,
    "agent_resolve": resolve,
    "agent_record_critique": record_critique,
    "agent_stop_run": stop_run,
    "agent_merge": merge,
    "/agent-revise": revise,
    "/agent-improve": improve,
    "edit plan.yaml": edit_plan,
}
MERGE_ACTIONS = ("merge", "keep", "discard")


def try_move(a, action, *args):
    """The states `action` leads to from `a`, or the Refused that stops it."""
    try:
        return action(a, *args)
    except Refused as refused:
        return refused


def require(agent_dir, action, *args):
    """Refuse the move unless its guards pass (the refusal's `next` is the state as read)."""
    a = read_state(agent_dir)
    if isinstance(refused := try_move(a, action, *args), Refused):
        refuse(str(refused), next=next_of(a))


# --- invariants: true in every state (Quint's vals) -------------------------------------------------
# Properties of moves ("never launch from a plan that can't run") are not here: they are the actions' guards.

INVARIANTS = {
    "a wave runs only in an active run": lambda a: not a.wave or a.run,
    "an active run is a run the agent has had": lambda a: not a.run or a.has_run,
    "only a run between waves is off its branch": lambda a: (
        not a.stuck or (a.run and not a.wave)
    ),
    "on main, main's plan is the plan": lambda a: (
        a.branch != Branch.MAIN or a.main_plan_error == a.plan_error
    ),
}


# --- the name, for people ---------------------------------------------------------------------------

HINTS = {
    "plan_draft": "plan: edit plan.yaml, run the critic, record it",
    "plan_ready": "the plan is ready: start the first run, or keep planning",
    "main_ready": "start a run, or change the plan on a branch",
    "run_ready": "compile the next wave (it lists what must happen first)",
    "run_wave": "wait for the completion notice, then agent_finish_wave",
    "run_draft": "run the critic on the resolved stages and record it",
    "run_review": "review the run's branch with the user: merge, keep or discard",
    "change_draft": "finish the change (edit, critic, record), or keep or discard it",
    "change_ready": "the plan is ready (unchanged, or the critic passed): make the change, or review it with the user: merge, keep or discard",
    "main_draft": "the plan on main is not ready: change it with /agent-revise",
    "other_branch": "a branch the agent did not make is checked out: ask the user to switch back (`git switch main`, or to the branch they were working on)",
}
STATES = tuple(HINTS)


def name_state(a):
    """`<place>_<status>`: the run, else the branch, else main before or after the first run."""
    status = "draft" if a.plan_error else "ready"
    if a.run:
        return "run_wave" if a.wave else f"run_{status}"
    if a.branch == Branch.MAIN:
        return f"{'main' if a.has_run else 'plan'}_{status}"
    return {
        Branch.RUN: "run_review",
        Branch.CHANGE: f"change_{status}",
        Branch.OTHER: "other_branch",
    }[a.branch]


def next_of(a):
    """{state, moves, hint, merge?, run?, last_launch?, branch?, off_branch?}: where the agent stands and what may
    happen next; `merge` the review's actions allowed."""
    name = name_state(a)
    reviews = [
        m for m in MERGE_ACTIONS if not isinstance(try_move(a, merge, m), Refused)
    ]
    moves = [
        m
        for m, action in MOVES.items()
        if (
            reviews if action is merge else not isinstance(try_move(a, action), Refused)
        )
    ]
    out = {"state": name, "moves": moves, "hint": HINTS[name]} | (
        {"merge": reviews} if reviews else {}
    )
    extra = {
        "run": a.run,
        "last_launch": a.last_launch,
        "off_branch": a.off_branch,
        "branch": a.branch_name if a.branch != Branch.MAIN else None,
    }
    return out | {k: v for k, v in extra.items() if v}


def read_next(agent_dir):
    return next_of(read_state(agent_dir))


def report_state(agent_dir):
    """From now on, every result the script prints (utils.emit) carries `next`: the agent's state and its legal moves,
    once it has a plan, so the model need not work out where the agent stands."""
    agent_dir = Path(agent_dir)
    carry_next(lambda: read_next(agent_dir) if (agent_dir / "plan.yaml").is_file() else None)


def main(agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = "."):
    root = Path(agent_dir)
    if not (root / "plan.yaml").is_file():
        refuse(f"{root.resolve()} is not an agent (no plan.yaml)", 2)
    a = read_state(root)
    emit({"ok": True, **next_of(a), "agent": a.model_dump(mode="json")})


if __name__ == "__main__":
    typer.run(main)
