# Run protocol

How a Pi session started inside a workflow runs, replans and changes
it. `/workflow-run`, `/workflow-status` and `/workflow-revise` point here.
The workflow root is the current directory; the skill lives at
`.pi/skills/workflow/` (written `<skill>` below). `<python>` is the
Python the workflow tools use: the nearest `.venv/bin/python` (`uv sync`
creates it). If its packages are missing, ask the user to run `uv sync`;
never install packages yourself.

The parent session is the orchestrator. It never writes a workflow script by
hand: `<skill>/scripts/compile_wave.py` turns the next wave into the exact
`subagent` call, and `<skill>/scripts/run_log.py` records launches and
results. The workflow's extension exposes them as tools; use the tools, and
fall back to the scripts only if the tools are missing:

| step | tool | script |
|---|---|---|
| validate + compile-check | `workflow_check` | `validate_plan.py .pi/plan.yaml`; `compile_wave.py --check` |
| start a run | `workflow_new_run {note?}` | `run_log.py new [--note T]` |
| stop the active run (then pass its `mission_close` to the subagent tool) | `workflow_stop_run {note?}` | `run_log.py stop [--note T]` |
| compile the next wave | `workflow_compile {approve?}` | `compile_wave.py [--approve k,k]` |
| record a decision | `workflow_record_decision {stage, answer}` | `run_log.py decision --stage S --answer JSON` |
| record a launch | `workflow_log_start {wave, subagent_run_id, mission_id?, approved?, decisions?}` | `run_log.py start …` |
| record a finished wave | `workflow_log_finish {wave}` | `run_log.py finish --wave W` |
| set param values | `workflow_set_params {values}` | `plan_edit.py params --values JSON` |
| change pending stages during a run | `workflow_replan {stages?, remove?, findings?, questions?, answers?, note?}` | `plan_edit.py replan --change JSON` |
| record the critic's result | `workflow_record_critique {run_id, verdict, findings}` | `plan_edit.py critique --result JSON` |
| finish a branch review | `workflow_merge {action: merge\|keep\|discard}` | `workflow_git.py merge --action A` |

The parent's jobs are the ones scripts cannot do: talk to the user,
collect decisions and approvals, read results, and replan. Children never
decide scope, never approve their own side effects, and never edit
`.pi/plan.yaml`.

**Runs.** The plan is reusable; each execution of it is a run, a
folder `runs/<id>/` holding its state (`run.yaml`: stage status and wave
launches), the children's `work/`, its outputs (relative param values) and
compiled waves (`plan-schema.md`, "Runs"). At most one run is active. Waves
are not written in the plan: the scripts derive them from the stages and the
active run's stage status (`plan-schema.md`, "Waves"). `summary.run` names
the active run, and `summary.waves` lists its waves still to run, each with
its `stages` and `ends_with`.

**Branches.** The workflow is a git repo; `main` holds the reviewed
plan (`plan-schema.md`, "Versioning"). A run starts from `main`; its
own plan changes (a replan, gate scripts written for it) go to the branch
`run/<id>`, created on its first change. `/workflow-revise` changes the
plan on a branch `change/<date>-<n>` while no run is active. Either
branch reaches `main` only when the user merges it after seeing the diff
(**Reviewing a branch**). The scripts commit; never run git commands that
change the repo.

## Every start

1. Validate: `<python> <skill>/scripts/validate_plan.py .pi/plan.yaml`.
   Read `summary`: `run`, `next_wave`, `waves`, `running`, `provisional`,
   `unresolved_unknowns`, `workflow.missing_params`, `workflow.missing_secrets`,
   `open_questions`.
2. If there is an active run, read its `runs/<id>/run.yaml`. If a launch is
   `running`, check it with
   `subagent({ action: "status", id: "<subagent_run_id>" })` before anything
   else, and go to step 6 of **Running** if it has finished. Never launch a
   wave that is running.
3. Route:
   - no active run and a `run/…` or `change/…` branch checked out → a run
     ended or a change was left without a review: **Reviewing a branch**.
   - plan `draft` or `needs_answers` with no run yet → **Planning**; with a
     change branch checked out → **Changing the plan**.
   - `ready` with the error "the next wave to run … has provisional
     stages" → a discovery wave finished but the replan did not happen;
     go to **Replanning**.
   - `ready` with no active run (`summary.run` is null) → **Starting a
     run** below.
   - `ready` → **Running** below.
   - any other validation error → report it and stop; do not run a wave
     from an invalid plan.
