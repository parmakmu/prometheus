"""Walk the workflow state machine (docs/state-machine.md) with the real scripts.

From each state reached, every move is tried on a copy of the workflow. Checked: a move the
state's `moves` (workflow_state.MOVES) does not list is refused and changes nothing; `main`
changes only by a merge, or by planning before the first run; every result carries `next`, the
state the workflow is in afterwards; and every state reached has a way out. States are explored
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

sys.path.insert(0, str(SKILL / "scripts"))
import compile_wave
import plan_edit
import run_log
import workflow_git
import workflow_state
from utils import read_yaml, write_yaml

COMPILE = typer.Typer(add_completion=False)
COMPILE.command()(compile_wave.main)
CRITIC = '{"run_id": "%s", "verdict": "pass", "agent": "worker"}'  # the plan's agents list no plan-critic
# accepted though the state's moves leave it out (docs/state-machine.md, "Allowed, outside the table")
TOLERATED = {("between_waves", "workflow_record_critique")}
ids = count(1)


def git(wf, *args):
    return subprocess.run(["git", "-C", str(wf), *args], capture_output=True, text=True, check=False).stdout.strip()


def running_launch(wf):
    for run_yaml in (wf / "runs").glob("*/run.yaml"):
        text = run_yaml.read_text(encoding="utf-8")
        if "status: running" in text:
            return text.split("subagent_run_id: ")[-1].split("\n")[0].strip()
    return None


def invoke(wf, move):
    """Run one move in the workflow; (accepted, its JSON)."""
    if move == "edit plan.yaml":  # a direct edit (the extension's guard allows it where the state lists it)
        plan = read_yaml(wf / "plan.yaml", {})
        plan["goal"] = f"{plan.get('goal', '')} (edited)"
        write_yaml(wf / "plan.yaml", plan)
        return True, {}
    if move == "workflow_record_critique":
        run_id = f"walk-critic-{next(ids)}"
        fake_critic(run_id, agent="worker")
        app, args = plan_edit.app, ["critique", "--result", CRITIC % run_id]
    elif move == "workflow_log_start":
        app, args = run_log.app, ["start", "--wave", "w1", "--subagent-run-id", f"walk-{next(ids)}"]
    elif move == "workflow_log_finish":
        if launched := running_launch(wf):
            record = Path(os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / launched
            record.mkdir(parents=True, exist_ok=True)
            (record / "status.json").write_text(json.dumps({"state": "complete", "workflow": {"value": {"status": "done", "children": {}}}}))
        app, args = run_log.app, ["finish", "--wave", "w1"]
    else:
        app, args = {
            "workflow_new_run": (run_log.app, ["new"]),
            "workflow_compile": (COMPILE, []),
            "workflow_record_decision": (run_log.app, ["decision", "--stage", "count", "--answer", "true"]),
            "workflow_replan": (plan_edit.app, ["replan", "--change", '{"note": "walk"}']),
            "workflow_stop_run": (run_log.app, ["stop"]),
            "workflow_merge:merge": (workflow_git.app, ["merge", "--action", "merge"]),
            "workflow_merge:keep": (workflow_git.app, ["merge", "--action", "keep"]),
            "workflow_merge:discard": (workflow_git.app, ["merge", "--action", "discard"]),
            "/workflow-revise": (workflow_git.app, ["begin-change"]),
            "/workflow-improve": (workflow_git.app, ["begin-change", "--kind", "improve"]),
        }[move]
    here = Path.cwd()
    os.chdir(wf)
    try:
        result = CliRunner().invoke(app, args)
    finally:
        os.chdir(here)
    data = json.loads(result.output)
    return result.exit_code == 0 and data.get("ok") is not False, data


MOVES = ["workflow_new_run", "workflow_compile", "workflow_log_start", "workflow_log_finish", "workflow_record_decision",
         "workflow_replan", "workflow_record_critique", "workflow_stop_run", "workflow_merge:merge", "workflow_merge:keep",
         "workflow_merge:discard", "/workflow-revise", "/workflow-improve", "edit plan.yaml"]


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
    if move.startswith("workflow_merge:"):
        return "workflow_merge" in where["moves"] and move.split(":")[1] in where.get("merge", [])
    return move in where["moves"]


def key(wf):
    return workflow_state.state(wf)["state"], bool(list((wf / "runs").glob("*/run.yaml")))


@pytest.fixture(scope="module")
def walk(tmp_path_factory):
    """Every (state, move) tried: {(state key, move): Try}."""
    tmp = tmp_path_factory.mktemp("walk")
    roots = []
    for status in ("ready", "draft"):
        here = Path.cwd()
        r = subprocess.run([sys.executable, SKILL / "scripts" / "scaffold_workflow.py", status, "--root", tmp, "--request", "x"],
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
        where = workflow_state.state(src)
        for move in MOVES:
            if move == "edit plan.yaml" and not listed(where, move):
                continue  # refused by the extension's guard, not a script: nothing here to try
            wf = tmp / f"c{next(copies)}"
            shutil.copytree(src, wf, symlinks=True)
            main, had_runs = git(wf, "rev-parse", "main"), before[1]
            accepted, data = invoke(wf, move)
            after = key(wf)
            tried[(before, move)] = Try(accepted, after, git(wf, "rev-parse", "main") != main, listed(where, move), had_runs, data)
            if accepted and after not in seen:
                seen[after] = wf
                queue.append(wf)
    return tried


def test_every_state_is_reached_but_main_draft(walk):
    reached = {k[0] for k, _ in walk} | {t.after[0] for t in walk.values()}
    assert reached == set(workflow_state.MOVES) - {"main_draft"}, reached


def test_a_move_the_state_does_not_list_is_refused_and_changes_nothing(walk):
    bad = [(k, m, t.result) for (k, m), t in walk.items()
           if not t.listed and (k[0], m) not in TOLERATED and (t.accepted or t.after != k or t.main_moved)]
    assert not bad, bad


def test_main_changes_only_by_a_merge_or_planning(walk):
    bad = [(k, m) for (k, m), t in walk.items() if t.main_moved and m != "workflow_merge:merge" and t.had_runs]
    assert not bad, bad


def test_every_result_says_where_the_workflow_is_next(walk):
    bad = [(k, m, t.result.get("next")) for (k, m), t in walk.items()
           if m != "edit plan.yaml" and (t.result.get("next") or {}).get("state") != t.after[0]]  # an edit has no result
    assert not bad, bad


def test_every_state_has_a_way_out(walk):
    states = {k for k, _ in walk}
    stuck = [k for k in states if not any(t.accepted and t.after != k for (s, _m), t in walk.items() if s == k)]
    assert not stuck, stuck
