/**
 * The router: the one HTTP server the browser talks to. It checks who is calling, finds that user's
 * folder, and forwards to the worker (worker.ts) for the agent asked for, starting it on first use and
 * stopping it when idle. Each worker is its own process: one Pi session, one folder, one `.env`.
 *
 *   GET  /me                     who you are
 *   GET  /workflows              your workflows (list_workflows.py; no model involved)
 *   *    /p/<agent path>         prometheus, your planner         → worker in <your prometheus>
 *   *    /w/<slug>/<agent path>  a workflow                       → worker in <your prometheus>/workflows/<slug>
 *
 * Who you are: with TRUST_USER_HEADER set (e.g. x-forwarded-user, from oauth2-proxy in front), that
 * header; listen on loopback then, so only the proxy reaches the router. Without it (development), you
 * are DEV_USER and must send the token printed at start, as in Jupyter. Either way only the web app's
 * origin and the allowed host names are served.
 *
 * Your folder: USERS_ROOT/<user>/prometheus, your own copy. Without USERS_ROOT every user is this
 * repository (one person on their own machine).
 *
 * Sandboxes (bubblewrap): each worker sees only what it needs, at the same paths as outside.
 *   prometheus: your copy read-only, its workflows/ read-write
 *   a workflow: that folder read-write; prometheus's .venv read-only; nothing of other workflows
 *   both:       system and node read-only, your Pi state (sessions, settings, auth), your files
 *               read-only (USERS_ROOT/<user>/files, or SANDBOX_READ paths), a home of its own
 * SANDBOX=off runs workers unsandboxed (development only). With a systemd user session, each worker
 * also gets MEMORY_MAX and CPU_QUOTA limits.
 *
 *   node ui/router.ts sandbox-run <user> <p | w/slug> -- <command…>   runs a command in that sandbox
 */
