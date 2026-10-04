#!/usr/bin/env python3
"""Add Python packages to an agent's pyproject.toml (`uv add`), after the user approved them.

Usage: add_package.py PACKAGE... --reason TEXT [--agent DIR]

Only the extensions' `agent_add_package` tool runs this, and only after it showed the user the
packages and the reason and got a yes; bash running it, or `uv add`, is blocked. A package is a
requirement as `uv add` takes it (`openpyxl`, `pandas>=2`, `httpx[http2]`), nothing else. Like gates,
scripts and agents, packages change only where a change can be reviewed: not on `main` once a run
exists (a run's or a /agent-revise branch). Prints the packages now listed in pyproject.toml.
"""
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Annotated

import agent_git
import typer
from runs import active_run, list_runs
from utils import emit, refuse, report_state

REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?([<>=!~]=?[A-Za-z0-9.*+!_-]+(,[<>=!~]=?[A-Za-z0-9.*+!_-]+)*)?$")


def listed(pyproject: Path):
    """The dependencies pyproject.toml lists."""
    return (tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}).get("dependencies") or []


def main(
    packages: Annotated[list[str], typer.Argument(help="requirements to add, as `uv add` takes them")],
    reason: Annotated[str, typer.Option(help="why the agent needs them, in a sentence the user can judge")] = "",
    agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = ".",
):
    """Add packages to the agent."""
    root = Path(agent_dir).resolve()
    if not (root / "plan.yaml").is_file() or not (root / "pyproject.toml").is_file():
        refuse(f"{root} is not an agent (no plan.yaml or pyproject.toml)", 2)
    report_state(root)
    if bad := [p for p in packages if not REQUIREMENT.match(p)]:
        refuse(f"not a package requirement: {', '.join(bad)}")
    if not reason.strip():
        refuse("say why the agent needs the package: the user is asked to approve it with the reason")
    if not shutil.which("uv"):
        refuse("uv is not installed: ask the user to install it (https://docs.astral.sh/uv/)", 2)
    if run := active_run(root):
        if run.wave_running:
            refuse(f"a wave of run {run.id} is running: add the package between waves")
    elif list_runs(root) and agent_git.branch(root) in (None, agent_git.MAIN):
        waiting = agent_git.unmerged_branches(root)
        refuse("main is checked out and no run is active: packages change only on a run or change branch, reviewed before they reach main."
               + (f" {', '.join(waiting)} waiting for review." if waiting else " Ask the user to start a change with /agent-revise."))
    r = subprocess.run(["uv", "add", "--project", str(root), *packages], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        refuse(f"uv add failed: {(r.stderr or r.stdout).strip()[-1500:]}")
    emit({"ok": True, "added": packages, "reason": reason.strip(), "dependencies": listed(root / "pyproject.toml")})


if __name__ == "__main__":
    typer.run(main)
