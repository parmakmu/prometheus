# Design decisions

Very few of prometheus's rules were designed up front. Most of them are there because we tried something simpler first, ran it for real, and watched it go wrong. This page tells those stories: what happened, what it taught us, and the rule we ended up with.

You don't need any of this to use prometheus. It's here for the moment you look at a rule and think "surely that's unnecessary", and especially before you change one, so you know what it's quietly preventing. The ideas themselves are explained in [How prometheus works](how-it-works.md), and the same decisions are listed one line each in [`CLAUDE.md`](../.claude/CLAUDE.md).

If there's one lesson running through nearly every story below, it's this: **a model is good at judgment and poor at bookkeeping.** Again and again, the fix was to take a decision that could be made by a rule away from the model and put it in code, where it can be tested and costs nothing to run.

## Who does what

### The model judges; scripts keep the books

In an early run, 12 of the orchestrating session's 26 turns went on bookkeeping: looking up a wave's outcome, reading files the gates had already checked, finding the plan's `done_when` for the final report.

Now Python scripts decide everything that can be decided by rule: whether the plan is valid, what the next wave is, which moves are allowed, how a run is recorded, when to commit. The agent passes `agent_log_finish` a wave's id and gets back a summary of the children, what the next wave needs approved and, at the end, the `done_when` lines. The extensions between Pi and the scripts are thin: each runs a script and formats the JSON it prints.

This also made the logic testable. A script runs without Pi, so the tests can drive it through every state; a model's behaviour can't be tested that way.

### The orchestrator gets paths and summaries, not text

The session that talks to you costs about as much as the children it launches: $0.021 in run 2, $0.0135 in run 4. Retyping a compiled wave's script into the `subagent` call was about half of its output tokens. So the compiled wave is saved to a file and passed by path, and a finished wave comes back as a summary of the children rather than their outputs. The gates and reviewers have already checked the outputs, so the agent opens one only when you need to see it.

Commands save turns too. pi-subagents starts a session with only its loader tool active, and in run 4 the model spent two turns and a prompt-cache rewrite (about $0.003 of $0.0135) turning the `subagent` tool on. Now `/agent-new`, `/agent-run` and `/agent-revise` turn it on before they send their prompt: running the command is your authorization.

### The state machine is code

The model used to work out where an agent stood by combining the plan check with `run.yaml`, which is bookkeeping again. Now the legal moves are a table in code (`agent_state.MOVES`), and every tool result names the current state and its moves, so the agent never has to guess. A test walks every state and move. On its first walk, it found two holes the hand-written table had missed: a critique that could commit straight to `main`, and a launch accepted on a draft plan. See [the state machine](state-machine.md).

### Choices are asked with the question tool

The rule first said only approvals had to use `ask_user_question`. One run then asked whether to start, and what to do after a failure, in plain chat, where the answer is easy to fumble. Now every question with fixed answers goes through the tool: start a run, decisions, approvals, after a failed wave, merge/keep/discard. Values with no fixed set of answers, such as paths, are still asked in chat.

## Planning

### Only a command starts planning

A user typed "I want to create documentation with docs in md" as an ordinary message. prometheus took it as a request and created an agent nobody had asked for. Now the planning skill can't be invoked by the model, and `agent_scaffold` refuses unless `/agent-new` ran in the session. Starting a plan is something only you can do.

### Stages are classified irreversibility first

The first question about any stage is what can't be undone; only then, who should do it. So the kinds are tried in the order side effect, decision, deterministic, judgment, and a stage that writes to a service is a side effect even when its work is mechanical. Approval is enforced in code, not asked of the model: the compiler refuses a side-effect stage that isn't in the approved list.

### Unknowns are staged, not guessed

A planner made to fill in every stage before seeing any data invents the detail it can't know. So each unknown names how it will be settled (by the planner, a read-only probe, you, or a discovery stage), and stages that depend on discovery stay provisional until it has run.

### The critic is a separate agent

A model grading its own plan misses the same things it missed writing it. So the critic is another agent, with a fresh context, that reads the plan only as a file. Every finding gets a disposition, and the critic's result is tied by a digest to the plan it saw, so a plan edited afterwards can't run unreviewed.

Until 2026-10-05 that tie held only inside an agent. A new agent's first plan was planned in prometheus's chat, which had no recording tool, so the planner wrote the critic's result and `status: ready` itself. It did so honestly in the live run that found this, but nothing checked it: a planner could have skipped the critic. Now prometheus records it with the same tool, and a plan that needs the critic is ready only with a matching digest. The three agents planned before then were stamped once, trusting the reviews they had.

The second pass is for checking fixes. When emails-to-excel's stages were given their programs, the first pass caught that `build-workbook` named a program but kept `pattern: worker`; the second approved the fix. That combination is now a validator error, so the planner catches it before the critic runs.

