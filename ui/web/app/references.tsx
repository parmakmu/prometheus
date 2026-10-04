"use client";

import { useCallback, useEffect, useState } from "react";
import { Download, X } from "lucide-react";
import { API, api, authHeaders, download, send } from "./api";
import { useAgent } from "./agent";
import { uploadsOf } from "./attachments";
import { useCanvas } from "./canvas";

// images get a real thumbnail where there is room for one
const IMAGE = /\.(png|jpe?g|gif|webp|svg)$/i;

// the icon that says what kind of file it is: VS Code's file icons (Material Icon Theme), served by /file-icon from the theme's own manifest
export function FileIcon({ name, className = "size-4" }: { name: string; className?: string }) {
  return <img src={`/file-icon?name=${encodeURIComponent(name)}`} alt="" draggable={false} className={`shrink-0 ${className}`} />;
}

// a folder in the same theme: named folders (scripts, runs, gates…) have their own
export function FolderIcon({ name, open = false, className = "size-4" }: { name: string; open?: boolean; className?: string }) {
  return <img src={`/file-icon?folder=1${open ? "&open=1" : ""}&name=${encodeURIComponent(name)}`} alt="" draggable={false} className={`shrink-0 ${className}`} />;
}

export const UPLOADS_CHANGED = "uploads-changed";

// where this chat's "+" put its files (attachments.ts): an agent's uploads/ folder, or your files folder for the planner
const where = (slug: string | null) => ({
  list: slug ? `/a/${slug}/tree?dir=uploads` : "/files?dir=chat-uploads",
  file: (name: string) => (slug ? `/a/${slug}/download?path=${encodeURIComponent(`uploads/${name}`)}` : `/files/download?path=${encodeURIComponent(`chat-uploads/${name}`)}`),
});

// an image from the server, fetched with the token (an <img> can't send it)
function useBlobUrl(url: string, on: boolean) {
  const [blob, setBlob] = useState<string | null>(null);
  useEffect(() => {
    if (!on) return;
    let made: string | null = null;
    fetch(`${API}${url}`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.blob() : Promise.reject()))
      .then((b) => setBlob((made = URL.createObjectURL(b))), () => {});
    return () => void (made && URL.revokeObjectURL(made));
  }, [url, on]);
  return blob;
}

function Preview({ file, name }: { file: string; name: string }) {
  const url = useBlobUrl(file, IMAGE.test(name));
  return (
    <div>
      <button type="button" onClick={() => void download(file, name)} className="text-muted-foreground hover:text-foreground mb-3 ml-auto flex items-center gap-1 text-xs">
        <Download className="size-3.5" /> Download
      </button>
      {url ? <img src={url} alt={name} className="max-w-full rounded-xl border" /> : <p className="text-muted-foreground text-sm">No preview for this file: download it instead.</p>}
    </div>
  );
}

function Thumb({ file, name }: { file: string; name: string }) {
  const url = useBlobUrl(file, IMAGE.test(name));
  return (
    <span className="bg-muted flex size-9 shrink-0 items-center justify-center overflow-hidden rounded-md border">
      {url ? <img src={url} alt="" className="size-full object-cover" /> : <FileIcon name={name} className="size-5" />}
    </span>
  );
}

// "Uploaded files": what you attached in the chat: read-only reference for the agent and its runs, never edited here. Each
// one has a thumbnail and a small x that removes it from your files folder.
export function References() {
  const { show, select } = useCanvas();
  const { slug } = useAgent();
  const at = where(slug);
  const [files, setFiles] = useState<{ name: string; size: number }[]>([]);
  const load = useCallback(() => void api<{ entries: { name: string; dir: boolean; size: number }[] }>(where(slug).list).then((l) => setFiles(l.entries.filter((e) => !e.dir)), () => setFiles([])), [slug]);
  useEffect(() => {
    load();
    window.addEventListener(UPLOADS_CHANGED, load);
    return () => window.removeEventListener(UPLOADS_CHANGED, load);
  }, [load]);
  const open = (name: string) => {
    const key = `upload:${slug}:${name}`;
    show({ key, title: name, node: <Preview file={at.file(name)} name={name} /> });
    select(key);
  };
  return (
    <section className="mb-5">
      <p className="text-muted-foreground text-xs font-medium">
        Uploaded files <span className="font-normal opacity-70">· read-only</span>
        {files.length > 0 && <span className="bg-muted ml-2 rounded-full px-2 py-0.5">{files.length}</span>}
      </p>
      {files.length === 0 && <p className="text-muted-foreground mt-2 px-2 text-xs">Nothing yet: use + in the chat, or drop files on it.</p>}
      <ul className="mt-2">
        {files.map((f) => (
          <li key={f.name} className="group hover:bg-accent/60 flex items-center gap-2 rounded-lg px-2 py-1">
            <button type="button" onClick={() => open(f.name)} className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm">
              <Thumb file={at.file(f.name)} name={f.name} />
              <span className="truncate">{f.name}</span>
            </button>
            <button type="button" title="Remove this file" onClick={() => void send(uploadsOf(slug).del(f.name), "DELETE").then(() => window.dispatchEvent(new Event(UPLOADS_CHANGED)))} className="text-muted-foreground hover:bg-accent hover:text-foreground rounded-full p-1">
              <X className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
