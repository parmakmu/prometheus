"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { MyRuntimeProvider } from "./MyRuntimeProvider";

// The agent the chat talks to, "/p" (prometheus, the planner) or "/w/<slug>" (a workflow's own agent),
// and the conversation: a Pi session of that agent ("new-…" starts one; none is its latest). Kept in the
// address (#/w/emails-to-excel/s/<id>), so a reload stays on it. `base` is where its endpoints are.
type Agent = {
  agent: string;
  slug: string | null;
  session: string | null;
  base: string;
  label: string;
  setAgent: (agent: string) => void;
  setSession: (session: string | null) => void;
};
const AgentContext = createContext<Agent | null>(null);
export const useAgent = () => useContext(AgentContext)!;

const HASH = /^#(\/p|\/w\/[a-z0-9-]+)?(?:\/s\/([\w-]+))?$/;
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
  const slug = agent.startsWith("/w/") ? agent.slice(3) : null;
  const base = session ? `${agent}/s/${session}` : agent;
  return (
    <AgentContext.Provider value={{ agent, slug, session, base, label: slug ?? "prometheus", setAgent: (a) => go(a, null), setSession: (s) => go(agent, s) }}>
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
