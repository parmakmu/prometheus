# What the Python scripts do

This page is for contributors. It has one entry per script in [`.pi/skills/agent/scripts/`](../.pi/skills/agent/scripts/), grouped by when the script runs. You never run them by hand: the commands and the agent tools do. How they fit into prometheus is in [Contributing](contributing.md#architecture).

## Create and maintain agents

**`scaffold_agent.py`** creates an agent from `templates/agent/`. It fills in the slug and request, writes a draft plan, starts the agent's git repository, and runs `uv lock`. With `--refresh`, it rewrites the files an agent got from prometheus and keeps the agent's own: its plan, runs, programs, agents, packages, and the lessons section of `.pi/APPEND_SYSTEM.md`. With `--from <agent>`, a new agent starts from another one: its plan as a draft, its gates, programs, subagents and packages. `agent_scaffold` and `/agent-refresh` use it. An agent's own copy refuses to run, because the templates belong to prometheus.

**`prometheus_version.py`** names the prometheus an agent's skill copy came from: `<pyproject version>+<commit>[.dirty] <hash>`: `.dirty` marks uncommitted changes, and the hash, of the files a refresh copies, alone decides whether an agent is up to date. The commit is the last one that touched the skill or the templates, so a docs or tests commit doesn't make agents stale. Creating or refreshing an agent writes the label to `.pi/skills/agent/VERSION`; `/agent-list` marks agents with another label stale. Standard library only.

**`export_agent.py`** copies an agent elsewhere and validates the copy. By default it leaves out runs, conversations (`.pi/sessions`) and git history; `--keep-state` keeps them. It never copies `.env` or local installs, and gives the copy a `.env` that lists each variable, unset.

**`list_agents.py`** summarises agents for `/agent-list` and `/agent-status`: status, revision, active or last run, stage progress, next wave, open questions, subagents, unreviewed branches. It changes nothing.

## Validate and compile

**`validate_plan.py`** loads `plan.yaml` and checks it: shape and types with pydantic, then the cross-reference rules (one `Checker` method per group), dependencies, portability and params. It derives the waves from the stages and the run's progress, and returns a summary with open questions, missing params and secrets, approvals and the next wave. It never runs a stage.

**`compile_wave.py`** turns the active run's next wave into the exact pi-subagents call. The compiled script is the wave as JSON data followed by `wave_runner.js` unchanged, saved to `runs/<id>/.compiled/<wave>.js`; the call names that path. When something is missing first (a param, a decision, an approval, a review, a plan fix), it returns a `preflight` instead. `--check` compile-checks the concrete pending waves during planning, without a run. A compile only prepares the call: the model launches it.

## Track and edit a run

**`agent_state.py`** works out the agent's [state](state-machine.md) from four facts (the plan's readiness, the active run, a running wave, the checked-out branch) and lists the moves the scripts accept from it (`MOVES`). Every agent tool's result carries it as `next`.

**`run_log.py`** owns `runs/<id>/run.yaml`. It starts and stops runs, records launches and decisions, and finishes waves. Finishing reads the outcome and cost from pi-subagents, updates the stages, and commits the files the wave wrote into the agent (stage programs, say) on the run's branch.

**`plan_edit.py`** makes the plan changes that must be checked: a replan of pending stages, a critic's result with its dispositions, and param values written to `.env` (`params`; `params-show` lists them for the web app's form, a secret only as set or not). A replan keeps the stages that ran and the locked fields, validates the result, raises the revision and commits on the run's branch.

**`agent_git.py`** does every git operation: creating the repository, committing the plan and the files runs write (`runs/` itself is not versioned), opening `run/`, `revise/` and `improve/` branches, and merging (as one squashed commit), keeping or discarding them.

**`run_history.py`** reads the agent's runs side by side (`show`: per run its cost, per stage how it went) and prunes old ones (`prune`: the `work/` folders of finished runs beyond the newest N, and pi-subagents' records of their launches; `run.yaml` and `.compiled/` stay).

**`session_digest.py`** reads the agent's conversations from its own `.pi/sessions` (else Pi's old per-path folder) and condenses what is new since the last `/agent-improve` into evidence: user messages, failed tool results, repeated calls, cost, failed or relaunched waves, and the lessons already in `improvements.yaml`. It reads at most the newest 5 sessions and 3 runs, and skips improve and revise sessions themselves. It changes nothing.

**`session_read.py`** is the `agent_sessions` tool. `list` gives one line per session; `show` gives a clipped transcript of one, chosen by record numbers or around a timestamp. Session lines are too long for Pi's read tool, so without this a model writes its own parser.

## Values and batches

**`agent_params.py`** is the param logic the other scripts import. It loads values from the environment, `.env` or the plan's default; resolves `path`, `text` and `secret` params; masks secrets for `run.yaml`; and writes `.env`, refusing secrets. It is a module, not a command.

**`batch_tool.py`** is run by the `batcher` command runner, so no model is needed for mechanical steps. It splits a map stage's input into batches and merges their outputs, and it runs a stage program with its `run:` arguments, then its gate. On failure it exits with the reason, which the wave hands to the stage's `<key>-fix` child. Standard library only, since the batcher runs it with plain `python3`.

## Shared helpers

**`runs.py`** is how every script reads runs: `Run` (id, folder, state, `.wave_running`, `.branch`, `.save()`), `list_runs`, `active_run`, `last_run`. Its docstring describes a run folder's layout.

**`utils.py`** holds the small shared helpers: results (`emit`, `refuse`, `report_state`), YAML in the plans' house style (`rt_yaml`, `load_yaml`, `read_yaml`, an atomic `write_yaml`), `read_json`, and access to Pi's and pi-subagents' records (`pi_agent_dir`, `mission_of`, `subagents_runs_root`, `subagent_records`, `subagent_child`, `process_alive`).

## How they fit together

`/agent-new` calls `scaffold_agent.py`, then planning uses `validate_plan.py` and `compile_wave.py --check` until the plan is valid and its waves compile. `/agent-run` starts a run with `run_log.py`. For each wave, `compile_wave.py` prepares the call, the model launches it, and `run_log.py` records the result. After discovery, `plan_edit.py` replans, and `agent_git.py` keeps the change on the run's branch until you review it.
