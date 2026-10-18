"""A released run's bundle: what goes in (what the run did, without what it read), what goes in only when asked (the run,
pi-subagents' records of it, the children's sessions, the caller's files), and secrets and param values replaced."""
import datetime
import json
import os
import tarfile
from pathlib import Path

from conftest import SKILL, git_in
from ruamel.yaml import YAML

SCRIPTS = SKILL / "scripts"
YAML_SAFE = YAML(typ="safe", pure=True)
SECRET = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"


def fake_released_run(tmp_path, slug="mailer", state="failed"):
    """A release's run folder in a releases folder, as frozen.Run leaves it, and pi-subagents' records of its one launch: the
    wave's folder, a failed child's folder, its session file (in the temp folder: one failed call, whose result quotes
    the caller's file and the secret) and its output (in artifacts/)."""
    agent = tmp_path / "releases" / slug / "v1.0"
    run = agent / "runs" / "20261016-101010-abcd"
    (run / ".compiled").mkdir(parents=True)
    (run / "inputs").mkdir()
    (run / "work").mkdir()
    (agent / "plan.yaml").write_text("format: 1\nparams:\n  api_key: {type: secret, env: MAILER_KEY}\n  folder: {type: path}\n",
                                     encoding="utf-8")
    (agent / "RELEASE.json").write_text(json.dumps({"agent": slug, "version": "v1.0", "commit": "0123456789ab" + "c" * 28}))
    (agent / ".pi" / "skills" / "agent").mkdir(parents=True)
    (agent / ".pi" / "skills" / "agent" / "VERSION").write_text("0.1.0+abc1234 deadbeef\n", encoding="utf-8")
    (run / "call.json").write_text(json.dumps({"agent": slug, "version": "v1.0", "params": {"folder": str(run / "inputs" / "folder")},
                                               "approve": []}), encoding="utf-8")
    (run / "inputs" / "mail.txt").write_text("the caller's data\n", encoding="utf-8")
    (run / "work" / "notes.md").write_text("half done\n", encoding="utf-8")
    (run / ".compiled" / "w1.json").write_text(json.dumps({"result": {"status": "failed", "at": ["read"]}}), encoding="utf-8")
    (run / "events.jsonl").write_text(json.dumps({"event": "end", "state": state}) + "\n", encoding="utf-8")
    # a secret param's value where it shouldn't be, and a key no param holds
    (run / "pi.log").write_text(f"auth with {SECRET}\naws AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")

    root = tmp_path / "pis"
    records = root / "async-subagent-runs"
    session = tmp_path / "tmp" / "pi-subagent-session-AbC" / "child-1" / "run-0" / "session.jsonl"
    session.parent.mkdir(parents=True)
    read = str(run / "inputs" / "folder" / "mail.txt")
    session.write_text("".join(json.dumps(x) + "\n" for x in (
        {"type": "session"},
        {"type": "message", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "c1", "name": "read", "arguments": {"path": read}}]}},
        {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "read", "isError": True,
                                        "content": [{"type": "text", "text": f"cannot open {read} with {SECRET}"}]}})), encoding="utf-8")
    output = root / "artifacts" / "child-1_reader_output.md"
    output.parent.mkdir(parents=True)
    output.write_text("could not parse mail.txt\n", encoding="utf-8")
    (records / "wave-1").mkdir(parents=True)
    (records / "wave-1" / "status.json").write_text(json.dumps({
        "runId": "wave-1", "state": "failed", "steps": [{"agent": "reader", "workflowKey": "read", "status": "failed",
                                                        "runId": "child-1", "sessionFile": str(session), "error": "exit 1"}]}), encoding="utf-8")
    (records / "wave-1" / "workflow-children.jsonl").write_text(json.dumps({"type": "settle", "key": "read", "result": {
        "ok": False, "error": "could not parse", "output": "could not parse mail.txt", "outputArtifactPath": str(output),
        "asyncDir": str(records / "child-1")}}) + "\n", encoding="utf-8")
    (records / "child-1").mkdir()
    (records / "child-1" / "status.json").write_text(json.dumps({"runId": "child-1", "state": "failed",
                                                                 "transcriptPath": "/etc/passwd"}), encoding="utf-8")  # not pi-subagents': never taken
    (run / "run.yaml").write_text(json.dumps({"status": state, "error": "stage read failed", "launches": [
        {"wave": "w1", "stages": ["read"], "subagent_run_id": "wave-1", "status": "failed", "cost_usd": 0.01}]}), encoding="utf-8")
    env = {**os.environ, "PI_SUBAGENTS_TEMP_ROOT": str(root), "TMPDIR": str(tmp_path / "tmp"), "MAILER_KEY": SECRET}
    return run, env


