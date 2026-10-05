/** Workers: one Pi process per user, agent and conversation; forwarding to them, the history before they're up,
 * restarts after a refresh, and stopping idle ones. */
import { spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, rmSync, statSync } from "node:fs";
import { request, type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import { sessionFile, snapshotFromFile, textOf } from "./history.ts";
import { HOME, IDLE_MS, PI_PACKAGE_ROOT, SANDBOX, SOCKETS, readBody, send, sessionDirOf } from "./common.ts";
import { launcherEnv, sandboxed } from "./sandbox.ts";
import { runActive } from "./files.ts";
// --- workers: one process per user and agent --------------------------------------------------------

export type Worker = {
    proc: ChildProcess; socket: string; ready: Promise<void>; up: boolean; lastUsed: number; open: number;
    key: string; agent: string; cwd: string; code: number; start: () => Worker; // what it runs, and how to start it again
};

// When the code a worker loaded last changed: Pi loads extensions once, at session start. An agent's refresh
// rewrites its skill's VERSION; prometheus's own code is its extension. 0 when there is none.
export const codeStamp = (agent: string, cwd: string) => {
    const file = agent === "prometheus" ? ".pi/extensions/prometheus/index.ts" : ".pi/skills/agent/VERSION";
    try {
        return statSync(path.join(cwd, file)).mtimeMs;
    } catch {
        return 0;
    }
};
export const workers = new Map<string, Worker>();

export const ask = (socket: string, p: string) =>
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
export function workerFor(user: string, agent: string, cwd: string, session = ""): Worker {
    const key = `${user}/${agent}${session ? `@${session}` : ""}`;
    const found = workers.get(key);
    if (found) return found; // one whose agent was refreshed is replaced by restartStale, when it is not busy
    const code = codeStamp(agent, cwd);
    // its own folder each time: the one it replaces may not have exited yet
    const dir = path.join(SOCKETS, createHash("sha1").update(`${key}:${Date.now()}`).digest("hex").slice(0, 16));
    mkdirSync(dir, { mode: 0o700 });
    const socket = path.join(dir, "w.sock");
    const vars = {
        AGENT_CWD: cwd,
        WORKER_SOCKET: socket,
        SESSION_ID: session,
        PI_SUBAGENTS_PI_CODING_AGENT_PACKAGE_ROOT: PI_PACKAGE_ROOT,
        // Node keeps Pi's compiled code between starts: ~0.3 s of a ~2.3 s start (the sandbox's home is its own and kept)
        NODE_COMPILE_CACHE: path.join(HOME, ".cache", "node-compile"),
        ...(process.env.DEBUG ? { DEBUG: process.env.DEBUG } : {}),
    };
    const [cmd, args] = sandboxed(user, agent, cwd, [process.execPath, path.join(import.meta.dirname, "worker.ts")], vars, [dir]);
    const proc = spawn(cmd, args, { cwd, env: SANDBOX === "off" ? { ...process.env, ...vars } : launcherEnv(), stdio: ["ignore", "pipe", "pipe"] });
    const tag = `[${key}]`;
    proc.stdout!.on("data", (d) => process.stdout.write(String(d).replace(/^(?=.)/gm, `${tag} `)));
    proc.stderr!.on("data", (d) => process.stderr.write(String(d).replace(/^(?=.)/gm, `${tag} `)));
    proc.on("exit", (code) => {
        for (const [k, v] of workers) if (v === w) workers.delete(k); // its key and its session alias
        rmSync(dir, { recursive: true, force: true });
        console.log(`${tag} stopped (${code ?? "signal"})`);
    });
    const w: Worker = { proc, socket, ready: Promise.resolve(), up: false, lastUsed: Date.now(), open: 0, key, agent, cwd, code, start: () => workerFor(user, agent, cwd, session) };
    // ready once the worker answers on its socket (loading Pi takes a few seconds)
    w.ready = (async () => {
        for (let i = 0; i < 120; i++) {
            if (proc.exitCode !== null) throw new Error(`worker for ${key} exited while starting`);
            const s = await ask(socket, "/status").catch(() => null);
            if (s) {
                if (s.sessionId) workers.set(`${user}/${agent}@${s.sessionId}`, w);
                w.up = true;
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

// The page's first request to a conversation is a sync (a run with nothing new): it wants the history. While the
// worker is still starting (Pi takes ~2 s to load), the router answers it from the session file, which reads in
// milliseconds, so the conversation shows at once (history.ts builds it as the worker does). A run with something new,
// or a file it can't read, waits for the worker.
export async function historyFirst(w: Worker, req: IncomingMessage, res: ServerResponse, p: string, cwd: string, session: string) {
    let body: Buffer;
    try {
        body = await readBody(req, MAX_RUN_BODY);
    } catch {
        return send(res, 413, "too large");
    }
    let input: any = {};
    try {
        input = JSON.parse(body.toString("utf8") || "{}");
    } catch {}
    const last = [...(input.messages ?? [])].reverse().find((m: any) => m.role === "user");
    const sync = !textOf(last?.content).trim() || String(last?.id ?? "").startsWith("pi-");
    if (sync && !input.resume?.length && !w.up) {
        const file = sessionFile(sessionDirOf(cwd), cwd, session);
        const messages = file ? snapshotFromFile(file) : []; // no file yet: a new conversation
        if (messages) {
            const { threadId, runId } = input;
            res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
            for (const e of [{ type: "RUN_STARTED", threadId, runId }, { type: "MESSAGES_SNAPSHOT", messages }, { type: "RUN_FINISHED", threadId, runId, outcome: { type: "success" } }])
                res.write(`data: ${JSON.stringify(e)}\n\n`);
            return res.end();
        }
    }
    return forward(w, req, res, p, body);
}
export const MAX_RUN_BODY = 50 * 1024 * 1024; // a run's body carries the page's messages back

export async function forward(w: Worker, req: IncomingMessage, res: ServerResponse, p: string, body?: Buffer) {
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
    if (body) up.end(body); // read already (historyFirst)
    else req.pipe(up);
}

// A worker runs the code it loaded: Pi loads extensions once (live run 2026-10-05-2 ran a half-hour-old /agent-auto
// after its refresh). One whose agent's code changed starts again at once, so it is warm when you come back (a start
// takes ~2 s), but only when its worker says it is not busy: a wave runs, or /agent-auto waits on one, with no request
// open. The page's agent list (every 15 s) and the idle check below both look.
export async function restartStale() {
    for (const w of new Set(workers.values())) {
        if (w.open || codeStamp(w.agent, w.cwd) === w.code || runActive(w.cwd)) continue; // a run: a wave may be under way
        if (await ask(w.socket, "/status").then((s) => s.busy, () => true)) continue;
        if (w.open || !workers.has(w.key)) continue; // it got a request, or stopped, meanwhile
        console.log(`[${w.key}] restarting: its agent's code changed`);
        for (const [k, v] of workers) if (v === w) workers.delete(k);
        w.proc.kill();
        w.start();
    }
}

// stop workers nobody used for IDLE_MS, unless their agent is busy (a wave can take a while)
setInterval(async () => {
    await restartStale();
    for (const [key, w] of workers) {
        if (w.open || Date.now() - w.lastUsed < IDLE_MS) continue;
        const busy = await ask(w.socket, "/status").then((s) => s.busy, () => false);
        if (!busy) {
            console.log(`[${key}] idle, stopping`);
            w.proc.kill("SIGTERM");
        }
    }
}, 60_000).unref();
