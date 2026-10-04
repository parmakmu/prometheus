"""A run finished end to end: a one-stage agent whose last wave completes, so
`run_log finish` returns what the parent needs for the final report. (The
scenario in test_agent.py stops or fails its runs; none completes.)"""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import SKILL, fake_critic, git_in

PLAN = """\
version: 1
revision: 1
status: ready
request: "count the lines"
goal: Count the lines of a file.
done_when:
  - counts.txt holds the line count.
  - The count matches the file.
agents:
  checked: test
  available: [worker]
stages:
  - key: count
    title: Count the lines
    kind: deterministic
    kind_reason: A program can check a count.
    pattern: script
    agent: worker
    out:
      - id: counts
        path: counts.txt
    verify:
      check: counts.txt holds one number
      gate: "true"
"""


def ready_agent(root, run_py, slug):
    """A scaffolded agent holding the ready one-stage plan, not yet committed (planning on main);
    returns (agent, its scripts folder)."""
    r = run_py([SKILL / "scripts" / "scaffold_agent.py", slug, "--root", root, "--request", "count the lines"])
    assert r.data and r.data["ok"], r
    wf = root / slug
    (wf / "plan.yaml").write_text(PLAN, encoding="utf-8")
    return wf, wf / ".pi" / "skills" / "agent" / "scripts"


# the tests reading `finished` share one finished run: keep them on one worker (the rest spread out)
FINISHED = pytest.mark.xdist_group("run-finish")


@pytest.fixture(scope="module")
def finished(tmp_path_factory, run_py):
    tmp = tmp_path_factory.mktemp("finish")
    wf, k = ready_agent(tmp, run_py, "tiny")
    pis = tmp / "pi-subagents"
    env = {**os.environ, "PI_SUBAGENTS_TEMP_ROOT": str(pis)}

    def run(*args):
        return run_py([k / args[0], *args[1:]], cwd=wf, env=env)

    assert run("run_log.py", "new").data["ok"]
    assert run("compile_wave.py").data["ok"]
    assert run("run_log.py", "start", "--wave", "w1", "--subagent-run-id", "wf-1", "--mission-id", "m-1").data["ok"]
    record = pis / "async-subagent-runs" / "wf-1"
    record.mkdir(parents=True)
    returned = {"wave": "w1", "revision": 1, "status": "done",
                "children": {"count": {"runId": "c-1", "status": "completed", "outputReference": "counts.txt"}}}
    (record / "status.json").write_text(json.dumps({"state": "complete", "workflow": {"value": returned},
                                                    "totalCost": {"costUsd": 0.002}}), encoding="utf-8")
    r = run("run_log.py", "finish", "--wave", "w1")
    assert r.data, r
    return r.data


@FINISHED
def test_the_last_wave_finishes_the_run(finished):
    assert (finished["wave_status"], finished["run_status"]) == ("done", "done")


@FINISHED
def test_finish_returns_what_the_parent_needs_not_the_log_entry(finished):
    assert "entry" not in finished and "commit" not in finished
    assert finished["next_wave"] is None  # the run is done


@FINISHED
def test_finish_returns_done_when_for_the_final_report(finished):
    # so the parent reports each criterion without reading the plan
    assert finished["done_when"] == ["counts.txt holds the line count.", "The count matches the file."]


@FINISHED
def test_finish_returns_the_mission_close_call(finished):
    assert (finished["mission_close"]["missionId"], finished["mission_close"]["missionStatus"]) == ("m-1", "completed")


