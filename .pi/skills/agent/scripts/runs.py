"""An agent's runs: each execution of its plan is a folder runs/<id>/ (id: <date>-<n>).

  run.yaml    id, status (active | done | stopped), revision, started_at, finished_at, note,
              stages {key: running | done} (absent = pending), launches [one entry per wave
              launch], critiques [{run_id, after_waves}] (critic results recorded while the run resolved stages;
              plan_edit.py caps them each time). Written by run_log.py and plan_edit.py.
  work/       the children's cwd: stage outputs, decisions, batches, scripts
  .compiled/  per wave launched: <wave>.js, the script pi-subagents runs (the wave as data, `const W`,
              then wave_runner.js), and <wave>.json: the compile's result (the call), `inputs` (each
              model stage's fingerprint, for reuse_earlier) and, once the wave ended, `result` (what it
              returned: status and children)

Relative param values (an `outputs` default, say) resolve against runs/<id>/. At most one run is
active; /agent-run continues it.
"""
import json
import re
from pathlib import Path
from typing import NamedTuple

from utils import read_json, read_yaml, write_yaml

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


def list_released_runs(agent_dir):
    """[(id, dir)] of a release's runs (prometheus/frozen.py: state.json, no run.yaml), oldest first: by when each
    started (its call.json, written once), since two ids from the same second sort by their random suffix."""
    base = Path(agent_dir) / RUNS
    found = [p for p in base.iterdir() if (p / "state.json").is_file() and not (p / "run.yaml").exists()] if base.is_dir() else []
    started = lambda p: (p / "call.json").stat().st_mtime_ns if (p / "call.json").exists() else 0
    return [(p.name, p) for p in sorted(found, key=lambda p: (started(p), p.name))]


def stages_done(run_dir):
    """The stages a run finished: an agent's run (run.yaml) or a release's (state.json)."""
    if state := read_run(run_dir):
        return {k for k, v in (state.get("stages") or {}).items() if v == "done"}
    try:
        return set(json.loads((Path(run_dir) / "state.json").read_text(encoding="utf-8")).get("done") or [])
    except (OSError, ValueError):
        return set()


def read_wave(run_dir, wave):
    """A launched wave's record (.compiled/<wave>.json), or {}."""
    record = read_json(Path(run_dir) / ".compiled" / f"{wave}.json")
    return record if isinstance(record, dict) else {}


def write_wave(run_dir, wave, record):
    compiled = Path(run_dir) / ".compiled"
    compiled.mkdir(parents=True, exist_ok=True)
    (compiled / f"{wave}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def record_wave_result(run_dir, wave, returned):
    """Add what a launch of the wave returned to its record."""
    write_wave(run_dir, wave, {**read_wave(run_dir, wave), "result": returned})


def read_wave_spec(run_dir, wave):
    """The wave as data (`const W = {...};`, the start of .compiled/<wave>.js), or None."""
    try:
        text = (Path(run_dir) / ".compiled" / f"{wave}.js").read_text(encoding="utf-8")
        spec, _end = json.JSONDecoder().raw_decode(text, text.index("const W = ") + len("const W = "))
    except (OSError, ValueError):
        return None
    return spec if isinstance(spec, dict) else None


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
