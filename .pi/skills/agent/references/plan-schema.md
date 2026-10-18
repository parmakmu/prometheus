# Plan schema (format 1)

`<agent>/plan.yaml`: what the agent does, for people to read. How it was
planned lives beside it in `<agent>/.plan/`, one file per section, each
under its own key: `unknowns.yaml`, `questions.yaml`, `agents.yaml`, and
`critique.yaml` (written only by `agent_record_critique`). The scripts read
them as one plan; a planning section in plan.yaml is an error. `agent_check`
enforces every **rule**; *warnings* do not fail it. The plan is reusable
and holds no run state: each execution is a run, `runs/<id>/`.

The scripts write plan.yaml in sections, each under a `# ── …` banner:
format, revision and status; what you asked for (request, goal, done_when); what it
works on (params, inputs, outputs, network); how it works (stages); assumptions;
limits and models. Keep that order when you edit it.

Write text longer than a line (a finding, a note, a check) as a folded block,
`>-` with lines of about 75 characters, so the plan reads as paragraphs; the
scripts write the same way.

## Top level

| field | required | meaning |
|---|---|---|
| `format` | yes | `1`: the version of this plan format (not the plan's `revision`, nor a release's `v1.0`) |
| `revision` | no (1) | raised by the scripts (resolving a stage, a recorded critique); not part of what the critic reviews |
| `status` | yes | `draft` \| `needs_answers` \| `ready` |
| `request` | yes | the user's words, verbatim |
| `goal` | yes | one sentence: the outcome, not the steps |
| `done_when` | yes (≥1) | observable acceptance criteria |
| `params`, `inputs`, `outputs` | no | see below |
| `agents` | no (*warning*) | in `.plan/agents.yaml`: what `subagent({ action: "list" })` showed |
| `unknowns`, `questions` | no | in `.plan/unknowns.yaml`, `.plan/questions.yaml`; see below |
| `assumptions` | no | see below |
| `stages` | yes (≥1) | units of work; waves are derived |
| `critique` | see rules | in `.plan/critique.yaml`, written by `agent_record_critique`: the critic's result and each finding's disposition |
| `limits` | no | `max_parallel`, `max_retries`, `max_batch`; `command_timeout_s` (a program or gate run as a command, default 600); `max_cost_usd` (the run's children may spend this much, then the user is asked) |
| `network` | no | hosts the stages call (APIs, sites), e.g. `[api.github.com, .example.com]` (a leading `.` covers subdomains). In the web app with Podman, only hosts the user allowed are reachable; these are offered to them to allow |
| `models` | no | `default`, `review`: the models children use (a stage's `model` wins over `default`; `review` is for `<id>-review` children). Omitted: each subagent's own |

## Params and paths

The agent must work when copied elsewhere, so everything outside it is a
param. Values are per machine, not in the plan: the environment, else `.env`
in the agent root, else `default`.

```yaml
params:
  mail_dir:
    description: folder with the supplier .eml export
    required: true          # default true
  report_dir:
    description: where the final report goes
    default: outputs        # relative: resolves inside runs/<id>/
  crm_table:
    description: the CRM table the invoices are matched against
    type: text
  crm_token:
    description: API token for the CRM
    type: secret
    env: CRM_API_TOKEN      # default: the name in upper case
  question:
    description: what to look up this time
    type: text
    per_run: true           # asked for each run, kept on the run, never in .env
```

A `per_run` param is one whose value differs every run (a question, a
search, a date). Its value is the run's own: given when the run starts, or
asked for when it is missing, and never saved to `.env`, so the next run
asks again. A model stage reads it through a text input
(`{id: question, kind: text, path: ${question}}`), which puts the value in
the child's task.

| type | value | `${name}` compiles to |
|---|---|---|
| `path` (default) | a file or folder | the path, `~` expanded, relative resolved inside the run |
| `text` | a table, URL, id | the value |
| `secret` | a key or token | `${ENV_NAME}`, expanded by the child's shell; never written down |

Use `${name}` in input and output `path`s, `writes` paths, a program's
`args` and gate commands. A secret only a tool reads from the environment need not be
referenced; declaring it makes the run check it is set. Relative `writes`
paths resolve in `runs/<id>/work/`, the children's `cwd`.

- **rule** every `${name}` is a declared param.
- **rule** a `writes` path is never a literal absolute path (`/…`, `~…`, `C:\…`).
- **rule** a path param starts its path (`${out_dir}/x.xlsx`, never
  `../${out_dir}/x.xlsx`): it compiles to an absolute path.
- *warning* a literal absolute path in an input or output `path` or a gate command.
- **rule** a param's variable is a valid name, not a system or tool one
  (`PATH`, `HOME`, `PI_…`), and not shared with another param.
- **rule** a secret has no `default`, is never part of a path, and is never `per_run`.
- *warning* a param nothing uses (secrets excepted).

## Inputs and outputs

```yaml
inputs:
  - id: mail              # unique across inputs and stage outputs
    kind: folder          # folder | file | app | url | text
    path: ${mail_dir}
    format: eml
    access: read          # read | read_write
    note: 1,240 files observed
outputs:                  # final deliverables
  - id: variance_report
    kind: file            # file | app | message
    path: ${report_dir}/Q3-variance.xlsx
    format: xlsx
    overwrite: false
```

- **rule** each output `id` is produced by exactly one stage.
- **rule** an output with `kind: app` or `message`, or `overwrite: true`, is
  produced by a `side_effect` stage.
- **rule** the agent of a `side_effect` stage that writes an `app` or
  `message` output can reach it: a subagent's `tools` list a connector
  (`mcp:<server>` or `mcp:<server>/<tool>`) or `bash` (for a CLI); *warning*
  a builtin agent (they get no connector tools, so only a CLI through bash).

## Agents

In `.plan/agents.yaml`:

```yaml
agents:
  checked: subagent list, 2026-09-26
  available:
    - worker                       # plain name = source: builtin
    - name: batcher
      source: project              # builtin | package | project | user
      runner: external-cli         # a command, no model
    - name: invoice-check-classifier
      source: project
      context: fresh               # the profile's defaultContext
```

- **rule** every stage `agent` and `review`, and `critique.agent`, is
  available.
- **rule** no stage uses a `source: user` agent: copy its file into
  `.pi/agents/` and make it `source: project`.
- **rule** every `source: project` agent has a file in `.pi/agents/`;
  *warning* one listed as builtin (a plain name) does too.
- Builtin contexts: `oracle` is `fork`; the others are `fresh`.

## Unknowns

In `.plan/unknowns.yaml`, under `unknowns:`:

```yaml
- id: u1
  text: which competitors matter and where their pricing is published
  resolved_by: discovery      # planner | probe | user | discovery
  stage: discover            # discovery: the stage that finds out
  affects: [analyse, memo]   # optional
  finding: null
- id: u2
  text: whether the export has attachments
  resolved_by: probe
  finding: 300 of 1,240 files carry PDF attachments
  run_id: <scout run id>     # *warning* if missing
- id: u3
  text: the audience of the memo
  resolved_by: user
  question: q1
```

- **rule** `planner` and `probe` unknowns have a `finding` before
  `needs_answers` or `ready`.
- **rule** `user` unknowns name an existing `question`.
- **rule** `discovery` unknowns name an existing concrete `stage`; until
  they have a `finding`, some provisional stage `resolves_after` it.

## Stages

```yaml
- id: extract                  # ^[a-z][a-z0-9-]*$, unique
  title: Parse invoice lines   # a short label
  kind:
    value: deterministic       # deterministic | judgment | decision | side_effect
    reason: fixed fields, checkable by recount
  pattern: script              # script | map | map_reduce | scout | worker | reviewer | approval | a2a
  agent: worker                # omit for decision/approval and resumed stages
  program:
    file: extract_lines.py     # its program in scripts/
    args: "${mail_dir} lines.csv"
  after: [find]                # stages that must finish first
  reads: [invoices]            # input ids, or what stages it runs after write
  writes:
    - id: lines
      path: lines.csv          # a file the child writes; without path: its returned result
      format: csv
      schema: {date: string, supplier: string, amount: number}
  map:                         # map / map_reduce only
    over: invoices             # the input or file output it reads, split into batches
    glob: "*.eml"              # optional, for a folder's files
    batch: 25
  gate: uv run ../gates/check.py lines.csv                    # pass/fail command
  # gate: {command: …, output: json, schema?, timeout_ms?}    # typed gate
  review: reviewer             # adds an <id>-review child
  done_when: every invoice id appears once
  pauses_after: false           # the user sees its results before the stages after it
  context: fork                # only when not the agent's default
  resumes: null                # an earlier single-child stage to continue
  model: null                  # its children's model (default: models.default, else the subagent's)
  reuse: true                  # false: never take an earlier run's result for the same inputs
  instructions: >-             # what the child must do, in full: it reaches the child's task after the title
    One sentence per email: who wrote it, what it is about, any action it asks for.
```

A `side_effect` stage always asks before it runs: the user approves it (in a
released run, the caller), so there is nothing to set for that.

A provisional stage is an outline, written out in full when the run resolves it:

```yaml
- id: analyse
  provisional: true
  resolves_after: discover
  kind:
    value: judgment
    reason: comparing positioning needs reading, not rules
  title: Compare competitor positioning
  intent: one comparison table across the competitors discovery finds
  after: [discover]
  reads: [competitor_list]
  writes:
    - id: comparison           # ids may be declared now; paths and schemas later
```

**Gates** live in `gates/`, written while planning or when the run resolves a stage, never by
the stage they check; commands call them as `uv run ../gates/<script>`
(children run in `work/`; `python3` is fine for a standard-library gate).
Every package a gate or program imports is in the agent's
`pyproject.toml`: add one with `agent_add_package` (it asks the user to
approve it, so give the reason), never in the script itself. `uv run` syncs that environment first.
`agent_check` rejects a third-party import `pyproject.toml` does not list,
a `# /// script` block, and one run with `python3`. A typed gate's JSON
becomes the child's structured output, so it excludes a returned `schema`.

**Stage programs.** `program.file` names a deterministic (or mechanical
side-effect) stage's program in `scripts/`; `program.args` gives its
arguments, called as `uv run scripts/<file> <args>` from `work/`, params
resolved and shell-quoted. The first run's child writes it (usage in its
docstring); the run's review shows it; later runs execute it as a command
with no model, and an `<id>-fix` child repairs it only if it or the gate fails.

**Compiling.** Each wave is one `subagent` call; stages run in layers by
`after`, all in `runs/<id>/work/`. A map stage's `batcher` (a
command runner) splits `map.over` into batches, one child per batch writes
`<id>/<n>.<ext>`, then a merge (`map`) or an `<id>-reduce` child
(`map_reduce`). Child keys: `<id>`; maps `<id>-split`, `<id>-<n>`,
`<id>-merge` / `<id>-reduce`; `review` adds `<id>-review`.
A map's gate checks the merged output (run by the batcher after the merge)
or the reduce child's. A relaunch keeps the batches that completed.
Decision and approval stages launch no child. A returned result exists only
in its own wave. External runners have no structured output, fork, gates or
reliable resume. A resumed stage keeps the earlier child's agent, and pi
rejects gates on it.

