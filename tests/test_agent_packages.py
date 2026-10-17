"""agent_packages.py: packages are added only as requirements, with a reason, and where a change can be reviewed."""
import json
import subprocess
import sys

import pytest
from conftest import SKILL, git_in


def run(agent_dir, *args):
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "agent_packages.py"), *args, "--agent", str(agent_dir)], capture_output=True, text=True, check=False)
    return json.loads(r.stdout)


@pytest.fixture
def agent_dir(tmp_path):
    (tmp_path / "plan.yaml").write_text("format: 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "w"\nversion = "0"\nrequires-python = ">=3.14"\ndependencies = []\n', encoding="utf-8")
    git = git_in(tmp_path)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return tmp_path


@pytest.mark.parametrize("bad", ["pandas; rm -rf /", "git+https://x/y", "a b", "../x", "x @ https://example.test/x.whl"])
def test_only_requirements_are_accepted(agent_dir, bad):
    r = run(agent_dir, bad, "--reason", "because")
    assert r["ok"] is False and "not a package requirement" in r["error"]


def test_options_are_not_requirements():
    # typer refuses them as options before the check; the check refuses them too
    sys.path.insert(0, str(SKILL / "scripts"))
    from agent_packages import is_requirement
    assert not any(is_requirement(x) for x in ("-e .", "--index-url=https://example.test", "./pkg", ""))


@pytest.mark.parametrize("good", ["openpyxl", "Pandas[excel]>=2,<3", "httpx[http2]~=0.27", "tzdata; sys_platform == 'win32'"])
def test_a_pep_508_requirement_is_accepted(agent_dir, good):
    # accepted: it gets as far as the reason
    assert "why the agent needs" in run(agent_dir, good)["error"]


def test_a_reason_is_required(agent_dir):
    assert "why the agent needs" in run(agent_dir, "openpyxl")["error"]
    assert "why the agent needs" in run(agent_dir, "openpyxl", "--reason", "  ")["error"]


def test_main_with_a_run_behind_it_refuses(agent_dir):
    (agent_dir / "runs" / "2026-01-01-1").mkdir(parents=True)
    (agent_dir / "runs" / "2026-01-01-1" / "run.yaml").write_text("id: 2026-01-01-1\nstatus: done\n", encoding="utf-8")
    r = run(agent_dir, "openpyxl", "--reason", "write the workbook")
    assert r["ok"] is False and "main is checked out and no run is active" in r["error"]


def test_a_checked_out_change_branch_waits_for_review_before_its_first_commit(agent_dir):
    # web-app edits leave a fresh revise/ branch at main's tip, its changes uncommitted: it still needs a review
    sys.path.insert(0, str(SKILL / "scripts"))
    import agent_repo
    git_in(agent_dir)("switch", "-q", "-c", "revise/2026-10-04-1")
    assert agent_repo.unmerged_branches(agent_dir) == ["revise/2026-10-04-1"]
    git_in(agent_dir)("switch", "-q", "main")
    assert agent_repo.unmerged_branches(agent_dir) == []  # kept with nothing of its own: nothing to review
