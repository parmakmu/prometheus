# Running the plan critic

`plan-critic` (in `.pi/agents/`, which carries its checklist) is a
fresh-context, read-only reviewer: it sees the plan file, not your reasoning.
It is required once the plan validates, before asking the user anything, if
the plan has a side_effect or judgment stage or more than 3 stages. If
`plan-critic` is missing, stop and tell the user to run
`/workflow-refresh <slug>` in Prometheus.

## Launch

```js
subagent({ async: true, mission: false, workflowScript: `return runs.run("plan-critic", {
  agent: "plan-critic", label: "Critique plan",
  task: "Critique the plan at <absolute plan path>. Read only that file and, if needed, cheap read-only looks at the inputs it names. Return only the structured report.",
  outputSchema: { type: "object", required: ["verdict", "goal", "findings", "questions_for_user"], properties: {
    verdict: { enum: ["pass", "revise", "ask_user"] },
    goal: { type: "object", required: ["matches_request", "notes"], properties: { matches_request: { type: "boolean" }, notes: { type: "string" } } },
    findings: { type: "array", maxItems: 8, items: { type: "object", required: ["id", "target", "category", "severity", "claim", "fix"], properties: {
      id: { type: "string" }, target: { type: "string" }, category: { type: "string" },
      severity: { enum: ["blocking", "major", "minor"] }, claim: { type: "string" }, evidence: { type: "string" }, fix: { type: "string" } } } },
    questions_for_user: { type: "array", maxItems: 3, items: { type: "string" } } } }
})` })
```

At replan or on a change branch, add to the task which stages changed. Tell
the user in one line that the critic is reviewing, and end the turn; Pi wakes
the session when it finishes.
If the critic fails (for example with no structured output), relaunch it
once; if that fails too, tell the user and stop.

## Folding findings in

Give every finding a disposition in `critique.findings` (in a workflow,
through `workflow_record_critique`):

- `accepted` — change the plan; `note` says what changed.
- `rejected` — `note` says why; tell the user about a rejected `blocking` one.
- `asked` — a question with `from_finding: <id>`, as are the critic's
  `questions_for_user` (the three-per-round limit still applies).

If accepted findings changed the stage graph, resume the critic once:
`runs.run("plan-critic-2", { resume: "<run_id>", task: "The plan changed: <summary>. Check only the changes." })`,
and set `passes: 2`. A new revision gets a fresh critic, `passes: 1`, except
during a run: a replan gets two passes in all, even when fixing findings
bumps the revision.
