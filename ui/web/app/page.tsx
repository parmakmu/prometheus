"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useAui, useAuiState, AuiProvider, AuiConfig, Suggestions } from "@assistant-ui/react";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ChevronRight, Flame, MoreHorizontal, PlusIcon, RefreshCw, SquareTerminal, Bot as AgentIcon } from "lucide-react";
import { AskCard, CheckCard, ModelsCard, PiPanelCard, QuestionCard, ScaffoldCard, SubagentCard } from "./cards";
import { CanvasPanel, useCanvas } from "./canvas";
import { StatusBar } from "./status-bar";
import { api } from "./api";
import { useAgent } from "./agent";
import { Sessions, sidebarRow } from "./sessions";
import { Workspace, costSplit, runCost, type Run, type AgentSummary } from "./workspace";
import { References } from "./references";
import { TerminalPanel } from "./terminal";
import { SlashMenu } from "./slash-menu";
import { money, took } from "./format";


type Agent = AgentSummary & {
  status: string;
  revision: number;
  goal: string;
  runs: number;
  stale: boolean;
  open_questions: string[];
  recent_runs: Run[];
};

// what each state means for the person, and whether it waits for them
const STATES: Record<string, { label: string; tone: string; needsYou?: boolean }> = {
  plan_draft: { label: "Being planned", tone: "bg-amber-100 text-amber-800" },
  plan_ready: { label: "Ready for its first run", tone: "bg-emerald-100 text-emerald-800" },
  main_ready: { label: "Ready", tone: "bg-emerald-100 text-emerald-800" },
  run_ready: { label: "Running", tone: "bg-sky-100 text-sky-800" },
  run_wave: { label: "Running", tone: "bg-sky-100 text-sky-800" },
  run_draft: { label: "Adjusting its plan", tone: "bg-sky-100 text-sky-800" },
  run_review: { label: "Waiting for your review", tone: "bg-violet-100 text-violet-800", needsYou: true },
  change_draft: { label: "Change in progress", tone: "bg-amber-100 text-amber-800" },
  change_ready: { label: "Change waiting for your review", tone: "bg-violet-100 text-violet-800", needsYou: true },
  main_draft: { label: "Needs a revise", tone: "bg-amber-100 text-amber-800", needsYou: true },
  other_branch: { label: "On a branch it didn't make", tone: "bg-amber-100 text-amber-800", needsYou: true },
};
const stateOf = (w: Agent) => STATES[w.state] ?? { label: w.state, tone: "bg-muted" };
const needsYou = (w: Agent) => !!stateOf(w).needsYou || w.open_questions.length > 0;

const when = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

// The page remounts on every switch of agent or conversation (AgentRuntime is keyed by it): the list is kept here
// so the sidebar starts from what it last showed instead of empty, then refreshes
let lastAgents: Agent[] = [];


// Pointing at an agent starts its worker (a start takes ~2 s, its status answers once it is up), so the chat is
// likely ready by the click; once a minute per agent at most
const warmed = new Map<string, number>();
const warm = (path: string) => {
  if (Date.now() - (warmed.get(path) ?? 0) < 60_000) return;
  warmed.set(path, Date.now());
  void api(`${path}/status`).catch(() => {});
};

function useAgents() {
  const [agents, setAgentsState] = useState<Agent[]>(lastAgents);
  const setAgents = useCallback((next: Agent[] | ((all: Agent[]) => Agent[])) => setAgentsState((all) => (lastAgents = typeof next === "function" ? next(all) : next)), []);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Agent[] | { error?: string }>("/agents")
      .then((d) => (Array.isArray(d) ? (setAgents(d), setError(null)) : setError(d.error ?? "unexpected answer")))
      .catch((e: Error) => setError(e.message.includes("refused") ? e.message : "Can't reach the agent server. Start it with `npm run ui:server`."));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    window.addEventListener("agents-changed", load); // a file was saved in the editor
    return () => (clearInterval(t), window.removeEventListener("agents-changed", load));
  }, [load]);
  // one agent's fresh summary, for the live view of a run (cheaper than listing all of them)
  const refresh = useCallback((slug: string) => {
    api<Agent>(`/a/${slug}/summary`).then((w) => setAgents((all) => all.map((x) => (x.slug === w.slug ? w : x))), () => {});
  }, []);
  return { agents, error, load, refresh };
}

