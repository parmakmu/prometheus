# Prometheus

A Pi agent that turns requests into **workflows**: portable folders that are Pi agents themselves and run multi-step work with [pi-subagents](https://github.com/nicobailon/pi-subagents).

```text
cd prometheus
pi                     # trust the folder when asked
/workflow-new I want to read the emails in a folder and save them in an Excel file
```

Prometheus plans the work (asking only the questions that change the plan, sending read-only probes, getting a critic's review) and writes a workflow agent to `workflows/<slug>/`. It never does the work itself. You run the workflow in its own folder, with `/workflow-run`; changes it makes to itself land on a branch you review.

## Documentation

The docs follow [Divio's four types](https://docs.divio.com/documentation-system/): each page is one type, so you can go straight to the kind of help you need.

| You want to… | Read | It is |
|---|---|---|
| **Learn** by doing | [Tutorial: your first workflow](docs/tutorial.md) | a lesson: plan, run, review and rerun a workflow |
| **Do** a specific thing | [How-to guides](docs/how-to.md) | steps for one goal, such as running on new input |
| | [Troubleshooting](docs/troubleshooting.md) | each refusal: the message, its cause, the fix |
| **Understand** | [How Prometheus works](docs/how-it-works.md) | the concepts, followed through one real workflow |
| | [Design decisions](docs/design.md) | why it is built this way, and what was tried first |
| | [Worked example](docs/worked-example.md) | the history of one workflow, run by run |
| **Look something up** | [Commands and tools](docs/commands.md) | every command and model-facing tool |
| | [The state machine](docs/state-machine.md) | each state a workflow can be in, and its moves |
| | [Params, `.env` and secrets](docs/params-and-secrets.md) | declaring params, where values come from, what is validated |
| **Contribute** | [Contributing](docs/contributing.md) | architecture, tests, rules for a change |
| | [What the Python scripts do](docs/python-scripts.md) | each script's job |

## Requirements

Pi 0.99.2 with pi-subagents, Python 3.14 with uv, and git. See the [tutorial](docs/tutorial.md#before-you-begin).
