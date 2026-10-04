"""add_package.py: packages are added only as requirements, with a reason, and where a change can be reviewed."""
import json
import subprocess
import sys

import pytest
from conftest import SKILL, git_in


def run(workflow, *args):
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "add_package.py"), *args, "--workflow", str(workflow)], capture_output=True, text=True, check=False)
    return json.loads(r.stdout)


@pytest.fixture
def workflow(tmp_path):
    (tmp_path / "plan.yaml").write_text("version: 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "w"\nversion = "0"\nrequires-python = ">=3.14"\ndependencies = []\n', encoding="utf-8")
    git = git_in(tmp_path)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return tmp_path


@pytest.mark.parametrize("bad", ["pandas; rm -rf /", "git+https://x/y", "a b", "../x"])
def test_only_requirements_are_accepted(workflow, bad):
    r = run(workflow, bad, "--reason", "because")
    assert r["ok"] is False and "not a package requirement" in r["error"]


def test_a_reason_is_required(workflow):
    assert "why the workflow needs" in run(workflow, "openpyxl")["error"]
    assert "why the workflow needs" in run(workflow, "openpyxl", "--reason", "  ")["error"]


def test_main_with_a_run_behind_it_refuses(workflow):
    (workflow / "runs" / "2026-01-01-1").mkdir(parents=True)
    (workflow / "runs" / "2026-01-01-1" / "run.yaml").write_text("id: 2026-01-01-1\nstatus: done\n", encoding="utf-8")
    r = run(workflow, "openpyxl", "--reason", "write the workbook")
    assert r["ok"] is False and "main is checked out and no run is active" in r["error"]


def test_a_checked_out_change_branch_waits_for_review_before_its_first_commit(workflow):
    # web-app edits leave a fresh revise/ branch at main's tip, its changes uncommitted: it still needs a review
    sys.path.insert(0, str(SKILL / "scripts"))
    import workflow_git
    git_in(workflow)("switch", "-q", "-c", "revise/2026-10-04-1")
    assert workflow_git.unmerged_branches(workflow) == ["revise/2026-10-04-1"]
    git_in(workflow)("switch", "-q", "main")
    assert workflow_git.unmerged_branches(workflow) == []  # kept with nothing of its own: nothing to review
