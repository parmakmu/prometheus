# Troubleshooting

Most problems show up as a **refusal**: a script declines a step, says why, and leaves the agent as it was. Search this page for a few words of the message (`<…>` marks the parts that vary). Messages the agent recovers from by itself are listed at the end, under [Handled by the agent](#handled-by-the-agent). `/agent-status` shows where an agent stands and which moves are open.

## Setup

### Commands are missing

The `/agent-*` commands don't appear in Pi: Pi loads a project's extension only once you trust its folder. **Fix:** start with `prometheus` or `prometheus <slug>` and accept the trust prompt; each agent, and each copy made with `/agent-export`, is trusted on its own.

### prometheus is not set up

```text
prometheus is not set up: run `prometheus init` (or set PROMETHEUS_AGENTS)
```

`prometheus` started with no terminal to ask in before `prometheus init` ran. **Fix:** `prometheus init --yes` (`--agents <folder>` for another folder than `~/agents`), or set `PROMETHEUS_AGENTS`.

### No agent with that name

```text
no agent '<slug>' in <folder>: `prometheus list` lists the agents
'<slug>' is a prometheus command (`prometheus <slug>`), so it can't name an agent; pick another slug
```

`prometheus <word>` opens that agent, and there is none (a typo, another `PROMETHEUS_AGENTS`, a command your version lacks); and no agent may be named like a command. **Fix:** `prometheus list` or `prometheus --help`. The planner picks another slug by itself.

### A tool isn't installed

```text
pi is not installed: see `prometheus doctor`
pi-subagents is not installed (<file> is missing): pi install npm:pi-subagents
git is required
this wave runs stage programs or gates with `uv run`, but uv is not installed
```

**Fix:** install it: Pi (`npm install -g @earendil-works/pi-coding-agent`), pi-subagents (`pi install npm:pi-subagents`), git, [uv](https://docs.astral.sh/uv/) (its first run needs the network). `prometheus doctor` lists anything else.

### Python package missing

```text
Python package '<name>' is missing … ask the user to run `uv sync`
```

The Python the extension found (`$PI_AGENT_PYTHON`, else the nearest `.venv`) lacks the scripts' packages; in an agent, its first `uv sync` failed (no uv, or offline). **Fix:** for the planner, `uv tool install --reinstall git+<prometheus repository>` (or `uv sync` in a clone); for an agent, `uv sync` in its folder. Restart `pi`.

### The subagent tool is missing

The model says it has no `subagent` tool. **Fix:** install pi-subagents, or start with `/agent-new`, `/agent-run` or `/agent-revise`, which activate it (with some providers it appears after your next message).

### Every wave fails at startup

```text
Background children require the host npm package (@earendil-works/pi-coding-agent) with its dependencies; … does not provide …
```

Pi and pi-subagents don't match. **Fix:** update both (`npm install -g @earendil-works/pi-coding-agent`, `pi update`), check with `prometheus doctor`, then `/agent-run` relaunches the wave.

## Starting a run

### Branch not reviewed

```text
the plan on main, which a run starts from, can't run: …
```

A run starts from `main`, and a branch awaiting review is checked out while `main`'s plan isn't ready. **Fix:** `/agent-run` to merge, keep or discard the branch (*Review and merge* in the web app), or make `main`'s plan ready with `/agent-revise`.

### Plan not ready

```text
the plan is <status>, not ready: finish it first
the critic's result for this plan was not recorded with agent_record_critique, which checks the critic ran and ties its result to the plan
```

A run needs a plan that validates and that the critic reviewed; a status or critique written by hand doesn't count. **Fix:** `/agent-run` goes back to planning, or runs the critic and records it.

### A run is already active

```text
run <id> is active: continue it (a change to its plan is resolved on its branch: agent_resolve), or stop it first (agent_stop_run)
```

One run at a time; another run, `/agent-revise`, `/agent-improve` and reviews wait. **Fix:** `/agent-run` continues it; to change its plan, ask the run; to start over, tell the agent to stop it (it stays in `runs/`).

### Another branch is checked out

```text
<branch> is checked out, which is neither main nor a run or change branch: switch back with `git switch main` (or to the branch you were working on)
```

The agent works only on `main`, `run/…`, `revise/…` and `improve/…`. **Fix:** `git switch main` (or your branch); to keep that branch's work, merge it into a `/agent-revise` branch with git.

### A value given for each run

```text
'<name>' is given for each run: set it once a run has started
```

A `per_run` param belongs to one run and is never saved in `.env`. **Fix:** `/agent-run` asks for it, or takes what follows the command (`/agent-run what changed in Q3?`).

### Input path is relative or missing

```text
input '<id>' resolves to '<path>', a relative path
input '<id>' resolves to '<path>', which does not exist
```

**Fix:** give an absolute path that exists, when asked or in `.env`.

### No connector for a side effect

```text
stage '<key>': agent '<name>' cannot reach <target>
builtin agent '<name>' writes <target> but has no connector tools
```

A side effect needs a subagent that reaches its target: a connector (`mcp:<server>` in its `tools`) or a command-line tool via `bash`. Built-in agents have no connectors. **Fix:** [How to make a stage use a connector](how-to.md#how-to-make-a-stage-use-a-connector).

## During a run

### A wave is running

```text
a wave of run <id> is running: let it finish (or stop it in pi-subagents), then record it with agent_finish_wave
run <id> is still <state> in pi-subagents: wait for its completion notice
```

Until a launched wave is recorded, nothing else moves. **Fix:** wait for the completion notice; after a `pi` restart, `/agent-run` records the wave once it ended. If you know it ended (a crash, or you stopped it), tell the agent to record it as failed.

### Still running in pi-subagents

See [A wave is running](#a-wave-is-running).

### A wave failed

The agent offers to **relaunch** (completed, unchanged children are kept), **change the plan** or **stop**: [How to relaunch a wave that failed](how-to.md#how-to-relaunch-a-wave-that-failed). A program that ran past the limit fails with "no result within 600 s (limits.command_timeout_s)": raise the limit if it's slow; a relaunch lets its fix child repair it if it hangs.

### A map's results are missing items

```text
the merged result does not name every item of the split exactly once; missing: …; named more than once: …
```

A batch's child skipped, repeated or misnamed an item, so a relaunch fails the same way. **Fix:** make the stage's `instructions` say how each result names its item (the email's file name, say): ask the run, or `/agent-revise` after it.

### The subagent tool returned an error

The agent stops with an error from the `subagent` tool (a launch, an extension or a child failed to start) instead of doing the stage itself. **Fix:** fix the cause, then `/agent-run`. If `/agent-auto` says pi-subagents didn't answer, install or update it and restart Pi (the router, in the web app), or use `/agent-run`.

### The run's branch isn't checked out

```text
run <id>: <branch> is checked out with uncommitted changes (<files>), but the run works on run/<id>. Commit, stash or undo them there, or switch back with `git switch run/<id>` …
```

A branch was switched by hand during the run and changed there; the run stops rather than move or lose those edits. **Fix:** commit, stash or undo them, or `git switch run/<id>` to take them into the run; then tell the agent to go on.

### Staged index changed

```text
Staged index changed after launch
```

Files were staged (`git add`) in the agent while a child wrote. **Fix:** relaunch, and don't stage during a wave.

## Changing an agent

### Editing the plan was blocked

After a run, the plan changes only by resolving in a run or by a change; outside a `/agent-revise` branch the guard blocks any shell command naming `plan.yaml`, even a read. **Fix:** usually none (the agent reads it another way); to change it, ask the run or use `/agent-revise`.

### A plan edited by hand doesn't check

```text
stage '<id>': unknown field '<field>' (a stage's fields are in .pi/skills/agent/references/plan-schema.md)
stage '<id>': kind: Value error, kind is a mapping: {value: <kind>, reason: <why>}
```

**Fix:** use the fields in the agent's `references/plan-schema.md` (`kind: {value, reason}`, not one word), then `prometheus check <slug>`: [How to edit a plan by hand](how-to.md#how-to-edit-a-plan-by-hand).

### Locked while resolving

```text
resolving cannot change [<fields>] … locked
```

A run can't change its request, goal, `done_when`, params, inputs, outputs, agents, stages that ran, or approvals. **Fix:** `/agent-revise` after the run.

### main changes only by a merge

```text
main is checked out and no run is active: gates, scripts, agents and packages change only on a run or change branch
main changes only by a reviewed merge once a run exists: change the plan with /agent-revise
```

Usually a branch was switched during a change. **Fix:** `git switch revise/…` and tell the agent to continue, or start `/agent-revise`.

### Another change is open

```text
<revise/… or improve/…> is an open /agent-<kind> change: finish it first
```

**Fix:** `/agent-run` to merge, keep or discard the open branch, then run the other command.

### Nothing to improve from

```text
no run yet, so nothing to improve from
Nothing new since the last /agent-improve: no unreviewed sessions or runs.
```

**Fix:** run the agent, or give a focus (`/agent-improve <focus>`); before a first run, `/agent-revise`.

### Merge a plan that is not ready

```text
the plan on <branch> is draft … only a ready, valid plan can reach main
main has moved and <branch> conflicts with it
```

**Fix:** finish the change, resolve the conflict by hand, or keep or discard the branch.

### Merge conflicts with `main`

See [Merge a plan that is not ready](#merge-a-plan-that-is-not-ready). After a merge with git by hand, also `git tag r<N>` on `main` and delete the branch, which the review does for you.

## prometheus commands

### prometheus answered instead of planning

prometheus plans only on `/agent-new <request>`.

### Refresh during a run or review

```text
<agent> has an active run (<id>); refresh it once the run has finished and its branch is reviewed
<agent> has branch <name> checked out; refresh it after merging, keeping or discarding that branch
```

**Fix:** finish the run and review its branch, then refresh.

### Export destination exists

```text
<dest>/<slug> already exists
destination is inside the agent
```

**Fix:** pick another destination or name.

## Releases and released runs

### A release is refused

```text
can't release: <reasons>
this release can break callers (<changes>): release it with --breaking, so the major version goes up and pinned callers keep the old one
this commit is already released as <version>
```

A release needs no one at run time (no provisional stage, programs written), a clean `main` and a ready plan; one that drops or changes params, outputs or side effects needs `--breaking`. **Fix:** do what each reason says; `--breaking` if the break is intended. An existing release is already the one.

### No such agent, release or commit

```text
no agent '<slug>': not in <agents folder>, and no release of it installed in <releases folder> (`prometheus install <source>@v1` installs one)
the agent has no release yet: `prometheus release <slug>` makes one
no release <version>; releases: <versions>
<agent> has no release or commit '<ref>': a release is v<major>[.<minor>]; a branch, tag or commit runs as a build
```

**Fix:** on a runner, `prometheus install <git url>@v1`; where it's developed, check the slug, release it, or pick a listed version or a committed branch.

### A release's tag moved

```text
<slug> <version> here is commit <a>, but its tag now names <b>: a release never changes; release the change as a new version
```

**Fix:** move the tag back and release a new version (or delete that version's folder, runs included).

### A release can't run

```text
<agent> <version> can't run: missing params: <names>; missing secrets (environment variables): <names>
```

**Fix:** pass the params (`-p`, `params=`, the form) and set the variables.

### A run waits for an answer

The state is `input_required`: a side effect the call didn't approve, or a decision. **Fix:** answer with `prometheus run` (it asks), `run.answer(...)`, `POST /runs/<id>/answer`, or an A2A message with the `taskId`; or cancel. An answer to a run that isn't waiting gets `run <id> is <state>, not waiting for an answer`.

### A run stopped with its server

```text
the process running it stopped before it ended: start it again
```

**Fix:** start it again; finished model stages are reused.

### A released run failed

```text
stage <key> failed: see <run folder> and pi-subagents' record <folder>
```

**Fix:** retry with corrected input, or bundle it and fix it: [How to fix a released agent after a failed run](how-to.md#how-to-fix-a-released-agent-after-a-failed-run).

### A run can't be bundled

```text
no run '<run>' of a release of <slug> in <releases folder>
run <run> is still running: bundle it once it ended (or cancel it)
a wave of run <run> is running: bundle it once the wave ended
<agent> has no run to bundle yet
<folder> is not a run (no run.yaml)
```

**Fix:** bundle where the run ran (same `PROMETHEUS_RELEASES`), once it ended. `serve` answers 409.

### A bundle isn't imported

```text
cannot read <bundle>: <reason>
<bundle> is a run of <agent>, not <this agent>
<bundle> is a bundle of format <n>; this agent reads format <m>: refresh it
```

**Fix:** use the agent it came from, `prometheus refresh <slug>` for a newer format, or bundle again. A warning that the commit isn't on `main` means pull first.

### A bundle can't be run again

```text
<bundle> was made without --with-data: it holds no params or files to run again
<bundle> is a run of the agent itself, not of a release: only a release's run runs again
```

**Fix:** if you may see the caller's data, bundle again `--with-data`; otherwise `prometheus run <slug>@<branch> -p <name>=<value>`.

### Serving needs a token

```text
serving on <host> needs a token (--token or PROMETHEUS_SERVE_TOKEN): anyone who reaches it could run the agent
```

**Fix:** a long random `--token`, or serve on `127.0.0.1`.

## Calls between agents

### A called agent stopped to ask

```text
the agent at <url> asks before it goes on (task <id>): <what it asks>; nobody can answer inside a wave: approve its side effects in the stage's a2a.approve
```

**Fix:** add them to `a2a.approve` with `/agent-revise`, or ask its owner to make the decision a param.

### A called agent's link goes elsewhere

```text
the agent at <url> gave its endpoint on another address (<link>): not followed, so the token and the files go nowhere else
```

**Fix:** its owner serves with `--public-url`, or set `a2a.url` to the address its card names.

### A called agent failed

```text
the agent at <url> ended task <id> as failed: <its message>
<method> <url>: HTTP <code>: <message>
```

**Fix:** its message says which: a missing param, a bad token (401), an address that doesn't answer, or its own failure (ask its owner).

## Sharing agents

### Sharing refused: it may contain secrets

```text
not shared, it may contain secrets: <file>: looks like an API key or token; …
not shared, it may contain secrets: <file>: looks like an API key or token, in commit <commit>; …
```

**Fix:** move the value into a secret param, remove it from the history, rotate it if it was ever pushed.

### Install refused

```text
<folder> exists: `prometheus pull <slug>` updates it, or install with --as <another name>
<url> is not an agent: it has no plan.yaml
cannot clone <url>: <git's message>
'<slug>' can't name an agent: pass another with --as
```

**Fix:** as the message says.

### Pull or share refused: main isn't clean

```text
<slug> has <branch> checked out: <pull or share> works on main (merge, keep or discard it first)
<slug> has uncommitted changes: <pull or share> needs them committed or discarded
<slug> has an active run (<id>): <pull or share> after it ends
<slug>'s main has commits the upstream doesn't (besides refreshes): propose them …
```

**Fix:** finish the run and the open branch, commit or discard edits; local commits go on a branch to `prometheus propose`.

### Propose refused

```text
<slug> has <branch> checked out: propose a change branch (/agent-revise or /agent-improve)
<slug> has uncommitted changes: finish the change first
the change isn't ready to propose: <why>
<slug> has no upstream (origin): it was not installed or shared
```

**Fix:** finish a `/agent-revise` or `/agent-improve` change first; an agent you made needs `prometheus share`.

## Web app

| You see | Fix |
|---|---|
| "TRUST_USER_HEADER needs PROXY_SECRET" | Set `PROXY_SECRET` (16+ characters) and have the login proxy send it in `x-proxy-secret`. |
| "Tunnel connection failed: 403 Forbidden", "… is not allowed for this agent" | Allow the host under **Internet** in the agent's workspace, then relaunch; or list it in the plan's `network:`. |
| "the egress proxy didn't start" | Check `podman network ls` / `podman info`, or start with `EGRESS=off` (no allow-list). |
| "PODMAN_RUNTIME=… can't start a container" | Install a current [gVisor](https://gvisor.dev/docs/user_guide/install/), or drop `PODMAN_RUNTIME`. |
| API 409 "the agent's plan isn't ready to run" | Finish the plan in the agent's chat. |
| API 400 "params the agent doesn't take", "approve", "files" | Check the card (`GET /a/<agent>/api`); secrets are never sent, paths are relative without `..`. |
| API 403 "this key calls one agent's API only" | Use that agent's key, under `/a/<agent>/api`. |
| A call stays `queued` | Another run is active (`waiting_for`); it starts within 15 s after. |
| "the web app's server stopped while this call ran" | Open the call's session (*API*) and `/agent-run`, or stop it. |
| "Read-only file system" under `.pi/` or `.git/config` | Change the agent's own code from a terminal; `.pi/APPEND_SYSTEM.md` stays writable. |

## Handled by the agent

The agent knows the next step for these; nothing for you to do.

| Message | What happens |
|---|---|
| "secret '…' reaches only what the batcher runs" | The stage becomes a program that gets the secret as an argument. |
| "wave … is already recorded", "no wave of run … is running: there is nothing to record" | A late completion notice; the run carries on. |
| "plan.yaml changed after the critic's result was recorded" | The critic runs again before runs and merges. |
| reading `.env` blocked | Intended: the agent names the variable for you to set. |
| "wave … was already launched as pi-subagents run … and not recorded" | It records that launch instead of launching twice. |
| "wave … was compiled from commit …, but … is checked out now" | It compiles again, back on the run's branch (uncommitted files there: commit or stash, then `/agent-run`). |
| "run … is still … in pi-subagents, but its process … is gone" | Recorded as failed; relaunch it. |
| "cannot read pi-subagents' record of run …" | Recorded from the completion notice. |
| "no active run: start one with agent_start_run" | It edits the plan directly, or suggests `/agent-revise`. |
| "… imports …, which the agent's pyproject.toml does not list" | It adds the package with `agent_add_package`, asking you. |
| "pi-subagents has no record of a critic run …", "… not a completed plan-critic run" | It passes the right id, or runs the critic again. |

## For contributors

The messages come from the scripts in [`.pi/skills/agent/scripts/`](../.pi/skills/agent/scripts/) (`{"ok": false, "error": …}`, non-zero exit); [`run-protocol.md`](../.pi/skills/agent/references/run-protocol.md) and [`run-failures.md`](../.pi/skills/agent/references/run-failures.md) tell the agent how to react. A new refusal a person can meet gets a test that triggers it and an entry here; [Commands and tools](commands.md) links to its heading.
