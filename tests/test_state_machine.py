"""Walk the agent state machine (docs/state-machine.md) with the real scripts.

Three checks of agent_state.py, the agent's state machine written like a Quint spec:

- the walk: from each state reached, every move is tried on a copy of a real agent, the tools and what a person
  does outside them (edit plan.yaml by hand, check out another branch, leave a file uncommitted). States are keyed
  by `shape` (what the actions depend on), so two histories that leave the agent alike are explored once. Checked:
  a move `next` does not list is refused, each one it lists is accepted somewhere, the invariants hold after every
  move, every result carries `next`, every state is reached and has a way out.
- conformance: each real move lands in a state its action predicted.
- the model check: every state the actions reach from a new agent keeps agent_state.INVARIANTS and has a move.

Scripts run in-process (typer's CliRunner).
"""
import hashlib
import json
import multiprocessing
import os
import pickle
import shutil
import subprocess
import sys
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from itertools import count
from pathlib import Path

import pytest
import typer
from conftest import SKILL, fake_critic
from test_run_finish import PLAN, ready_agent
from typer.testing import CliRunner

# the walk is built once and read by every test here: one worker, so it isn't rebuilt on each
pytestmark = pytest.mark.xdist_group("state-walk")

sys.path.insert(0, str(SKILL / "scripts"))
import agent_repo
import agent_state
import plan_edit
import run_compile
import run_steps
from utils import read_yaml, write_yaml

COMPILE = typer.Typer(add_completion=False)
COMPILE.command()(run_compile.main)
CRITIC = '{"run_id": "%s", "verdict": "pass", "agent": "worker"}'  # the plan's agents list no plan-critic


def git(wf, *args):
    return subprocess.run(["git", "-C", str(wf), *args], capture_output=True, text=True, check=False).stdout.strip()


def main_commit(wf):
    """main's commit, read from its ref file (twice per move, so not a git process each); git if the ref is packed."""
    ref = wf / ".git" / "refs" / "heads" / "main"
    return ref.read_text(encoding="utf-8").strip() if ref.is_file() else git(wf, "rev-parse", "main")


def running_launch(wf):
    for run_yaml in (wf / "runs").glob("*/run.yaml"):
        text = run_yaml.read_text(encoding="utf-8")
        if "status: running" in text:
            return text.split("subagent_run_id: ")[-1].split("\n")[0].strip()
    return None


