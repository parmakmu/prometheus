# Design decisions

Very few of prometheus's rules were designed up front. Most of them are there because we tried something simpler first, ran it for real, and watched it go wrong. This page tells those stories: what happened, what it taught us, and the rule we ended up with.

You don't need any of this to use prometheus. It's here for the moment you look at a rule and think "surely that's unnecessary", and especially before you change one, so you know what it's quietly preventing. The ideas themselves are explained in [How prometheus works](how-it-works.md), and the same decisions are recorded more tersely in [`CLAUDE.md`](../.claude/CLAUDE.md).

If there's one lesson running through nearly every story below, it's this: **a model is good at judgment and poor at bookkeeping.** Again and again, the fix was to take a decision that could be made by a rule away from the model and put it in code, where it can be tested and costs nothing to run.

## Who does what

### The model judges; scripts keep the books

In an early run, 12 of the orchestrating session's 26 turns went on bookkeeping: looking up a wave's outcome, reading files the gates had already checked, finding the plan's `done_when` for the final report.

Now Python scripts decide everything that can be decided by rule: whether the plan is valid, what the next wave is, which moves are allowed, how a run is recorded, when to commit. The agent passes `workflow_log_finish` a wave's id and gets back a summary of the children, what the next wave needs approved and, at the end, the `done_when` lines. The extensions between Pi and the scripts are thin: each runs a script and formats the JSON it prints.

This also made the logic testable. A script runs without Pi, so the tests can drive it through every state; a model's behaviour can't be tested that way.

### The orchestrator gets paths and summaries, not text

The session that talks to you costs about as much as the children it launches: $0.021 in run 2, $0.0135 in run 4. Retyping a compiled wave's script into the `subagent` call was about half of its output tokens. So the compiled wave is saved to a file and passed by path, and a finished wave comes back as a summary of the children rather than their outputs. The gates and reviewers have already checked the outputs, so the agent opens one only when you need to see it.

### The state machine is code

The model used to work out where a workflow stood by combining the plan check with `run.yaml`, which is bookkeeping again. Now the legal moves are a table in code (`workflow_state.MOVES`), and every tool result names the current state and its moves, so the agent never has to guess. A test walks every state and move. On its first walk, it found two holes the hand-written table had missed: a critique that could commit straight to `main`, and a launch accepted on a draft plan. See [the state machine](state-machine.md).

### Choices are asked with the question tool

The rule first said only approvals had to use `ask_user_question`. One run then asked whether to start, and what to do after a failure, in plain chat, where the answer is easy to fumble. Now every question with fixed answers goes through the tool: start a run, decisions, approvals, after a failed wave, merge/keep/discard. Values with no fixed set of answers, such as paths, are still asked in chat.

## Planning

### Only a command starts planning

A user typed "I want to create documentation with docs in md" as an ordinary message. prometheus took it as a request and created a workflow nobody had asked for. Now the planning skill can't be invoked by the model, and `workflow_scaffold` refuses unless `/workflow-new` ran in the session. Starting a plan is something only you can do.

### Stages are classified irreversibility first

The first question about any stage is what can't be undone; only then, who should do it. So the kinds are tried in the order side effect, decision, deterministic, judgment, and a stage that writes to a service is a side effect even when its work is mechanical. Approval is enforced in code, not asked of the model: the compiler refuses a side-effect stage that isn't in the approved list.

### Unknowns are staged, not guessed

A planner made to fill in every stage before seeing any data invents the detail it can't know. So each unknown names how it will be settled (by the planner, a read-only probe, you, or a discovery stage), and stages that depend on discovery stay provisional until it has run.

### The critic is a separate agent

A model grading its own plan misses the same things it missed writing it. So the critic is another agent, with a fresh context, that reads the plan only as a file. Every finding gets a disposition, and the critic's result is tied by a digest to the plan it saw, so a plan edited afterwards can't run unreviewed.

The second pass is for checking fixes. When emails-to-excel's stages were given their programs, the first pass caught that `build-workbook` named a program but kept `pattern: worker`; the second approved the fix. That combination is now a validator error, so the planner catches it before the critic runs.

### Few questions, asked late

Questions asked before drafting are guesses about what matters. So the planner drafts first, lets the critic run, and only then asks, where a wrong guess would change the stages, a side effect, a source or destination, or what counts as done. It never asks for param values, which belong to the machine.

