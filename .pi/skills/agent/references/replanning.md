# Replanning

Part of the run protocol (`<skill>/references/run-protocol.md`); read when it sends you here.

After a wave that `ends_with: replan`, or when the user wants pending work
changed. The plan changes only through `agent_replan` and
`agent_record_critique`, committed on `run/<id>`. Stages done or running
are history; request, goal, done_when, params, inputs, outputs and subagents (the plan's `agents:`) are
locked (they change with `/agent-revise` after the run). A side effect
stays a side effect.

Read the discovery outputs (or the user's request), `plan.yaml`, and
`<skill>/references/plan-schema.md` ("Stages", "Waves"); `classify.md` only
if a kind may change.

1. Write gates for the stages you make concrete into `gates/` (a package
   they import: `agent_add_package`, which asks the user), and new agents into `.pi/agents/`
   (`<skill>/references/agent-profiles.md`).
2. One `agent_replan` call: `findings` for the resolved unknowns, and
   each provisional stage whose `resolves_after` is done, in full and
   without `provisional` / `resolves_after` (`pattern`, `agent`, `writes`,
   `map` for maps, `gate` / `review` / `done_when`); for a user's change, the changed
   stages and a `note`. It bumps the revision, sets `draft` and commits; fix
   what its validation reports with another call.
3. Run the critic on the changed stages (`<skill>/references/critic-contract.md`)
   and record it with `agent_record_critique`, which marks the plan ready
   when it validates. At most two critic passes per replan, however many
   revisions it takes (`agent_record_critique` refuses a third): after
   the second, reject what is left with a note (tell the user about a
   blocking one) and continue.
4. If the stage graph changed beyond filled-in details, or a side effect or
   question appeared, show the user and ask before continuing.
