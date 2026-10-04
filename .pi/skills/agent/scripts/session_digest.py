#!/usr/bin/env python3
"""Condense an agent's Pi sessions and runs into evidence for /agent-improve.

Usage: session_digest.py --agent DIR [--sessions-dir DIR] [--sessions 5] [--runs 3]

Only what is new: sessions that started, and runs that started, after the newest
entry of the agent's improvements.yaml (the lessons /agent-improve saved)
were not reviewed yet; the entries are returned as `lessons`. A lesson's `date`
is a timestamp (an old one may be a bare date, read as the start of that day).
Sessions of /agent-improve and /agent-revise are skipped: they change the
plan, they are not evidence of how it ran.

Pi keeps one JSONL per session under <agent dir>/sessions/--<cwd with / as ->--/ (the agent dir is
$PI_CODING_AGENT_DIR, else ~/.pi/agent).
Prints one JSON object: `sessions` (newest first, at most --sessions), each with
its user messages, tool use and cost, the failed or blocked tool results, and the
calls repeated three times or more; `runs` from runs/*/run.yaml (status, cost,
launches that did not finish, waves launched more than once). Only signals: the
model opens the files they point at. Nothing is changed.
"""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated

import prometheus_version
import typer
from runs import list_runs
from utils import emit, read_yaml

SNIPPET = 240  # characters kept of a message, argument or result
FAILURE = ("traceback", "exit code", "command not found", "no module named", "permission denied")
REPEATS = 3
LESSONS = "improvements.yaml"
LIMIT = 12  # entries kept per signal per session
# how the commands' prompts begin (templates/agent/.pi/extensions/agent/index.ts, at the repo root): sessions that edit the plan
PLAN_SESSIONS = ("Improve this agent from how it actually went", "Change this agent's plan following")


def default_sessions_dir(agent_dir: Path) -> Path:
    """The agent's Pi sessions: its own .pi/sessions (the template's `sessionDir`)."""
    return agent_dir / ".pi" / "sessions"


def moment(value):
    """A timestamp or bare date as an aware datetime (a bare date is the start of that day, local time); None if unreadable."""
    try:
        when = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return when if when.tzinfo else when.astimezone()


def clip(text, size=SNIPPET):
    text = " ".join(str(text).split())
    return text if len(text) <= size else text[: size - 3] + "..."


def text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def session_start(path: Path):
    """When a session started, from its first line (Pi's session header) only; None if it has none."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            head = json.loads(f.readline())
    except (OSError, ValueError):
        return None
    return head.get("timestamp") if isinstance(head, dict) and head.get("type") == "session" else None


def digest_session(path: Path):
    calls, tools, args_seen, failures, users = {}, Counter(), Counter(), [], []
    cost = turns = records = 0
    started = model = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        records += 1
        kind, msg = entry.get("type"), entry.get("message") or {}
        if kind == "session":
            started = entry.get("timestamp")
        elif kind == "model_change":
            model = entry.get("modelId")
        elif kind == "custom_message":
            continue
        role = msg.get("role")
        if role == "user":
            users.append(clip(text_of(msg.get("content"))))
        elif role == "assistant":
            turns += 1
            cost += ((msg.get("usage") or {}).get("cost") or {}).get("total", 0)
            for part in msg.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "toolCall":
                    name, args = part.get("name"), json.dumps(part.get("arguments"), sort_keys=True)
                    calls[part.get("id")] = (name, clip(args))
                    tools[name] += 1
                    args_seen[(name, clip(args))] += 1
        elif role == "toolResult":
            text = text_of(msg.get("content"))
            if msg.get("isError") or any(word in text[:200].lower() for word in FAILURE):
                name, args = calls.get(msg.get("toolCallId"), (msg.get("toolName"), ""))
                failures.append({"tool": name, "call": args, "result": clip(text), "at": entry.get("timestamp")})
    return {
        "file": path.name,
        "plan_session": bool(users) and users[0].startswith(PLAN_SESSIONS),
        "started": started,
        "records": records,
        "model": model,
        "assistant_turns": turns,
        "cost_usd": round(cost, 4),
        "tool_calls": dict(tools.most_common()),
        "user_messages": users[:LIMIT],
        "failed_or_blocked": failures[:LIMIT],
        "repeated_calls": [{"tool": n, "call": a, "times": c} for (n, a), c in args_seen.most_common() if c >= REPEATS][:LIMIT],
    }


def digest_run(run_id: str, run_dir: Path):
    state = read_yaml(run_dir / "run.yaml", {})
    launches = [x for x in state.get("launches") or [] if isinstance(x, dict)]
    waves = Counter(x.get("wave") for x in launches)
    return {
        "id": run_id,
        "status": state.get("status"),
        "revision": state.get("revision"),
        "cost_usd": round(sum(x.get("cost_usd") or 0 for x in launches), 4),
        "launches": len(launches),
        "unfinished_launches": [
            {k: x.get(k) for k in ("wave", "status", "ends_with")} for x in launches if x.get("status") != "done"
        ],
        "relaunched_waves": [w for w, n in waves.items() if n > 1],
        "stages": state.get("stages"),
    }


def main(
    agent_dir: Annotated[Path, typer.Option("--agent", help="The agent folder, agents/<slug>.")],
    sessions_dir: Annotated[Path | None, typer.Option(help="Pi's session folder for the agent; default from its path.")] = None,
    sessions: Annotated[int, typer.Option(help="How many of the newest unreviewed sessions to read.")] = 5,
    runs: Annotated[int, typer.Option(help="How many of the newest unreviewed runs to read.")] = 3,
):
    agent_dir = agent_dir.resolve()
    if not (agent_dir / "plan.yaml").is_file():
        emit({"ok": False, "error": f"{agent_dir} is not an agent"}, 1)
    folder = sessions_dir or default_sessions_dir(agent_dir)
    lessons = [x for x in read_yaml(agent_dir / LESSONS, []) if isinstance(x, dict)]
    since = max(filter(None, (moment(x.get("date")) for x in lessons)), default=None)

    def unreviewed(started):
        when = moment(started)
        return since is None or (when is not None and when > since)

    found = []
    for f in sorted(folder.glob("*.jsonl"), reverse=True) if folder.is_dir() else []:
        if len(found) == sessions:
            break
        if (start := session_start(f)) is not None and not unreviewed(start):
            continue  # reviewed: known from the header, without reading the whole file
        d = digest_session(f)
        if unreviewed(d["started"]) and not d.pop("plan_session"):
            found.append(d)
    new_runs = [(rid, d) for rid, d in list_runs(agent_dir) if unreviewed((read_yaml(d / "run.yaml", {}) or {}).get("started_at"))]
    emit({
        "ok": True,
        "agent": agent_dir.name,
        "now": datetime.now().astimezone().isoformat(timespec="seconds"),  # for improvements.yaml `date`
        "prometheus_version": prometheus_version.installed(agent_dir),
        "sessions_dir": str(folder),
        "lessons": lessons,
        "sessions": found,
        "runs": [digest_run(rid, d) for rid, d in new_runs[-runs:]] if runs else [],
        "note": "This digest is the evidence: do not open the raw session files (their lines are too long to read). Read more of a session with agent_sessions (show <file> --around <the failure's at>); open only the run.yaml, gates and scripts a finding points at.",
    })


if __name__ == "__main__":
    typer.run(main)