def test_a_leftover_run_branch_is_not_reused(tmp_path, run_py):
    # runs/ is not in git: deleting a kept run's folder frees its id, but its branch run/<id> stays
    wf, k = ready_agent(tmp_path, run_py, "left")
    today = datetime.datetime.now().astimezone().date().isoformat()
    subprocess.run(["git", "branch", f"run/{today}-1"], cwd=wf, check=True)
    r = run_py([k / "run_log.py", "new"], cwd=wf)
    assert r.data["run"] == f"{today}-2", r
    # a run whose branch already exists is refused before the plan changes, and can still stop
    subprocess.run(["git", "branch", f"run/{today}-2"], cwd=wf, check=True)
    r = run_py([k / "plan_edit.py", "replan", "--change", '{"note": "x"}'], cwd=wf)
    assert "already exists" in r.data["error"], r
    assert subprocess.run(["git", "status", "--porcelain"], cwd=wf, capture_output=True, text=True, check=True).stdout == ""
    r = run_py([k / "run_log.py", "stop"], cwd=wf)
    assert r.data["run_status"] == "stopped" and "already exists" in r.data["git_note"], r


def test_a_replan_gets_two_critic_passes(tmp_path, run_py):
    # fixing a finding bumps the revision; the cap must hold across revisions
    wf, k = ready_agent(tmp_path, run_py, "crit")
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]

    def record(run_id):
        fake_critic(run_id)
        return run_py([k / "plan_edit.py", "critique", "--result", json.dumps({"run_id": run_id, "verdict": "revise"})], cwd=wf)

    assert record("c1").data["ok"] and record("c2").data["ok"]
    r = record("c3")
    assert "already had 2 critic passes" in r.data["error"], r
    assert record("c2").data["ok"]  # recording the last pass again, to reject what is left, still works
    r = run_py([k / "plan_edit.py", "critique", "--result", json.dumps({"run_id": "c2", "verdict": "revise", "passes": 3})], cwd=wf)
    assert "passes is 1 or 2" in r.data["error"], r  # a model that counted its own passes


def test_improve_and_revise_get_their_own_branches(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "kinds")
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]
    assert run_py([k / "run_log.py", "stop"], cwd=wf).data["ok"]  # a run exists, none is active
    r = run_py([k / "agent_git.py", "begin-change", "--kind", "improve"], cwd=wf)
    assert r.data["ok"] and r.data["branch"].startswith("improve/") and r.data["mode"] == "improve", r
    again = run_py([k / "agent_git.py", "begin-change", "--kind", "improve"], cwd=wf)  # already open: continue it
    assert again.data["branch"] == r.data["branch"] and again.data["continued"], again
    other = run_py([k / "agent_git.py", "begin-change"], cwd=wf)  # a revise does not land on the improve branch
    assert "open /agent-improve change" in errors(other), other
    assert run_py([k / "agent_git.py", "review"], cwd=wf).data["review"]["branch"] == r.data["branch"]


def test_a_change_that_leaves_the_plan_alone_merges_without_the_critic(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "lesson")
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"] and run_py([k / "run_log.py", "stop"], cwd=wf).data["ok"]
    r = run_py([k / "agent_git.py", "begin-change", "--kind", "improve"], cwd=wf)
    assert r.data["ok"] and r.data["next"]["state"] == "change_ready", r  # the plan is not drafted
    with (wf / ".pi" / "APPEND_SYSTEM.md").open("a", encoding="utf-8") as f:
        f.write("\n## Lessons for this agent\n- Ask once.\n")
    r = run_py([k / "agent_git.py", "merge", "--action", "merge", "--summary", "Adds a lesson: ask once."], cwd=wf)
    assert r.data["ok"] and (r.data["revision"], r.data["status"]) == (1, "ready"), r
    log = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=wf, capture_output=True, text=True, check=True).stdout.strip()
    assert log.startswith("r1: improve/")
    # the body says what changed: the summary and the files, not the review's own bookkeeping commit
    body = subprocess.run(["git", "log", "-1", "--format=%b"], cwd=wf, capture_output=True, text=True, check=True).stdout
    assert "Adds a lesson: ask once." in body and ".pi/APPEND_SYSTEM.md" in body and "1 file changed" in body, body
    assert "Uncommitted changes at review" not in body, body


