"use client";

import { isValidElement, type ReactNode, useEffect, useState } from "react";
import { CheckCircle2, ChevronRight, Circle, Loader2, XCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "./api";
import { useAgent } from "./agent";
import { useCanvas } from "./canvas";
import { CodeBlock } from "./code-editor";
import { PRIVATE_THOUGHT, StepRow, Thought } from "@/components/assistant-ui/elements/steps";
import { kilo, money, took } from "./format";

// A launch's subagents, as pi-subagents records them (GET /…/wave): a wave of a run, or the plan critic. The chat
// shows one live line per launch, opening to one row per subagent; a subagent opens its log in the drawer.
export type Child = {
  key: string; agent?: string; state?: string; runId?: string | null; startedAt?: number | null; endedAt?: number | null; cost?: number | null;
  model?: string | null; lastTool?: string | null; lastArgs?: string | null; toolCount?: number | null; tokens?: number | null;
  // a command child (no model): the program it ran and the gate after it, or a map stage's split / merge
  program?: { op?: string; script?: string | null; command?: string | null; gate?: string | null; exit?: number | null; gateExit?: number | null };
};
const isProgram = (c: Child) => !!c.program || c.agent === "batcher";
type Wave = { id: string; state: string; startedAt: number | null; endedAt: number | null; cost: number | null; children: Child[] };

const DONE = new Set(["complete", "completed", "done", "succeeded"]);
const FAILED = new Set(["failed", "error", "cancelled", "canceled", "stopped", "blocked"]); // blocked: a launch as run.yaml records it
const over = (state?: string) => !!state && (DONE.has(state) || FAILED.has(state));

export function StateIcon({ state }: { state?: string }) {
  if (state && DONE.has(state)) return <CheckCircle2 className="size-3.5 shrink-0 text-emerald-600" />;
  if (state && FAILED.has(state)) return <XCircle className="size-3.5 shrink-0 text-red-600" />;
  if (state === "queued" || state === "pending" || state === "unknown") return <Circle className="text-muted-foreground size-3.5 shrink-0" />;
  return <Loader2 className="size-3.5 shrink-0 animate-spin text-sky-600" />;
}

// A subagent's work in the drawer, read like the main agent's Details (the same thought and step lines): its task
// (folded), then its thoughts, tool calls (a line each in words, opening to the call and its result) and notes, in
// order, then its answer; refreshed while it runs.
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

// The acceptance report pi-subagents asks a child for (a fenced `acceptance-report` JSON block ending its answer), as
// a card: each criterion met or not with its evidence, then what it changed and ran and what it noticed.
type Report = {
  criteriaSatisfied?: { id?: string; status?: string; evidence?: string }[]; diffSummary?: string; changedFiles?: string[]; testsAddedOrUpdated?: string[];
  commandsRun?: { command?: string; result?: string; summary?: string }[]; validationOutput?: string[]; reviewFindings?: string[]; residualRisks?: string[]; manualNotes?: string;
};
const inAgent = (p: string) => p.replace(/^.*\/agents\/[^/]+\//, ""); // a path as the agent's folder sees it

function AcceptanceReport({ r }: { r: Report }) {
  const list = (label: string, items?: string[]) =>
    items?.length ? (
      <div>
        <p className="text-muted-foreground text-xs font-medium">{label}</p>
        <ul className="ms-4 list-disc">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
      </div>
    ) : null;
  return (
    <div className="bg-muted/30 space-y-3 rounded-xl border p-3 text-sm">
      <p className="font-medium">Acceptance report</p>
      {r.criteriaSatisfied?.map((c, i) => (
        <p key={i} className="flex gap-2">
          {c.status === "satisfied" ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-600" /> : <XCircle className="mt-0.5 size-3.5 shrink-0 text-red-600" />}
          <span>
            {c.evidence}
            {c.status !== "satisfied" && <span className="text-muted-foreground"> ({c.status})</span>}
          </span>
        </p>
      ))}
      {r.diffSummary && <p>{r.diffSummary}</p>}
      {r.changedFiles?.length ? (
        <div>
          <p className="text-muted-foreground text-xs font-medium">Files changed</p>
          {r.changedFiles.map((f) => <p key={f} title={f} className="font-mono text-xs">{inAgent(f)}</p>)}
        </div>
      ) : null}
      {list("Tests added or updated", r.testsAddedOrUpdated)}
      {r.commandsRun?.length ? (
        <div>
          <p className="text-muted-foreground text-xs font-medium">Commands run</p>
          {r.commandsRun.map((c, i) => (
            <p key={i}>
              <span className="font-mono text-xs">{c.command}</span>
              {c.result && <span className={c.result === "passed" ? "text-emerald-700" : "text-red-600"}> · {c.result}</span>}
              {c.summary && <span className="text-muted-foreground block">{c.summary}</span>}
            </p>
          ))}
        </div>
      ) : null}
      {list("Checks", r.validationOutput)}
      {list("Review findings", r.reviewFindings)}
      {list("Residual risks", r.residualRisks)}
      {r.manualNotes && (
        <div>
          <p className="text-muted-foreground text-xs font-medium">Notes</p>
          <p>{r.manualNotes}</p>
        </div>
      )}
    </div>
  );
}

// a fenced block in markdown: the <pre> around one <code class="language-…">, drawn read-only in CodeMirror (an
// acceptance report as its card)
function MarkdownCode({ children }: { children?: ReactNode }) {
  const code = isValidElement<{ className?: string; children?: ReactNode }>(children) ? children.props : { children };
  const language = code.className?.match(/language-(\S+)/)?.[1];
  const text = String(code.children ?? "");
  if (language === "acceptance-report") {
    try {
      return <AcceptanceReport r={JSON.parse(text)} />;
    } catch {} // not JSON (yet, or at all): as code
  }
  return <CodeBlock language={language === "acceptance-report" ? "json" : language} code={text} />;
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed [&_code]:bg-muted [&_code]:rounded [&_code]:px-1 [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ms-4 [&_ol]:list-decimal [&_table]:text-xs [&_td]:border [&_td]:px-1.5 [&_th]:border [&_th]:px-1.5 [&_ul]:list-disc">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ pre: MarkdownCode }}>{text}</ReactMarkdown>
    </div>
  );
}

const argsOf = (text?: string): Record<string, unknown> => {
  try {
    return JSON.parse(text ?? "{}");
  } catch {
    return {};
  }
};

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
  // its answer: the text it ended with, below its steps (as the main agent's reply sits below its Details)
  const last = steps.length - 1;
  const answer = steps[last].kind === "text" ? last : -1;
  return (
    <div className="space-y-1.5">
      {steps.map((s, i) =>
        s.kind === "task" ? (
          <div key={i} className="mb-3">
            <button type="button" onClick={() => setTask((t) => !t)} className="text-muted-foreground hover:text-foreground flex items-center gap-1.5 text-sm">
              Its task <ChevronRight className={`size-3.5 transition-transform ${task ? "rotate-90" : ""}`} />
            </button>
            {task && <pre className="bg-muted mt-1 max-h-96 overflow-auto rounded-lg p-2 text-xs whitespace-pre-wrap">{s.text}</pre>}
          </div>
        ) : s.kind === "thought" ? (
          (s.text ?? "").startsWith(PRIVATE_THOUGHT) ? null : <Thought key={i} text={s.text ?? ""} />
        ) : s.kind === "tool" ? (
          <StepRow key={i} toolName={s.name ?? ""} args={argsOf(s.args)} result={s.result ?? undefined} running={false} failed={!!s.failed} />
        ) : s.kind === "program" ? (
          <ProgramStep key={i} s={s} />
        ) : i === answer ? (
          <div key={i} className="mt-4 border-t pt-4">
            <Markdown text={s.text ?? ""} />
          </div>
        ) : (
          <p key={i} className="text-muted-foreground text-sm whitespace-pre-wrap">{s.text}</p>
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