def read_bundle(path):
    with tarfile.open(path) as tar:
        return {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers() if m.isfile()}


def test_a_bundle_holds_what_the_run_did_without_what_it_read(tmp_path, run_py):
    run, env = fake_released_run(tmp_path)
    r = run_py([SCRIPTS / "run_bundle.py", "make", "--run", run, "--out", tmp_path / "out"], env=env)
    assert r.data["ok"] and not r.data["with_data"] and r.data["files"] == 1 and r.data["redacted"] == ["MAILER_KEY"], r
    files = read_bundle(r.data["bundle"])
    assert Path(r.data["bundle"]).name == "mailer-v1.0-20261016-101010-abcd.tgz" and list(files) == ["bundle.json"]
    text = files["bundle.json"]
    assert SECRET not in text and str(run) not in text and "the caller's data" not in text and "could not parse" not in text
    info = json.loads(text)
    assert (info["agent"], info["version"], info["commit"], info["state"]) == ("mailer", "v1.0", "0123456789ab" + "c" * 28, "failed")
    assert info["versions"]["prometheus"] == "0.1.0+abc1234 deadbeef" and info["params"] == ["folder"]
    assert "paths" not in info and "sessions" not in info and "record" not in info["launches"][0]
    child = info["launches"][0]["children"][0]
    assert (child["key"], child["status"], child["error"]) == ("read", "failed", "exit 1") and not {"output", "session"} & set(child)
    assert child["activity"]["tool_calls"] == {"read": 1}, child
    quoted = "cannot open <folder>/mail.txt with ${MAILER_KEY}"  # the param's value and the secret replaced
    assert child["activity"]["failed_calls"] == [{"tool": "read", "result": quoted}], child
    assert r.data["to_check"] == ["run: stage read failed", "read: exit 1", f"read (read): {quoted}"], r


def test_a_bundle_with_data_holds_the_run_and_what_pi_subagents_kept_of_it(tmp_path, run_py):
    run, env = fake_released_run(tmp_path)
    r = run_py([SCRIPTS / "run_bundle.py", "make", "--run", run, "--out", tmp_path / "out", "--with-data"], env=env)
    assert r.data["ok"] and r.data["with_data"] and r.data["redacted"] == ["MAILER_KEY"] and r.data["to_check"] == [], r
    assert r.data["warnings"] == ["run/pi.log: looks like an API key or token"], r  # the param's value replaced, the other reported
    files = read_bundle(r.data["bundle"])
    assert {"run/call.json", "run/run.yaml", "run/events.jsonl", "run/pi.log", "run/.compiled/w1.json",
            "subagents/async-subagent-runs/wave-1/status.json", "subagents/async-subagent-runs/child-1/status.json",
            "subagents/artifacts/child-1_reader_output.md", "sessions/pi-subagent-session-AbC/child-1/run-0/session.jsonl"} <= set(files)
    assert files["run/inputs/mail.txt"] == "the caller's data\n" and "run/work/notes.md" in files, sorted(files)
    assert not any("passwd" in n for n in files)  # a path outside pi-subagents' folders is never taken
    assert SECRET not in "".join(files.values()) and "${MAILER_KEY}" in files["run/pi.log"]
    info = json.loads(files["bundle.json"])
    assert info["with_data"] is True
    child = info["launches"][0]["children"][0]
    assert child["session"] == "sessions/pi-subagent-session-AbC/child-1/run-0/session.jsonl" and "could not parse" in child["output"]


def test_a_running_run_is_not_bundled(tmp_path, run_py):
    run, env = fake_released_run(tmp_path, state="running")
    r = run_py([SCRIPTS / "run_bundle.py", "make", "--run", run, "--out", tmp_path], env=env)
    assert "still running" in r.data["error"], r
    r = run_py([SCRIPTS / "run_bundle.py", "make", "--run", tmp_path, "--out", tmp_path], env=env)
    assert "is not a run" in r.data["error"], r


def test_prometheus_bundle_finds_the_run_in_the_cache(tmp_path, monkeypatch):
    from prometheus import cli
    run, env = fake_released_run(tmp_path)
    for k in ("PI_SUBAGENTS_TEMP_ROOT", "TMPDIR", "MAILER_KEY"):
        monkeypatch.setenv(k, env[k])
    monkeypatch.setenv("PROMETHEUS_RELEASES", str(tmp_path / "releases"))
    assert cli.find_released_run("mailer", run.name) == run
    try:
        cli.find_released_run("mailer", "nope")
        raise AssertionError("found a run that isn't there")
    except cli.PrometheusError as exc:
        assert "no run 'nope'" in str(exc)


