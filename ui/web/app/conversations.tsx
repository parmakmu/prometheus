"use client";

import { useCallback, useEffect, useState } from "react";
import { PlusIcon, Trash2 } from "lucide-react";
import { api, send } from "./api";
import { useAgent } from "./agent";

type Conversation = { id: string; title: string; modified: string; messages: number; open: boolean };

const ago = (iso: string) => {
  const s = (Date.now() - +new Date(iso)) / 1000;
  return s < 60 ? "now" : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;
};

// A row of the sidebar, as Cowork draws them: dark grey text, black on a grey background on hover, and on a darker
// one when open (the agent, the conversation); no bold. Shared with the rows above (page.tsx).
export const sidebarRow = (active = false) =>
  `group relative flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-[13px] ${active ? "bg-foreground/10 text-foreground" : "text-foreground/90 hover:bg-foreground/5 hover:text-foreground"}`;

// The agent's conversations: its Pi sessions (in its folder's .pi/sessions, the same ones `pi --resume`
// lists there), newest first. New starts one; delete removes its history for good.
export function Conversations() {
  const { agent, label, session, setSession } = useAgent();
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

  return (
    <>
      {/* a heading as the sidebar's title; whose they are (prometheus's, or that workflow's) in a lighter word */}
      <div className="flex items-center justify-between gap-2 px-6 pt-6 pb-1.5">
        <span className="truncate text-base font-semibold">
          Conversations <span className="text-muted-foreground text-sm font-normal">· {label}</span>
        </span>
        <button type="button" onClick={() => setSession(`new-${crypto.randomUUID().slice(0, 8)}`)} title="New conversation" className="text-muted-foreground hover:text-foreground">
          <PlusIcon className="size-3.5" />
        </button>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-4">
        {fresh && (
          <div className={sidebarRow(true)}>
            <span className="truncate italic">New conversation</span>
          </div>
        )}
        {list.map((c) => (
          <div key={c.id} className={sidebarRow(!fresh && c.id === current)}>
            <button type="button" onClick={() => setSession(c.id)} className="min-w-0 flex-1 truncate text-left">
              {c.title}
            </button>
            <span className="text-muted-foreground shrink-0 text-[11px] group-hover:hidden">{ago(c.modified)}</span>
            <button type="button" title="Delete this conversation" onClick={() => void remove(c)} className="text-muted-foreground hidden shrink-0 group-hover:block hover:text-red-600">
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
        {!fresh && list.length === 0 && <p className="text-muted-foreground px-2 py-2 text-xs">None yet</p>}
      </div>
    </>
  );
}