### The plan is for people; how it was planned is kept beside it

`plan.yaml` used to hold everything: the stages, but also the unknowns and their findings, every question and answer, the critic's findings with their dispositions, and the list of subagents the planner saw. A person opening it to see what their agent does had to read past all of that. So the plan holds what the agent does, in sections under banners (what you asked for, what it works on, how it works, assumptions, limits), and `.plan/` holds how it was planned, a file per part. The scripts read and write the two as one plan, so the rules, the replan and the digest didn't change. Assumptions stay in the plan: they're what a person should check.

### Few questions, asked late

Questions asked before drafting are guesses about what matters. So the planner drafts first, lets the critic run, and only then asks, where a wrong guess would change the stages, a side effect, a source or destination, or what counts as done. It never asks for param values, which belong to the machine.

## Doing the work

### Deterministic work is a program plus a gate

In the first real runs, a model child wrote the extraction program from scratch on every run, and that program was about 70% of the children's output. So a deterministic stage now names a program in `scripts/`. The first run writes it, you review it, and later runs reuse it. If it fails, a `<key>-fix` child is launched and shown the error; it isn't retried blindly.

Reusing the program still left a model child to run it. In run 7, two children spent 35 seconds running two reviewed programs that take about a second as commands. So stage programs now run as plain commands, through the `batcher` runner, with no model involved. In run 8, `read-emails` and `build-workbook` took 0.1 s each, at no cost, and the whole run fell from 2 min 22 s to about a minute.

### The same judgment on the same inputs is made once

With its programs running as commands, emails-to-excel still spent every run's model cost on `discover`, a scout and a reviewer reading the same eight emails and writing a slightly different report each time. A model stage's output depends only on what it is told and what it reads: the rules let a child use only the listed inputs. So the compiler fingerprints each model stage that has no side effect (its child and review items, the contents of the files it consumes, its subagent file, program and gates) and, when an earlier run finished the stage with the same fingerprint, copies that run's outputs into this run instead of launching it, and skips its review, which that run passed. A stage after it in the same wave counts the producer by its fingerprint and the run its output came from, so it is reused only if it read exactly that output. Side effects always run; so does a stage that reads an app or a url, or whose subagent has a connector, since what it reads or does there no file shows.

### An agent's packages live in one place

Told to use "the standard library or what is installed", models hand-wrote their own XLSX readers and writers, about 100 lines each, instead of using openpyxl. Now programs and gates use the packages in the agent's `pyproject.toml`, locked in `uv.lock`, and the validator rejects an import that `pyproject.toml` doesn't list. Per-script package headers (`# /// script`) were tried and rejected: dependencies should be reviewable in one file.

A new package is the user's decision, since it is code that runs on their machine with their files. So the agent can't `uv add` or `pip install`: it calls `agent_add_package` with the packages and a reason, the tool asks you, and only a yes runs `uv add`. The block is in code, in both extensions, because a rule in the prompt alone would let a model add the package and mention it afterwards. A stage's subagent can't ask, so its task says to report a missing package and why instead of adding it.

Creating or refreshing an agent runs `uv lock` up front. Before that, the first `uv run` wrote the lock file in the middle of a wave, and every run left a branch to review.

### A producer is told who reads its output

In run 5, `discover` was told only to "report available fields". It left out `Cc`, which appears in one email of eight. Its reviewer read the plan, saw that `read-emails` extracts `Cc`, and blocked the wave. The two children had judged the same report by different standards, because only one knew what it was for. Now a stage whose output others read gets a "Used next by" section naming each reader, what it reads and how that is checked, and its reviewer gets the same section.

### Acceptance is explicit

The first real run failed with correct output. Left to infer how to accept a child, pi-subagents expected an asynchronous writer to leave no staged files and to have an independent review. Now every child that writes files, and every gated stage, carries an explicit acceptance (`level`, `preserveStagedIndex`, `review`, `verify`).

## Launching waves

### Waves are derived, not written

A workflow script can't pause in the middle for a person, so the work has to stop wherever a person or the agent must act. A wave is the stretch between two such points. The validator works them out from the stages, so a plan never states its waves and can't get them wrong.

### The compiler writes data, not code

Python used to assemble each wave's JavaScript as strings. That caused two bugs (list commas, and a `status` field) and was hard to read. Now a compiled wave is `const W = {…}` followed by one hand-written runner, `wave_runner.js`, that every wave shares. The data can't go through pi-subagents' `args` instead: those are capped at 16 KiB, and the example plan's first wave is 19.6 KB.

### Guards sit around the launch

