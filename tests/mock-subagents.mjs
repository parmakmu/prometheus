// Mock pi-subagents runtime for compiled workflow scripts.
// Usage: node harness.mjs <compiled.json> [--fail KEY] [--block KEY]
// - batcher children run the real batch_tool.py (stdin = task, cwd = item cwd)
// - other children write placeholder files for every "- id: /abs/path" under "Write:",
//   plus their `output`; return structuredOutput built from outputSchema
// - acceptance.verify commands run on the host in the child's cwd; output: "json" ones parse stdout
// - acceptance must not require git evidence about the user's repo (preserveStagedIndex) or an
//   implicit review (review.required false); gate is the compiler's old form and is rejected
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const [, , compiledPath, ...flags] = process.argv;
const failKey = flags.includes("--fail") ? flags[flags.indexOf("--fail") + 1] : null;
const blockKey = flags.includes("--block") ? flags[flags.indexOf("--block") + 1] : null;
const compiled = JSON.parse(fs.readFileSync(compiledPath, "utf8"));
// mirrors pi-subagents: a `workflow` value containing "/" is a script file path,
// read from disk, so it behaves exactly like inline script text
const script = compiled.call.workflow
  ? fs.readFileSync(compiled.call.workflow, "utf8")
  : compiled.call.workflowScript;
const log = [];
let n = 0;

function sample(schema) {
  if (!schema) return null;
  if (schema.type === "object") { const o = {}; for (const [k, v] of Object.entries(schema.properties || {})) o[k] = sample(v); return o; }
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  if (schema.type === "number" || schema.type === "integer") return 0;
  return "x";
}

function writePlaceholder(p, key) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const ext = path.extname(p);
  const body = ext === ".json" ? JSON.stringify([{ mock: key }]) : ext === ".csv" ? "a,b\n1,2\n" : "placeholder\n";
  fs.writeFileSync(p, body);
}

function run(key, opts) {
  const runId = `run-${++n}`;
  const entry = { key, agent: opts.agent ?? null, model: opts.model ?? null, resume: opts.resume ?? null, cwd: opts.cwd, output: opts.output ?? null,
                  acceptance: opts.acceptance ?? null, outputSchema: !!opts.outputSchema, context: opts.context ?? null,
                  task: opts.task };
  log.push(entry);
  if (!opts.agent && !opts.resume) throw new Error(`${key}: neither agent nor resume`);
  if (opts.agent && opts.resume) throw new Error(`${key}: agent and resume together`);
  if (opts.gate) throw new Error(`${key}: plain gate implies inferred writer acceptance; compile acceptance instead`);
  const verify = opts.acceptance?.verify ?? [];
  if (opts.acceptance && (opts.acceptance.preserveStagedIndex !== true || opts.acceptance.review?.required !== false))
    throw new Error(`${key}: acceptance must set preserveStagedIndex: true and review.required: false`);
  if (verify.some((v) => v.output === "json") && opts.outputSchema) throw new Error(`${key}: typed verify + outputSchema (pi rejects this)`);
  if (key === failKey) return { key, runId, ok: false, state: "failed", error: "mock failure" };
  if (opts.agent === "batcher") {
    const r = spawnSync("python3", ["../../../.pi/skills/agent/scripts/batch_tool.py"], { cwd: opts.cwd, input: "agent text\n" + opts.task, encoding: "utf8" });
    if (r.status !== 0) return { key, runId, ok: false, state: "failed", error: r.stdout + r.stderr };
    return { key, runId, ok: true, state: "complete", output: r.stdout };
  }
  // placeholder outputs named in the task's Write: section
  const writeSection = (opts.task.split("\nWrite:\n")[1] || "").split("\n\n")[0];
  for (const line of writeSection.split("\n")) {
    const m = line.match(/^- [^:]+: (\/[^\s(;]+)/);
    if (m && !opts.output) writePlaceholder(m[1], key);
    else if (m && opts.task.includes("Your batch file")) { /* batch child writes only its batch output */ }
    else if (m) writePlaceholder(m[1], key);
  }
  if (opts.output) writePlaceholder(opts.output, key);
  let structuredOutput = opts.outputSchema ? sample(opts.outputSchema) : null;
  if (key === blockKey && structuredOutput && "blocking" in structuredOutput) structuredOutput.blocking = true;
  for (const v of verify) {
    const g = spawnSync("sh", ["-c", v.command], { cwd: opts.cwd, encoding: "utf8" });
    entry.gateExit = g.status;
    if (g.status !== 0) return { key, runId, ok: false, state: "failed", error: `acceptance rejected: ${v.id} failed: ${g.stderr}` };
    if (v.output === "json") structuredOutput = JSON.parse(g.stdout);
  }
  return { key, runId, ok: true, state: "complete", output: "ok", outputReference: opts.output ?? null, structuredOutput };
}

// results have pi-subagents' shape ({ok, state, error, output, outputReference, structuredOutput}):
// runs.run throws on a failed child, runs.all (config objects) returns it as {ok: false}
const runs = {
  run: async (key, opts) => {
    const r = run(key, opts);
    if (!r.ok) throw new Error(`Run '${key}' failed: ${r.error}`);
    return r;
  },
  all: async (items) => items.map((it) => run(it.key, it)),
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const fn = new AsyncFunction("runs", script);
const result = await fn(runs);
console.log(JSON.stringify({ result, log }, null, 1));
