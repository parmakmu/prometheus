// Runs one wave of a workflow. compile_wave.py writes the wave as data, `const W = {...};`,
// and appends this file unchanged, so every compiled wave runs the same code.
//
// W: {wave, revision, max_parallel, steps}. Each step is one of
//   {layer: [item]}   children launched together with runs.all, max_parallel at a time
//   {map: {...}}      split (command runner) -> one child per batch -> merge or reduce
//   {review: item}    a fresh reviewer; its structured `blocking` stops the wave
// An item is a pi-subagents child. Its `task` is text, or parts [{text} | {result: key} |
// {failure: key}] that splice in the result, or the error, of a child launched earlier in this
// wave; `resumeFrom: key` resumes that child. A layer item with `fix` runs a stage's program as
// a command (no model); only if that fails is `fix`, a model child, launched to repair it. A
// layer item with `reuse` completed in the wave's last launch and is unchanged: its earlier
// result stands in and it is not launched again (compile_wave.py, reuse_finished).
//
// pi-subagents runs this as a script body: top-level await and return, no nested async
// functions. A child result has `ok` (no status): runs.all returns a failed child as
// {ok: false}, runs.run throws (caught below).

const R = {};
function failed(r) { return !r || r.ok !== true; }
function outcome(r) { return r.ok ? "completed" : r.stopped ? "stopped" : r.detached ? "paused" : "failed"; }
function summary() {
  const o = {};
  for (const k of Object.keys(R)) {
    const r = R[k] || {};
    o[k] = { runId: r.runId ?? null, status: outcome(r), outputReference: r.outputReference ?? null, structuredOutput: r.structuredOutput ?? null, reused: r.reused === true };
  }
  return o;
}
function stop(status, keys) { return { wave: W.wave, revision: W.revision, status: status, at: keys, children: summary() }; }
function text(task) {
  if (typeof task === "string") return task;
  return task.map((p) => (p.result !== undefined ? JSON.stringify(R[p.result].structuredOutput ?? R[p.result].output)
    : p.failure !== undefined ? String(R[p.failure].error || R[p.failure].output || "(no error text)")
    : p.text)).join("");
}
/** The item as pi-subagents takes it: task as text, resumeFrom as the resumed child's run id. */
function child(item) {
  const { resumeFrom, fix, reuse, ...c } = item;
  c.task = text(item.task);
  if (resumeFrom !== undefined) c.resume = R[resumeFrom].runId;
  return c;
}
/** runs.run takes the key separately from the params. */
function params(item) { const { key, ...p } = child(item); return p; }
function batchOutput(m, b) { return m.outdir + "/" + b.index + m.ext; }
function batchChild(m, b) {
  const c = { key: m.key + "-" + b.index, label: m.title + " — batch " + b.index, agent: m.agent, cwd: m.cwd,
              task: text(m.task) + "\n\nYour batch file (one item per line): " + b.file + "\nWrite your batch result to: " + batchOutput(m, b),
              output: batchOutput(m, b) };
  if (m.context) c.context = m.context;
  return c;
}

try {
  for (const step of W.steps) {
    if (step.review) {
      const k = step.review.key;
      R[k] = await runs.run(k, params(step.review));
      if (failed(R[k])) return stop("failed", [k]);
      if (R[k].structuredOutput && R[k].structuredOutput.blocking) return stop("blocked", [k]);
      continue;
    }
    const m = step.map;
    let items, batches;
    if (m) {
      R[m.split.key] = await runs.run(m.split.key, params(m.split));
      if (failed(R[m.split.key])) return stop("failed", [m.split.key]);
      batches = JSON.parse(R[m.split.key].output).batches;
      items = batches.map((b) => batchChild(m, b));
    } else {
      for (const it of step.layer.filter((x) => x.reuse)) {
        R[it.key] = { ok: true, reused: true, runId: it.reuse.runId, outputReference: it.reuse.outputReference, structuredOutput: it.reuse.structuredOutput };
      }
      items = step.layer.filter((x) => !x.reuse).map(child);
    }
    for (let i = 0; i < items.length; i += W.max_parallel) {
      const part = await runs.all(items.slice(i, i + W.max_parallel));
      part.forEach((r, j) => { R[items[i + j].key] = r; });
    }
    let bad = items.map((it) => it.key).filter((k) => failed(R[k]));
    // a stage program that failed as a command: its fix child repairs it and runs it again
    const fixes = (step.layer || []).filter((it) => it.fix && bad.includes(it.key)).map((it) => child(it.fix));
    if (fixes.length) {
      const fixed = await runs.all(fixes);
      fixed.forEach((r, j) => { R[fixes[j].key] = r; });
      bad = bad.map((k) => (R[k + "-fix"] ? k + "-fix" : k)).filter((k) => failed(R[k]));
    }
    if (bad.length) return stop("failed", bad);
    if (m) {
      const outputs = batches.map((b) => batchOutput(m, b));
      const last = m.pattern === "map"
        ? { key: m.key + "-merge", agent: m.split.agent, label: "Merge batches: " + m.key, cwd: m.cwd,
            task: "BATCH_TOOL_REQUEST " + JSON.stringify({ op: "merge", inputs: outputs, output: m.final }) }
        : { key: m.key + "-reduce", label: m.title + " — reduce", agent: m.agent, cwd: m.cwd,
            task: text(m.reduce_task) + "\n\nThis is the reduce step. Combine these batch results into the output above: " + outputs.join(", "),
            output: m.final };
      R[last.key] = await runs.run(last.key, params(last));
      if (failed(R[last.key])) return stop("failed", [last.key]);
    }
  }
} catch (e) {
  return { wave: W.wave, revision: W.revision, status: "failed", error: String((e && e.message) || e), children: summary() };
}
return { wave: W.wave, revision: W.revision, status: "done", children: summary() };
