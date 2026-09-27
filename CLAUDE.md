# Prometheus — context for Claude Code

This repo **is** Prometheus, a standalone Pi agent. You start `pi` in
the repo root; it turns a user's request into a *workflow*: a portable
folder under `workflows/<slug>/` that is itself a Pi agent (a *workflow agent*) and runs the work
with [pi-subagents](https://github.com/nicobailon/pi-subagents), one wave at a
time. Built in a long design conversation with Claude; this file carries the
decisions so you don't re-litigate them.

Owner's constraints (settled — don't reopen without asking):
- The agent is named **Prometheus** (repo folder `prometheus/`, extension `.pi/extensions/prometheus/`).
  What it creates is a **workflow** ("workflow agent" where it matters that it is a Pi agent; formerly "bundle").
  Commands keep the `workflow-` prefix.
- Runtime is the **Pi coding agent** (earendil-works/pi, built against **0.87.1**) + **pi-subagents** (0.73.1).
- Prometheus is **standalone**: this folder, `cd` + `pi`. Not a global install into `~/.pi/agent/` (option B was explicitly rejected).
- Plans are **YAML**.
- Commands and tools live in **Pi extensions** (`.pi/extensions/`), one self-contained `index.ts` per agent. No re-export indirection, no prompt templates.
- Keep it simple; the owner pushes back on indirection and duplicate folders.

## Names (settled; one word per thing)

- **workflow**: a folder `workflows/<slug>/`, itself a Pi agent ("workflow agent" only where that
  matters). Not "bundle" (the old name).
- **plan**: `.pi/plan.yaml`, reusable across runs; versioned in git with its gates and agents.
  Not "definition".
- **stage**: a unit of work in the plan. **wave**: stages launched as one subagent call (derived).
  **launch**: one attempt at a wave (`run.yaml` `launches`). **child**: one subagent item; a stage
  becomes one or more.
- **run**: one execution, `runs/<id>/`. pi-subagents' own run id for a launch is `subagent_run_id`.
- **gate**: a script in `gates/` that verifies a stage's output. "check" means validating the plan
  (`workflow_check`, `compile --check`).
- **project agent**: an agent the workflow defines in `.pi/agents/` (`source: project`, as
  pi-subagents calls them).
- **replan**: a plan change during a run (branch `run/<id>`); **change**: one made with
  `/workflow-revise` between runs (branch `change/<date>-<n>`).
- The skill is `.pi/skills/workflow/` (formerly `request-to-plan`).

## Two levels

```
prometheus/                                 ← Prometheus (cd here, run pi)
  .pi/APPEND_SYSTEM.md                      role: design workflows, never do the work
  .pi/settings.json                         subagents.projectRootResolution: nearest (pi-subagents is installed globally, not listed here)
  .pi/extensions/prometheus/index.ts        /workflow-new -list -export -refresh; tools workflow_scaffold, workflow_check
  .pi/agents/plan-critic.md                 fresh-context read-only critic
  .pi/skills/workflow/                      the skill (the logic): planning, and the run protocol workflows follow
    SKILL.md                                planning protocol (steps 1–11)
    references/                             plan-schema, classify, clarify-rules, critic-contract, agent-profiles, run-protocol
    scripts/                                validate_plan, compile_wave, batch_tool, run_log, plan_edit, workflow_git, scaffold_workflow,
                                            export_workflow, list_workflows
    examples/                               invoice-check.yaml (concrete), competitor-memo.yaml (discovery + replan)
    templates/workflow/                     everything a workflow gets (copied as-is by scaffold)
      AGENTS.md, pyproject.toml, .pi/APPEND_SYSTEM.md, .pi/settings.json (subagents config only; pi-subagents is installed globally on every machine), .pi/plan.yaml (skeleton)
      .pi/extensions/workflow/index.ts      /workflow-run -status -revise; tools workflow_check, _new_run, _stop_run, _compile, _record_decision,
                                            _log_start, _log_finish, _replan, _record_critique, _set_params, _merge; blocks direct
                                            plan/params/run-state edits once a run exists, and git commands that write
      .pi/agents/plan-critic.md, workflow-batcher.md
  workflows/<slug>/                         ← a workflow (cd here, run pi, /workflow-run)
    .git (own repo, scripts commit)  .pi/plan.yaml (the plan)  .env (values)  .pi/skills/workflow/ (copy)
    gates/ (gate scripts)  scripts/ (stage programs: written by the first run, reused after a merge)
    runs/<id>/                              one execution: run.yaml (stage status, launches), work/ (children's cwd),
                                            outputs of relative params, .compiled/
  pyproject.toml, uv.lock                   Python deps (typer, pydantic, pydantic-settings, ruamel.yaml); `uv sync` creates .venv/ (gitignored)
  package.json, package-lock.json           test-only Node deps (typescript, @types/node); pi-coding-agent and typebox
                                            come from the global Pi install (`npm root -g`), linked in by run_all.sh
  tests/                                    run_all.sh, test_validator.py, mock-subagents.mjs, ext-harness.mjs
```

