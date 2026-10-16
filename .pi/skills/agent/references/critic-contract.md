# Running the plan critic

`plan-critic` (in `.pi/agents/`, which carries its checklist) is a
fresh-context, read-only reviewer: it sees the plan file, not your reasoning.
It is required once the plan validates, before asking the user anything, if
the plan has a side_effect or judgment stage or more than 3 stages. If
`plan-critic` is missing, stop and tell the user to run
`/agent-refresh <slug>` in prometheus.

## Launch

The launch script is fixed in `<skill>/references/critic.js` (it holds the output schema); pass
only the task. Do not write a script block, list agents first (`subagent list`) or read `plan-critic.md`:

```js
subagent({ async: true, mission: false, workflow: ".pi/skills/agent/references/critic.js",
  args: { task: "Critique the plan at <absolute path of the agent's plan.yaml>. Read that file and the files in .plan/ beside it (unknowns, questions, the subagents seen; not critique.yaml, an earlier review) and, if needed, cheap read-only looks at the inputs it names. Return only the structured report." } })
```

At resolve or on a change branch, add to the task which stages changed. Tell
the user in one line that the critic is reviewing, and end the turn; Pi wakes
the session when it finishes.
If the critic fails (for example with no structured output), relaunch it
once; if that fails too, tell the user and stop.

## Folding findings in

Give every finding a disposition in `critique.findings` (through
`agent_record_critique`, in prometheus with the agent's `slug` too, with `run_id` the plan-critic child's run
id from the result, once it completed: the tool checks it in pi-subagents'
records, and ties the record to the plan as it is then, so edit first, record
last):

- `accepted` — change the plan; `note` says what changed.
- `rejected` — `note` says why; tell the user about a rejected `blocking` one.
- `asked` — a question with `from_finding: <id>`, as are the critic's
  `questions_for_user` (the three-per-round limit still applies).

If accepted findings changed the stage graph, resume the critic once:
a second launch of the same kind with `args: { task: "The plan changed: <summary>. Check only the changes.", resume: "<run_id>" }`
(`critic.js` passes `resume` through and drops `agent` and the schema then), and set `passes: 2`. A new revision gets a fresh critic, `passes: 1`, except
during a run: resolving gets two passes in all, even when fixing findings
bumps the revision.
