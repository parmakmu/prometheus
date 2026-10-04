"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ChevronRight, Circle, Loader2, XCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "./api";
import { useAgent } from "./agent";
import { useCanvas } from "./canvas";

// A launch's subagents, as pi-subagents records them (GET /…/wave): a wave of a run, or the plan critic. The chat
// shows one live line per launch, opening to one row per subagent; a subagent opens its log in the drawer.
export type Child = {
  key: string; agent?: string; state?: string; runId?: string | null; startedAt?: number | null; endedAt?: number | null; cost?: number | null;
  model?: string | null; lastTool?: string | null; lastArgs?: string | null; toolCount?: number | null; tokens?: number | null;
  // a command child (no model): the program it ran and the gate after it, or a map stage's split / merge
  program?: { op?: string; script?: string | null; command?: string | null; gate?: string | null; exit?: number | null; gateExit?: number | null };
};
const isProgram = (c: Child) => !!c.program || c.agent === "workflow-batcher";
type Wave = { id: string; state: string; startedAt: number | null; endedAt: number | null; cost: number | null; children: Child[] };

const DONE = new Set(["complete", "completed", "done", "succeeded"]);
const FAILED = new Set(["failed", "error", "cancelled", "canceled", "stopped"]);
const over = (state?: string) => !!state && (DONE.has(state) || FAILED.has(state));
export const money = (n?: number | null) => (n ? `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}` : "");
const took = (from?: number | null, to?: number | null) => {
  if (!from) return "";
  const ms = Math.max(0, (to ?? Date.now()) - from);
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`; // a program takes a fraction of a second: not "0s"
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};

export function StateIcon({ state }: { state?: string }) {
  if (state && DONE.has(state)) return <CheckCircle2 className="size-3.5 shrink-0 text-emerald-600" />;
  if (state && FAILED.has(state)) return <XCircle className="size-3.5 shrink-0 text-red-600" />;
  if (state === "queued" || state === "pending") return <Circle className="text-muted-foreground size-3.5 shrink-0" />;
  return <Loader2 className="size-3.5 shrink-0 animate-spin text-sky-600" />;
}

// A subagent's work in the drawer, read like the main agent's Details: its task (folded), then its thoughts, tool
// calls (a line each, opening to the call and its result) and text, in order; refreshed while it runs.
type Step = {
  kind: "task" | "thought" | "text" | "tool" | "program"; text?: string; name?: string; args?: string; result?: string | null; failed?: boolean;
  command?: string | null; gate?: string | null; exit?: number | null; stdout?: string; stderr?: string; gateExit?: number | null; gateOutput?: string;
};

// a command child's work: the program's command and output, then the gate's
function ProgramStep({ s }: { s: Step }) {
  const ok = (code?: number | null) => (code === 0 ? <CheckCircle2 className="size-3.5 text-emerald-600" /> : code == null ? null : <XCircle className="size-3.5 text-red-600" />);
  return (
    <div className="space-y-3 text-sm">
      <div>
        <p className="flex items-center gap-1.5 font-medium">{ok(s.exit)} Program {s.exit != null && <span className="text-muted-foreground text-xs font-normal">exit {s.exit}</span>}</p>
        {s.command && <pre className="bg-muted mt-1 overflow-auto rounded-lg p-2 text-xs whitespace-pre-wrap">{s.command}</pre>}
        {(s.stdout || s.stderr) && <pre className="bg-muted/60 mt-1 max-h-80 overflow-auto rounded-lg p-2 text-xs whitespace-pre-wrap">{[s.stdout, s.stderr].filter(Boolean).join("\n")}</pre>}
      </div>
      {s.gate && (
        <div>
          <p className="flex items-center gap-1.5 font-medium">{ok(s.gateExit)} Gate {s.gateExit != null && <span className="text-muted-foreground text-xs font-normal">{s.gateExit === 0 ? "passed" : `failed (exit ${s.gateExit})`}</span>}</p>
          <pre className="bg-muted mt-1 overflow-auto rounded-lg p-2 text-xs whitespace-pre-wrap">{s.gate}</pre>
          {s.gateOutput && <pre className="bg-muted/60 mt-1 max-h-60 overflow-auto rounded-lg p-2 text-xs whitespace-pre-wrap">{s.gateOutput}</pre>}
        </div>
      )}
    </div>
  );
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed [&_code]:bg-muted [&_code]:rounded [&_code]:px-1 [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ms-4 [&_ol]:list-decimal [&_pre]:bg-muted [&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:p-2 [&_table]:text-xs [&_td]:border [&_td]:px-1.5 [&_th]:border [&_th]:px-1.5 [&_ul]:list-disc">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function ToolStep({ s }: { s: Step }) {
  const [open, setOpen] = useState(false);
  let summary = "";
  try {
    const a = JSON.parse(s.args ?? "{}");
    summary = String(a.command ?? a.path ?? a.pattern ?? a.file ?? Object.values(a)[0] ?? "");
  } catch {}
  return (
    <div className="text-sm">
      <button type="button" onClick={() => setOpen((o) => !o)} className="text-muted-foreground hover:text-foreground flex w-full items-center gap-1.5 text-left">
        {s.failed ? <XCircle className="size-3.5 shrink-0 text-red-600" /> : <CheckCircle2 className="size-3.5 shrink-0" />}
        <span className="text-foreground font-medium">{s.name}</span>
        <span className="truncate font-mono text-xs">{summary.split("\n")[0]}</span>
        <ChevronRight className={`ml-auto size-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <div className="mt-1 space-y-1.5 ps-5">
          <pre className="bg-muted overflow-auto rounded-lg p-2 text-xs whitespace-pre-wrap">{s.args}</pre>
          {s.result !== null && s.result !== undefined && <pre className="bg-muted/60 max-h-80 overflow-auto rounded-lg p-2 text-xs whitespace-pre-wrap">{s.result || "(no output)"}</pre>}
        </div>
      )}
    </div>
  );
}

