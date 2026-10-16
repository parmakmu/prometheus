/**
 * Agent: commands and tools for running this agent.
 *
 * Commands
 *   /agent-run [note]        plan, continue or run from where it stands (model)
 *   /agent-auto              run waves with no model until one needs a person, fails, or the run ends
 *   /agent-status            where the agent stands (no model)
 *   /agent-history [n]       the last n runs, and how each stage went in them (no model)
 *   /agent-prune [keep]      delete old finished runs' work/ folders, keeping the newest (default 10; no model)
 *   /agent-revise [change]   change the plan on a branch, for review (no run active; model)
 *   /agent-bundle [run] [--with-files]   pack a run's evidence into one file, for /agent-improve where the agent is developed
 *   /agent-improve [bundle.tgz …] [focus]   propose fixes from the sessions and runs (and released runs' bundles),
 *                            applied on an improve/ branch (no run active; model)
 *
 * Tools for the model (the mechanical steps of the run protocol)
 *   agent_check            validate the plan and compile-check its waves
 *   agent_sessions         list this agent's Pi sessions, or read a bounded part of one
 *   agent_new_run          start a run (runs/<id>/)
 *   agent_stop_run         stop the active run (kept in runs/); returns its branch for review
 *   agent_compile          compile the active run's next wave into the exact subagent call
 *   agent_record_decision  write the user's answer for a decision stage
 *   agent_log_start        record a wave launch
 *   agent_log_finish       record a wave result
 *   agent_resolve           resolve provisional stages after their discovery (or change pending ones, asked)
 *   agent_record_critique  record a plan-critic result; marks the plan ready if it validates
 *   agent_set_params       set param values in .env (not secrets)
 *   agent_merge            finish the review of a run or change branch: merge, keep or discard
 *
 * The plan is reusable; each execution of it is a run, a folder runs/<id>/ with its
 * state (run.yaml), the children's work/ and compiled waves. Waves are not written in the
 * plan; the scripts derive them from the stages and the active run's stage status.
 * The agent is its own git repo (agent_git.py). main holds the reviewed plan; a
 * run's changes go to run/<id>, a /agent-revise change to revise/<date>-<n>, /agent-improve to improve/<date>-<n>, and each
 * reaches main only when the user merges it. The scripts commit (never while a wave runs:
 * pi-subagents checks the repo's staged index around writer children); git commands that
 * change the repo are blocked for the model.
 *
 * Param values live in .env (per machine, may hold secrets), which the model may not read or change
 * directly. It is not loaded into pi's environment: the scripts read it, the compiler writes values into
 * tasks and commands, and only a stage program or gate the batcher runs gets the secrets it names. Every
 * child shares pi's environment and sandbox, and one reading outside content can be told to leak it. Once a run exists, direct edits to runs/<id>/run.yaml are blocked,
 * and plan.yaml may be edited directly only on a change branch with no run active;
 * during a run the plan changes through agent_resolve, which refuses to touch what ran,
 * the goal or a side effect's approval.
 *
 * The logic lives in the skill's Python scripts (.pi/skills/agent/scripts);
 * this file runs them and formats their JSON.
 */
import { defineTool, type BeforeAgentStartEvent, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type, type Static, type TSchema } from "typebox";
import { randomUUID } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";


// --- prompts sent to the model ------------------------------------------------

const RUN_PROMPT = (note: string) => `Run this agent. Read \`.pi/skills/agent/references/run-protocol.md\`
and follow it from "Every start": check, then plan, resolve or run the next
wave, whichever the plan needs.

Use the agent tools for every mechanical step: \`agent_check\`,
\`agent_new_run\`, \`agent_compile\`, \`agent_record_decision\`,
\`agent_log_start\`, \`agent_log_finish\`, \`agent_resolve\`,
\`agent_stop_run\` and \`agent_merge\`. The subagent tool is already active: pass
the \`call\` that \`agent_compile\` returns to it unchanged, without calling
subagents_enable or listing agents first. Ask every question that has a fixed set of
answers with the question tool (run-protocol.md, "Asking the user").

Note from the user for this run (may be empty): ${note}`;

/** Starts every /agent-auto hand-over: its turn ends the autopilot's hold on the run tools. */
const HANDOVER_MARK = "/agent-auto ran";
/** pi-subagents wakes an idle parent with its notices, then a user message that starts with this; a run it starts
 * with a message of its own has a `subagent…` custom type. */
const PARENT_WAKE = "Inspect subagent updates above.";
const isNotice = (event: BeforeAgentStartEvent) =>
  event.messageType === "custom" ? event.message.customType.startsWith("subagent") : event.prompt.startsWith(PARENT_WAKE);
/** The tools /agent-auto calls itself while it runs; the model's are refused meanwhile. */
const AUTO_OWNS = new Set(["agent_compile", "agent_log_start", "agent_log_finish", "agent_new_run", "agent_stop_run", "subagent"]);

const FINISH_PROMPT = (result: string) => `${HANDOVER_MARK} this agent's run to the end. Its \`report\` below is the final
report, settled by code (deliverables, how each was checked, criteria): do what \`report.do\` says, nothing more. Pass \`mission_close\` to the subagent tool
unchanged (none when nothing was launched: skip it). Do not start anything else.

${result}`;

const REVISE_PROMPT = (change: string, branch: string, mode: string) => {
  const where =
    mode === "planning"
      ? "No run has started yet: plan (or continue planning) on main, editing plan.yaml directly."
      : `You are on the change branch "${branch}", created from main with its plan unchanged. Edit
plan.yaml, gates/, scripts/ and .pi/agents/ directly (packages: agent_add_package); anything in the plan may change. End with the
user's review of the branch and agent_merge (merge, keep or discard).`;
  return `Change this agent's plan following the "Changing the plan" section
of \`.pi/skills/agent/references/changing.md\`. Launch no work stages.

${where}

Change requested (may be empty: then ask what to change, or continue the change on this branch): ${change}`;
};

const IMPROVE_PROMPT = (focus: string, branch: string, digest: string) => `Improve this agent from how it actually went. You are on the improve
branch "${branch}". Launch no work stages.

Below is a digest of what is new since the last review: this agent's sessions and runs (user
messages, failed or blocked tool results, repeated calls, cost, launches that failed or ran twice).
It only points at evidence. Do not open the raw session files (their lines are too long to read) or parse them yourself;
before proposing anything, open only the files a finding points at (the digest already summarises
runs: no need to read every run.yaml, gate and script), read more of a session with agent_sessions
(show <file> around a failure's \`at\`) only where a finding needs it, and check with \`git branch\`
any branch you mean to cite.

\`imported\` lists runs from bundles, made somewhere else: a release of this agent (\`kind: release\`) or
a copy of it run there (\`kind: run\`, with that copy's own sessions in \`agent_sessions\`); \`commit\` is the
code that ran. Treat them like runs: a failure there is evidence, one that a caller's odd input explains is
still a one-off. Read a failed child's session, or one of \`agent_sessions\`, with agent_sessions (show <its
path>); its folder holds the run's records (\`run/\`, \`subagents/\`, and \`run/inputs/\` or \`run/work/\` when
it came with files). For a release's run, when the branch holds the fix, tell the user they can check it
before merging with \`prometheus run <slug>@<this branch> --bundle <the bundle file>\` (it runs the
branch's committed state on the bundle's params and files), and release it after the merge with
\`prometheus release <slug>\`.

1. Find causes, not symptoms: a gate that rejected correct output, a stage program rewritten or
   failing each run, a stage task that left something out, a question the plan should have
   settled, a refusal the user hit, work the model did by hand that a script could do. Skip
   one-offs; a proposal needs a repeat or a real cost.
2. Give each proposal: the evidence (session file or run id), the file to change, the edit, and what
   it should prevent. Targets you may change here: plan.yaml, gates/, scripts/, .pi/agents/,
   packages (agent_add_package), and rules for this agent: short lines under the heading
   "## Lessons for this agent" at the end of \`.pi/APPEND_SYSTEM.md\` (add the heading if missing;
   /agent-refresh keeps that section). Every line of it is sent with every turn, so write a rule only
   for a behaviour the evidence shows repeating, keep it to one or two sentences about this agent,
   merge or drop lines it makes redundant, and keep the section under 10 lines. Do not change the text
   above the heading, or the skill and references: they are prometheus's copies, which /agent-refresh
   overwrites. A problem there that is not specific to this agent goes in a separate list, as text for
   the user to take to prometheus.
   \`lessons\` are what earlier /agent-improve passes decided: do not re-propose a rejected one, and
   if an applied one's problem shows up again, say its fix did not work.
3. Ask the user about each proposal with the question tool (apply / skip), in plain words (run-protocol,
   "Asking the user"): what you noticed, what it costs them, what the change will do, and what skipping
   leaves. Show the evidence and the exact edit (before and after) in chat first, not in the question;
   a lesson for the agent is "the agent will stop doing X", not the rule's text. Then make the accepted
   edits following the "Changing the plan" section of
   \`.pi/skills/agent/references/changing.md\` (critic included when the plan changes; it says what to read).
   Append every proposal, applied or skipped, to \`improvements.yaml\` (a list; create it if missing):
   \`{date: <the digest's \`now\`>, target: <file>, proposal: <one line>, evidence: <session, run or imported folder>,
   decision: applied | rejected, note: <the user's reason, if given>}\`. Write it even when nothing
   was accepted; it marks these sessions as reviewed. Write it once, when the decisions are final,
   not before the critic: agent_merge commits leftover files, so never call agent_record_critique
   again just to commit it.
   End with the user's review of the branch and agent_merge (merge, keep or discard).

Focus requested (may be empty): ${focus}

Digest:
${digest}`;

