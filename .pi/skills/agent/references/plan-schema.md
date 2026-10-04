# Plan schema (version 1)

`<agent>/plan.yaml`. `agent_check` enforces every **rule**;
*warnings* do not fail it. The plan is reusable and holds no run state: each
execution is a run, `runs/<id>/`.

Write text longer than a line (a finding, a note, a check) as a folded block,
`>-` with lines of about 75 characters, so the plan reads as paragraphs; the
scripts write the same way.

## Top level

| field | required | meaning |
|---|---|---|
| `version` | yes | `1` |
| `revision` | no (1) | raised by the scripts (a replan, a recorded critique); not part of what the critic reviews |
| `status` | yes | `draft` \| `needs_answers` \| `ready` |
| `request` | yes | the user's words, verbatim |
| `goal` | yes | one sentence: the outcome, not the steps |
| `done_when` | yes (≥1) | observable acceptance criteria |
| `params`, `inputs`, `outputs` | no | see below |
| `agents` | no (*warning*) | what `subagent({ action: "list" })` showed |
| `unknowns`, `assumptions`, `questions` | no | see below |
| `stages` | yes (≥1) | units of work; waves are derived |
| `critique` | see rules | the critic's result and each finding's disposition |
| `limits` | no | `max_parallel`, `max_retries`, `max_batch` |

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
```

| type | value | `${name}` compiles to |
|---|---|---|
| `path` (default) | a file or folder | the path, `~` expanded, relative resolved inside the run |
| `text` | a table, URL, id | the value |
| `secret` | a key or token | `${ENV_NAME}`, expanded by the child's shell; never written down |

Use `${name}` in input and output `location`, `out.path`, `run` and gate
commands. A secret only a tool reads from the environment need not be
referenced; declaring it makes the run check it is set. Relative `out.path`
values resolve in `runs/<id>/work/`, the children's `cwd`.

- **rule** every `${name}` is a declared param.
- **rule** `out.path` is never a literal absolute path (`/…`, `~…`, `C:\…`).
- **rule** a path param starts its path (`${out_dir}/x.xlsx`, never
  `../${out_dir}/x.xlsx`): it compiles to an absolute path.
- *warning* a literal absolute path in a `location` or gate command.
- **rule** a param's variable is a valid name, not a system or tool one
  (`PATH`, `HOME`, `PI_…`), and not shared with another param.
- **rule** a secret has no `default` and is never part of a path.
- *warning* a param nothing uses (secrets excepted).

## Inputs and outputs

```yaml
inputs:
  - id: mail              # unique across inputs and stage outputs
    kind: folder          # folder | file | app | url | text
    location: ${mail_dir}
    format: eml
    access: read          # read | read_write
    note: 1,240 files observed
outputs:                  # final deliverables
  - id: variance_report
    kind: file            # file | app | message
    location: ${report_dir}/Q3-variance.xlsx
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

- **rule** every stage `agent`, `verify.review` and `critique.agent` is
  available.
- **rule** no stage uses a `source: user` agent: copy its file into
  `.pi/agents/` and make it `source: project`.
- **rule** every `source: project` agent has a file in `.pi/agents/`;
  *warning* one listed as builtin (a plain name) does too.
- Builtin contexts: `oracle` is `fork`; the others are `fresh`.

## Unknowns

```yaml
- id: u1
  text: which competitors matter and where their pricing is published
  resolve_by: discovery      # planner | probe | user | discovery
  stage: discover            # discovery: the stage that finds out
  affects: [analyse, memo]   # optional
  finding: null
- id: u2
  text: whether the export has attachments
  resolve_by: probe
  finding: 300 of 1,240 files carry PDF attachments
  run_id: <scout run id>     # *warning* if missing
- id: u3
  text: the audience of the memo
  resolve_by: user
  question: q1
```

- **rule** `planner` and `probe` unknowns have a `finding` before
  `needs_answers` or `ready`.
- **rule** `user` unknowns name an existing `question`.
- **rule** `discovery` unknowns name an existing concrete `stage`; until
  they have a `finding`, some provisional stage `resolves_after` it.

## Stages

```yaml
- key: extract                 # ^[a-z][a-z0-9-]*$, unique
  title: Parse invoice lines
  kind: deterministic          # deterministic | judgment | decision | side_effect
  kind_reason: fixed fields, checkable by recount
  pattern: script              # script | map | map_reduce | scout | worker | reviewer | approval
  agent: worker                # omit for decision/approval and resumed stages
  script: extract_lines.py     # its program in scripts/
  run: "${mail_dir} lines.csv" # the program's arguments
  needs: [find]                # stages that must finish first
  consumes: [invoices]         # input ids or out ids of needed stages
  out:
    - id: lines
      path: lines.csv          # a file the child writes; without path: its returned result
      format: csv
      schema: {date: string, supplier: string, amount: number}
  batch: 25                    # map / map_reduce
  items: invoices              # map / map_reduce: the input or file output to split
  items_glob: "*.eml"          # optional, for folder items
  verify:
    gate: uv run ../gates/check.py lines.csv                    # pass/fail command
    # gate: {command: …, output: json, schema?, timeout_ms?}    # typed gate
    review: reviewer           # adds a <key>-review child
    check: every invoice id appears once
  approval: none               # none | required
  pause_after: false           # the user sees its results before stages that need it
  context: fork                # only when not the agent's default
  resumes: null                # an earlier single-child stage to continue
  notes: free text
```

