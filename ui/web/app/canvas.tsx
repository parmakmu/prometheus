"use client";

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, PanelRight, X } from "lucide-react";
import { useAgent } from "./agent";

// The right-hand canvas: what the agent shows (a plan, a created workflow, the critic's verdict) and
// what you select on the left. Tool cards push their full view here and leave a chip in the chat.
type Item = { key: string; title: string; node: ReactNode; onClose?: () => void; scoped?: boolean }; // onClose: what closing its tab means; scoped: belongs to the conversation (a tool card), gone when it changes
type Canvas = { items: Item[]; selected: string | null; show: (item: Item) => void; remove: (key: string) => void; select: (key: string | null) => void };

const CanvasContext = createContext<Canvas | null>(null);
export const useCanvas = () => useContext(CanvasContext)!;

// Sits above the chat runtime, which remounts per conversation. When the conversation changes, the tabs its
// tool cards made go, while the ones you opened yourself (a workflow, Files, the terminal) stay. Done while
// rendering, so the new conversation's cards add theirs after.
export function CanvasProvider({ children }: { children: ReactNode }) {
  const { base: scope } = useAgent();
  const [items, setItems] = useState<Item[]>([]);
  const [selected, select] = useState<string | null>(null);
  const [seen, setSeen] = useState(scope);
  if (seen !== scope) {
    setSeen(scope);
    setItems((all) => all.filter((x) => !x.scoped));
  }
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
// `running`: whether the tool is still running. A card first drawn already finished is history (a conversation
// being opened): it puts nothing on the canvas until you click its chip, so opening one doesn't open a tab per call.
export function ShowOnCanvas({ id, title, node, deps, gone, onClose, running = true }: { id: string; title: string; node: ReactNode; deps: unknown[]; gone?: boolean; onClose?: () => void; running?: boolean }) {
  const { show, remove, select } = useCanvas();
  // `deps` may change length between renders (a card gains a field when it completes), which useEffect
  // forbids: compare it here and give the effect one number that moves when it changes
  const last = useRef<{ deps: unknown[]; version: number }>({ deps, version: 0 });
  if (last.current.deps.length !== deps.length || last.current.deps.some((d, i) => !Object.is(d, deps[i]))) last.current = { deps, version: last.current.version + 1 };
  const version = last.current.version;
  const live = useRef(running);
  if (running) live.current = true;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => (gone ? remove(id) : live.current ? show({ key: id, title, node, onClose, scoped: true }) : undefined), [id, title, gone, version]);
  if (gone)
    return (
      <p className="text-muted-foreground my-2 flex items-center gap-2 text-xs">
        <PanelRight className="size-3.5" />
        {title} closed
      </p>
    );
  return (
    <button type="button" onClick={() => (show({ key: id, title, node, onClose, scoped: true }), select(id))} className="hover:bg-accent my-2 flex items-center gap-2 rounded-full border px-3 py-1 text-xs">
      <PanelRight className="text-primary size-3.5" />
      {title}
      <span className="text-muted-foreground">→ on the right</span>
    </button>
  );
}

// The right column is the workspace (steps, input, output) or, while an item is open, a drawer over it that
// widens the column (the chat narrows to make room):
// a file, the plan, a tool's result, Files, the terminal. Back returns to the workspace; closing also drops the item.
export function CanvasPanel({ workspace }: { workspace: ReactNode }) {
  const { items, selected, select, remove } = useCanvas();
  const current = items.find((x) => x.key === selected);
  return (
    <aside className={`bg-muted/20 relative flex shrink-0 flex-col border-l transition-[width] duration-200 ${current ? "w-[min(60vw,880px)]" : "w-[420px]"}`}>
      <div className="flex-1 overflow-y-auto p-5">{workspace}</div>
      {current && (
        <div className="bg-background absolute inset-0 z-10 flex flex-col">
          <div className="flex items-center gap-1 border-b px-3 py-2 text-sm">
            <button type="button" onClick={() => select(null)} title="Back to the workspace" className="hover:bg-accent rounded-md p-1">
              <ArrowLeft className="size-4" />
            </button>
            <span className="truncate font-medium">{current.title}</span>
            <button type="button" title="Close" onClick={() => (current.onClose ?? (() => remove(current.key)))()} className="hover:bg-accent ml-auto rounded-md p-1">
              <X className="size-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-5">{current.node}</div>
        </div>
      )}
    </aside>
  );
}
