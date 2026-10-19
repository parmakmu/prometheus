#!/usr/bin/env python3
"""A run's evidence as one file, to fix the agent where it is developed.

Usage:
  run_bundle.py make [--run DIR | --agent DIR] [--with-data] [--out DIR]
  run_bundle.py show [--run DIR | --agent DIR]
  run_bundle.py add FILE [--agent DIR]

make   writes <slug>-<version>-<run>.tgz from a run folder: a release's (prometheus/run_release.py: call.json, run.yaml;
       `kind: release`, its version, and its commit from RELEASE.json) or the agent's own (run.yaml: `kind: run`, version
       r<revision>, the commit of its branch; for an agent copied elsewhere, which has no prometheus). With --agent and
       no --run: that agent's active run, else its last. Without --with-data it holds only bundle.json, written field by
       field: agent, version, commit, run, state, error, the stages' statuses, the params' names, the prometheus, Pi and
       pi-subagents versions, and each launch's children with their status, error, exit code and `activity` (turns,
       cost, model, tool calls by name, failed calls' results); nothing of the run is copied as it is, since its
       records, sessions and compiled waves hold what it read, its param values among them. The texts that may still
       quote it (errors, failed calls' results) have each param's value replaced by <NAME> and are listed in
       `to_check`. --with-data adds the run as it is: bundle.json with each child's output and where its session is,
       `paths` (each file pi-subagents named, to where it is in the bundle), run/ (run.yaml, .compiled/, its data
       folders: a release's call.json, events.jsonl, pi.log, inputs/, outputs/, work/; an agent's work/), subagents/
       (pi-subagents' records of the run's launches, pi_records.launch_records), sessions/ (the children's session files)
       and, for an agent's run, agent-sessions/ (its own Pi sessions that overlap the run: `sessions`). Either way the
       value of every secret param set (the environment, an agent's .env) is replaced by ${NAME} wherever it appears,
       and text that looks like a key is reported (`warnings`). A running run (a release's) or a running wave (an
       agent's) is refused.
show   prints what a bundle made --with-data would say (bundle.json without `paths`) and a release's last events,
       packing nothing: `prometheus runs <slug> <run>` on the machine the run ran on.
add    unpacks a bundle into the agent's runs/imported/<version>-<run>/ (once; again is a no-op), for /agent-improve:
       session_digest.py reads it as evidence. It is not one of the agent's runs (run_folders.list_runs never
       sees it), so nothing is reused from it. Refused for another agent's bundle; `warnings`
       when the commit that ran isn't in this repository or not on main (the fix starts from other code).
"""
import datetime
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Annotated

import agent_params
import agent_repo
import pi_records
import typer
from run_folders import Run, active_run, is_release_run, list_runs, read_run
from session_digest import (
    SNIPPET,
    clip,
    default_sessions_dir,
    digest_session,
    moment,
    session_start,
)
from utils import (
    SECRET_PATTERNS,
    agent_name,
    emit,
    read_json,
    read_plan,
    refuse,
)

app = typer.Typer(add_completion=False)
FORMAT = 3  # 3: without --with-data, bundle.json alone (2: a release's run keeps run.yaml, as an agent's does)
IMPORTED = Path("runs") / "imported"
RUN_FILES = ("call.json", "run.yaml", "events.jsonl", "pi.log", ".compiled")
DATA_FOLDERS = ("inputs", "outputs", "work")
AGENT_RUN_FILES = ("run.yaml", ".compiled")  # an agent's own run: its params are paths of its machine, not in the run
AGENT_DATA_FOLDERS = ("work",)
# the children's own sessions and records (input, output, transcript, meta): with the agent in a session, pi-subagents
# resolves its relative sessionDir in each child's cwd, the run's work/, and names them relative to it
CHILD_RECORDS = Path("work") / ".pi" / "sessions"
OUTPUT_CHARS = 4000  # of a child's output, in bundle.json
CONTENT = ("output", "session", "transcript")  # a child's fields that hold or point at what it read: --with-data only


@app.callback()
def commands():
    """A run's evidence as one file."""


def read_versions(agent_dir):
    """What ran it: the prometheus the release's skill copy came from, Pi's and pi-subagents' versions here."""
    try:
        pi = subprocess.run(["pi", "--version"], capture_output=True, text=True, timeout=20, check=False).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pi = None
    package = pi_records.package(agent_dir)
    version_file = agent_dir / ".pi" / "skills" / "agent" / "VERSION"
    return {"prometheus": version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else None,
            "pi": pi, "subagents": (read_json(package[0]) or {}).get("version") if package else None}


