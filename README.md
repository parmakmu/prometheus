# Prometheus

A standalone Pi agent that turns requests into **workflows**: portable
folders that are Pi agents themselves and run multi-step work with pi-subagents.

```
cd prometheus
pi                     # trust the folder when asked, so pi-subagents and the extension load
/workflow-new I want to read the emails in a folder and save them in an Excel file
```

Prometheus plans the work (asking only questions that change the plan,
sending read-only probes, getting a critic's review) and writes a workflow
agent to `workflows/<slug>/`. It never does the work itself.

## Commands in Prometheus

| command | what it does |
|---|---|
| `/workflow-new <request>` | plan a new workflow |
| `/workflow-list [slug]` | list workflows and where they stand |
| `/workflow-export <slug> <dest> [--keep-state]` | copy a workflow somewhere else to run |
| `/workflow-refresh <slug \| --all>` | update workflows to Prometheus's skill version |

Commands are prefixed `workflow-` because Pi has built-in `/new` and `/export`.

## Commands in a workflow

Each workflow is its own Pi agent:

```
cd workflows/<slug>     # or wherever you exported it
pi                     # trust the folder
/workflow-run
```

| command | what it does |
|---|---|
| `/workflow-run [note]` | plan, continue or run from where it stands |
| `/workflow-status` | report where it stands; changes nothing |
| `/workflow-revise [change]` | change the workflow's plan on a branch you review and merge (no run active) |

Machine-specific values (folders, pages, accounts, tokens) go in the
workflow's `.env` (not in git) or the environment pi runs in; `/workflow-run`
asks for missing ones, and for secrets tells you which variable to add.

Each run gets a folder `runs/<id>/`. The workflow is its own git repo: `main`
holds the reviewed plan; changes a run makes to its plan (replans after
discovery) and changes made with `/workflow-revise` go to a branch, and you choose
to merge, keep or discard it after seeing the diff.

## Layout

```
.pi/APPEND_SYSTEM.md                 Prometheus's role
.pi/settings.json                    subagents config (pi-subagents is installed globally)
.pi/extensions/prometheus/index.ts   the /workflow-* commands and workflow_scaffold / workflow_check tools
.pi/agents/plan-critic.md            fresh-context critic for plans
.pi/skills/workflow/                 the skill: planning and run rules, scripts, examples
  templates/workflow/                everything a new workflow gets
workflows/                           the workflows
tests/                               regression suite (see CLAUDE.md)
```

## Requirements

Pi with pi-subagents (installed globally: `pi install npm:pi-subagents`), Python 3 with
typer, pydantic, pydantic-settings and ruamel.yaml (run `uv sync` here; the extensions use `.venv`), and project trust for this
folder and each workflow.
