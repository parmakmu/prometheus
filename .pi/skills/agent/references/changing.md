# Reviewing a branch and changing the plan

Part of the run protocol (`<skill>/references/run-protocol.md`); read when it sends you here.

## Reviewing a branch

After a run ends with a `review`, after a change is ready, or whenever a
`run/…`, `revise/…` or `improve/…` branch is checked out with no run active
(`<python> <skill>/scripts/agent_git.py review`, `<python>` being the
nearest `.venv/bin/python`).

1. Read `git diff main...<branch>` and summarise it in words, without
   pasting it: stages filled in or changed, gates, stage programs in
   `scripts/` (later runs execute them without a model, so they are worth a
   look), agents, the revision, the commits; for a run, what it found.
2. Ask: **merge** (future runs use it), **keep** (back to `main`, the branch
   stays), **discard**. A draft plan can only be kept or discarded.
3. `agent_merge` with the choice; on merge, pass your summary from step 1 as `summary`
   (it becomes the commit message's body on `main`).

New runs and `/agent-revise` refuse until this is done.

## Changing the plan

`/agent-revise [change]`, no run active. The command creates or continues
`revise/<date>-<n>`; the plan is left as it is. Editing `plan.yaml` unreadies it until the
critic is recorded (which raises the revision); gates, programs and subagents alone need no critic.

1. If no change was given, ask what should change.
2. Edit `plan.yaml` (anything), `gates/`, stage programs in `scripts/` and
   `.pi/agents/` directly (packages: `agent_add_package`, which asks the user), following
   `<skill>/SKILL.md` for what you touch; `agent_check`. Delete a program
   to have the next run write it again.
3. Critic (`<skill>/references/critic-contract.md`, with the changed stages in its task), then
   `agent_record_critique`, which marks the plan ready when it validates.
4. **Reviewing a branch**.
