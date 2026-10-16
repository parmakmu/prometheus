# What the Python scripts do

This page is for contributors. It has one entry per script in [`.pi/skills/agent/scripts/`](../.pi/skills/agent/scripts/), grouped by when the script runs. You never run them by hand: the commands and the agent tools do. How they fit into prometheus as a whole is in [Contributing](contributing.md#architecture).

## How they fit together

Here is the path an agent takes through the scripts, from planning to a finished run:

1. `/agent-new` calls `scaffold_agent.py` to create the agent's folder.
2. Planning then calls `validate_plan.py` and `compile_wave.py --check` until the plan is valid and every wave compiles.
3. `/agent-run` starts a run with `run_log.py`.
4. For each wave, `compile_wave.py` prepares the call, the model passes it to pi-subagents, and `run_log.py` records how it ended. (`/agent-auto` makes the same calls from the extension, without the model.)
5. After a discovery stage, `plan_edit.py` resolves, and `agent_git.py` keeps the change on the run's branch until you review it.

Every one of them reads and writes runs through `runs.py`, and prints a single JSON object through `utils.py`.

A released agent takes a shorter path: `release.py` tags it, and prometheus's `frozen.py` compiles each wave with `compile_wave.py --frozen` and launches it, with no model and no run log. When a released run fails, `bundle.py make` packs its evidence there, and `bundle.py add` unpacks it in the agent for `/agent-improve`.

## Create and maintain agents

**`scaffold_agent.py`** creates an agent from `templates/agent/`. It fills in the slug and request, writes a draft plan, starts the agent's git repository, and runs `uv lock`. `agent_scaffold` and `/agent-refresh` use it. An agent's own copy refuses to run, because the templates belong to prometheus.

- With `--refresh`, it rewrites the files an agent got from prometheus and keeps the agent's own: its plan, runs, programs, subagents, packages, and the lessons section of `.pi/APPEND_SYSTEM.md`. It also adds the template's packages that the agent's `pyproject.toml` lacks, and raises a lower `requires-python` floor.
- With `--from <agent>`, a new agent starts from another one: its plan as a draft, and its gates, programs, subagents and packages.

**`prometheus_version.py`** names the prometheus an agent's skill copy came from, as `<pyproject version>+<commit>[.dirty] <hash>`. `.dirty` marks uncommitted changes. The hash covers the files a refresh copies, and it alone decides whether an agent is up to date. The commit is the last one that touched the skill or the templates, so a commit that changes only docs or tests doesn't make agents look out of date. Creating or refreshing an agent writes the label to `.pi/skills/agent/VERSION`, and `/agent-list` marks an agent with another label as needing `/agent-refresh`. Standard library only.

**`export_agent.py`** copies an agent elsewhere and validates the copy. By default it leaves out runs, conversations (`.pi/sessions`) and git history; `--keep-state` keeps them. It never copies `.env` or local installs, and gives the copy a `.env` that lists each variable, unset.

**`add_package.py`** is the `agent_add_package` tool. After the user has approved the packages and the reason, it runs `uv add` for them. A package is a PEP 508 requirement (a name, extras, version bounds and markers, parsed with `packaging`), never a URL, an option or a path. Like gates and programs, packages change only where a change can be reviewed: not on `main` once a run exists, and not while a wave runs.

**`list_agents.py`** summarises agents for `/agent-list`, `/agent-status` and `prometheus list`: status, revision, the active or last run, stage progress, the next wave, open questions, subagents, unreviewed branches, and whether the active run's branch isn't checked out. It changes nothing.

## Validate and compile

**`validate_plan.py`** loads `plan.yaml` and checks it. First pydantic checks its shape and types, then one `Checker` method per group checks the cross-reference rules, dependencies, portability and params. It derives the waves from the stages and the run's progress, and returns a summary with open questions, missing params and secrets, approvals and the next wave. It never runs a stage. Its models also generate `plan.schema.json` (`plan_json_schema()`), which editors use to check `plan.yaml` as you type.

**`compile_wave.py`** turns the active run's next wave into the exact pi-subagents call. A compile only prepares the call; the model, or `/agent-auto`'s code, launches it.

- **What it writes.** The compiled script is the wave as JSON data followed by `wave_runner.js`, unchanged, saved to `runs/<id>/.compiled/<wave>.js`; the call names that path. Beside it, `<wave>.json` keeps the compile's result and each model stage's fingerprint (`inputs`), and `run_log.py finish` later adds what the wave returned (`result`). Only a wave that can launch is saved.
- **When it can't launch yet.** If something is missing first (a param, a decision, an approval, a review, a plan fix), it returns a `preflight` saying what, instead of a call.
- **Reuse.** A model stage an earlier run finished on the same inputs isn't launched again; its outputs are copied from that run. A map stage is reused whole (the same items and batch size): it stands in the wave as one reused item, so its split, batches and merge aren't launched. When every stage of a wave is reused, nothing is launched at all: `compile_wave` records the wave itself (`run_log.record_without_launch`, the same code as `start` and `finish`) and answers `launched: false` with `finish`'s result. On a relaunch, children that completed and haven't changed aren't run again either.
- **`--check`** compile-checks the concrete pending waves during planning, without a run.
- **`--frozen <folder>`** compiles a released agent's next wave in that folder, with the caller's `--params` and `--approve`, the stages its `state.json` records as done, and no active run, commit or `.env`. A plan in which nothing asks the caller anything is one wave (`all`). Otherwise the waves are derived as in an agent's own run, and a decision or an unapproved side effect comes back in `preflight` for the caller to answer. A released run reuses the release's earlier runs just as an agent's run reuses its own.

**`release.py`** makes releases: it tags `v<major>.<minor>` on `main` when code can run the plan with no model deciding anything. That means the plan is ready, no stage is provisional, every program is written, and every wave compiles as a released run compiles it. Decisions and side effects are allowed: a released run asks its caller there.

- `check` reports whether `HEAD` can be released, the interface callers depend on (params, outputs, side effects, decisions), and the changes since the last release.
- `tag` tags it. It refuses a change that can break callers (a param removed, retyped or newly required, an output changed, a side effect added) unless `--breaking` is given, which raises the major version.
- `list` reads the releases back from their tags' messages.

## Track and edit a run

**`agent_state.py`** works out the agent's [state](state-machine.md) from four facts (the plan's readiness, the active run, a running wave and the checked-out branch), and lists the moves the scripts accept from it (`MOVES`). Every agent tool's result carries it as `next`.

