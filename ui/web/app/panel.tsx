"use client";

import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { API, authHeaders, send } from "./api";

// An extension's panel (/ccstyle, /goal…) as chat content. The worker sends whole frames: the lines the
// extension's component drew, with terminal styling (ANSI). They become styled spans here; keys and
// clicks go back as the keys the panel expects.

type Span = { text: string; style: CSSProperties };

const BASIC = ["#1e1e1e", "#c62828", "#2e7d32", "#a8770a", "#1565c0", "#8e24aa", "#00838f", "#616161"];
const BRIGHT = ["#757575", "#e53935", "#43a047", "#c49000", "#1e88e5", "#ab47bc", "#00acc1", "#424242"];
function xterm256(n: number): string {
  if (n < 8) return BASIC[n];
  if (n < 16) return BRIGHT[n - 8];
  if (n < 232) {
    const v = [0, 95, 135, 175, 215, 255];
    const i = n - 16;
    return `rgb(${v[Math.floor(i / 36)]},${v[Math.floor(i / 6) % 6]},${v[i % 6]})`;
  }
  const g = 8 + (n - 232) * 10;
  return `rgb(${g},${g},${g})`;
}
// colours meant for a dark terminal: very light ones would vanish on a light card, so they become the text colour
function readable(color: string): string | undefined {
  const m = color.match(/rgb\((\d+),(\d+),(\d+)\)/);
  if (!m) return color;
  const [r, g, b] = m.slice(1).map(Number);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 200 ? undefined : color;
}

// one line of terminal text into styled spans (SGR colours, bold, dim, italic, underline, inverse)
export function ansiLine(line: string): Span[] {
  const spans: Span[] = [];
  let st: { fg?: string; bg?: string; bold?: boolean; dim?: boolean; italic?: boolean; underline?: boolean; inverse?: boolean } = {};
  const clean = line.replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, ""); // OSC (links, titles)
  const re = /\x1b\[([0-9;]*)m|\x1b\[[0-9;?]*[A-Za-z]/g;
  let last = 0;
  const push = (text: string) => {
    if (!text) return;
    const fg = st.inverse ? st.bg ?? "#ffffff" : st.fg;
    const bg = st.inverse ? st.fg ?? "#1e1e1e" : st.bg;
    spans.push({
      text,
      style: {
        color: fg ? readable(fg) : undefined,
        background: bg,
        fontWeight: st.bold ? 600 : undefined,
        opacity: st.dim ? 0.6 : undefined,
        fontStyle: st.italic ? "italic" : undefined,
        textDecoration: st.underline ? "underline" : undefined,
        borderRadius: bg ? 3 : undefined,
      },
    });
  };
  for (let m: RegExpExecArray | null; (m = re.exec(clean)); ) {
    push(clean.slice(last, m.index));
    last = re.lastIndex;
    if (m[1] === undefined) continue; // not a colour code: dropped
    const codes = (m[1] || "0").split(";").map(Number);
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 0) st = {};
      else if (c === 1) st.bold = true;
      else if (c === 2) st.dim = true;
      else if (c === 3) st.italic = true;
      else if (c === 4) st.underline = true;
      else if (c === 7) st.inverse = true;
      else if (c === 22) st.bold = st.dim = false;
      else if (c === 23) st.italic = false;
      else if (c === 24) st.underline = false;
      else if (c === 27) st.inverse = false;
      else if (c >= 30 && c <= 37) st.fg = BASIC[c - 30];
      else if (c >= 90 && c <= 97) st.fg = BRIGHT[c - 90];
      else if (c === 39) st.fg = undefined;
      else if (c >= 40 && c <= 47) st.bg = BASIC[c - 40];
      else if (c >= 100 && c <= 107) st.bg = BRIGHT[c - 100];
      else if (c === 49) st.bg = undefined;
      else if ((c === 38 || c === 48) && codes[i + 1] === 5) {
        const col = xterm256(codes[i + 2]);
        if (c === 38) st.fg = col;
        else st.bg = col;
        i += 2;
      } else if ((c === 38 || c === 48) && codes[i + 1] === 2) {
        const col = `rgb(${codes[i + 2]},${codes[i + 3]},${codes[i + 4]})`;
        if (c === 38) st.fg = col;
        else st.bg = col;
        i += 4;
      }
    }
  }
  push(clean.slice(last));
  return spans;
}
const plain = (line: string) => ansiLine(line).map((s) => s.text).join("");
// a tab row ("Style / Features / …"): the active tab is the one not dimmed
const activeTab = (line: string) => ansiLine(line).find((s) => s.text.trim() && s.text.trim() !== "/" && s.style.opacity === undefined)?.text.trim();

