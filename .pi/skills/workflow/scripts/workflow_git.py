#!/usr/bin/env python3
"""Version a workflow's plan with git: one repo per workflow.

The repo tracks the plan with its gates, agents and the skill copy; the
workflow's .gitignore leaves out runs/, .env (param values) and installs. Only
the scripts commit, never the model.

Branches. `main` holds the reviewed plan and changes only by a merge
the user chose (or by planning before the first run):
  run/<run id>        a run's changes (replans, gate scripts written for them);
                      created on the run's first commit, so a run that changes
                      nothing has no branch
  revise/<date>-<n>   a change to the plan asked for with /workflow-revise
                      while no run is active (begin-change)
  improve/<date>-<n>  the same for /workflow-improve (begin-change --kind improve)
                      (older workflows may hold change/<date>-<n>: still reviewed)
When a run ends (done or stopped) or a change is ready, the user reviews the
branch against main and chooses: merge (only a ready, valid plan; squashed
into one commit on main, "r<revision>: <branch>" listing the branch's commits;
the branch is deleted; tags r<revision>), keep (switch back to main, keep the
branch) or discard.
A new run or change needs main checked out, so no branch is left unreviewed
by accident.

Nothing commits or switches branches while a wave is running: pi-subagents
compares the repo's staged index (git write-tree, from the child's cwd inside
the workflow) before and after each writer child. The same check makes git a
requirement: without a repo, writer children fail.

Usage (from the workflow root, or --workflow DIR):
  workflow_git.py begin-change     start or continue a change branch (no active run; improve needs a run first)
  workflow_git.py review           the checked-out branch against main: commits, files
  workflow_git.py merge --action merge|keep|discard
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
CHANGE_KINDS = ("revise", "improve")  # a change to the plan between runs: /workflow-revise, /workflow-improve
LEGACY_CHANGE = "change"  # the one prefix both commands used before; still reviewed
FALLBACK_IDENTITY = ["-c", "user.name=Prometheus", "-c", "user.email=prometheus@localhost"]
app = typer.Typer(add_completion=False)


def available():
    return shutil.which("git") is not None


def git(workflow, *args, check=True):
    r = subprocess.run(["git", *args], cwd=workflow, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r


def is_repo(workflow):
    return (Path(workflow) / ".git").is_dir()


def ensure_repo(workflow):
    """Create the workflow's own repo (not the enclosing one, if any), on branch main."""
    if not is_repo(workflow):
        git(workflow, "init", "-q", "-b", MAIN)


