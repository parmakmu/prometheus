# Tutorial: your first workflow

In this tutorial we'll build a workflow together that copies a folder of emails into an Excel file. You'll plan it, run it, approve the one step that writes a file, look over what the run taught the workflow, and then run it a second time. That second run is the real payoff: it does the same job with almost no model work at all.

It takes about fifteen minutes, and you don't need any emails of your own. We'll make three small samples in step 2.

> A model writes the agent's replies, so their wording and its questions vary between sessions. The commands, files and results below are what you should see. Output marked *from a real run* was copied from an actual session.

## Before you begin

| You need | Check | Install |
|---|---|---|
| Pi 0.99.2 (not 1.0: [why](troubleshooting.md#every-wave-fails-at-startup)) | `pi --version` | `npm install -g @earendil-works/pi-coding-agent@0.99.2` |
| pi-subagents | `ls ~/.pi/agent/npm/node_modules/pi-subagents` | `pi install npm:pi-subagents` |
| Python 3.14 and uv | `uv --version` | [docs.astral.sh/uv](https://docs.astral.sh/uv/) |
| git | `git --version` | your system's package manager |

You also need a model configured in Pi, as for any Pi session.

## 1. Set up Prometheus

Prometheus is the agent that plans workflows. Start it in the folder where you cloned this repository:

```sh
uv sync
pi
```

`uv sync` installs the Python packages the scripts use. When Pi starts, it asks whether to trust the folder. **Accept**: the `/workflow-*` commands exist only in a trusted folder.

Check: type `/workflow-list`. It answers that there are no workflows yet.

## 2. Create some emails to read

The workflow needs something to read, so make three small emails. Each is different on purpose: one is plain text, one is HTML with a copy recipient, and one has an empty subject. Open a second terminal and run:

```sh
mkdir -p ~/tutorial-emails && cd ~/tutorial-emails

cat > 01-status.eml <<'EOF'
From: Ada Lovelace <ada@example.test>
To: team@example.test
Subject: Weekly status
Date: Wed, 02 Sep 2026 10:00:00 +0000
Content-Type: text/plain; charset=utf-8

Hi all,

Deploy is done. Next: invoice export.

Ada
EOF

cat > 02-invoice.eml <<'EOF'
From: billing@vendor.test
To: you@example.test
Cc: accounts@example.test
Subject: Invoice INV-2041 due 2026-09-30
Date: Thu, 03 Sep 2026 11:00:00 +0000
Content-Type: text/html; charset=utf-8

<html><body><p>Invoice INV-2041: <b>EUR 1,250.00</b> due 2026-09-30.</p></body></html>
EOF

cat > 03-empty-subject.eml <<'EOF'
From: grace@example.test
To: you@example.test
Subject:
Date: Fri, 04 Sep 2026 09:30:00 +0000
Content-Type: text/plain; charset=utf-8

Forgot the subject line.
EOF

ls
```

You should see the three files. Note the folder's full path, which `echo ~/tutorial-emails` prints, because the run will ask for it in step 5.

## 3. Plan the workflow

With the emails in place, go back to the Prometheus terminal and describe the job:

```text
/workflow-new I want to read the emails in a folder and save them in an Excel file
```

Prometheus now plans. You will see it look at what it can, draft a **plan**, and have a second agent, the plan critic, review the draft. Then it asks you a few questions. Typical ones ask which fields to extract (choose sender, recipients, date, subject and body) and what to do about attachments.

It won't ask where your emails are. That depends on the machine the workflow runs on, so the run asks instead.

When Prometheus says the workflow is ready, look at what it made:

```sh
ls -a workflows/emails-to-excel
```

You should see at least `plan.yaml`, `gates` and `scripts`, plus the workflow's own `.pi` and `.git`.

The plan is the part to look at now. Open `workflows/emails-to-excel/plan.yaml`: near the top are the goal and what counts as done (*from a real run*):

```yaml
goal: >-
  Read the selected emails and save their requested information in an Excel workbook.
done_when:
  - The selected emails are processed according to the user's chosen scope and fields.
  - An .xlsx workbook is saved at the configured output path.
  - The workbook contains one row per email and its columns match the confirmed extraction scope.
```

Further down are the **stages**, the steps of the work. A plan like this one has three:

```text
discover         look at sample emails and report what formats and fields exist
read-emails      extract the fields from every email
build-workbook   write the .xlsx
```

Yours may name them differently or add a stage, but the shape is the same: look at the input, extract, write. To see what the workflow still needs before it can run, type `/workflow-list emails-to-excel`.

## 4. Start the workflow agent

Prometheus only plans. The workflow is a Pi agent of its own, and it does the work. Open a third terminal in the Prometheus folder and start it:

```sh
cd workflows/emails-to-excel
pi
```

Accept the trust prompt: Pi asks once per folder, and this is a new one.

Check: `/workflow-status` reports that the plan is ready and that no run has started.

## 5. Run it

Start the run:

```text
/workflow-run
```

The agent asks you to confirm the new run, then asks for the one value it doesn't have: the folder of emails. Give it the absolute path from step 2, such as `/Users/you/tutorial-emails`.

It then launches the first **wave**: a group of stages handed off together, each done by a **child** agent working in the background. *From a real run:*

> Run `2026-10-01-3` has started. Wave `w1` is reading and discovering the selected emails; it launched three children. I'll continue when the wave finishes.

You don't need to do anything while it works. After about a minute the wave ends and the agent picks up by itself.

On a brand-new plan, the agent may also stop after `discover` to fill in the stages that depend on it. This is a **replan**: it writes the details of `read-emails` from what `discover` found in your emails, has the critic check them, and carries on. You may be asked to confirm.

### Approve the side effect

Writing the workbook is a **side effect**: it creates something outside the run's working folder. The agent never takes such a step without your yes. It stops, says what will be created, where, and from which input, and asks you to approve `build-workbook`.

Choose **Approve**. The workbook is written in a couple of seconds.

The agent then checks each `done_when` line from the plan, and tells you whether it was met, with file paths as evidence.

## 6. Look at the result

The agent now asks you to review what the run changed. Leave that question open for a moment and check the result first. In the workflow's folder, the workbook is in the run's `outputs` folder:

```sh
ls runs/*/outputs
```

```text
emails.xlsx
```

Open `emails.xlsx` in Excel or any spreadsheet program. You should see a header row (From, To, Cc, Date, Subject, Body) and one row for each of the three emails you created.

The workbook was built from intermediate files, which stay in the run's `work` folder:

```sh
ls runs/*/work
```

```text
discovery.md  email-rows.json  unparsed-emails.json
```

`email-rows.json` holds what was extracted, one object per email. *From a real run:*

```json
{
  "file": "01-status.eml",
  "from": "Ada Lovelace <ada@example.test>",
  "to": "team@example.test",
  "cc": "",
  "date": "Wed, 02 Sep 2026 10:00:00 +0000",
  "subject": "Weekly status",
  "body": "Hi all,\n\nDeploy is done. Next: invoice export.\n\nAda\n"
}
```

`unparsed-emails.json` lists any email the program couldn't read. For your three emails it contains `[]`.

## 7. Review what the run wrote

Back to the agent's question. The run wrote two small programs, one that reads the emails and one that builds the workbook, so later runs can reuse them instead of writing them again.

Nothing a run writes reaches the workflow until you have seen it. So the programs wait on a **branch** of the workflow's own git repository, not yet on `main`. The agent summarises what is on the branch and asks you to **merge**, **keep** or **discard** it.

Choose **merge**. The programs are now part of the workflow:

```sh
ls scripts
```

```text
build_workbook.py  read_emails.py
```

And the branch is gone: it was squashed into one commit on `main`, which you can see with:

```sh
git log --oneline main
```

## 8. Run it again

Now see what the merge bought you. Start a second run:

```text
/workflow-run
```

The agent asks whether to start a new run (yes) and whether the input changed (no).

Watch how it differs. `read-emails` and `build-workbook` now run the programs you merged, as plain commands, in about a tenth of a second each, with no model involved. Only `discover` still uses a model. The run writes no programs, so there is no branch to review at the end.

*From real runs of this workflow:*

| | First run | A later run |
|---|---|---|
| Model cost of the children | $0.021 | $0.004 |
| Stages done by a model | all three | only `discover` |
| Branch to review | yes | none |
| Whole run | a few minutes | 77 seconds |

## What you have done

Well done: that's the whole cycle. You planned a workflow with `/workflow-new`, ran it with `/workflow-run`, approved a side effect, reviewed and merged what the first run wrote, and watched the second run reuse it at a fifth of the cost. Every workflow you build from here works the same way, however different the job.

## Where to go next

- To see why the second run was cheaper, and how the pieces fit together: [How Prometheus works](how-it-works.md).
- To do something specific, such as running on a new folder of emails or changing the plan: [How-to guides](how-to.md).
- If a command refuses: [Troubleshooting](troubleshooting.md).