Principle: **the Python scripts hold all logic; extensions are thin** (run a
script via `pi.exec`, format JSON). Scripts are testable without Pi.

Python: extensions run the scripts with `$PI_WORKFLOW_PYTHON`, else the nearest `.venv/bin/python`
upwards from the root (a workflow inside Prometheus uses Prometheus's), else `python3`; on
session start they warn if the packages are missing, and `runScript` turns a script's
`No module named ...` failure into "ask the user to run `uv sync`; never install packages yourself"
(the scripts have plain imports, no guards). Libraries: typer (CLIs), pydantic (the plan's
shape, as models in `validate_plan.py`; the cross-reference rules run after them on a well-formed
plan), ruamel.yaml (round-trip, so `plan_edit.py` keeps the plan's comments), graphlib (stdlib;
cycles and wave layering). `batch_tool.py` stays stdlib-only: the batcher runs it with plain `python3`.

## Flow

1. Prometheus, `/workflow-new <request>` → model follows `SKILL.md`: scaffold workflow
   (`workflow_scaffold`), record agents from `subagent({action:"list"})`, list
   unknowns with `resolve_by` (planner | probe | user | discovery), draft the stages
   (no waves: they are derived; stages that run first concrete, later ones
   **provisional** if they depend on discovery), classify stages, validate + compile-check (`workflow_check`), run
   `plan-critic` (fresh context) if the plan has side effects / judgment / >3
   stages, record every finding's disposition, ask ≤3 questions per round (≤2 rounds)
   **after the critic** (never param values: those are per machine, `/workflow-run`
   asks for them), set `status: ready`.
2. Workflow agent, `/workflow-run` → model follows `references/run-protocol.md`:
   no active run → `workflow_new_run` (asks first if earlier runs exist) →
   `workflow_compile` (the active run's next derived wave) → handle preflight (missing params → ask in chat, absolute
   paths, `workflow_set_params`; missing secrets → tell the user the variable to add to `.env`; decisions → ask, `workflow_record_decision`; side-effect
   approvals → describe + ask, recompile with `approve`) → pass `call` to the
   subagent tool **unchanged** → `workflow_log_start` → end turn → on wake
   `workflow_log_finish {wave}` (reads the outcome and cost from pi-subagents' `status.json`
   itself, commits files the wave wrote on `run/<id>`, returns a children summary; the parent
   doesn't re-read outputs gates checked) → act on the `ends_with` it returns (recorded at launch:
   review | replan | approval | none) → when the run is done: pass the returned `mission_close`
   to the subagent tool; if the run (or one stopped with `workflow_stop_run`) changed the plan: show `git diff main...run/<id>`, ask
   merge | keep | discard → `workflow_merge`.
3. After a discovery wave (`ends_with: replan`), or when the user asks for a change mid-run,
   the model writes gate scripts, then one `workflow_replan` call changes pending stages
   (revision bumped, committed on `run/<id>`), re-runs the critic, `workflow_record_critique`.
4. `/workflow-revise [change]` in the workflow, only with no run active: the command creates
   `change/<date>-<n>` from main (revision bumped, draft), the model edits the plan directly
   (anything may change), critic + `workflow_record_critique`, then the same review and
   `workflow_merge`. Prometheus itself no longer edits workflows (its old revise command, which
   edited a workflow from outside with no locks, was removed; the name moved to this command).

## Key design decisions (and why)

- **Four stage kinds, classified in this order** (`references/classify.md`):
  side_effect (changes anything outside the run) → decision (user's call) →
  deterministic (a program can decide correctness) → judgment. Order matters:
  irreversibility first. `kind_reason` required-ish (warning for judgment).
- **Split rule**: mostly-mechanical steps with a messy tail = script stage
  writing a leftovers file + small judgment stage on leftovers.
- **Deterministic work = script + gate**, not an LLM per item. Gate scripts live
  in `gates/`, written while planning, never by the stage they check. The program is the
  stage's `script: <file>` in the workflow's `scripts/` (versioned): the compiler tells the child
  to write it there if missing, else to run it and change it only if it fails; `run_log finish`
  commits it on `run/<id>`, so the user reviews it and later runs reuse it. Why: the first real
  runs rewrote the extraction script every time (in `runs/<id>/work/`, differently named), about
  70% of the children's output tokens. Not on map stages yet (every batch child would write it).
- **Clarifying questions**: draft first, then ask only if a wrong guess changes
  the stage graph, touches a side effect, picks the wrong source/destination,
  or changes "done". ≤3/round, ≤2 rounds, each with options + default.
- **Staged planning**: orchestrators often have nothing local to inspect, so
  unknowns get `resolve_by`; `discovery` unknowns become a discovery stage;
  dependent stages are `provisional` with `resolves_after` it (outline only; detail
  rules skipped, safety rules kept), which makes the run replan after it. Prevents
  invented detail.
- **Critic** is a separate fresh-context agent (self-grading misses the same
  things). Planner decides: accepted / rejected (with note) / asked (→ question
  with `from_finding`). Max 2 passes per revision.
- **Waves are derived, not written** (`validate_plan.derive_waves`): a workflowScript
  can't pause, so a wave is the stretch between two points where the parent acts.
  Pending stages go in the earliest wave their `needs` allow; a stage starts a later
  wave when it is a decision/approval/side effect needing a child-launching stage
  (human steps happen before a wave's script starts), when it needs a side effect
  (reported first) or a `pause_after: true` stage (user review, the planner's only
  wave choice), or when it is provisional and its `resolves_after` hasn't run
  (replan). `ends_with` is derived too (replan > review > approval > none). Stage
  status (running/done, set by `run_log.py`) lives in the active run; wave ids
  continue from the run's done launches, which record each launch's stages. This
  replaced a hand-written `waves` section and ~10 validator rules about
  it. Approval is enforced in code: `compile_wave.py` refuses side-effect stages not
  in `--approve`.
- **Runs are folders** (`runs/<id>/`, id `<date>-<n>`; helpers in `validate_plan.py`):
  the plan is reusable, each execution a run with its own `run.yaml`
  (status active|done|stopped, stage status, launches with params), `work/` (children's
  cwd), outputs and `.compiled/`. At most one active run; `workflow_new_run` starts one,
  `workflow_stop_run` stops one (kept as stopped). Relative param values (e.g. an
  `outputs` default) resolve inside the run, so runs don't overwrite each other.
  Gates keep writing `../gates/<script>`; the compiler rewrites that to the workflow's
  `gates/`. The batcher's command is `../../../.pi/...` (cwd is `runs/<id>/work`).
  One mission per run. Fresh export = plan without runs (no longer refused after a
  run); `--keep-state` copies `runs/`.
- **Each workflow is its own git repo** (`workflow_git.py`), replacing `.pi/history/`: it
  tracks the plan with its gates, agents and skill copy; the workflow `.gitignore` leaves
  out `runs/`, `.env`, installs. Only scripts commit: scaffold/refresh, run start
  (`run_log new`), compile before a launch, replan (before/after), critique; a commit
  while `ready` moves tag `r<rev>`. `run.yaml` records the commit per run and per launch.
  Never commits while a wave runs (pi-subagents compares the repo's staged index around
  writer children, and git finds the workflow's repo from the child's cwd, so the user's
  staging in an enclosing repo no longer matters). The extension blocks git commands that
  write. Git is required: without a repo `git write-tree` fails and writer children with
  `preserveStagedIndex` fail. Fresh export starts a new repo; `--keep-state` keeps `.git`.
- **Deterministic compiler** (`compile_wave.py`): next wave → one async `subagent`
  call. Layers of `runs.all` by `needs`, `max_parallel` chunking, `cwd: runs/<id>/work`,
  gates as explicit `acceptance` (below), `outputSchema` from shorthand schemas, `<key>-review` children,
  resume in-wave (`R[key].runId`) and cross-wave (`{workflowRunId, key, latest}`
  from the run's last done launch whose children include the key), mission created on
  the run's first wave then `missionId`.
- **Explicit acceptance** on every writer child (`worker`, project agents with
  `acceptanceRole: writer`) and every gated stage: `{level: verified|checked,
  preserveStagedIndex: true, review: {required: false}, verify: [gate]}`. Inferred
  acceptance for an async writer requires "no staged files" in the user's git repo
  (children's cwd is inside it) and an independent review; the first real run failed
  on that with correct output. Read-only children keep the default.
- **Branches: main changes only by a reviewed merge** (`workflow_git.py`). A run's first
  change (replan, gate scripts) creates `run/<id>` (lazily, in `workflow_git.commit`: an
  active run + main checked out → switch -c); `/workflow-revise` creates `change/<date>-<n>`
  (`workflow_git.py begin-change`, run by the command handler, bumps revision, draft). At the
  end (run done/stopped, change ready) the user sees the diff and chooses merge (only a
  ready, valid plan; ff-only, else a merge commit, conflicts → abort; tags `r<rev>`, deletes
  the branch), keep (back to main) or discard. `run_log new` and `begin-change` refuse while
  another branch is checked out, so reviews can't be skipped; merge refuses while a run is
  active. Tags only on main. Why: a run's discoveries are a proposal, not a silent change
  to every later run; and one place (the workflow) owns its plan, so Prometheus's own revise
  command (editing a workflow from outside with no locks) was removed.
- **Plan edits go through `plan_edit.py` during a run** (`workflow_replan`,
  `workflow_record_critique`; `replan` needs an active run); the workflow extension blocks
  write/edit/bash on `.pi/plan.yaml` then. Replan may change pending stages, findings,
  questions, answers; stages done/running in the run, request, goal, done_when, params,
  inputs, outputs, agents are locked (→ `/workflow-revise` after the run), side effects stay
  side effects. Direct plan edits are allowed before the first run (on main) and on a change
  branch with no run active. `runs/*/run.yaml` only through tools once a run exists; `.env`
  never (read, edit or bash; best effort for bash).
- **Round-trip YAML in house style** (`validate_plan.rt_yaml`: quotes kept, width
  4096, lists `  - `): a load + dump changes only edited lines; the model's edits
  against its memory of the file keep matching.
- **Map stages**: workflow scripts can't read files, so splitting/merging runs in a
  **command-runner agent** `workflow-batcher` (`runner.type: external-cli`,
  `python3 ../.pi/skills/workflow/scripts/batch_tool.py`, stdin marker
  `BATCH_TOOL_REQUEST {json}`). Child keys: `<key>-split`, `<key>-<n>`, `<key>-merge`
  (`map_reduce`: `<key>-reduce`). Map stages need `items`; no gates on maps.
- **Repeated YAML keys are an error** (ruamel.yaml's default; `validate_plan.load_yaml`): a loader
  that keeps the last one would let an edit that drops a `- id:` line merge two list items.
- **Returned (path-less) results don't cross waves**; later waves read files only.
- **Portability**: everything outside a workflow goes through `${params}`. Literal absolute
  `out.path` is an error. Agents with `source: user` are rejected; project agents live in the
  workflow. Export: fresh (no runs) or `--keep-state`; neither copies `.env`.
- **Param values in `.env`, typed** (`workflow_params.py`, pydantic-settings): each param has
  `type` path (default; `~` expands, relative → inside the run) | text (verbatim) | secret, and
  reads env var `env:` or its name upper-cased. Environment > `.env` > `default`. The workflow
  extension loads `.env` into pi's `process.env` (session start and before every script;
  the real environment wins), so children and gates inherit it (pi-subagents spawns children
  with `process.env`). A secret is never written down: `${secret}` compiles to `${ENV_NAME}`
  for the child's/gate's shell, `run.yaml` records "(secret, set)", `workflow_set_params`
  refuses it, preflight lists `missing_secrets` (variables for the user to set), and the
  model may not read `.env`. Why: params can be DB connections, API tokens, table names; the
  old `.pi/params.yaml` turned every non-path value into a path and would have put secrets in
  chat, compiled waves, child tasks and run logs. Refresh moves old `.pi/params.yaml` values
  into `.env`. Reserved env names (PATH, HOME, USER, PI_…) are rejected: a param `user` would
  read `$USER`.
- **Roles in `.pi/APPEND_SYSTEM.md`, not `AGENTS.md`**: Pi concatenates
  `AGENTS.md` from parent folders, which would leak Prometheus's role into workflows.
- **Command names prefixed `workflow-`**: Pi has built-in `/new` and `/export`.
- **Agent files**: pi-subagents discovers agents only in `~/.pi/agent/agents/` and
  a project's `.pi/agents/`. Prometheus has `plan-critic`; the workflow template
  has `plan-critic` + `workflow-batcher`. The two `plan-critic.md` copies must stay
  identical (tests check).

## Pi / pi-subagents facts relied on (verify if something breaks)

Pi 0.87.1 (source read from github.com/earendil-works/pi):
- Extensions: `.pi/extensions/<name>/index.ts` (project, needs trust) or `~/.pi/agent/extensions/`.
  `export default function (pi: ExtensionAPI)`. Imports `@earendil-works/pi-coding-agent`,
  `typebox` (Pi provides both as virtual modules; extensions need no node_modules).
- `pi.registerCommand(name, { description, getArgumentCompletions?(prefix), handler(args: string, ctx) })`.
- `pi.registerTool(defineTool({ name, label, description, promptSnippet?, promptGuidelines?, parameters: Type.Object(...), execute(id, params, signal, onUpdate, ctx) }))` → `{ content, details }`; throw for failure.
- `pi.exec(cmd, args, { cwd, timeout, signal })` → `{ stdout, stderr, code, killed }`.
- `pi.sendUserMessage(text)` triggers a turn; `pi.sendMessage({customType, content, display}, {triggerTurn:false})` shows text (also reaches the model next turn).
- `ctx.cwd`, `ctx.isIdle()`, `ctx.ui.notify(msg, "info"|"warning"|"error")`, `pi.on("session_start", (e, ctx) => …)`.
- `.pi/APPEND_SYSTEM.md` appends to the system prompt (project). `.pi/settings.json` `packages` load after project trust;
  Pi installs project packages into `<cwd>/.pi/npm/` (hard-coded, self-gitignored). Export leaves it out.

pi-subagents (docs: skills/pi-subagents/references/execution-controls.md):
- `subagent({ async: true, mission|missionId, workflowScript })`; script has `runs.run(key, opts)`, `runs.all([...])`, plain JS, top-level await; **no nested async functions**.
- Items: `key, label, agent, task, cwd, output, outputSchema, gate | acceptance, context, resume`.
  `gate: "cmd"` is shorthand for verified acceptance and can't be combined with `acceptance`;
  `acceptance: {level, verify: [{id, command, output?:"json", schema?, timeoutMs?}], preserveStagedIndex, review}`.
  Explicit level can't go below the inferred one (async writer → checked) except `{level:"none", reason}`.
  JSON verify + outputSchema is rejected; `resume` excludes `agent` and `gate`.
- Verified in the first real run: async launch returns the workflow run id and mission id; child
  results carry `runId`, `outputReference`, `structuredOutput`; gates run with the child's cwd (then `<workflow>/work`, now `runs/<id>/work`).
- Command-runner agents (`runner.type: external-cli`): stdin = instructions + task, stdout = output; async-only; no schema/gate/resume/fork.
- `preserveStagedIndex` (read in `src/runs/shared/acceptance.js`): `git write-tree` in the child's cwd at launch and
  at the end, compared; a failure to capture (not a repo) fails the child.
- `workflow-receipt.json` enables cross-workflow resume by key.
- An async run's record is `<tmp root>/async-subagent-runs/<runId>/status.json` (tmp root:
  `$PI_SUBAGENTS_TEMP_ROOT`, else `os.tmpdir()/pi-subagents-uid-<uid>`; src/shared/types.js).
  `state` (running … complete/failed), `workflow.value` = the workflow script's return value,
  `totalCost {costUsd, inputTokens, outputTokens}`. `mission.close` takes missionStatus
  completed | failed | cancelled. Since 0.73: a workflow that fails before returning has `error` and
  `workflow.failureKind` (validation | script | child | return-serialization | timeout |
  detached-child | runtime), which `run_log finish` records; completion notices cap long text and
  point to `status.json` (which the finish reads); a misspelled agent fails before any child starts.
- 0.73: `mcp:` direct-tool selections in agent files read pi-mcp-adapter 3.0's `mcp-adapter.json`
  (global and `.pi/mcp-adapter.json`), not Pi's `mcp.json`: matters when a stage agent gets
  connector tools (Gmail, Notion).

## Status

Tested here (all green: `uv run bash tests/run_all.sh`, 89 checks, incl. 50 validator cases and 15 param checks):
- validator rules incl. 31 broken-plan variants and 4 derived-wave cases; batch tool; scaffold; compile `--check`;
  compiled scripts executed in a **mock** of `runs.run/runs.all` (real batch tool, real gate
  scripts, fake model children): map split/merge, typed gate, failure and blocked paths,
  resume in-wave and cross-wave, preflight for params/decisions/approvals; run_log state
  transitions; export modes.
- Both extensions **type-check (strict) against real pi 0.87.1 types** and were **loaded in
  Node with Pi's real `defineTool`/`typebox`** and a mocked host; all commands and tools driven.

**Real runs so far** (emails-to-excel, sample .eml files in `scratch/emails/`): `/workflow-new`
planned it (critic loop, questions); `/workflow-run` asked for the param, ran wave 1
(scout + review) and replanned; wave 2 failed on inferred pi-subagents acceptance (fixed:
explicit acceptance) and the replan's direct plan edits misfired (fixed: `plan_edit.py`
tools + edit guard, house-style YAML). After that, w2 relaunched and passed with the explicit
acceptance; w3 (side effect) was approved through `ask_user_question` + recompile with `approve`,
wrote the workbook and passed its gate. The run completed and the mission was closed. Confirmed:
extension + package loading, async launch returns run and mission ids, child results carry
`runId`/`outputReference`/`structuredOutput` (`status` may be null on success), gates run with cwd
`work/`. A writer child fails acceptance with "Staged index changed after launch" if the user
stages files in the surrounding git repo while it runs (`preserveStagedIndex`); relaunching fixes it.
That workflow was migrated to derived waves and run folders: its first run is
`runs/2026-09-27-1/` (state rebuilt from the old `.pi/runs.yaml`, `work/` and `outputs/` moved in),
and got its own git repo, starting at tag `r2` (no copy of revision 1 existed).

Still unverified: derived waves, run folders, run/change branches and the merge review in a live run (a second run of emails-to-excel
would exercise both: `workflow_new_run`, then w1 = discover + read-emails, w2 = build-workbook), the `workflow-batcher` command
runner (no map stage has run: frontmatter format, cwd), the replan tools in a live run, and
cross-wave resume.

## Next steps (suggested order)

1. **A real run of a new plan with a map stage**, to exercise `workflow-batcher` and derived
   waves (plan with `/workflow-new`, check `summary.waves`, run it end to end).
2. Possibly let the extension launch the compiled call itself instead of the model copying
   `call` (pi-subagents may expose an extension API or event bus — check its source).
3. `lanes`/`council` patterns are validated but not compiled (validator warns).
4. Retry currently relaunches the whole wave; per-key resume of failed children would be cheaper.

## How to test

```
uv sync                                       # .venv with the scripts' packages
uv run bash tests/run_all.sh                  # scripts, compiler, mock run, export
npm install && uv run bash tests/run_all.sh   # + typecheck and extension loading (node >= 22, Pi installed globally with npm)
uv run python tests/test_validator.py         # validator cases only
```

When you add a validator rule, add a broken-variant case to `tests/test_validator.py`.
When you change a script's JSON output, check both extensions that format it.
