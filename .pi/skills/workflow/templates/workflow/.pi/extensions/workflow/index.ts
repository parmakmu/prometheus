/**
 * Workflow agent: commands and tools for running this workflow.
 *
 * Commands
 *   /workflow-run [note]        plan, continue or run from where it stands (model)
 *   /workflow-status            where the workflow stands (no model)
 *   /workflow-revise [change]   change the plan on a branch, for review (no run active; model)
 *   /workflow-improve [focus]   propose fixes from the sessions and runs, applied on a change branch (no run active; model)
 *
 * Tools for the model (the mechanical steps of the run protocol)
 *   workflow_check            validate the plan and compile-check its waves
 *   workflow_new_run          start a run (runs/<id>/)
 *   workflow_stop_run         stop the active run (kept in runs/); returns its branch for review
 *   workflow_compile          compile the active run's next wave into the exact subagent call
 *   workflow_record_decision  write the user's answer for a decision stage
 *   workflow_log_start        record a wave launch
 *   workflow_log_finish       record a wave result
 *   workflow_replan           change pending stages during a run (after discovery, or asked)
 *   workflow_record_critique  record a plan-critic result; marks the plan ready if it validates
 *   workflow_set_params       set param values in .env (not secrets)
 *   workflow_merge            finish the review of a run or change branch: merge, keep or discard
 *
 * The plan is reusable; each execution of it is a run, a folder runs/<id>/ with its
 * state (run.yaml), the children's work/ and compiled waves. Waves are not written in the
 * plan; the scripts derive them from the stages and the active run's stage status.
 * The workflow is its own git repo (workflow_git.py). main holds the reviewed plan; a
 * run's changes go to run/<id>, a /workflow-revise change to change/<date>-<n>, and each
 * reaches main only when the user merges it. The scripts commit (never while a wave runs:
 * pi-subagents checks the repo's staged index around writer children); git commands that
 * change the repo are blocked for the model.
 *
 * Param values live in .env (per machine, may hold secrets): this extension loads it into
 * pi's environment, which children and gate commands inherit, and the model may not read or
 * change it directly. Once a run exists, direct edits to runs/<id>/run.yaml are blocked,
 * and plan.yaml may be edited directly only on a change branch with no run active;
 * during a run the plan changes through workflow_replan, which refuses to touch what ran,
 * the goal or a side effect's approval.
 *
 * The logic lives in the skill's Python scripts (.pi/skills/workflow/scripts);
 * this file runs them and formats their JSON.
 */
import { defineTool, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type, type Static, type TSchema } from "typebox";
import * as fs from "node:fs";
import * as path from "node:path";
import { parseEnv } from "node:util";


// --- prompts sent to the model ------------------------------------------------

const RUN_PROMPT = (note: string) => `Run this workflow. Read \`.pi/skills/workflow/references/run-protocol.md\`
and follow it from "Every start": check, then plan, replan or run the next
wave, whichever the plan needs.

Use the workflow tools for every mechanical step: \`workflow_check\`,
\`workflow_new_run\`, \`workflow_compile\`, \`workflow_record_decision\`,
\`workflow_log_start\`, \`workflow_log_finish\`, \`workflow_replan\`,
\`workflow_stop_run\` and \`workflow_merge\`. The subagent tool is already active: pass
the \`call\` that \`workflow_compile\` returns to it unchanged, without calling
subagents_enable or listing agents first. Ask every question that has a fixed set of
answers with the question tool (run-protocol.md, "Asking the user").

Note from the user for this run (may be empty): ${note}`;

const REVISE_PROMPT = (change: string, branch: string, mode: string) => {
	const where =
		mode === "planning"
			? "No run has started yet: plan (or continue planning) on main, editing plan.yaml directly."
			: `You are on the change branch "${branch}", created from main with the revision bumped. Edit
plan.yaml, gates/, scripts/ and .pi/agents/ directly (packages: uv add); anything in the plan may change. End with the
user's review of the branch and workflow_merge (merge, keep or discard).`;
	return `Change this workflow's plan following the "Changing the plan" section
of \`.pi/skills/workflow/references/run-protocol.md\`. Launch no work stages.

${where}

Change requested (may be empty: then ask what to change, or continue the change on this branch): ${change}`;
};

