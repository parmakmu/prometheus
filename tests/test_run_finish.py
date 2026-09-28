"""A run finished end to end: a one-stage workflow whose last wave completes, so
`run_log finish` returns what the parent needs for the final report. (The
scenario in test_workflow.py stops or fails its runs; none completes.)"""
import datetime
import json
import os
import subprocess

import pytest
from conftest import SKILL

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


@pytest.fixture(scope="module")
def finished(tmp_path_factory, run_py):
    tmp = tmp_path_factory.mktemp("finish")
    r = run_py([SKILL / "scripts" / "scaffold_workflow.py", "tiny", "--root", tmp, "--request", "count the lines"])
    assert r.data and r.data["ok"], r
    wf = tmp / "tiny"
    (wf / "plan.yaml").write_text(PLAN, encoding="utf-8")
    k = wf / ".pi" / "skills" / "workflow" / "scripts"
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


def test_the_last_wave_finishes_the_run(finished):
    assert (finished["wave_status"], finished["run_status"]) == ("done", "done")


def test_finish_returns_done_when_for_the_final_report(finished):
    # so the parent reports each criterion without reading the plan
    assert finished["done_when"] == ["counts.txt holds the line count.", "The count matches the file."]


def test_finish_returns_the_mission_close_call(finished):
    assert (finished["mission_close"]["missionId"], finished["mission_close"]["missionStatus"]) == ("m-1", "completed")


def test_a_leftover_run_branch_is_not_reused(tmp_path, run_py):
    # runs/ is not in git: deleting a kept run's folder frees its id, but its branch run/<id> stays
    r = run_py([SKILL / "scripts" / "scaffold_workflow.py", "left", "--root", tmp_path, "--request", "x"])
    assert r.data and r.data["ok"], r
    wf = tmp_path / "left"
    k = wf / ".pi" / "skills" / "workflow" / "scripts"
    today = datetime.datetime.now().astimezone().date().isoformat()
    subprocess.run(["git", "branch", f"run/{today}-1"], cwd=wf, check=True)
    r = run_py([k / "run_log.py", "new"], cwd=wf)
    assert r.data["run"] == f"{today}-2", r
    # a run whose branch already exists is refused before the plan changes, and can still stop
    subprocess.run(["git", "branch", f"run/{today}-2"], cwd=wf, check=True)
    r = run_py([k / "plan_edit.py", "replan", "--change", '{"note": "x"}'], cwd=wf)
    assert "already exists" in " ".join(r.data["errors"]), r
    assert subprocess.run(["git", "status", "--porcelain"], cwd=wf, capture_output=True, text=True, check=True).stdout == ""
    r = run_py([k / "run_log.py", "stop"], cwd=wf)
    assert r.data["run_status"] == "stopped" and "already exists" in r.data["git_note"], r


def test_a_replan_gets_two_critic_passes(tmp_path, run_py):
    # fixing a finding bumps the revision; the cap must hold across revisions
    r = run_py([SKILL / "scripts" / "scaffold_workflow.py", "crit", "--root", tmp_path, "--request", "x"])
    assert r.data and r.data["ok"], r
    wf = tmp_path / "crit"
    k = wf / ".pi" / "skills" / "workflow" / "scripts"
    assert run_py([k / "run_log.py", "new"], cwd=wf).data["ok"]

    def record(run_id):
        return run_py([k / "plan_edit.py", "critique", "--result", json.dumps({"run_id": run_id, "verdict": "revise"})], cwd=wf)

    assert record("c1").data["ok"] and record("c2").data["ok"]
    r = record("c3")
    assert "already had 2 critic passes" in " ".join(r.data["errors"]), r
    assert record("c2").data["ok"]  # recording the last pass again, to reject what is left, still works
    r = run_py([k / "plan_edit.py", "critique", "--result", json.dumps({"run_id": "c2", "verdict": "revise", "passes": 3})], cwd=wf)
    assert "passes is 1 or 2" in " ".join(r.data["errors"]), r  # a model that counted its own passes


def test_merging_after_main_moved_rebases_and_deletes_the_branch(tmp_path, run_py):
    # history stays linear: no merge commits, and a merged branch does not linger
    r = run_py([SKILL / "scripts" / "scaffold_workflow.py", "reb", "--root", tmp_path, "--request", "x"])
    assert r.data and r.data["ok"], r
    wf = tmp_path / "reb"

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              cwd=wf, capture_output=True, text=True, check=True).stdout

    (wf / "plan.yaml").write_text(PLAN, encoding="utf-8")
    git("commit", "-qam", "ready plan")
    git("switch", "-q", "-c", "change/2026-01-01-1")
    (wf / "gates" / "on_branch.py").write_text("", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "branch work")
    git("switch", "-q", "main")
    (wf / "scripts" / "on_main.py").write_text("", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "main moved")
    git("switch", "-q", "change/2026-01-01-1")
    r = run_py([wf / ".pi" / "skills" / "workflow" / "scripts" / "workflow_git.py", "merge", "--action", "merge"], cwd=wf)
    assert r.data and r.data["ok"] and "rebased" in r.data["note"], r
    assert git("branch", "--list").split() == ["*", "main"]
    assert git("rev-list", "--merges", "--count", "main").strip() == "0"
    assert (wf / "gates" / "on_branch.py").exists() and (wf / "scripts" / "on_main.py").exists()


def test_refresh_keeps_the_workflows_packages(tmp_path, run_py):
    # gates and programs add packages to pyproject.toml; a refresh must not drop them
    scaffold = SKILL / "scripts" / "scaffold_workflow.py"
    assert run_py([scaffold, "pkgs", "--root", tmp_path, "--request", "x"]).data["ok"]
    pyproject = tmp_path / "pkgs" / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(text.replace('dependencies = ["typer>=0.12", ', 'dependencies = ["openpyxl>=3.1", '), encoding="utf-8")
    r = run_py([scaffold, "pkgs", "--root", tmp_path, "--refresh"])
    assert r.data and r.data["ok"], r
    text = pyproject.read_text(encoding="utf-8")
    assert '"openpyxl>=3.1"' in text and '"typer>=0.12"' in text  # kept, and the skill's package added back
