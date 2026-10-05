# Prometheus — brief for Claude Code

Prometheus is a standalone Pi agent that builds agents: `cd` here, run `pi`, and `/agent-new` turns a request into
`agents/<slug>/`, itself a full Pi agent (own chat, git repo, plan, programs, gates) that runs its work with
pi-subagents one wave at a time. This file is the settled decisions, one line each; the stories behind them are in
[`docs/design.md`](../docs/design.md), the architecture and the Pi facts relied on in
[`docs/contributing.md`](../docs/contributing.md). Don't re-litigate them; keep the docs true when behaviour changes.

Docs: `tutorial`, `how-to`, `troubleshooting` (every refusal a user meets), `commands`, `params-and-secrets`,
`how-it-works` (the *what*), `design` (the *why*), `state-machine`, `python-scripts`, `contributing` (Divio types);
`ui/README.md` for the web app. Model-facing rules: the skill's `SKILL.md` (planning), `references/run-protocol.md`
(read every run), `replanning.md` and `changing.md` (only when a route needs them), `plan-schema.md`.

## Owner's constraints (don't reopen without asking)

- Name **Prometheus**; commands keep the `agent-` prefix (Pi has `/new`, `/export`). Standalone: never installed
  into `~/.pi/agent/`.
- Runtime: Pi (built on 0.87.1; tests pass to 1.0.1; the owner runs Pi 1.0.1 + pi-subagents 0.75.0, since 0.74.0's
  runner breaks on Pi 1.0.0) + pi-subagents (0.73.1; tests pass to 0.75.0).
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
names) · replan (during a run, `run/<id>`) · change (`/agent-revise` or `/agent-improve`, `revise/…`, `improve/…`).
The skill is `.pi/skills/agent/`; the planner is Prometheus where it must be told apart from its agents.

## Architecture

Python scripts in `.pi/skills/agent/scripts/` hold all logic and print one JSON object (`utils.emit`; refusals
`utils.refuse`); runs via `runs.py`, YAML via `utils.read_yaml`/`write_yaml`, commits name their branch. The two
extensions are thin: run a script (`$PI_AGENT_PYTHON`, else the nearest `.venv`, else `python3`), format its JSON.
`templates/agent/` plus a copy of the skill make each agent (`scaffold_agent.py`), so it runs without Prometheus.
`wave_runner.js` is the only JS a wave carries; `batch_tool.py` is stdlib-only. Libraries: typer, pydantic
(`validate_plan.py`: shape, then one `Checker` method per rule group), pydantic-settings + python-dotenv, ruamel.yaml,
graphlib. Web app: `ui/router.ts` (routes; split into `common`, `sandbox` + `bwrap`/`podman`, `workers`, `files`,
`subagents`) + one `ui/worker.ts`
(Pi SDK) per user and agent, page in `ui/web`.

## Decisions (stories in design.md)

Planning
- Only `/agent-new` starts planning (`agent_scaffold` refuses otherwise).
- Kinds classified side_effect → decision → deterministic → judgment (`references/classify.md`).
- Unknowns get `resolve_by`; discovery stages make dependents `provisional`, which makes the run replan.
- A separate fresh-context critic; every finding gets a disposition; ≤2 passes per revision.
- Questions: draft first, ≤3 per round, ≤2 rounds, never param values; every fixed-answer question via
  `ask_user_question`, in the user's terms (no tool names, files, record numbers).

Doing the work
- Deterministic work = a program in `scripts/` + a gate in `gates/` (written while planning, never by the stage);
  with `run:` it runs as a command on `batcher`, a `<key>-fix` child only if it fails. A messy tail → leftovers file
  + small judgment stage.
- An agent's packages are in its `pyproject.toml` (`agent_add_package`, user-approved; `uv.lock` committed at
  create/refresh); gates and programs run with `uv run`; no PEP 723 headers.
- A producer is told who reads its output ("Used next by"), and its reviewer the same.
- Explicit acceptance on writers and gated stages: `{level, preserveStagedIndex: true, review: {required: false}}`.
- The same judgment on the same inputs is made once (`reuse_earlier`, fingerprint in `.compiled/<wave>.inputs.json`:
  items, input contents not places, subagent file, program, gates, settings, pi-subagents version; `reuse: false`).
