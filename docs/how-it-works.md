# How Prometheus works

Prometheus turns a request into a portable **workflow**: a directory that contains a Pi agent, a reusable YAML plan, and the tools needed to run that plan. Prometheus designs and manages the workflow; the workflow agent coordinates the actual work through [pi-subagents](https://github.com/nicobailon/pi-subagents).

In short:

```text
request → plan → validate → run waves → review or replan → finish
                 Prometheus       workflow agent
```

This page describes both the operator experience and the implementation. The [README](../README.md) has the short setup and command reference, and the [worked example](worked-example.md) follows one real workflow through planning, runs and a revision. The workflow agent's detailed operating instructions are in [`run-protocol.md`](../.pi/skills/workflow/references/run-protocol.md), and the plan format is documented in [`plan-schema.md`](../.pi/skills/workflow/references/plan-schema.md).

## Two agents, two jobs

### Prometheus

Start Pi in the Prometheus repository. Its extension registers the `/workflow-*` commands and two planning tools. `/workflow-new` sends the request to the model with instructions to follow the workflow planning skill. The model and skill produce a workflow under `workflows/<slug>/`.

Prometheus does not carry out the user's requested task. It creates, lists, exports, refreshes, and checks workflows. It plans only when you run `/workflow-new`: a request typed as a plain message gets an answer, or a suggestion to use `/workflow-new`, never a new workflow. The extension enforces this. `workflow_scaffold` refuses unless `/workflow-new` ran in the session, and the planning skill is hidden from the model's automatic skill selection.

### The workflow agent

A generated workflow is a self-contained Pi project. It has its own `.pi/plan.yaml`, extension, agents, skill copy, and Git repository. Start Pi in that workflow's directory and use `/workflow-run` to execute it. pi-subagents launches the plan's children; the workflow session remains the orchestrator that talks to the user and decides what happens next.

Exporting a workflow copies it to a chosen location so it can be run independently of the Prometheus repository. The destination machine still needs Pi, pi-subagents, and the workflow's Python dependencies, and the user must trust the project in Pi.

## Creating a workflow

`/workflow-new <request>` starts the planning process:

1. **Scaffold.** Create `workflows/<slug>/` from the workflow template. The scaffold includes a draft plan, a workflow extension, the planning skill and references, and project agents such as `plan-critic` and `workflow-batcher`.
2. **Resolve what can be checked now.** Record available agents and resolve cheap, read-only unknowns. A `scout` may inspect facts that are not visible to the planning session. Unknowns that only the eventual work can settle become discovery stages.
3. **Draft the plan.** The plan records the goal, acceptance criteria, inputs and outputs, typed parameters, assumptions, questions, agents, and stages. Anything outside the portable workflow (such as an input folder, URL, account name, or token) is represented as a parameter rather than hard-coded into the plan.
4. **Classify and check stages.** Each stage is classified as a side effect, decision, deterministic task, or judgment task. The plan validator checks structure and dependencies; the compiler checks that concrete pending work can be turned into pi-subagents calls.
5. **Critique and clarify.** Plans with judgment or side effects, or with more than three stages, receive a separate read-only plan critique. Findings are dispositioned; only then are consequential questions asked. A question is warranted when its answer changes the work, a source or destination, a side effect, or what counts as done.
6. **Mark ready.** The workflow can be run once the plan validates, required questions are answered, and any required critique is recorded.

The planning rules live in [`SKILL.md`](../.pi/skills/workflow/SKILL.md). The rules for classifying tasks and routing unknowns are in [`classify.md`](../.pi/skills/workflow/references/classify.md) and [`clarify-rules.md`](../.pi/skills/workflow/references/clarify-rules.md).

## How a plan becomes work

### Plans contain stages; waves are derived

A plan describes **stages** and their `needs` dependencies; it does not contain a hand-authored list of waves. The validator derives waves from the dependency graph and the points where the workflow must pause for a person or replan. The compiler turns each wave into one subagent workflow call, with stages ordered into dependency layers.

A stage can use a built-in agent such as `worker`, `scout`, or `reviewer`, or a project agent defined by the workflow. Some stage patterns expand into multiple children: for example, a `map` stage is split into batches, processed by children, then merged. The `workflow-batcher` command runner handles that file splitting and merging.

### Four kinds of stage

The stage kind determines the safety and correctness strategy:

- **`side_effect`** changes something outside the current run—for example, sending a message or writing to a real service. It requires explicit user approval before launch.
- **`decision`** represents a choice that belongs to the user. It is collected before the relevant wave starts, not delegated to a child.
- **`deterministic`** work has correctness rules a program can decide. It uses a program plus a verification gate. The stage names its program (`script:`) and its arguments (`run:`). The program lives in the workflow's `scripts/`: the first run writes it, and the user reviews it with that run's changes. Later runs execute it as a plain command, with no model involved, which takes about a second. A model child is only brought in if the program or its gate fails; it gets the error and fixes the program.
- **`judgment`** work needs interpretation by a reader or model. Its output should have a defined shape and be reviewed when appropriate.

When mechanical processing has a plausible messy tail, the plan can split it: a deterministic stage handles regular cases and records leftovers, then a judgment stage handles those exceptions. See [`classify.md`](../.pi/skills/workflow/references/classify.md) for the decision test and examples.

### Discovery, provisional stages, and replanning

Sometimes the plan cannot responsibly specify downstream work until an earlier inspection discovers the data or scope. In that case, the plan includes a concrete discovery stage and leaves dependent stages **provisional**. Once discovery finishes, the workflow replans those pending stages, validates and critiques the updated plan, and then continues. This avoids pretending unknown details are already known.

### Parameters, gates, and outputs

Parameters make workflows portable. Their types are `path`, `text`, and `secret`. Paths refer to files or folders, text is passed verbatim, and secrets are supplied through the environment or the workflow's untracked `.env` file. Secret values are not placed in the plan or run log; the workflow agent must not read `.env`. See [Params, `.env` and secrets](params-and-secrets.md).

Stages may write files under a run's work directory or return a structured result. A **gate** is a program that checks a stage's output; a **review** is a separate judgment check. Outputs needed by later waves must be files, because a returned result does not persist across waves.

## Running a workflow

Start Pi in the workflow directory and invoke `/workflow-run`. A run proceeds through this loop:

1. **Validate and preflight.** Check the plan and its next derived wave. Ask for missing non-secret parameter values. If a secret is missing, tell the user which environment variable to set; never ask them to disclose the secret in chat.
2. **Collect human input.** Before launch, collect the wave's decisions and explicit approvals for its side-effect stages. The workflow agent describes what an approved side effect will change.
3. **Compile and launch.** The Python compiler creates the pi-subagents call and saves the generated script to `runs/<id>/.compiled/`. The workflow session passes that call, which points to the script file, unchanged to pi-subagents and records the launch. Children run in the run's `work/` directory.
4. **Record results.** After completion, the run logger records stage status, child outcomes, and cost. Gates and configured reviewers check outputs. A failed or blocked wave can be retried, changed through a replan, or stopped.
5. **Continue, pause, or replan.** The derived wave boundary tells the session whether to proceed, show results for review, report an approved side effect, or replan after discovery.
6. **Finish and review changes.** When all stages are done, report the outcomes and deliverable locations. If the run changed the workflow plan or scripts, show the branch diff and ask whether to merge, keep, or discard it.

A workflow run is therefore not a single model response: it has durable state, possibly multiple subagent launches, and explicit human control points. The full state machine and operator instructions are in [`run-protocol.md`](../.pi/skills/workflow/references/run-protocol.md). When something is refused, see [Troubleshooting](troubleshooting.md).

## State, Git, and safety

Each execution has its own directory, `runs/<id>/`, rather than storing run state in the reusable plan:

```text
runs/<id>/
  run.yaml       stage states, launches, decisions, approvals, parameters
  work/          children's working directory and file outputs
  outputs/       run-scoped output locations when configured
  .compiled/     compiled waves and their results
```

Each workflow is also its own Git repository. `main` is the reviewed baseline. A replan during a run is made on `run/<id>`; `/workflow-revise` starts a `change/<date>-<n>` branch when no run is active. Plan and script changes reach `main` only after the user reviews and merges them. Run data and `.env` are excluded from Git.

This separation supports repeatable runs while preserving what happened in each one. It also keeps discoveries from silently changing the plan used by future runs. The workflow extension exposes controlled tools for plan edits and run state, and blocks unsafe direct edits once a run exists. The workflow session gathers approval; a child does not approve its own side effects.

## Implementation map

The implementation keeps the Pi extensions thin; the Python scripts hold most workflow logic.

| Component | Responsibility |
|---|---|
| [`.pi/extensions/prometheus/index.ts`](../.pi/extensions/prometheus/index.ts) | Prometheus commands and planning tools; invokes the scripts and formats their results. |
| [`.pi/skills/workflow/SKILL.md`](../.pi/skills/workflow/SKILL.md) | Planning procedure used to turn a request into a plan. |
| [`.pi/skills/workflow/references/`](../.pi/skills/workflow/references/) | Plan schema, classification, clarification, critic contract, agent profiles, and run protocol. |
| [`.pi/skills/workflow/scripts/`](../.pi/skills/workflow/scripts/) | Python implementation for plan validation, wave compilation, run state, parameters, workflow Git, exports, and map batching. See [What the Python scripts do](python-scripts.md) for the role of each script. |
| [`.pi/skills/workflow/templates/workflow/`](../.pi/skills/workflow/templates/workflow/) | Files copied into each new workflow: its extension, skill, plan skeleton, agents, settings, and instructions. |
| [`.pi/agents/plan-critic.md`](../.pi/agents/plan-critic.md) | Fresh-context, read-only critic profile used while planning. |
| [`tests/`](../tests/) | Validator and parameter tests, mock subagent runs, and extension harnesses. |

Prometheus slash commands, model-facing tools, and workflow slash commands are defined separately in the [command and tool reference](commands.md). The key distinction is that commands are user entry points, while extension tools are invoked by the model to perform controlled planning and run operations.

## Development and tests

Python dependencies are declared in [`pyproject.toml`](../pyproject.toml). Set them up with:

```sh
uv sync
uv run pytest
```

The pytest suite covers plan validation and derived waves, typed parameters, map batching, scaffolding and export, compiled runs against a pi-subagents mock, run state and Git review paths, and extension behavior. The extension tests type-check and load both extensions with Pi's real types; they need `npm install` in the repository and Pi installed globally with npm, and are skipped otherwise.

`tests/test_workflow.py` is one continuous scenario (scaffold, compile, run, replan, merge, export): later tests use what earlier ones left on disk, so its tests must run in file order, which is pytest's default.

## Glossary

- **Workflow:** a portable folder containing a Pi agent and reusable plan.
- **Plan:** `.pi/plan.yaml`, describing goals, inputs, outputs, stages, dependencies, and policies.
- **Stage:** one unit of work in a plan.
- **Wave:** a group of stages the compiler can launch together, derived from dependencies and pause points.
- **Run:** one execution of a plan, stored in `runs/<id>/`.
- **Child:** a pi-subagents item launched to do part of a wave.
- **Gate:** a program that checks the correctness of a stage's output.
- **Replan:** a reviewed update to pending plan work after discovery or a user-requested change during a run.
