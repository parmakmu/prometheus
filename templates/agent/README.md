# Agent: {{slug}}

A self-contained agent built by prometheus. Start `pi` in this folder (trust it when asked) and run it from there.

| command | what it does |
|---|---|
| `/agent-run [note]` | start a run, or continue the active one |
| `/agent-auto` | run the next waves without the model, until one needs you (`pi -p "/agent-auto"` runs it unattended) |
| `/agent-status` | where it stands: what comes next, what waits for you |
| `/agent-history [n]` | the last runs, and how each stage went |
| `/agent-prune [keep]` | delete old runs' work folders (keeps their history) |
| `/agent-revise [change]` | change the plan on a branch you review and merge |
| `/agent-improve [focus]` | improvements from past sessions and runs, on a branch you review |

## What is in this folder

- `plan.yaml`: the plan (goal, stages, approvals)
- `scripts/`: stage programs; the first run writes one, you review it, later runs reuse it
- `gates/`: programs that check what a stage produced
- `.pi/`: the agent itself (its role in `APPEND_SYSTEM.md`, extension, subagents, a copy of prometheus's skill)
- `.env`: this machine's values (paths, accounts, secrets); not in git, and the model may not read it
- `runs/<id>/`: one folder per run (its record, working files, outputs); not in git
- `.git`: this folder is its own repo; plan, gates, programs and subagents are versioned (tags `r1`, `r2`, …)

## On a new machine

- Pi with pi-subagents installed globally (`pi install npm:pi-subagents`)
- `uv` (the agent's packages come from `pyproject.toml`) and git
- this machine's values in `.env`: `/agent-run` asks for what is missing
