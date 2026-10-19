"""Released agents run from code (prometheus/run_release.py): picking a release, unpacking it, the values a run is given,
where its outputs land, and runs driven wave by wave (pi faked: the stage programs run directly), paused and answered. With PROMETHEUS_LIVE_TESTS=1, one run goes through real pi and
pi-subagents (no model: both stages are programs), using your Pi install."""
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import SKILL, git_in, stamp
from test_agent_release import commit, make_agent

from prometheus import run_release

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture
def released(tmp_path, run_py, monkeypatch):
    """The counter agent released as v1.0, and a cache of the test's own."""
    monkeypatch.setenv("PROMETHEUS_RELEASES", str(tmp_path / "releases"))
    (tmp_path / "agents").mkdir()
    folder = make_agent(tmp_path / "agents", run_py)
    assert run_py([SKILL / "scripts" / "agent_release.py", "tag", "--agent", folder]).data["version"] == "v1.0"
    return folder


def test_a_release_is_picked_newest_first_by_major_or_exactly():
    listed = [{"version": "v2.0"}, {"version": "v1.3"}, {"version": "v1.2"}]
    assert run_release.pick_release(listed, None)["version"] == "v2.0"
    assert run_release.pick_release(listed, "v1")["version"] == "v1.3"
    assert run_release.pick_release(listed, "v1.2")["version"] == "v1.2"
    with pytest.raises(run_release.PrometheusError, match="no release v3"):
        run_release.pick_release(listed, "v3")
    with pytest.raises(run_release.PrometheusError, match="no release yet"):
        run_release.pick_release([], None)


def test_load_agent_unpacks_the_release_once_named_like_the_agent(released, tmp_path):
    agent = run_release.load_agent(f"{released}@v1")
    assert (agent.name, agent.version, agent.side_effects) == ("counter", "v1.0", ["save"])
    assert agent.folder == tmp_path / "releases" / "counter" / "v1.0"
    assert (agent.folder / "plan.yaml").is_file() and (agent.folder / ".git").is_dir()  # pi-subagents' writer checks need a repo
    assert not (agent.folder / ".pi" / "extensions").exists()  # no agent commands there: a release isn't changed in place
    assert (released / ".pi" / "extensions" / "agent" / "index.ts").is_file()  # the agent itself keeps them
    told = json.loads((agent.folder / "RELEASE.json").read_text())
    commit = git_in(released)("rev-parse", "v1.0^{commit}").stdout.strip()
    assert (told["agent"], told["version"], told["commit"]) == ("counter", "v1.0", commit) and "/agent-improve" in told["note"]
    (released / "scripts" / "count.py").write_text("changed after the release\n")
    again = run_release.load_agent(str(released))
    assert again.folder == agent.folder and "changed" not in (again.folder / "scripts" / "count.py").read_text()


def test_values_become_files_or_absolute_paths_and_outputs_resolve_in_the_run(released, tmp_path, monkeypatch):
    agent = run_release.load_agent(str(released))
    run_dir = agent.folder / "runs" / "r1"
    monkeypatch.chdir(tmp_path)
    assert agent.prepare_params(run_dir, {"source": "in.txt"}) == {"source": str(tmp_path / "in.txt")}
    assert agent.prepare_params(run_dir, {"source": b"a\nb\n"}) == {"source": str(run_dir / "inputs" / "source")}
    assert (run_dir / "inputs" / "source").read_bytes() == b"a\nb\n"
    agent.prepare_params(run_dir, {"source": io.StringIO("x\n")})
    assert (run_dir / "inputs" / "source").read_text() == "x\n"
    assert agent.resolve_outputs(run_dir, {}) == {"count_file": run_dir / "outputs" / "count.txt"}  # the default, in the run
    assert agent.resolve_outputs(run_dir, {"report": "/srv/c.txt"}) == {"count_file": run_release.Path("/srv/c.txt")}


