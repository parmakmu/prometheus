"""An agent's runs: each execution of its plan is a folder runs/<id>/ (id: <date>-<n>).

  run.yaml    id, status (active | done | stopped), revision, started_at, finished_at, note,
              stages {key: running | done} (absent = pending), launches [one entry per wave
              launch], critiques [{run_id, after_waves}] (critic results a replan recorded;
              plan_edit.py caps them per replan). Written by run_log.py and plan_edit.py.
  work/       the children's cwd: stage outputs, decisions, batches, scripts
  .compiled/  compiled waves and their results

Relative param values (an `outputs` default, say) resolve against runs/<id>/. At most one run is
active; /agent-run continues it.
"""
import re
from pathlib import Path
from typing import NamedTuple

from utils import read_yaml, write_yaml

RUNS = "runs"
RUN_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d+)$")
HEADER = "# Run state written by the run protocol (run_log.py). Do not edit by hand.\n"


class Run(NamedTuple):
    id: str
    dir: Path
    state: dict

    @property
    def wave_running(self):
        return "running" in (self.state.get("stages") or {}).values()

    @property
    def branch(self):
        """The branch the run's commits go to."""
        return f"run/{self.id}"

    def save(self):
        write_yaml(self.dir / "run.yaml", self.state, HEADER)


def list_runs(agent_dir):
    """[(id, dir)] of the agent's runs, oldest first."""
    base = Path(agent_dir) / RUNS
    names = [p.name for p in base.iterdir() if (p / "run.yaml").is_file()] if base.is_dir() else []
    key = lambda n: (m.group(1), int(m.group(2))) if (m := RUN_ID_RE.match(n)) else (n, 0)
    return [(n, base / n) for n in sorted(names, key=key)]


def read_run(run_dir):
    state = read_yaml(Path(run_dir) / "run.yaml", {})
    return state if isinstance(state, dict) else {}


def active_run(agent_dir):
    """The run /agent-run continues, or None."""
    for rid, d in reversed(list_runs(agent_dir)):
        if (state := read_run(d)).get("status") == "active":
            return Run(rid, d, state)
    return None


def last_run(agent_dir):
    """The latest run in brief (so the parent need not look in runs/), or None."""
    runs = list_runs(agent_dir)
    if not runs:
        return None
    rid, d = runs[-1]
    state = read_run(d)
    last = (state.get("launches") or [{}])[-1]
    return {"id": rid, "status": state.get("status"), "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"), "note": state.get("note"), "runs": len(runs),
            "last_wave": {"wave": last.get("wave"), "status": last.get("status")} if last else None}
