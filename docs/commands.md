# Command and tool reference

Prometheus has two extensions: one is loaded in the Prometheus project, and one is copied into each generated workflow. Commands start with `/` and are entered by the user in Pi. Tool names have no slash; Pi exposes them to the model so it can carry out the mechanical steps of planning or running a workflow.

Start Pi in the relevant directory: the Prometheus repository for Prometheus commands, or `workflows/<slug>/` (or its exported location) for workflow commands.

## Prometheus commands

| Command | Definition |
|---|---|
| `/workflow-new <request>` | Start planning a workflow for the supplied request. Prometheus creates a workflow folder under `workflows/`, then the planning process drafts and checks its plan. This is the only way to create a workflow: a plain message never starts planning. |
| `/workflow-list [slug]` | List workflows and their status. With a slug, show that workflow's plan validity, derived waves, questions, approvals, missing params/secrets, and compile status. This is a status/check operation; it does not run the workflow. |
| `/workflow-export <slug> <destination> [--keep-state]` | Copy a workflow to a destination so it can be run there. By default, export the plan without previous run state; `--keep-state` also carries run state and Git history. Neither mode copies `.env` values (they are per machine and may hold secrets): the copy gets an `.env` that lists each variable, unset. |
| `/workflow-refresh <slug \| --all>` | Refresh one or all existing workflows with the current workflow template and planning skill. It updates the generated agent files while preserving the plan, `.env`, runs, gates, stage scripts, and project agents added to the workflow. It refuses while a wave of the workflow's active run is running, since it rewrites files the running children may use. |

### Prometheus tools

These are model-facing tools, not slash commands. In normal use, `/workflow-new` directs the planning process to use them as needed.

- **`workflow_scaffold({ slug, request })`** — create `workflows/<slug>/` from the workflow template, initialize its plan with the request, and add the workflow extension, skill, agents, and supporting files. It refuses unless `/workflow-new` ran in this session, and each `/workflow-new` allows one workflow. Running `scaffold_workflow.py` from the shell is blocked, so the tool can't be bypassed.
- **`workflow_check({ slug })`** — validate the workflow plan and compile-check each concrete pending wave. It reports errors, warnings, and a summary; it never launches a wave or performs the planned work.

## Workflow commands

These commands are available after starting Pi inside one workflow folder.

| Command | Definition |
|---|---|
| `/workflow-run [note]` | Plan, continue, or run the workflow from its current state. It checks the plan, starts or resumes a run, collects missing values and human decisions/approvals, and launches the next wave. A note is attached to a newly started run. It makes pi-subagents' `subagent` tool active first, so the model doesn't have to. |
| `/workflow-status` | Report the plan and run status without changing anything: last and next wave, outstanding questions, missing params/secrets, pending approvals, validity, and branch review state. A running wave is checked live the next time `/workflow-run` is used. |
| `/workflow-revise [change]` | Change the workflow plan without running its stages. When a run already exists, it opens or continues a change branch for the edits; the user reviews the resulting diff and chooses merge, keep, or discard. It is unavailable while a run is active. Before the first run, planning can continue on `main`. |
| `/workflow-improve [focus]` | Propose improvements from how the workflow actually went. The agent reads a digest of its latest sessions and runs (failed or blocked tool results, repeated calls, cost, failed or relaunched waves) plus its own context, proposes fixes to the plan, gates, stage programs and project agents with the evidence for each, asks about each one, and applies the accepted ones on a change branch you review like `/workflow-revise`. It can also add short rules for this workflow's agent under `## Lessons for this workflow` at the end of `.pi/APPEND_SYSTEM.md`; `/workflow-refresh` keeps that section and rewrites the rest. The skill and its references are not edited: a problem there is listed as text to take to Prometheus. Every proposal, applied or rejected, is recorded in `improvements.yaml` on the branch, so the next pass skips the reviewed sessions and doesn't re-propose a rejected fix. Nothing runs automatically: the command is only ever typed by you. Unavailable while a run is active. |

### Workflow tools

The workflow extension also gives the model tools for the run protocol. They are not user slash commands; use `/workflow-run` to initiate that protocol. Their order of use is in [`how-it-works.md`](how-it-works.md#running-a-workflow) and [`run-protocol.md`](../.pi/skills/workflow/references/run-protocol.md).

| Tool | Definition |
|---|---|
| `workflow_check` | Validate the plan and compile-check its concrete pending waves. |
| `workflow_new_run` | Start a run (`runs/<id>/`). Refuses while another run is active or an unreviewed branch is checked out. |
| `workflow_stop_run` | Stop the active run; it stays in `runs/`, marked stopped. Returns its branch for review, and the call that closes its pi-subagents mission. |
| `workflow_compile` | Compile the active run's next wave into the exact pi-subagents call, or list what must happen first: params to set, secrets to add to `.env`, decisions to collect, approvals to get. |
| `workflow_record_decision` | Write the user's answer for a decision stage where the compiler looks for it. |
| `workflow_log_start` | Record a wave launch: its stages, the pi-subagents run id, the plan's commit. |
| `workflow_log_finish` | Record a finished wave from just its id: the outcome and cost are read from pi-subagents. Commits files the wave wrote into the workflow on the run's branch, and says what comes next. After the last wave it also returns the plan's done-when criteria for the final report. |
| `workflow_replan` | Change pending stages during a run, after discovery or when the user asks. Stages that ran, the goal, params, inputs, outputs and agents are locked, and a side effect stays a side effect. |
| `workflow_record_critique` | Record a plan-critic result; marks the plan ready if it validates. |
| `workflow_set_params` | Set parameter values in `.env`. Refuses secrets: the user adds those. |
| `workflow_merge` | Finish the review of a run or change branch: merge, keep or discard. Only a ready, valid plan can be merged. |

## Typical sequence

Create a workflow in the Prometheus project, then run the generated agent in its own directory:

```text
# In the Prometheus directory
/workflow-new Summarize the research files into a Markdown report
/workflow-list
/workflow-export research-report ~/workflows

# In the workflow directory (or its export destination)
/workflow-status
/workflow-run
```

For setup requirements and a short overview, see the [README](../README.md). For how planning and execution fit together, see [How Prometheus works](how-it-works.md). When a command refuses, see [Troubleshooting](troubleshooting.md).
