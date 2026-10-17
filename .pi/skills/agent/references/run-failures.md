# Failures

Part of the run protocol (`<skill>/references/run-protocol.md`); read when it sends you here.

## A failed or blocked wave

Its stages go back to pending. Report what failed (`stopped_at`, gate or review
findings, or `error` and `failure_kind`), then ask, with the question tool
(`asking.md`): relaunch the wave (at most `limits.max_retries`; children that
completed and are unchanged are not run again, compile's `notes` say which),
change the plan (**Resolving**, `resolving.md`), or stop (`agent_stop_run`,
then **Reviewing a branch**, `changing.md`).

## A failed tool or launch

A failure of the subagent tool, a launch, extension loading or child startup
is infrastructure: report the exact error and run id and stop. Do not run the
stage another way (shell loops, yourself, a hand-written script) unless the
user says so.

"Acceptance rejected: Staged index changed after launch": something changed
the agent repo's staged index during the wave. Tell the user; relaunch
once nothing else touches the repo.
