# Classifying stages

Every stage gets exactly one `kind`. Decide it with four questions, asked in
this order. Stop at the first "yes". The order matters: an irreversible
action outranks everything, and a choice that belongs to the user outranks
whether a model could make it.

| # | Question | If yes |
|---|---|---|
| 1 | Does it change anything outside this run — send, post, write to a real system, overwrite, delete? | `side_effect` |
| 2 | Is it a choice only the user can legitimately make — a preference, a risk tolerance, a threshold, a scope? | `decision` |
| 3 | Given its inputs, could a program decide whether the output is correct? | `deterministic` |
| 4 | Otherwise: correctness can only be judged by another reader. | `judgment` |

What each kind implies in the plan:

- **side_effect** — `approval: required`; the compiler gives it its own wave
  after the stages it needs, and reports its results before anything that
  needs it runs.
- **decision** — a `decision` stage (pattern `approval`) or a question; never
  delegated to a child.
- **deterministic** — `pattern: script` with a `verify` (a gate when possible).
  A child writes or runs a program; it does not process items one by one.
- **judgment** — output `schema`, batched through `map` / `map_reduce` when
  there are many items, and a `verify` or a downstream `reviewer`.

Write the reason in `kind_reason`, one line. For `judgment` stages it must say
why a program cannot decide correctness — that is where plans most often go
wrong, by sending mechanical work to a model.

## Question 3, done honestly

"Could a program decide?" means with ordinary code and the inputs the stage
has — parsing, matching, arithmetic, lookups, format rules. It does not mean
"could a model write a program that guesses". Signs a step is deterministic:

- the rule can be written down before seeing the data
- two careful people would always agree on the answer
- the output can be checked by recomputing it

Signs it is judgment:

- the rule needs examples to explain ("things like…")
- the answer depends on reading intent, tone or meaning
- two careful people could disagree

## The split rule

Many real steps are mostly deterministic with a messy tail: most PDFs have a
text layer, some are scans; most emails follow a template, some don't.
Do not classify the whole step as judgment because of the tail, and do not
pretend the tail isn't there. Split it:

1. A `deterministic` script stage handles every item it can and writes the
   ones it could not handle to a leftovers file (an `out` with a `path`, for
   example `unparsed.json`, with a reason per item).
2. A `judgment` stage (`map` if many) consumes only the leftovers, with a
   schema that matches the script's output rows.
3. Downstream stages consume both outputs.

Split when the tail is plausible from what you observed or from the input's
nature (scans, free text, mixed senders, human-typed values). Do not split
speculatively when the input is uniform and machine-generated.

The mirror case also exists: a judgment step with a cheap mechanical
pre-filter (only emails with a PDF attachment can be invoices). Put the filter
in a script stage first so the judgment stage reads fewer items.

## Examples

| Step | Kind | Why |
|---|---|---|
| Convert .eml headers to rows | deterministic | fixed fields, checkable by recount |
| Is this email a supplier invoice? | judgment | forwarded and inline invoices defeat rules |
| Only emails with a PDF attachment | deterministic | pre-filter before the judgment stage |
| Match invoice to PO by number and amount | deterministic | exact rule, recomputable |
| How big a difference counts as a mismatch? | decision | the user's tolerance |
| Summarise each supplier's complaints | judgment | meaning, not fields |
| Draft replies in Gmail | side_effect | writes to a real system |
| Overwrite last month's report | side_effect | replaces something that exists |
