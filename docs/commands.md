# Commands and tools

**Commands** start with `/`, and you type them in Pi. To run a released agent from Python instead, see the [Python API](python-api.md). prometheus's commands run in the planner (`prometheus`); the others run in an agent (`prometheus <slug>`). The [`prometheus` command](#the-prometheus-command) itself runs in your shell.

| Command | Runs in | Does |
| --- | --- | --- |
| [`/agent-new`](#agent-new) | prometheus | plan a new agent for a request |
| [`/agent-list`](#agent-list) | prometheus | list agents and where each stands |
| [`/agent-export`](#agent-export) | prometheus | copy an agent somewhere else to run it |
| [`/agent-refresh`](#agent-refresh) | prometheus | update agents to this prometheus's template and skill |
| [`/agent-run`](#agent-run) | Agent | plan, continue or run, from wherever it stands |
| [`/agent-auto`](#agent-auto) | Agent | run the next waves with no model, until one needs you |
| [`/agent-status`](#agent-status) | Agent | show where it stands; changes nothing |
| [`/agent-history`](#agent-history) | Agent | the last runs, and how each stage went |
| [`/agent-prune`](#agent-prune) | Agent | delete old runs' work folders |
| [`/agent-bundle`](#agent-bundle) | Agent | pack a run's evidence into one file, for whoever develops the agent |
| [`/agent-revise`](#agent-revise) | Agent | change the plan on a branch you review |
| [`/agent-improve`](#agent-improve) | Agent | propose fixes from past sessions and runs, and released runs' bundles |

Commands are prefixed `agent-` because Pi has built-in `/new` and `/export`.

**Tools** have no slash. The model calls them to carry out the mechanical steps of planning and running; you never call them, but you see their results. They are listed [after the commands](#tools-the-model-calls).

Inside an agent, each entry names the [states](state-machine.md) it is accepted in. In any other state it is refused, with a message that says why; the refusals are in [Troubleshooting](troubleshooting.md).

## The prometheus command

`prometheus` is what you run in your shell: it starts the planner or an agent, and it does the jobs that don't need a chat, such as releasing and sharing agents. It's installed with `uv tool install git+<prometheus repository>`, and `prometheus upgrade` (or `uv tool upgrade prometheus`) updates it.

### Everyday use

| Command | Does |
| --- | --- |
| `prometheus [pi options]` | Starts the planner: `pi` in the planner's folder, with prometheus's extension, skill and role. Options such as `--resume` go to `pi`. |
| `prometheus <slug> [pi options]` | Starts that agent: `pi` in `<slug>/` in the agents folder. |
| `prometheus help` | The same as `prometheus --help`: the commands. For one command's arguments, `prometheus <command> --help`. |
| `prometheus init [--agents <folder>] [--home <folder>] [--yes]` | Chooses the agents folder and the planner's folder, and saves them. It asks only in a terminal, and never with `--yes`. Then it runs the checks `doctor` runs. |
| `prometheus doctor [<slug>[@v]]` | Checks that Pi (1.3.0 or later), pi-subagents (0.79.0 or later), Node, uv, a git identity and the agents folder are there, with the fix for each one that isn't. Given an agent, it also checks that its release can run on this machine: each secret is set in the environment, its packages install from its `uv.lock` (a dry run), and which stages need a model, and which model. A machine that only runs releases needs no git identity, so the git check is left out whenever an agent is given. Exits 1 if something is missing. |
| `prometheus list` | Lists the agents and where each one stands, and the releases installed here, with their versions and runs. |
| `prometheus check <slug>` | Checks the agent's `plan.yaml`, for example after you edited it by hand: its errors and warnings, and the waves it would run. Exits 1 if it isn't valid. |
| `prometheus refresh <slug \| --all>` | Does what `/agent-refresh` does, without starting Pi. |
| `prometheus prune <slug> [--keep N] [--keep-failed N] [--dry-run]` | Deletes the files of old finished runs (an agent's runs lose `work/`; a release's runs also their `inputs/` and `outputs/`; and pi-subagents' records, the children's sessions and outputs, and mission files none of whose launches is kept), in the agent and in each of its releases, keeping the newest N (default 10). A release's failed runs are counted apart: the newest `--keep-failed` (default 10) stay whole too, so they can still be bundled. Their history stays. |
| `prometheus upgrade` | Updates prometheus itself (`uv tool upgrade prometheus`). |

Each folder comes from the first of these that is set: the flag, the environment (`PROMETHEUS_AGENTS`, `PROMETHEUS_HOME`), `~/.config/prometheus/config.toml`, and finally the defaults `~/agents` and `~/.prometheus`. No agent can be named like one of the command's own words (`init`, `doctor`, `list`, `install`, `run` and a few more kept for later), since `prometheus <slug>` would then be ambiguous.

### Releases

A release is a version of an agent that code can run with nobody deciding anything. [How to release an agent](how-to.md#how-to-release-an-agent) walks through it.

| Command | Does |
| --- | --- |
| `prometheus release <slug> [--breaking] [--yes]` | Shows whether the agent's current commit on `main` can run as code, its interface (params, outputs, side effects, decisions), its model stages, and what changed since the last release. Then it tags the commit `v<major>.<minor>`, asking first unless `--yes`. A change that can break callers needs `--breaking`, which raises the major version. |
| `prometheus run <slug>[@<version or ref>] [options]` | Runs a release, described [below](#prometheus-run). |
| `prometheus runs <slug> [<run>] [--json]` | Lists the runs of the agent's releases on this machine: id, version, state, start, cost and error. Given a run, it shows how that run went: each wave, the children that failed with their error and output, and its last events. It changes nothing; use it to find the run to bundle. |
| `prometheus bundle <slug> <run> [--with-data] [--out <folder>]` | Packs a released run into `<slug>-<version>-<run>.tgz`. The run is one that `prometheus run`, `load_agent` or `serve` started, found in the releases folder. By default the bundle says what the run did, without what it read: how each stage ended, each child's error, and its tool calls with the results of those that failed. Errors and failed results can still quote what the run read, so param values in them are replaced by `<name>` and they're listed for you to check. `--with-data` adds the run as it is: its records, pi-subagents' records of its launches, each child's session and output, and the run's inputs, outputs and work folder, which are the caller's data. Secret values are replaced by `${NAME}` either way, and anything else that looks like a key is reported. A running run is refused. From Python, `run.bundle()` does the same. Where the bundle goes next: [How to fix a released agent after a failed run](how-to.md#how-to-fix-a-released-agent-after-a-failed-run). |
| `prometheus serve <slug>[@v] [options]` | Serves one release over HTTP and A2A, described [below](#prometheus-serve). |

### Sharing

Agents are shared through plain git, so any git host works. The how-to guides start at [How to share an agent with your team](how-to.md#how-to-share-an-agent-with-your-team).

| Command | Does |
| --- | --- |
| `prometheus share <slug> <git url> [--yes]` | Refuses files that look like secrets (keys, tokens, a `.env` in git), shows what goes out (the agent's own code, its packages and the lessons from its runs), then pushes `main` and its releases. On GitLab, the push can create a new project, if your instance allows it. |
| `prometheus install <git url, folder or catalog name>@<v1 or v1.2> [--as <slug>] [--yes]` | Installs one release, frozen, for a machine that only runs it: fetches only that tag (the newest 1.x for `@v1`), unpacks it into `~/.local/share/prometheus/releases/<slug>/<version>/` (`PROMETHEUS_RELEASES`), and shows the agent's own code, packages and lessons before keeping it. No repository is kept and nothing is refreshed. Installing an installed version again changes nothing; a tag that has moved since is refused. |
| `prometheus install <git url, folder or catalog name> [--as <slug>] [--yes]` | Clones the agent and refreshes it from your prometheus, so its extension, skill copy and template files are your own trusted copies. Then it shows the agent's own code (gates, programs, subagent profiles), packages and lessons, and keeps it only if you say so (`--yes` keeps it without asking). |
| `prometheus pull <slug> [--yes]` | Shows the upstream's new commits and which of the agent's own files they change, then moves `main` to them and refreshes again. Refused when `main` has local commits other than refreshes. |
| `prometheus propose <slug>` | Pushes the checked-out change branch (from `/agent-revise` or `/agent-improve`, ready to merge) to the upstream. On GitLab, it opens a merge request into `main`. |
| `prometheus catalog` | Lists the agents in prometheus's `catalog.yaml`, which `install` accepts by name (`PROMETHEUS_CATALOG` names another file). |

### prometheus run

```text
prometheus run <slug>[@v1 | @v1.2 | @<branch or commit>] [-p name=value]… [-a stage]… [--bundle <file>] [--json]
```

Runs a release. It comes from the agent's repository when that is in the agents folder (its tags decide), else from the releases installed here. After `@`, a branch, tag or commit that isn't a release runs that commit's committed state as a build, the same way.

| Option | Does |
| --- | --- |
| `-p name=value` | sets a param. Not a secret: secrets come from the environment. |
| `-a stage` | approves a side effect up front. |
| `--bundle <file>` | runs again what a released run bundled `--with-data` was given: its params and files. Not its approvals: its side effects ask again, since this machine's secrets may reach real systems. `-p` overrides a param. |
| `--json` | prints events and the result as JSON lines. |

A side effect that isn't approved, or a decision, stops the run there. In a terminal it shows what the stage would write and asks; otherwise it exits saying what to approve. It prints each stage as it goes and the outputs at the end, and exits 0 when the run completed.

### prometheus serve

```text
prometheus serve <slug>[@v] [--host] [--port] [--token] [--public-url] [--max-runs] [--max-upload-mb] [--keep N]
```

Serves one release over HTTP, one agent per process. A token (`--token` or `PROMETHEUS_SERVE_TOKEN`) is required unless it listens on loopback only. Behind a proxy, `--public-url` is the address callers use. `--keep N` prunes finished runs beyond the newest N after each run, keeping the newest 10 failed ones whole on top.

Callers never send paths. Inputs arrive as uploaded files, and outputs stay in the run until they are downloaded. Each run lives in the release's folder (`PROMETHEUS_RELEASES`), so a restarted server still answers for earlier runs, and a call that repeats an earlier call's judgment on the same files reuses it. With `--keep`, outputs nobody downloaded before their run is pruned are gone, but the run's history stays. The server logs one JSON line per request and run to stdout.

| Endpoint | Does |
| --- | --- |
| `GET /` | describes the agent |
| `POST /runs` | starts a run, from a form generated from the agent's interface: a file per path param (several for a folder), text fields, and `approve` once per side effect. With `?wait=true` it answers once the run stops. |
| `GET /runs/<id>` | the run's state; one waiting for an answer is `input_required`, with what it needs |
| `POST /runs/<id>/answer` | answers it: `{"approve": [...], "decisions": {...}}` |
| `GET /runs/<id>/events` | the run's events, as server-sent events |
| `GET /runs/<id>/outputs/<o>` | downloads an output |
| `GET /runs/<id>/bundle` | downloads an ended run's bundle, as `prometheus bundle` makes it; `?with_data=true` adds the run's data |
| `DELETE /runs/<id>` | cancels the run |
| `GET /health`, `GET /docs` | a health check (no token needed), and the interactive API docs |

It also speaks [A2A](https://a2a-protocol.org) 1.0 (HTTP+JSON binding) on the same runs: the Agent Card at `/.well-known/agent-card.json`, `POST /message:send` and `/message:stream`, and `/tasks…`.

- A caller reads the Agent Card first. It needs no token, and carries the release's interface.
- A message has a file part for each input file (`raw`, base64, and `filename`; `metadata.param` names the param when the agent takes several files) and a data part such as `{"params": {...}, "approve": ["build-workbook"]}`. URL parts are refused.
- The answer is a task whose artifacts link to the outputs. A small text output (up to 64 KB, such as an answer or a summary) also comes inline, as a text part before its link, so the caller needs no second request. A task that waits for an answer is `TASK_STATE_INPUT_REQUIRED`, and a message with its `taskId` and a data part `{"approve": [...], "decisions": {...}}` answers it.

An agent built with prometheus calls another one with a stage of `pattern: a2a`.

## Environment variables

Set these in your shell, or in whatever starts prometheus or an agent, such as a scheduled job or a service's unit file. None of them is read from an agent's `.env`, which holds only the agent's params and is never loaded into Pi's environment.

The folders can also be set in `~/.config/prometheus/config.toml` (which `prometheus init` writes), by their name without the prefix, in lower case: `agents`, `home`, `releases`, `cache` and `catalog` (not `PI_CODING_AGENT_DIR`, which is Pi's own and read only from the environment). A flag wins over the environment, and the environment over `config.toml`.

| Variable | Read by | Does | Default |
| --- | --- | --- | --- |
| `PROMETHEUS_AGENTS` | `prometheus`, the planner | the agents folder; wins over `config.toml` | `~/agents` |
| `PROMETHEUS_HOME` | `prometheus` | the planner's folder; wins over `config.toml` | `~/.prometheus` |
| `PROMETHEUS_RELEASES` | `run`, `install`, `serve`, `load_agent` | where releases are unpacked and run. It holds their runs, so it's kept, not a cache | `$XDG_DATA_HOME/prometheus/releases` (`~/.local/share/…`) |
| `PROMETHEUS_CACHE` | `run --bundle` | where a bundle's files are unpacked to rerun it; safe to delete | `$XDG_CACHE_HOME/prometheus` (`~/.cache/…`) |
| `PROMETHEUS_CATALOG` | `catalog`, `install` | the catalog file that names installable agents | prometheus's own `catalog.yaml` |
| `PROMETHEUS_SERVE_TOKEN` | `serve` | the token callers send (`--token`) | none: required unless it listens on loopback only |
| `PROMETHEUS_PUBLIC_URL` | `serve` | the address callers use behind a proxy (`--public-url`), named in the Agent Card and output links | `http://<host>:<port>` |
| `AGENT_NOTIFY_COMMAND` | an agent | a command run with a message as its last argument whenever a run stops for you (see [/agent-auto](#agent-auto)) | none: nobody is told |
| `PI_CODING_AGENT_DIR` | `doctor`, `run`, `serve`, `load_agent` | Pi's global folder, where pi-subagents is installed (Pi's own variable) | `~/.pi/agent` |
| `PI_AGENT_PYTHON` | the extensions | the Python that runs the skill's scripts; `prometheus` sets it for the planner | the nearest `.venv`, else `python3` |
| `XDG_CONFIG_HOME` | `prometheus` | where `prometheus/config.toml` is | `~/.config` |

A released agent's secret params are environment variables too, named by the plan: see [Params and secrets](params-and-secrets.md). The web app's settings are listed in [`ui/README.md`](../ui/README.md).

## Planner commands

### /agent-new

Plans a new agent for a request.

```text
/agent-new [--from <agent>] <request>
```

With `--from`, the new agent starts from another one: its plan as a draft for the new request, its gates, stage programs, subagents and packages. Planning keeps what fits and changes the rest; the critic still reviews the whole plan.

**Runs in:** prometheus

Creates `<slug>/` in the agents folder, then plans: it drafts the plan, checks it, has the plan critic review it, and asks only the questions whose answers change the plan. It never asks for param values (they belong to the machine; `/agent-run` asks for them) and never does the requested work.

This is the only way to create an agent. A request typed as a plain message gets an answer, or a suggestion to use `/agent-new`, never a new agent. Each `/agent-new` creates one agent.

#### Example

```text
/agent-new I want to read the emails in a folder and save them in an Excel file
```

**See also:** [Tutorial](tutorial.md) · [Planning](how-it-works.md#planning)

### /agent-list

Lists the agents and where each one stands.

```text
/agent-list [slug]
```

**Runs in:** prometheus · changes nothing

Without a slug: a table of each agent's state (status and revision, the active run and its stages done, open questions), runs, last run, and whether its copy of the skill is current or needs a `/agent-refresh`. With a slug, as a list: that agent's plan validity, waves, open questions, approvals, missing params and secrets, and whether its waves compile.

**See also:** [`/agent-status`](#agent-status) · [`/agent-refresh`](#agent-refresh)

### /agent-export

Copies an agent somewhere else, to run it there.

```text
/agent-export <slug> <destination> [--keep-state]
```

**Runs in:** prometheus

By default the copy has the plan, gates, stage programs and subagents, without earlier runs, conversations (`.pi/sessions`) or git history. `--keep-state` carries them too. Neither copies `.env`: its values are per machine and may hold secrets. The copy gets a `.env` that lists each variable, unset.

To run the copy, the destination machine needs Pi, pi-subagents and uv, and you trust the copied folder in Pi.

#### Example

```text
/agent-export emails-to-excel ~/handover
```

This writes the copy to `~/handover/emails-to-excel/`: the destination is a folder the copy goes into, named after the agent.

**Refusals:** [destination exists, or is inside the agent](troubleshooting.md#export-destination-exists)

### /agent-refresh

Updates agents to this prometheus's template and skill.

```text
/agent-refresh <slug | --all>
```

**Runs in:** prometheus

Rewrites the files an agent got from prometheus: its extension, the skill and its references, the shared subagents. It keeps the plan, `.env`, runs, gates, stage programs, the agent's own agents and packages, and the `## Lessons for this agent` section of `.pi/APPEND_SYSTEM.md`. It writes the version to `.pi/skills/agent/VERSION` and names it in the commit: `Refresh from prometheus 0.1.0+64ab6bc`, with `(was <version>)` added when the agent's previous version is known.

`--all` skips agents already at this version; an agent named on the command line is always refreshed.

**Refusals:** [a run is active, or a branch is checked out](troubleshooting.md#refresh-during-a-run-or-review)

## Agent commands

Start Pi in the agent's folder for these.

### /agent-run

Plans, continues or runs the agent, from wherever it stands.

```text
/agent-run [note]
```

**Runs in:** an agent · **States:** all

It checks where the agent stands, then does what that state needs: finishes planning a draft plan, reviews a branch an earlier run left, continues the active run, or asks whether to start a new one. In a run it asks for missing param values, collects decisions and approvals, and launches each wave. The note is attached to a newly started run.

It activates pi-subagents' `subagent` tool first, so the model doesn't spend a turn enabling it.

#### Example

```text
/agent-run the March export
```

**See also:** [Running](how-it-works.md#running) · [Params, `.env` and secrets](params-and-secrets.md)

### /agent-auto

Runs the next waves without the model.

```text
/agent-auto
```

**Runs in:** an agent · **States:** `plan_ready`, `main_ready`, `run_review`, `change_ready` (it starts a run), `run_ready`

It compiles a wave, launches it through pi-subagents' in-process RPC, waits for it, records it, and goes on to the next, all in code. It hands over to the model only where a person or judgment is needed: an approval, a decision, a review, a missing value, the run's budget, a failed wave, resolving provisional stages, a child's question, or the final report (a short prompt, without the run protocol). The model's turns are most of a run's cost once its programs run as commands, so a run that needs nothing from you costs little more than its children.

The chat shows one line as each wave starts (its stages) and one as it ends (how long it took, its children's cost, the next wave). It also runs without a terminal: `pi -p "/agent-auto"` in the agent's folder. With `AGENT_NOTIFY_COMMAND` set (for example `notify-send prometheus`, or a `curl` to a webhook), each hand-over, and each wave that waits for you during `/agent-run`, runs that command with the message as its last argument.

Launching waves through the RPC has been seen in live runs; the hand-over to the model has not yet.

### /agent-history

Shows the last runs and how each stage went in them.

```text
/agent-history [n]
```

**Runs in:** an agent · **States:** all · changes nothing

Per run: its status, revision and children's cost; per stage, how many times it completed, failed or was reused. A stage that keeps failing, or keeps costing, stands out. Default: the last 10 runs.

### /agent-prune

Deletes the work folders of old finished runs.

```text
/agent-prune [keep]
```

**Runs in:** an agent · **States:** all

Keeps the newest `keep` finished runs whole (default 10). For older ones it deletes `work/` and pi-subagents' records of their launches, after you confirm. It also removes the empty folder pi-subagents leaves in `.pi/sessions/` for each launch. Their `run.yaml` and compiled waves stay, so `/agent-history` still shows them; a later run no longer reuses their results. The active run is never touched.

### /agent-bundle

Packs a run's evidence into one file, for whoever develops the agent.

```text
/agent-bundle [run id] [--with-data]
```

**Runs in:** an agent · **States:** all · changes nothing but the file it writes

For an agent that runs on a machine without prometheus, copied there with `/agent-export` or cloned. It needs only the agent's own skill copy. Without a run id it takes the active run, else the last one, and writes `runs/<slug>-r<revision>-<run>.tgz`. By default the bundle says what the run did, without what it read: how each stage ended, each child's error, and its tool calls with the results of those that failed. Param values in those texts are replaced by `<name>`, and the texts are listed for you to check. `--with-data` adds the run as it is: its `run.yaml` and compiled waves, pi-subagents' records of its launches with each child's session, input, output and transcript, the run's `work/` folder, and the agent's own sessions during the run. The values of the agent's secrets (from `.env` or the environment) are replaced by `${NAME}` either way, and anything else that looks like a key is listed for you to check before you send it. A wave that is still running is refused.

Where the agent is developed, `/agent-improve <file>` reads it: [How to improve an agent from a run on another machine](how-to.md#how-to-improve-an-agent-from-a-run-on-another-machine). A release's run is bundled with [`prometheus bundle`](#releases) instead.

**Refusals:** [a run that can't be bundled](troubleshooting.md#a-run-cant-be-bundled)

### /agent-status

Shows where the agent stands.

```text
/agent-status
```

**Runs in:** an agent · **States:** all · changes nothing

Shows three things. **Next**: how far the active run is and its next wave, or the waves a new run would do. **Waiting for you**: open questions, decisions, approvals, a review, the budget, branches waiting for review, missing params and secrets. **Problem**: plan errors, waves that don't compile, and a branch checked out by hand during a run, with what to do ([The run's branch isn't checked out](troubleshooting.md#the-runs-branch-isnt-checked-out)); it doesn't switch back itself. A running wave's live status is checked by the next `/agent-run`.

### /agent-revise

Changes the agent's plan, without running it.

```text
/agent-revise [change]
```

**Runs in:** an agent · **States:** `plan_draft`, `plan_ready`, `main_ready`, `change_draft`, `change_ready`, `main_draft`

Once a run exists, it opens a `revise/<date>-<n>` branch (or continues the open one) and makes the change there. The plan is left as it is until edited; an edited `plan.yaml` needs the plan critic again before it can merge, while gates, programs and agents alone don't. At the end you see the diff and choose merge, keep or discard. Before the first run, it edits the plan directly on `main`, as planning does.

Without a change, it asks what to change.

#### Example

```text
/agent-revise drop the discover stage, the mailbox format is stable now
```

**Refusals:** [a run is active](troubleshooting.md#a-run-is-already-active) · [an improve is open](troubleshooting.md#another-change-is-open) · [a branch is not reviewed](troubleshooting.md#branch-not-reviewed) · [another branch is checked out](troubleshooting.md#another-branch-is-checked-out)

**See also:** [`/agent-improve`](#agent-improve) · [Branches and review](how-it-works.md#reviewing-what-a-run-changed)

### /agent-improve

Proposes fixes from how the agent's recent sessions and runs went, and applies the ones you accept on a branch you review.

```text
/agent-improve [bundle.tgz …] [focus]
```

**Runs in:** an agent · **States:** `main_ready`, `change_draft`, `change_ready`, `main_draft`

It reads a digest of what is new since the last improve: failed or blocked tool results, repeated calls, cost, failed or relaunched waves. Each word that names a bundle file (`.tgz`, from [`prometheus bundle`](#releases) or [`/agent-bundle`](#agent-bundle)) is unpacked into `runs/imported/` first, so a run that failed elsewhere is evidence too: what failed, each failed child's error, output and session, and for a copy's own run its sessions there. An imported run is never one of the agent's runs, so nothing is reused from it. The rest of the words are the focus. From that it proposes fixes to the plan, gates, stage programs, subagents and packages, each with its evidence, and asks you about each one. Accepted ones are applied on an `improve/<date>-<n>` branch, which you review like a revise.

- It can add short rules for this agent under `## Lessons for this agent` at the end of `.pi/APPEND_SYSTEM.md`. `/agent-refresh` keeps that section.
- It doesn't edit the skill or its references. A problem there is listed as text for you to take to prometheus.
- Every proposal, applied or rejected, is recorded in `improvements.yaml`. The next improve reads only sessions and runs newer than that, and doesn't propose a rejected fix again.
- With nothing new and no focus given, it says so and opens no branch.

Nothing runs it automatically: only you type it.

#### Example

```text
/agent-improve the approval question is hard to answer
```

**Refusals:** [no run yet](troubleshooting.md#nothing-to-improve-from-yet) · [nothing new](troubleshooting.md#nothing-new-to-improve-from) · [a run is active](troubleshooting.md#a-run-is-already-active) · [a revise is open](troubleshooting.md#another-change-is-open) · [another branch is checked out](troubleshooting.md#another-branch-is-checked-out) · [a bundle it can't import](troubleshooting.md#a-bundle-isnt-imported)

**See also:** [`/agent-revise`](#agent-revise) · [`agent_sessions`](#agent_sessions)

## Tools the model calls

You don't call these. They are here so you can read a tool result or a refusal the agent shows you.

| Tool | Runs in | Does |
| --- | --- | --- |
| [`agent_scaffold`](#agent_scaffold) | prometheus | create the folder for the agent being planned |
| [`agent_check (prometheus)`](#agent_check-prometheus) | prometheus | check an agent's plan from prometheus |
| [`agent_record_critique (prometheus)`](#agent_record_critique-prometheus) | prometheus | record the plan critic's result for a new agent: what makes its plan ready |
| [`agent_add_package`](#agent_add_package) | both | add a Python package, after you approve it and its reason |
| [`agent_check`](#agent_check) | Agent | check the plan and say where the agent stands |
| [`agent_start_run`](#agent_start_run) | Agent | start a run |
| [`agent_compile_wave`](#agent_compile_wave) | Agent | turn the next wave into a pi-subagents call, or say what must happen first |
| [`agent_start_wave`](#agent_start_wave) | Agent | record that a wave was launched |
| [`agent_finish_wave`](#agent_finish_wave) | Agent | record how a wave ended |
| [`agent_record_decision`](#agent_record_decision) | Agent | record your answer for a decision stage |
| [`agent_resolve`](#agent_resolve) | Agent | resolve provisional stages during a run, or change pending ones you ask about |
| [`agent_record_critique`](#agent_record_critique) | Agent | record the plan critic's result |
| [`agent_stop_run`](#agent_stop_run) | Agent | stop the active run |
| [`agent_merge`](#agent_merge) | Agent | end the review of a run or change branch |
| [`agent_set_params`](#agent_set_params) | Agent | set param values in `.env` |
| [`agent_sessions`](#agent_sessions) | Agent | read the agent's earlier Pi sessions |

## Planner tools

### agent_scaffold

Creates the folder for the agent being planned.

```text
agent_scaffold({ slug, request, from? })
```

**Called during:** `/agent-new`

Creates `agents/<slug>/` from the agent template: a draft plan holding the request, the agent's extension, a copy of the skill, its agents and its own git repository. It refuses unless `/agent-new` ran in this session, and allows one agent per `/agent-new`. Running `agent_scaffold.py` from the shell is blocked, so the tool can't be bypassed. With `from` (only when `/agent-new --from <agent>` named one), the new agent starts from that agent's plan, as a draft, and its gates, programs, subagents and packages.

### agent_check (prometheus)

Checks an agent's plan from prometheus.

```text
agent_check({ slug })
```

**Called during:** planning · changes nothing

Validates the plan and compile-checks each concrete pending wave. Returns errors, warnings and a summary. It never launches a wave.

### agent_record_critique (prometheus)

Records the plan critic's result for a new agent's plan, as [`agent_record_critique`](#agent_record_critique) does inside an agent.

```text
agent_record_critique({ slug, run_id, verdict, passes?, findings })
```

**Called during:** planning

The only way a plan that needs the critic (more than three stages, a judgment stage or a side effect) becomes ready: it checks the critic's run completed, and stamps the plan with a fingerprint of itself (`critique.plan_digest`). A critique or `status: ready` written by hand, or an edit after the record, leaves the plan not ready. After the user's answers are written into the plan, the planner records the same critic run again.

## Agent tools

The model calls these while it follows the run protocol ([`run-protocol.md`](../.pi/skills/agent/references/run-protocol.md)). Every result except `agent_sessions`' carries `next` (`agent_check` names it `state`): the state the agent is in now, the moves open from it and a one-line hint, so the model never has to work out where it stands.

### agent_check

Checks the plan, and says where the agent stands.

```text
agent_check({})
```

**States:** all · changes nothing

Validates the plan and compile-checks its concrete pending waves. Returns errors, warnings, a summary (revision, waves, open questions, approvals, `last_run`) and `state`. During a run, `state.last_launch` gives the latest launch's wave, status and pi-subagents run id.

### agent_start_run

Starts a run.

```text
agent_start_run({ note? })
```

**States:** `plan_ready`, `main_ready`, `run_review`, `change_draft`, `change_ready` → `run_ready`

Creates `runs/<id>/` (`<date>-<n>`) as the active run. A run always starts from `main`: with a run or change branch checked out, it commits that branch's leftovers to it, switches to `main` and keeps the branch for review (`left_branch` in the result). Commits leftover changes: before the first run on `main`, since that is planning; after it on the run's branch `run/<id>`.

**Refusals:** [a run is active](troubleshooting.md#a-run-is-already-active) · [main's plan is not ready while a branch is checked out](troubleshooting.md#branch-not-reviewed) · [the plan is not ready](troubleshooting.md#plan-not-ready) · [another branch is checked out](troubleshooting.md#another-branch-is-checked-out)

### agent_compile_wave

Turns the next wave into the exact pi-subagents call, or says what must happen first.

```text
agent_compile_wave({ approve?: [stage, …], reviewed?: true, budget?: usd })
```

**States:** `run_ready`, `run_draft` (a plan that can't run comes back in the preflight's `plan_errors`, not as a refusal)

When the wave can launch, returns `call`, which the model passes to pi-subagents unchanged. The call names the compiled script, saved at `runs/<id>/.compiled/<wave>.js`. Only then is the plan committed.

When every stage of the wave is reused from an earlier run, there is nothing to launch: it records the wave at once and returns `launched: false` with what [`agent_finish_wave`](#agent_finish_wave) would have returned, so the model goes on without launching or logging. `/agent-auto` and a released run do the same.

Otherwise returns `ok: false` and a `preflight` listing what is missing:

| Field | Means | What happens next |
| --- | --- | --- |
| `missing_params` | a param has no value on this machine | the model asks you, then `agent_set_params` |
| `missing_secrets` | a secret's variable is unset | you add it to `.env` |
| `decisions_needed` | a decision stage has no answer | the model asks you, then `agent_record_decision` |
| `approvals_needed` | a side effect needs your approval | the model describes it and asks; then `approve` |
| `review_needed` | the last wave ended with a review | the model shows you its outputs; on your go, `reviewed: true` |
| `budget` | the run's children spent `limits.max_cost_usd` | the model tells you the amount; on your go and a new limit, `budget: <usd>` (it holds for the rest of the run) |
| `plan_errors` | the plan is not ready or doesn't validate | the plan is fixed first |

When it relaunches a wave whose last launch failed, children that completed and haven't changed are not run again, nor a map's completed batches; `notes` says which. A model stage an earlier run finished on the same inputs is not run again either: its outputs are copied from that run. A map stage on the same items, batch size and stage reuses its merged output whole, and nothing of it is launched. When only some items are new or changed, a `pattern: map` stage maps just those: the items an earlier run mapped with the same stage keep their results, merged back in their place.

**Refusals:** [the wave was launched but not recorded](troubleshooting.md#launched-but-not-recorded) · [another branch is checked out, with changes](troubleshooting.md#the-runs-branch-isnt-checked-out) · [a wave is running](troubleshooting.md#a-wave-is-running) · [uv is missing](troubleshooting.md#uv-is-not-installed)

### agent_start_wave

Records that a wave was launched.

```text
agent_start_wave({ wave, subagent_run_id, mission_id?, approved?, decisions? })
```

**States:** `run_ready` → `run_wave`

Marks the wave's stages running and adds a launch to `run.yaml`, with the plan's commit, the param values (secrets masked), approvals and decisions. Without `mission_id`, it finds the pi-subagents mission that lists the launch.

**Refusals:** [a wave is running](troubleshooting.md#a-wave-is-running) · [draft plan](troubleshooting.md#plan-not-ready)

### agent_finish_wave

Records how a wave ended.

```text
agent_finish_wave({ wave, status?, result?, note? })
```

**States:** `run_wave` → `run_ready`; after the last wave → `run_review`, or `main_ready` when the run committed nothing

Reads the outcome and cost from pi-subagents, so the model passes only the wave. `done` marks the stages done. `failed`, `blocked` or `stopped` puts them back to pending, so the wave can be relaunched. Files the wave wrote into the agent (stage programs, say) are committed on the run's branch.

Returns `wave_status`, a summary of the children (status and output path, not the outputs themselves), `ends_with` and `next_wave`. `next_wave.preflight` lists what its compile will need first, so the model asks you before compiling. After the last wave it also returns the branch to review, the plan's `done_when` for the final report, `outputs` (each deliverable's path in this run, whether it exists, and how many items it holds when a program can count them: a JSON array's entries, JSON lines, CSV rows, a folder's files), and the call that closes the pi-subagents mission.

`status` and `result` are for when pi-subagents has no record of the run; the refusal says so.

**Refusals:** [pi-subagents is still running it](troubleshooting.md#still-running-in-pi-subagents) · [its process is gone](troubleshooting.md#the-waves-process-is-gone) · [no record](troubleshooting.md#no-pi-subagents-record)

### agent_record_decision

Records your answer for a decision stage.

```text
agent_record_decision({ stage, answer })
```

**States:** `run_ready`, `run_draft`

Writes the answer where the compiler looks for it, in the run's `work/`.

**Refusals:** [a wave is running](troubleshooting.md#a-wave-is-running)

### agent_resolve

Resolves provisional stages during a run, or changes pending ones you ask about.

```text
agent_resolve({ stages?, remove?, findings?, questions?, answers?, note? })
```

**States:** `run_ready`, `run_draft` → `run_draft`

Used after a discovery stage, or when you ask the run for a change. Raises the revision, sets the plan to draft and commits on the run's branch. The plan critic then reviews it (`agent_record_critique`) before the next wave compiles.

Locked: stages that ran, the request, goal, `done_when`, params, inputs, outputs and subagents. A side effect stays a side effect.

**Refusals:** [locked fields](troubleshooting.md#locked-while-resolving) · [another branch is checked out, with changes](troubleshooting.md#the-runs-branch-isnt-checked-out) · [no run is active](troubleshooting.md#resolve-without-a-run) · [a wave is running](troubleshooting.md#a-wave-is-running)

### agent_record_critique

Records the plan critic's result, and marks the plan ready if it validates.

```text
agent_record_critique({ run_id, verdict, passes?, findings })
```

**States:** `plan_draft`, `plan_ready`, `run_ready`, `run_draft`, `run_review`, `change_draft`, `change_ready`

`run_id` must be a completed plan-critic child in pi-subagents' records. Every finding carries a disposition: accepted, rejected (with a note) or asked. The record is tied to the plan as it is now: an edit after it makes the plan not ready until the critic is recorded again. On a change branch, the first critique of an edited plan gives it the next revision.

At most two passes per revision, and each time a run resolves stages.

**Refusals:** [unknown critic run](troubleshooting.md#unknown-critic-run) · [on `main` after a run](troubleshooting.md#critique-on-main) · [a wave is running](troubleshooting.md#a-wave-is-running)

### agent_stop_run

Stops the active run.

```text
agent_stop_run({ note? })
```

**States:** `run_ready`, `run_draft` → `run_review`, or `main_ready` when the run committed nothing

The run stays in `runs/`, marked stopped. Returns its branch, if any, for review, and the call that closes its pi-subagents mission.

**Refusals:** [a wave is running](troubleshooting.md#a-wave-is-running)

### agent_merge

Ends the review of a run or change branch.

```text
agent_merge({ action: "merge" | "keep" | "discard", summary? })
```

**States:** `run_review`, `change_ready` (all three actions); `change_draft` (keep, discard) → `main_ready`

| Action | Result |
| --- | --- |
| `merge` | The branch becomes one commit on `main`, tagged `r<revision>`. Its subject is `r<revision>: <branch>`; its body is the `summary` the agent gave you at review, the files changed (`git diff --stat`) and the branch's own commits. The branch is deleted. Only a ready, valid plan can merge. |
| `keep` | Back to `main`; the branch stays for later. |
| `discard` | Back to `main`; the branch is deleted. |

Uncommitted changes are committed on the branch first.

**Refusals:** [the plan is not ready](troubleshooting.md#merge-a-plan-that-is-not-ready) · [conflict with `main`](troubleshooting.md#merge-conflicts-with-main)

### agent_add_package

Adds Python packages to the agent's `pyproject.toml`, after you approve them.

```text
agent_add_package({ packages: ["openpyxl", …], reason })            (in an agent)
agent_add_package({ slug, packages: ["openpyxl", …], reason })      (in prometheus, for an agent it is planning)
```

**States:** all but `run_wave`; not on `main` once a run exists (packages change where a change can be reviewed)

It shows you the packages and the reason, and runs `uv add` only on a yes; on a no it tells the agent to work without the package or ask you. `uv add`, `pip install` and the script behind the tool are blocked for the agent in bash. A stage's subagent can't ask, so it is told to report the package it needs and why, and the agent asks you afterwards. Requirements are checked first: a package from the index as PEP 508 writes it (a name, extras, version bounds, markers), not a URL, an option or a path.

**Refusals:** [package not in pyproject.toml](troubleshooting.md#package-not-in-pyprojecttoml)

### agent_set_params

Sets param values: in `.env`, or on the active run for a `per_run` param.

```text
agent_set_params({ values: { name: value, … } })
```

**States:** all (a `per_run` param only during a run)

For paths and text. Refuses a param the plan doesn't declare, a secret (you add those yourself), and a `per_run` param while no run is active. It doesn't check paths: a relative input path, or one that doesn't exist, is reported by the plan check and the next compile ([Input path is relative or missing](troubleshooting.md#input-path-is-relative-or-missing)).

**See also:** [Params, `.env` and secrets](params-and-secrets.md)

### agent_sessions

Reads the agent's earlier Pi sessions.

```text
agent_sessions({ action: "list" })
agent_sessions({ action: "show", file, from?, to?, around?, window?, chars? })
```

**States:** all · changes nothing

Session files have lines too long for Pi's read tool; this reads them for the model.

- `list` gives the sessions, newest first, with records, turns, cost, whether it was a `/agent-improve` or `/agent-revise` session, and its first message.
- `show` gives a clipped transcript of one session, one line per record. Choose records by number (`from`, `to`) or around a timestamp (`around`, such as a failure's `at` in the improve digest). `file` can also be a failed child's session or transcript from an imported bundle (`runs/imported/…`, as the improve digest names it).

Used by `/agent-improve`.
