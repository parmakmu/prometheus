/**
 * Prometheus: commands and tools for creating agents.
 *
 * Commands
 *   /agent-new <request>                       plan a new agent (model)
 *   /agent-list [slug]                         list agents (no model)
 *   /agent-export <slug> <dest> [--keep-state] copy an agent elsewhere (no model)
 *   /agent-refresh <slug | --all>              update agents to this skill version (no model)
 *
 * Tools for the model
 *   agent_scaffold  create an agent (only after /agent-new in this session)
 *   agent_check     validate an agent's plan and compile-check its waves
 *
 * Changing an agent happens inside it: cd agents/<slug>, pi, /agent-revise.
 *
 * The logic lives in the skill's Python scripts (.pi/skills/agent/scripts);
 * this file runs them and formats their JSON.
 */
import { defineTool, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import * as fs from "node:fs";
import * as path from "node:path";

const AGENTS = "agents";

// --- prompts sent to the model ------------------------------------------------

// Says it comes from /agent-new: the role tells the model to refuse plain planning requests, and
// a model that can't tell this message from a typed one refused it (it looks the same in the session).
const NEW_PROMPT = (request: string) => `The user ran \`/agent-new\`: this is the planning request it authorizes, not a
plain message. Plan a new agent for the request below. Follow
\`.pi/skills/agent/SKILL.md\` from step 1; agents go under
\`agents/\`. Use the \`agent_scaffold\` tool to create the agent and
\`agent_check\` to validate and compile-check it. The subagent tool is already
active: do not call subagents_enable.

Request (copy it verbatim into the plan): ${request}`;

const FROM_PROMPT = (from: string) => `

Start from the agent "${from}": call \`agent_scaffold\` with \`from: "${from}"\`. It copies that agent's plan
as a draft (with this request), its gates, stage programs, subagents and packages. Keep what fits this
request, change the rest, and plan only what is new; the critic still reviews the whole plan.`;

// --- helpers --------------------------------------------------------------------

/** Prometheus's root: the folder whose .pi/ holds APPEND_SYSTEM.md, found from `start` upwards. */
function findRoot(start: string): string | undefined {
	let dir = path.resolve(start);
	for (;;) {
		if (fs.existsSync(path.join(dir, ".pi", "APPEND_SYSTEM.md")) && fs.existsSync(path.join(dir, AGENTS))) return dir;
		const parent = path.dirname(dir);
		if (parent === dir) return undefined;
		dir = parent;
	}
}

/** Python for the skill scripts: $PI_AGENT_PYTHON, else the nearest .venv (see pyproject.toml), else python3. */
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

/** Split "first rest of the line" into [first, rest]. */
function firstWord(args: string): [string, string] {
	const t = args.trim();
	const i = t.search(/\s/);
	return i === -1 ? [t, ""] : [t.slice(0, i), t.slice(i).trim()];
}

// a markdown table (the first row its header): Pi's terminal and the web app both draw it as a table
function table(rows: string[][]): string {
	const line = (r: string[]) => `| ${r.map((c) => (c ?? "").replace(/\|/g, "\\|")).join(" | ")} |`;
	return [line(rows[0]), line(rows[0].map(() => "---")), ...rows.slice(1).map(line)].join("\n");
}

function slugs(root: string, prefix = ""): string[] {
	try {
		return fs
			.readdirSync(path.join(root, AGENTS))
			.filter((d) => d.startsWith(prefix) && fs.existsSync(path.join(root, AGENTS, d, "plan.yaml")))
			.sort();
	} catch {
		return [];
	}
}

async function check(pi: ExtensionAPI, root: string, slug: string, signal?: AbortSignal) {
	const agent = path.join(root, AGENTS, slug);
	const v = await runScript(pi, root, "validate_plan.py", [path.join(agent, "plan.yaml")], signal);
	const c = await runScript(pi, root, "compile_wave.py", ["--agent", agent, "--check"], signal);
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
	// Set by /agent-new, used up by the scaffold it leads to: a plain message never creates an agent.
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
		pi.sendMessage({ customType: "agent", content: `${title}\n\n${body}`, display: true }, { triggerTurn: false });

	const rootOrWarn = (ctx: ExtensionContext) => {
		const root = findRoot(ctx.cwd);
		if (!root) ctx.ui.notify("Start pi inside the prometheus folder to use the agent commands.", "error");
		return root;
	};

	const ask = (ctx: ExtensionContext, text: string) => {
		if (!ctx.isIdle()) {
			ctx.ui.notify("The agent is busy; wait for it to finish, then run the command again.", "warning");
			return;
		}
		pi.sendUserMessage(text);
	};

	// the scaffold script is reachable only through agent_scaffold, which checks for /agent-new
	pi.on("tool_call", (event, ctx) => {
		if (!findRoot(ctx.cwd)) return;
		const command = (event.input as { command?: string }).command ?? "";
		if ((event.toolName === "bash" || event.toolName === "powershell") && /scaffold_agent\.py/.test(command))
			return { block: true, reason: "Create agents with agent_scaffold, and only after the user ran /agent-new." };
		const shell = event.toolName === "bash" || event.toolName === "powershell";
		const path_ = (event.input as { path?: string }).path ?? "";
		// any route to a new package: install commands, uvx / uv tool / uv run --with, or writing the package list itself
		const packageFile =
			((event.toolName === "write" || event.toolName === "edit") && /(^|[\\/])(pyproject\.toml|uv\.lock)$/.test(path_)) ||
			(shell && /(pyproject\.toml|uv\.lock)/.test(command) && /\bsed\s+-i|>|\btee\b|\bmv\b|\bcp\b|\bperl\s+-\w*i|\btruncate\b|\bpython3?\b|\bnode\b/.test(command));
		if ((shell && /\buv\s+(?:pip\s+)?(?:add|install)\b|\bpip3?\s+install\b|\buvx\b|\buv\s+tool\s+(?:install|run)\b|\buv\s+run\b[^\n;&|]*\s--with\b|add_package\.py/.test(command)) || packageFile)
			return { block: true, reason: "Python packages are added with agent_add_package (the packages and why they are needed), which asks the user to approve them. Do not run uv add, pip install, uvx or uv run --with, edit pyproject.toml or uv.lock, or run add_package.py yourself. Name the agent (slug) when you call it." };
	});

	// --- commands ---------------------------------------------------------------
	pi.registerCommand("agent-new", {
		description: "Plan a new agent from a request: /agent-new [--from <agent>] <what you want done>",
		getArgumentCompletions: (prefix: string) => (prefix.startsWith("--from ") ? completeSlug(prefix.slice(7)) : null),
		handler: async (args, ctx) => {
			if (!rootOrWarn(ctx)) return;
			const from = /^--from\s+(\S+)\s*/.exec(args.trim());
			const request = from ? args.trim().slice(from[0].length) : args.trim();
			if (!request) {
				ctx.ui.notify("Usage: /agent-new [--from <agent>] <what you want done>", "warning");
				return;
			}
			newRequested = true;
			activateSubagent(pi); // probes and the critic
			ask(ctx, NEW_PROMPT(request) + (from ? FROM_PROMPT(from[1]) : ""));
		},
	});

	pi.registerCommand("agent-list", {
		description: "List agents and where each one stands: /agent-list [slug]",
		getArgumentCompletions: completeSlug,
		handler: async (args, ctx) => {
			const root = rootOrWarn(ctx);
			if (!root) return;
			const [slug] = firstWord(args);
			if (slug) {
				const res = await check(pi, root, slug);
				const s = res.summary ?? {};
				// a markdown list: one line each, in the terminal and the web app alike
				show(
					`Agent ${slug}`,
					[
						`- **Status:** ${res.status}, revision ${s.revision ?? "?"}`,
						`- **Active run:** ${s.run?.id ?? "none"}${s.run ? `, next wave ${s.next_wave ?? "none"}` : ""}`,
						`- **Waves to go${s.run ? "" : " in a new run"}:** ${(s.waves ?? []).map((w: any) => `${w.id} (${w.stages.join(", ")}${w.ends_with === "none" ? "" : `, then ${w.ends_with}`})`).join("; ") || "none"}`,
						`- **Provisional stages:** ${(s.provisional ?? []).join(", ") || "none"}`,
						`- **Open questions:** ${s.open_questions ?? 0}; **approvals:** ${(s.approvals ?? []).join(", ") || "none"}`,
						`- **Missing params:** ${(s.agent?.missing_params ?? []).join(", ") || "none"}; **secrets:** ${(s.agent?.missing_secrets ?? []).join(", ") || "none"}`,
						`- **Plan valid:** ${res.valid ? "yes" : "no"}; **compiles:** ${res.compiles ? "yes" : "no"}`,
						...res.errors.map((e: string) => `- Error: ${e}`),
						...Object.entries(res.compile_errors as Record<string, string[]>).flatMap(([w, es]) => es.map((e) => `- Compile ${w}: ${e}`)),
					].join("\n"),
				);
				return;
			}
			const res = await runScript(pi, root, "list_agents.py", ["--root", AGENTS]);
			const list: any[] = Array.isArray(res.data) ? res.data : [];
			if (list.length === 0) {
				show("Agents", "No agents yet. Create one with /agent-new <request>.");
				return;
			}
			// five short columns: the state says what is under way and what waits; the rest only when it matters
			const rows = [["Agent", "State", "Runs", "Last run", "Skill"]];
			for (const b of list) {
				const open = (b.open_questions ?? []).length;
				rows.push([
					b.slug,
					[
						`${b.status ?? "?"} (r${b.revision ?? "?"})`,
						b.active_run ? `running ${b.active_run}, ${b.stages_done ?? 0}/${b.stages_total ?? 0} stages` : "",
						open ? `${open} open question${open === 1 ? "" : "s"}` : "",
					].filter(Boolean).join("; "),
					String(b.runs ?? 0),
					b.last_run ? `${b.last_run.id} ${b.last_run.status}` : "-",
					!b.prometheus_version ? "unknown" : b.stale ? "update: /agent-refresh" : "current",
				]);
			}
			show("Agents", table(rows));
		},
	});

	pi.registerCommand("agent-export", {
		description: "Copy an agent elsewhere: /agent-export <slug> <dest> [--keep-state]",
		getArgumentCompletions: completeSlug,
		handler: async (args, ctx) => {
			const root = rootOrWarn(ctx);
			if (!root) return;
			const parts = args.trim().split(/\s+/).filter(Boolean);
			const keep = parts.includes("--keep-state");
			const [slug, dest] = parts.filter((p) => p !== "--keep-state");
			if (!slug || !dest) {
				ctx.ui.notify("Usage: /agent-export <slug> <destination folder> [--keep-state]", "warning");
				return;
			}
			const res = await runScript(pi, root, "export_agent.py", [path.join(AGENTS, slug), dest, ...(keep ? ["--keep-state"] : [])]);
			const d = res.data ?? {};
			if (!d.ok) {
				show(`Export ${slug} failed`, d.error ?? res.stderr.trim());
				return;
			}
			show(
				`Exported ${slug} (${d.mode})`,
				[
					`to: ${d.agent}`,
					`plan valid there: ${d.valid}${d.errors?.length ? ` — ${d.errors.join("; ")}` : ""}`,
					`params to set: ${(d.missing_params ?? []).join(", ") || "none"}   secrets to add to .env: ${(d.missing_secrets ?? []).join(", ") || "none"}`,
					`start: ${d.start}`,
				].join("\n"),
			);
		},
	});

	pi.registerCommand("agent-refresh", {
		description: "Update agents to prometheus's skill version: /agent-refresh <slug | --all>",
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
				ctx.ui.notify("Usage: /agent-refresh <slug | --all>", "warning");
				return;
			}
			const targets = target === "--all" ? slugs(root) : [target];
			const rows = [["slug", "refreshed", "version", "plan valid", "compiles", "first error"]];
			for (const slug of targets) {
				// --all leaves an agent already at this Prometheus's version alone; a named one is always refreshed
				const res = await runScript(pi, root, "scaffold_agent.py", [slug, "--root", AGENTS, "--refresh", ...(target === "--all" ? ["--if-stale"] : [])]);
				const c = await check(pi, root, slug);
				const firstErr = c.errors[0] ?? Object.values(c.compile_errors as Record<string, string[]>).flat()[0] ?? "";
				rows.push([slug, res.data?.skipped ? "skipped (current)" : String(res.data?.ok ?? false), String(res.data?.prometheus_version ?? ""), String(c.valid), String(c.compiles), firstErr.slice(0, 80)]);
			}
			show("Refreshed agents", table(rows));
		},
	});

	// --- tools ------------------------------------------------------------------
	pi.registerTool(
		defineTool({
			name: "agent_scaffold",
			label: "Create agent",
			description:
				"Create a new agent at agents/<slug>/ with the plan skeleton, agents, commands and a copy of the planning skill. Only after the user ran /agent-new.",
			promptSnippet: "Create an agent",
			promptGuidelines: ["Use agent_scaffold to create an agent instead of running scaffold_agent.py yourself."],
			parameters: Type.Object({
				slug: Type.String({ description: "short lowercase slug: letters, digits and dashes" }),
				request: Type.String({ description: "the user's request, verbatim" }),
				from: Type.Optional(Type.String({ description: "only when /agent-new named one with --from: the agent to start from" })),
			}),
			async execute(_id, params, signal, _onUpdate, ctx) {
				const root = findRoot(ctx.cwd);
				if (!root) throw new Error("not inside the prometheus folder");
				if (!newRequested)
					throw new Error(
						"agents are created only through /agent-new. Do not plan this request; answer it normally, and if it is multi-step work, tell the user they can run `/agent-new <request>`.",
					);
				const res = await runScript(pi, root, "scaffold_agent.py", [params.slug, "--root", AGENTS, "--request", params.request, ...(params.from ? ["--from", params.from] : [])], signal);
				if (res.data === undefined) throw new Error(`scaffold_agent.py failed: ${(res.stderr || res.stdout).trim().slice(0, 2000)}`);
				if (res.data.ok) newRequested = false;
				return { content: [{ type: "text" as const, text: JSON.stringify(res.data) }], details: res.data };
			},
		}),
	);

	pi.registerTool(
		defineTool({
			name: "agent_check",
			label: "Check agent",
			description:
				"Validate agents/<slug>/plan.yaml and compile-check every concrete pending wave. Returns errors, warnings and a summary.",
			promptSnippet: "Validate and compile-check an agent plan",
			promptGuidelines: [
				"Use agent_check after every change to an agent plan, instead of running validate_plan.py or compile_wave.py yourself.",
			],
			parameters: Type.Object({ slug: Type.String({ description: "the agent's slug" }) }),
			async execute(_id, params, signal, _onUpdate, ctx) {
				const root = findRoot(ctx.cwd);
				if (!root) throw new Error("not inside the prometheus folder");
				const res = await check(pi, root, params.slug, signal);
				return { content: [{ type: "text" as const, text: JSON.stringify(res) }], details: res };
			},
		}),
	);

	pi.registerTool(
		defineTool({
			name: "agent_add_package",
			label: "Add package",
			description:
				"Add Python packages to agents/<slug>/pyproject.toml (uv add). The user is shown the packages and your reason and must approve first: say plainly why the agent needs them, in terms of what they do for the user's work.",
			promptSnippet: "Add a Python package to an agent, after the user approves it",
			promptGuidelines: ["To add a Python package to an agent use agent_add_package with the reason; never uv add or pip install. If the user declines, work without it or ask what to do instead."],
			parameters: Type.Object({
				slug: Type.String({ description: "the agent's slug" }),
				packages: Type.Array(Type.String(), { description: "requirements as uv add takes them: openpyxl, pandas>=2, httpx[http2]" }),
				reason: Type.String({ description: "why the agent needs them, in a sentence the user can judge" }),
			}),
			async execute(_id, params, signal, _onUpdate, ctx) {
				const root = findRoot(ctx.cwd);
				if (!root) throw new Error("not inside the prometheus folder");
				if (!/^[a-z0-9][a-z0-9-]*$/.test(params.slug)) throw new Error("bad slug");
				if (!ctx.hasUI) throw new Error("adding a package needs the user's approval and no one can be asked here: do not add it");
				if (!params.packages.length || !params.reason.trim()) throw new Error("name the packages and say why they are needed");
				const yes = await ctx.ui.confirm("Add a Python package?", `${params.packages.join(", ")} (agent ${params.slug})\n\nWhy: ${params.reason.trim()}`);
				if (!yes) return { content: [{ type: "text" as const, text: "The user declined. Do not add it: work without it, or ask the user what to do instead." }], details: { declined: true } };
				const res = await runScript(pi, root, "add_package.py", [...params.packages, "--reason", params.reason, "--agent", path.join(root, AGENTS, params.slug)], signal);
				if (res.data === undefined) throw new Error(`add_package.py failed: ${(res.stderr || res.stdout).trim().slice(0, 2000)}`);
				return { content: [{ type: "text" as const, text: JSON.stringify(res.data) }], details: res.data };
			},
		}),
	);
}
