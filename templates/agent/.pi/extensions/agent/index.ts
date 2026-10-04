/**
 * Agent: commands and tools for running this agent.
 *
 * Commands
 *   /agent-run [note]        plan, continue or run from where it stands (model)
 *   /agent-status            where the agent stands (no model)
 *   /agent-revise [change]   change the plan on a branch, for review (no run active; model)
 *   /agent-improve [focus]   propose fixes from the sessions and runs, applied on an improve/ branch (no run active; model)
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
 *   agent_replan           change pending stages during a run (after discovery, or asked)
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
 * Param values live in .env (per machine, may hold secrets): this extension loads it into
 * pi's environment, which children and gate commands inherit, and the model may not read or
 * change it directly. Once a run exists, direct edits to runs/<id>/run.yaml are blocked,
 * and plan.yaml may be edited directly only on a change branch with no run active;
 * during a run the plan changes through agent_replan, which refuses to touch what ran,
 * the goal or a side effect's approval.
 *
 * The logic lives in the skill's Python scripts (.pi/skills/agent/scripts);
 * this file runs them and formats their JSON.
 */
import { defineTool, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type, type Static, type TSchema } from "typebox";
import * as fs from "node:fs";
import * as path from "node:path";
import { parseEnv } from "node:util";


// --- prompts sent to the model ------------------------------------------------

