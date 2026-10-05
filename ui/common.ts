/** Settings from the environment, who is calling, the user's folders, and small helpers the other modules share. */
import { execFileSync } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { type IncomingMessage, type ServerResponse } from "node:http";
import { homedir, tmpdir, userInfo } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const REPO = path.resolve(import.meta.dirname, "..");
// Pi is the global install, the very `pi` the terminal runs: this repo keeps no copy of its own (Node never looks
// in the global folder, so it is loaded by path). PI_PACKAGE_ROOT points elsewhere. Workers get the same path:
// their entry is worker.ts, not Pi's cli.js, so pi-subagents cannot find the Pi package from argv and refuses
// background children ("neither is available") unless it is named for them.
export const PI_PACKAGE_ROOT =
	process.env.PI_PACKAGE_ROOT ?? path.join(execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim(), "@earendil-works", "pi-coding-agent");
if (!existsSync(path.join(PI_PACKAGE_ROOT, "dist", "index.js"))) {
	console.error(`No Pi at ${PI_PACKAGE_ROOT}: install it (npm install -g @earendil-works/pi-coding-agent), or set PI_PACKAGE_ROOT`);
	process.exit(1);
}
export const { SessionManager } = await import(pathToFileURL(path.join(PI_PACKAGE_ROOT, "dist", "index.js")).href);
export const PORT = Number(process.env.PORT ?? 8787);
// local only by default. Under WSL a Windows browser may need HOST=0.0.0.0
export const HOST = process.env.HOST ?? "127.0.0.1";
export const ORIGINS = (process.env.UI_ORIGIN ?? "http://localhost:3000,http://127.0.0.1:3000").split(",");
export const HOSTS = (process.env.ALLOWED_HOSTS ?? "localhost,127.0.0.1,[::1]").split(",");
export const USER_HEADER = process.env.TRUST_USER_HEADER?.toLowerCase();
// Sandboxes share the router's network, loopback included, so behind a login proxy an agent could call the router
// with any user's name in the header: the proxy must also send a secret only it knows (PROXY_SECRET, in the header
// PROXY_SECRET_HEADER, default x-proxy-secret).
export const PROXY_SECRET = process.env.PROXY_SECRET ?? "";
export const PROXY_SECRET_HEADER = (process.env.PROXY_SECRET_HEADER ?? "x-proxy-secret").toLowerCase();
if (USER_HEADER && PROXY_SECRET.length < 16) {
	console.error("TRUST_USER_HEADER needs PROXY_SECRET (16+ characters), sent by the login proxy in the header PROXY_SECRET_HEADER (default x-proxy-secret): an agent's sandbox can reach the router too");
	process.exit(1);
}
export const DEV_USER = process.env.DEV_USER ?? userInfo().username;
export const TOKEN = process.env.PROMETHEUS_TOKEN || randomBytes(24).toString("base64url");
export const USERS_ROOT = process.env.USERS_ROOT;
export const IDLE_MS = Number(process.env.IDLE_MINUTES ?? 15) * 60_000;
export const SOCKETS = mkdtempSync(path.join(tmpdir(), "prometheus-workers-")); // created 0700

export const works = (cmd: string, args: string[]) => {
	try {
		execFileSync(cmd, args, { stdio: "ignore" });
		return true;
	} catch {
		return false;
	}
};
export const SANDBOX = process.env.SANDBOX ?? (works("bwrap", ["--version"]) ? "bwrap" : "off"); // bwrap | podman | off
if (!["bwrap", "podman", "off"].includes(SANDBOX)) {
	console.error(`SANDBOX=${SANDBOX}: use bwrap, podman or off`);
	process.exit(1);
}
export const HOME = homedir();
export const PI_AGENT = path.join(HOME, ".pi", "agent");
export const NODE_PREFIX = path.dirname(path.dirname(process.execPath)); // node, and uv/python if installed beside it
export const EMPTY = path.join(SOCKETS, "empty"); // what a hidden file reads as
writeFileSync(EMPTY, "");