Rules for all stages:

- **rule** a stage has only the fields above (an unknown one, often a
  misspelling, is an error).
- **rule** `id` does not collide with another stage's child keys; no stage
  is called `<id>-fix`.
- **rule** `kind` is a mapping with `value`; `pattern` is required unless
  provisional.
- **rule** `after` names existing stages, with no cycles.
- **rule** every `reads` id is an input or something written by a stage this
  one runs after, directly or transitively.
- **rule** `writes` ids are unique and never reuse an input id.
- **rule** provisional stages have `intent` and `resolves_after` naming a
  concrete stage not after them, and no `resumes`. They skip the detail
  rules below.

Rules for concrete stages:

- **rule** at most one `writes` entry without `path` for a stage that
  launches a child.
- **rule** `judgment` outputs have a `schema`; a document output (`md`,
  `txt`, `docx`, `html`, `pdf`) may omit it if the stage has `review`
  or a downstream `reviewer`.
- **rule** `deterministic` stages have a `gate`, `review` or `done_when`.
- *warning* a stage a model child does has no `instructions`: `title` is a short
  label, `done_when` says when it is done, and `instructions` say what to do
  and how (length, language, what to include or leave out).
- **rule** `program.file` is a file name, on a deterministic or side-effect
  stage with `pattern: script`, not shared; with `program.args`, every
  `writes` entry has a `path`. *warning* a deterministic stage without
  `program`, or in a `worker`/`scout`/`reviewer` child: its program would be
  rewritten in `work/` every run.