def find_secrets(agent_dir):
    """{value: NAME} of the agent's secret params that are set: in the environment, or in its .env (agent_params.load;
    a release has none)."""
    specs = (read_plan(agent_dir, {}) or {}).get("params") or {}
    values = agent_params.load(agent_dir, specs)
    found = {}
    for name, spec in specs.items():
        value = values.get(name)
        if agent_params.kind(spec) == "secret" and value is not None and len(text := value.get_secret_value()) >= 4:
            found[text] = agent_params.env_name(name, spec)
    return found


def find_in_bundle(path, paths: dict, run_dir: Path):
    """Where a file a record names is in the bundle: one of pi-subagents' own (`paths` holds resolved paths: /var is
    /private/var on macOS), or a child's record named relative to its cwd, the run's work/ (CHILD_RECORDS)."""
    if not path:
        return None
    if not Path(path).is_absolute():
        found = (run_dir / "work" / path).resolve()
        return f"run/{found.relative_to(run_dir.resolve())}" if found.is_file() and found.is_relative_to(run_dir.resolve() / CHILD_RECORDS) else None
    return paths.get(str(Path(path).resolve()))


def find_on_disk(path, paths: dict, run_dir: Path) -> Path | None:
    """A file a record names, here, when it is one a bundle takes (find_in_bundle)."""
    if find_in_bundle(path, paths, run_dir) is None:
        return None
    return Path(path).resolve() if Path(path).is_absolute() else (run_dir / "work" / path).resolve()


def describe_activity(session: Path | None) -> dict | None:
    """What a child did, from its session or transcript, without what it read: turns, cost, model, its tool calls by
    name, and the results of those that failed, whole (they may quote it: finish)."""
    if session is None:
        return None
    d = digest_session(session, result_size=sys.maxsize)
    return {"turns": d["assistant_turns"], "cost_usd": d["cost_usd"], "model": d["model"], "tool_calls": d["tool_calls"],
            "failed_calls": [{"tool": f["tool"], "result": f["result"]} for f in d["failed_or_blocked"]]}


def describe_children(folder: Path, paths: dict, run_dir: Path) -> list[dict]:
    """A wave's children from its record: key, agent, status, error, what they did, output, and their files in the
    bundle."""
    status = read_json(folder / "status.json") or {}
    settled = {}
    try:
        for line in (folder / "workflow-children.jsonl").read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("type") == "settle":
                settled[entry.get("key")] = entry.get("result") or {}
    except (OSError, ValueError):
        pass
    children = []
    for step in status.get("steps") or []:
        key = step.get("workflowKey")
        result = settled.get(key) or {}
        first = next(iter(result.get("results") or []), {})  # the child's own result: where its transcript is
        output = str(result.get("output") or result.get("error") or "")
        transcript = step.get("transcriptPath") or first.get("transcriptPath")
        children.append({
            "key": key, "agent": step.get("agent"), "status": step.get("status"),
            "error": step.get("error") or result.get("error"), "exit_code": step.get("exitCode"), "run_id": step.get("runId"),
            "activity": describe_activity(find_on_disk(step.get("sessionFile"), paths, run_dir)
                                          or find_on_disk(transcript, paths, run_dir)),
            "output": output,
            "session": find_in_bundle(step.get("sessionFile"), paths, run_dir),
            "transcript": find_in_bundle(transcript, paths, run_dir),
        })
    return children


def describe_release_run(run_dir: Path, ended: bool):
    """A release's run (prometheus/run_release.py): its facts for bundle.json, its launches, and its files and data folders;
    `ended`: refuse one still running (a bundle)."""
    state, call = read_run(run_dir), read_json(run_dir / "call.json")
    if ended and state.get("status") == "running":
        refuse(f"run {run_dir.name} is still running: bundle it once it ended (or cancel it)")
    agent_dir = run_dir.parent.parent  # releases/<slug>/<version>/runs/<id>
    release = read_json(agent_dir / "RELEASE.json") or {}  # written when it was unpacked (prometheus/run_release.py)
    facts = {"kind": "release", "agent": call.get("agent") or agent_name(agent_dir), "version": call.get("version"),
             "commit": release.get("commit"),
             "state": state.get("status"), "error": state.get("error")}
    return agent_dir, facts, state.get("launches") or [], RUN_FILES, DATA_FOLDERS


