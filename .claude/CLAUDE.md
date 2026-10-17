# Prometheus — brief for Claude Code

Prometheus is a standalone Pi agent that builds agents: `cd` here, run `pi`, and `/agent-new` turns a request into
`agents/<slug>/`, itself a full Pi agent (own chat, git repo, plan, programs, gates) that runs its work with
pi-subagents one wave at a time. This file is the settled decisions, one line each; the stories behind them are in
[`docs/design.md`](../docs/design.md), the architecture and the Pi facts relied on in
[`docs/contributing.md`](../docs/contributing.md). Don't re-litigate them; keep the docs true when behaviour changes.

Docs: `tutorial`, `how-to`, `troubleshooting` (every refusal a user meets), `commands`, `python-api` (the package's
exports: `load_agent`, `Agent`, `Run`, `Result`, `PrometheusError`), `params-and-secrets`,
`how-it-works` (the *what*), `design` (the *why*), `state-machine`, `python-scripts`, `contributing` (Divio types);
`ui/README.md` for the web app. Model-facing rules: the skill's `SKILL.md` (planning), `references/run-protocol.md`
(its core, sent with `/agent-run`'s prompt), and its detours `asking.md`, `run-preflight.md`,
`run-failures.md`, `resolving.md`, `changing.md` (read only when it says), `plan-schema.md`.

## Owner's constraints (don't reopen without asking)

- Name **Prometheus**; commands keep the `agent-` prefix (Pi has `/new`, `/export`). Standalone: never installed
  into `~/.pi/agent/`. Installed as a command (`uv tool install git+<repo>`, GitLab) or run from a clone (`cd` + `pi`).
- Where this is going (2026-10-08, `TASKS.md`): three modes, interactive (as now), embedded (`load_agent`) and service
  (an image the tech team deploys); the last two run a frozen release as one launch with no orchestrator, through
  `pi -p "/<command>"` (verified: waits for an async handler, no model turn, RPC `spawn` works). Users are developers
  (TUI first; the chat web app is frozen); everyone has their own model keys. A release (`agent_release.py`, `prometheus
  release`) tags `v<major>.<minor>` on main when the plan needs no one at run time (no provisional, decision or
  approval stages, programs written, `compile_wave --frozen` compiles it); the tag's message holds the interface
  (params, outputs, side effects, decisions), and a change that can break callers needs `--breaking` (major+1; the
  flag alone raises it too). `load_agent` /
  `prometheus run` unpack a release into `~/.local/share/prometheus/releases/<slug>/<v>/` (`PROMETHEUS_RELEASES`;
  data, not a cache: + `git init`; no `.pi/extensions/`, so it can't be revised in place; `RELEASE.json`: the release
  record, agent, version, full commit, interface, source; `utils.agent_name` reads the agent's name from it, since the
  folder is named by version; a moved tag is refused) and run
  in its `runs/<id>/` (the batcher's relative path and `uv run` need the agent layout); the runner extension aborts any
  model turn and `process.exit`s at the end: in `pi -p`, pi-subagents' completion wake otherwise starts model turns.
  Sharing is plain git (`prometheus/share.py`; no GitLab API, no glab): install = clone + refresh from your own
  prometheus (what it owns becomes your trusted copy; no hash list) + review of the agent's own code; `install
  <source>@v1` (2026-10-16) freezes one release for a machine that only runs it (that tag only, reviewed, no repository,
  no refresh), and `load_agent` then picks from the installed `RELEASE.json` records; pull allows only
  local refresh commits on main; propose uses GitLab push options for the MR; the catalog is `catalog.yaml`.
  `prometheus serve <slug>[@v]` (FastAPI, a plain dependency): one agent per process (its environment holds only its
  secrets), a run form generated from its interface, paths only as uploads and outputs only in the run; no Docker or
  CI here: deploying it is the service team's. It also speaks A2A 1.0 (HTTP+JSON binding; `prometheus/a2a.py`, the
  owner chose A2A only, no MCP) on the same runs: card at `/.well-known/agent-card.json` (no token), file parts
  (`raw`) for path params, a data part for text params and `approve`, URL parts refused, outputs as artifact links.
  Released runs pause instead of refusing (A2A's input-required): `run_release.Run` drives waves in code (compile_wave
  `--frozen` reads `state.json`; one launch `all` when nothing asks), stops at a decision or an unapproved side
  effect, `Run.answer()` / `POST /runs/<id>/answer` / an A2A message with the taskId resumes it, in any process.
  The Agent Card carries the release's interface as extension `urn:prometheus:a2a:interface:v1`. Agents call agents
  with `pattern: a2a` (an `a2a:` block: url, files, params, approve, token): batcher's `a2a` op, no model;
  approving the callee's side effects makes the stage a side effect. Released runs reuse each other (`reuse_earlier`
  reads `state.json` too: `run_folders.list_released_runs`, `stages_done`); `run_history.py prune` / `prometheus prune` /
  `serve --keep` delete old runs' files (and orphaned pi-subagents mission files), keeping their history; a release's
  newest failed runs stay whole (`--keep-failed`). A failed released run is fixed from a bundle (2026-10-16; not an
  `/agent-doctor`): `prometheus bundle` / `GET /runs/<id>/bundle` (`run_bundle.py make`: the run, pi-subagents' records via
  `utils.launch_records`, children's sessions; secrets → `${NAME}`; caller files only with `--with-files`) →
  `/agent-improve <bundle.tgz>` (`run_bundle.py add` → `runs/imported/`, never reused from; `session_digest` `imported`) →
  `prometheus run <slug>@<branch> --bundle <file>` (`run_release.load_build`: any non-`v` ref, version `build`) → release.
  Also `Run.bundle()` (embedded), and `/agent-bundle` for an agent's own run in a copy without prometheus (`kind: run`,
  version `r<rev>`, its sessions during the run). On the runner machine, diagnosis only: `prometheus runs <slug> [run]`
  (`run_bundle.py show`) and `prometheus doctor <slug>[@v]` (secrets in the environment, `uv sync --locked --dry-run`, model
  stages; no git identity needed).
- Agents use pi-subagents' `projectRootResolution: "git-root"` (template settings.json): a child's session artifacts
  create `runs/<id>/work/.pi/`, and with `nearest` the next child resolved project agents there ("Unknown agent: reader",
  first live map stage, 2026-10-09; run -2 then passed: split, 2 reader batches, merge, $0.0022).
- A model stage's brief is `instructions` (in the task after the title); `title` is a label, `done_when` the
  done-condition. The validator warns on a model stage without instructions.
- Stage fields are named for people who edit `plan.yaml` by hand (2026-10-09, unpublished, so no old names):
  `id`, `kind: {value, reason}`, `after`, `reads`, `writes`, `done_when`, `gate`, `review` (no `verify`),
  `program: {file, args}`, `map: {over, glob, batch}`; a side effect always asks (no `approval:`). An unknown stage
  field is an error. `key` stays only where pi-subagents means a child. `plan.schema.json` (the skill's, generated by
  `plan_validate.plan_json_schema()` from the models, a test keeps it current) is named on plan.yaml's second line for
  editors; `prometheus check <slug>` runs the validator.
- Runtime: Pi 1.3.0 + pi-subagents 0.79.0 (the owner's, 2026-10-17), the least `prometheus doctor` accepts
  (`cli.MIN_VERSIONS`); no code for older versions: on an upgrade, adapt and raise the floor.
- Plans are YAML. Commands and tools live in one self-contained `index.ts` per agent: no re-exports, no prompt
  templates. Keep it simple: the owner pushes back on indirection and duplicate folders.
- Python 3.14 (a refresh raises a lower `>=` floor, keeps the agent's own packages); typer, pathlib (`os` only for
  `chdir`/`environ`/`getuid`), pytest. Never install packages: ask the user to run `uv sync`.
- Contributor notes stay in `.claude/` (Pi loads root `CLAUDE.md`/`AGENTS.md` into every turn); roles go in
  `.pi/APPEND_SYSTEM.md`, an agent's guide for people in its `README.md`.

## Names (one word per thing)

agent (what Prometheus builds; "workflow" only for pi-subagents' own: workflow script, `workflowRunId`, …) · plan
(`plan.yaml`) · stage · wave (stages launched as one call; derived) · launch (one attempt at a wave) · child · run
(`runs/<id>/`; pi-subagents' id is `subagent_run_id`) · gate (`gates/`) · stage program (`scripts/`) · check
(validating the plan) · subagent (a profile: builtin or `.pi/agents/`; the plan's `agents:`/`agent:` keep their
names) · resolve (during a run, `run/<id>`) · change (`/agent-revise` or `/agent-improve`, `revise/…`, `improve/…`).
The skill is `.pi/skills/agent/`; the planner is Prometheus where it must be told apart from its agents.

## Architecture

Python scripts in `.pi/skills/agent/scripts/` hold all logic and print one JSON object (`utils.emit`; refusals
`utils.refuse`); runs via `run_folders.py`, YAML via `utils.read_yaml`/`write_yaml`, commits name their branch. The two
extensions are thin: run a script (`$PI_AGENT_PYTHON`, else the nearest `.venv`, else `python3`), format its JSON.
`templates/agent/` plus a copy of the skill make each agent (`agent_scaffold.py`), so it runs without Prometheus.
`wave_runner.js` is the only JS a wave carries; `run_batcher.py` is stdlib-only. Libraries: typer, pydantic
(`plan_validate.py`: shape, then one `Checker` method per rule group), pydantic-settings + python-dotenv, ruamel.yaml,
packaging (requirements, versions), psutil (`process_alive`: a reused pid is not the process recorded), graphlib. Web app: `ui/router.ts` (routes; split into `common`, `sandbox` + `bwrap`/`podman`, `workers`, `files`,
`subagents`) + one `ui/worker.ts`
(Pi SDK) per user and agent, page in `ui/web`. `prometheus/cli.py` is the `prometheus` command (typer + rich): `init`,
`doctor`, the planner (`pi -e <ext> --skill <skill> --append-system-prompt <role>` in `~/.prometheus`, whose `.pi/` has
only settings.json and a link to `plan-critic.md`) and `prometheus <slug>`; the planner extension finds its root from
its own file and the agents from `PROMETHEUS_AGENTS` (else `<root>/agents`). The wheel keeps the repo layout under
`prometheus/`.

## Decisions (stories in design.md)

Planning
- Only `/agent-new` starts planning (`agent_scaffold` refuses otherwise).
- Kinds classified side_effect → decision → deterministic → judgment (`references/classify.md`).
- Unknowns get `resolved_by`; discovery stages make dependents `provisional`, which makes the run resolve.
- A separate fresh-context critic; every finding gets a disposition; ≤2 passes per revision.
- Questions: draft first, ≤3 per round, ≤2 rounds, never param values; every fixed-answer question via
  `ask_user_question`, in the user's terms (no tool names, files, record numbers).

Doing the work
- Deterministic work = a program in `scripts/` + a gate in `gates/` (written while planning, never by the stage);
  with `program.args` it runs as a command on `batcher`, an `<id>-fix` child only if it fails. A messy tail → leftovers file
  + small judgment stage.
- An agent's packages are in its `pyproject.toml` (`agent_add_package`, user-approved; `uv.lock` committed at
  create/refresh); gates and programs run with `uv run`; no PEP 723 headers.
- A producer is told who reads its output ("Used next by"), and its reviewer the same.
- Explicit acceptance on writers and gated stages: `{level, preserveStagedIndex: true, review: {required: false}}`.
- The same judgment on the same inputs is made once (`reuse_earlier`; fingerprint: items, input contents not places,
  subagent file, program, gates, settings, pi-subagents version; `reuse: false`; a map stage whole: its items' contents
  and batch size, a reused map is one reused layer item; per item is TASKS 38; outputs anywhere inside the run, not
  only work/, are copied). A wave all of whose stages are reused is recorded at compile, not launched
  (`nothing_to_launch`; `run_steps.record_without_launch`; `launched: false` with agent_finish_wave's result). A launched wave is two files in
  `.compiled/`: `<wave>.js` (its data, `const W`, and the runner) and `<wave>.json` (the call, `inputs` = fingerprints,
  `result` once it ended). No fallbacks for older formats: we are still developing.
- Cost is the plan's: `models: {default, review}`, a stage's `model`, `limits.max_cost_usd` (preflight `budget`;
  `--budget` raises the run's own), `limits.command_timeout_s`. Prometheus picks no model ids.

Launching
- Waves are derived (`plan_validate.derive_waves`); `ends_with` resolve > review > approval > none.
- The compiler writes data (`const W = {…}` + `wave_runner.js`), passed as a `workflow` path, not text.
- Guards: ready plan to start; commit only a launchable wave; refuse an unrecorded launch (`scriptDigest`); hold
  after `review`; side effects only with `--approve`; a relaunch reuses unchanged completed children and map batches.
- A layer stops launching after a child fails with no `fix`. Map stages split/merge on `batcher`; a map's gate runs
  after the merge or on the reduce child.
- The orchestrator keeps no books: `agent_finish_wave` reads `status.json`, returns a summary, `next_wave.preflight`,
  `mission_close`, `done_when`. Commands activate `subagent` before their prompt.
- `/agent-auto` runs waves in code through pi-subagents' RPC, handing over (as a `followUp`) only when the model or
  a person is needed; until then notice turns are told to do nothing and the run tools are refused (`AUTO_OWNS`).
  A notice turn is a user prompt starting "Inspect subagent updates above." or a run a `subagent…` custom message
  started (Pi >= 1.2: `before_agent_start` has `messageType`); a wave /agent-auto recorded stays
  refused to agent_finish_wave after the hand-over (a late notice, run 2026-10-09-4).

Runs and changes
- Runs are folders; ≤1 active; relative params resolve inside the run; one mission per run.
- Each agent is its own git repo; only scripts commit, never during a wave; the extension blocks writing git.
- `main` changes only by a reviewed, squashed merge of `run/`, `revise/` or `improve/`; editing `plan.yaml`
  unreadies it (`plan_digest`, written only by `agent_record_critique`, in prometheus too) until the critic is recorded; a new run starts from `main`.
- `plan.yaml` is for people, in sections under `# ── ` banners; how it was planned is in `.plan/` (`unknowns`,
  `questions`, `critique`, `agents`, a file each). Scripts use `utils.read_plan`/`write_plan` (one plan in memory).
- Plan edits during a run go through `plan_edit.py` (locked: what ran, request, goal, done_when, params, inputs,
  outputs, agents). Round-trip YAML in house style; long strings folded; repeated keys are an error.
- The state machine is a Quint-style spec in code (`agent_state.py`): `State`, one action per move (guards, then the
  states it leads to), `INVARIANTS`; each script calls `require(agent_dir, <action>)` first, so a refusal and `next`
  come from one definition. Tests: model check of the actions, a walk of the real scripts, conformance (a real move
  lands where its action said). "Has had a run" is `agent_repo.has_run`: `runs/`, else main's history (the first
  run's commit, merges; no forced empty commit: `prometheus pull` allows only refreshes on main).
- Params are typed (`path`/`text`/`secret`), values in `.env`, which is never loaded into Pi's environment (all
  children share it). A secret appears only in a program's `args` or its gate, as `"${ENV}"`; the batcher adds the
  ones a command names (`secrets_for`). Gate values are shell-quoted. Judgment over outside content runs as `reader`
  (no shell; the validator warns otherwise).
