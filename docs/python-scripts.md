# What the Python scripts do

Prometheus's extensions are intentionally thin: they expose Pi commands and tools, invoke Python scripts, and format the scripts' JSON results. The scripts contain most of the deterministic logic for creating, validating, compiling, and tracking workflows. The model still plans the work and coordinates with the user; pi-subagents launches the actual stage children.

```text
Pi command/tool → TypeScript extension → Python script → JSON result
                                      ↘ subagent call (only after compile)
```

The scripts live in [`.pi/skills/workflow/scripts/`](../.pi/skills/workflow/scripts/). The Prometheus extension invokes them from the Prometheus root; a generated workflow's extension invokes its copy from that workflow. They are primarily internal implementation components, not commands users need to run by hand.

## By lifecycle stage

### Create and maintain workflows

- **`scaffold_workflow.py`** — creates a new workflow from `templates/workflow/`, substitutes its slug and request, initializes the workflow's Git repository, and writes a draft plan. With `--refresh`, updates the copied template files and skill in an existing workflow while preserving its plan and run-specific/user-authored files. The Prometheus `/workflow-new` tool and `/workflow-refresh` command use this script.
- **`export_workflow.py`** — copies a workflow to another location and validates the copy. A fresh export leaves out run state and Git history; `--keep-state` carries them over. Both modes leave out local installs and `.env` values, and the exported workflow gets an unset parameter file for its new machine.
- **`list_workflows.py`** — reads plan and run state to produce summaries such as status, revision, active or last run, stage progress, next wave, open questions, custom agents, and unmerged branches. It does not change the workflow. Both `/workflow-list` and `/workflow-status` use it.

### Validate and prepare a run

- **`validate_plan.py`** — loads and validates `.pi/plan.yaml`: field types and required fields, cross-references, stage dependencies, portability and workflow-specific rules. It also derives the waves from stages and run status, and returns a summary that includes open questions, missing params/secrets, approvals, and the next wave. It does not run stages.
- **`compile_wave.py`** — turns the active run's next derived wave into the exact pi-subagents call, expanding parameters, gates, schemas, dependencies, and any map batching. The generated script is the wave as data (its layers, children, map batching and reviews) followed by `wave_runner.js` unchanged, so every wave runs the same hand-written runner. It is saved to `runs/<id>/.compiled/<wave>.js`, and the call refers to it by path, so the model passes a short path to pi-subagents instead of retyping the script. It returns preflight requirements when input values, decisions, approvals, or plan fixes are needed. `--check` compile-checks pending concrete waves during planning without starting a run. Even a successful compile only prepares a call; the model must pass that call to pi-subagents to launch it.

### Track and edit a run

- **`run_log.py`** — owns durable run state in `runs/<id>/run.yaml`. Its operations create or stop a run, record a wave launch, record a user's decision, and finish a wave. Finishing reads the child workflow outcome from pi-subagents, updates stage and run status, saves wave results, and commits workflow files written by children on the run branch.
- **`plan_edit.py`** — performs checked changes that should not be made by directly editing protected state. It applies a replan to pending stages, records the plan critic's findings and disposition, or writes non-secret parameter values to `.env`. Replans preserve completed/running history and locked plan fields, validate the result, bump its revision, and commit it on the run branch.
- **`workflow_git.py`** — centralizes workflow Git operations: ensuring the workflow repository exists, committing the plan and the files runs write into the workflow (gate scripts, stage programs; run state in `runs/` is not versioned), creating/reviewing branches, and merging, keeping, or discarding a reviewed branch. `main` remains the reviewed baseline; run and change branches keep proposed edits separate until the user chooses what to do.

### Resolve values and batch work

- **`workflow_params.py`** — shared parameter logic used by validation, compilation, and plan editing. It loads values from the process environment, `.env`, or a plan default; validates and resolves `path`, `text`, and `secret` parameters; and keeps secrets out of persisted run values. It is a Python module imported by the other scripts, not a user-facing CLI.
- **`batch_tool.py`** — splits a map stage's file or list input into deterministic batches, merges the batch outputs, and runs a stage's program with its `run:` arguments followed by its gate. It is run by the workflow's `workflow-batcher` command-runner agent, so no model is needed to divide or join files or to run a reviewed program. When the program or its gate fails, it exits with the reason on stderr, and the wave hands that reason to the stage's `<key>-fix` child.

## How they fit together

A typical new workflow calls `scaffold_workflow.py`, then the planning process uses `validate_plan.py` and `compile_wave.py --check` to make sure the proposed plan is valid and concrete waves can compile. `/workflow-run` uses `run_log.py` to start a run; `compile_wave.py` prepares each wave; and the extension passes its returned call to pi-subagents. After a wave completes, `run_log.py` records its result. Discovery may cause `plan_edit.py` to revise pending stages; `workflow_git.py` preserves that proposal on the run branch for later user review. When finished, `export_workflow.py` can copy the workflow elsewhere.

These responsibilities are split so that deterministic checks and state transitions are implemented as testable Python code, while the model focuses on interpreting the request, making judgment calls, and communicating with the user. The tests in [`tests/`](../tests/) exercise the scripts directly and in a mock workflow run.

## Running and testing

Most users should use `/workflow-*` commands rather than call these scripts manually. For contributors, set up the Python environment and run the regression suite from the repository root:

```sh
uv sync
uv run pytest
```

The Python dependencies are declared in [`pyproject.toml`](../pyproject.toml). The extensions choose the nearest `.venv` automatically, or use `PI_WORKFLOW_PYTHON` when explicitly set.