- **rule** a typed gate has `command` and `output: json`, and no returned
  `schema`.
- **rule** `map` / `map_reduce` have `map.batch` (1 … `limits.max_batch`) and
  `map.over` naming an input or file output the stage `reads`.
- **rule** no stage reads a returned result from an earlier wave.
- **rule** child-launching stages have an `agent` unless they `resumes`;
  `resumes` names a concrete, single-child native stage it runs after, and
  the resuming stage has no `agent` or `gate`.
- **rule** external-runner stages do not use `context: fork`.
- **rule** a secret param appears only in a program's `args` and the stage's
  gate, or an a2a stage's `token` (the batcher runs those; no model child
  gets a secret).
- **rule** `pattern: a2a` calls another agent over A2A (prometheus-built or
  not) instead of doing the work: the batcher sends one message and waits, no
  model. It has an `a2a:` block and no `program`; it is deterministic,
  judgment (the called agent judges) or side_effect, never a decision; its
  `writes` has a path (a folder: the called agent's outputs land there);
  `files` maps the called agent's file params to ids the stage `reads`;
  `token` is `"${secret param}"`; `approve` names the called agent's side
  effects it lets run, and then the stage is a `side_effect` itself, so the
  run asks before it. A called agent that stops to ask something fails the
  stage: approve its side effects in the plan.

  ```yaml
  - id: summarise
    kind:
      value: side_effect
      reason: the summariser agent writes a workbook
    pattern: a2a
    agent: batcher
    reads: [emails]
    a2a:
      url: ${summariser_url}          # a text param: <url>/.well-known/agent-card.json is its card
      files: {email_folder: emails}   # the called agent's param -> what this stage reads
      params: {language: en}
      approve: [build-workbook]
      token: ${summariser_token}      # a secret param
    writes:
      - id: summary
        path: summary
    done_when: summary/ holds the workbook
  ```
- *warning* a judgment stage that reads an input folder, file, url or app
  as an agent with a shell: use `reader`.
- **rule** a resumed stage has no `model` (it keeps the earlier child's).
  *warning* `model` on an external runner (it runs no model).
- *warning* `context` equal to the agent's default; a returned `schema` or a
  gate on an external runner; deterministic work in a map; a judgment stage
  without `kind.reason`, or without a `gate`, `review` or `done_when` and a
  downstream `reviewer`; a deterministic script stage without `program`; a
  `program` without `args`.
- In an agent, a gate script missing from `gates/` is a warning while
  drafting and an error once `ready`.

## Waves (derived)

A workflow script cannot pause, so a wave is the stretch of stages between
two points where the parent acts. Each pending stage goes in the earliest
wave its `after` allows, and starts a later wave when:

- it is a `decision`, `approval` pattern or `side_effect` and a stage it runs
  after launches a child (a decision may share a wave with the side effect after it);
- a stage it runs after is a `side_effect` or has `pauses_after: true`;
- it is provisional and its `resolves_after` stage has not run.

`ends_with` (what the parent does after the wave): `resolve` (a provisional
stage resolves after it) > `review` (a `pauses_after` stage) > `approval` (it
had side effects: report them) > `none`.

- **rule** no `waves` field and no stage `status` (status belongs to the run).
- **rule** in the active run, a `done` stage runs after only `done` stages, a
  `running` one after only `done` or `running` ones; provisional stages are never
  running or done.
- **rule** with `status: ready`, the next wave has no provisional stages.

## Assumptions and questions

Assumptions in plan.yaml (the user should see them); questions in
`.plan/questions.yaml`:

```yaml
assumptions:
  - id: a1
    text: Invoices are PDF attachments, not inline HTML
    source: observed       # observed | default | answer
    confirm: false         # true: worth the user's glance
questions:
  - id: q1
    question: Should replies be sent, or only drafted?
    why: sending is irreversible and changes the last wave
    options: [draft only, send after I approve each, send all after one approval]
    default: draft only
    blocks: [drafts]
    from_finding: f2       # optional: the critic finding it came from
    answer: null
```

- **rule** a question has `question`, `why`, `options` (≥2), `default` and
  `blocks` naming existing stages.
- **rule** `ready` needs every question answered; `needs_answers` at least
  one open.
- *warning* more than 3 open questions; more than 5 `confirm: true`
  assumptions.

## Critique

In `.plan/critique.yaml`, written by `agent_record_critique` (never by hand):

```yaml
critique:
  agent: plan-critic
  run_id: <runId>
  revision: 1               # the revision it reviewed
  passes: 1                 # 1 or 2
  verdict: revise           # pass | revise | ask_user
  findings:
    - id: f1
      severity: major       # blocking | major | minor
      category: should_split
      target: extract
      claim: scanned PDFs would drop out silently
      disposition: accepted # accepted | rejected | asked
      note: added extract-rest for unparsed invoices
```

- **rule** a plan with a `side_effect` or `judgment` stage, or more than 3
  stages, has a `critique` before `needs_answers` or `ready`.
- **rule** `agent`, `run_id`, `verdict` required; `passes` 1 or 2.
- **rule** each finding has `id`, `severity`, `claim`, `disposition`;
  accepted and rejected ones a `note`; asked ones a question with
  `from_finding`.
- **rule** `revise` / `ask_user` record at least one finding.
- *warning* a rejected blocking finding; a critique of an older revision.
