# Prometheus

Some jobs keep coming back: the monthly folder of emails to copy into a spreadsheet, the weekly report built from files that change. Prometheus lets you describe a job like that once, and turns your description into a **workflow**: a folder holding a plan for the job and an agent that carries it out, as often as you need.

Programs take care of the mechanical parts, models handle the parts that need judgment, and nothing with real consequences happens until you've said yes.

```text
cd prometheus
pi                     # trust the folder when asked
/workflow-new I want to read the emails in a folder and save them in an Excel file
```

Prometheus plans the job, asks you only the questions whose answers would change the plan, and writes the workflow to `workflows/<slug>/`. It never does the work itself. When the job comes round, you start the workflow in its own folder and type `/workflow-run`.

## Why a workflow

The first time a workflow runs, it writes small programs for its mechanical steps, and you look them over. Every run after that reuses them, so the same input gives the same result, faster and for a fraction of the cost. Here's what that looked like in real runs of the example above:

| | Every stage done by a model | Programs run as commands |
|---|---|---|
| Model cost of the work | $0.016 | $0.004 |
| Time, start to finish | under 4 minutes | 77 seconds |

To be fair, a workflow is more than many jobs need. If you'll only do the job once, a plain Pi session is quicker. If no step needs judgment, a script with a test is all you need. And if you want something to run with nobody watching, this isn't it: a workflow always waits for you to approve side effects and review changes, because that's the point of it.

## Documentation

**Using workflows**

| Read | When you want to |
|---|---|
| [Tutorial: your first workflow](docs/tutorial.md) | learn by doing: plan, run, review and rerun one workflow |
| [How-to guides](docs/how-to.md) | reach one goal, such as running on new input or changing the plan |
| [Troubleshooting](docs/troubleshooting.md) | find out why a step was refused, and what to do |
| [How Prometheus works](docs/how-it-works.md) | understand the ideas, following one workflow from plan to second run |
| [Commands](docs/commands.md) | look up a command |
| [Params, `.env` and secrets](docs/params-and-secrets.md) | look up how machine-specific values and secrets work |

**Working on Prometheus**

| Read | When you want to |
|---|---|
| [Design decisions](docs/design.md) | know why a rule exists, and what went wrong before it |
| [Contributing](docs/contributing.md) | set up, test and change Prometheus, and write these docs |
| [The state machine](docs/state-machine.md) | look up each state a workflow can be in and its legal moves |
| [What the Python scripts do](docs/python-scripts.md) | find the script that does something |

## Requirements

Pi 0.99.2 with pi-subagents, Python 3.14 with uv, and git. The [tutorial](docs/tutorial.md#before-you-begin) shows how to check and install them.
