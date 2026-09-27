# Plan schema (version 1)

A plan is `<workflow>/.pi/plan.yaml`. `scripts/validate_plan.py` enforces
everything marked **rule**; items marked *warning* are reported but do not
fail validation.

The plan is reusable. It holds no run state: each execution
is a run in its own folder, `runs/<id>/` (see Runs), so the workflow can run
again on new input.

## Top level

| field | required | type | meaning |
|---|---|---|---|
| `version` | yes | `1` | schema version |
| `revision` | no | int, default 1 | bump on every replan or change of goal |
| `status` | yes | `draft` \| `needs_answers` \| `ready` | where planning stands |
| `request` | yes | string | the user's words, verbatim |
| `goal` | yes | string | one sentence: the outcome, not the steps |
| `done_when` | yes | list of strings (≥1) | observable acceptance criteria |
| `params` | no | map | names for everything outside the workflow |
| `inputs` | no | list of Input | what the work reads |
| `outputs` | no | list of Output | final deliverables |
| `agents` | no (warning if absent) | Agents | what `subagent({ action: "list" })` showed |
| `unknowns` | no | list of Unknown | what the plan depends on but does not know yet |
| `stages` | yes | list of Stage (≥1) | units of work; waves are derived from them (see Waves) |
| `assumptions` | no | list of Assumption | guesses and observed facts |
| `questions` | no | list of Question | gaps for the user |
| `critique` | see rules | Critique | plan-critic result and what was done with each finding |
| `limits` | no | map | `max_parallel`, `max_retries`, `max_batch` (ints) |

## Params and paths (portability)

The workflow must work after being copied to another folder or machine.

```yaml
params:
  mail_dir:
    description: folder with the supplier .eml export
    required: true          # default true; false = may stay empty
  report_dir:
    description: where the final report goes
    default: outputs        # relative values resolve against the run's folder, runs/<id>/
  crm_table:
    description: the CRM table the invoices are matched against
    type: text
  crm_token:
    description: API token for the CRM
    type: secret
    env: CRM_API_TOKEN      # default: the name in upper case (CRM_TOKEN)
```

A param's `type` says how its value is used:

| type | value | a reference `${name}` compiles to |
|---|---|---|
| `path` (default) | a file or folder | the path: `~` expanded, a relative value resolved inside the run |
| `text` | a table name, URL, account id, … | the value, exactly |
| `secret` | a key, token or password | `${ENV_NAME}`: the child's (or gate's) shell expands it; the value is never written down |

Values are per machine and not part of the plan. A param reads the
environment variable named by `env:`, else its name in upper case
(`mail_dir` → `MAIL_DIR`). The values come from the environment pi was
started in, else `.env` in the workflow root (`MAIL_DIR=~/Mail/Suppliers`),
else the `default`. `.env` is not in git; the workflow extension loads it into
pi's environment, so children and gates see the same values, and the model
may not read or change it. The model sets values that are not secrets with
`workflow_set_params`; the user adds secrets to `.env` themselves.

