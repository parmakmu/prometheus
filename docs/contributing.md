# Contributing

How prometheus is built, how to test it, and the rules a change follows.

## Architecture

The Python scripts hold the logic; the two Pi extensions are thin.

```text
Pi command or tool → extension (TypeScript) → Python script → one JSON object
                                            ↘ pi-subagents call (only after a compile)
```

- **Scripts** ([`.pi/skills/agent/scripts/`](../.pi/skills/agent/scripts/)) validate, compile, record and commit. Each prints one JSON object; a refusal is `{"ok": false, "error": …}`. Every workflow script's result also carries `next`: the agent's state and the moves open from it. Scripts run and test without Pi.
- **Extensions** register the commands and tools, run a script with `pi.exec`, and format its JSON. One self-contained `index.ts` per agent: [prometheus's](../.pi/extensions/prometheus/index.ts) and [the agent template's](../templates/agent/.pi/extensions/agent/index.ts).
- **`wave_runner.js`** is the only JavaScript the compiler emits: a compiled wave is the wave as JSON data, followed by this runner unchanged.

The extensions run scripts with `$PI_AGENT_PYTHON`, else the nearest `.venv/bin/python` upwards (an agent inside prometheus uses prometheus's), else `python3`.

### What a launch looks like

The agent orchestrates a wave but never writes its code. `agent_compile` saves the wave as data plus the shared runner in one file, and the agent hands pi-subagents only that file's path (*from a real run*):

```json
{
  "async": true,
  "missionId": "f7a80a96-6b5a-473c-9768-9956d5e557fe",
  "agent": "/…/runs/2026-10-01-3/.compiled/w2.js"
}
```

When the wave ends, `agent_log_finish` gets the wave's id and returns a summary of the children, not their outputs:

```json
"children": {
  "discover":        { "status": "completed", "output": "…/work/discovery.md" },
  "discover-review": { "status": "completed", "output": null },
  "read-emails":     { "status": "completed", "output": null }
}
```

Every result also carries `next`, the agent's state and its legal moves (*from a real run*, between runs):

```json
{
  "state": "ready",
  "moves": ["agent_new_run", "/agent-revise", "/agent-improve"],
  "hint": "start a run, or change the plan on a branch"
}
```

**Learn more:** [What the Python scripts do](python-scripts.md) · [The state machine](state-machine.md) · [Design decisions](design.md)

## Where things are

| Path | Holds |
|---|---|
| [`.pi/extensions/prometheus/index.ts`](../.pi/extensions/prometheus/index.ts) | prometheus's commands and planning tools |
| [`.pi/skills/agent/SKILL.md`](../.pi/skills/agent/SKILL.md) | the planning procedure the model follows |
| [`.pi/skills/agent/references/`](../.pi/skills/agent/references/) | the model's references: plan schema, classification, critic contract, run protocol, replanning, changing |
| [`.pi/skills/agent/scripts/`](../.pi/skills/agent/scripts/) | the Python scripts |
| [`templates/agent/`](../templates/agent/) | what a new agent gets: its extension, agents, settings, role. Outside the skill, so agents don't carry a copy |
| [`.pi/agents/plan-critic.md`](../.pi/agents/plan-critic.md) | the critic; must stay identical to the template's copy (a test checks) |
| [`prometheus/cli.py`](../prometheus/cli.py) | the `prometheus` command: `init`, `doctor`, starting the planner (`pi -e … --skill …` in `~/.prometheus`) or an agent. The package (`pyproject.toml`) carries the repo's `.pi/` parts, `templates/` and `pyproject.toml` under `prometheus/`, in the same layout |
| [`prometheus/frozen.py`](../prometheus/frozen.py) | `load_agent`: a release unpacked into the cache (`git archive` + an empty repo), each run in its `runs/<id>/`, compiled with `compile_wave.py --frozen`, run by `pi -p "/prometheus-run <run>"` |
| [`prometheus/frozen_runner.ts`](../prometheus/frozen_runner.ts) | that command: launches the compiled plan through pi-subagents' RPC, writes `events.jsonl`, stops the run on SIGTERM, aborts any model turn and exits at the end (pi-subagents' completion message would start one) |
| [`prometheus/sharing.py`](../prometheus/sharing.py) | `install` (clone, refresh from this prometheus, review the agent's own code), `pull`, `share` (secret scan, push main and releases), `propose` (push a change branch; GitLab push options open a merge request), the catalog. Plain git: any remote works |
| [`prometheus/serve.py`](../prometheus/serve.py) | `prometheus serve`: one release as a FastAPI app; the run form is generated from the agent's interface (a function signature built per agent), runs are kept in memory and reopened from their folders (`call.json`) |
| [`prometheus/a2a.py`](../prometheus/a2a.py) | A2A 1.0 (HTTP+JSON binding) on `serve`'s runs: the Agent Card from the interface, messages (file and data parts) to runs, task states, artifacts as download links, SSE StreamResponses, the spec's `google.rpc.Status` errors; push notifications and the extended card not offered |
| [`catalog.yaml`](../catalog.yaml) | agents installable by name; shipped in the package |
| [`tests/`](../tests/) | the pytest suite; `PROMETHEUS_LIVE_TESTS=1` adds a run through your real Pi (no model) |
| [`ui/`](../ui/) | the web app: router (and its sandboxes), per-agent workers, the egress proxy, the page; see its [README](../ui/README.md) |

The skill's `SKILL.md` and `references/` are instructions for the model, not documentation for people. [`.claude/CLAUDE.md`](../.claude/CLAUDE.md) is the short brief for Claude Code: the settled decisions, one line each, linking here and to [Design decisions](design.md).

## Set up and test

```sh
uv sync                      # .venv with the scripts' packages, pytest, and prometheus itself (editable)
uv tool install -e .         # `prometheus` on your PATH, running this checkout
uv run pytest                # everything; extension tests skip without the Node deps
npm install && uv run pytest # adds type-checking and loading both extensions (Node >= 22, Pi installed globally with npm)
uv run ruff check
```

The suite covers plan validation and derived waves, params, map batching, scaffolding and export, compiled waves run against a mock of pi-subagents (real batch tool and gates, fake children), run state, git review paths, the state machine, and both extensions driven with Pi's real types.

[`tests/test_agent.py`](../tests/test_agent.py) is one continuous scenario (scaffold, compile, run, replan, merge, export): later tests use what earlier ones left on disk, so its tests run in file order. Don't add a test-randomizing plugin, put a new check where the state it needs exists, and rerun the whole file rather than one test from it.

The suite runs on all cores (pytest-xdist, `-n auto --dist loadgroup` in `pyproject.toml`; about 28 s instead of 71 s). Tests that share state are kept on one worker by an `xdist_group` mark: the whole of `test_agent.py`, the state-machine walk, the tests reading `test_run_finish.py`'s `finished` run, and `test_params.py`. A new test that uses a module-scoped fixture or state another test left belongs in its file's group. To debug one test serially: `uv run pytest -n 0 -k <name>`.

## Rules for a change

- **A validator rule** gets a broken-variant case in [`tests/test_validator.py`](../tests/test_validator.py).
- **A refusal a person can meet** gets a test that triggers it and an entry in [Troubleshooting](troubleshooting.md): a short heading, the message, its cause and the fix.
- **A script's JSON output that changes** is checked against both extensions, which format it.
- **A state or a move** goes in `agent_state.MOVES` and [the state machine](state-machine.md); [`tests/test_state_machine.py`](../tests/test_state_machine.py) walks every state and move and fails on a move the table doesn't list.
- **Behaviour that changes** is updated in these docs in the same change. Each fact lives on one page; others link to it.
- **Packages** are never installed by the model or a script: ask the person to run `uv sync`.

## Pi and pi-subagents facts relied on

What the code assumes about Pi and pi-subagents, with the versions it was checked on. Check here first when an upgrade breaks something.

### Pi (0.87.1)

- Extensions: `.pi/extensions/<name>/index.ts` (project, needs trust). `export default function (pi: ExtensionAPI)`; `@earendil-works/pi-coding-agent` and `typebox` are provided by Pi.
- `pi.registerCommand(name, { description, getArgumentCompletions?, handler(args, ctx) })`; `pi.registerTool(defineTool({ name, label, description, promptSnippet?, promptGuidelines?, parameters, execute(id, params, signal, onUpdate, ctx) }))` → `{ content, details }`, throw on failure.
- `pi.exec(cmd, args, { cwd, timeout, signal })` → `{ stdout, stderr, code, killed }`. `pi.sendUserMessage(text, {deliverAs?: "steer" | "followUp"})` triggers a turn; during one, without `deliverAs` it is dropped (the error goes to the extension runner, not the caller). `pi.sendMessage({customType, content, display}, {triggerTurn:false})` shows text. `ctx.cwd`, `ctx.ui.notify`, `pi.on("session_start" | "tool_call", …)`.
- Extensions load once per session: a changed extension takes a new session (or `/reload`).
- A session file is JSONL: a `session` header (`id`, `cwd`), then entries with `id`/`parentId`; the current leaf is the last entry, the branch its `parentId` chain (`SessionManager._buildIndex`, `getBranch`). The latest session is the newest file whose header `cwd` matches (`findMostRecentSession`).
- `.pi/APPEND_SYSTEM.md` appends to the system prompt. Project packages install into `<cwd>/.pi/npm/`.

### pi-subagents (0.73.1, with the `workflow` field from 0.74.0; source in `~/.pi/agent/npm/node_modules/pi-subagents`)

- `subagent({ async: true, mission|missionId, workflow: true | "<script path>" })`; the script uses `runs.run(key, opts)` / `runs.all([...])`, plain JS, top-level await, no nested async functions. 0.74.0 replaced `workflowScript`/`workflowScriptPath` with one `workflow` field: `true` runs the ```` ```js workflow ```` block written in the same reply; a string containing `/` loads that file relative to `cwd` (an absolute path is what the compiler emits; not live-verified); other strings are named workflows. Calls with the old names fail. (Path loading confirmed live in run 4 under 0.73.1 as `workflowScriptPath`; the `workflow` form is covered by the mock only.)
- A child result is `{ok, state, stopped?, detached?, error?, runId, output, outputReference, structuredOutput}`: no `status`. `runs.run` throws on a failed child; `runs.all` with config objects returns it as `{ok: false}`. The mock (`tests/mock-subagents.mjs`) mirrors both.
- Items: `key, label, agent, task, cwd, output, outputSchema, acceptance, context, resume`. `acceptance: {level, verify: [{id, command, output?:"json", schema?, timeoutMs?}], preserveStagedIndex, review}`; JSON verify + outputSchema is rejected; `resume` excludes `agent` and gates. Keyed cross-workflow resume: `{workflowRunId, key, latest}`.
- `preserveStagedIndex`: `git write-tree` in the child's cwd at launch and end, compared; failing to capture (not a repo) fails the child.
- Command-runner agents (`runner.type: external-cli`): stdin = instructions + task, stdout = output; a non-zero exit fails the child with stderr as its `error`; async only; no schema/gate/resume/fork. `runs.host` commands are only for extension-owned named workflows, not raw workflow scripts.
- Tool activation (Pi >= 0.86.1): a fresh session has `subagents_enable` active and `subagent` registered but inactive (pi-subagents 0.74: `toolActivation` in its `config.json`, default `auto`, starts models that can't add a tool mid-conversation with `subagent` already active; it doesn't use Pi 0.99's per-tool `exposure` for this, only for its own `model-only` tools). pi-subagents re-applies the recorded selection only on `session_start` and `session_tree`; its `before_agent_start` hook only keeps the loader active, so another extension's `setActiveTools([..., "subagent"])` from a command handler holds (not yet live-verified).
- Children are spawned with pi's `process.env` (so `.env` loaded by the extension reaches them).
- Async run record: `<tmp root>/async-subagent-runs/<runId>/status.json` (tmp root: `$PI_SUBAGENTS_TEMP_ROOT`, else `os.tmpdir()/pi-subagents-uid-<uid>`). `state`, `workflow.value` (the script's return value), `totalCost {costUsd, inputTokens, outputTokens}`; a workflow that fails before returning has `error` and `workflow.failureKind`; `pid` is the runner's process. `workflow.scriptDigest` is the sha256 of the script text (matched against `.compiled/<wave>.js` on 0.74.0; `compile_wave.unrecorded_launch` relies on it and fails open). A workflow run's children are in `workflowChildren.children` `[{childId, state, runId, agent}]`; the critic's child run id (what the model records, and what a second pass resumes) is there. `mission.close` takes missionStatus completed | failed | cancelled.
- In-process RPC for other extensions (`docs/extension-api.md`): emit `subagents:rpc:v1:request` `{version: 1, requestId, method, params}`, the reply on `subagents:rpc:v1:reply:<requestId>`. `spawn` takes the `subagent` tool's params (async only, `workflow` a path); its data's `details` has `runId` and `asyncDir`. Each child and the run's end post a completion notice that wakes the parent model; `quiet: true` silences success only for schedules. Live-verified (runs 2026-10-05-1 and -2).
- Missions: `<agent dir>/missions/projects/<sha256 of project root>/<mission id>.json`, each with `runs [{runId, …}]` (agent dir: `$PI_CODING_AGENT_DIR`, else `~/.pi/agent`; Pi's `getAgentDir`). A run's status.json does not name its mission.
- `mcp:` selections in an agent's `tools` (`mcp:server`, `mcp:server/tool`): with pi-mcp-adapter installed they resolve against its config; without it (the owner's setup) and on Pi >= 0.99 they resolve against Pi's built-in MCP and its `mcp.json` (a project's `.pi/mcp.json` only once trusted), whatever exposure the server has (default `codemode`, hidden from the model); the child gets only the selected tools, as direct ones. Waves are async, so servers an extension adds with `pi.registerMcpServer()` work too; a selected tool that doesn't register within 10 s fails the launch. (From pi-subagents 0.74 docs; not live-verified. Matters once a stage agent gets connector tools.)

## Writing these docs

The docs follow [Divio's four types](https://docs.divio.com/documentation-system/). Each page is one type and doesn't do the others' jobs:

| Type | Pages | It does | It doesn't |
|---|---|---|---|
| Tutorial | [Tutorial](tutorial.md) | teaches by doing, with a result at every step that works every time; tells the reader what they should see | explain, offer options, or assume knowledge |
| How-to guide | [How-to guides](how-to.md), [Troubleshooting](troubleshooting.md) | gives the steps to a goal the reader already has; the title says what it achieves ("How to …") | teach, or explain why |
| Explanation | [How prometheus works](how-it-works.md), [Design decisions](design.md) | gives context, the why, and alternatives, read at leisure | instruct, or list every option |
| Reference | [Commands and tools](commands.md), [Params, `.env` and secrets](params-and-secrets.md), and for contributors [The state machine](state-machine.md), [What the Python scripts do](python-scripts.md) | describes accurately and completely, in one consistent form | explain or opine |

- A page that needs another type's content links to it.
- Output in a doc is copied from a real run, and labelled as such. Where it can't be, don't invent it.
- A reason in an explanation comes from a recorded decision or an observed failure, as in [Design decisions](design.md). Otherwise leave it out.
- One word per concept: agent, plan, stage, wave, launch, child, run, gate, stage program, subagent, replan, change.
- A command or tool entry follows the form in [Commands and tools](commands.md): summary, syntax, where it runs and in which states, what it does, an example, its refusals, see also.
- Each fact lives on one page. Operator text comes first; implementation detail goes in a marked "For contributors" section, or on a contributor page.
- How prometheus works says *what* a thing is; Design decisions says *why*, starting from what went wrong. Don't explain a concept on both.
- Define a term where a reader first meets it, or link it to the glossary at the top of [How prometheus works](how-it-works.md#words-used-on-this-page).
- Write like a person explaining to a colleague: "you" for the reader, "we" for the people who built prometheus. Say why something matters to the reader before what it is, link sections with a sentence that says where we're going next, and admit trade-offs and past mistakes plainly. Vary sentence length; clipped fragments read as cold. Clarity still wins: split a sentence when it carries more than one colon or semicolon, or buries its point behind a qualification.
- Show, then tell: open a section with one line on what the thing does for the reader, then the real artifact (a command, a file, output from a run), then the explanation of what it shows. A number from a real run, or what went wrong, beats an abstract reason.
- Put tips, warnings and asides in GitHub callouts (`> [!TIP]`, `> [!NOTE]`, `> [!IMPORTANT]`), one short paragraph each, so the main text stays on its path.
- In Troubleshooting, a refusal the agent recovers from by itself goes under "Handled by the agent"; the other sections are for what needs the reader.
- Don't hard-wrap lines: one paragraph per line, so diffs stay small when a sentence changes.
