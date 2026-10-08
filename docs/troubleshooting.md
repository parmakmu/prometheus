# Troubleshooting

When something goes wrong in prometheus, it usually shows up as a **refusal**: a script declines to take a step, explains why, and leaves your agent exactly as it was. That can feel abrupt, but it's the system protecting your work, and nothing is lost. The agent passes the message on and usually suggests what to do next.

If you've hit one and want to understand it, you're in the right place. The entries are grouped by when you're likely to meet them, and each one starts with what you'll see (the message as printed, with `<…>` for the parts that vary), then explains the cause and the fix. The quickest way in is to search this page for a few words of the message.

Many refusals need nothing from you at all, because the agent knows how to recover. We've gathered those at the end, under [Handled by the agent](#handled-by-the-agent), so you can quickly tell whether a message is yours to deal with.

To see where an agent stands and which moves are open, run `/agent-status`.

## Setup

### Commands are missing

The `/agent-*` commands don't appear in Pi.

**Cause.** Pi loads a project's extension only once you trust the folder, and prometheus and each agent are separate projects.

**Fix.** Start the planner with `prometheus` and an agent with `prometheus <slug>` (or `pi` in its folder), and accept the trust prompt. Each agent needs trusting on its own, including a copy made with `/agent-export`.

### prometheus is not set up

```text
prometheus is not set up: run `prometheus init` (or set PROMETHEUS_AGENTS)
```