def test_a_merge_is_one_commit_on_main_and_deletes_the_branch(tmp_path, run_py):
    # main gets one commit per merged branch, however many the branch made, and stays linear
    r = run_py([SKILL / "scripts" / "scaffold_agent.py", "reb", "--root", tmp_path, "--request", "x"])
    assert r.data and r.data["ok"], r
    wf = tmp_path / "reb"

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              cwd=wf, capture_output=True, text=True, check=True).stdout

    (wf / "plan.yaml").write_text(PLAN, encoding="utf-8")
    git("commit", "-qam", "ready plan")
    git("switch", "-q", "-c", "revise/2026-01-01-1")
    (wf / "gates" / "on_branch.py").write_text("", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "branch work")
    (wf / "gates" / "on_branch_too.py").write_text("", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "more branch work")
    git("switch", "-q", "main")
    (wf / "scripts" / "on_main.py").write_text("", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "main moved")
    git("switch", "-q", "revise/2026-01-01-1")
    r = run_py([wf / ".pi" / "skills" / "agent" / "scripts" / "agent_git.py", "merge", "--action", "merge"], cwd=wf)
    assert r.data and r.data["ok"], r
    assert git("branch", "--list").split() == ["*", "main"]
    assert git("rev-list", "--merges", "--count", "main").strip() == "0"
    subject, *body = git("log", "-1", "--format=%B").strip().splitlines()
    assert subject == "r1: revise/2026-01-01-1" and body[-2:] == ["- branch work", "- more branch work"]
    assert git("log", "--format=%s", "main").splitlines()[1] == "main moved"  # the branch's commits are not on main
    assert (wf / "gates" / "on_branch.py").exists() and (wf / "scripts" / "on_main.py").exists()


