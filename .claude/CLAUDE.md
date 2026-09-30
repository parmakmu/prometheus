# Prometheus — context for Claude Code

This repo **is** Prometheus, a standalone Pi agent. You start `pi` in the repo root; it turns a
user's request into a *workflow*: a portable folder under `workflows/<slug>/` that is itself a Pi
agent (a *workflow agent*) and runs the work with
[pi-subagents](https://github.com/nicobailon/pi-subagents), one wave at a time. This file carries
the settled decisions and the reasons for them, so you don't re-litigate them.

**Docs** (for people running workflows and for contributors; keep them true when you change behaviour):

| Page | Covers |
|---|---|
| [`docs/how-it-works.md`](../docs/how-it-works.md) | lifecycle, stage kinds, waves, runs, git branches, implementation map, glossary |
| [`docs/commands.md`](../docs/commands.md) | every command and model-facing tool |
| [`docs/python-scripts.md`](../docs/python-scripts.md) | what each script does and how they fit together |
| [`docs/params-and-secrets.md`](../docs/params-and-secrets.md) | params, `.env`, secrets, their validator rules |
| [`docs/troubleshooting.md`](../docs/troubleshooting.md) | refusals a user meets, their causes and fixes |
| [`docs/worked-example.md`](../docs/worked-example.md) | emails-to-excel from request to reused stage programs |

The model-facing rules are in the skill: `SKILL.md` (planning), `references/run-protocol.md`
(running, replanning, changing, reviewing), `references/plan-schema.md` (the plan format).

## Owner's constraints (settled — don't reopen without asking)

- The agent is **Prometheus** (repo folder `prometheus/`, extension `.pi/extensions/prometheus/`).
  Commands keep the `workflow-` prefix (Pi has built-in `/new` and `/export`).
- Runtime is the **Pi coding agent** (earendil-works/pi, built against **0.87.1**; the test suite also passes on 0.99.2) + **pi-subagents** (0.73.1; tests also pass on 0.74.0). Live runs so far were on the older versions.
- Prometheus is **standalone**: this folder, `cd` + `pi`. Not a global install into `~/.pi/agent/`.
- Plans are **YAML**.
- Commands and tools live in **Pi extensions**, one self-contained `index.ts` per agent. No
  re-export indirection, no prompt templates.
- Keep it simple; the owner pushes back on indirection and duplicate folders.
- Python: typer for CLIs, pathlib for paths (`os` only where pathlib has no equivalent: `chdir`,
  `environ`, `getuid`), pytest for tests. Never install packages; ask the user to run `uv sync`.

## Names (settled; one word per thing)

- **workflow**: a folder `workflows/<slug>/`, itself a Pi agent ("workflow agent" only where that
  matters). Not "bundle".
- **plan**: `plan.yaml`, reusable across runs; versioned in git with its gates, programs and
  agents. Not "definition".
- **stage**: a unit of work in the plan. **wave**: stages launched as one subagent call (derived).
  **launch**: one attempt at a wave (`run.yaml` `launches`). **child**: one subagent item; a stage
  becomes one or more.
- **run**: one execution, `runs/<id>/`. pi-subagents' own run id for a launch is `subagent_run_id`.
- **gate**: a script in `gates/` that verifies a stage's output. **stage program**: a stage's
  `script:` in `scripts/`. "check" means validating the plan (`workflow_check`, `compile --check`).
- **project agent**: an agent the workflow defines in `.pi/agents/` (`source: project`).
- **replan**: a plan change during a run (branch `run/<id>`); **change**: one made with
  `/workflow-revise` between runs (branch `change/<date>-<n>`).
- The skill is `.pi/skills/workflow/` (formerly `request-to-plan`).

## Architecture in one paragraph

The Python scripts in `.pi/skills/workflow/scripts/` hold all logic and print one JSON object;
the two extensions are thin (run a script via `pi.exec`, format its JSON). Scripts are testable
without Pi. Extensions run them with `$PI_WORKFLOW_PYTHON`, else the nearest `.venv/bin/python`
upwards (a workflow inside Prometheus uses Prometheus's), else `python3`; a script's
`No module named …` becomes "ask the user to run `uv sync`". `templates/workflow/` is copied
into each workflow by `scaffold_workflow.py`, together with a copy of the whole skill, so a
workflow runs without Prometheus. `wave_runner.js` is the one piece of JS: the compiler appends
it to every wave. `batch_tool.py` stays stdlib-only: the batcher runs it with plain `python3`.
Libraries: typer, pydantic (plan shape, in `validate_plan.py`; the cross-reference rules, one
`Checker` method per group, run after on a well-formed plan), pydantic-settings + python-dotenv
(param values), ruamel.yaml (round-trip), graphlib (cycles, wave layering).

## Key design decisions (and why)

- **Four stage kinds, classified in this order** (`references/classify.md`): side_effect →
  decision → deterministic → judgment. Irreversibility first.
- **Split rule**: mostly-mechanical work with a messy tail becomes a script stage that writes a
  leftovers file, plus a small judgment stage on the leftovers.
- **Deterministic work = program + gate**, not an LLM per item. Gates are written while planning,
  never by the stage they check. The program is the stage's `script:` (requires
  `pattern: script`, not on map stages: every batch child would write it): the compiler tells the
  child to write it into `scripts/` if missing, else to run it and change it only if it fails;
  `run_log finish` commits it on `run/<id>`, so the user reviews it and later runs reuse it.
  Why: the first real runs rewrote the extraction program every run, ~70% of children's output.
- **Planning starts only with `/workflow-new`.** The planning skill has `disable-model-invocation: true`;
  `workflow_scaffold` refuses unless `/workflow-new` ran in the session (one workflow per call), and
  bash running `scaffold_workflow.py` is blocked. Why: a plain message ("I want to create
  documentation with docs in md") was taken as a request and scaffolded a workflow nobody asked for.
- **Choices go through the question tool**: in a workflow, every question with fixed answers (new
  run?, input changed?, decisions, approvals, after a failed/blocked wave, merge/keep/discard) uses
  `ask_user_question` when available (run-protocol "Asking the user"); plain chat only for values.
  Why: run 5 asked its start and failure questions in plain chat, since the rule covered approvals only.
- **Clarifying questions**: draft first, then ask only if a wrong guess changes the stage graph,
  a side effect, a source/destination or "done". ≤3 per round, ≤2 rounds, after the critic.
  Never param values (per machine; `/workflow-run` asks).
- **Staged planning**: unknowns get `resolve_by` (planner | probe | user | discovery); discovery
  unknowns become a discovery stage, and dependent stages are `provisional` with `resolves_after`
  (outline only: detail rules skipped, safety rules kept), which makes the run replan. Prevents
  invented detail.
- **Critic** is a separate fresh-context agent (self-grading misses the same things). Every
  finding gets a disposition: accepted / rejected (with note) / asked. Max 2 passes per revision.
- **Waves are derived, not written** (`validate_plan.derive_waves`): a workflow script can't
  pause, so a wave is the stretch between two points where the parent acts. A stage starts a
  later wave when it's a decision/approval/side effect needing a child-launching stage, needs a
  side effect or a `pause_after` stage, or is provisional and its `resolves_after` hasn't run.
  `ends_with` is derived (replan > review > approval > none) and recorded at launch. Approval is
  enforced in code: `compile_wave.py` refuses side-effect stages not in `--approve`.
- **Runs are folders** (`runs/<id>/`): the plan holds no run state. At most one active run.
  Relative param values resolve inside the run, so runs don't overwrite each other. One mission
  per run.
- **Each workflow is its own git repo** (`workflow_git.py`). Only scripts commit, never while a
  wave runs (pi-subagents compares the repo's staged index around writer children; git finds the
  workflow's repo from the child's cwd). The extension blocks git commands that write. A commit
  on `main` with a ready plan moves tag `r<rev>`.
- **Main changes only by a reviewed merge.** A run's first commit creates `run/<id>` lazily;
  `/workflow-revise` creates `change/<date>-<n>`. At the end the user sees the diff and chooses
  merge (only a ready, valid plan; a fast-forward, rebased onto `main` first if it moved, so history
  stays linear; the branch is deleted) / keep / discard. New runs and changes refuse while another
  branch is checked out, so reviews can't be skipped. Why: a run's discoveries are a proposal,
  not a silent change to every later run; and the workflow owns its plan (Prometheus's old
  outside revise command, with no locks, was removed).
- **Plan edits during a run go through `plan_edit.py`** (`workflow_replan`,
  `workflow_record_critique`). Locked: stages that ran, request, goal, done_when, params, inputs,
  outputs, agents; side effects stay side effects. Direct edits only before the first run or on
  a change branch with no run active. Why: the first replan's direct edits misfired.
- **Round-trip YAML in house style** (`validate_plan.rt_yaml`: quotes kept, width 4096, lists
  `  - `): a load + dump changes only edited lines. Repeated keys are an error, since a loader
  keeping the last one lets an edit that drops a `- id:` line merge two list items.
- **A producer is told who reads its output**: a stage whose outputs other stages consume gets a
  "Used next by" section (each consumer, what it reads, its `verify.check`); its reviewer gets the
  same lines. Why: in run 5 `discover`, told only "report available fields", left out `Cc`, which
  appears in one file of eight. Its reviewer read the plan, saw `read-emails` extracts `Cc`, and
  blocked the wave. The two children had judged the report by different standards.
- **Explicit acceptance** on every writer child and gated stage: `{level, preserveStagedIndex:
  true, review: {required: false}, verify}`. Why: inferred acceptance for an async writer demands
  no staged files and an independent review; the first real run failed on it with correct output.
- **The compiled call carries `workflowScriptPath`**, the script saved at
  `runs/<id>/.compiled/<wave>.js`, not the script text. Why: retyping it was ~half the
  orchestrator's output tokens. `--check` and a directly built `Compiler` keep it inline.
- **The compiler writes data, not code**: a compiled script is `const W = {wave as JSON};` plus
  `scripts/wave_runner.js` unchanged (layers via `runs.all`, map split/batches/merge|reduce,
  reviews, failure/block stops). Why: Python assembling JS strings caused two bugs (list commas,
  the `status` field) and was hard to read. Not via pi-subagents' `args`: it caps them at 16 KiB,
  16 fields per object and 64 items per array; the example plan's w1 is 19.6 KB. A test runs
  pi-subagents' own `validateWorkflowScript` on compiled waves.
- **Stage programs run as commands**: a stage with `script:` and `run: "<args>"` whose program
  exists compiles to a `workflow-batcher` item (`batch_tool.py` op `run`: `uv run
  scripts/<file> <args>` from `work/`, then the gate, through the shell). It carries a `fix` item, a worker child
  the runner launches only if that fails, shown the error (`{failure: key}` task part), with the
  usual acceptance gate. Values in `run:` are shell-quoted; secrets stay `"${ENV}"`. Why: run 7
  spent 35 s on two model children that only ran reviewed programs (~1 s as commands).
  `runs.host` is not open to raw workflow scripts, so the batcher (external-cli) does it.
- **A workflow's packages are in its `pyproject.toml`** (`uv add`, locked in `uv.lock`), used by
  the skill scripts, gates and stage programs alike; gates and programs run with `uv run`, which
  syncs `.venv/` first, so no `uv sync` pause. The validator rejects a third-party import
  `pyproject.toml` doesn't list, a PEP 723 `# /// script` block, and packages run with `python3`;
  the compile preflight a missing `uv`; `/workflow-refresh` keeps the workflow's packages. Creating
  and refreshing a workflow run `uv lock`, so `uv.lock` is committed up front: otherwise the first
  `uv run` wrote it mid-wave and every run left a branch to review. Why:
  with "standard library or what is installed" the models hand-wrote XLSX readers and writers
  (~100 lines each) instead of using openpyxl; per-script headers were tried and rejected by the
  owner: dependencies must be visible in one place.
- **The orchestrator does no bookkeeping**: `workflow_log_finish {wave}` reads the outcome and
  cost from pi-subagents' `status.json`, returns a children summary (not outputs), the
  `mission_close` call and, when the run is done, the plan's `done_when` for the final report;
  compile's preflight describes approvals; the check reports `last_run`. Why: run 2's log showed
  ~12 of 26 orchestrator turns spent on these; run 6 still read the plan for `done_when`.
- **Commands activate `subagent`**: `/workflow-new`, `/workflow-run` and `/workflow-revise` call
  `activateSubagent` (`pi.setActiveTools`) before sending their prompt. Why: pi-subagents starts
  sessions with only its `subagents_enable` loader; the model enabling it mid-turn cost run 4 two
  turns and a prompt-cache rewrite (~$0.003 of $0.0135). The command is the user's authorization.
- **Params are typed, values in `.env`** (see `docs/params-and-secrets.md`). Why: params can be
  tokens and table names; the old `.pi/params.yaml` turned every value into a path and would have
  put secrets in chat, compiled waves, child tasks and run logs.
- **Map stages** split/merge in the command-runner agent `workflow-batcher` (workflow scripts
  can't read files). Child keys `<key>-split`, `<key>-<n>`, `<key>-merge` / `-reduce`.
- **Returned (path-less) results don't cross waves**; later waves read files only.
- **The plan is `plan.yaml` at the workflow root**, beside `gates/` and `scripts/`, not in `.pi/`
  (Pi's config). `/workflow-refresh` moves an older `.pi/plan.yaml` and commits the move.
- **Roles in `.pi/APPEND_SYSTEM.md`, not `AGENTS.md`**: Pi concatenates `AGENTS.md` from parent
  folders, which would leak Prometheus's role into workflows.
- **This file is `.claude/CLAUDE.md`, not `CLAUDE.md`**: Pi loads the first of `AGENTS.override.md`,
  `AGENTS.md`, `CLAUDE.md` from every folder up from its cwd, so a root `CLAUDE.md` put ~5k tokens
  of contributor notes into every Prometheus and in-repo workflow turn (~20% of a planning
  session's tokens). Claude Code reads `.claude/CLAUDE.md`; Pi doesn't. Keep the root free of those names.
- **Agent files**: pi-subagents discovers agents only in `~/.pi/agent/agents/` and a project's
  `.pi/agents/`. The two `plan-critic.md` copies (Prometheus, workflow template) must stay
  identical (tests check).

## Pi / pi-subagents facts relied on (verify if something breaks)

Pi 0.87.1:

- Extensions: `.pi/extensions/<name>/index.ts` (project, needs trust). `export default function
  (pi: ExtensionAPI)`; `@earendil-works/pi-coding-agent` and `typebox` are provided by Pi.
- `pi.registerCommand(name, { description, getArgumentCompletions?, handler(args, ctx) })`;
  `pi.registerTool(defineTool({ name, label, description, promptSnippet?, promptGuidelines?,
  parameters, execute(id, params, signal, onUpdate, ctx) }))` → `{ content, details }`, throw on failure.
- `pi.exec(cmd, args, { cwd, timeout, signal })` → `{ stdout, stderr, code, killed }`.
  `pi.sendUserMessage(text)` triggers a turn; `pi.sendMessage({customType, content, display},
  {triggerTurn:false})` shows text. `ctx.cwd`, `ctx.ui.notify`, `pi.on("session_start" | "tool_call", …)`.
- `.pi/APPEND_SYSTEM.md` appends to the system prompt. Project packages install into `<cwd>/.pi/npm/`.

pi-subagents 0.73.1 (source in `~/.pi/agent/npm/node_modules/pi-subagents`):

- `subagent({ async: true, mission|missionId, workflowScript | workflowScriptPath })`; the script
  uses `runs.run(key, opts)` / `runs.all([...])`, plain JS, top-level await, no nested async
  functions. `workflowScriptPath` is read by `loadWorkflowScriptPath` in `executePublic` before
  anything else, so it behaves exactly like inline text (confirmed live in run 4).
- A child result is `{ok, state, stopped?, detached?, error?, runId, output, outputReference,
  structuredOutput}`: no `status`. `runs.run` throws on a failed child; `runs.all` with config
  objects returns it as `{ok: false}`. The mock (`tests/mock-subagents.mjs`) mirrors both.
- Items: `key, label, agent, task, cwd, output, outputSchema, acceptance, context, resume`.
  `acceptance: {level, verify: [{id, command, output?:"json", schema?, timeoutMs?}],
  preserveStagedIndex, review}`; JSON verify + outputSchema is rejected; `resume` excludes
  `agent` and gates. Keyed cross-workflow resume: `{workflowRunId, key, latest}`.
- `preserveStagedIndex`: `git write-tree` in the child's cwd at launch and end, compared;
  failing to capture (not a repo) fails the child.
- Command-runner agents (`runner.type: external-cli`): stdin = instructions + task, stdout =
  output; a non-zero exit fails the child with stderr as its `error`; async only; no
  schema/gate/resume/fork. `runs.host` commands are only for extension-owned named workflows,
  not raw `workflowScript`/`workflowScriptPath`.
- Tool activation (Pi >= 0.86.1): a fresh session has `subagents_enable` active and `subagent`
  registered but inactive (pi-subagents 0.74: `toolActivation` in its `config.json`, default `auto`,
  starts models that can't add a tool mid-conversation with `subagent` already active; it doesn't
  use Pi 0.99's per-tool `exposure` for this, only for its own `model-only` tools). pi-subagents re-applies the recorded selection only on `session_start`
  and `session_tree`; its `before_agent_start` hook only keeps the loader active, so another
  extension's `setActiveTools([..., "subagent"])` from a command handler holds (not yet live-verified).
- Children are spawned with pi's `process.env` (so `.env` loaded by the extension reaches them).
- Async run record: `<tmp root>/async-subagent-runs/<runId>/status.json` (tmp root:
  `$PI_SUBAGENTS_TEMP_ROOT`, else `os.tmpdir()/pi-subagents-uid-<uid>`). `state`,
  `workflow.value` (the script's return value), `totalCost {costUsd, inputTokens, outputTokens}`;
  a workflow that fails before returning has `error` and `workflow.failureKind`.
  `mission.close` takes missionStatus completed | failed | cancelled.
- `mcp:` selections in an agent's `tools` (`mcp:server`, `mcp:server/tool`): with pi-mcp-adapter
  installed they resolve against its config; without it (the owner's setup) and on Pi >= 0.99
  they resolve against Pi's built-in MCP and its `mcp.json` (a project's `.pi/mcp.json` only once
  trusted), whatever exposure the server has (default `codemode`, hidden from the model); the child
  gets only the selected tools, as direct ones. Waves are async, so servers an extension adds with
  `pi.registerMcpServer()` work too; a selected tool that doesn't register within 10 s fails the
  launch. (From pi-subagents 0.74 docs; not live-verified. Matters once a stage agent gets connector tools.)

## Status

`uv run pytest`: 190 tests, including the validator's broken-variant cases, param tests, compiled
waves run in a mock of `runs.run/runs.all` (real batch tool and gates, fake children), and both
extensions type-checked and driven with Pi's real types. The 92 outside `tests/test_workflow.py`
pass without Node or uv; that file's 98 (the scenario and the extensions) need `npm install` and
uv, and have not run since the plan moved to the workflow root and programs to `uv run`.

Verified in live runs of emails-to-excel (see `docs/worked-example.md`): planning with the
critic and questions; discovery + replan; explicit acceptance; derived waves and run folders;
side-effect approval; `.env` loading; `workflow_log_finish` reading `status.json` and returning
`mission_close`; stage programs written and committed at finish; the end-of-run review (keep);
`/workflow-revise` with the critic and a merge; `workflowScriptPath`; a run reusing merged programs
unchanged (run 4: nothing committed, children $0.0105, orchestrator $0.0135); the question tool for
every choice and `subagent` active from the command (run 6); "Used next by" (run 6: `discover`
covered `Cc`, review passed); the data + `wave_runner.js` compiled waves and `done_when` in the
finish result (run 7); stage programs run as commands via `run:` (run 8: `read-emails` and
`build-workbook` 0.1 s each at $0 on `workflow-batcher`, vs 20 s and 15 s as model children; the
run took about 1 min, down from 2 min 22 s).

Not yet verified live: the `<key>-fix` fallback for a failing program; the
`workflow-batcher` command runner (no map stage has run); the replan tools since they were added;
cross-wave resume; secret params; refresh refusing during a wave.

## Next steps (suggested order)

1. A new plan with a map stage, to exercise `workflow-batcher`.
2. Check that side-effect stages' agents have the connector tools they need (a validator rule).
3. Retry relaunches the whole wave; per-key resume of failed children would be cheaper.

## How to test

```sh
uv sync                                  # .venv with the scripts' packages and pytest
uv run pytest                            # everything; extension tests skip without the Node deps
npm install && uv run pytest             # + typecheck and extension loading (node >= 22, Pi installed globally with npm)
uv run pytest tests/test_validator.py    # validator cases only
uv run ruff check
```

`tests/test_workflow.py` is one continuous scenario: later tests use state earlier ones left on
disk (a module-scoped `env` fixture carries run ids and paths), so its tests run in file order.
Don't add a test-randomizing plugin, and put a new check where the state it needs exists.

When you add a validator rule, add a broken-variant case to `tests/test_validator.py`. When you
change a script's JSON output, check both extensions that format it. When you add a refusal a
user can meet, add it to `docs/troubleshooting.md`.
