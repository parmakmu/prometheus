# Design decisions

Why prometheus works the way it does, for contributors: read the entry before you change a rule, so you know what it prevents. What each part does is in [How prometheus works](how-it-works.md); the decisions in one line each are in [`CLAUDE.md`](../.claude/CLAUDE.md).

Most rules exist because something simpler was tried on a real agent and went wrong, so each entry gives the rule, what happened without it, and why this solution. Examples come from live runs of emails-to-excel (emails to a spreadsheet) and email-summaries; costs are one run's model spend in US dollars. The lesson behind most of them: **a model is good at judgment and poor at bookkeeping**, so whatever a rule can decide goes into code, where it is tested and costs nothing.

## The model and the code

### The model judges; scripts keep the books

**The rule.** Python scripts make every decision a rule can make (plan validity, the next wave, allowed moves, run records, commits). The extensions are thin: a tool runs a script and formats its JSON.

**Without it.** In an early run, 12 of the orchestrating session's 26 turns were bookkeeping: looking up how a wave ended, rereading files gates had checked, finding `done_when` lines for the report.

**Why this way.** Now `agent_finish_wave` returns the children's summary, the next wave's approvals and the `done_when` lines in one result. And a script can be tested through every state; a model's behaviour can't.

### The orchestrator gets paths and summaries, not text

**The rule.** A compiled wave goes to pi-subagents as a file path; a finished wave comes back as a summary, not the children's outputs.

**Without it.** The chat session cost about as much as the children it launched ($0.021, $0.0135), and retyping each compiled wave was half its output tokens. Turning on the `subagent` tool cost two more turns and a cache rewrite (about $0.003), so the commands now turn it on before their prompt.

### The state machine is a spec in code