Reference a param as `${mail_dir}` in input and output `location`, in
`out.path`, and in gate commands. A secret that only a tool or script reads
from the environment (a connector's token) need not be referenced: declaring
it makes the run check that it is set.

Relative `out.path` values resolve against the run's `work/`
(`runs/<id>/work/`). Children run with that as `cwd`, so gate commands can use
the same relative paths. A relative param value (such as the `outputs`
default above) resolves against the run's folder, so each run keeps its own
deliverables; set an absolute value to write elsewhere.

- **rule** every `${name}` refers to a declared param.
- **rule** `out.path` is never a literal absolute path (`/…`, `~…`, `C:\…`);
  use a param or a relative path.
- *warning* a literal absolute path in an input or output `location`, or in a
  gate command.
- **rule** a param's variable (`env:` or the upper-case name) is a valid
  environment variable name, not one the system or a tool already uses
  (`PATH`, `HOME`, `USER`, `PI_…`, …), and not shared with another param.
- **rule** a secret has no `default` and is never part of a path (`out.path`,
  a file or folder `location`).
- *warning* a declared param that nothing uses (secrets excepted).
- The validator reports `summary.workflow.missing_params` (required params
  with no value and no default) and `missing_secrets` (the variables of
  unset secrets). The run protocol asks for the first before the first wave
  and has the user set the second.

## Input

```yaml
- id: mail              # unique across inputs and all stage outputs
  kind: folder          # folder | file | app | url | text
  location: ${mail_dir}
  format: eml
  access: read          # read | read_write
  note: 1,240 files observed
```

## Output (final deliverable)

```yaml
- id: variance_report
  kind: file            # file | app | message
  location: ${report_dir}/Q3-variance.xlsx
  format: xlsx
  overwrite: false
```

- **rule** each output `id` is produced by exactly one stage.
- **rule** an output with `kind: app` or `message`, or `overwrite: true`,
  must be produced by a `side_effect` stage.

## Agents

```yaml
agents:
  checked: subagent list, 2026-09-26
  available:
    - worker                       # plain name = source: builtin
    - name: plan-critic
      source: project               # builtin | package | project | user
    - name: gpt-pro
      source: package
      runner: external-job         # native | external-cli | external-job
    - name: council-sol
      source: project
      context: fresh               # the profile's defaultContext
```

- **rule** every stage `agent`, `verify.review` and `critique.agent` is
  available (`--agents a,b,c` on the command line replaces the list).
- **rule** no stage uses a `source: user` agent: it will not exist where the
  workflow is copied. Copy its file into `.pi/agents/` and set `source: project`.
- **rule** (in a workflow) every `source: project` agent is defined by a file in
  `.pi/agents/`.
- *warning* no `agents` block and no `--agents`.
- Builtin default contexts: `oracle` is `fork`; `worker`, `reviewer`,
  `scout`, `researcher` and `evidence-auditor` are `fresh`.

## Unknown

```yaml
- id: u1
  text: which competitors matter and where their pricing is published
  resolve_by: discovery      # planner | probe | user | discovery
  stage: discover            # discovery: the stage that finds out
  affects: [analyse, memo]   # optional: stages that depend on the answer
  finding: null              # filled when resolved
- id: u2
  text: whether the export folder has attachments or only bodies
  resolve_by: probe
  finding: 300 of 1,240 files carry PDF attachments
  run_id: <scout run id>
- id: u3
  text: the audience of the memo
  resolve_by: user
  question: q1
```

- **rule** `resolve_by` uses the enum above.
- **rule** `planner` and `probe` unknowns have a `finding` before
  `status: needs_answers` or `ready`.
- **rule** `user` unknowns name an existing `question`.
- **rule** `discovery` unknowns name an existing, concrete `stage`; until the
  unknown has a `finding`, at least one provisional stage `resolves_after` it
  (that is what makes the plan replan after it).
- *warning* a `probe` finding without `run_id`.

## Stage

```yaml
- key: extract                 # lowercase slug, unique; the workflow key
  title: Parse invoice lines
  kind: deterministic          # deterministic | judgment | decision | side_effect (classify.md)
  kind_reason: fixed fields, checkable by recount
  pattern: script              # script | map | map_reduce | scout | worker | reviewer | lanes | council | approval
  agent: worker                # an available agent; omit for decision/approval and resumed stages
  script: extract_lines.py     # the stage's program in the workflow's scripts/ (see Stage programs)
  needs: [find]                # stage keys that must finish first
  consumes: [invoices]         # input ids or out ids of upstream stages
  out:
    - id: lines
      path: lines.csv          # a file the child writes (relative = runs/<id>/work/)
      format: csv
      schema: {date: string, supplier: string, amount: number}
  batch: 25                    # required for map / map_reduce
  items: invoices              # map / map_reduce: the consumed input or file output to split
  items_glob: "*.eml"          # optional, for folder items
  verify:
    gate: python3 check.py lines.csv                      # plain gate
    # gate: {command: python3 check.py lines.csv, output: json}   # typed gate
    review: reviewer           # the runner adds a <key>-review child
    check: every invoice id appears once
  approval: none               # none | required
  pause_after: false           # true: the user reviews this stage's results before stages that need it run
  context: fork                # only when different from the agent's default
  resumes: null                # key of an earlier single-child stage to continue
  notes: optional free text
```

### Provisional stages

A stage whose details depend on a discovery is written in outline and filled
in at replan:

```yaml
- key: analyse
  provisional: true
  resolves_after: discover     # the concrete discovery stage
  kind: judgment
  kind_reason: comparing positioning needs reading, not rules
  title: Compare competitor positioning
  intent: one comparison table across the competitors discovery finds
  needs: [discover]
  consumes: [competitor_list]
  out:
    - id: comparison           # ids may be declared; paths and schemas come later
```

Provisional stages skip the detail rules (agent, pattern, schema, batch,
verify). They keep the safety rules: `kind` is required, and a `side_effect`
still needs `approval: required`.

### Returned result versus files

An `out` entry **with** `path` is a file the child writes. One **without**
`path` is the child's returned result; with a `schema` it becomes
`outputSchema`. A child returns one result.

### Gates

- A string is a pass/fail host command.
- A mapping is a typed gate `{command, output: json, schema?, timeout_ms?}`;
  its parsed JSON becomes the child's structured output, so pi rejects it
  together with an `outputSchema`.
- Gate scripts live in `<workflow>/gates/` and are written while planning (or
  at replan), never by the stage they check. A gate calls them as
  `python3 ../gates/<script> …`; the compiler points `../gates/` at the
  workflow's `gates/` folder (children run in `runs/<id>/work/`). In a workflow, a missing
  script is a warning while drafting and an error once the plan is `ready`.

### Stage programs

A deterministic stage is a program plus a gate. `script: <file>` names the
program; it lives in the workflow's `scripts/` and is versioned with the
plan, so every run uses the same reviewed code:

- The first run's child writes it there (inputs and outputs as
  command-line arguments, usage in its docstring) and runs it. The run
  commits it on its branch `run/<id>`, and the user sees it in the run's
  review; after a merge, later runs' children just run it, and change it
  only if it fails or its output would miss "done when".
