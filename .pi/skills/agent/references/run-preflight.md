# What compile asks for first

Part of the run protocol (`<skill>/references/run-protocol.md`); read when it sends you here.

When `agent_compile_wave` is not `ok`, handle its `preflight`, then compile again.

- `plan_errors` — fix the plan (**Resolving**, `resolving.md`, if about
  provisional stages), or report and stop.
- `missing_params` — ask each value in plain chat (a value, not a choice):
  what it is for (its `description`); for files and folders an absolute path
  (`~/…` is fine) with an example. Set with `agent_set_params` and
  compile again; it rejects relative or missing input paths. A `per_run`
  param is asked every run and kept for this run only: if the user's note
  already says it, set it from the note instead of asking.
- `missing_secrets` — never ask for or read a secret, and never read or
  print `.env`. Tell the user the variable and its purpose: add
  `NAME=value` to the agent's `.env` (or the environment). When they say
  it is done, compile again.
- `decisions_needed`, `approvals_needed` — **Decisions and approvals** below.
- `review_needed` — the last wave ended with a review: summarise its outputs
  (paths, counts) and ask, with the question tool, whether to go on. On a go,
  compile with `reviewed: true`; otherwise resolve or stop as the user says.
- `budget` — the run spent its cost limit. Tell the user the amount and
  ask (question tool) whether to go on; on a go, ask the new limit and
  compile with `budget: <usd>`; otherwise `agent_stop_run`.
- error "already launched … and not recorded" — a session ended between a
  launch and its record. Do not launch again: `agent_start_wave` with the
  run id it names, then wait for it (or finish it if it has ended).

## Decisions and approvals

Everything happens before launch, in one question-tool call (`asking.md`).

- **Decisions**: show what `show_user` lists (short summaries, not dumps),
  ask, record with `agent_record_decision`.
- **Approvals**: for each side-effect stage, describe exactly what will
  happen (what, where, how many, from which inputs) from its
  `approvals_needed` entry, then ask with options such as `Approve <stage>` /
  `Don't run it`. Only an option naming the stage is an approval.

Then `agent_compile_wave` with `approve: [<keys>]`. If the user declines, the
wave cannot launch: ask whether to resolve without it or stop. Unattended,
never approve: stop and report what waits.
