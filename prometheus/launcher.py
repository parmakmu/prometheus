"""How a released run's waves run: pi, started once per wave in the release's folder (`pi -p "/prometheus-run <run>
<wave>"`, release_runner.ts), launches the wave run_compile.py --frozen compiled through pi-subagents and waits for it.

The only part of a released run that knows Pi or pi-subagents. run_release.Run calls check() before a run, launch()
for each wave, outcome() once it exited and stop() to cancel, and reads nothing else; another way of running a
compiled wave (.compiled/<wave>.json) replaces these four functions. The runner writes the run's events.jsonl
(launched, child, wave_end); the skill's pi_records.py reads pi-subagents' record of the launch."""
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from prometheus.errors import PrometheusError
from prometheus.skill import pi_records, utils

ENDED = {"complete", "completed"}  # the states of a launch that ran to its end (its value says how the wave went)


def check() -> tuple[str, Path]:
    """pi, and pi-subagents' entry point (loaded with -e: the runner disables pi's other extensions); refused without."""
    pi = shutil.which("pi")
    installed = pi_records.install()
    if pi is None:
        raise PrometheusError("pi is not installed: npm install -g @earendil-works/pi-coding-agent (see `prometheus doctor`)")
    if installed is None or not (installed / "index.js").is_file():
        raise PrometheusError("pi-subagents is not installed in Pi's folder: pi install npm:pi-subagents")
    return pi, installed / "index.js"


def launch(folder: Path, run_dir: Path, wave: str) -> subprocess.Popen:
    """Start the wave compiled in `run_dir` (the release in `folder`): a process the caller waits for, or signals
    (SIGTERM) to stop it."""
    pi, subagents = check()
    with (run_dir / "pi.log").open("ab") as log:
        return subprocess.Popen(
            [pi, "-p", "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-context-files",
             "--approve", "-e", str(subagents), "-e", str(Path(__file__).with_name("release_runner.ts")),
             f"/prometheus-run {run_dir} {wave}"],
            cwd=folder, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def outcome(run_dir: Path, wave: str) -> dict:
    """How the wave's last launch ended, once its process exited: {state (complete when it ran to its end, else stopped,
    failed, lost, ...), value (what the wave returned: status, children, at), cost_usd, tokens, error, where (what to
    look at when it failed), launch (where its own records are, for run.yaml: subagent_run_id, status_dir)}."""
    events = read_events(run_dir)
    end = next((e for e in reversed(events) if e.get("event") == "wave_end" and e.get("wave") == wave), {})
    launched = next((e for e in reversed(events) if e.get("event") == "launched" and e.get("wave") == wave), {})
    status_dir = end.get("status_dir") or launched.get("status_dir")
    record = read_status(status_dir)
    found = pi_records.outcome({**record, "totalCost": end.get("cost") or record.get("totalCost")})
    state = "stopped" if record.get("state") == "stopped" else end.get("state")
    return {**found, "state": "complete" if state in ENDED else state, "value": found["value"] or {},
            "where": f"pi-subagents' record {status_dir}",
            "launch": {"subagent_run_id": end.get("subagent_run_id") or launched.get("subagent_run_id"), "status_dir": status_dir}}


def stop(run_dir: Path, grace_s: float):
    """Stop the run's launches still running, which another process may have started: pi-subagents' runner is a process
    of its own, asked to stop (SIGTERM), then killed after `grace_s`."""
    alive = utils.process_alive
    still_running = pi_records.STILL_RUNNING
    pids = []
    for launched in (e for e in read_events(run_dir) if e.get("event") == "launched"):
        record = read_status(launched.get("status_dir"))
        pid = record.get("pid")  # only a launch still running: an ended one's pid may be another process's by now
        if isinstance(pid, int) and record.get("state") in still_running and alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                pids.append(pid)
            except OSError:
                pass
    deadline = time.monotonic() + grace_s
    while (pids := [p for p in pids if alive(p)]) and time.monotonic() < deadline:
        time.sleep(0.2)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def read_events(run_dir: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def read_status(status_dir: str | None) -> dict:
    """pi-subagents' record of a launch, in the folder the runner logs (pi_records.read_record), or {}."""
    return pi_records.read_record(status_dir) or {}
