// The plan-critic launch: pass the task as `args.task`; the output schema is fixed here so the
// model never retypes it; `args.resume` (a critic's run_id) is the second pass (see critic-contract.md).
if (args.resume) return runs.run("plan-critic-2", { resume: args.resume, label: "Critique plan, pass 2", task: args.task });
return runs.run("plan-critic", {
  agent: "plan-critic", label: "Critique plan",
  task: args.task,
  outputSchema: { type: "object", required: ["verdict", "goal", "findings", "questions_for_user"], properties: {
    verdict: { enum: ["pass", "revise", "ask_user"] },
    goal: { type: "object", required: ["matches_request", "notes"], properties: { matches_request: { type: "boolean" }, notes: { type: "string" } } },
    findings: { type: "array", maxItems: 8, items: { type: "object", required: ["id", "target", "category", "severity", "claim", "fix"], properties: {
      id: { type: "string" }, target: { type: "string" }, category: { type: "string" },
      severity: { enum: ["blocking", "major", "minor"] }, claim: { type: "string" }, evidence: { type: "string" }, fix: { type: "string" } } } },
    questions_for_user: { type: "array", maxItems: 3, items: { type: "string" } } } }
})
