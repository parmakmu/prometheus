"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, CheckCircle2, Circle, Loader2, Lock, Pencil } from "lucide-react";
import { useAui } from "@assistant-ui/react";
import { API, api, send, sendJson } from "./api";
import { useAgent } from "./agent";
import { useCanvas } from "./canvas";
import { FileView } from "./files";
import { BranchChip, ChangesSection } from "./changes";
import { FileIcon, FolderIcon, References } from "./references";
import { ChildName } from "./subagents";
import { money, took } from "./format";

export type Stage = { key: string; title?: string; status: "done" | "running" | null; children?: { done: number; failed: number; total: number; items?: { key: string; agent?: string; state?: string; run_id?: string | null }[] } };
// cost_usd: its subagents' (pi-subagents' records); agent_cost_usd: the agent's own model's (its sessions)
export type Run = { id: string; status: string; started_at?: string; finished_at?: string; note?: string | null; cost_usd: number; agent_cost_usd?: number };
export const runCost = (r: Run) => r.cost_usd + (r.agent_cost_usd ?? 0);
export const costSplit = (r: Run) => `agent ${money(r.agent_cost_usd ?? 0)} · subagents ${money(r.cost_usd)}`;
export type AgentSummary = {
  slug: string;
  state: string;
  active_run: string | null;
  next_wave: string | null;
  next_stages: string[];
  waiting: "approval" | "review" | "replan" | null;
  stages: Stage[];
  stages_done: number;
  stages_total: number;
  recent_runs: Run[];
  unmerged: string[];
  branch: string | null;
};

type Entry = { name: string; dir: boolean; editable?: boolean }; // editable: the router says (prometheus's own files are not)

function Section({ title, hint, count, children, open = true }: { title: string; hint?: string; count?: string; children: ReactNode; open?: boolean }) {
  const [shown, setShown] = useState(open);
  return (
    <section className="mb-5">
      <button type="button" onClick={() => setShown((s) => !s)} className="text-muted-foreground flex w-full items-center gap-1 text-xs font-medium">
        {title}
        {hint && <span className="font-normal opacity-70">· {hint}</span>}
        {count && <span className="bg-muted ml-auto rounded-full px-2 py-0.5">{count}</span>}
        {shown ? <ChevronDown className={`size-3.5 ${count ? "" : "ml-auto"}`} /> : <ChevronRight className={`size-3.5 ${count ? "" : "ml-auto"}`} />}
      </button>
      {shown && <div className="mt-2">{children}</div>}
    </section>
  );
}

