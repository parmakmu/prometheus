"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Copy, Download, FileText, Folder, FolderUp, Trash2, Upload } from "lucide-react";
import { api, download, send } from "./api";

type Entry = { name: string; dir: boolean; size: number; modified: string };
type Listing = { root: string; dir: string; path: string; entries: Entry[] };

const size = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 1e3 ? `${(n / 1e3).toFixed(1)} kB` : `${n} B`);
const join = (...p: string[]) => p.filter(Boolean).join("/");

// Your input files, on the server: workflows read them from here (read-only). Give a run the path shown.
export function FilesPanel() {
  const [dir, setDir] = useState("");
  const [list, setList] = useState<Listing | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const load = useCallback(() => {
    api<Listing>(`/files?dir=${encodeURIComponent(dir)}`).then(setList, (e: Error) => setError(e.message));
  }, [dir]);
  useEffect(load, [load]);

  const uploadAll = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    for (const [i, f] of [...files].entries()) {
      const rel = join(dir, (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name);
      setBusy(`uploading ${i + 1} of ${files.length}: ${rel}`);
      try {
        await send(`/files?path=${encodeURIComponent(rel)}`, "PUT", f);
      } catch (e) {
        setError(String((e as Error).message));
        break;
      }
    }
    setBusy(null);
    load();
  };
  const remove = async (name: string) => {
    if (!confirm(`Delete ${name}?`)) return;
    await send(`/files?path=${encodeURIComponent(join(dir, name))}`, "DELETE").catch((e) => setError(e.message));
    load();
  };
  const crumbs = dir ? dir.split("/") : [];

  return (
    <div>
      <p className="text-muted-foreground text-xs tracking-wide uppercase">Your files</p>
      <div className="mt-1 flex flex-wrap items-center gap-1 text-sm">
        <button type="button" onClick={() => setDir("")} className="font-medium hover:underline">files</button>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1">
            <ChevronRight className="text-muted-foreground size-3.5" />
            <button type="button" onClick={() => setDir(crumbs.slice(0, i + 1).join("/"))} className="hover:underline">{c}</button>
          </span>
        ))}
      </div>
      {list && (
        <button
          type="button"
          onClick={() => navigator.clipboard.writeText(list.path)}
          title="Copy: give this path when a run asks for a folder"
          className="bg-muted mt-2 flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left font-mono text-xs"
        >
          <Copy className="size-3.5 shrink-0" />
          <span className="truncate">{list.path}</span>
        </button>
      )}
      <div className="mt-3 flex gap-2">
        <button type="button" onClick={() => filesInput.current?.click()} className="hover:bg-accent flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm">
          <Upload className="size-4" /> Upload files
        </button>
        <button type="button" onClick={() => folderInput.current?.click()} className="hover:bg-accent flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm">
          <FolderUp className="size-4" /> Upload a folder
        </button>
        <input ref={filesInput} type="file" multiple hidden onChange={(e) => void uploadAll(e.target.files)} />
        <input ref={folderInput} type="file" hidden {...{ webkitdirectory: "" }} onChange={(e) => void uploadAll(e.target.files)} />
      </div>
      {busy && <p className="text-muted-foreground mt-2 text-xs">{busy}</p>}
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      <ul className="bg-card mt-3 divide-y overflow-hidden rounded-xl border">
        {list?.entries.length === 0 && <li className="text-muted-foreground px-3 py-6 text-center text-sm">Empty. Upload the files your workflows should read.</li>}
        {list?.entries.map((e) => (
          <li key={e.name} className="group flex items-center gap-2 px-3 py-2 text-sm">
            {e.dir ? <Folder className="text-primary size-4 shrink-0" /> : <FileText className="text-muted-foreground size-4 shrink-0" />}
            {e.dir ? (
              <button type="button" onClick={() => setDir(join(dir, e.name))} className="truncate hover:underline">{e.name}</button>
            ) : (
              <span className="truncate">{e.name}</span>
            )}
            <span className="text-muted-foreground ml-auto shrink-0 text-xs">{e.dir ? "" : size(e.size)}</span>
            {!e.dir && (
              <button type="button" title="Download" onClick={() => void download(`/files/download?path=${encodeURIComponent(join(dir, e.name))}`, e.name)} className="text-muted-foreground hover:text-foreground">
                <Download className="size-3.5" />
              </button>
            )}
            <button type="button" title="Delete" onClick={() => void remove(e.name)} className="text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-red-600">
              <Trash2 className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// a workflow's results (runs/<id>/outputs/), to download
export function Outputs({ slug }: { slug: string }) {
  const [files, setFiles] = useState<{ run: string; path: string; size: number }[] | null>(null);
  useEffect(() => {
    api<{ run: string; path: string; size: number }[]>(`/w/${slug}/outputs`).then(setFiles, () => setFiles([]));
  }, [slug]);
  if (!files) return null;
  return (
    <ul className="bg-card divide-y overflow-hidden rounded-xl border">
      {files.length === 0 && <li className="text-muted-foreground px-3 py-4 text-center text-sm">No results yet</li>}
      {files.map((f) => (
        <li key={f.path} className="flex items-center gap-2 px-3 py-2 text-sm">
          <FileText className="text-muted-foreground size-4 shrink-0" />
          <span className="truncate">{f.path.split("/").pop()}</span>
          <span className="text-muted-foreground font-mono text-xs">{f.run}</span>
          <span className="text-muted-foreground ml-auto text-xs">{size(f.size)}</span>
          <button type="button" title="Download" onClick={() => void download(`/w/${slug}/download?path=${encodeURIComponent(f.path)}`, f.path.split("/").pop()!)} className="text-muted-foreground hover:text-foreground">
            <Download className="size-3.5" />
          </button>
        </li>
      ))}
    </ul>
  );
}
