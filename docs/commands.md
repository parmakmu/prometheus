# Commands and tools

**Commands** start with `/`, and you type them in Pi. prometheus's commands run in the prometheus folder; the others run in an agent's folder.

| Command | Runs in | Does |
|---|---|---|
| [`/agent-new`](#agent-new) | prometheus | plan a new agent for a request |
| [`/agent-list`](#agent-list) | prometheus | list agents and where each stands |
| [`/agent-export`](#agent-export) | prometheus | copy an agent somewhere else to run it |
| [`/agent-refresh`](#agent-refresh) | prometheus | update agents to this prometheus's template and skill |
| [`/agent-run`](#agent-run) | Agent | plan, continue or run, from wherever it stands |
| [`/agent-status`](#agent-status) | Agent | show where it stands; changes nothing |
| [`/agent-revise`](#agent-revise) | Agent | change the plan on a branch you review |
| [`/agent-improve`](#agent-improve) | Agent | propose fixes from past sessions and runs |

Commands are prefixed `agent-` because Pi has built-in `/new` and `/export`.

**Tools** have no slash. The model calls them to carry out the mechanical steps of planning and running; you never call them, but you see their results. They are listed [after the commands](#tools-the-model-calls).

Inside an agent, each entry names the [states](state-machine.md) it is accepted in. In any other state it is refused, with a message that says why; the refusals are in [Troubleshooting](troubleshooting.md).

## prometheus commands

### /agent-new

Plans a new agent for a request.

```text
/agent-new <request>
```

**Runs in:** prometheus

Creates `agents/<slug>/`, then plans: it drafts the plan, checks it, has the plan critic review it, and asks only the questions whose answers change the plan. It never asks for param values (they belong to the machine; `/agent-run` asks for them) and never does the requested work.

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
/agent-export emails-to-excel ~/agents
```

**Refusals:** [destination exists](troubleshooting.md#export-destination-exists)

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

### /agent-status

Shows where the agent stands.

```text
/agent-status
```

**Runs in:** an agent · **States:** all · changes nothing

Shows the state and the moves open from it, the plan's status and validity, the active run, the last and next wave, open questions, missing params and secrets, pending approvals and reviews, and branches waiting for review. A running wave's live status is checked by the next `/agent-run`.

### /agent-revise

Changes the agent's plan, without running it.

```text
/agent-revise [change]
```

**Runs in:** an agent · **States:** `planning`, `planned`, `ready`, `change_draft`, `change_ready`, `main_draft`

Once a run exists, it opens a `revise/<date>-<n>` branch (or continues the open one) and makes the change there. The plan is left as it is until edited; an edited `plan.yaml` needs the plan critic again before it can merge, while gates, programs and agents alone don't. At the end you see the diff and choose merge, keep or discard. Before the first run, it edits the plan directly on `main`, as planning does.

Without a change, it asks what to change.

#### Example

```text
/agent-revise drop the discover stage, the mailbox format is stable now
```

**Refusals:** [a run is active](troubleshooting.md#change-while-a-run-is-active) · [an improve is open](troubleshooting.md#another-change-is-open) · [a branch is not reviewed](troubleshooting.md#branch-not-reviewed)

**See also:** [`/agent-improve`](#agent-improve) · [Branches and review](how-it-works.md#reviewing-what-a-run-changed)

### /agent-improve

Proposes fixes from how the agent's recent sessions and runs went, and applies the ones you accept on a branch you review.

```text
/agent-improve [focus]
```

**Runs in:** an agent · **States:** `ready`, `change_draft`, `change_ready`, `main_draft`

It reads a digest of what is new since the last improve: failed or blocked tool results, repeated calls, cost, failed or relaunched waves. From that it proposes fixes to the plan, gates, stage programs, subagents and packages, each with its evidence, and asks you about each one. Accepted ones are applied on an `improve/<date>-<n>` branch, which you review like a revise.

- It can add short rules for this agent's agent under `## Lessons for this agent` at the end of `.pi/APPEND_SYSTEM.md`. `/agent-refresh` keeps that section.
- It doesn't edit the skill or its references. A problem there is listed as text for you to take to prometheus.
- Every proposal, applied or rejected, is recorded in `improvements.yaml`. The next improve reads only sessions and runs newer than that, and doesn't propose a rejected fix again.
- With nothing new and no focus given, it says so and opens no branch.

Nothing runs it automatically: only you type it.

#### Example

```text
/agent-improve the approval question is hard to answer
```

**Refusals:** [no run yet](troubleshooting.md#nothing-to-improve-from-yet) · [nothing new](troubleshooting.md#nothing-new-to-improve-from) · [a run is active](troubleshooting.md#change-while-a-run-is-active) · [a revise is open](troubleshooting.md#another-change-is-open)

**See also:** [`/agent-revise`](#agent-revise) · [`agent_sessions`](#agent_sessions)

## Tools the model calls

You don't call these. They are here so you can read a tool result or a refusal the agent shows you.

| Tool | Runs in | Does |
|---|---|---|
| [`agent_scaffold`](#agent_scaffold) | prometheus | create the folder for the agent being planned |
| [`agent_check (prometheus)`](#agent_check-prometheus) | prometheus | check an agent's plan from prometheus |
| [`agent_add_package`](#agent_add_package) | both | add a Python package, after you approve it and its reason |
| [`agent_check`](#agent_check) | Agent | check the plan and say where the agent stands |
| [`agent_new_run`](#agent_new_run) | Agent | start a run |
| [`agent_compile`](#agent_compile) | Agent | turn the next wave into a pi-subagents call, or say what must happen first |
| [`agent_log_start`](#agent_log_start) | Agent | record that a wave was launched |
| [`agent_log_finish`](#agent_log_finish) | Agent | record how a wave ended |
| [`agent_record_decision`](#agent_record_decision) | Agent | record your answer for a decision stage |
| [`agent_replan`](#agent_replan) | Agent | change pending stages during a run |
| [`agent_record_critique`](#agent_record_critique) | Agent | record the plan critic's result |
| [`agent_stop_run`](#agent_stop_run) | Agent | stop the active run |
| [`agent_merge`](#agent_merge) | Agent | end the review of a run or change branch |
| [`agent_set_params`](#agent_set_params) | Agent | set param values in `.env` |
| [`agent_sessions`](#agent_sessions) | Agent | read the agent's earlier Pi sessions |

## prometheus tools

### agent_scaffold

Creates the folder for the agent being planned.

```text
agent_scaffold({ slug, request })
```

**Called during:** `/agent-new`

Creates `agents/<slug>/` from the agent template: a draft plan holding the request, the agent's extension, a copy of the skill, its agents and its own git repository. It refuses unless `/agent-new` ran in this session, and allows one agent per `/agent-new`. Running `scaffold_agent.py` from the shell is blocked, so the tool can't be bypassed.

### agent_check (prometheus)

Checks an agent's plan from prometheus.

```text
agent_check({ slug })
```

**Called during:** planning · changes nothing

Validates the plan and compile-checks each concrete pending wave. Returns errors, warnings and a summary. It never launches a wave.

## Agent tools

The model calls these while it follows the run protocol ([`run-protocol.md`](../.pi/skills/agent/references/run-protocol.md)). Every result carries `next`: the state the agent is in now, the moves open from it and a one-line hint, so the model never has to work out where it stands.

### agent_check

Checks the plan, and says where the agent stands.

```text
agent_check({})
```

**States:** all · changes nothing

Validates the plan and compile-checks its concrete pending waves. Returns errors, warnings, a summary (revision, waves, open questions, approvals, `last_run`) and `state`. During a run, `state.last_launch` gives the latest launch's wave, status and pi-subagents run id.

### agent_new_run

Starts a run.

```text
agent_new_run({ note? })
```

**States:** `planned`, `ready`, `branch_review`, `change_draft`, `change_ready` → `between_waves`

Creates `runs/<id>/` (`<date>-<n>`) as the active run. A run always starts from `main`: with a run or change branch checked out, it commits that branch's leftovers to it, switches to `main` and keeps the branch for review (`left_branch` in the result). Commits leftover changes: before the first run on `main`, since that is planning; after it on the run's branch `run/<id>`.

**Refusals:** [a run is active](troubleshooting.md#a-run-is-already-active) · [main's plan is not ready while a branch is checked out](troubleshooting.md#branch-not-reviewed) · [the plan is not ready](troubleshooting.md#plan-not-ready)

### agent_compile

Turns the next wave into the exact pi-subagents call, or says what must happen first.

```text
agent_compile({ approve?: [stage, …], reviewed?: true })
```

**States:** `between_waves`

When the wave can launch, returns `call`, which the model passes to pi-subagents unchanged. The call names the compiled script, saved at `runs/<id>/.compiled/<wave>.js`. Only then is the plan committed.

Otherwise returns `ok: false` and a `preflight` listing what is missing:

| Field | Means | What happens next |
|---|---|---|
| `missing_params` | a param has no value on this machine | the model asks you, then `agent_set_params` |
| `missing_secrets` | a secret's variable is unset | you add it to `.env` |
| `decisions_needed` | a decision stage has no answer | the model asks you, then `agent_record_decision` |
| `approvals_needed` | a side effect needs your approval | the model describes it and asks; then `approve` |
| `review_needed` | the last wave ended with a review | the model shows you its outputs; on your go, `reviewed: true` |
| `plan_errors` | the plan is not ready or doesn't validate | the plan is fixed first |

When it relaunches a wave whose last launch failed, children that completed and haven't changed are not run again; `notes` says which.

**Refusals:** [the wave was launched but not recorded](troubleshooting.md#launched-but-not-recorded) · [a wave is running](troubleshooting.md#a-wave-is-running) · [uv is missing](troubleshooting.md#uv-is-not-installed)

### agent_log_start

Records that a wave was launched.

```text
agent_log_start({ wave, subagent_run_id, mission_id?, approved?, decisions? })
```

**States:** `between_waves` → `wave_running`

Marks the wave's stages running and adds a launch to `run.yaml`, with the plan's commit, the param values (secrets masked), approvals and decisions. Without `mission_id`, it finds the pi-subagents mission that lists the launch.

**Refusals:** [a wave is running](troubleshooting.md#a-wave-is-running) · [draft plan](troubleshooting.md#launch-recorded-on-a-draft-plan)

### agent_log_finish

Records how a wave ended.

```text
agent_log_finish({ wave, status?, result?, note? })
```

**States:** `wave_running` → `between_waves`; after the last wave → `branch_review`, or `ready` when the run committed nothing

Reads the outcome and cost from pi-subagents, so the model passes only the wave. `done` marks the stages done. `failed`, `blocked` or `stopped` puts them back to pending, so the wave can be relaunched. Files the wave wrote into the agent (stage programs, say) are committed on the run's branch.

Returns `wave_status`, a summary of the children (status and output path, not the outputs themselves), `ends_with` and `next_wave`. `next_wave.preflight` lists what its compile will need first, so the model asks you before compiling. After the last wave it also returns the branch to review, the plan's `done_when` for the final report, and the call that closes the pi-subagents mission.

`status` and `result` are for when pi-subagents has no record of the run; the refusal says so.

**Refusals:** [pi-subagents is still running it](troubleshooting.md#still-running-in-pi-subagents) · [its process is gone](troubleshooting.md#the-waves-process-is-gone) · [no record](troubleshooting.md#no-pi-subagents-record)

### agent_record_decision

Records your answer for a decision stage.

```text
agent_record_decision({ stage, answer })
```

**States:** `between_waves`, `replan_draft`

Writes the answer where the compiler looks for it, in the run's `work/`.

**Refusals:** [a wave is running](troubleshooting.md#waits-for-the-running-wave)

### agent_replan

Changes pending stages during a run.

```text
agent_replan({ stages?, remove?, findings?, questions?, answers?, note? })
```

**States:** `between_waves`, `replan_draft` → `replan_draft`

Used after a discovery stage, or when you ask the run for a change. Raises the revision, sets the plan to draft and commits on the run's branch. The plan critic then reviews it (`agent_record_critique`) before the next wave compiles.

Locked: stages that ran, the request, goal, `done_when`, params, inputs, outputs and subagents. A side effect stays a side effect.

**Refusals:** [locked fields](troubleshooting.md#locked-in-a-replan) · [no run is active](troubleshooting.md#replan-without-a-run) · [a wave is running](troubleshooting.md#waits-for-the-running-wave)

### agent_record_critique

Records the plan critic's result, and marks the plan ready if it validates.

```text
agent_record_critique({ run_id, verdict, passes?, findings })
```

**States:** `planning`, `planned`, `replan_draft`, `branch_review`, `change_draft`, `change_ready`

`run_id` must be a completed plan-critic child in pi-subagents' records. Every finding carries a disposition: accepted, rejected (with a note) or asked. The record is tied to the plan as it is now: an edit after it makes the plan not ready until the critic is recorded again. On a change branch, the first critique of an edited plan gives it the next revision.

At most two passes per revision, and per replan during a run.

**Refusals:** [unknown critic run](troubleshooting.md#unknown-critic-run) · [on `main` after a run](troubleshooting.md#critique-on-main) · [a wave is running](troubleshooting.md#waits-for-the-running-wave)

### agent_stop_run

Stops the active run.

```text
agent_stop_run({ note? })
```

**States:** `between_waves`, `replan_draft` → `branch_review`, or `ready` when the run committed nothing

The run stays in `runs/`, marked stopped. Returns its branch, if any, for review, and the call that closes its pi-subagents mission.

**Refusals:** [a wave is running](troubleshooting.md#waits-for-the-running-wave)

### agent_merge

Ends the review of a run or change branch.

```text
agent_merge({ action: "merge" | "keep" | "discard", summary? })
```

**States:** `branch_review`, `change_ready` (all three actions); `change_draft` (keep, discard) → `ready`

| Action | Result |
|---|---|
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

**States:** all but `wave_running`; not on `main` once a run exists (packages change where a change can be reviewed)

It shows you the packages and the reason, and runs `uv add` only on a yes; on a no it tells the agent to work without the package or ask you. `uv add`, `pip install` and the script behind the tool are blocked for the agent in bash. A stage's subagent can't ask, so it is told to report the package it needs and why, and the agent asks you afterwards. Requirements are checked first: names with optional extras and version bounds, nothing else (no URLs, flags or paths).

**Refusals:** [package not in pyproject.toml](troubleshooting.md#package-not-in-pyprojecttoml)

### agent_set_params

Sets param values in `.env`.

```text
agent_set_params({ values: { name: value, … } })
```

**States:** all

For paths and text. Files and folders take absolute paths; a relative or missing input path is refused. Refuses secrets: you add those yourself.

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
- `show` gives a clipped transcript of one session, one line per record. Choose records by number (`from`, `to`) or around a timestamp (`around`, such as a failure's `at` in the improve digest).

Used by `/agent-improve`.