4. For `/workflow-status`, report plan status, the active run, the last and
   next wave, open questions, missing params, pending approvals and
   unmerged branches, then stop.

## Planning

Before the first run, the plan is written on `main` by following
`<skill>/SKILL.md`. In a workflow:

- the plan file is `.pi/plan.yaml`; do not create another
- if `request` is empty, ask the user for the request and write it verbatim
- the critic is `plan-critic` from `.pi/agents/`
- external locations become `params`, each with a `type` (path, text, secret;
  `plan-schema.md`, "Params"); set values with `workflow_set_params`

## Starting a run

- No earlier runs: start one with `workflow_new_run`, with the user's note
  for this run (from `/workflow-run <note>`) if there is one.
- Earlier runs exist, all `done` or `stopped`: say when the last one ran
  and how it ended (the check's `summary.last_run` has it; no need to look
  in `runs/`), and ask whether to start a new run (it starts from
  `main` at the first wave with an empty `work/`; the previous runs stay in
  `runs/`). Params keep their values; ask whether any input changed (say,
  another folder) and set it with `workflow_set_params`.
- The user wants to start over while a run is active: say what the active
  run has done, and only after they agree call `workflow_stop_run`, review
  its branch if it has one (**Reviewing a branch**), then start the new run.

## Running

### 1. Compile

```
<python> <skill>/scripts/compile_wave.py
```

It compiles the active run's next wave (and saves it to
`runs/<id>/.compiled/<wave>.json`), returning its `wave` id, `stages` and
`ends_with`. If `ok` is true, go to step 3. Otherwise handle its
`preflight`:

- `plan_errors` — fix the plan (replan if the error is about provisional
  stages), or report and stop.
- `missing_params` — ask the user for each value in plain chat, not with a
  question tool: a value is not a choice, so do not invent options for it.
  Say what the param is for, from its `description` in the plan's
  `params`. For a file or folder, ask for the absolute path (`~/…` is
  fine) and give an example. Record the values with `workflow_set_params`
  (it writes `.env`) and compile again; it rejects input paths that are
  relative or do not exist, so ask again if it does.