def head(workflow):
    """The current commit, or None before the first commit or without git."""
    if not available() or not is_repo(workflow):
        return None
    r = git(workflow, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return r.stdout.strip() or None


def branch(workflow):
    """The checked-out branch (None without a repo)."""
    if not available() or not is_repo(workflow):
        return None
    return git(workflow, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip() or None


def identity(workflow):
    return [] if git(workflow, "config", "user.email", check=False).stdout.strip() else FALLBACK_IDENTITY


def plan_revision(workflow):
    plan = read_yaml(Path(workflow) / "plan.yaml", {})
    return (plan.get("revision", 1), plan.get("status")) if plan else (None, None)


def tag_ready(workflow):
    """On main with a ready plan: move the tag r<revision> to HEAD."""
    rev, status = plan_revision(workflow)
    if branch(workflow) == MAIN and status == "ready" and rev is not None:
        git(workflow, "tag", "-f", f"r{rev}")


def commit(workflow, message, to=None):
    """Commit every change to the plan, gates and agents; returns (commit or None, note).

    `to`: the branch the commit belongs on (a run's run/<id>). When main is checked out, that
    branch is created for it, so a run never changes main directly. Skipped (with a note) while a
    wave runs or without git."""
    if not available():
        return None, "git not found: the plan was not committed"
    if (run := active_run(workflow)) and run.wave_running:
        return head(workflow), "a wave is running: not committed now (the next compile commits it)"
    ensure_repo(workflow)
    if conflict := branch_conflict(workflow, to):
        raise RuntimeError(conflict)  # checked before staging: a refused commit leaves nothing staged on main
    git(workflow, "add", "-A")
    staged = git(workflow, "diff", "--cached", "--quiet", check=False).returncode != 0
    if staged or head(workflow) is None:
        if to and branch(workflow) == MAIN and head(workflow) is not None:
            git(workflow, "switch", "-q", "-c", to)
        git(workflow, *identity(workflow), "commit", "-q", "--allow-empty", "-m", message)
    tag_ready(workflow)
    return head(workflow), None


def review_info(workflow):
    """The checked-out branch against main, or None on main."""
    b = branch(workflow)
    if not b or b == MAIN:
        return None
    base = git(workflow, "merge-base", MAIN, "HEAD").stdout.strip()
    commits = git(workflow, "log", "--format=%h %s", f"{base}..HEAD").stdout.splitlines()
    files = git(workflow, "diff", "--stat", base, "HEAD").stdout.strip().splitlines()
    dirty = git(workflow, "status", "--porcelain").stdout.strip().splitlines()
    return {"branch": b, "commits": commits, "files": files, "uncommitted": dirty,
            "main_moved": git(workflow, "rev-parse", MAIN).stdout.strip() != base,
            "see": f"git diff {MAIN}...{b}"}


def branch_conflict(workflow, to):
    """Why a commit cannot create branch `to` from main, or None: an earlier run with the same id
    left that branch (runs/ is not in git, so deleting its folder frees the id but not the branch)."""
    if not (to and available() and is_repo(workflow) and branch(workflow) == MAIN and head(workflow) is not None):
        return None
    if to not in run_branches(workflow):
        return None
    return (f"branch {to} already exists from an earlier run with this id, so this run cannot commit to it. "
            "Stop this run (workflow_stop_run), then ask the user to review or delete that branch")


def run_branches(workflow):
    """Every run/<id> branch, merged or not."""
    if not available() or not is_repo(workflow):
        return []
    return git(workflow, "branch", "--list", "run/*", "--format=%(refname:short)", check=False).stdout.split()


def is_change_branch(name):
    return bool(name) and name.split("/")[0] in (*CHANGE_KINDS, LEGACY_CHANGE) and "/" in name


def is_review_branch(name):
    return bool(name) and (name.startswith("run/") or is_change_branch(name))


def unmerged_branches(workflow):
    if not available() or not is_repo(workflow):
        return []
    r = git(workflow, "branch", "--no-merged", MAIN, "--format=%(refname:short)", check=False)
    return [b for b in r.stdout.split() if is_review_branch(b)]


# --- commands -----------------------------------------------------------------

@app.callback()
def main(workflow: Annotated[str, typer.Option(help="workflow root")] = "."):
    os.chdir(workflow)
    if not Path("plan.yaml").is_file():
        refuse(f"{Path(workflow).resolve()} is not a workflow (no plan.yaml)", 2)
    if not available():
        refuse("git is required; ask the user to install it", 2)
    report_state(Path.cwd())


@app.command("begin-change")
def begin_change(kind: Annotated[str, typer.Option(help="revise (/workflow-revise) | improve (/workflow-improve)")] = "revise"):
    """Start (or continue) a branch for changing the plan: revise/<date>-<n> or improve/<date>-<n>."""
    if kind not in CHANGE_KINDS:
        refuse(f"kind must be one of {', '.join(CHANGE_KINDS)}")
    workflow = Path.cwd()
    if run := active_run(workflow):
        refuse(f"run {run.id} is active: tell the run what to change (it replans on its branch), or stop it first")
    if not list_runs(workflow):
        if kind == "improve":
            refuse("no run yet, so nothing to improve from: run the workflow first (/workflow-run), "
                   "or change the plan directly while planning (/workflow-revise)")
        emit({"ok": True, "branch": branch(workflow), "mode": "planning", "note": "no run yet: planning edits the plan directly"})
    b = branch(workflow)
    if is_change_branch(b):
        open_kind = b.split("/")[0]
        if open_kind not in (kind, LEGACY_CHANGE):  # both commands used change/ once
            refuse(f"{b} is an open /workflow-{open_kind} change: finish it first (review: merge, keep "
                   f"or discard), then start /workflow-{kind}")
        emit({"ok": True, "branch": b, "mode": open_kind, "continued": True})
    if b != MAIN:
        refuse(f"branch {b} is checked out and not reviewed: review it first (workflow_merge: merge, keep or discard)")
    today = datetime.datetime.now().astimezone().date().isoformat()  # local date
    taken = [int(m.group(1)) for x in git(workflow, "branch", "--list", f"{kind}/{today}-*", "--format=%(refname:short)").stdout.split()
             if (m := re.match(rf"^{kind}/{today}-(\d+)$", x))]
    new = f"{kind}/{today}-{max(taken, default=0) + 1}"
    git(workflow, "switch", "-q", "-c", new)  # uncommitted changes come along: main changes only by a merge
    # the plan is left as it is: an edit to it unreadies it (critique.plan_digest) until the critic is
    # recorded, which also raises the revision; a change to gates, scripts or lessons alone needs no critic
    sha, _ = commit(workflow, f"{kind} started: changes left uncommitted on main")
    emit({"ok": True, "branch": new, "mode": kind, "revision": plan_revision(workflow)[0], "commit": sha})


def main_plan(workflow):
    """plan.yaml as main has it, or None."""
    r = git(workflow, "show", f"{MAIN}:plan.yaml", check=False)
    return load_yaml(r.stdout) if r.returncode == 0 else None


def why_not_ready(workflow, plan):
    """check_ready, against main's plan when a branch is checked out (a change must be critiqued)."""
    return check_ready(plan, main_plan(workflow) if branch(workflow) not in (None, MAIN) else None)


@app.command()
def review():
    """Show the checked-out branch against main."""
    info = review_info(Path.cwd())
    emit({"ok": True, "review": info, "unmerged": unmerged_branches(Path.cwd())})


AT_REVIEW = "Uncommitted changes at review"


def merge_message(workflow, b, summary, steps):
    """`r<rev>: <branch>`, then the summary, the files changed and the branch's own steps (not the review's leftovers)."""
    stat = git(workflow, "diff", "--cached", "--stat").stdout.rstrip()
    parts = [f"r{plan_revision(workflow)[0]}: {b}", summary.strip(), stat, "\n".join(f"- {s}" for s in steps)]
    return "\n\n".join(p for p in parts if p)


@app.command()
def merge(action: Annotated[str, typer.Option(help="merge | keep | discard")],
          summary: Annotated[str, typer.Option(help="merge: what the branch changes, in words, for the commit message")] = ""):
    """Finish the review of the checked-out branch and switch back to main."""
    workflow = Path.cwd()
    if action not in ("merge", "keep", "discard"):
        refuse("action must be merge, keep or discard")
    b = branch(workflow)
    if b in (None, MAIN):
        refuse("main is checked out: nothing to review")
    if run := active_run(workflow):
        refuse(f"run {run.id} is still active: let it finish, or stop it (workflow_stop_run), then review")
    if action == "merge":
        plan = read_yaml("plan.yaml", {})
        errors = validate(plan, None, None, workflow)[0]
        if errors or why_not_ready(workflow, plan):
            refuse(f"the plan on {b} is {plan.get('status')}" + (f" with errors {errors[:3]}" if errors else "")
                   + ": only a ready, valid plan can reach main. Finish the change (critic, workflow_record_critique), or keep or discard the branch.")
    commit(workflow, AT_REVIEW)  # leftovers belong to the branch
    git(workflow, "switch", "-q", MAIN)
    if action == "merge":
        # one commit on main per merged branch (a squash); the branch's commits were its working steps
        steps = [s for s in git(workflow, "log", "--reverse", "--format=%s", f"{MAIN}..{b}").stdout.splitlines() if s != AT_REVIEW]
        if git(workflow, "merge", "-q", "--squash", b, check=False).returncode != 0:
            git(workflow, "reset", "-q", "--merge", check=False)
            git(workflow, "switch", "-q", b)
            refuse(f"main has moved and {b} conflicts with it; the branch is checked out again. Resolve by hand, or discard it.")
        if git(workflow, "diff", "--cached", "--quiet", check=False).returncode != 0:
            git(workflow, *identity(workflow), "commit", "-q", "-m", merge_message(workflow, b, summary, steps))
        tag_ready(workflow)
    if action in ("merge", "discard"):
        git(workflow, "branch", "-q", "-D", b)  # -D: a squashed branch is not an ancestor of main
    rev, status = plan_revision(workflow)
    emit({"ok": True, "action": action, "branch": b, "main": head(workflow), "revision": rev, "status": status,
         "unmerged": unmerged_branches(workflow)})


if __name__ == "__main__":
    app()