import { spawn, execFile, execFileSync, type ChildProcess } from "node:child_process";
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { createReadStream, createWriteStream, existsSync, mkdirSync, mkdtempSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import type { Writable } from "node:stream";
import { createServer, request, type IncomingMessage, type ServerResponse } from "node:http";
import { homedir, tmpdir, userInfo } from "node:os";
import path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";

const REPO = path.resolve(import.meta.dirname, "..");
const PORT = Number(process.env.PORT ?? 8787);
// local only by default. Under WSL a Windows browser may need HOST=0.0.0.0
const HOST = process.env.HOST ?? "127.0.0.1";
const ORIGINS = (process.env.UI_ORIGIN ?? "http://localhost:3000,http://127.0.0.1:3000").split(",");
const HOSTS = (process.env.ALLOWED_HOSTS ?? "localhost,127.0.0.1,[::1]").split(",");
const USER_HEADER = process.env.TRUST_USER_HEADER?.toLowerCase();
const DEV_USER = process.env.DEV_USER ?? userInfo().username;
const TOKEN = process.env.PROMETHEUS_TOKEN || randomBytes(24).toString("base64url");
const USERS_ROOT = process.env.USERS_ROOT;
const IDLE_MS = Number(process.env.IDLE_MINUTES ?? 15) * 60_000;
const SOCKETS = mkdtempSync(path.join(tmpdir(), "prometheus-workers-")); // created 0700

const works = (cmd: string, args: string[]) => {
	try {
		execFileSync(cmd, args, { stdio: "ignore" });
		return true;
	} catch {
		return false;
	}
};
const SANDBOX = process.env.SANDBOX ?? (works("bwrap", ["--version"]) ? "bwrap" : "off");
const LIMITS = SANDBOX !== "off" && works("systemd-run", ["--user", "--scope", "-q", "true"]);
const HOME = homedir();
const PI_AGENT = path.join(HOME, ".pi", "agent");
const NODE_PREFIX = path.dirname(path.dirname(process.execPath)); // node, and uv/python if installed beside it
// DNS: /etc/resolv.conf may link outside /etc (WSL: /mnt/wsl/resolv.conf), which must be mounted too
const RESOLV = existsSync("/etc/resolv.conf") ? realpathSync("/etc/resolv.conf") : "";
const EMPTY = path.join(SOCKETS, "empty"); // what a hidden file reads as
writeFileSync(EMPTY, "");

const send = (res: ServerResponse, code: number, body: unknown) => {
	res.writeHead(code, { "Content-Type": typeof body === "string" ? "text/plain" : "application/json" });
	res.end(typeof body === "string" ? body : JSON.stringify(body));
};

// --- who is calling -------------------------------------------------------------------------------

function tokenOf(req: IncomingMessage) {
	const auth = req.headers.authorization ?? "";
	if (auth.startsWith("Bearer ")) return auth.slice(7);
	return new URL(req.url ?? "/", "http://x").searchParams.get("token") ?? ""; // for EventSource/WebSocket later
}
const sameToken = (t: string) => t.length === TOKEN.length && timingSafeEqual(Buffer.from(t), Buffer.from(TOKEN));
// a user name that is safe as a folder name
const folderName = (u: string) => u.trim().replace(/[^a-zA-Z0-9._-]/g, "_");

// the user making the request, or null after answering 401/403 (host, origin, identity)
function caller(req: IncomingMessage, res: ServerResponse): string | null {
	const host = (req.headers.host ?? "").replace(/:\d+$/, "");
	const origin = req.headers.origin;
	if (!HOSTS.includes(host)) return send(res, 403, "forbidden: host"), null;
	if (origin && !ORIGINS.includes(origin)) return send(res, 403, "forbidden: origin"), null;
	if (origin) res.setHeader("Access-Control-Allow-Origin", origin);
	res.setHeader("Vary", "Origin");
	if (req.method === "OPTIONS") return ""; // a preflight carries no credentials
	if (USER_HEADER) {
		const user = folderName(String(req.headers[USER_HEADER] ?? ""));
		return user ? user : (send(res, 401, "no user from the login proxy"), null);
	}
	return sameToken(tokenOf(req)) ? folderName(DEV_USER) : (send(res, 403, "forbidden: token"), null);
}

// --- the user's folders ---------------------------------------------------------------------------

const prometheusOf = (user: string) => (USERS_ROOT ? path.join(USERS_ROOT, user, "prometheus") : REPO);
// a user's own folder (their sandbox home, Pi state, input files): USERS_ROOT/<user>, or for one person ~/.prometheus-sandbox
const ownOf = (user: string) => (USERS_ROOT ? path.join(USERS_ROOT, user) : path.join(homedir(), ".prometheus-sandbox"));
const filesOf = (user: string) => path.join(ownOf(user), "files");
const SLUG = /^[a-z0-9][a-z0-9-]*$/;

function listWorkflows(user: string, res: ServerResponse) {
	const root = prometheusOf(user);
	const python = [path.join(root, ".venv/bin/python"), path.join(REPO, ".venv/bin/python")].find(existsSync) ?? "python3";
	const script = path.join(root, ".pi/skills/workflow/scripts/list_workflows.py");
	execFile(python, [script, "--root", "workflows"], { cwd: root }, (err, stdout, stderr) =>
		err ? send(res, 500, { error: stderr || String(err) }) : (res.writeHead(200, { "Content-Type": "application/json" }), res.end(stdout)),
	);
}

// --- sandboxes ------------------------------------------------------------------------------------

// the planner creates and refreshes workflows but never needs their secrets or conversations: each
// .env reads as empty, each .pi/sessions as an empty folder
const hideSecrets = (workflows: string) =>
	existsSync(workflows)
		? readdirSync(workflows).flatMap((s) => [
				...(existsSync(path.join(workflows, s, ".env")) ? ["--ro-bind", EMPTY, path.join(workflows, s, ".env")] : []),
				...(existsSync(path.join(workflows, s, ".pi", "sessions")) ? ["--tmpfs", path.join(workflows, s, ".pi", "sessions")] : []),
			])
		: [];

// [command, args] that run `command` for this user's agent inside its sandbox (or as is, SANDBOX=off)
function sandboxed(user: string, agent: string, cwd: string, command: string[], env: Record<string, string>, writable: string[] = [], tty = false): [string, string[]] {
	if (SANDBOX === "off") return [command[0], command.slice(1)];
	const root = prometheusOf(user);
	const own = ownOf(user);
	const home = path.join(own, "home");
	mkdirSync(home, { recursive: true });
	const agentDir = USERS_ROOT ? path.join(own, "pi-agent") : PI_AGENT; // one person: your own Pi state
	const ro = (p: string) => (existsSync(p) ? ["--ro-bind", p, p] : []);
	const rw = (p: string) => (existsSync(p) ? ["--bind", p, p] : []);
	mkdirSync(filesOf(user), { recursive: true });
	const files = [filesOf(user), ...(process.env.SANDBOX_READ ?? "").split(",").filter(Boolean)];
	const a = [
		// --new-session stops a sandbox typing into the router's terminal; a terminal of its own (tty) must
		// keep its pseudo-terminal as the controlling one, for resizes
		"--unshare-all", "--share-net", "--die-with-parent", ...(tty ? [] : ["--new-session"]), "--clearenv",
		"--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
		"--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin", "--ro-bind", "/etc", "/etc",
		...(RESOLV && !RESOLV.startsWith("/etc/") ? ["--ro-bind", RESOLV, RESOLV] : []),
		"--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
		"--bind", home, HOME, // a home of its own; what follows is mounted into it
		...ro(NODE_PREFIX), ...ro(path.join(REPO, "ui")), ...ro(path.join(REPO, "node_modules")),
		"--bind", agentDir, PI_AGENT, ...(USERS_ROOT ? ro(path.join(PI_AGENT, "npm")) : []), // shared Pi packages
		...(agent === "prometheus"
			? [...ro(root), ...rw((mkdirSync(sessionDirOf(root), { recursive: true }), sessionDirOf(root))), ...rw(path.join(root, "workflows")), ...hideSecrets(path.join(root, "workflows"))]
			: [...ro(path.join(root, ".venv")), ...rw(cwd)]),
		...files.flatMap(ro),
		...writable.flatMap(rw),
		"--chdir", cwd,
	];
	const vars = {
		HOME, USER: user, LANG: process.env.LANG ?? "C.UTF-8", TERM: "xterm-256color",
		PATH: `${path.join(NODE_PREFIX, "bin")}:/usr/local/bin:/usr/bin:/bin`,
		PYTHONDONTWRITEBYTECODE: "1", // the code is read-only
		PI_SUBAGENTS_TEMP_ROOT: path.join(HOME, ".cache", "pi-subagents"), // survives a worker restart
		...env,
	};
	for (const [k, v] of Object.entries(vars)) a.push("--setenv", k, v);
	const limits = LIMITS ? ["systemd-run", "--user", "--scope", "-q", "-p", `MemoryMax=${process.env.MEMORY_MAX ?? "4G"}`, "-p", `CPUQuota=${process.env.CPU_QUOTA ?? "200%"}`, "--"] : [];
	const all = [...limits, "bwrap", ...a, "--", ...command];
	return [all[0], all.slice(1)];
}

// node ui/router.ts sandbox-run <user> <p | w/slug> -- <command…>: for checking what a sandbox can do
if (process.argv[2] === "sandbox-run") {
	const [user, which] = process.argv.slice(3, 5);
	const cmd = process.argv.slice(process.argv.indexOf("--") + 1);
	const cwd = which === "p" ? prometheusOf(user) : path.join(prometheusOf(user), "workflows", which.slice(2));
	const [c, args] = sandboxed(user, which === "p" ? "prometheus" : which, cwd, cmd, {});
	const r = spawn(c, args, { stdio: "inherit", env: SANDBOX === "off" ? process.env : {} });
	r.on("exit", (code) => process.exit(code ?? 1));
	await new Promise(() => {});
}

// --- files: your input files, and workflows' outputs -----------------------------------------------

const MAX_UPLOAD = Number(process.env.MAX_UPLOAD_MB ?? 200) * 1024 * 1024;

// `rel` resolved inside `base`, or null: no ../, no absolute paths, no symlink out of it
function inside(base: string, rel: string): string | null {
	const p = path.resolve(base, rel.replace(/^\/+/, ""));
	if (p !== base && !p.startsWith(base + path.sep)) return null;
	if (existsSync(p)) {
		const real = realpathSync(p), root = realpathSync(base);
		if (real !== root && !real.startsWith(root + path.sep)) return null;
	}
	return p;
}

function listFiles(user: string, dir: string, res: ServerResponse) {
	const base = filesOf(user);
	mkdirSync(base, { recursive: true });
	const d = inside(base, dir);
	if (!d || !existsSync(d) || !statSync(d).isDirectory()) return send(res, 404, "no such folder");
	const entries = readdirSync(d, { withFileTypes: true })
		.map((e) => {
			const s = statSync(path.join(d, e.name));
			return { name: e.name, dir: e.isDirectory(), size: s.size, modified: s.mtime.toISOString() };
		})
		.sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name));
	send(res, 200, { root: base, dir: path.relative(base, d), path: d, entries });
}

