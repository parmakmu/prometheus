#!/usr/bin/env python3
"""Copy an agent to another folder so it can run there.

Usage: export_agent.py AGENT DEST_FOLDER [--keep-state] [--name SLUG]

Creates DEST_FOLDER/<slug>/ (or DEST_FOLDER/<SLUG>).

Fresh export (default): a clean start. Copies the plan (as
refined by any merged resolve), agents, gates, prompts, settings and skill; leaves
out the runs (runs/), the git history (.git; the copy starts its own repo
with one commit), pi-subagents runtime state (.pi/subagents/) and the conversations
(.pi/sessions/).

Both modes leave out .venv (run `uv sync` there), .pi/npm (a project package
install, if any; pi-subagents is expected to be installed globally) and .env:
param values are per machine and may hold secrets. The copy gets a .env that
lists each param's variable, unset.

--keep-state: an exact copy (minus caches and .env) that continues where the
agent stands: runs/, .git, .pi/subagents/ and .pi/sessions/.

Validates the copy and prints one JSON object.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import agent_git
import agent_params
import typer
from utils import read_yaml as load
from utils import refuse

SCRIPT_DIR = Path(__file__).resolve().parent
CACHE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def main(
    agent_dir: str,
    dest: str,
    keep_state: Annotated[bool, typer.Option("--keep-state", help="copy runs/, the git history and pi-subagents state too")] = False,
    name: Annotated[str | None, typer.Option(help="slug for the copy (default: the agent's folder name)")] = None,
):
    """Copy an agent elsewhere."""
    src = Path(agent_dir).resolve()
    plan_path = src / "plan.yaml"
    if not plan_path.is_file():
        refuse(f"{src} is not an agent (no plan.yaml)", 1)
    slug = name or src.name
    dst = (Path(dest) / slug).resolve()
    if dst.exists():
        refuse(f"{dst} already exists", 1)
    if dst.is_relative_to(src):
        refuse("destination is inside the agent", 1)

    plan = load(plan_path, {})

    def base_ignore(dirpath, names):
        skip = set(CACHE(dirpath, names))
        rel = str(Path(dirpath).relative_to(src))
        if rel == ".":
            skip |= {".venv", agent_params.ENV_FILE} & set(names)  # per machine; `uv sync` recreates .venv
        if rel == ".pi":
            skip |= {"npm"} & set(names)      # Pi's project package install; reinstalled on the new machine
        return skip

    if keep_state:
        shutil.copytree(src, dst, ignore=base_ignore)
        cleared = []
    else:
        def ignore(dirpath, names):
            skip = base_ignore(dirpath, names)
            rel = str(Path(dirpath).relative_to(src))
            if rel == "runs":
                skip |= set(names)            # keep the folder, drop the runs
            if rel == ".":
                skip |= {".git"} & set(names)  # a fresh repo below
            if rel == ".pi":
                skip |= {"subagents", "history", "sessions"} & set(names)
            return skip
        shutil.copytree(src, dst, ignore=ignore)
        cleared = ["runs/", ".git history", ".pi/subagents/", ".pi/sessions/"]
        source = agent_git.head(src)
        try:
            agent_git.commit(dst, f"Exported from {slug}" + (f" at {source[:12]}" if source else ""))
        except RuntimeError as exc:
            cleared.append(f"(not committed: {exc})")

    specs = plan.get("params") if isinstance(plan, dict) and isinstance(plan.get("params"), dict) else {}
    (dst / agent_params.ENV_FILE).write_text(
        agent_params.ENV_HEADER + "".join(f"{agent_params.env_name(n, s)}=\n" for n, s in specs.items()), encoding="utf-8")
    cleared.append(".env values")

    check = subprocess.run(
        [sys.executable, str(dst / ".pi" / "skills" / "agent" / "scripts" / "validate_plan.py"),
         str(dst / "plan.yaml")],
        capture_output=True, text=True, check=False,
    )
    try:
        report = json.loads(check.stdout)
    except ValueError:
        report = {"valid": False, "errors": [check.stderr.strip() or "validator produced no output"]}

    print(json.dumps({
        "ok": True,
        "mode": "keep-state" if keep_state else "fresh",
        "agent": str(dst),
        "cleared": cleared,
        "valid": report.get("valid"),
        "errors": report.get("errors", []),
        "missing_params": ((report.get("summary") or {}).get("agent") or {}).get("missing_params", []),
        "missing_secrets": ((report.get("summary") or {}).get("agent") or {}).get("missing_secrets", []),
        "start": f"cd {dst} && pi   then /agent-run",
    }, indent=2))


if __name__ == "__main__":
    typer.run(main)
