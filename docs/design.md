# Design decisions

Why Prometheus is built the way it is. Most of these decisions replaced something that was tried first and went wrong, so each entry says what happened. This page is for people who wonder why a rule exists, or who want to change one; to use Prometheus you don't need it. The settled decisions are also recorded in [`CLAUDE.md`](../.claude/CLAUDE.md).

## The model does the judging, scripts do the rest

The Python scripts decide everything that can be decided by rules: whether the plan is valid, what the next wave is, what a move is allowed, how a run is recorded, when to commit. The model plans, talks to you and does the judgment stages. The extensions that connect them are thin: they run a script and format its JSON.

The reason is testability, and cost. A script can be tested without Pi, in a loop over every state. And when the model did the bookkeeping, it was most of the bill: in an early run's log, about 12 of 26 turns of the orchestrating session went on looking up outcomes and reading files the gates had already checked. Now `workflow_log_finish` takes just the wave's id, and returns a summary.

## Stages are classified irreversibility first

A stage is a side effect, a decision, deterministic or judgment, tried in that order. A stage that both writes to a service and is mechanical is a side effect: the first question is what can't be undone, and only then who should do the work. Approval is enforced in code, not asked of the model: the compiler refuses to compile a side-effect stage that isn't in the approved list.

## Deterministic work is a program plus a gate

The first real runs had a model write the extraction program from scratch every run, which was about 70% of the children's output. So a deterministic stage now names a program in `scripts/`. The first run writes it and you review it; later runs run it as a command.

The gate is written during planning and never by the stage it checks.

A program that fails isn't retried blindly. A `<key>-fix` child is launched only then, shown the error.

Stage programs run as commands (through the `workflow-batcher` runner) after a run in which two model children had spent 35 seconds on two reviewed programs that take about a second as commands.

## A workflow's packages live in one place

Programs and gates use the packages in the workflow's `pyproject.toml`, installed by `uv` and locked in `uv.lock`. The validator rejects an import `pyproject.toml` doesn't list, and a script with its own `# /// script` package block.

The rule was added after models, told to use "the standard library or what is installed", wrote their own XLSX readers and writers of about 100 lines each instead of using openpyxl. Per-script package headers were tried and rejected: dependencies should be visible, and reviewable, in one file. Creating and refreshing a workflow run `uv lock`, so the lock file is committed up front; otherwise the first `uv run` wrote it in the middle of a wave, and every run left a branch to review.

## Waves are derived, not written

A workflow script can't pause in the middle for a person. A wave is therefore the stretch between two points where the parent has to act, and the validator works those points out from the stages.

## The compiler writes data, not code

A compiled wave is `const W = {…}` followed by one hand-written runner program that every wave shares. Python assembling JavaScript strings had caused two bugs (list commas, a `status` field) and was hard to read. The data is also passed by file path, not text: retyping the compiled script cost about half of the orchestrator's output tokens. It can't go through pi-subagents' `args` either, which caps them at 16 KiB; the example plan's first wave is 19.6 KB.

## Commands start planning; plain messages never do

The planning skill can't be invoked by the model, and `workflow_scaffold` refuses unless `/workflow-new` ran in the session. A plain message once ("I want to create documentation with docs in md") was taken as a request and scaffolded a workflow nobody asked for.

## Choices are asked with the question tool

Every question with fixed answers, such as starting a new run, decisions, approvals, what to do after a failure, or merge/keep/discard, goes through `ask_user_question` when it exists. One run asked its start and failure questions in plain chat, because the rule covered approvals only. Values, such as paths, are asked in chat.

## Few questions, asked late

The planner drafts first, and asks only if a wrong guess would change the stages, a side effect, a source or destination, or what counts as done: at most three per round and two rounds, after the critic. It never asks for param values, which belong to the machine.

## Unknowns are staged, not guessed

An unknown gets a way to be resolved: the planner decides, a read-only probe, you, or a discovery stage. Stages that depend on a discovery stage are provisional outlines, which makes the run replan. This prevents the planner from inventing detail it has no way to know.

## The critic is a separate agent

Self-grading misses the same things the plan-writing missed. The critic starts fresh, reads the plan as a file, and every finding gets a disposition. It has at most two passes per revision, and its result is tied to the plan it saw, so an edit afterwards needs it again.

## A producer is told who reads its output

