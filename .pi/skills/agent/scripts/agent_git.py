#!/usr/bin/env python3
"""Version an agent's plan with git: one repo per agent.

The repo tracks the plan with its gates, agents and the skill copy; the
agent's .gitignore leaves out runs/, .env (param values) and installs. Only
the scripts commit, never the model.

Branches. `main` holds the reviewed plan and changes only by a merge
the user chose (or by planning before the first run):
  run/<run id>        a run's changes (replans, gate scripts written for them);
                      created on the run's first commit, so a run that changes
                      nothing has no branch
  revise/<date>-<n>   a change to the plan asked for with /agent-revise
                      while no run is active (begin-change)
  improve/<date>-<n>  the same for /agent-improve (begin-change --kind improve)
When a run ends (done or stopped) or a change is ready, the user reviews the
branch against main and chooses: merge (only a ready, valid plan; squashed
into one commit on main, "r<revision>: <branch>" listing the branch's commits;
the branch is deleted; tags r<revision>), keep (switch back to main, keep the
branch) or discard.
A new run or change needs main checked out, so no branch is left unreviewed
by accident.

Nothing commits or switches branches while a wave is running: pi-subagents
compares the repo's staged index (git write-tree, from the child's cwd inside
the agent) before and after each writer child. The same check makes git a
requirement: without a repo, writer children fail.

Usage (from the agent root, or --agent DIR):
  agent_git.py begin-change     start or continue a change branch (no active run; improve needs a run first)
  agent_git.py review           the checked-out branch against main: commits, files
  agent_git.py merge --action merge|keep|discard
"""
import datetime
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from runs import active_run, list_runs
from utils import emit, load_yaml, read_yaml, refuse, report_state
from validate_plan import check_ready, validate

MAIN = "main"
CHANGE_KINDS = ("revise", "improve")  # a change to the plan between runs: /agent-revise, /agent-improve
FALLBACK_IDENTITY = ["-c", "user.name=prometheus", "-c", "user.email=prometheus@localhost"]
app = typer.Typer(add_completion=False)


def available():
    return shutil.which("git") is not None


