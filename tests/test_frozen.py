"""Released agents run from code (prometheus/frozen.py): picking a release, unpacking it, the values a run is given,
where its outputs land, and runs driven wave by wave (pi faked: the stage programs run directly), paused and answered. With PROMETHEUS_LIVE_TESTS=1, one run goes through real pi and
pi-subagents (no model: both stages are programs), using your Pi install."""
import io
import json
import os
import shutil
import subprocess
import sys

import pytest
from conftest import SKILL, stamp
from test_release import commit, make_agent

from prometheus import frozen

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture
def released(tmp_path, run_py, monkeypatch):
    """The counter agent released as v1.0, and a cache of the test's own."""
    monkeypatch.setenv("PROMETHEUS_CACHE", str(tmp_path / "cache"))
    (tmp_path / "agents").mkdir()
    folder = make_agent(tmp_path / "agents", run_py)
    assert run_py([SKILL / "scripts" / "release.py", "tag", "--agent", folder]).data["version"] == "v1.0"
    return folder


def test_a_release_is_picked_newest_first_by_major_or_exactly():
    listed = [{"version": "v2.0"}, {"version": "v1.3"}, {"version": "v1.2"}]
    assert frozen.pick_release(listed, None)["version"] == "v2.0"
    assert frozen.pick_release(listed, "v1")["version"] == "v1.3"
    assert frozen.pick_release(listed, "v1.2")["version"] == "v1.2"
    with pytest.raises(frozen.PrometheusError, match="no release v3"):
        frozen.pick_release(listed, "v3")
    with pytest.raises(frozen.PrometheusError, match="no release yet"):
        frozen.pick_release([], None)


def test_load_agent_unpacks_the_release_once_named_like_the_agent(released, tmp_path):
    agent = frozen.load_agent(f"{released}@v1")
    assert (agent.name, agent.version, agent.side_effects) == ("counter", "v1.0", ["save"])
    assert agent.folder.name == "counter" and agent.folder.is_relative_to(tmp_path / "cache")
    assert (agent.folder / "plan.yaml").is_file() and (agent.folder / ".git").is_dir()  # pi-subagents' writer checks need a repo
    (released / "scripts" / "count.py").write_text("changed after the release\n")
    again = frozen.load_agent(str(released))
    assert again.folder == agent.folder and "changed" not in (again.folder / "scripts" / "count.py").read_text()


def test_values_become_files_or_absolute_paths_and_outputs_resolve_in_the_run(released, tmp_path, monkeypatch):
    agent = frozen.load_agent(str(released))
    run_dir = agent.folder / "runs" / "r1"
    monkeypatch.chdir(tmp_path)
    assert agent.prepare_params(run_dir, {"source": "in.txt"}) == {"source": str(tmp_path / "in.txt")}
    assert agent.prepare_params(run_dir, {"source": b"a\nb\n"}) == {"source": str(run_dir / "inputs" / "source")}
    assert (run_dir / "inputs" / "source").read_bytes() == b"a\nb\n"
    agent.prepare_params(run_dir, {"source": io.StringIO("x\n")})
    assert (run_dir / "inputs" / "source").read_text() == "x\n"
    assert agent.resolve_outputs(run_dir, {}) == {"count_file": run_dir / "outputs" / "count.txt"}  # the default, in the run
    assert agent.resolve_outputs(run_dir, {"report": "/srv/c.txt"}) == {"count_file": frozen.Path("/srv/c.txt")}