**`run_log.py`** owns `runs/<id>/run.yaml`. It starts and stops runs, records launches and decisions, and finishes waves. A run can start with its own param values, approvals and cost ceiling (`new --params --approve --max-cost-usd`), which is what the web app's run API passes. Finishing a wave reads its outcome and cost from pi-subagents, updates the stages, saves what the wave returned to its record, and commits the files the wave wrote into the agent (stage programs, say) on the run's branch.

**`plan_edit.py`** makes the plan changes that must be checked:

- `resolve` writes out provisional stages once their discovery has run, or changes other pending stages the user asks about, during a run. It keeps the stages that ran and the locked fields, validates the result, raises the revision and commits on the run's branch.
- `critique` records a critic's result, with a disposition for each finding.
- `params` writes param values to `.env`, refusing secrets, and `params-show` lists them for the web app's form, showing a secret only as set or not.
- `card` describes the agent to a caller of its API, without any values.

**`agent_git.py`** does every git operation: creating the repository, committing the plan and the files runs write (`runs/` itself is not versioned), opening `run/`, `revise/` and `improve/` branches, and merging them (as one squashed commit), keeping them or discarding them. It also checks that a run's branch is checked out (`find_off_branch`): `agent_state.py` reports it, and a run's own steps switch back when nothing is uncommitted and refuse otherwise.

**`run_history.py`** has two commands:

- `show` reads the agent's runs side by side: per run its cost, and per stage how each launch went, from the wave's record (`.compiled/<wave>.json`, its `result`).
- `prune` deletes the files of old runs: the `work/` folders of finished runs beyond the newest N, pi-subagents' records of their launches (each wave's folder, its children's, and their sessions and outputs, from `utils.launch_records`), and its mission files once none of their launches is kept. `run.yaml` and `.compiled/` stay, so the history stays. On a release's folder it prunes the release's runs the same way, with their `inputs/` and `outputs/` (`state.json` and `events.jsonl` stay). It keeps a run that waits for an answer, and the newest `--keep-failed` failed runs on top of `--keep`, so they can still be bundled. It also removes the empty folder pi-subagents leaves in the agent's `.pi/sessions/<session>/` for each launch, except while a wave runs.

