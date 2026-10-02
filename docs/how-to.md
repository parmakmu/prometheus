# How-to guides

These guides are for when you know what you want to do and just need the steps. Each one covers a single goal, and they assume you've already got a workflow and run it at least once. If you haven't yet, the [tutorial](tutorial.md) is the place to start.

We've kept the guides to the point, so they say what to do rather than why. If you're curious about the reasons, [How prometheus works](how-it-works.md) has them. And if a step gets refused along the way, [Troubleshooting](troubleshooting.md) will tell you what the message means.

**Running**
- [How to run a workflow on new input](#how-to-run-a-workflow-on-new-input)
- [How to add a secret](#how-to-add-a-secret)
- [How to decline an approval](#how-to-decline-an-approval)
- [How to relaunch a wave that failed](#how-to-relaunch-a-wave-that-failed)
- [How to stop a run](#how-to-stop-a-run)
- [How to check where a workflow stands](#how-to-check-where-a-workflow-stands)
- [How to read what happened in an earlier session](#how-to-read-what-happened-in-an-earlier-session)

**Changing**
- [How to change a workflow's plan](#how-to-change-a-workflows-plan)
- [How to drop a discovery stage once the input is stable](#how-to-drop-a-discovery-stage-once-the-input-is-stable)
- [How to improve a workflow from its past runs](#how-to-improve-a-workflow-from-its-past-runs)
- [How to review and merge a branch](#how-to-review-and-merge-a-branch)
- [How to make a stage use a connector](#how-to-make-a-stage-use-a-connector)

**Managing workflows**
- [How to use prometheus from a browser](#how-to-use-prometheus-from-a-browser)
- [How to run a workflow on another machine](#how-to-run-a-workflow-on-another-machine)
- [How to update workflows after updating prometheus](#how-to-update-workflows-after-updating-prometheus)

## Running

### How to run a workflow on new input

Use this when the same job has new input, such as next month's emails.

1. In the workflow's `.env`, change the value of the param that names the input, for example `EMAIL_FOLDER='/Users/me/Mail/april'`. Use an absolute path.
2. Start Pi in the workflow's folder and run `/workflow-run`.
3. When the agent asks whether the input changed, answer yes.

Any value you don't change is used again, since values persist across runs. For the params themselves, see [Params, `.env` and secrets](params-and-secrets.md).

### How to add a secret

Use this when the agent asks you to add a variable to `.env`.

1. Open the workflow's `.env` and add `NAME=value`, where `NAME` is the variable the agent named. A param called `crm_token` reads `CRM_TOKEN`, unless its `env:` names another variable.
2. Tell the agent the secret is set. You don't need to restart Pi, because `.env` is read again before each step.

Don't paste the value into the chat: the agent can't read `.env` and refuses to set a secret itself, so the value would only end up in the session's history.

### How to decline an approval

Use this when the agent asks to approve a side effect and you don't want it to run.

1. Choose the option that says not to run it. Nothing is launched.
2. When asked what to do next, choose **stop**. The run stays in `runs/`, marked stopped, the stages that ran keep their results, and anything the run wrote is offered for review, as in [How to stop a run](#how-to-stop-a-run).
3. To take the side effect out of the plan for good, follow [How to change a workflow's plan](#how-to-change-a-workflows-plan) once the run has stopped.

If the agent offers to replan without the stage, decline: a replan can't remove a side effect or weaken its approval, so only step 3 removes it.

### How to relaunch a wave that failed

Use this when the agent reports a failed wave.

1. Read what the agent reports: the stage, and the cause, which is a gate that rejected an output, a review that found a blocking problem, or an error with its `failure_kind`.
2. Fix the cause if it is outside the workflow, such as a missing file or a network problem.
3. Choose **relaunch**. Children that completed and haven't changed are not run again.
4. If the wave fails again, choose **change the plan** (a replan) or **stop** instead. A wave is relaunched at most `limits.max_retries` times.

### How to stop a run

1. Wait until no wave is running: a run can't be stopped mid-wave. `/workflow-status` says `wave_running` while one is; the agent also tells you when a wave ends.
2. Tell the agent to stop the run. It describes what the run has done and asks you to confirm.
3. If the run changed anything, the agent shows you its branch and asks you to merge, keep or discard it, as in [How to review and merge a branch](#how-to-review-and-merge-a-branch).

The stopped run stays in `runs/`, marked stopped.

### How to check where a workflow stands

1. In the workflow's folder, run `/workflow-status`. It changes nothing.
2. Read the state line first, which names the state and the moves open from it, then the plan's validity, the last and next wave, and anything outstanding.

From prometheus, `/workflow-list <slug>` shows the same for one workflow, and `/workflow-list` summarises all of them.

### How to read what happened in an earlier session

Use this to find out what the agent did in an earlier session, for example why a run failed.

1. Ask the agent to list its earlier sessions. It shows them newest first, with their turns, cost and first message.
2. Ask for one session by its file name, or by the time something went wrong. The agent shows the part around that time, or between two record numbers.

Ask the agent for these rather than opening the session files yourself: their lines are too long for an editor or Pi's read tool, and the agent's `workflow_sessions` tool reads them for you.


## Changing

### How to change a workflow's plan

Use this between runs, to change what every later run does.

1. Make sure no run is active, and stop it first if one is: [How to stop a run](#how-to-stop-a-run).
2. Run `/workflow-revise <what to change>`, for example `/workflow-revise drop the discover stage`. The agent opens a `revise/…` branch.
3. Answer the agent's questions. It edits the plan, validates it and runs the plan critic.
4. Review the diff when asked, and choose merge: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

The merge tags the plan's revision `r<N>` on `main`. A change that touches only gates, stage programs, agents or lessons merges without the critic, and keeps the plan's revision number: its tag moves to the new commit. To undo a merged change, make another with `/workflow-revise`.

### How to drop a discovery stage once the input is stable

Use this when discovery keeps running although the input's format no longer changes. Once the other stages run as programs, a discovery stage can be most of a run's model cost; keep it if each run reads a different kind of input.

1. Check the last few runs: the stages after discovery passed their gates, and nothing was left over for a person or a model to handle.
2. Follow [How to change a workflow's plan](#how-to-change-a-workflows-plan) with `/workflow-revise drop the discover stage`. If other stages read the discovery report, as `read-emails` reads `discovery.md` in emails-to-excel, say in the same request what they should do without it.
3. Review the diff and merge.

If a later input has a format the programs don't handle, its gate fails the wave. Fix the programs with another `/workflow-revise` rather than bringing discovery back.

### How to improve a workflow from its past runs

Use this to let the agent propose fixes from what earlier runs and sessions show.

1. Make sure the workflow has run at least once and no run is active.
2. Run `/workflow-improve`. To aim it at a problem, name the problem: `/workflow-improve the approval question is hard to answer`.
3. For each proposal, answer apply or skip. The agent shows its evidence, the exact edit, and what skipping leaves unfixed.
4. Review the branch and choose merge: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

If nothing has happened since the last improve and you gave no focus, the agent says so and opens no branch.

### How to review and merge a branch

Use this when a run or a change ends and the agent shows you a branch.

1. Read the agent's summary of the diff. To read the diff yourself, run `git diff main...<branch>` in the workflow's folder.
2. Choose one of three:
   - **merge**: `main` gets one commit, `r<revision>: <branch>`, and every later run uses it. Only a ready plan can be merged.
   - **keep**: the workflow goes back to `main`, and the branch stays for later.
   - **discard**: the workflow goes back to `main`, and the branch is deleted.

New runs and changes wait until you have chosen. To review a kept branch later, run `/workflow-run`: it notices the branch and offers the choice again.

### How to make a stage use a connector

Use this for a side effect that writes to an app or sends a message. Built-in agents such as `worker` have no connector tools, so the stage needs a project agent.

1. Run `/workflow-revise` and ask for the stage to use the connector, naming the server, for example `/workflow-revise build-workbook should write to Notion through mcp:notion`.
2. In the diff, check the two edits the agent makes on the branch, where the critic sees them:
   - a project agent in the workflow's `.pi/agents/`, such as `.pi/agents/notion-writer.md`, whose `tools` list has the connector (`mcp:<server>` or `mcp:<server>/<tool>`), or `bash` for a command-line tool;
   - the stage's `agent:` in the plan, naming that agent.
3. Ask the agent to run `workflow_check`, and make sure it reports no stage that writes to an app or message with an agent that can't reach it.
4. Review the diff and merge.

## Managing workflows

### How to use prometheus from a browser

1. In the prometheus folder, run `npm install` once, then `npm run ui:server` and, in a second terminal, `npm run ui:web`.
2. Open the link `ui:server` prints (`http://localhost:3000/?token=…`).
3. Plan with prometheus in the chat; click a workflow on the left to talk to its own agent, and *Files* to upload what it should read.

For a team, the settings, sandboxes and limits are in [`ui/README.md`](../ui/README.md).

### How to run a workflow on another machine

1. In prometheus, run `/workflow-export <slug> <destination>`. Add `--keep-state` to copy the runs and the git history as well.
2. On the other machine, install Pi, pi-subagents and uv, as in the tutorial's [Before you begin](tutorial.md#before-you-begin).
3. Start Pi in the copied folder and accept the trust prompt.
4. Fill in the copy's `.env`, which lists each variable unset, or let `/workflow-run` ask you for the values.

### How to update workflows after updating prometheus

1. In prometheus, run `/workflow-list`. A workflow marked *stale* was created or last refreshed by an older version.
2. Run `/workflow-refresh <slug>` for one workflow, or `/workflow-refresh --all` for every stale one.
3. If the refresh refuses because of an active run or a checked-out branch, finish the run or review the branch first, then refresh again.

A refresh keeps the workflow's plan, `.env`, runs, gates, programs, agents, packages and lessons.