def fake_launch(agent, run_dir, wave):
    """pi running a compiled wave, without pi: each program stage's command runs as the batcher would (`uv run` as this
    Python), and the runner's events and pi-subagents' record are written as they would be. A stage fails when its
    program does; the wave stops there."""
    spec = json.loads((run_dir / ".compiled" / f"{wave}.wave.json").read_text())
    status = run_dir / "pi-subagents" / wave
    status.mkdir(parents=True)
    events, children, failed = [{"event": "launched", "wave": wave, "status_dir": str(status)}], {}, []
    for step in spec["steps"]:
        for item in step.get("layer") or []:
            if item.get("reuse"):  # an earlier run's result: the runner launches nothing
                ok = True
            elif not item["task"].startswith("BATCH_TOOL_REQUEST "):  # a model child: it writes its output
                frozen.Path(item["output"]).write_text(f"judged by a model in {run_dir.name}\n")
                ok = True
            else:
                request = json.loads(item["task"].removeprefix("BATCH_TOOL_REQUEST "))
                command = request["command"].replace("uv run ", f"{sys.executable} ", 1)
                ok = not failed and subprocess.run(command, shell=True, cwd=item["cwd"], capture_output=True, check=False).returncode == 0
            children[item["key"]] = {"status": "completed" if ok else "failed", "reused": bool(item.get("reuse"))}
            failed += [] if ok else [item["key"]]
            events.append({"event": "child", "wave": wave, "key": item["key"], "state": children[item["key"]]["status"]})
    value = {"wave": wave, "status": "failed" if failed else "done", "children": children, **({"at": failed[:1]} if failed else {})}
    (status / "status.json").write_text(json.dumps({"state": "complete", "workflow": {"value": value}, "totalCost": {"costUsd": 0.001}}))
    run_id = f"fake-{run_dir.name}-{wave}"  # pi-subagents' own record of the launch, and its mission, as a real one leaves
    record = frozen.Path(os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / run_id
    record.mkdir(parents=True)
    missions = frozen.Path(os.environ["PI_CODING_AGENT_DIR"]) / "missions" / "projects" / "p"
    missions.mkdir(parents=True, exist_ok=True)
    (missions / f"m-{run_id}.json").write_text(json.dumps({"id": f"m-{run_id}", "runs": [{"runId": run_id}]}))
    events.append({"event": "wave_end", "wave": wave, "state": "complete", "status_dir": str(status), "subagent_run_id": run_id,
                   "cost": {"costUsd": 0.001}})
    with (run_dir / "events.jsonl").open("a") as f:
        f.writelines(json.dumps(e) + "\n" for e in events)
    return Ended()


class Ended:
    """A pi process that has already exited."""
    returncode = 0

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


@pytest.fixture
def fake_pi(monkeypatch):
    monkeypatch.setattr(frozen, "find_tools", lambda: ("pi", frozen.Path("pi-subagents.js")))
    monkeypatch.setattr(frozen, "launch_wave", fake_launch)


def test_a_run_approved_up_front_is_one_wave(released, fake_pi, tmp_path):
    (tmp_path / "in.txt").write_text("a\nb\nc\n")
    run = frozen.load_agent(str(released)).start(params={"source": str(tmp_path / "in.txt")}, approve=["save"])
    result = run.wait(30)
    assert (result.ok, result.state, result.cost_usd) == (True, "complete", 0.001)
    assert [w["wave"] for w in result.record["launches"]] == ["all"]
    assert result.outputs["count_file"].read_text() == "3\n"


def test_a_side_effect_not_approved_stops_the_run_until_it_is_answered(released, fake_pi, tmp_path):
    (tmp_path / "in.txt").write_text("a\nb\n")
    agent = frozen.load_agent(str(released))
    run = agent.start(params={"source": str(tmp_path / "in.txt")})
    paused = run.wait(30)
    assert (paused.ok, paused.state) == (False, "input_required")
    assert [a["stage"] for a in paused.needs["approve"]] == ["save"] and paused.needs["approve"][0]["writes"]
    assert [w["wave"] for w in paused.record["launches"]] == ["w1"]  # count ran; save waits
    assert not paused.outputs["count_file"].exists()
    again = frozen.Run.reopen(agent, run.id)  # another process (a restarted server) answers it
    again.answer(approve=["save"])
    result = again.wait(30)
    assert (result.ok, result.state, [w["wave"] for w in result.record["launches"]]) == (True, "complete", ["w1", "w2"])
    assert result.outputs["count_file"].read_text() == "2\n"
    events = [e["event"] for e in run.read_events()]
    assert events.index("input_required") < events.index("resumed") < events.index("end")
    with pytest.raises(frozen.PrometheusError, match="not waiting for an answer"):
        again.answer(approve=["save"])


def test_a_paused_run_can_be_canceled_and_a_failing_program_fails_the_run(released, fake_pi, tmp_path):
    (tmp_path / "in.txt").write_text("x\n")
    agent = frozen.load_agent(str(released))
    run = agent.start(params={"source": str(tmp_path / "in.txt")})
    assert run.wait(30).state == "input_required"
    run.cancel()
    assert run.wait(30).state == "stopped"
    failing = agent.start(params={"source": str(tmp_path / "missing.txt")}, approve=["save"])  # count.py can't open it
    result = failing.wait(30)
    assert (result.ok, result.state) == (False, "failed") and "stage count failed" in result.error


JUDGE = """\
version: 1
revision: 1
status: ready
request: "describe the file"
goal: Describe a file in a few lines.
done_when:
  - notes.md describes the file.
params:
  source:
    description: The file to describe
inputs:
  - id: source_file
    kind: file
    location: ${source}
agents:
  checked: test
  available: [worker, reviewer, {name: plan-critic, source: project}]
stages:
  - id: describe
    title: Describe the file
    kind:
      value: judgment
      reason: Describing needs judgment.
    pattern: worker
    agent: worker
    reads: [source_file]
    writes:
      - id: notes
        path: notes.md
        format: md
    done_when: notes.md describes the file
    review: reviewer
"""


def test_a_judgment_made_on_the_same_input_is_reused_by_the_next_call(tmp_path, run_py, monkeypatch, fake_pi):
    monkeypatch.setenv("PROMETHEUS_CACHE", str(tmp_path / "cache"))
    (tmp_path / "agents").mkdir()
    folder = make_agent(tmp_path / "agents", run_py, slug="describer")
    shutil.rmtree(folder / ".plan")  # the counter plan's planning record: this plan brings its own
    (folder / "plan.yaml").write_text(JUDGE)
    stamp(folder / "plan.yaml")
    commit(folder)
    assert run_py([SKILL / "scripts" / "release.py", "tag", "--agent", folder]).data["ok"]
    (tmp_path / "in.txt").write_text("hello\n")
    agent = frozen.load_agent(str(folder))
    first = agent.run(params={"source": str(tmp_path / "in.txt")}, timeout=30)
    second = agent.run(params={"source": str(tmp_path / "in.txt")}, timeout=30)
    assert first.ok and second.ok
    reused = json.loads((second.run_dir / ".compiled" / "all-result.json").read_text())["children"]["describe"]
    assert reused["reused"] and "describe-review" not in json.loads((second.run_dir / ".compiled" / "all-result.json").read_text())["children"]
    assert (second.run_dir / "work" / "notes.md").read_text() == f"judged by a model in {first.run_dir.name}\n"  # copied
    (tmp_path / "in.txt").write_text("changed\n")
    third = agent.run(params={"source": str(tmp_path / "in.txt")}, timeout=30)
    assert (third.run_dir / "work" / "notes.md").read_text() == f"judged by a model in {third.run_dir.name}\n"  # judged again


def test_prune_deletes_old_released_runs_files_and_keeps_their_history(released, fake_pi, tmp_path, run_py):
    (tmp_path / "in.txt").write_text("a\n")
    agent = frozen.load_agent(str(released))
    runs = [agent.run(params={"source": str(tmp_path / "in.txt")}, approve=["save"], timeout=30) for _ in range(3)]
    paused = agent.start(params={"source": str(tmp_path / "in.txt")})  # waits for an answer: not finished
    assert paused.wait(30).state == "input_required"
    out = run_py([SKILL / "scripts" / "run_history.py", "prune", "--agent", agent.folder, "--keep", "1"]).data
    assert sorted(out["pruned"]) == sorted(r.run_dir.name for r in runs[:2]) and out["missions_removed"] == 2
    for old in runs[:2]:
        assert not (old.run_dir / "work").exists() and not (old.run_dir / "outputs").exists()
        assert (old.run_dir / "state.json").is_file() and (old.run_dir / "events.jsonl").is_file()  # the history stays
        assert not (frozen.Path(os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / f"fake-{old.run_dir.name}-all").exists()
    assert runs[2].outputs["count_file"].read_text() == "1\n" and (paused.dir / "work").is_dir()


def test_a_run_that_cant_start_is_refused_and_leaves_nothing(released, fake_pi):
    agent = frozen.load_agent(str(released))
    with pytest.raises(frozen.PrometheusError, match="missing params: source"):
        agent.start(params={}, approve=["save"])
    assert not list((agent.folder / "runs").glob("*"))


@pytest.mark.skipif(os.environ.get("PROMETHEUS_LIVE_TESTS") != "1", reason="live: set PROMETHEUS_LIVE_TESTS=1 (uses your Pi install)")
def test_live_a_released_agent_runs_through_pi_with_no_model(released, tmp_path, monkeypatch):
    for name in ("PI_CODING_AGENT_DIR", "PI_SUBAGENTS_TEMP_ROOT"):  # your Pi, not the suite's empty one
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "in.txt").write_text("a\nb\nc\n")
    run = frozen.load_agent(str(released)).start(params={"source": str(tmp_path / "in.txt")}, approve=["save"])
    states = [(e.get("key"), e.get("state")) for e in run.events() if e["event"] == "child"]
    result = run.wait(120)
    assert result.ok, result.error
    assert states == [("count", "running"), ("count", "completed"), ("save", "running"), ("save", "completed")]
    assert result.outputs["count_file"].read_text() == "3\n" and result.cost_usd == 0
    assert not any(e["event"] == "model_turn_blocked" for e in run.read_events())
    paused = frozen.load_agent(str(released)).start(params={"source": str(tmp_path / "in.txt")})  # save not approved
    assert paused.wait(120).state == "input_required"
    paused.answer(approve=["save"])
    assert paused.wait(120).ok


def test_a_named_file_keeps_its_name_and_a_dict_is_a_folder(released):
    agent = frozen.load_agent(str(released))
    run_dir = agent.folder / "runs" / "r2"
    assert agent.prepare_params(run_dir, {"source": ("lines.txt", b"a\n")}) == {"source": str(run_dir / "inputs" / "source" / "lines.txt")}
    values = agent.prepare_params(run_dir, {"source": {"one.eml": b"1", "../escape.eml": b"2"}})
    assert values == {"source": str(run_dir / "inputs" / "source")}
    assert sorted(p.name for p in (run_dir / "inputs" / "source").iterdir()) == ["escape.eml", "lines.txt", "one.eml"]
    assert agent.folder_params == set() and agent.output_params == {"report"}


def paused_run(released, tmp_path):
    (tmp_path / "in.txt").write_text("a\nb\n")
    agent = frozen.load_agent(str(released))
    run = agent.start(params={"source": str(tmp_path / "in.txt")})
    assert run.wait(30).state == "input_required"
    return agent, run


def test_a_run_whose_driver_died_ends_as_an_error_and_its_events_end(released, fake_pi, tmp_path):
    agent, run = paused_run(released, tmp_path)
    gone = subprocess.Popen(["true"])
    gone.wait()
    run.write_state(state="running", driver=gone.pid)  # a server stopped mid-run
    reopened = frozen.Run.reopen(agent, run.id)
    assert not reopened.running and reopened.wait().error == frozen.INTERRUPTED
    assert list(reopened.events())[-1]["event"] == "end"  # a stream of it ends instead of waiting forever


def test_a_second_answer_is_refused_and_a_cancel_during_the_compile_launches_nothing(released, fake_pi, tmp_path, monkeypatch):
    agent, run = paused_run(released, tmp_path)
    first, second = frozen.Run.reopen(agent, run.id), frozen.Run.reopen(agent, run.id)
    compile_wave = first.compile

    def canceled_meanwhile():  # someone cancels it (from another process) while its next wave compiles
        compiled = compile_wave()
        second.cancel()
        return compiled
    monkeypatch.setattr(first, "compile", canceled_meanwhile)
    first.answer(approve=["save"])
    with pytest.raises(frozen.PrometheusError, match="not waiting for an answer"):
        second.answer(approve=["save"])  # never a second driver
    result = first.wait(30)
    assert result.state == "stopped" and [w["wave"] for w in result.record["launches"]] == ["w1"]  # save never launched


def test_a_file_name_cannot_leave_or_be_its_folder(released):
    agent = frozen.load_agent(str(released))
    run_dir = agent.folder / "runs" / "r-names"
    folder = agent.prepare_params(run_dir, {"source": {"..": b"a", "../x": b"b"}})["source"]
    assert sorted(p.name for p in frozen.Path(folder).iterdir()) == ["source-0", "x"]
    assert agent.prepare_params(run_dir, {"source": ("", b"c")})["source"] == str(run_dir / "inputs" / "source" / "source-0")