**Cause.** `prometheus` was started with no terminal to ask in (a script, a devpod's setup command) before `prometheus init` ever ran, and `PROMETHEUS_AGENTS` isn't set.

**Fix.** Run `prometheus init --yes` (add `--agents <folder>` for another folder than `~/agents`), or set `PROMETHEUS_AGENTS`.

### No agent with that name

```text
no agent '<slug>' in <folder>: `prometheus` starts the planner, /agent-list lists the agents
```

**Cause.** `prometheus <word>` opens the agent `<word>` in the agents folder, and there is none: a typo, another agents folder (check `PROMETHEUS_AGENTS`), or a command the version you have doesn't know yet.

**Fix.** Run `prometheus` and `/agent-list` for the names, or `prometheus --help` for the commands.

### A slug that is a command

```text
'<slug>' is a prometheus command (`prometheus <slug>`), so it can't name an agent; pick another slug
```

**Cause.** `prometheus <slug>` opens an agent, so an agent called `init`, `list` or another of the command's words could never be opened. Planning asked `agent_scaffold` for such a slug.

**Fix.** Nothing to do: the planner picks another slug. Existing agents with such a name still refresh.

### pi is not installed

```text
pi is not installed: see `prometheus doctor`
```

**Fix.** Install Pi (`npm install -g @earendil-works/pi-coding-agent`); `prometheus doctor` lists anything else missing.

### Python package missing

```text
Python package '<name>' is missing … ask the user to run `uv sync`
```

**Cause.** The scripts' Python packages aren't installed for the Python the extension found, which is `$PI_AGENT_PYTHON` if set (the `prometheus` command sets it to its own Python for the planner), else the nearest `.venv`. An agent creates its own `.venv` with `uv sync` when it first starts, so inside an agent the message means that step failed, usually because uv isn't installed or you were offline.

**Fix.** For the planner, reinstall it (`uv tool install --reinstall git+<prometheus repository>`), or run `uv sync` in a clone; for an agent, run `uv sync` in its folder; install uv or get online first if that was the problem. Then restart `pi`. The agent never installs packages itself.

### git is required

```text
git is required
```

**Cause.** Each agent is its own git repository, and pi-subagents checks the repository's staged files around every child that writes.

**Fix.** Install git.

### uv is not installed

```text
this wave runs stage programs or gates with `uv run`, but uv is not installed
```

**Cause.** Gates and stage programs run with `uv run`, which first installs the agent's packages into its `.venv/`.

**Fix.** Install [uv](https://docs.astral.sh/uv/). The first `uv run` needs network access to fetch the packages.

### The subagent tool is missing

The model says it has no `subagent` tool.

**Cause.** pi-subagents isn't installed, or its `subagent` tool isn't active in this session.

**Fix.** If it isn't installed, install it globally with `pi install npm:pi-subagents`. If it is, start with `/agent-new`, `/agent-run` or `/agent-revise`, which activate the tool before they begin. In a session continued without one of those commands, the model enables the tool itself, and with some providers it appears only after your next message.

### Every wave fails at startup

```text
Background children require the host npm package (@earendil-works/pi-coding-agent) with its dependencies; … does not provide …
```

**Cause.** The installed Pi and pi-subagents don't match. pi-subagents starts each background child with packages it expects Pi to provide, so when Pi stops providing one, every wave fails on its first child. With pi-subagents 0.74.0 this happened on Pi 1.0, which dropped `@earendil-works/pi-agent-core/node`; pi-subagents 0.75.0 works with Pi 1.0.1.

**Fix.** Update pi-subagents (`pi update`, or `pi install npm:pi-subagents`) to a release that supports your Pi (0.75.0 or later for Pi 1.0), or install a Pi it supports. Then run `/agent-run`, and the active run relaunches the failed wave.

## Starting a run

### Branch not reviewed

```text
the plan on main is not ready to run (…); <run/…, revise/… or improve/…> is still checked out
```

**Cause.** A run always starts from `main`. When a branch awaiting review is checked out, starting a run commits its leftovers, switches to `main` and keeps the branch for review, but only if the plan on `main` is ready. Here it isn't (a draft, or errors), so nothing was switched.

**Fix.** Review the branch (`/agent-revise` or the workspace's *Review and merge*: merge, keep or discard), or change the plan on `main` until it is ready.

### Plan not ready

```text
the plan is <status>, not ready: finish it first
```

**Cause.** A run starts only from a ready plan, which means one that validates and that the plan critic has reviewed.

**Fix.** Run `/agent-run`. With a draft plan it goes back to planning and asks what the plan still needs.

### The critic's result was not recorded

```text
the critic's result for this plan was not recorded with agent_record_critique, which checks the critic ran and ties its result to the plan
```

**Cause.** The plan says `status: ready`, but its critique wasn't recorded by `agent_record_critique`, which stamps the plan with a fingerprint of itself (`critique.plan_digest`). A model wrote the critique or the status by hand. Before 2026-10-05, prometheus planned new agents this way.

**Fix.** Run `/agent-run` in the agent: it runs the critic and records it. An agent planned before 2026-10-05 was stamped once when this rule came in.

### A run is already active

```text
run <id> is active: continue it, or stop it first
```

**Cause.** Only one run can be active at a time.

**Fix.** To continue the run, use `/agent-run`. To start over, tell the agent: it stops the active run, which stays in `runs/` marked stopped, and shows you its branch if it has one.

### A value is missing

The run asks you for a folder or another value.

**Cause.** A required param has no value on this machine.

**Fix.** Give the value. For a file or folder, give an absolute path (`~/…` is fine). See [Params, `.env` and secrets](params-and-secrets.md).

### Input path is relative or missing

```text
input '<id>' resolves to '<path>', a relative path
input '<id>' resolves to '<path>', which does not exist
```

**Cause.** Inputs live outside the agent, so their paths must be absolute and must exist.

**Fix.** Give the value again when asked, or correct it in `.env`.

### A secret is missing

The agent asks you to add a variable to `.env`.

**Cause.** A secret param has no value, and the agent never asks for a secret in chat.

**Fix.** Add `NAME=value` to the agent's `.env`, or set it in the environment you start `pi` from, then tell the agent it is set.

### No connector for a side effect

```text
stage '<key>': agent '<name>' cannot reach <target>
builtin agent '<name>' writes <target> but has no connector tools
```

**Cause.** A side effect that writes outside the agent, to an app or as a message, needs an agent that can reach the target, either through a connector tool (`mcp:<server>` in the agent's `tools`) or through a command-line tool run with `bash`. The first message is an error: the stage's subagent has neither. The second is a warning: built-in agents such as `worker` get no connector tools, so the stage can reach its target only with a command-line tool.

**Fix.** To use a connector, give the stage a subagent in `.pi/agents/` whose `tools` list it. See [How to make a stage use a connector](how-to.md#how-to-make-a-stage-use-a-connector).

## During a run

### A wave is running

```text
a wave is running (stages <…>); check its run before launching again
```

**Cause.** A wave was launched and hasn't been recorded as finished.

**Fix.** Wait for pi-subagents' completion notice; the agent then records the result and carries on. If `pi` restarted in the meantime, `/agent-run` checks the wave first and records it once it has finished.

### Waits for the running wave

```text
a wave of run <id> is running: …
```

You see this after asking for a replan, a critique, a decision or a stop while a wave runs.

**Cause.** These steps wait until the running wave is recorded as finished, so the plan and the answers for the next wave don't change underneath it.

**Fix.** Wait for the completion notice, then ask again.

### Still running in pi-subagents

```text
run <id> is still <state> in pi-subagents: wait for its completion notice
```

**Cause.** The agent tried to record a wave that pi-subagents is still running.

**Fix.** Wait. If you know the wave has ended, because `pi` crashed or you stopped it, tell the agent: it records the wave as failed or stopped, so you can relaunch it.

### Review before the next wave

The agent shows you a wave's results and asks whether to go on.

**Cause.** The plan puts a review after that wave (`pause_after`), and until you answer, compiling the next wave returns `review_needed`.

**Fix.** Say go on, ask for a change (a replan), or stop the run.

### A wave failed

The agent reports a failed wave and offers three options.

**Cause.** A gate rejected an output, a review found a blocking problem, or the wave failed before its script returned, in which case the agent reports the `failure_kind`, such as `timeout` or `validation`.

**Fix.** Choose one of the options: relaunch the wave, which skips children that completed and haven't changed; change the plan with a replan; or stop the run.

### The run reached its budget

The agent says the run spent its cost limit and asks whether to go on.

**Cause.** The plan sets `limits.max_cost_usd`, and the run's children have spent at least that much.

**Fix.** To go on, give a new, higher limit for this run; it holds for the rest of the run. Otherwise stop the run.

### A program did not finish in time

A wave fails with "no result within 600 s (limits.command_timeout_s)".

**Cause.** A stage program or gate, run as a command, ran longer than the limit; it may be waiting for input or a network.

**Fix.** If it is just slow, raise `limits.command_timeout_s` in the plan. If it hangs, relaunch: its fix child gets the error and repairs the program.

### /agent-auto is not available

`/agent-auto` says pi-subagents did not answer.

**Cause.** The installed pi-subagents has no in-process RPC, or is not loaded in this session. In the web app before 2026-10-05, no package loaded in a sandbox at all (Pi couldn't lock `trust.json` in a read-only folder).

**Fix.** Update pi-subagents (0.74 or later) and restart Pi, or use `/agent-run`. In the web app, restart the router.

### The subagent tool returned an error

The agent stops and reports an error from the `subagent` tool, with a run id.

**Cause.** An infrastructure problem: the wave failed to launch, an extension failed to load, or a child failed to start. The agent is designed to report the exact error and stop, not to work around it by doing the stage itself or writing its own scripts, unless you tell it to.

**Fix.** Fix the cause, then run `/agent-run` again.

### Staged index changed

```text
Staged index changed after launch
```

**Cause.** A child that writes files failed its acceptance check, because files were staged (`git add`) in the agent's repository while it ran.

**Fix.** Relaunch the wave, and don't stage files in an agent while a wave runs.

## Changing an agent

### Editing the plan was blocked

The agent was blocked from editing `plan.yaml`, or from running a shell command that names it, even a read-only one such as `git diff -- plan.yaml`.

**Cause.** Once an agent has a run, its plan changes only through the replan tools during a run or through a change between runs, so that every edit is checked and reviewed. The guard can't tell a shell command that reads the plan from one that writes it, so outside a `/agent-revise` branch it blocks any that names `plan.yaml`.

**Fix.** To read the plan, the agent uses its read tool, or `git diff main...<branch>` without naming the file; it usually recovers by itself. To change the plan during a run, ask the agent and it replans with its tools. Between runs, use `/agent-revise`.

### A plan edited by hand doesn't check

```text
stage '<id>': unknown field '<field>' (a stage's fields are in .pi/skills/agent/references/plan-schema.md)
stage '<id>': kind: Value error, kind is a mapping: {value: <kind>, reason: <why>}
```

**Cause.** `plan.yaml` has a stage field the plan doesn't know, often a misspelling, or a `kind` written as one word.

**Fix.** Use the stage fields in `references/plan-schema.md` (in the agent's `.pi/skills/agent/`): `id`, `title`, `kind: {value, reason}`, `after`, `reads`, `writes`, `instructions`, `done_when`, `gate`, `review`, `program: {file, args}`, `map: {over, glob, batch}` and the others listed there. Then `prometheus check <slug>`; an editor with a YAML language server marks these as you type ([How to edit a plan by hand](how-to.md#how-to-edit-a-plan-by-hand)).

### Locked in a replan

```text
a replan cannot change [<fields>] … locked
```

**Cause.** A replan during a run can't change the request, goal, `done_when`, params, inputs, outputs, agents, stages that already ran, or a side effect's approval.

**Fix.** Make those changes with `/agent-revise` once the run has finished or been stopped.

### Files blocked on `main`

```text
main is checked out and no run is active: gates, scripts, agents and packages change only on a run or change branch
```

**Cause.** After the first run, `main` changes only by a reviewed merge. The message usually means the checked-out branch was switched, from a terminal or an editor, while a change was in progress.

**Fix.** Switch back to the branch you were working on (`git switch revise/…`) and tell the agent to continue. To start something new, use `/agent-revise`.

### Critique on `main`

```text
main changes only by a reviewed merge once a run exists: change the plan with /agent-revise
```

**Cause.** After the first run, a critic's result is recorded on a run's or a change's branch, never on `main`.

**Fix.** Start a change with `/agent-revise`.

### Change while a run is active

```text
run <id> is active: tell the run what to change … or stop it first
```

**Cause.** `/agent-revise` and `/agent-improve` work only while no run is active.

**Fix.** Ask the run itself for the change: it replans on the run's branch, which you review at the end. Or stop the run first.

### Another change is open

```text
<revise/… or improve/…> is an open /agent-<kind> change: finish it first
```

**Cause.** A revise and an improve each get their own branch, so you review them separately, one at a time.

**Fix.** Run `/agent-run`, which shows you the open branch and asks whether to merge, keep or discard it. Then run the other command.

### Nothing to improve from yet

```text
no run yet, so nothing to improve from
```

**Cause.** `/agent-improve` proposes fixes drawn from earlier runs and sessions, so it needs at least one run.

**Fix.** Run the agent first. To change the plan before then, use `/agent-revise`, which edits the plan directly while no run exists.

### Nothing new to improve from

```text
Nothing new since the last /agent-improve: no unreviewed sessions or runs.
```

**Cause.** Every session and run is older than the last entry in `improvements.yaml`, so there is nothing new to draw from, and no branch was opened.

**Fix.** Run the agent first, or give a focus to improve it anyway: `/agent-improve <focus>`.

### Merge a plan that is not ready

```text
the plan on <branch> is draft … only a ready, valid plan can reach main
```

**Cause.** The change isn't finished: the plan critic hasn't passed it, or the plan doesn't validate.

**Fix.** Finish the change, or choose keep, which returns to `main` and leaves the branch for later, or discard.

### Merge conflicts with `main`

```text
main has moved and <branch> conflicts with it
```

**Cause.** `main` changed after the branch was created, in a way that overlaps the branch's changes, so git couldn't combine them into the one commit a merge adds to `main`. The branch is checked out again, unchanged.

**Fix.** Resolve the conflict by hand, or discard the branch.

### Branch exists from an earlier run

```text
branch run/<id> already exists from an earlier run with this id
```

**Cause.** `runs/` isn't in git, so deleting a run's folder frees its id but leaves the branch that run created. New runs skip ids that still have a branch, so only a run started by an older version of prometheus can collide.

**Fix.** Stop the run, then either review the old branch (`git switch run/<id>`, then `/agent-run`) or delete it (`git branch -D run/<id>`).

### Merged with git by hand

You merged a branch with git yourself instead of choosing merge in the review.

**Cause.** A git merge combines the changes, but skips two steps the review takes: tagging the new revision on `main` and deleting the merged branch.

**Fix.** Tag the revision (`git tag r<N>` on `main`) and delete the branch. Next time, choose merge in the review, which does both.

## prometheus commands

### prometheus answered instead of planning

You described some work, and prometheus answered or suggested a command instead of planning.

**Cause.** prometheus plans only when you run `/agent-new`, so a plain message gets an answer, even one that describes work.

**Fix.** Run `/agent-new <request>`.

### Refresh during a run or review

```text
<agent> has an active run (<id>); refresh it once the run has finished and its branch is reviewed
<agent> has branch <name> checked out; refresh it after merging, keeping or discarding that branch
```

**Cause.** A refresh rewrites the agent's copy of the skill, including the tool a running map stage uses, and its commit would land on the checked-out branch and show up in that branch's review.

**Fix.** Finish the run, then merge, keep or discard its branch, or the open `revise/…` or `improve/…` branch, and refresh after that.

### Export destination exists

```text
<dest>/<slug> already exists
destination is inside the agent
```

**Cause.** An export never overwrites anything, and can't copy an agent into itself.

**Fix.** Pick another destination, or pass a different name.

### A release is refused

```text
can't release: <reasons>
```

**Cause.** A release runs with no model to decide anything, so `prometheus release` refuses a plan that would need one: a provisional stage (its run would replan), a stage program not written yet (a released run never writes code), uncommitted changes, an active run or another branch checked out, or a plan that isn't ready. (Decisions and side effects are fine: a released run asks its caller there.)

**Fix.** Each reason says what to do: run the agent until its plan is settled and its programs are written and merged, or commit and go back to `main`. Then run `prometheus release <slug>` again.

### A release can break callers

```text
this release can break callers (<changes>): release it with --breaking, so the major version goes up and pinned callers keep the old one
```

**Cause.** Since the last release a param was removed, retyped or made required, an output changed or went away, or a side effect was added that callers must now approve.

**Fix.** If that's intended, run `prometheus release <slug> --breaking`: the version becomes `v<major+1>.0`, and callers pinned to the old major keep the old release. Otherwise undo the change with `/agent-revise`.

### A release can't run

```text
<agent> <version> can't run: missing params: <names>; missing secrets (environment variables): <names>
```

**Cause.** Nothing has started: a required param needs a value with the call, and a secret is read from the environment of the process running it.

**Fix.** Add `-p name=value` (`params={...}` in Python, a form field or file over HTTP) for each missing param, and set the environment variables named, then call again.

### A run waits for an answer

The run's state is `input_required` (A2A: `TASK_STATE_INPUT_REQUIRED`), and it lists what it needs.

**Cause.** It reached a side effect the call didn't approve, or a decision. Nothing of that stage has run.

**Fix.** Answer it: in a terminal `prometheus run` asks; in Python `run.answer(approve=[...], decisions={...})`; over HTTP `POST /runs/<id>/answer`; over A2A a message with the task's `taskId` and a data part `{"approve": [...], "decisions": {...}}`. Cancel the run to decline. To never stop there, approve the side effect up front.

### A called agent stopped to ask

```text
the agent at <url> asks before it goes on (task <id>): <what it asks>; nobody can answer inside a wave: approve its side effects in the stage's a2a.approve
```

**Cause.** An a2a stage called another agent, which reached a side effect the stage didn't approve (or a decision), and waits. A wave can't answer it.

**Fix.** With `/agent-revise`, add the side effects it names to the stage's `a2a.approve` (the stage is a side effect, so your run asks you first), or ask the called agent's owner to turn the decision into a param.

### A called agent's link goes elsewhere

```text
the agent at <url> gave its endpoint on another address (<link>): not followed, so the token and the files go nowhere else
```

**Cause.** An `a2a` stage follows only links on the address its plan names: the called agent's card, or one of its outputs, points at another host. Usually that agent is behind a proxy and doesn't know its public address.

**Fix.** Have its owner serve it with `--public-url <the address you call>`, or give the stage's `a2a.url` the address its card names.

### A called agent failed

```text
the agent at <url> ended task <id> as failed: <its message>
<method> <url>: HTTP <code>: <message>
```

**Fix.** Read the called agent's message: a missing param (add it to `a2a.params` or `a2a.files`), a wrong or missing token (HTTP 401: check the secret param's variable), an address that doesn't answer, or a run that failed on its side (ask its owner, with the task id).

### Not waiting for an answer

```text
run <id> is <state>, not waiting for an answer
```

**Fix.** Nothing to answer: the run is going, or has ended. Read its state first.

### No release

```text
the agent has no release yet: `prometheus release <slug>` makes one
no release <version>; releases: <versions>
```

**Fix.** Release the agent with `prometheus release <slug>`, or ask for one of the versions listed (`@v1` is the newest 1.x).

### A run stopped with its server

```text
the process running it stopped before it ended: start it again
```

**Cause.** The process driving a released run (`prometheus serve`, `prometheus run` or your code) stopped mid-run, for example a server restart. Its waves don't resume on their own.

**Fix.** Start the run again. Model stages that finished on the same inputs are reused, so little is paid twice.

### A released run failed

```text
stage <key> failed: see <run folder> and pi-subagents' record <folder>
```

**Cause.** A stage's program or gate failed, and its `fix` child (a model, which can repair a program but not change the release) couldn't make it pass. The run stopped there: stages after it didn't run.

**Fix.** Look at the run folder's `work/` and pi-subagents' record for the error. If the input was the problem, call again with a corrected one. If the program was, run the agent interactively on that input (`prometheus <slug>`, `/agent-run`), review and merge the fix, and release again.

### Sharing refused: it may contain secrets

```text
not shared, it may contain secrets: <file>: looks like an API key or token; …
not shared, it may contain secrets: <file>: looks like an API key or token, in commit <commit>; …
```

**Cause.** A tracked file holds something that looks like a key, a token or a private key, or a `.env` file is in git. Pushing it would publish it, and git keeps it in the history even after you delete it.

**Fix.** Move the value into a secret param (the plan names it, `.env` or the environment holds it), remove it from the history if it was ever committed, and rotate the key if it was ever pushed anywhere. Then share again.

### Install refused

```text
<folder> exists: `prometheus pull <slug>` updates it, or install with --as <another name>
<url> is not an agent: it has no plan.yaml
cannot clone <url>: <git's message>
'<slug>' can't name an agent: pass another with --as
```

**Fix.** As the message says: pull to update an agent you already have, `--as` for a second copy or a name that is taken by a command, and check the URL (and your access to it) when the clone fails.

### Pull refused: local commits

```text
<slug>'s main has commits the upstream doesn't (besides refreshes): propose them …
```

**Cause.** `main` of your copy has a change the upstream doesn't, so moving it to the upstream's would drop that change.

**Fix.** Put the change on a branch and `prometheus propose <slug>` it, or, if you don't need it, reset `main` to the upstream yourself. Pull works on `main` with nothing uncommitted and no run active.

### Propose refused

```text
<slug> has <branch> checked out: propose a change branch (/agent-revise or /agent-improve)
the change isn't ready to propose: <why>
<slug> has no upstream (origin): it was not installed or shared
```

**Fix.** Make the change with `/agent-revise` or `/agent-improve` and finish it (the critic recorded, when the plan changed) before proposing it; an agent you created yourself needs `prometheus share` first.

### Serving needs a token

```text
serving on <host> needs a token (--token or PROMETHEUS_SERVE_TOKEN): anyone who reaches it could run the agent
```

**Fix.** Pass `--token` (or set `PROMETHEUS_SERVE_TOKEN`) with a long random value, and give it to the callers; or serve on `127.0.0.1` only, for callers on the same machine.

### Already released

```text
this commit is already released as <version>
```

**Fix.** Nothing to do: that version is the release. Change the agent and merge the change to release again.

## Web app

### The router doesn't start behind a login proxy

The router exits with "TRUST_USER_HEADER needs PROXY_SECRET".

**Cause.** With a login proxy, the router trusts the user header it sends. Agents' sandboxes can reach the router's port too, so the proxy must also send a secret only it knows.

**Fix.** Set `PROXY_SECRET` (16 characters or more) for the router, and have the proxy send the same value in the `x-proxy-secret` header (or the header named by `PROXY_SECRET_HEADER`).

### A stage can't reach a site or an API

With `SANDBOX=podman`, a stage fails with "Tunnel connection failed: 403 Forbidden" or "… is not allowed for this agent".

**Cause.** The agent may reach only the model providers, PyPI and the hosts you allowed for it.

**Fix.** In the web app, open the agent's workspace: under **Internet**, the refused host is offered with an Allow button. Then relaunch the wave. If the host should always be allowed, add it to the plan's `network:` so it's offered up front.

### The router stops: "the egress proxy didn't start"

**Cause.** With `SANDBOX=podman`, the router starts a proxy container on two networks of its own, and Podman refused (for example, an older Podman without network support).

**Fix.** Check `podman network ls` and `podman info`. To run without the allow-list, start the router with `EGRESS=off`: agents then reach the internet freely.

### The router stops: "PODMAN_RUNTIME=… can't start a container"

**Cause.** You asked for a container runtime (`PODMAN_RUNTIME`, usually gVisor's `runsc`) that can't start a container on this machine. The router checks at start, rather than run without the boundary you chose. Ubuntu's 2023 `runsc` fails this way as a normal user under WSL.

**Fix.** Install a current gVisor release from [gvisor.dev](https://gvisor.dev/docs/user_guide/install/), or start the router without `PODMAN_RUNTIME` (plain rootless Podman).

### An API call is refused

`POST /a/<agent>/api/runs` answers 400 or 409, or a key gets 403.

**Cause.** The call is checked against what the agent takes before it queues:
- 409 "the agent's plan isn't ready to run": its plan is a draft or changed since the critic.
- 400 "params the agent doesn't take": a name its plan doesn't declare, or a secret (secrets are set on the agent, in its `.env`, never sent).
- 400 "approve": a stage that doesn't ask for approval.
- 400 "files": a path that is absolute or has `..`.
- 403 "this key calls one agent's API only": a key used for another agent, or outside `/a/<agent>/api`, or revoked.

**Fix.** Read the agent's card (`GET /a/<agent>/api`): its params, and the stages you can approve. For a plan that isn't ready, finish it in the agent's chat.

### An API call waits in queued

**Cause.** The agent has one active run at a time: another call's, or one started from its chat. The call's `waiting_for` says so.

**Fix.** Let that run finish, or stop it in the agent's chat. The call starts within 15 seconds after.

### An API call failed: "the web app's server stopped while this call ran"

**Cause.** The router drives a call while it runs; it stopped (restart, crash) mid-way. The run stays as it was.

**Fix.** Open the call's session in the web app (the agent's sessions, tagged *API*) and continue the run there with `/agent-run`, or stop it.

### An agent can't change its own code in the web app

A change to a file under the agent's `.pi/` (its extension, skill copy, settings or subagent profiles), or to `.git/config`, fails with "Read-only file system". `/agent-improve` can hit this when it proposes a change to a subagent profile.

**Cause.** In the web app, an agent's sandbox can't change its own code: your terminal's `pi` would run that code with no sandbox.

**Fix.** Make that change from a terminal (`pi` in the agent's folder), where you see and approve it. Lessons in `.pi/APPEND_SYSTEM.md` stay writable.

## Handled by the agent

You may see these messages go by. Each is a guard doing its job, and the agent knows the next step, so you don't need to act.

### A secret used outside a program

`agent_check` reports "secret '…' reaches only what the batcher runs".

**Cause.** A secret is referenced somewhere a model would see it: a model stage's gate, an input's location, an intent. Models never get secrets.

**Fix.** The agent makes that stage a program (`program: {file, args}`), which gets the secret as an argument, or moves the call to a connector.

### Plan changed after the critique

```text
plan.yaml changed after the critic's result was recorded
```

**Cause.** The plan was edited after the plan critic reviewed it, so the critic hasn't seen it as it is now. On a branch, any difference from `main`'s plan counts as an edit.

**What the agent does.** The agent runs the critic again and records its result, and runs and merges wait until it has.

### Reading `.env` was blocked

The model tried to read or edit `.env` and was blocked.

**Cause.** This is intended: `.env` may hold secrets, so the model may neither read nor edit it.

**What the agent does.** If a value is missing, the agent names the variable for you to set.

### Launched but not recorded

```text
wave <w> was already launched as pi-subagents run <id> … and not recorded
```

**Cause.** The session ended between launching the wave and recording the launch, so launching again would run the wave twice.

**What the agent does.** The agent records the existing launch, then waits for it or records its result.

### Launch recorded on a draft plan

```text
<why the plan is not ready>. No wave was compiled from this plan, so none can have launched
```

**Cause.** The agent tried to record a launch while the plan wasn't ready, typically after a replan the critic hasn't passed yet. Compiling refuses a plan that isn't ready, so no wave can have launched from it.

**What the agent does.** The agent records the critic's result first, then compiles and launches the wave.

### The wave's process is gone

```text
run <id> is still <state> in pi-subagents, but its process (<pid>) is gone
```

**Cause.** The wave's process died without recording how it ended, because `pi` or the machine stopped mid-wave.

**What the agent does.** The agent records the wave as failed, and you can relaunch it.

### No pi-subagents record

```text
cannot read pi-subagents' record of run <id>
```

**Cause.** pi-subagents keeps each background run's record in a temporary folder, and either that folder was cleaned up or `PI_SUBAGENTS_TEMP_ROOT` differs from when the wave launched.

**What the agent does.** The agent records the wave from the completion notice, passing its status and result.

### Replan without a run

```text
no run is active: the replan tools work only during a run
```

**Cause.** The agent tried to replan outside a run.

**What the agent does.** Before the first run, or on a change branch, the agent edits `plan.yaml` directly; after a run has finished, use `/agent-revise`.

### Package not in pyproject.toml

```text
gates/<file> would fail before checking anything: it imports …, which the agent's pyproject.toml does not list
```

The same message names `scripts/<file>` for a stage program.

**Cause.** Every package that an agent's gates and stage programs use must be listed in its `pyproject.toml` and locked in `uv.lock`, so you can see them all in one place and review them with the branch. The same check reports a script with its own `# /// script` package block, and a gate that needs packages but runs with `python3` instead of `uv run`.

**What the agent does.** The agent adds the package with `agent_add_package`, which asks you to approve it and shows why it is needed, or moves the block's packages into `pyproject.toml`.

### Unknown critic run

```text
pi-subagents has no record of a critic run <id>
run <id> is a <agent> child in state <state>, not a completed plan-critic run
```

**Cause.** A critic's result can be recorded only for a plan-critic child that ran and completed, and pi-subagents keeps the records of those children in its temporary folder.

**What the agent does.** The agent passes the right run id or relaunches a failed critic, and if the records were cleaned up, it runs the critic again.

## For contributors

The messages come from the Python scripts in [`.pi/skills/agent/scripts/`](../.pi/skills/agent/scripts/), which print `{"ok": false, "error": …}` and exit non-zero; the extensions pass that on to the model. A failed validation prints its report instead, with `errors: […]`. The run protocol ([`run-protocol.md`](../.pi/skills/agent/references/run-protocol.md), "Failures") tells the agent how to react.

When you add a refusal that a person running agents can meet, add a test in [`tests/`](../tests/) that triggers it, and an entry here with a short heading (its anchor is what [Commands and tools](commands.md) links to), what the person sees, the cause and the fix.