def test_prune_keeps_failed_runs_for_a_bundle_then_takes_all_pi_subagents_kept(tmp_path, run_py):
    run, env = fake_released_run(tmp_path)
    agent, pis = run.parent.parent, tmp_path / "pis"
    prune = lambda *extra: run_py([SCRIPTS / "run_history.py", "prune", "--agent", agent, "--keep", "0", *extra], env=env)
    assert prune().data["kept"] == [run.name]  # a failed run stays whole: a bundle is made from it
    r = prune("--keep-failed", "0")
    assert r.data["pruned"] == [run.name], r
    assert not (pis / "async-subagent-runs" / "wave-1").exists() and not (pis / "async-subagent-runs" / "child-1").exists()
    assert not (pis / "artifacts" / "child-1_reader_output.md").exists()
    assert not (tmp_path / "tmp" / "pi-subagent-session-AbC" / "child-1" / "run-0" / "session.jsonl").exists()
    assert (run / "run.yaml").is_file() and not (run / "work").exists() and Path("/etc/passwd").is_file()


def make_bundle(tmp_path, run_py, *extra):
    run, env = fake_released_run(tmp_path)
    r = run_py([SCRIPTS / "run_bundle.py", "make", "--run", run, "--out", tmp_path / "out", *extra], env=env)
    assert r.data["ok"], r
    return Path(r.data["bundle"])


def test_a_bundle_is_imported_once_for_improve_and_read_as_evidence(tmp_path, run_py):
    bundle = make_bundle(tmp_path, run_py, "--with-data")
    agent = tmp_path / "agents" / "mailer"
    agent.mkdir(parents=True)
    (agent / "plan.yaml").write_text("format: 1\n", encoding="utf-8")
    r = run_py([SCRIPTS / "run_bundle.py", "add", bundle, "--agent", agent])
    folder = "runs/imported/v1.0-20261016-101010-abcd"
    assert r.data["ok"] and r.data["imported"] == folder and not r.data["already"] and r.data["state"] == "failed", r
    assert r.data["warnings"] == []  # no git here: nothing to compare the release's commit with
    assert run_py([SCRIPTS / "run_bundle.py", "add", bundle, "--agent", agent]).data["already"]
    assert not (agent / "runs" / "imported" / ".v1.0-20261016-101010-abcd.partial").exists()

    digest = run_py([SCRIPTS / "session_digest.py", "--agent", agent]).data
    assert digest["runs"] == [] and len(digest["imported"]) == 1, digest  # not one of the agent's runs
    found = digest["imported"][0]
    assert (found["folder"], found["version"], found["error"]) == (folder, "v1.0", "stage read failed")
    child = found["failed_children"][0]
    assert (child["key"], child["wave"], child["error"]) == ("read", "w1", "exit 1") and "could not parse" in child["output"]
    assert child["session"] == f"{folder}/sessions/pi-subagent-session-AbC/child-1/run-0/session.jsonl"
    shown = run_py([SCRIPTS / "session_read.py", "show", child["session"], "--agent", agent])
    assert shown.data["ok"], shown  # agent_sessions reads an imported child's session
    outside = run_py([SCRIPTS / "session_read.py", "show", "runs/imported/../../plan.yaml", "--agent", agent])
    assert not outside.data["ok"]

    (agent / "improvements.yaml").write_text("- {date: '2999-01-01T00:00:00+00:00', target: x, decision: applied}\n", encoding="utf-8")
    assert run_py([SCRIPTS / "session_digest.py", "--agent", agent]).data["imported"] == []  # reviewed


def test_a_bundle_without_data_is_read_as_evidence_from_what_the_children_did(tmp_path, run_py):
    bundle = make_bundle(tmp_path, run_py)
    agent = tmp_path / "agents" / "mailer"
    agent.mkdir(parents=True)
    (agent / "plan.yaml").write_text("format: 1\n", encoding="utf-8")
    assert run_py([SCRIPTS / "run_bundle.py", "add", bundle, "--agent", agent]).data["ok"]
    found = run_py([SCRIPTS / "session_digest.py", "--agent", agent]).data["imported"][0]
    child = found["failed_children"][0]
    assert found["with_data"] is False and (child["output"], child["session"]) == ("", None), child
    assert child["activity"]["failed_calls"][0]["result"] == "cannot open <folder>/mail.txt with ${MAILER_KEY}", child


