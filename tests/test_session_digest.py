"""session_digest.py condenses a workflow's Pi sessions and runs into evidence for /workflow-improve."""
import json

from conftest import SKILL

SCRIPT = SKILL / "scripts" / "session_digest.py"


def line(**entry):
    return json.dumps(entry)


def call(n, name, **args):
    return line(type="message", message={"role": "assistant", "content": [{"type": "toolCall", "id": f"c{n}", "name": name, "arguments": args}],
                                         "usage": {"cost": {"total": 0.01}}})


def result(n, name, text, error=False):
    return line(type="message", timestamp="t", message={"role": "toolResult", "toolCallId": f"c{n}", "toolName": name,
                                                        "isError": error, "content": [{"type": "text", "text": text}]})


def test_digest_reports_failures_repeats_cost_and_unfinished_launches(tmp_path, run_py):
    workflow = tmp_path / "wf"
    (workflow / "runs" / "2026-01-01-1").mkdir(parents=True)
    (workflow / "plan.yaml").write_text("version: 1\n")
    (workflow / "runs" / "2026-01-01-1" / "run.yaml").write_text(
        "id: 2026-01-01-1\nstatus: done\nlaunches:\n  - {wave: w1, status: failed, cost_usd: 0.5}\n  - {wave: w1, status: done, cost_usd: 0.25}\n"
    )
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rows = [line(type="session", timestamp="2026-01-01T00:00:00Z"), line(type="message", message={"role": "user", "content": "go"})]
    for n in range(3):
        rows += [call(n, "bash", command="uv run gates/g.py"), result(n, "bash", "Traceback (most recent call last)")]
    rows += [call(9, "workflow_check"), result(9, "workflow_check", '{"summary": {"note": "blocked by review"}}')]
    (sessions / "a.jsonl").write_text("\n".join(rows))

    data = run_py([SCRIPT, "--workflow", workflow, "--sessions-dir", sessions]).data

    session = data["sessions"][0]
    assert session["user_messages"] == ["go"]
    assert session["cost_usd"] == 0.04
    assert len(session["failed_or_blocked"]) == 3  # a normal result that mentions "blocked" is not one
    assert session["repeated_calls"][0]["times"] == 3
    run = data["runs"][0]
    assert run["cost_usd"] == 0.75
    assert run["relaunched_waves"] == ["w1"]
    assert [x["status"] for x in run["unfinished_launches"]] == ["failed"]


def test_digest_refuses_a_folder_that_is_not_a_workflow(tmp_path, run_py):
    result = run_py([SCRIPT, "--workflow", tmp_path])
    assert result.returncode == 1 and result.data["ok"] is False


def test_sessions_before_the_last_lesson_are_skipped(tmp_path, run_py):
    workflow = tmp_path / "wf"
    workflow.mkdir()
    (workflow / "plan.yaml").write_text("version: 1\n")
    (workflow / "improvements.yaml").write_text("- {date: '2026-01-02', target: gates/g.py, decision: rejected}\n")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "old.jsonl").write_text(line(type="session", timestamp="2026-01-01T00:00:00Z"))
    (sessions / "new.jsonl").write_text(line(type="session", timestamp="2026-01-03T00:00:00Z"))

    data = run_py([SCRIPT, "--workflow", workflow, "--sessions-dir", sessions]).data

    assert [x["file"] for x in data["sessions"]] == ["new.jsonl"]
    assert data["lessons"][0]["decision"] == "rejected"
