# Design decisions

This page explains why prometheus works the way it does. It is written for contributors: read it before you change a rule, so you know what the rule prevents. You don't need it to build or run agents; for what each part does, see [How prometheus works](how-it-works.md), and for the decisions in one line each, [`CLAUDE.md`](../.claude/CLAUDE.md).

Few of these rules were designed up front. Most exist because something simpler was tried first, used on a real agent, and went wrong. So each decision below follows the same order: the rule, what happened without it, why this solution was chosen over others, and what it costs or leaves open.

The examples come from live runs of the example agents (emails-to-excel, which turns a folder of emails into a spreadsheet, and email-summaries, which summarises them). Costs are the model spend of one run, in US dollars.

One lesson runs through nearly all of them: **a model is good at judgment and poor at bookkeeping.** Again and again, the fix was to take a decision a rule could make away from the model and put it in code, where it can be tested and costs nothing to run.

The words this page uses, briefly (each is explained in [How prometheus works](how-it-works.md)):

- **Pi** is the coding agent prometheus runs in. **pi-subagents** is the Pi package that launches other Pi sessions, the **children**. Each agent has an **extension**, a TypeScript file that adds its commands and tools to Pi; the planner has its own, so there are two.
- A **plan** (`plan.yaml`) is a list of **stages**. A **wave** is the stages launched together in one pi-subagents call; a **run** (`runs/<id>/`) is one pass through the plan's waves.
- A **gate** is a check, in `gates/`, that a stage's output must pass. A **stage program**, in `scripts/`, does a deterministic stage's work. The **batcher** is a child with no model: a small runner that executes commands (programs, gates, a map stage's split and merge) inside a wave.
- A **side effect** is a stage that changes something outside the agent (sends, writes to a service); it always asks for approval. A **decision** is a stage where a person chooses. A **connector** is a tool that reaches an app, such as an MCP server.

## The model and the code

### The model judges; scripts keep the books

**The rule.** Python scripts make every decision that a rule can make: whether the plan is valid, which wave comes next, which moves are allowed, how a run is recorded, when to commit. The model is left with what needs judgment. The two extensions that sit between Pi and the scripts are thin: each tool runs a script and formats the JSON it prints.

**Without it.** In an early run, 12 of the 26 turns of the session that orchestrated the run went on bookkeeping: looking up how a wave ended, reading files the gates had already checked, finding the plan's `done_when` lines for the final report.

**Why this way.** Now, for example, the agent passes `agent_finish_wave` the wave's id and gets back a summary of the children, what the next wave needs approved and, at the end, the `done_when` lines. Each of those turns became part of one tool result. A second gain was testing: a script runs without Pi, so the tests can drive it through every state, which no one can do with a model's behaviour.

### The orchestrator gets paths and summaries, not text

**The rule.** A compiled wave is saved to a file and handed to pi-subagents by its path. A finished wave comes back as a summary of its children, not their outputs.

**Without it.** The session that talks to you costs about as much as the children it launches ($0.021 in one early run, $0.0135 in another), and retyping each compiled wave into the `subagent` call was about half of its output tokens.

**Why this way.** The gates and reviewers have already checked the outputs, so the session opens one only when you need to see it.

The same reasoning applies to the `subagent` tool itself. pi-subagents starts a session with only its loader tool active, and in one run the model spent two turns and a prompt-cache rewrite (about $0.003 of that run's $0.0135) turning it on. Now `/agent-new`, `/agent-run` and `/agent-revise` turn it on before they send their prompt: running the command is your consent to launching children, so the model doesn't need to ask for the tool.

### The state machine is a spec in code

**The rule.** Where an agent stands, and which moves it allows, is defined once, in `agent_state.py`, written like a [Quint](https://github.com/quint-co/quint) specification. `State` holds the facts (the active run, whether a wave is running, whether each plan can run, the checked-out branch). Each move is an action: a function that checks its guards, then returns the states the move can lead to. Every script calls `require(agent_dir, <action>)` before it does anything, and the result of every tool that makes a move lists, as `next`, the moves whose guards pass. The state's name (`plan_draft`, `main_ready`, `run_wave`, `run_review`, …) is made from the same facts, for people. See [the state machine](state-machine.md).

**Without it.** At first the model worked out where an agent stood by combining the plan check with `run.yaml`, which is bookkeeping again. The next version was a table of legal moves per state, while each script kept its own guards. That made three copies of the rules (the table, the guards and the docs), kept equal only by a test. They drifted: ten state names had to describe four facts, so on a branch the agent hadn't made, the state read `ready` and offered to start a run, which the script then refused. A small model of the machine found that `next` was wrong in 8 of 22 configurations. Separately, "has the agent had a run?" was read from `runs/`, which git doesn't carry, so a clone of an agent arrived with `main` open to direct edits.

**Why this way.** With the guards in the action itself, a refusal and `next` come from the same function and can't disagree. Tables of condition names were tried in between, and put one move in three places joined by strings. "Has had a run" now also reads `main`'s history. Three tests keep the spec honest: a model check explores every state the actions can reach from a new agent and checks the invariants in each; a walk runs every real script from every configuration it reaches, including what a person does by hand (edit the plan, switch branches, leave files uncommitted); and a conformance check fails when a real move lands somewhere its action didn't predict.

**The cost.** Each action states its effect, which is a second description of what its script does. The conformance check is what keeps the two equal, and only for the moves the walk tries.

### Choices are asked with the question tool

**The rule.** Every question with a fixed set of answers goes through `ask_user_question`: whether to start a run, decisions, approvals, what to do after a failed wave, merge, keep or discard. Values with no fixed set of answers, such as paths, are asked in chat.

**Without it.** The rule once covered only approvals. A run then asked whether to start, and what to do after a failure, in plain chat, where a reply is easy to fumble.

## Planning

### Only a command starts planning

**The rule.** Only `/agent-new` starts planning. The model can't invoke the planning skill, and `agent_scaffold` refuses unless `/agent-new` ran in the session.

**Without it.** A user typed "I want to create documentation with docs in md" as an ordinary message, and prometheus created an agent nobody had asked for.

### Stages are classified by what can't be undone

**The rule.** The first question about a stage is what it can't undo; only then, who should do it. So the kinds are tried in the order side effect, decision, deterministic, judgment, and a stage that writes to a service is a side effect even when its work is mechanical.

**Why this way.** A side effect is the one thing a relaunch can't take back, so it must never hide behind another kind. Its approval is enforced in code, not left to the model: the compiler refuses a side-effect stage that isn't in the approved list.

### Unknowns are staged, not guessed

**The rule.** Each unknown names how it will be settled: by the planner, by a read-only probe, by you, or by a discovery stage. Stages that depend on a discovery stay provisional until it has run, and the run then resolves them.

**Without it.** A planner made to fill in every stage before seeing any data invents the details it can't know.

### The critic is a separate agent

**The rule.** The plan is reviewed by another agent with a fresh context, which reads the plan only as a file. Every finding gets a disposition (accepted, rejected with a reason, or asked). The critic's result is tied to the plan it saw by a digest, so a plan edited afterwards can't run unreviewed.

**Without it.** A model grading its own plan misses the same things it missed writing it.

**What changed.** Until 2026-10-05 the digest held only inside an agent. A new agent's first plan was written in prometheus's own chat, which had no tool to record the critic, so the planner wrote the critic's result and `status: ready` itself. In the live run that found this it did so honestly, but nothing checked it: a planner could have skipped the critic. Now prometheus records the critic with the same tool, and a plan that needs the critic is ready only with a matching digest.

**Two passes.** The second pass checks the fixes. When emails-to-excel's stages were given programs, the first pass caught that `build-workbook` named a program but kept `pattern: worker`, and the second approved the fix. That combination is now a validator error, so the planner catches it before the critic runs.

### Few questions, asked late

**The rule.** The planner drafts first, lets the critic run, and only then asks, and only where a wrong guess would change the stages, a side effect, a source or destination, or what counts as done. At most three questions a round, two rounds. It never asks for param values, which belong to the machine the agent runs on.

**Why this way.** Questions asked before drafting are guesses about what matters.

### The plan is for people; how it was planned is kept beside it

**The rule.** `plan.yaml` holds what the agent does, in sections under banners: what you asked for, what it works on, how it works, assumptions, limits. How it was planned (unknowns and their findings, questions and answers, the critic's findings, the subagents the planner saw) is in `.plan/`, a file per part.

**Without it.** `plan.yaml` used to hold all of it, and someone opening it to see what their agent does had to read past the planning record.

**Why this way.** The scripts read and write the two as one plan, so the rules, resolving and the digest didn't change. Assumptions stay in the plan, because they are what a person should check.

### Plan fields are named for the people who edit them

**The rule.** A stage says what it runs `after`, what it `reads` and `writes`, when it is `done_when`, and its `gate` and `review`; a deterministic stage's `program` is `{file, args}`. A field the plan doesn't know is an error. The plan's second line names its JSON schema, which the validator's own models generate.

**Without it.** The first names came from the code's point of view (`needs`, `consumes`, `out`, `verify`), and people editing a plan by hand had to look each one up.

**Why this way.** An ignored misspelling silently changes nothing, so it must be an error. With the schema named, an editor completes the fields and marks misspellings as you type.

## Doing the work

### Deterministic work is a program plus a gate

**The rule.** A deterministic stage names a program in `scripts/` and a check in `gates/`, both written while planning. The program runs as a plain command, with no model. If it fails, a repair child (`<id>-fix`) is launched and shown the error; nothing is retried blindly.

**Without it.** In the first real runs, a model child wrote the extraction program from scratch on every run, and that program was about 70% of the children's output. Keeping the program fixed the waste, but still left a model child to run it: in one run, two children spent 35 seconds running two reviewed programs that take about a second as commands.

**Why this way.** Programs now run through the batcher, which executes commands inside the wave without a model. In the next run, `read-emails` and `build-workbook` took 0.1 s each, at no cost, and the whole run fell from 2 min 22 s to about a minute.

### An agent's packages live in one place

**The rule.** Programs and gates use the packages in the agent's `pyproject.toml`, locked in `uv.lock`, and the validator rejects an import the file doesn't list. Only you add a package: the agent calls `agent_add_package` with the packages and a reason, the tool asks you, and only a yes runs `uv add`.

**Without it.** Told to use "the standard library or what is installed", models hand-wrote their own XLSX readers and writers, about 100 lines each, instead of using openpyxl.

**Why this way.** Dependencies should be reviewable in one file, so per-script headers (`# /// script`) were rejected. A package is code that runs on your machine with your files, so adding one is your decision. The block on `uv add` and `pip install` is in code, in both extensions, because a rule in the prompt would let a model add a package and mention it afterwards. A stage's child can't ask you, so its task says to report a missing package rather than add it. Creating or refreshing an agent runs `uv lock` up front: before that, the first `uv run` wrote the lock file in the middle of a wave, and every run left a branch to review.

### A producer is told who reads its output

**The rule.** A stage whose output other stages read gets a "Used next by" section naming each reader, what it reads and how that is checked. Its reviewer gets the same section.

**Without it.** In one run, `discover` was told only to "report available fields". It left out `Cc`, which appears in one email of eight. Its reviewer read the plan, saw that `read-emails` extracts `Cc`, and blocked the wave. The two children had judged the same report by different standards, because only one of them knew what it was for.

### Acceptance is explicit

**The rule.** Every child that writes files, and every gated stage, carries an explicit acceptance: `{level, preserveStagedIndex, review}`.

**Without it.** The first real run failed with correct output. Left to infer it, pi-subagents expected a writer running in the background to leave no staged files and to have an independent review.

## Reuse

### The same judgment on the same inputs is made once

**The rule.** Before launching a model stage that has no side effect, the compiler computes its fingerprint: its child's instructions and review, the contents of the files it reads, its subagent file, its program and gates, the agent's settings and pi-subagents' version. When an earlier run finished the stage (it ended done, its gate and review passed) with the same fingerprint, the compiler copies that run's outputs into this run instead of launching it, and skips its review, since that run's review passed. A stage that reads another stage's output is reused only if it would read exactly the same output.

**Without it.** With its programs running as commands, emails-to-excel still spent all of each run's model cost on `discover`: a scout and a reviewer reading the same eight emails and writing a slightly different report each time.

**Why this works.** A model stage's output depends only on what it is told and what it reads, because a child may use only its listed inputs.

**How the fingerprint grew.** Each of these was found on a real agent:

- It hashes an input's contents, not where it lives. When the emails moved from `scratch/` to the agent's `uploads/`, an unchanged stage paid for a model again only because its path had changed.
- It includes the agent's settings and pi-subagents' version, either of which can change a builtin subagent's model or prompt.
- A map stage splits its items (files, lines or rows) into batches, one child per batch. Its results are either merged into one result per item (`pattern: map`) or combined by a final reduce child (`pattern: map_reduce`). Either kind is fingerprinted as a whole: its items' contents, batch size, instructions, subagent and model. At first map stages had none, so email-summaries judged the same eight emails on every run, and nothing after a map could be reused either.
- When the whole stage doesn't match, a merged map stage (`pattern: map`) is also reused item by item. One email more used to change the whole stage's fingerprint, so every email was judged again. Now the merge stores each item's record in the run, keyed by the item (a file's name and contents, or the line or row itself), under a fingerprint of the stage without what it maps over or its batch size. The next run's split leaves out the items an earlier run that finished the stage has stored, and its merge puts their records back in item order, so only new or changed items reach a model. A record names its item, which is how the merge already checked coverage, so the batch children's contract didn't change. A `map_reduce` stage still counts whole, since its reduce combines batches rather than keeping one result per item.
- Outputs count anywhere inside the run, not only in `work/`. email-summaries writes `outputs/summaries.json`, and was judged again on its second run until this changed.

**What is never reused.** Side effects, because doing them is the point. A stage that reads an app or a URL, or whose subagent has a connector, because what it reads there is not in any file the fingerprint could cover. A stage that writes an output outside the run, to a path you chose, because every run overwrites that path and there is nothing to copy from.

**A wave with nothing left to run.** When every stage of a wave is reused, the model still used to launch an empty workflow and record it: three turns for nothing. Now such a wave is recorded when it is compiled, and the compile answers as recording a finished wave would, with `launched: false`.

## Launching waves

### Waves are derived, not written

**The rule.** A plan never lists its waves. The validator works them out from the stages: a wave is the stretch of work between two points where a person or the agent must act.

**Why this way.** A workflow script can't pause in the middle for a person, so work must stop at each such point, and a plan that stated its waves could state them wrong.

### The compiler writes data, not code

**The rule.** A compiled wave is its data (`const W = {…}`) followed by one hand-written runner, `wave_runner.js`, shared by every wave.

**Without it.** Python used to assemble each wave's JavaScript from strings. That caused two bugs (list commas, and a `status` field) and was hard to read.

**Why not pass the data as arguments.** pi-subagents caps `args` at 16 KiB, and the example plan's first wave is 19.6 KB.

### A wave is compiled when it launches

**The rule.** The agent keeps what a compile reads (the plan, its programs and gates, its subagents). Each run keeps the waves compiled for it in `runs/<id>/.compiled/`, beside their results. Compiling takes no model.

**Why not compile once and keep it.** Most of a compiled wave belongs to one launch: the run's folder (children work in its `work/`, relative params resolve inside it), the param values, the approvals and decisions you gave for this run, which stages are done, and which children take their outputs from an earlier run instead of running. The guards need it per launch too: a launch is recognised by its script's digest, so each launch's script must be the one recorded for it.

A release is checked the same way: `prometheus release` compiles every wave as a released run would and refuses a plan where one doesn't compile, but a released run still compiles its own waves, in its own folder, with its caller's values.

### Guards sit around the launch

**The rule.** A run starts only from a ready, valid plan. Compiling commits only a wave that can launch. A wave that was launched but never recorded is refused, since pi-subagents keeps its script's digest. A launch must come from the commit its wave was compiled from. A side-effect stage launches only once you have approved it. After a wave that ends with a `review`, the next waits until you say go on. A relaunch reuses the children that completed and haven't changed.

**Why this way.** A session can end at any moment, and the model can skip a step, so every point where that would leave a run in a bad state has a guard.

### Children find the agent's subagents from its git root

**The rule.** The agent template sets pi-subagents' `projectRootResolution` to `git-root`, so every child finds project subagents (such as the agent's own `reader`) in the agent's repository.

**Without it.** A child looks in the nearest `.pi/` folder above where it works. In the first live run with a map stage, the split child's session left a `runs/<id>/work/.pi/` folder behind, and the next child looked there, found nothing, and failed with "Unknown agent: reader". The next run, with the setting, split, ran two reader batches and merged them for $0.0022.

### Waves can run without the model

**The rule.** `/agent-auto` compiles, launches and records waves in code, through pi-subagents' in-process RPC, and wakes the model only when a person or judgment is needed: an approval, a decision, a review, a missing value, the budget, a failed wave, resolving provisional stages, or the final report.

**Without it.** Once programs ran as commands, the session orchestrating a run cost more than the children doing the work ($0.0135 against $0.0105), and most of its turns were mechanical.

**What the first live run found.** pi-subagents posts a completion notice for each wave, and the notice starts a model turn. Pi drops a message an extension sends during a turn, so `/agent-auto`'s hand-over was lost, and the woken model tried to record a wave that `/agent-auto` had already recorded. Now the hand-over is queued as a follow-up, and until it starts, the run tools are refused and notice turns are told to do nothing. The final report is a short prompt, without the run protocol, which is about 3,000 tokens.

### Cost and failures are bounded by the plan

**The rule.** A plan can name the models its children use (`models:`, or `model:` on a stage), so reviews and batches can run on cheaper ones. `limits.max_cost_usd` makes the agent ask before the next wave once a run has spent that much; a yes raises that run's limit, so it isn't asked again at every wave.

**Why this way.** prometheus picks no model, since model names depend on your provider.

**Failures cost less too.** A layer of a wave stops launching new children once one has failed with no way to repair it, since the wave fails anyway and a relaunch runs only what didn't finish. A relaunched map stage keeps the batches that completed.

## Runs and changes

### Runs are folders, and the plan holds no run state

**The rule.** Each run lives in `runs/<id>/`, relative paths resolve inside it, and runs never overwrite each other's files. At most one run is active at a time.

**Why this way.** The plan has to be reusable on new input, so nothing about one run can live in it.

### `main` changes only by a reviewed merge

**The rule.** A run's first commit opens a `run/<id>` branch; a change you ask for opens a `revise/` or `improve/` branch. You see the diff and choose merge, keep or discard. A merge is one commit on `main`.

**Why this way.** What a run discovers is a proposal, not a silent change to every later run. A branch's commits are working steps, and `main` should read as one entry per reviewed change. A new change is refused while another branch is checked out, so a review can't be skipped.

**One exception.** A new run always starts from `main`, and keeps a checked-out branch for review later rather than refusing. An edit saved in the web app opens a `revise/` branch, and making you merge or discard it before every run would force a review at the wrong moment. The run reads only `main`'s reviewed plan, so nothing unreviewed reaches it either way.

An earlier design let prometheus revise an agent's plan from outside, with no locks. It was removed: the agent owns its plan.

### Only scripts commit, and never during a wave

**The rule.** The model never runs git commands that change the repository, and the scripts commit only between waves.

**Why this way.** pi-subagents compares the repository's staged files before and after each child that writes, and a commit during a wave would fail that check.

### Plan edits during a run go through a tool

**The rule.** Once a run has started, the plan changes only through `agent_resolve` and `agent_record_critique`, which refuse to touch the [locked fields](how-it-works.md#changing-the-plan-mid-run). Both save the plan as round-tripped YAML in a house style (quotes kept, no wrapping), so a save changes only the edited lines. Repeated keys are an error.

**Without it.** The first time a run resolved stages, it edited the plan file directly, and the edits misfired. A loader that keeps the last of two repeated keys would let an edit that drops a `- id:` line silently merge two list items.

## Secrets and sandboxes

### Params are typed, and values live in `.env`

**The rule.** A param is a `path`, `text` or `secret`. Values live in `.env`, which the model can't read. A secret appears in the plan only as `${ENV_NAME}`, and the agent asks you to add a missing secret to `.env` rather than asking you for its value.

**Without it.** An earlier `.pi/params.yaml` turned every value into a path, and would have put secrets into chat, compiled waves, children's tasks and run logs. Params can be tokens and table names too.

### Secrets reach only programs, and readers have no shell

**The rule.** `.env` is never loaded into Pi's environment. A secret can be used only in a stage program's arguments and its gate, which the batcher runs, and the batcher gives each command only the secrets it names. A judgment stage that reads outside content (an input folder, a file, a URL, an app) runs as `reader`, a subagent that can read, search and write files but has no shell; the validator warns otherwise. Param values in commands are shell-quoted.

**Without it.** Every child of a run shares one sandbox and one environment, and pi-subagents can't give one child an environment of its own. The extension used to load all of `.env` into that environment, so a child reading an email had every secret within reach, and an instruction hidden in the email could have told it to send them somewhere, or to run a command.

### Podman on a server, bubblewrap on your machine

**The rule.** The web app runs each agent's worker in a sandbox: bubblewrap for one person on their own machine, or rootless Podman on a server (`SANDBOX=podman`). Both are built from one description of the sandbox, and both pass the same escape tests.

**Why not a VM per agent.** Firecracker, alone or through E2B, gives a real VM boundary but can't share folders, and prometheus is built on agent folders that the router reads at the same paths (sessions, outputs, uploads, git). Self-hosting E2B also means running a small cluster. A bigger box around an agent wouldn't help with the risk that remains anyway, which is inside the agent: all its children share one sandbox.

**What Podman adds.** Memory and CPU limits, its own loopback, and gVisor's kernel where it is installed.

### The internet, by allow-list

**The rule.** With Podman, workers sit on a network with no route out. The only way out is a small proxy that lets each agent reach the model providers, PyPI, and the hosts you allowed for it. The plan's `network:` is only a request: the allowed list lives in your folder, outside every sandbox. A host an agent was refused shows in the web app with an Allow button.

**Why this way.** An agent that reads outside content and can reach any host can be told to send what it read anywhere. The list can't live in the plan, because an agent can edit its own plan. The Allow button makes a missing entry cost a click, not a debugging session.

**What it leaves open.** The proxy sees only host names, since it tunnels HTTPS without decrypting it, and the model providers must stay reachable, so a determined injection could still use a provider's API as a channel. A proxy for model keys would close that.

## The web app

### One worker process per agent

**The rule.** The router starts one Pi process per user, agent and conversation, each in a sandbox that sees only its own agent's folder.

**Why this way.** Agents must not see each other's folders, and every child of a session shares its process's environment.

**What a sandbox can leave behind.** A sandbox is only as good as what it can leave for something outside it to run. Three such paths were open and are now closed:

- Your Pi state was writable, so an agent could drop an extension there that your terminal's `pi` would load with no sandbox. It is now read-only, except for what Pi itself writes while it runs.
- An agent could edit its own extension, skill copy, settings and subagent profiles, which your terminal loads when you start `pi` in its folder. Those are read-only to it now.
- The router ran `git` on agents' repositories outside any sandbox, and a line in a repository's `.git/config` can name a command git runs on `git diff`. The router now runs every command on an agent inside that agent's sandbox, and only prometheus's own copy of a script.

Two smaller openings are closed too: uploads refuse links an agent left in its folder, and behind a login proxy the router needs a secret only the proxy has, since sandboxes can reach the router's port. Each of these has a test that tries it.

### Who may call the router

**The rule.** The router serves only the web app's origin and loopback host names, with a token printed at start (as Jupyter does) or, on a team, a login proxy's user header. The token travels only in a header.

**Without it.** An early version allowed any origin, so any website could have driven an agent that has bash. URLs end up in logs, so a token in one would leak.

### The page speaks AG-UI directly

**The rule.** The worker serves Pi as AG-UI, and the page is built on assistant-ui, which speaks AG-UI itself. An extension's dialogs, which the question tool falls back to, become AG-UI interrupts, so a question is a card you click.

**Why not CopilotKit.** Its SDK needs its own runtime in between.

### Panels are drawn, not streamed

**The rule.** The worker renders a panel's frame and records the pi-tui widgets drawn in it; the page draws native controls from their state.

**Without it.** Pi's panels refused to run outside a terminal. Streaming a terminal's bytes to the browser was tried first and showed a black card, since the page must receive every byte in order.

### The router hides Pi's start

**The rule.** The router answers the page's first request for the history from the session file (about 30 ms), hovering over an agent in the sidebar starts its worker, and a worker whose agent was refreshed restarts as soon as it is idle.

**Why this way.** A worker takes about 2.3 s to start, most of it loading Pi; once it is up, the history takes about 15 ms. Pi loads an extension only when a session starts, so after an agent was refreshed (`prometheus refresh`, which updates its copy of prometheus's code), a run once used half-hour-old code.

## Running agents from code

### A release needs no model and no author

**The rule.** Besides the chat, an agent can run embedded in a program (`load_agent`) or as a service a tech team deploys. Both run with no orchestrating model and nobody at the keyboard. A released run can still ask its caller for what only the caller can give, a decision or the approval of a side effect (see [Released runs pause instead of refusing](#released-runs-pause-instead-of-refusing)), but nothing may need a model or the agent's author. So only such a plan can be released:

- no stage is provisional, since resolving one means editing the plan;
- every stage program is written, since a released run never writes code;
- every wave compiles as a released run compiles it.

A release is a tag, `v<major>.<minor>`, on `main`. The tag's message holds the interface a caller depends on: params, outputs, side effects and decisions. A change that can break a caller (a param removed or newly required, an output changed, a side effect added) needs `--breaking`, which raises the major version, so a caller pinned to `@v1` never picks it up.

**How it runs.** A release runs from its own unpacked copy (`~/.local/share/prometheus/releases/<slug>/<version>/`, with a fresh git repository), because the batcher's relative paths and `uv run` need the agent's layout. The runner launches the waves through pi-subagents' RPC and exits when they end. It aborts any model turn, since pi-subagents' completion notice would otherwise start one.

**Why the copy can't be changed.** It is unpacked once and reused by every run of that version, so an edit there would change what `v1.0` does, unreviewed, and its fresh repository would keep the change from ever reaching the agent's own. So the copy leaves out the agent's extension, which holds the commands and tools that change an agent (`/agent-revise`, `/agent-improve`, resolving); the runner doesn't load it anyway. A `pi` started in the copy offers no way to change it. Nothing stops an edit by hand, but nothing invites one either. A `RELEASE.json` beside the plan records the agent, version, commit and interface, names the agent (the folder is named by version), and says that changes are made in the agent's repository and released again. A tag moved to another commit is refused rather than unpacked over the old copy: a version never changes.

### A machine that only runs an agent installs a release

**The rule.** `prometheus install <source>@v1` installs one release: it fetches only that tag, unpacks it, shows the agent's own code for review, and keeps no repository. The release runs exactly as released; nothing of it is refreshed from the local prometheus.

**Without it.** A release could run only where the agent's repository was, since `load_agent` read the tags there. A server that only runs the agent needed the whole repository: its history, its other branches and the tooling to change it, none of which it should use.

**Why this way.** On such a machine the unpacked releases are where the agent lives, and they hold its runs. That makes them data, not a cache, so they live in `~/.local/share/prometheus/releases/` (`PROMETHEUS_RELEASES`), a folder per version, each with its `RELEASE.json`, which `load_agent` picks from when there is no repository (as Hugging Face's cache keeps a small file per name beside each revision). Where the repository is present, its tags decide. An upgrade is another install: `@v1` again adds the newest 1.x beside the old one, whose runs stay.

### Released runs pause instead of refusing

**The rule.** A released run that reaches a decision or an unapproved side effect stops and waits, in what A2A calls `input-required`. `Run.answer()`, `POST /runs/<id>/answer` or an A2A message with the task's id lets it go on, from any process, because the run's state is in its folder (`run.yaml`, the same file an agent's own run keeps). A run records the process driving it and when that process started, so a run whose process died ends as an error.

**Without it.** A decision stopped a released run with nothing to resume, and a run whose server restarted would have waited forever, even after another process got the same pid.

### A failed release is fixed where the agent is developed

**The rule.** `prometheus bundle` (or `serve`'s `GET /runs/<id>/bundle`) packs what a failed run did into one `.tgz` file: how each launch and child ended, the children's tool calls (by name) and the results of the failed ones, and the errors. `/agent-improve <bundle>` turns it into fixes on a branch you review. `prometheus run <slug>@<branch> --bundle <file>` reruns the failed call on the fix before anything is released.

**Without it.** A release runs on someone else's machine, while the agent is developed elsewhere, and the evidence of a failure stays where it ran.

**Why this way.** `/agent-improve` already turns evidence into reviewed fixes, so a bundle is one more kind of evidence rather than a new `/agent-doctor`. It is unpacked into `runs/imported/`, which no run reuses from, and names the release's commit, so the import warns when the agent here isn't the code that ran. Secret values are replaced by `${NAME}`. `prune` keeps a release's newest failed runs whole, so there is still something to bundle.

**Without the run's data, by default (2026-10-18).** A runner holds production data that isn't the developer's to see, and almost everything a run leaves (the children's sessions and outputs, the compiled waves, `run.yaml`) holds what it read. Secrets can be replaced because their values are known; data can't be found that way. So a default bundle holds only `bundle.json`, written field by field from what the run did rather than copied from its files. Two kinds of text in it may still quote data: children's errors and the results of their failed tool calls. In those, each param's value is replaced by `<name>` (a secret's, everywhere in the bundle, by `${NAME}`), and the texts are listed in `to_check` for the operator to read before sending. Long texts are shortened to fit, and values are replaced before that, since a value cut in half no longer matches. `--with-data` adds the run as it is, for when the data's owner agrees.

**A rerun asks again.** A rerun from a bundle once carried the bundled run's approvals over. On a machine whose secrets reach the same systems, it would then send the same email again without asking. Now a rerun asks for every side effect as a new run does: `prometheus run` prompts in the terminal. Checking each fix against its bundle automatically before a merge (as Google's harness2 does) was considered and dropped: it needs the data on the developer's machine, and one run of a model stage proves little.

**On the machine that runs it, diagnosis only.** `prometheus runs` shows how a run went, and `prometheus doctor <slug>` says whether the release can run there at all (secrets, packages, models). What they find is fixed in that machine's setup or in the agent's repository, never in the release.

**A copy without prometheus.** A copy of an agent can run where prometheus isn't installed, after `/agent-export` or a clone, and its runs and sessions never reach the developer, since neither is in git. So the agent's own skill copy can bundle them too (`/agent-bundle`), with the same default; with `--with-data` it also takes the agent's sessions during the run, since an interactive run's story is in its chat.

### Sharing is plain git

**The rule.** Agents are shared through any git remote, with no GitLab API and no `glab`. Installing an agent to use and change it (`prometheus install <source>`, with no version) clones it, then refreshes it from your own prometheus; installing one release (`@v1`, above) is the other kind of install. `pull` refuses a `main` with commits other than refreshes. `propose` opens a merge request with GitLab's push options, so it needs only a plain `git push`.

**Why this way.** After the refresh, everything prometheus owns (the extension, the skill copy) is your trusted copy rather than the author's, so what is left to review is the agent's own code: gates, programs, subagents, packages and lessons.

### One release per server process, spoken over A2A

**The rule.** `prometheus serve` serves one agent per process. Inputs arrive as uploads and outputs stay in the run until downloaded, so no path crosses the wire. Other agents call it over A2A, not MCP: its Agent Card carries the release's interface, and an agent calls another with `pattern: a2a`, run by the batcher with no model.

**Why this way.** One agent per process means the process's environment holds only that agent's secrets. The batcher follows links only on the address the plan names, so a called agent can't redirect the token or the files elsewhere.

## How the code is laid out

### One self-contained `index.ts` per agent

**The rule.** Each agent has one extension file, with no re-export layers, and prometheus is standalone: the `prometheus` command starts Pi with prometheus's extension, skill and role in the planner's own folder, and nothing is installed into `~/.pi/agent/`.

**Why this way.** Indirection and duplicate folders make the code harder to follow than the duplication they save, so the duplication between the two extensions is accepted.

### Contributor notes are kept where Pi can't read them

**The rule.** Notes for contributors are in `.claude/CLAUDE.md`, which Claude Code reads and Pi doesn't. Roles live in `.pi/APPEND_SYSTEM.md`.

**Without it.** Pi loads the first of `AGENTS.override.md`, `AGENTS.md` and `CLAUDE.md` from every folder above its working directory, so a root `CLAUDE.md` put about 5,000 tokens of contributor notes into every turn, about a fifth of a planning session. An `AGENTS.md` would leak prometheus's role into the agents below it.

### No releases for prometheus itself

**The rule.** Agents have releases; prometheus doesn't. An agent's skill copy is labelled `<version>+<commit>[.dirty] <hash>`, the hash being of the files a refresh copies, and `/agent-refresh --all` updates only the agents whose hash differs.

**Why this way.** Only the hash decides whether an agent is current, so refreshing from uncommitted changes and committing them unchanged leaves the agent current.