def git(agent_dir, *args, check=True):
    r = subprocess.run(["git", *args], cwd=agent_dir, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r


def is_repo(agent_dir):
    return (Path(agent_dir) / ".git").is_dir()


def ensure_repo(agent_dir):
    """Create the agent's own repo (not the enclosing one, if any), on branch main."""
    if not is_repo(agent_dir):
        git(agent_dir, "init", "-q", "-b", MAIN)


def head(agent_dir):
    """The current commit, or None before the first commit or without git."""
    if not available() or not is_repo(agent_dir):
        return None
    r = git(agent_dir, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return r.stdout.strip() or None


def branch(agent_dir):
    """The checked-out branch (None without a repo, or with a detached HEAD). Read from .git/HEAD, as
    `git symbolic-ref --short HEAD` would answer: every result's state asks, and a git process each
    time was most of a state's cost."""
    if not is_repo(agent_dir):
        return None
    try:
        ref = (Path(agent_dir) / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return ref.removeprefix("ref: refs/heads/") if ref.startswith("ref: refs/heads/") else None


def identity(agent_dir):
    return [] if git(agent_dir, "config", "user.email", check=False).stdout.strip() else FALLBACK_IDENTITY


def plan_revision(agent_dir):
    plan = read_yaml(Path(agent_dir) / "plan.yaml", {})
    return (plan.get("revision", 1), plan.get("status")) if plan else (None, None)


def tag_ready(agent_dir):
    """On main with a ready plan: move the tag r<revision> to HEAD."""
    rev, status = plan_revision(agent_dir)
    if branch(agent_dir) == MAIN and status == "ready" and rev is not None:
        git(agent_dir, "tag", "-f", f"r{rev}")


def commit(agent_dir, message, to=None):
    """Commit every change to the plan, gates and agents; returns (commit or None, note).

    `to`: the branch the commit belongs on (a run's run/<id>). When main is checked out, that
    branch is created for it, so a run never changes main directly. Skipped (with a note) while a
    wave runs or without git."""
    if not available():
        return None, "git not found: the plan was not committed"
    if (run := active_run(agent_dir)) and run.wave_running:
        return head(agent_dir), "a wave is running: not committed now (the next compile commits it)"
    ensure_repo(agent_dir)
    if conflict := branch_conflict(agent_dir, to):
        raise RuntimeError(conflict)  # checked before staging: a refused commit leaves nothing staged on main
    git(agent_dir, "add", "-A")
    staged = git(agent_dir, "diff", "--cached", "--quiet", check=False).returncode != 0
    if staged or head(agent_dir) is None:
        if to and branch(agent_dir) == MAIN and head(agent_dir) is not None:
            git(agent_dir, "switch", "-q", "-c", to)
        git(agent_dir, *identity(agent_dir), "commit", "-q", "--allow-empty", "-m", message)
    tag_ready(agent_dir)
    return head(agent_dir), None


def review_info(agent_dir):
    """The checked-out branch against main, or None on main."""
    b = branch(agent_dir)
    if not b or b == MAIN:
        return None
    base = git(agent_dir, "merge-base", MAIN, "HEAD").stdout.strip()
    commits = git(agent_dir, "log", "--format=%h %s", f"{base}..HEAD").stdout.splitlines()
    files = git(agent_dir, "diff", "--stat", base, "HEAD").stdout.strip().splitlines()
    dirty = git(agent_dir, "status", "--porcelain").stdout.strip().splitlines()
    return {"branch": b, "commits": commits, "files": files, "uncommitted": dirty,
            "main_moved": git(agent_dir, "rev-parse", MAIN).stdout.strip() != base,
            "see": f"git diff {MAIN}...{b}"}


def branch_conflict(agent_dir, to):
    """Why a commit cannot create branch `to` from main, or None: an earlier run with the same id
    left that branch (runs/ is not in git, so deleting its folder frees the id but not the branch)."""
    if not (to and available() and is_repo(agent_dir) and branch(agent_dir) == MAIN and head(agent_dir) is not None):
        return None
    if to not in run_branches(agent_dir):
        return None
    return (f"branch {to} already exists from an earlier run with this id, so this run cannot commit to it. "
            "Stop this run (agent_stop_run), then ask the user to review or delete that branch")


def run_branches(agent_dir):
    """Every run/<id> branch, merged or not."""
    if not available() or not is_repo(agent_dir):
        return []
    return git(agent_dir, "branch", "--list", "run/*", "--format=%(refname:short)", check=False).stdout.split()


def is_change_branch(name):
    return bool(name) and name.split("/")[0] in CHANGE_KINDS and "/" in name


def is_review_branch(name):
    return bool(name) and (name.startswith("run/") or is_change_branch(name))


def unmerged_branches(agent_dir):
    """Run and change branches waiting for review: those with commits main lacks, and the checked-out one even
    without any (begin-change leaves the edits uncommitted, so a fresh revise/ branch still sits at main's tip)."""
    if not available() or not is_repo(agent_dir):
        return []
    r = git(agent_dir, "branch", "--no-merged", MAIN, "--format=%(refname:short)", check=False)
    waiting = [b for b in r.stdout.split() if is_review_branch(b)]
    current = branch(agent_dir)
    return waiting + [current] if is_review_branch(current) and current not in waiting else waiting


# --- commands -----------------------------------------------------------------

@app.callback()
def main(agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = "."):
    os.chdir(agent_dir)
    if not Path("plan.yaml").is_file():
        refuse(f"{Path(agent_dir).resolve()} is not an agent (no plan.yaml)", 2)
    if not available():
        refuse("git is required; ask the user to install it", 2)
    report_state(Path.cwd())


@app.command("begin-change")
def begin_change(kind: Annotated[str, typer.Option(help="revise (/agent-revise) | improve (/agent-improve)")] = "revise"):
    """Start (or continue) a branch for changing the plan: revise/<date>-<n> or improve/<date>-<n>."""
    if kind not in CHANGE_KINDS:
        refuse(f"kind must be one of {', '.join(CHANGE_KINDS)}")
    agent_dir = Path.cwd()
    if run := active_run(agent_dir):
        refuse(f"run {run.id} is active: tell the run what to change (it replans on its branch), or stop it first")
    if not list_runs(agent_dir):
        if kind == "improve":
            refuse("no run yet, so nothing to improve from: run the agent first (/agent-run), "
                   "or change the plan directly while planning (/agent-revise)")
        emit({"ok": True, "branch": branch(agent_dir), "mode": "planning", "note": "no run yet: planning edits the plan directly"})
    b = branch(agent_dir)
    if is_change_branch(b):
        open_kind = b.split("/")[0]
        if open_kind != kind:
            refuse(f"{b} is an open /agent-{open_kind} change: finish it first (review: merge, keep "
                   f"or discard), then start /agent-{kind}")
        emit({"ok": True, "branch": b, "mode": open_kind, "continued": True})
    if b != MAIN:
        refuse(f"branch {b} is checked out and not reviewed: review it first (agent_merge: merge, keep or discard)")
    today = datetime.datetime.now().astimezone().date().isoformat()  # local date
    taken = [int(m.group(1)) for x in git(agent_dir, "branch", "--list", f"{kind}/{today}-*", "--format=%(refname:short)").stdout.split()
             if (m := re.match(rf"^{kind}/{today}-(\d+)$", x))]
    new = f"{kind}/{today}-{max(taken, default=0) + 1}"
    git(agent_dir, "switch", "-q", "-c", new)  # uncommitted changes come along: main changes only by a merge
    # the plan is left as it is: an edit to it unreadies it (critique.plan_digest) until the critic is
    # recorded, which also raises the revision; a change to gates, scripts or lessons alone needs no critic
    sha, _ = commit(agent_dir, f"{kind} started: changes left uncommitted on main")
    emit({"ok": True, "branch": new, "mode": kind, "revision": plan_revision(agent_dir)[0], "commit": sha})


def main_plan(agent_dir):
    """plan.yaml as main has it, or None."""
    r = git(agent_dir, "show", f"{MAIN}:plan.yaml", check=False)
    return load_yaml(r.stdout) if r.returncode == 0 else None


def why_not_ready(agent_dir, plan):
    """check_ready, against main's plan when a branch is checked out (a change must be critiqued)."""
    return check_ready(plan, main_plan(agent_dir) if branch(agent_dir) not in (None, MAIN) else None)


@app.command()
def review():
    """Show the checked-out branch against main."""
    info = review_info(Path.cwd())
    emit({"ok": True, "review": info, "unmerged": unmerged_branches(Path.cwd())})


AT_REVIEW = "Uncommitted changes at review"


def leave_branch(agent_dir):
    """Commit what is uncommitted to the checked-out branch and switch to main; the branch is kept."""
    commit(agent_dir, AT_REVIEW)  # leftovers belong to the branch
    git(agent_dir, "switch", "-q", MAIN)


def merge_message(agent_dir, b, summary, steps):
    """`r<rev>: <branch>`, then the summary, the files changed and the branch's own steps (not the review's leftovers)."""
    stat = git(agent_dir, "diff", "--cached", "--stat").stdout.rstrip()
    parts = [f"r{plan_revision(agent_dir)[0]}: {b}", summary.strip(), stat, "\n".join(f"- {s}" for s in steps)]
    return "\n\n".join(p for p in parts if p)


@app.command()
def merge(action: Annotated[str, typer.Option(help="merge | keep | discard")],
          summary: Annotated[str, typer.Option(help="merge: what the branch changes, in words, for the commit message")] = ""):
    """Finish the review of the checked-out branch and switch back to main."""
    agent_dir = Path.cwd()
    if action not in ("merge", "keep", "discard"):
        refuse("action must be merge, keep or discard")
    b = branch(agent_dir)
    if b in (None, MAIN):
        refuse("main is checked out: nothing to review")
    if run := active_run(agent_dir):
        refuse(f"run {run.id} is still active: let it finish, or stop it (agent_stop_run), then review")
    if action == "merge":
        plan = read_yaml("plan.yaml", {})
        errors = validate(plan, None, None, agent_dir)[0]
        if errors or why_not_ready(agent_dir, plan):
            refuse(f"the plan on {b} is {plan.get('status')}" + (f" with errors {errors[:3]}" if errors else "")
                   + ": only a ready, valid plan can reach main. Finish the change (critic, agent_record_critique), or keep or discard the branch.")
    leave_branch(agent_dir)
    if action == "merge":
        # one commit on main per merged branch (a squash); the branch's commits were its working steps
        steps = [s for s in git(agent_dir, "log", "--reverse", "--format=%s", f"{MAIN}..{b}").stdout.splitlines() if s != AT_REVIEW]
        if git(agent_dir, "merge", "-q", "--squash", b, check=False).returncode != 0:
            git(agent_dir, "reset", "-q", "--merge", check=False)
            git(agent_dir, "switch", "-q", b)
            refuse(f"main has moved and {b} conflicts with it; the branch is checked out again. Resolve by hand, or discard it.")
        if git(agent_dir, "diff", "--cached", "--quiet", check=False).returncode != 0:
            git(agent_dir, *identity(agent_dir), "commit", "-q", "-m", merge_message(agent_dir, b, summary, steps))
        tag_ready(agent_dir)
    if action in ("merge", "discard"):
        git(agent_dir, "branch", "-q", "-D", b)  # -D: a squashed branch is not an ancestor of main
    rev, status = plan_revision(agent_dir)
    emit({"ok": True, "action": action, "branch": b, "main": head(agent_dir), "revision": rev, "status": status,
         "unmerged": unmerged_branches(agent_dir)})


if __name__ == "__main__":
    app()
