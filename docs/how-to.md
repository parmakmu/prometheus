# How-to guides

Short recipes for specific goals. Each assumes you have a workflow and have run it once; if not, start with the [tutorial](tutorial.md). They give the steps, not the reasons: for those, see [How Prometheus works](how-it-works.md). When a step is refused, look the message up in [Troubleshooting](troubleshooting.md).

**Running**
- [How to run a workflow on new input](#how-to-run-a-workflow-on-new-input)
- [How to add a secret](#how-to-add-a-secret)
- [How to decline an approval](#how-to-decline-an-approval)
- [How to relaunch a wave that failed](#how-to-relaunch-a-wave-that-failed)
- [How to stop a run](#how-to-stop-a-run)
- [How to check where a workflow stands](#how-to-check-where-a-workflow-stands)

**Changing**
- [How to change a workflow's plan](#how-to-change-a-workflows-plan)
- [How to improve a workflow from its past runs](#how-to-improve-a-workflow-from-its-past-runs)
- [How to review and merge a branch](#how-to-review-and-merge-a-branch)
- [How to make a stage use a connector](#how-to-make-a-stage-use-a-connector)
- [How to read what happened in an earlier session](#how-to-read-what-happened-in-an-earlier-session)

**Managing workflows**
- [How to run a workflow on another machine](#how-to-run-a-workflow-on-another-machine)
- [How to update workflows after updating Prometheus](#how-to-update-workflows-after-updating-prometheus)

## Running

### How to run a workflow on new input

1. In the workflow's `.env`, change the value of the param that names the input, for example `EMAIL_FOLDER='/Users/me/Mail/april'`. Use an absolute path.
2. Start Pi in the workflow's folder and run `/workflow-run`.
3. When the agent asks whether the input changed, answer yes.

Values persist across runs, so a value you don't change is used again. For the params, see [Params, `.env` and secrets](params-and-secrets.md).

### How to add a secret

1. Open the workflow's `.env` and add `NAME=value`. `NAME` is the variable the agent named (a param called `crm_token` reads `CRM_TOKEN`, unless its `env:` says otherwise).
2. Tell the agent it is set. You don't need to restart Pi: `.env` is read again before each step.

Don't paste the value into the chat. The agent can't read `.env`, and refuses to set a secret itself.

### How to decline an approval

1. When the agent asks to approve a side effect, choose the option that says not to run it. Nothing is launched.
2. Choose **stop** when asked what to do next. The run stays in `runs/`, marked stopped, and anything it wrote is offered for review: see [How to stop a run](#how-to-stop-a-run).
3. To take the side effect out of the plan for good, follow [How to change a workflow's plan](#how-to-change-a-workflows-plan).

The agent may also offer to replan without the stage. For a side effect that is refused: a replan can't remove one or weaken its approval. The earlier stages of a stopped run keep their results.

### How to relaunch a wave that failed

1. Read what the agent reports: the stage, and the cause (a gate that rejected an output, a review that found a blocking problem, or an error with its `failure_kind`).
2. Choose **relaunch** when asked. Children that completed and haven't changed are not run again.
3. If it fails again, choose **change the plan** (a replan) or **stop** instead. A wave is relaunched at most `limits.max_retries` times.

### How to stop a run

1. Tell the agent to stop the run. It describes what the run has done and asks you to confirm.
2. If the run changed anything, the agent shows the branch and asks you to merge, keep or discard it: see [How to review and merge a branch](#how-to-review-and-merge-a-branch).

A stopped run stays in `runs/`, marked stopped. You can't stop while a wave is running: wait for it to finish.

### How to check where a workflow stands

1. In the workflow's folder, run `/workflow-status`.
2. Read the state line, which names the state and the moves open from it, then the plan's validity, the last and next wave, and anything outstanding.

From Prometheus, `/workflow-list <slug>` shows the same for one workflow, and `/workflow-list` summarises all of them.

## Changing

### How to change a workflow's plan

1. Make sure no run is active: stop it first if it is.
2. Run `/workflow-revise <what to change>`, for example `/workflow-revise drop the discover stage`.
3. Answer the agent's questions. It edits the plan, validates it and runs the plan critic.
4. Review the diff when asked, and choose merge.

A change that touches only gates, stage programs or agents merges without the critic. The new revision is tagged `r<N>` on `main`. To undo a merged change, make another with `/workflow-revise`.

### How to improve a workflow from its past runs

1. Make sure the workflow has run at least once, and no run is active.
2. Run `/workflow-improve`. To aim it at a problem, add one: `/workflow-improve the approval question is hard to answer`.
3. For each proposal the agent shows, answer apply or skip. It states the evidence, the exact edit, and what skipping leaves.
4. Review the branch and choose merge: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

If nothing is new since the last improve and you gave no focus, it says so and does nothing.

### How to review and merge a branch

1. Let the agent show you the branch: it summarises the diff in words. To read it yourself, run `git diff main...<branch>` in the workflow's folder.
2. Choose one of three:
   - **merge**: `main` gets one commit, `r<revision>: <branch>`, and future runs use it. Needs a ready plan.
   - **keep**: back to `main`, the branch stays for later.
   - **discard**: back to `main`, the branch is deleted.

New runs and changes wait until this is done. If you keep a branch, review it later by running `/workflow-run`: it notices the branch and offers the choice again.

### How to make a stage use a connector

For a side effect that writes to an app or sends a message.

1. Create a project agent in the workflow's `.pi/agents/`, for example `.pi/agents/notion-writer.md`, whose `tools` list includes the connector: `mcp:<server>` or `mcp:<server>/<tool>`. For a command-line tool, include `bash` instead.
2. In the plan, set the stage's `agent:` to that agent. Do both through `/workflow-revise`, so the critic sees the change.
3. Check with `workflow_check`: it warns when a stage writes to an app or message with an agent that can't reach it.

Built-in agents such as `worker` have no connector tools.

### How to read what happened in an earlier session

1. Ask the agent to show its earlier sessions. It calls `workflow_sessions` with `list`: sessions newest first, with turns, cost and the first message.
2. Ask for a session by its file name, or by the time something went wrong: it calls `show`, around that timestamp or between record numbers.

Session files are too long to open in an editor or with Pi's read tool, so don't ask the agent to open them directly.

## Managing workflows

### How to run a workflow on another machine

1. In Prometheus, run `/workflow-export <slug> <destination>`. Add `--keep-state` to take the runs and git history too.
2. On the other machine, install Pi, pi-subagents and uv.
3. Start Pi in the copied folder and trust it.
4. Fill in the copy's `.env`, which lists each variable unset, or let `/workflow-run` ask for the values.

### How to update workflows after updating Prometheus

1. In Prometheus, run `/workflow-list`. Workflows marked *stale* came from an older version.
2. Run `/workflow-refresh <slug>`, or `/workflow-refresh --all` for every stale one.
3. If it refuses because of an active run or a checked-out branch, finish or review that first.

A refresh keeps your plan, `.env`, runs, gates, programs, agents, packages and the workflow's lessons.
