# Params, `.env` and secrets

An agent is portable: everything that depends on the machine it runs on (a folder of input files, an output path, a database table, an API token) is a **param**, not a literal in the plan. The plan declares each param; the values live outside it, per machine.

The formal rules are in [`plan-schema.md`](../.pi/skills/agent/references/plan-schema.md), "Params and paths"; how values are loaded is under [For contributors](#for-contributors).

## Declaring params in the plan

```yaml
params:
  email_folder:
    description: Folder containing the exported .eml files
    required: true            # the default; false means it may stay empty
  output_file:
    description: Where to save the workbook
    default: outputs/emails.xlsx
  crm_table:
    description: The CRM table the invoices are matched against
    type: text
  crm_token:
    description: API token for the CRM
    type: secret
    env: CRM_API_TOKEN        # default: the param's name in upper case
```

The plan refers to a param as `${email_folder}` in input and output locations, in stage output paths and in gate commands. Planning never asks for param values: they belong to the machine, and `/agent-run` asks for them.

### Types

| Type | Use it for | What `${name}` becomes |
|---|---|---|
| `path` (default) | files and folders | the path, with `~` expanded; a relative value resolves inside the run's folder (`runs/<id>/`), so runs don't overwrite each other's outputs |
| `text` | a table name, URL, account id, anything that is not a path | the value, exactly as given |
| `secret` | keys, tokens, passwords | `${ENV_NAME}`, which the child's or gate's shell expands; the value itself is never written anywhere |

A `secret` that only a tool reads from the environment (a connector's token, say) doesn't need to be referenced anywhere in the plan. Declaring it is enough: the run then checks that it is set before launching.

## Where values come from

Each param reads one environment variable: the one its `env:` names, or its name in upper case (`email_folder` → `EMAIL_FOLDER`). A value is looked up in this order:

1. the environment pi was started in;
2. `.env` in the agent's folder;
3. the plan's `default`.

`.env` is an ordinary dotenv file:

```sh
# Values for this machine; not in git.
EMAIL_FOLDER='/Users/me/Mail/export'
CRM_API_TOKEN='…'
```

It is in the agent's `.gitignore`, so it is never committed, and exports leave it out.

## Setting values

- **Paths and text:** run `/agent-run`. If a required value is missing, the agent asks for it in chat and writes it to `.env`. For files and folders, give an absolute path (`~/…` is fine). A relative input path, or one that doesn't exist, is refused and asked for again. You can also edit `.env` yourself.
- **Secrets:** add them yourself, to `.env` or to the environment you start pi from. The agent never asks for a secret's value. When one is missing, it tells you which variable to set and what it is for, then waits. You don't need to restart pi: `.env` is reloaded before each step.

To point the next run at different input, change the value in `.env` (or tell `/agent-run` that the input changed). Values persist across runs.

## What happens to secrets

A secret's value stays in the environment. Specifically:

- The agent can't read `.env`: the agent extension blocks the read, write and edit tools on it, and shell commands that mention it (best effort for shell commands).
- `agent_set_params`, the model's tool for setting values, refuses secrets.
- In compiled waves, a secret appears only as `${ENV_NAME}`. Children and gate commands inherit pi's environment, so their shell expands it at run time.
- A run's record (`runs/<id>/run.yaml`) lists the param values each launch ran with, but a secret only as `(secret, set)`.

## Exporting an agent

Neither export mode copies `.env`: values are per machine and may hold secrets. The copy gets a `.env` listing each param's variable with no value, ready to fill in on the new machine.

## Rules the validator enforces

- Every `${name}` refers to a declared param.
- A param's variable is a valid environment variable name, and not one the system or a tool already uses (`PATH`, `HOME`, `USER`, anything starting `PI_`, …). Otherwise a param called `user` would silently read `$USER`.
- Two params can't read the same variable.
- A secret has no `default`, and can't be part of a path (a stage's output path, or a file or folder location).
- A literal absolute path in a stage's output path is an error: use a param or a path relative to the run.

## For contributors

- **Loading and resolving:** [`agent_params.py`](../.pi/skills/agent/scripts/agent_params.py) builds a pydantic-settings model from the plan's params. `load()` returns values (a `SecretStr` for secrets); `resolve()` turns one reference into the text the compiler writes; `record()` masks secrets for `run.yaml`; `set_values()` writes `.env` and refuses secrets. The validator, compiler, run log and plan editor all go through it.
- **Environment for children:** the agent extension loads `.env` into pi's own process environment at session start and before every script it runs. A variable already set in the real environment is left alone. pi-subagents spawns children with pi's environment, so children and gates see the same values the scripts do.
- **Migration:** older agents kept values in `.pi/params.yaml`. `/agent-refresh` moves them into `.env`; the validator warns while the old file exists.
