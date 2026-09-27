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
  change/<date>-<n>   a change to the plan asked for with /workflow-revise
                      while no run is active (begin-change)
When a run ends (done or stopped) or a change is ready, the user reviews the
branch against main and chooses: merge (only a ready, valid plan; a
fast-forward when main has not moved; tags r<revision>), keep (switch back to
main, keep the branch) or discard.
A new run or change needs main checked out, so no branch is left unreviewed
by accident.

Nothing commits or switches branches while a wave is running: pi-subagents
compares the repo's staged index (git write-tree, from the child's cwd inside
the workflow) before and after each writer child. The same check makes git a
requirement: without a repo, writer children fail.

Usage (from the workflow root, or --workflow DIR):
  workflow_git.py begin-change     start or continue a change branch (no active run)
  workflow_git.py review           the checked-out branch against main: commits, files
  workflow_git.py merge --action merge|keep|discard
"""
import datetime
import io
import json
import os
import re
import shutil
import subprocess
from typing import Annotated

import typer

MAIN = "main"
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
    return os.path.isdir(os.path.join(workflow, ".git"))


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


def active_run(workflow):
    from validate_plan import active_run as find
    return find(workflow)


def wave_running(workflow):
    """True while a stage of the active run is running (see validate_plan, runs)."""
    act = active_run(workflow)
    return bool(act and "running" in (act[2].get("stages") or {}).values())


def identity(workflow):
    return [] if git(workflow, "config", "user.email", check=False).stdout.strip() else FALLBACK_IDENTITY


def plan_revision(workflow):
    from validate_plan import load_yaml
    try:
        with open(os.path.join(workflow, ".pi", "plan.yaml"), encoding="utf-8") as fh:
            plan = load_yaml(fh) or {}
    except OSError:
        return None, None
    return plan.get("revision", 1), plan.get("status")


def tag_ready(workflow):
    """On main with a ready plan: move the tag r<revision> to HEAD."""
    rev, status = plan_revision(workflow)
    if branch(workflow) == MAIN and status == "ready" and rev is not None:
        git(workflow, "tag", "-f", f"r{rev}")


def commit(workflow, message):
    """Commit every change to the plan, gates and agents; returns (commit or None, note).

    Skipped (with a note) while a wave runs or without git. While a run is
    active and main is checked out, the commit goes to a new branch run/<id>:
    a run never changes main directly."""
    if not available():
        return None, "git not found: the plan was not committed"
    if wave_running(workflow):
        return head(workflow), "a wave is running: not committed now (the next compile commits it)"
    ensure_repo(workflow)
    git(workflow, "add", "-A")
    staged = git(workflow, "diff", "--cached", "--quiet", check=False).returncode != 0
    if staged or head(workflow) is None:
        act = active_run(workflow)
        if act and branch(workflow) == MAIN and head(workflow) is not None:
            git(workflow, "switch", "-q", "-c", f"run/{act[0]}")
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


def unmerged_branches(workflow):
    if not available() or not is_repo(workflow):
        return []
    r = git(workflow, "branch", "--no-merged", MAIN, "--format=%(refname:short)", check=False)
    return [b for b in r.stdout.split() if b.startswith(("run/", "change/"))]


# --- commands -----------------------------------------------------------------

def out(obj, code=0):
    print(json.dumps(obj, indent=2, default=str))
    raise typer.Exit(code)


@app.callback()
def main(workflow: Annotated[str, typer.Option(help="workflow root")] = "."):
    os.chdir(workflow)
    if not os.path.isfile(os.path.join(".pi", "plan.yaml")):
        out({"ok": False, "error": f"{os.path.abspath(workflow)} is not a workflow (no .pi/plan.yaml)"}, 2)
    if not available():
        out({"ok": False, "error": "git is required; ask the user to install it"}, 2)


@app.command("begin-change")
def begin_change():
    """Start (or continue) a branch for changing the plan with /workflow-revise."""
    from validate_plan import list_runs, rt_yaml
    workflow = os.getcwd()
    act = active_run(workflow)
    if act:
        out({"ok": False, "error": f"run {act[0]} is active: tell the run what to change (it replans on its branch), or stop it first"}, 1)
    if not list_runs(workflow):
        out({"ok": True, "branch": branch(workflow), "mode": "planning", "note": "no run yet: planning edits the plan directly"})
    b = branch(workflow)
    if b and b.startswith("change/"):
        out({"ok": True, "branch": b, "mode": "change", "continued": True})
    if b != MAIN:
        out({"ok": False, "error": f"branch {b} is checked out and not reviewed: review it first (workflow_merge: merge, keep or discard)"}, 1)
    commit(workflow, "Uncommitted changes before a change")
    today = datetime.datetime.now().astimezone().date().isoformat()  # local date
    taken = [int(m.group(1)) for x in git(workflow, "branch", "--list", f"change/{today}-*", "--format=%(refname:short)").stdout.split()
             if (m := re.match(rf"^change/{today}-(\d+)$", x))]
    new = f"change/{today}-{max(taken, default=0) + 1}"
    git(workflow, "switch", "-q", "-c", new)
    # a new revision, drafted; the critic's recorded result marks it ready again
    y = rt_yaml()
    path = os.path.join(".pi", "plan.yaml")
    with open(path, encoding="utf-8") as fh:
        plan = y.load(fh)
    plan["revision"] = plan.get("revision", 1) + 1
    plan["status"] = "draft"
    plan.pop("critique", None)
    buf = io.StringIO()
    y.dump(plan, buf)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    sha, _ = commit(workflow, f"r{plan['revision']} draft: change started")
    out({"ok": True, "branch": new, "mode": "change", "revision": plan["revision"], "commit": sha})


@app.command()
def review():
    """Show the checked-out branch against main."""
    info = review_info(os.getcwd())
    out({"ok": True, "review": info, "unmerged": unmerged_branches(os.getcwd())})


@app.command()
def merge(action: Annotated[str, typer.Option(help="merge | keep | discard")]):
    """Finish the review of the checked-out branch and switch back to main."""
    workflow = os.getcwd()
    if action not in ("merge", "keep", "discard"):
        out({"ok": False, "error": "action must be merge, keep or discard"}, 1)
    act = active_run(workflow)
    b = branch(workflow)
    if b in (None, MAIN):
        out({"ok": False, "error": "main is checked out: nothing to review"}, 1)
    if act:
        out({"ok": False, "error": f"run {act[0]} is still active: let it finish, or stop it (workflow_stop_run), then review"}, 1)
    if action == "merge":
        from validate_plan import load_yaml, validate
        with open(os.path.join(".pi", "plan.yaml"), encoding="utf-8") as fh:
            plan = load_yaml(fh) or {}
        errors = validate(plan, None, None, workflow)[0]
        if errors or plan.get("status") != "ready":
            out({"ok": False, "error": f"the plan on {b} is {plan.get('status')}" + (f" with errors {errors[:3]}" if errors else "")
                 + ": only a ready, valid plan can reach main. Finish the change (critic, workflow_record_critique), or keep or discard the branch."}, 1)
    commit(workflow, "Uncommitted changes at review")  # leftovers belong to the branch
    git(workflow, "switch", "-q", MAIN)
    note = None
    if action == "merge":
        if git(workflow, "merge", "-q", "--ff-only", b, check=False).returncode != 0:
            r = git(workflow, *identity(workflow), "merge", "-q", "--no-edit", b, check=False)
            if r.returncode != 0:
                git(workflow, "merge", "--abort", check=False)
                git(workflow, "switch", "-q", b)
                out({"ok": False, "error": f"main has moved and {b} conflicts with it; the branch is checked out again. Resolve by hand, or discard it."}, 1)
            note = "main had moved: merged with a merge commit"
        git(workflow, "branch", "-q", "-d", b)
        tag_ready(workflow)
    elif action == "discard":
        git(workflow, "branch", "-q", "-D", b)
    rev, status = plan_revision(workflow)
    out({"ok": True, "action": action, "branch": b, "main": head(workflow), "revision": rev, "status": status,
         "note": note, "unmerged": unmerged_branches(workflow)})


if __name__ == "__main__":
    app()
