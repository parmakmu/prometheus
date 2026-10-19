"""The skill's scripts that prometheus uses as modules, imported here once, so the rest imports them like any module:
`from prometheus.skill import pi_records`.

The scripts are not a package: they run as plain files (`python3 run_steps.py`, in agents that have no prometheus) and
import each other by name from their own folder. So that folder goes on sys.path, and they are top-level modules named
`utils`, `pi_records`, ...: prometheus always uses its own copy (the newest), on any agent, with --agent. A process that
already imported another module by one of those names (an embedding program's own `utils`) is refused here, by name,
instead of getting the wrong one.

Also: find_files (prometheus's own files: the package, or the repo in a clone), scripts_dir, and run_script (a
script run as a command, with this Python, its JSON returned).
"""

import json
import subprocess
import sys
from pathlib import Path

from prometheus.errors import PrometheusError


def find_files() -> Path:
    """The folder holding prometheus's `.pi/` and `templates/`: the package itself when installed, the repo in a clone."""
    package = Path(__file__).resolve().parent
    for folder in (package, package.parent):
        if (folder / ".pi" / "skills" / "agent" / "SKILL.md").is_file():
            return folder
    raise SystemExit(f"prometheus's files are missing beside {package}: reinstall it")


def scripts_dir() -> Path:
    """The skill's scripts: prometheus's own copy (the newest), used on any agent with --agent."""
    return find_files() / ".pi" / "skills" / "agent" / "scripts"


def run_script(script: str, *args: str) -> dict:
    """Run a skill script with this Python (it has their packages) and return its JSON."""
    r = subprocess.run([sys.executable, str(scripts_dir() / script), *args], capture_output=True, text=True, check=False)
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise PrometheusError(f"{script} failed: {(r.stderr or r.stdout).strip()[-2000:]}") from None


_SCRIPTS = scripts_dir()
for _name, _module in list(sys.modules.items()):
    _file = getattr(_module, "__file__", None)
    if (
        (_SCRIPTS / f"{_name}.py").is_file()
        and _file
        and Path(_file).resolve().parent != _SCRIPTS
    ):
        raise ImportError(
            f"a module named {_name!r} is already imported (from {_file}), and prometheus's own {_name}.py "
            f"in {_SCRIPTS} must have that name: rename yours, or import prometheus first"
        )
sys.path.insert(
    0, str(_SCRIPTS)
)  # first while ours are imported, so no other folder's `utils` is taken for ours

import agent_params
import agent_release
import agent_repo
import agent_scaffold
import pi_records
import run_compile
import run_folders
import utils

# then last: the scripts' own later imports (run_steps, agent_state, ...) still find their folder, and an embedding
# program's own modules are not shadowed by it
sys.path.remove(str(_SCRIPTS))
sys.path.append(str(_SCRIPTS))

__all__ = [
    "agent_params",
    "agent_release",
    "agent_repo",
    "agent_scaffold",
    "find_files",
    "pi_records",
    "run_compile",
    "run_folders",
    "run_script",
    "scripts_dir",
    "utils",
]
