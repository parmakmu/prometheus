# Clarify rules

The goal is the fewest questions that still make the plan safe to run.
Draft first, then ask; a question about a plan you have not drafted is
usually a question you did not need.

## Who resolves each unknown

The planner is an orchestrator: it often has nothing local to look at, and
the real inputs are only reachable by children or only exist once work has
run. So every unknown gets a `resolve_by`, chosen in this order:

1. **planner** — you can check it yourself in one or two cheap read-only
   calls (list a folder you can reach, read a file header, list agents,
   check a connector). Do it now.
2. **probe** — a child can check it cheaply before planning continues
   (a folder only a child's tools reach, a web page, an app a connector
   exposes, "how many items and what shape"). Send one read-only `scout`
   with a narrow question and a small structured answer:

   ```js
   subagent({ async: true, mission: false, workflowScript:
     `return runs.run("probe-u2", { agent: "scout", label: "Probe export folder",
        task: "Read-only. In <location>: count files by extension and read the headers of 3. Return only the JSON.",
        outputSchema: { type: "object", required: ["counts","samples"],
          properties: { counts: { type: "object" }, samples: { type: "array", items: { type: "string" } } } } })` })
   ```

   Batch several probes into one `runs.all`. A probe is for shape and
   presence, never for doing the work.
3. **user** — only the user can say (preference, scope, threshold, audience,
   permission). Becomes a question under the rules below.
4. **discovery** — only doing part of the work reveals it (which competitors
   exist, what the data looks like at scale, which sources are reliable).
   Becomes a concrete discovery stage, and the stages that depend on it
   become provisional (`resolves_after` the discovery stage), so the run
   replans after it.

Record every finding in the unknown's `finding` (and observed facts as
assumptions with `source: observed`). Never read in bulk to answer a planning
question, and never write outside the workflow.

## Do not invent detail

When a stage's schema, batch size, agent or gate depends on something you do
not know, do not guess it to satisfy the validator. Make the stage
provisional and name the discovery that will settle it. A plan that says
"we find out in wave 1" is better than one that is precise and wrong.

## Ask — only when a wrong guess would

1. **Change the stage graph.** Different stages, patterns or waves depending
   on the answer. Example: "extract fixed fields" (one `script` stage) versus
   "summarise each email" (a batched `judgment` map).
2. **Touch a side effect.** Anything that sends, posts, writes to a real
   system, overwrites or deletes. Always ask whether it should happen, and
   whether per-item or batch approval is wanted, unless the user already said.
3. **Pick the wrong source or destination** and you cannot observe which is
   right. Example: "my mail folder" when both a local export and a Gmail
   connector exist.
4. **Change what "done" means.** Thresholds, matching rules, scope limits
   ("last quarter" versus "all time") that decide the output.

## Assume — everything else

Record it in `assumptions`, `source: default`. Set `confirm: true` only when
it is worth a glance (for example a column list or a date range), not for
file naming, formatting, batch sizes, or agent choice.

Typical assumptions: output file name and folder, column order, date format,
batch size, which agent runs a stage, retry counts, whether to include
attachments' names.

## Asking well

- Three questions per round at most, most consequential first.
- One line of why, tied to what changes: "decides whether the last wave sends
  mail".
- 2–4 concrete options and the default you would pick. Never an open-ended
  "what do you want?" unless no options can be named.
- Put the default where the user can accept it with one word.
- Two rounds maximum. After that, remaining gaps become assumptions with
  `confirm: true`.

## Bad and good

Bad: "What format should the Excel file be in?" (formatting is an assumption)
Good: "Should each email be one row, or each invoice line? — decides whether
we need a line-item extraction stage. (a) one row per email (default)
(b) one row per line item"

Bad: "Should I use the worker agent?" (agent choice is yours)
Good: "Send replies, or only draft them? — sending is irreversible.
(a) draft only (default) (b) send after I approve each (c) send all after one
approval"
