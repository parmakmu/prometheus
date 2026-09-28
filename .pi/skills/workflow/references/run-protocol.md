# Run protocol

How a Pi session in a workflow runs, replans and changes it. The workflow
root is the current directory; `<skill>` is `.pi/skills/workflow/`.

You orchestrate: talk to the user, collect decisions and approvals, read
results, replan. The workflow tools do the bookkeeping (`workflow_check`,
`workflow_new_run`, `workflow_stop_run`, `workflow_compile`,
`workflow_record_decision`, `workflow_log_start`, `workflow_log_finish`,
`workflow_set_params`, `workflow_replan`, `workflow_record_critique`,
`workflow_merge`); never hand-write a workflow script or edit a compiled one.
Children never decide scope, approve their own side effects or edit the plan.

Each execution is a run, `runs/<id>/` (`run.yaml`: stage status and
launches; `work/`: the children's files; `.compiled/`); at most one is
active. The workflow is a git repo whose `main` holds the reviewed plan. A
run's plan changes (replan, gates, stage programs) go to branch `run/<id>`;
`/workflow-revise` works on `change/<date>-<n>`. Either reaches `main` only
through **Reviewing a branch**. The scripts commit; never run git commands
that change the repo (read-only `log`, `diff`, `show` are fine).

## Asking the user

With a question tool (`ask_user_question` or similar), every question with a
fixed set of answers goes through it, all of one moment's questions in one
call: new run and changed input, decisions, approvals, what to do after a
failed or blocked wave, merge / keep / discard. First write in chat what the
user needs to answer (what a side effect will do, what failed and why).
Values (params, paths, what to change) are asked in plain chat. Without the
tool, ask in chat and end the turn.

## Every start

1. `workflow_check`. Read `summary`: `run`, `next_wave`, `waves` (each with
   `stages`, `ends_with`), `running`, `provisional`, `last_run`,
   `open_questions`, `workflow.missing_params`, `workflow.missing_secrets`.
2. With an active run, read `runs/<id>/run.yaml`. If a launch is `running`,
   check it with `subagent({ action: "status", id: "<subagent_run_id>" })`
   first and, if it finished, go to **Running** step 5. Never relaunch a
   running wave.
3. Route:
   - no active run and a `run/…` or `change/…` branch checked out
     (`git branch --show-current`) → **Reviewing a branch**
   - `draft` or `needs_answers` with no run → planning: follow
     `<skill>/SKILL.md` on `main` (skip step 1; the plan file exists; if
     `request` is empty, ask for it and write it verbatim); on a change
     branch → **Changing the plan**
   - error "the next wave to run … has provisional stages" → **Replanning**
   - `ready`, no active run → **Starting a run**; active run → **Running**
   - any other error → report it and stop
4. `/workflow-status`: report plan status, active run, last and next wave,
   open questions, missing params, pending approvals, unmerged branches;
   stop.

## Starting a run

- No earlier runs: `workflow_new_run` with the user's note, if any.
- Earlier runs, all finished: say when the last ran and how it ended
  (`summary.last_run`), and ask in one call whether to start a new run (from
  `main`, first wave, empty `work/`) and whether an input changed. For a
  changed input, ask the value in chat and set it with `workflow_set_params`.
- Start over while a run is active: say what it has done; only after the
  user agrees, `workflow_stop_run`, review its branch if any, then start.

## Running

### 1. Compile

`workflow_compile`. If `ok`, go to 3. Otherwise handle its `preflight`:

- `plan_errors` — fix the plan (**Replanning** if about provisional
  stages), or report and stop.
- `missing_params` — ask each value in plain chat (a value, not a choice):
  what it is for (its `description`); for files and folders an absolute path
  (`~/…` is fine) with an example. Set with `workflow_set_params` and
  compile again; it rejects relative or missing input paths.
- `missing_secrets` — never ask for or read a secret, and never read or
  print `.env`. Tell the user the variable and its purpose: add
  `NAME=value` to the workflow's `.env` (or the environment). When they say
  it is done, compile again.
- `decisions_needed`, `approvals_needed` — step 2.

### 2. Collect what needs a human

Everything happens before launch, in one question-tool call.

- **Decisions**: show what `show_user` lists (short summaries, not dumps),
  ask, record with `workflow_record_decision`.
- **Approvals**: for each side-effect stage, describe exactly what will
  happen (what, where, how many, from which inputs) from its
  `approvals_needed` entry, then ask with options such as `Approve <key>` /
  `Don't run it`. Only an option naming the stage is an approval.

Then `workflow_compile` with `approve: [<keys>]`. If the user declines, the
wave cannot launch: ask whether to replan without it or stop. Unattended,
never approve: stop and report what waits.

### 3. Launch

Pass the compiled `call` to the subagent tool exactly as given
(`workflowScriptPath` included; do not open or paste the script). If the wave
is wrong, fix the plan and recompile. `/workflow-run` activates the subagent
tool; enable it only if missing. No `subagent list` first.

Then `workflow_log_start` with the wave, the returned run id and mission id,
and the approvals and decisions. Tell the user what the wave does and roughly
how many children it launches, and end the turn; Pi wakes the session.

### 4. While it runs

Answer `contact_supervisor` requests with `subagent_supervisor`; send the
user's decisions to the user. Launch nothing else for this wave.

### 5. When the wave finishes

`workflow_log_finish` with just the wave. It reads the outcome and cost from
pi-subagents, commits files the wave wrote into the workflow, and returns
`wave_status`, a children summary, `ends_with` and `run_status`.
Do not look the run up yourself; only if it says pi-subagents has no record,
pass `status` and `result` from the completion notice.

- `done` — gates and reviews have checked the outputs: report from the
  summary. Read an output only when the user must see it (`review`) or a
  `verify.check` has no gate or review, and then only counts or a few lines.
  Record external outcomes (drafts, pages) with `mission.update` receipts.
  Then by `ends_with`: `review` — summarise with paths, wait for the user's
  go; `replan` — **Replanning**, then continue; `approval` — report what the
  side effects did; `none` — continue. `run_status: done` → **Finish**.
- `failed` / `blocked` — the stages go back to pending. Report what failed
  (`entry.stopped_at`, gate or review findings, or `error` and `failure_kind`), then ask:
  relaunch the whole wave (at most `limits.max_retries`), change the plan
  (**Replanning**), or stop (`workflow_stop_run`, then **Reviewing a
  branch**).

Then start again at **Every start**.

## Replanning

After a wave that `ends_with: replan`, or when the user wants pending work
changed. The plan changes only through `workflow_replan` and
`workflow_record_critique`, committed on `run/<id>`. Stages done or running
are history; request, goal, done_when, params, inputs, outputs and agents are
locked (they change with `/workflow-revise` after the run). A side effect
stays a side effect.

Read the discovery outputs (or the user's request), `plan.yaml`, and
`<skill>/references/plan-schema.md` ("Stages", "Waves"); `classify.md` only
if a kind may change.

1. Write gates for the stages you make concrete into `gates/` (a package
   they import: `uv add <package>`), and new agents into `.pi/agents/`
   (`<skill>/references/agent-profiles.md`).
2. One `workflow_replan` call: `findings` for the resolved unknowns, and
   each provisional stage whose `resolves_after` is done, in full and
   without `provisional` / `resolves_after` (`pattern`, `agent`, `out`,
   `batch` and `items` for maps, `verify`); for a user's change, the changed
   stages and a `note`. It bumps the revision, sets `draft` and commits; fix
   what its validation reports with another call.
3. Run the critic on the changed stages (`<skill>/references/critic-contract.md`)
   and record it with `workflow_record_critique`, which marks the plan ready
   when it validates. At most two critic passes per replan, however many
   revisions it takes (`workflow_record_critique` refuses a third): after
   the second, reject what is left with a note (tell the user about a
   blocking one) and continue.
4. If the stage graph changed beyond filled-in details, or a side effect or
   question appeared, show the user and ask before continuing.

## Finish

On `run_status: done`: report each `done_when` criterion it returns as met or
not, with paths, from what gates and reviews checked. Pass `mission_close`
to the subagent tool unchanged. Deliverables are at `outputs[].location`
(relative values inside `runs/<id>/`). If it returns a `review`, go on to
**Reviewing a branch**.

## Reviewing a branch

After a run ends with a `review`, after a change is ready, or whenever a
`run/…` or `change/…` branch is checked out with no run active
(`<python> <skill>/scripts/workflow_git.py review`, `<python>` being the
nearest `.venv/bin/python`).

1. Read `git diff main...<branch>` and summarise it in words, without
   pasting it: stages filled in or changed, gates, stage programs in
   `scripts/` (later runs execute them without a model, so they are worth a
   look), agents, the revision, the commits; for a run, what it found.
2. Ask: **merge** (future runs use it), **keep** (back to `main`, the branch
   stays), **discard**. A draft plan can only be kept or discarded.
3. `workflow_merge` with the choice.

New runs and `/workflow-revise` refuse until this is done.

## Changing the plan

`/workflow-revise [change]`, no run active. The command creates or continues
`change/<date>-<n>`, bumps the revision and sets `draft`.

1. If no change was given, ask what should change.
2. Edit `plan.yaml` (anything), `gates/`, stage programs in `scripts/` and
   `.pi/agents/` directly (packages: `uv add <package>`), following
   `<skill>/SKILL.md` for what you touch; `workflow_check`. Delete a program
   to have the next run write it again.
3. Critic and `workflow_record_critique`, as in **Replanning** step 3.
4. **Reviewing a branch**.

## Failures

A failure of the subagent tool, a launch, extension loading or child startup
is infrastructure: report the exact error and run id and stop. Do not run the
stage another way (shell loops, yourself, a hand-written script) unless the
user says so.

"Acceptance rejected: Staged index changed after launch": something changed
the workflow repo's staged index during the wave. Tell the user; relaunch
once nothing else touches the repo.

## What you may edit

The plan directly only before the first run (on `main`) or on a change
branch, with `gates/`, `scripts/`, `.pi/agents/` and packages (`uv add`,
into `pyproject.toml`). During a run only through the replan tools, plus
`gates/`, `.pi/agents/` and packages while replanning; stage programs are
the children's. Params only through `workflow_set_params`; `.env` never. Run
state only through the workflow tools. Nothing else.
