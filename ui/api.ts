/** Replaced by `prometheus serve` (prometheus/serve.py: a release over HTTP and A2A, runs that pause for an answer);
 * kept while the web app is in use, not developed further (2026-10-08).
 *
 * The run API: an agent called like a service, on its own. A caller (a script, or another agent) starts a run with a
 * request, param values and files; the agent runs it in its own sandbox, in a session of its own (listed with its
 * sessions as "API · …"), with /agent-auto. Where the run needs someone (a question, an approval, a review, a message
 * from the model), the call waits in needs_input until the caller answers: questions go back to whoever called. One
 * call at a time per agent (an agent has one active run); the rest queue. Calls are kept outside every sandbox
 * (<your folder>/api/<agent>/<id>.json), as are the keys that may make them (<your folder>/api-keys.json, hashed).
 * See ui/README.md, "The run API". */
import { createHash, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { request, type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import { SLUG, folderName, ownOf, prometheusOf, readBody, send, tokenOf } from "./common.ts";
import { MAX_UPLOAD, inAgent, inside, runActive, sendFile } from "./files.ts";
import { skillScript } from "./sandbox.ts";
import { ask, workerFor, type Worker } from "./workers.ts";

// --- keys: each lets its holder call one agent's API, nothing else ------------------------------------------------

type Key = { id: string; agent: string; label: string; hash: string; created: string };
const keysFile = (user: string) => path.join(ownOf(user), "api-keys.json");
const sha = (s: string) => createHash("sha256").update(s).digest("hex");
function readKeys(user: string): Key[] {
  try {
    const keys = JSON.parse(readFileSync(keysFile(user), "utf8")).keys;
    return Array.isArray(keys) ? keys : [];
  } catch {
    return [];
  }
}
function writeKeys(user: string, keys: Key[]) {
  mkdirSync(ownOf(user), { recursive: true });
  writeFileSync(`${keysFile(user)}.tmp`, JSON.stringify({ keys }, null, 1) + "\n", { mode: 0o600 });
  renameSync(`${keysFile(user)}.tmp`, keysFile(user));
}
const now = () => new Date().toISOString();

// pak.<user, base64url>.<agent>.<id>.<secret>: the key names whose it is, so the router finds it without a search;
// only its hash is kept
export function newKey(user: string, agent: string, label: string) {
  const id = randomBytes(4).toString("hex");
  const key = `pak.${Buffer.from(user).toString("base64url")}.${agent}.${id}.${randomBytes(24).toString("base64url")}`;
  writeKeys(user, [...readKeys(user), { id, agent, label: label.slice(0, 80), hash: sha(key), created: now() }]);
  return { id, key, label };
}
export const keysOf = (user: string, agent: string) =>
  readKeys(user).filter((k) => k.agent === agent).map(({ id, label, created }) => ({ id, label, created }));
export function revokeKey(user: string, agent: string, id: string) {
  const keys = readKeys(user);
  writeKeys(user, keys.filter((k) => !(k.agent === agent && k.id === id)));
  return keys.length !== readKeys(user).length;
}
/** Whose key this request carries, and for which agent; null when it carries none that is valid. */
export function keyCaller(req: IncomingMessage): { user: string; agent: string } | null {
  const t = tokenOf(req);
  const [kind, u, agent, id] = t.split(".");
  if (kind !== "pak" || !u || !agent || !id) return null;
  const user = Buffer.from(u, "base64url").toString();
  if (!user || folderName(user) !== user) return null;
  const k = readKeys(user).find((x) => x.agent === agent && x.id === id);
  if (!k) return null;
  const [a, b] = [Buffer.from(sha(t)), Buffer.from(k.hash)];
  return a.length === b.length && timingSafeEqual(a, b) ? { user, agent } : null;
}

// --- calls ------------------------------------------------------------------------------------------------------

type Status = "queued" | "running" | "needs_input" | "done" | "failed" | "cancelled";
type Question = { id?: string; kind: string; message: string; options?: string[]; questions?: unknown };
type Call = {
  id: string; agent: string; status: Status; request: string; params: Record<string, string>; approve: string[];
  max_cost_usd?: number; files: string[]; created_at: string; started_at?: string; finished_at?: string;
  run?: string; session?: string; waiting_for?: string; question?: Question; report?: string; error?: string;
  outputs?: { path: string; size: number }[]; cost_usd?: number; cost?: { agent?: number; subagents?: number };
};
const ENDED: Status[] = ["done", "failed", "cancelled"];
const agentDir = (user: string, agent: string) => path.join(prometheusOf(user), "agents", agent);
const callsDir = (user: string, agent: string) => path.join(ownOf(user), "api", agent);
const callFile = (user: string, agent: string, id: string) => path.join(callsDir(user, agent), `${id}.json`);
const staged = (user: string, agent: string, id: string) => path.join(callsDir(user, agent), id); // its files, until its run
const CALL_ID = /^c-[a-f0-9]{12}$/;

function readCall(user: string, agent: string, id: string): Call | null {
  try {
    return CALL_ID.test(id) ? JSON.parse(readFileSync(callFile(user, agent, id), "utf8")) : null;
  } catch {
    return null;
  }
}
const callsOf = (user: string, agent: string): Call[] =>
  existsSync(callsDir(user, agent))
    ? readdirSync(callsDir(user, agent)).filter((f) => f.endsWith(".json")).map((f) => readCall(user, agent, f.slice(0, -5))).filter((c): c is Call => !!c)
    : [];

// what a call has going on while it is driven: who watches it, the answer it waits for, the turn it is in
type Live = { watchers: Set<ServerResponse>; answer?: (a: { value?: unknown; cancel?: boolean }) => void; cancelled?: boolean; turn?: () => void; worker?: Worker };
const live = new Map<string, Live>();
const liveOf = (id: string) => live.get(id) ?? (live.set(id, { watchers: new Set() }), live.get(id)!);

function save(user: string, call: Call) {
  mkdirSync(callsDir(user, call.agent), { recursive: true });
  const f = callFile(user, call.agent, call.id);
  writeFileSync(`${f}.tmp`, JSON.stringify(call, null, 1) + "\n");
  renameSync(`${f}.tmp`, f);
  for (const w of liveOf(call.id).watchers) w.write(`event: status\ndata: ${JSON.stringify(call)}\n\n`);
  if (ENDED.includes(call.status)) {
    for (const w of liveOf(call.id).watchers) w.end();
    live.delete(call.id);
  }
}

// --- driving a call: one per agent at a time, the rest wait their turn --------------------------------------------

const driving = new Set<string>(); // user/agent
const waitTimers = new Map<string, NodeJS.Timeout>();
const recovered = new Set<string>();

// Calls a router that stopped was driving: their worker went with it. Marked failed (their run stays as it was, to
// continue in the web app); queued ones go on.
function recover(user: string, agent: string) {
  const k = `${user}/${agent}`;
  if (recovered.has(k)) return;
  recovered.add(k);
  for (const c of callsOf(user, agent))
    if (c.status === "running" || c.status === "needs_input")
      save(user, { ...c, status: "failed", finished_at: now(), question: undefined,
        error: `the web app's server stopped while this call ran${c.run ? `; run ${c.run} stays as it was: continue it in the web app` : ""}` });
  pump(user, agent);
}

function pump(user: string, agent: string) {
  const k = `${user}/${agent}`;
  if (driving.has(k) || waitTimers.has(k)) return;
  const next = callsOf(user, agent).filter((c) => c.status === "queued").sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
  if (!next) return;
  // a run of its own (from its chat, or a call that failed mid-way) is active: wait for it to end
  if (runActive(agentDir(user, agent))) {
    const why = "another run of this agent is active; this call starts when it ends";
    if (next.waiting_for !== why) save(user, { ...next, waiting_for: why });
    waitTimers.set(k, setTimeout(() => (waitTimers.delete(k), pump(user, agent)), 15_000).unref());
    return;
  }
  driving.add(k);
  void drive(user, next)
    .catch((e) => {
      const now_ = readCall(user, agent, next.id) ?? next;
      if (!ENDED.includes(now_.status)) save(user, { ...now_, status: "failed", finished_at: now(), error: String(e?.message ?? e) }); // not a cancel's
    })
    .finally(() => (driving.delete(k), pump(user, agent)));
}

const json = (s: string) => {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
};
// the agent's state (agent_state.py): run_wave while a wave is out, run_ready, … ; a finished run's own status
const stateOf = async (cwd: string) => json((await skillScript(cwd, "agent_state.py", [])).out)?.state as string | undefined;
const runStatus = (runDir: string) => {
  try {
    return readFileSync(path.join(runDir, "run.yaml"), "utf8").match(/^status:\s*['"]?(\w+)/m)?.[1] ?? "";
  } catch {
    return "";
  }
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function drive(user: string, queued: Call) {
  const cwd = agentDir(user, queued.agent);
  let call: Call = { ...queued, status: "running", started_at: now(), waiting_for: undefined };
  const update = (patch: Partial<Call>) => save(user, (call = { ...call, ...patch }));
  update({});
  const me = liveOf(call.id);
  const r = await skillScript(cwd, "run_steps.py", [
    "start-run", "--note", `API: ${call.request}`,
    ...(Object.keys(call.params).length ? ["--params", JSON.stringify(call.params)] : []),
    ...(call.approve.length ? ["--approve", call.approve.join(",")] : []),
    ...(call.max_cost_usd ? ["--max-cost-usd", String(call.max_cost_usd)] : []),
  ]);
  const started = json(r.out);
  if (!started?.ok) return update({ status: "failed", finished_at: now(), error: started?.error ?? (r.err.trim().slice(-500) || "run_steps.py start-run failed") });
  const runDir: string = started.path;
  update({ run: started.run });
  // the files it sent, into the run's inputs/ (links the agent may have left there are not followed)
  const from = staged(user, call.agent, call.id);
  for (const rel of call.files) {
    const to = inside(runDir, path.join("inputs", rel));
    if (!to) return update({ status: "failed", finished_at: now(), error: `can't place ${rel} in the run's inputs` });
    mkdirSync(path.dirname(to), { recursive: true });
    cpSync(path.join(from, rel), to);
  }
  rmSync(from, { recursive: true, force: true });

  // a session of its own, in the agent's sandbox; kept up (open) while the call needs it, then it idles out
  const w = workerFor(user, `a/${call.agent}`, cwd, `new-api-${call.id}`);
  me.worker = w;
  w.open++;
  try {
    await w.ready;
    update({ session: (await ask(w.socket, "/status")).sessionId });
    await post(w.socket, "/name", { name: `API · ${call.request.replace(/\s+/g, " ").slice(0, 60)}` });
    let next: { text?: string; resume?: { interruptId: string; value: unknown } } = { text: "/agent-auto" };
    for (;;) {
      if (me.cancelled) return;
      const t = await turn(w, call.id, next);
      if (me.cancelled) return;
      if (t.error) return update({ status: "failed", finished_at: now(), error: t.error, report: t.text });
      let question: Question | undefined;
      if (t.outcome?.type === "interrupt") {
        const i = t.outcome.interrupts?.[0] ?? {};
        question = { id: i.id, kind: i.reason ?? "input", message: i.message ?? "", options: i.metadata?.options, questions: i.metadata?.question };
      } else {
        // the agent may still be at it: a hand-over's turn about to start, a wave it launched itself
        const state = await stateOf(cwd);
        if (state === "run_wave" || (await ask(w.socket, "/status").then((s) => s.busy, () => false))) {
          await sleep(10_000);
          next = {};
          continue;
        }
        const status = runStatus(runDir);
        if (status !== "active") {
          const ok = status === "done";
          return update({
            status: ok ? "done" : "failed", finished_at: now(), report: t.text, outputs: outputsOf(cwd, runDir),
            ...(await costOf(cwd, call.run!, w)), ...(ok ? {} : { error: `the run ended ${status || "without a status"}` }),
          });
        }
        question = { kind: "message", message: t.text || "The agent stopped and waits for an answer." }; // it asked in words
      }
      update({ status: "needs_input", question });
      const a = await new Promise<{ value?: unknown; cancel?: boolean }>((resolve) => (me.answer = resolve));
      me.answer = undefined;
      if (a.cancel) return;
      update({ status: "running", question: undefined });
      next = question.id ? { resume: { interruptId: question.id, value: a.value } } : { text: String(a.value ?? "") };
    }
  } finally {
    w.open = Math.max(0, w.open - 1);
    w.lastUsed = Date.now();
    me.worker = undefined;
  }
}

function post(socket: string, p: string, body: unknown) {
  return new Promise<void>((resolve) => {
    const r = request({ socketPath: socket, path: p, method: "POST", headers: { "content-type": "application/json" } }, (res) => (res.resume(), res.on("end", resolve)));
    r.on("error", () => resolve()).end(JSON.stringify(body));
  });
}

/** One AG-UI run on the worker: a message, an answer to its question, or (nothing) a wait until it settles. Its events
 * go to whoever watches the call; returns how it ended and the last thing the agent said. */
async function turn(w: Worker, callId: string, next: { text?: string; resume?: { interruptId: string; value: unknown } }) {
  const body = JSON.stringify({
    threadId: callId, runId: randomUUID(),
    messages: next.text ? [{ id: randomUUID(), role: "user", content: next.text }] : [],
    ...(next.resume ? { resume: [{ interruptId: next.resume.interruptId, status: "resolved", payload: { value: next.resume.value } }] } : {}),
  });
  for (let tries = 0; ; tries++) {
    const r = await new Promise<{ code: number; outcome?: any; error?: string; text: string }>((resolve) => {
      const texts = new Map<string, string>();
      let last = "", said = "", outcome: any, error: string | undefined, buf = "";
      const req = request({ socketPath: w.socket, path: "/agent", method: "POST", headers: { "content-type": "application/json", accept: "text/event-stream" } }, (res) => {
        if (res.statusCode !== 200) return res.resume(), res.on("end", () => resolve({ code: res.statusCode ?? 502, text: "" }));
        res.setEncoding("utf8");
        res.on("data", (chunk: string) => {
          buf += chunk;
          let at: number;
          while ((at = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, at);
            buf = buf.slice(at + 2);
            for (const w of liveOf(callId).watchers) w.write(`${frame}\n\n`);
            const e = json(frame.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n"));
            if (e?.type === "TEXT_MESSAGE_START") (last = e.messageId), texts.set(e.messageId, "");
            else if (e?.type === "TEXT_MESSAGE_CONTENT") (last = e.messageId), texts.set(e.messageId, (texts.get(e.messageId) ?? "") + e.delta);
            else if (e?.type === "MESSAGES_SNAPSHOT") said = [...(e.messages ?? [])].reverse().find((x: any) => x.role === "assistant" && typeof x.content === "string" && x.content.trim())?.content ?? said;
            else if (e?.type === "RUN_FINISHED") outcome = e.outcome;
            else if (e?.type === "RUN_ERROR") error = e.message;
          }
        });
        res.on("end", () => resolve({ code: 200, outcome, error, text: texts.get(last) || said })); // a sync says it only in its snapshot
      });
      liveOf(callId).turn = () => req.destroy();
      req.on("error", (e) => resolve({ code: 502, error: String(e), text: "" })).end(body);
    });
    // 409: a page has this session open and is syncing; its sync ends once the agent settles
    if (r.code === 409 && tries < 90) {
      await sleep(2_000);
      continue;
    }
    return r.code === 200 ? r : { ...r, error: r.error ?? `the agent's worker answered ${r.code}` };
  }
}

const outputsOf = (cwd: string, runDir: string) => {
  const o = path.join(runDir, "outputs");
  if (!existsSync(o)) return [];
  return (readdirSync(o, { recursive: true }) as string[])
    .map((f) => ({ f, s: statSync(path.join(o, f)) }))
    .filter(({ f, s }) => s.isFile() && inAgent(cwd, path.relative(cwd, path.join(o, f))))
    .map(({ f, s }) => ({ path: f.split(path.sep).join("/"), size: s.size }));
};
// what the call cost: the model that drove it (its session is the call's own) and the run's subagents
async function costOf(cwd: string, run: string, w: Worker) {
  const runs = json((await skillScript(cwd, "run_history.py", ["show", "--last", "20"])).out)?.runs ?? [];
  const subagents: number | undefined = runs.find((r: any) => r.id === run)?.cost_usd;
  const agent: number | undefined = await ask(w.socket, "/status").then((s) => s.cost, () => undefined);
  return { cost_usd: (agent ?? 0) + (subagents ?? 0), cost: { agent, subagents } };
}

// --- routes: /a/<agent>/api/… (the user, or a key for that agent) -----------------------------------------------

const MAX_CALL = Math.ceil(MAX_UPLOAD * 1.4); // files come base64 in the JSON
const SAFE_REL = (p: string) => !!p && !path.isAbsolute(p) && !p.split(/[\\/]/).some((s) => s === ".." || s === "");

async function card(cwd: string) {
  const c = json((await skillScript(cwd, "plan_edit.py", ["card"])).out);
  if (c) delete c.next;
  return c;
}

export async function apiRoute(user: string, agent: string, p: string, req: IncomingMessage, res: ServerResponse) {
  const cwd = agentDir(user, agent);
  if (!SLUG.test(agent) || !existsSync(path.join(cwd, "plan.yaml"))) return send(res, 404, `no agent ${agent}`);
  recover(user, agent);
  if (req.method === "GET" && p === "/") {
    const c = await card(cwd);
    return c?.ok ? send(res, 200, { agent, ...c }) : send(res, 500, "the agent's plan can't be read");
  }
  if (req.method === "GET" && p === "/runs")
    return send(res, 200, callsOf(user, agent).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 100));
  if (req.method === "POST" && p === "/runs") return start(user, agent, cwd, req, res);
  const m = p.match(/^\/runs\/(c-[a-f0-9]{12})(\/.*)?$/);
  const call = m ? readCall(user, agent, m[1]) : null;
  if (!m || !call) return send(res, 404, "no such call");
  const sub = m[2] ?? "";
  if (req.method === "GET" && sub === "") return send(res, 200, call);
  if (req.method === "GET" && sub === "/events") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    res.write(`event: status\ndata: ${JSON.stringify(call)}\n\n`);
    if (ENDED.includes(call.status)) return res.end();
    const me = liveOf(call.id);
    me.watchers.add(res);
    res.on("close", () => me.watchers.delete(res));
    return;
  }
  if (req.method === "POST" && sub === "/answer") {
    const me = live.get(call.id);
    if (call.status !== "needs_input" || !me?.answer) return send(res, 409, `the call is ${call.status}: nothing to answer`);
    let body: any;
    try {
      body = JSON.parse((await readBody(req, 1024 * 1024)).toString("utf8") || "{}");
    } catch {
      return send(res, 400, "the body must be JSON: {answer}");
    }
    if (!("answer" in body)) return send(res, 400, "the body must be JSON: {answer}");
    me.answer({ value: body.answer });
    return send(res, 200, { ok: true });
  }
  if (req.method === "GET" && sub.startsWith("/outputs/") && call.run)
    return sendFile(inAgent(cwd, path.join("runs", call.run, "outputs", decodeURIComponent(sub.slice(9)))), res);
  if (req.method === "DELETE" && sub === "") return void cancel(user, call, cwd, res);
  return send(res, 404, "not found");
}

async function start(user: string, agent: string, cwd: string, req: IncomingMessage, res: ServerResponse) {
  let body: any;
  try {
    body = JSON.parse((await readBody(req, MAX_CALL)).toString("utf8") || "{}");
  } catch (e) {
    return send(res, (e as Error).message === "too large" ? 413 : 400, (e as Error).message === "too large" ? "too large" : "the body must be JSON");
  }
  const request = typeof body.request === "string" ? body.request.trim() : "";
  if (!request || request.length > 4000) return send(res, 400, "request: what this run is for, 1 to 4000 characters");
  const params = body.params ?? {};
  if (typeof params !== "object" || Array.isArray(params) || !Object.values(params).every((v) => ["string", "number", "boolean"].includes(typeof v)))
    return send(res, 400, "params: {name: value} of plain values");
  const files = body.files ?? {};
  if (typeof files !== "object" || Array.isArray(files) || !Object.entries(files).every(([k, v]) => SAFE_REL(k) && typeof v === "string"))
    return send(res, 400, "files: {path inside the run's inputs/: base64 content}");
  const max = body.max_cost_usd;
  if (max !== undefined && !(typeof max === "number" && max > 0)) return send(res, 400, "max_cost_usd: a positive number");
  // checked now against what the agent takes, not when its turn comes
  const c = await card(cwd);
  if (!c?.ok) return send(res, 500, "the agent's plan can't be read");
  if (!c.ready) return send(res, 409, "the agent's plan isn't ready to run");
  const names = new Map<string, string>(c.params.map((x: any) => [x.name, x.type]));
  const bad = Object.keys(params).filter((n) => !names.has(n) || names.get(n) === "secret");
  if (bad.length) return send(res, 400, `params the agent doesn't take (secrets are set on the agent, never sent): ${bad.join(", ")}`);
  const approvable: string[] = c.approvals.map((a: any) => a.stage);
  const approve: string[] = body.approve === true ? approvable : Array.isArray(body.approve) ? body.approve.map(String) : [];
  if (approve.some((a) => !approvable.includes(a))) return send(res, 400, `approve: stages that ask for approval: ${approvable.join(", ") || "none"} (or true for all)`);

  const id = `c-${randomBytes(6).toString("hex")}`;
  for (const [rel, b64] of Object.entries(files) as [string, string][]) {
    const to = path.join(staged(user, agent, id), rel);
    mkdirSync(path.dirname(to), { recursive: true });
    writeFileSync(to, Buffer.from(b64, "base64"));
  }
  const call: Call = {
    id, agent, status: "queued", request, approve, files: Object.keys(files), created_at: now(),
    params: Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])), ...(max ? { max_cost_usd: max } : {}),
  };
  save(user, call);
  send(res, 202, { id, status: "queued", url: `/a/${agent}/api/runs/${id}` });
  pump(user, agent);
}

async function cancel(user: string, call: Call, cwd: string, res: ServerResponse) {
  if (ENDED.includes(call.status)) return send(res, 409, `the call is ${call.status} already`);
  const me = liveOf(call.id);
  me.cancelled = true;
  me.turn?.();
  me.answer?.({ cancel: true });
  let note = "";
  if (call.status !== "queued") {
    me.worker?.proc.kill("SIGTERM");
    const r = json((await skillScript(cwd, "run_steps.py", ["stop", "--note", "cancelled by the API caller"])).out);
    if (!r?.ok) note = `run ${call.run} stays active: ${r?.error ?? "it couldn't be stopped"}`;
  }
  rmSync(staged(user, call.agent, call.id), { recursive: true, force: true });
  save(user, { ...(readCall(user, call.agent, call.id) ?? call), status: "cancelled", finished_at: now(), question: undefined, ...(note ? { error: note } : {}) });
  send(res, 200, { ok: true, ...(note ? { note } : {}) });
}
