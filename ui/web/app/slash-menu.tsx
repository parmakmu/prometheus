"use client";

import { useEffect, useState } from "react";
import { useAui, useAuiState } from "@assistant-ui/react";
import { SquareSlash } from "lucide-react";
import { api } from "./api";
import { useAgent } from "./agent";


type Command = { name: string; description: string; group?: string };

// Typing "/" lists the commands the agent accepts, filtered as you type; a click fills one in
export function SlashMenu() {
  const aui = useAui();
  const { base: agent, label } = useAgent(); // this conversation's endpoints
  const text = useAuiState((s) => s.composer.text) as string;
  const [commands, setCommands] = useState<Command[] | null>(null);
  const [more, setMore] = useState(false); // other extensions' commands: some only work in the terminal
  const typing = /^\/\S*$/.test(text ?? "");
  // fetched when you first type "/", and again while the server could not give it (e.g. restarted)
  useEffect(() => {
    if (!typing || commands?.length) return;
    api<Command[]>(`${agent}/commands`)
      .then((d) => setCommands(Array.isArray(d) ? d : []))
      .catch(() => setCommands([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typing]); // only when "/" is first typed: an empty answer must not refetch in a loop
  if (!typing || commands === null) return null;
  const q = text.slice(1).toLowerCase();
  const found = commands.filter((c) => c.name.toLowerCase().startsWith(q));
  const ours = found.filter((c) => c.group === "main" || c.group === "pi"); // this agent's, and pi's own
  const others = found.length - ours.length;
  const matches = more || !ours.length ? found : ours;
  if (!commands.length)
    return (
      <div className="bg-card text-muted-foreground absolute inset-x-4 bottom-28 z-20 mx-auto max-w-(--thread-max-width) rounded-xl border px-3 py-2 text-xs shadow-lg">
        No command list from the agent server: restart it with npm run ui:server.
      </div>
    );
  if (!matches.length) return null;
  return (
    <div className="bg-card absolute inset-x-4 bottom-28 z-20 mx-auto max-w-(--thread-max-width) overflow-hidden rounded-xl border shadow-lg">
      <ul className="max-h-80 overflow-y-auto py-1">
        {matches.map((c, i) => (
          <li key={c.name}>
            {c.group !== matches[i - 1]?.group && <p className="text-muted-foreground px-3 pt-2 pb-1 text-xs">{c.group === "main" ? label : (c.group ?? "Commands")}</p>}
            <button
              type="button"
              onClick={() => aui.thread().composer().setText(`/${c.name} `)}
              className="hover:bg-accent flex w-full items-start gap-2 px-3 py-2 text-left text-sm"
            >
              <SquareSlash className="text-primary mt-0.5 size-4 shrink-0" />
              <span>
                <span className="font-mono">/{c.name}</span>
                {c.description && <span className="text-muted-foreground block text-xs">{c.description}</span>}
              </span>
            </button>
          </li>
        ))}
        {!more && ours.length > 0 && others > 0 && (
          <li>
            <button type="button" onClick={() => setMore(true)} className="text-muted-foreground hover:bg-accent w-full px-3 py-2 text-left text-xs">
              More: {others} command(s) from other extensions
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}
