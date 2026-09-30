#!/usr/bin/env python3
"""Create or refresh a workflow (a workflow agent).

Usage:
  scaffold_workflow.py SLUG [--root workflows] [--request TEXT] [--refresh]

Creates <root>/<slug>/ from templates/workflow/ (everything in it, with
{{slug}} and {{request}} filled in) plus:
  runs/                          one folder per run: state, work files, outputs (see validate_plan.py)
  gates/                         gate scripts, written while planning
  scripts/                       stage programs (a stage's `script:`), written by its first run
  .env                           per-machine param values (workflow_params.py); not in git
  .pi/skills/workflow/           a copy of this skill (rules, validator, compiler, run protocol)

The template holds AGENTS.md, pyproject.toml (Python deps for `uv sync`),
.pi/APPEND_SYSTEM.md (the workflow agent's role), .pi/settings.json,
.pi/extensions/workflow/index.ts (commands and tools), .pi/agents/ (plan-critic,
workflow-batcher) and the plan skeleton.

--refresh rewrites the template files and the skill copy in an existing
workflow. It never touches plan.yaml, .env, runs/, gates/, scripts/ or agents
the plan added. Values an older workflow kept in .pi/params.yaml move to .env.

The workflow is its own git repo (workflow_git.py); both modes commit the result.
A refresh refuses while a wave of the workflow's active run is running.

Prints one JSON object (with `today`, the date for the plan's agents.checked).
"""
import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
import workflow_git
import workflow_params
from ruamel.yaml import YAML

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_DIR / "templates" / "workflow"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
PLAN = Path("plan.yaml")
STATE_FILES = {Path(workflow_params.ENV_FILE): workflow_params.ENV_HEADER}
OLD_PARAMS = Path(".pi") / "params.yaml"  # where values lived before .env
OLD_PLAN = Path(".pi") / "plan.yaml"
PYPROJECT = Path("pyproject.toml")  # the workflow adds its own packages: a refresh keeps them  # where the plan lived before it moved to the workflow root
ROLE = Path(".pi") / "APPEND_SYSTEM.md"
LESSONS_HEADING = "## Lessons for this workflow"  # the end of the role file that /workflow-improve writes: a refresh keeps it
# Files and folders earlier versions wrote that are no longer part of a workflow.
LEGACY_FILES = [Path(".pi") / "prompts" / name
                for name in ("start.md", "workflow-run.md", "workflow-status.md", "workflow-replan.md")]
LEGACY_DIRS = [Path(".pi") / "skills" / "request-to-plan"]  # the skill's old name


def template_files() -> list[Path]:
    """Return sorted template file paths relative to TEMPLATE_DIR."""
    return sorted(
        (path.relative_to(TEMPLATE_DIR) for path in TEMPLATE_DIR.rglob("*")
         if path.is_file() and path.suffix != ".pyc" and path.name != ".DS_Store"),
        key=lambda path: path.as_posix(),
    )


def add_skill_packages(path: Path, template: str):
    """Add the template's dependencies that the workflow's pyproject.toml lacks, keeping the
    packages the workflow added; the names added."""
    import tomllib
    text = path.read_text(encoding="utf-8")
    try:
        have = tomllib.loads(text)["project"]["dependencies"]
    except (KeyError, TypeError, ValueError):
        return []
    def name(dep):
        return re.split(r"[\s<>=!~;\[(]", dep, maxsplit=1)[0].lower().replace("_", "-")

    names = {name(d) for d in have}
    missing = [d for d in tomllib.loads(template)["project"]["dependencies"] if name(d) not in names]
    if missing:
        deps = "dependencies = [" + ", ".join(f'"{d}"' for d in [*have, *missing]) + "]"
        path.write_text(re.sub(r"(?ms)^dependencies\s*=\s*\[.*?\]", lambda _m: deps, text, count=1), encoding="utf-8")
    return missing