**The rule.** Where an agent stands and which moves it allows is defined once, in `agent_state.py`, written like a [Quint](https://github.com/quint-co/quint) spec: `State` holds the facts, each move is an action that checks its guards and returns the states it leads to. Every script calls `require(agent_dir, <action>)` first, and every result lists the moves whose guards pass as `next`. See [the state machine](state-machine.md).

**Without it.** First the model worked out the state itself. Then a table of legal moves sat beside each script's own guards: three copies of the rules (table, guards, docs) that drifted. On a branch the agent hadn't made, the state read `ready` and offered a run the script refused; a small model found `next` wrong in 8 of 22 configurations. And "has it had a run?" read `runs/`, which git doesn't carry, so a clone arrived with `main` open to direct edits.

**Why this way.** A refusal and `next` come from the same function, so they can't disagree. "Has had a run" also reads `main`'s history. Three tests hold it: a model check of every reachable state against the invariants, a walk of the real scripts including hand edits and branch switches, and a conformance check that a real move lands where its action said. The cost: an action's effect restates its script, and conformance covers only the moves the walk tries.

### Choices are asked with the question tool

**The rule.** Every question with a fixed set of answers goes through `ask_user_question`; open values, such as paths, are asked in chat.

**Without it.** Only approvals used it, and a run asked whether to start, or what to do after a failure, in plain chat, where replies are easy to fumble.

## Planning

### Only a command starts planning

**The rule.** Only `/agent-new` starts planning; `agent_scaffold` refuses otherwise.

**Without it.** "I want to create documentation with docs in md", typed as a message, created an agent nobody asked for.

### Stages are classified by what can't be undone

**The rule.** Kinds are tried in the order side effect, decision, deterministic, judgment, so a stage that writes to a service is a side effect even when its work is mechanical.

**Why this way.** A side effect is the one thing a relaunch can't take back, so it must never hide behind another kind. Its approval is enforced in the compiler, not left to the model.

### Unknowns are staged, not guessed

**The rule.** Each unknown names how it will be settled: the planner, a read-only probe, you, or a discovery stage, whose dependents stay provisional until the run resolves them.

**Without it.** A planner made to fill in every stage before seeing data invents what it can't know.

### The critic is a separate agent

**The rule.** A fresh-context agent reviews the plan as a file; every finding gets a disposition; the result is tied to the plan by a digest, so a plan edited afterwards can't run unreviewed. At most two passes, the second checking the fixes.

**Without it.** A model grading its own plan misses what it missed writing it. Until 2026-10-05 a new agent's first plan was written in prometheus's chat, with no tool to record the critic, so the planner wrote the result and `status: ready` itself; nothing checked it. Now prometheus records the critic with the same tool.

### Few questions, asked late

**The rule.** The planner drafts, lets the critic run, then asks only where a wrong guess would change the stages, a side effect, a source or destination, or what counts as done: three questions a round, two rounds. Never param values, which belong to the machine that runs the agent.

**Why this way.** Questions asked before a draft are guesses about what matters.

### The plan is for people; how it was planned is kept beside it

**The rule.** `plan.yaml` holds what the agent does, in banner sections. How it was planned (unknowns, questions, critique, the subagents seen) is in `.plan/`, a file per part; scripts read both as one plan.

**Without it.** Someone opening `plan.yaml` to see what their agent does had to read past the planning record.

### Plan fields are named for the people who edit them

**The rule.** Stages say `after`, `reads`, `writes`, `done_when`, `gate`, `review`, `program: {file, args}`. An unknown field is an error, and the plan's second line names its generated JSON schema.

**Without it.** The first names (`needs`, `consumes`, `out`, `verify`) had to be looked up, and an ignored misspelling silently changes nothing.

## Doing the work

### Deterministic work is a program plus a gate

**The rule.** A deterministic stage names a program in `scripts/` and a check in `gates/`, both written while planning. The program runs as a command on the `batcher`, with no model; if it fails, an `<id>-fix` child gets the error.

**Without it.** A model child rewrote the extraction program every run (about 70% of the children's output). With the program fixed, two children still spent 35 s running programs that take a second; as commands, they take 0.1 s, and the run fell from 2 min 22 s to about a minute.

### An agent's packages live in one place

**The rule.** Programs and gates use the packages in the agent's `pyproject.toml` and `uv.lock`; the validator rejects an import it doesn't list. Only you add one, through `agent_add_package`, which asks.

**Without it.** Told to use "what is installed", models hand-wrote 100-line XLSX readers instead of using openpyxl.

**Why this way.** Dependencies are reviewable in one file (so no `# /// script` headers), and a package is code running on your machine, so it's your call. The block on `uv add` and `pip install` is in the extensions, since a prompt rule lets a model install first and mention it later. Creating or refreshing runs `uv lock`, so a run doesn't write the lock mid-wave and leave a branch to review.

### A producer is told who reads its output

**The rule.** A stage whose output others read gets a "Used next by" section, and so does its reviewer.

**Without it.** `discover`, told only to "report available fields", left out `Cc` (in one email of eight); its reviewer knew `read-emails` needs it and blocked the wave. They judged one report by different standards.

### Acceptance is explicit

**The rule.** Every writer and gated stage carries `{level, preserveStagedIndex, review}`.

**Without it.** The first real run failed with correct output: pi-subagents inferred that a background writer needed an independent review and no staged files.

## Reuse

### The same judgment on the same inputs is made once

**The rule.** Before launching a model stage without side effects, the compiler fingerprints it: its instructions and review, the contents of what it reads, its subagent file, program, gates, the agent's settings and pi-subagents' version. If an earlier run finished it with the same fingerprint, its outputs are copied instead, its review skipped. A wave with nothing left to run is recorded at compile (`launched: false`) instead of launched.

**Without it.** With programs as commands, all of emails-to-excel's model cost was `discover` reading the same eight emails each run. An empty wave still took three turns to launch and record.

**Why this works.** A child may use only its listed inputs, so its output depends only on what it's told and reads. Each part of the fingerprint was found on a real agent: contents, not paths (moving the emails cost a model run); settings and pi-subagents' version (both can change a builtin's model); outputs anywhere in the run, not only `work/`. A map stage counts whole; a `pattern: map` one also item by item: the merge stores each item's record, and the next split leaves out stored items, so one new email costs one email.

**Never reused.** Side effects; stages that read an app, a URL or through a connector (outside any file the fingerprint covers); outputs written outside the run.

## Launching waves

### Waves are derived, not written

**The rule.** The validator derives waves from the stages: the work between two points where a person or the agent must act.

**Why this way.** A workflow can't pause midway for a person, and a plan that stated its waves could state them wrong.

### The compiler writes data, not code

**The rule.** A compiled wave is its data (`const W = {…}`) plus one hand-written `wave_runner.js`.

**Without it.** Python built each wave's JavaScript from strings, with two bugs to show for it. Passing the data as `args` fails: pi-subagents caps them at 16 KiB, and a first wave was 19.6 KB.

### A wave is compiled when it launches

**The rule.** Each run compiles its own waves into `runs/<id>/.compiled/`, beside their results, with no model.

**Why not compile once.** Most of a compiled wave belongs to one launch: the run's folder, its param values, approvals and decisions, which stages are done or reused. And a launch is recognised by its script's digest. A release is checked by compiling every wave as a released run would, but its runs still compile their own.

### Guards sit around the launch

**The rule.** A run starts only from a ready plan; only a launchable wave is committed; an unrecorded launch is refused; a launch must come from the commit it was compiled from; after a `review`, the next wave waits; side effects need approval.

**Why this way.** A session can end at any moment and a model can skip a step, so each point where that would leave a run broken has a guard.

### Children find the agent's subagents from its git root

**The rule.** Agents set pi-subagents' `projectRootResolution: "git-root"`.

**Without it.** In the first live map stage, a child left `runs/<id>/work/.pi/` behind, and the next child looked for project agents there: "Unknown agent: reader". With the setting, the run passed for $0.0022.

### Waves can run without the model

**The rule.** `/agent-auto` compiles, launches and records waves in code through pi-subagents' RPC, waking the model only for a person or judgment: approvals, decisions, reviews, missing values, the budget, failures, resolving, the report.

**Without it.** With programs as commands, orchestration cost more than the work ($0.0135 against $0.0105), mostly mechanical turns. The first live run found that each completion notice starts a model turn and that Pi drops a message sent during one, so the hand-over is now a queued follow-up and notice turns are told to do nothing.

### Cost and failures are bounded by the plan

**The rule.** A plan names its models (`models:`, a stage's `model:`); prometheus picks none, since names depend on your provider. `limits.max_cost_usd` asks before the next wave; a yes raises that run's limit. A layer stops launching after an unrepairable failure, and a relaunched map keeps its completed batches.

## Runs and changes

### Runs are folders, and the plan holds no run state

**The rule.** Each run lives in `runs/<id>/`; relative paths resolve there; at most one run is active.

**Why this way.** The plan must be reusable on new input.

### `main` changes only by a reviewed merge

**The rule.** A run commits on `run/<id>`, a change on `revise/` or `improve/`; you merge (one commit on `main`), keep or discard. A new change waits while another branch is open.

**Why this way.** What a run discovers is a proposal, not a silent change to every later run, and `main` reads as one entry per reviewed change. A new run is the exception: it starts from `main` and keeps an open branch for later, since the web app opens a `revise/` branch for every saved edit; the run reads only `main`'s reviewed plan either way.

### Only scripts commit, and never during a wave

**The rule.** The model runs no git command that changes the repository; scripts commit only between waves.

**Why this way.** pi-subagents compares staged files around each writing child, and a commit mid-wave fails that check.

### Plan edits during a run go through a tool

**The rule.** During a run, the plan changes only through `agent_resolve` and `agent_record_critique`, which refuse the [locked fields](how-it-works.md#changing-the-plan-mid-run) and save round-tripped YAML, so only edited lines change. Repeated keys are an error.

**Without it.** The first resolve edited the file directly and misfired, and a loader that keeps the last repeated key lets a dropped `- id:` merge two stages silently.

## Secrets and sandboxes

### Params are typed, and values live in `.env`

**The rule.** A param is `path`, `text` or `secret`; values live in `.env`, which the model can't read; a secret appears in the plan only as `${ENV_NAME}`, and the agent asks you to set it rather than for its value.

**Without it.** An earlier `.pi/params.yaml` made every value a path and would have put secrets into chat, compiled waves, tasks and logs.

### Secrets reach only programs, and readers have no shell

**The rule.** `.env` is never loaded into Pi's environment. A secret appears only in a program's arguments or its gate; the batcher gives each command only the secrets it names. Judgment over outside content runs as `reader`, with no shell.

**Without it.** All of a run's children share one environment, and `.env` was loaded into it, so a child reading an email had every secret within reach of an instruction hidden in it.

### Podman on a server, bubblewrap on your machine

**The rule.** The web app runs each worker in bubblewrap (your machine) or rootless Podman (a server), built from one description and passing the same escape tests. Podman adds limits, its own loopback, and gVisor where installed.

**Why not a VM per agent.** Firecracker (or E2B) can't share folders, and prometheus is built on folders the router reads at the same paths. And the risk that remains is inside an agent, whose children share one sandbox anyway.

### The internet, by allow-list

**The rule.** With Podman, workers have no route out except a proxy allowing the model providers, PyPI and the hosts you allowed, kept in your folder outside every sandbox; the plan's `network:` is a request. A refused host shows with an Allow button.

**Why this way.** An agent that reads outside content and reaches any host can be told to send it anywhere, and an agent can edit its own plan. **Left open:** the proxy sees only host names, and the model providers stay reachable as a channel.

## The web app

### One worker process per agent

**The rule.** One Pi process per user, agent and conversation, each sandboxed to its own agent's folder.

**Why this way.** Agents mustn't see each other's folders, and a session's children share its environment. A sandbox is only as good as what it can leave for something outside to run, so these are closed, each with a test: your Pi state is read-only (but what Pi writes), as are an agent's own extension, skill copy, settings and profiles, which your terminal would load unsandboxed; the router runs every command on an agent inside its sandbox (a `.git/config` can name a command `git diff` runs); uploads refuse links; a login proxy needs a secret, since sandboxes reach the router's port.

### Who may call the router

**The rule.** Only the web app's origin and loopback hosts, with a token in a header (as Jupyter prints one) or a login proxy's user header.

**Without it.** An early version allowed any origin, so any website could drive an agent with bash; and a token in a URL ends up in logs.

### The page speaks AG-UI directly

**The rule.** The worker serves Pi as AG-UI to assistant-ui; dialogs become interrupts, so a question is a card. CopilotKit needs its own runtime in between.

### Panels are drawn, not streamed

**The rule.** The worker records the pi-tui widgets of a panel's frame; the page draws native controls. Streaming a terminal's bytes showed a black card.

### The router hides Pi's start

**The rule.** The first history request is answered from the session file (about 30 ms), hovering an agent starts its worker, and a refreshed agent's idle worker restarts.

**Why this way.** A worker takes about 2.3 s to start, and Pi loads an extension only at session start, so after a refresh a run once used half-hour-old code.

## Running agents from code

### A release is a plan that needs no one

**The rule.** Embedded (`load_agent`) and service runs have no orchestrating model and nobody at the keyboard, so only a plan with no provisional stage, all programs written and every wave compiling is released: a tag `v<major>.<minor>` on `main` whose message holds the interface (params, outputs, side effects, decisions). A change that can break callers needs `--breaking`.

**Why the copy can't be changed.** A release runs from one unpacked copy per version (the batcher and `uv run` need the agent's layout), shared by all its runs, so an edit there would change `v1.0` unreviewed, and never reach the agent. The copy leaves out the extension, records itself in `RELEASE.json`, and a moved tag is refused. The runner aborts any model turn, since a completion notice would start one.

### A machine that only runs an agent installs a release

**The rule.** `prometheus install <source>@v1` fetches that tag only, shows the agent's own code, and keeps no repository or refresh.

**Without it.** A server that only runs the agent needed its whole repository, history and tooling to change it.

**Why this way.** There the unpacked releases hold the runs, so they are data, not a cache: `~/.local/share/prometheus/releases/<slug>/<version>/` (`PROMETHEUS_RELEASES`), each with its `RELEASE.json`, which `load_agent` picks from when there's no repository. An upgrade is another install beside the old one.

### Released runs pause instead of refusing

**The rule.** At a decision or an unapproved side effect, a released run waits (A2A's `input-required`); `Run.answer()`, `POST /runs/<id>/answer` or an A2A message resumes it from any process, since its state is in `run.yaml`. A run records its driving process, so one whose process died ends as an error.

**Without it.** A decision stopped a released run with nothing to resume, and a run whose server restarted waited forever, even after its pid was reused.

### A failed release is fixed where the agent is developed

**The rule.** `prometheus bundle` packs what a failed run did; `/agent-improve <bundle>` turns it into fixes on a branch you review. On the runner, `prometheus runs` and `prometheus doctor` diagnose only: fixes go to the machine's setup or the agent's repository, never the release. A copy without prometheus bundles its own runs with `/agent-bundle`, its chat included.

**Why this way.** `/agent-improve` already turns evidence into reviewed fixes, so a bundle is one more kind of evidence, unpacked where no run reuses it, naming the commit that ran.

**Without the run's data by default (2026-10-18).** A runner holds production data that isn't the developer's to see, and the run's sessions, outputs, compiled waves and `run.yaml` all hold what it read. Secrets have known values to replace; data doesn't. So a default bundle is `bundle.json` alone, written field by field, with param values replaced in the texts that may quote data, which are listed for the operator to check. Values are replaced before texts are clipped, since a clipped secret no longer matches. `--with-data` takes the run as it is, when the data's owner agrees.

**A rerun asks again.** `prometheus run <slug>@<branch> --bundle` once carried the bundled run's approvals over, so on a machine whose secrets reach the same systems it would send the email again, unasked. Probing each fix against its bundle before a merge (from Google's harness2) was dropped: it needs the data on the developer's machine, and one run of a model stage proves little.

### Sharing is plain git

**The rule.** Agents are shared through any git remote, with no GitLab API. Install clones, then refreshes from your prometheus; `pull` allows only refreshes on `main`; `propose` opens a merge request with push options.

**Why this way.** After the refresh, what prometheus owns is your trusted copy, so the review is of the agent's own code: gates, programs, subagents, packages and lessons.

### One release per server process, spoken over A2A

**The rule.** `prometheus serve` serves one agent per process; inputs arrive as uploads and outputs stay in the run, so no path crosses the wire. Agents call it over A2A, not MCP, with `pattern: a2a` run by the batcher.

**Why this way.** The process's environment holds only that agent's secrets, and the batcher follows links only on the plan's address, so a called agent can't redirect the token or files.

## How the code is laid out

### One self-contained `index.ts` per agent

**The rule.** One extension file per agent, no re-export layers; prometheus is standalone, never installed into `~/.pi/agent/`.

**Why this way.** Indirection and duplicate folders cost more to follow than the duplication between the two extensions.

### Contributor notes are kept where Pi can't read them

**The rule.** Contributor notes are in `.claude/CLAUDE.md`; roles in `.pi/APPEND_SYSTEM.md`.

**Without it.** Pi loads `AGENTS.md` or `CLAUDE.md` from every folder above it, so a root `CLAUDE.md` put about 5,000 tokens into every turn, and an `AGENTS.md` would leak prometheus's role into its agents.

### No releases for prometheus itself

**The rule.** An agent's skill copy is labelled `<version>+<commit>[.dirty] <hash>`, and only the hash of the files a refresh copies decides whether an agent is current, so a refresh from uncommitted changes stays current once they're committed.
