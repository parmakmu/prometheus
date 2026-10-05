"use client";

import { useEffect, useRef, useState } from "react";
import { useAui, useAuiState } from "@assistant-ui/react";
import { useAgUiInterrupts } from "@assistant-ui/react-ag-ui";
import { Brain, Coins, Cpu, Database, Gauge } from "lucide-react";
import { api, NO_ACCESS, send } from "./api";
import { useAgent } from "./agent";
import { useCanvas } from "./canvas";
import { ModelPicker } from "./models";
import { kilo } from "./format";


type Status = {
  model: { provider: string; id: string; name: string; contextWindow?: number } | null;
  thinking?: string;
  thinkingLevels?: string[];
  busy: boolean;
  panels?: number;
  context: { tokens: number | null; contextWindow: number; percent: number | null } | null;
  cache: { lastPercent: number | null; read: number; write: number };
  tokens: { input: number; output: number };
  cost: number;
};


function Chip({ icon, children, title }: { icon: React.ReactNode; children: React.ReactNode; title: string }) {
  return (
    <span title={title} className="flex items-center gap-1.5 whitespace-nowrap">
      <span className="text-muted-foreground">{icon}</span>
      {children}
    </span>
  );
}

// Below the chat, like pi-cc-extensions' footer: the session's model, context, cache, tokens and cost
export function StatusBar() {
  const aui = useAui();
  const { base: agent, draft, clearDraft, setBusy, loaded, setLoaded, setReady } = useAgent(); // this conversation's endpoints, and a command left for it
  const { show, remove } = useCanvas();
  const running = useAuiState((st) => st.thread.isRunning);
  const lastId = useAuiState((st) => st.thread.messages.at(-1)?.id ?? null);
  const synced = useRef(false);
  // a question waiting for an answer: no run may start (the answer resumes it)
  const asking = useAgUiInterrupts().length > 0;
  // refused (say, a question just opened): ignored, the next poll tries again
  const sync = async () => {
    try {
      await (aui.thread().startRun({ parentId: lastId }) as unknown);
    } catch {}
  };
  const [s, setS] = useState<Status | null>(null);
  useEffect(() => setBusy(!!s?.busy), [s?.busy]); // eslint-disable-line react-hooks/exhaustive-deps
  const [error, setError] = useState<string | null>(null);
  // when each status was asked for, and when our last run ended: a status asked before that end may still say busy
  // about the run itself, and a sync on it ends at once, again and again until the next poll (the send button blinked)
  const [statusAt, setStatusAt] = useState(0);
  const endedAt = useRef(0);
  const load = useRef(() => {});
  useEffect(() => {
    load.current = () => {
      const at = Date.now();
      api<Status>(`${agent}/status`)
        .then((d) => (setS(d), setStatusAt(at), setError(null), setReady())) // the worker answered: its Pi is up
        .catch((e: Error) => (setS(null), setError(e.message === NO_ACCESS ? "refused: open the link with the token" : null)));
    };
    load.current();
    const t = setInterval(() => load.current(), 3000);
    return () => clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (running) return;
    endedAt.current = Date.now();
    load.current(); // whether the agent goes on working without us, asked now
  }, [running]);
  // A run with nothing new is a sync: the server answers with the conversation so far and, while the
  // agent works on its own (a wave finished, say), streams that work. Once when the page opens, at once: while the
  // worker starts (~2 s), the router answers from the session file, so the conversation doesn't wait for Pi…
  useEffect(() => {
    if (synced.current || asking) return;
    synced.current = true;
    void sync().then(setLoaded);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s]);
  // …and whenever the agent is busy, or shows a panel, without a run of ours
  const wants = !!(s?.busy || s?.panels) && statusAt >= endedAt.current;
  useEffect(() => {
    if (synced.current && wants && !running && !asking) void sync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wants, running, asking]);
  // A command another part of the page left for this conversation (useAgent's open): text for the composer at once; a
  // command to send once the conversation has loaded and no run is going, since a run started during the first sync
  // lost its stream to it (the command ran, the page never showed it). React runs effects twice in development: a
  // draft is used once.
  const used = useRef<object | null>(null);
  useEffect(() => {
    if (!draft || used.current === draft || (draft.send && (!loaded || running || asking))) return;
    used.current = draft;
    clearDraft();
    if (draft.send) void aui.thread().append(draft.text);
    else aui.thread().composer().setText(draft.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, loaded, running, asking]);

  if (!s) return <div className="text-muted-foreground border-t px-4 py-2 text-xs">{error ?? "agent server not running: start it with npm run ui:server"}</div>;
  const pct = s.context?.percent ?? null;
  const tone = pct === null ? "bg-muted-foreground/40" : pct >= 80 ? "bg-red-500" : pct >= 60 ? "bg-amber-500" : "bg-primary";
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t px-4 py-2 text-xs">
      <Chip icon={<Cpu className="size-3.5" />} title={s.model ? `${s.model.provider}/${s.model.id}: click to change` : "no model"}>
        <button type="button" onClick={() => show({ key: "models", title: "Model", node: <ModelPicker base={agent} onClose={() => remove("models")} /> })} className="font-medium hover:underline">
          {s.model?.name ?? "no model"}
        </button>
        {s.busy && <span className="size-1.5 animate-pulse rounded-full bg-sky-500" />}
      </Chip>
      {!!s.thinkingLevels?.length && (
        <Chip icon={<Brain className="size-3.5" />} title="thinking level, for this conversation">
          <select
            value={s.thinking}
            onChange={(e) => {
              const level = e.target.value;
              setS({ ...s, thinking: level });
              void send(`${agent}/thinking`, "POST", JSON.stringify({ level })).catch(() => {});
            }}
            className="hover:bg-accent rounded bg-transparent text-xs outline-none"
          >
            {s.thinkingLevels.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </Chip>
      )}
      <Chip icon={<Gauge className="size-3.5" />} title={`context: ${s.context?.tokens ?? "?"} of ${s.context?.contextWindow ?? "?"} tokens`}>
        <span className="bg-muted h-1.5 w-16 overflow-hidden rounded-full">
          <span className={`block h-full ${tone}`} style={{ width: `${Math.min(pct ?? 0, 100)}%` }} />
        </span>
        <span className="text-muted-foreground">context</span> {pct === null ? "–" : `${Math.round(pct)}%`}
        {s.context && <span className="text-muted-foreground">of {kilo(s.context.contextWindow)}</span>}
        <button
          type="button"
          onClick={() => aui.thread().append("/compact")}
          disabled={!pct}
          title="Compact: summarise the conversation so far to free context"
          className={`rounded border px-1.5 text-[10px] disabled:opacity-40 ${(pct ?? 0) >= 60 ? "border-amber-500 text-amber-700" : "hover:bg-accent"}`}
        >
          Compact
        </button>
      </Chip>
      <Chip icon={<Database className="size-3.5" />} title={`cache: ${kilo(s.cache.read)} read, ${kilo(s.cache.write)} written this session; % of the last reply's prompt read from cache`}>
        <span className="text-muted-foreground">cache</span> {s.cache.lastPercent === null ? "–" : `${s.cache.lastPercent}%`}
        <span className="text-muted-foreground">({kilo(s.cache.read)} read)</span>
      </Chip>
      <Chip icon={<span className="text-[10px]">↑↓</span>} title="tokens in / out this session">
        <span className="text-muted-foreground">tokens</span> {kilo(s.tokens.input)} in / {kilo(s.tokens.output)} out
      </Chip>
      <Chip icon={<Coins className="size-3.5" />} title="cost of this session">
        <span className="text-emerald-700">${s.cost.toFixed(4)}</span>
      </Chip>
    </div>
  );
}
