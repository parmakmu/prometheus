"""session_read.py lists an agent's Pi sessions and shows bounded transcripts, so no model parses the JSONL."""
import json
import os

from conftest import SKILL

SCRIPT = SKILL / "scripts" / "session_read.py"


def line(**entry):
    return json.dumps(entry)


def message(role, ts, content, **extra):
    return line(type="message", timestamp=ts, message={"role": role, "content": content, **extra})


def make_sessions(tmp_path):
    """An agent and its conversations, in its own .pi/sessions."""
    agent_dir = tmp_path / "wf"
    agent_dir.mkdir()
    (agent_dir / "plan.yaml").write_text("format: 1\n")
    folder = agent_dir / ".pi" / "sessions"
    folder.mkdir(parents=True)
    rows = [line(type="session", timestamp="2026-01-01T10:00:00Z"),
            line(type="model_change", modelId="m"),
            message("user", "2026-01-01T10:00:01Z", "Run this agent."),
            message("assistant", "2026-01-01T10:00:02Z", [{"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "Launching."},
                                                           {"type": "toolCall", "id": "c1", "name": "agent_compile_wave", "arguments": {"x": 1}}],
                    usage={"cost": {"total": 0.01}}),
            message("toolResult", "2026-01-01T10:00:03Z", [{"type": "text", "text": "boom " * 1000}], toolName="agent_compile_wave", isError=True)]
    rows += [message("user", f"2026-01-01T10:01:{n:02d}Z", f"later {n}") for n in range(10, 20)]
    (folder / "2026-01-01T10-00-00-000Z_abc.jsonl").write_text("\n".join(rows))
    (folder / "2026-01-02T10-00-00-000Z_def.jsonl").write_text("\n".join(
        [line(type="session", timestamp="2026-01-02T10:00:00Z"), message("user", "2026-01-02T10:00:01Z", "Improve this agent from how it actually went.")]))
    return agent_dir, dict(os.environ)


def test_list_is_one_line_per_session_newest_first_marking_plan_sessions(tmp_path, run_py):
    agent_dir, env = make_sessions(tmp_path)
    lines = run_py([SCRIPT, "list", "--agent", agent_dir], env=env).data["text"].splitlines()
    assert len(lines) == 2 and lines[0].startswith("2026-01-02") and " | plan | " in lines[0]
    assert lines[1].startswith("2026-01-01") and "15 records | 1 turns | $0.0100" in lines[1] and "plan" not in lines[1]


def test_show_is_compact_lines_clipped_without_thinking(tmp_path, run_py):
    agent_dir, env = make_sessions(tmp_path)
    data = run_py([SCRIPT, "show", "2026-01-01", "--agent", agent_dir, "--to", "5", "--text-chars", "50"], env=env).data
    user, assistant, result = data["text"].splitlines()  # records 1-2 are not conversation
    assert user == "3 10:00:01 U Run this agent."
    assert assistant == '4 10:00:02 A Launching. -> agent_compile_wave({"x": 1})'
    assert result.startswith("5 10:00:03 R! agent_compile_wave: boom boom") and len(result) < 90  # a failure keeps the full width
    assert "secret" not in data["text"] and "{" not in user


def test_a_successful_result_is_clipped_harder_than_a_failure(tmp_path, run_py):
    agent_dir, env = make_sessions(tmp_path)
    folder = agent_dir / ".pi" / "sessions"
    (folder / "2026-01-03T00-00-00-000Z_ok.jsonl").write_text("\n".join(
        [line(type="session", timestamp="2026-01-03T00:00:00Z"),
         message("toolResult", "2026-01-03T00:00:01Z", [{"type": "text", "text": "x" * 500}], toolName="t", isError=False)]))
    data = run_py([SCRIPT, "show", "2026-01-03", "--agent", agent_dir, "--text-chars", "100"], env=env).data
    assert data["text"].count("x") <= 50 and data["text"].startswith("2 00:00:01 R t:")


def test_show_around_a_timestamp_and_the_size_cap_with_a_continuation(tmp_path, run_py):
    agent_dir, env = make_sessions(tmp_path)
    around = run_py([SCRIPT, "show", "2026-01-01", "--agent", agent_dir, "--around", "2026-01-01T10:01:15Z", "--window", "1"], env=env).data
    assert [row.split(" U ")[1] for row in around["text"].splitlines()] == ["later 14", "later 15", "later 16"]
    capped = run_py([SCRIPT, "show", "2026-01-01", "--agent", agent_dir, "--max-chars", "200"], env=env).data
    assert capped["next_from"] and len(capped["text"].splitlines()) < 13
    rest = run_py([SCRIPT, "show", "2026-01-01", "--agent", agent_dir, "--from", str(capped["next_from"])], env=env).data
    assert rest["text"].startswith(f"{capped['next_from']} ")


def test_show_refuses_an_unknown_or_ambiguous_session_and_paths(tmp_path, run_py):
    agent_dir, env = make_sessions(tmp_path)
    for name in ("nope", "2026-01", "../../etc/passwd"):
        r = run_py([SCRIPT, "show", name, "--agent", agent_dir], env=env)
        assert r.returncode == 1 and r.data["ok"] is False, name
