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

The plan refers to a param as `${email_folder}` in input and output paths, in stage output paths, in a stage program's `args` and in gate commands. Planning never asks for param values: they belong to the machine, and `/agent-run` asks for them.

### Types

| Type | Use it for | What `${name}` becomes |
| --- | --- | --- |
| `path` (default) | files and folders | the path, with `~` expanded. A relative value, such as the default `outputs/emails.xlsx`, resolves inside the run's folder (`runs/<id>/`), so runs don't overwrite each other's outputs. An input's path must be absolute (see [Setting values](#setting-values)). |
| `text` | a table name, URL, account id, anything that is not a path | the value, exactly as given |
| `secret` | keys, tokens, passwords | `"${ENV_NAME}"`, which the shell of a stage program or its gate expands. prometheus never writes the value anywhere: you put it in `.env` or the environment. |

### Values that change every run

A param whose value is different each time, such as a question to answer or a term to search for, is marked `per_run: true`:

```yaml
params:
  question:
    description: what to look up this time
    type: text
    per_run: true
```

Its value belongs to one run. `/agent-run` asks for it, or takes it from what you typed after the command, and keeps it on that run instead of in `.env`, so the next run asks again rather than quietly answering the last question. A run nobody is there to ask, such as `pi -p "/agent-auto"` from a script, reads it from the environment it was started with. Callers of a released agent give it like any other value. A secret can't be `per_run`.

### Where secrets reach

A secret can be used in three places only:

- a stage program's `args`;
- the gate of a stage whose program has `args`;
- an `a2a` stage's `token`, when it calls another agent.

All three are run by the batcher, the part of a wave that executes commands with no model, so no model child ever gets a secret. A program without `args` is run by a model child, so its stage can't use a secret.

That's deliberate. Every child of a run shares one sandbox and one environment, and a child reading an email can be told by that email to send what it finds somewhere. So a stage that needs a token to call a service does it with a program, or through a connector (a tool, such as an MCP server, that holds its own credentials).

A secret that only a connector reads, such as the connector's own token, goes in the environment you start Pi from, or in the connector's configuration, because `.env` isn't loaded into Pi's environment. Declaring it in the plan is still worth it: the run then checks that it's set before launching.

## Where values come from

Each param reads one environment variable: the one its `env:` names, or else its name in upper case (`email_folder` → `EMAIL_FOLDER`). In an agent's own runs, a value is looked up in this order:

1. the environment Pi was started in;
2. `.env` in the agent's folder;
3. the plan's `default`.

Two kinds of run get their values from whoever started them instead:

- **A call to the web app's run API** gives values for that run only, which win over the list above. It can't give a secret, and a relative path resolves inside the run, so `inputs/emails` names the files the call sent. See [ui/README.md](../ui/README.md), "The run API".
- **A released agent run from code** (`prometheus run -p`, `load_agent`, `prometheus serve`) has no `.env` at all. A value is looked up in the caller's values, then the environment of the process that runs it (the only place for secrets), then the plan's `default`. A relative path the caller gives is relative to the caller's own working folder; a default such as `outputs/emails.xlsx` still resolves inside the run. A served agent gets its files as uploads. A required param with none of these refuses the run before it starts ("can't run: missing params"). See [How to run an agent from code](how-to.md#how-to-run-an-agent-from-code).

`.env` is an ordinary dotenv file:

```sh
# Values for this machine; not in git.
EMAIL_FOLDER='/Users/me/Mail/export'
CRM_API_TOKEN='…'
```

It is in the agent's `.gitignore`, so it is never committed, and `/agent-export` leaves it out.

## Setting values

- **Paths and text:** run `/agent-run`. If a required value is missing, the agent asks for it in chat and writes it to `.env`. For files and folders, give an absolute path (`~/…` is fine). A relative input path, or one that doesn't exist, is refused and asked for again. You can also edit `.env` yourself.
- **Secrets:** add them yourself, to `.env` or to the environment you start Pi from. The agent never asks for a secret's value. When one is missing, it tells you which variable to set and what it is for, then waits. You don't need to restart Pi: `.env` is read afresh each time it's needed.

To point the next run at different input, change the value in `.env` (or tell `/agent-run` that the input changed). Values persist across runs, except a `per_run` param's, which each run asks for.

## What happens to secrets

A secret's value stays in `.env` (or your environment), and reaches only the command that names it. Specifically:

- The agent can't read `.env`: the agent extension blocks the read, write and edit tools on it, and shell commands that mention it (best effort for shell commands).
- `agent_set_params`, the model's tool for setting values, refuses secrets.
- `.env` is not loaded into Pi's environment, so no child inherits a secret.
- In compiled waves, a secret appears only as `"${ENV_NAME}"`, in a stage program's command, its gate or an `a2a` token. Each command the batcher is asked to run lists the secret variables it names (`secrets`); the batcher reads just those, from the environment or else `.env`, at that moment, into that command's environment and no other.
- A run's record (`runs/<id>/run.yaml`) lists the param values each launch ran with, but a secret only as `(secret, set)`.

## Exporting an agent

`/agent-export` never copies `.env`: values are per machine and may hold secrets. The copy gets a `.env` listing each param's variable with no value, ready to fill in on the new machine.

## Rules the validator enforces

- Every `${name}` refers to a declared param.
- A param's variable is a valid environment variable name, and not one the system or a tool already uses (`PATH`, `HOME`, `USER`, anything starting `PI_`, …). Otherwise a param called `user` would silently read `$USER`.
- Two params can't read the same variable.
- A secret has no `default`, can't be `per_run`, and can't be part of a path (a stage's output path, or a file or folder input's path).
- A secret appears only in the three places under [Where secrets reach](#where-secrets-reach).
- A path param starts the path it is in (`${folder}/x`, not `../${folder}`), since it compiles to an absolute path.
- Every value in a gate command is shell-quoted, as in a program's `args`, so a value can't add a command.
- A literal absolute path in a stage's output path is an error: use a param or a path relative to the run.
- A param that nothing uses gets a warning (except a secret, which a tool may read from the environment).

## For contributors

- **Loading and resolving:** [`agent_params.py`](../.pi/skills/agent/scripts/agent_params.py) builds a pydantic-settings model from the plan's params. `load()` returns values (a `SecretStr` for secrets); `resolve()` turns one reference into the text the compiler writes; `record()` masks secrets for `run.yaml`; `set_values()` writes `.env` and refuses secrets. The validator, compiler, run log and plan editor all go through it.
- **No environment for children:** `.env` is not loaded into pi's process environment; the scripts read it themselves (`agent_params.load`, the environment first). The compiler writes non-secret values into tasks and commands, and for a batcher request whose command or gate names a secret it adds `secrets` (the variable names) and `env_file` (`run_compile.secrets_for`); [`run_batcher.py`](../.pi/skills/agent/scripts/run_batcher.py) reads those from `.env` (the environment wins) into that command's environment only.
