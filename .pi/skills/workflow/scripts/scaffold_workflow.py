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
.pi/APPEND_SYSTEM.md (the workflow agent's role), .pi/settings.json, .pi/extensions/workflow/index.ts (commands and
tools), .pi/agents/ (plan-critic, workflow-batcher) and the plan skeleton.

--refresh rewrites the template files and the skill copy in an existing
workflow. It never touches .pi/plan.yaml, .env, runs/, gates/, scripts/ or agents
the plan added. Values an older workflow kept in .pi/params.yaml move to .env.

The workflow is its own git repo (workflow_git.py); both modes commit the result
(a refresh not while a wave is running).

Prints one JSON object (with `today`, the date for the plan's agents.checked).
"""
import datetime
import json
import os
import re
import shutil
from typing import Annotated

import typer
import workflow_git
import workflow_params
from ruamel.yaml import YAML

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(SKILL_DIR, "templates", "workflow")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
PLAN = os.path.join(".pi", "plan.yaml")
STATE_FILES = {workflow_params.ENV_FILE: workflow_params.ENV_HEADER}
OLD_PARAMS = os.path.join(".pi", "params.yaml")  # where values lived before .env
# Files and folders earlier versions wrote that are no longer part of a workflow.
LEGACY_FILES = [os.path.join(".pi", "prompts", n)
                for n in ("start.md", "workflow-run.md", "workflow-status.md", "workflow-replan.md")]
LEGACY_DIRS = [os.path.join(".pi", "skills", "request-to-plan")]  # the skill's old name


def template_files():
    for dirpath, _, files in os.walk(TEMPLATE_DIR):
        for f in sorted(files):
            if f.endswith((".pyc", ".DS_Store")):
                continue
            yield os.path.relpath(os.path.join(dirpath, f), TEMPLATE_DIR)


def move_old_params(workflow):
    """Move values from .pi/params.yaml into .env (names already in .env win); the moved names, or None."""
    old = os.path.join(workflow, OLD_PARAMS)
    if not os.path.exists(old):
        return None
    with open(old, encoding="utf-8") as fh:
        values = YAML(typ="safe").load(fh) or {}
    with open(os.path.join(workflow, PLAN), encoding="utf-8") as fh:
        specs = (YAML(typ="safe").load(fh) or {}).get("params") or {}
    env = os.path.join(workflow, workflow_params.ENV_FILE)
    if not os.path.exists(env):
        with open(env, "w", encoding="utf-8") as fh:
            fh.write(workflow_params.ENV_HEADER)
    present = workflow_params.file_keys(workflow)
    moved = []
    for name, value in values.items() if isinstance(values, dict) else []:
        key = workflow_params.env_name(name, specs.get(name))
        if value not in (None, "") and key not in present:
            workflow_params.set_key(env, key, str(value), quote_mode="auto")
            moved.append(f"{name} -> {key}")
    os.remove(old)
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
    workflow = os.path.join(root, slug)
    exists = os.path.exists(os.path.join(workflow, PLAN))
    if exists and not refresh:
        print(json.dumps({"ok": False, "error": f"{workflow} already has a plan; use --refresh to update it or pick another slug"}))
        raise typer.Exit(1)
    if refresh and not exists:
        print(json.dumps({"ok": False, "error": f"{workflow} has no .pi/plan.yaml to refresh"}))
        raise typer.Exit(1)

    written, kept, removed = [], [], []
    for rel in LEGACY_FILES:
        path = os.path.join(workflow, rel)
        if os.path.exists(path):
            os.remove(path)
            removed.append(rel)
    for rel in LEGACY_DIRS:
        path = os.path.join(workflow, rel)
        if os.path.isdir(path):
            shutil.rmtree(path)
            removed.append(rel + os.sep)
    for d in ("runs", "gates", "scripts"):
        os.makedirs(os.path.join(workflow, d), exist_ok=True)

    for rel in template_files():
        dst = os.path.join(workflow, rel)
        if rel == PLAN and exists:
            kept.append(rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(os.path.join(TEMPLATE_DIR, rel), encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("{{slug}}", slug).replace("{{request}}", json.dumps(request))
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(rel)

    skill_dst = os.path.join(workflow, ".pi", "skills", "workflow")
    if os.path.abspath(skill_dst) != SKILL_DIR:  # not when refreshing from the workflow's own copy
        if os.path.exists(skill_dst):
            shutil.rmtree(skill_dst)
        shutil.copytree(SKILL_DIR, skill_dst, ignore=IGNORE)
        written.append(os.path.join(".pi", "skills", "workflow") + os.sep)

    moved = move_old_params(workflow)
    if moved is not None:
        removed.append(OLD_PARAMS)
        written.append(f"{workflow_params.ENV_FILE} (values from {OLD_PARAMS}: {', '.join(moved) or 'none set'})")

    for rel, content in STATE_FILES.items():
        path = os.path.join(workflow, rel)
        if os.path.exists(path):
            kept.append(rel)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(rel)

    note = None
    try:
        commit, note = workflow_git.commit(os.path.abspath(workflow), "Refresh from Prometheus" if refresh else f"Create workflow {slug}")
    except RuntimeError as exc:
        commit, note = None, str(exc)
    print(json.dumps({
        "ok": True,
        "workflow": os.path.abspath(workflow),
        "commit": commit,
        "git_note": note,
        "mode": "refresh" if refresh else "create",
        "written": written,
        "kept": kept,
        "removed": removed,
        "plan": os.path.join(os.path.abspath(workflow), PLAN),
        "today": datetime.datetime.now().astimezone().date().isoformat(),  # local date
    }, indent=2))


if __name__ == "__main__":
    typer.run(main)
