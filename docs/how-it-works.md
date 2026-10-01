# How Prometheus works

A **workflow** is a folder that holds a plan for a piece of work and a Pi agent that can carry the plan out. Prometheus is the agent that writes workflows. You describe what you want, Prometheus turns it into a plan, and from then on the workflow runs by itself, as many times as you like, asking you only for what a person has to decide.

Some work that suits a workflow:

* Reading a folder of emails each month and saving the interesting fields in a spreadsheet.
* Producing a report from files that change every week, where a person approves it before it is sent.
* Any job you would otherwise explain to a model again each time, where doing it differently on Tuesday would be a bug.

You can think of a workflow as a mix between a script and a task you hand to a team. Like a script, it does the same thing every time, and most of its steps are programs that run in a fraction of a second without a model. Like a team, the parts that need judgment go to model agents, a separate agent reviews the work, and a person approves anything with consequences.

This page follows one real workflow, `emails-to-excel`, and shows what each idea looks like in it. The output blocks were copied from its runs. For the exact command syntax see the [reference](commands.md); for how to do a specific thing, the [how-to guides](how-to.md).

## Two agents

You use two Pi agents, each started in its own folder.

**Prometheus** is started in this repository. It plans: it creates a workflow folder, drafts the plan, has the plan checked, and asks the questions whose answers change the plan. It does not do your work, and it doesn't act on a plain message at all: it plans only when you run `/workflow-new`. The planning skill is hidden from the model's automatic skill selection, and the tool that creates a workflow refuses unless that command ran. This is deliberate: a casual sentence about documentation once led an earlier version to scaffold a workflow nobody had asked for.

