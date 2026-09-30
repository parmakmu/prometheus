#!/usr/bin/env python3
"""Condense a workflow's Pi sessions and runs into evidence for /workflow-improve.

Usage: session_digest.py --workflow DIR [--sessions-dir DIR] [--sessions 5]

Sessions older than the newest entry of the workflow's improvements.yaml (the
lessons /workflow-improve saved) were already reviewed and are skipped; the
entries are returned as `lessons`.

Pi keeps one JSONL per session under ~/.pi/agent/sessions/--<cwd with / as ->--/.
Prints one JSON object: `sessions` (newest first, at most --sessions), each with
its user messages, tool use and cost, the failed or blocked tool results, and the
calls repeated three times or more; `runs` from runs/*/run.yaml (status, cost,
launches that did not finish, waves launched more than once). Only signals: the
model opens the files they point at. Nothing is changed.
"""
import json
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from utils import emit, load_yaml_safe
from validate_plan import list_runs

SNIPPET = 240  # characters kept of a message, argument or result
FAILURE = ("traceback", "exit code", "command not found", "no module named", "permission denied")
REPEATS = 3
LESSONS = "improvements.yaml"
LIMIT = 12  # entries kept per signal per session


def default_sessions_dir(workflow: Path) -> Path:
    return Path.home() / ".pi" / "agent" / "sessions" / f"--{str(workflow).strip('/').replace('/', '-')}--"


def clip(text, size=SNIPPET):
    text = " ".join(str(text).split())
    return text if len(text) <= size else text[: size - 3] + "..."


def text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def digest_session(path: Path):
    calls, tools, args_seen, failures, users = {}, Counter(), Counter(), [], []
    cost = turns = 0
    started = model = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
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
        "started": started,
        "model": model,
        "assistant_turns": turns,
        "cost_usd": round(cost, 4),
        "tool_calls": dict(tools.most_common()),
        "user_messages": users[:LIMIT],
        "failed_or_blocked": failures[:LIMIT],
        "repeated_calls": [{"tool": n, "call": a, "times": c} for (n, a), c in args_seen.most_common() if c >= REPEATS][:LIMIT],
    }


def digest_run(run_id: str, run_dir: Path):
    state = load_yaml_safe(run_dir / "run.yaml", {})
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
    workflow: Annotated[Path, typer.Option(help="The workflow folder, workflows/<slug>.")],
    sessions_dir: Annotated[Path | None, typer.Option(help="Pi's session folder for the workflow; default from its path.")] = None,
    sessions: Annotated[int, typer.Option(help="How many of the newest sessions to read.")] = 5,
):
    workflow = workflow.resolve()
    if not (workflow / "plan.yaml").is_file():
        emit({"ok": False, "error": f"{workflow} is not a workflow"}, 1)
    folder = sessions_dir or default_sessions_dir(workflow)
    lessons = [x for x in load_yaml_safe(workflow / LESSONS, []) if isinstance(x, dict)]
    since = max((str(x.get("date", "")) for x in lessons), default="")
    files = sorted(folder.glob("*.jsonl"), reverse=True)[:sessions] if folder.is_dir() else []
    found = [d for d in (digest_session(f) for f in files) if str(d["started"] or "") > since]
    emit({
        "ok": True,
        "workflow": workflow.name,
        "sessions_dir": str(folder),
        "lessons": lessons,
        "sessions": found,
        "runs": [digest_run(rid, d) for rid, d in list_runs(workflow)],
    })


if __name__ == "__main__":
    typer.run(main)