def test_a_merge_that_conflicts_with_main_leaves_both_unchanged(tmp_path, run_py):
    r = run_py([SKILL / "scripts" / "scaffold_agent.py", "clash", "--root", tmp_path, "--request", "x"])
    assert r.data and r.data["ok"], r
    wf = tmp_path / "clash"

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              cwd=wf, capture_output=True, text=True, check=True).stdout

    (wf / "plan.yaml").write_text(PLAN, encoding="utf-8")
    git("commit", "-qam", "ready plan")
    git("switch", "-q", "-c", "revise/2026-01-01-1")
    (wf / "gates" / "g.py").write_text("branch\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "branch work")
    git("switch", "-q", "main")
    (wf / "gates" / "g.py").write_text("main\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "main moved")
    main = git("rev-parse", "main")
    git("switch", "-q", "revise/2026-01-01-1")
    r = run_py([wf / ".pi" / "skills" / "agent" / "scripts" / "agent_git.py", "merge", "--action", "merge"], cwd=wf)
    assert "conflicts" in errors(r), r
    assert git("symbolic-ref", "--short", "HEAD").strip() == "revise/2026-01-01-1"
    assert git("rev-parse", "main") == main and not git("status", "--porcelain").strip()
    assert (wf / "gates" / "g.py").read_text(encoding="utf-8") == "branch\n"


def test_refresh_keeps_the_agents_packages(tmp_path, run_py):
    # gates and programs add packages to pyproject.toml; a refresh must not drop them
    scaffold = SKILL / "scripts" / "scaffold_agent.py"
    assert run_py([scaffold, "pkgs", "--root", tmp_path, "--request", "x"]).data["ok"]
    pyproject = tmp_path / "pkgs" / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(text.replace('dependencies = ["typer>=0.12", ', 'dependencies = ["openpyxl>=3.1", '), encoding="utf-8")
    r = run_py([scaffold, "pkgs", "--root", tmp_path, "--refresh"])
    assert r.data and r.data["ok"], r
    text = pyproject.read_text(encoding="utf-8")
    assert '"openpyxl>=3.1"' in text and '"typer>=0.12"' in text  # kept, and the skill's package added back


def test_refresh_brings_back_a_moved_agents_conversations(tmp_path, run_py):
    # Pi lists a session folder's conversations only for the cwd each records
    scaffold = SKILL / "scripts" / "scaffold_agent.py"
    assert run_py([scaffold, "moved", "--root", tmp_path, "--request", "x"]).data["ok"]
    agent = tmp_path / "moved"
    sessions = agent / ".pi" / "sessions"
    sessions.mkdir(exist_ok=True)
    talk = sessions / "2026-10-02T22-27-52-997Z_x.jsonl"
    body = '{"type":"message","id":"a","message":{"role":"user","content":"hi"}}\n'
    talk.write_text(json.dumps({"type": "session", "id": "x", "cwd": str(tmp_path / "elsewhere" / "moved")}) + "\n" + body, encoding="utf-8")
    assert run_py([scaffold, "moved", "--root", tmp_path, "--refresh"]).data["ok"]
    first, rest = talk.read_text(encoding="utf-8").split("\n", 1)
    assert json.loads(first)["cwd"] == str(agent.resolve()) and rest == body


def test_refresh_raises_an_old_python_floor_to_the_templates(tmp_path, run_py):
    scaffold = SKILL / "scripts" / "scaffold_agent.py"
    assert run_py([scaffold, "floor", "--root", tmp_path, "--request", "x"]).data["ok"]
    pyproject = tmp_path / "floor" / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace('requires-python = ">=3.14"', 'requires-python = ">=3.10"'), encoding="utf-8")
    r = run_py([scaffold, "floor", "--root", tmp_path, "--refresh"])
    assert r.data and r.data["ok"], r
    assert 'requires-python = ">=3.14"' in pyproject.read_text(encoding="utf-8")
    assert any("requires-python" in w and "updated" in w for w in r.data["written"]), r.data["written"]


# --- the state machine's refusals (docs/state-machine.md) --------------------------

def errors(r):
    return r.data.get("error", "") if r.data else r.stderr


def test_a_run_needs_a_ready_plan(tmp_path, run_py):
    r = run_py([SKILL / "scripts" / "scaffold_agent.py", "draft", "--root", tmp_path, "--request", "x"])
    assert r.data and r.data["ok"], r
    k = tmp_path / "draft" / ".pi" / "skills" / "agent" / "scripts"
    r = run_py([k / "run_log.py", "new"], cwd=tmp_path / "draft")
    assert "not ready" in errors(r) and not list((tmp_path / "draft" / "runs").glob("*/run.yaml")), r


def test_improve_needs_a_run(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "early")
    assert "nothing to improve" in errors(run_py([k / "agent_git.py", "begin-change", "--kind", "improve"], cwd=wf))
    assert run_py([k / "agent_git.py", "begin-change"], cwd=wf).data["mode"] == "planning"  # revise: plan on main


def test_leftovers_on_main_go_to_a_branch_once_a_run_exists(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "left2")
    git = git_in(wf)
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]  # the first run commits planning on main
    assert run_py([k / "run_log.py", "stop"], cwd=wf).data["ok"]
    main = git("rev-parse", "main").stdout
    (wf / "gates" / "stray.py").write_text("", encoding="utf-8")  # changed outside the extension's guard
    r = run_py([k / "run_log.py", "new"], cwd=wf)
    assert r.data["ok"] and git("branch", "--show-current").stdout.strip() == f"run/{r.data['run']}", r
    assert git("rev-parse", "main").stdout == main
    assert run_py([k / "run_log.py", "stop"], cwd=wf).data["ok"]
    assert run_py([k / "agent_git.py", "merge", "--action", "discard"], cwd=wf).data["ok"]
    (wf / "gates" / "stray2.py").write_text("", encoding="utf-8")
    r = run_py([k / "agent_git.py", "begin-change"], cwd=wf)
    assert r.data["ok"] and git("rev-parse", "main").stdout == main, r
    assert "gates/stray2.py" in git("show", "--name-only", "--format=", "HEAD").stdout