A stage whose outputs other stages consume gets a "Used next by" section, and so does its reviewer. In one run `discover`, told only to "report available fields", left out `Cc`, which appears in one email of eight. Its reviewer read the plan, saw that `read-emails` extracts `Cc`, and blocked the wave. The two children had judged the report by different standards.

## Acceptance is explicit

Every writing child and every gated stage carries an explicit acceptance (`level`, `preserveStagedIndex`, `review`, `verify`). With pi-subagents' inferred acceptance, an asynchronous writer demands no staged files and an independent review, and the first real run failed on it with correct output.

## Runs are folders, and the plan holds no run state

A run is `runs/<id>/`, so the plan can be reused on new input and relative output paths resolve inside the run instead of overwriting each other's. At most one run is active, and each has one pi-subagents mission.

## `main` changes only by a reviewed merge

A run's discoveries are a proposal, not a silent change to every later run. So a run's first commit creates `run/<id>`, and a change you ask for goes to `revise/` or `improve/`. At the end you see the diff and choose. A merge is one commit, because a branch's commits are the working steps of one change, and `main`'s history should read as one entry per reviewed change.

New runs and changes refuse while another branch is checked out, so a review can't be skipped.

The workflow owns its plan: an earlier design had an outside revise command with no locks, and it was removed.

## Plan edits during a run go through a tool

Once a run starts, plan changes go through `workflow_replan` and `workflow_record_critique`, with the locked fields listed in [How Prometheus works](how-it-works.md#discovery-and-replanning). The first replan's direct edits misfired.

The plan is round-tripped YAML in a house style (quotes kept, no wrapping), so that load and dump change only the lines you edited. Repeated keys are an error: a loader that keeps the last one would let an edit that drops a `- id:` line silently merge two list items.

## The state machine is code

The legal moves are a table (`workflow_state.MOVES`), and every workflow tool's result carries the state and the moves. The model used to work out where it stood from the plan check and `run.yaml`. A test walks every state and move and found two holes a hand-written table had missed: a critique that could commit straight to `main`, and `log_start` accepted on a draft plan. See [the state machine](state-machine.md).

## Guards sit around the launch

A run starts only from a ready, valid plan. Compile commits only a wave that can launch, refuses one that was launched but never recorded (pi-subagents records the script's digest), and holds the next wave after a `review` until you say to go on. A relaunch reuses the children that completed and haven't changed. A critique must name a completed plan-critic child. Each of these closes a gap where a session ended at the wrong moment, or a step was skipped.

## Params are typed, and values live in `.env`

Params can be tokens and table names. An earlier `.pi/params.yaml` turned every value into a path, and would have put secrets in chat, compiled waves, child tasks and run logs. Now a secret appears only as `${ENV_NAME}`, the model can't read `.env`, and the agent asks you to add a secret rather than asking for it.

## One self-contained `index.ts` per agent

Each agent has one extension file, with no re-export layers: the owner pushes back on indirection and duplicate folders. The duplication between the two extensions is accepted. Prometheus is standalone: you start Pi in its folder, and it is not installed into `~/.pi/agent/`.

## Contributor notes aren't where Pi can read them

Pi loads the first of `AGENTS.override.md`, `AGENTS.md` and `CLAUDE.md` from every folder above its working directory. A `CLAUDE.md` at the repository root put about 5,000 tokens of contributor notes into every turn, about a fifth of a planning session's tokens. So the notes are in `.claude/CLAUDE.md`, which Claude Code reads and Pi doesn't. For the same reason, roles live in `.pi/APPEND_SYSTEM.md`: `AGENTS.md` from parent folders would leak Prometheus's role into workflows.

## Alternatives that were tried or rejected

* **An LLM per item for deterministic work.** What the first runs did; replaced by a program plus a gate.
* **Per-script dependency headers.** Rejected by the owner: dependencies belong in one visible place.
* **Passing a compiled wave through pi-subagents' `args`.** Not possible at the size of real waves (the cap is 16 KiB).
* **Python assembling the wave's JavaScript.** Replaced by data plus one fixed runner, after two bugs.
* **An outside revise command, with no locks.** Removed: the workflow owns its plan.
* **Releases and tags for Prometheus itself.** Skipped. A workflow's skill copy is labelled `<version>+<commit>[.dirty-<hash>]` instead, and `/workflow-refresh --all` updates only stale ones.