- Prometheus version `<version>+<commit>[.dirty] <hash>` in the agent's skill `VERSION`; only the hash decides
  staleness; no tags.

Web app
- One worker process per user, agent and conversation, each in a sandbox at the same paths: bubblewrap, or
  rootless Podman (`SANDBOX=podman`; gVisor via `PODMAN_RUNTIME`): `sandbox.ts` describes it once (`Spec`),
  `bwrap.ts` and `podman.ts` run it.
  Not Firecracker/E2B: no shared folders, and the remaining risk is inside an agent. With Podman, the internet by
  allow-list: workers on an internal network, the only way out `ui/egress_proxy.py` (CONNECT only, a token per
  agent): model providers + PyPI + hosts you allowed (`<own>/network/<agent>.json`, outside sandboxes); the plan's
  `network:` is a request; refusals offered in the web app. Nothing a sandbox can write may run outside it: Pi state read-only (but
  what Pi writes; `settings.json` a per-user copy), an agent's `.pi/` and `.git/config|hooks` read-only to it, the
  router runs every command on an agent in its sandbox and only Prometheus's scripts, uploads refuse links, a login
  proxy needs `PROXY_SECRET`. Each has an escape test in `tests/test_router.py`.
- AG-UI to assistant-ui, no CopilotKit; dialogs become interrupts; the UI context defines every method.
- Panels: the worker renders frames and records pi-tui widgets; the page draws native controls.
- Who may call: the web app's origin, loopback hosts, a token (header only) or a login proxy's user header.
- Sessions ("conversations" until 2026-10-05) live in the folder (`.pi/sessions`); a refresh rewrites their `cwd` header.
- The page loads history before Pi: the router answers the first sync from the session file (`ui/history.ts`, the
  one snapshot builder); hovering warms a worker; a refreshed agent's idle worker restarts (not during a run).
