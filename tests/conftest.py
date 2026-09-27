"""Shared fixtures for the pytest suite.

ROOT is the Prometheus repo root, SKILL its copy of the workflow skill.
`run_py`/`run_node` run a script and parse its stdout as JSON. A nonzero exit
is not itself a failure: scripts report refusals as {"ok": false, ...} on exit
1 or 2, and tests check that. `git_in(path)` returns a callable that runs
`git -C path ...` and returns the completed process.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".pi" / "skills" / "workflow"
HERE = Path(__file__).resolve().parent


class Result:
    """A finished process: .data is its parsed stdout JSON (None if not JSON)."""

    def __init__(self, proc: subprocess.CompletedProcess):
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        try:
            self.data = json.loads(proc.stdout)
        except ValueError:
            self.data = None

    def __repr__(self):
        return f"Result(returncode={self.returncode}, stdout={self.stdout!r}, stderr={self.stderr!r})"


def _run(args, cwd=None, env=None, input=None) -> Result:
    proc = subprocess.run(args, cwd=cwd, env=env, input=input, capture_output=True, text=True, check=False)
    return Result(proc)


@pytest.fixture(scope="session")
def run_py():
    """run_py([...script args...], cwd=None, env=None) -> Result. Uses `python3`,
    matching how pi-subagents' command-runner and the extensions invoke scripts."""
    def _call(args, cwd=None, env=None):
        return _run(["python3", *[str(a) for a in args]], cwd=cwd, env=env)
    return _call


@pytest.fixture(scope="session")
def run_node():
    def _call(args, cwd=None, env=None):
        return _run(["node", *[str(a) for a in args]], cwd=cwd, env=env)
    return _call


def git_in(path):
    """A callable running `git -C path *args`, returning the CompletedProcess (text output)."""
    def _git(*args):
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=False)
    return _git


@pytest.fixture(scope="session")
def python_exe():
    return sys.executable
