"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { PanelRight, X } from "lucide-react";

// The right-hand canvas: what the agent shows (a plan, a created workflow, the critic's verdict) and
// what you select on the left. Tool cards push their full view here and leave a chip in the chat.
type Item = { key: string; title: string; node: ReactNode; onClose?: () => void }; // onClose: what closing its tab means
type Canvas = { items: Item[]; selected: string | null; show: (item: Item) => void; remove: (key: string) => void; select: (key: string | null) => void };

const CanvasContext = createContext<Canvas | null>(null);
export const useCanvas = () => useContext(CanvasContext)!;

export function CanvasProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const [selected, select] = useState<string | null>(null);
  const show = (item: Item) => {
    setItems((all) => {
      const i = all.findIndex((x) => x.key === item.key);
      if (i < 0) select(item.key); // something new from the agent takes the focus
      return i < 0 ? [...all, item] : all.map((x, j) => (j === i ? item : x));
    });
  };
  const remove = (key: string) => {
    setItems((all) => all.filter((x) => x.key !== key));
    select((s) => (s === key ? null : s));
  };
  return <CanvasContext.Provider value={{ items, selected, show, remove, select }}>{children}</CanvasContext.Provider>;
}

// A one-line chip in the chat that puts `node` on the canvas (and keeps it current while `deps` change)
// `gone`: the item is over (a panel closed): its tab leaves the canvas, and the chip says so
export function ShowOnCanvas({ id, title, node, deps, gone, onClose }: { id: string; title: string; node: ReactNode; deps: unknown[]; gone?: boolean; onClose?: () => void }) {
  const { show, remove, select } = useCanvas();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => (gone ? remove(id) : show({ key: id, title, node, onClose })), [id, title, gone, ...deps]);
  if (gone)
    return (
      <p className="text-muted-foreground my-2 flex items-center gap-2 text-xs">
        <PanelRight className="size-3.5" />
        {title} closed
      </p>
    );
  return (
    <button type="button" onClick={() => select(id)} className="hover:bg-accent my-2 flex items-center gap-2 rounded-full border px-3 py-1 text-xs">
      <PanelRight className="text-primary size-3.5" />
      {title}
      <span className="text-muted-foreground">→ on the right</span>
    </button>
  );
}

export function CanvasPanel({ overview, overviewTitle }: { overview: ReactNode; overviewTitle: string }) {
  const { items, selected, select, remove } = useCanvas();
  const current = items.find((x) => x.key === selected);
  const tab = (key: string | null, title: string) => {
    const active = selected === key || (key === null && !current);
    return (
      <span key={key ?? "overview"} className={`flex shrink-0 items-center rounded-full text-xs ${active ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}>
        <button type="button" onClick={() => select(key)} className={`py-1 ${key ? "pr-1 pl-3" : "px-3"}`}>
          {title}
        </button>
        {key && (
          <button type="button" title="Close" onClick={() => (items.find((x) => x.key === key)?.onClose ?? (() => remove(key)))()} className="rounded-full p-1 pr-2 opacity-60 hover:opacity-100">
            <X className="size-3" />
          </button>
        )}
      </span>
    );
  };
  return (
    <aside className="bg-muted/20 flex w-[480px] shrink-0 flex-col border-l">
      <div className="flex gap-1 overflow-x-auto border-b px-3 py-2">
        {tab(null, overviewTitle)}
        {items.map((x) => tab(x.key, x.title))}
      </div>
      <div className="flex-1 overflow-y-auto p-5">{current ? current.node : overview}</div>
    </aside>
  );
}