function upload(user: string, rel: string, req: IncomingMessage, res: ServerResponse) {
	const base = filesOf(user);
	const p = inside(base, rel);
	if (!p || p === base) return send(res, 400, "bad path");
	mkdirSync(path.dirname(p), { recursive: true });
	const out = createWriteStream(p);
	let size = 0;
	req.on("data", (c: Buffer) => {
		if ((size += c.length) <= MAX_UPLOAD) return;
		req.unpipe(out);
		out.destroy();
		rmSync(p, { force: true });
		if (!res.headersSent) send(res, 413, `larger than ${MAX_UPLOAD / 1024 / 1024} MB`);
	});
	req.pipe(out);
	out.on("finish", () => res.headersSent || send(res, 200, { ok: true, path: p }));
	out.on("error", (e) => res.headersSent || send(res, 500, String(e)));
}

function sendFile(file: string | null, res: ServerResponse) {
	if (!file || !existsSync(file) || !statSync(file).isFile()) return send(res, 404, "no such file");
	res.writeHead(200, {
		"Content-Type": "application/octet-stream",
		"Content-Disposition": `attachment; filename="${path.basename(file).replace(/"/g, "")}"`,
		"Content-Length": statSync(file).size,
	});
	createReadStream(file).pipe(res);
}

// a workflow's results: every file under runs/<id>/outputs/, newest run first
function outputs(cwd: string, res: ServerResponse) {
	const runs = path.join(cwd, "runs");
	const out: { run: string; path: string; size: number; modified: string }[] = [];
	for (const id of existsSync(runs) ? readdirSync(runs).sort().reverse() : []) {
		const o = path.join(runs, id, "outputs");
		if (!existsSync(o)) continue;
		for (const f of readdirSync(o, { recursive: true }) as string[]) {
			const s = statSync(path.join(o, f));
			if (s.isFile()) out.push({ run: id, path: path.join("runs", id, "outputs", f), size: s.size, modified: s.mtime.toISOString() });
		}
	}
	send(res, 200, out);
}

