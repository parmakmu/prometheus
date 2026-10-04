# Writing subagents

Write an agent into the agent's `.pi/agents/<slug>-<role>.md` only when no
builtin (`scout`, `researcher`, `evidence-auditor`, `worker`, `reviewer`,
`oracle`) fits because the stage needs:

- a restricted tool set (read-only judgment, no shell)
- stable domain rules every batch child shares (how to recognise a supplier
  invoice)
- a specific model or thinking level (cheap for a 1,000-item map)
- the same instructions across several stages

Not to rename a builtin, and never with task details (paths, counts): those
go in the task text.

```markdown
---
name: invoice-check-classifier
description: Read-only classifier that decides whether an email carries a supplier invoice
tools: read, grep, find, ls
model: provider/model-id          # optional
thinking: medium                  # optional
systemPromptMode: replace
inheritProjectContext: false
inheritSkills: false
defaultContext: fresh
acceptanceRole: read-only         # omit for agents that write outputs
---

You classify emails for a supplier-invoice agent.

An email carries a supplier invoice when … (the rule and the edge cases found
while planning).

Work only on the files listed in your task. Do not edit anything, run
mutating commands, contact other agents or spawn subagents. Return only the
structured result your task asks for.
```

- **Tools**: the fewest. Judgment and review: `read, grep, find, ls`. Script
  writers: add `bash, write`. Side effects: the one tool for the action plus
  read tools.
- **Context**: `fresh`; `fork` only if the stage needs the parent's history.
- **Body**: stable rules only, ending with what it must not do.
- **`runner`**: only for external CLIs, which lose structured output, fork,
  gates and resume.

List it in `agents.available` with `source: project` (and `context` if not
`fresh`); the `description` is what the critic sees.
