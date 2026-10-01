# Troubleshooting

Most problems show up as a refusal: a script declines a step and says why. The workflow agent passes that on and suggests the next step. Each entry here gives the message as the script prints it (`<…>` marks the parts that vary), its cause, and the fix.

To see where a workflow stands, and which moves are open, run `/workflow-status`. The states are described in [the state machine](state-machine.md).

## Setup

### Commands are missing

The `/workflow-*` commands don't appear in Pi.

**Cause.** Pi loads a project's extension only after you trust the folder.

**Fix.** Start `pi` in the Prometheus folder or the workflow's folder and accept the trust prompt. Each workflow is its own project and needs trusting too, including a copy made by `/workflow-export`.

### Python package missing

```text
Python package '<name>' is missing … ask the user to run `uv sync`
```

**Cause.** The scripts' Python packages aren't installed for the Python the extension found. A workflow creates its `.venv` with `uv sync` on first start, so in a workflow this means that failed: `uv` isn't installed, or you are offline.

**Fix.** Run `uv sync` in the Prometheus folder (for an exported workflow, in the workflow's folder), or install uv or get online, then restart pi. The extensions use the nearest `.venv`, or `$PI_WORKFLOW_PYTHON` if set. The workflow agent never installs packages itself.

### git is required

```text
git is required
```

**Cause.** Each workflow is its own git repository, and pi-subagents checks the repository's staged files around every child that writes.

**Fix.** Install git.

### uv is not installed

```text
this wave runs stage programs or gates with `uv run`, but uv is not installed
```

**Cause.** Gates and stage programs run with `uv run`, which installs the workflow's packages into its `.venv/` first.

**Fix.** Install [uv](https://docs.astral.sh/uv/). The first `uv run` needs network access.

### The subagent tool is missing

The model says it has no `subagent` tool.

**Cause.** pi-subagents isn't installed, or isn't active in this session.

**Fix.** Install it globally: `pi install npm:pi-subagents`. `/workflow-new`, `/workflow-run` and `/workflow-revise` activate its `subagent` tool before they start. In a session continued without one of those commands, the model enables it itself; on some providers it appears only after your next message.

### Every wave fails at startup

```text
Background children require the host npm package (@earendil-works/pi-coding-agent) with its dependencies; … does not provide …
```

**Cause.** pi-subagents and the installed Pi don't match. pi-subagents starts each background child with packages it expects Pi to provide, and this Pi no longer provides one of them, so every wave fails on its first child. With pi-subagents 0.74.0 this happens on Pi 1.0.0 (`@earendil-works/pi-agent-core/node` is gone); Pi 0.99.2 works.

**Fix.** Install a Pi that pi-subagents supports (`npm install -g @earendil-works/pi-coding-agent@0.99.2`), or wait for a pi-subagents release for the newer Pi. Then run `/workflow-run`: the active run relaunches the failed wave.

## Starting a run

### Branch not reviewed

```text
branch <run/…, revise/… or improve/…> is checked out and not reviewed: review it first
```

**Cause.** An earlier run or change left a branch you haven't reviewed. New runs and changes start from `main`.

**Fix.** Run `/workflow-run`. It shows you what the branch changes and asks whether to merge, keep or discard it.

### Plan not ready

```text
the plan is <status>, not ready: finish it first
```

**Cause.** A run starts only from a ready plan: one the plan critic has reviewed and that validates.

**Fix.** Run `/workflow-run`. With a draft plan it goes back to planning and asks what the plan still needs.

### Plan changed after the critique

```text
plan.yaml changed after the critic's result was recorded
```

**Cause.** The plan was edited after the plan critic reviewed it, so the critic hasn't seen it as it is now. On a branch, this also covers any difference from `main`'s plan.

**Fix.** None needed from you: the workflow agent runs the critic again and records the result. Runs and merges wait until then.

### A run is already active

```text
run <id> is active: continue it, or stop it first
```

**Cause.** Only one run can be active.

**Fix.** `/workflow-run` continues it. To start over, say so: the workflow agent stops the active run (it stays in `runs/`, marked stopped) and reviews its branch, if any.

### A value is missing

The run asks you for a folder or a value.

**Cause.** A required param has no value on this machine.

**Fix.** Give it. For files and folders, give an absolute path (`~/…` is fine). See [Params, `.env` and secrets](params-and-secrets.md).

### Input path is relative or missing

```text
input '<id>' resolves to '<path>', a relative path
input '<id>' resolves to '<path>', which does not exist
```

**Cause.** Inputs live outside the workflow, so their paths must be absolute and exist.

**Fix.** Give the value again when asked, or correct it in `.env`.

### A secret is missing

The workflow agent asks you to add a variable to `.env`.

**Cause.** A secret param isn't set.

**Fix.** Add `NAME=value` to the workflow's `.env`, or set it in the environment you start pi from, then say so. The agent never asks for the value in chat.

### Reading `.env` was blocked

**Cause.** Intended: `.env` may hold secrets, and the model may not read or edit it.

**Fix.** None.

### No connector for a side effect

```text
stage '<key>': agent '<name>' cannot reach <target>
builtin agent '<name>' writes <target> but has no connector tools
```

**Cause.** A side effect that writes outside the workflow (an app, a message) needs an agent that can reach it: a connector tool (`mcp:<server>` in the agent's `tools`) or, for a command-line tool, `bash`. Built-in agents such as `worker` have no connector tools, so the warning means the stage can only use a command-line tool.

**Fix.** To use a connector, give the stage a project agent in `.pi/agents/` whose `tools` list it.

## During a run

### A wave is running

```text
a wave is running (stages <…>); check its run before launching again
```

**Cause.** A wave was launched and hasn't been recorded as finished.

**Fix.** Wait for pi-subagents' completion notice; the workflow agent then records the result and continues. If pi restarted meanwhile, `/workflow-run` checks the wave first and records it once it has finished.

### Waits for the running wave

```text
a wave of run <id> is running: …
```

From a replan, a critique, a decision or stopping the run.

**Cause.** These wait until the running wave is recorded as finished, so the plan and the next wave's answers don't change under it.

**Fix.** Wait for the completion notice.

### Launched but not recorded

```text
wave <w> was already launched as pi-subagents run <id> … and not recorded
```

**Cause.** The session ended between launching the wave and recording the launch. Launching again would run it twice.

**Fix.** None needed from you: the workflow agent records the existing launch, then waits for it or records its result.

### Launch recorded on a draft plan

```text
the plan is draft … no wave was compiled from it
```

**Cause.** A replan the critic hasn't passed can't have been launched: compiling refuses a draft plan.

**Fix.** Record the critic's result first.

### Still running in pi-subagents

```text
run <id> is still <state> in pi-subagents: wait for its completion notice
```

**Cause.** The workflow agent tried to record a wave pi-subagents is still running.

**Fix.** Wait. If you know it has ended (pi crashed, or you stopped it), tell the agent: it records the wave as failed or stopped, so it can be relaunched.

### The wave's process is gone

```text
run <id> is still <state> in pi-subagents, but its process (<pid>) is gone
```

**Cause.** The wave's process died without recording how it ended (pi or the machine stopped mid-wave).

**Fix.** None needed from you: the workflow agent records the wave as failed, and it can be relaunched.

### No pi-subagents record

```text
cannot read pi-subagents' record of run <id>
```

**Cause.** pi-subagents keeps each background run's record in a temporary folder. The folder was cleaned up, or `PI_SUBAGENTS_TEMP_ROOT` differs from when the wave launched.

**Fix.** None needed from you: the workflow agent records the wave from the completion notice, passing its status and result.

### Review before the next wave

The workflow agent shows you a wave's results and asks whether to go on.

**Cause.** The plan puts a review after that wave (`pause_after`). Until you answer, compiling the next wave returns `review_needed`.

**Fix.** Say go on, or ask for a change (a replan) or to stop.

### A wave failed

**Cause.** A gate rejected an output, a review found a blocking problem, or the wave failed before its script returned (`failure_kind`, such as `timeout` or `validation`). The workflow agent reports which.

**Fix.** Choose one of the three options it offers: relaunch the wave (children that completed and haven't changed are not run again), change the plan (a replan), or stop the run.

### The subagent tool returned an error

**Cause.** The wave failed to launch, an extension failed to load, or a child failed to start: an infrastructure problem. By design the workflow agent stops and reports the exact error and run id, and doesn't work around it (no running the stage itself, no hand-written scripts) unless you tell it to.

**Fix.** Fix the cause, then run `/workflow-run` again.

### Staged index changed

```text
Staged index changed after launch
```

**Cause.** A child that writes files failed its acceptance check, because files were staged (`git add`) in the workflow's repository while it ran.

**Fix.** Don't stage files in a workflow while a wave runs. Relaunch the wave.

## Changing a workflow

### Editing the plan was blocked

The workflow agent was blocked from editing `plan.yaml`.

**Cause.** Once a workflow has a run, its plan changes only through controlled steps.

**Fix.** During a run, the agent replans with its replan tool. Between runs, use `/workflow-revise`.

### Locked in a replan

```text
a replan cannot change [<fields>] … locked
```

**Cause.** A replan during a run can't change the request, goal, `done_when`, params, inputs, outputs, agents, stages that already ran, or a side effect's approval.

**Fix.** Make those changes with `/workflow-revise` once the run has finished or been stopped.

### Replan without a run

```text
no run is active: the replan tools work only during a run
```

**Cause.** The workflow agent tried to replan outside a run.

**Fix.** None needed from you. Before the first run, or on a change branch, it edits `plan.yaml` directly; after a run, use `/workflow-revise`.

### Package not in pyproject.toml

```text
gates/<file> would fail before checking anything: it imports …, which the workflow's pyproject.toml does not list
```

Also `scripts/<file>`, for a stage program.

**Cause.** Every package a workflow's gates and stage programs use is listed in its `pyproject.toml` and locked in `uv.lock`, so you can see them in one place and review them with the branch. The same check reports a script with its own `# /// script` package block, and a gate with packages run with `python3` instead of `uv run`.

**Fix.** None needed from you: the workflow agent adds the package with `uv add <package>` (or moves the block's packages to `pyproject.toml`).

### Files blocked on `main`

```text
main is checked out and no run is active: gates, scripts, agents and packages change only on a run or change branch
```

**Cause.** After the first run, `main` changes only by a reviewed merge. This usually means the checked-out branch was switched while a change was in progress, from a terminal or the editor.

**Fix.** Switch back to the branch the message names (`git switch revise/…`) and tell the agent to continue. To change something new, use `/workflow-revise`.

### Critique on `main`

```text
main changes only by a reviewed merge once a run exists: change the plan with /workflow-revise
```

**Cause.** After the first run, a critic's result is recorded on a branch (a run's or a change's), never on `main`.

**Fix.** Start a change with `/workflow-revise`.

### Unknown critic run

```text
pi-subagents has no record of a critic run <id>
run <id> is a <agent> child in state <state>, not a completed plan-critic run
```

**Cause.** A critic's result can only be recorded for a plan-critic child that ran and completed. Its records are kept in pi-subagents' temporary folder.

**Fix.** None needed from you: the workflow agent passes the right run id, or relaunches a failed critic. If the records were cleaned up, it runs the critic again.

### Change while a run is active

```text
run <id> is active: tell the run what to change … or stop it first
```

**Cause.** `/workflow-revise` and `/workflow-improve` work only with no run active.

**Fix.** During a run, ask the run itself for the change: it replans on its own branch, which you review at the end.

### Another change is open

```text
<revise/… or improve/…> is an open /workflow-<kind> change: finish it first
```

**Cause.** A revise and an improve each get their own branch, so you review them separately.

**Fix.** Run `/workflow-run`: it shows you the open branch and asks whether to merge, keep or discard it. Then run the other command.

### Nothing to improve from yet

```text
no run yet, so nothing to improve from
```

**Cause.** `/workflow-improve` proposes fixes from earlier runs and sessions, so it needs at least one run.

**Fix.** Run the workflow first. To change the plan before that, use `/workflow-run` or `/workflow-revise`, which edit it directly on `main`.

### Nothing new to improve from

```text
Nothing new since the last /workflow-improve: no unreviewed sessions or runs.
```

**Cause.** Every session and run is older than the last entry in `improvements.yaml`. No branch was opened.

**Fix.** Run the workflow first, or give a focus to improve it anyway: `/workflow-improve <focus>`.

### Merge a plan that is not ready

```text
the plan on <branch> is draft … only a ready, valid plan can reach main
```

**Cause.** The change isn't finished: the plan critic hasn't passed it, or it doesn't validate.

**Fix.** Finish the change, or choose keep (back to `main`, the branch stays) or discard.

### Merge conflicts with `main`

```text
main has moved and <branch> conflicts with it
```

**Cause.** `main` changed after the branch was created, in a way that overlaps the branch's changes, and git couldn't combine them into the one commit a merge adds to `main`. The branch is checked out again, unchanged.

**Fix.** Resolve the conflict by hand, or discard the branch.

### Branch exists from an earlier run

```text
branch run/<id> already exists from an earlier run with this id
```

**Cause.** `runs/` isn't in git, so deleting a run's folder frees its id but not the branch that run left. New runs skip ids that still have a branch; a run started before that fix may still collide.

**Fix.** Stop the run, then review the old branch (`git switch run/<id>`, then `/workflow-run`) or delete it (`git branch -D run/<id>`).

### Merged with git by hand

You merged a branch with git yourself.

**Cause.** That works, but skips two steps the review does.

**Fix.** Tag the revision on `main` (`git tag r<N>`) and delete the merged branch. Choosing merge in the review is simpler.

## Prometheus commands

### Prometheus answered instead of planning

**Cause.** Prometheus plans only when asked with `/workflow-new`. A plain message, even one that describes work, gets an answer or a suggestion to use the command.

**Fix.** Run `/workflow-new <request>`.

### Refresh during a run or review

```text
<workflow> has an active run (<id>); refresh it once the run has finished and its branch is reviewed
<workflow> has branch <name> checked out; refresh it after merging, keeping or discarding that branch
```

**Cause.** A refresh rewrites the workflow's copy of the skill, including the tool a running map stage uses, and its commit would land on the checked-out branch and show up in that branch's review.

**Fix.** Finish the run, then merge, keep or discard its branch (or the `revise/…` or `improve/…` branch), then refresh.

### Export destination exists

```text
<dest>/<slug> already exists
destination is inside the workflow
```

**Cause.** Export never overwrites, and can't copy a workflow into itself.

**Fix.** Pick another destination, or pass a different name.

## For contributors

The messages come from the Python scripts in [`.pi/skills/workflow/scripts/`](../.pi/skills/workflow/scripts/), which print `{"ok": false, "error": …}` and exit non-zero; the extensions pass that to the model. A failed validation prints its report instead, with `errors: […]`. The run protocol ([`run-protocol.md`](../.pi/skills/workflow/references/run-protocol.md), "Failures") tells the workflow agent how to react.

When you add a refusal a person running workflows can meet, add a test in [`tests/`](../tests/) that triggers it and an entry here: a short heading (its anchor is what [Commands and tools](commands.md) links to), the message, its cause and the fix.