**The workflow agent** is started inside `workflows/<slug>/`. It carries out the plan, one wave at a time, using [pi-subagents](https://github.com/nicobailon/pi-subagents) to launch the children that do the work. It talks to you, but it does very little thinking about *how* to run: the Python scripts around it decide what is allowed next, and it follows them.

The workflow folder is self-contained. It has its own plan, its own extension, its own copy of the planning skill and its own git repository, so you can copy it to another machine and run it there without Prometheus.

```text
workflows/emails-to-excel/
  plan.yaml       the plan
  gates/          programs that check what a stage produced
  scripts/        stage programs: the work itself, as plain code
  .pi/            the workflow agent: extension, agents, skill copy
  .env            this machine's values (not in git)
  runs/           one folder per run (not in git)
```

## The plan

`plan.yaml` is the whole definition of the work. It records the request in your words, the goal, what counts as done, the inputs and outputs, the machine-specific values it needs, and the stages. In emails-to-excel it begins:

```yaml
request: I want to read emails and save them in Excel
goal: >-
  Read the selected emails and save their requested information in an Excel workbook.
done_when:
  - The selected emails are processed according to the user's chosen scope and fields.
  - An .xlsx workbook is saved at the configured output path.
  - The workbook contains one row per email and its columns match the confirmed extraction scope.
```

The plan holds no run state. That is why the same plan can run again on new input: everything a particular execution produces goes in a run's own folder instead. The plan is versioned. Its `revision` goes up with each reviewed change, and `status` says whether it is a `draft`, still has questions (`needs_answers`), or is `ready`.

## Stages and their kinds

A **stage** is one unit of work. The interesting thing about a stage is its **kind**, because the kind decides how it is done and how it is checked. Prometheus gives each stage one of four kinds, trying them in this order, so that the most dangerous interpretation wins:

1. **`side_effect`**: changes something outside the run, such as sending a message or writing to a service. Needs your explicit approval before every launch.
2. **`decision`**: a choice that belongs to you. It is collected before its wave starts and is never left to a child.
3. **`deterministic`**: work a program can get right. It is done by a program and checked by a gate.
4. **`judgment`**: work that needs a reader, such as classifying or summarising. It is done by a model child and, where it matters, reviewed by another.

Irreversibility comes first on purpose. A stage that writes to your CRM and also happens to be mechanical is a side effect, and is treated as one.

The three stages of emails-to-excel are one of each of the interesting kinds:

```text
discover         judgment        a scout reads sample emails and reports the formats and fields
read-emails      deterministic   a program extracts the fields; a gate checks every file is accounted for
build-workbook   side_effect     a program writes the .xlsx; you approve it first
```

Work that is mostly mechanical but has a messy tail is split. A deterministic stage handles the regular cases and writes the leftovers to a file; a small judgment stage handles only those. That keeps the model away from the thousand ordinary cases.

## Gates and stage programs

The reason a deterministic stage is cheap is that no model does it. It has a **stage program** in `scripts/`, named in the plan with its arguments:

```yaml
- key: read-emails
  kind: deterministic
  pattern: script
  script: read_emails.py
  run: "${email_folder} discovery.md email-rows.json unparsed-emails.json"
```

The first run writes the program, a model child doing so once, and you review it with that run's branch. Every later run executes it as a plain command, which takes about a tenth of a second. A model is brought back only if the program or its gate fails, and then it is shown the error and asked to fix the program.

A **gate** is a program in `gates/` that checks what a stage produced. For `read-emails` it checks that every `.eml` file has exactly one parsed row or a machine-readable error. Gates are written while planning, never by the stage they check, so a stage can't mark its own homework.

## Waves

Stages are launched in **waves**. You never write them: they are derived from the stages. A wave is the stretch of work between two points where the workflow agent has to act, to ask you something, or to look at a result. The validator reports the waves it derived:

```json
"waves": [
  { "id": "w1", "stages": ["discover", "read-emails"], "ends_with": "none" },
  { "id": "w2", "stages": ["build-workbook"],          "ends_with": "approval" }
]
```

`discover` and `read-emails` run together because nothing between them needs a person. `build-workbook` is a side effect, so it gets its own wave, and the agent stops to ask for your approval before launching it. A wave can also end with a `review` (you look at the outputs first) or a `replan` (the plan is filled in from what was found).

Outputs that a later wave needs must be files. A result a stage merely returns doesn't cross a wave boundary.

## Runs

Each execution is a **run**, in its own folder:

```text
runs/2026-10-01-3/
  run.yaml        what happened
  work/           the children's working folder and their file outputs
  outputs/        where relative output paths resolve
  .compiled/      each wave as compiled, and its result
```

At most one run is active at a time. `run.yaml` is the durable record: if the session ends and you come back tomorrow, the state is on disk. After the run above, it holds, for example:

```yaml
id: 2026-10-01-3
status: done
revision: 4
stages:
  discover: done
  read-emails: done
  build-workbook: done
launches:
  - wave: w1
    stages: [discover, read-emails]
    ends_with: none
    status: done
    subagent_run_id: d1ff16c7-0573-4648-bb1f-1b04540b10a4
    params:
      email_folder: /Users/musaparmaksiz/projects/prometheus/scratch/emails
      output_file: outputs/emails.xlsx
    cost_usd: 0.00377
    started_at: '2026-10-01T23:45:18+02:00'
    finished_at: '2026-10-01T23:46:08+02:00'
```

Each launch records the plan's commit and revision, so `git show r4:plan.yaml` shows exactly what ran. Param values appear here, but a secret is recorded only as `(secret, set)`.

### What a launch looks like

The workflow agent doesn't write the code for a wave. A compiler turns the wave into data, a JSON description of its stages and checks, saved next to a fixed runner program, and hands pi-subagents only the path:

```json
{
  "async": true,
  "missionId": "f7a80a96-6b5a-473c-9768-9956d5e557fe",
  "workflow": "/…/runs/2026-10-01-3/.compiled/w2.js"
}
```

When the wave ends, the agent asks for just the wave's id and gets back a summary, not the outputs:

```json
"children": {
  "discover":        { "status": "completed", "output": "…/work/discovery.md" },
  "discover-review": { "status": "completed", "output": null },
  "read-emails":     { "status": "completed", "output": null }
}
```

The outputs were already checked by the gates and the reviewer, so the agent reads one only when you need to see it.

## Params

Anything that depends on the machine is a **param**: an input folder, an output path, a table name, a token. The plan declares them and never contains their values:

```yaml
params:
  email_folder:
    description: Folder containing the user's exported .eml email files
    required: true
  output_file:
    description: Where to save the completed Excel workbook
    default: outputs/emails.xlsx
```

Values live in `.env`, which is not in git, or in the environment Pi runs in. A param is a `path`, `text` or `secret`. A secret's value is never written to the plan, a compiled wave, a child's task or a run log, and the model is blocked from reading `.env`: when one is missing, the agent names the variable and waits for you to add it. Paths from outside the workflow must be absolute.

Planning never asks for these values, since they belong to whoever runs the workflow. `/workflow-run` asks.

## The plan critic

Before a plan can run, a separate agent reviews it. The **plan critic** is read-only and starts with a fresh context: it sees the plan file and none of the planner's reasoning, because a model grading its own plan tends to miss what it missed while writing it. It checks that the goal and `done_when` match the request, that stages are classified correctly, that nothing with a side effect is hidden, and that no question is missing.

Every finding gets a disposition: accepted, rejected with a note, or asked of you. A plan gets at most two critic passes per revision.

The critic's result is tied to the exact plan it saw, by a digest. Edit the plan afterwards and it is no longer ready until the critic has seen the new version.

## Discovery and replanning

Sometimes a plan can't responsibly describe a later stage until an earlier one has looked at the real data. In emails-to-excel, nobody knew at first what the emails looked like: plain text or HTML, with attachments or without. The plan then has a **discovery** stage, and the stages that depend on it stay **provisional**, just an outline. After discovery the run **replans**: it fills those stages in from what was found, the critic reviews the change, and the run continues.

A replan is deliberately narrow. It can change only pending stages. The request, goal, `done_when`, params, inputs, outputs and agents are locked, stages that already ran are history, and a side effect stays a side effect. Anything wider is a change you make between runs.

## Branches and review

Each workflow is its own git repository, and `main` holds the reviewed plan. The rule is that `main` changes only through a merge you chose, apart from planning before the first run. So a run's discoveries, such as a replan or a program it wrote, go to a branch:

| Branch | Opened by | Holds |
|---|---|---|
| `run/<id>` | a run's first commit | its replans and the stage programs it wrote |
| `revise/<date>-<n>` | `/workflow-revise` | a change you asked for |
| `improve/<date>-<n>` | `/workflow-improve` | fixes drawn from earlier sessions and runs |

A branch can have many commits as it goes. When a run ends or a change is ready, the agent shows you the diff and you choose: **merge** (the whole branch becomes one commit on `main`, tagged with the revision), **keep**, or **discard**. A run that changes nothing opens no branch.

After a few reviewed changes, emails-to-excel's `main` carries the tags `r2`, `r3`, `r4` and `r5`, one per revision, so `git show r4:plan.yaml` is the plan as it was then.

Only scripts commit, never the model, and never while a wave is running: pi-subagents compares the repository's staged files before and after each child that writes, and a commit in the middle would fail that check.

## States

At any moment a workflow is in one of ten **states**, worked out from four facts: whether the plan is ready, whether a run is active, whether a wave is running, and which branch is checked out. Each state allows certain moves, and the scripts refuse the rest, so the agent never has to guess what it may do next. Every tool result carries the answer:

```json
{
  "state": "ready",
  "moves": ["workflow_new_run", "/workflow-revise", "/workflow-improve"],
  "hint": "start a run, or change the plan on a branch"
}
```

This is emails-to-excel between runs. During a run the same block would say `between_waves` or `wave_running`, with `workflow_compile` or `workflow_log_finish` as the only legal moves.

## Planning

`/workflow-new` plans in six steps. It creates the folder from a template, with a draft plan holding your request. It then resolves what can be checked cheaply, and a read-only `scout` may look at facts the planner can't see; an unknown only the work can settle becomes a discovery stage. It drafts the plan, turning every folder, URL, account and token into a param. It classifies each stage and checks the plan, including that each concrete wave can compile. The critic reviews the draft. And only then does it ask questions, at most three per round and two rounds, and only where a wrong guess would change the stages, a side effect, a source or destination, or what counts as done.

## Running

`/workflow-run` runs. It checks the plan and the next wave, asks for missing values, collects the wave's decisions and approvals in one question, compiles and launches, and records the outcome from pi-subagents when the wave ends. Between waves it follows what the last one ended with: on to the next wave, show you the outputs, or replan. At the end it reports each `done_when` line against what the gates and reviews checked, and shows the branch if the run changed the workflow.

A failed wave doesn't lose work. Its stages go back to pending, and when it is relaunched, children that completed and haven't changed are not run again.

## Performance

What a run costs depends mostly on how much of it is model work. In emails-to-excel's real runs:

| Run | What happened | Children's cost |
|---|---|---|
| 2 | every stage done by model children | $0.016 |
| 3 | the two programs written (once) | $0.021 |
| 4 | the programs reused | $0.0105 |
| later | same plan, after a plan change | $0.004 |

A reused program takes about a tenth of a second; the model children that did the same work took 15 to 20 seconds each. What is left is `discover`, the one stage that still needs a model.

The orchestrating session, the agent that talks to you, costs about as much again as the children. That is why the scripts hand it summaries and a path to the compiled wave rather than outputs and script text: retyping a script had been about half of its output.

Two more things about cost. A plan with many stages makes a compile and a record per wave, not per stage, so waves matter more than stage counts. And a `map` stage runs one child per batch, which is as costly as the batches are many.

## Alternatives

A workflow is more than most jobs need. Consider something simpler when:

* **The job is a one-off.** A single Pi session, or one subagent, is quicker than planning, a critic and a review. A workflow's plan, gates and programs are reused from the second run on.
* **The whole job is mechanical.** If no step needs judgment, a plain script with a test may be all you need. A workflow adds a plan, a gate and a review around the same program.
* **You don't want a human in the loop.** Approvals and reviews are the point of a workflow. For fully unattended work, the workflow agent refuses to approve a side effect itself.

## Learn more

* [Tutorial: your first workflow](tutorial.md) runs the example above yourself.
* [Design decisions](design.md) explains why each part is the way it is, and what was tried first.
* [The state machine](state-machine.md) lists every state and move.
* [Worked example](worked-example.md) is the history of emails-to-excel: what each run showed, and what changed because of it.
* [`plan-schema.md`](../.pi/skills/workflow/references/plan-schema.md) documents the plan format; [`run-protocol.md`](../.pi/skills/workflow/references/run-protocol.md) is the workflow agent's own instructions.
