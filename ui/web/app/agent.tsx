"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { MyRuntimeProvider } from "./MyRuntimeProvider";

// The agent the chat talks to, "/p" (prometheus, the planner) or "/a/<slug>" (an agent prometheus built),
// and the conversation: a Pi session of that agent ("new-…" starts one; none is its latest). Kept in the
// address (#/a/emails-to-excel/s/<id>), so a reload stays on it. `base` is where its endpoints are.
type Agent = {
  agent: string;
  slug: string | null;
  session: string | null;
  base: string;
  label: string;
  setAgent: (agent: string) => void;
  setSession: (session: string | null) => void;
  // another agent and conversation at once, with `text` waiting in its composer (New agent: prometheus, a fresh
  // conversation, "/agent-new "), or sent when `send` (a command that only reads, as /agent-list); the status
  // bar puts it there, once the conversation has loaded (status-bar.tsx)
  open: (agent: string, session: string | null, text?: string, send?: boolean) => void;
  draft: { text: string; send: boolean } | null;
  clearDraft: () => void;
  // the agent of this conversation is working, as its worker says (the status bar polls it): steadier than the
  // thread's own running, which stops and starts with each sync while the agent writes
  busy: boolean;
  setBusy: (busy: boolean) => void;
  // this conversation's history is on the page (the status bar's first sync is done): until then the chat shows a
  // loading skeleton, not the welcome of a new chat (a worker starting, after a refresh say, takes seconds)
  loaded: boolean;
  setLoaded: () => void;
  // its Pi is up (the worker answered a status): until then the input is grayed out
  ready: boolean;
  setReady: () => void;
};
const AgentContext = createContext<Agent | null>(null);
export const useAgent = () => useContext(AgentContext)!;

const HASH = /^#(\/p|\/a\/[a-z0-9-]+)?(?:\/s\/([\w-]+))?$/;
const fromHash = (): [string, string | null] => {
  const m = typeof window !== "undefined" ? window.location.hash.match(HASH) : null;
  return [m?.[1] ?? "/p", m?.[2] ?? null];
};

export function AgentProvider({ children }: { children: ReactNode }) {
  const [[agent, session], set] = useState<[string, string | null]>(["/p", null]);
  useEffect(() => {
    set(fromHash());
    const onHash = () => set(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = (a: string, s: string | null) => {
    const hash = `${a === "/p" && s ? "/p" : a === "/p" ? "" : a}${s ? `/s/${s}` : ""}`;
    window.history.replaceState(null, "", hash ? `#${hash}` : window.location.pathname + window.location.search);
    set([a, s]);
  };
  // state, not a ref: a command for the conversation already open (no remount) reaches it too
  const [draft, setDraft] = useState<{ text: string; send: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadedBase, setLoadedBase] = useState<string | null>(null); // the conversation whose history has loaded
  const [readyBase, setReadyBase] = useState<string | null>(null); // the conversation whose Pi is up
  const slug = agent.startsWith("/a/") ? agent.slice(3) : null;
  const base = session ? `${agent}/s/${session}` : agent;
  return (
    <AgentContext.Provider value={{ agent, slug, session, base, label: slug ?? "Forge", setAgent: (a) => go(a, null), setSession: (s) => go(agent, s), open: (a, s, text, send = false) => (setDraft(text ? { text, send } : null), go(a, s)), draft, clearDraft: () => setDraft(null), busy, setBusy, loaded: loadedBase === base, setLoaded: () => setLoadedBase(base), ready: readyBase === base, setReady: () => setReadyBase(base) }}>
      {children}
    </AgentContext.Provider>
  );
}

// a fresh chat runtime per conversation: switching remounts it, and its first sync loads that history
export function AgentRuntime({ children }: { children: ReactNode }) {
  const { base } = useAgent();
  return (
    <MyRuntimeProvider key={base} agentPath={base}>
      {children}
    </MyRuntimeProvider>
  );
}
