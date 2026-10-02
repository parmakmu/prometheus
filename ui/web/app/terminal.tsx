"use client";

import { useEffect, useRef, useState } from "react";
import { API, authHeaders, send } from "./api";

// A terminal screen fed by an event stream (base64 chunks, "exit" at the end), sending keys and sizes
// back: pi itself (TerminalPanel), or an extension's panel inside the chat (cards.tsx).
export function LiveTerminal({ base, query = "", height, onExit }: { base: string; query?: string; height: number; onExit?: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [state, setState] = useState("connecting…");
  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      if (disposed || !box.current) return;
      const term = new Terminal({ fontSize: 13, cursorBlink: true, theme: { background: "#0b0b0f" } });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(box.current);
      fit.fit();
      const size = () => void send(`${base}/resize`, "POST", JSON.stringify({ cols: term.cols, rows: term.rows })).catch(() => {});
      size();
      // the screen, as an event stream read with fetch (the token goes as a header, and errors show)
      const ctl = new AbortController();
      void (async () => {
        let bytes = 0;
        try {
          const r = await fetch(`${API}${base}/stream?cols=${term.cols}&rows=${term.rows}${query}`, { headers: authHeaders(), signal: ctl.signal });
          if (!r.ok || !r.body) return setState(`couldn't connect: HTTP ${r.status} ${await r.text()}`);
          setState("connected");
          const reader = r.body.getReader();
          const dec = new TextDecoder();
          let buf = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            const events = buf.split("\n\n");
            buf = events.pop() ?? "";
            for (const e of events) {
              if (e.startsWith("event: exit")) return (setState("closed"), onExit?.());
              if (e.startsWith("event: frame")) {
                // a whole frame (an extension's panel): clear, then its lines
                const text = new TextDecoder().decode(Uint8Array.from(atob(e.split("data: ")[1] ?? ""), (c) => c.charCodeAt(0)));
                term.write("\x1b[?25l\x1b[H\x1b[2J" + text.replace(/\n/g, "\r\n"));
                setState("connected");
                continue;
              }
              const data = e.startsWith("data: ") ? e.slice(6) : "";
              if (!data) continue;
              const chunk = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
              bytes += chunk.length;
              term.write(chunk);
              setState(`connected, ${bytes} bytes`);
            }
          }
          setState("stream ended");
        } catch (err) {
          if (!ctl.signal.aborted) setState(`stream failed: ${String((err as Error).message ?? err)}`);
        }
      })();
      const stream = { close: () => ctl.abort() };
      const keys = term.onData((d) => void send(`${base}/input`, "POST", d).catch(() => {}));
      const resized = new ResizeObserver(() => (fit.fit(), size()));
      resized.observe(box.current);
      term.focus();
      cleanup = () => (resized.disconnect(), keys.dispose(), stream.close(), term.dispose());
    })();
    return () => {
      disposed = true;
      cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base]);
  return (
    <div>
      <div ref={box} style={{ height }} className="overflow-hidden rounded-lg bg-[#0b0b0f] p-2" />
      <p className="text-muted-foreground mt-1 font-mono text-[10px]">{state}</p>
    </div>
  );
}

// pi itself, in a real terminal, in the agent's sandbox (its own session)
export function TerminalPanel({ agent }: { agent: string }) {
  const [ended, setEnded] = useState(false);
  return (
    <div className="flex h-full flex-col">
      <LiveTerminal base={`${agent}/term`} height={460} onExit={() => setEnded(true)} />
      <p className="text-muted-foreground mt-2 text-xs">
        {ended ? "pi has exited. Reopen the tab to start it again." : "pi in this agent's sandbox, with its own session; it stops a minute after you close it."}
      </p>
    </div>
  );
}