const IMPROVE_PROMPT = (focus: string, branch: string, digest: string) => `Improve this workflow from how it actually went. You are on the change
branch "${branch}". Launch no work stages.

Below is a digest of this workflow's latest sessions and its runs: user messages, failed or
blocked tool results, repeated calls, cost, launches that failed or ran twice. It only points at
evidence; you were there, so use what you remember of this session too, and open the files it
points at before proposing anything.

1. Find causes, not symptoms: a gate that rejected correct output, a stage program rewritten or
   failing each run, a stage task that left something out, a question the plan should have
   settled, a refusal the user hit, work the model did by hand that a script could do. Skip
   one-offs; a proposal needs a repeat or a real cost.
2. Give each proposal: the evidence (session file or run id), the file to change, the edit, and what
   it should prevent. Targets you may change here: plan.yaml, gates/, scripts/, .pi/agents/,
   packages (uv add), and rules for this agent: short lines under the heading
   "## Lessons for this workflow" at the end of \`.pi/APPEND_SYSTEM.md\` (add the heading if missing;
   /workflow-refresh keeps that section). Every line of it is sent with every turn, so write a rule only
   for a behaviour the evidence shows repeating, keep it to one or two sentences about this workflow,
   merge or drop lines it makes redundant, and keep the section under 10 lines. Do not change the text
   above the heading, or the skill and references: they are Prometheus's copies, which /workflow-refresh
   overwrites. A problem there that is not specific to this workflow goes in a separate list, as text for
   the user to take to Prometheus.
   \`lessons\` are what earlier /workflow-improve passes decided: do not re-propose a rejected one, and
   if an applied one's problem shows up again, say its fix did not work.
3. Ask the user about each proposal with the question tool (apply / skip), then make the accepted
   edits following the "Changing the plan" section of
   \`.pi/skills/workflow/references/run-protocol.md\` (critic included when the plan changes).
   Append every proposal, applied or skipped, to \`improvements.yaml\` (a list; create it if missing):
   \`{date: <today, YYYY-MM-DD>, target: <file>, proposal: <one line>, evidence: <session or run>,
   decision: applied | rejected, note: <the user's reason, if given>}\`. Write it even when nothing
   was accepted; it marks these sessions as reviewed.
   End with the user's review of the branch and workflow_merge (merge, keep or discard).

Focus requested (may be empty): ${focus}

Digest:
${digest}`;

// Pi opens its system prompt with this coding-assistant line; the workflow agent orchestrates instead.
const PI_PREAMBLE =
	"You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.";
const ROLE_PREAMBLE =
	"You are the workflow agent of one workflow, operating inside pi, a coding agent harness. You orchestrate: you run the workflow's plan with subagents and look after its plan, gates and scripts as the run protocol allows. You do not do a stage's work yourself.";

// --- helpers --------------------------------------------------------------------

/** The workflow root: the folder holding plan.yaml and .pi/, found from `start` upwards. */
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
	if (!root) throw new Error("not inside a workflow folder (no plan.yaml)");
	return root;
}

/** Python for the skill scripts: $PI_WORKFLOW_PYTHON, else the nearest .venv (the workflow's
 * own after `uv sync`, or Prometheus's when the workflow lives inside it), else python3. */
function pythonFor(start: string): string {
	if (process.env.PI_WORKFLOW_PYTHON) return process.env.PI_WORKFLOW_PYTHON;
	const bin = process.platform === "win32" ? path.join("Scripts", "python.exe") : path.join("bin", "python");
	for (let dir = path.resolve(start); ; dir = path.dirname(dir)) {
		if (fs.existsSync(path.join(dir, ".venv", bin))) return path.join(dir, ".venv", bin);
		if (path.dirname(dir) === dir) return "python3";
	}
}