// Pi opens its system prompt with this coding-assistant line; the agent orchestrates instead.
const PI_PREAMBLE =
  "You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.";
const ROLE_PREAMBLE =
  "You are the agent of one agent, operating inside pi, a coding agent harness. You orchestrate: you run the agent's plan with subagents and look after its plan, gates and scripts as the run protocol allows. You do not do a stage's work yourself.";

// --- helpers --------------------------------------------------------------------

/** The agent root: the folder holding plan.yaml and .pi/, found from `start` upwards. */
function findRoot(start: string): string | undefined {
  let dir = path.resolve(start);
  for (;;) {
    if (fs.existsSync(path.join(dir, "plan.yaml")) && fs.existsSync(path.join(dir, ".pi"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

function mustRoot(ctx: ExtensionContext): string {
  const root = findRoot(ctx.cwd);
  if (!root) throw new Error("not inside an agent folder (no plan.yaml)");
  return root;
}

/** Python for the skill scripts: $PI_AGENT_PYTHON, else the nearest .venv (the agent's
 * own after `uv sync`, or Prometheus's when the agent lives inside it), else python3. */
function pythonFor(start: string): string {
  if (process.env.PI_AGENT_PYTHON) return process.env.PI_AGENT_PYTHON;
  const bin = process.platform === "win32" ? path.join("Scripts", "python.exe") : path.join("bin", "python");
  for (let dir = path.resolve(start); ; dir = path.dirname(dir)) {
    if (fs.existsSync(path.join(dir, ".venv", bin))) return path.join(dir, ".venv", bin);
    if (path.dirname(dir) === dir) return "python3";
  }
}

/** Make pi-subagents' `subagent` tool active before a command's prompt goes out. The command is the
 * user's authorization to delegate; left to the model, subagents_enable changes the tool list mid-turn,
 * which costs two turns and drops the prompt cache.
 * pi-subagents records the selection, so it holds on resume. No-op when pi-subagents is missing. */
function activateSubagent(pi: ExtensionAPI) {
  if (!pi.getAllTools().some((tool) => tool.name === "subagent")) return;
  const active = pi.getActiveTools();
  if (!active.includes("subagent")) pi.setActiveTools([...active, "subagent"]);
}

interface ScriptResult {
  code: number;
  data: any;
  stdout: string;
  stderr: string;
}

async function runScript(
  pi: ExtensionAPI,
  root: string,
  script: string,
  args: string[],
  signal?: AbortSignal,
): Promise<ScriptResult> {
  const file = path.join(root, ".pi", "skills", "agent", "scripts", script);
  const py = pythonFor(root);
  const r = await pi.exec(py, [file, ...args], { cwd: root, timeout: 120_000, signal });
  let data: any;
  try {
    data = JSON.parse(r.stdout);
  } catch {
    data = undefined;
  }
  // A missing package fails at import, before the script prints JSON: say how to fix it.
  const missing = /No module named '([^'.]+)/.exec(r.stderr);
  const stderr = missing
    ? `Python package '${missing[1]}' is missing for ${py}. Do not install packages yourself: ask the user to run \`uv sync\` in ${root}.`
    : r.stderr;
  return { code: r.code, data, stdout: r.stdout, stderr };
}

/** Whether process `pid` still runs (EPERM: it does, under another user). */
function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e: any) {
    return e?.code === "EPERM";
  }
}

/** Why a recorded wave (run_log finish) waits for the user, or undefined when the run goes on by itself. */
function waitsForUser(d: any): string | undefined {
  if (!d?.ok) return undefined;
  if (d.run_status === "done") return `run ${d.run} is done`;
  if (d.wave_status !== "done") return `run ${d.run}: a wave did not complete${d.error ? ` (${String(d.error).slice(0, 200)})` : ""}`;
  if (d.ends_with && d.ends_with !== "none") return `run ${d.run}: a wave ended with ${d.ends_with}, which needs you`;
  if (d.next_wave?.preflight) return `run ${d.run}: the next wave needs ${Object.keys(d.next_wave.preflight).join(", ")}`;
  return undefined;
}

/** `value` without what says nothing: null, "", [] and {} (absent reads the same to the model, and costs no tokens). */
function terse(value: any): any {
  if (Array.isArray(value)) return value.map(terse);
  if (value && typeof value === "object") {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(value)) {
      const t = terse(v);
      const empty = t === null || t === undefined || t === "" || (Array.isArray(t) && !t.length) || (typeof t === "object" && !Array.isArray(t) && !Object.keys(t).length);
      if (!empty) out[k] = t;
    }
    return out;
  }
  return value;
}

/** agent_check's result without what the model already has: identity key maps, the active run again as last_run, the root. */
function slimCheck(res: any) {
  const s = res?.summary;
  if (!s) return res;
  const out = { ...s };
  if (out.agent_keys && Object.entries(out.agent_keys).every(([k, v]) => v === k)) delete out.agent_keys;
  if (out.run && out.last_run?.id === out.run.id) delete out.last_run;
  if (out.agent) out.agent = { ...out.agent, root: undefined };
  return { ...res, summary: out };
}

/** The text a model reads for a script's JSON: compact and without empty fields (the full data stays in `details`). */
const compact = (data: any) => JSON.stringify(terse(data));

/** Tool result carrying a script's JSON (or `format` of it); throws when the script produced no JSON. */
function toolResult(r: ScriptResult, what: string, format?: (data: any) => string | undefined) {
  if (r.data === undefined) throw new Error(`${what} failed (exit ${r.code}): ${(r.stderr || r.stdout).trim().slice(0, 2000)}`);
  const text = format?.(r.data) ?? compact(r.data);
  return { content: [{ type: "text" as const, text }], details: r.data };
}

/** The plan: plan.yaml, and how it was planned in .plan/ (unknowns, questions, the subagents seen). */
const PLAN_FILES = /^(plan\.yaml|\.plan[\\/][^\\/]+)$/;
/** The critic's record: only agent_record_critique writes it (it checks the critic ran, and stamps the plan). */
const CRITIQUE_FILE = /^\.plan[\\/]critique\.yaml$/;
/** True for any run's state file, the critic's record, and the plan unless it is open for a change. */
function isProtected(root: string, file: string, planOpen: boolean): boolean {
  const rel = path.relative(root, file);
  if (CRITIQUE_FILE.test(rel)) return true;
  if (planOpen && PLAN_FILES.test(rel)) return false;
  return PLAN_FILES.test(rel) || /^runs[\\/][^\\/]+[\\/]run\.yaml$/.test(rel);
}