// A folder you can open in place: its entries load when it first opens, files open in the drawer
function Tree({ slug, dir, depth = 0, editable }: { slug: string; dir: string; depth?: number; editable?: boolean }) {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  useEffect(() => {
    api<{ entries: Entry[] }>(`/a/${slug}/tree?dir=${encodeURIComponent(dir)}`).then((l) => setEntries(l.entries), () => setEntries([]));
  }, [slug, dir]);
  return (
    <>
      {entries?.length === 0 && <p className="text-muted-foreground py-1 text-xs" style={{ paddingLeft: 8 + depth * 14 }}>Empty</p>}
      {entries?.map((e) => (
        <Node key={e.name} slug={slug} path={`${dir}/${e.name}`.replace(/^\//, "")} dir={e.dir} depth={depth} editable={editable && (e.dir || e.editable)} locked={editable && !e.dir && !e.editable ? KEPT : undefined} />
      ))}
    </>
  );
}

// `meta`: quiet facts at the row's end (a run's duration and cost), `metaTitle` on hover; `locked`: why a file among
// the editable ones is not, shown as a lock
function Node({ slug, path, dir, depth = 0, label, meta, metaTitle, editable, locked }: { slug: string; path: string; dir: boolean; depth?: number; label?: string; meta?: string; metaTitle?: string; editable?: boolean; locked?: string }) {
  const { show, select } = useCanvas();
  const [open, setOpen] = useState(false);
  const key = `file:${slug}:${path}`;
  const name = label ?? path.split("/").pop()!;
  const row = "hover:bg-accent/60 flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left text-sm";
  const pad = { paddingLeft: 8 + depth * 14 };
  return (
    <>
      {dir ? (
        <button type="button" onClick={() => setOpen((o) => !o)} className={row} style={pad}>
          {open ? <ChevronDown className="text-muted-foreground size-3.5 shrink-0" /> : <ChevronRight className="text-muted-foreground size-3.5 shrink-0" />}
          <FolderIcon name={name.split(" ")[0]} open={open} />
          <span className="min-w-0 flex-1 truncate">{name}</span>
          {/* a folder of files you can edit says so before it is opened */}
          {editable && <Pencil className="size-3 shrink-0 text-emerald-700" aria-label="You can edit the files in this folder" />}
          {meta && <span title={metaTitle} className="text-muted-foreground ml-auto shrink-0 text-xs">{meta}</span>}
        </button>
      ) : (
        <button type="button" onClick={() => (show({ key, title: name, node: <FileView slug={slug} path={path} editable={editable} /> }), select(key))} className={row} style={{ paddingLeft: 8 + depth * 14 + 18 }}>
          <FileIcon name={name} />
          <span className="min-w-0 flex-1 truncate">{name}</span>
          {editable && <Pencil className="size-3 shrink-0 text-emerald-700" aria-label="You can edit this file" />}
          {locked && (
            <span title={locked}>
              <Lock className="text-muted-foreground size-3 shrink-0" />
            </span>
          )}
        </button>
      )}
      {dir && open && <Tree slug={slug} dir={path} depth={depth + 1} editable={editable} />}
    </>
  );
}

// what the run is doing right now, in one line: the running steps, or what it waits for
// the run's line under "Steps": only what isn't already in the list, i.e. that it waits for you (or is replanning)
function Now({ w }: { w: AgentSummary }) {
  if (w.stages.some((s) => s.status === "running") || !w.waiting) return null;
  const text = { approval: "Waiting for your approval in the chat", review: "Waiting for your review in the chat", replan: "Adjusting the plan" }[w.waiting];
  return <p className={`px-2 pb-1 text-xs ${w.waiting === "replan" ? "text-muted-foreground" : "text-violet-700"}`}>{text}</p>;
}

// A plain list: an icon says where each step is (done, running, waiting); a running step names its subagents in one
// quiet line under it, each opening its log
function Steps({ w }: { w: AgentSummary }) {
  const next = new Set(w.waiting || !w.stages.some((s) => s.status === "running") ? w.next_stages : []);
  const icon = (s: Stage) =>
    s.status === "done" ? <CheckCircle2 className="size-4 text-emerald-600" /> : s.status === "running" ? <Loader2 className="size-4 animate-spin text-sky-600" /> : <Circle className="text-muted-foreground/60 size-4" />;
  return (
    <Section title="Steps" count={w.active_run ? `${w.stages_done}/${w.stages_total}` : undefined}>
      {w.active_run && <p className="text-muted-foreground px-2 pb-1 text-xs">Run <span className="font-mono">{w.active_run}</span></p>}
      {!w.active_run && w.recent_runs[0] && (
        <p className="text-muted-foreground px-2 pb-1 text-xs">
          Last run <span className="font-mono">{w.recent_runs[0].id}</span> · {w.recent_runs[0].status}
          {w.recent_runs[0].finished_at && ` · ${new Date(w.recent_runs[0].finished_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`}
        </p>
      )}
      {w.active_run && <Now w={w} />}
      <ul>
        {w.stages.length === 0 && <li className="text-muted-foreground px-2 text-sm">No stages yet</li>}
        {w.stages.map((s) => (
          <li key={s.key} className="px-2 py-1">
            <span className={`flex items-start gap-2 text-sm ${s.status === "running" ? "font-medium" : !s.status ? "text-muted-foreground" : ""}`}>
              <span className="mt-0.5 shrink-0">{icon(s)}</span>
              <span className="min-w-0 flex-1">{s.title || s.key}</span>
              {next.has(s.key) && <span className="text-muted-foreground shrink-0 text-xs font-normal">next</span>}
            </span>
            {s.status === "running" && (s.children?.items?.length ?? 0) > 0 && (
              <span className="text-muted-foreground ms-6 flex flex-wrap gap-x-2 text-xs">
                {s.children!.items!.map((c) => (
                  <ChildName key={c.key} c={{ key: c.key, agent: c.agent, state: c.state, runId: c.run_id }} />
                ))}
              </span>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}

// what each agent's Input and Output last showed: the page remounts on every switch (AgentRuntime is keyed by the
// conversation), so they start from it instead of empty, then refresh
const lastInputs = new Map<string, Set<string>>();
const lastOutputs = new Map<string, { run: string; path: string }[]>();

// what shapes a run, as a small file explorer: what the person reviews and changes; read-only, with why: the role
// (and in subagents plan-critic and batcher), which a refresh rewrites, and the record /agent-improve keeps
const KEPT = "Read-only: prometheus keeps this file (a refresh rewrites it)";
const INPUT: { path: string; dir: boolean; label?: string; readOnly?: string }[] = [
  { path: "plan.yaml", dir: false },
  { path: ".pi/APPEND_SYSTEM.md", dir: false, readOnly: KEPT },
  { path: "scripts", dir: true },
  { path: "gates", dir: true },
  { path: ".pi/agents", dir: true, label: "subagents" },
  { path: "improvements.yaml", dir: false, readOnly: "Read-only: /agent-improve's record of what it proposed and what you decided" },
].sort((a, b) => Number(b.dir) - Number(a.dir) || (a.label ?? a.path.split("/").pop()!).localeCompare(b.label ?? b.path.split("/").pop()!)); // folders first, then by name, as in a folder

function Input({ slug }: { slug: string }) {
  const [have, setHave] = useState<Set<string> | null>(() => lastInputs.get(slug) ?? null);
  useEffect(() => {
    const names = (dir: string) => api<{ entries: Entry[] }>(`/a/${slug}/tree?dir=${dir}`).then((l) => l.entries.map((e) => `${dir}/${e.name}`.replace(/^\//, "")), () => [] as string[]);
    Promise.all([names(""), names(".pi")]).then((all) => {
      const found = new Set(all.flat());
      lastInputs.set(slug, found);
      setHave(found);
    });
  }, [slug]);
  return (
    <Section title="Input" hint="you can edit these">
      {INPUT.filter((i) => have?.has(i.path)).map((i) => (
        <Node key={i.path} slug={slug} {...i} editable={!i.readOnly} locked={i.readOnly} />
      ))}
    </Section>
  );
}

// each run a folder (its record, results, working files), read-only, with how long it took and what it cost; its
// status only when it is not done. The latest run's results show right under it: what is usually wanted.
type Param = { name: string; description?: string; type: "path" | "text" | "secret"; required: boolean; env: string; set: boolean; value: string | null };

// The values a run needs, set here instead of one by one in chat; a secret only as set or not (it goes in .env)
function Params({ slug }: { slug: string }) {
  const [params, setParams] = useState<Param[] | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const load = () => api<{ params: Param[] }>(`/a/${slug}/params`).then((r) => (setParams(r.params), setEdits({})), () => setParams([]));
  useEffect(() => void load(), [slug]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!params?.length) return null;
  const save = () =>
    send(`/a/${slug}/params`, "POST", JSON.stringify(edits)).then(
      () => (setNote("Saved."), load()),
      (e: Error) => setNote(e.message),
    );
  return (
    <Section title="Settings" hint="this machine's values" count={`${params.filter((p) => p.set).length}/${params.length}`}>
      {params.map((p) => (
        <label key={p.name} className="mb-2 block text-xs">
          <span className="font-medium">{p.name}</span>
          {p.required && !p.set && <span className="text-destructive"> · needed</span>}
          {p.description && <span className="text-muted-foreground block">{p.description}</span>}
          {p.type === "secret" ? (
            <span className="text-muted-foreground block">{p.set ? "set" : `add ${p.env}=… to the agent's .env`}</span>
          ) : (
            <input className="bg-background mt-1 w-full rounded border px-2 py-1" value={edits[p.name] ?? p.value ?? ""}
              placeholder={p.type === "path" ? "/absolute/path" : ""} onChange={(e) => setEdits({ ...edits, [p.name]: e.target.value })} />
          )}
        </label>
      ))}
      {Object.keys(edits).length > 0 && (
        <button type="button" onClick={save} className="bg-primary text-primary-foreground rounded px-3 py-1 text-xs">Save</button>
      )}
      {note && <p className="text-muted-foreground mt-1 text-xs">{note}</p>}
    </Section>
  );
}

type Network = { enforced: boolean; base: string[]; allowed: string[]; requested: string[]; refused: string[] };

// What this agent may reach on the internet (SANDBOX=podman): hosts you allowed, and those its plan asks for or that
// it was refused, to allow with one click. Kept in your folder, outside the agent's sandbox; the proxy rereads it at once.
function NetworkAccess({ slug }: { slug: string }) {
  const [n, setN] = useState<Network | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const load = () => api<Network>(`/a/${slug}/network`).then(setN, () => setN(null));
  useEffect(() => void load(), [slug]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!n?.enforced) return null;
  const save = (allow: string[]) =>
    send(`/a/${slug}/network`, "POST", JSON.stringify({ allow })).then(() => (setNote(null), load()), (e: Error) => setNote(e.message));
  const offered = [...new Set([...n.requested, ...n.refused])];
  const chip = "rounded-full border px-2 py-0.5";
  return (
    <Section title="Internet" hint="what this agent may reach" count={String(n.allowed.length)} open={offered.length > 0}>
      <p className="text-muted-foreground mb-2 text-xs">Always: the model providers and PyPI. Allowed for this agent:</p>
      <div className="mb-2 flex flex-wrap gap-1 text-xs">
        {n.allowed.length === 0 && <span className="text-muted-foreground">nothing else</span>}
        {n.allowed.map((h) => (
          <button key={h} type="button" className={chip} title="Remove" onClick={() => save(n.allowed.filter((x) => x !== h))}>
            {h} ×
          </button>
        ))}
      </div>
      {offered.length > 0 && (
        <>
          <p className="text-muted-foreground mb-1 text-xs">Asked for by its plan, or refused when it tried:</p>
          <div className="flex flex-wrap gap-1 text-xs">
            {offered.map((h) => (
              <button key={h} type="button" className={`${chip} border-dashed`} title={n.refused.includes(h) ? "Refused when the agent tried: allow it" : "Its plan asks for it: allow it"} onClick={() => save([...n.allowed, h])}>
                + {h}
              </button>
            ))}
          </div>
        </>
      )}
      {note && <p className="text-muted-foreground mt-1 text-xs">{note}</p>}
    </Section>
  );
}

type ApiKey = { id: string; label: string; created: string };

// The agent's run API (ui/api.ts): keys that let a script or another agent call this agent, and only it. A key is
// shown once, when made: the server keeps only its hash.
function ApiAccess({ slug }: { slug: string }) {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [made, setMade] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const load = () => api<ApiKey[]>(`/a/${slug}/api-keys`).then(setKeys, () => setKeys([]));
  useEffect(() => (setMade(null), void load()), [slug]); // eslint-disable-line react-hooks/exhaustive-deps
  const make = () => {
    const label = prompt("What is this key for? (a name to recognise it by)");
    if (label === null) return;
    sendJson<{ key: string }>(`/a/${slug}/api-keys`, "POST", JSON.stringify({ label })).then(
      (r) => (setMade(r.key), setNote(null), load()),
      (e: Error) => setNote(e.message),
    );
  };
  const revoke = (k: ApiKey) => confirm(`Revoke the key "${k.label}"? Whatever uses it can no longer call this agent.`) &&
    send(`/a/${slug}/api-keys?id=${k.id}`, "DELETE").then(load, (e: Error) => setNote(e.message));
  const base = `${API}/a/${slug}/api`;
  return (
    <Section title="API" hint="call this agent from a script or another agent" count={String(keys.length)} open={false}>
      <p className="text-muted-foreground mb-2 text-xs">
        <code>POST {base}/runs</code> with <code>{"{request, params, files}"}</code> starts a run; questions wait for the caller. See the web app&apos;s README, &quot;The run API&quot;.
      </p>
      {made && (
        <div className="mb-2 rounded border border-dashed p-2 text-xs">
          <p className="mb-1 font-medium">Copy it now: it isn&apos;t shown again.</p>
          <code className="block break-all select-all">{made}</code>
          <button type="button" className="mt-1 underline" onClick={() => void navigator.clipboard.writeText(made)}>Copy</button>
        </div>
      )}
      {keys.map((k) => (
        <div key={k.id} className="mb-1 flex items-center justify-between gap-2 text-xs">
          <span className="truncate">{k.label} <span className="text-muted-foreground">· {k.id} · {new Date(k.created).toLocaleDateString()}</span></span>
          <button type="button" className="text-muted-foreground hover:text-red-600" onClick={() => void revoke(k)}>Revoke</button>
        </div>
      ))}
      <button type="button" onClick={make} className="bg-primary text-primary-foreground mt-1 rounded px-3 py-1 text-xs">New key</button>
      {note && <p className="text-muted-foreground mt-1 text-xs">{note}</p>}
    </Section>
  );
}

function Output({ w }: { w: AgentSummary }) {
  const [files, setFiles] = useState<{ run: string; path: string }[]>(() => lastOutputs.get(w.slug) ?? []);
  const latest = w.recent_runs[0];
  useEffect(
    () => void api<{ run: string; path: string }[]>(`/a/${w.slug}/outputs`).then((f) => (lastOutputs.set(w.slug, f), setFiles(f)), () => setFiles([])),
    [w.slug, latest?.id, latest?.status],
  );
  return (
    <Section title="Output" hint="read-only" count={String(w.recent_runs.length)}>
      {w.recent_runs.length === 0 && <p className="text-muted-foreground px-2 text-sm">No runs yet</p>}
      {w.recent_runs.map((r) => (
        <div key={r.id}>
          <Node slug={w.slug} path={`runs/${r.id}`} dir label={r.status === "done" ? r.id : `${r.id} · ${r.status}`} meta={[took(r.started_at, r.finished_at), money(runCost(r))].filter(Boolean).join(" · ")} metaTitle={costSplit(r)} />
          {r === latest && files.filter((f) => f.run === r.id).map((f) => (
            <Node key={f.path} slug={w.slug} path={f.path} dir={false} depth={1} label={f.path.split("/").slice(3).join("/")} />
          ))}
        </div>
      ))}
    </Section>
  );
}

// Branches waiting for your review: edits saved here (a revise/ branch), a change, or a finished run's discoveries.
// Reviewing one checks it out if it was kept aside, then hands it to the agent: /agent-revise for a change
// (the critic if the plan changed, then merge, keep or discard), /agent-run for a run's branch (it asks).
const CHANGE = /^(revise|improve|change)\//;
function ChangeBanner({ w }: { w: AgentSummary }) {
  const aui = useAui();
  const { slug } = useAgent(); // the chat must be this agent's agent to hand it the review
  const [error, setError] = useState<string | null>(null);
  const waiting = w.unmerged.filter((b) => CHANGE.test(b) || b.startsWith("run/"));
  if (!waiting.length) return null;
  const review = async (b: string) => {
    setError(null);
    try {
      if (b !== w.branch) {
        await send(`/a/${w.slug}/checkout?name=${encodeURIComponent(b)}`, "POST");
        window.dispatchEvent(new Event("agents-changed"));
      }
      aui.thread().append(CHANGE.test(b) ? "/agent-revise take the changes on this branch through review: check them, run the plan critic if the plan changed, then ask me to merge, keep or discard" : "/agent-run");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="mb-4 rounded-xl border border-violet-200 bg-violet-50 p-3 text-sm text-violet-900">
      <p>Waiting for your review:</p>
      {waiting.map((b) => (
        <p key={b} className="mt-2 flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate font-mono text-xs">{b}{b === w.branch ? " (checked out)" : ""}</span>
          {slug === w.slug ? (
            <button type="button" onClick={() => void review(b)} className="shrink-0 rounded-md bg-violet-700 px-2 py-1 text-xs text-white">
              Review and merge
            </button>
          ) : (
            <span className="shrink-0 text-xs">open its chat to review</span>
          )}
        </p>
      ))}
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
    </div>
  );
}

export function Workspace({ w }: { w: AgentSummary }) {
  return (
    <div className="flex min-h-full flex-col">
      <p className="mb-4 flex flex-wrap items-center gap-2 text-sm font-semibold">
        {/* whose: the agent open in the chat (its name is in the chat's header and the sidebar) */}
        Workspace
        {w.branch && <BranchChip branch={w.branch} />}
      </p>
      <ChangeBanner w={w} />
      <Steps w={w} />
      <Output w={w} />
      <ChangesSection slug={w.slug} branches={w.unmerged} branch={w.branch} />
      <References />
      <div className="mt-auto border-t pt-4">
        <Params slug={w.slug} />
        <NetworkAccess slug={w.slug} />
        <ApiAccess slug={w.slug} />
        <Input slug={w.slug} />
      </div>
    </div>
  );
}
