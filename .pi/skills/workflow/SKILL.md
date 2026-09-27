---
name: workflow
description: Turn a user's natural-language request for multi-step work into a portable workflow (workflows/<slug>/) holding a validated, critic-reviewed YAML plan that a Pi session started in that folder can run with pi-subagents. Use when the user asks for work that spans several steps, files, apps or agents, or asks to "plan", "break down", "turn this into a workflow" or "make a workflow". Plans in stages when details can only be discovered by running, and asks only the questions that would change the plan.
disable-model-invocation: true
---

# Request to Plan

Turn a request into a workflow: a folder you can copy anywhere, start
`pi` in, and run with `/workflow-run`. This skill plans; the workflow's run protocol
(`references/run-protocol.md`) runs. While planning, the only children this
skill launches are read-only: probes and the plan critic.

Where this skill shows a script command, `<python>` is the Python the
workflow tools use: the nearest `.venv/bin/python` (`uv sync` creates it). If
its packages are missing, ask the user to run `uv sync`; never install
packages yourself.

Before writing, read:

- `references/plan-schema.md` — every field and the rules the validator enforces
- `references/classify.md` — the four-question test and the split rule
- `references/clarify-rules.md` — unknowns: who resolves each one, and how to ask
- `references/critic-contract.md` — when and how to run the plan critic
- `references/agent-profiles.md` — when and how to write the workflow's own agents

## Protocol

1. **Create the workflow.** Pick a short slug (`^[a-z0-9-]+$`) and call the
   `workflow_scaffold` tool (in Prometheus), or run
   `<python> <skill-dir>/scripts/scaffold_workflow.py <slug> --request "<verbatim request>"`.
   It creates `workflows/<slug>/` (the user may name another root with
   `--root`) with the plan skeleton at `workflows/<slug>/.pi/plan.yaml`, the
   critic agent, a copy of this skill, the `/workflow-*` commands and
   `AGENTS.md`. All planning below edits that plan file. Its result carries
   `today`, the date for `agents.checked`. If you are already inside a
   workflow (started by `/workflow-run`), skip this step.

2. **Record the agents.** Run `subagent({ action: "list" })` and fill the
   plan's `agents` block: every name, `runner` for external runners,
   `context` for custom agents with a `defaultContext`, and `source`
   (`builtin`, `package`, `workflow`, `user`). A plan may only use `user`
   agents after copying their file into the workflow's `.pi/agents/` and
   switching them to `source: project`.

3. **List the unknowns.** Write down what the plan depends on but you do not
   know, and give each a `resolve_by` (see `references/clarify-rules.md`):
   - `planner` — a cheap check you can do yourself right now
   - `probe` — a read-only `scout` child you send to look (one small
     async `workflowScript`, `mission: false`); record its `run_id`
   - `user` — only the user can say; becomes a question
   - `discovery` — only running part of the work reveals it; becomes a
     discovery stage, and the stages that depend on it become provisional
     (step 4), so the run replans after it

   Resolve `planner` and `probe` unknowns now and write the `finding`. Being
   an orchestrator with nothing local to look at is normal: most unknowns
   will be `probe`, `user` or `discovery`. Never invent detail to fill a gap.

4. **Draft the plan.** Write stages only; waves follow from them (see
   "Waves" in `references/plan-schema.md`). Make the stages that run first
   fully concrete. Everything that depends on a `discovery` unknown becomes a
   **provisional** stage: `kind`, `title`, `needs`, an `intent` (what it must
   produce) and `resolves_after: <discovery stage>` — no agent, schema,
   batch or gate yet. Give a stage `pause_after: true` only if the user must
   see its result before work builds on it and no decision or side effect
   already pauses there. Everything outside the workflow becomes a param
   (`${name}`) with a `type`: `path` for files and folders, `text` for other
   values (a table, a URL, an id), `secret` for keys and tokens
   (`references/plan-schema.md`, "Params"). Each run of the workflow gets its own folder, `runs/<id>/`:
   relative `out.path` values land in its `work/`, and relative param values
   (an `outputs` default) resolve inside it. Every guess goes into
   `assumptions` or into `questions` with a `default`. Leave `status: draft`
   until step 9; the validator rejects `needs_answers` and `ready` before the
   critique.

5. **Classify every stage** with `references/classify.md`: the four
   questions in order, a one-line `kind_reason`, and the split rule.
   Provisional stages get a kind too; side effects stay side effects even
   in outline.

   Then **pick or write the agents** for concrete stages. Use a builtin when
   it fits. When a stage needs a narrower tool set, shared domain rules, or a
   specific model, write a project agent into the workflow's `.pi/agents/`
   following `references/agent-profiles.md`, and list it in `agents` with
   `source: project`. Agents for provisional stages are written at replan.

6. **Sort the gaps** with `references/clarify-rules.md`. A gap becomes a
   question only if a wrong guess would change the stage graph, touch a side
   effect, pick the wrong source or destination, or change what "done" means.
   Do not ask yet.