def test_a_bundle_of_another_agent_or_with_a_path_out_is_refused(tmp_path, run_py):
    import io
    bundle = make_bundle(tmp_path, run_py)
    other = tmp_path / "agents" / "other"
    other.mkdir(parents=True)
    (other / "plan.yaml").write_text("format: 1\n", encoding="utf-8")
    assert "is a run of mailer, not other" in run_py([SCRIPTS / "run_bundle.py", "add", bundle, "--agent", other]).data["error"]
    evil = tmp_path / "evil.tgz"
    with tarfile.open(evil, "w:gz") as tar:
        for name, data in (("bundle.json", json.dumps({"format": 3, "agent": "other", "version": "v1", "run": "x"}).encode()),
                           ("../../escaped.txt", b"x")):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    r = run_py([SCRIPTS / "run_bundle.py", "add", evil, "--agent", other])
    assert "cannot read" in r.data["error"] and not (tmp_path / "agents" / "escaped.txt").exists(), r
    assert not (other / "runs" / "imported" / "v1-x").exists()


def test_an_import_says_when_the_release_is_not_on_main(tmp_path, run_py):
    bundle = make_bundle(tmp_path, run_py)
    agent = tmp_path / "agents" / "mailer"
    agent.mkdir(parents=True)
    (agent / "plan.yaml").write_text("format: 1\n", encoding="utf-8")
    git = git_in(agent)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    r = run_py([SCRIPTS / "run_bundle.py", "add", bundle, "--agent", agent])
    assert r.data["ok"] and "0123456789abcccccccccccccccccccccccccccc, isn't in this repository" in r.data["warnings"][0], r


