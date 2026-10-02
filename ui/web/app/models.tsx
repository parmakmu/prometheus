"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Search } from "lucide-react";
import { api, send } from "./api";

type ModelInfo = { key: string; provider: string; id: string; name: string; contextWindow?: number; reasoning: boolean };

const k = (n?: number) => (!n ? "" : n >= 1e6 ? `${(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M` : `${Math.round(n / 1e3)}k`);

// The models this conversation can use (those you have credentials for), searchable; a click switches
export function ModelPicker({ base, onClose }: { base: string; onClose?: () => void }) {
  const [data, setData] = useState<{ current: string | null; models: ModelInfo[] } | null>(null);
  const [q, setQ] = useState("");
  const load = () => void api<{ current: string | null; models: ModelInfo[] }>(`${base}/models`).then(setData, () => setData({ current: null, models: [] }));
  useEffect(load, [base]);
  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return (data?.models ?? []).filter((m) => words.every((w) => `${m.key} ${m.name}`.toLowerCase().includes(w)));
  }, [data, q]);
  const choose = async (m: ModelInfo) => {
    setData((d) => d && { ...d, current: m.key });
    await send(`${base}/model`, "POST", JSON.stringify({ key: m.key })).catch(load);
    onClose?.(); // a choice made: the picker is done
  };
  return (
    <div>
      <div className="flex items-center gap-2">
        <p className="text-muted-foreground flex-1 text-xs tracking-wide uppercase">Model</p>
        {onClose && (
          <button type="button" onClick={onClose} className="hover:bg-accent rounded-md border px-2.5 py-0.5 text-xs">
            Cancel
          </button>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2 rounded-lg border px-3 py-1.5">
        <Search className="text-muted-foreground size-4" />
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${data?.models.length ?? ""} models`} className="flex-1 bg-transparent text-sm outline-none" />
      </div>
      <ul className="mt-3 divide-y overflow-hidden rounded-xl border">
        {!data && <li className="text-muted-foreground px-3 py-4 text-sm">Loading…</li>}
        {data && shown.length === 0 && <li className="text-muted-foreground px-3 py-4 text-sm">No model matches.</li>}
        {shown.slice(0, 200).map((m) => (
          <li key={m.key}>
            <button type="button" onClick={() => void choose(m)} className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${m.key === data?.current ? "bg-accent" : "hover:bg-accent/60"}`}>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{m.name}</span>
                <span className="text-muted-foreground block truncate font-mono text-xs">{m.key}</span>
              </span>
              <span className="text-muted-foreground shrink-0 text-xs">{[k(m.contextWindow), m.reasoning ? "thinks" : ""].filter(Boolean).join(" · ")}</span>
              {m.key === data?.current && <Check className="text-primary size-4 shrink-0" />}
            </button>
          </li>
        ))}
      </ul>
      {shown.length > 200 && <p className="text-muted-foreground mt-2 text-xs">Showing 200 of {shown.length}: search to narrow.</p>}
    </div>
  );
}