def describe_agent_run(run_dir: Path, ended: bool):
    """One of an agent's own runs (run.yaml), the same way: its revision as the version, its branch's commit, and how
    its last unfinished launch ended as the error."""
    run = Run(run_dir.name, run_dir, read_run(run_dir))
    if ended and run.wave_running:
        refuse(f"a wave of run {run.id} is running: bundle it once the wave ended")
    agent_dir = run_dir.parent.parent
    branch = run.branch if run.branch in agent_repo.run_branches(agent_dir) else agent_repo.MAIN
    commit = agent_repo.git(agent_dir, "rev-parse", "--short=12", branch, check=False).stdout.strip() or None \
        if agent_repo.available() and agent_repo.is_repo(agent_dir) else None
    launches = [x for x in run.state.get("launches") or [] if isinstance(x, dict)]
    unfinished = next((x for x in reversed(launches) if x.get("status") not in ("done", None)), None)
    facts = {"kind": "run", "agent": agent_dir.name, "version": f"r{run.state.get('revision', 1)}", "commit": commit,
             "state": run.state.get("status"),
             "error": f"wave {unfinished.get('wave')} {unfinished.get('status')}" if unfinished else None}
    return agent_dir, facts, launches, AGENT_RUN_FILES, AGENT_DATA_FOLDERS


def find_agent_sessions(agent_dir: Path, run_state: dict) -> list[Path]:
    """The agent's own Pi sessions that overlap an agent's run: started before it finished, written since it started."""
    started, finished = moment(run_state.get("started_at")), moment(run_state.get("finished_at"))
    found = []
    for path in sorted(default_sessions_dir(agent_dir).glob("*.jsonl")):
        begun = moment(session_start(path))
        written = datetime.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        if begun and (finished is None or begun <= finished) and (started is None or written >= started):
            found.append(path)
    return found


def read_given_params(run_dir: Path) -> list[dict]:
    """The params a run was given: a release's call.json, an agent's launches and `given_params`."""
    state = read_run(run_dir)
    return [(read_json(run_dir / "call.json") or {}).get("params") or {}, state.get("given_params") or {},
            *((x.get("params") or {}) for x in state.get("launches") or [] if isinstance(x, dict))]


def find_param_values(run_dir: Path) -> dict:
    """{value: param name} of the run's params, and of a path's file name; a value under 4 characters is left out (it
    would be replaced anywhere)."""
    found = {}
    for params in read_given_params(run_dir):
        for name, value in params.items():
            for text in (str(value), Path(str(value)).name):
                if len(text) >= 4:
                    found.setdefault(text, name)
    return found


def finish(info: dict, replace: dict, with_data: bool) -> tuple[dict, list[str], set[str]]:
    """bundle.json from collect's facts: in its texts (errors, failed calls' results, outputs) each value in `replace`
    replaced, in one pass, and only then clipped (a clipped value would no longer match); without what the run read
    unless `with_data` (children's output and files, `paths`, the agent's sessions). (it, the texts left that may quote
    what the run read, for `to_check`, the values replaced)."""
    pattern = re.compile("|".join(map(re.escape, sorted(replace, key=len, reverse=True)))) if replace else None
    to_check, hits = [], set()

    def scrub(text, size: int | None = SNIPPET, where=None):
        """`text` replaced, then clipped to `size` (None: whole; negative: its end)."""
        if not text:
            return text
        if pattern:
            text = pattern.sub(lambda m: hits.add(m.group(0)) or replace[m.group(0)], str(text))
        text = str(text) if size is None else clip(text, size) if size > 0 else str(text)[size:]
        if where and not with_data:
            to_check.append(f"{where}: {text}")
        return text

    error, launches = scrub(info.get("error"), None, "run"), []
    for launch in info["launches"]:
        children = []
        for c in launch["children"]:
            child = {**c, "error": scrub(c["error"], None, c["key"]), "output": scrub(c["output"], -OUTPUT_CHARS)}
            if c["activity"]:
                child["activity"] = {**c["activity"], "failed_calls": [
                    {**f, "result": scrub(f["result"], where=f"{c['key']} ({f['tool']})")} for f in c["activity"]["failed_calls"]]}
            children.append(child if with_data else {k: v for k, v in child.items() if k not in CONTENT})
        launches.append({**launch, "children": children} if with_data
                        else {**{k: v for k, v in launch.items() if k != "record"}, "children": children})
    dropped = () if with_data else ("paths", "sessions")
    return {**{k: v for k, v in info.items() if k not in dropped}, "error": error, "launches": launches}, to_check, hits