- Terminal via `pty_relay.py` (stdlib), not `node-pty`.
- An agent is called on its own through its run API (`ui/api.ts`, `/a/<slug>/api`): a call is a run with its own
  values (`given_params` on the run, over `.env`, never secrets; `preapproved`), in a session of its own; questions
  wait in `needs_input` for the caller; one at a time per agent, queued; keys per agent, hashed, outside sandboxes.
- A sandbox's `~/.pi/agent` is its own tmpfs with each entry of yours mounted in (Pi locks beside `trust.json`).

- Review fixes (2026-10-09): `install` refreshes with `--no-lock` until you confirm (uv lock may build the agent's
  packages); `share` scans the history it pushes too; serve's and A2A's async routes run blocking work in a thread pool;
  a run records its driver's pid (`state.json` `driver`), so a run whose process died ends as an error; `Run.answer`
  and `cancel` hold a file lock (`runs/<id>/.lock`); a cancel is seen before and right after a launch, from any process;
  the batcher's a2a op follows links (endpoint, artifacts) only on the plan's origin; uploads are capped as they
  arrive (`LimitBody`); an empty serve token is none.

## Status (2026-10-05)

`uv run pytest --all`: 396 tests (103 in `tests/test_agent.py`, which needs `npm install` and uv; the rest skip what
they can't run). Verified live on emails-to-excel: planning with critic and questions, discovery + resolve,
approvals, programs written, committed, reviewed and run as commands, review/merge, reuse of programs, the
question tool, "Used next by", `/agent-auto` launching through the RPC (runs 2026-10-05-1/-2), a run API call with its own files and params (run -5). Not yet live: its
follow-up hand-over and hold, lost runners, `pi -p`; per-stage models; budgets; map stages and `batcher` batches;
`<id>-fix`; cross-wave resume; secret params; the guards and `next` (mock-tested). Sandboxes: bwrap and rootless
Podman 4.9 pass the escape tests here (WSL; no cgroup controllers, so no Podman limits); gVisor (Ubuntu's 2023
runsc) can't start rootless under WSL, so the router refuses `PODMAN_RUNTIME=runsc` here. Web app: seen working in the
browser up to 2026-10-02 (chat, planning, question cards, panels, status bar); today's additions not yet seen there.

Next: a plan with a map stage; a live run that fails and relaunches, stops at a review and records a real critic;
an agent run from the web app end to end; an agent calling another (API caller side); team setup
(model-key proxy, per-person Linux users, systemd limits).

## Testing

`uv sync && uv run pytest` runs the fast tests (~6 s); `uv run pytest --all` adds the end-to-end files
(`conftest.SLOW`, ~50 s; before a commit; a slow file named on the command line runs too). Skill scripts run
in-process (`--subprocess`: as real processes); the state-machine walk is cached by a hash of what it depends on
(`--cache-clear` redoes it). Add `npm install` for the extension tests; `uv run ruff check`. `tests/test_agent.py`
is one scenario in file order; `tests/test_worker.py` runs a real worker per sandbox (a probe extension, no model); tests sharing state carry an `xdist_group` mark; `-n 0` to debug. A validator rule
gets a broken-variant case; a changed script output is checked against both extensions; a refusal a user can meet
goes in `docs/troubleshooting.md`; the two `plan-critic.md` copies stay identical.