- A side-effect stage whose work is mechanical (writing a workbook) may name
  one too.

- **rule** `script` is a file name (no folders), on a deterministic or
  side-effect stage with `pattern: script` (not a map-type stage: every
  batch child would write it), and not shared with another stage.
- *warning* a deterministic `pattern: script` stage without `script`: every
  run writes its program again.

### Document outputs

A judgment output in a document format (`md`, `txt`, `docx`, `html`, `pdf`)
may omit `schema`, but then the stage needs `verify.review` or a downstream
`reviewer` stage. Data outputs from judgment stages always need a `schema`.

### Resumed stages

`resumes: <key>` continues the retained child of an earlier stage. pi keeps
its agent, model and tools (no `agent` here) and rejects gates on resumed
children (verify in a following stage).

### External runners

Agents with `runner: external-cli` or `external-job` have no structured
output, no forked context, no acceptance gates and no reliable resume.

### Workflow keys

| pattern | child keys |
|---|---|
| `map` | `<key>-split`, `<key>-0`, `<key>-1`, … (one per batch), `<key>-merge` |
| `map_reduce` | `<key>-split`, `<key>-0`, …, `<key>-reduce` |
| `lanes`, `council` | `<key>-<lane or advisor>` |
| with `verify.review` | also `<key>-review` |
| everything else | `<key>` |

`decision` stages and the `approval` pattern launch no child.

### How stages compile

`scripts/compile_wave.py` turns the next wave into one `subagent` call. Run
stages are ordered by `needs` into layers of `runs.all`; decisions and
approvals are collected before launch. Every child runs with
`cwd: runs/<id>/work`. Map stages split their `items` with the
`workflow-batcher` command runner (no model), launch one child per batch
writing `<key>/<n>.<ext>` in the run's `work/`, and merge the batch files into the stage's
output (`map`) or hand them to a `<key>-reduce` child (`map_reduce`).
`lanes` and `council` are not compiled; the runner launches them by hand.

A child's returned result only exists inside the run that produced it, so
later waves can only consume outputs that have a `path`.

Rules:

- **rule** `key` matches `^[a-z][a-z0-9-]*$`, is unique, and does not collide
  with another stage's child keys (including `<key>-review`).
