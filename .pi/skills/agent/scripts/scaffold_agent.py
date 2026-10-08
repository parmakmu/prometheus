#!/usr/bin/env python3
"""Create or refresh an agent (an agent).

Usage:
  scaffold_agent.py SLUG [--root agents] [--request TEXT] [--refresh]

Creates <root>/<slug>/ from templates/agent/ (at the repo root, outside the skill and .pi) (everything in it, with
{{slug}} and {{request}} filled in) plus:
  runs/                          one folder per run: state, work files, outputs (see validate_plan.py)
  gates/                         gate scripts, written while planning
  scripts/                       stage programs (a stage's `script:`), written by its first run
  uploads/                       files the user attaches in the web app's chat; not in git
  .env                           per-machine param values (agent_params.py); not in git
  .pi/skills/agent/           a copy of this skill (rules, validator, compiler, run protocol)

The template holds README.md, pyproject.toml (Python deps for `uv sync`),
.pi/APPEND_SYSTEM.md (the agent's role), .pi/settings.json,
.pi/extensions/agent/index.ts (commands and tools), .pi/agents/ (plan-critic,
batcher) and the plan skeleton.

--refresh rewrites the template files and the skill copy in an existing
agent, and moves an older plan's planning sections (unknowns, questions,
critique, agents) from plan.yaml to .plan/. Otherwise it never touches plan.yaml, .env, runs/, uploads/, gates/, scripts/ or agents
the plan added.

The agent is its own git repo (agent_git.py); both modes commit the result.
--if-stale (with --refresh) skips an agent that is up to date (prometheus_version.up_to_date: the same
hash of the skill and templates); the version is written to .pi/skills/agent/VERSION and goes in the commit message.
A refresh refuses while the agent has an active run (it rewrites the skill copy, including the
batch tool a running map stage uses, and its commit would land on the run branch) or while a run
or change branch is checked out for review.

Prints one JSON object (with `today`, the date for the plan's agents.checked).
"""
import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import agent_git
import agent_params
import prometheus_version
import typer
from runs import active_run
from utils import parts_in_plan_file, read_plan, refuse, write_plan

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROMETHEUS_DIR = SKILL_DIR.parents[2]  # .pi/skills/agent -> the repo root; the templates are not part of the skill
TEMPLATE_DIR = PROMETHEUS_DIR / "templates" / "agent"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# the `prometheus` command's own words: `prometheus <slug>` opens an agent, so no agent may be named like one of them
RESERVED = {"init", "doctor", "help", "list", "install", "share", "upgrade", "refresh", "pull", "run", "release", "ui", "check",
            "propose", "catalog", "serve", "prune"}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
PLAN = Path("plan.yaml")
STATE_FILES = {Path(agent_params.ENV_FILE): agent_params.ENV_HEADER}
PYPROJECT = Path("pyproject.toml")  # the agent adds its own packages: a refresh keeps them
ROLE = Path(".pi") / "APPEND_SYSTEM.md"
LESSONS_HEADING = "## Lessons for this agent"  # the end of the role file that /agent-improve writes: a refresh keeps it


def template_files() -> list[Path]:
    """Return sorted template file paths relative to TEMPLATE_DIR."""
    return sorted(
        (path.relative_to(TEMPLATE_DIR) for path in TEMPLATE_DIR.rglob("*")
         if path.is_file() and path.suffix != ".pyc" and path.name != ".DS_Store"),
        key=lambda path: path.as_posix(),
    )


def version_key(text: str):
    """'3.14' -> (3, 14), for comparing Python versions."""
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def add_skill_packages(path: Path, template: str):
    """Bring the agent's pyproject.toml up to the template: its dependencies the agent lacks
    (keeping the packages the agent added), and its requires-python when the agent's `>=`
    floor is lower (a higher one is the agent's own); what changed."""
    import tomllib
    text = path.read_text(encoding="utf-8")
    try:
        have = tomllib.loads(text)["project"]["dependencies"]
    except (KeyError, TypeError, ValueError):
        return []
    def name(dep):
        return re.split(r"[\s<>=!~;\[(]", dep, maxsplit=1)[0].lower().replace("_", "-")

    names = {name(d) for d in have}
    wanted = tomllib.loads(template)["project"]
    missing = [d for d in wanted["dependencies"] if name(d) not in names]
    changes = list(missing)
    if missing:
        deps = "dependencies = [" + ", ".join(f'"{d}"' for d in [*have, *missing]) + "]"
        text = re.sub(r"(?ms)^dependencies\s*=\s*\[.*?\]", lambda _m: deps, text, count=1)
    # raise a lower `>=` floor to the template's; keep a higher one, or any other specifier the agent chose
    have_floor = re.search(r'(?m)^requires-python\s*=\s*"\s*>=\s*([\d.]+)\s*"\s*$', text)
    if have_floor and version_key(have_floor.group(1)) < version_key(wanted["requires-python"].lstrip(">= ")):
        floor = f'requires-python = "{wanted["requires-python"]}"'
        text = text[:have_floor.start()] + floor + text[have_floor.end():]
        changes.append(floor)
    if changes:
        path.write_text(text, encoding="utf-8")
    return changes


