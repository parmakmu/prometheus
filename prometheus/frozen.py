"""Released agents, run from code: no one to ask, no model deciding anything.

    from prometheus import load_agent
    agent = load_agent("emails-to-excel")              # the newest release; "emails-to-excel@v1" the newest 1.x
    result = agent.run(params={"email_folder": "mail/"}, approve=["build-workbook"])
    result.outputs["email_workbook"]                    # a Path

A release (release.py) is unpacked once into a cache folder (`git archive` of its tag, plus an empty git repo, which
pi-subagents' writer checks need), and each run is a folder runs/<id>/ inside it, as in the agent itself, so the
batcher, `uv run` and gates find what they expect. A Run is driven in code, wave by wave (compile_wave.py --frozen, then
`pi -p "/prometheus-run <run> <wave>"`, frozen_runner.ts, with pi-subagents: no model turn); a plan in which nothing
asks the caller anything is one wave, one launch. At a decision or a side effect not approved up front it stops
(`input_required`, what it needs in Result.needs) until Run.answer() gives it.

A param value can be a path (str or Path; a relative one is the caller's, made absolute), bytes or an open file (written
into the run's inputs/ first). Secrets are never passed: a secret param reads its environment variable, as a program's
command does. Approve side effects up front (`approve=`), or when the run asks.
"""
import contextlib
import datetime
import fcntl
import io
import json
import os
import secrets
import shutil
import signal
import subprocess
import tarfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from prometheus import cli
from prometheus.cli import PrometheusError, run_script


def get_cache() -> Path:
    return Path(os.environ.get("PROMETHEUS_CACHE") or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "prometheus")


def find_tools() -> tuple[str, Path]:
    """pi, and pi-subagents' entry point (loaded with -e: the runner disables pi's other extensions)."""
    pi = shutil.which("pi")
    agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent")
    subagents = agent_dir / "npm" / "node_modules" / "pi-subagents" / "index.js"
    if pi is None:
        raise PrometheusError("pi is not installed: npm install -g @earendil-works/pi-coding-agent (see `prometheus doctor`)")
    if not subagents.is_file():
        raise PrometheusError(f"pi-subagents is not installed ({subagents} is missing): pi install npm:pi-subagents")
    return pi, subagents


def pick_release(releases: list[dict], wanted: str | None) -> dict:
    """The newest release, the newest of a major ("v1"), or an exact one ("v1.2")."""
    if not releases:
        raise PrometheusError("the agent has no release yet: `prometheus release <slug>` makes one")
    if wanted is None:
        return releases[0]
    for r in releases:  # newest first
        if r["version"] == wanted or r["version"].startswith(f"{wanted}."):
            return r
    raise PrometheusError(f"no release {wanted}; releases: {', '.join(r['version'] for r in releases)}")


def unpack(folder: Path, release: dict) -> Path:
    """The release's files in the cache, unpacked once (a folder per commit, never changed after), in a folder named
    like the agent (children are told the agent's name from it)."""
    target = get_cache() / "releases" / folder.name / f"{release['version']}-{release['commit'][:12]}" / folder.name
    if (target / "plan.yaml").is_file():
        return target
    tar = subprocess.run(["git", "-C", str(folder), "archive", "--format=tar", release["commit"]], capture_output=True, check=False)
    if tar.returncode != 0:
        raise PrometheusError(f"cannot read release {release['version']}: {tar.stderr.decode(errors='replace').strip()}")
    staging = target.with_name(f".{target.name}.{secrets.token_hex(4)}")
    staging.mkdir(parents=True)  # beside the target: the rename below stays on one file system
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as archive:
        archive.extractall(staging, filter="data")
    subprocess.run(["git", "init", "-q", str(staging)], capture_output=True, check=True)
    try:
        staging.rename(target)
    except OSError:  # another process unpacked it first
        shutil.rmtree(staging, ignore_errors=True)
    return target


