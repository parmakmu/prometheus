"""session_digest.py condenses an agent's Pi sessions and runs into evidence for /agent-improve."""
import json
from datetime import datetime

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
    agent_dir = tmp_path / "wf"
    (agent_dir / "runs" / "2026-01-01-1").mkdir(parents=True)
    (agent_dir / "plan.yaml").write_text("version: 1\n")
    (agent_dir / "runs" / "2026-01-01-1" / "run.yaml").write_text(
        "id: 2026-01-01-1\nstatus: done\nlaunches:\n  - {wave: w1, status: failed, cost_usd: 0.5}\n  - {wave: w1, status: done, cost_usd: 0.25}\n"
    )
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rows = [line(type="session", timestamp="2026-01-01T00:00:00Z"), line(type="message", message={"role": "user", "content": "go"})]
    for n in range(3):
        rows += [call(n, "bash", command="uv run gates/g.py"), result(n, "bash", "Traceback (most recent call last)")]
    rows += [call(9, "agent_check"), result(9, "agent_check", '{"summary": {"note": "blocked by review"}}')]
    (sessions / "a.jsonl").write_text("\n".join(rows))

    data = run_py([SCRIPT, "--agent", agent_dir, "--sessions-dir", sessions]).data

    session = data["sessions"][0]
    assert session["user_messages"] == ["go"]
    assert session["cost_usd"] == 0.04
    assert datetime.fromisoformat(data["now"]).utcoffset() is not None  # improvements.yaml `date`, with its offset
    assert len(session["failed_or_blocked"]) == 3  # a normal result that mentions "blocked" is not one
    assert session["repeated_calls"][0]["times"] == 3
    run = data["runs"][0]
    assert run["cost_usd"] == 0.75
    assert run["relaunched_waves"] == ["w1"]
    assert [x["status"] for x in run["unfinished_launches"]] == ["failed"]


def test_digest_refuses_a_folder_that_is_not_an_agent(tmp_path, run_py):
    result = run_py([SCRIPT, "--agent", tmp_path])
    assert result.returncode == 1 and result.data["ok"] is False


def test_sessions_before_the_last_lesson_are_skipped(tmp_path, run_py):
    agent_dir = tmp_path / "wf"
    agent_dir.mkdir()
    (agent_dir / "plan.yaml").write_text("version: 1\n")
    (agent_dir / "improvements.yaml").write_text("- {date: '2026-01-02', target: gates/g.py, decision: rejected}\n")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "old.jsonl").write_text(line(type="session", timestamp="2026-01-01T00:00:00Z"))
    (sessions / "new.jsonl").write_text(line(type="session", timestamp="2026-01-03T00:00:00Z"))

    data = run_py([SCRIPT, "--agent", agent_dir, "--sessions-dir", sessions]).data

    assert [x["file"] for x in data["sessions"]] == ["new.jsonl"]
    assert data["lessons"][0]["decision"] == "rejected"


def agent_with(tmp_path, lessons=None):
    agent_dir = tmp_path / "wf"
    agent_dir.mkdir()
    (agent_dir / "plan.yaml").write_text("version: 1\n")
    if lessons:
        (agent_dir / "improvements.yaml").write_text(lessons)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    return agent_dir, sessions


def test_a_session_from_the_day_of_the_last_lesson_counts_only_if_it_started_after_it(tmp_path, run_py):
    agent_dir, sessions = agent_with(tmp_path, "- {date: '2026-01-02T12:00:00+00:00', target: gates/g.py, decision: applied}\n")
    (sessions / "morning.jsonl").write_text(line(type="session", timestamp="2026-01-02T09:00:00Z"))
    (sessions / "evening.jsonl").write_text(line(type="session", timestamp="2026-01-02T18:00:00Z"))

    data = run_py([SCRIPT, "--agent", agent_dir, "--sessions-dir", sessions]).data

    assert [x["file"] for x in data["sessions"]] == ["evening.jsonl"]


def test_sessions_that_edit_the_plan_are_not_evidence(tmp_path, run_py):
    agent_dir, sessions = agent_with(tmp_path)
    for name, first in (("a", "Run this agent."), ("b", "Improve this agent from how it actually went. You are on"),
                        ("c", "Change this agent's plan following the rules")):
        (sessions / f"{name}.jsonl").write_text("\n".join([line(type="session", timestamp="2026-01-01T00:00:00Z"),
                                                          line(type="message", message={"role": "user", "content": first})]))

    data = run_py([SCRIPT, "--agent", agent_dir, "--sessions-dir", sessions]).data

    assert [x["file"] for x in data["sessions"]] == ["a.jsonl"] and "plan_session" not in data["sessions"][0]


def test_only_the_newest_runs_since_the_last_review_are_digested(tmp_path, run_py):
    agent_dir, sessions = agent_with(tmp_path, "- {date: '2026-01-03T00:00:00+00:00', target: plan.yaml, decision: applied}\n")
    for n, started in enumerate(["2026-01-01", "2026-01-04", "2026-01-05", "2026-01-06", "2026-01-07"], start=1):
        run = agent_dir / "runs" / f"2026-01-0{n}-1"
        run.mkdir(parents=True)
        (run / "run.yaml").write_text(f"id: 2026-01-0{n}-1\nstatus: done\nstarted_at: '{started}T10:00:00+00:00'\n")

    data = run_py([SCRIPT, "--agent", agent_dir, "--sessions-dir", sessions, "--runs", "3"]).data

    # run 1 predates the lesson; of the four after it, the newest three
    assert [r["id"] for r in data["runs"]] == ["2026-01-03-1", "2026-01-04-1", "2026-01-05-1"]
    assert "do not open the raw session files" in data["note"].lower()


def test_digest_reads_the_agents_own_sessions_folder(tmp_path, run_py):
    # sessionDir ".pi/sessions" (the template's setting): conversations live in the agent
    agent_dir = tmp_path / "wf"
    (agent_dir / ".pi" / "sessions").mkdir(parents=True)
    (agent_dir / "plan.yaml").write_text("request: x\n")
    data = run_py([SCRIPT, "--agent", agent_dir]).data
    assert data["sessions_dir"] == str(agent_dir / ".pi" / "sessions"), data


def test_agent_costs_count_each_turn_toward_the_runs_it_names(tmp_path):
    import sys
    sys.path.insert(0, str(SKILL / "scripts"))
    from session_digest import agent_costs

    user = lambda text: line(type="message", message={"role": "user", "content": text})
    rows = [user("/agent-run"), call(1, "ask_user_question"), call(2, "agent_new_run"), result(2, "agent_new_run", '{"run": "r1"}'),
            line(type="custom_message", customType="subagent-notify"),  # a wave's end wakes the agent: same turn
            call(3, "agent_log_finish"), result(3, "agent_log_finish", '{"run": "r1"}'),
            user("thanks"), call(4, "read"),  # a turn naming no run counts toward none
            user("/agent-run"), call(5, "agent_compile"), result(5, "agent_compile", '{"run": "r2"}')]
    (tmp_path / "a.jsonl").write_text("\n".join(rows))
    assert agent_costs(tmp_path) == {"r1": 0.03, "r2": 0.01}
