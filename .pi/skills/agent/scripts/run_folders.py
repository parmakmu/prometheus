"""An agent's runs: each execution of its plan is a folder runs/<id>/ (id: <date>-<n>; a release's run
(prometheus/run_release.py): <utc time>-<random>, with its caller's call.json and an events.jsonl beside).

  run.yaml    id, status, started_at, finished_at, stages {key: running | done} (absent = pending), launches
              [one entry per wave launch], given_params and preapproved (a caller's values and approvals), the
              same for both kinds. status: an agent's run is active (between waves, or a wave running), then
              done or stopped (a failed wave leaves it active, for a relaunch); a release's run is running or
              input_required (waiting for its caller's answer), then done, failed, stopped or error. An
              agent's run also has revision, note, max_cost_usd, critiques [{run_id, after_waves}] (critic
              results recorded while the run resolved stages; plan_edit.py caps them each time); a release's
              driver, cancel, needs and error. Written by run_steps.py and plan_edit.py, or run_release.Run.
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
HEADER = "# Run state, written by the agent's scripts (or a release's runner: run_release.py). Do not edit by hand.\n"


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
    """[(id, dir)] of the runs here, oldest first: an agent's by id (<date>-<n>), a release's by when each started
    (its run.yaml's started_at), since two of its ids from the same second sort by their random suffix."""
    base = Path(agent_dir) / RUNS
    found = [p for p in base.iterdir() if (p / "run.yaml").is_file()] if base.is_dir() else []
    key = lambda p: (m.group(1), int(m.group(2))) if (m := RUN_ID_RE.match(p.name)) else (str(read_run(p).get("started_at") or ""), 0)
    return [(p.name, p) for p in sorted(found, key=lambda p: (key(p), p.name))]


def is_release_run(run_dir):
    """Whether a run is a release's (prometheus/run_release.py), which keeps its caller's call beside its run.yaml."""
    return (Path(run_dir) / "call.json").is_file()


def stages_done(run_dir):
    """The stages a run finished."""
    return {k for k, v in (read_run(run_dir).get("stages") or {}).items() if v == "done"}


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
