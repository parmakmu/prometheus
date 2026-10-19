"""The skill's scripts that prometheus uses as modules, imported here once, so the rest imports them like any module:
`from prometheus.skill import pi_records`.

The scripts are not a package: they run as plain files (`python3 run_steps.py`, in agents that have no prometheus) and
import each other by name from their own folder. So that folder goes on sys.path, and they are top-level modules named
`utils`, `pi_records`, ...: prometheus always uses its own copy (the newest), on any agent, with --agent. They are
loaded under those names and the names handed back, so an embedding program's own `utils`, imported before or after
prometheus, stays its own.

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
# The scripts import each other by plain name (`import utils`), so they are loaded under those names, then the names
# are handed back: a program's own `utils`, imported before or after prometheus, stays its own. Modules already
# imported from this folder (the tests') are shared.
_NAMES = {p.stem for p in _SCRIPTS.glob("*.py")}
_ours = {n for n, m in sys.modules.items() if n in _NAMES and Path(getattr(m, "__file__", None) or "/").resolve().parent == _SCRIPTS}
_theirs = {n: sys.modules.pop(n) for n in _NAMES & sys.modules.keys() - _ours}
sys.path.insert(0, str(_SCRIPTS))
try:
    import agent_params
    import agent_release
    import agent_repo
    import agent_scaffold
    import pi_records
    import run_compile
    import run_folders
    import utils
finally:
    sys.path.remove(str(_SCRIPTS))
    for _name in _NAMES - _ours:
        sys.modules.pop(_name, None)
    sys.modules.update(_theirs)

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