/** What only a reviewed branch changes once a run exists: gates, stage programs, agents, packages. */
const BRANCH_FILES = /^(gates|scripts|\.pi[\\/]agents)[\\/]|^(pyproject\.toml|uv\.lock)$/;
const SESSION_FILES = /[\\/]\.pi[\\/]agent[\\/]sessions[\\/]/;
// adding a package is the user's call: only agent_add_package, which asks, may do it
// (also uvx, uv tool and uv run --with, which fetch packages for one command; and any write to the package list)
const PACKAGE_INSTALL = /\buv\s+(?:pip\s+)?(?:add|install)\b|\bpip3?\s+install\b|\buvx\b|\buv\s+tool\s+(?:install|run)\b|\buv\s+run\b[^\n;&|]*\s--with\b|add_package\.py/;
const PACKAGE_FILES = /(^|[\\/])(pyproject\.toml|uv\.lock)$/;
const PACKAGE_FILES_WRITE = /(pyproject\.toml|uv\.lock)/;
const SHELL_WRITE = /\bsed\s+-i|>|\btee\b|\bmv\b|\bcp\b|\bperl\s+-\w*i|\btruncate\b|\bpython3?\b|\bnode\b/;
const PACKAGE_COMMAND = /\buv\s+(add|remove)\b/;

/** run/, revise/ and improve/ branches (loose and packed refs): ones waiting for review. */
const REVIEW_KINDS = ["run", "revise", "improve"];
const isChangeBranch = (b: string) => /^(revise|improve)\//.test(b);
function reviewBranches(root: string): string[] {
  const names = new Set<string>();
  for (const kind of REVIEW_KINDS) {
    try {
      for (const d of fs.readdirSync(path.join(root, ".git", "refs", "heads", kind))) names.add(`${kind}/${d}`);
    } catch {}
  }
  try {
    for (const m of fs.readFileSync(path.join(root, ".git", "packed-refs"), "utf8").matchAll(/refs\/heads\/((?:run|revise|improve|change)\/\S+)/g))
      names.add(m[1]);
  } catch {}
  return [...names].sort();
}

/** .env and its variants (.env.local, …): param values, possibly secrets. */
const ENV_FILE = /^\.env(\..*)?$/;
const ENV_IN_COMMAND = /(^|[^\w.])\.env(?![\w-])/;

/** Git commands that change the repo or its index; only the scripts commit. */
const GIT_WRITE = /\bgit\s+(?:-C\s+\S+\s+|-c\s+\S+\s+)*(?:add|commit|reset|checkout|restore|switch|rebase|merge|stash|tag|rm|mv|revert|cherry-pick|am|apply|init|pull|push|update-index|update-ref)\b/;

/** The checked-out branch of the agent's repo (from .git/HEAD), if any. */
function currentBranch(root: string): string | undefined {
  try {
    return /^ref: refs\/heads\/(.+)$/m.exec(fs.readFileSync(path.join(root, ".git", "HEAD"), "utf8"))?.[1];
  } catch {
    return undefined;
  }
}

/** True while a run is active (a runs/<id>/run.yaml with status: active). */
function runActive(root: string): boolean {
  try {
    return fs.readdirSync(path.join(root, "runs")).some((d) => {
      try {
        return /^status: active$/m.test(fs.readFileSync(path.join(root, "runs", d, "run.yaml"), "utf8"));
      } catch {
        return false;
      }
    });
  } catch {
    return false;
  }
}

/** True once a run has been started (runs/<id>/run.yaml exists). */
function hasRun(root: string): boolean {
  try {
    return fs.readdirSync(path.join(root, "runs")).some((d) => fs.existsSync(path.join(root, "runs", d, "run.yaml")));
  } catch {
    return false;
  }
}

async function check(pi: ExtensionAPI, root: string, signal?: AbortSignal) {
  const v = await runScript(pi, root, "validate_plan.py", ["plan.yaml"], signal);
  const c = await runScript(pi, root, "compile_wave.py", ["--check"], signal);
  return {
    valid: v.data?.valid ?? false,
    status: v.data?.status,
    errors: v.data?.errors ?? [v.stderr.trim()],
    warnings: v.data?.warnings ?? [],
    summary: v.data?.summary,
    state: v.data?.state, // where the agent stands and the legal moves (agent_state.py)
    compiles: c.data?.ok ?? false,
    compile_errors: c.data?.compile_errors ?? {},
  };
}

// --- extension --------------------------------------------------------------------