def lock_packages(agent_dir: Path):
    """Write uv.lock from pyproject.toml, so it is committed with the agent: otherwise the first
    `uv run` of a gate or program writes it during a wave, and every run has a change to review.
    Returns what it wrote, or None (no uv, or nothing to change)."""
    if shutil.which("uv") is None:
        return None
    before = (agent_dir / "uv.lock").read_bytes() if (agent_dir / "uv.lock").exists() else None
    r = subprocess.run(["uv", "lock", "--quiet", "--project", str(agent_dir)], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return f"uv.lock (not updated: {(r.stderr or r.stdout).strip()[:200]})"
    after = (agent_dir / "uv.lock").read_bytes() if (agent_dir / "uv.lock").exists() else None
    return "uv.lock" if after != before else None


def rehome_sessions(agent_dir: Path):
    """Conversations started before the folder moved (workflows/ to agents/, say) record the old folder as their cwd,
    and Pi lists a session folder's conversations only for the cwd they record: the web app and `pi --resume` showed
    none. Each conversation's first line gets the folder it is in now. The number rewritten."""
    here, n = str(agent_dir.resolve()), 0
    for path in sorted((agent_dir / ".pi" / "sessions").glob("*.jsonl")):
        first, _, rest = path.read_text(encoding="utf-8").partition("\n")
        try:
            header = json.loads(first)
        except ValueError:
            continue
        if header.get("type") == "session" and header.get("cwd") not in (None, here):
            header["cwd"] = here
            path.write_text(json.dumps(header, separators=(",", ":")) + "\n" + rest, encoding="utf-8")
            n += 1
    return n


# before agents were called agents (2026-10-04): a refresh removes these
OBSOLETE = (".pi/extensions/workflow", ".pi/skills/workflow")


def refresh_refusal(agent_dir: Path):
    """Why a refresh must wait, or None: its commit would land on a run or change branch."""
    if run := active_run(agent_dir):
        return f"has an active run ({run.id}); refresh it once the run has finished and its branch is reviewed"
    if (b := agent_git.branch(agent_dir)) and b != agent_git.MAIN:
        return f"has branch {b} checked out; refresh it after merging, keeping or discarding that branch"
    return None


def copy_from(source: Path, agent_dir: Path, slug: str, request: str):
    """Start `agent_dir` from another agent: its plan as a draft for `request` (revision 1, no critique),
    its gates, stage programs, subagents and packages. Runs, .env and sessions stay with the source."""
    plan = read_plan(source, None)
    if not isinstance(plan, dict):
        refuse(f"{source} has no readable plan.yaml to start from", 1)
    plan["status"], plan["revision"], plan["request"] = "draft", 1, request or plan.get("request")
    for key in ("critique", "questions"):
        plan.pop(key, None)
    write_plan(agent_dir, plan)
    copied = [str(PLAN) + f" (from {source.name}, draft)"]
    for rel in ("gates", "scripts", ".pi/agents"):
        if (source / rel).is_dir():
            shutil.copytree(source / rel, agent_dir / rel, dirs_exist_ok=True, ignore=IGNORE)
            copied.append(f"{rel}/")
    if (source / PYPROJECT).is_file():
        text = re.sub(r'(?m)^name\s*=\s*".*"$', f'name = "{slug}"', (source / PYPROJECT).read_text(encoding="utf-8"), count=1)
        (agent_dir / PYPROJECT).write_text(text, encoding="utf-8")
        copied.append(str(PYPROJECT))
    return copied


def main(
    slug: str,
    root: Annotated[str, typer.Option(help="folder that holds agents")] = "agents",
    request: Annotated[str, typer.Option(help="the user's request, verbatim")] = "",
    refresh: Annotated[bool, typer.Option("--refresh", help="update template files and the skill copy")] = False,
    if_stale: Annotated[bool, typer.Option("--if-stale", help="with --refresh: do nothing when the agent already has this prometheus version")] = False,
    lock: Annotated[bool, typer.Option(help="write uv.lock (uv resolves the agent's packages, which may build them: --no-lock for an agent not yet reviewed)")] = True,
    from_agent: Annotated[str | None, typer.Option("--from", help="start from this agent (a slug under --root, or a folder): its plan as a draft, its gates, programs, subagents and packages")] = None,
):
    """Create or refresh an agent."""

    if not SLUG_RE.match(slug):
        refuse(f"slug must match {SLUG_RE.pattern}", 2)
    if slug in RESERVED and not refresh:
        refuse(f"{slug!r} is a prometheus command (`prometheus {slug}`), so it can't name an agent; pick another slug", 2)
    if not TEMPLATE_DIR.is_dir():  # an agent's copy of the skill has no templates: they are Prometheus's
        refuse(f"no agent template at {TEMPLATE_DIR}: create and refresh agents from prometheus", 2)
    agent_dir = Path(root) / slug
    exists = (agent_dir / PLAN).exists()
    if exists and not refresh:
        refuse(f"{agent_dir} already has a plan; use --refresh to update it or pick another slug", 1)
    source = None
    if from_agent:
        source = (Path(root) / from_agent if SLUG_RE.match(from_agent) else Path(from_agent)).resolve()
        if refresh or not (source / PLAN).is_file():
            refuse(f"--from needs an agent to copy, for a new agent ({source} has no plan.yaml)" if not refresh else "--from is only for a new agent", 1)
    if refresh and not exists:
        refuse(f"{agent_dir} has no plan.yaml to refresh", 1)
    if refresh and (why := refresh_refusal(agent_dir.resolve())):
        refuse(f"{agent_dir} {why}", 1)
    version = prometheus_version.current()
    was = prometheus_version.installed(agent_dir) if refresh else None
    if refresh and if_stale and was and prometheus_version.up_to_date(was, version):
        print(json.dumps({"ok": True, "agent": str(agent_dir.resolve()), "mode": "refresh", "prometheus_version": version,
                          "skipped": f"already at prometheus {version}"}))
        raise typer.Exit(0)

    written, kept = [], []
    if refresh and (rehomed := rehome_sessions(agent_dir)):
        written.append(f".pi/sessions ({rehomed} conversation{'s' if rehomed > 1 else ''} moved to this folder)")
    for directory in ("runs", "gates", "scripts", "uploads"):
        (agent_dir / directory).mkdir(parents=True, exist_ok=True)
    for directory in ("gates", "scripts"):  # git drops empty folders: checking out main after a run would delete them
        (agent_dir / directory / ".gitkeep").touch()

    for rel in template_files():
        dst = agent_dir / rel
        if rel == PLAN and exists:
            kept.append(str(rel))
            continue
        if rel == PYPROJECT and dst.exists():
            added = add_skill_packages(dst, (TEMPLATE_DIR / rel).read_text(encoding="utf-8"))
            (written if added else kept).append(str(rel) + (f" (updated {', '.join(added)})" if added else ""))
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

    # an older agent's plan.yaml holds how it was planned too: that moves to .plan/ (the same plan, read as one)
    if refresh and (moved := parts_in_plan_file(agent_dir)):
        write_plan(agent_dir, read_plan(agent_dir, {}))
        written.append(f"{PLAN} ({', '.join(moved)} moved to .plan/)")

    # what an older prometheus put in an agent under names it no longer uses: Pi would still load it
    removed = [rel for rel in OBSOLETE if (agent_dir / rel).exists()]
    for rel in removed:
        shutil.rmtree(agent_dir / rel)

    skill_dst = agent_dir / ".pi" / "skills" / "agent"  # never this script's own copy: that has no templates
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
    shutil.copytree(SKILL_DIR, skill_dst, ignore=IGNORE)
    if version:
        (skill_dst / prometheus_version.VERSION_FILE).write_text(version + "\n", encoding="utf-8")
    written.append(str(Path(".pi") / "skills" / "agent") + "/")

    for rel, content in STATE_FILES.items():
        path = agent_dir / rel
        if path.exists():
            kept.append(str(rel))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(str(rel))

    if source:
        written += copy_from(source, agent_dir, slug, request)
    agent_abs = agent_dir.resolve()
    locked = lock_packages(agent_abs) if lock else None
    if locked:
        written.append(locked)
    note = None
    try:
        label = f" {version}" if version else ""
        moved_from = f" (was {was})" if was and was != version else ""
        commit, note = agent_git.commit(str(agent_abs), f"Refresh from prometheus{label}{moved_from}" if refresh else f"Create agent {slug} with prometheus{label}")
    except RuntimeError as exc:
        commit, note = None, str(exc)
    print(json.dumps({
        "ok": True,
        "agent": str(agent_abs),
        "commit": commit,
        "git_note": note,
        "mode": "refresh" if refresh else "create",
        "prometheus_version": version,
        "was": was,
        "written": written,
        "kept": kept,
        **({"removed": removed} if removed else {}),
        "plan": str(agent_abs / PLAN),
        "today": datetime.datetime.now().astimezone().date().isoformat(),  # local date
    }, indent=2))


if __name__ == "__main__":
    typer.run(main)
