/** What pi-subagents records about a launch (a wave, the critic) and its children, as the page shows them. */
import { existsSync, readdirSync, readFileSync, realpathSync, statSync } from "node:fs";
import { type ServerResponse } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { thoughtOf } from "./thoughts.ts";
import { SANDBOX, SUBAGENTS_ROOT, send, sessionDirOf } from "./common.ts";
// --- subagents: what pi-subagents records about a launch (a wave, the critic) and its children ----------------

// where the agents' pi-subagents keep their async runs: the sandboxed workers' root, else pi-subagents' default
export const subagentRuns = () =>
  path.join(process.env.PI_SUBAGENTS_TEMP_ROOT ?? (SANDBOX === "off" ? path.join(tmpdir(), `pi-subagents-uid-${process.getuid?.() ?? 0}`) : SUBAGENTS_ROOT), "async-subagent-runs");

// a run's status.json, only if the run worked inside this agent's folder (so one user's page never reads another's)
export function subagentStatus(cwd: string, id: string): any | null {
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

export const costOf = (st: any) => st?.totalCost?.costUsd ?? null;

// A command child (the batcher runner: no model) runs a reviewed program, then the stage's gate, or splits
// or merges a map stage. Its request is in the compiled wave (the data at the start of runs/<id>/.compiled/<wave>.js,
// found by the child's key; the child runs in runs/<id>/work), its result in its output log: {program: {exit, stdout, stderr}, gate: {exit}}.
// A compiled wave's data: `const W = {...};` (JSON, indented, so its closing brace is the first "\n};\n") before the runner.
const waveOf = (script: string) => JSON.parse(script.slice(script.indexOf("const W = ") + "const W = ".length, script.indexOf("\n};\n") + 2));

export function commandOf(cwd: string, childCwd: string | undefined, key: string): { op: string; script: string | null; command: string | null; gate: string | null } | null {
  if (!childCwd) return null;
  const compiled = path.join(path.dirname(childCwd), ".compiled");
  if (!existsSync(compiled) || !realpathSync(compiled).startsWith(realpathSync(cwd))) return null;
  for (const f of readdirSync(compiled).filter((n) => n.endsWith(".js"))) {
    try {
      const items = waveOf(readFileSync(path.join(compiled, f), "utf8")).steps.flatMap((s: any) => s.layer ?? []);
      const item = items.find((i: any) => i.key === key && typeof i.task === "string" && i.task.startsWith("BATCH_TOOL_REQUEST "));
      if (!item) continue;
      const r = JSON.parse(item.task.slice("BATCH_TOOL_REQUEST ".length));
      const script = String(r.command ?? "").match(/\/(scripts\/[^\s/]+\.py)\b/)?.[1] ?? null;
      return { op: r.op ?? "run", script, command: r.command ?? null, gate: r.gate ?? null };
    } catch {}
  }
  return null;
}
export const resultOf = (id: string) => {
  try {
    return JSON.parse(readFileSync(path.join(subagentRuns(), id, "output-0.log"), "utf8"));
  } catch {
    return null;
  }
};

// pi-subagents' record of a launch lives in the temp folder, which macOS empties on a restart. What outlasts it: the
// launch in runs/<id>/run.yaml, and per child its artifacts in the folder's sessions (subagent-artifacts/<child run
// id>_<agent>_meta.json: agent, model, exit code, cost; _transcript.jsonl: its messages; _output.md: a command
// child's result).
export function artifactOf(cwd: string, id: string) {
  if (!/^[\w-]{8,64}$/.test(id)) return null;
  const dir = path.join(sessionDirOf(cwd), "subagent-artifacts");
  const meta = existsSync(dir) ? readdirSync(dir).find((f) => f.startsWith(`${id}_`) && f.endsWith("_meta.json")) : undefined;
  if (!meta) return null;
  const base = path.join(dir, meta.slice(0, -"_meta.json".length));
  const json = (f: string) => { try { return JSON.parse(readFileSync(f, "utf8")); } catch { return null; } };
  const m = json(path.join(dir, meta)) ?? {};
  return { agent: m.agent as string | undefined, model: m.model ?? null, exitCode: m.exitCode as number | undefined, cost: m.usage?.cost ?? null, transcript: `${base}_transcript.jsonl`, result: () => json(`${base}_output.md`) };
}

// Each launch is a "  - wave:" item of run.yaml's launches, its fields indented four spaces, its children a map of
// key: child run id. A child with no artifacts shows as done when the launch was, else unknown.
export function launchOf(cwd: string, id: string) {
  if (!/^[\w-]{8,64}$/.test(id)) return null;
  const runs = path.join(cwd, "runs");
  if (!existsSync(runs)) return null;
  for (const run of readdirSync(runs)) {
    const f = path.join(runs, run, "run.yaml");
    if (!existsSync(f)) continue;
    const launch = readFileSync(f, "utf8").split(/^  - /m).find((b: string) => new RegExp(`^    subagent_run_id: ['"]?${id}['"]?$`, "m").test(b));
    if (!launch) continue;
    const field = (name: string) => launch.match(new RegExp(`^    ${name}: ['"]?([^'"\\n]*)['"]?$`, "m"))?.[1] ?? null;
    const time = (name: string) => { const t = Date.parse(field(name) ?? ""); return Number.isNaN(t) ? null : t; };
    const state = field("status") ?? "unknown";
    const children = [...(launch.match(/^    children:\n((?: {6}.*\n?)*)/m)?.[1] ?? "").matchAll(/^ {6}([\w-]+): ['"]?([\w-]*)/gm)].map(([, key, runId]) => {
      const a = artifactOf(cwd, runId);
      const program = a?.agent === "batcher" ? a.result() : null;
      return {
        key, agent: a?.agent, runId: a ? runId : null, model: a?.model ?? null, cost: a?.cost ?? null,
        state: a?.exitCode != null ? (a.exitCode === 0 ? "done" : "failed") : state === "done" ? "done" : "unknown",
        ...(a?.agent === "batcher" ? { program: { ...commandOf(cwd, path.join(runs, run, "work"), key), exit: program?.program?.exit ?? null, gateExit: program?.gate?.exit ?? null } } : {}),
      };
    });
    return { id, state, startedAt: time("started_at"), endedAt: time("finished_at"), cost: Number(field("cost_usd")) || null, children };
  }
  return null;
}

// the step key and run folder of a child, from the launch in run.yaml that lists its run id
export function childOf(cwd: string, id: string) {
  const runs = path.join(cwd, "runs");
  for (const run of existsSync(runs) ? readdirSync(runs) : []) {
    const f = path.join(runs, run, "run.yaml");
    const key = existsSync(f) ? readFileSync(f, "utf8").match(new RegExp(`^ {6}([\\w-]+): ['"]?${id}['"]?$`, "m"))?.[1] : undefined;
    if (key) return { key, work: path.join(runs, run, "work") };
  }
  return null;
}

// a launch and its children so far: state, times (ms), cost, and per child its step key, agent, model, last tool
export function waveInfo(cwd: string, id: string, res: ServerResponse) {
  const st = subagentStatus(cwd, id);
  if (!st) {
    const launch = launchOf(cwd, id);
    return launch ? send(res, 200, launch) : send(res, 404, "no such run here");
  }
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
      ...(c.agent === "batcher" ? { program: { ...commandOf(cwd, cs?.cwd, c.childId), exit: resultOf(c.runId)?.program?.exit ?? null, gateExit: resultOf(c.runId)?.gate?.exit ?? null } } : {}),
    };
  });
  send(res, 200, { id, state: st.state, startedAt: st.startedAt ?? null, endedAt: st.endedAt ?? null, cost: costOf(st), children });
}

