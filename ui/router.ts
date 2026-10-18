/**
 * The router: the one HTTP server the browser talks to. It checks who is calling, finds that user's
 * folder, and forwards to the worker (worker.ts) for the agent asked for, starting it on first use and
 * stopping it when idle. Each worker is its own process: one Pi session, one folder, one `.env`.
 *
 *   GET  /me                     who you are
 *   GET  /agents              your agents (agent_list.py; no model involved)
 *   GET|POST /a/<slug>/params    its params for a form, and setting them (.env; never a secret's value)
 *   *    /p/<agent path>         prometheus, your planner         → worker in <your prometheus>
 *   *    /a/<slug>/<agent path>  an agent                       → worker in <your prometheus>/agents/<slug>
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
 *   prometheus: your copy read-only, its agents/ read-write
 *   an agent: that folder read-write; prometheus's .venv read-only; nothing of other agents
 *   both:       system and node read-only, your Pi state (sessions, settings, auth), your files
 *               read-only (USERS_ROOT/<user>/files, or SANDBOX_READ paths), a home of its own
 * SANDBOX=off runs workers unsandboxed (development only). With a systemd user session, each worker
 * also gets MEMORY_MAX and CPU_QUOTA limits.
 *
 *   node ui/router.ts sandbox-run <user> <p | a/slug> -- <command…>   runs a command in that sandbox
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, existsSync, mkdirSync, openSync, readSync, rmSync, writeFileSync } from "node:fs";
import type { Writable } from "node:stream";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import { HOST, HOSTS, ORIGINS, PORT, SANDBOX, SLUG, SOCKETS, SessionManager, TOKEN, USERS_ROOT, USER_HEADER, caller, filesOf, prometheusOf, readBody, send, sessionDirOf, tokenOf } from "./common.ts";
import { apiRoute, keyCaller, keysOf, newKey, revokeKey } from "./api.ts";
import { LIMITS } from "./bwrap.ts";
import { EGRESS, EGRESS_BASE, HOST_RE, allowFile, allowedFor, refusedFor, removeOwnContainers, writeAllow } from "./podman.ts";
import { sandboxed, skillScript } from "./sandbox.ts";
import { MAX_EDIT, changes, checkoutBranch, diffOf, discardBranch, discardEdits, inAgent, inside, listAgentFolder, listFiles, readAgentFile, saveAgentFile, sendFile, upload } from "./files.ts";
import { childLog, outputs, textIn, waveInfo } from "./subagents.ts";
import { ask, forward, historyFirst, restartStale, workerFor, workers } from "./workers.ts";
// all your agents, or just one (`only`: its folder), as agent_list.py summarises them
function listAgents(user: string, res: ServerResponse, only?: string) {
  // in the planner's sandbox (it reads every agent's git), or the one agent's
  void skillScript(only ?? prometheusOf(user), "agent_list.py", only ? ["--agent", only] : ["--root", "agents"]).then((r) =>
    r.code ? send(res, 500, { error: r.err || r.out }) : (res.writeHead(200, { "Content-Type": "application/json" }), res.end(r.out)));
}

// a skill script for an agent, run in its folder (no model): its JSON as the answer; a refusal is a 400. Prometheus's
// copy of the script, never the agent's: this runs outside the sandbox, and the agent's model can write its own folder
function agentScript(cwd: string, [script, ...args]: string[], res: ServerResponse) {
  void skillScript(cwd, script, args).then((r) => {
    let out: { ok?: boolean } | undefined;
    try {
      out = JSON.parse(r.out);
    } catch {}
    if (!out) return send(res, 500, { error: r.err || `${script} failed` });
    send(res, out.ok ? 200 : 400, out);
  });
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
        env: SANDBOX === "bwrap" ? { PATH: process.env.PATH ?? "/usr/bin:/bin" } : process.env,
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
const sessionsOf = async (_user: string, cwd: string) =>
  existsSync(sessionDirOf(cwd)) ? (await SessionManager.list(cwd, sessionDirOf(cwd))).filter((s) => s.messageCount > 0) : [];

// A conversation's title: its name, else the command it began with (the worker records which command a prompt came
// from, and the command before what it showed), else its first message. Read from the start of its file only.
function titleOf(s: { path: string; name?: string; firstMessage: string }) {
  if (s.name) return s.name;
  const head = Buffer.alloc(256 * 1024);
  const fd = openSync(s.path, "r");
  const text = head.subarray(0, readSync(fd, head, 0, head.length, 0)).toString("utf8");
  closeSync(fd);
  const entries = text.split("\n").slice(0, -1).map((l) => { try { return JSON.parse(l); } catch { return null; } });
  const notes = entries.filter((e) => e?.type === "custom" && e.customType === "prometheus-ui-command").map((e) => e.data ?? {});
  const first = entries.find((e) => (e?.type === "message" && e.message?.role === "user") || e?.type === "custom_message");
  const prompt = first?.type === "message" ? textIn(first.message.content).trim() : "";
  const hash = createHash("sha256").update(prompt).digest("hex").slice(0, 16);
  const command = notes.find((n) => (prompt ? n.prompt === hash : n.before === first?.id))?.command;
  return (command ?? s.firstMessage).replace(/\s+/g, " ").slice(0, 80) || "(empty)";
}

async function listSessions(user: string, agent: string, cwd: string, res: ServerResponse) {
  const list = (await sessionsOf(user, cwd)).sort((a, b) => +b.modified - +a.modified);
  send(res, 200, list.map((s) => ({ id: s.id, title: titleOf(s), modified: s.modified, messages: s.messageCount, open: workers.has(`${user}/${agent}@${s.id}`) })));
}

// a conversation deleted: its worker stopped (refused while its agent works), its session file removed
async function deleteSession(user: string, agent: string, cwd: string, id: string, res: ServerResponse) {
  const s = (await sessionsOf(user, cwd)).find((x) => x.id === id);
  if (!s) return send(res, 404, "no such session");
  const w = workers.get(`${user}/${agent}@${id}`);
  if (w) {
    const busy = await ask(w.socket, "/status").then((st) => st.busy || st.panels, () => false);
    if (busy) return send(res, 409, "the agent is working in this session: wait until it finishes");
    w.proc.kill("SIGTERM");
  }
  rmSync(s.path, { force: true });
  rmSync(s.path.replace(/\.jsonl$/, ""), { recursive: true, force: true }); // its artifacts, if any
  send(res, 200, { ok: true });
}

// a conversation renamed: Pi's session name (what /name sets), by its worker when one has it open, since a line
// appended beside a live session would not be on its tree; else appended to the file
async function renameSession(user: string, agent: string, cwd: string, id: string, req: IncomingMessage, res: ServerResponse) {
  const s = (await sessionsOf(user, cwd)).find((x) => x.id === id);
  if (!s) return send(res, 404, "no such session");
  const w = workers.get(`${user}/${agent}@${id}`);
  if (w) return void forward(w, req, res, "/name");
  const chunks: Buffer[] = [];
  req.on("data", (c: Buffer) => chunks.push(c));
  req.on("end", () => {
    const name = String(JSON.parse(String(Buffer.concat(chunks)) || "{}").name ?? "").trim();
    if (!name) return send(res, 400, "a name is needed");
    SessionManager.open(s.path, sessionDirOf(cwd)).appendSessionInfo(name);
    send(res, 200, { ok: true });
  });
}

for (const sig of ["SIGINT", "SIGTERM"] as const)
  process.on(sig, () => {
    for (const w of workers.values()) w.proc.kill("SIGTERM");
    for (const t of terms.values()) t.proc.kill("SIGTERM");
    removeOwnContainers();
    rmSync(SOCKETS, { recursive: true, force: true });
    process.exit(0);
  });

// --- the internet an agent may reach (SANDBOX=podman: podman.ts holds the allow-lists) -------------------------------

// what the web app's Settings shows: the hosts this agent may reach, those its plan asks for, those it was refused
async function networkOf(user: string, agent: string, cwd: string) {
  const plan = await skillScript(cwd, "plan_edit.py", ["network-show"]);
  let requested: string[] = [];
  try {
    requested = JSON.parse(plan.out).requested ?? [];
  } catch {}
  const allowed = allowedFor(user, agent);
  return {
    enforced: EGRESS, base: EGRESS ? EGRESS_BASE : [], allowed,
    requested: requested.filter((h) => !allowed.includes(h)),
    refused: EGRESS ? refusedFor(user, agent).filter((h) => !allowed.includes(h)) : [],
  };
}
// you change what an agent may reach: kept in your folder, outside every sandbox; the proxy rereads it at once
function setNetwork(user: string, agent: string, body: string, res: ServerResponse) {
  let allow: unknown;
  try {
    allow = JSON.parse(body).allow;
  } catch {}
  if (!Array.isArray(allow) || !allow.every((h) => typeof h === "string" && HOST_RE.test(h)))
    return send(res, 400, "allow: a list of host names (a leading . covers subdomains)");
  mkdirSync(path.dirname(allowFile(user, agent)), { recursive: true });
  writeFileSync(allowFile(user, agent), JSON.stringify({ allow: [...new Set(allow as string[])] }, null, 1) + "\n");
  writeAllow();
  send(res, 200, { ok: true });
}

// --- routes ---------------------------------------------------------------------------------------

function route(req: IncomingMessage, res: ServerResponse) {
  // an API key: its agent's run API and nothing else (no page, no other agent, no keys)
  const api = (req.url ?? "/").split("?")[0].match(/^\/a\/([^/]+)\/api(\/.*)?$/);
  if (tokenOf(req).startsWith("pak.")) {
    if (!HOSTS.includes((req.headers.host ?? "").replace(/:\d+$/, ""))) return send(res, 403, "forbidden: host");
    const key = keyCaller(req);
    if (!api || !key || key.agent !== api[1]) return send(res, 403, "forbidden: this key calls one agent's API only");
    return void apiRoute(key.user, key.agent, api[2] ?? "/", req, res);
  }
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
  if (req.method === "GET" && url === "/agents") return void restartStale(), listAgents(user, res);
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

  // whom to talk to: prometheus (/p/…) or an agent it built (/a/<slug>/…)
  let agent: string, cwd: string, rest: string;
  let m = url.match(/^\/p(\/.*)$/);
  if (m) [agent, cwd, rest] = ["prometheus", root, m[1]];
  else if ((m = url.match(/^\/a\/([^/]+)(\/.*)$/))) {
    [agent, cwd, rest] = [`a/${m[1]}`, path.join(root, "agents", m[1]), m[2]];
    if (!SLUG.test(m[1]) || !existsSync(path.join(cwd, "plan.yaml"))) return send(res, 404, `no agent ${m[1]}`);
    if (rest === "/api" || rest.startsWith("/api/")) return void apiRoute(user, m[1], rest.slice(4) || "/", req, res);
    if (rest === "/api-keys") {
      if (req.method === "GET") return send(res, 200, keysOf(user, m[1]));
      if (req.method === "POST") return void readBody(req, 4096).then((b) => {
        const label = String(JSON.parse(b.toString("utf8") || "{}").label ?? "").trim();
        send(res, 200, newKey(user, m![1], label || "key")); // the key itself only now: only its hash is kept
      }).catch(() => send(res, 400, "the body must be JSON: {label}"));
      if (req.method === "DELETE") return revokeKey(user, m[1], q.get("id") ?? "") ? send(res, 200, { ok: true }) : send(res, 404, "no such key");
    }
    if (req.method === "GET" && rest === "/summary") return listAgents(user, res, cwd);
    if (req.method === "GET" && rest === "/network") return void networkOf(user, `a/${m[1]}`, cwd).then((n) => send(res, 200, n));
    if (req.method === "POST" && rest === "/network") return void readBody(req, MAX_EDIT).then(
      (body) => setNetwork(user, `a/${m![1]}`, body.toString("utf8"), res),
      () => send(res, 413, "too large"));
    if (req.method === "GET" && rest === "/params") return agentScript(cwd, ["plan_edit.py", "params-show"], res);
    if (req.method === "POST" && rest === "/params") return void readBody(req, MAX_EDIT).then(
      (body) => agentScript(cwd, ["plan_edit.py", "params", "--values", body.toString("utf8")], res),
      () => send(res, 413, "too large"));
    if (req.method === "GET" && rest === "/outputs") return outputs(cwd, res);
    if (req.method === "GET" && rest === "/download") {
      return sendFile(inAgent(cwd, q.get("path") ?? ""), res);
    }
    if (req.method === "GET" && rest === "/tree") return listAgentFolder(cwd, q.get("dir") ?? "", res);
    if (req.method === "GET" && rest === "/file") return readAgentFile(cwd, q.get("path") ?? "", res);
    if (req.method === "GET" && rest === "/changes") return void changes(cwd, res);
    if (req.method === "GET" && rest === "/diff") return void diffOf(cwd, q.get("path") ?? "", res);
    if (req.method === "POST" && rest === "/discard") return void discardEdits(cwd, q.get("path") ?? "", res);
    if (req.method === "POST" && rest === "/checkout") return void checkoutBranch(cwd, q.get("name") ?? "", res);
    if (req.method === "POST" && rest === "/discard-branch") return discardBranch(user, cwd, q.get("name") ?? "", res);
    if (req.method === "PUT" && rest === "/file") return saveAgentFile(user, cwd, q.get("path") ?? "", req, res);
  } else return send(res, 404, "not found");
  // files attached in a chat: an agent's go to your files folder (files/<slug>/: they outlive runs and prunes, and
  // every sandbox reads it), the planner's to inputs/ in prometheus's folder (not in git), of which the page sees only that
  if (rest === "/upload") {
    const base = agent === "prometheus" ? path.join(cwd, "inputs") : path.join(filesOf(user), agent.slice(2));
    if (req.method === "PUT") return upload(base, q.get("path") ?? "", req, res, q.get("unique") === "1");
    if (req.method === "DELETE") {
      const p = inside(base, q.get("path") ?? "");
      if (!p || p === base) return send(res, 400, "bad path");
      rmSync(p, { recursive: true, force: true });
      return send(res, 200, { ok: true });
    }
  }
  if (agent === "prometheus" && req.method === "GET" && (rest === "/tree" || rest === "/download")) {
    const base = path.join(cwd, "inputs");
    mkdirSync(base, { recursive: true });
    const rel = q.get(rest === "/tree" ? "dir" : "path") ?? "";
    const at = inside(cwd, rel);
    if (!at || !inside(base, path.relative(base, at))) return send(res, 404, "not found"); // links resolved too
    return rest === "/tree" ? listAgentFolder(cwd, rel, res) : sendFile(inAgent(cwd, rel), res);
  }
  if (req.method === "GET" && rest === "/wave") return waveInfo(cwd, q.get("id") ?? "", res);
  if (req.method === "GET" && rest === "/wave/child") return childLog(cwd, q.get("id") ?? "", res);
  if (rest.startsWith("/term/")) return terminal(user, agent, cwd, rest.slice(6), q, req, res);
  if (req.method === "GET" && rest === "/sessions") return void listSessions(user, agent, cwd, res);
  const del = rest.match(/^\/sessions\/([\w-]+)$/);
  if (req.method === "DELETE" && del) return void deleteSession(user, agent, cwd, del[1], res);
  const ren = rest.match(/^\/sessions\/([\w-]+)\/name$/);
  if (req.method === "POST" && ren) return void renameSession(user, agent, cwd, ren[1], req, res);
  // a conversation: /…/s/<session id or new-…>/<agent path>
  const conv = rest.match(/^\/s\/([\w-]+)(\/.*)$/);
  const [w, p, session] = conv ? [workerFor(user, agent, cwd, conv[1]), conv[2], conv[1]] : [workerFor(user, agent, cwd), rest, ""];
  if (req.method === "POST" && p === "/agent" && !w.up) return void historyFirst(w, req, res, p, cwd, session);
  return void forward(w, req, res, p);
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
  console.log(SANDBOX === "off" ? "WARNING: SANDBOX=off, workers run with your full access"
    : SANDBOX === "podman" ? `sandbox: rootless podman${EGRESS ? ", the internet by allow-list" : ""}`
    : `sandbox: bubblewrap${LIMITS ? ", with memory/CPU limits" : " (no systemd user session: no memory/CPU limits)"}`);
  if (!USER_HEADER) console.log(`open ${ORIGINS[0]}/?token=${TOKEN}`);
});
