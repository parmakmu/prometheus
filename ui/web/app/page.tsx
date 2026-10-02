"use client";

import { useCallback, useEffect, useState } from "react";
import { useAui, AuiProvider, AuiConfig, Suggestions } from "@assistant-ui/react";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { Flame, FolderOpen, MoreHorizontal, PlusIcon, RefreshCw, SquareTerminal } from "lucide-react";
import { AskCard, CheckCard, ModelsCard, PiPanelCard, QuestionCard, ScaffoldCard, SubagentCard } from "./cards";
import { CanvasPanel, CanvasProvider, useCanvas } from "./canvas";
import { StatusBar } from "./status-bar";
import { api } from "./api";
import { useAgent } from "./agent";
import { Conversations } from "./conversations";
import { FilesPanel, Outputs } from "./files";
import { TerminalPanel } from "./terminal";
import { SlashMenu } from "./slash-menu";


type Run = { id: string; status: string; started_at?: string; finished_at?: string; note?: string | null; cost_usd: number };
type Workflow = {
  slug: string;
  state: string;
  status: string;
  revision: number;
  goal: string;
  runs: number;
  stale: boolean;
  open_questions: string[];
  unmerged: string[];
  recent_runs: Run[];
};

// what each state means for the person, and whether it waits for them
const STATES: Record<string, { label: string; tone: string; needsYou?: boolean }> = {
  planning: { label: "Being planned", tone: "bg-amber-100 text-amber-800" },
  planned: { label: "Ready for its first run", tone: "bg-emerald-100 text-emerald-800" },
  ready: { label: "Ready", tone: "bg-emerald-100 text-emerald-800" },
  between_waves: { label: "Running", tone: "bg-sky-100 text-sky-800" },
  wave_running: { label: "Running", tone: "bg-sky-100 text-sky-800" },
  replan_draft: { label: "Adjusting its plan", tone: "bg-sky-100 text-sky-800" },
  branch_review: { label: "Waiting for your review", tone: "bg-violet-100 text-violet-800", needsYou: true },
  change_draft: { label: "Change in progress", tone: "bg-amber-100 text-amber-800" },
  change_ready: { label: "Change waiting for your review", tone: "bg-violet-100 text-violet-800", needsYou: true },
  main_draft: { label: "Needs a revise", tone: "bg-amber-100 text-amber-800", needsYou: true },
};
const stateOf = (w: Workflow) => STATES[w.state] ?? { label: w.state, tone: "bg-muted" };
const needsYou = (w: Workflow) => !!stateOf(w).needsYou || w.open_questions.length > 0;

const money = (n: number) => (n ? `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}` : "$0");
const when = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const took = (r: Run) => {
  if (!r.started_at || !r.finished_at) return "";
  const s = Math.round((+new Date(r.finished_at) - +new Date(r.started_at)) / 1000);
  return s < 60 ? `${s} s` : `${Math.round(s / 60)} min`;
};

function useWorkflows() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Workflow[] | { error?: string }>("/workflows")
      .then((d) => (Array.isArray(d) ? (setWorkflows(d), setError(null)) : setError(d.error ?? "unexpected answer")))
      .catch((e: Error) => setError(e.message.includes("refused") ? e.message : "Can't reach the agent server. Start it with `npm run ui:server`."));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);
  return { workflows, error, load };
}