// --- terminals: pi in a pseudo-terminal (pty_relay.py), in the agent's sandbox ---------------------

type Term = { proc: ChildProcess; clients: Set<ServerResponse>; backlog: Buffer[]; size: number; stop?: NodeJS.Timeout };
const terms = new Map<string, Term>();
const BACKLOG = 256 * 1024; // what a page that (re)connects sees first

function terminal(user: string, agent: string, cwd: string, what: string, q: URLSearchParams, req: IncomingMessage, res: ServerResponse) {
	const key = `${user}/${agent}`;
	if (what === "stream") {
		let t = terms.get(key);
		if (!t) {
			const cols = String(Number(q.get("cols")) || 100), rows = String(Number(q.get("rows")) || 30);
			const [cmd, args] = sandboxed(user, agent, cwd, ["pi"], {}, [], true);
			const proc = spawn("python3", [path.join(import.meta.dirname, "pty_relay.py"), cols, rows, "--", cmd, ...args], {
				cwd,
				env: SANDBOX === "off" ? process.env : { PATH: process.env.PATH ?? "/usr/bin:/bin" },
				stdio: ["pipe", "pipe", "pipe", "pipe"],
			});
			const term: Term = { proc, clients: new Set(), backlog: [], size: 0 };
			proc.stdout!.on("data", (d: Buffer) => {
				term.backlog.push(d);
				term.size += d.length;
				while (term.size > BACKLOG && term.backlog.length > 1) term.size -= term.backlog.shift()!.length;
				for (const c of term.clients) c.write(`data: ${d.toString("base64")}\n\n`);
			});
			proc.stderr!.on("data", (d) => process.stderr.write(`[${key} terminal] ${d}`));
			proc.on("exit", () => {
				terms.delete(key);
				for (const c of term.clients) c.end("event: exit\ndata: \n\n");
			});
			terms.set(key, (t = term));
			console.log(`[${key}] terminal started`);
		}
		const term = t;
		clearTimeout(term.stop);
		res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
		for (const d of term.backlog) res.write(`data: ${d.toString("base64")}\n\n`);
		term.clients.add(res);
		// the last page left: stop pi a minute later, unless one comes back
		res.on("close", () => {
			term.clients.delete(res);
			if (!term.clients.size) term.stop = setTimeout(() => term.proc.kill("SIGTERM"), 60_000);
		});
		return;
	}
	const t = terms.get(key);
	if (!t) return send(res, 404, "no terminal");
	const chunks: Buffer[] = [];
	req.on("data", (c: Buffer) => chunks.push(c));
	req.on("end", () => {
		const body = Buffer.concat(chunks);
		if (what === "input") t.proc.stdin!.write(body);
		else if (what === "resize") {
			const { cols, rows } = JSON.parse(String(body) || "{}");
			(t.proc.stdio[3] as Writable).write(`${Number(cols) || 100} ${Number(rows) || 30}\n`);
		} else return send(res, 404, "not found");
		send(res, 200, { ok: true });
	});
}

