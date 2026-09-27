// Load a workflow extension with a mock pi host and drive it.
// Usage: node --experimental-strip-types ext-harness.mjs <entry index.ts> <cwd> <steps.json>
// steps: [{cmd:"workflow-list", args:""}, {tool:"workflow_check", params:{...}}, {toolCall:{toolName, input}}, {env:"NAME"}, ...]
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import { Value } from "typebox/value";

const [, , entry, cwd, stepsFile] = process.argv;
const commands = {}, tools = {};
const handlers = {};
const pi = {
  on(ev, h) { (handlers[ev] ??= []).push(h); return () => {}; },
  registerCommand(name, def) { commands[name] = def; },
  registerTool(tool) { tools[tool.name] = tool; },
  async exec(command, args, opts = {}) {
    const r = spawnSync(command, args, { cwd: opts.cwd, encoding: "utf8", timeout: opts.timeout });
    return { stdout: r.stdout ?? "", stderr: r.stderr ?? "", code: r.status ?? 1, killed: !!r.signal };
  },
  sendMessage(m, o) { out.push({ kind: "shown", triggerTurn: o?.triggerTurn, text: m.content }); },
  sendUserMessage(t) { out.push({ kind: "prompt", text: t }); },
};
let out = [];
const ctx = { cwd, isIdle: () => true, ui: { notify: (m, t) => out.push({ kind: "notify", type: t, text: m }) } };

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
  report.steps.push(rec);
}
console.log(JSON.stringify(report, null, 1));
