# Contributing

How prometheus is built, how to test it, and the rules a change follows.

## Architecture

The Python scripts hold the logic; the two Pi extensions are thin.

```text
Pi command or tool → extension (TypeScript) → Python script → one JSON object
                                            ↘ pi-subagents call (only after a compile)
```

- **Scripts** ([`.pi/skills/workflow/scripts/`](../.pi/skills/workflow/scripts/)) validate, compile, record and commit. Each prints one JSON object; a refusal is `{"ok": false, "error": …}`. Every workflow script's result also carries `next`: the workflow's state and the moves open from it. Scripts run and test without Pi.
- **Extensions** register the commands and tools, run a script with `pi.exec`, and format its JSON. One self-contained `index.ts` per agent: [prometheus's](../.pi/extensions/prometheus/index.ts) and [the workflow template's](../templates/workflow/.pi/extensions/workflow/index.ts).
- **`wave_runner.js`** is the only JavaScript the compiler emits: a compiled wave is the wave as JSON data, followed by this runner unchanged.

The extensions run scripts with `$PI_WORKFLOW_PYTHON`, else the nearest `.venv/bin/python` upwards (a workflow inside prometheus uses prometheus's), else `python3`.

### What a launch looks like

The workflow agent orchestrates a wave but never writes its code. `workflow_compile` saves the wave as data plus the shared runner in one file, and the agent hands pi-subagents only that file's path (*from a real run*):

```json
{
  "async": true,
  "missionId": "f7a80a96-6b5a-473c-9768-9956d5e557fe",
  "workflow": "/…/runs/2026-10-01-3/.compiled/w2.js"
}
```

When the wave ends, `workflow_log_finish` gets the wave's id and returns a summary of the children, not their outputs:

```json
"children": {
  "discover":        { "status": "completed", "output": "…/work/discovery.md" },
  "discover-review": { "status": "completed", "output": null },
  "read-emails":     { "status": "completed", "output": null }
}
```

Every result also carries `next`, the workflow's state and its legal moves (*from a real run*, between runs):

```json
{
  "state": "ready",
  "moves": ["workflow_new_run", "/workflow-revise", "/workflow-improve"],
  "hint": "start a run, or change the plan on a branch"
}
```

**Learn more:** [What the Python scripts do](python-scripts.md) · [The state machine](state-machine.md) · [Design decisions](design.md)

## Where things are

| Path | Holds |
|---|---|
| [`.pi/extensions/prometheus/index.ts`](../.pi/extensions/prometheus/index.ts) | prometheus's commands and planning tools |
| [`.pi/skills/workflow/SKILL.md`](../.pi/skills/workflow/SKILL.md) | the planning procedure the model follows |
| [`.pi/skills/workflow/references/`](../.pi/skills/workflow/references/) | the model's references: plan schema, classification, critic contract, run protocol, replanning, changing |
| [`.pi/skills/workflow/scripts/`](../.pi/skills/workflow/scripts/) | the Python scripts |
| [`templates/workflow/`](../templates/workflow/) | what a new workflow gets: its extension, agents, settings, role. Outside the skill, so workflows don't carry a copy |
| [`.pi/agents/plan-critic.md`](../.pi/agents/plan-critic.md) | the critic; must stay identical to the template's copy (a test checks) |
| [`tests/`](../tests/) | the pytest suite |
| [`ui/`](../ui/) | the web app: router, per-agent workers, the page; see its [README](../ui/README.md) |

The skill's `SKILL.md` and `references/` are instructions for the model, not documentation for people. [`.claude/CLAUDE.md`](../.claude/CLAUDE.md) records the settled design decisions and the reasons for them.

## Set up and test

```sh
uv sync                      # .venv with the scripts' packages and pytest
uv run pytest                # everything; extension tests skip without the Node deps
npm install && uv run pytest # adds type-checking and loading both extensions (Node >= 22, Pi installed globally with npm)
uv run ruff check
```

The suite covers plan validation and derived waves, params, map batching, scaffolding and export, compiled waves run against a mock of pi-subagents (real batch tool and gates, fake children), run state, git review paths, the state machine, and both extensions driven with Pi's real types.

[`tests/test_workflow.py`](../tests/test_workflow.py) is one continuous scenario (scaffold, compile, run, replan, merge, export): later tests use what earlier ones left on disk, so its tests run in file order. Don't add a test-randomizing plugin, put a new check where the state it needs exists, and rerun the whole file rather than one test from it.

## Rules for a change

- **A validator rule** gets a broken-variant case in [`tests/test_validator.py`](../tests/test_validator.py).
- **A refusal a person can meet** gets a test that triggers it and an entry in [Troubleshooting](troubleshooting.md): a short heading, the message, its cause and the fix.
- **A script's JSON output that changes** is checked against both extensions, which format it.
- **A state or a move** goes in `workflow_state.MOVES` and [the state machine](state-machine.md); [`tests/test_state_machine.py`](../tests/test_state_machine.py) walks every state and move and fails on a move the table doesn't list.
- **Behaviour that changes** is updated in these docs in the same change. Each fact lives on one page; others link to it.
- **Packages** are never installed by the model or a script: ask the person to run `uv sync`.

## Writing these docs

The docs follow [Divio's four types](https://docs.divio.com/documentation-system/). Each page is one type and doesn't do the others' jobs:

| Type | Pages | It does | It doesn't |
|---|---|---|---|
| Tutorial | [Tutorial](tutorial.md) | teaches by doing, with a result at every step that works every time; tells the reader what they should see | explain, offer options, or assume knowledge |
| How-to guide | [How-to guides](how-to.md), [Troubleshooting](troubleshooting.md) | gives the steps to a goal the reader already has; the title says what it achieves ("How to …") | teach, or explain why |
| Explanation | [How prometheus works](how-it-works.md), [Design decisions](design.md) | gives context, the why, and alternatives, read at leisure | instruct, or list every option |
| Reference | [Commands and tools](commands.md), [Params, `.env` and secrets](params-and-secrets.md), and for contributors [The state machine](state-machine.md), [What the Python scripts do](python-scripts.md) | describes accurately and completely, in one consistent form | explain or opine |

- A page that needs another type's content links to it.
- Output in a doc is copied from a real run, and labelled as such. Where it can't be, don't invent it.
- A reason in an explanation comes from a recorded decision or an observed failure, as in [Design decisions](design.md). Otherwise leave it out.
- One word per concept: workflow, plan, stage, wave, launch, child, run, gate, stage program, project agent, replan, change.
- A command or tool entry follows the form in [Commands and tools](commands.md): summary, syntax, where it runs and in which states, what it does, an example, its refusals, see also.
- Each fact lives on one page. Operator text comes first; implementation detail goes in a marked "For contributors" section, or on a contributor page.
- How prometheus works says *what* a thing is; Design decisions says *why*, starting from what went wrong. Don't explain a concept on both.
- Define a term where a reader first meets it, or link it to the glossary at the top of [How prometheus works](how-it-works.md#words-used-on-this-page).
- Write like a person explaining to a colleague: "you" for the reader, "we" for the people who built prometheus. Say why something matters to the reader before what it is, link sections with a sentence that says where we're going next, and admit trade-offs and past mistakes plainly. Vary sentence length; clipped fragments read as cold. Clarity still wins: split a sentence when it carries more than one colon or semicolon, or buries its point behind a qualification.
- Show, then tell: open a section with one line on what the thing does for the reader, then the real artifact (a command, a file, output from a run), then the explanation of what it shows. A number from a real run, or what went wrong, beats an abstract reason.
- Put tips, warnings and asides in GitHub callouts (`> [!TIP]`, `> [!NOTE]`, `> [!IMPORTANT]`), one short paragraph each, so the main text stays on its path.
- In Troubleshooting, a refusal the agent recovers from by itself goes under "Handled by the agent"; the other sections are for what needs the reader.
- Don't hard-wrap lines: one paragraph per line, so diffs stay small when a sentence changes.
