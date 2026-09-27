"""A run finished end to end: a one-stage workflow whose last wave completes, so
`run_log finish` returns what the parent needs for the final report. (The
scenario in test_workflow.py stops or fails its runs; none completes.)"""
import json
import os

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
    (wf / ".pi" / "plan.yaml").write_text(PLAN, encoding="utf-8")
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