- `missing_secrets` — environment variables for secret params. Never ask for
  a secret's value, and never read or print `.env`. Tell the user which
  variable to set and what it is for (the param's `description`): add
  `NAME=value` to `.env` in the workflow folder, or set it in the environment
  pi runs in. Wait until they say it is done, then compile again (`.env` is
  reloaded; no restart needed).
- `decisions_needed` — see step 2.
- `approvals_needed` — see step 2.

### 2. Collect what needs a human

A workflow script cannot pause, so everything that needs the user happens
before launch. If a structured question tool is available
(`ask_user_question` or similar), put this wave's decisions and approvals
in one call (params are values, asked in chat as above); otherwise ask in
plain chat and end the turn.

- **decisions**: for each entry, show the user what `show_user` lists (short
  summaries of those files, not dumps) and ask for the decision. Record the
  answer with `workflow_record_decision`, which writes it to the entry's
  `write_answer_to` path.
- **approvals**: for each side-effect stage, describe exactly what will
  happen (what, where, how many, from which inputs) and get explicit
  approval naming the stage. The compile's `approvals_needed` entry has
  what you need (`title`, `why`, `consumes`, `writes`, `done_when`,
  `program`); do not read the plan for it. "Go ahead" for the wave counts only if the side
  effect was described. With a question tool, write the description in chat
  first, then ask with options such as `Approve <key>` and `Don't run it`;
  an option that does not name the stage is not an approval.

Then compile again with the approved stage keys:

```
<python> <skill>/scripts/compile_wave.py --approve <key>,<key>
```

If the user declines a side effect, do not pass it; the wave cannot launch.
Say so and ask whether to change the plan without it (**Replanning**) or
stop. In an unattended session, never approve on the user's behalf: stop
and report what is waiting.

### 3. Launch

Call the subagent tool with the compiled `call` object exactly as given:
`async`, `mission` or `missionId`, and `workflowScript`. Do not edit the
script; if something in it is wrong, fix the plan and recompile. If the
subagent tool is not loaded yet, enable it and launch; you do not need
`subagent({ action: "list" })` or a guide, since the compiled call names
its agents.

Map stages use the `workflow-batcher` agent (a command runner from
`.pi/agents/`, no model) to split items into batch files and merge batch
outputs; it must stay in the workflow.

### 4. Record the launch

With the wave id from the compile and the workflow run id (and mission id)
the launch returned:

```
<python> <skill>/scripts/run_log.py start --wave <wave> --subagent-run-id <id> --mission-id <mission> --approved <keys> --decisions '<json>'
```

This marks the wave's stages running in the run's `run.yaml` and appends a
launch entry listing them, with the wave's `ends_with`. Tell the user the
wave is running, what it does and roughly how many children it launches,
then end the turn; Pi wakes the session when it finishes.

### 5. While it runs

Answer `contact_supervisor` requests from children with
`subagent_supervisor`; route anything that is really the user's decision to
the user. Use `/subagents-fleet` or `subagent({ action: "status", … })` to
look in; do not launch anything else for this wave.

### 6. When the wave finishes

When Pi reports the wave's workflow finished, call `workflow_log_finish`
with just the wave (script: `run_log.py finish --wave <wave>`). It reads the
outcome from pi-subagents: the script's return value `{ wave, revision,
status, at?, error?, children }`, where `status` is `done`, `failed` (a child
or gate failed; `at` names the keys) or `blocked` (a review child found a
blocking problem; `at` names it), and the cost. It saves the value to
`runs/<id>/.compiled/<wave>-result.json`, commits files the wave wrote into
the workflow (a stage's program) on the run's branch, and returns a summary
of the children (status and output path each). Do not look the run up with
`subagent status`, read pi-subagents' files or assemble the result
yourself. Only if the tool says pi-subagents has no record of the run, pass
`status` and `result` from the completion notice.

- `done` → the wave's stages are marked done. Gates and review children
  have already checked the outputs: report from the summary (which stages
  ran, where their outputs are) without reading the outputs again. Read an
  output only when the user has to see it (`ends_with: review`) or a
  `verify.check` has neither a gate nor a review, and then only what you
  need (counts, a few lines), never a whole data file. Record external
  outcomes (drafts, pages, uploads) with `mission.update` receipts. Then act
  on the `ends_with` that `workflow_log_finish` returns (recorded at launch,
  so it survives a restart):
  - `review` — show a short result summary with paths; wait for the user's
    go before the next wave
  - `replan` — **Replanning** below, then continue
  - `approval` — report what the side effects did (counts, links, receipts)
  - `none` — continue to the next wave
  If it returns `run_status: done`, go to **Finish**.
- `failed` or `blocked` → the wave's stages go back to pending. Report what
  failed (the keys in `at`, the gate or review findings; or `error` and
  `failure_kind` when the workflow ended before returning, such as a
  `timeout` or a `validation` error) and offer: relaunch
  the wave (recompile and launch; the whole wave runs again, at most
  `limits.max_retries` times), change the plan (the user says what;
  **Replanning**), or stop (`workflow_stop_run`, then **Reviewing a
  branch**).

Then start again at "Every start".

## Replanning

During a run: after a wave that `ends_with: replan`, or when the user asks
for a change of pending work (after a review pause, a failure or a declined
side effect). Everything here goes through `workflow_replan` and
`workflow_record_critique` (script: `plan_edit.py replan|critique`); direct
edits to `.pi/plan.yaml` are blocked during a run. The changes are committed
on the run's branch `run/<id>`; they reach `main` only if the user merges
them when the run ends. Stages done or running in the run are history, and
the request, goal, done_when, params, inputs, outputs and agents are locked:
if those must change, say so; after the run (or after stopping it) the user
changes them with `/workflow-revise`.

What to read: the discovery stages' outputs (or what the user asked for),
`.pi/plan.yaml`, and the "Stages" and "Waves" sections of
`<skill>/references/plan-schema.md`. Read `classify.md` only if a stage's
kind may change. You do not need the compiler's source or the examples.

1. Write gate scripts for the stages you are about to make concrete into
   `gates/` (never by the stage they check), and any new project agents
   into `.pi/agents/` (`<skill>/references/agent-profiles.md`).
2. Call `workflow_replan` once with everything: `findings` for the unknowns
   the discovery resolved, and each provisional stage whose `resolves_after`
   is done, in full, without `provisional` and `resolves_after`: `pattern`,
   `agent`, `out`, `batch` and `items` (for maps), `verify`. For a change
   the user asked for, the changed pending stages in full, and a `note`
   saying why. Re-run the four questions if a stage's kind may change; a
   side effect stays a side effect. Add or remove pending stages if needed;
   the waves follow from the stages (check `waves` in the tool's result).
   The tool bumps `revision`, sets `draft`, clears the critique and commits
   on the run's branch. Fix what its validation reports with another call.
3. Run the critic on the changed stages (`critic-contract.md`, a fresh run),
   then call `workflow_record_critique` with its verdict and your
   disposition of every finding. It marks the plan ready when it validates.
4. If the stage graph changed beyond filling in details, or a new side
   effect or question appeared, show the user the change and ask before
   continuing. Otherwise compile and run the next wave.

## Finish

When `workflow_log_finish` returns `run_status: done` (every stage done):
report each `done_when` criterion as met or not, with file paths, from what
the gates and reviews checked (read an output only for a criterion none of
them covers, and only what you need). Close the run's mission by passing
the returned `mission_close` to the subagent tool unchanged. Outputs the
user asked for are where `outputs[].location` says (params resolved;
relative values inside `runs/<id>/`). If the result
carries a `review` (the run changed the plan on its branch), continue with
**Reviewing a branch**.

## Reviewing a branch

After a run ends (`workflow_log_finish` with `run_status: done`, or
`workflow_stop_run`) with a `review`, or after a change on a change branch
is ready, or whenever a `run/…` or `change/…` branch is checked out with no
run active (`<python> <skill>/scripts/workflow_git.py review` shows it).

1. Show the user what the branch changes compared with `main`: read
   `git diff main...<branch>` (read-only git is fine) and summarise it in
   words — which stages were filled in or changed and how, new or changed
   gate scripts, stage programs in `scripts/` (a run writes a stage's
   program the first time; after a merge later runs reuse it, so it is
   worth a look) and agents, the revision — plus the commit list from
   `review`. For a run branch, say what the run found that led to the
   change. Do not paste the diff.
2. Ask what to do, with these options: **merge** (future runs use this
   plan), **keep** (switch back to `main`; the branch stays for
   later), **discard** (delete the branch). Only a ready, valid plan can be
   merged; if the plan on the branch is still a draft (a run stopped
   mid-replan, an unfinished change), offer keep or discard.
3. Call `workflow_merge` with the user's choice. It switches back to `main`.
   A merge tags the revision (`r<N>`).

A new run and `/workflow-revise` both refuse while a branch other than `main`
is checked out, so this review happens before either.

## Changing the plan

`/workflow-revise [change]`, only while no run is active. The command itself
creates the branch `change/<date>-<n>` from `main` (or continues the one
checked out), bumps the revision and sets the plan to `draft`. Before the
first run there is no branch: that is **Planning** on `main`.

1. If no change was given, ask what should change.
2. Edit directly on the branch: `.pi/plan.yaml` (anything,
   including the request, goal, done_when, params, inputs, outputs and
   agents), `gates/` and `.pi/agents/`. Follow the planning rules of
   `<skill>/SKILL.md` for what you touch: classify changed stages, keep
   side effects approved, ask only the questions the change raises
   (`clarify-rules.md`), and check with `workflow_check`.
3. Run the critic on the changed plan (`critic-contract.md`, a fresh run)
   and record its result with `workflow_record_critique`; it marks the plan
   ready when it validates and commits. A branch merges only with a ready
   plan.
4. Go to **Reviewing a branch**.

## Failures

A failure of the subagent tool, workflow launch, extension loading or child
startup is an infrastructure problem: stop, report the exact error and the
run id, and do not switch to another way of running the stage (no shell
loops, no doing the stage yourself, no hand-written script) unless the user
says so.

"Acceptance rejected: Staged index changed after launch" means something
changed the workflow repo's staged index while the wave ran (writers must
leave it as they found it; the scripts never commit during a wave). Tell the
user, and relaunch once nothing else touches the workflow's repo.

## What the parent may edit

- Before the first run: `.pi/plan.yaml` directly, as the skill allows
  (planning on `main`).
- During a run: the plan only through `workflow_replan` and
  `workflow_record_critique`; the extension blocks direct edits.
- On a change branch with no run active (`/workflow-revise`): `.pi/plan.yaml`,
  `gates/` and `.pi/agents/` directly.
- Param values only through `workflow_set_params` (never secrets; `.env` is
  off limits to read or edit); run state only through
  `workflow_new_run`, `workflow_stop_run`, `workflow_record_decision`,
  `workflow_log_start` and `workflow_log_finish`.
- Gate scripts in `gates/` and agents in `.pi/agents/` while replanning.
- Never run git commands that change the workflow's repo (commit, add, reset,
  checkout, switch, merge, …; the extension blocks them). The scripts
  commit and `workflow_merge` merges; read-only git (`log`, `diff`, `show`)
  is fine.
- Nothing else in the workflow. Never edit a compiled script.
