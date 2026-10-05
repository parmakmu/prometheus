/**
 * A conversation as the page shows it, built from Pi's session entries: the worker builds it from its live session,
 * and the router from the session file while the worker is still starting (Pi takes ~2 s to load; the file reads in
 * milliseconds), so the page shows the conversation at once. No Pi import here: that is what takes the time.
 */
import { createHash } from "node:crypto";
import { closeSync, openSync, readdirSync, readFileSync, readSync, statSync } from "node:fs";
import path from "node:path";
import { thoughtOf } from "./thoughts.ts";

export const textOf = (content: unknown): string =>
	typeof content === "string"
		? content
		: Array.isArray(content)
			? content.map((p: any) => (p?.type === "text" ? p.text : "")).join("")
			: "";

export const hashOf = (text: string) => createHash("sha256").update(text.trim()).digest("hex").slice(0, 16);

// The worker records which command each prompt came from (a custom entry the model never sees), so the history shows
// "/agent-run" as you typed it: not the prompt, and not as two versions of one message.
export const COMMAND_ENTRY = "prometheus-ui-command";

const shownCommands = (entries: any[]) =>
	new Map<string, { id: string; command: string }>(
		entries
			.filter((e) => e.type === "custom" && e.customType === COMMAND_ENTRY && e.data?.before)
			.map((e) => [e.data.before, { id: e.id, command: e.data.command }]),
	);
export const commandsByPrompt = (entries: any[]) =>
	new Map<string, string | null>(
		entries
			.filter((e) => e.type === "custom" && e.customType === COMMAND_ENTRY && e.data?.prompt)
			.map((e) => [e.data.prompt, e.data.hidden ? null : e.data.command]),
	);

// The messages of the current branch, root to leaf.
export const branchMessages = (branch: any[]) =>
	branch
		.filter((e) => e.type === "message" || e.type === "custom_message")
		.map((e) => ({ id: e.id, m: e.type === "message" ? e.message : { role: "custom", content: e.content, display: e.display } }));

// The conversation so far as AG-UI messages: the current branch of the session's tree. Ids are "pi-<entry id>": a
// run whose last user message has such an id brings nothing new, and an edit names the entry it rewinds to. An
// assistant message's thinking comes first, as a reasoning message, so the page can show it in the turn's Details.
export function snapshotOf(entries: any[], branch: any[]) {
	const out: Record<string, unknown>[] = [];
	const commands = commandsByPrompt(entries);
	const shown = shownCommands(entries);
	for (const { id: entry, m } of branchMessages(branch)) {
		const id = `pi-${entry}`;
		// a pi- id: the page sending it back is a sync, never the command again
		const command = shown.get(entry);
		if (command) out.push({ id: `pi-${command.id}`, role: "user", content: command.command });
		if (m.role === "user") {
			const hash = hashOf(textOf(m.content));
			if (commands.get(hash) !== null) out.push({ id, role: "user", content: commands.get(hash) ?? textOf(m.content) }); // null: shown above
		} else if (m.role === "assistant") {
			const parts: any[] = Array.isArray(m.content) ? m.content : [];
			const thinking = [...new Set(parts.filter((p) => p.type === "thinking").map(thoughtOf))].join("\n\n");
			if (thinking) out.push({ id: `${id}-thinking`, role: "reasoning", content: thinking });
			const toolCalls = parts
				.filter((p) => p.type === "toolCall")
				.map((p) => ({ id: p.id, type: "function", function: { name: p.name, arguments: JSON.stringify(p.arguments ?? {}) } }));
			out.push({ id, role: "assistant", content: textOf(parts), ...(toolCalls.length ? { toolCalls } : {}) });
		} else if (m.role === "toolResult") {
			const limit = String(m.toolName).startsWith("agent_") ? 20000 : 4000;
			out.push({ id, role: "tool", toolCallId: m.toolCallId, content: textOf(m.content).slice(0, limit) || "done" });
		} else if (m.role === "custom" && m.display !== false && textOf(m.content)) out.push({ id, role: "assistant", content: textOf(m.content) });
	}
	return out;
}

// --- from the file, without Pi ---------------------------------------------------------------------------------

// A session file's first line, its header: read alone (64 KiB at most), since the latest conversation is found by
// opening the files newest first, and a long one runs to megabytes.
const header = (file: string): any => {
	let fd: number | undefined;
	try {
		fd = openSync(file, "r");
		const buf = Buffer.alloc(64 * 1024);
		const n = readSync(fd, buf, 0, buf.length, 0);
		return JSON.parse(buf.toString("utf8", 0, n).split("\n", 1)[0]);
	} catch {
		return null;
	} finally {
		if (fd !== undefined) closeSync(fd);
	}
};

// The session file the worker would open (worker.ts openSession): `session` by its id; none, the folder's latest for
// this cwd (Pi's findMostRecentSession: newest file first, its header's cwd matching); "new-…", none yet.
export function sessionFile(sessionDir: string, cwd: string, session: string): string | null {
	if (session.startsWith("new-")) return null;
	let files: { file: string; mtime: number }[];
	try {
		files = readdirSync(sessionDir)
			.filter((f) => f.endsWith(".jsonl"))
			.map((f) => ({ file: path.join(sessionDir, f), mtime: statSync(path.join(sessionDir, f)).mtimeMs }))
			.sort((a, b) => b.mtime - a.mtime);
	} catch {
		return null;
	}
	for (const { file } of files) {
		const h = header(file);
		if (h?.type === "session" && (session ? h.id === session : path.resolve(h.cwd ?? "") === path.resolve(cwd))) return file;
	}
	return null;
}

// The snapshot of a session file, as Pi reads it (SessionManager): entries in order, the current leaf the last one,
// the branch its parentId chain. Null when there is no file or it can't be read.
export function snapshotFromFile(file: string | null) {
	if (!file) return null;
	try {
		const entries = readFileSync(file, "utf8")
			.split("\n")
			.filter((l) => l.trim())
			.map((l) => JSON.parse(l))
			.filter((e) => e.type !== "session");
		const byId = new Map(entries.map((e) => [e.id, e]));
		const branch: any[] = [];
		for (let e = entries.at(-1); e; e = e.parentId ? byId.get(e.parentId) : undefined) branch.push(e);
		return snapshotOf(entries, branch.reverse());
	} catch {
		return null;
	}
}