// the panel's tab row ("Style / Features / …"), drawn as pills; clicking one switches to it
const isTabRow = (lines: string[], i: number) => plain(lines[i]).includes(" / ") && lines.map(plain).findIndex((t) => t.includes(" / ")) === i;
function TabRow({ line, onTab }: { line: string; onTab: (name: string) => void }) {
  return (
    <div className="mb-2 flex flex-wrap gap-1 font-sans">
      {plain(line)
        .trim()
        .split(" / ")
        .map((name) => (
          <button key={name} type="button" onClick={() => onTab(name.trim())} className={`rounded-full px-2.5 py-0.5 text-xs ${activeTab(line) === name.trim() ? "bg-primary text-primary-foreground" : "hover:bg-accent text-muted-foreground"}`}>
            {name.trim()}
          </button>
        ))}
    </div>
  );
}

// browser keys as the terminal keys a panel reads
function keyOf(e: KeyboardEvent): string | null {
  const named: Record<string, string> = {
    Enter: "\r", Escape: "\x1b", Tab: e.shiftKey ? "\x1b[Z" : "\t", Backspace: "\x7f", Delete: "\x1b[3~",
    ArrowUp: "\x1b[A", ArrowDown: "\x1b[B", ArrowRight: "\x1b[C", ArrowLeft: "\x1b[D",
    Home: "\x1b[H", End: "\x1b[F", PageUp: "\x1b[5~", PageDown: "\x1b[6~",
  };
  if (named[e.key]) return named[e.key];
  if (e.ctrlKey && e.key.length === 1) return String.fromCharCode(e.key.toLowerCase().charCodeAt(0) - 96);
  return e.key.length === 1 ? e.key : null;
}

// the standard pi-tui widgets a frame reports (the worker hijacks their render), drawn as native controls
type Widget =
  | { kind: "settings"; selected: number; items: { id: string; label: string; description?: string; value: string; values?: string[]; custom: boolean }[] }
  | { kind: "select"; selected: number; items: { value: string; label: string; description?: string }[] }
  | { kind: "input"; value: string };

const SWITCH = [["on", "off"], ["true", "false"], ["yes", "no"], ["enabled", "disabled"]];
const switchPair = (values?: string[]) =>
  values?.length === 2 ? SWITCH.find(([a, b]) => values.map((v) => v.toLowerCase()).sort().join() === [a, b].sort().join()) : undefined;

function Native({ widgets, act }: { widgets: Widget[]; act: (a: Record<string, unknown>) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <div className="space-y-1 font-sans">
      {widgets.map((w, wi) => {
        if (w.kind === "settings")
          return w.items.map((it) => {
            const pair = switchPair(it.values);
            const on = pair && it.value.toLowerCase() === pair[0];
            return (
              <div key={`${wi}-${it.id}`} className="flex items-start gap-4 rounded-lg px-1 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{it.label}</p>
                  {it.description && <p className="text-muted-foreground text-xs">{it.description}</p>}
                </div>
                {pair ? (
                  <button
                    type="button"
                    role="switch"
                    aria-checked={on}
                    onClick={() => act({ widget: wi, op: "set", id: it.id, value: it.values!.find((v) => v.toLowerCase() === (on ? pair[1] : pair[0])) })}
                    className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors ${on ? "bg-primary" : "bg-muted-foreground/30"}`}
                  >
                    <span className={`absolute top-0.5 size-4 rounded-full bg-white shadow transition-all ${on ? "left-4.5" : "left-0.5"}`} />
                  </button>
                ) : it.values?.length && it.values.length <= 4 && !it.custom ? (
                  <div className="flex shrink-0 overflow-hidden rounded-lg border text-xs">
                    {it.values.map((v) => (
                      <button key={v} type="button" onClick={() => act({ widget: wi, op: "set", id: it.id, value: v })} className={`px-2.5 py-1 ${v === it.value ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}>
                        {v}
                      </button>
                    ))}
                  </div>
                ) : it.values?.length && !it.custom ? (
                  <select value={it.value} onChange={(e) => act({ widget: wi, op: "set", id: it.id, value: e.target.value })} className="bg-background shrink-0 rounded-lg border px-2 py-1 text-xs">
                    {it.values.map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </select>
                ) : (
                  <button type="button" onClick={() => act({ widget: wi, op: "edit", id: it.id })} className="hover:bg-accent flex shrink-0 items-center gap-2 rounded-lg border px-2.5 py-1 text-xs">
                    <span className="font-mono">{it.value || "—"}</span>
                    <span className="text-muted-foreground">Change</span>
                  </button>
                )}
              </div>
            );
          });
        if (w.kind === "select")
          return (
            <div key={wi} className="space-y-2 py-1">
              {w.items.map((it, i) => (
                <button key={it.value} type="button" onClick={() => act({ widget: wi, op: "choose", index: i })} className={`hover:border-primary hover:bg-accent w-full rounded-lg border px-3 py-2 text-left text-sm ${i === w.selected ? "border-primary" : ""}`}>
                  <span className="font-medium">{it.label}</span>
                  {it.description && <span className="text-muted-foreground block text-xs">{it.description}</span>}
                </button>
              ))}
            </div>
          );
        return (
          <form key={wi} className="flex gap-2 py-1" onSubmit={(e) => (e.preventDefault(), act({ widget: wi, op: "submit", value: draft ?? w.value }), setDraft(null))}>
            <input autoFocus value={draft ?? w.value} onChange={(e) => setDraft(e.target.value)} className="flex-1 rounded-lg border px-3 py-1.5 text-sm" />
            <button type="submit" className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">Save</button>
            <button type="button" onClick={() => (setDraft(null), act({ widget: wi, op: "cancel" }))} className="text-muted-foreground px-2 text-sm">Cancel</button>
          </form>
        );
      })}
    </div>
  );
}