@dataclass
class Result:
    """Where a run stands once it stopped: ended (`ok` when complete), or `input_required` with `needs`: what the
    caller must answer (Run.answer) for it to go on. `outputs`: the plan's outputs by id, as paths."""
    ok: bool
    state: str
    run_dir: Path
    outputs: dict[str, Path | None] = field(default_factory=dict)
    cost_usd: float | None = None
    error: str | None = None
    needs: dict | None = None
    record: dict = field(default_factory=dict)  # the run's state.json: its waves, each with pi-subagents' record folder


ENDED = {"complete", "failed", "stopped", "error"}  # a run's own states besides running and input_required
INTERRUPTED = "the process running it stopped before it ended: start it again"


def safe_name(name: str, fallback: str) -> str:
    """A file name a caller gave, as one plain name inside a folder ("..", "" and paths become the fallback)."""
    name = Path(name or "").name
    return fallback if name in ("", ".", "..") else name


def launch_wave(agent: Agent, run_dir: Path, wave: str) -> subprocess.Popen:
    """pi running one compiled wave (frozen_runner.ts), with pi-subagents and nothing else loaded."""
    pi, subagents = find_tools()
    with (run_dir / "pi.log").open("ab") as log:
        return subprocess.Popen(
            [pi, "-p", "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-context-files",
             "--approve", "-e", str(subagents), "-e", str(Path(__file__).with_name("frozen_runner.ts")),
             f"/prometheus-run {run_dir} {wave}"],
            cwd=agent.folder, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def what_it_needs(preflight: dict) -> dict:
    """A paused run's questions, from the compile's preflight: the side effects to approve (what each writes) and the
    decisions to make (what to look at, and where the answer goes)."""
    return {"approve": [{k: a.get(k) for k in ("stage", "title", "why", "writes", "done_when")} for a in preflight.get("approvals_needed") or []],
            "decide": [{k: d.get(k) for k in ("stage", "title", "show_user", "write_answer_to")} for d in preflight.get("decisions_needed") or []]}


class Run:
    """A released agent's run, driven wave by wave in code (no model decides anything). It stops when it ends, or at
    a decision or an unapproved side effect (`input_required`), until answer() gives what it needs. Its state is in
    the run folder (state.json), so a run paused by one process can be answered and resumed by another."""

    def __init__(self, agent: Agent, run_dir: Path, values: dict, outputs: dict[str, Path | None]):
        self.agent, self.dir, self.values, self.planned_outputs = agent, run_dir, values, outputs
        self.events_file, self.state_file = run_dir / "events.jsonl", run_dir / "state.json"
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen | None = None
        self.cancelled = threading.Event()
        self.lock = threading.RLock()

    @property
    def id(self) -> str:
        return self.dir.name

    @classmethod
    def reopen(cls, agent: Agent, run_id: str) -> Run | None:
        """A run of `agent` from its folder (call.json, state.json), or None: one started by another process."""
        run_dir = agent.folder / "runs" / run_id
        try:
            call = json.loads((run_dir / "call.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        values = call.get("params") or {}
        return cls(agent, run_dir, values, agent.resolve_outputs(run_dir, values))

    # --- state and events ---------------------------------------------------------
    def read_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def write_state(self, **changes) -> dict:
        with self.lock:
            state = {**self.read_state(), **changes}
            self.state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")
        return state

    @property
    def state(self) -> str:
        """The run's state; a run whose driver died (its process is gone: a server restarted) ends as an error."""
        state = self.read_state()
        name = state.get("state", "running")
        if name == "running" and not (self.thread and self.thread.is_alive()) \
                and cli.import_script("utils").process_alive(state.get("driver")) is False:
            self.finish("error", INTERRUPTED)
            return "error"
        return name

    @contextlib.contextmanager
    def exclusive(self):
        """Held while the run's state is checked and changed, across threads and processes (a file lock)."""
        with self.lock, (self.dir / ".lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def cancel_requested(self) -> bool:
        return self.cancelled.is_set() or bool(self.read_state().get("cancel"))

    @property
    def running(self) -> bool:
        return self.state == "running"

    def log(self, **event):
        with self.events_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"), **event}) + "\n")

    def read_events(self) -> list[dict]:
        try:
            return [json.loads(line) for line in self.events_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            return []

    def events(self, poll_s: float = 0.2) -> Iterator[dict]:
        """Each event as it is written (launched, child, wave_end, input_required, end, ...), until the run stops: it
        ends, or waits for an answer (then a later events() after answer() goes on from there)."""
        sent = 0
        while True:
            found = self.read_events()
            yield from found[sent:]
            sent = len(found)
            if not self.running and not (self.thread and self.thread.is_alive()):
                yield from self.read_events()[sent:]
                return
            time.sleep(poll_s)

    # --- driving ------------------------------------------------------------------
    def compile(self) -> dict:
        return run_script("compile_wave.py", "--agent", str(self.agent.folder), "--frozen", str(self.dir),
                          "--params", json.dumps(self.values), "--approve", ",".join(self.read_state().get("approve") or []))

    def finish(self, state: str, error: str | None = None):
        launches = self.read_state().get("launches") or []
        cost = round(sum(r.get("cost_usd") or 0 for r in launches), 6)
        self.write_state(state=state, error=error, needs=None)
        self.log(event="end", state=state, error=error, cost_usd=cost)

    def drive(self, compiled: dict | None = None):
        """Compile, launch, record, until the run ends or needs an answer; `compiled`: the first wave's, already made."""
        try:
            while not self.cancel_requested():
                compiled = compiled or self.compile()
                if compiled.get("done"):
                    return self.finish("complete")
                if not compiled.get("ok"):
                    preflight = compiled.get("preflight") or {}
                    if preflight.get("decisions_needed") or preflight.get("approvals_needed"):
                        needs = what_it_needs(preflight)
                        self.write_state(state="input_required", needs=needs)
                        return self.log(event="input_required", wave=compiled.get("wave"), needs=needs)
                    return self.finish("error", describe_refusal(self.agent, compiled))
                wave = compiled["wave"]
                if self.cancel_requested():  # canceled while it compiled: launch nothing
                    break
                self.process = launch_wave(self.agent, self.dir, wave)
                if self.cancel_requested():  # canceled as it launched: cancel() saw no process to stop
                    self.process.send_signal(signal.SIGTERM)
                self.process.wait()
                end = next((e for e in reversed(self.read_events()) if e.get("event") == "wave_end" and e.get("wave") == wave), {})
                launched = next((e for e in reversed(self.read_events()) if e.get("event") == "launched" and e.get("wave") == wave), {})
                record = read_record(end.get("status_dir") or launched.get("status_dir"))
                value = (record.get("workflow") or {}).get("value") or {}
                # what later runs reuse by (compile_wave.reuse_earlier reads <wave>-result.json beside <wave>.inputs.json)
                (self.dir / ".compiled" / f"{wave}-result.json").write_text(json.dumps(value), encoding="utf-8")
                cost = ((end.get("cost") or record.get("totalCost") or {}).get("costUsd"))
                state = self.read_state()
                self.write_state(launches=[*(state.get("launches") or []), {
                    "wave": wave, "stages": compiled.get("stages"), "subagent_run_id": end.get("subagent_run_id") or launched.get("subagent_run_id"),
                    "status_dir": end.get("status_dir") or launched.get("status_dir"), "status": value.get("status") or end.get("state"),
                    "cost_usd": cost}])
                if self.cancel_requested() or record.get("state") == "stopped":
                    return self.finish("stopped", record.get("error") or "canceled")
                if end.get("state") in ("complete", "completed") and value.get("status") == "done":
                    self.write_state(done=[*(state.get("done") or []), *(compiled.get("stages") or [])])
                    compiled = None
                    continue
                failed = value.get("at") or [k for k, c in (value.get("children") or {}).items() if c.get("status") == "failed"]
                why = (f"stage {', '.join(failed)} failed" if failed else record.get("error") or f"wave {wave} ended {end.get('state') or 'without an end'}")
                return self.finish("failed", f"{why}: see {self.dir} and pi-subagents' record {end.get('status_dir') or launched.get('status_dir')}")
            self.finish("stopped", "canceled")
        except Exception as exc:  # noqa: BLE001 (the run must end with a state, whatever broke)
            self.finish("error", f"{type(exc).__name__}: {exc}")

    def go(self, compiled: dict | None = None):
        self.cancelled.clear()
        self.write_state(driver=os.getpid())  # who drives it: a later process can tell it died (state)
        self.thread = threading.Thread(target=self.drive, args=(compiled,), daemon=True)
        self.thread.start()

    # --- the caller's side ----------------------------------------------------------
    def wait(self, timeout: float | None = None) -> Result:
        """The run once it stopped: ended, or waiting for an answer (`state == "input_required"`, see `needs`)."""
        if self.thread:
            self.thread.join(timeout)
            if self.thread.is_alive():
                self.cancel()
                raise TimeoutError(f"run {self.id} took longer than {timeout} s: canceled")
        name = self.state  # a dead driver's run ends here as an error
        state = self.read_state()
        launches = state.get("launches") or []
        return Result(ok=name == "complete", state=name, run_dir=self.dir, outputs=self.planned_outputs,
                      cost_usd=round(sum(r.get("cost_usd") or 0 for r in launches), 6) if launches else None,
                      error=state.get("error"), needs=state.get("needs") if name == "input_required" else None, record=state)

    def answer(self, approve: list[str] | tuple = (), decisions: dict | None = None):
        """Give a paused run what it asked for (side effects approved, decisions made), and let it go on."""
        with self.exclusive():  # two answers at once: one goes on, the other is refused (never two drivers)
            state = self.read_state()
            if state.get("state") != "input_required":
                raise PrometheusError(f"run {self.id} is {state.get('state')}, not waiting for an answer")
            needs = state.get("needs") or {}
            asked = {d["stage"]: d for d in needs.get("decide") or []}
            if unknown := [s for s in decisions or {} if s not in asked]:
                raise PrometheusError(f"run {self.id} asks no decision of stage {', '.join(unknown)}; it asks: {', '.join(asked) or 'none'}")
            for stage, value in (decisions or {}).items():
                path = Path(asked[stage]["write_answer_to"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"stage": stage, "answer": value, "recorded_at": datetime.datetime.now(datetime.UTC).isoformat()}, indent=2),
                                encoding="utf-8")
            self.write_state(state="running", needs=None, approve=sorted({*(state.get("approve") or []), *approve}), driver=os.getpid())
        self.log(event="resumed", approved=list(approve), decided=sorted(decisions or {}))
        self.go()

    def cancel(self, grace_s: float = 30):
        """Stop the run: its wave's runner asks pi-subagents to stop it, then exits; a paused run just ends."""
        self.cancelled.set()
        with self.exclusive():
            self.write_state(cancel=True)  # seen by its driver, in whichever process
            if self.state == "input_required":
                return self.finish("stopped", "canceled while waiting for an answer")
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(grace_s)
            except subprocess.TimeoutExpired:
                self.process.kill()
        for launched in (e for e in self.read_events() if e.get("event") == "launched"):
            pid = read_record(launched.get("status_dir")).get("pid")
            if isinstance(pid, int):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        if self.thread:
            self.thread.join(grace_s)


def public_needs(run: Run, needs: dict | None) -> dict:
    """A paused run's questions for a caller: paths relative to the run, and not where the answer is kept."""
    prefix = str(run.dir) + "/"

    def clean(value):
        if isinstance(value, str):
            return value.replace(prefix, "")
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k != "write_answer_to"}
        return value
    return clean(needs or {})


def read_record(status_dir: str | None) -> dict:
    """pi-subagents' status.json for a launch, or {}."""
    try:
        return json.loads((Path(status_dir) / "status.json").read_text(encoding="utf-8")) if status_dir else {}
    except (OSError, ValueError):
        return {}


def describe_refusal(agent: Agent, compiled: dict) -> str:
    preflight = compiled.get("preflight") or {}
    why = [f"missing params: {', '.join(preflight['missing_params'])}" if preflight.get("missing_params") else "",
           f"missing secrets (environment variables): {', '.join(preflight['missing_secrets'])}" if preflight.get("missing_secrets") else "",
           "its cost limit is reached" if preflight.get("budget") else "",
           compiled.get("error") or "", "; ".join(compiled.get("errors") or [])]
    return f"{agent.name} {agent.version} can't run: {'; '.join(w for w in why if w) or 'see the compile result'}"


class Agent:
    """A release of an agent, unpacked and ready to run."""

    def __init__(self, folder: Path, release: dict):
        self.source = folder
        self.name = folder.name
        self.release = release
        self.version = release["version"]
        self.interface = release.get("interface") or {"params": {}, "outputs": {}, "side_effects": []}
        self.folder = unpack(folder, release)

    def __repr__(self):
        return f"<agent {self.name} {self.version}>"

    @property
    def params(self) -> dict:
        return self.interface["params"]

    @property
    def outputs(self) -> dict:
        return self.interface["outputs"]

    @property
    def side_effects(self) -> list[str]:
        return self.interface["side_effects"]

    @property
    def output_params(self) -> set[str]:
        """The params an output's location names (where the run writes): for a server, always inside the run."""
        PARAM_RE = cli.import_script("compile_wave").PARAM_RE
        plan = cli.import_script("utils").read_plan(self.folder, {}) or {}
        return {m for o in plan.get("outputs") or [] if isinstance(o, dict) for m in PARAM_RE.findall(str(o.get("location") or ""))}

    def output_file_name(self, name: str) -> str:
        """The file an output param names inside a run: its default's name (outputs/emails.xlsx -> emails.xlsx), else the
        param's name with the output's format as extension (report -> report.txt)."""
        PARAM_RE = cli.import_script("compile_wave").PARAM_RE
        plan = cli.import_script("utils").read_plan(self.folder, {}) or {}
        default = cli.import_script("agent_params").spec_of((plan.get("params") or {}).get(name)).get("default")
        if default:
            return Path(str(default)).name
        fmt = next((o.get("format") for o in plan.get("outputs") or [] if isinstance(o, dict)
                    and name in PARAM_RE.findall(str(o.get("location") or "")) and o.get("format")), None)
        return f"{name}.{fmt}" if fmt else name

    @property
    def folder_params(self) -> set[str]:
        """The params naming a folder (an input or output of kind folder): over HTTP, several files under one name."""
        PARAM_RE = cli.import_script("compile_wave").PARAM_RE
        plan = cli.import_script("utils").read_plan(self.folder, {}) or {}
        return {m for o in [*(plan.get("inputs") or []), *(plan.get("outputs") or [])]
                if isinstance(o, dict) and o.get("kind") == "folder" for m in PARAM_RE.findall(str(o.get("location") or ""))}

    def prepare_params(self, run_dir: Path, given: dict, in_run: tuple | set = ()) -> dict:
        """JSON-ready values, files written into the run's inputs/: bytes or an open file become inputs/<name>, a
        (file name, bytes) pair inputs/<name>/<file name> (programs may care about the extension), a {file name: bytes}
        dict the folder inputs/<name>/; a path becomes absolute. A param named in `in_run` is outputs/<name> in the
        run instead (a server: callers never name paths on it)."""
        values = {name: str(run_dir / "outputs" / self.output_file_name(name)) for name in in_run}
        for name, value in (given or {}).items():
            kind = (self.params.get(name) or {}).get("type", "path")
            if isinstance(value, dict) or isinstance(value, tuple) and len(value) == 2:
                folder = run_dir / "inputs" / name
                written = []
                for i, (file_name, data) in enumerate(value.items() if isinstance(value, dict) else [value]):
                    target = folder / safe_name(file_name, f"{name}-{i}")  # a bare name: never a path out of the folder
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data if isinstance(data, (bytes, bytearray)) else str(data).encode())
                    written.append(target)
                values[name] = str(folder if isinstance(value, dict) else written[0])
            elif isinstance(value, (bytes, bytearray)) or hasattr(value, "read"):
                data = value if isinstance(value, (bytes, bytearray)) else value.read()
                target = run_dir / "inputs" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data if isinstance(data, (bytes, bytearray)) else str(data).encode())
                values[name] = str(target)
            elif kind == "path" and isinstance(value, (str, os.PathLike)) and "://" not in str(value):
                values[name] = str(Path(value).expanduser().resolve())
            else:
                values[name] = value if isinstance(value, str) else str(value)
        return values

    def resolve_outputs(self, run_dir: Path, values: dict) -> dict[str, Path | None]:
        """Each plan output's location with the run's values (agent_params.resolve, as the compiler does)."""
        agent_params = cli.import_script("agent_params")
        PARAM_RE = cli.import_script("compile_wave").PARAM_RE
        read_plan = cli.import_script("utils").read_plan
        plan = read_plan(self.folder, {}) or {}
        specs = plan.get("params") or {}
        known = {**{n: agent_params.spec_of(s).get("default") for n, s in specs.items()}, **values}
        found = {}
        for o in plan.get("outputs") or []:
            if not isinstance(o, dict) or not o.get("id"):
                continue
            text, missing = str(o.get("location") or ""), False

            def sub(m):
                nonlocal missing
                name = m.group(1)
                if known.get(name) in (None, "") or name not in specs:
                    missing = True
                    return m.group(0)
                return agent_params.resolve(name, specs[name], known[name], run_dir)
            location = PARAM_RE.sub(sub, text)
            found[o["id"]] = None if missing or not location else Path(agent_params.resolve("_", {}, location, run_dir))
        return found

    def start(self, params: dict | None = None, approve: list[str] | tuple = (), in_run: tuple | set = ()) -> Run:
        """Start a run: refused at once if it can't start (a missing param or secret); otherwise it runs on its own, and
        may stop at a decision or an unapproved side effect (see Run.answer)."""
        find_tools()
        run_id = f"{datetime.datetime.now(datetime.UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        run_dir = self.folder / "runs" / run_id
        (run_dir / "work").mkdir(parents=True)  # the children's folder (run_log.py new makes it in an agent)
        try:
            values = self.prepare_params(run_dir, params or {}, in_run)
            (run_dir / "call.json").write_text(json.dumps({"agent": self.name, "version": self.version, "params": values,
                                                           "approve": list(approve)}, indent=1), encoding="utf-8")
            run = Run(self, run_dir, values, self.resolve_outputs(run_dir, values))
            run.write_state(state="running", approve=sorted(approve), done=[], launches=[], driver=os.getpid())
            compiled = run.compile()
            preflight = compiled.get("preflight") or {}
            if not compiled.get("ok") and not (preflight.get("decisions_needed") or preflight.get("approvals_needed")):
                raise PrometheusError(describe_refusal(self, compiled), compiled)
        except BaseException:
            shutil.rmtree(run_dir, ignore_errors=True)  # nothing ran
            raise
        run.go(compiled)
        return run

    def run(self, params: dict | None = None, approve: list[str] | tuple = (), timeout: float | None = None) -> Result:
        """Start a run and wait for it to stop: it ends, or (state "input_required") asks for an answer."""
        return self.start(params, approve).wait(timeout)


def load_agent(name: str, version: str | None = None) -> Agent:
    """An agent's release: `name` is a slug in the agents folder or a path to an agent, with an optional
    "@<version>" (v1: the newest 1.x; v1.2: exactly that); `version` says the same."""
    if "@" in name and version is None:
        name, version = name.rsplit("@", 1)
    folder = Path(name).expanduser()
    if not (folder / "plan.yaml").is_file():
        folder = cli.get_setting("agents")[0] / name
    if not (folder / "plan.yaml").is_file():
        raise PrometheusError(f"no agent {name!r} (neither a folder nor a slug in {cli.get_setting('agents')[0]})")
    listed = run_script("release.py", "list", "--agent", str(folder.resolve()))
    return Agent(folder.resolve(), pick_release(listed.get("releases") or [], version))