## Doing the work

### Deterministic work is a program plus a gate

In the first real runs, a model child wrote the extraction program from scratch on every run, and that program was about 70% of the children's output. So a deterministic stage now names a program in `scripts/`. The first run writes it, you review it, and later runs reuse it. If it fails, a `<key>-fix` child is launched and shown the error; it isn't retried blindly.

Reusing the program still left a model child to run it. In run 7, two children spent 35 seconds running two reviewed programs that take about a second as commands. So stage programs now run as plain commands, through the `workflow-batcher` runner, with no model involved. In run 8, `read-emails` and `build-workbook` took 0.1 s each, at no cost, and the whole run fell from 2 min 22 s to about a minute.

### A workflow's packages live in one place

Told to use "the standard library or what is installed", models hand-wrote their own XLSX readers and writers, about 100 lines each, instead of using openpyxl. Now programs and gates use the packages in the workflow's `pyproject.toml`, locked in `uv.lock`, and the validator rejects an import that `pyproject.toml` doesn't list. Per-script package headers (`# /// script`) were tried and rejected: dependencies should be reviewable in one file.

Creating or refreshing a workflow runs `uv lock` up front. Before that, the first `uv run` wrote the lock file in the middle of a wave, and every run left a branch to review.

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

## Keeping runs and changes apart

### Runs are folders, and the plan holds no run state

The plan has to be reusable on new input. So each run lives in `runs/<id>/`, relative output paths resolve inside it, and runs never overwrite each other's files. At most one run is active at a time.

### `main` changes only by a reviewed merge

What a run discovers is a proposal, not a silent change to every later run. So a run's first commit opens a `run/<id>` branch, a change you ask for opens a `revise/` or `improve/` branch, and you see the diff and choose. A merge is one commit, because a branch's commits are working steps, and `main` should read as one entry per reviewed change. New runs and changes refuse while another branch is checked out, so a review can't be skipped.

An earlier design let prometheus revise a workflow's plan from outside, with no locks. It was removed: the workflow owns its plan.

### Only scripts commit, and never during a wave

pi-subagents compares the repository's staged files before and after each child that writes. A commit in the middle of a wave would fail that check. So the model never commits, and the scripts commit only between waves.

### Plan edits during a run go through a tool

The first replan edited the plan directly, and the edits misfired. Now, once a run starts, plan changes go through `workflow_replan` and `workflow_record_critique`, which refuse to touch the [locked fields](how-it-works.md#changing-the-plan-mid-run).

Those tools save the plan as round-tripped YAML in a house style (quotes kept, no wrapping), so a save changes only the edited lines. Repeated keys are an error: a loader that keeps the last one would let an edit that drops a `- id:` line silently merge two list items.

### Params are typed, and values live in `.env`

An earlier `.pi/params.yaml` turned every value into a path, and would have put secrets into chat, compiled waves, child tasks and run logs. Params can be tokens and table names too. Now a param is a `path`, `text` or `secret`; a secret appears only as `${ENV_NAME}`; the model can't read `.env`; and the agent asks you to add a missing secret rather than asking you for it.

## How the code is laid out

### One self-contained `index.ts` per agent

Each agent has one extension file, with no re-export layers. The owner pushes back on indirection and duplicate folders, and the duplication this leaves between the two extensions is accepted. For the same reason prometheus is standalone: you start Pi in its folder, and nothing is installed into `~/.pi/agent/`.

### Contributor notes are kept where Pi can't read them

Pi loads the first of `AGENTS.override.md`, `AGENTS.md` and `CLAUDE.md` from every folder above its working directory. A root `CLAUDE.md` therefore put about 5,000 tokens of contributor notes into every turn, about a fifth of a planning session. So the notes are in `.claude/CLAUDE.md`, which Claude Code reads and Pi doesn't. Roles live in `.pi/APPEND_SYSTEM.md` for the same reason: an `AGENTS.md` would leak prometheus's role into workflows below it.

### No releases for prometheus itself

Instead of releases and tags, a workflow's skill copy is labelled `<version>+<commit>[.dirty-<hash>]`. `/workflow-refresh --all` updates only the workflows whose label differs.
