# Plan critic contract

The critic is a fresh-context, read-only reviewer of a drafted plan. It sees
the plan file (which carries the user's request verbatim) and nothing of the
planner's reasoning. It advises; the planner decides and records every
finding's disposition in the plan's `critique` block.

## When to run it

Run the critic once the plan validates, before asking the user anything, when
the plan has any of:

- a `side_effect` stage
- a `judgment` stage
- more than 3 stages

Otherwise skip it; the validator does not require it.

## Which agent

Use `plan-critic` if `subagent({ action: "list" })` shows it. Prometheus
and every workflow ship it in their `.pi/agents/`, so it is normally
there. If it is missing, use `reviewer` and paste the "Critic brief" below
into the task. Record the fallback as `agent: reviewer` in `critique`.

At replan, run a fresh critic for the new revision (`critique.revision` =
the plan's new `revision`, `passes` back to 1) and tell it which stages
changed.

## Launch

```js
subagent({
  async: true,
  mission: false,
  workflowScript: `return runs.run("plan-critic", {
    agent: "plan-critic",
    label: "Critique plan",
    task: "Critique the plan at <absolute path to plan>. Read only that file and, if needed, cheap read-only looks at the inputs it names. Return only the structured report.",
    outputSchema: CRITIC_SCHEMA
  })`
})
```

Tell the user the critic is reviewing the draft and end the turn; Pi wakes the
session when it finishes. Record the returned `runId` in `critique.run_id`.

## Output schema

```js
const CRITIC_SCHEMA = {
  type: "object",
  required: ["verdict", "goal", "findings", "questions_for_user"],
  properties: {
    verdict: { enum: ["pass", "revise", "ask_user"] },
    goal: {
      type: "object",
      required: ["matches_request", "notes"],
      properties: {
        matches_request: { type: "boolean" },
        notes: { type: "string" }
      }
    },
    findings: {
      type: "array",
      maxItems: 8,
      items: {
        type: "object",
        required: ["id", "target", "category", "severity", "claim", "fix"],
        properties: {
          id: { type: "string" },
          target: { type: "string" },
          category: { enum: [
            "goal_drift", "done_when", "misclassified", "should_split",
            "missing_stage", "hidden_side_effect", "wave_boundary",
            "invented_detail", "unknown_misrouted", "not_portable", "agent_scope",
            "missing_question", "unnecessary_question", "other"
          ] },
          severity: { enum: ["blocking", "major", "minor"] },
          claim: { type: "string" },
          evidence: { type: "string" },
          fix: { type: "string" }
        }
      }
    },
    questions_for_user: { type: "array", maxItems: 3, items: { type: "string" } }
  }
};
```

`target` is a stage key, a field such as `goal` or `done_when`, or `plan`.

## Critic brief

Paste this into the task when falling back to `reviewer`; `plan-critic` has it
in its profile.

> You review an execution plan before anything runs. Check, in order:
> 1. **Goal fidelity** — does `goal` say what `request` asks, no more and no
>    less? Flag scope added or dropped.
> 2. **done_when** — observable, checkable, and enough to prove the goal?
> 3. **Classification** — apply the four questions in order to each stage:
>    changes anything outside the run → side_effect; the user's choice →
>    decision; a program could decide correctness → deterministic; otherwise
>    judgment. Flag mechanical work sent to a model and judgment disguised as
>    a script.
> 4. **Split** — flag deterministic stages with a plausible messy tail and no
>    leftovers path, and judgment stages that a cheap filter could shrink.
> 5. **Hidden side effects** — writes, overwrites or messages not marked as
>    such, including in provisional stages.
> 6. **Invented detail** — concrete schemas, batch sizes, sources or counts
>    that nothing in the plan (findings, observed assumptions, answers)
>    supports. They should be provisional, resolved after a discovery stage.
> 7. **Unknowns** — each routed to the cheapest resolver that can really
>    answer it (planner, probe, user, discovery)? Flag user questions a probe
>    could answer, probes that are really the work, and discovery stages that
>    do not produce what the provisional stages need.
> 8. **Portability and agents** — paths outside the workflow not going through
>    `params`, agents that only exist on this machine, and project agent
>    profiles (`.pi/agents/`) with tools broader than their stage needs.
> 9. **Questions** — gaps that would change the stage graph, a side effect,
>    a source or destination, or what "done" means, and that no question
>    covers; and questions that do not meet that bar.
> Do not restyle the plan, rename things, or suggest agents or batch sizes.
> At most 8 findings, most severe first. `blocking` means the plan would
> produce the wrong result or an unapproved side effect. Stay read-only.

## Folding findings in

For each finding, decide and record a `disposition` in `critique.findings`:

- `accepted` — change the plan; say what changed in `note`.
- `rejected` — keep the plan; say why in `note`. Allowed for any severity,
  but a rejected `blocking` finding should be mentioned to the user.
- `asked` — turn it into a question with `from_finding: <id>`. Merge the
  critic's `questions_for_user` the same way; the 3-per-round limit still
  applies.

If accepted findings changed the stage graph (stages added, removed or
reclassified), resume the critic once with the changes:
`runs.run("plan-critic-2", { resume: <run_id>, task: "The plan changed: <summary>. Check only the changes." })`.
Two passes maximum per revision. Record `passes`.