def collect(run_dir: Path | None, agent_dir: Path | None, ended: bool = True):
    """(agent folder, run folder, bundle.json's facts, {name in the bundle: file}: all of them, as --with-data takes
    them) for a run folder, or the agent's active (else last) run; `ended`: refuse a run (or an agent's wave) still
    running."""
    if run_dir is None:
        runs = list_runs(agent_dir or Path("."))
        run_dir = (active_run(agent_dir or Path(".")) or Run(*runs[-1], {})).dir if runs else None
        if run_dir is None:
            refuse(f"{(agent_dir or Path('.')).resolve().name} has no run to bundle yet")
    run_dir = run_dir.resolve()
    if not (run_dir / "run.yaml").is_file():
        refuse(f"{run_dir} is not a run (no run.yaml)", 2)
    if is_release_run(run_dir):
        agent_dir, facts, launches, run_files, data_folders = describe_release_run(run_dir, ended)
        sessions = []
    else:
        agent_dir, facts, launches, run_files, data_folders = describe_agent_run(run_dir, ended)
        sessions = find_agent_sessions(agent_dir, read_run(run_dir))

    entries = {}  # name in the bundle -> file
    for name in run_files + data_folders + (str(CHILD_RECORDS),):
        path = run_dir / name
        for f in sorted(path.rglob("*")) if path.is_dir() else [path] if path.is_file() else []:
            if f.is_file() and not f.is_symlink():
                entries[f"run/{f.relative_to(run_dir)}"] = f
    for f in sessions:
        entries[f"agent-sessions/{f.name}"] = f
    paths, described = {}, []
    for launch in launches:
        records = pi_records.launch_records(str(launch.get("subagent_run_id") or ""))
        for folder in records["folders"]:
            for f in sorted(folder.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    entries[pi_records.bundle_name(f)] = f
        for f in records["files"]:
            paths[str(f)] = pi_records.bundle_name(f)
            entries[paths[str(f)]] = f
    for launch in launches:  # once every file has its place: the children name theirs
        folder = pi_records.runs_root() / str(launch.get("subagent_run_id") or "")
        described.append({**{k: launch.get(k) for k in ("wave", "stages", "status", "cost_usd", "subagent_run_id")},
                          "record": pi_records.bundle_name(folder) if folder.is_dir() else None,
                          "children": describe_children(folder, paths, run_dir) if folder.is_dir() else []})

    info = {"format": FORMAT, **facts, "run": run_dir.name, "stages": read_run(run_dir).get("stages"),
            "params": sorted({name for params in read_given_params(run_dir) for name in params}),
            "made_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"), "versions": read_versions(agent_dir),
            "launches": described, "sessions": [f"agent-sessions/{f.name}" for f in sessions], "paths": paths}
    return agent_dir, run_dir, info, entries


RUN_OPTION = Annotated[Path | None, typer.Option("--run", help="a run folder: a release's or the agent's own")]
AGENT_OPTION = Annotated[Path | None, typer.Option("--agent", help="without --run: the agent whose active (else last) run to take")]
EVENTS_SHOWN = 20


@app.command()
def show(run_dir: RUN_OPTION = None, agent_dir: AGENT_OPTION = None):
    """What a run's bundle made --with-data would say (bundle.json, without `paths`), and a release's last events: packs
    nothing (it is shown where the run ran)."""
    _agent_dir, _run_dir, info, _entries = collect(run_dir, agent_dir, ended=False)
    info = finish(info, {}, with_data=True)[0]
    folder = run_dir.resolve() if run_dir else None
    events = []
    if folder and (folder / "events.jsonl").is_file():
        for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()[-EVENTS_SHOWN:]:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    info.pop("paths")
    emit({"ok": True, **info, "events": events})


