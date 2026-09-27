# Writing project agents

A workflow carries its own agents in `.pi/agents/` (pi-subagents calls them
project agents; in the plan, `source: project`). Write one when no available
agent fits a stage; otherwise use the builtins (`scout`, `researcher`,
`evidence-auditor`, `worker`, `reviewer`, `oracle`).

## When a stage deserves its own agent

- it needs a **restricted tool set** a builtin does not have (read-only
  access for a judgment stage that must not touch files; no shell)
- it needs a **stable stance or domain rules** every batch child must share
  (how to recognise a supplier invoice, a style guide for a memo)
- it needs a **specific model or thinking level** (a cheap model for a
  1,000-item map, a strong one for a reviewer)
- several stages or batches reuse the same instructions, and repeating them
  in every task text would drift

Do not create an agent just to rename a builtin, or to hold task-specific
details (paths, batch files, counts): those belong in the task text the run
protocol writes.

## File

`<workflow>/.pi/agents/<name>.md`; name it `<slug>-<role>` so agents from
different workflows never collide (`invoice-check-classifier`).

```markdown
---
name: invoice-check-classifier
description: Read-only classifier that decides whether an email carries a supplier invoice
tools: read, grep, find, ls
model: provider/model-id          # optional; omit to use the default
thinking: medium                  # optional
systemPromptMode: replace
inheritProjectContext: false
inheritSkills: false
defaultContext: fresh
acceptanceRole: read-only         # omit for agents that write outputs
---

You classify emails for a supplier-invoice workflow.

An email carries a supplier invoice when … (the rule, with the edge cases
the planner found: forwarded invoices, inline invoices, credit notes).

Work only on the files listed in your task. Do not edit anything, run
mutating commands, contact other agents or spawn subagents. Return only the
structured result your task asks for.
```

Rules of thumb:

- **Tools**: the fewest that do the job. Judgment and review agents get
  `read, grep, find, ls`. Script-writing workers need `bash` and `write`.
  Side-effect agents get only the tool for their one action (for example a
  single connector) plus read tools.
- **Context**: `fresh` unless the stage truly needs the parent's history
  (`fork`), which is rare in a workflow.
- **Body**: the stable rules and stance only. End with the boundaries
  (what it must not do) and "return only the result your task asks for".
- **External runners**: set `runner` only for agents that call an external
  CLI or job provider, and remember they lose structured output, fork,
  gates and resume.

## In the plan

Add each one to `agents.available` with `source: project` (and `context` if
not `fresh`). The validator checks that a file in `.pi/agents/` defines it.
Treat new agents like stages: the critic sees them through the plan, so give
them a one-line `description` that says what they may and may not do.
