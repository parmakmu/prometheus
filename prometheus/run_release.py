"""Released agents, run from code: no one to ask, no model deciding anything.

    from prometheus import load_agent
    agent = load_agent("emails-to-excel")              # the newest release; "emails-to-excel@v1" the newest 1.x
    result = agent.run(params={"email_folder": "mail/"}, approve=["build-workbook"])
    result.outputs["email_workbook"]                    # a Path

A release (agent_release.py) is unpacked once into the releases folder, releases/<slug>/<version>/ (`git archive` of its tag,
plus an empty git repo, which pi-subagents' writer checks need), with RELEASE.json: its record (agent, version, commit,
interface); the agent's name comes from there, not from the folder's. Each run is a folder runs/<id>/ inside it, as in the agent itself, so the batcher, `uv run` and gates find
what they expect. Releases are kept, not cached: on a machine that only runs them (`prometheus install <source>@v1`)
there is no repository to unpack them from again, and their runs are inside. With the agent's repository at hand its
tags decide which release a name means; without it, the installed releases' records do. A Run is driven in code, wave by wave (run_compile.py --frozen, then
`pi -p "/prometheus-run <run> <wave>"`, release_runner.ts, with pi-subagents: no model turn); a plan in which nothing
asks the caller anything is one wave, one launch. At a decision or a side effect not approved up front it stops
(`input_required`, what it needs in Result.needs) until Run.answer() gives it.

A branch, tag or commit that isn't a release runs the same way as a build (load_agent("mailer@improve/2026-10-16-1")):
to check a fix before releasing it, with a failed run's call from its bundle (read_bundle_call; its side effects ask
again).

A param value can be a path (str or Path; a relative one is the caller's, made absolute), bytes or an open file (written
into the run's inputs/ first). Secrets are never passed: a secret param reads its environment variable, as a program's
command does. Approve side effects up front (`approve=`), or when the run asks. A run that failed can be bundled
(run.bundle()) for whoever develops the agent.
"""
import contextlib
import datetime
import fcntl
import io
import json
import os
import re
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

RELEASE_FILE = "RELEASE.json"  # in an unpacked release: its record (version, commit, interface); run_bundle.py reads it
VERSION_RE = re.compile(r"^v\d+(\.\d+)?$")  # a release (v1: the newest 1.x); anything else names a commit to build


def get_cache() -> Path:
    """What can be made again: bundles' files unpacked to rerun them."""
    return Path(os.environ.get("PROMETHEUS_CACHE") or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "prometheus")


def get_releases() -> Path:
    """Where releases are unpacked and run: kept, not a cache (installed ones have no repository to come back from)."""
    return Path(os.environ.get("PROMETHEUS_RELEASES")
                or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "prometheus" / "releases")


def release_folder(slug: str, release: dict) -> Path:
    """releases/<slug>/<version>/ (a build: build-<commit12>): the agent's folder; its RELEASE.json names the agent."""
    name = f"build-{release['commit'][:12]}" if release["version"] == "build" else release["version"]
    return get_releases() / slug / name


