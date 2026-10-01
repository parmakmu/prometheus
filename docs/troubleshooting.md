# Troubleshooting

Most problems show up as a refusal: a script declines to do something and says why. The workflow agent normally relays that and suggests the next step. This page lists the refusals you are most likely to meet, what causes them, and what to do.

Messages are quoted as the scripts print them; `<…>` marks the parts that vary.

## Setup

**The `/workflow-*` commands don't exist.**
Pi only loads a project's extension after you trust the folder. Start `pi` in the Prometheus folder (or the workflow's folder) and accept the trust prompt. Each workflow is its own project, so it needs trusting too, including after an export.

**"Python package '<name>' is missing … ask the user to run `uv sync`"**
A workflow creates its `.venv` itself on first start (`uv sync`), so this means that failed: `uv` is not installed, or offline. Install `uv` or get online, then restart pi. Otherwise, the scripts' Python packages aren't installed for the Python the extension found. Run `uv sync` in the Prometheus folder (or, for an exported workflow, in the workflow's folder), then restart pi. The extensions use the nearest `.venv`, or `$PI_WORKFLOW_PYTHON` if set. The workflow agent is told never to install packages itself.

**"git is required"**
Each workflow is its own git repository, and pi-subagents checks the repository's staged files around every child that writes. Install git.

**The subagent tool isn't available.**
pi-subagents must be installed globally (`pi install npm:pi-subagents`). `/workflow-new`, `/workflow-run` and `/workflow-revise` make its `subagent` tool active before they send their prompt. In a session you continue without one of those commands, the model enables it itself; on some providers it then appears only after your next message.

## Starting and running

**"branch <run/…, revise/… or improve/…> is checked out and not reviewed: review it first"**
A previous run, or a `/workflow-revise` change, left a branch that you haven't reviewed. New runs and new changes start from `main`, so this has to be settled first. Run `/workflow-run`: it notices the branch, shows you what it changes, and asks whether to merge, keep or discard it.

**"the plan is <status>, not ready: finish it first"**
A run starts only from a ready plan: one the plan critic has reviewed and that validates. Run `/workflow-run`; with a draft plan it goes back to planning, and asks what the plan still needs.

**"pi-subagents has no record of a critic run <id>"** / **"… not a completed plan-critic run"**
The plan critic's result can only be recorded for a critic that ran and completed: the workflow agent must pass the run id of the plan-critic child from the critic's result. If the critic failed, it is relaunched. Records are kept in pi-subagents' temporary folder; if that was cleaned up, run the critic again.

**"main changes only by a reviewed merge once a run exists: change the plan with /workflow-revise"**
After the first run, a critic's result is recorded on a branch (a run's, or a change's), never straight on `main`. Start a change with `/workflow-revise`.

**"the plan is draft … no wave was compiled from it"** (recording a launch)
A replan the critic hasn't passed can't have been launched, since compiling refuses a draft plan. Record the critic's result first.

**"stage '<key>': agent '<name>' cannot reach <target>"** / **"builtin agent '<name>' writes <target> but has no connector tools"**
A side effect that writes outside the workflow (an app, a message) needs an agent that can reach it: a connector tool (`mcp:<server>` in the agent's `tools`) or, for a command-line tool, `bash`. Builtin agents like `worker` get no connector tools at all, so the warning means the stage can only use a CLI. To use a connector, give the stage a project agent in `.pi/agents/` whose `tools` list it.

**"plan.yaml changed after the critic's result was recorded"**
The plan was edited after the plan critic reviewed it, so the critic hasn't seen the plan as it is now. The workflow agent runs the critic again and records the result. A run or merge refuses the plan until then.

**"run <id> is active: continue it, or stop it first"**
Only one run can be active. `/workflow-run` continues the active run. To start over, ask for that: the workflow agent will stop the active run (it stays in `runs/`, marked stopped) and review its branch if it has one.

**The run asks for a folder or a value.**
A required param has no value on this machine. Give an absolute path for files and folders. See [Params, `.env` and secrets](params-and-secrets.md).

**"input '<id>' resolves to '<path>', a relative path" / "… which does not exist"**
Input files and folders live outside the workflow, so their paths must be absolute and must exist. Fix the value in `.env`, or give it again when asked.

**The workflow agent asks you to add a variable to `.env`.**
A secret param isn't set. Add `NAME=value` to the workflow's `.env`, or set it in the environment you start pi from, then say so. The agent will never ask you for the value in chat.

**The workflow agent was blocked from reading `.env`.**
That is intended: `.env` may hold secrets, and the model may not read or edit it.

**"a wave is running (stages <…>); check its run before launching again"**
A wave was launched and hasn't been recorded as finished. Wait for pi-subagents' completion notice; the workflow agent then records the result and continues. If pi was restarted in the meantime, `/workflow-run` checks the wave's status first and records it once it has finished.

**The workflow agent shows you a wave's results and asks whether to go on.**
The plan put a review after that wave (`pause_after`). The next wave waits for your go: until you give it, compiling the next wave answers "review_needed". Say go on, or ask for a change (a replan) or to stop.

**"wave <w> was already launched as pi-subagents run <id> … and not recorded"**
The wave was launched, but the session ended before the launch was recorded (the agent records it right after launching). Launching it again would run it twice. The workflow agent records the existing launch and waits for it, or records its result if it has already ended.

**"run <id> is still <state> in pi-subagents: wait for its completion notice"**
The workflow agent tried to record a wave that pi-subagents is still running. Wait. If you know it has ended (pi crashed, or you stopped it), tell the agent: it records the wave as failed or stopped, so it can be relaunched.

**"run <id> is still <state> in pi-subagents, but its process (<pid>) is gone"**
The wave's process died without recording how it ended (pi or the machine stopped mid-wave). The workflow agent records the wave as failed; the wave can then be relaunched.

**"a wave of run <id> is running: …"** (from a replan, the plan critic's result, a decision or stopping)
These wait until the running wave is recorded as finished, so that the plan and the next wave's answers don't change under it. Wait for the completion notice.

**"cannot read pi-subagents' record of run <id>"**
pi-subagents keeps each background run's record in a temporary folder. If that folder was cleaned up, or `PI_SUBAGENTS_TEMP_ROOT` differs from when the wave launched, the record is gone. The workflow agent can record the wave from the completion notice instead, passing its status and result explicitly.

**A wave failed.**
The workflow agent reports which stage failed and why: a gate that rejected an output, a review that found a blocking problem, or a failure before the wave's script returned (a `failure_kind` such as `timeout` or `validation`). It then offers three options: relaunch the wave (children that completed and haven't changed are not run again), change the plan (a replan), or stop the run.

**The workflow agent stopped with an error from the subagent tool.**
A failure to launch the wave, load an extension or start a child is an infrastructure problem. The workflow agent stops and reports the exact error and run id; by design it doesn't work around it (no running the stage itself, no hand-written scripts) unless you tell it to. Fix the cause, then run `/workflow-run` again.

**"Background children require the host npm package (@earendil-works/pi-coding-agent) with its dependencies; … does not provide …"**
pi-subagents and the installed Pi don't match: pi-subagents starts each background child with packages it expects Pi to provide, and this Pi version no longer provides one of them. Every wave fails on its first child, before any stage runs. With pi-subagents 0.74.0 this happens on Pi 1.0.0 (`@earendil-works/pi-agent-core/node` is gone); Pi 0.99.2 works. Install a Pi version pi-subagents supports (`npm install -g @earendil-works/pi-coding-agent@0.99.2`), or wait for a pi-subagents release for the newer Pi, then run `/workflow-run` again: the active run relaunches the failed wave.

**"Staged index changed after launch"**
A child that writes files failed its acceptance check because files were staged (`git add`) in the workflow's repository while it ran. Don't stage files in a workflow while a wave runs, then relaunch the wave.

## Changing a workflow

**The workflow agent was blocked from editing `plan.yaml`.**
Once a workflow has a run, its plan changes only through controlled steps. During a run, the workflow agent replans pending stages with its replan tool. Between runs, use `/workflow-revise`.

**"a replan cannot change [<fields>] … locked"**
A replan during a run can't change the request, goal, done-when criteria, params, inputs, outputs or agents, stages that already ran, or a side effect's approval. Make those changes with `/workflow-revise` after the run has finished or been stopped.

**"gates/<file> would fail before checking anything: it imports …, which the workflow's pyproject.toml does not list"** (or `scripts/<file>` for a stage program)
Every package a workflow's gates and stage programs use is listed in its `pyproject.toml` (and locked in `uv.lock`), so you can see them in one place and review them with the branch. The workflow agent adds the missing one with `uv add <package>`. The same check reports a script with its own `# /// script` package block (move the packages to `pyproject.toml`) and a gate with packages run with `python3` instead of `uv run`.

**"this wave runs stage programs or gates with `uv run`, but uv is not installed"**
Install [uv](https://docs.astral.sh/uv/). `uv run` installs the workflow's packages into its `.venv/` before the first gate or program runs; that needs network access the first time.

**"main is checked out and no run is active: gates, scripts, agents and packages change only on a run or change branch"**
After a workflow's first run, `main` changes only by a reviewed merge, so the workflow agent is blocked from editing these files on `main`. It usually means the checked-out branch was switched while a change was in progress (from a terminal or the editor). Switch back to the branch the message names (`git switch change/…`) and tell the agent to continue; to change something new, use `/workflow-revise`.

**"branch run/<id> already exists from an earlier run with this id"**
`runs/` isn't in Git, so deleting a run's folder frees its id, but not the branch that run left. New runs skip ids that still have a branch. A run started before that fix may still collide: stop it, then review the old branch (`git switch run/<id>`, then `/workflow-run`) or delete it (`git branch -D run/<id>`).

**"no run is active: the replan tools work only during a run"**
The workflow agent tried to replan outside a run. Before the first run, or on a change branch, it edits `plan.yaml` directly. After a run, use `/workflow-revise`. You don't need to approve the direct edit.

**"Nothing new since the last /workflow-improve"**
Every session and run is older than the last entry in `improvements.yaml`, so there is no new evidence, and no branch was opened. Run the workflow first, or give a focus (`/workflow-improve <focus>`) to improve it anyway.

**"no run yet, so nothing to improve from"** (from `/workflow-improve`)
`/workflow-improve` proposes fixes from how earlier runs and sessions went, so it needs at least one run. Before that, the plan is still being planned: change it with `/workflow-run` or `/workflow-revise`, which edit it directly on `main`.

**"<revise/… or improve/…> is an open /workflow-<kind> change: finish it first"**
A `/workflow-revise` change and a `/workflow-improve` pass each get their own branch, so you review them separately. Finish the open one first: run `/workflow-run`, which shows you the branch and asks whether to merge, keep or discard it. Then run the other command.

**"run <id> is active: tell the run what to change … or stop it first"** (from `/workflow-revise`)
`/workflow-revise` only works with no run active. During a run, ask the run itself for the change: it replans on its own branch, and you review that at the end.

**"the plan on <branch> is draft … only a ready, valid plan can reach main"**
You chose merge, but the change isn't finished: the plan critic hasn't passed it, or it doesn't validate. Finish the change, or choose keep (switch back to `main`, keep the branch for later) or discard.

**"main has moved and <branch> conflicts with it"**
`main` changed after the branch was created in a way that overlaps the branch's changes, and git couldn't combine them into the one commit a merge adds to `main`. The branch is checked out again, unchanged. Resolve the conflict by hand, or discard the branch.

**You merged a branch with git yourself.**
That works, but the review step does two things you then have to do by hand: tag the revision (`r<N>`) on `main`, and delete the merged branch. Choosing merge in the review is simpler.

## Prometheus commands

**Prometheus answered a request instead of planning it.**
Prometheus plans only when asked with `/workflow-new <request>`. A plain message, even one that describes work, gets an answer or a suggestion to use the command. Run `/workflow-new` with the request.

**"<workflow> has an active run (<id>); refresh it once the run has finished and its branch is reviewed"**
`/workflow-refresh` rewrites the workflow's copy of the skill, including the tool a running map stage uses, and its commit would land on the run's branch and show up in the end-of-run review. Finish the run, merge, keep or discard its branch, then refresh.

**"<workflow> has branch <name> checked out; refresh it after merging, keeping or discarding that branch"**
Same reason for a `run/…`, `revise/…` or `improve/…` branch left checked out for review.

**"<dest>/<slug> already exists" / "destination is inside the workflow"** (from `/workflow-export`)
Export never overwrites, and can't copy a workflow into itself. Pick another destination, or pass a different name.

## For contributors

The messages come from the Python scripts in [`.pi/skills/workflow/scripts/`](../.pi/skills/workflow/scripts/), which print `{"ok": false, "error": …}` (or `"errors": […]`) and exit non-zero; the extensions pass that on to the model. The run protocol ([`run-protocol.md`](../.pi/skills/workflow/references/run-protocol.md), "Failures") tells the workflow agent how to react. When you add a refusal, add a test in [`tests/`](../tests/) that triggers it, and a line here if a person running workflows can meet it.
