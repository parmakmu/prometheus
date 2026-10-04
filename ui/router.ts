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
import { appendFileSync, createReadStream, createWriteStream, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import type { Writable } from "node:stream";
import { createServer, request, type IncomingMessage, type ServerResponse } from "node:http";
import { homedir, tmpdir, userInfo } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const REPO = path.resolve(import.meta.dirname, "..");
// Pi is the global install, the very `pi` the terminal runs: this repo keeps no copy of its own (Node never looks
// in the global folder, so it is loaded by path). PI_PACKAGE_ROOT points elsewhere. Workers get the same path:
// their entry is worker.ts, not Pi's cli.js, so pi-subagents cannot find the Pi package from argv and refuses
// background children ("neither is available") unless it is named for them.
const PI_PACKAGE_ROOT =
	process.env.PI_PACKAGE_ROOT ?? path.join(execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim(), "@earendil-works", "pi-coding-agent");
if (!existsSync(path.join(PI_PACKAGE_ROOT, "dist", "index.js"))) {
	console.error(`No Pi at ${PI_PACKAGE_ROOT}: install it (npm install -g @earendil-works/pi-coding-agent), or set PI_PACKAGE_ROOT`);
	process.exit(1);
}
const { SessionManager } = await import(pathToFileURL(path.join(PI_PACKAGE_ROOT, "dist", "index.js")).href);
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

// where sandboxed workers' pi-subagents keep their runs (survives a worker restart); list_workflows.py reads them there
const SUBAGENTS_ROOT = path.join(HOME, ".cache", "pi-subagents");

const pythonFor = (root: string) => [path.join(root, ".venv/bin/python"), path.join(REPO, ".venv/bin/python")].find(existsSync) ?? "python3";

// all your workflows, or just one (`only`: its folder), as list_workflows.py summarises them
function listWorkflows(user: string, res: ServerResponse, only?: string) {
	const root = prometheusOf(user);
	const python = pythonFor(root);
	const script = path.join(root, ".pi/skills/workflow/scripts/list_workflows.py");
	const env = SANDBOX === "off" ? process.env : { ...process.env, PI_SUBAGENTS_TEMP_ROOT: SUBAGENTS_ROOT };
	execFile(python, [script, ...(only ? ["--workflow", only] : ["--root", "workflows"])], { cwd: root, env }, (err, stdout, stderr) =>
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
		...ro(NODE_PREFIX), ...ro(path.dirname(path.dirname(PI_PACKAGE_ROOT))), ...ro(path.join(REPO, "ui")), ...ro(path.join(REPO, "node_modules")),
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
		PI_SUBAGENTS_TEMP_ROOT: SUBAGENTS_ROOT,
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

const MAX_PREVIEW = 200 * 1024;
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

// `unique`: a file already there keeps its name and this one becomes "name (2).ext" (two pasted image.png)
function upload(base: string, rel: string, req: IncomingMessage, res: ServerResponse, unique = false) {
	let p = inside(base, rel);
	if (!p || p === base) return send(res, 400, "bad path");
	if (unique) {
		const { dir, name, ext } = path.parse(p);
		for (let n = 2; existsSync(p); n++) p = path.join(dir, `${name} (${n})${ext}`);
	}
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

// what the folder view of a workflow never shows or serves: secrets, git's and uv's internals, session logs, macOS litter
const HIDDEN = /(^|\/)(\.git|\.venv|node_modules|__pycache__|\.DS_Store|\.gitkeep|\.env(\..*)?|\.pi\/sessions)(\/|$)/i; // i: macOS's file system ignores case (.ENV is .env)

// `rel` inside the workflow folder `cwd`, or null if it escapes, is hidden or (for a file) does not exist
// (checked both as asked and as resolved: a symlink such as runs/x/work/cfg -> ../../../.env must not serve .env)
function inWorkflow(cwd: string, rel: string): string | null {
	const p = inside(cwd, rel);
	if (!p) return null;
	const shown = (file: string, root: string) => path.relative(root, file).split(path.sep).join("/");
	if (HIDDEN.test(shown(p, cwd))) return null;
	if (existsSync(p) && HIDDEN.test(shown(realpathSync(p), realpathSync(cwd)))) return null;
	return p;
}

// whether a run of the workflow is active (its run.yaml says so): then nothing outside the scripts touches its git
function runActive(cwd: string) {
	const runs = path.join(cwd, "runs");
	return existsSync(runs) && readdirSync(runs).some((id) => {
		const f = path.join(runs, id, "run.yaml");
		return existsSync(f) && /^status:\s*['"]?active['"]?\s*$/m.test(readFileSync(f, "utf8"));
	});
}

function listWorkflowFolder(cwd: string, dir: string, res: ServerResponse) {
	const d = inWorkflow(cwd, dir);
	if (!d || !existsSync(d) || !statSync(d).isDirectory()) return send(res, 404, "no such folder");
	const entries = readdirSync(d, { withFileTypes: true })
		.filter((e) => inWorkflow(cwd, path.relative(cwd, path.join(d, e.name))))
		.map((e) => {
			const s = statSync(path.join(d, e.name));
			return { name: e.name, dir: s.isDirectory(), size: s.size, modified: s.mtime.toISOString() };
		})
		.sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name));
	send(res, 200, { root: cwd, dir: path.relative(cwd, d), path: d, entries });
}

// a file's text for the preview; a binary or large one answers `text: null` (the page offers a download)
function readWorkflowFile(cwd: string, rel: string, res: ServerResponse) {
	const f = inWorkflow(cwd, rel);
	if (!f || !existsSync(f) || !statSync(f).isFile()) return send(res, 404, "no such file");
	const size = statSync(f).size;
	if (size > MAX_PREVIEW) return send(res, 200, { size, text: null });
	const bytes = readFileSync(f);
	send(res, 200, { size, text: bytes.includes(0) ? null : bytes.toString("utf8") });
}

// what the person may change in the page: the plan, the agent role, programs, gates and project agents
const EDITABLE = /^(plan\.yaml|\.pi\/APPEND_SYSTEM\.md|(scripts|gates|\.pi\/agents)\/.+)$/;
const MAX_EDIT = 1024 * 1024;

// Saves a file of the workflow. Once a run exists the edit goes to a change branch (begin-change opens or continues
// revise/<date>-<n>), so main changes only by a reviewed merge; before the first run it is planning, on main.
function saveWorkflowFile(user: string, cwd: string, rel: string, req: IncomingMessage, res: ServerResponse) {
	const f = inWorkflow(cwd, rel);
	if (!f || !EDITABLE.test(path.relative(cwd, f).split(path.sep).join("/")) || !existsSync(f) || !statSync(f).isFile()) return send(res, 400, "this file can't be edited here");
	const chunks: Buffer[] = [];
	let size = 0;
	req.on("data", (c: Buffer) => {
		size += c.length;
		if (size <= MAX_EDIT) chunks.push(c);
	});
	req.on("end", () => {
		if (size > MAX_EDIT) return send(res, 413, "larger than 1 MB");
		const root = prometheusOf(user);
		const script = path.join(root, ".pi/skills/workflow/scripts/workflow_git.py");
		execFile(pythonFor(root), [script, "--workflow", cwd, "begin-change"], { cwd }, (err, stdout, stderr) => {
			let out: { ok?: boolean; error?: string; branch?: string; mode?: string } = {};
			try {
				out = JSON.parse(stdout);
			} catch {}
			if (err && !out.error) return send(res, 500, stderr || String(err));
			if (out.error) return send(res, 409, out.error);
			writeFileSync(f, Buffer.concat(chunks));
			send(res, 200, { ok: true, branch: out.branch, mode: out.mode });
		});
	});
}

// --- what changed: the checked-out branch against main (or, on main, uncommitted edits against HEAD) ----------

const gitIn = (cwd: string, args: string[]) =>
	new Promise<{ code: number; out: string; err: string }>((resolve) =>
		execFile("git", args, { cwd, maxBuffer: 8 * 1024 * 1024 }, (e, out, err) => resolve({ code: e ? ((e as { code?: number }).code ?? 1) : 0, out, err })),
	);
const REVIEW_BRANCH = /^(run|revise|improve|change)\/[\w.-]+$/;

async function baseOf(cwd: string) {
	const branch = (await gitIn(cwd, ["branch", "--show-current"])).out.trim();
	return { branch, base: branch && branch !== "main" ? "main" : "HEAD" };
}

async function changes(cwd: string, res: ServerResponse) {
	const { branch, base } = await baseOf(cwd);
	const files = new Map<string, string>();
	for (const l of (await gitIn(cwd, ["diff", "--name-status", base])).out.split("\n").filter(Boolean)) {
		const [status, ...p] = l.split("\t");
		files.set(p[p.length - 1], status[0]);
	}
	for (const p of (await gitIn(cwd, ["ls-files", "--others", "--exclude-standard"])).out.split("\n").filter(Boolean)) files.set(p, "A");
	send(res, 200, { branch, base, files: [...files].filter(([p]) => inWorkflow(cwd, p)).map(([p, status]) => ({ path: p, status })) });
}

async function diffOf(cwd: string, rel: string, res: ServerResponse) {
	const f = inWorkflow(cwd, rel);
	if (!f) return send(res, 400, "bad path");
	const { base } = await baseOf(cwd);
	const tracked = (await gitIn(cwd, ["ls-files", "--error-unmatch", "--", rel])).code === 0;
	const r = tracked ? await gitIn(cwd, ["diff", base, "--", rel]) : await gitIn(cwd, ["diff", "--no-index", "--", "/dev/null", rel]); // exit 1 is "differs"
	send(res, 200, { diff: r.out });
}

// put a file back as main (or HEAD) has it; a file new on the branch is deleted
async function discardEdits(cwd: string, asked: string, res: ServerResponse) {
	const f = inWorkflow(cwd, asked);
	const rel = f ? path.relative(cwd, f).split(path.sep).join("/") : "";
	if (!f || !EDITABLE.test(rel)) return send(res, 400, "this file can't be changed here");
	// a wave's writer children are checked against the repo's staged index: leave it alone while a run is active
	if (runActive(cwd)) return send(res, 409, "a run is active: discard edits once it has finished");
	const { base } = await baseOf(cwd);
	const known = (await gitIn(cwd, ["cat-file", "-e", `${base}:${rel}`])).code === 0;
	if (known) {
		const r = await gitIn(cwd, ["restore", `--source=${base}`, "--worktree", "--staged", "--", rel]);
		if (r.code) return send(res, 500, r.err);
	} else {
		await gitIn(cwd, ["rm", "-q", "-f", "--ignore-unmatch", "--", rel]);
		rmSync(f, { force: true });
	}
	send(res, 200, { ok: true });
}

// check out a branch kept for review, so the agent can review it (its own tools work on the checked-out branch).
// Only from a clean main with no run active: nothing in progress may come along or be lost.
async function checkoutBranch(cwd: string, name: string, res: ServerResponse) {
	if (!REVIEW_BRANCH.test(name)) return send(res, 400, "not a branch to review");
	if (runActive(cwd)) return send(res, 409, "a run is active: review branches once it has finished");
	const { branch } = await baseOf(cwd);
	if (branch === name) return send(res, 200, { ok: true, branch });
	if (branch !== "main") return send(res, 409, `${branch} is checked out: finish its review first (merge, keep or discard)`);
	if ((await gitIn(cwd, ["status", "--porcelain"])).out.trim()) return send(res, 409, "main has uncommitted changes: they would come along to the branch");
	const r = await gitIn(cwd, ["switch", "-q", name]);
	if (r.code) return send(res, 409, r.err.trim() || "git switch failed");
	send(res, 200, { ok: true, branch: name });
}

// the checked-out branch goes through the script (it also switches back to main); another one is just deleted
function discardBranch(user: string, cwd: string, name: string, res: ServerResponse) {
	if (!REVIEW_BRANCH.test(name)) return send(res, 400, "not a branch to review");
	if (runActive(cwd)) return send(res, 409, "a run is active: discard branches once it has finished");
	const root = prometheusOf(user);
	void baseOf(cwd).then(({ branch }) => {
		if (branch !== name) return void gitIn(cwd, ["branch", "-q", "-D", name]).then((r) => (r.code ? send(res, 409, r.err) : send(res, 200, { ok: true })));
		const script = path.join(root, ".pi/skills/workflow/scripts/workflow_git.py");
		execFile(pythonFor(root), [script, "--workflow", cwd, "merge", "--action", "discard"], { cwd }, (err, stdout, stderr) => {
			let out: { error?: string } = {};
			try {
				out = JSON.parse(stdout);
			} catch {}
			if (out.error || (err && !stdout)) return send(res, 409, out.error ?? (stderr || String(err)));
			send(res, 200, { ok: true });
		});
	});
}

// --- subagents: what pi-subagents records about a launch (a wave, the critic) and its children ----------------

// where the agents' pi-subagents keep their async runs: the sandboxed workers' root, else pi-subagents' default
const subagentRuns = () =>
	path.join(process.env.PI_SUBAGENTS_TEMP_ROOT ?? (SANDBOX === "off" ? path.join(tmpdir(), `pi-subagents-uid-${process.getuid?.() ?? 0}`) : SUBAGENTS_ROOT), "async-subagent-runs");

// a run's status.json, only if the run worked inside this agent's folder (so one user's page never reads another's)
function subagentStatus(cwd: string, id: string): any | null {
	if (!/^[\w-]{8,64}$/.test(id)) return null;
	const file = path.join(subagentRuns(), id, "status.json");
	if (!existsSync(file)) return null;
	try {
		const st = JSON.parse(readFileSync(file, "utf8"));
		const root = realpathSync(cwd);
		const where = st.cwd && existsSync(st.cwd) ? realpathSync(st.cwd) : "";
		return where === root || where.startsWith(root + path.sep) ? st : null;
	} catch {
		return null;
	}
}

const costOf = (st: any) => st?.totalCost?.costUsd ?? null;

// A command child (the workflow-batcher runner: no model) runs a reviewed program, then the stage's gate, or splits
// or merges a map stage. Its request is in the compiled wave (runs/<id>/.compiled/<wave>.wave.json, found by the
// child's key; the child runs in runs/<id>/work), its result in its output log: {program: {exit, stdout, stderr}, gate: {exit}}.
function commandOf(cwd: string, childCwd: string | undefined, key: string): { op: string; script: string | null; command: string | null; gate: string | null } | null {
	if (!childCwd) return null;
	const compiled = path.join(path.dirname(childCwd), ".compiled");
	if (!existsSync(compiled) || !realpathSync(compiled).startsWith(realpathSync(cwd))) return null;
	for (const f of readdirSync(compiled).filter((n) => n.endsWith(".wave.json"))) {
		try {
			const items = JSON.parse(readFileSync(path.join(compiled, f), "utf8")).steps.flatMap((s: any) => s.layer ?? []);
			const item = items.find((i: any) => i.key === key && typeof i.task === "string" && i.task.startsWith("BATCH_TOOL_REQUEST "));
			if (!item) continue;
			const r = JSON.parse(item.task.slice("BATCH_TOOL_REQUEST ".length));
			const script = String(r.command ?? "").match(/\/(scripts\/[^\s/]+\.py)\b/)?.[1] ?? null;
			return { op: r.op ?? "run", script, command: r.command ?? null, gate: r.gate ?? null };
		} catch {}
	}
	return null;
}
const resultOf = (id: string) => {
	try {
		return JSON.parse(readFileSync(path.join(subagentRuns(), id, "output-0.log"), "utf8"));
	} catch {
		return null;
	}
};

// a launch and its children so far: state, times (ms), cost, and per child its step key, agent, model, last tool
function waveInfo(cwd: string, id: string, res: ServerResponse) {
	const st = subagentStatus(cwd, id);
	if (!st) return send(res, 404, "no such run here");
	const children = ((st.workflowChildren?.children ?? []) as any[]).map((c) => {
		const cs = c.runId ? subagentStatus(cwd, c.runId) : null;
		const step = cs?.steps?.[0];
		const tools = step?.recentTools ?? [];
		return {
			key: c.childId, agent: c.agent, state: c.state, runId: c.runId ?? null,
			startedAt: cs?.startedAt ?? null, endedAt: cs?.endedAt ?? null, cost: costOf(cs),
			model: step?.model ?? null, lastTool: tools.length ? tools[tools.length - 1].tool : null,
			lastArgs: tools.length ? String(tools[tools.length - 1].args ?? "").slice(0, 120) : null,
			toolCount: cs?.toolCount ?? null, tokens: cs?.totalTokens?.total ?? null,
			...(c.agent === "workflow-batcher" ? { program: { ...commandOf(cwd, cs?.cwd, c.childId), exit: resultOf(c.runId)?.program?.exit ?? null, gateExit: resultOf(c.runId)?.gate?.exit ?? null } } : {}),
		};
	});
	send(res, 200, { id, state: st.state, startedAt: st.startedAt ?? null, endedAt: st.endedAt ?? null, cost: costOf(st), children });
}

// one subagent's work, read from its own Pi session so the page can show it like the main agent's: its task, then
// each thought, tool call (with its result) and text, in order. A command runner (workflow-batcher) has no session:
// then its log's summary. Thoughts as the worker shows them (text, a readable summary, or a note that it is encrypted).
const MAX_STEP = 4000;
function thoughtOf(block: any): string {
	if (block?.thinking?.trim()) return block.thinking;
	try {
		const d = JSON.parse(block?.thinkingSignature ?? "");
		const text = (Array.isArray(d) ? d : [d]).map((x: any) => x?.summary ?? x?.text ?? "").filter(Boolean).join("\n\n");
		if (text.trim()) return text;
	} catch {}
	return "(The model kept this reasoning private: it sent it encrypted, with no readable summary.)";
}
const clip = (t: string) => (t.length > MAX_STEP ? `${t.slice(0, MAX_STEP)}\n…` : t);
const textIn = (content: any) => (typeof content === "string" ? content : Array.isArray(content) ? content.map((p: any) => (p?.type === "text" ? p.text : "")).join("") : "");

function childLog(cwd: string, id: string, res: ServerResponse) {
	const st = subagentStatus(cwd, id);
	if (!st) return send(res, 404, "no such run here");
	const sessionFile = st.steps?.[0]?.sessionFile;
	const inside = (f: string) => { const r = realpathSync(cwd); const p = realpathSync(f); return p.startsWith(r + path.sep); };
	if (sessionFile && existsSync(sessionFile) && inside(sessionFile)) {
		const steps: any[] = [];
		const results = new Map<string, any>();
		const lines = readFileSync(sessionFile, "utf8").split("\n").filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } });
		for (const e of lines) if (e?.message?.role === "toolResult") results.set(e.message.toolCallId, e.message);
		for (const e of lines) {
			const m = e?.message;
			if (!m) continue;
			if (m.role === "user") steps.push({ kind: "task", text: clip(textIn(m.content)) });
			else if (m.role === "assistant")
				for (const p of Array.isArray(m.content) ? m.content : []) {
					if (p.type === "thinking") steps.push({ kind: "thought", text: clip(thoughtOf(p)) });
					else if (p.type === "text" && p.text?.trim()) steps.push({ kind: "text", text: clip(p.text) });
					else if (p.type === "toolCall") {
						const r = results.get(p.id);
						steps.push({ kind: "tool", name: p.name, args: clip(JSON.stringify(p.arguments ?? {})), result: r ? clip(textIn(r.content)) : null, failed: !!r?.isError });
					}
				}
		}
		return send(res, 200, { steps });
	}
	// a command child: what it ran and how that went
	const done = resultOf(id);
	if (done && (done.program || done.gate)) {
		const key = (st.parentWorkflowRunId && subagentStatus(cwd, st.parentWorkflowRunId)?.workflowChildren?.children?.find((c: any) => c.runId === id)?.childId) ?? "";
		const cmd = commandOf(cwd, st.cwd, key);
		return send(res, 200, { steps: [{ kind: "program", command: cmd?.command ?? null, gate: cmd?.gate ?? null, exit: done.program?.exit ?? null, stdout: clip(String(done.program?.stdout ?? "")), stderr: clip(String(done.program?.stderr ?? "")), gateExit: done.gate?.exit ?? null, gateOutput: clip(String(done.gate?.stdout ?? done.gate?.stderr ?? "")) }] });
	}
	const dir = path.join(subagentRuns(), id);
	const file = readdirSync(dir).find((f) => f.startsWith("subagent-log-") && f.endsWith(".md")) ?? (existsSync(path.join(dir, "output-0.log")) ? "output-0.log" : null);
	const text = file ? readFileSync(path.join(dir, file), "utf8") : "";
	const summary = text.includes("## Summary") ? text.slice(text.indexOf("## Summary") + "## Summary".length).trim() : text;
	send(res, 200, { steps: summary ? [{ kind: "text", text: clip(summary) }] : [] });
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
		PI_SUBAGENTS_PI_CODING_AGENT_PACKAGE_ROOT: PI_PACKAGE_ROOT,
		...(process.env.DEBUG ? { DEBUG: process.env.DEBUG } : {}),
	}, [dir]);
	const proc = spawn(cmd, args, { cwd, env: SANDBOX === "off" ? { ...process.env, AGENT_CWD: cwd, WORKER_SOCKET: socket, SESSION_ID: session, PI_SUBAGENTS_PI_CODING_AGENT_PACKAGE_ROOT: PI_PACKAGE_ROOT } : {}, stdio: ["ignore", "pipe", "pipe"] });
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