- Cost is the plan's: `models: {default, review}`, a stage's `model`, `limits.max_cost_usd` (preflight `budget`;
  `--budget` raises the run's own), `limits.command_timeout_s`. Prometheus picks no model ids.

Launching
- Waves are derived (`validate_plan.derive_waves`); `ends_with` replan > review > approval > none.
- The compiler writes data (`const W = {…}` + `wave_runner.js`), passed as a `workflow` path, not text.
- Guards: ready plan to start; commit only a launchable wave; refuse an unrecorded launch (`scriptDigest`); hold
  after `review`; side effects only with `--approve`; a relaunch reuses unchanged completed children and map batches.
- A layer stops launching after a child fails with no `fix`. Map stages split/merge on `batcher`; a map's gate runs
  after the merge or on the reduce child.
- The orchestrator keeps no books: `agent_log_finish` reads `status.json`, returns a summary, `next_wave.preflight`,
  `mission_close`, `done_when`. Commands activate `subagent` before their prompt.
- `/agent-auto` runs waves in code through pi-subagents' RPC, handing over (as a `followUp`) only when the model or
  a person is needed; until then notice turns are told to do nothing and the run tools are refused (`AUTO_OWNS`).

Runs and changes
- Runs are folders; ≤1 active; relative params resolve inside the run; one mission per run.
- Each agent is its own git repo; only scripts commit, never during a wave; the extension blocks writing git.
- `main` changes only by a reviewed, squashed merge of `run/`, `revise/` or `improve/`; editing `plan.yaml`
  unreadies it (`plan_digest`, written only by `agent_record_critique`, in prometheus too) until the critic is recorded; a new run starts from `main`.
- `plan.yaml` is for people, in sections under `# ── ` banners; how it was planned is in `.plan/` (`unknowns`,
  `questions`, `critique`, `agents`, a file each). Scripts use `utils.read_plan`/`write_plan` (one plan in memory).
- Plan edits during a run go through `plan_edit.py` (locked: what ran, request, goal, done_when, params, inputs,
  outputs, agents). Round-trip YAML in house style; long strings folded; repeated keys are an error.
- The state machine is code (`agent_state.MOVES`); every tool result carries `next`.
- Params are typed (`path`/`text`/`secret`), values in `.env`, which is never loaded into Pi's environment (all
  children share it). A secret appears only in a program's `run:` or its gate, as `"${ENV}"`; the batcher adds the
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

## Status (2026-10-05)

`uv run pytest`: 312 tests (104 in `tests/test_agent.py`, which needs `npm install` and uv; the rest skip what
they can't run). Verified live on emails-to-excel: planning with critic and questions, discovery + replan,
approvals, programs written, committed, reviewed and run as commands, review/merge, reuse of programs, the
question tool, "Used next by", `/agent-auto` launching through the RPC (runs 2026-10-05-1/-2), a run API call with its own files and params (run -5). Not yet live: its
follow-up hand-over and hold, lost runners, `pi -p`; per-stage models; budgets; map stages and `batcher` batches;
`<key>-fix`; cross-wave resume; secret params; the guards and `next` (mock-tested). Sandboxes: bwrap and rootless
Podman 4.9 pass the escape tests here (WSL; no cgroup controllers, so no Podman limits); gVisor (Ubuntu's 2023
runsc) can't start rootless under WSL, so the router refuses `PODMAN_RUNTIME=runsc` here. Web app: seen working in the
browser up to 2026-10-02 (chat, planning, question cards, panels, status bar); today's additions not yet seen there.

Next: a plan with a map stage; a live run that fails and relaunches, stops at a review and records a real critic;
an agent run from the web app end to end; an agent calling another (API caller side); team setup
(model-key proxy, per-person Linux users, systemd limits).

## Testing

`uv sync && uv run pytest` (add `npm install` for the extension tests); `uv run ruff check`. `tests/test_agent.py`
is one scenario in file order; `tests/test_worker.py` runs a real worker per sandbox (a probe extension, no model); tests sharing state carry an `xdist_group` mark; `-n 0` to debug. A validator rule
gets a broken-variant case; a changed script output is checked against both extensions; a refusal a user can meet
goes in `docs/troubleshooting.md`; the two `plan-critic.md` copies stay identical.
