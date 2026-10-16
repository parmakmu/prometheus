#!/usr/bin/env python3
"""Read this agent's Pi sessions (JSONL) without opening the files.

  session_read.py list [--agent .] [--limit 10]
  session_read.py show FILE [--agent .] [--from N] [--to N] [--around TIME] [--window 6]
                           [--text-chars 300] [--max-chars 8000]

A session's JSONL has lines of tens of KB (the system prompt, tool results), which Pi's read tool
refuses, so a model ends up writing its own parser. This is that parser, once, and its output is
made to be cheap to read: plain lines, no JSON syntax, clipped text.

`list`: one line per session, newest first: file, start, records, turns, cost, `plan` for a
/agent-improve or /agent-revise one, and the first user message.
`show`: a bounded transcript of one session (FILE is a name from `list`, or a unique prefix of it, or a
child's session or pi-subagents transcript in an imported bundle, runs/imported/..., as session_digest names it),
one line per record: its number (the line number in the file), the time, then U (user), A
(assistant: its text, then ` -> tool(args)` for each call) or R (a tool result; R! when it failed).
Text is clipped to --text-chars (a successful result to half of that: failures matter more).
--from/--to select records by number; --around TIME centres --window records on the first one at or
after TIME (the `at` of a failure in session_digest's output). It stops at --max-chars and says
where to continue (`next_from`). Only the conversation is shown, not the thinking.

Prints one JSON object whose `text` holds the lines, so a tool can print it as it is. Changes nothing.
"""
import json
from pathlib import Path
from typing import Annotated

import typer
from session_digest import clip, default_sessions_dir, digest_session, moment, text_of
from utils import emit

app = typer.Typer(add_completion=False)


def records(path: Path):
    """[(line number, entry)] of the readable lines."""
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        try:
            out.append((n, json.loads(line)))
        except ValueError:
            continue
    return out


def sessions_dir(agent_dir: Path):
    return default_sessions_dir(agent_dir.resolve())


def find(agent_dir: Path, name: str):
    """The session file called `name` (or the one `name` is the start of), or a child's session in an imported bundle
    (runs/imported/..., as session_digest names it); exits with an error otherwise."""
    imported = (agent_dir.resolve() / name).resolve()
    if name.startswith("runs/imported/") and name.endswith(".jsonl") and imported.is_file() \
            and imported.is_relative_to(agent_dir.resolve() / "runs" / "imported"):
        return imported
    folder = sessions_dir(agent_dir)
    names = sorted(p.name for p in folder.glob("*.jsonl")) if folder.is_dir() else []
    exact = [n for n in names if n == name or n == f"{name}.jsonl"]
    found = (exact or [n for n in names if n.startswith(name)]) if name and "/" not in name else []
    if len(found) != 1:
        emit({"ok": False, "error": f"{name!r} matches {len(found)} sessions" + (f": {found[:5]}" if found else "") + "; use a name from `list`"}, 1)
    return folder / found[0]


def clock(timestamp):
    """HH:MM:SS of an ISO timestamp (the date is in the session's name), as written."""
    return str(timestamp or "")[11:19]


def as_session_entry(entry):
    """A record of a pi-subagents transcript (a child's: `recordType: message`, from an imported bundle) as the Pi
    session entry it carries; any other record as it is."""
    if entry.get("recordType") == "message" and isinstance(entry.get("message"), dict):
        return {"type": "message", "timestamp": entry.get("timestamp"),
                "message": {"toolName": entry.get("toolName"), "isError": entry.get("isError"), **entry["message"]}}
    return entry


def render(n, entry, size):
    """One record as one line, or None when it is not part of the conversation."""
    entry = as_session_entry(entry)
    msg = entry.get("message") or {}
    role = msg.get("role")
    if entry.get("type") != "message" or role not in ("user", "assistant", "toolResult"):
        return None
    head = f"{n} {clock(entry.get('timestamp'))}"
    if role == "toolResult":
        failed = bool(msg.get("isError"))
        return f"{head} R{'!' if failed else ''} {msg.get('toolName')}: {clip(text_of(msg.get('content')), size if failed else size // 2)}"
    if role == "user":
        return f"{head} U {clip(text_of(msg.get('content')), size)}"
    parts = [clip(text_of(msg.get("content")), size)] if text_of(msg.get("content")) else []
    parts += [f"-> {p.get('name')}({clip(json.dumps(p.get('arguments'), ensure_ascii=False), size // 2)})"
              for p in msg.get("content") or [] if isinstance(p, dict) and p.get("type") == "toolCall"]
    return f"{head} A {' '.join(parts)}"


@app.command("list")
def list_sessions(
    agent_dir: Annotated[Path, typer.Option("--agent", help="The agent folder.")] = Path("."),
    limit: Annotated[int, typer.Option(help="How many of the newest sessions.")] = 10,
):
    """The agent's sessions, newest first."""
    folder = sessions_dir(agent_dir)
    files = sorted(folder.glob("*.jsonl"), reverse=True)[:limit] if folder.is_dir() else []
    lines = []
    for d in map(digest_session, files):
        first = clip(d["user_messages"][0], 80) if d["user_messages"] else ""
        lines.append(f"{d['file']} | {d['records']} records | {d['assistant_turns']} turns | ${d['cost_usd']:.4f}"
                     f"{' | plan' if d['plan_session'] else ''} | {first}")
    emit({"ok": True, "text": "\n".join(lines) or "no sessions"})


@app.command("show")
def show(
    file: Annotated[str, typer.Argument(help="A session file name from `list`, or the start of one.")],
    agent_dir: Annotated[Path, typer.Option("--agent", help="The agent folder.")] = Path("."),
    first: Annotated[int | None, typer.Option("--from", help="First record number to show.")] = None,
    last: Annotated[int | None, typer.Option("--to", help="Last record number to show.")] = None,
    around: Annotated[str | None, typer.Option(help="A timestamp: centre the window on the first record at or after it.")] = None,
    window: Annotated[int, typer.Option(help="With --around: records on each side.")] = 6,
    text_chars: Annotated[int, typer.Option(help="Characters kept of each message, argument or result.")] = 300,
    max_chars: Annotated[int, typer.Option(help="Stop the transcript here; `next_from` says where to continue.")] = 8000,
):
    """A bounded transcript of one session."""
    path = find(agent_dir, file)
    recs = records(path)
    if around:
        when = moment(around)
        if when is None:
            emit({"ok": False, "error": f"--around {around!r} is not a timestamp"}, 1)
        hit = next((n for n, e in recs if (t := moment(e.get("timestamp"))) is not None and t >= when), recs[-1][0] if recs else 1)
        first, last = max(1, hit - window), hit + window
    lines, used, next_from = [], 0, None
    for n, entry in recs:
        if (first is not None and n < first) or (last is not None and n > last):
            continue
        row = render(n, entry, text_chars)
        if row is None:
            continue
        if lines and used + len(row) > max_chars:
            next_from = n
            break
        lines.append(row)
        used += len(row) + 1
    emit({"ok": True, "file": path.name, "records": recs[-1][0] if recs else 0, "next_from": next_from, "text": "\n".join(lines)})


if __name__ == "__main__":
    app()
