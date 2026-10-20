# Failures

Part of the run protocol (`<skill>/references/run-protocol.md`); read when it sends you here.

## A failed or blocked wave

Its stages go back to pending. Report what failed (`stopped_at`, gate or review
findings, or `error` and `failure_kind`), then ask, with the question tool
(`asking.md`): relaunch the wave (at most `limits.max_retries`; children that
completed and are unchanged are not run again, compile's `notes` say which),
change the plan (**Resolving**, `resolving.md`), or stop (`agent_stop_run`,
then **Reviewing a branch**, `changing.md`).

## A stage needs what the agent lacks

The result's `missing` lists what a child needed and wasn't given (`stage`,
`kind`: package, connector or skill, `need`, `why`); the wave failed. Children
can't ask, so this is yours:

1. Find it: `agent_find_capability` with that `kind` (a package by its name).
   Pick the best fit; a conversion is a package, not a skill.
2. Propose it with `agent_add_package`, `agent_add_connector` or
   `agent_add_skill`; each asks the user. It lands on the run's branch.
3. Give the stage it. A package needs nothing more. A connector or skill goes
   in the stage's subagent, if it is the agent's own (`.pi/agents/`: `tools:
   mcp:<server>/<tool>`, `skills: <name>`). A stage on a builtin needs a subagent
   of its own, and the plan's agents are locked during a run: ask the user
   whether to stop the run (`agent_stop_run`) and make the change with `/agent-revise`.
4. Relaunch the wave (compile it again).

If the user declines, or nothing fits, ask (question tool) whether to change
the plan (**Resolving**) or stop. Never install it another way, or do the
stage yourself.

## A failed tool or launch

A failure of the subagent tool, a launch, extension loading or child startup
is infrastructure: report the exact error and run id and stop. Do not run the
stage another way (shell loops, yourself, a hand-written script) unless the
user says so.

"Acceptance rejected: Staged index changed after launch": something changed
the agent repo's staged index during the wave. Tell the user; relaunch
once nothing else touches the repo.