export function PanelView({ base }: { base: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const act = (a: Record<string, unknown>) => void send(`${base}/action`, "POST", JSON.stringify(a)).catch(() => {});
  const [state, setState] = useState("opening…");
  const key = (k: string) => void send(`${base}/input`, "POST", k).catch(() => {});

  useEffect(() => {
    const ctl = new AbortController();
    (async () => {
      try {
        const r = await fetch(`${API}${base}/stream`, { headers: authHeaders(), signal: ctl.signal });
        if (!r.ok || !r.body) return setState(`couldn't open the panel: HTTP ${r.status}`);
        setState("");
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
            if (e.startsWith("event: exit")) return;
            if (!e.startsWith("event: frame")) continue;
            const frame = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(e.split("data: ")[1] ?? ""), (c) => c.charCodeAt(0))));
            setWidgets(frame.widgets ?? []);
            const all: string[] = frame.lines ?? [];
            while (all.length && !plain(all[all.length - 1]).trim()) all.pop(); // no empty rows below
            setLines(all);
          }
        }
      } catch (err) {
        if (!ctl.signal.aborted) setState(`panel stream failed: ${String((err as Error).message ?? err)}`);
      }
    })();
    return () => ctl.abort();
  }, [base]);

  // the panel's width in characters follows the card's
  useEffect(() => {
    if (!box.current) return;
    const fit = () => {
      const width = box.current?.clientWidth ?? 640;
      void send(`${base}/resize`, "POST", JSON.stringify({ cols: Math.max(40, Math.floor((width - 32) / 7.8)), rows: 24 })).catch(() => {});
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(box.current);
    box.current.focus();
    return () => ro.disconnect();
  }, [base]);

  // a click on a row moves the selection there (the row marked with an arrow is the current one); a click
  // on a tab name switches to that tab
  const click = (i: number, double: boolean) => {
    const text = lines.map(plain);
    const tabs = text[i].includes(" / ") && text.findIndex((t) => t.includes(" / ")) === i;
    if (tabs) return;
    const current = text.findIndex((t) => /^\s*(→|›|>|❯)\s/.test(t));
    if (current < 0) return double ? key("\r") : undefined;
    const steps = i - current;
    for (let n = 0; n < Math.abs(steps); n++) key(steps > 0 ? "\x1b[B" : "\x1b[A");
    if (double) key("\r");
  };
  const clickTab = (name: string) => {
    const text = lines.map(plain);
    const row = text.find((t) => t.includes(" / "));
    if (!row) return;
    const names = row.trim().split(" / ").map((s) => s.trim());
    const active = activeTab(lines[text.indexOf(row)]);
    const from = Math.max(0, names.indexOf(active ?? names[0]));
    const to = names.indexOf(name);
    for (let n = 0; n < (to - from + names.length) % names.length; n++) key("\t");
  };

  return (
    <div className="my-3 w-full max-w-(--thread-max-width)">
      <div
        ref={box}
        tabIndex={0}
        onKeyDown={(e) => {
          const k = keyOf(e);
          if (k === null) return;
          e.preventDefault();
          key(k);
        }}
        className="bg-card focus-visible:ring-primary/40 rounded-xl border px-4 py-3 font-mono text-[13px] leading-6 shadow-sm outline-none focus-visible:ring-2"
      >
        {state && <p className="text-muted-foreground font-sans text-sm">{state}</p>}
        {widgets.length > 0 && (
          <>
            {lines.map((line, i) => (isTabRow(lines, i) ? <TabRow key={i} line={line} onTab={clickTab} /> : null))}
            <Native widgets={widgets} act={act} />
          </>
        )}
        {widgets.length === 0 && lines.map((line, i) => {
          const text = plain(line);
          if (/^[─━-]{8,}$/.test(text.trim())) return <hr key={i} className="my-1.5 border-t" />; // rules drawn with ─
          if (isTabRow(lines, i)) return <TabRow key={i} line={line} onTab={clickTab} />;
          return (
            <div key={i} onClick={() => click(i, false)} onDoubleClick={() => click(i, true)} className="hover:bg-accent/50 cursor-default rounded px-1 whitespace-pre">
              {ansiLine(line).map((s, j) => (
                <span key={j} style={s.style}>
                  {s.text}
                </span>
              ))}
              {"​"}
            </div>
          );
        })}
      </div>
      <div className="text-muted-foreground mt-1 flex items-center gap-3 text-xs">
        {widgets.length === 0 && <span>Click a row to select it, double-click to change it; or use the keys.</span>}
        <button type="button" onClick={() => key("\x1b")} className="hover:text-foreground ml-auto rounded-md border px-2 py-0.5">
          Done
        </button>
      </div>
    </div>
  );
}
