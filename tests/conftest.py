"""Shared fixtures for the pytest suite.

ROOT is the Prometheus repo root, SKILL its copy of the agent skill.
`run_py`/`run_node` run a script and parse its stdout as JSON (a skill script runs in this process, as python would
run it, unless `--subprocess`). Without `--all`, the end-to-end files (SLOW) are left out unless named. A nonzero exit
is not itself a failure: scripts report refusals as {"ok": false, ...} on exit
1 or 2, and tests check that. `git_in(path)` returns a callable that runs
`git -C path ...` and returns the completed process.
"""
import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".pi" / "skills" / "agent"
HERE = Path(__file__).resolve().parent


def copy_example(name, agent_dir):
    """An example plan (examples/<name>/: plan.yaml and .plan/) into an agent's folder."""
    import shutil
    src = SKILL / "examples" / name
    shutil.copy(src / "plan.yaml", Path(agent_dir) / "plan.yaml")
    shutil.copytree(src / ".plan", Path(agent_dir) / ".plan", dirs_exist_ok=True)


def stamp(plan_path):
    """Tie a ready plan's critique to the plan as it now is, as agent_record_critique does: tests that edit a ready
    plan (and plans written for a test) are not the critic's subject."""
    sys.path.insert(0, str(SKILL / "scripts"))
    from plan_validate import plan_digest
    from utils import read_plan, write_plan
    agent = Path(plan_path).parent
    plan = read_plan(agent, {})
    plan.setdefault("critique", {"agent": "plan-critic", "run_id": "test-critic", "revision": plan.get("revision", 1),
                                 "passes": 1, "verdict": "pass", "findings": []})
    plan["critique"]["plan_digest"] = plan_digest(plan)
    write_plan(agent, plan)


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


def is_skill_script(path):
    """A script of the agent skill: prometheus's own, or an agent's copy (`.../skills/agent/scripts/x.py`)."""
    return path.suffix == ".py" and path.parent.name == "scripts" and path.parent.parent.name == "agent"


def _run_in_process(script, args, cwd=None, env=None, input=None) -> Result:
    """`python3 script args…` in this process: its argv, cwd, environment, stdin and output, and the exit code it
    would have. A new python costs ~0.15 s, most of it importing the libraries, which stay imported here; the
    scripts' own modules are imported afresh for each call (several agents' copies, and module state such as
    utils' report_state, must not cross calls) and what was imported before is put back."""
    def ours(module):
        return is_skill_script(Path(getattr(module, "__file__", None) or "x"))

    before = {name: m for name, m in sys.modules.items() if ours(m)}
    for name in before:
        del sys.modules[name]
    saved = os.getcwd(), sys.argv, list(sys.path), sys.stdin, dict(os.environ), tempfile.tempdir
    out, err, code = io.StringIO(), io.StringIO(), 0
    try:
        os.chdir(cwd or saved[0])
        sys.argv, sys.path[:0], sys.stdin = [str(script), *args], [str(script.parent)], io.StringIO(input or "")
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
        tempfile.tempdir = None  # read from TMPDIR again, as a new python would
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                runpy.run_path(str(script), run_name="__main__")
            except SystemExit as exit_:
                code = exit_.code if isinstance(exit_.code, int) else (0 if exit_.code is None else 1)
                if isinstance(exit_.code, str):
                    print(exit_.code, file=sys.stderr)
            except Exception:  # noqa: BLE001 - a crash is the script's result, as with a process
                traceback.print_exc()
                code = 1
    finally:
        os.chdir(saved[0])
        sys.argv, sys.path[:], sys.stdin = saved[1], saved[2], saved[3]
        os.environ.clear()
        os.environ.update(saved[4])
        tempfile.tempdir = saved[5]
        for name in [n for n, m in sys.modules.items() if ours(m)]:
            del sys.modules[name]
        sys.modules.update(before)
    return Result(SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue()))


# end to end: an agent, a run, a release or a server through every script (10-50 s each); `--all` runs them, and so
# does naming one on the command line
SLOW = {"test_run_finish.py", "test_agent.py", "test_run_release.py", "test_share.py", "test_router.py",
        "test_serve.py", "test_a2a_stage.py", "test_a2a.py"}


def pytest_addoption(parser):
    parser.addoption("--subprocess", action="store_true", help="run skill scripts as processes, not in-process")
    parser.addoption("--all", action="store_true", help="also run the end-to-end tests (run this before a commit)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--all"):
        return
    named = {Path(a.split("::")[0]).name for a in config.invocation_params.args if not a.startswith("-")}
    slow = [i for i in items if i.path.name in SLOW and i.path.name not in named]
    if slow:
        config.hook.pytest_deselected(items=slow)
        items[:] = [i for i in items if i not in slow]


@pytest.fixture(scope="session", autouse=True)
def private_prometheus(tmp_path_factory):
    """Releases and the cache in the test session's own folders, never the user's: a test that forgets to set them
    still can't install or unpack a release into ~/.local/share."""
    root = tmp_path_factory.mktemp("prometheus")
    saved = {k: os.environ.get(k) for k in ("PROMETHEUS_RELEASES", "PROMETHEUS_CACHE")}
    os.environ["PROMETHEUS_RELEASES"] = str(root / "releases")
    os.environ["PROMETHEUS_CACHE"] = str(root / "cache")
    yield root
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session", autouse=True)
def private_pi(tmp_path_factory):
    """Scripts read pi-subagents' run records and Pi's agent folder (missions, sessions): point both
    at a folder of the test session's own, never the user's."""
    root = tmp_path_factory.mktemp("pi")
    saved = {k: os.environ.get(k) for k in ("PI_SUBAGENTS_TEMP_ROOT", "PI_CODING_AGENT_DIR")}
    os.environ["PI_SUBAGENTS_TEMP_ROOT"] = str(root / "subagents")
    os.environ["PI_CODING_AGENT_DIR"] = str(root / "agent")
    yield root
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def fake_critic(run_id, agent="plan-critic", state="completed", root=None):
    """A pi-subagents record of a critic agent whose child `run_id` ran (what
    agent_record_critique checks), in $PI_SUBAGENTS_TEMP_ROOT unless `root` is given."""
    folder = Path(root or os.environ["PI_SUBAGENTS_TEMP_ROOT"]) / "async-subagent-runs" / f"critic-of-{run_id}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(json.dumps({"runId": f"critic-of-{run_id}", "state": "complete", "workflowChildren": {
        "children": [{"childId": "plan-critic", "state": state, "runId": run_id, "agent": agent}]}}), encoding="utf-8")


@pytest.fixture(scope="session")
def run_py(request):
    """run_py([...script args...], cwd=None, env=None) -> Result: as `python3 …`, the way pi-subagents' command
    runner and the extensions invoke scripts; a skill script in this process unless `--subprocess`."""
    in_process = not request.config.getoption("--subprocess")

    def _call(args, cwd=None, env=None, input=None):
        args = [str(a) for a in args]
        if in_process and args and is_skill_script(Path(args[0])):
            return _run_in_process(Path(args[0]), args[1:], cwd=cwd, env=env, input=input)
        return _run(["python3", *args], cwd=cwd, env=env, input=input)
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