// --- conversations: an agent's Pi sessions ----------------------------------------------------------

// a folder's conversations: its own .pi/sessions (the `sessionDir` of its .pi/settings.json)
const sessionDirOf = (cwd: string) => path.join(cwd, ".pi", "sessions");
const sessionsOf = async (_user: string, cwd: string) =>
	existsSync(sessionDirOf(cwd)) ? (await SessionManager.list(cwd, sessionDirOf(cwd))).filter((s) => s.messageCount > 0) : [];

async function listSessions(user: string, agent: string, cwd: string, res: ServerResponse) {
	const list = (await sessionsOf(user, cwd)).sort((a, b) => +b.modified - +a.modified);
	send(res, 200, list.map((s) => ({ id: s.id, title: s.name || s.firstMessage.replace(/\s+/g, " ").slice(0, 80) || "(empty)", modified: s.modified, messages: s.messageCount, open: workers.has(`${user}/${agent}@${s.id}`) })));
}

// a conversation deleted: its worker stopped (refused while its agent works), its session file removed
async function deleteSession(user: string, agent: string, cwd: string, id: string, res: ServerResponse) {
	const s = (await sessionsOf(user, cwd)).find((x) => x.id === id);
	if (!s) return send(res, 404, "no such conversation");
	const w = workers.get(`${user}/${agent}@${id}`);
	if (w) {
		const busy = await ask(w.socket, "/status").then((st) => st.busy || st.panels, () => false);
		if (busy) return send(res, 409, "the agent is working in this conversation: wait until it finishes");
		w.proc.kill("SIGTERM");
	}
	rmSync(s.path, { force: true });
	rmSync(s.path.replace(/\.jsonl$/, ""), { recursive: true, force: true }); // its artifacts, if any
	send(res, 200, { ok: true });
}

// --- workers: one process per user and agent --------------------------------------------------------

type Worker = { proc: ChildProcess; socket: string; ready: Promise<void>; lastUsed: number; open: number };
const workers = new Map<string, Worker>();

const ask = (socket: string, p: string) =>
	new Promise<any>((resolve, reject) => {
		const r = request({ socketPath: socket, path: p }, (res) => {
			let body = "";
			res.on("data", (c) => (body += c)).on("end", () => (res.statusCode === 200 ? resolve(JSON.parse(body)) : reject(new Error(String(res.statusCode)))));
		});
		r.on("error", reject).end();
	});

