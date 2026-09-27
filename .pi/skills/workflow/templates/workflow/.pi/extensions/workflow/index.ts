/**
 * Workflow agent: commands and tools for running this workflow.
 *
 * Commands
 *   /workflow-run [note]        plan, continue or run from where it stands (model)
 *   /workflow-status            where the workflow stands (no model)
 *   /workflow-revise [change]   change the plan on a branch, for review (no run active; model)
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
 * and .pi/plan.yaml may be edited directly only on a change branch with no run active;
 * during a run the plan changes through workflow_replan, which refuses to touch what ran,
 * the goal or a side effect's approval.
 *
 * The logic lives in the skill's Python scripts (.pi/skills/workflow/scripts);
 * this file runs them and formats their JSON.
 */
import { defineTool, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
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
\`workflow_stop_run\` and \`workflow_merge\`. Pass the \`call\` that \`workflow_compile\` returns to the
subagent tool unchanged.

Note from the user for this run (may be empty): ${note}`;

const REVISE_PROMPT = (change: string, branch: string, mode: string) => {
	const where =
		mode === "planning"
			? "No run has started yet: plan (or continue planning) on main, editing .pi/plan.yaml directly."
			: `You are on the change branch "${branch}", created from main with the revision bumped. Edit
.pi/plan.yaml, gates/ and .pi/agents/ directly; anything in the plan may change. End with the
user's review of the branch and workflow_merge (merge, keep or discard).`;
	return `Change this workflow's plan following the "Changing the plan" section
of \`.pi/skills/workflow/references/run-protocol.md\`. Launch no work stages.

${where}

Change requested (may be empty: then ask what to change, or continue the change on this branch): ${change}`;
};

// --- helpers --------------------------------------------------------------------

/** The workflow root: the folder whose .pi/ holds plan.yaml, found from `start` upwards. */
function findRoot(start: string): string | undefined {
	let dir = path.resolve(start);
	for (;;) {
		if (fs.existsSync(path.join(dir, ".pi", "plan.yaml"))) return dir;
		const parent = path.dirname(dir);
		if (parent === dir) return undefined;
		dir = parent;
	}
}

function mustRoot(ctx: ExtensionContext): string {
	const root = findRoot(ctx.cwd);
	if (!root) throw new Error("not inside a workflow folder (no .pi/plan.yaml)");
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
	if (planOpen && rel === path.join(".pi", "plan.yaml")) return false;
	return rel === path.join(".pi", "plan.yaml") || /^runs[\\/][^\\/]+[\\/]run\.yaml$/.test(rel);
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
	const v = await runScript(pi, root, "validate_plan.py", [path.join(".pi", "plan.yaml")], signal);
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

	pi.on("session_start", async (_event, ctx) => {
		const root = findRoot(ctx.cwd);
		if (!root) return;
		loadEnvFile(root);
		const py = pythonFor(root);
		const r = await pi.exec(py, ["-c", "import dotenv, pydantic, pydantic_settings, ruamel.yaml, typer"], { cwd: root, timeout: 10_000 });
		if (r.code !== 0) ctx.ui.notify(`The skill scripts' Python packages are missing for ${py}. Run \`uv sync\` in ${root}, then restart pi.`, "warning");
	});

	/** Show text in the conversation without starting a model turn. */
	const show = (title: string, body: string) =>
		pi.sendMessage({ customType: "workflow", content: `${title}\n\n${body}`, display: true }, { triggerTurn: false });

	const ask = (ctx: ExtensionContext, text: string) => {
		if (!findRoot(ctx.cwd)) {
			ctx.ui.notify("Not inside a workflow folder (no .pi/plan.yaml).", "error");
			return;
		}
		if (!ctx.isIdle()) {
			ctx.ui.notify("The agent is busy; wait for it to finish, then run the command again.", "warning");
			return;
		}
		pi.sendUserMessage(text);
	};

	// --- commands ---------------------------------------------------------------
	pi.registerCommand("workflow-run", {
		description: "Plan, continue or run this workflow from where it stands",
		handler: async (args, ctx) => ask(ctx, RUN_PROMPT(args.trim())),
	});

	pi.registerCommand("workflow-revise", {
		description: "Change this workflow's plan on a branch you review before it reaches main (no run active): /workflow-revise [change]",
		handler: async (args, ctx) => {
			const root = findRoot(ctx.cwd);
			if (!root) {
				ctx.ui.notify("Not inside a workflow folder (no .pi/plan.yaml).", "error");
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
			pi.sendUserMessage(REVISE_PROMPT(args.trim(), r.data.branch ?? "main", r.data.mode));
		},
	});

	pi.registerCommand("workflow-status", {
		description: "Show where this workflow stands (changes nothing)",
		handler: async (_args, ctx) => {
			const root = findRoot(ctx.cwd);
			if (!root) {
				ctx.ui.notify("Not inside a workflow folder (no .pi/plan.yaml).", "error");
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
			description: "Validate this workflow's .pi/plan.yaml and compile-check every concrete pending wave. The summary lists the waves still to run (derived from the stages).",
			promptSnippet: "Validate and compile-check this workflow's plan",
			promptGuidelines: ["Use workflow_check after every change to .pi/plan.yaml."],
			parameters: Type.Object({}),
			async execute(_id, _params, signal, _onUpdate, ctx) {
				const res = await check(pi, mustRoot(ctx), signal);
				return { content: [{ type: "text" as const, text: JSON.stringify(res, null, 2) }], details: res };
			},
		}),
	);

	pi.registerTool(
		defineTool({
			name: "workflow_new_run",
			label: "New run",
			description:
				"Start a run of this workflow: a folder runs/<id>/ for its state, work files and outputs, which becomes the active run. It starts from main; the run's own plan changes go to a branch run/<id>. Refuses while another run is active or another branch is checked out (review it first with workflow_merge).",
				promptGuidelines: ["Start a run only when there is no active run and the user wants to run the workflow."],
				parameters: Type.Object({
					note: Type.Optional(Type.String({ description: "what this run is for, in the user's words" })),
				}),
				async execute(_id, params, signal, _onUpdate, ctx) {
					const args = ["new"];
					if (params.note) args.push("--note", params.note);
					return toolResult(await runScript(pi, mustRoot(ctx), "run_log.py", args, signal), "run_log.py new");
				},
				}),
				);
				
				pi.registerTool(
				defineTool({
					name: "workflow_stop_run",
					label: "Stop run",
					description:
						"Stop the active run (it stays in runs/, marked stopped). Returns its branch, if it changed the plan, for the user's review with workflow_merge, and `mission_close` (pass it to the subagent tool unchanged). Refuses while a wave is running.",
					promptGuidelines: ["Stop a run only when the user asked to stop it or to start over."],
					parameters: Type.Object({ note: Type.Optional(Type.String({ description: "why it stopped" })) }),
					async execute(_id, params, signal, _onUpdate, ctx) {
						const args = ["stop"];
						if (params.note) args.push("--note", params.note);
						return toolResult(await runScript(pi, mustRoot(ctx), "run_log.py", args, signal), "run_log.py stop");
					},
				}),
				);
				
				pi.registerTool(
				defineTool({
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
					async execute(_id, params, signal, _onUpdate, ctx) {
						return toolResult(await runScript(pi, mustRoot(ctx), "workflow_git.py", ["merge", "--action", params.action], signal), "workflow_git.py merge");
					},
				}),
				);
	
	pi.registerTool(
		defineTool({
			name: "workflow_compile",
			label: "Compile wave",
			description:
				"Compile the active run's next wave into the exact pi-subagents call. Returns {ok, run, wave, stages, ends_with, call, children} when it can launch, or {ok: false, preflight} listing params to set (missing_params), secrets the user must add to .env (missing_secrets), decisions to collect and approvals to get.",
			promptSnippet: "Compile the next workflow wave into a subagent call",
			promptGuidelines: [
				"Never write a workflowScript by hand; use workflow_compile and pass its `call` to the subagent tool unchanged.",
				"Only list a stage in `approve` after the user explicitly approved that stage's side effect in this session.",
			],
			parameters: Type.Object({
				approve: Type.Optional(Type.Array(Type.String(), { description: "side-effect stage keys the user explicitly approved" })),
			}),
			async execute(_id, params, signal, _onUpdate, ctx) {
				const args: string[] = [];
				if (params.approve?.length) args.push("--approve", params.approve.join(","));
				return toolResult(await runScript(pi, mustRoot(ctx), "compile_wave.py", args, signal), "compile_wave.py");
			},
		}),
	);

	pi.registerTool(
		defineTool({
			name: "workflow_record_decision",
			label: "Record decision",
			description: "Record the user's answer for a decision stage, in the file the compiler reads.",
			promptGuidelines: ["Record only answers the user actually gave; never answer a decision stage yourself."],
			parameters: Type.Object({
				stage: Type.String({ description: "the decision stage key" }),
				answer: Type.Any({ description: "the user's answer as JSON (object, string, list)" }),
			}),
			async execute(_id, params, signal, _onUpdate, ctx) {
				const args = ["decision", "--stage", params.stage, "--answer", JSON.stringify(params.answer)];
				return toolResult(await runScript(pi, mustRoot(ctx), "run_log.py", args, signal), "run_log.py decision");
			},
		}),
	);

	pi.registerTool(
		defineTool({
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
			async execute(_id, params, signal, _onUpdate, ctx) {
				const args = ["start", "--wave", params.wave, "--subagent-run-id", params.subagent_run_id];
				if (params.mission_id) args.push("--mission-id", params.mission_id);
				if (params.approved?.length) args.push("--approved", params.approved.join(","));
				if (params.decisions) args.push("--decisions", JSON.stringify(params.decisions));
				return toolResult(await runScript(pi, mustRoot(ctx), "run_log.py", args, signal), "run_log.py start");
			},
		}),
	);

	pi.registerTool(
		defineTool({
			name: "workflow_log_finish",
			label: "Log wave result",
			description:
				"Record a finished wave in the active run. Pass only the wave: the outcome (the workflow script's return value, status, cost) is read from pi-subagents. `done` marks its stages done (and the run done after the last wave); failed, blocked or stopped put them back to pending so the wave can be fixed and relaunched. Commits files the wave wrote into the workflow on the run's branch. Returns the wave's ends_with (what to do next), a summary of its children and, when the run is done, its branch for review and `mission_close` (pass it to the subagent tool unchanged).",
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
			async execute(_id, params, signal, _onUpdate, ctx) {
				const args = ["finish", "--wave", params.wave];
				if (params.status) args.push("--status", params.status);
				if (params.result !== undefined) args.push("--result", JSON.stringify(params.result)); // saved in the run's .compiled/
				if (params.note) args.push("--note", params.note);
				return toolResult(await runScript(pi, mustRoot(ctx), "run_log.py", args, signal), "run_log.py finish");
			},
		}),
	);

	/** Run a plan_edit.py command with its JSON payload. */
	const planEdit = async (ctx: ExtensionContext, cmd: "replan" | "critique" | "params", payload: unknown, signal?: AbortSignal) => {
		const flag = { replan: "--change", critique: "--result", params: "--values" }[cmd];
		return toolResult(await runScript(pi, mustRoot(ctx), "plan_edit.py", [cmd, flag, JSON.stringify(payload)], signal), `plan_edit.py ${cmd}`);
	};

	pi.registerTool(
		defineTool({
			name: "workflow_replan",
			label: "Replan",
			description:
				"Change pending work in .pi/plan.yaml: replace or add stages (full definitions), remove pending stages, record unknowns' findings, add questions or record answers. Waves follow from the stages. Refuses changes to stages that are done or running in the active run, the request, goal, done_when, params, inputs, outputs and agents, and any weakening of a side effect. Commits the workflow (git keeps the previous revision), bumps revision, sets status draft and clears the critique.",
			promptSnippet: "Change pending stages of the workflow plan",
			promptGuidelines: [
				"Use workflow_replan for every plan change after a wave has run; direct edits to .pi/plan.yaml are blocked.",
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
			async execute(_id, params, signal, _onUpdate, ctx) {
				return planEdit(ctx, "replan", params, signal);
			},
		}),
	);

	pi.registerTool(
		defineTool({
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
			async execute(_id, params, signal, _onUpdate, ctx) {
				return planEdit(ctx, "critique", params, signal);
			},
		}),
	);

	pi.registerTool(
		defineTool({
			name: "workflow_set_params",
			label: "Set params",
			description:
				"Set values for the plan's params in .env (per machine). Refuses secrets: the user adds those to .env themselves. Returns validation, including input paths that are relative or do not exist.",
			parameters: Type.Object({
				values: Type.Record(Type.String(), Type.String(), { description: "param name -> value; absolute paths for files and folders" }),
			}),
			async execute(_id, params, signal, _onUpdate, ctx) {
				return planEdit(ctx, "params", params.values, signal);
			},
		}),
	);
}