def test_replan_critique_and_decisions_wait_for_the_running_wave(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "busy")
    env = {**os.environ, "PI_SUBAGENTS_TEMP_ROOT": str(tmp_path / "pi-subagents")}
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]
    assert run_py([k / "compile_wave.py"], cwd=wf, env=env).data["ok"]
    assert run_py([k / "run_log.py", "start", "--wave", "w1", "--subagent-run-id", "wf-1"], cwd=wf).data["ok"]
    plan = (wf / "plan.yaml").read_text(encoding="utf-8")
    for args in (["plan_edit.py", "replan", "--change", '{"note": "x"}'],
                 ["plan_edit.py", "critique", "--result", '{"run_id": "c1", "verdict": "pass"}'],
                 ["run_log.py", "decision", "--stage", "count", "--answer", "true"]):
        assert "running" in errors(run_py([k / args[0], *args[1:]], cwd=wf)), args
    assert (wf / "plan.yaml").read_text(encoding="utf-8") == plan


def test_a_launch_nobody_recorded_is_not_compiled_again(tmp_path, run_py):
    # the session ended between the launch and agent_log_start
    wf, k = ready_agent(tmp_path, run_py, "twice")
    pis = tmp_path / "pi-subagents"
    env = {**os.environ, "PI_SUBAGENTS_TEMP_ROOT": str(pis)}
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]
    r = run_py([k / "compile_wave.py"], cwd=wf, env=env)
    script = Path(r.data["call"]["workflow"])
    record = pis / "async-subagent-runs" / "wf-7"
    record.mkdir(parents=True)
    (record / "status.json").write_text(json.dumps({"runId": "wf-7", "state": "running", "workflow": {
        "scriptDigest": hashlib.sha256(script.read_bytes()).hexdigest()}}), encoding="utf-8")
    r = run_py([k / "compile_wave.py"], cwd=wf, env=env)
    assert "already launched as pi-subagents run wf-7" in errors(r), r
    assert run_py([k / "run_log.py", "start", "--wave", "w1", "--subagent-run-id", "wf-7"], cwd=wf).data["ok"]


def test_a_dead_run_still_marked_running_names_the_way_out(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "dead")
    pis = tmp_path / "pi-subagents"
    env = {**os.environ, "PI_SUBAGENTS_TEMP_ROOT": str(pis)}
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]
    assert run_py([k / "compile_wave.py"], cwd=wf, env=env).data["ok"]
    assert run_py([k / "run_log.py", "start", "--wave", "w1", "--subagent-run-id", "wf-9"], cwd=wf).data["ok"]
    gone = subprocess.Popen(["true"])
    gone.wait()
    (pis / "async-subagent-runs" / "wf-9").mkdir(parents=True)
    (pis / "async-subagent-runs" / "wf-9" / "status.json").write_text(json.dumps({"state": "running", "pid": gone.pid}), encoding="utf-8")
    r = run_py([k / "run_log.py", "finish", "--wave", "w1"], cwd=wf, env=env)
    assert "process" in errors(r) and "result {}" in errors(r), r
    r = run_py([k / "run_log.py", "finish", "--wave", "w1", "--status", "failed", "--result", "{}"], cwd=wf, env=env)
    assert r.data["ok"] and r.data["wave_status"] == "pending", r


def test_an_edit_after_the_critique_needs_the_critic_again(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "edited")
    critic = '{"run_id": "%s", "verdict": "pass", "agent": "worker"}'  # the plan's agents list no plan-critic
    fake_critic("c1", agent="worker")
    fake_critic("c2", agent="worker")
    r = run_py([k / "plan_edit.py", "critique", "--result", critic % "c1"], cwd=wf)
    assert r.data["ready"], r
    assert run_py([k / "validate_plan.py", "plan.yaml"], cwd=wf).data["valid"]
    plan = wf / "plan.yaml"
    plan.write_text(plan.read_text(encoding="utf-8").replace("Count the lines of a file.", "Count the words."), encoding="utf-8")
    r = run_py([k / "validate_plan.py", "plan.yaml"], cwd=wf)
    assert not r.data["valid"] and any("changed after the critic" in e for e in r.data["errors"]), r.data
    assert run_py([k / "plan_edit.py", "critique", "--result", critic % "c2"], cwd=wf).data["ready"]


