# What the Python scripts do

This page is for contributors. It has one entry per script in [`.pi/skills/agent/scripts/`](../.pi/skills/agent/scripts/), grouped by when the script runs (and an entry for `wave_runner.js`, the one JavaScript file beside them). People never need to run them: the commands and the agent tools do. To try a change, run one in an agent's folder with the agent's Python, `uv run python <prometheus>/.pi/skills/agent/scripts/<script>.py --help`, or through the tests, which run them in-process. How they fit into prometheus as a whole is in [Contributing](contributing.md#architecture).

## How they fit together

Here is the path an agent takes through the scripts, from planning to a finished run:

1. `/agent-new` calls `agent_scaffold.py` to create the agent's folder.
2. Planning then calls `plan_validate.py` and `run_compile.py --check` until the plan is valid and every wave compiles.
3. `/agent-run` starts a run with `run_steps.py`.
4. For each wave, `run_compile.py` prepares the call, the model passes it to pi-subagents, and `run_steps.py` records how it ended. (`/agent-auto` makes the same calls from the extension, without the model.)
5. After a discovery stage, `plan_edit.py` resolves, and `agent_repo.py` keeps the change on the run's branch until you review it.

Every script that is a command prints a single JSON object through `utils.emit` (a refusal through `utils.refuse`) and reads and writes runs through `run_folders.py`. Each works on the agent its `--agent` names (by default the current folder, where the extensions run it), and never on the current folder after that. Those that move an agent from one state to another (`run_steps.py`, `run_compile.py`, `plan_edit.py`, `agent_repo.py`) call `agent_state.require` before they change anything. A few are not commands, or not that kind: `agent_params.py` and `run_folders.py` are modules other scripts import, and `agent_version.py` and `run_batcher.py` use only the standard library, since they run where prometheus's packages may not be installed.

A released agent takes a shorter path: `agent_release.py` tags it, and prometheus's `run_release.py` compiles each wave with `run_compile.py --frozen` and launches it, with no model and no run log. When a released run fails, `run_bundle.py make` packs its evidence there, and `run_bundle.py add` unpacks it in the agent for `/agent-improve`.

## Create and maintain agents

**`agent_scaffold.py`** creates an agent from `templates/agent/`. It fills in the slug and request, writes a draft plan, starts the agent's git repository, and runs `uv lock` (`--no-lock` skips it, for an agent whose packages aren't reviewed yet). With `--refresh` it updates an existing agent's template files and skill copy instead (`--if-stale`: only when its version differs), and refuses during an active run or while a branch waits for review. The planner's `agent_scaffold` tool and `/agent-refresh` call it. An agent's own copy refuses to run, because the templates belong to prometheus.

- With `--refresh`, it rewrites the files an agent got from prometheus and keeps the agent's own: its plan, runs, programs, subagents, packages, and the lessons section of `.pi/APPEND_SYSTEM.md`. It also adds the template's packages that the agent's `pyproject.toml` lacks, and raises a lower `requires-python` floor.
- With `--from <agent>`, a new agent starts from another one: its plan as a draft, and its gates, programs, subagents and packages.

**`agent_version.py`** names the prometheus an agent's skill copy came from, as `<pyproject version>+<commit>[.dirty] <hash>`. `.dirty` marks uncommitted changes. The hash covers the files a refresh copies, and it alone decides whether an agent is up to date. The commit is the last one that touched the skill or the templates, so a commit that changes only docs or tests doesn't make agents look out of date. Creating or refreshing an agent writes the label to `.pi/skills/agent/VERSION`, and `/agent-list` marks an agent whose hash differs (`up_to_date`) as needing `/agent-refresh`. Standard library only.

**`agent_export.py`** copies an agent elsewhere and validates the copy. By default it leaves out runs, conversations (`.pi/sessions`) and git history; `--keep-state` keeps them. It never copies `.env` or local installs, and gives the copy a `.env` that lists each variable, unset.

**`agent_packages.py`** is the `agent_add_package` tool. After the user has approved the packages and the reason, it runs `uv add` for them. A package is a PEP 508 requirement (a name, extras, version bounds and markers, parsed with `packaging`), never a URL, an option or a path. Like gates and programs, packages change only where a change can be reviewed: not on `main` once a run exists, and not while a wave runs.

**`agent_list.py`** summarises agents for `/agent-list`, `/agent-status` and `prometheus list`: status, revision, the active or last run, stage progress, the next wave, open questions, subagents, unreviewed branches, and whether the active run's branch isn't checked out. It changes nothing.