export default function agentExtension(pi: ExtensionAPI) {
  pi.on("tool_call", (event, ctx) => {
    const root = findRoot(ctx.cwd);
    if (!root) return;
    // run 2026-10-05-1: a completion notice woke the model, which called agent_log_finish on a wave /agent-auto had recorded
    if (autopilot && AUTO_OWNS.has(event.toolName))
      return { block: true, reason: "/agent-auto is running this run's waves and records them itself; it hands over when it needs you. Do nothing now." };
    // run 2026-10-09-4: the wave's completion notice came after the hand-over, and the model recorded the wave again
    const wave = (event.input as { wave?: string }).wave;
    if (event.toolName === "agent_log_finish" && wave && autoRecorded.has(wave))
      return { block: true, reason: `/agent-auto already recorded wave ${wave}: there is nothing to record. Follow its hand-over message.` };
    if (event.toolName === "agent_new_run") autoRecorded.clear();
    const input = event.input as { path?: string; command?: string };
    const shellCommand = event.toolName === "bash" || event.toolName === "powershell" ? (input.command ?? "") : "";
    const packageFile =
      ((event.toolName === "write" || event.toolName === "edit") && !!input.path && PACKAGE_FILES.test(path.relative(root, path.resolve(ctx.cwd, input.path)))) ||
      (PACKAGE_FILES_WRITE.test(shellCommand) && SHELL_WRITE.test(shellCommand));
    if (PACKAGE_INSTALL.test(shellCommand) || packageFile)
      return { block: true, reason: "Python packages are added with agent_add_package (the packages and why they are needed), which asks the user to approve them. Do not run uv add, pip install, uvx or uv run --with, edit pyproject.toml or uv.lock, or run add_package.py yourself. If you cannot ask (you are a stage's child), do not add the package: finish without it, or fail and name the package and why." };
    if ((event.toolName === "bash" || event.toolName === "powershell") && GIT_WRITE.test(input.command ?? ""))
      return {
        block: true,
        reason: "The skill scripts commit this agent's git repo (compile, resolve, critique); do not run git commands that change it. Read-only git (log, diff, show, status) is fine.",
      };
    // a session's JSONL has lines too long for the read tool, so a model writes a parser: point it at the tool
    if (SESSION_FILES.test(input.path ?? "") || ((event.toolName === "bash" || event.toolName === "powershell") && SESSION_FILES.test(input.command ?? "")))
      return {
        block: true,
        reason: "Pi's session files are read with the agent_sessions tool (action list, then show with from/to or around), which returns a compact, clipped transcript. Do not open or parse the JSONL yourself.",
      };
    const envFile = input.path
      ? ENV_FILE.test(path.basename(input.path))
      : (event.toolName === "bash" || event.toolName === "powershell") && ENV_IN_COMMAND.test(input.command ?? "");
    if (envFile)
      return {
        block: true,
        reason: ".env holds this machine's param values and may hold secrets: do not read or change it. Values that are not secrets: agent_set_params. Secrets: ask the user to add the variable to .env themselves (agent_compile's preflight names it); never ask for a secret's value.",
      };
    // the critic's record, always: only agent_record_critique writes it (a hand-written one would claim a review)
    const critique = input.path
      ? CRITIQUE_FILE.test(path.relative(root, path.resolve(ctx.cwd, input.path)))
      : (event.toolName === "bash" || event.toolName === "powershell") && /\.plan[\\/]critique/.test(input.command ?? "") && SHELL_WRITE.test(input.command ?? "");
    if (critique && event.toolName !== "read")
      return { block: true, reason: "The critic's record (.plan/critique.yaml) is written only by agent_record_critique, which checks the critic ran and ties its result to the plan. Read it with the read tool." };
    if (!hasRun(root)) return; // before the first run the plan is still a draft on main
    // after the first run, main changes only by a reviewed merge: a write here means the work
    // is on the wrong branch (say, someone switched branches during a change)
    if (!runActive(root) && currentBranch(root) === "main") {
      const shell = event.toolName === "bash" || event.toolName === "powershell";
      const rel = input.path ? path.relative(root, path.resolve(ctx.cwd, input.path)) : "";
      if (((event.toolName === "write" || event.toolName === "edit") && BRANCH_FILES.test(rel)) || (shell && PACKAGE_COMMAND.test(input.command ?? ""))) {
        const waiting = reviewBranches(root);
        return {
          block: true,
          reason:
            "main is checked out and no run is active: gates, scripts, agents and packages change only on a run or change branch, reviewed before it reaches main." +
            (waiting.length
              ? ` ${waiting.join(", ")} ${waiting.length > 1 ? "are" : "is"} waiting for review; if you were working on one, the checked-out branch was switched: stop and ask the user to \`git switch\` back to it.`
              : " Ask the user to start a change with /agent-revise."),
        };
      }
    }
    // the plan is open for direct edits only on a change branch (/agent-revise) with no run active
    const planOpen = !runActive(root) && isChangeBranch(currentBranch(root) ?? "");
    const touches =
      event.toolName === "write" || event.toolName === "edit"
        ? !!input.path && isProtected(root, path.resolve(ctx.cwd, input.path), planOpen)
        : event.toolName === "bash" || event.toolName === "powershell"
          ? (planOpen ? /run\.yaml|\.plan[\\/]critique/ : /(plan|run)\.yaml|\.plan[\\/]/).test(input.command ?? "")
          : false;
    if (touches)
      return {
        block: true,
        reason: planOpen
          ? "Run state changes only through the agent tools (agent_new_run, agent_log_start/finish), and the critic's record (.plan/critique.yaml) only through agent_record_critique. Read the files with the read tool."
          : "A run exists, so the plan changes only through the agent tools: during a run agent_resolve and agent_record_critique; otherwise /agent-revise (a change branch). Run state: agent_new_run, agent_log_start/finish. Read the files with the read tool.",
      };
  });

  // Swap only Pi's opening line, so the tool list, rules and the appended role stay as Pi builds them
  // (customPrompt would drop them). If Pi rewords the line, nothing matches and the prompt is left alone.
  // /agent-auto is running waves: from its start until it hands over
  let autopilot = false;
  let looping = false; // its loop is launching or waiting for a wave
  const autoRecorded = new Set<string>(); // waves of this run /agent-auto recorded: their late notices need nothing
  pi.on("before_agent_start", (event, ctx) => {
    if (!findRoot(ctx.cwd)) return;
    let systemPrompt = event.systemPrompt.replace(PI_PREAMBLE, ROLE_PREAMBLE);
    if (autopilot && event.messageType === "user" && event.prompt.includes(HANDOVER_MARK)) autopilot = false;
    // pi-subagents wakes the model with each wave's completion notice; /agent-auto records the wave itself, and its
    // run tools are refused meanwhile (tool_call)
    else if ((autopilot || autoRecorded.size) && isNotice(event))
      systemPrompt += `\n\n/agent-auto ${autopilot ? "is running the waves and records them itself" : `already recorded wave ${[...autoRecorded].join(", ")} itself`}: answer a completion notice with one word and call no tools.`;
    return systemPrompt === event.systemPrompt ? undefined : { systemPrompt };
  });

  pi.on("session_start", async (_event, ctx) => {
    const root = findRoot(ctx.cwd);
    if (!root) return;
    const probe = () => pi.exec(pythonFor(root), ["-c", "import dotenv, packaging, psutil, pydantic, pydantic_settings, ruamel.yaml, typer"], { cwd: root, timeout: 10_000 });
    let r = await probe();
    // First start: no .venv yet. Create it from this agent's pyproject.toml and uv.lock, as the gates and
    // programs' `uv run` would (skipped when $PI_AGENT_PYTHON names the Python to use).
    if (r.code !== 0 && !process.env.PI_AGENT_PYTHON) {
      ctx.ui.notify("Setting up this agent's Python environment (uv sync)...", "info");
      try {
        const sync = await pi.exec("uv", ["sync"], { cwd: root, timeout: 300_000 });
        if (sync.code === 0) r = await probe();
      } catch {
        // uv is not installed: the warning below says what to do
      }
    }
    if (r.code !== 0) ctx.ui.notify(`The skill scripts' Python packages are missing for ${pythonFor(root)}. Install uv, run \`uv sync\` in ${root}, then restart pi.`, "warning");
  });

  /** Show text in the conversation without starting a model turn. */
  const show = (title: string, body: string) =>
    pi.sendMessage({ customType: "agent", content: `${title}\n\n${body}`, display: true }, { triggerTurn: false });

  const ask = (ctx: ExtensionContext, text: string) => {
    if (!findRoot(ctx.cwd)) {
      ctx.ui.notify("Not inside an agent folder (no plan.yaml).", "error");
      return;
    }
    if (!ctx.isIdle()) {
      ctx.ui.notify("The agent is busy; wait for it to finish, then run the command again.", "warning");
      return;
    }
    activateSubagent(pi);
    pi.sendUserMessage(text);
  };

  /** Tell someone away from the chat: $AGENT_NOTIFY_COMMAND (e.g. `notify-send prometheus`, or a curl to a
   * webhook) runs with the message as its last argument. Unset: nothing. */
  const notifyUser = async (message: string) => {
    const command = process.env.AGENT_NOTIFY_COMMAND;
    if (!command) return;
    const root = findRoot(process.cwd()) ?? process.cwd();
    await pi.exec("sh", ["-c", `${command} "$1"`, "agent-notify", `${path.basename(root)}: ${message}`], { cwd: root, timeout: 15_000 }).catch(() => {});
  };

  /** One request to pi-subagents' in-process RPC (its docs/extension-api.md); resolves to the reply's data. */
  const rpc = (method: string, params: object, timeoutMs = 30_000) =>
    new Promise<any>((resolve, reject) => {
      const requestId = randomUUID();
      const timer = setTimeout(() => {
        off();
        reject(new Error(`pi-subagents did not answer ${method} (is it installed and loaded?)`));
      }, timeoutMs);
      const off = pi.events.on(`subagents:rpc:v1:reply:${requestId}`, (reply: any) => {
        clearTimeout(timer);
        off();
        if (reply?.success) resolve(reply.data);
        else reject(new Error(reply?.error?.message ?? `pi-subagents ${method} failed`));
      });
      pi.events.emit("subagents:rpc:v1:request", { version: 1, requestId, method, params });
    });

  /** The state a launched wave ended in, read from its status.json (pi-subagents' record; events can be missed);
   * "lost" when its runner is gone without recording an end, or the record never appears: else /agent-auto would
   * wait forever, and its hold refuses /agent-run. */
  const waveEnd = async (asyncDir: string) => {
    const began = Date.now();
    let dead = 0; // polls in a row that found the runner gone
    for (;;) {
      let st: any;
      try {
        st = JSON.parse(fs.readFileSync(path.join(asyncDir, "status.json"), "utf8"));
      } catch {
        if (Date.now() - began > 120_000) return "lost"; // not written yet, two minutes on
      }
      // paused: a child asked its supervisor (contact_supervisor); the model answers it
      if (["complete", "completed", "failed", "stopped", "paused"].includes(st?.state)) return st.state as string;
      if (typeof st?.pid === "number" && !alive(st.pid)) {
        if (++dead >= 2) return "lost"; // twice, so an end written just after it exited is read first
      } else dead = 0;
      await new Promise((r) => setTimeout(r, 3_000));
    }
  };

  /** Waves run by code, the model woken only where the run protocol needs judgment or a person:
   * compile, launch through pi-subagents' RPC, record, repeat. Why: the orchestrator's turns cost
   * more than the children of a run whose programs run as commands (run 4: $0.0135 vs $0.0105). */
  const autoRun = async (ctx: ExtensionContext) => {
    const root = mustRoot(ctx);
    const step = async (script: string, args: string[]) => (await runScript(pi, root, script, args)).data ?? { ok: false, error: `${script} printed no result` };
    const handOver = (why: string, data: any) => {
      ctx.ui.setStatus("agent-auto", undefined);
      void notifyUser(why);
      activateSubagent(pi);
      // a finished run with nothing to review needs only the report: not the whole run protocol (~3k tokens)
      const finished = data?.run_status === "done" && !data?.review;
      // The hold ends now, not when this message's turn starts: delivered into a notice turn already running, it starts
      // no turn of its own, and the model, refused agent_compile after asking for the approval, stopped believing
      // /agent-auto would go on (an API call, 2026-10-05). The loop is over: nothing is left to protect.
      autopilot = false;
      // a follow-up: a completion notice may have started a turn, and Pi drops a plain message sent during one
      // (run 2026-10-05-1 lost its report this way)
      pi.sendUserMessage(finished ? FINISH_PROMPT(compact(data))
        : `${RUN_PROMPT("")}\n\n${HANDOVER_MARK} the waves it could without you and stopped: ${why}. Go on from here (skip the checks it did):\n${compact(data)}`,
        { deliverAs: "followUp" });
    };
    try {
      await rpc("ping", {});
    } catch (e: any) {
      return ctx.ui.notify(`${e.message}. Use /agent-run instead.`, "error");
    }
    const state = (await step("agent_state.py", [])).state;
    if (["planned", "ready", "branch_review", "change_ready"].includes(state)) {
      const r = await step("run_log.py", ["new", "--note", "started by /agent-auto"]);
      if (!r.ok) return ctx.ui.notify(r.error ?? "cannot start a run", "error");
    } else if (state !== "between_waves") {
      return ctx.ui.notify(`/agent-auto starts from a ready plan or a run between waves; the agent is ${state}: use /agent-run.`, "warning");
    }
    autopilot = true;
    autoRecorded.clear();
    for (;;) {
      const c = await step("compile_wave.py", []);
      if (!c.ok) return handOver("the next wave needs something first", c);
      // one line in the chat per wave start and end: with no model turn, nothing else shows the run moving
      const started = Date.now();
      show(`Wave ${c.wave} ${c.launched === false ? "reused" : "started"}`, `${c.stages.join(", ")}${c.notes?.length ? ` (${c.notes.join("; ")})` : ""}`);
      let f: any = c; // every stage reused: compile_wave recorded the wave, with agent_log_finish's result, and launched nothing
      if (c.launched !== false) {
        ctx.ui.setStatus("agent-auto", `agent-auto: wave ${c.wave} running`);
        let launched: any;
        try {
          launched = await rpc("spawn", { ...c.call, cwd: root });
        } catch (e: any) {
          return handOver(`launching wave ${c.wave} failed: ${e.message}`, { wave: c.wave });
        }
        const id = launched?.details?.runId ?? launched?.details?.asyncId;
        const dir = launched?.details?.asyncDir;
        const recorded = await step("run_log.py", ["start", "--wave", c.wave, "--subagent-run-id", String(id)]);
        if (!recorded.ok || !dir) return handOver(`wave ${c.wave} launched as ${id} but not recorded`, recorded);
        const end = await waveEnd(dir);
        if (end === "lost") return handOver(`pi-subagents' runner for wave ${c.wave} stopped without recording how it ended (run ${id}); check the run before relaunching`, { wave: c.wave, subagent_run_id: id });
        if (end === "paused") return handOver(`a child of wave ${c.wave} asked a question (pi-subagents run ${id})`, { wave: c.wave, subagent_run_id: id });
        f = await step("run_log.py", ["finish", "--wave", c.wave]);
        if (!f.ok) return handOver(`recording wave ${c.wave} failed`, f);
        autoRecorded.add(c.wave);
      }
      const took = Math.round((Date.now() - started) / 1000);
      show(`Wave ${c.wave} ${f.wave_status === "done" ? "done" : "did not complete"}`,
        `${took >= 60 ? `${Math.floor(took / 60)}m ${took % 60}s` : `${took}s`}${typeof f.cost_usd === "number" ? ` · $${f.cost_usd.toFixed(4)}` : ""}` +
        (f.wave_status === "done" && f.next_wave ? ` · wave ${f.next_wave.id} next` : ""));
      if (f.run_status === "done") return handOver("the run is done: close its mission and give the final report", f);
      if (f.wave_status !== "done") return handOver(`wave ${c.wave} did not complete`, f);
      if (f.ends_with && f.ends_with !== "none") return handOver(`wave ${c.wave} ended with ${f.ends_with}`, f);
    }
  };

  // --- commands ---------------------------------------------------------------
  pi.registerCommand("agent-auto", {
    description: "Run the next waves without the model until one needs you, fails, or the run ends",
    handler: async (_args, ctx) => {
      if (looping) return ctx.ui.notify("/agent-auto is already running.", "warning");
      if (!ctx.isIdle()) return ctx.ui.notify("The agent is busy; wait for it to finish, then run the command again.", "warning");
      looping = true;
      try {
        await autoRun(ctx);
      } catch (e: any) {
        autopilot = false;
        ctx.ui.setStatus("agent-auto", undefined);
        ctx.ui.notify(`/agent-auto stopped: ${e.message}`, "error");
      } finally {
        looping = false;
      }
    },
  });

  pi.registerCommand("agent-history", {
    description: "The last runs and how each stage went in them (no model)",
    handler: async (args, ctx) => {
      const root = mustRoot(ctx);
      const r = (await runScript(pi, root, "run_history.py", ["show", ...opt("--last", args.trim())])).data;
      if (!r?.ok) return ctx.ui.notify(r?.error ?? "run_history.py failed", "error");
      const runs = r.runs.map((x: any) => `- ${x.id} (${x.status}, r${x.revision}) $${x.cost_usd.toFixed(4)}`);
      const stages = Object.entries(r.stages as Record<string, Record<string, number>>).map(
        ([k, c]) => `- ${k}: ${Object.entries(c).map(([s, n]) => `${n} ${s}`).join(", ")}`);
      show("Run history", [`${r.runs.length} runs, $${r.total_cost_usd.toFixed(4)} in children`, ...runs, "", "Stages:", ...stages].join("\n"));
    },
  });

  pi.registerCommand("agent-prune", {
    description: "Delete old finished runs' work folders (keeps their history; default: keep the newest 10)",
    handler: async (args, ctx) => {
      const root = mustRoot(ctx);
      const keep = opt("--keep", args.trim());
      const dry = (await runScript(pi, root, "run_history.py", ["prune", "--dry-run", ...keep])).data;
      if (!dry?.ok) return ctx.ui.notify(dry?.error ?? "run_history.py failed", "error");
      if (!dry.pruned.length && !dry.empty_session_folders) return ctx.ui.notify("Nothing to prune.", "info");
      const what = [
        ...(dry.pruned.length ? [`the work folders of ${dry.pruned.length} runs (${dry.freed_mb} MB): ${dry.pruned.join(", ")}`] : []),
        ...(dry.empty_session_folders ? [`${dry.empty_session_folders} empty launch folders in .pi/sessions`] : []),
      ];
      if (!(await ctx.ui.confirm("Prune runs", `Delete ${what.join(", and ")}?`))) return;
      const r = (await runScript(pi, root, "run_history.py", ["prune", ...keep])).data;
      ctx.ui.notify(r?.ok ? `Pruned ${r.pruned.length} runs, ${r.freed_mb} MB freed, ${r.empty_session_folders} empty launch folders removed.`
        : (r?.error ?? "prune failed"), r?.ok ? "info" : "error");
    },
  });

  pi.registerCommand("agent-run", {
    description: "Plan, continue or run this agent from where it stands",
    handler: async (args, ctx) => {
      if (looping) return ctx.ui.notify("/agent-auto is running this run's waves; it hands over when it needs you.", "warning");
      autopilot = false; // a hand-over that never arrived: you take the run back
      autoRecorded.clear();
      ask(ctx, RUN_PROMPT(args.trim()));
    },
  });

  /** Open (or continue) the change branch for a command that edits the agent; undefined after telling the user why not. */
  const beginChange = async (ctx: ExtensionContext, kind: "revise" | "improve") => {
    const root = findRoot(ctx.cwd);
    if (!root) {
      ctx.ui.notify("Not inside an agent folder (no plan.yaml).", "error");
      return;
    }
    if (!ctx.isIdle()) {
      ctx.ui.notify("The agent is busy; wait for it to finish, then run the command again.", "warning");
      return;
    }
    const r = await runScript(pi, root, "agent_git.py", ["begin-change", "--kind", kind]);
    if (!r.data?.ok) {
      ctx.ui.notify(r.data?.error ?? `agent_git.py begin-change failed: ${(r.stderr || r.stdout).trim().slice(0, 500)}`, "error");
      return;
    }
    return { root, branch: r.data.branch ?? "main", mode: r.data.mode };
  };

  pi.registerCommand("agent-revise", {
    description: "Change this agent's plan on a branch you review before it reaches main (no run active): /agent-revise [change]",
    handler: async (args, ctx) => {
      const change = await beginChange(ctx, "revise");
      if (!change) return;
      activateSubagent(pi); // the critic
      pi.sendUserMessage(REVISE_PROMPT(args.trim(), change.branch, change.mode));
    },
  });

  pi.registerCommand("agent-improve", {
    description: "Propose improvements from this agent's sessions and runs, applied on an improve/ branch you review (no run active): /agent-improve [bundle.tgz …] [focus]",
    handler: async (args, ctx) => {
      const root = findRoot(ctx.cwd);
      if (!root) {
        ctx.ui.notify("Not inside an agent folder (no plan.yaml).", "error");
        return;
      }
      // bundles first (a released run that failed elsewhere: prometheus bundle), then the rest is the focus
      const words = args.trim().split(/\s+/).filter(Boolean);
      const isBundle = (w: string) => /\.tgz$/.test(w) && fs.existsSync(path.resolve(ctx.cwd, w));
      for (const file of words.filter(isBundle)) {
        const added = await runScript(pi, root, "bundle.py", ["add", path.resolve(ctx.cwd, file), "--agent", root]);
        if (!added.data?.ok) {
          ctx.ui.notify(added.data?.error ?? `bundle.py failed: ${(added.stderr || added.stdout).trim().slice(0, 500)}`, "error");
          return;
        }
        ctx.ui.notify(`Imported ${file}: run ${added.data.run} of ${added.data.version}, ${added.data.state}` +
          `${added.data.already ? " (already imported)" : ""}${(added.data.warnings ?? []).map((w: string) => `\nwarning: ${w}`).join("")}`, "info");
      }
      const focus = words.filter((w) => !isBundle(w)).join(" ");
      // the digest first: with nothing new to learn from, no branch is opened (and none left to review)
      const d = await runScript(pi, root, "session_digest.py", ["--agent", root]);
      if (!d.data?.ok) {
        ctx.ui.notify(d.data?.error ?? `session_digest.py failed: ${(d.stderr || d.stdout).trim().slice(0, 500)}`, "error");
        return;
      }
      if (!d.data.sessions?.length && !d.data.runs?.length && !d.data.imported?.length && !focus && currentBranch(root) === "main") {
        ctx.ui.notify("Nothing new since the last /agent-improve: no unreviewed sessions or runs. Give a focus to improve anyway: /agent-improve <focus>.", "info");
        return;
      }
      const change = await beginChange(ctx, "improve");
      if (!change) return;
      activateSubagent(pi); // the critic
      pi.sendUserMessage(IMPROVE_PROMPT(focus, change.branch, JSON.stringify(d.data)));
    },
  });

  pi.registerCommand("agent-bundle", {
    description: "Pack a run's evidence into one file for whoever develops this agent (changes nothing): /agent-bundle [run id] [--with-files]",
    handler: async (args, ctx) => {
      const root = findRoot(ctx.cwd);
      if (!root) {
        ctx.ui.notify("Not inside an agent folder (no plan.yaml).", "error");
        return;
      }
      const words = args.trim().split(/\s+/).filter(Boolean);
      const run = words.find((w) => !w.startsWith("--"));
      const res = await runScript(pi, root, "bundle.py", [
        "make", ...(run ? ["--run", path.join(root, "runs", path.basename(run))] : ["--agent", root]),
        "--out", path.join(root, "runs"), ...(words.includes("--with-files") ? ["--with-files"] : []),
      ]);
      const b = res.data ?? {};
      if (!b.ok) {
        ctx.ui.notify(b.error ?? `bundle.py failed: ${(res.stderr || res.stdout).trim().slice(0, 500)}`, "error");
        return;
      }
      show("Bundle", [
        `\`${b.bundle}\` (${b.size_mb} MB, ${b.files} files${b.with_files ? ", with the run's work folder" : ""})`,
        "",
        "Copy it to where this agent is developed and run `/agent-improve <file>` there.",
        ...(b.redacted?.length ? [`The values of ${b.redacted.join(", ")} were replaced by \${NAME}.`] : []),
        ...(b.warnings ?? []).map((w: string) => `- **Check before you send it:** ${w}`),
      ].join("\n"));
    },
  });

  pi.registerCommand("agent-status", {
    description: "Show where this agent stands (changes nothing)",
    handler: async (_args, ctx) => {
      const root = findRoot(ctx.cwd);
      if (!root) {
        ctx.ui.notify("Not inside an agent folder (no plan.yaml).", "error");
        return;
      }
      const [info, chk, next] = await Promise.all([
        runScript(pi, root, "list_agents.py", ["--agent", root]),
        check(pi, root),
        runScript(pi, root, "compile_wave.py", ["--no-commit"]),
      ]);
      const b = info.data ?? {};
      const s = chk.summary ?? {};
      const pf = next.data?.preflight ?? {};
      // only what the page and the workspace don't show already: what comes next, what waits for the person, problems
      const ends: Record<string, string> = { approval: ", needs your approval", review: ", then your review", resolve: ", then the run resolves stages" };
      const waves = (s.waves ?? []).map((w: any) => `${w.id} (${w.stages.join(", ")}${ends[w.ends_with] ?? ""})`).join(", then ");
      const comingUp = b.active_run
        ? `run ${b.active_run}: ${b.stages_done}/${b.stages_total} stages done${b.next_wave ? `, wave ${b.next_wave} next` : ""}`
        : `/agent-run starts a run: ${waves || "nothing to run"}`;
      const waiting = [
        ...(b.open_questions ?? []).map((q: string) => `answer: ${q}`),
        ...(b.active_run ? (pf.decisions_needed ?? []).map((d: any) => `decide: ${d.stage}`) : []),
        ...(b.active_run ? (pf.approvals_needed ?? []).map((a: any) => `approve: ${a.stage}`) : []),
        ...(b.active_run && pf.review_needed ? [`review wave ${pf.review_needed.wave}`] : []),
        ...(b.active_run && pf.budget ? [`over budget: $${pf.budget.spent_usd} of $${pf.budget.max_cost_usd}`] : []),
        ...(b.unmerged ?? []).map((br: string) => `review branch ${br}`),
        ...(s.agent?.missing_params ?? []).map((p: string) => `set ${p}`),
        ...(s.agent?.missing_secrets ?? []).map((v: string) => `add ${v} to .env`),
      ];
      const problems = [
        ...chk.errors,
        ...Object.entries(chk.compile_errors as Record<string, string[]>).flatMap(([w, es]) => es.map((e) => `${w}: ${e}`)),
        ...(b.off_branch ? [`run ${b.active_run}: ${b.off_branch}`] : []),
        ...(b.active_run && !b.off_branch && !next.data?.ok && next.data?.error ? [next.data.error] : []),
      ];
      show(
        `Agent ${path.basename(root)}`,
        [
          `- **Next:** ${comingUp}`,
          `- **Waiting for you:** ${waiting.join("; ") || "nothing"}`,
          ...problems.map((p: string) => `- **Problem:** ${p}`),
          ...(b.active_run ? ["", "A running wave is shown as last recorded; /agent-run checks it live."] : []),
        ].join("\n"),
      );
    },
  });

  // --- tools ------------------------------------------------------------------
  pi.registerTool(
    defineTool({
      name: "agent_check",
      label: "Check agent",
      description: "Validate this agent's plan.yaml and compile-check every concrete pending wave. The summary lists the waves still to run (derived from the stages).",
      promptSnippet: "Validate and compile-check this agent's plan",
      promptGuidelines: ["Use agent_check after every change to plan.yaml."],
      parameters: Type.Object({}),
      async execute(_id, _params, signal, _onUpdate, ctx) {
        const res = await check(pi, mustRoot(ctx), signal);
        return { content: [{ type: "text" as const, text: compact(slimCheck(res)) }], details: res };
      },
    }),
  );

  pi.registerTool(
    defineTool({
      name: "agent_add_package",
      label: "Add package",
      description:
        "Add Python packages to this agent's pyproject.toml (uv add). The user is shown the packages and your reason and must approve first: say plainly why the agent needs them, in terms of what they do for the user's work. Packages change only on a run or change branch.",
      promptSnippet: "Add a Python package, after the user approves it",
      promptGuidelines: ["To add a Python package use agent_add_package with the reason; never uv add or pip install. If the user declines, work without it or ask what to do instead."],
      parameters: Type.Object({
        packages: Type.Array(Type.String(), { description: "requirements as uv add takes them: openpyxl, pandas>=2, httpx[http2]" }),
        reason: Type.String({ description: "why the agent needs them, in a sentence the user can judge" }),
      }),
      async execute(_id, params, signal, _onUpdate, ctx) {
        const root = mustRoot(ctx);
        if (!ctx.hasUI) throw new Error("adding a package needs the user's approval and no one can be asked here: do not add it; finish without it, or fail and name the package and why");
        if (!params.packages.length || !params.reason.trim()) throw new Error("name the packages and say why they are needed");
        const yes = await ctx.ui.confirm("Add a Python package?", `${params.packages.join(", ")}\n\nWhy: ${params.reason.trim()}`);
        if (!yes) return { content: [{ type: "text" as const, text: "The user declined. Do not add it: work without it, or ask the user what to do instead." }], details: { declined: true } };
        return toolResult(await runScript(pi, root, "add_package.py", [...params.packages, "--reason", params.reason], signal), "add_package.py");
      },
    }),
  );

  /** A tool that runs one script: `args` turns the tool's parameters into the script's arguments. */
  const scriptTool = <P extends TSchema>(tool: {
    name: string;
    label: string;
    description: string;
    promptSnippet?: string;
    promptGuidelines?: string[];
    parameters: P;
    script: string;
    args: (params: Static<P>) => string[];
    format?: (data: any) => string | undefined;
  }) => {
    const { script, args, format, ...definition } = tool;
    pi.registerTool(
      defineTool({
        ...definition,
        async execute(_id, params, signal, _onUpdate, ctx) {
          const argv = args(params);
          const what = argv[0] && !argv[0].startsWith("-") ? `${script} ${argv[0]}` : script;
          return toolResult(await runScript(pi, mustRoot(ctx), script, argv, signal), what, format);
        },
      }),
    );
  };
  /** [flag, value] when the value is given, else nothing. */
  const opt = (flag: string, value: string | undefined): string[] => (value ? [flag, value] : []);

  scriptTool({
    name: "agent_sessions",
    label: "Sessions",
    description:
      "Read this agent's earlier Pi sessions (their JSONL files have lines too long for the read tool). action list: the sessions, newest first. action show: a bounded transcript of one (file from list, or a failed child's `session` in the improve digest's imported runs): per record its number i, time, role, text, tool calls and results, each clipped. One line per record: number, time, U (user) / A (assistant, then -> tool(args)) / R (result; R! failed). Select records with from/to (record numbers) or around (a timestamp, e.g. a failure's `at` in the improve digest; window records each side); it stops at a size cap and ends with [more: next_from N].",
    promptSnippet: "List or read this agent's earlier sessions",
    promptGuidelines: [
      "To look into an earlier session, use agent_sessions; do not read the session JSONL files with read or bash, or write a parser for them.",
    ],
    parameters: Type.Object({
      action: Type.Union([Type.Literal("list"), Type.Literal("show")]),
      file: Type.Optional(Type.String({ description: "show: a session file name from list, or the start of one, or an imported child's session (runs/imported/…)" })),
      from: Type.Optional(Type.Integer({ description: "show: first record number" })),
      to: Type.Optional(Type.Integer({ description: "show: last record number" })),
      around: Type.Optional(Type.String({ description: "show: a timestamp to centre the window on" })),
      window: Type.Optional(Type.Integer({ description: "show with around: records on each side (default 6)" })),
      chars: Type.Optional(Type.Integer({ description: "show: characters kept of each message, argument or result (default 300)" })),
    }),
    script: "session_read.py",
    // plain lines, not indented JSON: a session transcript is read for its text
    format: (d) => (d.ok && typeof d.text === "string" ? (d.next_from ? `${d.text}\n[more: next_from ${d.next_from}]` : d.text) : undefined),
    args: (p) =>
      p.action === "list"
        ? ["list"]
        : [
            "show",
            p.file ?? "",
            ...opt("--from", p.from?.toString()),
            ...opt("--to", p.to?.toString()),
            ...opt("--around", p.around),
            ...opt("--window", p.window?.toString()),
            ...opt("--text-chars", p.chars?.toString()),
          ],
  });

  scriptTool({
    name: "agent_new_run",
    label: "New run",
    description:
      "Start a run of this agent: a folder runs/<id>/ for its state, work files and outputs, which becomes the active run. It starts from main; the run's own plan changes go to a branch run/<id>. Always starts from main: a run or change branch checked out is committed and kept for review (the result's left_branch names it; tell the user it still waits for review), unless main's plan is not ready. Refuses while another run is active.",
    promptGuidelines: ["Start a run only when there is no active run and the user wants to run the agent."],
    parameters: Type.Object({
      note: Type.Optional(Type.String({ description: "what this run is for, in the user's words" })),
    }),
    script: "run_log.py",
    args: (p) => ["new", ...opt("--note", p.note)],
  });

  scriptTool({
    name: "agent_stop_run",
    label: "Stop run",
    description:
      "Stop the active run (it stays in runs/, marked stopped). Returns its branch, if it changed the plan, for the user's review with agent_merge, and `mission_close` (pass it to the subagent tool unchanged). Refuses while a wave is running.",
    promptGuidelines: ["Stop a run only when the user asked to stop it or to start over."],
    parameters: Type.Object({ note: Type.Optional(Type.String({ description: "why it stopped" })) }),
    script: "run_log.py",
    args: (p) => ["stop", ...opt("--note", p.note)],
  });

  scriptTool({
    name: "agent_merge",
    label: "Merge branch",
    description:
      "Finish the review of the checked-out run or change branch and switch back to main: merge (one commit on main, however many the branch made; tags the revision; deletes the branch), keep (the branch stays for later) or discard (delete it). Refuses while a run is active.",
    promptGuidelines: [
      "Call agent_merge only with the action the user chose after seeing the branch's changes (git diff main...<branch>).",
      "On merge, pass `summary`: the summary of the changes you gave the user, in a few lines. It becomes the body of the commit on main.",
    ],
    parameters: Type.Object({
      action: Type.Union([Type.Literal("merge"), Type.Literal("keep"), Type.Literal("discard")]),
      summary: Type.Optional(Type.String({ description: "merge: what the branch changes, in words, for the commit message" })),
    }),
    script: "agent_git.py",
    args: (p) => ["merge", "--action", p.action, ...opt("--summary", p.summary)],
  });

  scriptTool({
    name: "agent_compile",
    label: "Compile wave",
    description:
      "Compile the active run's next wave into the exact pi-subagents call. Returns {ok, run, wave, stages, ends_with, call, children} when it can launch; {ok, launched: false, ...} when every stage was reused from an earlier run, the wave already recorded with nothing launched (what agent_log_finish returns: go on from it, launch and log nothing); or {ok: false, preflight} listing params to set (missing_params), secrets the user must add to .env (missing_secrets), decisions to collect, approvals to get, and review_needed when the last wave ended with a review the user has not answered yet. Only the launched wave's plan is committed; children that completed in a failed launch of the same wave, unchanged since, are not run again (notes say which).",
    promptSnippet: "Compile the next agent wave into a subagent call",
    promptGuidelines: [
      "Never write a wave script by hand; use agent_compile and pass its `call` to the subagent tool unchanged. `call.workflow` is a script path (pi-subagents reads it itself): pass it as-is, do not open the file or paste its contents into the tool call.",
      "Only list a stage in `approve` after the user explicitly approved that stage's side effect in this session.",
      "Pass `reviewed: true` only after the user saw the last wave's outputs (preflight review_needed) and said to go on.",
      "Pass `budget` only after preflight budget: the user saw what the run spent and gave a new limit.",
    ],
    parameters: Type.Object({
      approve: Type.Optional(Type.Array(Type.String(), { description: "side-effect stage ids the user explicitly approved" })),
      reviewed: Type.Optional(Type.Boolean({ description: "the user reviewed the last wave's outputs and said to go on" })),
      budget: Type.Optional(Type.Number({ description: "the run's new cost limit in USD, given by the user after preflight budget" })),
    }),
    script: "compile_wave.py",
    args: (p) => [...opt("--approve", p.approve?.join(",")), ...(p.reviewed ? ["--reviewed"] : []), ...opt("--budget", p.budget?.toString())],
  });

  scriptTool({
    name: "agent_record_decision",
    label: "Record decision",
    description: "Record the user's answer for a decision stage, in the file the compiler reads.",
    promptGuidelines: ["Record only answers the user actually gave; never answer a decision stage yourself."],
    parameters: Type.Object({
      stage: Type.String({ description: "the decision stage id" }),
      answer: Type.Any({ description: "the user's answer as JSON (object, string, list)" }),
    }),
    script: "run_log.py",
    args: (p) => ["decision", "--stage", p.stage, "--answer", JSON.stringify(p.answer)],
  });

  scriptTool({
    name: "agent_log_start",
    label: "Log wave start",
    description: "Record that the active run's next wave was launched: marks its stages running and appends a launch to runs/<id>/run.yaml.",
    parameters: Type.Object({
      wave: Type.String({ description: "the wave id agent_compile returned" }),
      subagent_run_id: Type.String({ description: "the run id the subagent launch returned" }),
      mission_id: Type.Optional(Type.String()),
      approved: Type.Optional(Type.Array(Type.String())),
      decisions: Type.Optional(Type.Record(Type.String(), Type.Any())),
    }),
    script: "run_log.py",
    args: (p) => ["start", "--wave", p.wave, "--subagent-run-id", p.subagent_run_id, ...opt("--mission-id", p.mission_id),
      ...opt("--approved", p.approved?.join(",")), ...opt("--decisions", p.decisions && JSON.stringify(p.decisions))],
  });

  scriptTool({
    name: "agent_log_finish",
    label: "Log wave result",
    description:
      "Record a finished wave in the active run. Pass only the wave: the outcome (the workflow script's return value, status, cost) is read from pi-subagents. `done` marks its stages done (and the run done after the last wave); failed, blocked or stopped put them back to pending so the wave can be fixed and relaunched. Commits files the wave wrote into the agent on the run's branch. Returns the wave's ends_with (what to do next), a summary of its children, next_wave (with `preflight`: the decisions, approvals, params or secrets its compile needs first, to ask for before compiling) and, when the run is done, its branch for review, the plan's `done_when` for the final report, `outputs` (each deliverable's path in this run, whether it exists, and its `items` count when one can be read), and `mission_close` (pass it to the subagent tool unchanged).",
    promptGuidelines: [
      "After a wave's completion notice, call agent_log_finish with just the wave; do not look up the run's status or assemble its result yourself.",
    ],
    parameters: Type.Object({
      wave: Type.String(),
      status: Type.Optional(
        Type.Union([Type.Literal("done"), Type.Literal("failed"), Type.Literal("blocked"), Type.Literal("stopped")], {
          description: "only when pi-subagents has no record of the run (it says so); default: what the wave returned",
        }),
      ),
      result: Type.Optional(Type.Any({ description: "only with status, when pi-subagents has no record: the workflow script's return value" })),
      note: Type.Optional(Type.String()),
    }),
    script: "run_log.py",
    // a given result is saved in the run's .compiled/
    args: (p) => ["finish", "--wave", p.wave, ...opt("--status", p.status),
      ...opt("--result", p.result === undefined ? undefined : JSON.stringify(p.result)), ...opt("--note", p.note)],
    format: (d) => {
      const why = waitsForUser(d);
      if (why) void notifyUser(why);
      return undefined;
    },
  });

  scriptTool({
    name: "agent_resolve",
    label: "Resolve",
    description:
      "Resolve provisional stages once their discovery has run: give each in full, without `resolves_after`, and record the unknowns' findings. Also changes other pending stages when the user asks the run for a change: replace or add stages (full definitions), remove pending stages, add questions or record answers. Waves follow from the stages. Refuses changes to stages that are done or running in the active run, the request, goal, done_when, params, inputs, outputs and agents, and any weakening of a side effect. Commits the agent (git keeps the previous revision), bumps revision, sets status draft and clears the critique.",
    promptSnippet: "Resolve provisional stages (or change pending ones the user asks about) in the agent plan",
    promptGuidelines: [
      "Use agent_resolve for every plan change after a wave has run; direct edits to plan.yaml are blocked.",
      "Give each changed stage in full, as it should read in the plan; omit `resolves_after` to make a provisional stage concrete.",
    ],
    parameters: Type.Object({
      stages: Type.Optional(Type.Array(Type.Any(), { description: "full stage definitions (plan-schema.md, Stages)" })),
      remove: Type.Optional(Type.Array(Type.String(), { description: "keys of pending stages to drop" })),
      findings: Type.Optional(Type.Record(Type.String(), Type.Any(), { description: "unknown id -> finding" })),
      questions: Type.Optional(Type.Array(Type.Any(), { description: "new questions" })),
      answers: Type.Optional(Type.Record(Type.String(), Type.Any(), { description: "question id -> the user's answer" })),
      note: Type.Optional(Type.String({ description: "one line: why" })),
    }),
    script: "plan_edit.py",
    args: (p) => ["resolve", "--change", JSON.stringify(p)],
  });

  scriptTool({
    name: "agent_record_critique",
    label: "Record critique",
    description:
      "Record the plan-critic's result for the current revision, with your disposition of every finding; marks the plan ready if it then validates (needs_answers if questions are open).",
    parameters: Type.Object({
      run_id: Type.String({ description: "the critic's run id" }),
      verdict: Type.Union([Type.Literal("pass"), Type.Literal("revise"), Type.Literal("ask_user")]),
      passes: Type.Optional(Type.Integer({ minimum: 1, maximum: 2 })),
      findings: Type.Array(Type.Any(), {
        description: "{id, severity, category, target, claim, disposition: accepted|rejected|asked, note}",
      }),
    }),
    script: "plan_edit.py",
    args: (p) => ["critique", "--result", JSON.stringify(p)],
  });

  scriptTool({
    name: "agent_set_params",
    label: "Set params",
    description:
      "Set values for the plan's params in .env (per machine). Refuses secrets: the user adds those to .env themselves. Returns validation, including input paths that are relative or do not exist.",
    parameters: Type.Object({
      values: Type.Record(Type.String(), Type.String(), { description: "param name -> value; absolute paths for files and folders" }),
    }),
    script: "plan_edit.py",
    args: (p) => ["params", "--values", JSON.stringify(p.values)],
  });
}