// one subagent's work, read from its own Pi session so the page can show it like the main agent's: its task, then
// each thought, tool call (with its result) and text, in order. A command runner (batcher) has no session:
// then its log's summary. Thoughts as the worker shows them (thoughts.ts).
export const MAX_STEP = 4000;
export const clip = (t: string) => (t.length > MAX_STEP ? `${t.slice(0, MAX_STEP)}\n…` : t);
export const textIn = (content: any) => (typeof content === "string" ? content : Array.isArray(content) ? content.map((p: any) => (p?.type === "text" ? p.text : "")).join("") : "");

// a subagent's messages (lines of its Pi session, or of its transcript artifact: both carry `message`) as steps
export function stepsOf(file: string) {
  const steps: any[] = [];
  const results = new Map<string, any>();
  const lines = readFileSync(file, "utf8").split("\n").filter(Boolean).map((l: string) => { try { return JSON.parse(l); } catch { return null; } });
  for (const e of lines) if (e?.message?.role === "toolResult") results.set(e.message.toolCallId, e.message);
  for (const e of lines) {
    const m = e?.message;
    if (!m || e.sourceEventType === "initial_prompt") continue; // a transcript's first line: the task, redacted (it follows whole)
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
  return steps;
}

// a command child: what it ran and how that went
export const programStep = (cmd: ReturnType<typeof commandOf>, done: any) => ({
  kind: "program", command: cmd?.command ?? null, gate: cmd?.gate ?? null, exit: done.program?.exit ?? null, stdout: clip(String(done.program?.stdout ?? "")),
  stderr: clip(String(done.program?.stderr ?? "")), gateExit: done.gate?.exit ?? null, gateOutput: clip(String(done.gate?.stdout ?? done.gate?.stderr ?? "")),
});

export function childLog(cwd: string, id: string, res: ServerResponse) {
  const st = subagentStatus(cwd, id);
  if (!st) {
    // pi-subagents' record is gone: the child's artifacts
    const a = artifactOf(cwd, id);
    if (!a) return send(res, 404, "no such run here");
    const done = a.agent === "batcher" ? a.result() : null;
    if (done && (done.program || done.gate)) {
      const child = childOf(cwd, id);
      return send(res, 200, { steps: [programStep(child && commandOf(cwd, child.work, child.key), done)] });
    }
    return send(res, 200, { steps: existsSync(a.transcript) ? stepsOf(a.transcript) : [] });
  }
  const sessionFile = st.steps?.[0]?.sessionFile;
  const inside = (f: string) => { const r = realpathSync(cwd); const p = realpathSync(f); return p.startsWith(r + path.sep); };
  if (sessionFile && existsSync(sessionFile) && inside(sessionFile)) return send(res, 200, { steps: stepsOf(sessionFile) });
  const done = resultOf(id);
  if (done && (done.program || done.gate)) {
    const key = (st.parentWorkflowRunId && subagentStatus(cwd, st.parentWorkflowRunId)?.workflowChildren?.children?.find((c: any) => c.runId === id)?.childId) ?? "";
    return send(res, 200, { steps: [programStep(commandOf(cwd, st.cwd, key), done)] });
  }
  const dir = path.join(subagentRuns(), id);
  const file = readdirSync(dir).find((f) => f.startsWith("subagent-log-") && f.endsWith(".md")) ?? (existsSync(path.join(dir, "output-0.log")) ? "output-0.log" : null);
  const text = file ? readFileSync(path.join(dir, file), "utf8") : "";
  const summary = text.includes("## Summary") ? text.slice(text.indexOf("## Summary") + "## Summary".length).trim() : text;
  send(res, 200, { steps: summary ? [{ kind: "text", text: clip(summary) }] : [] });
}

// an agent's results: every file under runs/<id>/outputs/, newest run first
export function outputs(cwd: string, res: ServerResponse) {
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
