# How-to guides

These guides are for when you know what you want to do and just need the steps. Each one covers a single goal, and they assume you've already got an agent and run it at least once. If you haven't yet, the [tutorial](tutorial.md) is the place to start.

We've kept the guides to the point, so they say what to do rather than why. If you're curious about the reasons, [How prometheus works](how-it-works.md) has them. And if a step gets refused along the way, [Troubleshooting](troubleshooting.md) will tell you what the message means.

**Running**
- [How to run an agent on new input](#how-to-run-a-agent-on-new-input)
- [How to add a secret](#how-to-add-a-secret)
- [How to decline an approval](#how-to-decline-an-approval)
- [How to relaunch a wave that failed](#how-to-relaunch-a-wave-that-failed)
- [How to stop a run](#how-to-stop-a-run)
- [How to check where an agent stands](#how-to-check-where-a-agent-stands)
- [How to read what happened in an earlier session](#how-to-read-what-happened-in-an-earlier-session)

**Changing**
- [How to change an agent's plan](#how-to-change-a-agents-plan)
- [How to edit a plan by hand](#how-to-edit-a-plan-by-hand)
- [How to drop a discovery stage once the input is stable](#how-to-drop-a-discovery-stage-once-the-input-is-stable)
- [How to improve an agent from its past runs](#how-to-improve-a-agent-from-its-past-runs)
- [How to review and merge a branch](#how-to-review-and-merge-a-branch)
- [How to make a stage use a connector](#how-to-make-a-stage-use-a-connector)

**Managing agents**
- [How to use prometheus from a browser](#how-to-use-prometheus-from-a-browser)
- [How to run an agent on another machine](#how-to-run-a-agent-on-another-machine)
- [How to update agents after updating prometheus](#how-to-update-agents-after-updating-prometheus)

## Running

### How to run an agent on new input

Use this when the same job has new input, such as next month's emails.

1. In the agent's `.env`, change the value of the param that names the input, for example `EMAIL_FOLDER='/Users/me/Mail/april'`. Use an absolute path.
2. Start Pi in the agent's folder and run `/agent-run`.
3. When the agent asks whether the input changed, answer yes.

Any value you don't change is used again, since values persist across runs. For the params themselves, see [Params, `.env` and secrets](params-and-secrets.md).

### How to make a run cheaper

Most of a run's cost is the model that orchestrates it and the children that make judgments. In order of effect:

1. Run with `/agent-auto` instead of `/agent-run`: waves are launched and recorded by code, and the model is woken only when something needs you.
2. Give deterministic stages a program with its `args`: from the second run they run as commands, at no model cost.
3. Set cheap models for children in the plan: `models: {default: <model>, review: <model>}`, or `model:` on one stage. Reviews are the easiest to make cheaper.
4. Set `limits.max_cost_usd`: once a run's children have spent that much, the agent asks before the next wave.
5. Leave `reuse` on (the default): a judgment made on the same inputs is not made again.

### How to run an agent unattended

In the agent's folder, run `pi -p "/agent-auto"`. It runs the waves that need nobody and stops at the first that does. Set `AGENT_NOTIFY_COMMAND` (for example `notify-send prometheus`) to hear when it stops. To give the next run its values without a chat, use the agent's **Settings** in the web app, or edit `.env`.

### How to add a secret

Use this when the agent asks you to add a variable to `.env`.

1. Open the agent's `.env` and add `NAME=value`, where `NAME` is the variable the agent named. A param called `crm_token` reads `CRM_TOKEN`, unless its `env:` names another variable.
2. Tell the agent the secret is set. You don't need to restart Pi, because `.env` is read again before each step.

Don't paste the value into the chat: the agent can't read `.env` and refuses to set a secret itself, so the value would only end up in the session's history.

### How to decline an approval

Use this when the agent asks to approve a side effect and you don't want it to run.

1. Choose the option that says not to run it. Nothing is launched.
2. When asked what to do next, choose **stop**. The run stays in `runs/`, marked stopped, the stages that ran keep their results, and anything the run wrote is offered for review, as in [How to stop a run](#how-to-stop-a-run).
3. To take the side effect out of the plan for good, follow [How to change an agent's plan](#how-to-change-a-agents-plan) once the run has stopped.

If the agent offers to replan without the stage, decline: a replan can't remove a side effect or weaken its approval, so only step 3 removes it.

### How to relaunch a wave that failed

Use this when the agent reports a failed wave.

1. Read what the agent reports: the stage, and the cause, which is a gate that rejected an output, a review that found a blocking problem, or an error with its `failure_kind`.
2. Fix the cause if it is outside the agent, such as a missing file or a network problem.
3. Choose **relaunch**. Children that completed and haven't changed are not run again.
4. If the wave fails again, choose **change the plan** (a replan) or **stop** instead. A wave is relaunched at most `limits.max_retries` times.

### How to stop a run

1. Wait until no wave is running: a run can't be stopped mid-wave. `/agent-status` says `wave_running` while one is; the agent also tells you when a wave ends.
2. Tell the agent to stop the run. It describes what the run has done and asks you to confirm.
3. If the run changed anything, the agent shows you its branch and asks you to merge, keep or discard it, as in [How to review and merge a branch](#how-to-review-and-merge-a-branch).

The stopped run stays in `runs/`, marked stopped.

### How to check where an agent stands

1. In the agent's folder, run `/agent-status`. It changes nothing.
2. Read the state line first, which names the state and the moves open from it, then the plan's validity, the last and next wave, and anything outstanding.

From prometheus, `/agent-list <slug>` shows the same for one agent, and `/agent-list` summarises all of them.

### How to read what happened in an earlier session

Use this to find out what the agent did in an earlier session, for example why a run failed.

1. Ask the agent to list its earlier sessions. It shows them newest first, with their turns, cost and first message.
2. Ask for one session by its file name, or by the time something went wrong. The agent shows the part around that time, or between two record numbers.

Ask the agent for these rather than opening the session files yourself: their lines are too long for an editor or Pi's read tool, and the agent's `agent_sessions` tool reads them for you.


## Changing

### How to change an agent's plan

Use this between runs, to change what every later run does.

1. Make sure no run is active, and stop it first if one is: [How to stop a run](#how-to-stop-a-run).
2. Run `/agent-revise <what to change>`, for example `/agent-revise drop the discover stage`. The agent opens a `revise/…` branch.
3. Answer the agent's questions. It edits the plan, validates it and runs the plan critic.
4. Review the diff when asked, and choose merge: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

The merge tags the plan's revision `r<N>` on `main`. A change that touches only gates, stage programs, agents or lessons merges without the critic, and keeps the plan's revision number: its tag moves to the new commit. To undo a merged change, make another with `/agent-revise`.

### How to edit a plan by hand

Use this when you'd rather change `plan.yaml` yourself than describe the change.

1. Run `/agent-revise` (no run active): you edit on the `revise/…` branch it opens, as the agent would.
2. Edit `plan.yaml`. In an editor with a YAML language server (VS Code with Red Hat's YAML extension, …), the file's
   second line points it at the plan's schema: it completes stage fields and marks misspelled ones as you type.
3. Run `prometheus check <slug>`: it prints the errors and warnings and the waves the plan would run, and exits 1 if
   the plan isn't valid.
4. Back in the agent, ask it to run the plan critic and record it (an edited plan is not ready until then), then
   merge: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

### How to drop a discovery stage once the input is stable

Use this when discovery keeps running on new input although its format no longer changes (on unchanged input it doesn't run: its last report is reused). Once the other stages run as programs, a discovery stage can be most of a run's model cost; keep it if each run reads a different kind of input.

1. Check the last few runs: the stages after discovery passed their gates, and nothing was left over for a person or a model to handle.
2. Follow [How to change an agent's plan](#how-to-change-a-agents-plan) with `/agent-revise drop the discover stage`. If other stages read the discovery report, as `read-emails` reads `discovery.md` in emails-to-excel, say in the same request what they should do without it.
3. Review the diff and merge.

If a later input has a format the programs don't handle, its gate fails the wave. Fix the programs with another `/agent-revise` rather than bringing discovery back.

### How to improve an agent from its past runs

Use this to let the agent propose fixes from what earlier runs and sessions show.

1. Make sure the agent has run at least once and no run is active.
2. Run `/agent-improve`. To aim it at a problem, name the problem: `/agent-improve the approval question is hard to answer`.
3. For each proposal, answer apply or skip. The agent shows its evidence, the exact edit, and what skipping leaves unfixed.
4. Review the branch and choose merge: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

If nothing has happened since the last improve and you gave no focus, the agent says so and opens no branch.

### How to review and merge a branch

Use this when a run or a change ends and the agent shows you a branch.

1. Read the agent's summary of the diff. To read the diff yourself, run `git diff main...<branch>` in the agent's folder.
2. Choose one of three:
   - **merge**: `main` gets one commit, `r<revision>: <branch>`, and every later run uses it. Only a ready plan can be merged.
   - **keep**: the agent goes back to `main`, and the branch stays for later.
   - **discard**: the agent goes back to `main`, and the branch is deleted.

New runs and changes wait until you have chosen. To review a kept branch later, run `/agent-run`: it notices the branch and offers the choice again.

### How to make a stage use a connector

Use this for a side effect that writes to an app or sends a message. Built-in agents such as `worker` have no connector tools, so the stage needs a subagent.

1. Run `/agent-revise` and ask for the stage to use the connector, naming the server, for example `/agent-revise build-workbook should write to Notion through mcp:notion`.
2. In the diff, check the two edits the agent makes on the branch, where the critic sees them:
   - a subagent in the agent's `.pi/agents/`, such as `.pi/agents/notion-writer.md`, whose `tools` list has the connector (`mcp:<server>` or `mcp:<server>/<tool>`), or `bash` for a command-line tool;
   - the stage's `agent:` in the plan, naming that agent.
3. Ask the agent to run `agent_check`, and make sure it reports no stage that writes to an app or message with an agent that can't reach it.
4. Review the diff and merge.

## Managing agents

### How to use prometheus from a browser

1. In the prometheus folder, run `npm install` once, then `npm run ui:server` and, in a second terminal, `npm run ui:web`.
2. Open the link `ui:server` prints (`http://localhost:3000/?token=…`).
3. Plan with prometheus in the chat; click an agent on the left to talk to its own agent, and *Files* to upload what it should read.

For a team, the settings, sandboxes and limits are in [`ui/README.md`](../ui/README.md).

### How to run an agent on another machine

1. In prometheus, run `/agent-export <slug> <destination>`. Add `--keep-state` to copy the runs and the git history as well.
2. On the other machine, install Pi, pi-subagents and uv, as in the tutorial's [Before you begin](tutorial.md#before-you-begin).
3. Start Pi in the copied folder and accept the trust prompt.
4. Fill in the copy's `.env`, which lists each variable unset, or let `/agent-run` ask you for the values.

### How to run an agent from code

1. Release it: `prometheus release <slug>` (see [The prometheus command](commands.md#the-prometheus-command)). A plan still being settled (a provisional stage) or missing a program can't be released yet.
2. From the shell: `prometheus run <slug>@v1 -p email_folder=mail/ -a build-workbook`. Add `--json` for a script to read.
3. From Python, in an environment with prometheus installed:

   ```python
   from prometheus import load_agent

   agent = load_agent("emails-to-excel@v1")       # the newest 1.x; agent.params lists what it takes
   result = agent.run(params={"email_folder": "mail/"}, approve=["build-workbook"])
   if result.ok:
       print(result.outputs["email_workbook"])    # a Path
   ```

   A value can be a path (a relative one is yours, not the run's), bytes or an open file. To follow a run, `run = agent.start(…)`, then `for event in run.events(): …`, `run.wait()` or `run.cancel()`.
   Leave a side effect out of `approve` and the run stops before it: `result.state == "input_required"`, and `result.needs` says what the stage would write (and which decisions it asks). `run.answer(approve=["build-workbook"])` (or `decisions={stage: answer}`) lets it go on; `run.cancel()` declines.
4. Pin the major version (`@v1`): a release that could break callers raises it, so `@v1` never picks one up.

Each call is a fresh run in the release's own folder under `~/.cache/prometheus/releases/` (`PROMETHEUS_CACHE` moves it). Secrets come from your environment, by the name the plan gives them.

### How to serve an agent over HTTP

1. Release it (`prometheus release <slug>`).
2. Run `prometheus serve <slug>@v1 --host 0.0.0.0 --port 8080 --token <a long random string>`, with the agent's secrets in the environment (`.env` is not read: a release has none).
3. Callers send `Authorization: Bearer <token>`. `GET /docs` shows the run form: for emails-to-excel, `curl -H "Authorization: Bearer …" -F email_folder=@a.eml -F email_folder=@b.eml -F approve=build-workbook "http://host:8080/runs?wait=true"`, then download the output from the `url` in the answer.
4. For several agents, run one `prometheus serve` each (on its own port, with only its own secrets) behind a proxy. Containers, TLS, restarts and log shipping are the deployment's: `serve` logs one JSON line per request and run to stdout, and `/health` needs no token.

Other agents can call it over [A2A](https://a2a-protocol.org): they read its Agent Card at `/.well-known/agent-card.json` and send a message to `/message:send`, with a file part (`raw`, base64, and `filename`; `metadata.param` names the param when the agent takes several files) and a data part `{"params": {...}, "approve": ["build-workbook"]}`. The answer is a task whose artifacts link to the outputs. Behind a proxy, pass `--public-url https://…` so the card and the links name the address callers use.

Callers never send paths: inputs arrive as uploaded files and outputs stay in the run until downloaded. Each run lives in the release's cache folder (`PROMETHEUS_CACHE`), so a restarted server still answers for earlier runs, and a call that repeats an earlier one's judgment on the same files reuses it. Pass `--keep 100` (say) so old runs' files are deleted as new ones end: outputs not downloaded by then are gone, their history stays.

### How to share an agent with your team

1. Create an empty project for it on your git host (on GitLab, the push below can create it), and copy its clone URL.
2. Run `prometheus share <slug> <url>`. It refuses files that look like secrets, then shows what will be public: the agent's own code and the lessons its runs taught it. Read the lessons: they can quote your data.
3. Release it first (`prometheus release <slug>`) if others will run it from code: `share` pushes the releases too.
4. To list it by name, add it to prometheus's `catalog.yaml` (name, url, description, owner) in a merge request.

### How to install someone else's agent

1. `prometheus install <url>`, or `prometheus install <name>` for one in `prometheus catalog`.
2. Read what it shows: its own code runs on your machine with your permissions, and its lessons go into every prompt. prometheus has already replaced everything it owns (the extension, the skill copy) with your copy.
3. Answer yes to keep it, then start it with `prometheus <slug>` and set its params with `/agent-run`.
4. Later, `prometheus pull <slug>` shows the upstream's changes before taking them.

### How to propose a change to an agent you installed

1. In the agent, make the change on a branch: `/agent-revise <change>` (or `/agent-improve`), and finish it until it is ready to merge.
2. Instead of merging it, run `prometheus propose <slug>`. It pushes the branch to the upstream; on GitLab a merge request opens.
3. Once the owner merges it, discard your branch and `prometheus pull <slug>`.

### How to keep agents in another folder

1. Run `prometheus init --agents <folder>`; it saves the folder in `~/.config/prometheus/config.toml`, and takes over the agents already in it.
2. Or set `PROMETHEUS_AGENTS=<folder>` where you start `prometheus` (in a devpod, in `devcontainer.json`'s `remoteEnv`, on a path that survives a rebuild). It wins over the saved folder.
3. Move existing agents with the folder, then run `/agent-refresh <slug>` for each, so its saved sessions show up again (each records the folder it ran in; `--all` skips agents already current).

### How to update agents after updating prometheus

0. Update prometheus itself with `uv tool upgrade prometheus`.
1. In prometheus, run `/agent-list`. An agent marked *stale* was created or last refreshed by an older version.
2. Run `/agent-refresh <slug>` for one agent, or `/agent-refresh --all` for every stale one.
3. If the refresh refuses because of an active run or a checked-out branch, finish the run or review the branch first, then refresh again.

A refresh keeps the agent's plan, `.env`, runs, gates, programs, agents, packages and lessons.
