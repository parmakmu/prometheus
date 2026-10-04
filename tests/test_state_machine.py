"""Walk the agent state machine (docs/state-machine.md) with the real scripts.

From each state reached, every move is tried on a copy of the agent. Checked: a move the
state's `moves` (agent_state.MOVES) does not list is refused and changes nothing; `main`
changes only by a merge, or by planning before the first run; every result carries `next`, the
state the agent is in afterwards; and every state reached has a way out. States are explored
once each (breadth first), keyed by name and whether a run exists, so the walk covers every
state and move without enumerating every history. Scripts run in-process (typer's CliRunner):
the walk makes a few hundred calls.
"""
import json
import os
import shutil
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from itertools import count
from pathlib import Path

import pytest
import typer
from conftest import SKILL, fake_critic
from test_run_finish import PLAN
from typer.testing import CliRunner

# the walk is built once and read by every test here: one worker, so it isn't rebuilt on each
pytestmark = pytest.mark.xdist_group("state-walk")

sys.path.insert(0, str(SKILL / "scripts"))
import agent_git
import agent_state
import compile_wave
import plan_edit
import run_log
from utils import read_yaml, write_yaml

COMPILE = typer.Typer(add_completion=False)
COMPILE.command()(compile_wave.main)
CRITIC = '{"run_id": "%s", "verdict": "pass", "agent": "worker"}'  # the plan's agents list no plan-critic
# accepted though the state's moves leave it out (docs/state-machine.md, "Allowed, outside the table")
TOLERATED = {("between_waves", "agent_record_critique")}
ids = count(1)


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
    if move == "edit plan.yaml":  # a direct edit (the extension's guard allows it where the state lists it)
        plan = read_yaml(wf / "plan.yaml", {})
        plan["goal"] = f"{plan.get('goal', '')} (edited)"
        write_yaml(wf / "plan.yaml", plan)
        return True, {}
    if move == "agent_record_critique":
        run_id = f"walk-critic-{next(ids)}"
        fake_critic(run_id, agent="worker")
        app, args = plan_edit.app, ["critique", "--result", CRITIC % run_id]
    elif move == "agent_log_start":
        app, args = run_log.app, ["start", "--wave", "w1", "--subagent-run-id", f"walk-{next(ids)}"]
    elif move == "agent_log_finish":
        if launched := running_launch(wf):
            record = Path(os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / launched
            record.mkdir(parents=True, exist_ok=True)
            (record / "status.json").write_text(json.dumps({"state": "complete", "workflow": {"value": {"status": "done", "children": {}}}}))
        app, args = run_log.app, ["finish", "--wave", "w1"]
    else:
        app, args = {
            "agent_new_run": (run_log.app, ["new"]),
            "agent_compile": (COMPILE, []),
            "agent_record_decision": (run_log.app, ["decision", "--stage", "count", "--answer", "true"]),
            "agent_replan": (plan_edit.app, ["replan", "--change", '{"note": "walk"}']),
            "agent_stop_run": (run_log.app, ["stop"]),
            "agent_merge:merge": (agent_git.app, ["merge", "--action", "merge"]),
            "agent_merge:keep": (agent_git.app, ["merge", "--action", "keep"]),
            "agent_merge:discard": (agent_git.app, ["merge", "--action", "discard"]),
            "/agent-revise": (agent_git.app, ["begin-change"]),
            "/agent-improve": (agent_git.app, ["begin-change", "--kind", "improve"]),
        }[move]
    here = Path.cwd()
    os.chdir(wf)
    try:
        result = CliRunner().invoke(app, args)
    finally:
        os.chdir(here)
    data = json.loads(result.output)
    return result.exit_code == 0 and data.get("ok") is not False, data


MOVES = ["agent_new_run", "agent_compile", "agent_log_start", "agent_log_finish", "agent_record_decision",
         "agent_replan", "agent_record_critique", "agent_stop_run", "agent_merge:merge", "agent_merge:keep",
         "agent_merge:discard", "/agent-revise", "/agent-improve", "edit plan.yaml"]


@dataclass
class Try:
    """One move tried from one state."""
    accepted: bool
    after: tuple  # (state, a run exists)
    main_moved: bool
    listed: bool
    had_runs: bool
    result: dict


def listed(where, move):
    if move.startswith("agent_merge:"):
        return "agent_merge" in where["moves"] and move.split(":")[1] in where.get("merge", [])
    return move in where["moves"]


def key(wf):
    return agent_state.state(wf)["state"], bool(list((wf / "runs").glob("*/run.yaml")))


@pytest.fixture(scope="module")
def walk(tmp_path_factory):
    """Every (state, move) tried: {(state key, move): Try}."""
    tmp = tmp_path_factory.mktemp("walk")
    roots = []
    for status in ("ready", "draft"):
        here = Path.cwd()
        r = subprocess.run([sys.executable, SKILL / "scripts" / "scaffold_agent.py", status, "--root", tmp, "--request", "x"],
                           capture_output=True, text=True, check=False)
        assert json.loads(r.stdout)["ok"], r
        os.chdir(here)
        wf = tmp / status
        (wf / "plan.yaml").write_text(PLAN.replace("status: ready", f"status: {status}"), encoding="utf-8")
        roots.append(wf)
    seen, queue, tried, copies = {}, deque(), {}, count()
    for wf in roots:
        seen[key(wf)] = wf
        queue.append(wf)
    while queue:
        src = queue.popleft()
        before = key(src)
        where = agent_state.state(src)
        for move in MOVES:
            if move == "edit plan.yaml" and not listed(where, move):
                continue  # refused by the extension's guard, not a script: nothing here to try
            wf = tmp / f"c{next(copies)}"
            shutil.copytree(src, wf, symlinks=True)
            main, had_runs = main_commit(wf), before[1]
            accepted, data = invoke(wf, move)
            after = key(wf)
            tried[(before, move)] = Try(accepted, after, main_commit(wf) != main, listed(where, move), had_runs, data)
            if accepted and after not in seen:
                seen[after] = wf
                queue.append(wf)
    return tried


def test_every_state_is_reached_but_main_draft(walk):
    reached = {k[0] for k, _ in walk} | {t.after[0] for t in walk.values()}
    assert reached == set(agent_state.MOVES) - {"main_draft"}, reached


def test_a_move_the_state_does_not_list_is_refused_and_changes_nothing(walk):
    bad = [(k, m, t.result) for (k, m), t in walk.items()
           if not t.listed and (k[0], m) not in TOLERATED and (t.accepted or t.after != k or t.main_moved)]
    assert not bad, bad


def test_main_changes_only_by_a_merge_or_planning(walk):
    bad = [(k, m) for (k, m), t in walk.items() if t.main_moved and m != "agent_merge:merge" and t.had_runs]
    assert not bad, bad


def test_every_result_says_where_the_agent_is_next(walk):
    bad = [(k, m, t.result.get("next")) for (k, m), t in walk.items()
           if m != "edit plan.yaml" and (t.result.get("next") or {}).get("state") != t.after[0]]  # an edit has no result
    assert not bad, bad


def test_every_state_has_a_way_out(walk):
    states = {k for k, _ in walk}
    stuck = [k for k in states if not any(t.accepted and t.after != k for (s, _m), t in walk.items() if s == k)]
    assert not stuck, stuck
