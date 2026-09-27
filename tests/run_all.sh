#!/usr/bin/env bash
# Full regression for Prometheus. Needs the Python packages in pyproject.toml (`uv sync`, then `uv run bash tests/run_all.sh`) and node >= 22.
# Extension checks need `npm install` in the repo root and Pi installed globally with npm.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
SKILL="$ROOT/.pi/skills/workflow"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; exit 1; }
run() { "$@" || true; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "== validator"
python3 "$HERE/test_validator.py" | tail -1 | grep -q '^\([0-9]*\)/\1 passed' || { python3 "$HERE/test_validator.py"; fail validator; }
pass "validator cases"
python3 "$HERE/test_params.py" | tail -1 | grep -q '^\([0-9]*\)/\1 passed' || { python3 "$HERE/test_params.py"; fail params; }
pass "param values: .env, environment, types, secrets"

echo "== batch tool"
mkdir -p "$TMP/bt/mail"; for i in 1 2 3 4 5 6 7; do echo "m$i" > "$TMP/bt/mail/m$i.eml"; done
[ "$(run python3 "$SKILL/scripts/batch_tool.py" '{"op":"split","source":"'"$TMP"'/bt/mail","glob":"*.eml","batch":3,"outdir":"'"$TMP"'/bt/b"}' | json 'len(d["batches"])')" = 3 ] && pass "split 7 items into 3 batches" || fail split
echo '[1]' > "$TMP/bt/a.json"; echo '[2,3]' > "$TMP/bt/b.json"
[ "$(run python3 "$SKILL/scripts/batch_tool.py" '{"op":"merge","inputs":["'"$TMP"'/bt/a.json","'"$TMP"'/bt/b.json"],"output":"'"$TMP"'/bt/m.json"}' | json 'd["count"]')" = 3 ] && pass "merge json" || fail merge

echo "== Prometheus, workflow, compile, mock run"
mkdir -p "$TMP/prometheus/workflows" && cp -r "$ROOT/.pi" "$TMP/prometheus/" && pass "Prometheus copied from the repo"
[ -f "$TMP/prometheus/.pi/agents/plan-critic.md" ] && [ ! -f "$TMP/prometheus/.pi/agents/workflow-batcher.md" ] && pass "Prometheus has only plan-critic" || fail "Prometheus agents"
cmp -s "$ROOT/.pi/agents/plan-critic.md" "$SKILL/templates/workflow/.pi/agents/plan-critic.md" && pass "plan-critic identical in Prometheus and workflow template" || fail "plan-critic copies differ"
cd "$TMP/prometheus"
K=.pi/skills/workflow/scripts
run python3 $K/scaffold_workflow.py invoice-check --root workflows --request "invoices" | json 'd["ok"] and d["today"]' | grep -q "$(date +%F)" && pass "scaffold (returns today)" || fail scaffold
B="$TMP/prometheus/workflows/invoice-check"
grep -q 'name = "workflow-invoice-check"' "$B/pyproject.toml" && pass "workflow pyproject named after the slug" || fail "workflow pyproject"
[ -f "$B/.pi/agents/workflow-batcher.md" ] && [ -f "$B/.pi/extensions/workflow/index.ts" ] && pass "workflow has batcher and extension" || fail "workflow files"
G() { git -C "$B" "$@"; }
[ -d "$B/.git" ] && G log --format=%s | grep -q "Create workflow invoice-check" && pass "workflow is its own git repo with a first commit" || fail "workflow git repo"
grep -q '^\.pi/npm/' "$B/.gitignore" && [ ! -e "$B/.pi/npm" ] && ! grep -q packages "$B/.pi/settings.json" && pass "workflow declares no Pi packages (pi-subagents is global)" || fail "workflow settings"
mkdir -p "$B/.pi/skills/request-to-plan" && touch "$B/.pi/skills/request-to-plan/SKILL.md"
printf 'mail_dir: /old/mail\n' > "$B/.pi/params.yaml"
run python3 $K/scaffold_workflow.py invoice-check --root workflows --refresh | json 'd["removed"]' | grep -q "request-to-plan" && [ ! -e "$B/.pi/skills/request-to-plan" ] && [ -f "$B/.pi/skills/workflow/SKILL.md" ] && pass "refresh removes the skill's old copy (request-to-plan)" || fail "refresh legacy skill"
[ ! -e "$B/.pi/params.yaml" ] && grep -q "^MAIL_DIR='/old/mail'$" "$B/.env" && grep -q '^\.env$' "$B/.gitignore" && [ -z "$(G ls-files .env)" ] && pass "refresh moves .pi/params.yaml values into .env (not in git)" || { cat "$B/.env"; fail "params.yaml -> .env"; }
: > "$B/.env"
cp "$SKILL/examples/invoice-check.yaml" "$B/.pi/plan.yaml"
printf 'import json,sys,os\nprint(json.dumps({"ok": all(os.path.exists(p) for p in sys.argv[1:])}))\n' > "$B/gates/check_lines.py"
printf 'import os,sys\nsys.exit(0 if os.path.exists(sys.argv[1]) else 1)\n' > "$B/gates/check_report.py"
run python3 $K/compile_wave.py --workflow "$B" --check | json 'd["ok"]' | grep -q True && pass "compile --check" || fail "compile --check"
cd "$B"
sed -i.bak 's/^    batch: 50$/    batch: 3/' .pi/plan.yaml && rm .pi/plan.yaml.bak
run python3 $K/compile_wave.py | json 'd["error"]' | grep -q "no active run" && pass "compile needs an active run" || fail "compile needs an active run"
NEW="$(run python3 $K/run_log.py new --note first)"; RID="$(json 'd["run"]' <<< "$NEW")"; RUN="$(json 'd["path"]' <<< "$NEW")"; BR="$(dirname "$(dirname "$RUN")")"  # as the scripts see them (/var -> /private/var on macOS)
[ -f "$RUN/run.yaml" ] && [ -d "$RUN/work" ] && pass "new run $RID: runs/<id>/ with run.yaml and work/" || fail "new run"
grep -q "^commit: $(G rev-parse HEAD)$" "$RUN/run.yaml" && G log -1 --format=%s | grep -q "plan for run $RID" && [ -z "$(G status --porcelain)" ] && pass "new run commits the plan and records the commit" || fail "new run commit"
run python3 $K/run_log.py new | json 'd["error"]' | grep -q "is active" && pass "second run refused while one is active" || fail "second run refused"
run python3 $K/compile_wave.py | json 'd["preflight"]["missing_params"]' | grep -q mail_dir && pass "preflight asks for params" || fail "preflight asks for params"
mkdir -p "$TMP/data/mail"; for i in 1 2 3 4 5 6 7; do echo "invoice $i" > "$TMP/data/mail/m$i.eml"; done; echo x > "$TMP/data/budget.xlsx"
printf 'MAIL_DIR=data/mail\nBUDGET_FILE=%s\n' "$TMP/data/budget.xlsx" > .env
run python3 $K/compile_wave.py | json '" ".join(d["preflight"]["plan_errors"])' | grep -q "relative path" && pass "relative input path refused" || fail "relative input path refused"
printf 'MAIL_DIR=%s\nBUDGET_FILE=%s\n' "$TMP/data/nope" "$TMP/data/budget.xlsx" > .env
run python3 $K/compile_wave.py | json '" ".join(d["preflight"]["plan_errors"])' | grep -q "does not exist" && pass "missing input path refused" || fail "missing input path refused"
printf 'MAIL_DIR=%s\nBUDGET_FILE=%s\n' "$TMP/data/mail" "$TMP/data/budget.xlsx" > .env
run env MAIL_DIR="$TMP/data/nope" python3 $K/compile_wave.py --no-commit | json '" ".join(d["preflight"]["plan_errors"])' | grep -q "does not exist" && pass "the environment wins over .env" || fail "environment over .env"
C1="$(run python3 $K/compile_wave.py)"; json 'd["ok"], d["run"], d["commit"]' <<< "$C1" | grep -q "True $RID $(G rev-parse HEAD)" && [ -f "$RUN/.compiled/w1.json" ] && [ "$(G symbolic-ref --short HEAD)" = main ] && [ "$(G rev-parse r1)" = "$(G rev-parse HEAD)" ] && pass "compile w1 from main (tag r1) into the run's .compiled/" || fail "compile w1"
W1="$RUN/.compiled/w1.json"
json 'd["call"]["workflowScript"]' < "$W1" | grep -q "\"cwd\": \"$RUN/work\"\|cwd: \"$RUN/work\"" && pass "children run in the run's work/" || fail "children cwd"
json 'd["call"]["workflowScript"]' < "$W1" | grep -q "python3 $BR/gates/check_lines.py" && pass "gate ../gates/ points at the workflow's gates/" || fail "gate checks path"
run node "$HERE/mock-subagents.mjs" "$W1" > "$TMP/w1.json"
[ "$(json 'd["result"]["status"]' < "$TMP/w1.json")" = done ] && pass "w1 runs in the pi-subagents mock" || fail "w1 mock run"
[ "$(json 'sum(1 for e in d["log"] if e["key"].startswith("find-") and e["key"][5:].isdigit())' < "$TMP/w1.json")" = 3 ] && pass "map stage launched 3 batch children" || fail batches
[ -f "$RUN/outputs/Q3-invoice-variance.xlsx" ] && [ ! -e "$B/outputs" ] && pass "report written to the run's outputs/ (relative param default)" || fail report
[ "$(run node "$HERE/mock-subagents.mjs" "$W1" --fail extract | json 'd["result"]["status"]')" = failed ] && pass "failed child stops the wave" || fail "failure path"
[ "$(run node "$HERE/mock-subagents.mjs" "$W1" --block review | json 'd["result"]["status"]')" = done ] && pass "blocked only applies to <key>-review children" || fail "blocked only applies to <key>-review children"
run python3 $K/run_log.py start --wave w2 --subagent-run-id wf-0 | json 'd["error"]' | grep -q "not the next wave" && pass "run_log start refuses a wave that is not next" || fail "run_log start refuses a wave that is not next"
cp .pi/plan.yaml "$TMP/plan.before"
run python3 $K/run_log.py start --wave w1 --subagent-run-id wf-1 --mission-id m-1 | json 'd["ok"]' | grep -q True && pass "run_log start" || fail "run_log start"
cmp -s "$TMP/plan.before" .pi/plan.yaml && [ "$(grep -c ': running$' "$RUN/run.yaml")" = 6 ] && pass "run_log start writes the run's state, not the plan" || { cat "$RUN/run.yaml"; fail "run_log start state"; }
run python3 $K/compile_wave.py | json 'd.get("error","")' | grep -q running && pass "running wave cannot relaunch" || fail "running wave cannot relaunch"
grep -q "commit: $(G rev-parse HEAD)" "$RUN/run.yaml" && pass "launch records the plan's commit" || fail "launch commit"
json 'd["call"]["workflowScript"]' < "$W1" | grep -q "Write this stage's program at $BR/scripts/extract_invoice_lines.py" && pass "a stage with a script and no program yet: the child writes it into the workflow's scripts/" || fail "script task (new)"
echo "# edited during the wave" >> gates/check_report.py; mkdir -p scripts && echo '"""usage: extract_invoice_lines.py MAIL OUT"""' > scripts/extract_invoice_lines.py; tree="$(G write-tree)"
(cd "$SKILL/scripts" && python3 -c "import workflow_git; print(workflow_git.commit('$B', 'x')[1])") | grep -q "a wave is running" && [ "$(G write-tree)" = "$tree" ] && pass "nothing commits while a wave runs (staged index unchanged)" || fail "commit during a wave"
python3 -c "import json; print(json.dumps(json.load(open('$TMP/w1.json'))['result']))" > "$TMP/w1-ret.json"
MAIN0="$(G rev-parse main)"
PIS="$TMP/pi-subagents/async-subagent-runs/wf-1"; mkdir -p "$PIS"   # pi-subagents' record of the async run (PI_SUBAGENTS_TEMP_ROOT)
echo '{"state": "running"}' > "$PIS/status.json"
run env PI_SUBAGENTS_TEMP_ROOT="$TMP/pi-subagents" python3 $K/run_log.py finish --wave w1 | json 'd["error"]' | grep -q "still running" && pass "finish refuses while pi-subagents still runs the wave" || fail "finish while running"
python3 -c "import json; json.dump({'state': 'complete', 'workflow': {'value': json.load(open('$TMP/w1-ret.json'))}, 'totalCost': {'costUsd': 0.0123, 'inputTokens': 10, 'outputTokens': 900}}, open('$PIS/status.json', 'w'))"
FIN="$(run env PI_SUBAGENTS_TEMP_ROOT="$TMP/pi-subagents" python3 $K/run_log.py finish --wave w1)"
json 'd["wave_status"], d["run_status"], d["ends_with"]' <<< "$FIN" | grep -q "done active none" && [ -f "$RUN/.compiled/w1-result.json" ] && grep -q "ends_with: none" "$RUN/run.yaml" && grep -q "cost_usd: 0.0123" "$RUN/run.yaml" && pass "finish with just the wave: outcome and cost read from pi-subagents" || { echo "$FIN"; fail "run_log finish"; }
json '"structuredOutput" not in json.dumps(d["children"]) and set(d["children"]["extract"]) == {"status", "output"}' <<< "$FIN" | grep -q True && pass "finish returns a children summary, not their outputs" || fail "children summary"
[ "$(grep -c ': done$' "$RUN/run.yaml")" = 6 ] && ! grep -q ': running$' "$RUN/run.yaml" && pass "finish marks the wave's stages done" || fail "finish marks the wave's stages done"
[ "$(G symbolic-ref --short HEAD)" = "run/$RID" ] && [ "$(G rev-parse main)" = "$MAIN0" ] && G show --stat --format=%s HEAD | grep -q "scripts/extract_invoice_lines.py" && G log -1 --format=%s | grep -q "wave w1: files the wave wrote" && pass "files a wave wrote (its program, a gate edited mid-run) are committed at finish on run/$RID; main untouched" || fail "finish commit on the run branch"
run python3 $K/compile_wave.py | json 'd["wave"], [x["stage"] for x in d["preflight"]["decisions_needed"]]' | grep -q "w2 \['approve-drafts'\]" && pass "next wave is w2 and asks for the decision" || fail "w2 asks for the decision"
run python3 $K/run_log.py decision --stage drafts --answer '"yes"' | json 'd["ok"]' | grep -q False && pass "decision refused for a non-decision stage" || fail "decision refused for a non-decision stage"
run python3 $K/run_log.py decision --stage approve-drafts --answer '{"approved": true}' | json 'd["path"]' | grep -q "^$RUN/work/" && pass "decision recorded in the run's work/" || fail "decision recorded"
run python3 $K/compile_wave.py | tee "$TMP/appr.json" | json '[(x["stage"], bool(x["done_when"]), len(x["consumes"])) for x in d["preflight"]["approvals_needed"]]' | grep -q "(.drafts., True, 2)" && pass "side effect needs approval (preflight says what it consumes and when it is done)" || { cat "$TMP/appr.json"; fail "side effect needs approval"; }
run python3 $K/compile_wave.py --approve drafts | json 'd["call"].get("missionId")' | grep -q m-1 && pass "approved w2 compiles and reuses the run's mission" || fail "approved w2 compiles and reuses the mission"
json 'd["call"]["workflowScript"]' < "$W1" | grep -q 'acceptance: {"level": "verified", "preserveStagedIndex": true, "review": {"required": false}, "verify": \[{"id": "gate"' && pass "gated stages compile to explicit acceptance (no git or implicit review)" || fail "explicit acceptance"
PE="python3 $K/plan_edit.py"
run $PE replan --change '{"stages":[{"key":"find","kind":"judgment"}]}' | json '" ".join(d["errors"])' | grep -q "history" && pass "replan refuses a stage done in the active run" || fail "replan refuses a done stage"
run $PE replan --change '{"waves":[]}' | json '" ".join(d["errors"])' | grep -q "not part of the plan" && pass "replan refuses waves" || fail "replan refuses waves"
run $PE replan --change '{"stages":[{"key":"drafts","kind":"deterministic"}]}' | json '" ".join(d["errors"])' | grep -q "must stay kind: side_effect" && pass "replan refuses to weaken a side effect" || fail "replan refuses to weaken a side effect"
run $PE replan --change '{"goal":"something else"}' | json '" ".join(d["errors"])' | grep -q "locked" && pass "replan refuses to change the goal" || fail "replan refuses to change the goal"
python3 -c "
import json, sys; sys.path.insert(0, '$K'); from validate_plan import load_yaml
st = next(s for s in load_yaml(open('.pi/plan.yaml'))['stages'] if s['key'] == 'approve-drafts')
st = json.loads(json.dumps(st, default=str)); st['title'] = 'Confirm the drafts (replanned)'
print(json.dumps({'stages': [st]}))" > "$TMP/change.json"
replanned="$(run $PE replan --change "$TMP/change.json" || true)"
json 'd["ok"], d["revision"], d["status"]' <<< "$replanned" | grep -q "True 2 draft" && [ ! -e .pi/history ] && G log -1 --format=%s | grep -q "^r2 draft: replan$" && [ "$(json 'd["commit"], d["previous_commit"]' <<< "$replanned")" = "$(G rev-parse HEAD) $(G rev-parse HEAD~1)" ] && G show HEAD~1:.pi/plan.yaml | grep -q "^revision: 1$" && pass "replan commits (previous revision in git history, no .pi/history)" || { echo "$replanned" | head -30; G log --format=%s | head; fail "replan of a pending stage"; }
run $PE critique --result '{"run_id":"c-2","verdict":"pass","findings":[]}' | json 'd["ready"]' | grep -q True && G log -1 --format=%s | grep -q "^r2 ready: critic pass$" && ! G rev-parse -q --verify r2 >/dev/null && pass "recorded critique marks the plan ready (committed on the run branch, no tag yet)" || fail "record critique"
run $PE params --values '{"mail_dir":"data/mail"}' | json '" ".join(d["errors"])' | grep -q "relative path" && pass "params: relative input path reported" || fail "params: relative path"
run $PE params --values "{\"mail_dir\":\"$TMP/data/mail\"}" | json 'd["errors"]' | grep -q '^\[\]$' && grep -q "^MAIL_DIR='$TMP/data/mail'$" .env && pass "params: absolute path accepted, written to .env" || fail "params: absolute path"
grep -q "mail_dir: $TMP/data/mail" "$RUN/run.yaml" && pass "a launch records the param values it ran with" || fail "launch params"
BG="python3 $K/workflow_git.py"
run $BG merge --action merge | json 'd["error"]' | grep -q "still active" && pass "no merge while the run is active" || fail "merge refused during a run"
STOP="$(run python3 $K/run_log.py stop --note test)"; json 'd["run_status"], d["review"]["branch"], len(d["review"]["commits"])' <<< "$STOP" | grep -q "stopped run/$RID 3" && grep -q '^status: stopped$' "$RUN/run.yaml" && pass "stop keeps the run (stopped) and returns its branch for review" || { echo "$STOP"; fail "stop run"; }
run python3 $K/run_log.py new | json 'd["error"]' | grep -q "not reviewed" && pass "no new run while a run branch waits for review" || fail "new run refused on a branch"
M="$(run $BG merge --action merge)"; json 'd["ok"], d["revision"], d["unmerged"]' <<< "$M" | grep -q "True 2 \[\]" && [ "$(G symbolic-ref --short HEAD)" = main ] && G merge-base --is-ancestor "$MAIN0" main && [ "$(G rev-parse r2)" = "$(G rev-parse main)" ] && ! G rev-parse -q --verify "run/$RID" >/dev/null && pass "merge: main fast-forwards to the run's changes, tag r2, branch deleted" || { echo "$M"; fail "merge run branch"; }
RID2="$(run python3 $K/run_log.py new | json 'd["run"]')"
[ "$RID2" != "$RID" ] && run python3 $K/compile_wave.py | json 'd["ok"], d["run"], d["wave"]' | grep -q "True $RID2 w1" && pass "the next run starts from the merged main at w1" || fail "second run"
json 'd["call"]["workflowScript"]' < "$B/runs/$RID2/.compiled/w1.json" | grep -q "This stage's program is $BR/scripts/extract_invoice_lines.py" && pass "the next run reuses the merged program" || fail "script task (reuse)"
run python3 $K/validate_plan.py .pi/plan.yaml | json 'd["summary"]["last_run"]["id"], d["summary"]["last_run"]["runs"]' | grep -q "$RID2 2" && pass "the check reports the last run (the parent need not look in runs/)" || fail "last_run in the summary"
run python3 $K/run_log.py start --wave w1 --subagent-run-id wf-9 >/dev/null; mkdir -p "$TMP/pi-subagents/async-subagent-runs/wf-9"
echo '{"state": "failed", "error": "workflow timed out after 600000 ms", "workflow": {"failureKind": "timeout"}}' > "$TMP/pi-subagents/async-subagent-runs/wf-9/status.json"
run env PI_SUBAGENTS_TEMP_ROOT="$TMP/pi-subagents" python3 $K/run_log.py finish --wave w1 | json 'd["wave_status"], d["failure_kind"], "timed out" in d["error"]' | grep -q "pending timeout True" && pass "a workflow that failed without returning (timeout) is recorded as failed, with pi-subagents' failureKind" || fail "failure without a return value"
run $BG begin-change | json 'd["error"]' | grep -q "is active" && pass "/workflow-revise refused while a run is active" || fail "begin-change during a run"
run python3 $K/run_log.py stop | json 'd["review"]' | grep -q '^None$' && pass "a run that changed nothing has no branch to review" || fail "stop without changes"
BC="$(run $BG begin-change)"; json 'd["ok"], d["branch"], d["revision"]' <<< "$BC" | grep -q "True change/$(date +%F)-1 3" && grep -q '^status: draft$' .pi/plan.yaml && pass "/workflow-revise: change branch from main, revision 3, draft" || { echo "$BC"; fail "begin-change"; }
run $PE replan --change '{"note":"x"}' | json '" ".join(d["errors"])' | grep -q "no run is active" && pass "replan needs an active run" || fail "replan without a run"
python3 -c "p='.pi/plan.yaml'; s=open(p).read(); open(p,'w').write(s.replace('goal: >-\n', 'goal: >-\n  Changed on a branch.\n', 1))"
run $PE critique --result '{"run_id":"c-3","verdict":"pass","findings":[]}' | json 'd["ready"]' | grep -q True && pass "direct edits on the change branch, critique marks it ready" || fail "change critique"
M2="$(run $BG merge --action merge)"; json 'd["ok"], d["revision"]' <<< "$M2" | grep -q "True 3" && G show main:.pi/plan.yaml | grep -q "Changed on a branch" && [ "$(G rev-parse r3)" = "$(G rev-parse main)" ] && pass "merged change: goal changed on main, tag r3" || { echo "$M2"; fail "merge change"; }
run $BG begin-change | json 'd["ok"]' | grep -q True && run $BG merge --action merge | json 'd["error"]' | grep -q "only a ready, valid plan" && run $BG merge --action discard | json 'd["ok"], d["revision"]' | grep -q "True 3" && [ -z "$(G branch --list 'change/*')" ] && pass "a draft change cannot merge; discarded: main unchanged, branch deleted" || fail "discard change"
RID3="$(run python3 $K/run_log.py new | json 'd["run"]')"

echo "== export"
cd "$TMP/prometheus"
run python3 $K/export_workflow.py workflows/invoice-check "$TMP/fresh" | json 'd["ok"], d["valid"]' | grep -q "True True" && [ -z "$(ls -A "$TMP/fresh/invoice-check/runs")" ] && [ "$(git -C "$TMP/fresh/invoice-check" rev-list --count HEAD)" = 1 ] && pass "fresh export: the plan without runs, in a new repo" || fail "fresh export"
grep -q '^MAIL_DIR=$' "$TMP/fresh/invoice-check/.env" && ! grep -q "$TMP/data" "$TMP/fresh/invoice-check/.env" && pass "export: .env lists the variables, without values" || fail "export .env"
mkdir -p workflows/invoice-check/.pi/npm/node_modules workflows/invoice-check/.venv/bin
run python3 $K/export_workflow.py workflows/invoice-check "$TMP/elsewhere" --keep-state | json 'd["ok"]' | grep -q True && [ -f "$TMP/elsewhere/invoice-check/runs/$RID/run.yaml" ] && [ "$(git -C "$TMP/elsewhere/invoice-check" rev-parse HEAD)" = "$(G rev-parse HEAD)" ] && pass "keep-state export keeps the runs and the git history" || fail "keep-state export"
[ ! -e "$TMP/elsewhere/invoice-check/.pi/npm" ] && [ ! -e "$TMP/elsewhere/invoice-check/.venv" ] && ! grep -q "$TMP/data" "$TMP/elsewhere/invoice-check/.env" && pass "export leaves out .pi/npm, .venv and .env values (keep-state too)" || fail "export leaves out .pi/npm, .venv and .env"

# The installed Pi (global npm) provides pi-coding-agent and the typebox it ships; the repo's
# node_modules only adds typescript and @types/node.
PI_PKG="$(npm root -g 2>/dev/null)/@earendil-works/pi-coding-agent"
if [ -d "$ROOT/node_modules/typescript" ] && [ -d "$PI_PKG" ]; then
  echo "== extensions (pi $(json 'd["version"]' < "$PI_PKG/package.json"))"
  NM="$TMP/node_modules"; mkdir -p "$NM/@earendil-works" "$NM/@types" "$TMP/.ext"
  ln -s "$PI_PKG" "$NM/@earendil-works/pi-coding-agent"
  ln -s "$PI_PKG/node_modules/typebox" "$NM/typebox"
  ln -s "$ROOT/node_modules/@types/node" "$NM/@types/node"
  cp "$ROOT/.pi/extensions/prometheus/index.ts" "$TMP/.ext/prometheus.ts"; cp "$SKILL/templates/workflow/.pi/extensions/workflow/index.ts" "$TMP/.ext/workflow.ts"
  cp "$HERE/tsconfig.json" "$TMP/tsconfig.json"
  "$ROOT/node_modules/.bin/tsc" -p "$TMP/tsconfig.json" && pass "typecheck against pi types" || fail "typecheck against pi types"
  cp "$HERE/ext-harness.mjs" "$TMP/h.mjs"
  echo '[{"cmd":"workflow-list","args":""},{"tool":"workflow_check","params":{"slug":"invoice-check"}},{"cmd":"workflow-list","complete":"inv"}]' > "$TMP/s.json"
  (cd "$TMP" && node --experimental-strip-types --no-warnings h.mjs "$TMP/prometheus/.pi/extensions/prometheus/index.ts" "$TMP/prometheus" "$TMP/s.json") > "$TMP/ext.json"
  [ "$(json 'len(d["commands"]), len(d["tools"])' < "$TMP/ext.json")" = "4 2" ] && pass "Prometheus extension registers 4 commands, 2 tools" || fail "Prometheus extension"
  [ "$(json 'd["steps"][1]["result"]["valid"]' < "$TMP/ext.json")" = True ] && pass "workflow_check tool" || fail "workflow_check"
  [ "$(json 'd["steps"][2]["completions"][0]["value"]' < "$TMP/ext.json")" = invoice-check ] && pass "slug completion" || fail completion
  uv venv -q "$TMP/bare" && echo '[{"tool":"workflow_check","params":{"slug":"invoice-check"}}]' > "$TMP/s0.json"
  (cd "$TMP" && PI_WORKFLOW_PYTHON="$TMP/bare/bin/python" node --experimental-strip-types --no-warnings h.mjs "$TMP/prometheus/.pi/extensions/prometheus/index.ts" "$TMP/prometheus" "$TMP/s0.json") > "$TMP/ext0.json"
  json 'd["steps"][0]["result"]["errors"][0]' < "$TMP/ext0.json" | grep -q "Do not install packages yourself" && pass "missing packages: tool says ask for uv sync" || fail "missing packages message"
  echo '[{"cmd":"workflow-status","args":""},{"tool":"workflow_compile","params":{"approve":["drafts"]}},{"toolCall":{"toolName":"edit","input":{"path":".pi/plan.yaml"}}},{"toolCall":{"toolName":"bash","input":{"command":"sed -i s/x/y/ .pi/plan.yaml"}}},{"toolCall":{"toolName":"write","input":{"path":"runs/x/run.yaml"}}},{"toolCall":{"toolName":"read","input":{"path":".pi/plan.yaml"}}},{"toolCall":{"toolName":"bash","input":{"command":"git commit -am x"}}},{"toolCall":{"toolName":"bash","input":{"command":"git log --oneline"}}},{"toolCall":{"toolName":"read","input":{"path":".env"}}},{"toolCall":{"toolName":"bash","input":{"command":"cat ./.env"}}},{"toolCall":{"toolName":"bash","input":{"command":"source .venv/bin/activate && ls"}}},{"env":"MAIL_DIR"}]' > "$TMP/s2.json"
  (cd "$TMP" && node --experimental-strip-types --no-warnings h.mjs "$B/.pi/extensions/workflow/index.ts" "$B" "$TMP/s2.json") > "$TMP/ext2.json"
  [ "$(json 'len(d["commands"]), len(d["tools"])' < "$TMP/ext2.json")" = "3 11" ] && pass "workflow extension registers 3 commands, 11 tools" || fail "workflow extension"
  [ "$(json 'd["steps"][1]["result"]["ok"]' < "$TMP/ext2.json")" = True ] && pass "workflow_compile tool" || fail "workflow_compile"
  [ "$(json '[s["blocked"] for s in d["steps"][2:6]]' < "$TMP/ext2.json")" = "[True, True, True, False]" ] && pass "once a run exists: edits to the plan and run state blocked, read allowed" || fail "plan edit guard"
[ "$(json '[s["blocked"] for s in d["steps"][6:8]]' < "$TMP/ext2.json")" = "[True, False]" ] && pass "git commands that change the workflow's repo blocked, git log allowed" || fail "git guard"
[ "$(json '[s["blocked"] for s in d["steps"][8:11]]' < "$TMP/ext2.json")" = "[True, True, False]" ] && pass ".env blocked for the model (read, bash), .venv not" || fail ".env guard"
[ "$(json 'd["steps"][11]["env"]' < "$TMP/ext2.json")" = "$TMP/data/mail" ] && pass "the extension loads .env into pi's environment (children inherit it)" || fail ".env loaded into the environment"
(cd "$B" && python3 $K/run_log.py stop >/dev/null && python3 $K/workflow_git.py begin-change >/dev/null)
echo '[{"toolCall":{"toolName":"edit","input":{"path":".pi/plan.yaml"}}},{"toolCall":{"toolName":"bash","input":{"command":"cat .pi/plan.yaml"}}},{"toolCall":{"toolName":"write","input":{"path":"runs/x/run.yaml"}}},{"toolCall":{"toolName":"edit","input":{"path":".env"}}}]' > "$TMP/s3.json"
(cd "$TMP" && node --experimental-strip-types --no-warnings h.mjs "$B/.pi/extensions/workflow/index.ts" "$B" "$TMP/s3.json") > "$TMP/ext3.json"
[ "$(json '[s["blocked"] for s in d["steps"]]' < "$TMP/ext3.json")" = "[False, False, True, True]" ] && pass "on a change branch (no run active): plan editable, run state and .env still guarded" || fail "change-branch guard"
else
  echo "== extensions skipped (needs npm install here, and pi installed globally with npm)"
fi
echo "all passed"
