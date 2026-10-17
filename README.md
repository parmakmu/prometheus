# prometheus

Some jobs keep coming back: the monthly folder of emails to copy into a spreadsheet, the weekly report built from files that change. prometheus lets you describe a job like that once, and turns your description into an **agent**: a folder that is a Pi agent of its own, holding a plan for the job and carrying it out, as often as you need.

Programs take care of the mechanical parts, models handle the parts that need judgment, and nothing with real consequences happens until you've said yes.

```text
uv tool install git+<prometheus repository>
prometheus init        # where your agents go: ~/agents unless you choose another folder
prometheus             # the planner
/agent-new I want to read the emails in a folder and save them in an Excel file
```

prometheus plans the job, asks you only the questions whose answers would change the plan, and writes the agent to `<slug>/` in your agents folder. It never does the work itself. When the job comes round, you start the agent with `prometheus <slug>` and type `/agent-run`.

Prefer a browser? `npm install`, `npm run ui:server` and `npm run ui:web` start a web app with the same agents: chat, files, the agents' state and results, and a terminal. It also serves a team, each person with their own sandboxed copy. See [`ui/README.md`](ui/README.md).

## Why an agent

The first time an agent runs, it writes small programs for its mechanical steps, and you look them over. Every run after that reuses them, so the same input gives the same result, faster and for a fraction of the cost. Here's what that looked like in real runs of the example above:

| | Every stage done by a model | Programs run as commands | The same emails again |
|---|---|---|---|
| Model cost of the work | $0.016 | $0.004 | $0: the earlier judgment is reused |
| Time, start to finish | under 4 minutes | 77 seconds | 29 seconds |

To be fair, an agent is more than many jobs need. If you'll only do the job once, a plain Pi session is quicker. If no step needs judgment, a script with a test is all you need. And an agent never acts behind your back: it waits for you to approve side effects and to review what its runs change, because that's the point of it. Once its plan has settled, you can release it and run it from code or as a service, with its side effects approved up front by whoever calls it.

## Documentation

**Using agents**

| Read | When you want to |
|---|---|
| [Tutorial: your first agent](docs/tutorial.md) | learn by doing: plan, run, review and rerun one agent |
| [How-to guides](docs/how-to.md) | reach one goal, such as running on new input or changing the plan |
| [Troubleshooting](docs/troubleshooting.md) | find out why a step was refused, and what to do |
| [How prometheus works](docs/how-it-works.md) | understand the ideas, following one agent from plan to second run |
| [Commands](docs/commands.md) | look up a command |
| [Python API](docs/python-api.md) | look up how to run a released agent from Python code |
| [Params, `.env` and secrets](docs/params-and-secrets.md) | look up how machine-specific values and secrets work |

**Working on prometheus**

| Read | When you want to |
|---|---|
| [Design decisions](docs/design.md) | know why a rule exists, and what went wrong before it |
| [Contributing](docs/contributing.md) | set up, test and change prometheus, and write these docs |
| [The state machine](docs/state-machine.md) | look up each state an agent can be in and its legal moves |
| [What the Python scripts do](docs/python-scripts.md) | find the script that does something |

## Requirements

Pi 1.3.0 or later with pi-subagents 0.79.0 or later, Node, uv (which brings Python 3.14), and git. `prometheus doctor` checks them; the [tutorial](docs/tutorial.md#before-you-begin) shows how to install them.