function route(req: IncomingMessage, res: ServerResponse) {
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
		if (req.method === "PUT") return upload(filesOf(user), q.get("path") ?? "", req, res, q.get("unique") === "1");
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
		// files attached in this workflow's chat: uploads/ in its folder, which its own sandbox reads at the same path
		if (rest === "/upload") {
			const base = path.join(cwd, "uploads");
			if (req.method === "PUT") {
				// an older workflow's .gitignore has no uploads/ until it is refreshed: keep the files out of git through
				// the repo's own exclude file, which is not versioned (editing .gitignore would leave main dirty)
				const exclude = path.join(cwd, ".git", "info", "exclude");
				if (existsSync(path.join(cwd, ".git")) && !(existsSync(exclude) && /^\/?uploads\/?$/m.test(readFileSync(exclude, "utf8")))) {
					mkdirSync(path.dirname(exclude), { recursive: true });
					appendFileSync(exclude, "\n/uploads/\n");
				}
				return upload(base, q.get("path") ?? "", req, res, q.get("unique") === "1");
			}
			if (req.method === "DELETE") {
				const p = inside(base, q.get("path") ?? "");
				if (!p || p === base) return send(res, 400, "bad path");
				rmSync(p, { recursive: true, force: true });
				return send(res, 200, { ok: true });
			}
		}
		if (req.method === "GET" && rest === "/summary") return listWorkflows(user, res, cwd);
		if (req.method === "GET" && rest === "/outputs") return outputs(cwd, res);
		if (req.method === "GET" && rest === "/download") {
			return sendFile(inWorkflow(cwd, q.get("path") ?? ""), res);
		}
		if (req.method === "GET" && rest === "/tree") return listWorkflowFolder(cwd, q.get("dir") ?? "", res);
		if (req.method === "GET" && rest === "/file") return readWorkflowFile(cwd, q.get("path") ?? "", res);
		if (req.method === "GET" && rest === "/changes") return void changes(cwd, res);
		if (req.method === "GET" && rest === "/diff") return void diffOf(cwd, q.get("path") ?? "", res);
		if (req.method === "POST" && rest === "/discard") return void discardEdits(cwd, q.get("path") ?? "", res);
		if (req.method === "POST" && rest === "/checkout") return void checkoutBranch(cwd, q.get("name") ?? "", res);
		if (req.method === "POST" && rest === "/discard-branch") return discardBranch(user, cwd, q.get("name") ?? "", res);
		if (req.method === "PUT" && rest === "/file") return saveWorkflowFile(user, cwd, q.get("path") ?? "", req, res);
	} else return send(res, 404, "not found");
	if (req.method === "GET" && rest === "/wave") return waveInfo(cwd, q.get("id") ?? "", res);
	if (req.method === "GET" && rest === "/wave/child") return childLog(cwd, q.get("id") ?? "", res);
	if (rest.startsWith("/term/")) return terminal(user, agent, cwd, rest.slice(6), q, req, res);
	if (req.method === "GET" && rest === "/sessions") return void listSessions(user, agent, cwd, res);
	const del = rest.match(/^\/sessions\/([\w-]+)$/);
	if (req.method === "DELETE" && del) return void deleteSession(user, agent, cwd, del[1], res);
	// a conversation: /…/s/<session id or new-…>/<agent path>
	const conv = rest.match(/^\/s\/([\w-]+)(\/.*)$/);
	if (conv) return void forward(workerFor(user, agent, cwd, conv[1]), req, res, conv[2]);
	return void forward(workerFor(user, agent, cwd), req, res, rest);
}

// one request's error is that request's 500, not the end of the router (and every user's workers with it)
createServer((req, res) => {
	try {
		route(req, res);
	} catch (e) {
		console.error(e);
		if (!res.headersSent) send(res, 500, String(e));
	}
}).listen(PORT, HOST, () => {
	console.log(`prometheus router on http://${HOST}:${PORT}  (users: ${USERS_ROOT ?? "this repository, one user"})`);
	console.log(SANDBOX === "off" ? "WARNING: SANDBOX=off, workers run with your full access" : `sandbox: bubblewrap${LIMITS ? ", with memory/CPU limits" : " (no systemd user session: no memory/CPU limits)"}`);
	if (!USER_HEADER) console.log(`open ${ORIGINS[0]}/?token=${TOKEN}`);
	}
});