A session can end at any moment, and the model can skip a step, so every point where that would leave a run in a bad state has a guard. A run starts only from a ready, valid plan. Compiling commits only a wave that can launch. It refuses a wave that was launched but never recorded (pi-subagents keeps the script's digest, which gives it away), and holds the next wave after a `review` until you say go on. A relaunch reuses the children that completed and haven't changed.

### Waves can run without the model

Once stage programs ran as commands, the session that orchestrates a run cost more than the children doing the work: $0.0135 against $0.0105 in run 4. Most of its turns were mechanical: compile, launch, record, compile the next.

`/agent-auto` does those steps in the agent's extension, launching waves through pi-subagents' in-process RPC, and wakes the model only where it's needed: an approval, a decision, a review, a missing value, the budget, a failed wave, a replan, or the final report. That last one is a short prompt without the run protocol, which is about 3,000 tokens.

The first live run turned up two problems. pi-subagents posts a completion notice for each wave that starts a model turn. Pi drops a message an extension sends during a turn, so the hand-over was lost, and the woken model tried to record a wave that `/agent-auto` had already recorded. Now the hand-over is queued as a follow-up, and until its turn starts, the run tools are refused and notice turns are told to do nothing. A runner that dies without recording an end is noticed too, rather than waited on forever.

### Cost is the plan's to set

A plan can name the models its children use (`models:`, or `model:` on a stage), so reviews and batches can run on cheaper ones; prometheus doesn't pick them, since model names depend on your provider. `limits.max_cost_usd` makes the agent ask before the next wave once a run has spent that much, and a yes raises that run's limit so it isn't asked again every wave.

Failures cost less too. A layer stops launching new children once one has failed with no way to repair it: the wave fails anyway, and a relaunch runs only what didn't finish. A relaunched map keeps the batches that completed, instead of running every batch again.

Reuse got stricter and looser. Stricter: a stage's fingerprint now includes the agent's settings and pi-subagents' version, which can change a builtin subagent's model or prompt. Looser: it ignores where an input lives and hashes only its contents. When the emails moved from `scratch/` to the agent's `uploads/`, an unchanged stage paid for a model again only because its path had changed.

## Keeping runs and changes apart

### Runs are folders, and the plan holds no run state

The plan has to be reusable on new input. So each run lives in `runs/<id>/`, relative output paths resolve inside it, and runs never overwrite each other's files. At most one run is active at a time.

### `main` changes only by a reviewed merge

What a run discovers is a proposal, not a silent change to every later run. So a run's first commit opens a `run/<id>` branch, a change you ask for opens a `revise/` or `improve/` branch, and you see the diff and choose. A merge is one commit, because a branch's commits are working steps, and `main` should read as one entry per reviewed change. A new change refuses while another branch is checked out, so a review can't be skipped. A new run always starts from `main`: it keeps a checked-out branch for later review rather than refusing, because an edit saved in the web app opens a `revise/` branch, and making the user merge or discard it before every run would force a review at the wrong moment; the run reads only main's reviewed plan.

An earlier design let prometheus revise an agent's plan from outside, with no locks. It was removed: the agent owns its plan.

### Only scripts commit, and never during a wave

pi-subagents compares the repository's staged files before and after each child that writes. A commit in the middle of a wave would fail that check. So the model never commits, and the scripts commit only between waves.

### Plan edits during a run go through a tool

The first replan edited the plan directly, and the edits misfired. Now, once a run starts, plan changes go through `agent_replan` and `agent_record_critique`, which refuse to touch the [locked fields](how-it-works.md#changing-the-plan-mid-run).

Those tools save the plan as round-tripped YAML in a house style (quotes kept, no wrapping), so a save changes only the edited lines. Repeated keys are an error: a loader that keeps the last one would let an edit that drops a `- id:` line silently merge two list items.

### Params are typed, and values live in `.env`

An earlier `.pi/params.yaml` turned every value into a path, and would have put secrets into chat, compiled waves, child tasks and run logs. Params can be tokens and table names too. Now a param is a `path`, `text` or `secret`; a secret appears only as `${ENV_NAME}`; the model can't read `.env`; and the agent asks you to add a missing secret rather than asking you for it.

### Podman for a server, bubblewrap on your machine

A container or VM per agent was weighed against bubblewrap. Firecracker, alone or through E2B, gives a real VM boundary but can't share folders, and Prometheus is built on agent folders the router reads at the same paths (sessions, outputs, uploads, git); self-hosting E2B also means running a small cluster. A bigger box around an agent doesn't help the risk that remains anyway, which is inside an agent: all its children share one sandbox. So bubblewrap stays for one person, and a server can use rootless Podman instead (`SANDBOX=podman`), which adds what bubblewrap lacks there: memory and CPU limits, its own loopback, and gVisor's kernel if installed. Both are built from one description of the sandbox, so they can't drift, and both run the same escape tests.

### The internet, by allow-list

An agent that reads outside content and can reach any host can be told to send what it read anywhere. With Podman, workers now sit on a network with no route out, and the only way out is a small proxy that lets each agent reach the model providers, PyPI and the hosts you allowed for it. The plan lists the hosts it needs (`network:`), but that's only a request: the list of what's allowed lives in your folder, outside every sandbox, because an agent can edit its own plan. Hosts it was refused show up in the web app with an Allow button, so a missing entry costs one click, not a debugging session. The proxy sees only host names (it tunnels HTTPS, and never decrypts it), and the model providers stay reachable, so a determined injection could still use a provider's API as a channel; a proxy for model keys is the next step.

### Secrets reach only programs, and readers have no shell

Every child of a run shares one sandbox and one environment, and pi-subagents can't give one child an environment of its own. The agent extension used to load all of `.env` into that environment, so a child reading an email had every secret within reach, and an instruction hidden in the email could have told it to send them somewhere. Now `.env` stays out of the environment. A secret can only be used in a stage program's arguments and its gate, the commands the batcher runs, and the batcher adds just the secrets a command names, to that command. A stage that needs a token to call a service does it with a program, or through a connector.

The same email could also tell the child to run a command, if it has a shell. So the agent template has a `reader` subagent, which can read, search and write files but has no shell, and the validator warns when a judgment stage reads outside content (an input folder, file, URL or app) as an agent that has one. Param values in gate commands are quoted now too, as they already were in `run:`, so a value can't add a command.

## The web app

### One worker process per agent

Agents must not see each other's folders, and every child of a session shares its process's environment. So the router starts one Pi process per user and agent (and per conversation), each in a bubblewrap sandbox that sees only its own folder.

A sandbox is only as good as what it can leave behind for something outside it to run. Three such paths were open, and are now closed. Your Pi state was writable, so an agent could drop an extension there that your terminal's `pi` would load with no sandbox; it's now read-only, except what Pi writes while it runs. An agent could edit its own extension, skill copy, settings and subagent profiles, which your terminal loads when you start `pi` in its folder; those are read-only to it now. And the router ran `git` on agents' repositories outside any sandbox, where a line in a repository's `.git/config` names a command git runs on `git diff`; the router now runs every command on an agent inside that agent's sandbox, and only prometheus's copy of a script. Uploads refuse links an agent left in its folder, and behind a login proxy the router needs a secret only the proxy has, since sandboxes can reach the router's port. Each of these has a test that tries it.

### The page speaks AG-UI directly

The worker serves Pi as AG-UI, and the page is built on assistant-ui, which speaks AG-UI itself. CopilotKit was considered, but its Vue SDK needs its own runtime in between. An extension's dialogs, which the question tool falls back to, become AG-UI interrupts, so a question is a card you click.

### Panels are drawn, not streamed

The owner wants Pi's full experience in a chat, and `/ccstyle` refused to run outside a terminal. Streaming a terminal's bytes to the browser was tried first and showed a black card, since the page must get every byte in order. Now the worker renders a panel's frame and records the pi-tui widgets drawn in it, and the page draws native controls from their state.

### Pi loads once, so the router works around the start

A worker takes about 2.3 s to start, most of it loading Pi; once up, the history takes about 15 ms. Three things hide that start. The router answers the page's first request for the history straight from the session file, which takes about 30 ms. Pointing at an agent in the sidebar starts its worker. And because Pi loads an extension only when a session starts, a run after a refresh once used half-hour-old code, so a worker whose agent was refreshed now restarts as soon as it's idle.

### Who may call the router

An early version allowed any origin, so any website could have driven an agent that has bash. Now only the web app's origin and loopback host names are served, with a token printed at start (as Jupyter does) or, on a team, a login proxy's user header. The token travels only as a header, since URLs end up in logs.

## How the code is laid out

### One self-contained `index.ts` per agent

Each agent has one extension file, with no re-export layers. The owner pushes back on indirection and duplicate folders, and the duplication this leaves between the two extensions is accepted. For the same reason prometheus is standalone: you start Pi in its folder, and nothing is installed into `~/.pi/agent/`.

### Contributor notes are kept where Pi can't read them

Pi loads the first of `AGENTS.override.md`, `AGENTS.md` and `CLAUDE.md` from every folder above its working directory. A root `CLAUDE.md` therefore put about 5,000 tokens of contributor notes into every turn, about a fifth of a planning session. So the notes are in `.claude/CLAUDE.md`, which Claude Code reads and Pi doesn't. Roles live in `.pi/APPEND_SYSTEM.md` for the same reason: an `AGENTS.md` would leak prometheus's role into agents below it.

### No releases for prometheus itself

Instead of releases and tags, an agent's skill copy is labelled `<version>+<commit>[.dirty] <hash>`, the hash being of the files a refresh copies. `/agent-refresh --all` updates only the agents whose hash differs, so refreshing from uncommitted changes and committing them unchanged leaves the agent current.