A provisional stage is an outline filled in at replan:

```yaml
- key: analyse
  provisional: true
  resolves_after: discover
  kind: judgment
  kind_reason: comparing positioning needs reading, not rules
  title: Compare competitor positioning
  intent: one comparison table across the competitors discovery finds
  needs: [discover]
  consumes: [competitor_list]
  out:
    - id: comparison           # ids may be declared now; paths and schemas later
```

**Gates** live in `gates/`, written while planning or at replan, never by
the stage they check; commands call them as `uv run ../gates/<script>`
(children run in `work/`; `python3` is fine for a standard-library gate).
Every package a gate or program imports is in the agent's
`pyproject.toml`: add one with `agent_add_package` (it asks the user to
approve it, so give the reason), never in the script itself. `uv run` syncs that environment first.
`agent_check` rejects a third-party import `pyproject.toml` does not list,
a `# /// script` block, and one run with `python3`. A typed gate's JSON
becomes the child's structured output, so it excludes a returned `schema`.

**Stage programs.** `script: <file>` names a deterministic (or mechanical
side-effect) stage's program in `scripts/`; `run:` gives its arguments,
called as `uv run scripts/<file> <run>` from `work/`, params resolved and
shell-quoted. The first run's child writes it (usage in its docstring); the
run's review shows it; later runs execute it as a command with no model, and
a `<key>-fix` child repairs it only if it or the gate fails.

**Compiling.** Each wave is one `subagent` call; stages run in layers by
`needs`, all in `runs/<id>/work/`. A map stage's `batcher` (a
command runner) splits `items` into batches, one child per batch writes
`<key>/<n>.<ext>`, then a merge (`map`) or a `<key>-reduce` child
(`map_reduce`). Child keys: `<key>`; maps `<key>-split`, `<key>-<n>`,
`<key>-merge` / `<key>-reduce`; `verify.review` adds `<key>-review`.
Decision and approval stages launch no child. A returned result exists only
in its own wave. External runners have no structured output, fork, gates or
reliable resume. A resumed stage keeps the earlier child's agent, and pi
rejects gates on it.

Rules for all stages:

- **rule** `key` does not collide with another stage's child keys; no stage
  is called `<key>-fix`.
- **rule** `kind` is required; `pattern` too, unless provisional.
- **rule** `needs` names existing stages, with no cycles.
- **rule** every `consumes` id is an input or an `out` of a stage this one
  transitively `needs`.
- **rule** `out` ids are unique and never reuse an input id.
- **rule** `side_effect` stages have `approval: required`, provisional or not.
- **rule** provisional stages have `intent` and `resolves_after` naming a
  concrete stage not after them, and no `resumes`. They skip the detail
  rules below.

Rules for concrete stages:

- **rule** at most one `out` without `path` for a stage that launches a child.
- **rule** `judgment` outputs have a `schema`; a document output (`md`,
  `txt`, `docx`, `html`, `pdf`) may omit it if the stage has `verify.review`
  or a downstream `reviewer`.
- **rule** `deterministic` stages have a `verify`.
- **rule** `script` is a file name, on a deterministic or side-effect stage
  with `pattern: script`, not shared; `run` needs `script`, and then every
  `out` has a `path`. *warning* a deterministic stage without `script:`,
  or in a `worker`/`scout`/`reviewer` child: its program would be rewritten
  in `work/` every run.
- **rule** a typed gate has `command` and `output: json`, and no returned
  `schema`.
- **rule** `map` / `map_reduce` have `batch` (1 … `limits.max_batch`) and
  `items` naming a consumed input or file output, and no `gate`.
- **rule** no stage consumes a returned result from an earlier wave.
- **rule** child-launching stages have an `agent` unless they `resumes`;
  `resumes` names a needed, concrete, single-child native stage, and the
  resuming stage has no `agent` or `gate`.
- **rule** external-runner stages do not use `context: fork`.
- *warning* `context` equal to the agent's default; a returned `schema` or a
  gate on an external runner; deterministic work in a map; a judgment stage
  without `kind_reason`, or without `verify` and a downstream `reviewer`; a
  deterministic script stage without `script`; `script` without `run`.
- In an agent, a gate script missing from `gates/` is a warning while
  drafting and an error once `ready`.

## Waves (derived)

A workflow script cannot pause, so a wave is the stretch of stages between
two points where the parent acts. Each pending stage goes in the earliest
wave its `needs` allow, and starts a later wave when:

- it is a `decision`, `approval` pattern or `side_effect` and a stage it needs
  launches a child (a decision may share a wave with the side effect needing it);
- a stage it needs is a `side_effect` or has `pause_after: true`;
- it is provisional and its `resolves_after` stage has not run.

`ends_with` (what the parent does after the wave): `replan` (a provisional
stage resolves after it) > `review` (a `pause_after` stage) > `approval` (it
had side effects: report them) > `none`.

- **rule** no `waves` field and no stage `status` (status belongs to the run).
- **rule** in the active run, a `done` stage needs only `done` stages, a
  `running` one only `done` or `running` ones; provisional stages are never
  running or done.
- **rule** with `status: ready`, the next wave has no provisional stages.

## Assumptions and questions

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