**`session_digest.py`** reads the agent's conversations from its own `.pi/sessions` and condenses what is new since the last `/agent-improve` into evidence: user messages, failed tool results, repeated calls, cost, failed or relaunched waves, and the lessons already in `improvements.yaml`. It reads at most the newest 5 sessions and 3 runs, skips improve and revise sessions themselves, and changes nothing. Imported released runs (`runs/imported/`, from bundles) come as `imported`: the release that ran, how it ended, and each failed child's error, output and session file.

**`session_read.py`** is the `agent_sessions` tool. `list` gives one line per session, and `show` gives a clipped transcript of one, chosen by record numbers or around a timestamp; it also reads a failed child's session, or its pi-subagents transcript, from an imported bundle. Session lines are too long for Pi's read tool, so without this a model writes its own parser.

**`bundle.py`** carries a run's evidence to where the agent is developed.

- `make` packs a run folder into `<slug>-<version>-<run>.tgz`: a release's (`prometheus bundle`, `serve`'s `GET /runs/<id>/bundle`, `Run.bundle()`), or one of the agent's own (`/agent-bundle`, in a copy with no prometheus: version `r<revision>`, its branch's commit, and its Pi sessions during the run under `agent-sessions/`). `bundle.json` says what ran (agent, version, commit, the prometheus, Pi and pi-subagents versions), how it ended, and each launch's children (status, error, output, where their session is in the bundle). Beside it go the run's records, pi-subagents' records of its launches (`utils.launch_records`) and the children's sessions; `--with-files` adds the caller's inputs, the outputs and `work/`; the children's own sessions and records in `work/.pi/sessions/` (pi-subagents writes them there in a session) go in either way. The values of the agent's secret params that are set (the environment, an agent's `.env`) are replaced by `${NAME}`, and text matching `utils.SECRET_PATTERNS` is reported.
- `show` prints what a run's bundle would say, and a release's last events, without packing anything (`prometheus runs <slug> <run>`). Unlike `make`, it reads a run that's still going.
- `add` unpacks a bundle into `runs/imported/<version>-<run>/` for `/agent-improve`. Neither `runs.list_runs` nor `list_released_runs` sees that folder, so nothing is reused from it. It refuses another agent's bundle and one that names a path outside its folder, and warns when the commit that ran isn't on `main`.

## Values and batches

**`agent_params.py`** is the param logic the other scripts import. It loads values from the environment, `.env` or the plan's default; resolves `path`, `text` and `secret` params; masks secrets for `run.yaml`; and writes `.env`, refusing secrets. It is a module, not a command.

**`batch_tool.py`** is run by the `batcher` command runner, so mechanical steps need no model. It splits a map stage's input into batches and merges their outputs, and it runs a stage program with its `args`, then its gate. On failure it exits with the reason, which the wave hands to the stage's `<id>-fix` child.

Its `a2a` op runs a stage that calls another agent. It reads the called agent's Agent Card, sends one A2A message (a file part per file the stage reads, and a data part with params and approvals), polls the task until it ends, and downloads the artifacts into the stage's output folder. A task that asks something, fails or is canceled fails the stage with its message.

It uses the standard library only, since the batcher runs it with plain `python3`, outside the agent's environment.

## Shared helpers

**`runs.py`** is how every script reads runs: `Run` (id, folder, state, `.wave_running`, `.branch`, `.save()`), `list_runs`, `list_released_runs`, `active_run` and `last_run`. It also reads and writes a launched wave's record: `read_wave` and `record_wave_result` for `<wave>.json`, and `read_wave_spec` for the wave's data at the start of `<wave>.js`. Its docstring describes a run folder's layout.

**`utils.py`** holds the small shared helpers:

- results: `emit`, `refuse`, `report_state`;
- YAML in the plans' house style: `rt_yaml`, `load_yaml`, `read_yaml`, and an atomic `write_yaml`; plus `read_json`;
- Pi's and pi-subagents' records: `pi_agent_dir`, `mission_of`, `subagents_runs_root`, `subagent_records`, `subagent_child`, `subagents_package` (its version), and `launch_records`, everything pi-subagents keeps of one launch (its folder, its children's, the sessions and outputs they name), only inside its own temporary folders;
- what looks like a secret: `SECRET_PATTERNS`, for `bundle.py` and prometheus's `share`;
- processes, with psutil: `process_alive`, which also recognises a pid that another process has reused since the recorded start, and `this_process`;
- packages, with `packaging`: `package_name`, a requirement's normalised name.
