# Agent state machine

For contributors: where an agent can stand (its **state**) and the moves between states, as the scripts enforce them. If you only run agents, `/agent-status` tells you your state and what you can do next. Why the rules live in one spec is in [Design decisions](design.md#the-state-machine-is-a-spec-in-code).

## How it is defined

Everything is in [`agent_state.py`](../.pi/skills/agent/scripts/agent_state.py), written like a [Quint](https://github.com/quint-co/quint) spec:

- **`State`** holds the facts about the agent, read once per call (`read_state`).
- **An action** is one move: it checks its **guards**, then returns the states the move can lead to. A guard that fails refuses the move with its reason.
- **`require(agent_dir, <action>)`** is the first thing every script calls. Checks on a move's own arguments (which wave, which stage, locked keys, the critic's run id) stay in the scripts.
- **`next`**, in every agent tool's result, is the state's name, the moves whose guards pass, and a hint. A refusal and `next` come from the same function, so they can't disagree. `/agent-status` shows the same.
- **`INVARIANTS`** must hold in every state.

## The state

`State` has seven fields the moves depend on, and three for people:

| Field | Means | Read from |
| --- | --- | --- |
| `run` | the active run's id, or none (at most one) | `runs/*/run.yaml` with `status: active` |
| `wave` | a wave of that run is launched and not yet recorded as finished | `run.yaml` `stages`: `start-wave` sets them running, `finish-wave` clears them |
| `plan_error` | why the checked-out `plan.yaml` can't run; none when it can | `plan_validate.check_ready`: invalid, `status` not `ready`, or edited since its critique was recorded (on a branch: differs from `main`'s without a critique) |
| `main_plan_error` | the same for `main`'s plan, which a new run starts from | the same, on `main` |
| `has_run` | the agent has had a run | `runs/`, else `main`'s history (`agent_repo.has_run`): `runs/` isn't in git, but a run's first commit and every merge are |
| `branch` | `main`, `run` (`run/<id>`), `change` (`revise/…`, `improve/…`) or `other` (one the agent didn't make) | git |
| `stuck` | during a run, its branch isn't checked out and files are left uncommitted, so a script can't switch back | `agent_repo.find_off_branch` |
| `branch_name`, `off_branch`, `last_launch` | the branch's full name; what to do when off the run's branch; the latest launch | for people only |

## The states

A state is named `<place>_<status>`, from the fields in this order:

1. A run is active: `run_wave` while a wave runs, else `run_ready`, or `run_draft` when the plan can't run.
2. Else on `main`: `plan_…` before the first run, `main_…` after it; `…_ready` or `…_draft` by `plan_error`.
3. Else by branch: `run/…` is `run_review`; `revise/…` or `improve/…` is `change_ready` or `change_draft`; any other branch is `other_branch`.

| State | What it means |
| --- | --- |
| `plan_draft` | planning: the plan is edited directly on `main` |
| `plan_ready` | ready for the first run, still open to direct edits |
| `main_ready` | between runs: start a run, or a change |
| `main_draft` | `main`'s plan was edited outside the tools; the way out is `/agent-revise` |
| `run_ready` | a run between waves: compile the next one (its preflight may ask for decisions, approvals, values or resolving) |
| `run_wave` | a launch is in pi-subagents; nothing else moves until it's recorded |
| `run_draft` | the run resolved stages; the critic must be recorded before the next launch |
| `run_review` | a run ended and left its branch to review |
| `change_draft` | a revise or improve in progress |
| `change_ready` | a change just opened, or the critic passed: review it next |
| `other_branch` | a branch the agent didn't make; no move is allowed until a person switches back |

## One agent's life

The tutorial's agent goes through these states:

| What happens | State after it |
| --- | --- |
| `/agent-new` creates the agent and drafts its plan | `plan_draft` |
| the critic's result is recorded (`agent_record_critique`) | `plan_ready` |
| `/agent-run` starts the first run (`agent_start_run`) | `run_ready` |
| the first wave launches (`agent_start_wave`) | `run_wave` |
| it ends (`agent_finish_wave`) | `run_ready` |
| `discover`'s findings fill in the stages after it (`agent_resolve`) | `run_draft` |
| the critic reviews the resolved stages | `run_ready` |
| the side effect is approved and the last wave launches | `run_wave` |
| it ends; the run leaves a branch | `run_review` |
| you merge the branch (`agent_merge`) | `main_ready` |

Between runs, `agent_state.py` reports (*emails-to-excel, 2026-10-17*):

```json
{
  "ok": true,
  "state": "main_ready",
  "moves": ["agent_start_run", "/agent-revise", "/agent-improve"],
  "hint": "start a run, or change the plan on a branch",
  "agent": {
    "run": null, "wave": false, "plan_error": null, "main_plan_error": null, "has_run": true,
    "branch": "main", "stuck": false, "branch_name": "main", "off_branch": null, "last_launch": null
  }
}
```

```mermaid
stateDiagram-v2
    direction TB
    [*] --> plan_draft: /agent-new
    plan_draft --> plan_ready: record_critique
    plan_ready --> plan_draft: edit after the critique
    plan_ready --> run_ready: start_run
    main_ready --> run_ready: start_run

    run_ready --> run_wave: compile, launch, start_wave
    run_wave --> run_ready: finish_wave
    run_ready --> run_draft: resolve
    run_draft --> run_ready: record_critique

    run_wave --> run_review: last finish_wave (commits)
    run_wave --> main_ready: last finish_wave (no commits)
    run_ready --> run_review: stop_run
    run_draft --> run_review: stop_run
    run_review --> main_ready: merge / keep / discard

    main_ready --> change_ready: /agent-revise, /agent-improve
    change_ready --> change_draft: edit plan.yaml
    change_draft --> change_ready: record_critique
    change_ready --> main_ready: merge / keep / discard
    change_draft --> main_ready: keep / discard
    run_review --> run_ready: start_run (branch kept)
    change_ready --> run_ready: start_run (branch kept)
    change_draft --> run_ready: start_run (branch kept)

    main_draft --> change_draft: /agent-revise
    other_branch --> main_ready: git switch main (by hand)
```

Not drawn: moves that stay in their state (a decision, a second resolve, a compile that answers with a preflight, a critique in `run_review`), a run that committed nothing (it has no branch, so stopping or finishing it goes straight to `main_ready`), and the hand edits that lead to `main_draft` and `other_branch`.

## Moves

| Move | From | Guards | To |
| --- | --- | --- | --- |
| `agent_start_run` | `plan_ready`, `main_ready`, `run_review`, `change_draft`, `change_ready` | no run; a known branch; `main`'s plan can run | `run_ready`, on `main` (an open branch is kept for review) |
| `agent_compile_wave` | `run_ready`, `run_draft` | a run between waves; not `stuck` | the same; a plan that can't run comes back in the preflight, not as a refusal |
| `agent_start_wave` | `run_ready` | a run between waves; the plan can run | `run_wave` |
| `agent_finish_wave` | `run_wave` | a wave running | `run_ready`; after the last wave `run_review`, or `main_ready` if nothing was committed |
| `agent_record_decision` | `run_ready`, `run_draft` | a run between waves | the same |
| `agent_resolve` | `run_ready`, `run_draft` | a run between waves; not `stuck` | `run_draft` |
| `agent_record_critique` | every state but `run_wave`, `main_…` and `other_branch` | no wave running; a known branch; not on `main` once a run exists; not `stuck` | its place's `…_ready` if the plan validates, else unchanged |
| `agent_stop_run` | `run_ready`, `run_draft` | a run between waves | `run_review`, or `main_ready` if nothing was committed |
| `agent_merge` | `run_review`, `change_ready`, `change_draft` | no run; a branch other than `main`; for merge, a plan that can run | `main_ready` (`main_draft` if a keep or discard leaves `main`'s plan unable to run) |
| `/agent-revise` | `plan_…`, `main_…`, `change_…` | no run; a known branch; not a `run/…` branch awaiting review | `change_ready` (`change_draft` from `main_draft`); planning and an open change stay where they are |
| `/agent-improve` | `main_…`, `change_…` | as `/agent-revise`, and the agent has had a run | as `/agent-revise` |
| edit `plan.yaml` | `plan_…`, `change_…` | no run; a change branch, or `main` before the first run | `…_draft` until the critic is recorded again |

The scripts add their own checks on arguments: `agent_finish_wave` needs pi-subagents to have finished the wave and to have a record of it (or a `result` and `status` passed in); `agent_record_critique` needs a completed critic child with that run id and allows two passes each time a run resolves; `agent_resolve` refuses locked keys, stages that ran and changed side effects; compiling refuses a wave launched but not recorded. A move that commits during a run first switches back to the run's branch, which `stuck` prevents.

## Invariants

The spec's `INVARIANTS` hold in every state:

- a wave runs only in an active run;
- an active run is one the agent has had;
- only a run between waves can be `stuck`;
- on `main`, `main`'s plan is the plan (`main_plan_error` = `plan_error`).

The walk checks these on the real files after every move:

- at most one run is active, with at most one wave running;
- nothing launches while a wave runs, and only a ready plan launches;
- a run starts only from a ready plan on `main`;
- `main` changes only by a merge, or by planning before the first run.

## Tests

[`tests/test_state_machine.py`](../tests/test_state_machine.py) checks the spec three ways:

- **a model check:** every state the actions reach from a new agent keeps the invariants and has a way out;
- **a walk** of the real scripts from every configuration it reaches, including what a person does by hand (edit the plan, switch branches, leave files uncommitted): a move `next` doesn't list is refused and changes nothing;
- **conformance:** each real move lands in a state its action predicted.

## Outside the spec

- **Hand edits to `gates/`, `scripts/`, `.pi/agents/` and packages** are allowed wherever the plan may be edited, plus in `run_review` and during a run, but never on `main` once a run exists. The extension's `tool_call` hook enforces this and the plan rule above.
- **Changes the hook can't see** (your editor, a shell command it doesn't parse) aren't refused. Once a run exists, `agent_start_run` and a new change commit them on the new branch, so they still reach `main` only through a review.
- **`ends_with` is not a state.** It is recorded at launch and tells the agent what follows the wave; the compile enforces `approval` (side effects need `--approve`) and `resolve` (no provisional stages), not `review`.

## Limits

- **pi-subagents' records are internal** (`scriptDigest`, `workflowChildren`, the missions store). If their shape changes, the launched-twice check and the mission lookup find nothing (they let the move through), and the critic check refuses every critique. The tests use fake records of today's shape.
- **The model check explores the spec, not the scripts**; conformance ties them together only for the moves the walk tries.
- **The walk tries each configuration once**, from the first history that reaches it, so a bug that needs a particular history (resolving twice in one run, say) can slip past.
- **Not in scope:** blocking tools in the extension by state (the scripts refuse unsafe moves) and the quality of a stage's output.
