import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from "@assistant-ui/react";
import { send, sendJson } from "./api";
import { UPLOADS_CHANGED } from "./references";

const changed = () => window.dispatchEvent(new Event(UPLOADS_CHANGED));

// The chat's "+": a file goes to the server as soon as it is picked, and the message carries its path, so the
// agent can use it. A name already taken there gets " (2)" added (two pasted image.png), so each attachment has its own file.
const paths = new Map<string, string>(); // attachment id → path on the server

// "image" is what makes the chat draw a picture's own thumbnail; anything else is shown by its type icon
const kind = (file: File) => (file.type.startsWith("image/") ? "image" : "file");

// The file goes to the uploads/ folder of the one you're talking to: the agent's, or prometheus's for the planner.
export const uploadsOf = (slug: string | null) => {
  const base = slug ? `/a/${slug}` : "/p";
  return { put: (name: string) => `${base}/upload?unique=1&path=${encodeURIComponent(name)}`, del: (name: string) => `${base}/upload?path=${encodeURIComponent(name)}` };
};

export const filesAdapter = (slug: string | null): AttachmentAdapter => ({
  accept: "*", // any file: assistant-ui matches "*" only ("*/*" matched nothing, so every file was refused)
  async add({ file }): Promise<PendingAttachment> {
    const id = crypto.randomUUID();
    let r: { path: string };
    try {
      r = await sendJson<{ path: string }>(uploadsOf(slug).put(file.name), "PUT", file);
    } catch (e) { // a tile that says it failed, rather than nothing
      const message = e instanceof TypeError ? "Can't reach the agent server" : (e as Error).message;
      return { id, type: kind(file), name: file.name, contentType: file.type, file, status: { type: "incomplete", reason: "error", message } };
    }
    paths.set(id, r.path);
    changed();
    return { id, type: kind(file), name: r.path.split("/").pop() ?? file.name, contentType: file.type, file, status: { type: "requires-action", reason: "composer-send" } };
  },
  async send(a): Promise<CompleteAttachment> {
    return { ...a, status: { type: "complete" }, content: [{ type: "text", text: `[attached file ${a.name}: ${paths.get(a.id)}]` }] };
  },
  async remove(a) {
    const p = paths.get(a.id);
    paths.delete(a.id);
    if (p) await send(uploadsOf(slug).del(p.split("/").pop()!), "DELETE").catch(() => {}); // the name it got on the server
    changed();
  },
});
