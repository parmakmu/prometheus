# Worked example: emails to Excel

This follows one real workflow from request to steady state: how it was planned, what its first runs did, and how its plan changed along the way. It was built while Prometheus was being developed, so it also shows why some features exist. The workflow's folder isn't part of this repository; the numbers below come from its runs, using `openai/gpt-6-luna` through OpenRouter on eight sample `.eml` files.

## 1. The request

In the Prometheus folder:

```text
/workflow-new I want to read emails and save them in Excel
```

Prometheus planned without doing any of the work. It asked the questions whose answers change the plan (where the emails come from, which fields to extract, where the workbook goes), had the plan critic review the draft, and wrote the workflow to `workflows/emails-to-excel/`.

## 2. The plan

**Params.** Two machine-specific values, set per machine rather than written into the plan (see [Params, `.env` and secrets](params-and-secrets.md)):

| Param | Type | Value |
|---|---|---|
| `email_folder` | path, required | the folder of exported `.eml` files; asked for on the first run |
| `output_file` | path | defaults to `outputs/emails.xlsx`, which resolves inside each run's folder |

**Stages.** Three, one of each kind that matters here:

| Stage | Kind | How it's done and checked |
|---|---|---|
| `discover` | judgment | a `scout` reads sample emails and reports the formats and fields it finds; a `reviewer` checks the report |
| `read-emails` | deterministic | a program extracts sender, recipients, date, subject and body into `email-rows.json`, and lists anything it can't parse in `unparsed-emails.json`; the gate `check_email_rows.py` checks that every source file is accounted for |
| `build-workbook` | side effect | a program writes the `.xlsx`; the gate `check_email_workbook.py` checks it row by row against the extracted data. Needs your approval before each run of it |

**Waves.** Derived from the stages, not written in the plan:

- **w1:** `discover` and `read-emails`. They run as one launch, since nothing needs a person between them.
- **w2:** `build-workbook`. It's a side effect, so it gets its own wave, and you approve it before launch.

## 3. Run 1: discovery, then replanning

At first, nobody knew what the emails looked like: plain text or HTML, attachments, encoded headers. So the plan left `read-emails` **provisional**, an outline to be filled in after `discover` had looked. Run 1 ran `discover` (8 messages: plain, HTML-only, multipart with an attachment, non-ASCII headers, a reply, a newsletter, an empty subject), then **replanned**. The workflow agent filled in `read-emails` with what discovery found, wrote its gate script, had the critic check the change, and continued.

Run 1 also hit two problems that led to fixes in Prometheus:

- A wave failed although its output was correct. pi-subagents' default acceptance check for writing children treated staged files in the surrounding git repository as a failure. Since then, the compiler sets each child's acceptance explicitly, and each workflow has its own repository.
- The replan's direct edits to the plan file misfired. Since then, a replan goes through a tool that validates the change and keeps the file's formatting, and direct edits are blocked once a workflow has a run.

## 4. Run 2: a normal run

```text
cd workflows/emails-to-excel
pi
/workflow-run
```

With the plan concrete, a run is two waves and one question:

1. The workflow agent asks whether to start a new run. A new run starts from the reviewed plan with an empty `work/`; earlier runs stay in `runs/`.
2. **w1** launches (`discover`, its review, `read-emails`), about 80 seconds.
3. Before **w2**, it describes what `build-workbook` will create, and where, and asks for approval.
4. **w2** writes the workbook, about 45 seconds, and its gate checks it.
5. The run is done. The workbook is at `runs/<id>/outputs/emails.xlsx`.

The whole run took under four minutes and cost about $0.04: $0.021 for the orchestrating session and $0.016 for the children.

Reading its log showed where the cost went, and led to three changes:

- **The children rewrote their programs every run.** The extraction and workbook code was written from scratch each time, in the run's own folder, and even under a different name. That was about 70% of the children's output. This led to **stage programs**: a deterministic stage names its program, and the program lives in the workflow's `scripts/`, versioned with the plan.
- **The orchestrator did bookkeeping by hand.** It looked up each wave's outcome in pi-subagents, read outputs its gates had already checked, and read the plan to describe the approval. Now the scripts return what it needs: finishing a wave takes just the wave's id.
- **The orchestrator retyped each compiled wave.** It passed the generated script back as its own output, about half of its output tokens. Now the compiled call points to the script file instead.

## 5. Changing the plan with `/workflow-revise`

Adding the programs to the plan is a change to the workflow, so it goes through review:

```text
/workflow-revise give read-emails and build-workbook their programs (script:)
```

1. The command created the branch `change/2026-09-27-1` from `main`, bumped the plan to revision 3 and marked it draft.
2. The workflow agent added `script: read_emails.py` and `script: build_workbook.py` and validated the plan.
3. The critic's first pass asked for a fix: `build-workbook` named a program but kept `pattern: worker`. That combination is now a validator error, so the planner gets it right before the critic runs. After the fix, the second pass approved it.
4. It showed the diff and asked: merge, keep or discard. After a merge, `main` moved to the new revision, tagged `r3`.

## 6. Run 3: the programs are written, once

Now each stage with a program checks whether the program exists yet:

- `read-emails` found no `scripts/read_emails.py`, so its child wrote one (214 lines, taking the input and output paths as arguments), then ran it.
- `build-workbook` did the same for `scripts/build_workbook.py` (129 lines).

When each wave finished, the files it had written into the workflow were committed on the run's branch, `run/2026-09-27-3`. At the end, the review showed the two programs for you to read before any later run relies on them. Once they are on `main`, later runs just run them.

This run cost a little more than run 2 ($0.021 for the children), because writing a reusable program with arguments and documentation is more work than a one-off. That cost is paid once.

## 7. Run 4: the programs are reused

The next run started from `main` with both programs merged:

- `read-emails` ran `scripts/read_emails.py` instead of writing it, and so did `build-workbook` with its program. Neither changed its program, so the run committed nothing and needed no review at the end.
- The orchestrating session passed each compiled wave to pi-subagents as a file path (`workflowScriptPath`), and recorded each wave with nothing but the wave's id.

| | Run 2 | Run 4 |
|---|---|---|
| children | $0.016 | $0.0105 |
| orchestrating session | $0.021 | $0.0135 |
| `build-workbook` wave | about 45 s | 19 s |
| whole run, including your two answers | under 4 min | about 2.5 min |

Of the $0.0105 the children cost, `discover` and its review were $0.0053, about half (see the open choice below).

## 8. What's on disk

```text
workflows/emails-to-excel/
  plan.yaml                  the plan (revision 3)
  .env                       EMAIL_FOLDER=…   (not in git)
  gates/                     check_email_rows.py, check_email_workbook.py
  scripts/                   read_emails.py, build_workbook.py
  runs/
    2026-09-27-1/            run.yaml, work/, outputs/emails.xlsx
    2026-09-27-2/            …
    2026-09-27-3/            …
    2026-09-28-1/            …
  .git                       main, tags r2, r3
```

Each run's `run.yaml` records what ran: each wave's launches, the plan's commit, the param values, your approvals and decisions, and the children's cost.

## An open choice

`discover` still runs on every run, although nothing replans on it any more. If the mailbox format is stable, it can be removed with `/workflow-revise`: in run 4 that would have saved about half of the children's cost ($0.0053) and 35 seconds. If each run reads a different kind of mailbox, keep it. In that case, a format the programs don't handle shows up as a failed gate, and the fix is a change to the programs.