def invoke(wf, move):
    """Run one move in the agent; (accepted, its JSON)."""
    if move == "edit plan.yaml":  # by hand: the extension's guard refuses it where `next` doesn't list it, a person's editor doesn't
        plan = read_yaml(wf / "plan.yaml", {})
        plan["goal"] = f"{plan.get('goal', '')} (edited)"
        write_yaml(wf / "plan.yaml", plan)
        return True, {}
    if move == "plan.yaml to draft":  # main's plan unready, a hand edit only can make it (main_draft)
        plan = read_yaml(wf / "plan.yaml", {})
        plan["status"] = "draft"
        write_yaml(wf / "plan.yaml", plan)
        return True, {}
    if move == "git switch other":
        return subprocess.run(["git", "-C", str(wf), "switch", "-q", "-C", "other"], capture_output=True, check=False).returncode == 0, {}
    if move == "uncommitted file":
        (wf / "notes.txt").write_text(f"{wf.name}\n", encoding="utf-8")
        return True, {}
    if move == "agent_record_critique":
        run_id = f"walk-critic-{wf.name}"  # the copy's name: unique, whichever process tries it
        fake_critic(run_id, agent="worker")
        app, args = plan_edit.app, ["critique", "--result", CRITIC % run_id]
    elif move == "agent_start_wave":
        app, args = run_steps.app, ["start-wave", "--wave", "w1", "--subagent-run-id", f"walk-{wf.name}"]
    elif move == "agent_finish_wave":
        if launched := running_launch(wf):
            record = Path(os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / launched
            record.mkdir(parents=True, exist_ok=True)
            (record / "status.json").write_text(json.dumps({"state": "complete", "workflow": {"value": {"status": "done", "children": {}}}}))
        app, args = run_steps.app, ["finish-wave", "--wave", "w1"]
    else:
        app, args = {
            "agent_start_run": (run_steps.app, ["start-run"]),
            "agent_compile_wave": (COMPILE, []),
            "agent_record_decision": (run_steps.app, ["decision", "--stage", "count", "--answer", "true"]),
            "agent_resolve": (plan_edit.app, ["resolve", "--change", '{"note": "walk"}']),
            "agent_stop_run": (run_steps.app, ["stop"]),
            "agent_merge:merge": (agent_repo.app, ["merge", "--action", "merge"]),
            "agent_merge:keep": (agent_repo.app, ["merge", "--action", "keep"]),
            "agent_merge:discard": (agent_repo.app, ["merge", "--action", "discard"]),
            "/agent-revise": (agent_repo.app, ["begin-change"]),
            "/agent-improve": (agent_repo.app, ["begin-change", "--kind", "improve"]),
        }[move]
    here = Path.cwd()
    os.chdir(wf)
    try:
        result = CliRunner().invoke(app, args)
    finally:
        os.chdir(here)
    data = json.loads(result.output)
    return result.exit_code == 0 and data.get("ok") is not False, data


MOVES = ["agent_start_run", "agent_compile_wave", "agent_start_wave", "agent_finish_wave", "agent_record_decision",
         "agent_resolve", "agent_record_critique", "agent_stop_run", "agent_merge:merge", "agent_merge:keep",
         "agent_merge:discard", "/agent-revise", "/agent-improve"]
BY_HAND = ["edit plan.yaml", "plan.yaml to draft", "git switch other", "uncommitted file"]  # a person, outside the tools
# refused on its own argument here, not on a condition: the walk's plan has no decision stage
ARGUMENT_ONLY = {"agent_record_decision"}


@dataclass
class Try:
    """One move tried from one configuration."""
    accepted: bool
    after: tuple  # key(): (state, conditions)
    listed: bool
    broken: list  # invariants the move broke
    conforms: bool  # it landed in a state its action said (refused: where it was, or back on the run's branch)
    result: dict
    wf: Path  # the copy it was tried on


def listed(where, move):
    if move.startswith("agent_merge:"):
        return "agent_merge" in where["moves"] and move.split(":")[1] in where.get("merge", [])
    return move in where["moves"]


def shape(a):
    """What the actions depend on, without the words (a plan's error) and names (a run's id)."""
    return bool(a.run), a.wave, bool(a.plan_error), bool(a.main_plan_error), a.has_run, a.branch, a.stuck


def key(a):
    return agent_state.name_state(a), shape(a)


def try_in_copy(src, wf, move):
    """One move on a copy of the agent at `src`: (accepted, its JSON, the state it left, its census). Run in a pool
    of processes, so it shares nothing with the others but pi-subagents' temp folder, where ids are the copy's."""
    shutil.copytree(src, wf, symlinks=True)
    accepted, data = invoke(wf, move)
    return accepted, data, agent_state.read_state(wf), census(wf)


def action_of(move):
    """The spec's action for a walk move, with its arguments; None for what a person does by hand."""
    if move.startswith("agent_merge:"):
        return agent_state.merge, move.split(":")[1]
    return (agent_state.MOVES[move],) if move in MOVES else None


def conforms(move, before, after, accepted):
    """Whether the real script landed where the move's action said it could."""
    if not (action := action_of(move)):
        return True
    predicted = agent_state.try_move(before, *action)
    if accepted and not isinstance(predicted, agent_state.Refused):
        return shape(after) in {shape(a) for a in predicted}
    return shape(after) in {shape(before)} | {shape(a) for a in agent_state.back_on_run_branch(before)}


def census(wf):
    """What the invariants count: active runs, launches (all, and running), main's commit."""
    runs = [read_yaml(p, {}) for p in (wf / "runs").glob("*/run.yaml")]
    launches = [entry for r in runs for entry in r.get("launches") or []]
    return {"active": [r["id"] for r in runs if r.get("status") == "active"], "launches": len(launches),
            "running": sum(1 for entry in launches if entry.get("status") == "running"), "main": main_commit(wf)}


def broken(move, before, was, now):
    """The invariants a move from state `before` (census `was`) to census `now` broke."""
    return [name for name, ok in (
        ("at most one active run", len(now["active"]) <= 1),
        ("a wave runs only in an active run, and one at a time", now["running"] <= len(now["active"])),
        ("nothing launches while a wave runs", not (was["running"] and now["launches"] > was["launches"])),
        ("only a ready plan launches", now["launches"] <= was["launches"] or not before.plan_error),
        ("a run starts only from a ready plan on main",
         set(now["active"]) <= set(was["active"]) or not before.main_plan_error),
        ("main changes only by a merge, or by planning before the first run",
         now["main"] == was["main"] or move == "agent_merge:merge" or not before.has_run),
    ) if not ok]


@pytest.fixture(scope="module")
def walk(tmp_path_factory, request):
    """Every (configuration, move) tried: {(key, move): Try}. Kept in pytest's cache under a hash of all it depends
    on (the skill's scripts, the agent template, these tests, python's version), so an unchanged walk isn't redone;
    `--cache-clear` redoes it."""
    folder = request.config.cache.mkdir("state-walk")
    cached = folder / f"{hash_walk_inputs()}.pickle"
    if cached.is_file():
        return pickle.loads(cached.read_bytes())
    tried = do_walk(tmp_path_factory.mktemp("walk"))
    for old in folder.glob("*.pickle"):
        old.unlink()
    cached.write_bytes(pickle.dumps(tried))
    return tried


def hash_walk_inputs():
    files = [*(SKILL / "scripts").glob("*.py"), SKILL / "scripts" / "wave_runner.js",
             *(p for p in (SKILL.parents[2] / "templates" / "agent").rglob("*") if p.is_file()),
             *(Path(__file__).parent / name for name in ("test_state_machine.py", "conftest.py", "test_run_finish.py"))]
    digest = hashlib.sha256(sys.version.encode())
    for f in sorted(files):
        digest.update(f.name.encode() + f.read_bytes())
    return digest.hexdigest()[:16]


def do_walk(tmp):
    roots = []
    for status in ("ready", "draft"):
        here = Path.cwd()
        r = subprocess.run([sys.executable, SKILL / "scripts" / "agent_scaffold.py", status, "--root", tmp, "--request", "x"],
                           capture_output=True, text=True, check=False)
        assert json.loads(r.stdout)["ok"], r
        os.chdir(here)
        wf = tmp / status
        (wf / "plan.yaml").write_text(PLAN.replace("status: ready", f"status: {status}"), encoding="utf-8")
        roots.append(wf)
    seen, tried, copies = {}, {}, count()
    level = []
    for wf in roots:
        seen[key(agent_state.read_state(wf))] = wf
        level.append(wf)
    # breadth first, a level at a time: every try of a level runs at once, each on its own copy, in a third of the
    # cores (the suite's other workers share them; all of them made the whole suite no faster)
    with ProcessPoolExecutor(max(2, os.cpu_count() // 3), mp_context=multiprocessing.get_context("spawn")) as pool:
        while level:
            jobs = []
            for src in level:
                a = agent_state.read_state(src)
                before, where, was = key(a), agent_state.next_of(a), census(src)
                for move in MOVES + BY_HAND:
                    wf = tmp / f"c{next(copies)}"
                    jobs.append((before, where, was, a, move, wf, pool.submit(try_in_copy, src, wf, move)))
            level = []
            for before, where, was, a, move, wf, job in jobs:
                accepted, data, now_state, now = job.result()
                after = key(now_state)
                tried[(before, move)] = Try(accepted, after, listed(where, move), broken(move, a, was, now),
                                            conforms(move, a, now_state, accepted), data, wf)
                if accepted and after not in seen:
                    seen[after] = wf
                    level.append(wf)
    return tried


def test_every_state_is_reached(walk):
    reached = {k[0] for k, _ in walk} | {t.after[0] for t in walk.values()}
    assert reached == set(agent_state.STATES), set(agent_state.STATES) - reached


def test_a_move_next_does_not_list_is_refused_and_changes_nothing(walk):
    bad = [(k[0], m, t.result) for (k, m), t in walk.items()
           if m in MOVES and not t.listed and t.accepted]
    assert not bad, bad


def test_every_move_next_lists_is_accepted_somewhere(walk):
    """So no action's guard is one a script's own checks always contradict."""
    dead = {m for m in set(MOVES) - ARGUMENT_ONLY if any(t.listed for (_k, mm), t in walk.items() if mm == m)
            and not any(t.listed and t.accepted for (_k, mm), t in walk.items() if mm == m)}
    assert not dead, dead


def test_the_invariants_hold_after_every_move(walk):
    bad = [(k[0], m, t.broken) for (k, m), t in walk.items() if t.broken]
    assert not bad, bad


def test_every_result_says_where_the_agent_is_next(walk):
    bad = [(k[0], m, t.result.get("next")) for (k, m), t in walk.items()
           if m in MOVES and (t.result.get("next") or {}).get("state") != t.after[0]]
    assert not bad, bad


def test_every_state_has_a_way_out(walk):
    """By a tool, except on a branch the agent didn't make: a person switches back (its hint says so)."""
    states = {k for k, _ in walk}
    stuck = [k[0] for k in states if k[0] != "other_branch"
             and not any(t.accepted and t.after != k for (s, m), t in walk.items() if s == k and m in MOVES)]
    assert not stuck, stuck


def test_a_clone_keeps_main_closed(tmp_path, run_py):
    """runs/ is not in git, so a clone has none: main's history says a run happened (agent_repo.has_run)."""
    src, scripts = ready_agent(tmp_path, run_py, "ran")
    assert run_py([scripts / "run_steps.py", "start-run"], cwd=src).data["ok"]  # commits the plan on main
    assert run_py([scripts / "run_steps.py", "stop"], cwd=src).data["ok"]
    subprocess.run(["git", "clone", "-q", str(src), str(tmp_path / "clone")], check=True)
    where = agent_state.read_next(tmp_path / "clone")
    assert where["state"] == "main_ready" and "edit plan.yaml" not in where["moves"], where


def test_every_move_lands_where_its_action_said(walk):
    """The spec and the scripts agree: each real move ends in a state its action in agent_state predicted."""
    bad = [(k[0], m, t.after[0]) for (k, m), t in walk.items() if not t.conforms]
    assert not bad, bad


# what a person does outside the tools, as actions (the walk does them for real: BY_HAND)
BY_HAND_ACTIONS = [
    lambda a: [a.update(plan_error=agent_state.ERROR)],
    lambda a: [a.update(branch=agent_state.Branch.OTHER, branch_name="other")],
    lambda a: [a.update(stuck=a.run is not None and not a.wave and a.branch in (agent_state.Branch.OTHER, agent_state.Branch.CHANGE))],
]


@pytest.fixture(scope="module")
def reachable():
    """Every state the actions reach from a new agent (a draft plan or a ready one), as Quint's model checker
    explores them: {shape: an Agent}."""
    seen = {}
    queue = deque([agent_state.State(plan_error=agent_state.ERROR, main_plan_error=agent_state.ERROR), agent_state.State()])
    while queue:
        a = queue.popleft()
        if shape(a) in seen:
            continue
        seen[shape(a)] = a
        for action, *args in [(f,) for f in agent_state.MOVES.values() if f is not agent_state.merge] + \
                [(agent_state.merge, m) for m in agent_state.MERGE_ACTIONS] + [(f,) for f in BY_HAND_ACTIONS]:
            if not isinstance(after := agent_state.try_move(a, action, *args), agent_state.Refused):
                queue.extend(after)
    return seen


def test_the_invariants_hold_in_every_reachable_state(reachable):
    bad = [(name, shape(a)) for a in reachable.values() for name, holds in agent_state.INVARIANTS.items() if not holds(a)]
    assert not bad, bad


def test_the_spec_reaches_every_state_and_has_no_dead_end(reachable):
    """On a branch the agent didn't make, the way out is a person's: its hint says so."""
    assert {agent_state.name_state(a) for a in reachable.values()} == set(agent_state.STATES)
    stuck = [shape(a) for a in reachable.values() if not agent_state.next_of(a)["moves"] and agent_state.name_state(a) != "other_branch"]
    assert not stuck, stuck
