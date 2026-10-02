// The router's address, and the token it prints at start ("open …/?token=…"): read from the link
// once, kept for this tab, and sent with every request.
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8787";

export function token(): string {
  if (typeof window === "undefined") return "";
  const url = new URL(window.location.href);
  const fromLink = url.searchParams.get("token");
  if (fromLink) {
    sessionStorage.setItem("prometheus-token", fromLink);
    url.searchParams.delete("token"); // keep it out of the address bar and history
    window.history.replaceState(null, "", url);
  }
  return sessionStorage.getItem("prometheus-token") ?? "";
}

export const authHeaders = (): Record<string, string> => ({ Authorization: `Bearer ${token()}` });

export const NO_ACCESS = "The agent server refused this page: open the link that `npm run ui:server` printed (…/?token=…).";

export async function api<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { headers: authHeaders() });
  if (r.status === 403) throw new Error(NO_ACCESS);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

// a request that changes something (upload, delete, resize, keys); throws on refusal
export async function send(path: string, method: "PUT" | "POST" | "DELETE", body?: BodyInit) {
  const r = await fetch(`${API}${path}`, { method, headers: authHeaders(), body });
  if (r.status === 403) throw new Error(NO_ACCESS);
  if (!r.ok) throw new Error(`${path}: ${await r.text()}`);
}

// a download: fetched with the token, then handed to the browser as a file
export async function download(path: string, name: string) {
  const r = await fetch(`${API}${path}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  const url = URL.createObjectURL(await r.blob());
  Object.assign(document.createElement("a"), { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
