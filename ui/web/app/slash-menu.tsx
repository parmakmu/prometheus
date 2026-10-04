"use client";

import { useEffect, useRef, useState } from "react";
import { useAui, useAuiState } from "@assistant-ui/react";
import { SquareSlash, X } from "lucide-react";
import { api } from "./api";
import { useAgent } from "./agent";


type Command = { name: string; description: string; group?: string; args?: "required" | "optional" | "none" };

// Typing "/" lists the commands the agent accepts, filtered as you type. A click or Enter runs a command that needs
// nothing more, and fills in one that needs input (its usage shows <…>); Tab always fills in, to add arguments.
// Escape, the x or a click elsewhere closes it until the "/" is typed again.
export function SlashMenu() {
  const aui = useAui();
  const { base: agent, label } = useAgent(); // this conversation's endpoints
  const text = useAuiState((s) => s.composer.text) as string;
  const [commands, setCommands] = useState<Command[] | null>(null);
  const typing = /^\/\S*$/.test(text ?? "");
  const [closed, setClosed] = useState(false); // closed for this "/…": opens again once the text is no longer a command
  const [active, setActive] = useState(0); // the highlighted command, for the keys
  const box = useRef<HTMLDivElement>(null);
  if (!typing && closed) setClosed(false);
  // fetched when you first type "/", and again while the server could not give it (e.g. restarted)
  useEffect(() => {
    if (!typing || commands?.length) return;
    api<Command[]>(`${agent}/commands`)
      .then((d) => setCommands(Array.isArray(d) ? d : []))
      .catch(() => setCommands([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typing]); // only when "/" is first typed: an empty answer must not refetch in a loop
  const q = (text ?? "").slice(1).toLowerCase();
  const found = (commands ?? []).filter((c) => c.name.toLowerCase().startsWith(q));
  // every command that matches: this agent's and pi's own first, then other extensions' (grouped, so it shows whose)
  const ours = (c: Command) => c.group === "main" || c.group === "pi";
  const matches = [...found.filter(ours), ...found.filter((c) => !ours(c))];
  const open = typing && !closed && commands !== null && matches.length > 0;
  const fill = (c: Command) => aui.thread().composer().setText(`/${c.name} `);
  const pick = (c: Command) => {
    if (c.args === "required") return fill(c);
    aui.thread().composer().setText("");
    aui.thread().append(`/${c.name}`);
  };
  useEffect(() => setActive(0), [q]);

  // the keys act on the menu before the composer sees them (Enter would otherwise send "/wor")
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      const act = { Escape: () => setClosed(true), ArrowDown: () => setActive((i) => (i + 1) % matches.length), ArrowUp: () => setActive((i) => (i - 1 + matches.length) % matches.length), Enter: () => pick(matches[active] ?? matches[0]), Tab: () => fill(matches[active] ?? matches[0]) }[e.key];
      if (!act || (e.key === "Enter" && e.shiftKey)) return;
      e.preventDefault();
      e.stopPropagation();
      act();
    };
    const onClick = (e: MouseEvent) => {
      const t = e.target as Element;
      if (!box.current?.contains(t) && !t.closest?.(".aui-composer-root")) setClosed(true);
    };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onClick);
    return () => (window.removeEventListener("keydown", onKey, true), document.removeEventListener("mousedown", onClick));
  });
  useEffect(() => box.current?.querySelector("[data-active=true]")?.scrollIntoView({ block: "nearest" }), [active]);

  if (!typing || closed || commands === null) return null;
  if (!commands.length)
    return (
      <div className="bg-card text-muted-foreground absolute inset-x-4 bottom-28 z-20 mx-auto max-w-(--thread-max-width) rounded-xl border px-3 py-2 text-xs shadow-lg">
        No command list from the agent server: restart it with npm run ui:server.
      </div>
    );
  if (!matches.length) return null;
  return (
    <div ref={box} className="bg-card absolute inset-x-4 bottom-28 z-20 mx-auto max-w-(--thread-max-width) overflow-hidden rounded-xl border shadow-lg">
      <div className="text-muted-foreground flex items-center gap-2 border-b px-3 py-1.5 text-xs">
        <span>Commands · ↑ ↓ to choose, Enter to run, Tab to add text, Esc to close</span>
        <button type="button" title="Close (Esc)" onClick={() => setClosed(true)} className="hover:bg-accent hover:text-foreground ml-auto rounded p-0.5">
          <X className="size-3.5" />
        </button>
      </div>
      <ul className="max-h-80 overflow-y-auto py-1">
        {matches.map((c, i) => (
          <li key={c.name}>
            {c.group !== matches[i - 1]?.group && <p className="text-muted-foreground px-3 pt-2 pb-1 text-xs">{c.group === "main" ? label : (c.group ?? "Commands")}</p>}
            <button
              type="button"
              data-active={i === active}
              onClick={() => pick(c)}
              onMouseEnter={() => setActive(i)}
              className="hover:bg-accent data-[active=true]:bg-accent flex w-full items-start gap-2 px-3 py-2 text-left text-sm"
            >
              <SquareSlash className="text-primary mt-0.5 size-4 shrink-0" />
              <span>
                <span className="font-mono">/{c.name}</span>
                {c.args === "required" && <span className="text-muted-foreground ms-2 text-xs">needs input</span>}
                {c.description && <span className="text-muted-foreground block text-xs">{c.description}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