// one worker per conversation: `session` is a Pi session id, "new-…" for a new one, or none for the
// folder's latest (as `pi --continue`). Once started, a worker is also found by its real session id, so
// opening a conversation that is already open reuses its worker.
function workerFor(user: string, agent: string, cwd: string, session = ""): Worker {
	const key = `${user}/${agent}${session ? `@${session}` : ""}`;
	const found = workers.get(key);
	if (found) return found;
	const dir = path.join(SOCKETS, createHash("sha1").update(key).digest("hex").slice(0, 16));
	mkdirSync(dir, { mode: 0o700 });
	const socket = path.join(dir, "w.sock");
	const [cmd, args] = sandboxed(user, agent, cwd, [process.execPath, path.join(import.meta.dirname, "worker.ts")], {
		AGENT_CWD: cwd,
		WORKER_SOCKET: socket,
		SESSION_ID: session,
		...(process.env.DEBUG ? { DEBUG: process.env.DEBUG } : {}),
	}, [dir]);
	const proc = spawn(cmd, args, { cwd, env: SANDBOX === "off" ? { ...process.env, AGENT_CWD: cwd, WORKER_SOCKET: socket, SESSION_ID: session } : {}, stdio: ["ignore", "pipe", "pipe"] });
	const tag = `[${key}]`;
	proc.stdout!.on("data", (d) => process.stdout.write(String(d).replace(/^(?=.)/gm, `${tag} `)));
	proc.stderr!.on("data", (d) => process.stderr.write(String(d).replace(/^(?=.)/gm, `${tag} `)));
	proc.on("exit", (code) => {
		for (const [k, v] of workers) if (v === w) workers.delete(k); // its key and its session alias
		rmSync(dir, { recursive: true, force: true });
		console.log(`${tag} stopped (${code ?? "signal"})`);
	});
	const w: Worker = { proc, socket, ready: Promise.resolve(), lastUsed: Date.now(), open: 0 };
	// ready once the worker answers on its socket (loading Pi takes a few seconds)
	w.ready = (async () => {
		for (let i = 0; i < 120; i++) {
			if (proc.exitCode !== null) throw new Error(`worker for ${key} exited while starting`);
			const s = await ask(socket, "/status").catch(() => null);
			if (s) {
				if (s.sessionId) workers.set(`${user}/${agent}@${s.sessionId}`, w);
				return;
			}
			await new Promise((r) => setTimeout(r, 250));
		}
		throw new Error(`worker for ${key} did not start in 30 s`);
	})();
	void w.ready.catch(() => {});
	workers.set(key, w);
	console.log(`${tag} starting in ${cwd}`);
	return w;
}

async function forward(w: Worker, req: IncomingMessage, res: ServerResponse, p: string) {
	w.open++;
	w.lastUsed = Date.now();
	const done = () => ((w.open = Math.max(0, w.open - 1)), (w.lastUsed = Date.now()));
	try {
		await w.ready;
	} catch (err) {
		done();
		return send(res, 503, String((err as Error).message));
	}
	const headers = { "content-type": req.headers["content-type"] ?? "", accept: req.headers.accept ?? "" };
	const up = request({ socketPath: w.socket, path: p, method: req.method, headers }, (r) => {
		res.writeHead(r.statusCode ?? 502, { "Content-Type": r.headers["content-type"] ?? "application/octet-stream", "Cache-Control": "no-cache" });
		r.pipe(res); // SSE streams through as it comes
		r.on("end", done);
	});
	up.on("error", (err) => (done(), res.headersSent ? res.end() : send(res, 502, String(err))));
	// the page went away before the answer finished (reload, closed tab): tell the worker
	res.on("close", () => res.writableEnded || up.destroy());
	req.pipe(up);
}

// stop workers nobody used for IDLE_MS, unless their agent is busy (a wave can take a while)
setInterval(async () => {
	for (const [key, w] of workers) {
		if (w.open || Date.now() - w.lastUsed < IDLE_MS) continue;
		const busy = await ask(w.socket, "/status").then((s) => s.busy, () => false);
		if (!busy) {
			console.log(`[${key}] idle, stopping`);
			w.proc.kill("SIGTERM");
		}
	}
}, 60_000).unref();