## Validate and compile

**`plan_validate.py`** loads `plan.yaml` and checks it. First pydantic checks its shape and types, then one `Checker` method per group checks the cross-reference rules, dependencies, portability and params. It derives the waves from the stages and the run's progress, and returns a summary with open questions, missing params and secrets, approvals and the next wave. It never runs a stage. Its models also generate `plan.schema.json` (`plan_json_schema()`), which editors use to check `plan.yaml` as you type.

**`run_compile.py`** turns the active run's next wave into the exact pi-subagents call. A compile launches nothing; the model, or `/agent-auto`'s code, launches the call. When the wave can launch, it first switches back to the run's branch and commits pending plan changes there (`--no-commit` skips that). Its flags: `--approve` (side effects approved for this launch), `--reviewed` (after a wave that ends with a review), `--budget` (raise the run's cost limit), `--out`, `--check`, and `--frozen` for a released run.

- **What it writes.** The compiled script is the wave as JSON data followed by `wave_runner.js`, unchanged, saved to `runs/<id>/.compiled/<wave>.js`; the call names that path. Beside it, `<wave>.json` keeps the compile's result and each model stage's fingerprint (`inputs`), and `run_steps.py finish-wave` later adds what the wave returned (`result`). Only a wave that can launch is saved.
- **When it can't launch yet.** If something is missing first (a param, a decision, an approval, a review, a plan fix), it returns a `preflight` saying what, instead of a call.
- **Reuse.** A model stage an earlier run finished on the same inputs isn't launched again; its outputs are copied from that run. A map stage is reused whole (the same items and batch size): it stands in the wave as one reused item, so its split, batches and merge aren't launched. Otherwise a merged map stage (`pattern: map`) is reused item by item: its split gets the item stores (`work/<stage>/items/`) of earlier runs that finished it with the same item fingerprint (`<stage>/items` among the wave's fingerprints: the stage's, without what it maps over and its batch size) and leaves out the items they hold, and its merge restores their records and stores this run's. When every stage of a wave is reused, nothing is launched at all: `run_compile` records the wave itself (`run_steps.record_without_launch`, the same code as `start-wave` and `finish-wave`) and answers `launched: false` with `finish`'s result. On a relaunch, children that completed and haven't changed aren't run again either.
- **`--check`** compile-checks the concrete pending waves during planning, without a run.
- **`--frozen <folder>`** compiles a released agent's next wave in that folder, with the caller's `--params` and `--approve`, the stages its `run.yaml` records as done, and no active run, commit or `.env`. A plan in which nothing asks the caller anything is one wave (`all`). Otherwise the waves are derived as in an agent's own run, and a decision or an unapproved side effect comes back in `preflight` for the caller to answer. A released run reuses the release's earlier runs just as an agent's run reuses its own.

**`agent_release.py`** makes releases: it tags `v<major>.<minor>` on `main` when code can run the plan with no model deciding anything. That means the plan is ready, no stage is provisional, every program is written, and every wave compiles as a released run compiles it. Decisions and side effects are allowed: a released run asks its caller there.

- `check` reports whether `HEAD` can be released, the interface callers depend on (params, outputs, side effects, decisions), and the changes since the last release.
- `tag` tags it. It refuses a change that can break callers (a param removed, retyped or newly required, an output changed, a side effect added) unless `--breaking` is given, which raises the major version.
- `list` reads the releases back from their tags' messages.

**`wave_runner.js`** is not a script but the runner every compiled wave carries: `run_compile.py` writes the wave as data (`const W = {wave, revision, max_parallel, steps}`) and appends this file unchanged. It launches each layer's children together, at most `max_parallel` at a time, runs the batcher's commands, stops a layer after a child fails with no `-fix`, and returns the wave's outcome to pi-subagents. Its header comment describes the step types.

## Track and edit a run

**`agent_state.py`** is the agent's state machine, written like a Quint spec: `State` (read once per call), one action per move (guards, then the states it leads to; `require` refuses with the first guard that fails), `INVARIANTS`, and the [state](state-machine.md)'s name for people. Every agent tool's result carries the state and its moves as `next`.