- **rule** `kind` is required; `pattern` is required unless provisional; both
  use the enums above.
- **rule** `needs` names existing stages; the graph has no cycles.
- **rule** every `consumes` id is an input id or an `out` id of a stage this
  stage transitively `needs`.
- **rule** `out` ids are unique across all stages and never reuse an input id.
- **rule** `side_effect` stages have `approval: required`, provisional or not.
- **rule** provisional stages have `intent` and `resolves_after` naming a
  concrete stage that does not itself come after them (no cycle through
  `needs` and `resolves_after`).
- **rule** provisional stages do not `resumes`.
- Concrete stages only:
  - **rule** at most one `out` entry without `path` when the stage launches a child.
  - **rule** `judgment` stages give every `out` entry a `schema`, except
    document outputs, which need a reviewer instead.
  - **rule** `deterministic` stages have a `verify`.
  - **rule** a typed gate has `command` and `output: json` and no returned `schema`.
  - **rule** `map` / `map_reduce` stages have a positive integer `batch` ≤ `limits.max_batch`,
    and `items` naming a consumed input or a consumed file output (one with a `path`).
  - **rule** `map` / `map_reduce` stages have no `gate` (verify in a following script stage).
  - **rule** a stage never consumes a returned (path-less) result of a stage in an earlier wave.
  - *warning* `lanes` / `council` stages are not compiled automatically.
  - **rule** stages that launch a child have an `agent` unless they `resumes`.
  - **rule** `resumes` names a needed, concrete, single-child native stage;
    the resuming stage sets no `agent` and no `gate`.
  - **rule** external-runner stages do not use `context: fork`.
  - *warning* `context` equal to the agent's default.
  - *warning* a returned `schema` or a `gate` on an external-runner stage.
  - *warning* deterministic work in a `map` / `map_reduce`.
  - *warning* a `judgment` stage without `kind_reason`, or without `verify`
    and without a downstream `reviewer`.

## Waves (derived, not written)

A wave is the stretch of stages between two points where the parent must act:
a workflow script cannot pause, so a wave's script runs start to finish. Plans
do not list waves. `validate_plan.derive_waves` groups the stages that are not
`done`, each in the earliest wave its `needs` allow, and starts a later wave
for a stage when

- it is a `decision`, an `approval` pattern or a `side_effect`, and a stage it
  needs launches a child (the parent asks the user before a wave's script
  starts; a decision may share a wave with the side effect that needs it);
- a stage it needs is a `side_effect` (its results are reported first) or has
  `pause_after: true` (the user reviews it first);
- it is provisional and the stage it `resolves_after` has not run (the parent
  replans in between).

Waves are numbered `w1`, `w2`, … within a run, on from the waves the run
has finished. What the parent does when a wave finishes (`ends_with`, derived):

- `replan` — a pending provisional stage resolves after a stage in the wave:
  fill it in from what the wave found
- `review` — a stage in the wave has `pause_after`: show its results and wait
  for the user's go
- `approval` — the wave had side effects (approved before it started): report
  what they did
- `none` — continue; the next wave's decisions and approvals are collected
  before it starts

Use `pause_after` only when the user must see a result before work builds on
it and no decision or side effect already pauses there.

Rules:

- **rule** `waves` is not a plan field.
- **rule** in the active run, a `done` stage needs only `done` stages; a
  `running` stage needs only `done` or `running` ones; provisional stages are
  never `running` or `done`.
- **rule** stages have no `status` field: status belongs to the run.
- **rule** with `status: ready`, the next wave to run has no provisional stages.

## Runs

Each execution of the plan is a run, a folder under the workflow:

```
runs/2026-09-27-1/
  run.yaml      state, written by run_log.py (never by hand)
  work/         the children's cwd: stage outputs, decisions, batches
  outputs/      e.g. an `outputs` param default resolved for this run
  .compiled/    compiled waves and their results
```