const RUN_PROMPT = (note: string) => `Run this agent. Read \`.pi/skills/agent/references/run-protocol.md\`
and follow it from "Every start": check, then plan, replan or run the next
wave, whichever the plan needs.

Use the agent tools for every mechanical step: \`agent_check\`,
\`agent_new_run\`, \`agent_compile\`, \`agent_record_decision\`,
\`agent_log_start\`, \`agent_log_finish\`, \`agent_replan\`,
\`agent_stop_run\` and \`agent_merge\`. The subagent tool is already active: pass
the \`call\` that \`agent_compile\` returns to it unchanged, without calling
subagents_enable or listing agents first. Ask every question that has a fixed set of
answers with the question tool (run-protocol.md, "Asking the user").

Note from the user for this run (may be empty): ${note}`;

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
   \`{date: <the digest's \`now\`>, target: <file>, proposal: <one line>, evidence: <session or run>,
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

/** Values pi's environment got from .env, so a reload can update or drop them. */
const fromEnvFile = new Map<string, string>();

/** Load the agent's .env into pi's environment: children and gate commands inherit it, and
 * the scripts see the same values. The real environment wins: a variable this extension did
 * not set is left alone. Reloaded before every script, so edits count without a restart. */
function loadEnvFile(root: string): void {
	let parsed: Record<string, string>;
	try {
		parsed = parseEnv(fs.readFileSync(path.join(root, ".env"), "utf8")) as Record<string, string>;
	} catch {
		parsed = {};
	}
	for (const [k, v] of [...fromEnvFile]) {
		if (k in parsed) continue;
		if (process.env[k] === v) delete process.env[k];
		fromEnvFile.delete(k);
	}
	for (const [k, v] of Object.entries(parsed)) {
		if (process.env[k] !== undefined && process.env[k] !== fromEnvFile.get(k)) continue;
		process.env[k] = v;
		fromEnvFile.set(k, v);
	}
}

async function runScript(
	pi: ExtensionAPI,
	root: string,
	script: string,
	args: string[],
	signal?: AbortSignal,
): Promise<ScriptResult> {
	loadEnvFile(root);
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

/** True for any run's state file, and the plan unless it is open for a change. */
function isProtected(root: string, file: string, planOpen: boolean): boolean {
	const rel = path.relative(root, file);
	if (planOpen && rel === "plan.yaml") return false;
	return rel === "plan.yaml" || /^runs[\\/][^\\/]+[\\/]run\.yaml$/.test(rel);
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
				reason: "The skill scripts commit this agent's git repo (compile, replan, critique); do not run git commands that change it. Read-only git (log, diff, show, status) is fine.",
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
					? (planOpen ? /run\.yaml/ : /(plan|run)\.yaml/).test(input.command ?? "")
					: false;
		if (touches)
			return {
				block: true,
				reason: planOpen
					? "Run state changes only through the agent tools (agent_new_run, agent_log_start/finish). Read the files with the read tool."
					: "A run exists, so the plan changes only through the agent tools: during a run agent_replan and agent_record_critique; otherwise /agent-revise (a change branch). Run state: agent_new_run, agent_log_start/finish. Read the files with the read tool.",
			};
	});

	// Swap only Pi's opening line, so the tool list, rules and the appended role stay as Pi builds them
	// (customPrompt would drop them). If Pi rewords the line, nothing matches and the prompt is left alone.
	pi.on("before_agent_start", (event, ctx) => {
		if (!findRoot(ctx.cwd) || !event.systemPrompt.includes(PI_PREAMBLE)) return;
		return { systemPrompt: event.systemPrompt.replace(PI_PREAMBLE, ROLE_PREAMBLE) };
	});

	pi.on("session_start", async (_event, ctx) => {
		const root = findRoot(ctx.cwd);
		if (!root) return;
		loadEnvFile(root);
		const probe = () => pi.exec(pythonFor(root), ["-c", "import dotenv, pydantic, pydantic_settings, ruamel.yaml, typer"], { cwd: root, timeout: 10_000 });
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

	// --- commands ---------------------------------------------------------------
	pi.registerCommand("agent-run", {
		description: "Plan, continue or run this agent from where it stands",
		handler: async (args, ctx) => ask(ctx, RUN_PROMPT(args.trim())),
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
		description: "Propose improvements from this agent's sessions and runs, applied on an improve/ branch you review (no run active): /agent-improve [focus]",
		handler: async (args, ctx) => {
			const root = findRoot(ctx.cwd);
			if (!root) {
				ctx.ui.notify("Not inside an agent folder (no plan.yaml).", "error");
				return;
			}
			// the digest first: with nothing new to learn from, no branch is opened (and none left to review)
			const d = await runScript(pi, root, "session_digest.py", ["--agent", root]);
			if (!d.data?.ok) {
				ctx.ui.notify(d.data?.error ?? `session_digest.py failed: ${(d.stderr || d.stdout).trim().slice(0, 500)}`, "error");
				return;
			}
			if (!d.data.sessions?.length && !d.data.runs?.length && !args.trim() && currentBranch(root) === "main") {
				ctx.ui.notify("Nothing new since the last /agent-improve: no unreviewed sessions or runs. Give a focus to improve anyway: /agent-improve <focus>.", "info");
				return;
			}
			const change = await beginChange(ctx, "improve");
			if (!change) return;
			activateSubagent(pi); // the critic
			pi.sendUserMessage(IMPROVE_PROMPT(args.trim(), change.branch, JSON.stringify(d.data)));
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
			const ends: Record<string, string> = { approval: ", needs your approval", review: ", then your review", replan: ", then a replan" };
			const waves = (s.waves ?? []).map((w: any) => `${w.id} (${w.stages.join(", ")}${ends[w.ends_with] ?? ""})`).join(", then ");
			const comingUp = b.active_run
				? `run ${b.active_run}: ${b.stages_done}/${b.stages_total} stages done${b.next_wave ? `, wave ${b.next_wave} next` : ""}`
				: `/agent-run starts a run: ${waves || "nothing to run"}`;
			const waiting = [
				...(b.open_questions ?? []).map((q: string) => `answer: ${q}`),
				...(b.active_run ? (pf.decisions_needed ?? []).map((d: any) => `decide: ${d.stage}`) : []),
				...(b.active_run ? (pf.approvals_needed ?? []).map((a: any) => `approve: ${a.stage}`) : []),
				...(b.active_run && pf.review_needed ? [`review wave ${pf.review_needed.wave}`] : []),
				...(b.unmerged ?? []).map((br: string) => `review branch ${br}`),
				...(s.agent?.missing_params ?? []).map((p: string) => `set ${p}`),
				...(s.agent?.missing_secrets ?? []).map((v: string) => `add ${v} to .env`),
			];
			const problems = [
				...chk.errors,
				...Object.entries(chk.compile_errors as Record<string, string[]>).flatMap(([w, es]) => es.map((e) => `${w}: ${e}`)),
				...(b.active_run && !next.data?.ok && next.data?.error ? [next.data.error] : []),
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
			"Read this agent's earlier Pi sessions (their JSONL files have lines too long for the read tool). action list: the sessions, newest first. action show: a bounded transcript of one (file from list): per record its number i, time, role, text, tool calls and results, each clipped. One line per record: number, time, U (user) / A (assistant, then -> tool(args)) / R (result; R! failed). Select records with from/to (record numbers) or around (a timestamp, e.g. a failure's `at` in the improve digest; window records each side); it stops at a size cap and ends with [more: next_from N].",
		promptSnippet: "List or read this agent's earlier sessions",
		promptGuidelines: [
			"To look into an earlier session, use agent_sessions; do not read the session JSONL files with read or bash, or write a parser for them.",
		],
		parameters: Type.Object({
			action: Type.Union([Type.Literal("list"), Type.Literal("show")]),
			file: Type.Optional(Type.String({ description: "show: a session file name from list, or the start of one" })),
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
			"Compile the active run's next wave into the exact pi-subagents call. Returns {ok, run, wave, stages, ends_with, call, children} when it can launch, or {ok: false, preflight} listing params to set (missing_params), secrets the user must add to .env (missing_secrets), decisions to collect, approvals to get, and review_needed when the last wave ended with a review the user has not answered yet. Only the launched wave's plan is committed; children that completed in a failed launch of the same wave, unchanged since, are not run again (notes say which).",
		promptSnippet: "Compile the next agent wave into a subagent call",
		promptGuidelines: [
			"Never write a wave script by hand; use agent_compile and pass its `call` to the subagent tool unchanged. `call.workflow` is a script path (pi-subagents reads it itself): pass it as-is, do not open the file or paste its contents into the tool call.",
			"Only list a stage in `approve` after the user explicitly approved that stage's side effect in this session.",
			"Pass `reviewed: true` only after the user saw the last wave's outputs (preflight review_needed) and said to go on.",
		],
		parameters: Type.Object({
			approve: Type.Optional(Type.Array(Type.String(), { description: "side-effect stage keys the user explicitly approved" })),
			reviewed: Type.Optional(Type.Boolean({ description: "the user reviewed the last wave's outputs and said to go on" })),
		}),
		script: "compile_wave.py",
		args: (p) => [...opt("--approve", p.approve?.join(",")), ...(p.reviewed ? ["--reviewed"] : [])],
	});

	scriptTool({
		name: "agent_record_decision",
		label: "Record decision",
		description: "Record the user's answer for a decision stage, in the file the compiler reads.",
		promptGuidelines: ["Record only answers the user actually gave; never answer a decision stage yourself."],
		parameters: Type.Object({
			stage: Type.String({ description: "the decision stage key" }),
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
			"Record a finished wave in the active run. Pass only the wave: the outcome (the workflow script's return value, status, cost) is read from pi-subagents. `done` marks its stages done (and the run done after the last wave); failed, blocked or stopped put them back to pending so the wave can be fixed and relaunched. Commits files the wave wrote into the agent on the run's branch. Returns the wave's ends_with (what to do next), a summary of its children, next_wave (with `preflight`: the decisions, approvals, params or secrets its compile needs first, to ask for before compiling) and, when the run is done, its branch for review, the plan's `done_when` for the final report, and `mission_close` (pass it to the subagent tool unchanged).",
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
	});

	scriptTool({
		name: "agent_replan",
		label: "Replan",
		description:
			"Change pending work in plan.yaml: replace or add stages (full definitions), remove pending stages, record unknowns' findings, add questions or record answers. Waves follow from the stages. Refuses changes to stages that are done or running in the active run, the request, goal, done_when, params, inputs, outputs and agents, and any weakening of a side effect. Commits the agent (git keeps the previous revision), bumps revision, sets status draft and clears the critique.",
		promptSnippet: "Change pending stages of the agent plan",
		promptGuidelines: [
			"Use agent_replan for every plan change after a wave has run; direct edits to plan.yaml are blocked.",
			"Give each changed stage in full, as it should read in the plan; omit `provisional` and `resolves_after` to make a provisional stage concrete.",
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
		args: (p) => ["replan", "--change", JSON.stringify(p)],
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
