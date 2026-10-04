"use client";

import { useEffect, useState } from "react";
import { Download, Lock, Trash2 } from "lucide-react";
import { api, download, sendJson } from "./api";
import { CodeEditor } from "./code-editor";
import { BranchChip, DiffView, discardEdits, useChanges } from "./changes";

const size = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 1e3 ? `${(n / 1e3).toFixed(1)} kB` : `${n} B`);

// a file's text, or a note that it has no preview; `editable` makes it an editor with a Save button
function FileBody({ name, size: bytes, text, onDownload, save }: { name: string; size: number; text: string | null; onDownload: () => void; save?: (text: string) => Promise<string> }) {
  const [draft, setDraft] = useState(text ?? "");
  const [saved, setSaved] = useState(text ?? "");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const dirty = draft !== saved;
  const doSave = async () => {
    if (!save || !dirty || busy) return;
    setBusy(true);
    try {
      setNote({ ok: true, text: await save(draft) });
      setSaved(draft);
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  };
  return (
    <div>
      <div className="text-muted-foreground mb-2 flex items-center gap-2 text-xs">
        <span className="truncate font-mono">{name}</span>
        <span>{size(bytes)}</span>
        {!save && <span className="bg-muted flex items-center gap-1 rounded-full px-2 py-0.5"><Lock className="size-3" /> read-only</span>}
        {save && dirty && <span className="text-amber-700">unsaved</span>}
        {save && (
          <button type="button" disabled={!dirty || busy} onClick={() => void doSave()} className="bg-primary text-primary-foreground ml-auto rounded-md px-2 py-1 disabled:opacity-40">
            {busy ? "Saving…" : "Save"}
          </button>
        )}
        <button type="button" onClick={onDownload} className={`hover:text-foreground flex items-center gap-1 ${save ? "" : "ml-auto"}`}>
          <Download className="size-3.5" /> Download
        </button>
      </div>
      {note && <p className={`mb-2 text-xs ${note.ok ? "text-emerald-700" : "text-red-600"}`}>{note.text}</p>}
      {text === null ? <p className="text-muted-foreground text-sm">No preview for this file: download it instead.</p> : <CodeEditor name={name} value={text} onChange={save ? setDraft : undefined} onSave={() => void doSave()} />}
    </div>
  );
}

// one file of an agent (a path from its root), opened in the drawer; editable ones save to a change branch
export function FileView({ slug, path, editable }: { slug: string; path: string; editable?: boolean }) {
  const [file, setFile] = useState<{ size: number; text: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0); // a discard puts the file back: read it again, into a fresh editor
  useEffect(() => {
    setFile(null);
    api<{ size: number; text: string | null }>(`/a/${slug}/file?path=${encodeURIComponent(path)}`).then(setFile, (e: Error) => setError(e.message));
  }, [slug, path, reload]);
  const save = async (text: string) => {
    const r = await sendJson<{ branch: string; mode: string }>(`/a/${slug}/file?path=${encodeURIComponent(path)}`, "PUT", text);
    window.dispatchEvent(new Event("agents-changed"));
    return r.mode === "planning" ? "Saved. The agent has no run yet, so this edits its plan directly." : `Saved on ${r.branch}. Review and merge it from the Workspace.`;
  };
  const c = useChanges(slug);
  const [diff, setDiff] = useState(false);
  const differs = !!c?.files.some((f) => f.path === path);
  if (error) return <p className="text-xs text-red-600">{error}</p>;
  if (!file) return null;
  const tab = (on: boolean) => `rounded-md px-2 py-1 text-xs ${on ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/60"}`;
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        {c && <BranchChip branch={c.branch} />}
        {differs && (
          <>
            <button type="button" onClick={() => setDiff(false)} className={tab(!diff)}>File</button>
            <button type="button" onClick={() => setDiff(true)} className={tab(diff)}>Changes</button>
            <button type="button" onClick={() => void discardEdits(slug, path).then((done) => done && (setDiff(false), setReload((n) => n + 1))).catch((e: Error) => alert(e.message))} className="text-muted-foreground ml-auto flex items-center gap-1 text-xs hover:text-red-600">
              <Trash2 className="size-3.5" /> Discard edits
            </button>
          </>
        )}
      </div>
      {diff && differs && <DiffView slug={slug} path={path} />}
      <div className={diff && differs ? "hidden" : ""}>
        <FileBody name={path} {...file} save={editable ? save : undefined} onDownload={() => void download(`/a/${slug}/download?path=${encodeURIComponent(path)}`, path.split("/").pop()!)} />
      </div>
    </div>
  );
}
