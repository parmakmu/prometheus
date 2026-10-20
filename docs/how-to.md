# How-to guides

These guides are for when you know what you want to do and just need the steps. Each one covers a single goal, and they assume you've already got an agent and run it at least once. If you haven't yet, the [tutorial](tutorial.md) is the place to start.

We've kept the guides to the point, so they say what to do rather than why. If you're curious about the reasons, [How prometheus works](how-it-works.md) has them. And if a step gets refused along the way, [Troubleshooting](troubleshooting.md) will tell you what the message means.

The examples use emails-to-excel, the agent from the tutorial. Put your own agent's name and values in their place.

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
- [How to give a stage a skill](#how-to-give-a-stage-a-skill)

**Running an agent from code**

- [How to release an agent](#how-to-release-an-agent)
- [How to run an agent from code](#how-to-run-an-agent-from-code)
- [How to approve a side effect after a run has started](#how-to-approve-a-side-effect-after-a-run-has-started)
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
2. Start the agent with `prometheus emails-to-excel` and type `/agent-run`. It doesn't ask whether the input changed: it compares the files' contents and runs again only what reads changed ones.

Any value you don't change is used again, since values persist across runs. The params themselves are described in [Params, `.env` and secrets](params-and-secrets.md).

Check: the new run's results are in its own folder, `runs/<id>/outputs/`, beside the earlier runs'.

### How to make a run cheaper

Use this when an agent's runs cost more than you'd like. Most of a run's cost is the agent's own model and the children that make judgments, so the steps go after those, the biggest saving first. Most steps are a change to the plan, which you ask for with `/agent-revise` ([How to change an agent's plan](#how-to-change-an-agents-plan)).

1. Run with `/agent-auto` instead of `/agent-run`. Code then launches and records the waves, and the model is woken only when something needs you.
2. Make each deterministic stage run its program as a plain command, for example `/agent-revise run read-emails as a command`. From the next run on, that stage costs nothing.
3. Choose cheaper models for the children, for example `/agent-revise use a cheaper model for the reviews`. The plan then names them under `models:` (`default` and `review`), or as `model:` on one stage. Reviews are usually the easiest place to save.
4. Set a ceiling, for example `/agent-revise ask me before going on once a run has spent $0.05`. The plan gets `limits.max_cost_usd`, and the agent asks you before the next wave once the run's children have spent that much.
5. Leave reuse on, as it is by default. A judgment already made on the same inputs is then not paid for again.

Check: `/agent-history` shows each run's cost, so you can compare the next run with the last one.

### How to run an agent unattended

Use this when you want an agent to run without you watching the chat, for example from a scheduled job.

1. Give the run its values first: edit the agent's `.env`, or use the agent's **Settings** in the web app.
2. Optionally, set `AGENT_NOTIFY_COMMAND` (for example `notify-send prometheus`, or a `curl` to a webhook), so you hear when the run stops. Set it in the job's environment, not in `.env`: see [Environment variables](commands.md#environment-variables).
3. In the agent's folder, run `pi -p "/agent-auto"`.

It runs every wave that needs nobody and stops at the first one that does, such as an approval. Start the agent with `prometheus emails-to-excel` to answer it, and type `/agent-run` to go on.

To run an agent with nobody involved at all, release it instead: [How to run an agent from code](#how-to-run-an-agent-from-code).

### How to add a secret

Use this when the agent asks you to add a variable to `.env`.

1. Open the agent's `.env` and add `NAME=value`, where `NAME` is the variable the agent named. A param called `crm_token` reads `CRM_TOKEN`, unless its `env:` names another variable.
2. Tell the agent the secret is set. You don't need to restart Pi, because `.env` is read again before each step.

Don't paste the value into the chat. The agent can't read `.env` and won't set a secret itself, so the value would only end up in the session's history.

### How to decline an approval

Use this when the agent asks to approve a side effect and you don't want it to run.

1. Choose the option that says not to run it. Nothing is launched.
2. The agent then asks whether to resolve the plan without that stage, or stop. To go on without it, choose to resolve: the run rewrites its pending stages so they don't need it, and the critic checks the change. To end the run, choose **stop**. The run stays in `runs/`, marked stopped, and the stages that ran keep their results. If the run wrote anything, it's offered for review, as in [How to stop a run](#how-to-stop-a-run).
3. To take the side effect out of the plan for good, follow [How to change an agent's plan](#how-to-change-an-agents-plan) once the run has stopped.

If the agent offers to drop the stage during the run, decline. A run can't remove a side effect from its own plan or weaken its approval, so only step 3 removes it.

### How to relaunch a wave that failed

Use this when the agent reports a failed wave.

1. Read what the agent reports: the stage, and the cause. It's one of three: a gate rejected an output, a review found a blocking problem, or the wave hit an error, such as a timeout.
2. Fix the cause if it is outside the agent, such as a missing file or a network problem.
3. Choose **relaunch**. Children that completed and haven't changed are not run again.
4. If the wave fails again, choose **change the plan** instead, and the run rewrites its pending stages, or choose **stop**. A wave is relaunched at most as many times as the plan's `limits.max_retries` allows.

### How to stop a run

Use this when a run should go no further, for example because its input was wrong.

1. Wait until no wave is running: a run can't be stopped in the middle of a wave. The agent tells you when the wave ends; until then, the stop is refused with "a wave of run … is running".
2. Tell the agent to stop the run. It describes what the run has done and asks you to confirm.
3. If the run changed anything, the agent shows you its branch and asks you to merge, keep or discard it, as in [How to review and merge a branch](#how-to-review-and-merge-a-branch).

Check: `/agent-status` reports no active run. The stopped run stays in `runs/`, marked stopped.

### How to check where an agent stands

Use this before you start something, or when you're not sure what the agent is waiting for.

1. In the agent, type `/agent-status`. It changes nothing.
2. Read its three parts: **Next** (how far the active run is and its next wave, or the waves a new run would do), **Waiting for you** (questions, decisions, approvals, a review, the budget, branches to review, missing values and secrets), and any **Problem** (an invalid plan, a wave that won't compile, a branch switched by hand).

From prometheus, `/agent-list emails-to-excel` shows one agent's plan status, active run, waves left, provisional stages, missing values and whether it validates and compiles; `/agent-list` summarises all of them. The states are listed in [Commands and tools](commands.md), under each command.

### How to read what happened in an earlier session

Use this to find out what the agent did in an earlier session, for example why a run failed.

1. Ask the agent to list its earlier sessions. It shows them newest first, with their turns, cost and first message.
2. Ask for one session by its file name, or by the time something went wrong. The agent shows the part around that time, or between two record numbers.

Ask the agent rather than opening the session files yourself. Their lines are too long for an editor, and the agent has a tool that reads them for you.

## Changing

### How to change an agent's plan

Use this between runs, to change what every later run does.

1. Make sure no run is active, and stop it first if one is: [How to stop a run](#how-to-stop-a-run).
2. Type `/agent-revise <what to change>`, for example `/agent-revise drop the discover stage`. The agent opens a `revise/…` branch.
3. Answer the agent's questions. It edits the plan, checks it and has the plan critic review the change.
4. Review the diff when asked, and choose **merge**: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

Check: `git log --oneline main` in the agent's folder shows the change as the newest commit, `r<N>: revise/…`.

The merge tags the plan's new revision `r<N>`. A change that touches only gates, stage programs, subagents or lessons merges without the critic and keeps the plan's revision number: its tag moves to the new commit. To undo a merged change, make another with `/agent-revise`.

### How to edit a plan by hand

Use this when you'd rather change `plan.yaml` yourself than describe the change.

1. With no run active, type `/agent-revise`. It opens a `revise/…` branch, and you edit there, just as the agent would.
2. Edit `plan.yaml`. If your editor has a YAML language server (VS Code with Red Hat's YAML extension, for example), it picks up the plan's schema from the file's second line, completes stage fields and marks misspelled ones as you type.
3. Run `prometheus check emails-to-excel`. It prints the errors and warnings and the waves the plan would run, and exits 1 if the plan isn't valid.
4. Back in the agent, tell it you've edited the plan. It has the plan critic review your edit, since an edited plan can't run or merge until the critic has seen it.
5. Merge the branch: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

### How to drop a discovery stage once the input is stable

Use this when your input keeps the same format from run to run, but each new batch still runs the discovery stage. Once the other stages run as programs, discovery can be most of a run's model cost. (On input it has already seen, discovery doesn't run: its last report is reused.)

Keep discovery if each run reads a different kind of input. That is what it's for.

1. Check the last few runs: the stages after discovery passed their gates, and nothing was left over for a person or a model to handle.
2. Follow [How to change an agent's plan](#how-to-change-an-agents-plan) with `/agent-revise drop the discover stage`. If other stages read the discovery report, say in the same request what they should do without it. In emails-to-excel, `read-emails` reads `discovery.md`.
3. Review the diff and merge.

If a later input has a format the programs don't handle, its gate fails the wave. Fix the programs with another `/agent-revise` rather than bringing discovery back.

### How to improve an agent from its past runs

Use this to let the agent propose fixes from what earlier runs and sessions show.

1. Make sure the agent has run at least once and no run is active.
2. Type `/agent-improve`. To aim it at a problem, name the problem: `/agent-improve the approval question is hard to answer`. To learn from a released run that failed elsewhere, name its bundle too: [How to fix a released agent after a failed run](#how-to-fix-a-released-agent-after-a-failed-run).
3. For each proposal, answer **apply** or **skip**. The agent shows its evidence, the exact edit, and what skipping leaves unfixed.
4. Review the branch and choose **merge**: [How to review and merge a branch](#how-to-review-and-merge-a-branch).

If nothing has happened since the last improve and you gave no focus, the agent says so and opens no branch.

### How to improve an agent from a run on another machine

Use this when a copy of the agent runs somewhere prometheus isn't installed, copied there with `/agent-export` or cloned, and you develop the agent here. Its runs and sessions never reach you through git, since `runs/` and `.pi/sessions` aren't in it.

1. On the other machine, in the agent, type `/agent-bundle`. It packs the active run, or the last one, into `runs/<slug>-r<revision>-<run>.tgz`. To pack an earlier run, name it (`/agent-bundle 2026-10-16-2`). It says what the run did, not what it read. If whoever develops the agent may see the run's data, add `--with-data`: the children's sessions and output, and the run's `work/` folder.
2. Check the warnings and the texts it lists, if any, then copy the file here.
3. Here, type `/agent-improve <file>` and answer the proposals as usual: [How to improve an agent from its past runs](#how-to-improve-an-agent-from-its-past-runs).
4. Merge the branch, then bring the change to the other machine: pull it there, or export the agent again.

A copy made with `/agent-export` has no git history, so the import warns that the commit that ran isn't in this repository. That's expected. Compare with the copy's revision instead, the `r<n>` in the bundle's name.

### How to review and merge a branch

Use this when a run or a change ends and the agent shows you a branch.

1. Read the agent's summary of the diff. To read the diff yourself, run `git diff main...<branch>` in the agent's folder.
2. Choose one of three:
   - **merge**: `main` gets one commit, `r<revision>: <branch>`, and every later run uses it. Only a ready plan can be merged.
   - **keep**: the agent goes back to `main`, and the branch stays for later.
   - **discard**: the agent goes back to `main`, and the branch is deleted.

New runs and changes wait until you have chosen. A kept branch shows in `/agent-status` under **Waiting for you** ("review branch …"). To review it later, check it out (`git switch <branch>`) and type `/agent-run`: it sees the branch and offers the choice again.

### How to make a stage use a connector

Use this for a stage that reads from an app or writes to one, such as a side effect that sends a message. A connector is an MCP server the agent carries in its `.pi/mcp.json`, so it works wherever the agent is copied, installed or served. Built-in subagents such as `worker` get no connector tools, so the stage also needs a subagent of its own.

1. Type `/agent-revise` and say what the stage should reach, for example `/agent-revise build-workbook should write to Notion`. You can name a server, or let the agent search the MCP registry (`agent_find_capability`). It looks for the app's own server first (Notion's is `com.notion/mcp`) and tells you when it proposes a third party's.
2. Approve the connector when the agent asks. The dialog shows the command it starts (an `npx` or `uvx` package pinned to one version) or the URL it connects to, the environment variables it reads, and why the agent wants it. Nothing is written if you decline.
3. Set the variables it reads, such as `NOTION_TOKEN`, in the environment you start `pi` from, not in `.env`: Pi starts the server, and `.env` never reaches Pi's environment. For a server that signs in with OAuth, run `/mcp login <server>` instead. Once it connects, the agent can see its tools and pick the ones the stage needs; before that, it waits rather than guess their names.
4. In the diff, check the three edits the agent makes on the branch:
   - the server's entry in `.pi/mcp.json`;
   - a subagent in `.pi/agents/`, such as `.pi/agents/notion-writer.md`, whose `tools` list has the connector (`mcp:<server>` or, better, `mcp:<server>/<tool>`);
   - the stage's `agent:` in the plan, naming that subagent.
5. Run `prometheus check emails-to-excel`. It must report no stage whose subagent "cannot reach" what it writes, and no connector that "is not configured".
6. Review the diff and merge.

A connector the agent adds during a run, because a discovery stage found it needs one, goes through the same approval and lands on the run's branch.

### How to give a stage a skill

Use this when a judgment stage needs know-how it would otherwise improvise: a format's quirks, a house style, a checklist. A skill is a folder with a `SKILL.md` that a stage's child reads when its task matches. Work a program can do, such as converting PDF or Word files to text, is better done by a package and a program: ask for that instead, and the agent adds the package with `agent_add_package`.

1. Type `/agent-revise` and say what the stage lacks, for example `/agent-revise the review stage should follow our tone guide`, or name a skill's repository. The agent can search Pi packages on npm and GitHub (`agent_find_capability`).
2. Approve the skill when the agent asks. The dialog shows its description, where it comes from (a git commit, or an npm package's version), how many files it has and which of them are code. Only the commit or version you saw is installed.
3. In the diff, check the skill's folder in `.pi/skills/<name>/`, with `SOURCE.json` naming where it came from, and the subagent in `.pi/agents/` that lists it (`skills: <name>`). Read the skill as you would a program: its text instructs every child that loads it, and its scripts run.
4. Run `prometheus check <slug>`, then review and merge.

## Running an agent from code

### How to release an agent

Use this when the agent's plan has settled and you want code, a script or a service, to run it with nobody deciding anything.

1. Make sure the plan needs no one at run time: no discovery stage still waiting to fill in later ones, and every stage program written and merged. Run the agent until that's so.
2. Make sure `main` is checked out, with nothing uncommitted and no run active.
3. Run `prometheus release emails-to-excel`. It shows whether the agent can be released, the interface callers will depend on (its params, outputs and the side effects callers approve), the stages that use a model, and what changed since the last release. Then it asks before it tags.
4. If it says the release can break callers, check that you mean it, then run `prometheus release emails-to-excel --breaking`. That raises the major version, so callers pinned to the old one aren't affected.

Check: `git tag` in the agent's folder lists the new version, such as `v1.0`.

To release a later change, merge it and run `prometheus release` again.

### How to run an agent from code

Use this to run a released agent from the shell or from a Python program. Every class and method used here is described in the [Python API](python-api.md).

1. Release it first: [How to release an agent](#how-to-release-an-agent).
2. From the shell, run it with its values and the side effects you approve up front:

   ```sh
   prometheus run emails-to-excel@v1 -p email_folder=mail/ -a build-workbook
   ```

   It prints each stage as it goes and the outputs at the end, and exits 0 when the run completed. Add `--json` if a script reads the output.
3. From Python, in an environment where prometheus is installed:

   ```python
   from prometheus import load_agent

   agent = load_agent("emails-to-excel@v1")       # the newest 1.x; agent.params lists what it takes
   result = agent.run(params={"email_folder": "mail/"}, approve=["build-workbook"])
   if result.ok:
       print(result.outputs["email_workbook"])    # a Path
   ```

   A value can be a path, bytes or an open file. A relative path is relative to your program, not to the run. Secrets aren't passed: they come from your environment, under the names the plan gives them.
4. To follow a run as it goes, start it instead of running it: `run = agent.start(…)`, then `for event in run.events(): …`, `run.wait()` or `run.cancel()`.
5. Pin the major version, as in `@v1`. A release that could break callers raises the major version, so `@v1` never picks one up.

If a run fails, `run.bundle(out_folder)` packs its evidence into one file for whoever develops the agent: [How to fix a released agent after a failed run](#how-to-fix-a-released-agent-after-a-failed-run).

Don't change anything in the release's folder, `~/.local/share/prometheus/releases/<slug>/<version>/`. Every later run of that version would use the change. To change the agent, change its repository and release again.

### How to approve a side effect after a run has started

Use this when the code that runs the agent should see what a side effect will write before it lets it happen.

1. Start the run without approving the side effect:

   ```python
   run = agent.start(params={"email_folder": "mail/"})
   result = run.wait()
   ```

2. The run stops before the stage, with `result.state == "input_required"`. Read `result.needs["approve"]`: each entry says which stage would run, what it `writes`, and why.
3. To let it go on, call `run.answer(approve=["build-workbook"])`, then `run.wait()` again. A decision stage is answered the same way, with `run.answer(decisions={"<stage>": <answer>})`.
4. To decline, call `run.cancel()`.

From the shell, `prometheus run` without `-a` asks you in the terminal instead. Over HTTP, see [How to serve an agent over HTTP](#how-to-serve-an-agent-over-http).

### How to install a release on a machine that only runs it

Use this on a server, or any machine that runs the agent but never changes it.

1. Release it first, where it's developed, and share it ([How to share an agent with your team](#how-to-share-an-agent-with-your-team)), so the machine can reach its repository.
2. On the machine, run `prometheus install <git url>@v1`. It fetches only the newest 1.x release and shows the agent's own code (gates, programs, subagents) before keeping it. No repository is kept, and nothing is refreshed: it runs as released.
3. Check it can run here: `prometheus doctor emails-to-excel@v1` lists its secrets, whether its packages install, and the models it needs.
4. Run it with `prometheus run emails-to-excel@v1`, `load_agent("emails-to-excel@v1")` or `prometheus serve emails-to-excel@v1`.

Check: `prometheus list` shows the version installed.

To upgrade, run `prometheus install <git url>@v1` again. A newer 1.x goes in beside the old one, whose runs stay, and `@v1` then means the newer one. A running `serve` picks it up when restarted.

### How to serve an agent over HTTP

Use this when other programs or agents should call the agent over the network.

1. Release it first: [How to release an agent](#how-to-release-an-agent). On a machine that only runs it, install the release: [How to install a release on a machine that only runs it](#how-to-install-a-release-on-a-machine-that-only-runs-it).
2. Put the agent's secrets in the environment, since a release has no `.env`, and start the server:

   ```sh
   prometheus serve emails-to-excel@v1 --host 0.0.0.0 --port 8080 --token <a long random string>
   ```

3. Give callers the token. They send it as `Authorization: Bearer <token>`. For emails-to-excel, a call looks like this:

   ```sh
   curl -H "Authorization: Bearer …" -F email_folder=@a.eml -F email_folder=@b.eml \
        -F approve=build-workbook "http://host:8080/runs?wait=true"
   ```

   The answer has a `url` for each output, to download it from.
4. To keep the server's disk from filling up, add `--keep 100` (say): old runs' files are then deleted as new runs end, but their history stays.
5. To serve several agents, run one `prometheus serve` for each, on its own port and with only its own secrets, behind a proxy. Behind a proxy, also pass `--public-url https://…`, so the A2A Agent Card and its artifact links name the address callers use (the HTTP API's links are relative, so they need nothing).

Check: `GET /health` answers without a token, and `GET /docs` shows the run form generated from the agent's interface.

Other agents can call it over A2A, and an agent built with prometheus does so with a stage of `pattern: a2a`. The endpoints and what a message carries are in [`prometheus serve`](commands.md#prometheus-serve). Containers, TLS, restarts and log shipping are left to whoever deploys it.

### How to fix a released agent after a failed run

Use this when a release ran somewhere else, from code, `prometheus run` or `serve`, and a run failed. You bundle the run where it failed and bring the bundle to where the agent is developed. There you fix the agent, check the fix on the same call, and release it.

1. Where it failed, find the run with `prometheus runs emails-to-excel`, and see how it went with `prometheus runs emails-to-excel <run>`. If the machine itself is the problem, such as a secret not set or packages that don't install, `prometheus doctor emails-to-excel@v1` says so, and there's nothing to fix in the agent.
2. Decide whether whoever fixes the agent may see the caller's data. By default the bundle says what the run did without what it read: how each stage ended, each child's error, and its tool calls with the results of those that failed. `--with-data` adds the children's sessions and output and the run's files, which a failure of judgment often needs, and lets the fix be checked on the same call (step 5). Add it only if the data's owner agrees.
3. Bundle the run:

   ```sh
   prometheus bundle emails-to-excel 20261016-101010-abcd            # or add --with-data
   ```

   It writes `emails-to-excel-v1.0-20261016-101010-abcd.tgz` in the current folder (`--out` for another). Behind `serve`, download it instead, from `GET /runs/<id>/bundle` (`?with_data=true`) with the token. From Python, `run.bundle()` writes it.

   Before you send it, check what it lists: errors and failed results can still quote the run's data, so param values in them are replaced by `<name>` and the texts are listed for you to read; secret values are replaced by `${NAME}`, and anything else that looks like a key is reported.
4. Copy the bundle to the machine where the agent is developed. In the agent, type `/agent-improve` with the bundle's path, and a focus if you want one: `/agent-improve emails-to-excel-v1.0-20261016-101010-abcd.tgz`. Answer the proposals as usual. The fixes wait on an `improve/…` branch, whose name the agent shows.
5. With a bundle made `--with-data`, you can check the fix on the bundled call before you merge. This runs that branch's committed state with the bundle's params and files:

   ```sh
   prometheus run emails-to-excel@improve/2026-10-16-1 --bundle emails-to-excel-v1.0-20261016-101010-abcd.tgz
   ```

   The bundled run's approvals aren't carried over. Each side effect asks again, because the secrets on this machine may reach real systems. A single run that passes shows that the failure is gone that time, not that it can't come back.
6. Merge the branch ([How to review and merge a branch](#how-to-review-and-merge-a-branch)), then release it ([How to release an agent](#how-to-release-an-agent)), and update where it runs.

If the import warns that the release's commit isn't on `main`, the agent here has moved on from what ran, or was never pulled. Pull it first if you can, so the fix starts from the code that failed.

## Sharing agents

### How to share an agent with your team

Use this to publish an agent you built so that others can install it.

1. Create an empty project for it on your git host, and copy its clone URL. On GitLab, the push in the next step can create the project for you, if your instance allows it.
2. Run `prometheus share emails-to-excel <url>`. It refuses files that look like secrets, then shows what will be public: the agent's own code, its packages and the lessons its runs taught it. Read the lessons, because they can quote your data.
3. If others will run it from code, release it before you share it: `share` pushes the releases too.
4. To make it installable by name, add it to prometheus's `catalog.yaml` (name, url, description, owner) in a merge request.

### How to install someone else's agent

Use this to work with an agent someone else built: run it, change it, and take their updates.

1. Run `prometheus install <url>`, or `prometheus install <name>` for an agent listed by `prometheus catalog`. (To only run one of its releases, install that instead: [How to install a release on a machine that only runs it](#how-to-install-a-release-on-a-machine-that-only-runs-it).)
2. Read what it shows you. The agent's own code (gates, programs, subagents) will run on your machine with your permissions, and its lessons go into every prompt. Everything prometheus owns in it, such as the extension and the skill copy, has already been replaced with your own trusted copy.
3. Answer yes to keep it. Then start it with `prometheus <slug>`, and let `/agent-run` ask you for its values.
4. Later, `prometheus pull <slug>` shows you the upstream's changes before it takes them.

### How to propose a change to an agent you installed

Use this when you've improved an agent someone else owns and want the change in their copy.

1. In the agent, make the change on a branch with `/agent-revise <change>` (or `/agent-improve`), and finish it until it's ready to merge.
2. When the agent asks you to merge, keep or discard the branch, don't answer yet: leave the branch checked out and, in a terminal, run `prometheus propose <slug>`. It pushes the branch to the upstream, and on GitLab it opens a merge request. Then answer **keep** in the agent.
3. Once the owner has merged it, delete your branch (`git branch -D <branch>`) and run `prometheus pull <slug>`.

## Setting up

### How to use prometheus from a browser

Use this when you'd rather plan and run agents in a web page than in a terminal.

1. In a clone of the prometheus repository, run `npm install` once. Then run `npm run ui:server` and, in a second terminal, `npm run ui:web`.
2. Open the link that `ui:server` prints (`http://localhost:3000/?token=…`).
3. Plan with prometheus in the chat. Click an agent on the left to talk to it, and *Files* to upload what it should read.

To set it up for a team, with its settings, sandboxes and limits, see [`ui/README.md`](../ui/README.md).

### How to run an agent on another machine

Use this to hand someone a copy of an agent to run, when they won't be developing it with you.

1. In prometheus, type `/agent-export <slug> <destination>`. Add `--keep-state` to copy the runs and the git history as well.
2. On the other machine, install what the tutorial's [Before you begin](tutorial.md#before-you-begin) lists.
3. In the copied folder, run `uv sync`: the export leaves out `.venv`, which holds the agent's Python packages.
4. Start Pi in the copied folder and accept the trust prompt.
5. Fill in the copy's `.env`, which lists each variable unset, or let `/agent-run` ask you for the values.

To share an agent with people who keep it up to date with you, use git instead: [How to share an agent with your team](#how-to-share-an-agent-with-your-team).

### How to keep agents in another folder

Use this when your agents should live somewhere other than `~/agents`, such as a folder that survives a devpod rebuild.

1. Tell prometheus where the agents go, in one of two ways:
   - Run `prometheus init --agents <folder>`. It saves the folder in `~/.config/prometheus/config.toml`, and picks up any agents already in it.
   - Or set `PROMETHEUS_AGENTS=<folder>` wherever you start `prometheus`. It wins over the saved folder. In a devpod, set it in `devcontainer.json`'s `remoteEnv`, on a path that survives a rebuild.
2. If you had agents in the old folder, move them to the new one.
3. Type `/agent-refresh <slug>` for each agent you moved (`--all` skips the ones already current). Each saved session records the folder it ran in, and the refresh points them at the new one, so they show up again.

Check: `prometheus list` shows the agents you moved.

### How to update agents after updating prometheus

Use this after you've upgraded prometheus, so your agents get its fixes.

1. Update prometheus itself with `uv tool upgrade prometheus` (or `prometheus upgrade`).
2. In prometheus, type `/agent-list` (or run `prometheus list`). An agent marked *update: /agent-refresh* (in `prometheus list`, *update: prometheus refresh*) was created or last refreshed by an older version.
3. Type `/agent-refresh <slug>` for one agent, or `/agent-refresh --all` for every agent that needs it.
4. If the refresh refuses because of an active run or a checked-out branch, finish the run or review the branch, then refresh again.

A refresh keeps the agent's plan, `.env`, runs, gates, programs, subagents, packages and lessons. It also adds any packages the new prometheus needs to the agent's `pyproject.toml`, and the agent installs them the next time it starts.