/** Make pi-subagents' `subagent` tool active before a command's prompt goes out. The command is the
 * user's authorization to delegate; left to the model, subagents_enable changes the tool list mid-turn,
 * which costs two turns and drops the prompt cache (about a fifth of run 4's orchestrator cost).
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

/** Load the workflow's .env into pi's environment: children and gate commands inherit it, and
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
	const file = path.join(root, ".pi", "skills", "workflow", "scripts", script);
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

/** Tool result carrying a script's JSON; throws when the script produced no JSON. */
function toolResult(r: ScriptResult, what: string) {
	if (r.data === undefined) throw new Error(`${what} failed (exit ${r.code}): ${(r.stderr || r.stdout).trim().slice(0, 2000)}`);
	return { content: [{ type: "text" as const, text: JSON.stringify(r.data, null, 2) }], details: r.data };
}

/** True for any run's state file, and the plan unless it is open for a change. */
function isProtected(root: string, file: string, planOpen: boolean): boolean {
	const rel = path.relative(root, file);
	if (planOpen && rel === "plan.yaml") return false;
	return rel === "plan.yaml" || /^runs[\\/][^\\/]+[\\/]run\.yaml$/.test(rel);
}

/** What only a reviewed branch changes once a run exists: gates, stage programs, agents, packages. */
const BRANCH_FILES = /^(gates|scripts|\.pi[\\/]agents)[\\/]|^(pyproject\.toml|uv\.lock)$/;
const PACKAGE_COMMAND = /\buv\s+(add|remove)\b/;

/** run/… and change/… branches (loose and packed refs): ones waiting for review. */
function reviewBranches(root: string): string[] {
	const names = new Set<string>();
	for (const kind of ["run", "change"]) {
		try {
			for (const d of fs.readdirSync(path.join(root, ".git", "refs", "heads", kind))) names.add(`${kind}/${d}`);
		} catch {}
	}
	try {
		for (const m of fs.readFileSync(path.join(root, ".git", "packed-refs"), "utf8").matchAll(/refs\/heads\/((?:run|change)\/\S+)/g))
			names.add(m[1]);
	} catch {}
	return [...names].sort();
}

/** .env and its variants (.env.local, …): param values, possibly secrets. */
const ENV_FILE = /^\.env(\..*)?$/;
const ENV_IN_COMMAND = /(^|[^\w.])\.env(?![\w-])/;

/** Git commands that change the repo or its index; only the scripts commit. */
const GIT_WRITE = /\bgit\s+(?:-C\s+\S+\s+|-c\s+\S+\s+)*(?:add|commit|reset|checkout|restore|switch|rebase|merge|stash|tag|rm|mv|revert|cherry-pick|am|apply|init|pull|push|update-index|update-ref)\b/;

/** The checked-out branch of the workflow's repo (from .git/HEAD), if any. */
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
		compiles: c.data?.ok ?? false,
		compile_errors: c.data?.compile_errors ?? {},
	};
}

// --- extension --------------------------------------------------------------------