```yaml
id: 2026-09-27-1
status: active          # active | done | stopped
revision: 2             # plan revision when the run started
commit: 3f1c…           # the workflow's git commit when the run started
started_at: …
note: March mailbox     # optional, what the run is for
stages:                 # stages that are running or done in this run
  discover: done
  read-emails: running
launches:               # one entry per wave launch
  - wave: w2
    stages: [read-emails]
    commit: 9a07…       # the plan this launch ran (committed at compile)
    status: running     # running | done | failed | blocked | stopped
    subagent_run_id: …
    mission_id: …
    approvals: []
    decisions: {}
    params: {…}         # the values at launch; secrets only as "(secret, set)"
    cost_usd: 0.0161    # children's cost, from pi-subagents (with tokens)
    children: {read-emails: <runId>}
```

At most one run is `active`; `/workflow-run` continues it, and the run
becomes `done` when every stage is. A new run starts from the first wave with
an empty `work/`; starting one while another is active stops that one (kept
as `stopped`). A replan changes the plan for this run and all later ones.

## Versioning

A workflow is its own git repo. It tracks the plan with `gates/`,
`.pi/agents/` and the skill copy; its `.gitignore` leaves out `runs/`,
`.env` and installs. The scripts commit, never the model: when the
workflow is created or refreshed, when a run starts, before a wave launches
(compile), and around a replan and a recorded critique. A commit made while
the plan is `ready` moves the tag `r<revision>` to it. Runs and launches
record their commit, so `git show <commit>:.pi/plan.yaml` (or `r<N>`) shows
exactly what ran. Nothing commits while a wave runs: pi-subagents compares
the repo's staged index before and after each writer child.

## Assumption

```yaml
- id: a1
  text: Invoices are PDF attachments, not inline HTML
  source: observed       # observed | default | answer
  confirm: false         # true = the user should glance at it
```

## Question

```yaml
- id: q1
  question: Should replies be sent, or only drafted?
  why: sending is irreversible and changes the last wave
  options: [draft only, send after I approve each, send all after one approval]
  default: draft only
  blocks: [drafts]
  from_finding: f2       # optional: the critic finding this came from
  answer: null
```

- **rule** every question has `question`, `why`, `options` (≥2), `default`,
  and `blocks` naming existing stages.
- **rule** `status: ready` requires every question answered.
- **rule** `status: needs_answers` requires at least one open question.
- *warning* more than 3 open questions; more than 5 assumptions with `confirm: true`.

## Critique

```yaml
critique:
  agent: plan-critic
  run_id: <runId>
  revision: 1               # the plan revision it reviewed
  passes: 1                 # 1 or 2 within that revision
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

- **rule** a plan with a `side_effect` stage, a `judgment` stage, or more
  than 3 stages needs a `critique` before `needs_answers` or `ready`.
- **rule** `agent`, `run_id`, `verdict` required; `passes` is 1 or 2.
- **rule** every finding has `id`, `severity`, `claim`, `disposition`;
  accepted and rejected findings have a `note`; asked ones are covered by a
  question with `from_finding`.
- **rule** `revise` / `ask_user` verdicts record at least one finding.
- *warning* a rejected blocking finding; a critique for an older revision.

## Validator

```
validate_plan.py PLAN.yaml [--agents a,b,c] [--external x,y] [--workflow DIR]
```

For `<workflow>/.pi/plan.yaml` the workflow is detected automatically and its
files checked; stage status and the waves come from the active run (with no
active run, `waves` shows what a new run would do). Output:

```json
{"valid": true, "status": "ready", "errors": [], "warnings": [],
 "summary": {"revision": 1, "stages": 5, "done": [], "running": [],
             "run": {"id": "2026-09-27-1", "status": "active"},
             "provisional": ["analyse"], "next_wave": "w1",
             "waves": [{"id": "w1", "stages": ["discover"], "ends_with": "replan"},
                       {"id": "w2", "stages": ["analyse", "memo"], "ends_with": "none"},
                       {"id": "w3", "stages": ["publish"], "ends_with": "approval"}],
             "open_questions": 0, "unresolved_unknowns": [], "approvals": ["publish"],
             "workflow_keys": {"discover": "discover", "analyse": "(provisional)"},
             "agents_checked": true, "critic_required": true, "critic_verdict": "pass",
             "workflow": {"root": "/…/workflows/competitor-memo", "missing_params": ["notion_parent"], "missing_secrets": ["NOTION_TOKEN"]}}}
```