7. **Validate and compile-check.** Call the `workflow_check` tool, which runs
   both checks. Without the tool:
   `<python> <skill-dir>/scripts/validate_plan.py workflows/<slug>/.pi/plan.yaml`
   (it detects the workflow and checks portability too), then
   `<python> <skill-dir>/scripts/compile_wave.py --workflow workflows/<slug> --check`.
   Fix every error either reports.

8. **Critique.** If the plan has a side_effect stage, a judgment stage, or
   more than 3 stages, run the plan critic as in
   `references/critic-contract.md` and end the turn with one line telling the
   user the critic is reviewing the draft. When it returns, record every
   finding in `critique` with a disposition, change the plan for accepted
   ones, turn asked ones into questions with `from_finding`, re-validate.

9. **Ask, or finish.** This is the one point where planning talks to the
   user, after the critic, so the questions include what the critic raised.
   - Open questions → `status: needs_answers`, validate, ask (see "Asking").
     With a question tool the answers come back in this turn: go on to
     step 10. In plain chat, end the turn.
   - Never ask for param values (folders, files, page ids, and never a
     secret): they belong to the machine that runs the workflow, and
     `/workflow-run` asks for them (secrets: the user adds them to `.env`).
     Ask about a param only if its *meaning* is unclear (which source, not
     which path).
   - No open questions → `status: ready`, validate, and show the user: the
     goal, the waves in order from the check's `summary.waves` (with where
     the run replans or pauses), the
     approvals, the params `/workflow-run` will ask for, assumptions worth a glance, any
     rejected blocking critic finding, and how to run it:
     `cd workflows/<slug> && pi`, then `/workflow-run`. Give paths; do not
     paste the YAML.

10. **On answers.** Write each answer into its question, update the plan,
    re-classify changed stages, re-validate. Resume the critic only if the
    stage graph changed and it ran fewer than two passes this revision.
    Two question rounds maximum; after that, remaining gaps become
    assumptions with `confirm: true`.

11. **On a change of mind.** Bump `revision`, keep answers that still apply,
    clear `critique`, redo steps 3–9.

Replanning after a discovery wave is part of the run protocol
(`references/run-protocol.md`, "Replanning"): during a run the plan changes
only through the workflow's plan tools, on the run's branch. Once a workflow
has run, a change to its plan is `/workflow-revise` inside the workflow
(a change branch the user reviews and merges).

## Asking

- At most three questions per round, most consequential first, counting
  questions that came from the critic.
- Each question states why it matters in one clause and offers 2–4 concrete
  options plus the default you would use.
- If a structured question tool is available (`ask_user_question`, or a
  similar `questionnaire` / `ask_user` tool from an extension), use it: all
  of a round's questions in one call, the default as the first option with
  "(Recommended)" appended to its label, the one-clause reason in the
  question text. Otherwise ask in plain chat as a numbered list the user can
  answer as "1b, 2 default, 3: …". If the user dismisses the dialog, ask in
  chat instead.
- The tool does not change when to ask: draft first and ask only what
  `references/clarify-rules.md` allows, even if the tool's own guidance
  invites earlier questions.
- Unattended runs: do not ask. Apply defaults, record them as assumptions
  with `confirm: true`, and leave `status: needs_answers` only if an
  unanswered question blocks a side effect. Wait for the critic with
  `bg_wait({ id })` instead of ending the turn.

## What a good plan looks like

- The first wave is concrete and small; later waves are provisional where
  the details really depend on what the first wave finds.
- Every stage's `kind` follows the four-question test, with a `kind_reason`.
- Deterministic work is a `script` stage with a `verify` gate and a named
  program (`script: <file>`, kept in the workflow's `scripts/`: the first run
  writes it, later runs reuse it); messy tails go to a leftovers file and a
  small judgment stage.
- Judgment work is batched (`map` / `map_reduce`) with an output schema and
  an `items` source (a folder input or a file an earlier stage writes).
- Anything a later wave reads is a file (`out` with `path`), never a returned
  result.
- The plan compiles: before marking it ready, run
  `<python> <skill-dir>/scripts/compile_wave.py --workflow workflows/<slug> --check`.
  It compiles every concrete pending wave without needing params,
  decisions or approvals, and must report `"ok": true`.
- A typed gate goes with a file output; a returned schema goes with a child
  whose answer is the result. Not both.
- Follow-ups `resumes` the earlier stage, with no `agent` and no gate.
- No `waves` section: the compiler derives waves from `needs`, kinds,
  `resolves_after` and `pause_after`. Check them in `summary.waves`; if a
  pause is missing, the fix is a need, a kind or `pause_after`, never a wave.
- Every side effect has `approval: required`.
- Nothing in the plan points outside the workflow except through `params`.

Examples: `examples/invoice-check.yaml` (concrete from the start) and
`examples/competitor-memo.yaml` (discovery first, then replan).

## Out of scope

Running waves belongs to the run protocol, started with `/workflow-run` inside the
workflow. If the user asks to "just do it", finish the plan (with defaults if
needed) and tell them to start it.