function RunsTable({ runs }: { runs: (Run & { slug?: string })[] }) {
  return (
    <div className="bg-card overflow-hidden rounded-xl border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-muted-foreground text-xs">
          <tr>
            {[runs.some((r) => r.slug) && "Agent", "Run", "Started", "Took", "Status", "Cost"].filter(Boolean).map((h) => (
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
              <td className="px-3 py-2 text-xs">{took(r.started_at, r.finished_at)}</td>
              <td className="px-3 py-2">
                <span className={`rounded-full px-2 py-0.5 text-xs ${r.status === "done" ? "bg-emerald-100 text-emerald-800" : r.status === "active" ? "bg-sky-100 text-sky-800" : "bg-muted"}`}>{r.status}</span>
              </td>
              <td title={costSplit(r)} className="px-3 py-2 text-xs text-emerald-700">{money(runCost(r))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// A "…" button opening a short list of actions. The list is fixed under the button, so a scrolling list around it
// (the sidebar's agents) doesn't clip it; a click anywhere else closes it. `className` places the button; `plain`
// drops its border (in a row).
function MoreMenu({ actions, className = "relative", plain = false }: { actions: [string, () => void][]; className?: string; plain?: boolean }) {
  const [at, setAt] = useState<{ top: number; right: number } | null>(null);
  useEffect(() => {
    if (!at) return;
    const close = () => setAt(null);
    window.addEventListener("click", close);
    window.addEventListener("resize", close);
    return () => (window.removeEventListener("click", close), window.removeEventListener("resize", close));
  }, [at]);
  return (
    <div className={className} style={at ? { display: "block" } : undefined /* open: shown even when it shows on hover */}>
      <button
        type="button"
        title="More"
        onClick={(e) => {
          e.stopPropagation();
          const r = e.currentTarget.getBoundingClientRect();
          setAt(at ? null : { top: r.bottom + 4, right: window.innerWidth - r.right });
        }}
        className={plain ? "hover:bg-background rounded-md px-1.5 py-0.5 text-xs" : "hover:bg-accent rounded-md border px-2 py-1 text-xs"}
      >
        <MoreHorizontal className="size-3.5" />
      </button>
      {at && (
        <div style={{ top: at.top, right: at.right }} className="bg-popover fixed z-50 w-56 overflow-hidden rounded-lg border py-1 shadow-lg">
          {actions.map(([title, act]) => (
            <button key={title} type="button" onClick={(e) => (e.stopPropagation(), setAt(null), act())} className="hover:bg-accent block w-full px-3 py-1.5 text-left text-sm">
              {title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Sidebar({ agents, error, load }: ReturnType<typeof useAgents>) {
  const { select } = useCanvas();
  const { agent, session, setAgent, open } = useAgent();
  const row = sidebarRow;
  const icon = "size-4 shrink-0 opacity-80";
  // a command for prometheus: in the conversation with it that is open, else its latest; one that changes files
  // waits in the composer to be sent
  const ask = (command: string, send = false) => (open("/p", agent === "/p" ? session : null, command, send), select(null));
  const [openRow, setOpenRow] = useState<string | null>(null); // the agent whose actions show under it
  const stale = agents.filter((w) => w.stale).length;
  return (
    <aside className="bg-muted/30 flex w-64 shrink-0 flex-col border-r">
      {/* the app's name, a heading like the sections' (no click); then prometheus's own rows: the Forge (its chat, where
          agents are created: it is not one of them) and its commands */}
      <div className="text-brand flex items-center gap-2 px-6 pt-4 pb-3 text-base font-semibold">
        <Flame className="size-5" /> prometheus
      </div>
      <nav className="space-y-0.5 px-4">
        <button type="button" onClick={() => (setAgent("/p"), select(null))} onMouseEnter={() => warm("/p")} title="prometheus's own chat, where agents are created" className={row(agent === "/p")}>
          <Flame className={icon} /> Forge
        </button>
        <button type="button" onClick={() => (open("/p", `new-${crypto.randomUUID().slice(0, 8)}`, "/agent-new "), select(null))} className={row()}>
          <PlusIcon className={icon} /> New agent
        </button>
        {/* prometheus updates its agents' copies of the skill, so the action is its own; shown only when one is behind */}
        {stale > 0 && (
          <button type="button" onClick={() => ask("/agent-refresh --all")} title="Give the agents marked with an amber dot this prometheus's skill (their plans, runs and programs are kept)" className={row()}>
            <RefreshCw className={icon} /> Update agents <span className="text-muted-foreground ms-auto text-xs">{stale}</span>
          </button>
        )}
      </nav>

      {/* a heading as the title: what is not a row is not clickable */}
      <div className="flex items-center justify-between px-6 pt-6 pb-1.5">
        <span className="text-base font-semibold">Agents</span>
        <button type="button" onClick={load} title="Refresh" className="text-muted-foreground hover:text-foreground">
          <RefreshCw className="size-3.5" />
        </button>
      </div>
      <div className="max-h-[45%] space-y-0.5 overflow-y-auto px-4">
        {error && <p className="px-2 text-xs text-red-600">{error}</p>}
        {!error && !agents.length && <p className="text-muted-foreground px-2 py-1 text-xs">None yet: New agent plans one.</p>}
        {agents.map((w) => (
          <div key={w.slug}>
            <div className={row(agent === `/a/${w.slug}`)}>
              <button type="button" onClick={() => (setAgent(`/a/${w.slug}`), select(null))} onMouseEnter={() => warm(`/a/${w.slug}`)} title={`${w.slug}: ${stateOf(w).label}`} className="min-w-0 flex-1 truncate text-left">
                {w.slug}
              </button>
              {/* only what calls for attention, as dots (in words on hover) */}
              {w.active_run && <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-sky-500" title="Running" />}
              {needsYou(w) && <span className="size-1.5 shrink-0 rounded-full bg-violet-500" title={stateOf(w).label} />}
              {w.stale && <span className="size-1.5 shrink-0 rounded-full bg-amber-500" title="Its copy of the skill is out of date: Update agents, under prometheus" />}
              <button type="button" onClick={() => setOpenRow(openRow === w.slug ? null : w.slug)} title={openRow === w.slug ? "Hide its actions" : "Show its actions"} className="text-muted-foreground hover:text-foreground -me-1 shrink-0 rounded p-0.5">
                <ChevronRight className={`size-3.5 transition-transform ${openRow === w.slug ? "rotate-90" : ""}`} />
              </button>
            </div>
            {/* its actions, under it: its status in its own chat; export, prometheus's command for it (it works on the
                folder from outside), in prometheus's */}
            {openRow === w.slug && (
              <div className="ms-4 mt-0.5 mb-1 space-y-0.5 border-s ps-1.5">
                <button type="button" onClick={() => (open(`/a/${w.slug}`, null, "/agent-status", true), select(null))} className={row()}>
                  Status
                </button>
                <button type="button" onClick={() => ask(`/agent-export ${w.slug} `)} className={row()}>
                  Export…
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <Sessions />
    </aside>
  );
}

// the canvas when nothing is selected: what waits for you, and the recent runs of all agents
function Overview({ agents }: { agents: Agent[] }) {
  const waiting = agents.filter(needsYou);
  const runs = agents
    .flatMap((w) => w.recent_runs.map((r) => ({ ...r, slug: w.slug })))
    .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""))
    .slice(0, 8);
  const spent = runs.reduce((n, r) => n + runCost(r), 0);
  return (
    <div>
      <p className="text-muted-foreground text-xs tracking-wide uppercase">Overview</p>
      <h2 className="mt-1 text-xl font-semibold">{waiting.length ? `${waiting.length} waiting for you` : "All quiet"}</h2>
      <div className="mt-4 grid grid-cols-3 gap-3">
        {[
          ["Agents", String(agents.length)],
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
      <div className="mt-6">
        <References />
      </div>
    </div>
  );
}

// whom the chat talks to: prometheus, or an agent it built (with its state)
function ChatHeader({ agents }: { agents: Agent[] }) {
  const { agent, slug, label } = useAgent();
  const { show } = useCanvas();
  const aui = useAui();
  const run = (command: string) => void aui.thread().append(command);
  const actions: [string, () => void][] = [
    ["Session info", () => run("/session")],
    ["Rename…", () => {
      const name = prompt("Name this session");
      if (name?.trim()) run(`/name ${name.trim()}`);
    }],
    ["Reload extensions and skills", () => run("/reload")],
  ];
  const w = agents.find((x) => x.slug === slug);
  return (
    <div className="flex items-center gap-2 border-b px-4 py-2.5 text-sm">
      {slug ? <AgentIcon className="text-muted-foreground size-4" /> : <Flame className="text-brand size-4" />}
      <span className="font-medium">{label}</span>
      {!slug && <span className="text-muted-foreground text-xs">prometheus, where agents are created</span>}
      {w && <span className={`ml-auto rounded-full px-2 py-0.5 text-xs ${stateOf(w).tone}`}>{stateOf(w).label}</span>}
      <button
        type="button"
        onClick={() => show({ key: `terminal:${agent}`, title: `Terminal: ${label}`, node: <TerminalPanel agent={agent} /> })}
        title="pi in a terminal, for what only the terminal can do"
        className={`hover:bg-accent flex items-center gap-1 rounded-md border px-2 py-1 text-xs ${w ? "" : "ml-auto"}`}
      >
        <SquareTerminal className="size-3.5" /> Terminal
      </button>
      <MoreMenu actions={actions} />
    </div>
  );
}

// the whole chat takes dropped files, not only the composer: they become attachments (see attachments.ts)
function DropZone({ children }: { children: ReactNode }) {
  const aui = useAui();
  const [over, setOver] = useState(false);
  const hasFiles = (e: DragEvent) => [...(e.dataTransfer?.types ?? [])].includes("Files");
  return (
    <section
      className="relative flex min-w-0 flex-1 flex-col"
      onDragOver={(e) => hasFiles(e.nativeEvent) && (e.preventDefault(), setOver(true))}
      onDragLeave={(e) => e.currentTarget.contains(e.relatedTarget as Node) || setOver(false)}
      onDrop={(e) => {
        setOver(false);
        if (e.defaultPrevented || !hasFiles(e.nativeEvent)) return; // the composer's own dropzone already took it
        e.preventDefault();
        for (const f of e.dataTransfer.files) void aui.thread().composer().addAttachment(f);
      }}
    >
      {children}
      {over && <div className="bg-background/70 border-primary text-primary pointer-events-none absolute inset-2 z-30 flex items-center justify-center rounded-2xl border-2 border-dashed text-sm font-medium">Drop files to attach them</div>}
    </section>
  );
}

// the agent the workspace shows: the selected one, or (with the planner) the one being planned
function Layout() {
  const data = useAgents();
  const { slug, loaded, ready } = useAgent();
  const running = useAuiState((s) => s.thread.isRunning);
  useEffect(() => {
    if (!running) data.load(); // what the agent just did (a plan drafted, a wave finished) shows now, not at the next poll
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const shown = data.agents.find((w) => (slug ? w.slug === slug : w.state === "plan_draft"));
  // while its run is active the steps follow it every 2 s (wave starts, finishes, children done)
  const live = shown?.active_run ? shown.slug : null;
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => data.refresh(live), 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);
  return (
    <div className="flex h-dvh">
      <Sidebar {...data} />
      <DropZone>
        <ChatHeader agents={data.agents} />
        <div className="min-h-0 flex-1">
          <Thread loading={!loaded} starting={!ready} />
        </div>
        <QuestionCard />
        <SlashMenu />
        <StatusBar />
      </DropZone>
      <CanvasPanel workspace={shown ? <Workspace w={shown} /> : <Overview agents={data.agents} />} />
    </div>
  );
}

export default function Home() {
  const aui = useAui();
  const { slug } = useAgent();
  const config = AuiConfig({
    // the commands are chips above the input (command-bar.tsx); an empty Forge chat adds a worked example
    suggestions: Suggestions(
      slug ? [] : [{ title: "Create an agent", label: "emails to a spreadsheet", prompt: "/agent-new I want to read the emails in a folder and save them in an Excel file" }],
    ),
  });
  return (
    <AuiProvider extends={aui} config={config}>
      <ScaffoldCard />
      <CheckCard />
      <SubagentCard />
      <PiPanelCard />
      <AskCard />
      <ModelsCard />
      <Layout />
    </AuiProvider>
  );
}