**`run_steps.py`** owns `runs/<id>/run.yaml`. Its commands: `start-run`, `start-wave`, `finish-wave`, `decision` and `stop`. A run can start with its own param values, approvals and cost ceiling (`start-run --params --approve --max-cost-usd`), which is what the web app's run API passes. Finishing a wave reads its outcome and cost from pi-subagents, updates the stages, saves what the wave returned to its record, and commits the files the wave wrote into the agent (stage programs, say) on the run's branch.

**`plan_edit.py`** makes the plan changes that must be checked:

- `resolve` writes out provisional stages once their discovery has run, or changes other pending stages the user asks about, during a run. It keeps the stages that ran and the locked fields, validates the result, raises the revision, sets the plan back to draft (the critic must be recorded again) and commits on the run's branch.
- `critique` records a critic's result, with a disposition for each finding. It checks the critic ran, marks the plan ready when it validates and stores the plan's digest; it is refused while a wave runs, and on `main` once the agent has had a run.
- `params` writes param values to `.env`, refusing secrets, and `params-show` lists them for the web app's form, showing a secret only as set or not.
- `network-show` lists the hosts the plan asks to reach, for the web app to offer you to allow.
- `card` describes the agent to a caller of its API, without any values.

**`agent_repo.py`** does every git operation: creating the repository, committing the plan and the files runs write (`runs/` itself is not versioned), opening `run/`, `revise/` and `improve/` branches, and merging them (as one squashed commit), keeping them or discarding them. Its commands: `begin-change --kind revise|improve`, `review` (what a branch changed, for the review) and `merge --action merge|keep|discard`. It also checks that a run's branch is checked out (`find_off_branch`): `agent_state.py` reports it, and a run's own steps switch back when nothing is uncommitted and refuse otherwise.

**`run_history.py`** has two commands:

- `show` reads the agent's runs side by side: per run its cost, and per stage how each launch went, from the wave's record (`.compiled/<wave>.json`, its `result`).
- `prune` deletes the files of old runs: the `work/` folders of finished runs beyond the newest N, pi-subagents' records of their launches (each wave's folder, its children's, and their sessions and outputs, from `pi_records.launch_records`), and its mission files once none of their launches is kept. `run.yaml` and `.compiled/` stay, so the history stays. On a release's folder it prunes the release's runs the same way, with their `inputs/` and `outputs/` (`run.yaml`, `call.json` and `events.jsonl` stay). It keeps a run that waits for an answer, and the newest `--keep-failed` failed runs on top of `--keep`, so they can still be bundled. It also removes the empty folder pi-subagents leaves in the agent's `.pi/sessions/<session>/` for each launch, except while a wave runs.

**`session_digest.py`** reads the agent's conversations from its own `.pi/sessions` and condenses what is new since the last `/agent-improve` into evidence: user messages, failed tool results, repeated calls, cost, failed or relaunched waves, and the lessons already in `improvements.yaml`. It reads at most the newest 5 sessions and 3 runs, skips improve and revise sessions themselves, and changes nothing. Imported released runs (`runs/imported/`, from bundles) come as `imported`: the release that ran, how it ended, and each failed child's error and `activity`, plus its output and session file when the bundle came `--with-data`.

**`session_read.py`** is the `agent_sessions` tool. `list` gives one line per session, and `show` gives a clipped transcript of one, chosen by record numbers or around a timestamp; it also reads a failed child's session, or its pi-subagents transcript, from an imported bundle. Session lines are too long for Pi's read tool, so without this a model writes its own parser.

**`run_bundle.py`** carries a run's evidence to where the agent is developed.

- `make` packs a run folder into `<slug>-<version>-<run>.tgz`. The run is a release's (`prometheus bundle`, `serve`'s `GET /runs/<id>/bundle`, `Run.bundle()`), or one of the agent's own (`/agent-bundle`, in a copy with no prometheus). An agent's own run gets version `r<revision>`, its branch's commit, and its Pi sessions during the run under `agent-sessions/`.
  - By default the bundle is `bundle.json` alone, written field by field: what ran (agent, version, commit, the prometheus, Pi and pi-subagents versions), how it ended, the stages' statuses, the params' names, and each launch's children (status, error, exit code, and `activity`: turns, cost, model, tool calls by name, failed calls' results). Nothing of the run is copied as it is, since its records, sessions and compiled waves hold what it read, its param values among them. The texts left that may quote it (errors, failed calls' results) have each param's value, and a path's file name, replaced by `<name>`, and are returned as `to_check`.
  - `--with-data` adds the run as it is: each child's output and where its session is, the run's records and data folders, pi-subagents' records of its launches (`pi_records.launch_records`), the children's sessions (also the ones in `work/.pi/sessions/`, where pi-subagents writes them in a session), and an agent's own sessions during the run.
  - The values of the agent's secret params that are set (in the environment, or an agent's `.env`) are replaced by `${NAME}`, in texts before they are clipped, so a cut never leaves part of a value, and text matching `utils.SECRET_PATTERNS` is reported.
