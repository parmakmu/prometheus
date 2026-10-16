# Python API

This page is for developers who run a released agent from their own Python code. It lists everything the `prometheus` package exports, and what each part takes, returns and raises. For the steps from a release to a first call, see [How to run an agent from code](how-to.md#how-to-run-an-agent-from-code); for releasing an agent in the first place, [How to release an agent](how-to.md#how-to-release-an-agent).

```python
from prometheus import load_agent

agent = load_agent("emails-to-excel@v1")
result = agent.run(params={"email_folder": "mail/"}, approve=["build-workbook"])
if result.ok:
    print(result.outputs["email_workbook"])
```

The package exports five names: [`load_agent`](#load_agent), [`Agent`](#agent), [`Run`](#run), [`Result`](#result) and [`PrometheusError`](#prometheuserror).

## Installing it

The package is prometheus itself. Add it to your project like any git dependency:

```sh
uv add git+<prometheus repository>
```

It needs Python 3.14. A run also needs what any released run needs on the machine:

- Pi 1.3.0 or later and pi-subagents 0.78.0 or later, the same versions `prometheus doctor` checks;
- uv, since the agent's programs and gates run with `uv run`;
- a model login or key, if the release has stages that use a model;
- the agent's secrets in the environment, since a release has no `.env`.

`prometheus doctor <slug>@v1` checks all of this for one release.

Your code finds a release in one of two places. Where the agent is developed, it's in the agents folder, and its git tags say which releases exist. On a machine that only runs it, it's a release installed with `prometheus install <source>@v1` ([How to install a release on a machine that only runs it](how-to.md#how-to-install-a-release-on-a-machine-that-only-runs-it)).

## load_agent

Finds a release of an agent and unpacks it, ready to run.

```python
load_agent(name: str, version: str | None = None) -> Agent
```

- `name` is the agent's slug, or a path to its folder, optionally with `@<version>`: `"emails-to-excel@v1"`. `version` says the same, as a separate argument.
- `@v1` is the newest 1.x, `@v1.2` exactly that release, and no version the newest release of all. Pin the major version: a release that could break callers raises it, so `@v1` never picks one up.
- With the agent's repository at hand (the path, or the slug in the agents folder), its tags decide. Without it, the releases installed here do.
- With the repository, any other branch, tag or commit runs as a build: `"emails-to-excel@improve/2026-10-16-1"`. That is for checking a fix before releasing it. A build's `version` is `"build"`.

The first call for a release unpacks it into `~/.local/share/prometheus/releases/<slug>/<version>/` (`PROMETHEUS_RELEASES` moves it). Later calls reuse that folder, and every run of the release lives inside it.

**Raises** [`PrometheusError`](#prometheuserror) when there is no such agent here, no release of it, no release matching the version, or no such branch or commit. It also raises one when the release was unpacked here from one commit and its tag now names another, since a version never changes. The messages are in [Troubleshooting](troubleshooting.md#releases-and-released-runs).

## Agent

A release of an agent, unpacked and ready to run. You get one from [`load_agent`](#load_agent).

| Attribute | What it is |
|---|---|
| `name` | the agent's slug |
| `version` | the release, such as `"v1.1"`, or `"build"` |
| `folder` | the unpacked release, a `Path`; its runs are in `folder / "runs"` |
| `release` | the release's record: `version`, `commit`, and for a tagged release `revision`, `breaking` and `interface` |
| `interface` | what callers depend on: `params`, `outputs`, `side_effects`, `decisions` |
| `params` | `{name: {"type": "path" \| "text" \| "secret", "required": bool}}` |
| `outputs` | `{output id: {"kind": ..., "format": ...}}` |
| `side_effects` | the ids of the stages that change something outside the run, which a run performs only when approved |

### Agent.run

Starts a run and waits until it stops.

```python
agent.run(params: dict | None = None, approve: list[str] = (), timeout: float | None = None) -> Result
```

The same as `agent.start(params, approve).wait(timeout)`. A run stops when it ends, or when it waits for an answer (see [`Run.answer`](#runanswer)). Check `result.state`, not only `result.ok`.

### Agent.start

Starts a run and returns at once, while the run goes on in a thread of your process.

```python
agent.start(params: dict | None = None, approve: list[str] = ()) -> Run
```

- `params` gives each param a value. What a value can be:

  | Value | Becomes |
  |---|---|
  | a path (`str` or `Path`) | an absolute path; a relative one is relative to your program, not the run |
  | `bytes`, or an open file | a file in the run's `inputs/<param>` |
  | `(file name, bytes)` | `inputs/<param>/<file name>`, for programs that care about the extension |
  | `{file name: bytes, …}` | the folder `inputs/<param>/`, for a param that names a folder |
  | anything else | its text |

  A secret is never passed: a secret param reads its environment variable, under the name the plan gives it.
- `approve` lists the side-effect stages the run may perform. One left out stops the run before it, waiting for an answer.

**Raises** [`PrometheusError`](#prometheuserror) at once when the run can't start: a required param or a secret is missing, or Pi or pi-subagents isn't installed. Nothing is left behind then. `exc.details` holds the compile's result, with what is missing.

## Run

One run of a release. You get one from [`Agent.start`](#agentstart), or from [`Run.reopen`](#runreopen) for a run another process started.

| Attribute | What it is |
|---|---|
| `id` | the run's id, such as `"20261016-101010-abcd"` |
| `dir` | its folder: `call.json`, `state.json`, `events.jsonl`, `inputs/`, `outputs/`, `work/` |
| `state` | `"running"`, `"input_required"`, `"complete"`, `"failed"`, `"stopped"` or `"error"` |
| `running` | whether `state` is `"running"` |

A run whose driving process died, in a restart say, reads as `"error"` from then on, so it never seems to run forever.

### Run.wait

Waits until the run stops, and says how.

```python
run.wait(timeout: float | None = None) -> Result
```

**Raises** `TimeoutError` when the run takes longer than `timeout` seconds; the run is canceled first.

### Run.events

Each event as the run writes it, until the run stops.

```python
for event in run.events():
    print(event["event"], event)
```

Every event is a dict with `time` and `event`. The events:

| `event` | When | Also has |
|---|---|---|
| `launched` | a wave was handed to pi-subagents | `wave`, `subagent_run_id`, `status_dir` |
| `child` | a child changed state | `key`, `state`, `agent`, `child_run_id` |
| `wave_end` | a wave ended | `wave`, `state`, `subagent_run_id`, `status_dir`, `cost` |
| `input_required` | the run waits for an answer | `wave`, `needs` |
| `resumed` | an answer let it go on | `approved`, `decided` |
| `stopping` | a cancel reached the wave | |
| `error` | something in the runner failed | `message` |
| `end` | the run ended | `state`, `error`, `cost_usd` |

A run that waits for an answer stops the iteration. After [`Run.answer`](#runanswer), call `events()` again to follow it from there. Everything is also in `run.dir / "events.jsonl"`, one event per line.

### Run.answer

Gives a waiting run what it asked for, and lets it go on.

```python
run.answer(approve: list[str] = (), decisions: dict | None = None)
```

A run waits (`state == "input_required"`) at a side effect that wasn't approved up front, or at a decision. `result.needs` says what it needs (see [`Result`](#result)). `approve` approves side effects, and `decisions` gives `{stage: answer}`, any value that can be written as JSON. To decline, cancel the run instead.

**Raises** [`PrometheusError`](#prometheuserror) when the run isn't waiting for an answer, or when a decision names a stage it didn't ask about. Two answers at once can't both win: a file lock lets one through and refuses the other.

### Run.cancel

Stops the run.

```python
run.cancel(grace_s: float = 30)
```

A running wave is asked to stop, then killed after `grace_s` seconds. A run waiting for an answer just ends as `"stopped"`. Any process can cancel a run: the request is written to the run's folder, where its driver sees it.

### Run.bundle

Packs the run's evidence into one file, for whoever develops the agent.

```python
run.bundle(out: Path | str = ".", with_files: bool = False) -> Path
```

The bundle holds the run's records, pi-subagents' records of its launches, and each child's session and output. `with_files` adds the run's inputs, outputs and work folder, which are your data. The values of the agent's secrets are replaced by `${NAME}`. Where it goes next: [How to fix a released agent after a failed run](how-to.md#how-to-fix-a-released-agent-after-a-failed-run).

**Raises** [`PrometheusError`](#prometheuserror) while the run is still going.

### Run.reopen

A run another process started, from its folder.

```python
Run.reopen(agent: Agent, run_id: str) -> Run | None
```

Returns `None` when the release has no such run. Use it to answer, cancel, wait for or bundle a run from another process. Answering starts a new driver in your process, so the run goes on there.

## Result

How a run stopped: what [`Agent.run`](#agentrun) and [`Run.wait`](#runwait) return.

| Attribute | What it is |
|---|---|
| `ok` | `True` when the run completed |
| `state` | `"complete"`, `"failed"`, `"stopped"`, `"error"`, or `"input_required"` when it waits for an answer |
| `outputs` | `{output id: Path or None}`: where each of the plan's outputs is |
| `cost_usd` | what its children cost, summed over its launches; `None` before any launch |
| `error` | why it failed or stopped, else `None` |
| `needs` | when it waits: `{"approve": [...], "decide": [...]}` |
| `run_dir` | the run's folder |
| `record` | the run's `state.json`: its waves, each with pi-subagents' record folder |

In `needs`, each entry of `approve` has the side effect's `stage`, `title`, `why`, the paths it `writes`, and its `done_when`. Each entry of `decide` has the decision's `stage`, `title`, the lines to `show_user`, and `write_answer_to`, the file where `Run.answer` keeps your answer for the stages after it.

## PrometheusError

Something the caller must fix: no such agent or release, a missing tool, a refused step. `str(exc)` says what and usually how; `exc.details` holds the script's full result when there is one. Every message is in [Troubleshooting](troubleshooting.md#releases-and-released-runs).

A run that fails doesn't raise: it ends with `result.state == "failed"` and `result.error`.

## Example: decide later, then bundle a failure

```python
from prometheus import load_agent

agent = load_agent("emails-to-excel@v1")
run = agent.start(params={"email_folder": "mail/"})       # build-workbook isn't approved up front
result = run.wait()
if result.state == "input_required":
    for side_effect in result.needs["approve"]:
        print(side_effect["stage"], "writes", side_effect["writes"])
    run.answer(approve=["build-workbook"])
    result = run.wait()
if result.state == "failed":
    print(result.error, run.bundle("bundles/"))
```