def test_an_agents_own_run_is_bundled_with_its_sessions_and_read_back_as_evidence(tmp_path, run_py):
    # an agent copied to another machine runs without prometheus: its own skill copy bundles its run there
    from test_run_finish import ready_agent
    elsewhere, k = ready_agent(tmp_path / "elsewhere", run_py, "mailer")
    plan = YAML_SAFE.load((elsewhere / "plan.yaml").read_text())
    plan["params"] = {**(plan.get("params") or {}), "api_key": {"type": "secret", "env": "MAILER_KEY"}}
    YAML_SAFE.dump(plan, elsewhere / "plan.yaml")
    (elsewhere / ".env").write_text(f"MAILER_KEY={SECRET}\n", encoding="utf-8")
    _, env = fake_released_run(tmp_path)  # only for pi-subagents' records of a launch (wave-1) and its env
    env.pop("MAILER_KEY")  # the agent's secret comes from its .env here
    rid = run_py([k / "run_steps.py", "start-run"], cwd=elsewhere, env=env).data["run"]
    run_yaml = elsewhere / "runs" / rid / "run.yaml"
    state = YAML_SAFE.load(run_yaml.read_text())
    state["launches"] = [{"wave": "w1", "stages": ["read"], "status": "failed", "subagent_run_id": "wave-1"}]
    YAML_SAFE.dump(state, run_yaml)
    sessions = elsewhere / ".pi" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().astimezone().isoformat()
    (sessions / "2026-10-16_run.jsonl").write_text(json.dumps({"type": "session", "timestamp": now}) + "\n"
                                                    + json.dumps({"type": "message", "text": f"key {SECRET}"}) + "\n")
    (sessions / "2020-01-01_old.jsonl").write_text(json.dumps({"type": "session", "timestamp": "2020-01-01T00:00:00+00:00"}) + "\n")
    os.utime(sessions / "2020-01-01_old.jsonl", (0, 0))  # long before the run: not part of it

    # the child's own records, in its cwd (the run's work/), named relative to it: what pi-subagents does in a session
    records = elsewhere / "runs" / rid / "work" / ".pi" / "sessions" / "subagent-artifacts"
    records.mkdir(parents=True)
    (records / "child-1_reader_transcript.jsonl").write_text(json.dumps({
        "recordType": "message", "timestamp": now, "message": {"role": "assistant", "content": [{"type": "text", "text": "cannot parse"}]}}) + "\n")
    child_session = elsewhere / "runs" / rid / "work" / ".pi" / "sessions" / "2026-10-16_run" / "launch-1" / "run-0" / "session.jsonl"
    child_session.parent.mkdir(parents=True)
    child_session.write_text(json.dumps({"type": "session", "timestamp": now}) + "\n" + json.dumps(
        {"type": "message", "timestamp": now, "message": {"role": "user", "content": "Task: read the mail"}}) + "\n")
    steps = Path(env["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / "wave-1" / "status.json"
    status = json.loads(steps.read_text())
    status["steps"][0]["sessionFile"] = ".pi/sessions/2026-10-16_run/launch-1/run-0/session.jsonl"  # relative to its cwd
    steps.write_text(json.dumps(status))
    (elsewhere / "runs" / rid / "work" / "data.txt").write_text("the user's data\n")
    wave = Path(env["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / "wave-1" / "workflow-children.jsonl"
    wave.write_text(wave.read_text() + json.dumps({"type": "settle", "key": "read", "result": {"ok": False, "results": [
        {"transcriptPath": ".pi/sessions/subagent-artifacts/child-1_reader_transcript.jsonl"}]}}) + "\n")
    r = run_py([k / "run_bundle.py", "make", "--agent", elsewhere, "--out", elsewhere / "runs", "--with-data"], cwd=elsewhere, env=env)
    assert r.data["ok"] and r.data["redacted"] == ["MAILER_KEY"], r
    files = read_bundle(r.data["bundle"])
    info = json.loads(files["bundle.json"])
    assert Path(r.data["bundle"]).name == f"mailer-r1-{rid}.tgz" and info["kind"] == "run" and info["error"] == "wave w1 failed", info
    assert info["sessions"] == ["agent-sessions/2026-10-16_run.jsonl"] and "${MAILER_KEY}" in files["agent-sessions/2026-10-16_run.jsonl"]
    assert "run/run.yaml" in files and "subagents/async-subagent-runs/wave-1/status.json" in files
    child = info["launches"][0]["children"][0]
    transcript = "run/work/.pi/sessions/subagent-artifacts/child-1_reader_transcript.jsonl"
    assert child["key"] == "read" and child["transcript"] == transcript and transcript in files, child
    session = "run/work/.pi/sessions/2026-10-16_run/launch-1/run-0/session.jsonl"
    assert child["session"] == session and session in files, child  # the child's own Pi session, in its cwd
    assert "run/work/data.txt" in files

    home, _ = ready_agent(tmp_path / "home", run_py, "mailer")  # where it is developed
    assert run_py([SCRIPTS / "run_bundle.py", "add", r.data["bundle"], "--agent", home]).data["ok"]
    found = run_py([SCRIPTS / "session_digest.py", "--agent", home]).data["imported"][0]
    assert found["kind"] == "run" and found["agent_sessions"] == [f"runs/imported/r1-{rid}/agent-sessions/2026-10-16_run.jsonl"], found
    assert run_py([SCRIPTS / "session_read.py", "show", found["agent_sessions"][0], "--agent", home]).data["ok"]
    failed = found["failed_children"][0]
    assert failed["session"] == f"runs/imported/r1-{rid}/{session}", failed  # its own session, when it has one
    assert "U Task: read the mail" in run_py([SCRIPTS / "session_read.py", "show", failed["session"], "--agent", home]).data["text"]
    shown = run_py([SCRIPTS / "session_read.py", "show", f"runs/imported/r1-{rid}/{transcript}", "--agent", home]).data
    assert "A cannot parse" in shown["text"], shown  # a pi-subagents transcript reads like a session


def test_prune_removes_empty_launch_folders_but_not_while_a_wave_runs(tmp_path, run_py):
    from test_run_finish import ready_agent
    agent, k = ready_agent(tmp_path, run_py, "tidy")
    sessions = agent / ".pi" / "sessions"
    (sessions / "s1" / "empty-launch").mkdir(parents=True)
    (sessions / "s1" / "old-launch" / "run-0").mkdir(parents=True)
    (sessions / "s1" / "old-launch" / "run-0" / "session.jsonl").write_text("{}\n")  # an older pi-subagents kept sessions here
    prune = lambda *extra: run_py([k / "run_history.py", "prune", "--agent", agent, *extra])
    rid = run_py([k / "run_steps.py", "start-run"], cwd=agent).data["run"]
    run_yaml = agent / "runs" / rid / "run.yaml"
    state = YAML_SAFE.load(run_yaml.read_text())
    YAML_SAFE.dump({**state, "stages": {"count": "running"}}, run_yaml)  # a wave runs: it may still use its folder
    assert prune().data["empty_session_folders"] == 0 and (sessions / "s1" / "empty-launch").is_dir()
    YAML_SAFE.dump({**state, "stages": {}}, run_yaml)
    assert prune("--dry-run").data["empty_session_folders"] == 1 and (sessions / "s1" / "empty-launch").is_dir()
    assert prune().data["empty_session_folders"] == 1
    assert not (sessions / "s1" / "empty-launch").exists() and (sessions / "s1" / "old-launch" / "run-0" / "session.jsonl").is_file()