function ChildLog({ agentPath, runId, live }: { agentPath: string; runId: string; live: boolean }) {
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [task, setTask] = useState(false);
  useEffect(() => {
    const load = () => void api<{ steps: Step[] }>(`${agentPath}/wave/child?id=${runId}`).then((d) => setSteps(d.steps), () => setSteps([]));
    load();
    if (!live) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [agentPath, runId, live]);
  if (steps === null) return null;
  if (!steps.length) return <p className="text-muted-foreground text-sm">Nothing recorded yet.</p>;
  return (
    <div className="space-y-3">
      {steps.map((s, i) =>
        s.kind === "task" ? (
          <div key={i}>
            <button type="button" onClick={() => setTask((t) => !t)} className="text-muted-foreground hover:text-foreground flex items-center gap-1.5 text-sm">
              Its task <ChevronRight className={`size-3.5 transition-transform ${task ? "rotate-90" : ""}`} />
            </button>
            {task && <pre className="bg-muted mt-1 max-h-96 overflow-auto rounded-lg p-2 text-xs whitespace-pre-wrap">{s.text}</pre>}
          </div>
        ) : s.kind === "thought" ? (
          <p key={i} className="text-muted-foreground border-s-2 ps-3 text-sm whitespace-pre-wrap italic">{s.text}</p>
        ) : s.kind === "tool" ? (
          <ToolStep key={i} s={s} />
        ) : s.kind === "program" ? (
          <ProgramStep key={i} s={s} />
        ) : (
          <Markdown key={i} text={s.text ?? ""} />
        ),
      )}
    </div>
  );
}

// opens a subagent's log in the drawer
export function useOpenChild() {
  const { agent } = useAgent();
  const { show, select } = useCanvas();
  return (c: Child) => {
    if (!c.runId) return;
    const key = `subagent:${c.runId}`;
    show({ key, title: `${c.key}${c.agent ? ` · ${c.agent}` : ""}`, node: <ChildLog agentPath={agent} runId={c.runId} live={!over(c.state)} /> });
    select(key);
  };
}

const kilo = (n?: number | null) => (n ? (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)) : "");

// one subagent as Claude Code shows its own: the step and its agent, then tool uses · tokens · time · cost, and
// under it, while it works, what it is doing now (its latest tool call)
export function ChildRow({ c }: { c: Child }) {
  const open = useOpenChild();
  if (isProgram(c)) return <ProgramRow c={c} open={() => open(c)} />;
  const stats = [c.toolCount ? `${c.toolCount} tool use${c.toolCount === 1 ? "" : "s"}` : "", c.tokens ? `${kilo(c.tokens)} tokens` : "", took(c.startedAt, c.endedAt), money(c.cost)].filter(Boolean);
  return (
    <button type="button" disabled={!c.runId} onClick={() => open(c)} className="hover:bg-accent/60 block w-full rounded-md px-2 py-1 text-left text-xs disabled:cursor-default">
      <span className="flex items-center gap-2">
        <StateIcon state={c.state} />
        <span className="font-mono font-medium">{c.key}</span>
        {c.agent && <span className="text-muted-foreground">({c.agent})</span>}
        {stats.length > 0 && <span className="text-muted-foreground ml-auto shrink-0">{stats.join(" · ")}</span>}
      </span>
      {!over(c.state) && c.lastTool && (
        <span className="text-muted-foreground ms-1.5 mt-0.5 flex gap-1.5 font-mono">
          <span>└</span>
          <span className="truncate">
            {c.lastTool}
            {c.lastArgs ? ` ${c.lastArgs}` : ""}
          </span>
        </span>
      )}
    </button>
  );
}

