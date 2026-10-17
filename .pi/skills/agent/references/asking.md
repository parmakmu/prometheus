# Asking the user

Part of the run protocol (`<skill>/references/run-protocol.md`); read before you ask the user anything.

With a question tool (`ask_user_question` or similar), every question with a
fixed set of answers goes through it, all of one moment's questions in one
call: new run and changed input, decisions, approvals, what to do after a
failed or blocked wave, merge / keep / discard. Every question must stand on
its own, because the dialog may show it without the chat above it: the
`question` text says what happened, why it matters to the user, and what the
answer changes, and each option's `description` says what happens if it is
chosen. Keep each `header` to 16 characters (the tool rejects longer). A bare
"Apply or skip?" or "Keep approval?" is not enough.

Write questions and options for the person, in their terms: their emails, their
workbook, what a run costs them or asks of them, what will be different next
time. Leave out the agent's internals: tool names, session files, record
numbers, state names, waves, YAML keys, commits, "uncommitted", branch
mechanics. Name a file only if the user works with it (an input folder, an
output). Not "agent_check ran after agent_record_critique (records
69–72)" but "after the reviewer approved a change, the agent checked the plan
again, which it didn't need to". Technical detail (evidence, the exact edit, a
diff) goes in chat just before the question, for whoever wants it.
Values (params, paths, what to change) are asked in plain chat. Without the
tool, ask in chat and end the turn.
