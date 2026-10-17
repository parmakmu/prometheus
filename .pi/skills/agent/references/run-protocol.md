# Run protocol

How a Pi session in an agent runs, resolves and changes it. The agent
root is the current directory; `<skill>` is `.pi/skills/agent/`.

You orchestrate: talk to the user, collect decisions and approvals, read
results, resolve. The agent tools do the bookkeeping (`agent_check`,
`agent_start_run`, `agent_stop_run`, `agent_compile_wave`,
`agent_record_decision`, `agent_start_wave`, `agent_finish_wave`,
`agent_set_params`, `agent_resolve`, `agent_record_critique`,
`agent_merge`); never hand-write a workflow script or edit a compiled one.
Children never decide scope, approve their own side effects or edit the plan.

Each execution is a run, `runs/<id>/`; at most one is active. The scripts
commit; never run git commands that change the repo (read-only `log`, `diff`,
`show` are fine).

Some parts are in their own files, read only when this protocol sends you
there (not before): **Resolving** is `<skill>/references/resolving.md`;
**Reviewing a branch** and **Changing the plan** are
`<skill>/references/changing.md`; what compile's preflight asks for is
`run-preflight.md`; a failed wave or tool is `run-failures.md`. Before you ask
the user anything, read `asking.md` (once per session): every question with a
fixed set of answers goes through the question tool, in the user's terms.

Params change only through `agent_set_params`, never in `.env`; run state only
through the agent tools.

## Every start

1. `agent_check`. Read `state` first: where the agent stands, its `hint` and
   the `moves` the scripts accept. Every agent tool's result carries the same
   as `next`: when unsure what comes next, pick from `next.moves`; anything
   else is refused. Then its `summary`.
2. With an active run, `state.last_launch` says how its latest wave went
   (no need to read `run.yaml`). `run_wave`: check that launch with
   `subagent({ action: "status", id: "<subagent_run_id>" })` and, if it
   finished, go to **Running** step 4; never relaunch a running wave.
   `run_ready` with `last_launch.status` `failed`, `blocked` or `stopped`:
   handle it as a failed wave (`run-failures.md`) first, and
   compile only once the user chose to relaunch.
3. Route:
   - no active run and a `run/…`, `revise/…` or `improve/…` branch checked out
     (`git branch --show-current`): a run always starts from `main`, and
     `agent_start_run` keeps that branch for later. Ask (question tool)
     whether to review it now or start the run and review it later; review
     → **Reviewing a branch**, run → **Starting a run**
   - `draft` or `needs_answers` with no run → planning: follow
     `<skill>/SKILL.md` on `main` (skip step 1; the plan file exists; if
     `request` is empty, ask for it and write it verbatim); on a change
     branch → **Changing the plan**
   - `other_branch`: tell the user, in its `hint`'s words, that a branch the
     agent didn't make is checked out and that they switch back; stop
   - error "the next wave to run … has provisional stages" → **Resolving**
   - `ready`, no active run → **Starting a run**; active run → **Running**
   - any other error → report it and stop
4. `/agent-status`: report plan status, active run, last and next wave,
   open questions, missing params, pending approvals, unmerged branches;
   stop.

## Starting a run

- No earlier runs: `agent_start_run` with the user's note, if any.
- Earlier runs, all finished: `agent_start_run` without asking; in one line,
  say when the last ran (`summary.last_run`). Don't ask whether an input
  changed: the compile compares contents and reuses only what is unchanged.
  Only a value the user's note changes is set (`agent_set_params`).
- Start over while a run is active: say what it has done; only after the
  user agrees, `agent_stop_run`, review its branch if any, then start.

## Running

### 1. Compile

`agent_compile_wave`. If `ok`, go to 2. Otherwise its `preflight` says what
must happen first: read `<skill>/references/run-preflight.md` and handle it
there (missing values or secrets, decisions and approvals, a review, the
budget, an unrecorded launch), then compile again.

### 2. Launch

`launched: false` (every stage of the wave reused from an earlier run): the
wave is already recorded, with nothing launched. Its result is what
`agent_finish_wave` returns: tell the user in one line that it was reused,
and go on at step 4's outcome, without launching or logging.

Otherwise pass the compiled `call` to the subagent tool exactly as given
(`workflow` path included; do not open or paste the script). If the wave
is wrong, fix the plan and recompile. `/agent-run` activates the subagent
tool; enable it only if missing. No `subagent list` first.

Then `agent_start_wave` with the wave, the returned run id and mission id,
and the approvals and decisions. Tell the user what the wave does and roughly
how many children it launches (a stage compile's `notes` say was reused from
an earlier run takes that run's outputs: say so in one line, it launches
nothing), and end the turn; Pi wakes the session.

### 3. While it runs

Answer `contact_supervisor` requests with `subagent_supervisor`; send the
user's decisions to the user. Launch nothing else for this wave. A notice
that one child completed while the workflow is still running needs nothing:
end the turn without a word or a tool call (no `bg_wait`). The workflow's
own completion notice wakes you for step 4.

### 4. When the wave finishes

`agent_finish_wave` with just the wave. It reads the outcome and cost from
pi-subagents, commits files the wave wrote into the agent, and returns
`wave_status`, a children summary, `ends_with`, `run_status` and `next_wave`.
Do not look the run up yourself; only if it says pi-subagents has no record,
pass `status` and `result` from the completion notice.

- `done` — gates and reviews have checked the outputs: report from the
  summary. Read an output only when the user must see it (`review`) or a
  `done_when` has no gate or review and the result's `outputs` (`items`) or
  reuse don't settle it, and then only counts or a few lines.
  Record external outcomes (drafts, pages) with `mission.update` receipts.
  Then by `ends_with`: `review` — summarise with paths, wait for the user's
  go (the next compile asks for it: `review_needed`, then `reviewed: true`); `resolve` — **Resolving**, then continue; `approval` — report what the
  side effects did; `none` — continue. `run_status: done` → **Finish**.
  To continue: if `next_wave.preflight` lists something, handle it first as
  `run-preflight.md` says (ask decisions and approvals in one question-tool call), then
  `agent_compile_wave` with `approve`; otherwise go straight to
  `agent_compile_wave`. Skip `agent_check` and re-reading `run.yaml` between
  waves of one session.
- `failed` / `blocked` — read `<skill>/references/run-failures.md`.

Then compile `next_wave` as above, or after a failure or a new session start
again at **Every start**.

## Finish

On `run_status: done`: the result's `report` is the final report, settled by
code: its `deliverables` (path, items, how each was checked) and `criteria`.
Do what `report.do` says, nothing more. Pass `mission_close` to the subagent tool unchanged (none when
nothing was launched: skip it). If it returns a `review`, go on to
**Reviewing a branch**.