// a subagent as a word in a line (the workspace's Steps): its name, ✓ when done, ✗ when failed; it opens its log
export function ChildName({ c }: { c: Child }) {
  const open = useOpenChild();
  const mark = c.state && DONE.has(c.state) ? " ✓" : c.state && FAILED.has(c.state) ? " ✗" : "…";
  return (
    <button type="button" disabled={!c.runId} onClick={() => open(c)} className={`font-mono hover:underline disabled:no-underline ${c.state && FAILED.has(c.state) ? "text-red-600" : ""}`}>
      {c.key}
      {mark}
    </button>
  );
}

// a command child: "program" where an agent's name would be, the script it ran, whether the gate passed, its time
function ProgramRow({ c, open }: { c: Child; open: () => void }) {
  const p = c.program ?? {};
  const what = p.op && p.op !== "run" ? p.op : p.script ?? "its program";
  const gate = p.gateExit == null ? "" : p.gateExit === 0 ? "gate passed" : "gate failed";
  return (
    <button type="button" disabled={!c.runId} onClick={open} className="hover:bg-accent/60 flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs disabled:cursor-default">
      <StateIcon state={c.state} />
      <span className="font-mono font-medium">{c.key}</span>
      <span className="text-muted-foreground">program</span>
      <span className="text-muted-foreground ml-auto shrink-0">{[<span key="s" className="font-mono">{what}</span>, gate, took(c.startedAt, c.endedAt)].filter(Boolean).flatMap((x, i) => (i ? [" · ", x] : [x]))}</span>
    </button>
  );
}

// the chat's line for one launch: live while it runs, then what it took
export function WaveLine({ id, title }: { id: string; title: string }) {
  const { agent } = useAgent();
  const [wave, setWave] = useState<Wave | null>(null);
  const [open, setOpen] = useState(false);
  const [, tick] = useState(0); // the running time moves even between polls
  useEffect(() => {
    let stop = false;
    const load = () =>
      api<Wave>(`${agent}/wave?id=${id}`).then(
        (w) => {
          setWave(w);
          if (!stop && !over(w.state)) setTimeout(load, 2000);
        },
        () => !stop && setTimeout(load, 5000), // not recorded yet, or a restart: try again
      );
    void load();
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => ((stop = true), clearInterval(t));
  }, [agent, id]);
  const running = !wave || !over(wave.state);
  const n = wave?.children.length ?? 0;
  const programs = wave?.children.filter(isProgram).length ?? 0;
  const agents = n - programs;
  const count = [agents && `${agents} subagent${agents === 1 ? "" : "s"}`, programs && `${programs} program${programs === 1 ? "" : "s"}`].filter(Boolean).join(", ");
  return (
    <div className="my-1.5 w-full max-w-(--thread-max-width)">
      {/* a plain line, as Cowork's "Working on 1 background task · In progress 2m 39s" */}
      <button type="button" onClick={() => setOpen((o) => !o)} className="text-muted-foreground hover:text-foreground flex items-center gap-1.5 text-left text-sm">
        <StateIcon state={wave?.state ?? "running"} />
        <span className="text-foreground/90">{running ? `Working on ${title.charAt(0).toLowerCase()}${title.slice(1)}` : title}</span>
        <span className="text-xs">{[n ? count : running ? "starting" : "", running ? `in progress ${took(wave?.startedAt, null)}` : took(wave?.startedAt, wave?.endedAt), money(wave?.cost)].filter(Boolean).join(" · ")}</span>
        <ChevronRight className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
      </button>
      {open && <div className="mt-1 border-s ps-2">{n ? wave!.children.map((c) => <ChildRow key={c.key} c={c} />) : <p className="text-muted-foreground px-2 py-1 text-xs">No subagents yet.</p>}</div>}
    </div>
  );
}