def lock_packages(workflow: Path):
    """Write uv.lock from pyproject.toml, so it is committed with the workflow: otherwise the first
    `uv run` of a gate or program writes it during a wave, and every run has a change to review.
    Returns what it wrote, or None (no uv, or nothing to change)."""
    if shutil.which("uv") is None:
        return None
    before = (workflow / "uv.lock").read_bytes() if (workflow / "uv.lock").exists() else None
    r = subprocess.run(["uv", "lock", "--quiet", "--project", str(workflow)], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return f"uv.lock (not updated: {(r.stderr or r.stdout).strip()[:200]})"
    after = (workflow / "uv.lock").read_bytes() if (workflow / "uv.lock").exists() else None
    return "uv.lock" if after != before else None


def move_old_params(workflow: Path):
    """Move values from .pi/params.yaml into .env (names already in .env win); the moved names, or None."""
    old = workflow / OLD_PARAMS
    if not old.exists():
        return None
    values = YAML(typ="safe").load(old.read_text(encoding="utf-8")) or {}
    plan = YAML(typ="safe").load((workflow / PLAN).read_text(encoding="utf-8")) or {}
    specs = plan.get("params") or {}
    env = workflow / workflow_params.ENV_FILE
    if not env.exists():
        env.write_text(workflow_params.ENV_HEADER, encoding="utf-8")
    present = workflow_params.file_keys(str(workflow))
    moved = []
    for name, value in values.items() if isinstance(values, dict) else []:
        key = workflow_params.env_name(name, specs.get(name))
        if value not in (None, "") and key not in present:
            workflow_params.set_key(str(env), key, str(value), quote_mode="auto")
            moved.append(f"{name} -> {key}")
    old.unlink()
    return moved


def main(
    slug: str,
    root: Annotated[str, typer.Option(help="folder that holds workflows")] = "workflows",
    request: Annotated[str, typer.Option(help="the user's request, verbatim")] = "",
    refresh: Annotated[bool, typer.Option("--refresh", help="update template files and the skill copy")] = False,
):
    """Create or refresh a workflow."""

    if not SLUG_RE.match(slug):
        print(json.dumps({"ok": False, "error": f"slug must match {SLUG_RE.pattern}"}))
        raise typer.Exit(2)
    workflow = Path(root) / slug
    moved_plan = refresh and (workflow / OLD_PLAN).exists() and not (workflow / PLAN).exists()
    exists = (workflow / PLAN).exists() or moved_plan
    if exists and not refresh:
        print(json.dumps({"ok": False, "error": f"{workflow} already has a plan; use --refresh to update it or pick another slug"}))
        raise typer.Exit(1)
    if refresh and not exists:
        print(json.dumps({"ok": False, "error": f"{workflow} has no plan.yaml to refresh"}))
        raise typer.Exit(1)
    if refresh and workflow_git.wave_running(workflow.resolve()):
        # the refresh rewrites the skill copy, including the batch tool a running map stage uses
        print(json.dumps({"ok": False, "error": f"{workflow} has a wave running; refresh it once the wave has finished"}))
        raise typer.Exit(1)

    written, kept, removed = [], [], []
    if moved_plan:
        (workflow / OLD_PLAN).rename(workflow / PLAN)  # the refresh commit records the move
        removed.append(f"{OLD_PLAN} (now {PLAN})")
    for rel in LEGACY_FILES:
        path = workflow / rel
        if path.exists():
            path.unlink()
            removed.append(str(rel))
    for rel in LEGACY_DIRS:
        path = workflow / rel
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(str(rel) + "/")
    for directory in ("runs", "gates", "scripts"):
        (workflow / directory).mkdir(parents=True, exist_ok=True)
    for directory in ("gates", "scripts"):  # git drops empty folders: checking out main after a run would delete them
        (workflow / directory / ".gitkeep").touch()

    for rel in template_files():
        dst = workflow / rel
        if rel == PLAN and exists:
            kept.append(str(rel))
            continue
        if rel == PYPROJECT and dst.exists():
            added = add_skill_packages(dst, (TEMPLATE_DIR / rel).read_text(encoding="utf-8"))
            (written if added else kept).append(str(rel) + (f" (added {', '.join(added)})" if added else ""))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = (TEMPLATE_DIR / rel).read_text(encoding="utf-8")
        text = text.replace("{{slug}}", slug).replace("{{request}}", json.dumps(request))
        if rel == ROLE and dst.exists():
            old = dst.read_text(encoding="utf-8")
            if LESSONS_HEADING in old:
                text = text.rstrip("\n") + "\n\n" + old[old.index(LESSONS_HEADING):]
        dst.write_text(text, encoding="utf-8")
        written.append(str(rel))

    skill_dst = workflow / ".pi" / "skills" / "workflow"
    if skill_dst.resolve() != SKILL_DIR.resolve():  # not when refreshing from the workflow's own copy
        if skill_dst.exists():
            shutil.rmtree(skill_dst)
        shutil.copytree(SKILL_DIR, skill_dst, ignore=IGNORE)
        written.append(str(Path(".pi") / "skills" / "workflow") + "/")

    moved = move_old_params(workflow)
    if moved is not None:
        removed.append(str(OLD_PARAMS))
        written.append(f"{workflow_params.ENV_FILE} (values from {OLD_PARAMS}: {', '.join(moved) or 'none set'})")

    for rel, content in STATE_FILES.items():
        path = workflow / rel
        if path.exists():
            kept.append(str(rel))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(str(rel))

    workflow_abs = workflow.resolve()
    locked = lock_packages(workflow_abs)
    if locked:
        written.append(locked)
    note = None
    try:
        commit, note = workflow_git.commit(str(workflow_abs), "Refresh from Prometheus" if refresh else f"Create workflow {slug}")
    except RuntimeError as exc:
        commit, note = None, str(exc)
    print(json.dumps({
        "ok": True,
        "workflow": str(workflow_abs),
        "commit": commit,
        "git_note": note,
        "mode": "refresh" if refresh else "create",
        "written": written,
        "kept": kept,
        "removed": removed,
        "plan": str(workflow_abs / PLAN),
        "today": datetime.datetime.now().astimezone().date().isoformat(),  # local date
    }, indent=2))


if __name__ == "__main__":
    typer.run(main)
