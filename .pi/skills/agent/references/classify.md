# Classifying stages

Every stage gets one `kind`: ask these in order and stop at the first yes
(irreversibility first; a choice that belongs to the user outranks whether a
model could make it).

| # | Question | If yes |
|---|---|---|
| 1 | Does it change anything outside this run: send, post, write to a real system, overwrite, delete? | `side_effect` (the user approves it before it runs) |
| 2 | Is it a choice only the user can make: a preference, risk tolerance, threshold, scope? | `decision` (a decision stage or a question; never a child) |
| 3 | Could ordinary code, given the stage's inputs, decide whether the output is correct? | `deterministic` (program + gate, never item by item) |
| 4 | Otherwise. | `judgment` (a schema; batched if many items; a gate or reviewer) |

`kind.reason` is one line; for `judgment` it says why a program cannot decide.
The common mistake is sending mechanical work to a model.

Question 3 means parsing, matching, arithmetic, lookups, format rules; not "a
model could write a program that guesses". Deterministic: the rule can be
written before seeing the data, two careful people always agree, the output
can be recomputed. Judgment: the rule needs examples ("things like…"), it
depends on intent, tone or meaning, careful people could disagree.

## The split rule

Mostly mechanical work with a messy tail (most PDFs have text, some are
scans) is neither all judgment nor all script. Split it:

1. A `deterministic` script stage handles what it can and writes the rest to
   a leftovers file (a `writes` entry with `path`, a reason per item).
2. A `judgment` stage (`map` if many) reads only the leftovers, with a schema
   matching the script's rows.
3. Downstream stages consume both.

Split when the tail is plausible from what you observed or from the input
(scans, free text, mixed senders, human-typed values), not for uniform
machine-generated input. Mirror case: a cheap mechanical pre-filter (only
emails with a PDF can be invoices) goes in a script stage before the judgment
stage.

## Examples

| Step | Kind | Why |
|---|---|---|
| .eml headers to rows | deterministic | fixed fields, recountable |
| Is this email a supplier invoice? | judgment | forwarded and inline invoices defeat rules |
| Match invoice to PO by number and amount | deterministic | exact rule |
| How big a difference is a mismatch? | decision | the user's tolerance |
| Summarise each supplier's complaints | judgment | meaning, not fields |
| Draft replies in Gmail; overwrite last month's report | side_effect | writes to a real system; replaces what exists |
