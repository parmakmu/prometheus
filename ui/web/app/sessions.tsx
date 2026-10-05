"use client";

import { useCallback, useEffect, useState } from "react";
import { PencilIcon, PlusIcon, Trash2 } from "lucide-react";
import { api, send } from "./api";
import { useAgent } from "./agent";

type Session = { id: string; title: string; modified: string; messages: number; open: boolean };

const ago = (iso: string) => {
  const s = (Date.now() - +new Date(iso)) / 1000;
  return s < 60 ? "now" : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;
};

// A row of the sidebar, as Cowork draws them: dark grey text, black on a grey background on hover, and on a darker
// one when open (the agent, the conversation); no bold. Shared with the rows above (page.tsx).
export const sidebarRow = (active = false) =>
  `group relative flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-[13px] ${active ? "bg-foreground/10 text-foreground" : "text-foreground/90 hover:bg-foreground/5 hover:text-foreground"}`;

// what each agent's list last showed: the page remounts on every switch (AgentRuntime is keyed by the conversation),
// so the list starts from it instead of empty, then refreshes
const lastLists = new Map<string, Session[]>();

// The agent's sessions: its Pi sessions (in its folder's .pi/sessions, the same ones `pi --resume`
// lists there), newest first. New starts one; the pencil renames one in place (Pi's name, as /name sets it: the
// terminal shows it too); delete removes its history for good.
export function Sessions() {
  const { agent, session, setSession } = useAgent();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [list, setListState] = useState<Session[]>(() => lastLists.get(agent) ?? []);
  const setList = useCallback((l: Session[]) => (lastLists.set(agent, l), setListState(l)), [agent]);
  const load = useCallback(() => {
    api<Session[]>(`${agent}/sessions`).then(setList, () => setList([]));
  }, [agent]);
  useEffect(() => {
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, [load, session]);

  const current = session ?? list[0]?.id; // none named: the agent continues its latest
  const fresh = session?.startsWith("new-") && !list.some((c) => c.open && c.id === session);
  const remove = async (c: Session) => {
    if (!confirm(`Delete "${c.title}"?\n\nIts history is removed for good.`)) return;
    try {
      await send(`${agent}/sessions/${c.id}`, "DELETE");
      if (c.id === current) setSession(null);
    } catch (e) {
      alert(String((e as Error).message).replace(/^[^:]*:\s*/, ""));
    }
    load();
  };

  const rename = async (c: Session, name: string) => {
    setRenaming(null);
    if (!name.trim() || name.trim() === c.title) return;
    setList(list.map((x) => (x.id === c.id ? { ...x, title: name.trim() } : x)));
    try {
      await send(`${agent}/sessions/${c.id}/name`, "POST", JSON.stringify({ name: name.trim() }));
    } catch (e) {
      alert(String((e as Error).message).replace(/^[^:]*:\s*/, ""));
    }
    load();
  };

  return (
    <>
      {/* a heading as the sidebar's title; whose they are shows above, in the row that is selected (the Forge or an agent) */}
      <div className="flex items-center justify-between gap-2 px-6 pt-6 pb-1.5">
        <span className="text-base font-semibold">Sessions</span>
        <button type="button" onClick={() => setSession(`new-${crypto.randomUUID().slice(0, 8)}`)} title="New session" className="text-muted-foreground hover:text-foreground">
          <PlusIcon className="size-3.5" />
        </button>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-4">
        {fresh && (
          <div className={sidebarRow(true)}>
            <span className="truncate italic">New session</span>
          </div>
        )}
        {list.map((c) => (
          <div key={c.id} className={sidebarRow(!fresh && c.id === current)}>
            {renaming === c.id ? (
              <input
                autoFocus
                defaultValue={c.title}
                onFocus={(e) => e.currentTarget.select()}
                onBlur={(e) => void rename(c, e.currentTarget.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") e.currentTarget.value = c.title; // blur then changes nothing
                  if (e.key === "Enter" || e.key === "Escape") e.currentTarget.blur();
                }}
                className="bg-background min-w-0 flex-1 rounded px-1 outline-none ring-1 ring-foreground/20"
              />
            ) : (
              <button type="button" onClick={() => setSession(c.id)} onDoubleClick={() => setRenaming(c.id)} className="min-w-0 flex-1 truncate text-left">
                {/* a run a caller started through the agent's API (ui/api.ts names its session "API · <request>") */}
                {c.title.startsWith("API · ") ? (
                  <>
                    <span className="text-muted-foreground mr-1.5 rounded border px-1 text-[10px] font-medium" title="Started through the agent's API">API</span>
                    {c.title.slice(6)}
                  </>
                ) : (
                  c.title
                )}
              </button>
            )}
            {renaming !== c.id && (
              <>
                <span className="text-muted-foreground shrink-0 text-[11px] group-hover:hidden">{ago(c.modified)}</span>
                <button type="button" title="Rename" onClick={() => setRenaming(c.id)} className="text-muted-foreground hover:text-foreground hidden shrink-0 group-hover:block">
                  <PencilIcon className="size-3.5" />
                </button>
                <button type="button" title="Delete this session" onClick={() => void remove(c)} className="text-muted-foreground hidden shrink-0 group-hover:block hover:text-red-600">
                  <Trash2 className="size-3.5" />
                </button>
              </>
            )}
          </div>
        ))}
        {!fresh && list.length === 0 && <p className="text-muted-foreground px-2 py-2 text-xs">None yet</p>}
      </div>
    </>
  );
}
