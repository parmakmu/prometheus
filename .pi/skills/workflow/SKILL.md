---
name: workflow
description: Plan a workflow (workflows/<slug>/) from a request, on /workflow-new only.
disable-model-invocation: true
---

# Planning a workflow

A workflow is a folder with a validated, critic-reviewed plan
(`plan.yaml`) that a Pi session started in it runs with `/workflow-run`
(`references/run-protocol.md`). While planning, the only children you launch
are read-only: `scout` probes and the plan critic.

Read `references/plan-schema.md` and `references/classify.md` before
drafting. Read another reference or an example only at the step that names
it, when that step applies. After the first draft, change the plan with small
edits, never by rewriting the file.

## Protocol

1. **Create the workflow** with `workflow_scaffold` (slug `^[a-z0-9-]+$`,
   the request verbatim). All planning edits `workflows/<slug>/plan.yaml`.
   Its result carries `today`, for `agents.checked`. Inside a workflow
   (`/workflow-run` on a draft plan), skip this step.

2. **Record the agents.** `subagent({ action: "list" })`, then fill `agents`
   (plan-schema "Agents").

3. **List the unknowns**, each with a `resolve_by`, chosen in this order:
   - `planner` — one or two cheap read-only calls you can make now (list a
     folder, read a header). Do it.
   - `probe` — a child can check it cheaply (a folder only a child reaches, a
     web page, a connector; "how many, what shape"). One read-only `scout`
     per question, several in one `runs.all`, and record the `run_id`:
     Write the script as one ```` ```js workflow ```` code block in the reply, then call
     `subagent({ async: true, mission: false, workflow: true })` in that same reply:
     ```js workflow
     return runs.run("probe-u2", { agent: "scout", label: "Probe export folder",
       task: "Read-only. In <location>: count files by extension and read the headers of 3. Return only the JSON.",
       outputSchema: { type: "object", required: ["counts","samples"],
         properties: { counts: { type: "object" }, samples: { type: "array", items: { type: "string" } } } } })
     ```
     A probe checks shape and presence; it never does the work.
   - `user` — a preference, scope, threshold, audience or permission; a
     question (step 6).
   - `discovery` — only doing part of the work reveals it; a discovery
     stage, with the stages that depend on it provisional (step 4).

   Write each `finding`, and observed facts as assumptions with
   `source: observed`. Never read in bulk, never write outside the workflow.
   Having nothing local to look at is normal: most unknowns are `probe`,
   `user` or `discovery`.

4. **Draft the stages** (no waves: they are derived). The first wave is
   concrete and small. A stage whose schema, batch size, agent or gate
   depends on a discovery is **provisional** (`intent`, `resolves_after`, no
   detail): never guess detail to satisfy the validator. Everything outside
   the workflow is a typed param (plan-schema "Params"). `pause_after: true`
   only if the user must see a result before work builds on it and no
   decision or side effect already pauses there. Keep `status: draft` until
   step 8.

5. **Classify every stage** (`references/classify.md`), provisional ones too.
   Use builtin agents where they fit; when a stage needs a narrower tool set,
   shared domain rules or a specific model, write a project agent (read
   `references/agent-profiles.md` then, not before). Provisional stages get agents at replan.

6. **Sort the gaps.** A gap becomes a question only if a wrong guess would:
   - change the stage graph ("extract fixed fields" vs "summarise each email")
   - touch a side effect (whether it happens, per-item or batch approval,
     unless the user said)
   - pick the wrong source or destination, and you cannot observe which
   - change what "done" means (threshold, matching rule, date range)

   Everything else is an assumption, `source: default`; `confirm: true` only
   for what is worth a glance (a column list, a date range), not naming,
   formatting, batch sizes or agent choice. Never ask for param values:
   `/workflow-run` asks for them, and the user puts secrets in `.env`. Ask
   about a param only if its meaning is unclear (which source, not which
   path). Do not ask yet.

7. **Check and critique.** Call `workflow_check` after every plan change and
   fix every error. If the plan has a side_effect or judgment stage or more
   than 3 stages, run the critic (`references/critic-contract.md`).

8. **Ask, or finish.**
   - Open questions → `status: needs_answers`, check, ask (below). Write each
     answer into its question and update the plan by editing the file (the
     replan tools work only during a run). Resume the critic only if the
     stage graph changed and it ran fewer than two passes this revision; an
     answer that leaves the stages as they were needs no critic.
     After two rounds, remaining gaps become assumptions with
     `confirm: true`. Then the next bullet.
   - None open → `status: ready` (never back to `draft`), check, and tell the user, in their terms
     (see Asking): what they will get; what a run does, in order, from `summary.waves`, and
     where it will stop for them (to approve something, or to settle details once it has seen
     their data); what `/workflow-run` will ask for, described ("the folder your emails are
     in", not `mail_dir`); assumptions worth a glance; a rejected blocking finding, as what it
     means for them; and `cd workflows/<slug> && pi`, then `/workflow-run`. No waves, stage
     kinds, provisional, critic or validation talk.

9. **On a change of mind**, bump `revision`, keep answers that still apply,
   clear `critique`, redo steps 3–8.

If the user says "just do it", finish the plan with defaults and tell them to
start it; running is not planning. After the first run, plan changes happen
inside the workflow (`references/replanning.md`, `references/changing.md`).

## Asking

- After the critic, never before a draft. At most three questions per round,
  most consequential first, the critic's included.
- Each: one clause of why, tied to what changes for the user ("decides whether
  the workflow sends mail"), 2–4 concrete options, and your default.
  Bad: "What format should the Excel file be?" (an assumption). Good: "Send
  replies, or only draft them? Sending is irreversible."
- In the user's terms: what they get, what it costs per run, whether it is the
  same every run, what can't be undone. Not the plan's: no stage kinds,
  waves, "deterministic", "judgment", "parser", param names. Not "decides
  whether export is a deterministic parser or a judgment stage" but "the full
  text is copied as is, quickly and the same every time; a summary needs a
  model to read every email on each run, and may paraphrase".
- With `ask_user_question` (or a similar tool): one call per round, the
  default first with "(Recommended)" in its label. Otherwise a numbered list
  answerable as "1b, 2 default"; end the turn. If the user dismisses the
  dialog, ask in chat.
- Unattended: do not ask. Apply defaults as assumptions with `confirm: true`;
  leave `needs_answers` only if an unanswered question blocks a side effect.
  Wait for the critic with `bg_wait({ id })`.

## A good plan

- Deterministic work is a `script` stage: a program (`script:`), its
  arguments (`run:`, the program's interface, decided now) and a gate.
  A messy tail goes to a leftovers file and a small judgment stage.
- Judgment work over many items is a `map` / `map_reduce` with a schema.
- Anything a later wave reads is a file (`out` with `path`).
- A pause in the wrong place is fixed with a need, a kind or `pause_after`.
- Only if the schema leaves a field's use unclear, read one example:
  `examples/invoice-check.yaml` (concrete from the start) or
  `examples/competitor-memo.yaml` (discovery, then replan).
