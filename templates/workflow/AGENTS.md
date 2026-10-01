# Workflow: {{slug}}

This folder is a self-contained, portable workflow. Everything it needs to
plan and run lives here:

- `plan.yaml` — the plan (goal, stages, approvals; waves are derived)
- `.env` — param values for this machine (paths, accounts, secrets); not in git,
  and the model may not read it. `/workflow-run` asks for missing values and
  tells you which variables to add for secrets
- `.pi/agents/` — the agents the plan uses, including `plan-critic` and
  `workflow-batcher` (a command runner that splits and merges map batches)
- `.pi/skills/workflow/` — planning rules, validator, wave compiler,
  run log and run protocol
- `gates/` — gate scripts that verify stage outputs
- `scripts/` — stage programs (a stage's `script:`): the first run writes one,
  you review it with the run's changes, later runs reuse it
- `.git` — this folder is its own git repo: the workflow scripts commit the
  plan, gates, stage programs and agents as they change; `git log` shows the
  revisions (tags `r1`, `r2`, …). Runs and `.env` are not tracked.
- `runs/<id>/` — one folder per run of the workflow: `run.yaml` (what has run),
  `work/` (files the stages write; relative `out` paths land here), outputs
  for relative param values, and compiled waves. `/workflow-run` continues
  the active run or starts a new one.

## How to use it

| command | what it does |
|---|---|
| `/workflow-run [note]` | plan, continue or run from where the workflow stands |
| `/workflow-status` | report where it stands; changes nothing |
| `/workflow-revise [change]` | change the plan on a branch you review and merge (no run active) |

When working in this folder, follow
`.pi/skills/workflow/references/run-protocol.md`. Do not run stages
outside that protocol, do not edit `plan.yaml` except as the protocol
says, and never perform a stage marked `approval: required` without the
user's explicit approval for that stage.

## Requirements on a new machine

- Pi with `pi-subagents` installed globally (`pi install npm:pi-subagents`),
  and project trust granted for this folder (the first start asks), so the
  workflow extension loads
- Python with the skill scripts' packages (typer, pydantic, pydantic-settings, ruamel.yaml): run `uv sync` here (creates `.venv`,
  which the workflow commands use)
- git (the workflow is a repo, and pi-subagents checks each writer child's
  staged index with it)
- param values for this machine in `.env` (or the environment pi runs in)