export const send = (res: ServerResponse, code: number, body: unknown) => {
	res.writeHead(code, { "Content-Type": typeof body === "string" ? "text/plain" : "application/json" });
	res.end(typeof body === "string" ? body : JSON.stringify(body));
};

// --- who is calling -------------------------------------------------------------------------------

// a header only: a token in a URL ends up in proxy and server logs (the page takes it from the printed link
// once, then keeps it in sessionStorage and drops it from the address bar)
export function tokenOf(req: IncomingMessage) {
	const auth = req.headers.authorization ?? "";
	return auth.startsWith("Bearer ") ? auth.slice(7) : "";
}
export const sameToken = (t: string) => t.length === TOKEN.length && timingSafeEqual(Buffer.from(t), Buffer.from(TOKEN));
// a user name that is safe as a folder name
export const folderName = (u: string) => u.trim().replace(/[^a-zA-Z0-9._-]/g, "_");

// the user making the request, or null after answering 401/403 (host, origin, identity)
export function caller(req: IncomingMessage, res: ServerResponse): string | null {
	const host = (req.headers.host ?? "").replace(/:\d+$/, "");
	const origin = req.headers.origin;
	if (!HOSTS.includes(host)) return send(res, 403, "forbidden: host"), null;
	if (origin && !ORIGINS.includes(origin)) return send(res, 403, "forbidden: origin"), null;
	if (origin) res.setHeader("Access-Control-Allow-Origin", origin);
	res.setHeader("Vary", "Origin");
	if (req.method === "OPTIONS") return ""; // a preflight carries no credentials
	if (USER_HEADER) {
		const secret = String(req.headers[PROXY_SECRET_HEADER] ?? "");
		if (!(secret.length === PROXY_SECRET.length && timingSafeEqual(Buffer.from(secret), Buffer.from(PROXY_SECRET))))
			return send(res, 403, "forbidden: not from the login proxy"), null;
		const user = folderName(String(req.headers[USER_HEADER] ?? ""));
		return user ? user : (send(res, 401, "no user from the login proxy"), null);
	}
	return sameToken(tokenOf(req)) ? folderName(DEV_USER) : (send(res, 403, "forbidden: token"), null);
}

// --- the user's folders ---------------------------------------------------------------------------

export const prometheusOf = (user: string) => (USERS_ROOT ? path.join(USERS_ROOT, user, "prometheus") : REPO);
// a user's own folder (their sandbox home, Pi state, input files): USERS_ROOT/<user>, or for one person ~/.prometheus-sandbox
export const ownOf = (user: string) => (USERS_ROOT ? path.join(USERS_ROOT, user) : path.join(homedir(), ".prometheus-sandbox"));
export const filesOf = (user: string) => path.join(ownOf(user), "files");
export const SLUG = /^[a-z0-9][a-z0-9-]*$/;

// where sandboxed workers' pi-subagents keep their runs (survives a worker restart); list_agents.py reads them there
export const SUBAGENTS_ROOT = path.join(HOME, ".cache", "pi-subagents");

export const pythonFor = (root: string) => [path.join(root, ".venv/bin/python"), path.join(REPO, ".venv/bin/python")].find(existsSync) ?? "python3";

export const readBody = (req: IncomingMessage, max: number) =>
	new Promise<Buffer>((resolve, reject) => {
		const chunks: Buffer[] = [];
		let size = 0;
		req.on("data", (c: Buffer) => {
			size += c.length;
			if (size <= max) chunks.push(c);
		});
		req.on("end", () => (size > max ? reject(new Error("too large")) : resolve(Buffer.concat(chunks))));
		req.on("error", reject);
	});


// an agent's or prometheus's conversations: Pi's sessionDir in its .pi/settings.json
export const sessionDirOf = (cwd: string) => path.join(cwd, ".pi", "sessions");
