# Run protocol

How a Pi session in an agent runs, replans and changes it. The agent
root is the current directory; `<skill>` is `.pi/skills/agent/`.

You orchestrate: talk to the user, collect decisions and approvals, read
results, replan. The agent tools do the bookkeeping (`agent_check`,
`agent_new_run`, `agent_stop_run`, `agent_compile`,
`agent_record_decision`, `agent_log_start`, `agent_log_finish`,
`agent_set_params`, `agent_replan`, `agent_record_critique`,
`agent_merge`); never hand-write a workflow script or edit a compiled one.
Children never decide scope, approve their own side effects or edit the plan.

Each execution is a run, `runs/<id>/` (`run.yaml`: stage status and
launches; `work/`: the children's files; `.compiled/`); at most one is
active. The agent is a git repo whose `main` holds the reviewed plan. A
run's plan changes (replan, gates, stage programs) go to branch `run/<id>`;
`/agent-revise` works on `revise/<date>-<n>`, `/agent-improve` on `improve/<date>-<n>`. Either reaches `main` only
through **Reviewing a branch**. The scripts commit; never run git commands
that change the repo (read-only `log`, `diff`, `show` are fine).

Three parts are in their own files, read only when a route below sends you
there (not before): **Replanning** is `<skill>/references/replanning.md`;
**Reviewing a branch** and **Changing the plan** are
`<skill>/references/changing.md`.

## Asking the user

With a question tool (`ask_user_question` or similar), every question with a
fixed set of answers goes through it, all of one moment's questions in one
call: new run and changed input, decisions, approvals, what to do after a
failed or blocked wave, merge / keep / discard. Every question must stand on
its own, because the dialog may show it without the chat above it: the
`question` text says what happened, why it matters to the user, and what the
answer changes, and each option's `description` says what happens if it is
chosen. Keep each `header` to 16 characters (the tool rejects longer). A bare
"Apply or skip?" or "Keep approval?" is not enough.

Write questions and options for the person, in their terms: their emails, their
workbook, what a run costs them or asks of them, what will be different next
time. Leave out the agent's internals: tool names, session files, record
numbers, state names, waves, YAML keys, commits, "uncommitted", branch
mechanics. Name a file only if the user works with it (an input folder, an
output). Not "agent_check ran after agent_record_critique (records
69–72)" but "after the reviewer approved a change, the agent checked the plan
again, which it didn't need to". Technical detail (evidence, the exact edit, a
diff) goes in chat just before the question, for whoever wants it.
Values (params, paths, what to change) are asked in plain chat. Without the
tool, ask in chat and end the turn.

## Every start

1. `agent_check`. Read `state` first: where the agent stands
   (`planning`, `planned`, `ready`, `between_waves`, `wave_running`, `replan_draft`,
   `branch_review`, `change_draft`, `change_ready`, `main_draft`), its `hint`
   and the `moves` the scripts accept from it. Every agent tool's result
   carries the same as `next`: when unsure what comes next, pick from
   `next.moves`; anything else is refused. Then read `summary`: `run`,
   `next_wave`, `waves` (each with `stages`, `ends_with`), `running`,
   `provisional`, `last_run`, `open_questions`, `agent.missing_params`,
   `agent.missing_secrets`.
2. With an active run, `state.last_launch` says how its latest wave went
   (no need to read `run.yaml`). `wave_running`: check that launch with
   `subagent({ action: "status", id: "<subagent_run_id>" })` and, if it
   finished, go to **Running** step 5; never relaunch a running wave.
   `between_waves` with `last_launch.status` `failed`, `blocked` or `stopped`:
   ask the question of **Running** step 5 for a failed wave first, and
   compile only once the user chose to relaunch.
3. Route:
   - no active run and a `run/…`, `revise/…` or `improve/…` branch checked out
     (`git branch --show-current`): a run always starts from `main`, and
     `agent_new_run` keeps that branch for later. Ask (question tool)
     whether to review it now or start the run and review it later; review
     → **Reviewing a branch**, run → **Starting a run**
   - `draft` or `needs_answers` with no run → planning: follow
     `<skill>/SKILL.md` on `main` (skip step 1; the plan file exists; if
     `request` is empty, ask for it and write it verbatim); on a change
     branch → **Changing the plan**
   - error "the next wave to run … has provisional stages" → **Replanning**
   - `ready`, no active run → **Starting a run**; active run → **Running**
   - any other error → report it and stop
4. `/agent-status`: report plan status, active run, last and next wave,
   open questions, missing params, pending approvals, unmerged branches;
   stop.

## Starting a run

- No earlier runs: `agent_new_run` with the user's note, if any.
- Earlier runs, all finished: say when the last ran and how it ended
  (`summary.last_run`), and ask in one call whether to start a new run (from
  `main`, first wave, empty `work/`) and whether an input changed. For a
  changed input, ask the value in chat and set it with `agent_set_params`.
- Start over while a run is active: say what it has done; only after the
  user agrees, `agent_stop_run`, review its branch if any, then start.

## Running

### 1. Compile

`agent_compile`. If `ok`, go to 3. Otherwise handle its `preflight`:

- `plan_errors` — fix the plan (**Replanning** if about provisional
  stages), or report and stop.
- `missing_params` — ask each value in plain chat (a value, not a choice):
  what it is for (its `description`); for files and folders an absolute path
  (`~/…` is fine) with an example. Set with `agent_set_params` and
  compile again; it rejects relative or missing input paths.
- `missing_secrets` — never ask for or read a secret, and never read or
  print `.env`. Tell the user the variable and its purpose: add
  `NAME=value` to the agent's `.env` (or the environment). When they say
  it is done, compile again.
- `decisions_needed`, `approvals_needed` — step 2.
- `review_needed` — the last wave ended with a review: summarise its outputs
  (paths, counts) and ask, with the question tool, whether to go on. On a go,
  compile with `reviewed: true`; otherwise replan or stop as the user says.
- error "already launched … and not recorded" — a session ended between a
  launch and its record. Do not launch again: `agent_log_start` with the
  run id it names, then wait for it (or finish it if it has ended).

### 2. Collect what needs a human

Everything happens before launch, in one question-tool call.

- **Decisions**: show what `show_user` lists (short summaries, not dumps),
  ask, record with `agent_record_decision`.
- **Approvals**: for each side-effect stage, describe exactly what will
  happen (what, where, how many, from which inputs) from its
  `approvals_needed` entry, then ask with options such as `Approve <key>` /
  `Don't run it`. Only an option naming the stage is an approval.

Then `agent_compile` with `approve: [<keys>]`. If the user declines, the
wave cannot launch: ask whether to replan without it or stop. Unattended,
never approve: stop and report what waits.

### 3. Launch

Pass the compiled `call` to the subagent tool exactly as given
(`workflow` path included; do not open or paste the script). If the wave
is wrong, fix the plan and recompile. `/agent-run` activates the subagent
tool; enable it only if missing. No `subagent list` first.

Then `agent_log_start` with the wave, the returned run id and mission id,
and the approvals and decisions. Tell the user what the wave does and roughly
how many children it launches (a stage compile's `notes` say was reused from
an earlier run takes that run's outputs: say so in one line, it launches
nothing), and end the turn; Pi wakes the session.

### 4. While it runs

Answer `contact_supervisor` requests with `subagent_supervisor`; send the
user's decisions to the user. Launch nothing else for this wave.

### 5. When the wave finishes

`agent_log_finish` with just the wave. It reads the outcome and cost from
pi-subagents, commits files the wave wrote into the agent, and returns
`wave_status`, a children summary, `ends_with`, `run_status` and `next_wave`.
Do not look the run up yourself; only if it says pi-subagents has no record,
pass `status` and `result` from the completion notice.

- `done` — gates and reviews have checked the outputs: report from the
  summary. Read an output only when the user must see it (`review`) or a
  `verify.check` has no gate or review, and then only counts or a few lines.
  Record external outcomes (drafts, pages) with `mission.update` receipts.
  Then by `ends_with`: `review` — summarise with paths, wait for the user's
  go (the next compile asks for it: `review_needed`, then `reviewed: true`); `replan` — **Replanning**, then continue; `approval` — report what the
  side effects did; `none` — continue. `run_status: done` → **Finish**.
  To continue: if `next_wave.preflight` lists something, handle it first as
  step 1 says (ask decisions and approvals in one question-tool call), then
  `agent_compile` with `approve`; otherwise go straight to
  `agent_compile`. Skip `agent_check` and re-reading `run.yaml` between
  waves of one session.
- `failed` / `blocked` — the stages go back to pending. Report what failed
  (`stopped_at`, gate or review findings, or `error` and `failure_kind`), then ask:
  relaunch the wave (at most `limits.max_retries`; children that completed and
  are unchanged are not run again, compile's `notes` say which), change the plan
  (**Replanning**), or stop (`agent_stop_run`, then **Reviewing a
  branch**).

Then compile `next_wave` as above, or after a failure or a new session start
again at **Every start**.

## Finish

On `run_status: done`: report each `done_when` criterion it returns as met or
not, with paths, from what gates and reviews checked. Pass `mission_close`
to the subagent tool unchanged. Deliverables are at `outputs[].location`
(relative values inside `runs/<id>/`). If it returns a `review`, go on to
**Reviewing a branch**.

## Failures

A failure of the subagent tool, a launch, extension loading or child startup
is infrastructure: report the exact error and run id and stop. Do not run the
stage another way (shell loops, yourself, a hand-written script) unless the
user says so.

"Acceptance rejected: Staged index changed after launch": something changed
the agent repo's staged index during the wave. Tell the user; relaunch
once nothing else touches the repo.

## What you may edit

The plan directly only before the first run (on `main`) or on a change
branch, with `gates/`, `scripts/`, `.pi/agents/` and packages (`agent_add_package`,
into `pyproject.toml`). During a run only through the replan tools, plus
`gates/`, `.pi/agents/` and packages while replanning; stage programs are
the children's. Params only through `agent_set_params`; `.env` never. Run
state only through the agent tools. Nothing else.
