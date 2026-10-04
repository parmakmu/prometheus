"use client";

import { useCallback, useEffect, useState } from "react";
import { GitBranch, Trash2 } from "lucide-react";
import { api, send } from "./api";
import { useCanvas } from "./canvas";

export type Changes = { branch: string; base: string; files: { path: string; status: string }[] };

const changed = () => window.dispatchEvent(new Event("workflows-changed"));

// the checked-out branch and what differs from main (on main: uncommitted edits), kept current while edits happen
export function useChanges(slug: string, version?: unknown) {
  const [data, setData] = useState<Changes | null>(null);
  const load = useCallback(() => void api<Changes>(`/w/${slug}/changes`).then(setData, () => setData(null)), [slug]);
  useEffect(() => {
    load();
    window.addEventListener("workflows-changed", load);
    return () => window.removeEventListener("workflows-changed", load);
  }, [load, version]);
  return data;
}

export const BranchChip = ({ branch }: { branch: string }) => (
  <span title="The branch checked out in this workflow" className={`flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-xs font-normal ${branch === "main" ? "bg-muted" : "bg-violet-100 text-violet-800"}`}>
    <GitBranch className="size-3" /> {branch}
  </span>
);

// a unified diff, added and removed lines coloured
export function DiffView({ slug, path }: { slug: string; path: string }) {
  const [diff, setDiff] = useState<string | null>(null);
  useEffect(() => void api<{ diff: string }>(`/w/${slug}/diff?path=${encodeURIComponent(path)}`).then((d) => setDiff(d.diff), () => setDiff("")), [slug, path]);
  if (diff === null) return null;
  if (!diff.trim()) return <p className="text-muted-foreground text-sm">No difference from main.</p>;
  return (
    <pre className="bg-card overflow-auto rounded-xl border py-2 text-xs">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={`px-3 whitespace-pre-wrap ${l.startsWith("+") && !l.startsWith("+++") ? "bg-emerald-100/70 text-emerald-900" : l.startsWith("-") && !l.startsWith("---") ? "bg-red-100/70 text-red-900" : l.startsWith("@@") ? "text-sky-700" : l.startsWith("diff") || l.startsWith("index") || l.startsWith("---") || l.startsWith("+++") ? "text-muted-foreground" : ""}`}>
          {l || " "}
        </div>
      ))}
    </pre>
  );
}

export async function discardEdits(slug: string, path: string) {
  if (!confirm(`Discard your edits to ${path}? It goes back to how main has it.`)) return false;
  await send(`/w/${slug}/discard?path=${encodeURIComponent(path)}`, "POST");
  changed();
  return true;
}

export async function discardBranch(slug: string, name: string) {
  if (!confirm(`Discard ${name}? Every change on it is deleted.`)) return false;
  await send(`/w/${slug}/discard-branch?name=${encodeURIComponent(name)}`, "POST");
  changed();
  return true;
}

// "Changes" in the workspace: the branch (discardable), the files that differ, and branches kept for review
export function ChangesSection({ slug, branches, branch }: { slug: string; branches: string[]; branch: string | null }) {
  const c = useChanges(slug, branch);
  const { show, select } = useCanvas();
  const [error, setError] = useState<string | null>(null);
  const attempt = (f: () => Promise<unknown>) => f().catch((e: Error) => setError(e.message));
  const reviewable = branches.filter((b) => /^(run|revise|improve|change)\//.test(b));
  if (!c?.files.length && !reviewable.length) return null;
  const open = (path: string) => {
    const key = `diff:${slug}:${path}`;
    show({ key, title: `${path.split("/").pop()} (changes)`, node: <DiffView slug={slug} path={path} /> });
    select(key);
  };
  return (
    <section className="mb-5">
      <p className="text-muted-foreground text-xs font-medium">Changes{c?.files.length ? <span className="bg-muted ml-2 rounded-full px-2 py-0.5">{c.files.length}</span> : null}</p>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      <ul className="mt-2">
        {c?.files.map((f) => (
          <li key={f.path} className="group hover:bg-accent/60 flex items-center gap-2 rounded-md px-2 py-1 text-sm">
            <span className={`w-4 font-mono text-xs ${f.status === "A" ? "text-emerald-700" : f.status === "D" ? "text-red-700" : "text-amber-700"}`}>{f.status}</span>
            <button type="button" onClick={() => open(f.path)} className="min-w-0 flex-1 truncate text-left">{f.path}</button>
            <button type="button" title="Discard edits" onClick={() => void attempt(() => discardEdits(slug, f.path))} className="text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-red-600">
              <Trash2 className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
      {reviewable.map((b) => (
        <p key={b} className="mt-1 flex items-center gap-2 px-2 text-xs">
          <BranchChip branch={b} />
          <span className="text-muted-foreground">{b === c?.branch ? "checked out" : "kept"}</span>
          <button type="button" onClick={() => void attempt(() => discardBranch(slug, b))} className="text-muted-foreground ml-auto flex items-center gap-1 hover:text-red-600">
            <Trash2 className="size-3.5" /> Discard branch
          </button>
        </p>
      ))}
    </section>
  );
}
