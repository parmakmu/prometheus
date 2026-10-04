"""list_workflows' live view of a running wave: the children pi-subagents has launched, counted per stage."""
import json
import sys

from conftest import SKILL

sys.path.insert(0, str(SKILL / "scripts"))
import list_workflows


def test_children_are_counted_under_the_longest_stage_key_they_start_with(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_SUBAGENTS_TEMP_ROOT", str(tmp_path))
    record = tmp_path / "async-subagent-runs" / "sub-1"
    record.mkdir(parents=True)
    children = [{"childId": "read-emails-1", "state": "complete"}, {"childId": "read-emails-2", "state": "running"},
                {"childId": "read", "state": "running"}, {"childId": "discover-review", "state": "failed"},
                {"childId": "stray", "state": "running"}]
    (record / "status.json").write_text(json.dumps({"workflowChildren": {"children": children}}), encoding="utf-8")
    state = {"launches": [{"status": "running", "subagent_run_id": "sub-1"}]}
    progress = list_workflows.child_progress(state, ["discover", "read", "read-emails"])
    assert {k: {n: v[n] for n in ("done", "failed", "total")} for k, v in progress.items()} == {
        "read-emails": {"done": 1, "failed": 0, "total": 2},
        "read": {"done": 0, "failed": 0, "total": 1},
        "discover": {"done": 0, "failed": 1, "total": 1},
    }
    assert [i["key"] for i in progress["read-emails"]["items"]] == ["read-emails-1", "read-emails-2"]


def test_no_progress_unless_the_last_launch_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_SUBAGENTS_TEMP_ROOT", str(tmp_path))
    assert list_workflows.child_progress({}, ["a"]) == {}
    assert list_workflows.child_progress({"launches": [{"status": "done", "subagent_run_id": "x"}]}, ["a"]) == {}
    assert list_workflows.child_progress({"launches": [{"status": "running", "subagent_run_id": "missing"}]}, ["a"]) == {}


def test_a_run_waits_for_what_its_last_finished_wave_ended_with():
    done = lambda ends: {"launches": [{"status": "done", "ends_with": ends}]}
    assert list_workflows.waiting_for(done("approval"), "w2") == "approval"
    assert list_workflows.waiting_for(done("review"), "w2") == "review"
    assert list_workflows.waiting_for(done("replan"), "w2") == "replan"
    assert list_workflows.waiting_for(done("none"), "w2") is None
    assert list_workflows.waiting_for(done("review"), None) is None  # no next wave: the run is over
    assert list_workflows.waiting_for({"launches": [{"status": "running", "ends_with": "review"}]}, "w2") is None