def fake_launch(folder, run_dir, wave):
    """pi running a compiled wave, without pi: each program stage's command runs as the batcher would (`uv run` as this
    Python), and the runner's events and pi-subagents' record are written as they would be. A stage fails when its
    program does; the wave stops there."""
    spec = run_release.cli.import_script("run_folders").read_wave_spec(run_dir, wave)
    status = run_dir / "pi-subagents" / wave
    status.mkdir(parents=True)
    events, children, failed = [{"event": "launched", "wave": wave, "status_dir": str(status)}], {}, []
    for step in spec["steps"]:
        for item in step.get("layer") or []:
            if item.get("reuse"):  # an earlier run's result: the runner launches nothing
                ok = True
            elif not item["task"].startswith("BATCH_TOOL_REQUEST "):  # a model child: it writes its output
                run_release.Path(item["output"]).write_text(f"judged by a model in {run_dir.name}\n")
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
    record = run_release.Path(os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / run_id
    record.mkdir(parents=True)
    missions = run_release.Path(os.environ["PI_CODING_AGENT_DIR"]) / "missions" / "projects" / "p"
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
    monkeypatch.setattr(run_release.launcher, "check", lambda: ("pi", run_release.Path("pi-subagents.js")))
    monkeypatch.setattr(run_release.launcher, "launch", fake_launch)


def test_a_run_approved_up_front_is_one_wave(released, fake_pi, tmp_path):
    (tmp_path / "in.txt").write_text("a\nb\nc\n")
    run = run_release.load_agent(str(released)).start(params={"source": str(tmp_path / "in.txt")}, approve=["save"])
    result = run.wait(30)
    assert (result.ok, result.state, result.cost_usd) == (True, "complete", 0.001)
    assert [w["wave"] for w in result.record["launches"]] == ["all"]
    assert result.outputs["count_file"].read_text() == "3\n"


def test_a_side_effect_not_approved_stops_the_run_until_it_is_answered(released, fake_pi, tmp_path):
    (tmp_path / "in.txt").write_text("a\nb\n")
    agent = run_release.load_agent(str(released))
    run = agent.start(params={"source": str(tmp_path / "in.txt")})
    paused = run.wait(30)
    assert (paused.ok, paused.state) == (False, "input_required")
    assert [a["stage"] for a in paused.needs["approve"]] == ["save"] and paused.needs["approve"][0]["writes"]
    assert [w["wave"] for w in paused.record["launches"]] == ["w1"]  # count ran; save waits
    assert not paused.outputs["count_file"].exists()
    again = run_release.Run.reopen(agent, run.id)  # another process (a restarted server) answers it
    again.answer(approve=["save"])
    result = again.wait(30)
    assert (result.ok, result.state, [w["wave"] for w in result.record["launches"]]) == (True, "complete", ["w1", "w2"])
    assert result.outputs["count_file"].read_text() == "2\n"
    events = [e["event"] for e in run.read_events()]
    assert events.index("input_required") < events.index("resumed") < events.index("end")
    with pytest.raises(run_release.PrometheusError, match="not waiting for an answer"):
        again.answer(approve=["save"])


def test_a_paused_run_can_be_canceled_and_a_failing_program_fails_the_run(released, fake_pi, tmp_path):
    (tmp_path / "in.txt").write_text("x\n")
    agent = run_release.load_agent(str(released))
    run = agent.start(params={"source": str(tmp_path / "in.txt")})
    assert run.wait(30).state == "input_required"
    run.cancel()
    assert run.wait(30).state == "stopped"
    failing = agent.start(params={"source": str(tmp_path / "missing.txt")}, approve=["save"])  # count.py can't open it
    result = failing.wait(30)
    assert (result.ok, result.state) == (False, "failed") and "stage count failed" in result.error


JUDGE = """\
format: 1
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
    path: ${source}
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
    monkeypatch.setenv("PROMETHEUS_RELEASES", str(tmp_path / "releases"))
    (tmp_path / "agents").mkdir()
    folder = make_agent(tmp_path / "agents", run_py, slug="describer")
    shutil.rmtree(folder / ".plan")  # the counter plan's planning record: this plan brings its own
    (folder / "plan.yaml").write_text(JUDGE)
    stamp(folder / "plan.yaml")
    commit(folder)
    assert run_py([SKILL / "scripts" / "agent_release.py", "tag", "--agent", folder]).data["ok"]
    (tmp_path / "in.txt").write_text("hello\n")
    agent = run_release.load_agent(str(folder))
    first = agent.run(params={"source": str(tmp_path / "in.txt")}, timeout=30)
    second = agent.run(params={"source": str(tmp_path / "in.txt")}, timeout=30)
    assert first.ok and second.ok
    briefs = json.dumps(run_release.cli.import_script("run_folders").read_wave_spec(first.run_dir, "all"))
    assert 'Agent \\"describer\\", stage \\"describe\\"' in briefs  # its name, from RELEASE.json: not its folder's, v1.0
    children = json.loads((second.run_dir / ".compiled" / "all.json").read_text())["result"]["children"]
    assert children["describe"]["reused"] and "describe-review" not in children
    assert second.record["launches"][0]["launched"] is False and second.cost_usd == 0  # nothing left to launch: recorded only
    assert (second.run_dir / "work" / "notes.md").read_text() == f"judged by a model in {first.run_dir.name}\n"  # copied
    (tmp_path / "in.txt").write_text("changed\n")
    third = agent.run(params={"source": str(tmp_path / "in.txt")}, timeout=30)
    assert (third.run_dir / "work" / "notes.md").read_text() == f"judged by a model in {third.run_dir.name}\n"  # judged again


def test_prune_deletes_old_released_runs_files_and_keeps_their_history(released, fake_pi, tmp_path, run_py):
    (tmp_path / "in.txt").write_text("a\n")
    agent = run_release.load_agent(str(released))
    runs = [agent.run(params={"source": str(tmp_path / "in.txt")}, approve=["save"], timeout=30) for _ in range(3)]
    paused = agent.start(params={"source": str(tmp_path / "in.txt")})  # waits for an answer: not finished
    assert paused.wait(30).state == "input_required"
    out = run_py([SKILL / "scripts" / "run_history.py", "prune", "--agent", agent.folder, "--keep", "1"]).data
    assert sorted(out["pruned"]) == sorted(r.run_dir.name for r in runs[:2]) and out["missions_removed"] == 2
    for old in runs[:2]:
        assert not (old.run_dir / "work").exists() and not (old.run_dir / "outputs").exists()
        assert (old.run_dir / "run.yaml").is_file() and (old.run_dir / "events.jsonl").is_file()  # the history stays
        assert not (run_release.Path(os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / f"fake-{old.run_dir.name}-all").exists()
    assert runs[2].outputs["count_file"].read_text() == "1\n" and (paused.dir / "work").is_dir()


def test_a_run_that_cant_start_is_refused_and_leaves_nothing(released, fake_pi):
    agent = run_release.load_agent(str(released))
    with pytest.raises(run_release.PrometheusError, match="missing params: source"):
        agent.start(params={}, approve=["save"])
    assert not list((agent.folder / "runs").glob("*"))


@pytest.mark.skipif(os.environ.get("PROMETHEUS_LIVE_TESTS") != "1", reason="live: set PROMETHEUS_LIVE_TESTS=1 (uses your Pi install)")
def test_live_a_released_agent_runs_through_pi_with_no_model(released, tmp_path, monkeypatch):
    for name in ("PI_CODING_AGENT_DIR", "PI_SUBAGENTS_TEMP_ROOT"):  # your Pi, not the suite's empty one
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "in.txt").write_text("a\nb\nc\n")
    run = run_release.load_agent(str(released)).start(params={"source": str(tmp_path / "in.txt")}, approve=["save"])
    states = [(e.get("key"), e.get("state")) for e in run.events() if e["event"] == "child"]
    result = run.wait(120)
    assert result.ok, result.error
    assert states == [("count", "running"), ("count", "completed"), ("save", "running"), ("save", "completed")]
    assert result.outputs["count_file"].read_text() == "3\n" and result.cost_usd == 0
    assert not any(e["event"] == "model_turn_blocked" for e in run.read_events())
    paused = run_release.load_agent(str(released)).start(params={"source": str(tmp_path / "in.txt")})  # save not approved
    assert paused.wait(120).state == "input_required"
    paused.answer(approve=["save"])
    assert paused.wait(120).ok


def test_a_named_file_keeps_its_name_and_a_dict_is_a_folder(released):
    agent = run_release.load_agent(str(released))
    run_dir = agent.folder / "runs" / "r2"
    assert agent.prepare_params(run_dir, {"source": ("lines.txt", b"a\n")}) == {"source": str(run_dir / "inputs" / "source" / "lines.txt")}
    values = agent.prepare_params(run_dir, {"source": {"one.eml": b"1", "../escape.eml": b"2"}})
    assert values == {"source": str(run_dir / "inputs" / "source")}
    assert sorted(p.name for p in (run_dir / "inputs" / "source").iterdir()) == ["escape.eml", "lines.txt", "one.eml"]
    assert agent.folder_params == set() and agent.output_params == {"report"}


def paused_run(released, tmp_path):
    (tmp_path / "in.txt").write_text("a\nb\n")
    agent = run_release.load_agent(str(released))
    run = agent.start(params={"source": str(tmp_path / "in.txt")})
    assert run.wait(30).state == "input_required"
    return agent, run


def test_a_run_whose_driver_died_ends_as_an_error_and_its_events_end(released, fake_pi, tmp_path):
    agent, run = paused_run(released, tmp_path)
    gone = subprocess.Popen(["true"])
    gone.wait()
    run.write_state(status="running", driver={"pid": gone.pid, "started": time.time()})  # a server stopped mid-run
    reopened = run_release.Run.reopen(agent, run.id)
    assert not reopened.running and reopened.wait().error == run_release.INTERRUPTED
    assert list(reopened.events())[-1]["event"] == "end"  # a stream of it ends instead of waiting forever


def test_a_driver_pid_another_process_reused_counts_as_dead(released, fake_pi, tmp_path):
    agent, run = paused_run(released, tmp_path)
    # this process holds the pid now, but started after the driver recorded under it: not the driver
    run.write_state(status="running", driver={"pid": os.getpid(), "started": time.time() - 3600 * 24 * 365})
    assert run_release.Run.reopen(agent, run.id).wait().error == run_release.INTERRUPTED
    run.write_state(status="running", error=None, driver=run_release.cli.import_script("utils").this_process())
    assert run_release.Run.reopen(agent, run.id).state == "running"  # the driver itself: alive


def test_a_run_another_process_drives_is_waited_for_by_its_state(released, fake_pi, tmp_path):
    agent, run = paused_run(released, tmp_path)
    run.write_state(status="running", needs=None, driver=run_release.cli.import_script("utils").this_process())
    reopened = run_release.Run.reopen(agent, run.id)  # no thread here: the run goes on elsewhere
    threading.Timer(1, run.finish, args=("complete",)).start()
    assert reopened.wait(30, poll_s=0.1).state == "complete"
    run.write_state(status="running", error=None)
    with pytest.raises(TimeoutError):
        run_release.Run.reopen(agent, run.id).wait(0.3, poll_s=0.1)
    assert run.read_state().get("cancel")  # a timeout cancels it, as for a run of one's own


def test_a_cancel_from_another_process_kills_a_wave_that_ignores_it_after_the_grace(released, fake_pi, tmp_path):
    agent, run = paused_run(released, tmp_path)
    ignores_term = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(flush=True); time.sleep(60)"
    stubborn = subprocess.Popen([sys.executable, "-c", ignores_term], stdout=subprocess.PIPE)
    stubborn.stdout.readline()  # its handler is set
    status = tmp_path / "wave-status"
    status.mkdir()
    (status / "status.json").write_text(json.dumps({"state": "running", "pid": stubborn.pid}))
    run.log(event="launched", wave="w2", status_dir=str(status))
    run.write_state(status="running", needs=None, driver=run_release.cli.import_script("utils").this_process())
    started = time.monotonic()
    run_release.Run.reopen(agent, run.id).cancel(grace_s=1)
    assert stubborn.wait(5) == -9 and 0.9 < time.monotonic() - started < 5  # asked, waited for, then killed
    (status / "status.json").write_text(json.dumps({"state": "complete", "pid": os.getpid()}))
    run_release.Run.reopen(agent, run.id).cancel(grace_s=1)  # an ended launch's pid is never signalled: here, ours


def test_a_second_answer_is_refused_and_a_cancel_during_the_compile_launches_nothing(released, fake_pi, tmp_path, monkeypatch):
    agent, run = paused_run(released, tmp_path)
    first, second = run_release.Run.reopen(agent, run.id), run_release.Run.reopen(agent, run.id)
    compile_wave = first.compile

    def canceled_meanwhile():  # someone cancels it (from another process) while its next wave compiles
        compiled = compile_wave()
        second.cancel()
        return compiled
    monkeypatch.setattr(first, "compile", canceled_meanwhile)
    first.answer(approve=["save"])
    with pytest.raises(run_release.PrometheusError, match="not waiting for an answer"):
        second.answer(approve=["save"])  # never a second driver
    result = first.wait(30)
    assert result.state == "stopped" and [w["wave"] for w in result.record["launches"]] == ["w1"]  # save never launched


def test_a_file_name_cannot_leave_or_be_its_folder(released):
    agent = run_release.load_agent(str(released))
    run_dir = agent.folder / "runs" / "r-names"
    folder = agent.prepare_params(run_dir, {"source": {"..": b"a", "../x": b"b"}})["source"]
    assert sorted(p.name for p in run_release.Path(folder).iterdir()) == ["source-0", "x"]
    assert agent.prepare_params(run_dir, {"source": ("", b"c")})["source"] == str(run_dir / "inputs" / "source" / "source-0")


def test_a_bundled_run_runs_again_on_a_branch_with_its_files_and_asks_for_its_side_effects(released, fake_pi, tmp_path, run_py):
    # a release counts blank lines too; the fix, on a branch, is checked on the bundled run's own file before a release
    agent = run_release.load_agent(str(released))
    run = agent.start(params={"source": b"a\n\nb\n"}, approve=["save"], in_run={"report"})  # as serve calls it
    assert run.wait(30).outputs["count_file"].read_text() == "3\n"
    made = run_py([SKILL / "scripts" / "run_bundle.py", "make", "--run", run.dir, "--out", tmp_path, "--with-data"])
    assert run.bundle(tmp_path / "api", with_data=True).name == made.data["bundle"].rsplit("/", 1)[-1]  # the same from Python
    bare = run_py([SKILL / "scripts" / "run_bundle.py", "make", "--run", run.dir, "--out", tmp_path / "bare"])
    git = git_in(released)
    git("switch", "-q", "-c", "improve/2026-10-16-1")
    (released / "scripts" / "count.py").write_text(
        "import json, sys\njson.dump(len([x for x in open(sys.argv[1]).read().splitlines() if x]), open(sys.argv[2], 'w'))\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "count only lines with text")

    build = run_release.load_agent(f"{released}@improve/2026-10-16-1")
    assert build.version == "build" and build.side_effects == ["save"] and build.folder != agent.folder
    values, in_run = run_release.read_bundle_call(Path(made.data["bundle"]), build)
    assert in_run == {"report"}  # report named the old run's outputs: the new run's now
    assert Path(values["source"]).read_text() == "a\n\nb\n"
    again = build.start(params=values, in_run=in_run)
    assert again.wait(30).state == "input_required"  # the bundled run's approval isn't carried over: save asks again
    again.answer(approve=["save"])
    done = again.wait(30)
    assert done.ok and done.outputs["count_file"].read_text() == "2\n" and done.run_dir.parent.parent == build.folder
    with pytest.raises(run_release.PrometheusError, match="made without --with-data"):
        run_release.read_bundle_call(Path(bare.data["bundle"]), build)
    with pytest.raises(run_release.PrometheusError, match="no release or commit 'nope'"):
        run_release.load_agent(f"{released}@nope")
    from typer.testing import CliRunner

    from prometheus import cli
    said = CliRunner().invoke(cli.app, ["run", f"{released}@improve/2026-10-16-1", "--bundle", bare.data["bundle"]])
    assert said.exit_code != 0 and "made without --with-data" in str(said.exception or said.output), said.output


def test_the_runner_machine_lists_runs_shows_one_and_checks_a_release(released, fake_pi, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from prometheus import cli
    monkeypatch.setenv("PROMETHEUS_AGENTS", str(released.parent))
    (tmp_path / "in.txt").write_text("a\n")
    agent = run_release.load_agent(str(released))
    done = agent.start(params={"source": str(tmp_path / "in.txt")}, approve=["save"])
    done.wait(30)
    failing = agent.start(params={"source": str(tmp_path / "missing.txt")}, approve=["save"])
    failing.wait(30)

    listed = cli.list_released_runs("counter")
    assert [(r["run"], r["version"], r["state"]) for r in listed] == [(done.id, "v1.0", "complete"), (failing.id, "v1.0", "failed")]
    assert "stage count failed" in listed[1]["error"]
    table = CliRunner().invoke(cli.app, ["runs", "counter"], terminal_width=200)
    assert table.exit_code == 0 and done.id in table.output and "failed" in table.output, table.output
    one = CliRunner().invoke(cli.app, ["runs", "counter", failing.id], terminal_width=200)
    assert one.exit_code == 0 and "stage count failed" in one.output and "wave all" in one.output, one.output
    assert f"prometheus bundle counter {failing.id}" in one.output  # where to go from here
    assert "running" not in [r["state"] for r in cli.list_released_runs("counter")]  # showing changed nothing

    checked = CliRunner().invoke(cli.app, ["doctor", "counter"], terminal_width=200)
    assert "counter v1.0" in checked.output and "model stages" in checked.output and "none: no model key needed" in checked.output
    assert "git identity" not in checked.output  # a machine that only runs releases commits nothing
    assert ("uv.lock installs here" in checked.output) or ("uv sync --locked --dry-run" in checked.output), checked.output


def test_a_release_installed_frozen_runs_without_the_agents_repository(released, fake_pi, tmp_path, monkeypatch):
    from prometheus import cli, share
    monkeypatch.setenv("PROMETHEUS_AGENTS", str(tmp_path / "runner-agents"))  # a machine that only runs it: no repository
    seen = []
    assert pytest.raises(run_release.PrometheusError, share.install, f"{released}@v1", confirm=lambda info: seen.append(info) or False)
    assert [f for f, _ in seen[0]["own_code"]] == ["scripts/count.py", "scripts/save.py"]  # its own code, for review
    assert not (tmp_path / "releases" / "counter" / "v1.0").exists()  # declined: nothing kept
    with pytest.raises(run_release.PrometheusError, match="no agent 'counter'.*prometheus install"):
        run_release.load_agent("counter@v1")

    folder = share.install(f"{released}@v1")
    assert folder == tmp_path / "releases" / "counter" / "v1.0"
    record = json.loads((folder / "RELEASE.json").read_text())
    assert (record["version"], record["source"], record["interface"]["side_effects"]) == ("v1.0", str(released), ["save"])
    assert not (folder / ".pi" / "extensions").exists() and not (tmp_path / "runner-agents" / "counter").exists()
    assert git_in(folder)("log").returncode != 0  # no history: an empty repository, for pi-subagents' checks
    assert share.install(f"{released}@v1.0") == folder  # here already

    (tmp_path / "in.txt").write_text("a\nb\n")
    agent = run_release.load_agent("counter@v1")
    assert (agent.version, agent.folder, agent.side_effects) == ("v1.0", folder, ["save"])
    assert agent.run(params={"source": str(tmp_path / "in.txt")}, approve=["save"], timeout=30).ok
    assert [r["version"] for r in cli.list_released_runs("counter")] == ["v1.0"]

    git = git_in(released)  # someone moves the tag: the installed v1.0 stays what it was
    (released / "scripts" / "count.py").write_text("print('moved')\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "moved")
    git("-c", "user.name=t", "-c", "user.email=t@t", "tag", "-f", "-a", "v1.0", "-m", git("tag", "-l", "--format=%(contents)", "v1.0").stdout)
    with pytest.raises(run_release.PrometheusError, match="a release never changes"):
        share.install(f"{released}@v1.0")