def test_refresh_keeps_a_higher_python_floor(tmp_path, run_py):
    # an agent that raised its own floor (a package needing a newer Python) keeps it
    scaffold = SKILL / "scripts" / "scaffold_agent.py"
    assert run_py([scaffold, "high", "--root", tmp_path, "--request", "x"]).data["ok"]
    pyproject = tmp_path / "high" / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace('requires-python = ">=3.14"', 'requires-python = ">=3.15"'), encoding="utf-8")
    r = run_py([scaffold, "high", "--root", tmp_path, "--refresh"])
    assert r.data and r.data["ok"], r
    assert 'requires-python = ">=3.15"' in pyproject.read_text(encoding="utf-8")


def test_an_agents_copy_of_the_skill_cannot_scaffold(tmp_path, run_py):
    # the templates are Prometheus's, outside the skill: a copy without them must say so, not write nothing
    _wf, k = ready_agent(tmp_path, run_py, "copy")
    r = run_py([k / "scaffold_agent.py", "other", "--root", tmp_path, "--request", "x"])
    assert "no agent template" in errors(r) and not (tmp_path / "other").exists(), r


# --- launches, critiques and reviews ------------------------------------------------

TWO = PLAN + """\
  - key: count2
    title: Count the lines again
    kind: deterministic
    kind_reason: A program can check a count.
    pattern: script
    agent: worker
    out:
      - id: counts2
        path: counts2.txt
    verify:
      check: counts2.txt holds one number
      gate: "true"
"""
PAUSED = TWO.replace("    agent: worker\n    out:\n      - id: counts\n", "    agent: worker\n    pause_after: true\n    out:\n      - id: counts\n", 1) \
            .replace("    title: Count the lines again\n", "    title: Count the lines again\n    needs: [count]\n", 1)


def record_launch(root, run_id, returned):
    folder = Path(os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(json.dumps({"runId": run_id, "state": "complete", "workflow": {"value": returned}}), encoding="utf-8")


def test_a_late_recorded_launch_finds_its_mission(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "mission")
    missions = Path(os.environ["PI_CODING_AGENT_DIR"]) / "missions" / "projects" / "abc"
    missions.mkdir(parents=True, exist_ok=True)
    (missions / "m-42.json").write_text(json.dumps({"id": "m-42", "runs": [{"runId": "wf-late"}]}), encoding="utf-8")
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]
    assert run_py([k / "compile_wave.py"], cwd=wf).data["ok"]
    assert run_py([k / "run_log.py", "start", "--wave", "w1", "--subagent-run-id", "wf-late"], cwd=wf).data["ok"]
    assert "mission_id: m-42" in next((wf / "runs").glob("*/run.yaml")).read_text(encoding="utf-8")


def test_a_run_needs_a_plan_that_validates(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "stale")
    fake_critic("s1", agent="worker")
    assert run_py([k / "plan_edit.py", "critique", "--result", '{"run_id": "s1", "verdict": "pass", "agent": "worker"}'], cwd=wf).data["ready"]
    plan = wf / "plan.yaml"
    plan.write_text(plan.read_text(encoding="utf-8").replace("Count the lines of a file.", "Count words."), encoding="utf-8")
    r = run_py([k / "run_log.py", "new"], cwd=wf)
    assert "changed after the critic" in r.data["error"], r


def test_a_critique_needs_a_completed_critic_run_and_not_main_after_a_run(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "critics")

    def critique(run_id):
        return run_py([k / "plan_edit.py", "critique", "--result", json.dumps({"run_id": run_id, "verdict": "pass", "agent": "worker"})], cwd=wf)

    assert "no record of a critic run nobody-ran" in errors(critique("nobody-ran"))
    fake_critic("half", agent="worker", state="running")
    assert "record the critic's result only once it has completed" in errors(critique("half"))
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]
    assert run_py([k / "run_log.py", "stop"], cwd=wf).data["ok"]  # nothing committed: back on main
    fake_critic("late", agent="worker")
    assert "main changes only by a reviewed merge" in errors(critique("late"))


