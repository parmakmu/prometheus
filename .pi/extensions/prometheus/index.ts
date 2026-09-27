/**
 * Prometheus: commands and tools for creating workflows.
 *
 * Commands
 *   /workflow-new <request>                       plan a new workflow (model)
 *   /workflow-list [slug]                         list workflows (no model)
 *   /workflow-export <slug> <dest> [--keep-state] copy a workflow elsewhere (no model)
 *   /workflow-refresh <slug | --all>              update workflows to this skill version (no model)
 *
 * Tools for the model
 *   workflow_scaffold  create a workflow (only after /workflow-new in this session)
 *   workflow_check     validate a workflow's plan and compile-check its waves
 *
 * Changing a workflow happens inside it: cd workflows/<slug>, pi, /workflow-revise.
 *
 * The logic lives in the skill's Python scripts (.pi/skills/workflow/scripts);
 * this file runs them and formats their JSON.
 */
import { defineTool, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import * as fs from "node:fs";
import * as path from "node:path";

const WORKFLOWS = "workflows";

// --- prompts sent to the model ------------------------------------------------

const NEW_PROMPT = (request: string) => `Plan a new workflow for the request below. Follow
\`.pi/skills/workflow/SKILL.md\` from step 1; workflows go under
\`workflows/\`. Use the \`workflow_scaffold\` tool to create the workflow and
\`workflow_check\` to validate and compile-check it.

Request (copy it verbatim into the plan): ${request}`;

// --- helpers --------------------------------------------------------------------

/** Prometheus's root: the folder whose .pi/ holds APPEND_SYSTEM.md, found from `start` upwards. */
function findRoot(start: string): string | undefined {
	let dir = path.resolve(start);
	for (;;) {
		if (fs.existsSync(path.join(dir, ".pi", "APPEND_SYSTEM.md")) && fs.existsSync(path.join(dir, WORKFLOWS))) return dir;
		const parent = path.dirname(dir);
		if (parent === dir) return undefined;
		dir = parent;
	}
}

/** Python for the skill scripts: $PI_WORKFLOW_PYTHON, else the nearest .venv (see pyproject.toml), else python3. */
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

async function runScript(
	pi: ExtensionAPI,
	root: string,
	script: string,
	args: string[],
	signal?: AbortSignal,
): Promise<ScriptResult> {
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

/** Split "first rest of the line" into [first, rest]. */
function firstWord(args: string): [string, string] {
	const t = args.trim();
	const i = t.search(/\s/);
	return i === -1 ? [t, ""] : [t.slice(0, i), t.slice(i).trim()];
}

function table(rows: string[][]): string {
	const widths = rows[0].map((_, i) => Math.max(...rows.map((r) => (r[i] ?? "").length)));
	return rows.map((r) => r.map((c, i) => (c ?? "").padEnd(widths[i])).join("  ").trimEnd()).join("\n");
}

function slugs(root: string, prefix = ""): string[] {
	try {
		return fs
			.readdirSync(path.join(root, WORKFLOWS))
			.filter((d) => d.startsWith(prefix) && fs.existsSync(path.join(root, WORKFLOWS, d, ".pi", "plan.yaml")))
			.sort();
	} catch {
		return [];
	}
}

async function check(pi: ExtensionAPI, root: string, slug: string, signal?: AbortSignal) {
	const workflow = path.join(root, WORKFLOWS, slug);
	const v = await runScript(pi, root, "validate_plan.py", [path.join(workflow, ".pi", "plan.yaml")], signal);
	const c = await runScript(pi, root, "compile_wave.py", ["--workflow", workflow, "--check"], signal);
	return {
		valid: v.data?.valid ?? false,
		status: v.data?.status,
		errors: v.data?.errors ?? [v.stderr.trim()],
		warnings: v.data?.warnings ?? [],
		summary: v.data?.summary,
		compiles: c.data?.ok ?? false,
		compile_errors: c.data?.compile_errors ?? (c.data ? {} : { all: [c.stderr.trim()] }),
	};
}

// --- extension --------------------------------------------------------------------

export default function prometheus(pi: ExtensionAPI) {
	// Completion callbacks get no context, so remember the session's cwd.
	let sessionCwd = process.cwd();
	// Set by /workflow-new, used up by the scaffold it leads to: a plain message never creates a workflow.
	let newRequested = false;
	pi.on("session_start", async (_event, ctx) => {
		sessionCwd = ctx.cwd;
		newRequested = false;
		const root = findRoot(ctx.cwd);
		if (!root) return;
		const py = pythonFor(root);
		const r = await pi.exec(py, ["-c", "import dotenv, pydantic, pydantic_settings, ruamel.yaml, typer"], { cwd: root, timeout: 10_000 });
		if (r.code !== 0) ctx.ui.notify(`The skill scripts' Python packages are missing for ${py}. Run \`uv sync\` in ${root}, then restart pi.`, "warning");
	});

	const completeSlug = (prefix: string) => {
		const root = findRoot(sessionCwd);
		const found = root ? slugs(root, prefix) : [];
		return found.length ? found.map((s) => ({ value: s, label: s })) : null;
	};

	/** Show text in the conversation without starting a model turn. */
	const show = (title: string, body: string) =>
		pi.sendMessage({ customType: "workflow", content: `${title}\n\n${body}`, display: true }, { triggerTurn: false });

	const rootOrWarn = (ctx: ExtensionContext) => {
		const root = findRoot(ctx.cwd);
		if (!root) ctx.ui.notify("Start pi inside the Prometheus folder to use the workflow commands.", "error");
		return root;
	};

	const ask = (ctx: ExtensionContext, text: string) => {
		if (!ctx.isIdle()) {
			ctx.ui.notify("The agent is busy; wait for it to finish, then run the command again.", "warning");
			return;
		}
		pi.sendUserMessage(text);
	};

	// the scaffold script is reachable only through workflow_scaffold, which checks for /workflow-new
	pi.on("tool_call", (event, ctx) => {
		if (!findRoot(ctx.cwd)) return;
		const command = (event.input as { command?: string }).command ?? "";
		if ((event.toolName === "bash" || event.toolName === "powershell") && /scaffold_workflow\.py/.test(command))
			return { block: true, reason: "Create workflows with workflow_scaffold, and only after the user ran /workflow-new." };
	});

	// --- commands ---------------------------------------------------------------
	pi.registerCommand("workflow-new", {
		description: "Plan a new workflow from a request",
		handler: async (args, ctx) => {
			if (!rootOrWarn(ctx)) return;
			if (!args.trim()) {
				ctx.ui.notify("Usage: /workflow-new <what you want done>", "warning");
				return;
			}
			newRequested = true;
			activateSubagent(pi); // probes and the critic
			ask(ctx, NEW_PROMPT(args.trim()));
		},
	});

	pi.registerCommand("workflow-list", {
		description: "List workflows and where each one stands: /workflow-list [slug]",
		getArgumentCompletions: completeSlug,
		handler: async (args, ctx) => {
			const root = rootOrWarn(ctx);
			if (!root) return;
			const [slug] = firstWord(args);
			if (slug) {
				const res = await check(pi, root, slug);
				const s = res.summary ?? {};
				show(
					`Workflow ${slug}`,
					[
						`status: ${res.status}   revision: ${s.revision ?? "?"}   active run: ${s.run?.id ?? "none"}   next wave: ${s.run ? (s.next_wave ?? "none") : "-"}`,
						`waves to go${s.run ? "" : " in a new run"}: ${(s.waves ?? []).map((w: any) => `${w.id}:[${w.stages.join(",")}]${w.ends_with === "none" ? "" : ` then ${w.ends_with}`}`).join("  ") || "none"}`,
						`provisional: ${(s.provisional ?? []).join(", ") || "none"}`,
						`open questions: ${s.open_questions ?? 0}   approvals: ${(s.approvals ?? []).join(", ") || "none"}`,
						`missing params: ${(s.workflow?.missing_params ?? []).join(", ") || "none"}   secrets: ${(s.workflow?.missing_secrets ?? []).join(", ") || "none"}`,
						`plan valid: ${res.valid}   compiles: ${res.compiles}`,
						...res.errors.map((e: string) => `  error: ${e}`),
						...Object.entries(res.compile_errors as Record<string, string[]>).flatMap(([w, es]) =>
							es.map((e) => `  compile ${w}: ${e}`),
						),
					].join("\n"),
				);
				return;
			}
			const res = await runScript(pi, root, "list_workflows.py", ["--root", WORKFLOWS]);
			const list: any[] = Array.isArray(res.data) ? res.data : [];
			if (list.length === 0) {
				show("Workflows", "No workflows yet. Create one with /workflow-new <request>.");
				return;
			}
			const rows = [["slug", "status", "rev", "runs", "active run", "next", "open q", "last run"]];
			for (const b of list) {
				rows.push([
					b.slug,
					String(b.status ?? "?"),
					String(b.revision ?? ""),
					String(b.runs ?? 0),
					b.active_run ? `${b.active_run} (${b.stages_done ?? 0}/${b.stages_total ?? 0} stages)` : "-",
					String(b.next_wave ?? "-"),
					String((b.open_questions ?? []).length),
					b.last_run ? `${b.last_run.id} ${b.last_run.status}` : "-",
				]);
			}
			show("Workflows", table(rows));
		},
	});

	pi.registerCommand("workflow-export", {
		description: "Copy a workflow elsewhere: /workflow-export <slug> <dest> [--keep-state]",
		getArgumentCompletions: completeSlug,
		handler: async (args, ctx) => {
			const root = rootOrWarn(ctx);
			if (!root) return;
			const parts = args.trim().split(/\s+/).filter(Boolean);
			const keep = parts.includes("--keep-state");
			const [slug, dest] = parts.filter((p) => p !== "--keep-state");
			if (!slug || !dest) {
				ctx.ui.notify("Usage: /workflow-export <slug> <destination folder> [--keep-state]", "warning");
				return;
			}
			const res = await runScript(pi, root, "export_workflow.py", [path.join(WORKFLOWS, slug), dest, ...(keep ? ["--keep-state"] : [])]);
			const d = res.data ?? {};
			if (!d.ok) {
				show(`Export ${slug} failed`, d.error ?? res.stderr.trim());
				return;
			}
			show(
				`Exported ${slug} (${d.mode})`,
				[
					`to: ${d.workflow}`,
					`plan valid there: ${d.valid}${d.errors?.length ? ` — ${d.errors.join("; ")}` : ""}`,
					`params to set: ${(d.missing_params ?? []).join(", ") || "none"}   secrets to add to .env: ${(d.missing_secrets ?? []).join(", ") || "none"}`,
					`start: ${d.start}`,
				].join("\n"),
			);
		},
	});

	pi.registerCommand("workflow-refresh", {
		description: "Update workflows to Prometheus's skill version: /workflow-refresh <slug | --all>",
		getArgumentCompletions: (prefix) => {
			const found = completeSlug(prefix) ?? [];
			const all = "--all".startsWith(prefix) ? [{ value: "--all", label: "--all" }] : [];
			return all.length || found.length ? [...all, ...found] : null;
		},
		handler: async (args, ctx) => {
			const root = rootOrWarn(ctx);
			if (!root) return;
			const [target] = firstWord(args);
			if (!target) {
				ctx.ui.notify("Usage: /workflow-refresh <slug | --all>", "warning");
				return;
			}
			const targets = target === "--all" ? slugs(root) : [target];
			const rows = [["slug", "refreshed", "plan valid", "compiles", "first error"]];
			for (const slug of targets) {
				const res = await runScript(pi, root, "scaffold_workflow.py", [slug, "--root", WORKFLOWS, "--refresh"]);
				const c = await check(pi, root, slug);
				const firstErr = c.errors[0] ?? Object.values(c.compile_errors as Record<string, string[]>).flat()[0] ?? "";
				rows.push([slug, String(res.data?.ok ?? false), String(c.valid), String(c.compiles), firstErr.slice(0, 80)]);
			}
			show("Refreshed workflows", table(rows));
		},
	});

	// --- tools ------------------------------------------------------------------
	pi.registerTool(
		defineTool({
			name: "workflow_scaffold",
			label: "Create workflow",
			description:
				"Create a new workflow at workflows/<slug>/ with the plan skeleton, agents, commands and a copy of the planning skill. Only after the user ran /workflow-new.",
			promptSnippet: "Create a workflow",
			promptGuidelines: ["Use workflow_scaffold to create a workflow instead of running scaffold_workflow.py yourself."],
			parameters: Type.Object({
				slug: Type.String({ description: "short lowercase slug: letters, digits and dashes" }),
				request: Type.String({ description: "the user's request, verbatim" }),
			}),
			async execute(_id, params, signal, _onUpdate, ctx) {
				const root = findRoot(ctx.cwd);
				if (!root) throw new Error("not inside the Prometheus folder");
				if (!newRequested)
					throw new Error(
						"workflows are created only through /workflow-new. Do not plan this request; answer it normally, and if it is multi-step work, tell the user they can run `/workflow-new <request>`.",
					);
				const res = await runScript(pi, root, "scaffold_workflow.py", [params.slug, "--root", WORKFLOWS, "--request", params.request], signal);
				if (res.data === undefined) throw new Error(`scaffold_workflow.py failed: ${(res.stderr || res.stdout).trim().slice(0, 2000)}`);
				if (res.data.ok) newRequested = false;
				return { content: [{ type: "text" as const, text: JSON.stringify(res.data, null, 2) }], details: res.data };
			},
		}),
	);

	pi.registerTool(
		defineTool({
			name: "workflow_check",
			label: "Check workflow",
			description:
				"Validate workflows/<slug>/.pi/plan.yaml and compile-check every concrete pending wave. Returns errors, warnings and a summary.",
			promptSnippet: "Validate and compile-check a workflow plan",
			promptGuidelines: [
				"Use workflow_check after every change to a workflow plan, instead of running validate_plan.py or compile_wave.py yourself.",
			],
			parameters: Type.Object({ slug: Type.String({ description: "the workflow's slug" }) }),
			async execute(_id, params, signal, _onUpdate, ctx) {
				const root = findRoot(ctx.cwd);
				if (!root) throw new Error("not inside the Prometheus folder");
				const res = await check(pi, root, params.slug, signal);
				return { content: [{ type: "text" as const, text: JSON.stringify(res, null, 2) }], details: res };
			},
		}),
	);
}
