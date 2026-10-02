"use client";

import { useCallback, useEffect, useState } from "react";
import { MessageSquare, PlusIcon, Trash2 } from "lucide-react";
import { api, send } from "./api";
import { useAgent } from "./agent";

type Conversation = { id: string; title: string; modified: string; messages: number; open: boolean };

const ago = (iso: string) => {
  const s = (Date.now() - +new Date(iso)) / 1000;
  return s < 60 ? "now" : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;
};

// The agent's conversations: its Pi sessions (in its folder's .pi/sessions, the same ones `pi --resume`
// lists there), newest first. New starts one; delete removes its history for good.
export function Conversations() {
  const { agent, session, setSession } = useAgent();
  const [list, setList] = useState<Conversation[]>([]);
  const load = useCallback(() => {
    api<Conversation[]>(`${agent}/sessions`).then(setList, () => setList([]));
  }, [agent]);
  useEffect(() => {
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, [load, session]);

  const current = session ?? list[0]?.id; // none named: the agent continues its latest
  const fresh = session?.startsWith("new-") && !list.some((c) => c.open && c.id === session);
  const remove = async (c: Conversation) => {
    if (!confirm(`Delete "${c.title}"?\n\nIts history is removed for good.`)) return;
    try {
      await send(`${agent}/sessions/${c.id}`, "DELETE");
      if (c.id === current) setSession(null);
    } catch (e) {
      alert(String((e as Error).message).replace(/^[^:]*:\s*/, ""));
    }
    load();
  };
  const item = (active: boolean) => `group flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm ${active ? "bg-accent" : "hover:bg-accent/60"}`;

  return (
    <>
      <div className="mt-4 flex items-center justify-between px-4 pb-1 text-xs">
        <span className="text-muted-foreground">Conversations</span>
        <button type="button" onClick={() => setSession(`new-${crypto.randomUUID().slice(0, 8)}`)} title="New conversation" className="text-muted-foreground hover:text-foreground">
          <PlusIcon className="size-3.5" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2">
        {fresh && (
          <div className={item(true)}>
            <MessageSquare className="text-muted-foreground size-3.5 shrink-0" />
            <span className="truncate italic">New conversation</span>
          </div>
        )}
        {list.map((c) => (
          <div key={c.id} className={item(!fresh && c.id === current)}>
            <button type="button" onClick={() => setSession(c.id)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
              <MessageSquare className="text-muted-foreground size-3.5 shrink-0" />
              <span className="truncate">{c.title}</span>
            </button>
            <span className="text-muted-foreground shrink-0 text-[10px] group-hover:hidden">{ago(c.modified)}</span>
            <button type="button" title="Delete this conversation" onClick={() => void remove(c)} className="text-muted-foreground hidden shrink-0 group-hover:block hover:text-red-600">
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
        {!fresh && list.length === 0 && <p className="text-muted-foreground px-3 py-2 text-xs">None yet</p>}
      </div>
    </>
  );
}