def test_the_next_wave_waits_for_the_users_review_and_a_refused_compile_commits_nothing(tmp_path, run_py):
    wf, k = ready_agent(tmp_path, run_py, "review")
    (wf / "plan.yaml").write_text(PAUSED, encoding="utf-8")
    git = git_in(wf)
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]
    r = run_py([k / "compile_wave.py"], cwd=wf)
    assert r.data["ok"] and r.data["ends_with"] == "review", r
    assert run_py([k / "run_log.py", "start", "--wave", "w1", "--subagent-run-id", "rv-1"], cwd=wf).data["ok"]
    record_launch(wf, "rv-1", {"status": "done", "children": {}})
    assert run_py([k / "run_log.py", "finish", "--wave", "w1"], cwd=wf).data["wave_status"] == "done"
    (wf / "gates" / "later.py").write_text("", encoding="utf-8")  # pending change: committed only with a launch
    head = git("rev-parse", "HEAD").stdout
    r = run_py([k / "compile_wave.py"], cwd=wf)
    assert r.data["preflight"]["review_needed"]["wave"] == "w1", r
    assert git("rev-parse", "HEAD").stdout == head and "gates/later.py" in git("status", "--porcelain").stdout
    assert r.data["next"]["state"] == "between_waves" and "agent_compile" in r.data["next"]["moves"]
    assert r.data["next"]["last_launch"] == {"wave": "w1", "status": "done", "subagent_run_id": "rv-1"}
    r = run_py([k / "compile_wave.py", "--reviewed"], cwd=wf)
    assert r.data["ok"] and r.data["wave"] == "w2" and git("rev-parse", "HEAD").stdout != head, r


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_a_relaunch_runs_only_the_children_that_did_not_complete(tmp_path, run_py, run_node):
    wf, k = ready_agent(tmp_path, run_py, "relaunch")
    (wf / "plan.yaml").write_text(TWO, encoding="utf-8")
    mock = Path(__file__).parent / "mock-subagents.mjs"
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]
    compiled = Path(run_py([k / "compile_wave.py"], cwd=wf).data["call"]["workflow"]).with_suffix(".json")
    first = run_node([mock, compiled, "--fail", "count2"]).data["result"]
    assert first["status"] == "failed" and first["children"]["count"]["status"] == "completed", first
    assert run_py([k / "run_log.py", "start", "--wave", "w1", "--subagent-run-id", "rl-1"], cwd=wf).data["ok"]
    record_launch(wf, "rl-1", first)
    assert run_py([k / "run_log.py", "finish", "--wave", "w1"], cwd=wf).data["wave_status"] == "pending"
    r = run_py([k / "compile_wave.py"], cwd=wf)
    assert r.data["ok"] and any("count completed in the last launch" in n for n in r.data["notes"]), r
    again = run_node([mock, compiled])
    assert again.data["result"]["status"] == "done", again
    assert [e["key"] for e in again.data["log"]] == ["count2"]  # count's earlier result stands
    assert again.data["result"]["children"]["count"] == {**first["children"]["count"], "reused": True}


def test_the_list_gives_each_agent_its_state_and_recent_runs(tmp_path, run_py):
    # the web UI's dashboard reads these: a state for the "needs you" badge, and runs with their cost
    wf, k = ready_agent(tmp_path, run_py, "listed")
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"] and run_py([k / "run_log.py", "stop"], cwd=wf).data["ok"]
    r = run_py([k / "list_agents.py", "--root", tmp_path])
    (item,) = [w for w in r.data if w["slug"] == "listed"]
    assert item["state"] in ("ready", "branch_review"), item
    (run,) = item["recent_runs"]
    assert run["status"] == "stopped" and run["cost_usd"] == 0 and run["id"] == item["last_run"]["id"], run