export default function workflowAgent(pi: ExtensionAPI) {
	pi.on("tool_call", (event, ctx) => {
		const root = findRoot(ctx.cwd);
		if (!root) return;
		const input = event.input as { path?: string; command?: string };
		if ((event.toolName === "bash" || event.toolName === "powershell") && GIT_WRITE.test(input.command ?? ""))
			return {
				block: true,
				reason: "The workflow scripts commit this workflow's git repo (compile, replan, critique); do not run git commands that change it. Read-only git (log, diff, show, status) is fine.",
			};
		const envFile = input.path
			? ENV_FILE.test(path.basename(input.path))
			: (event.toolName === "bash" || event.toolName === "powershell") && ENV_IN_COMMAND.test(input.command ?? "");
		if (envFile)
			return {
				block: true,
				reason: ".env holds this machine's param values and may hold secrets: do not read or change it. Values that are not secrets: workflow_set_params. Secrets: ask the user to add the variable to .env themselves (workflow_compile's preflight names it); never ask for a secret's value.",
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
							: " Ask the user to start a change with /workflow-revise."),
				};
			}
		}
		// the plan is open for direct edits only on a change branch (/workflow-revise) with no run active
		const planOpen = !runActive(root) && (currentBranch(root) ?? "").startsWith("change/");
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
					? "Run state changes only through the workflow tools (workflow_new_run, workflow_log_start/finish). Read the files with the read tool."
					: "A run exists, so the plan changes only through the workflow tools: during a run workflow_replan and workflow_record_critique; otherwise /workflow-revise (a change branch). Run state: workflow_new_run, workflow_log_start/finish. Read the files with the read tool.",
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
		// First start: no .venv yet. Create it from this workflow's pyproject.toml and uv.lock, as the gates and
		// programs' `uv run` would (skipped when $PI_WORKFLOW_PYTHON names the Python to use).
		if (r.code !== 0 && !process.env.PI_WORKFLOW_PYTHON) {
			ctx.ui.notify("Setting up this workflow's Python environment (uv sync)...", "info");
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
		pi.sendMessage({ customType: "workflow", content: `${title}\n\n${body}`, display: true }, { triggerTurn: false });

	const ask = (ctx: ExtensionContext, text: string) => {
		if (!findRoot(ctx.cwd)) {
			ctx.ui.notify("Not inside a workflow folder (no plan.yaml).", "error");
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
	pi.registerCommand("workflow-run", {
		description: "Plan, continue or run this workflow from where it stands",
		handler: async (args, ctx) => ask(ctx, RUN_PROMPT(args.trim())),
	});

	/** Open (or continue) the change branch for a command that edits the workflow; undefined after telling the user why not. */
	const beginChange = async (ctx: ExtensionContext) => {
		const root = findRoot(ctx.cwd);
		if (!root) {
			ctx.ui.notify("Not inside a workflow folder (no plan.yaml).", "error");
			return;
		}
		if (!ctx.isIdle()) {
			ctx.ui.notify("The agent is busy; wait for it to finish, then run the command again.", "warning");
			return;
		}
		const r = await runScript(pi, root, "workflow_git.py", ["begin-change"]);
		if (!r.data?.ok) {
			ctx.ui.notify(r.data?.error ?? `workflow_git.py begin-change failed: ${(r.stderr || r.stdout).trim().slice(0, 500)}`, "error");
			return;
		}
		return { root, branch: r.data.branch ?? "main", mode: r.data.mode };
	};

	pi.registerCommand("workflow-revise", {
		description: "Change this workflow's plan on a branch you review before it reaches main (no run active): /workflow-revise [change]",
		handler: async (args, ctx) => {
			const change = await beginChange(ctx);
			if (!change) return;
			activateSubagent(pi); // the critic
			pi.sendUserMessage(REVISE_PROMPT(args.trim(), change.branch, change.mode));
		},
	});

	pi.registerCommand("workflow-improve", {
		description: "Propose improvements from this workflow's sessions and runs, applied on a change branch you review (no run active): /workflow-improve [focus]",
		handler: async (args, ctx) => {
			const change = await beginChange(ctx);
			if (!change) return;
			const d = await runScript(pi, change.root, "session_digest.py", ["--workflow", change.root]);
			if (!d.data?.ok) {
				ctx.ui.notify(d.data?.error ?? `session_digest.py failed: ${(d.stderr || d.stdout).trim().slice(0, 500)}`, "error");
				return;
			}
			activateSubagent(pi); // the critic
			pi.sendUserMessage(IMPROVE_PROMPT(args.trim(), change.branch, JSON.stringify(d.data)));
		},
	});

	pi.registerCommand("workflow-status", {
		description: "Show where this workflow stands (changes nothing)",
		handler: async (_args, ctx) => {
			const root = findRoot(ctx.cwd);
			if (!root) {
				ctx.ui.notify("Not inside a workflow folder (no plan.yaml).", "error");
				return;
			}
			const [info, chk, next] = await Promise.all([
				runScript(pi, root, "list_workflows.py", ["--workflow", root]),
				check(pi, root),
				runScript(pi, root, "compile_wave.py", ["--no-commit"]),
			]);
			const b = info.data ?? {};
			const s = chk.summary ?? {};
			const pf = next.data?.preflight ?? {};
			const lines = [
				`goal: ${b.goal || "(none yet)"}`,
				`status: ${b.status}   revision: ${b.revision}   runs: ${b.runs ?? 0}`,
				b.active_run
					? `active run: ${b.active_run}   stages done: ${b.stages_done}/${b.stages_total}   next wave: ${b.next_wave ?? "none"}`
					: `no active run${b.last_run ? ` (last: ${b.last_run.id} ${b.last_run.status})` : ""}; /workflow-run starts one`,
				`waves to go${b.active_run ? "" : " in a new run"}: ${(s.waves ?? []).map((w: any) => `${w.id}:[${w.stages.join(",")}]${w.ends_with === "none" ? "" : ` then ${w.ends_with}`}`).join("  ") || "none"}`,
				`last wave: ${b.last_run?.last_wave ? `${b.last_run.last_wave.wave} ${b.last_run.last_wave.status}` : "none"}`,
				`branch: ${b.branch ?? "none"}${(b.unmerged ?? []).length ? `   unmerged: ${b.unmerged.join(", ")}` : ""}`,
				`provisional (waiting for replan): ${(s.provisional ?? []).join(", ") || "none"}`,
				`open questions: ${(b.open_questions ?? []).join(", ") || "none"}`,
				`missing params: ${(s.workflow?.missing_params ?? []).join(", ") || "none"}`,
				`secrets to add to .env: ${(s.workflow?.missing_secrets ?? []).join(", ") || "none"}`,
			];
			if (next.data?.ok) {
				lines.push(`next wave ${next.data.wave} compiles and can launch`);
			} else if (b.active_run) {
				const decisions = (pf.decisions_needed ?? []).map((d: any) => d.stage);
				const approvals = (pf.approvals_needed ?? []).map((a: any) => a.stage);
				if (decisions.length) lines.push(`decisions to collect: ${decisions.join(", ")}`);
				if (approvals.length) lines.push(`approvals needed: ${approvals.join(", ")}`);
				if (next.data?.error) lines.push(`next wave: ${next.data.error}`);
			}
			lines.push(`plan valid: ${chk.valid}   compiles: ${chk.compiles}`);
			for (const e of chk.errors) lines.push(`  error: ${e}`);
			for (const [w, es] of Object.entries(chk.compile_errors as Record<string, string[]>)) {
				for (const e of es) lines.push(`  compile ${w}: ${e}`);
			}
			lines.push("A running wave is shown as last recorded; /workflow-run checks it live.");
			show(`Workflow ${path.basename(root)}`, lines.join("\n"));
		},
	});

	// --- tools ------------------------------------------------------------------
	pi.registerTool(
		defineTool({
			name: "workflow_check",
			label: "Check workflow",
			description: "Validate this workflow's plan.yaml and compile-check every concrete pending wave. The summary lists the waves still to run (derived from the stages).",
			promptSnippet: "Validate and compile-check this workflow's plan",
			promptGuidelines: ["Use workflow_check after every change to plan.yaml."],
			parameters: Type.Object({}),
			async execute(_id, _params, signal, _onUpdate, ctx) {
				const res = await check(pi, mustRoot(ctx), signal);
				return { content: [{ type: "text" as const, text: JSON.stringify(res, null, 2) }], details: res };
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
	}) => {
		const { script, args, ...definition } = tool;
		pi.registerTool(
			defineTool({
				...definition,
				async execute(_id, params, signal, _onUpdate, ctx) {
					const argv = args(params);
					const what = argv[0] && !argv[0].startsWith("-") ? `${script} ${argv[0]}` : script;
					return toolResult(await runScript(pi, mustRoot(ctx), script, argv, signal), what);
				},
			}),
		);
	};
	/** [flag, value] when the value is given, else nothing. */
	const opt = (flag: string, value: string | undefined): string[] => (value ? [flag, value] : []);

	scriptTool({
		name: "workflow_new_run",
		label: "New run",
		description:
			"Start a run of this workflow: a folder runs/<id>/ for its state, work files and outputs, which becomes the active run. It starts from main; the run's own plan changes go to a branch run/<id>. Refuses while another run is active or another branch is checked out (review it first with workflow_merge).",
		promptGuidelines: ["Start a run only when there is no active run and the user wants to run the workflow."],
		parameters: Type.Object({
			note: Type.Optional(Type.String({ description: "what this run is for, in the user's words" })),
		}),
		script: "run_log.py",
		args: (p) => ["new", ...opt("--note", p.note)],
	});

	scriptTool({
		name: "workflow_stop_run",
		label: "Stop run",
		description:
			"Stop the active run (it stays in runs/, marked stopped). Returns its branch, if it changed the plan, for the user's review with workflow_merge, and `mission_close` (pass it to the subagent tool unchanged). Refuses while a wave is running.",
		promptGuidelines: ["Stop a run only when the user asked to stop it or to start over."],
		parameters: Type.Object({ note: Type.Optional(Type.String({ description: "why it stopped" })) }),
		script: "run_log.py",
		args: (p) => ["stop", ...opt("--note", p.note)],
	});

	scriptTool({
		name: "workflow_merge",
		label: "Merge branch",
		description:
			"Finish the review of the checked-out run or change branch and switch back to main: merge (into main; tags the revision), keep (the branch stays for later) or discard (delete it). Refuses while a run is active.",
		promptGuidelines: [
			"Call workflow_merge only with the action the user chose after seeing the branch's changes (git diff main...<branch>).",
		],
		parameters: Type.Object({
			action: Type.Union([Type.Literal("merge"), Type.Literal("keep"), Type.Literal("discard")]),
		}),
		script: "workflow_git.py",
		args: (p) => ["merge", "--action", p.action],
	});

	scriptTool({
		name: "workflow_compile",
		label: "Compile wave",
		description:
			"Compile the active run's next wave into the exact pi-subagents call. Returns {ok, run, wave, stages, ends_with, call, children} when it can launch, or {ok: false, preflight} listing params to set (missing_params), secrets the user must add to .env (missing_secrets), decisions to collect and approvals to get.",
		promptSnippet: "Compile the next workflow wave into a subagent call",
		promptGuidelines: [
			"Never write a workflowScript by hand; use workflow_compile and pass its `call` to the subagent tool unchanged. `call.workflowScriptPath` is a path (pi-subagents reads it itself): pass it as-is, do not open the file or paste its contents into the tool call.",
			"Only list a stage in `approve` after the user explicitly approved that stage's side effect in this session.",
		],
		parameters: Type.Object({
			approve: Type.Optional(Type.Array(Type.String(), { description: "side-effect stage keys the user explicitly approved" })),
		}),
		script: "compile_wave.py",
		args: (p) => opt("--approve", p.approve?.join(",")),
	});

	scriptTool({
		name: "workflow_record_decision",
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
		name: "workflow_log_start",
		label: "Log wave start",
		description: "Record that the active run's next wave was launched: marks its stages running and appends a launch to runs/<id>/run.yaml.",
		parameters: Type.Object({
			wave: Type.String({ description: "the wave id workflow_compile returned" }),
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
		name: "workflow_log_finish",
		label: "Log wave result",
		description:
			"Record a finished wave in the active run. Pass only the wave: the outcome (the workflow script's return value, status, cost) is read from pi-subagents. `done` marks its stages done (and the run done after the last wave); failed, blocked or stopped put them back to pending so the wave can be fixed and relaunched. Commits files the wave wrote into the workflow on the run's branch. Returns the wave's ends_with (what to do next), a summary of its children and, when the run is done, its branch for review, the plan's `done_when` for the final report, and `mission_close` (pass it to the subagent tool unchanged).",
		promptGuidelines: [
			"After a wave's completion notice, call workflow_log_finish with just the wave; do not look up the run's status or assemble its result yourself.",
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
		name: "workflow_replan",
		label: "Replan",
		description:
			"Change pending work in plan.yaml: replace or add stages (full definitions), remove pending stages, record unknowns' findings, add questions or record answers. Waves follow from the stages. Refuses changes to stages that are done or running in the active run, the request, goal, done_when, params, inputs, outputs and agents, and any weakening of a side effect. Commits the workflow (git keeps the previous revision), bumps revision, sets status draft and clears the critique.",
		promptSnippet: "Change pending stages of the workflow plan",
		promptGuidelines: [
			"Use workflow_replan for every plan change after a wave has run; direct edits to plan.yaml are blocked.",
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
		name: "workflow_record_critique",
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
		name: "workflow_set_params",
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