@app.command()
def make(run_dir: RUN_OPTION = None, agent_dir: AGENT_OPTION = None,
         with_data: Annotated[bool, typer.Option("--with-data", help="add the run as it is: its records, the children's sessions and output, its data folders")] = False,
         out: Annotated[Path, typer.Option(help="folder to write the bundle in")] = Path(".")):
    """Write a run's bundle."""
    agent_dir, run_dir, info, entries = collect(run_dir, agent_dir)
    secrets = find_secrets(agent_dir)
    params = {} if with_data else {v: f"<{name}>" for v, name in find_param_values(run_dir).items()}
    info, to_check, hits = finish(info, {**params, **{v: f"${{{env}}}" for v, env in secrets.items()}}, with_data)
    redacted, warnings = {env for v, env in secrets.items() if v in hits}, []
    if not with_data:
        entries = {}
    out.mkdir(parents=True, exist_ok=True)
    target = out.resolve() / f"{info['agent']}-{info['version']}-{info['run']}.tgz"
    with tarfile.open(target, "w:gz") as tar:
        def add(name, data: bytes):
            for value, env in secrets.items():
                if value.encode() in data:
                    data = data.replace(value.encode(), f"${{{env}}}".encode())
                    redacted.add(env)
            try:
                text = data.decode("utf-8")
                warnings.extend(f"{name}: looks like {what}" for pattern, what in SECRET_PATTERNS if pattern.search(text))
            except UnicodeDecodeError:
                pass
            member = tarfile.TarInfo(name)
            member.size, member.mtime = len(data), int(datetime.datetime.now(datetime.UTC).timestamp())
            tar.addfile(member, io.BytesIO(data))
        for name, path in entries.items():
            try:
                add(name, path.read_bytes())
            except OSError:
                continue
        add("bundle.json", json.dumps({**info, "with_data": with_data, "redacted": sorted(redacted)}, indent=1).encode())
    emit({"ok": True, "bundle": str(target), "size_mb": round(target.stat().st_size / 1e6, 2), "files": len(entries) + 1,
          "with_data": with_data, "redacted": sorted(redacted), "warnings": warnings, "to_check": to_check})


def check_commit(agent_dir: Path, commit: str | None) -> list[str]:
    """Why the fix may start from other code than what ran: its commit unknown here, or not on main."""
    if not commit or not (agent_repo.available() and agent_repo.is_repo(agent_dir)):
        return []
    if agent_repo.git(agent_dir, "cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode != 0:
        return [f"the commit that ran, {commit}, isn't in this repository: pull the agent first, or the fix starts from other code"]
    if agent_repo.git(agent_dir, "merge-base", "--is-ancestor", commit, agent_repo.MAIN, check=False).returncode != 0:
        return [f"the commit that ran, {commit}, isn't on {agent_repo.MAIN}: the fix starts from other code than what ran"]
    return []


@app.command()
def add(bundle: Annotated[Path, typer.Argument(help="a bundle made by `prometheus bundle` or /agent-bundle")],
        agent_dir: Annotated[Path, typer.Option("--agent", help="agent root")] = Path(".")):
    """Unpack a bundle into the agent, for /agent-improve."""
    agent_dir = agent_dir.resolve()
    if not (agent_dir / "plan.yaml").is_file():
        refuse(f"{agent_dir} is not an agent (no plan.yaml)", 2)
    try:
        with tarfile.open(bundle) as tar:
            info = json.load(tar.extractfile("bundle.json"))
            if info.get("format") != FORMAT:
                refuse(f"{bundle.name} is a bundle of format {info.get('format')}; this agent reads format {FORMAT}: refresh it")
            if info.get("agent") != agent_dir.name:
                refuse(f"{bundle.name} is a run of {info.get('agent')}, not {agent_dir.name}")
            target = agent_dir / IMPORTED / f"{info.get('version')}-{info.get('run')}"
            already = (target / "bundle.json").is_file()
            if not already:
                staging = target.with_name(f".{target.name}.partial")
                shutil.rmtree(staging, ignore_errors=True)  # what an interrupted add left
                tar.extractall(staging, filter="data")  # no links out, no absolute or ../ names
                info["imported_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
                (staging / "bundle.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
                staging.rename(target)
    except (OSError, KeyError, ValueError, tarfile.TarError) as exc:
        refuse(f"cannot read {bundle}: {exc}")
    emit({"ok": True, "imported": str(target.relative_to(agent_dir)), "already": already,
          **{k: info.get(k) for k in ("version", "commit", "run", "state", "error")},
          "warnings": check_commit(agent_dir, info.get("commit"))})


if __name__ == "__main__":
    app()
