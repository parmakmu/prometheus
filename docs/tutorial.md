# Tutorial: your first workflow

In this tutorial you will plan a workflow that reads emails and saves them in an Excel file, run it, look at what it produced, and run it again. It takes about fifteen minutes. You need no emails of your own: you will create three sample ones.

Along the way you will meet the main ideas: a **plan**, **stages**, a **run**, an **approval**, and a **branch** you review. This page doesn't explain them; each step links to the page that does, for later.

> The agent's wording, and the questions it asks, differ from session to session, because a model writes them. The commands, the files and the results below are what you should see. Output marked *from a real run* was copied from one.

## Before you begin

| You need | Check | Install |
|---|---|---|
| Pi 0.99.2 (not 1.0: [why](troubleshooting.md#every-wave-fails-at-startup)) | `pi --version` | `npm install -g @earendil-works/pi-coding-agent@0.99.2` |
| pi-subagents | `ls ~/.pi/agent/npm/node_modules/pi-subagents` | `pi install npm:pi-subagents` |
| Python 3.14 and uv | `uv --version` | [docs.astral.sh/uv](https://docs.astral.sh/uv/) |
| git | `git --version` | your system's package manager |

You also need a model configured in Pi, as for any Pi session.

## 1. Set up Prometheus

In the folder where you cloned this repository:

```sh
uv sync
pi
```

`uv sync` installs the Python packages the scripts use. When Pi starts, it asks whether to trust the folder. **Accept.** Until you do, none of the `/workflow-*` commands exist.

Check: type `/workflow-list`. It answers that there are no workflows yet.

## 2. Create some emails to read

Open a second terminal and create a folder with three small emails:

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

You should see the three files. Note the folder's full path (`echo ~/tutorial-emails` prints it): you will need it in step 5.

## 3. Plan the workflow

Back in the Prometheus terminal:

```text
/workflow-new I want to read the emails in a folder and save them in an Excel file
```

Prometheus now plans. You will see it look at what it can check, draft a plan, have a separate agent (the plan critic) review it, and ask you a few questions. Answer them. Typical ones are which fields to extract (choose sender, recipients, date, subject and body) and what to do about attachments.

It will not ask where your emails are. That value belongs to your machine, and the run asks for it.

When it finishes, it says the workflow is ready. Look at what it made:

```sh
ls workflows/emails-to-excel
```

You should see at least `plan.yaml`, `gates` and `scripts`, plus the workflow's own `.pi` and `.git` (hidden).

Open `workflows/emails-to-excel/plan.yaml`. Near the top you will find the goal and what counts as done:

```yaml
goal: >-
  Read the selected emails and save their requested information in an Excel workbook.
done_when:
  - The selected emails are processed according to the user's chosen scope and fields.
  - An .xlsx workbook is saved at the configured output path.
  - The workbook contains one row per email and its columns match the confirmed extraction scope.
```

*From a real run.* Further down are the **stages**, the steps of the work. A plan like this one has three:

```text
discover         look at sample emails and report what formats and fields exist
read-emails      extract the fields from every email
build-workbook   write the .xlsx
```

Yours may use other names or add a stage; the idea is the same. Ask `/workflow-list emails-to-excel` to see its waves and what it needs before it can run.

## 4. Start the workflow agent

The workflow is a Pi agent of its own. In a new terminal:

```sh
cd workflows/emails-to-excel
pi
```

Accept the trust prompt again: this is a different folder, so it needs its own.

Check: `/workflow-status` reports that the plan is ready and that no run has started.

## 5. Run it

```text
/workflow-run
```

The agent asks you to confirm starting a run, and then asks for the one value it doesn't have: the folder of emails. Give it the absolute path from step 2, such as `/Users/you/tutorial-emails`.

It then launches the first wave and tells you what it is doing. *From a real run:*

> Run `2026-10-01-3` has started. Wave `w1` is reading and discovering the selected emails; it launched three children. I'll continue when the wave finishes.

Wait. When the wave ends, the agent wakes up by itself. The first wave takes about a minute.

### Approve the side effect

Next the agent stops and asks for your approval. Writing the workbook is the one step that changes something outside the work folder, so it never goes ahead without a yes. It tells you what will be created, where, and from which input. *From a real run:*

> The completed workflow run `2026-10-01-3` is ready to create an Excel workbook. Approve the `build-workbook` stage to …

Choose **Approve**. The workbook is written in a couple of seconds.

The agent finishes by going through each of the plan's `done_when` lines and saying, with the file paths, whether it was met.

## 6. Look at the result

```sh
ls runs/*/outputs
```

```text
emails.xlsx
```

Open `emails.xlsx` in Excel or any spreadsheet program. You should see a header row (From, To, Cc, Date, Subject, Body) and one row per email: three rows for the three emails you created.

Two more things are in the run's folder. Look at them:

```sh
ls runs/*/work
```

```text
discovery.md  email-rows.json  unparsed-emails.json
```

`email-rows.json` is what was extracted, one object per email. *From a real run:*

```json
{
  "file": "01-plain.eml",
  "from": "Ada Lovelace <ada@example.test>",
  "to": "team@example.test",
  "cc": "",
  "date": "Wed, 02 Sep 2026 10:00:00 +0000",
  "subject": "Weekly status",
  "body": "Hi all,\n\nDeploy is done. Next: invoice export.\n\nAda\n"
}
```

`unparsed-emails.json` lists any email the program couldn't read. For your three it contains `[]`.

## 7. Review what the run wrote

A first run usually writes small programs into the workflow so that later runs don't have to: one that reads the emails, one that builds the workbook. They are on a branch of the workflow's own git repository, not yet on `main`, and the agent now shows you what is on the branch and asks you to **merge**, **keep** or **discard** it.

Choose **merge**. Check:

```sh
ls scripts
```

```text
build_workbook.py  read_emails.py
```

```sh
git log --oneline main
```

The branch became one commit on `main`, and the branch is gone.

## 8. Run it again

```text
/workflow-run
```

The agent asks whether to start a new run and whether the input changed. Say yes to the first, and no to the second.

This time nothing is written: the two stages that needed programs now just run them. They take about a tenth of a second each, with no model involved, and the run has nothing to commit, so the agent has no branch to show you.

*From real runs of this workflow:* the run that wrote the programs cost $0.021 in model calls for its children. The next run cost $0.0105, and a later one, with the discovery stage unchanged, $0.004 and 77 seconds from start to finish.

## What you have done

You planned a workflow with `/workflow-new`, ran it with `/workflow-run`, approved a side effect, reviewed what a run changed, and ran it again.

## Where to go next

- To see why the second run was cheaper, and how the pieces fit: [How Prometheus works](how-it-works.md).
- To do something specific, such as running on a new folder of emails or changing the plan: [How-to guides](how-to.md).
- If a command refuses: [Troubleshooting](troubleshooting.md).