def list_installed(slug: str) -> list[dict]:
    """The release records (RELEASE.json) of a slug's unpacked releases, newest first; builds left out."""
    records = []
    for path in (get_releases() / slug).glob(f"v*/{RELEASE_FILE}"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (m := re.match(r"^v(\d+)\.(\d+)$", str(record.get("version")))) and record.get("commit"):
            records.append(((int(m.group(1)), int(m.group(2))), record))
    return [r for _, r in sorted(records, key=lambda x: x[0], reverse=True)]


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


def unpack(repo: Path, release: dict, slug: str | None = None, source: str | None = None) -> Path:
    """The release's files from `repo` (the agent's repository, or the one tag an install fetched), unpacked once into
    release_folder and never changed after. Without the agent's extension (`.pi/extensions/`: the runner never loads it,
    and without it Pi started there has no commands to change the release in place), and with RELEASE.json: the
    release's record, and where to change it (`source`: the repository it came from). A release found unpacked from
    another commit (its tag moved) is refused: a version never changes."""
    slug = slug or repo.name
    target = release_folder(slug, release)
    if (target / RELEASE_FILE).is_file():
        kept = json.loads((target / RELEASE_FILE).read_text(encoding="utf-8")).get("commit")
        if kept != release["commit"]:
            raise PrometheusError(f"{slug} {release['version']} here is commit {str(kept)[:12]}, but its tag now names "
                                  f"{release['commit'][:12]}: a release never changes; release the change as a new version")
        return target
    tar = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", release["commit"]], capture_output=True, check=False)
    if tar.returncode != 0:
        raise PrometheusError(f"cannot read release {release['version']}: {tar.stderr.decode(errors='replace').strip()}")
    staging = target.with_name(f".{target.name}.{secrets.token_hex(4)}")
    staging.mkdir(parents=True)  # beside the target: the rename below stays on one file system
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as archive:
        archive.extractall(staging, filter="data")
    shutil.rmtree(staging / ".pi" / "extensions", ignore_errors=True)
    source = source or str(repo)
    (staging / RELEASE_FILE).write_text(json.dumps({
        "agent": slug, **{k: release.get(k) for k in ("version", "commit", "revision", "breaking", "interface")}, "source": source,
        "note": f"Release {release['version']} of {slug}, unpacked to run. Don't change it here: every later run of "
                f"{release['version']} would use the change, unreviewed. Change the agent in its repository ({source}) with "
                "/agent-revise or /agent-improve, then `prometheus release`."}, indent=1) + "\n", encoding="utf-8")
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
    record: dict = field(default_factory=dict)  # the run's run.yaml: its stages and waves, each with pi-subagents' record folder


ENDED = {"complete", "failed", "stopped", "error"}  # a run's own states besides running and input_required


def state_of(record: dict) -> str:
    """A released run's state as callers see it (Result.state, serve, A2A), from its run.yaml's status: the same words,
    but `done`, as every run records it, is `complete`; running until the first write."""
    status = record.get("status") or "running"
    return "complete" if status == "done" else status


def mark_done(state: dict, stages: list | None) -> dict:
    """A run's stages (run.yaml), with `stages` done."""
    return {**(state.get("stages") or {}), **{s: "done" for s in stages or []}}
INTERRUPTED = "the process running it stopped before it ended: start it again"


def safe_name(name: str, fallback: str) -> str:
    """A file name a caller gave, as one plain name inside a folder ("..", "" and paths become the fallback)."""
    name = Path(name or "").name
    return fallback if name in ("", ".", "..") else name


def launch_wave(agent: Agent, run_dir: Path, wave: str) -> subprocess.Popen:
    """pi running one compiled wave (release_runner.ts), with pi-subagents and nothing else loaded."""
    pi, subagents = find_tools()
    with (run_dir / "pi.log").open("ab") as log:
        return subprocess.Popen(
            [pi, "-p", "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-context-files",
             "--approve", "-e", str(subagents), "-e", str(Path(__file__).with_name("release_runner.ts")),
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
    the run folder (run.yaml, as an agent's own run keeps it: run_folders.py), so a run paused by one process can be
    answered and resumed by another."""

    def __init__(self, agent: Agent, run_dir: Path, values: dict, outputs: dict[str, Path | None]):
        self.agent, self.dir, self.values, self.planned_outputs = agent, run_dir, values, outputs
        self.events_file = run_dir / "events.jsonl"
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen | None = None
        self.cancelled = threading.Event()
        self.lock = threading.RLock()

    @property
    def id(self) -> str:
        return self.dir.name

    @classmethod
    def reopen(cls, agent: Agent, run_id: str) -> Run | None:
        """A run of `agent` from its folder (call.json, run.yaml), or None: one started by another process."""
        run_dir = agent.folder / "runs" / run_id
        try:
            call = json.loads((run_dir / "call.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        values = call.get("params") or {}
        return cls(agent, run_dir, values, agent.resolve_outputs(run_dir, values))

    # --- state and events ---------------------------------------------------------
    def read_state(self) -> dict:
        return cli.import_script("run_folders").read_run(self.dir)

    def write_state(self, **changes) -> dict:
        run_folders = cli.import_script("run_folders")
        with self.lock:
            state = {**self.read_state(), **changes}
            run_folders.Run(self.id, self.dir, state).save()
        return state

    @property
    def state(self) -> str:
        """The run's state; a run whose driver died (its process is gone: a server restarted) ends as an error."""
        state = self.read_state()
        name = state_of(state)
        driver = state.get("driver") or {}
        if name == "running" and not (self.thread and self.thread.is_alive()) \
                and cli.import_script("utils").process_alive(driver.get("pid"), driver.get("started")) is False:
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
        return run_script("run_compile.py", "--agent", str(self.agent.folder), "--frozen", str(self.dir),
                          "--params", json.dumps(self.values), "--approve", ",".join(self.read_state().get("preapproved") or []))

    def finish(self, state: str, error: str | None = None):
        launches = self.read_state().get("launches") or []
        cost = round(sum(r.get("cost_usd") or 0 for r in launches), 6)
        self.write_state(status="done" if state == "complete" else state, error=error, needs=None,
                         finished_at=datetime.datetime.now(datetime.UTC).isoformat())
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
                        self.write_state(status="input_required", needs=needs)
                        return self.log(event="input_required", wave=compiled.get("wave"), needs=needs)
                    return self.finish("error", describe_refusal(self.agent, compiled))
                wave = compiled["wave"]
                if compiled.get("nothing_to_launch"):  # every stage reused from an earlier run: recorded, not launched
                    cli.import_script("run_folders").record_wave_result(self.dir, wave, compiled["outcome"])
                    state = self.read_state()
                    self.write_state(launches=[*(state.get("launches") or []), {"wave": wave, "stages": compiled.get("stages"),
                                                                                "status": "done", "cost_usd": 0.0, "launched": False}],
                                     stages=mark_done(state, compiled.get("stages")))
                    self.log(event="wave_end", wave=wave, state="complete", reused=True, cost=None)
                    compiled = None
                    continue
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
                # what later runs reuse by (run_compile.reuse_earlier: the record's `result` beside its `inputs`)
                cli.import_script("run_folders").record_wave_result(self.dir, wave, value)
                cost = ((end.get("cost") or record.get("totalCost") or {}).get("costUsd"))
                state = self.read_state()
                self.write_state(launches=[*(state.get("launches") or []), {
                    "wave": wave, "stages": compiled.get("stages"), "subagent_run_id": end.get("subagent_run_id") or launched.get("subagent_run_id"),
                    "status_dir": end.get("status_dir") or launched.get("status_dir"), "status": value.get("status") or end.get("state"),
                    "cost_usd": cost}])
                if self.cancel_requested() or record.get("state") == "stopped":
                    return self.finish("stopped", record.get("error") or "canceled")
                if end.get("state") in ("complete", "completed") and value.get("status") == "done":
                    self.write_state(stages=mark_done(state, compiled.get("stages")))
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
        self.write_state(driver=cli.import_script("utils").this_process())  # who drives it: a later process can tell it died (state)
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
            if state_of(state) != "input_required":
                raise PrometheusError(f"run {self.id} is {state_of(state)}, not waiting for an answer")
            needs = state.get("needs") or {}
            asked = {d["stage"]: d for d in needs.get("decide") or []}
            if unknown := [s for s in decisions or {} if s not in asked]:
                raise PrometheusError(f"run {self.id} asks no decision of stage {', '.join(unknown)}; it asks: {', '.join(asked) or 'none'}")
            for stage, value in (decisions or {}).items():
                path = Path(asked[stage]["write_answer_to"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"stage": stage, "answer": value, "recorded_at": datetime.datetime.now(datetime.UTC).isoformat()}, indent=2),
                                encoding="utf-8")
            self.write_state(status="running", needs=None, preapproved=sorted({*(state.get("preapproved") or []), *approve}),
                             driver=cli.import_script("utils").this_process())
        self.log(event="resumed", approved=list(approve), decided=sorted(decisions or {}))
        self.go()

    def bundle(self, out: Path | str = ".", with_data: bool = False) -> Path:
        """The run's evidence as one file (run_bundle.py make), to fix the agent where it is developed: /agent-improve
        <file>. Without what it read unless `with_data`: its records, the children's sessions and output, its inputs,
        outputs and work, the caller's data. Refused while it runs."""
        made = run_script("run_bundle.py", "make", "--run", str(self.dir), "--out", str(out), *(["--with-data"] if with_data else []))
        if not made.get("ok"):
            raise PrometheusError(made.get("error") or "run_bundle.py failed", made)
        return Path(made["bundle"])

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
        """`folder`: the release unpacked (release_folder); `release`: its record."""
        self.folder = folder
        self.name = cli.import_script("utils").agent_name(folder)
        self.release = release
        self.version = release["version"]
        self.interface = release.get("interface") or {"params": {}, "outputs": {}, "side_effects": []}

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
        """The params an output's path names (where the run writes): for a server, always inside the run."""
        PARAM_RE = cli.import_script("run_compile").PARAM_RE
        plan = cli.import_script("utils").read_plan(self.folder, {}) or {}
        return {m for o in plan.get("outputs") or [] if isinstance(o, dict) for m in PARAM_RE.findall(str(o.get("path") or ""))}

    def output_file_name(self, name: str) -> str:
        """The file an output param names inside a run: its default's name (outputs/emails.xlsx -> emails.xlsx), else the
        param's name with the output's format as extension (report -> report.txt)."""
        PARAM_RE = cli.import_script("run_compile").PARAM_RE
        plan = cli.import_script("utils").read_plan(self.folder, {}) or {}
        default = cli.import_script("agent_params").spec_of((plan.get("params") or {}).get(name)).get("default")
        if default:
            return Path(str(default)).name
        fmt = next((o.get("format") for o in plan.get("outputs") or [] if isinstance(o, dict)
                    and name in PARAM_RE.findall(str(o.get("path") or "")) and o.get("format")), None)
        return f"{name}.{fmt}" if fmt else name

    @property
    def folder_params(self) -> set[str]:
        """The params naming a folder (an input or output of kind folder): over HTTP, several files under one name."""
        PARAM_RE = cli.import_script("run_compile").PARAM_RE
        plan = cli.import_script("utils").read_plan(self.folder, {}) or {}
        return {m for o in [*(plan.get("inputs") or []), *(plan.get("outputs") or [])]
                if isinstance(o, dict) and o.get("kind") == "folder" for m in PARAM_RE.findall(str(o.get("path") or ""))}

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
        """Each plan output's path with the run's values (agent_params.resolve, as the compiler does)."""
        agent_params = cli.import_script("agent_params")
        PARAM_RE = cli.import_script("run_compile").PARAM_RE
        read_plan = cli.import_script("utils").read_plan
        plan = read_plan(self.folder, {}) or {}
        specs = plan.get("params") or {}
        known = {**{n: agent_params.spec_of(s).get("default") for n, s in specs.items()}, **values}
        found = {}
        for o in plan.get("outputs") or []:
            if not isinstance(o, dict) or not o.get("id"):
                continue
            text, missing = str(o.get("path") or ""), False

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
        (run_dir / "work").mkdir(parents=True)  # the children's folder (run_steps.py start-run makes it in an agent)
        try:
            values = self.prepare_params(run_dir, params or {}, in_run)
            (run_dir / "call.json").write_text(json.dumps({"agent": self.name, "version": self.version, "params": values,
                                                           "approve": list(approve)}, indent=1), encoding="utf-8")
            run = Run(self, run_dir, values, self.resolve_outputs(run_dir, values))
            run.write_state(id=run_id, status="running", started_at=datetime.datetime.now(datetime.UTC).isoformat(),
                            preapproved=sorted(approve), stages={}, launches=[], driver=cli.import_script("utils").this_process())
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
    """An agent's release: `name` is a slug or a path to an agent, with an optional "@<version>" (v1: the newest 1.x;
    v1.2: exactly that); `version` says the same. With the agent's repository (the path, or the slug in the agents
    folder) its tags decide; without it, the releases installed here (`prometheus install <source>@v1`)."""
    if "@" in name and version is None:
        name, version = name.rsplit("@", 1)
    folder = Path(name).expanduser()
    if not (folder / "plan.yaml").is_file():
        folder = cli.get_setting("agents")[0] / name
    if (folder / "plan.yaml").is_file():
        if version is not None and not VERSION_RE.match(version):
            return load_build(folder.resolve(), version)
        listed = run_script("agent_release.py", "list", "--agent", str(folder.resolve()))
        release = pick_release(listed.get("releases") or [], version)
        return Agent(unpack(folder.resolve(), release), release)
    installed = list_installed(name)
    if not installed:
        raise PrometheusError(f"no agent {name!r}: not in {cli.get_setting('agents')[0]}, and no release of it installed in "
                              f"{get_releases()} (`prometheus install <source>@v1` installs one)")
    release = pick_release(installed, version)
    return Agent(release_folder(name, release), release)


def load_build(folder: Path, ref: str) -> Agent:
    """A commit of the agent that isn't released (a branch, a tag or a commit id), unpacked and run as a release would
    be: to check a fix before `prometheus release`. Only what is committed; its first compile refuses what can't run."""
    found = subprocess.run(["git", "-C", str(folder), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                           capture_output=True, text=True, check=False)
    if found.returncode != 0:
        raise PrometheusError(f"{folder.name} has no release or commit {ref!r}: a release is v<major>[.<minor>]; a branch, tag or "
                              "commit runs as a build")
    release = {"version": "build", "commit": found.stdout.strip()}
    agent = Agent(unpack(folder, release), release)
    agent.interface = cli.import_script("agent_release").interface(cli.import_script("utils").read_plan(agent.folder, {}) or {})
    return agent


def read_bundle_call(bundle: Path, agent: Agent) -> tuple[dict, set[str]]:
    """A bundled run's call (run_bundle.py, --with-data), for a new run of `agent`: (params, the params that named the
    run's outputs: placed in the new run). Not its approvals: the side effects ask again, since the new run has this
    machine's secrets. The bundle's files are unpacked once under the cache's bundles/."""
    try:
        with tarfile.open(bundle) as tar:
            info = json.load(tar.extractfile("bundle.json"))
            if info.get("agent") != agent.name:
                raise PrometheusError(f"{bundle.name} is a run of {info.get('agent')}, not {agent.name}")
            if info.get("kind") != "release":
                raise PrometheusError(f"{bundle.name} is a run of the agent itself, not of a release: only a release's run runs again")
            if not info.get("with_data"):
                raise PrometheusError(f"{bundle.name} was made without --with-data: it holds no params or files to run again")
            call = json.load(tar.extractfile("run/call.json"))
            files = get_cache() / "bundles" / bundle.name.removesuffix(".tgz")
            if not files.is_dir():
                staging = files.with_name(f".{files.name}.{secrets.token_hex(4)}")
                tar.extractall(staging, members=[m for m in tar.getmembers() if m.name.startswith("run/inputs/")], filter="data")
                try:
                    staging.rename(files)
                except OSError:  # another process unpacked it first
                    shutil.rmtree(staging, ignore_errors=True)
    except (OSError, KeyError, ValueError, tarfile.TarError) as exc:
        raise PrometheusError(f"cannot read {bundle}: {exc}") from None
    old = f"/runs/{info.get('run')}/"
    values, in_run = {}, set()
    for name, value in (call.get("params") or {}).items():
        text = str(value)
        if old + "outputs/" in text:
            in_run.add(name)
        elif old + "inputs/" in text:
            values[name] = str(files / "run" / "inputs" / text.split(old + "inputs/", 1)[1])
        else:
            values[name] = value
    return values, in_run