for (const sig of ["SIGINT", "SIGTERM"] as const)
	process.on(sig, () => {
		for (const w of workers.values()) w.proc.kill("SIGTERM");
		for (const t of terms.values()) t.proc.kill("SIGTERM");
		rmSync(SOCKETS, { recursive: true, force: true });
		process.exit(0);
	});

// --- routes ---------------------------------------------------------------------------------------

createServer((req, res) => {
	const user = caller(req, res);
	if (user === null) return;
	if (req.method === "OPTIONS") {
		res.writeHead(204, { "Access-Control-Allow-Headers": "content-type, authorization, accept", "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS" });
		return res.end();
	}
	const url = (req.url ?? "/").split("?")[0];
	const root = prometheusOf(user);
	if (!existsSync(path.join(root, ".pi", "APPEND_SYSTEM.md"))) return send(res, 403, `no prometheus folder for user ${user}`);
	if (req.method === "GET" && url === "/me") return send(res, 200, { user, folder: USERS_ROOT ? `${user}/prometheus` : "this repository" });
	if (req.method === "GET" && url === "/workflows") return listWorkflows(user, res);
	const q = new URL(req.url ?? "/", "http://x").searchParams;
	if (url === "/files") {
		if (req.method === "GET") return listFiles(user, q.get("dir") ?? "", res);
		if (req.method === "PUT") return upload(user, q.get("path") ?? "", req, res);
		if (req.method === "DELETE") {
			const p = inside(filesOf(user), q.get("path") ?? "");
			if (!p || p === filesOf(user)) return send(res, 400, "bad path");
			rmSync(p, { recursive: true, force: true });
			return send(res, 200, { ok: true });
		}
	}
	if (req.method === "GET" && url === "/files/download") return sendFile(inside(filesOf(user), q.get("path") ?? ""), res);

	// an agent: prometheus (/p/…) or a workflow (/w/<slug>/…)
	let agent: string, cwd: string, rest: string;
	let m = url.match(/^\/p(\/.*)$/);
	if (m) [agent, cwd, rest] = ["prometheus", root, m[1]];
	else if ((m = url.match(/^\/w\/([^/]+)(\/.*)$/))) {
		[agent, cwd, rest] = [`w/${m[1]}`, path.join(root, "workflows", m[1]), m[2]];
		if (!SLUG.test(m[1]) || !existsSync(path.join(cwd, "plan.yaml"))) return send(res, 404, `no workflow ${m[1]}`);
		if (req.method === "GET" && rest === "/outputs") return outputs(cwd, res);
		if (req.method === "GET" && rest === "/download") {
			const p = q.get("path") ?? "";
			return sendFile(/^runs\/[^/]+\/outputs\//.test(p) ? inside(cwd, p) : null, res);
		}
	} else return send(res, 404, "not found");
	if (rest.startsWith("/term/")) return terminal(user, agent, cwd, rest.slice(6), q, req, res);
	if (req.method === "GET" && rest === "/sessions") return void listSessions(user, agent, cwd, res);
	const del = rest.match(/^\/sessions\/([\w-]+)$/);
	if (req.method === "DELETE" && del) return void deleteSession(user, agent, cwd, del[1], res);
	// a conversation: /…/s/<session id or new-…>/<agent path>
	const conv = rest.match(/^\/s\/([\w-]+)(\/.*)$/);
	if (conv) return void forward(workerFor(user, agent, cwd, conv[1]), req, res, conv[2]);
	return void forward(workerFor(user, agent, cwd), req, res, rest);
}).listen(PORT, HOST, () => {
	console.log(`prometheus router on http://${HOST}:${PORT}  (users: ${USERS_ROOT ?? "this repository, one user"})`);
	console.log(SANDBOX === "off" ? "WARNING: SANDBOX=off, workers run with your full access" : `sandbox: bubblewrap${LIMITS ? ", with memory/CPU limits" : " (no systemd user session: no memory/CPU limits)"}`);
	if (!USER_HEADER) console.log(`open ${ORIGINS[0]}/?token=${TOKEN}`);
});