- `show` prints what a run's bundle made `--with-data` would say, and a release's last events, without packing anything (`prometheus runs <slug> <run>`). Unlike `make`, it reads a run that's still going.
- `add` unpacks a bundle into `runs/imported/<version>-<run>/` for `/agent-improve`. `run_folders.list_runs` doesn't see that folder, so nothing is reused from it. It refuses another agent's bundle and one that names a path outside its folder, and warns when the commit that ran isn't on `main`.

## Values and batches

**`agent_params.py`** is the param logic the other scripts import. It loads values from the environment, `.env` or the plan's default; resolves `path`, `text` and `secret` params; masks secrets for `run.yaml`; and writes `.env`, refusing secrets. It is a module, not a command.

**`run_batcher.py`** is run by the `batcher` command runner, so mechanical steps need no model. It splits a map stage's input into batches and merges their outputs, failing the merge when an item of the split is missing from the result or named twice. For per-item reuse the split leaves out items an earlier run's store holds, and the merge brings their records back in item order (an earlier run's path for an item renamed to this run's) and, once the gate passed, stores each item's record in this run. It also runs a stage program with its `args`, then its gate. On failure it exits with the reason, which the wave hands to the stage's `<id>-fix` child.

Its `a2a` op runs a stage that calls another agent. It reads the called agent's Agent Card, sends one A2A message (a file part per file the stage reads, and a data part with params and approvals), polls the task until it ends, and downloads the artifacts into the stage's output folder (a text part beside a file in the same artifact is that file's inline copy, so it isn't written twice). A task that asks something, fails or is canceled fails the stage with its message.

It uses the standard library only, since the batcher runs it with plain `python3`, outside the agent's environment.

## Shared helpers

**`run_folders.py`** is how every script reads runs: `Run` (`id`, `dir`, `state`, `.wave_running`, `.branch`, `.save()`), `read_run`, `list_runs` (an agent's runs, or a release's: both keep a `run.yaml`; `is_release_run` tells them apart by the caller's `call.json`), `stages_done`, `active_run` and `last_run`. It also reads and writes a launched wave's record: `read_wave` and `record_wave_result` for `<wave>.json`, and `read_wave_spec` for the wave's data at the start of `<wave>.js`; and `end_launch`, how a launch ended on its `run.yaml` entry (status, cost, tokens, children, error), the same for an agent's run and a release's. Its docstring describes a run folder's layout.

**`pi_records.py`** is the only script that reads pi-subagents' own files; the others read a launch as `run.yaml` records it. `runs_root`, `read_status` and `records` (its async runs' `status.json`), `outcome` (a launch's record as Prometheus keeps it: state, the wave's return value, cost, tokens, error, failure kind), `unrecorded_launch` (a launch of a compiled wave nobody recorded, by its script's digest), `launch_children`, `child`, `launch_records` (everything pi-subagents keeps of one launch: its folder, its children's, the sessions and outputs they name, only inside its own temporary folders), `bundle_name`, `package` (its version), `missions`, `mission_of` and `prune_missions`.

**`utils.py`** holds the small shared helpers:

- results: `emit`, `refuse`, `report_state`;
- YAML in the plans' house style: `rt_yaml`, `load_yaml`, `read_yaml`, an atomic `write_yaml`, and `fold_long` for long strings; plus `read_json`;
- the plan: `read_plan` and `write_plan`, which read and write `plan.yaml` and `.plan/` as one plan (every script uses them), `plan_text` (`plan.yaml`'s text, its sections in order under their banners), `stage_kind`, and `agent_name` (from `RELEASE.json` in a release's folder);
- Pi's folder: `pi_agent_dir`;
- what looks like a secret: `SECRET_PATTERNS`, for `run_bundle.py` and prometheus's `share`;
- processes, with psutil: `process_alive`, which also recognises a pid that another process has reused since the recorded start, and `this_process`;
- packages, with `packaging`: `package_name`, a requirement's normalised name.
