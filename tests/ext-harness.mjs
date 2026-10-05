// Load an agent extension with a mock pi host and drive it.
// Usage: node --experimental-strip-types ext-harness.mjs <entry index.ts> <cwd> <steps.json>
// steps: [{cmd:"agent-list", args:""}, {systemPrompt:"..."}, {tool:"agent_check", params:{...}}, {toolCall:{toolName, input}}, {env:"NAME"}, ...]
// With FAKE_SUBAGENTS_RPC=<mock-subagents.mjs>, pi-subagents' RPC answers ping and spawn: a spawned wave
// runs in the mock at once and its status.json is written under $PI_SUBAGENTS_TEMP_ROOT (FAKE_SUBAGENTS_DEAD: as
// still running, under a process that is gone).
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { Value } from "typebox/value";

const [, , entry, cwd, stepsFile] = process.argv;
const commands = {}, tools = {};
const handlers = {};
// pi-subagents in a fresh session: subagent registered but inactive, its loader active
let active = ["read", "bash", "subagents_enable"];
const pi = {
  getAllTools() { return [...Object.keys(tools), "subagent", "subagents_enable"].map((name) => ({ name })); },
  getActiveTools() { return [...active]; },
  setActiveTools(names) { active = [...names]; },
  on(ev, h) { (handlers[ev] ??= []).push(h); return () => {}; },
  registerCommand(name, def) { commands[name] = def; },
  registerTool(tool) { tools[tool.name] = tool; },
  async exec(command, args, opts = {}) {
    const r = spawnSync(command, args, { cwd: opts.cwd, encoding: "utf8", timeout: opts.timeout });
    return { stdout: r.stdout ?? "", stderr: r.stderr ?? "", code: r.status ?? 1, killed: !!r.signal };
  },
  sendMessage(m, o) { out.push({ kind: "shown", triggerTurn: o?.triggerTurn, text: m.content }); },
  sendUserMessage(t, o) { out.push({ kind: "prompt", text: t, deliverAs: o?.deliverAs }); },
};
const listeners = {};
pi.events = {
  on(ch, h) { (listeners[ch] ??= []).push(h); return () => { listeners[ch] = listeners[ch].filter((x) => x !== h); }; },
  emit(ch, data) { for (const h of [...(listeners[ch] ?? [])]) h(data); },
};
let spawned = 0;
if (process.env.FAKE_SUBAGENTS_RPC) pi.events.on("subagents:rpc:v1:request", (req) => {
  const reply = (data) => setTimeout(() => pi.events.emit(`subagents:rpc:v1:reply:${req.requestId}`, { version: 1, requestId: req.requestId, success: true, data }), 0);
  if (req.method === "ping") return reply({});
  const id = `fake-${++spawned}`;
  const dir = path.join(process.env.PI_SUBAGENTS_TEMP_ROOT, "async-subagent-runs", id);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "call.json"), JSON.stringify({ call: req.params }));
  const r = spawnSync("node", [process.env.FAKE_SUBAGENTS_RPC, path.join(dir, "call.json")], { encoding: "utf8" });
  const value = JSON.parse(r.stdout).result;
  // FAKE_SUBAGENTS_DEAD: a runner that died before recording how the wave ended (no such process)
  const record = process.env.FAKE_SUBAGENTS_DEAD ? { runId: id, state: "running", pid: 2147483646 } : { runId: id, state: "complete", workflow: { value } };
  fs.writeFileSync(path.join(dir, "status.json"), JSON.stringify(record));
  out.push({ kind: "spawn", id, status: value.status });
  reply({ details: { runId: id, asyncDir: dir } });
});
let out = [];
const ctx = { cwd, isIdle: () => true, ui: { notify: (m, t) => out.push({ kind: "notify", type: t, text: m }), setStatus: () => {} } };

const mod = await import(entry);
mod.default(pi);
for (const h of handlers.session_start ?? []) await h({ type: "session_start", reason: "startup" }, ctx);
const steps = JSON.parse(fs.readFileSync(stepsFile, "utf8"));
const report = { commands: Object.keys(commands).sort(), tools: Object.keys(tools).sort(), steps: [] };
for (const step of steps) {
  out = [];
  const rec = { step };
  try {
    if (step.env) {
      rec.env = process.env[step.env] ?? null;
    } else if (step.toolCall) {
      let res;
      for (const h of handlers.tool_call ?? []) res = (await h({ type: "tool_call", toolCallId: "t-1", ...step.toolCall }, ctx)) ?? res;
      rec.blocked = !!res?.block;
      rec.reason = res?.reason ?? null;
    } else if (step.systemPrompt !== undefined) {
      let res;
      for (const h of handlers.before_agent_start ?? []) res = (await h({ type: "before_agent_start", prompt: step.prompt ?? "", systemPrompt: step.systemPrompt }, ctx)) ?? res;
      rec.systemPrompt = res?.systemPrompt ?? null;
    } else if (step.cmd) {
      const def = commands[step.cmd];
      if (step.complete !== undefined) rec.completions = await def.getArgumentCompletions?.(step.complete);
      else await def.handler(step.args ?? "", ctx);
    } else {
      const tool = tools[step.tool];
      const params = step.params ?? {};
      rec.schemaOk = Value.Check(tool.parameters, params);
      const res = await tool.execute("call-1", params, undefined, undefined, ctx);
      rec.result = res.details;
    }
  } catch (e) {
    rec.error = String(e.message ?? e);
  }
  rec.out = out;
  rec.activeTools = [...active];
  report.steps.push(rec);
}
console.log(JSON.stringify(report, null, 1));
