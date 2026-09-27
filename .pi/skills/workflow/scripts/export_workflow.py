#!/usr/bin/env python3
"""Copy a workflow to another folder so it can run there.

Usage: export_workflow.py WORKFLOW DEST_FOLDER [--keep-state] [--name SLUG]

Creates DEST_FOLDER/<slug>/ (or DEST_FOLDER/<SLUG>).

Fresh export (default): a clean start. Copies the plan (as
refined by any merged replan), agents, gates, prompts, settings and skill; leaves
out the runs (runs/), the git history (.git; the copy starts its own repo
with one commit) and pi-subagents runtime state (.pi/subagents/).

Both modes leave out .venv (run `uv sync` there), .pi/npm (a project package
install, if any; pi-subagents is expected to be installed globally) and .env:
param values are per machine and may hold secrets. The copy gets a .env that
lists each param's variable, unset.

--keep-state: an exact copy (minus caches and .env) that continues where the
workflow stands: runs/, .git and .pi/subagents/.

Validates the copy and prints one JSON object.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
import workflow_git
import workflow_params
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

SCRIPT_DIR = Path(__file__).resolve().parent
CACHE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def load(path, default):
    try:
        data = YAML(typ="safe").load(Path(path).read_text(encoding="utf-8"))
        return default if data is None else data
    except (OSError, YAMLError):
        return default


def main(
    workflow: str,
    dest: str,
    keep_state: Annotated[bool, typer.Option("--keep-state", help="copy runs/, the git history and pi-subagents state too")] = False,
    name: Annotated[str | None, typer.Option(help="slug for the copy (default: the workflow's folder name)")] = None,
):
    """Copy a workflow elsewhere."""
    src = Path(workflow).resolve()
    plan_path = src / ".pi" / "plan.yaml"
    if not plan_path.is_file():
        print(json.dumps({"ok": False, "error": f"{src} is not a workflow (no .pi/plan.yaml)"}))
        raise typer.Exit(1)
    slug = name or src.name
    dst = (Path(dest) / slug).resolve()
    if dst.exists():
        print(json.dumps({"ok": False, "error": f"{dst} already exists"}))
        raise typer.Exit(1)
    if dst.is_relative_to(src):
        print(json.dumps({"ok": False, "error": "destination is inside the workflow"}))
        raise typer.Exit(1)

    plan = load(plan_path, {})

    def base_ignore(dirpath, names):
        skip = set(CACHE(dirpath, names))
        rel = str(Path(dirpath).relative_to(src))
        if rel == ".":
            skip |= {".venv", workflow_params.ENV_FILE} & set(names)  # per machine; `uv sync` recreates .venv
        if rel == ".pi":
            skip |= {"params.yaml"} & set(names)  # values before .env
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
                skip |= {"subagents", "history"} & set(names)
            return skip
        shutil.copytree(src, dst, ignore=ignore)
        cleared = ["runs/", ".git history", ".pi/subagents/"]
        source = workflow_git.head(src)
        try:
            workflow_git.commit(dst, f"Exported from {slug}" + (f" at {source[:12]}" if source else ""))
        except RuntimeError as exc:
            cleared.append(f"(not committed: {exc})")

    specs = plan.get("params") if isinstance(plan, dict) and isinstance(plan.get("params"), dict) else {}
    (dst / workflow_params.ENV_FILE).write_text(
        workflow_params.ENV_HEADER + "".join(f"{workflow_params.env_name(n, s)}=\n" for n, s in specs.items()), encoding="utf-8")
    cleared.append(".env values")

    check = subprocess.run(
        [sys.executable, str(dst / ".pi" / "skills" / "workflow" / "scripts" / "validate_plan.py"),
         str(dst / ".pi" / "plan.yaml")],
        capture_output=True, text=True, check=False,
    )
    try:
        report = json.loads(check.stdout)
    except ValueError:
        report = {"valid": False, "errors": [check.stderr.strip() or "validator produced no output"]}

    print(json.dumps({
        "ok": True,
        "mode": "keep-state" if keep_state else "fresh",
        "workflow": str(dst),
        "cleared": cleared,
        "valid": report.get("valid"),
        "errors": report.get("errors", []),
        "missing_params": ((report.get("summary") or {}).get("workflow") or {}).get("missing_params", []),
        "missing_secrets": ((report.get("summary") or {}).get("workflow") or {}).get("missing_secrets", []),
        "start": f"cd {dst} && pi   then /workflow-run",
    }, indent=2))


if __name__ == "__main__":
    typer.run(main)