function RunsTable({ runs }: { runs: (Run & { slug?: string })[] }) {
  return (
    <div className="bg-card overflow-hidden rounded-xl border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-muted-foreground text-xs">
          <tr>
            {[runs.some((r) => r.slug) && "Workflow", "Run", "Started", "Took", "Status", "Cost"].filter(Boolean).map((h) => (
              <th key={h as string} className="px-3 py-2 text-left font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.length === 0 && (
            <tr>
              <td colSpan={6} className="text-muted-foreground px-3 py-6 text-center">No runs yet</td>
            </tr>
          )}
          {runs.map((r) => (
            <tr key={`${r.slug}/${r.id}`} className="border-t">
              {r.slug && <td className="px-3 py-2 font-mono text-xs">{r.slug}</td>}
              <td className="px-3 py-2 font-mono text-xs">{r.id}</td>
              <td className="px-3 py-2 text-xs">{when(r.started_at)}</td>
              <td className="px-3 py-2 text-xs">{took(r)}</td>
              <td className="px-3 py-2">
                <span className={`rounded-full px-2 py-0.5 text-xs ${r.status === "done" ? "bg-emerald-100 text-emerald-800" : r.status === "active" ? "bg-sky-100 text-sky-800" : "bg-muted"}`}>{r.status}</span>
              </td>
              <td className="px-3 py-2 text-xs text-emerald-700">{money(r.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// a workflow on the canvas: what you see when you select it on the left
function WorkflowDetail({ w, open }: { w: Workflow; open: () => void }) {
  const s = stateOf(w);
  return (
    <div>
      <div className="from-primary/90 to-primary/60 text-primary-foreground rounded-2xl bg-gradient-to-br p-5 shadow">
        <div className="flex items-start justify-between">
          <span className="font-mono">{w.slug}</span>
          <span className="rounded-full bg-white/20 px-2 py-0.5 text-xs">revision {w.revision}</span>
        </div>
        <p className="mt-6 text-sm opacity-90">{w.goal}</p>
      </div>
      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        <span className={`rounded-full px-2 py-0.5 font-medium ${s.tone}`}>{s.label}</span>
        {w.open_questions.length > 0 && <span className="rounded-full bg-violet-100 px-2 py-0.5 text-violet-800">{w.open_questions.length} open question(s)</span>}
        {w.unmerged.length > 0 && <span className="rounded-full bg-violet-100 px-2 py-0.5 text-violet-800">{w.unmerged.length} change(s) to review</span>}
        {w.stale && <span className="bg-muted rounded-full px-2 py-0.5">update available</span>}
      </div>
      <button type="button" onClick={open} className="bg-primary text-primary-foreground mt-4 rounded-lg px-3 py-1.5 text-sm">
        Open this workflow
      </button>
      <h3 className="mt-6 mb-2 text-sm font-semibold">Results</h3>
      <Outputs slug={w.slug} />
      <h3 className="mt-6 mb-2 text-sm font-semibold">Runs ({w.runs})</h3>
      <RunsTable runs={w.recent_runs} />
    </div>
  );
}

function Sidebar({ workflows, error, load }: ReturnType<typeof useWorkflows>) {
  const aui = useAui();
  const { show } = useCanvas();
  const { agent, setAgent } = useAgent();
  const entry = (active: boolean) => `w-full rounded-lg px-3 py-2 text-left ${active ? "bg-accent" : "hover:bg-accent/60"}`;
  return (
    <aside className="bg-muted/30 flex w-64 shrink-0 flex-col border-r">
      <div className="flex items-center gap-2 px-4 py-4 font-semibold">
        <Flame className="text-primary size-5" /> prometheus
      </div>

      <div className="flex items-center justify-between px-4 pb-1 text-xs">
        <span className="text-muted-foreground">Workflows</span>
        <span className="flex gap-2">
          <button type="button" onClick={load} title="Refresh" className="text-muted-foreground hover:text-foreground">
            <RefreshCw className="size-3.5" />
          </button>
          <button type="button" onClick={() => aui.thread().composer().setText("/workflow-new ")} title="New workflow" className="text-muted-foreground hover:text-foreground">
            <PlusIcon className="size-3.5" />
          </button>
        </span>
      </div>
      <div className="max-h-[50%] overflow-y-auto px-2">
        <button type="button" onClick={() => setAgent("/p")} className={entry(agent === "/p")}>
          <span className="flex items-center gap-2 text-sm font-medium">
            <Flame className="text-primary size-3.5" /> prometheus
          </span>
          <span className="text-muted-foreground text-xs">plans new workflows</span>
        </button>
        {error && <p className="px-2 text-xs text-red-600">{error}</p>}
        {workflows.map((w) => (
          <button
            key={w.slug}
            type="button"
            onClick={() => {
              setAgent(`/w/${w.slug}`);
              show({ key: `workflow:${w.slug}`, title: w.slug, node: <WorkflowDetail w={w} open={() => setAgent(`/w/${w.slug}`)} /> });
            }}
            className={entry(agent === `/w/${w.slug}`)}
          >
            <span className="flex items-center gap-2 text-sm">
              <span className="truncate font-mono">{w.slug}</span>
              {needsYou(w) && <span className="ml-auto size-2 shrink-0 rounded-full bg-violet-500" title="Waiting for you" />}
            </span>
            <span className="text-muted-foreground text-xs">{stateOf(w).label}</span>
          </button>
        ))}
      </div>

      <div className="px-2 pt-2">
        <button type="button" onClick={() => show({ key: "files", title: "Files", node: <FilesPanel /> })} className="hover:bg-accent/60 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm">
          <FolderOpen className="text-muted-foreground size-4" /> Files
        </button>
      </div>

      <Conversations />
    </aside>
  );
}

// the canvas when nothing is selected: what waits for you, and the recent runs of all workflows
function Overview({ workflows }: { workflows: Workflow[] }) {
  const waiting = workflows.filter(needsYou);
  const runs = workflows
    .flatMap((w) => w.recent_runs.map((r) => ({ ...r, slug: w.slug })))
    .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""))
    .slice(0, 8);
  const spent = runs.reduce((n, r) => n + r.cost_usd, 0);
  return (
    <div>
      <p className="text-muted-foreground text-xs tracking-wide uppercase">Overview</p>
      <h2 className="mt-1 text-xl font-semibold">{waiting.length ? `${waiting.length} waiting for you` : "All quiet"}</h2>
      <div className="mt-4 grid grid-cols-3 gap-3">
        {[
          ["Workflows", String(workflows.length)],
          ["Recent runs", String(runs.length)],
          ["Their cost", money(spent)],
        ].map(([k, v]) => (
          <div key={k} className="bg-card rounded-xl border p-3">
            <p className="text-muted-foreground text-xs">{k}</p>
            <p className="mt-1 text-lg font-semibold">{v}</p>
          </div>
        ))}
      </div>
      {waiting.length > 0 && (
        <ul className="mt-4 space-y-2">
          {waiting.map((w) => (
            <li key={w.slug} className="rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-sm">
              <span className="font-mono">{w.slug}</span>: {stateOf(w).label.toLowerCase()}
            </li>
          ))}
        </ul>
      )}
      <h3 className="mt-6 mb-2 text-sm font-semibold">Recent runs</h3>
      <RunsTable runs={runs} />
    </div>
  );
}

// whom the chat talks to: prometheus, or a workflow's own agent (with its state)
function ChatHeader({ workflows }: { workflows: Workflow[] }) {
  const { agent, slug, label } = useAgent();
  const { show } = useCanvas();
  const aui = useAui();
  const [menu, setMenu] = useState(false);
  const run = (command: string) => (setMenu(false), aui.thread().append(command));
  const actions: [string, () => void][] = [
    ["Conversation info", () => run("/session")],
    ["Rename…", () => {
      const name = prompt("Name this conversation");
      if (name?.trim()) run(`/name ${name.trim()}`);
      else setMenu(false);
    }],
    ["Reload extensions and skills", () => run("/reload")],
  ];
  const w = workflows.find((x) => x.slug === slug);
  return (
    <div className="flex items-center gap-2 border-b px-4 py-2.5 text-sm">
      <Flame className="text-primary size-4" />
      <span className="font-medium">{label}</span>
      <span className="text-muted-foreground text-xs">{slug ? "workflow agent" : "planner"}</span>
      {w && <span className={`ml-auto rounded-full px-2 py-0.5 text-xs ${stateOf(w).tone}`}>{stateOf(w).label}</span>}
      <button
        type="button"
        onClick={() => show({ key: `terminal:${agent}`, title: `Terminal: ${label}`, node: <TerminalPanel agent={agent} /> })}
        title="pi in a terminal, for what only the terminal can do"
        className={`hover:bg-accent flex items-center gap-1 rounded-md border px-2 py-1 text-xs ${w ? "" : "ml-auto"}`}
      >
        <SquareTerminal className="size-3.5" /> Terminal
      </button>
      <div className="relative">
        <button type="button" onClick={() => setMenu((m) => !m)} title="More" className="hover:bg-accent rounded-md border px-2 py-1 text-xs">
          <MoreHorizontal className="size-3.5" />
        </button>
        {menu && (
          <div className="bg-popover absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-lg border py-1 shadow-lg">
            {actions.map(([title, act]) => (
              <button key={title} type="button" onClick={act} className="hover:bg-accent block w-full px-3 py-1.5 text-left text-sm">
                {title}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Layout() {
  const data = useWorkflows();
  return (
    <div className="flex h-dvh">
      <Sidebar {...data} />
      <section className="relative flex min-w-0 flex-1 flex-col">
        <ChatHeader workflows={data.workflows} />
        <div className="min-h-0 flex-1">
          <Thread />
        </div>
        <QuestionCard />
        <SlashMenu />
        <StatusBar />
      </section>
      <CanvasPanel overviewTitle="Overview" overview={<Overview workflows={data.workflows} />} />
    </div>
  );
}

export default function Home() {
  const aui = useAui();
  const { slug } = useAgent();
  const config = AuiConfig({
    suggestions: Suggestions(
      slug
        ? [
            { title: "Run it", label: "start or continue a run", prompt: "/workflow-run" },
            { title: "Where it stands", label: "plan, runs, what waits", prompt: "/workflow-status" },
          ]
        : [
            { title: "List workflows", label: "and where each stands", prompt: "/workflow-list" },
            { title: "Plan a workflow", label: "emails to a spreadsheet", prompt: "/workflow-new I want to read the emails in a folder and save them in an Excel file" },
          ],
    ),
  });
  return (
    <AuiProvider extends={aui} config={config}>
      <CanvasProvider>
        <ScaffoldCard />
        <CheckCard />
        <SubagentCard />
        <PiPanelCard />
        <AskCard />
        <ModelsCard />
        <Layout />
      </CanvasProvider>
    </AuiProvider>
  );
}
