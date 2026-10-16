# How-to guides

These guides are for when you know what you want to do and just need the steps. Each one covers a single goal, and they assume you've already got an agent and run it at least once. If you haven't yet, the [tutorial](tutorial.md) is the place to start.

We've kept the guides to the point, so they say what to do rather than why. If you're curious about the reasons, [How prometheus works](how-it-works.md) has them. And if a step gets refused along the way, [Troubleshooting](troubleshooting.md) will tell you what the message means.

**Running**
- [How to run an agent on new input](#how-to-run-an-agent-on-new-input)
- [How to make a run cheaper](#how-to-make-a-run-cheaper)
- [How to run an agent unattended](#how-to-run-an-agent-unattended)
- [How to add a secret](#how-to-add-a-secret)
- [How to decline an approval](#how-to-decline-an-approval)
- [How to relaunch a wave that failed](#how-to-relaunch-a-wave-that-failed)
- [How to stop a run](#how-to-stop-a-run)
- [How to check where an agent stands](#how-to-check-where-an-agent-stands)
- [How to read what happened in an earlier session](#how-to-read-what-happened-in-an-earlier-session)

**Changing**
- [How to change an agent's plan](#how-to-change-an-agents-plan)
- [How to edit a plan by hand](#how-to-edit-a-plan-by-hand)
- [How to drop a discovery stage once the input is stable](#how-to-drop-a-discovery-stage-once-the-input-is-stable)
- [How to improve an agent from its past runs](#how-to-improve-an-agent-from-its-past-runs)
- [How to improve an agent from a run on another machine](#how-to-improve-an-agent-from-a-run-on-another-machine)
- [How to review and merge a branch](#how-to-review-and-merge-a-branch)
- [How to make a stage use a connector](#how-to-make-a-stage-use-a-connector)

**Running an agent from code**
- [How to release an agent](#how-to-release-an-agent)
- [How to run an agent from code](#how-to-run-an-agent-from-code)
- [How to install a release on a machine that only runs it](#how-to-install-a-release-on-a-machine-that-only-runs-it)
- [How to serve an agent over HTTP](#how-to-serve-an-agent-over-http)
- [How to fix a released agent after a failed run](#how-to-fix-a-released-agent-after-a-failed-run)

**Sharing agents**
- [How to share an agent with your team](#how-to-share-an-agent-with-your-team)
- [How to install someone else's agent](#how-to-install-someone-elses-agent)
- [How to propose a change to an agent you installed](#how-to-propose-a-change-to-an-agent-you-installed)

**Setting up**
- [How to use prometheus from a browser](#how-to-use-prometheus-from-a-browser)
- [How to run an agent on another machine](#how-to-run-an-agent-on-another-machine)
- [How to keep agents in another folder](#how-to-keep-agents-in-another-folder)
- [How to update agents after updating prometheus](#how-to-update-agents-after-updating-prometheus)

## Running

### How to run an agent on new input

Use this when the same job has new input, such as next month's emails.

1. In the agent's `.env`, change the value of the param that names the input, for example `EMAIL_FOLDER='/Users/me/Mail/april'`. Use an absolute path.
2. Start the agent with `prometheus <slug>` and run `/agent-run`.
3. When the agent asks whether the input changed, answer yes.

Any value you don't change is used again, since values persist across runs. For the params themselves, see [Params, `.env` and secrets](params-and-secrets.md).

### How to make a run cheaper

Use this when an agent's runs cost more than you'd like. Most of a run's cost is the model that orchestrates it and the children that make judgments, so the steps below go after those, the biggest saving first:

1. Run with `/agent-auto` instead of `/agent-run`. Waves are then launched and recorded by code, and the model is woken only when something needs you.
2. Give each deterministic stage a program with its `args`. From the second run on, those stages run as plain commands, at no model cost.
3. Set cheaper models for the children in the plan, with `models: {default: <model>, review: <model>}`, or `model:` on a single stage. Reviews are usually the easiest place to save.
4. Set `limits.max_cost_usd`. Once a run's children have spent that much, the agent asks you before it launches the next wave.
5. Leave `reuse` on, as it is by default. A judgment already made on the same inputs is then not paid for again.

### How to run an agent unattended

Use this when you want an agent to run without you watching the chat, for example from a scheduled job.

1. Give the run its values first: edit the agent's `.env`, or use the agent's **Settings** in the web app.
2. Optionally, set `AGENT_NOTIFY_COMMAND` (for example `notify-send prometheus`, or a `curl` to a webhook), so you hear when the run stops.
3. In the agent's folder, run `pi -p "/agent-auto"`.

It runs every wave that needs nobody and stops at the first one that does, such as an approval. To run an agent with nobody involved at all, release it instead: [How to run an agent from code](#how-to-run-an-agent-from-code).

### How to add a secret

Use this when the agent asks you to add a variable to `.env`.

1. Open the agent's `.env` and add `NAME=value`, where `NAME` is the variable the agent named. A param called `crm_token` reads `CRM_TOKEN`, unless its `env:` names another variable.
2. Tell the agent the secret is set. You don't need to restart Pi, because `.env` is read again before each step.

Don't paste the value into the chat: the agent can't read `.env` and refuses to set a secret itself, so the value would only end up in the session's history.

### How to decline an approval

Use this when the agent asks to approve a side effect and you don't want it to run.

1. Choose the option that says not to run it. Nothing is launched.
2. When asked what to do next, choose **stop**. The run stays in `runs/`, marked stopped, the stages that ran keep their results, and anything the run wrote is offered for review, as in [How to stop a run](#how-to-stop-a-run).
3. To take the side effect out of the plan for good, follow [How to change an agent's plan](#how-to-change-an-agents-plan) once the run has stopped.

If the agent offers to drop the stage during the run, decline: resolving can't remove a side effect or weaken its approval, so only step 3 removes it.

### How to relaunch a wave that failed

Use this when the agent reports a failed wave.

1. Read what the agent reports: the stage, and the cause, which is a gate that rejected an output, a review that found a blocking problem, or an error with its `failure_kind`.
2. Fix the cause if it is outside the agent, such as a missing file or a network problem.
3. Choose **relaunch**. Children that completed and haven't changed are not run again.
4. If the wave fails again, choose **change the plan** (the run resolves its pending stages anew) or **stop** instead. A wave is relaunched at most `limits.max_retries` times.

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

1. With no run active, run `/agent-revise`. It opens a `revise/…` branch, and you edit there, just as the agent would.
2. Edit `plan.yaml`. If your editor has a YAML language server (VS Code with Red Hat's YAML extension, for example), it picks up the plan's schema from the file's second line, completes stage fields and marks misspelled ones as you type.
3. Run `prometheus check <slug>`. It prints the errors and warnings and the waves the plan would run, and exits 1 if the plan isn't valid.
4. Back in the agent, ask it to run the plan critic and record the result. An edited plan isn't ready until then.
5. Merge the branch: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

### How to drop a discovery stage once the input is stable

Use this when your input keeps the same format from run to run, but each new batch still runs the discovery stage. (On input it has already seen, discovery doesn't run: its last report is reused.) Once the other stages run as programs, discovery can be most of a run's model cost.

Keep discovery if each run reads a different kind of input: that is what it's for.

1. Check the last few runs: the stages after discovery passed their gates, and nothing was left over for a person or a model to handle.
2. Follow [How to change an agent's plan](#how-to-change-an-agents-plan) with `/agent-revise drop the discover stage`. If other stages read the discovery report, as `read-emails` reads `discovery.md` in emails-to-excel, say in the same request what they should do without it.
3. Review the diff and merge.

If a later input has a format the programs don't handle, its gate fails the wave. Fix the programs with another `/agent-revise` rather than bringing discovery back.

### How to improve an agent from its past runs

Use this to let the agent propose fixes from what earlier runs and sessions show.

1. Make sure the agent has run at least once and no run is active.
2. Run `/agent-improve`. To aim it at a problem, name the problem: `/agent-improve the approval question is hard to answer`. To learn from a released run that failed elsewhere, name its bundle too: [How to fix a released agent after a failed run](#how-to-fix-a-released-agent-after-a-failed-run).
3. For each proposal, answer apply or skip. The agent shows its evidence, the exact edit, and what skipping leaves unfixed.
4. Review the branch and choose merge: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

If nothing has happened since the last improve and you gave no focus, the agent says so and opens no branch.

### How to improve an agent from a run on another machine

Use this when a copy of the agent runs somewhere prometheus isn't installed, copied there with `/agent-export` or cloned, and you develop the agent here. Its runs and sessions never reach you through git, since `runs/` and `.pi/sessions` aren't in it.

1. On the other machine, in the agent, run `/agent-bundle`. It packs the active run, or the last one, into `runs/<slug>-r<revision>-<run>.tgz`. Name an earlier run to pack that one instead (`/agent-bundle 2026-10-16-2`), and add `--with-files` to include the run's `work/` folder.
2. Check the warnings it lists, if any, then copy the file here.
3. Here, run `/agent-improve <file>` and answer the proposals as usual: [How to improve an agent from its past runs](#how-to-improve-an-agent-from-its-past-runs).
4. Merge the branch, then bring the change to the other machine: pull it there, or export the agent again.

A copy made with `/agent-export` has no git history, so the import warns that the commit that ran isn't in this repository. That's expected: compare with the copy's revision (`r<n>` in the bundle's name) instead.

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

## Running an agent from code

### How to release an agent

Use this when the agent's plan has settled and you want code, a script or a service, to run it with nobody deciding anything.

1. Make sure the plan needs no one at run time: no discovery stage still waiting to fill in later ones, and every stage program written and merged. Run the agent until that's so.
2. Make sure `main` is checked out, with nothing uncommitted and no run active.
3. Run `prometheus release <slug>`. It shows whether the agent can be released, the interface callers will depend on (its params, outputs, side effects and decisions) and what changed since the last release, then asks before it tags.
4. If it says the release can break callers, check that you mean it, then run `prometheus release <slug> --breaking`. That raises the major version, so callers pinned to the old one aren't affected.

The release is a tag on `main`, `v<major>.<minor>`. To release a later change, merge it and run `prometheus release` again.

### How to run an agent from code

Use this to run a released agent from the shell or from a Python program. Every class and method used here is described in the [Python API](python-api.md).

1. Release it first: [How to release an agent](#how-to-release-an-agent).
2. From the shell, run it with its values and the side effects you approve up front:

   ```sh
   prometheus run emails-to-excel@v1 -p email_folder=mail/ -a build-workbook
   ```

   Add `--json` if a script reads the output.
3. From Python, in an environment where prometheus is installed:

   ```python
   from prometheus import load_agent

   agent = load_agent("emails-to-excel@v1")       # the newest 1.x; agent.params lists what it takes
   result = agent.run(params={"email_folder": "mail/"}, approve=["build-workbook"])
   if result.ok:
       print(result.outputs["email_workbook"])    # a Path
   ```

   A value can be a path, bytes or an open file. A relative path is relative to your program, not to the run.
4. To follow a run as it goes, start it instead of running it: `run = agent.start(…)`, then `for event in run.events(): …`, `run.wait()` or `run.cancel()`.
5. If a run fails, `run.bundle(out_folder)` packs its evidence into one file for whoever develops the agent ([How to fix a released agent after a failed run](#how-to-fix-a-released-agent-after-a-failed-run)).
6. To decide on a side effect later, leave it out of `approve`. The run then stops before it, with `result.state == "input_required"`, and `result.needs` says what the stage would write and which decisions it asks. Call `run.answer(approve=["build-workbook"])` (or `decisions={stage: answer}`) to let it go on, or `run.cancel()` to decline.
7. Pin the major version, as in `@v1`. A release that could break callers raises the major version, so `@v1` never picks one up.

Each call is a fresh run in the release's own folder, `~/.local/share/prometheus/releases/<slug>/<version>/` (`PROMETHEUS_RELEASES` moves it). That folder's `RELEASE.json` holds the release's record: version, commit and interface. Don't change anything there: every later run of that version would use it. The agent's commands aren't in that copy, so a change is made in the agent's repository and released again. Secrets come from your environment, under the names the plan gives them.

### How to install a release on a machine that only runs it

Use this on a server, or any machine that runs the agent but never changes it.

1. Release it first, where it's developed, and share it ([How to share an agent with your team](#how-to-share-an-agent-with-your-team)), so the machine can reach its repository.
2. On the machine, run `prometheus install <git url>@v1`. It fetches only the newest 1.x release, unpacks it into `~/.local/share/prometheus/releases/<slug>/<version>/`, and shows the agent's own code (gates, programs, subagents) before keeping it. No repository is kept, and nothing is refreshed: it runs as released.
3. Check it can run here: `prometheus doctor <slug>@v1` lists its secrets, whether its packages install, and the models it needs.
4. Run it with `prometheus run <slug>@v1`, `load_agent("<slug>@v1")` or `prometheus serve <slug>@v1`.
5. To upgrade, run `prometheus install <git url>@v1` again. A newer 1.x goes in beside the old one, whose runs stay, and `@v1` then means the newer one. A running `serve` picks it up when restarted.

`prometheus list` shows the versions installed here.

### How to serve an agent over HTTP

Use this when other programs or agents should call the agent over the network.

1. Release it first: [How to release an agent](#how-to-release-an-agent). On a machine that only runs it, install the release: [How to install a release on a machine that only runs it](#how-to-install-a-release-on-a-machine-that-only-runs-it).
2. Put the agent's secrets in the environment, since a release has no `.env`, and start the server:

   ```sh
   prometheus serve emails-to-excel@v1 --host 0.0.0.0 --port 8080 --token <a long random string>
   ```

3. Give callers the token. They send it as `Authorization: Bearer <token>`. `GET /docs` shows the run form generated from the agent's interface. For emails-to-excel, a call looks like this:

   ```sh
   curl -H "Authorization: Bearer …" -F email_folder=@a.eml -F email_folder=@b.eml \
        -F approve=build-workbook "http://host:8080/runs?wait=true"
   ```

   The answer has a `url` for each output, to download it from.
4. To serve several agents, run one `prometheus serve` for each, on its own port and with only its own secrets, behind a proxy. Behind a proxy, also pass `--public-url https://…`, so the links the server gives out name the address callers use.

Callers never send paths. Inputs arrive as uploaded files, and outputs stay in the run until they are downloaded. Each run lives in the release's folder (`PROMETHEUS_RELEASES`), so a restarted server still answers for earlier runs, and a call that repeats an earlier call's judgment on the same files reuses it. To keep the cache from growing, pass `--keep 100` (say): old runs' files are then deleted as new runs end. Outputs nobody downloaded by then are gone, but the runs' history stays. The newest 10 failed runs are kept whole on top of that, so there is something to bundle when one fails.

Containers, TLS, restarts and log shipping are left to whoever deploys it. `serve` logs one JSON line per request and run to stdout, and `/health` answers without a token.

Other agents can also call it over [A2A](https://a2a-protocol.org):

- They read its Agent Card at `/.well-known/agent-card.json`.
- They send a message to `/message:send` with a file part for each input file (`raw`, base64, and `filename`; `metadata.param` names the param when the agent takes several files) and a data part such as `{"params": {...}, "approve": ["build-workbook"]}`.
- The answer is a task whose artifacts link to the outputs.

An agent built with prometheus calls another one with a stage of `pattern: a2a`.

### How to fix a released agent after a failed run

Use this when a release ran somewhere else, from code, `prometheus run` or `serve`, and a run failed. You bundle the run where it failed, bring the bundle to where the agent is developed, fix the agent there, check the fix on the same call, then release it.

1. Where it failed, find the run with `prometheus runs emails-to-excel`, and see how it went with `prometheus runs emails-to-excel <run>`. If the machine itself is the problem (a secret not set, packages that don't install), `prometheus doctor emails-to-excel@v1` says so, and there's nothing to fix in the agent.
2. Bundle the run:

   ```sh
   prometheus bundle emails-to-excel 20261016-101010-abcd
   ```

   Behind `serve`, download it instead: `GET /runs/<id>/bundle`, with the token. A program that runs the agent with `load_agent` can write it itself, with `run.bundle()`. The bundle holds the run's records, pi-subagents' records of its launches, and each child's session and output. The values of the agent's secrets are replaced by `${NAME}`, and anything else that looks like a key is reported, so check those warnings before you send it on.
3. To rerun the same call later, the bundle needs the files the caller sent. Add `--with-files` (`?with_files=true` from `serve`) when you may hand the caller's data to whoever fixes the agent.
4. Copy the bundle to the machine where the agent is developed. In the agent, run `/agent-improve` with the bundle's path, and a focus if you want one: `/agent-improve emails-to-excel-v1.0-20261016-101010-abcd.tgz`. The bundle is unpacked into `runs/imported/` and is read as evidence, like the agent's own runs. Answer the proposals as usual.
5. Before you merge, check the fix on the bundled call. Run the branch's committed state as a build, with the bundle's params, approvals and files:

   ```sh
   prometheus run emails-to-excel@improve/2026-10-16-1 --bundle emails-to-excel-v1.0-20261016-101010-abcd.tgz
   ```

   A bundle without files can still be rerun when you give the caller's files yourself, with `--param <name>=<path>`.
6. Merge the branch ([How to review and merge a branch](#how-to-review-and-merge-a-branch)), then release it ([How to release an agent](#how-to-release-an-agent)), and update where it runs.

If the import warns that the release's commit isn't on `main`, the agent here has moved on from what ran, or was never pulled. Pull it first if you can, so the fix starts from the code that failed.

## Sharing agents

### How to share an agent with your team

Use this to publish an agent you built so that others can install it.

1. Create an empty project for it on your git host, and copy its clone URL. On GitLab, the push in the next step can create the project for you, if your instance allows it.
2. Run `prometheus share <slug> <url>`. It refuses files that look like secrets, then shows what will be public: the agent's own code, its packages and the lessons its runs taught it. Read the lessons: they can quote your data.
3. If others will run it from code, release it before you share it: `share` pushes the releases too.
4. To make it installable by name, add it to prometheus's `catalog.yaml` (name, url, description, owner) in a merge request.

### How to install someone else's agent

1. Run `prometheus install <url>`, or `prometheus install <name>` for an agent listed by `prometheus catalog`. (To only run one of its releases, install that instead: [How to install a release on a machine that only runs it](#how-to-install-a-release-on-a-machine-that-only-runs-it).)
2. Read what it shows you. Its own code (gates, programs, subagents) will run on your machine with your permissions, and its lessons go into every prompt. Everything prometheus owns in it, such as the extension and the skill copy, has already been replaced with your own trusted copy.
3. Answer yes to keep it. Then start it with `prometheus <slug>`, and let `/agent-run` ask you for its values.
4. Later, `prometheus pull <slug>` shows you the upstream's changes before it takes them.

### How to propose a change to an agent you installed

1. In the agent, make the change on a branch with `/agent-revise <change>` (or `/agent-improve`), and finish it until it's ready to merge.
2. Instead of merging it, run `prometheus propose <slug>`. It pushes the branch to the upstream, and on GitLab it opens a merge request.
3. Once the owner has merged it, discard your branch and run `prometheus pull <slug>`.

## Setting up

### How to use prometheus from a browser

1. In a clone of the prometheus repository, run `npm install` once. Then run `npm run ui:server` and, in a second terminal, `npm run ui:web`.
2. Open the link that `ui:server` prints (`http://localhost:3000/?token=…`).
3. Plan with prometheus in the chat. Click an agent on the left to talk to it, and *Files* to upload what it should read.

To set it up for a team, with its settings, sandboxes and limits, see [`ui/README.md`](../ui/README.md).

### How to run an agent on another machine

1. In prometheus, run `/agent-export <slug> <destination>`. Add `--keep-state` to copy the runs and the git history as well.
2. On the other machine, install what the tutorial's [Before you begin](tutorial.md#before-you-begin) lists.
3. Start Pi in the copied folder and accept the trust prompt.
4. Fill in the copy's `.env`, which lists each variable unset, or let `/agent-run` ask you for the values.

To share an agent with people who keep it up to date with you, use git instead: [How to share an agent with your team](#how-to-share-an-agent-with-your-team).

### How to keep agents in another folder

1. Tell prometheus where the agents go, in one of two ways:
   - Run `prometheus init --agents <folder>`. It saves the folder in `~/.config/prometheus/config.toml`, and picks up any agents already in it.
   - Or set `PROMETHEUS_AGENTS=<folder>` wherever you start `prometheus`. It wins over the saved folder. In a devpod, set it in `devcontainer.json`'s `remoteEnv`, on a path that survives a rebuild.
2. If you had agents in the old folder, move them to the new one.
3. Run `/agent-refresh <slug>` for each agent you moved (`--all` skips the ones already current). Each saved session records the folder it ran in, and the refresh points them at the new one, so they show up again.

### How to update agents after updating prometheus

1. Update prometheus itself with `uv tool upgrade prometheus` (or `prometheus upgrade`).
2. In prometheus, run `/agent-list` (or `prometheus list`). An agent marked *update: /agent-refresh* was created or last refreshed by an older version.
3. Run `/agent-refresh <slug>` for one agent, or `/agent-refresh --all` for every agent that needs it.
4. If the refresh refuses because of an active run or a checked-out branch, finish the run or review the branch, then refresh again.

A refresh keeps the agent's plan, `.env`, runs, gates, programs, subagents, packages and lessons. It also adds any packages the new prometheus needs to the agent's `pyproject.toml`, and the agent installs them the next time it starts.
