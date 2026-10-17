/** Files: your input files, uploads, an agent's outputs and folder, and what changed on its branch. */
import { lstatSync, createReadStream, createWriteStream, existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import { type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import { REPO, filesOf, send } from "./common.ts";
import { runIn, skillScript } from "./sandbox.ts";
// --- files: your input files, and agents' outputs -----------------------------------------------

export const MAX_PREVIEW = 200 * 1024;
export const MAX_UPLOAD = Number(process.env.MAX_UPLOAD_MB ?? 200) * 1024 * 1024;

// `rel` resolved inside `base`, or null: no ../, no absolute paths, no symlink out of it
// `rel` inside `base`, or null. Links are resolved, for a path that doesn't exist yet too (its nearest existing folder),
// and a link with nothing behind it is refused: an agent can leave links in its folder, and the router writes as you.
export function inside(base: string, rel: string): string | null {
  const p = path.resolve(base, rel.replace(/^\/+/, ""));
  if (p !== base && !p.startsWith(base + path.sep)) return null;
  const root = realpathSync(base);
  const within = (q: string) => q === root || q.startsWith(root + path.sep);
  let existing = p;
  while (!existsSync(existing)) {
    try {
      lstatSync(existing); // there, but its target isn't: a dangling link
      return null;
    } catch {}
    existing = path.dirname(existing);
  }
  return within(realpathSync(existing)) ? p : null;
}

export function listFiles(user: string, dir: string, res: ServerResponse) {
  const base = filesOf(user);
  mkdirSync(base, { recursive: true });
  const d = inside(base, dir);
  if (!d || !existsSync(d) || !statSync(d).isDirectory()) return send(res, 404, "no such folder");
  const entries = readdirSync(d, { withFileTypes: true })
    .map((e) => {
      const s = statSync(path.join(d, e.name));
      return { name: e.name, dir: e.isDirectory(), size: s.size, modified: s.mtime.toISOString() };
    })
    .sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name));
  send(res, 200, { root: base, dir: path.relative(base, d), path: d, entries });
}

// `unique`: a file already there keeps its name and this one becomes "name (2).ext" (two pasted image.png)
export function upload(base: string, rel: string, req: IncomingMessage, res: ServerResponse, unique = false) {
  let p = inside(base, rel);
  if (!p || p === base) return send(res, 400, "bad path");
  if (unique) {
    const { dir, name, ext } = path.parse(p);
    for (let n = 2; existsSync(p); n++) p = path.join(dir, `${name} (${n})${ext}`);
  }
  mkdirSync(path.dirname(p), { recursive: true });
  const out = createWriteStream(p);
  let size = 0;
  req.on("data", (c: Buffer) => {
    if ((size += c.length) <= MAX_UPLOAD) return;
    req.unpipe(out);
    out.destroy();
    rmSync(p, { force: true });
    if (!res.headersSent) send(res, 413, `larger than ${MAX_UPLOAD / 1024 / 1024} MB`);
  });
  req.pipe(out);
  out.on("finish", () => res.headersSent || send(res, 200, { ok: true, path: p }));
  out.on("error", (e) => res.headersSent || send(res, 500, String(e)));
}

export function sendFile(file: string | null, res: ServerResponse) {
  if (!file || !existsSync(file) || !statSync(file).isFile()) return send(res, 404, "no such file");
  res.writeHead(200, {
    "Content-Type": "application/octet-stream",
    "Content-Disposition": `attachment; filename="${path.basename(file).replace(/"/g, "")}"`,
    "Content-Length": statSync(file).size,
  });
  createReadStream(file).pipe(res);
}

// what the folder view of an agent never shows or serves: secrets, git's and uv's internals, session logs, the
// planner's record of how the plan was made (.plan), macOS litter
export const HIDDEN = /(^|\/)(\.git|\.venv|node_modules|__pycache__|\.DS_Store|\.gitkeep|\.env(\..*)?|\.pi\/sessions|\.plan)(\/|$)/i; // i: macOS's file system ignores case (.ENV is .env)

// `rel` inside the agent folder `cwd`, or null if it escapes, is hidden or (for a file) does not exist
// (checked both as asked and as resolved: a symlink such as runs/x/work/cfg -> ../../../.env must not serve .env)
export function inAgent(cwd: string, rel: string): string | null {
  const p = inside(cwd, rel);
  if (!p) return null;
  const shown = (file: string, root: string) => path.relative(root, file).split(path.sep).join("/");
  if (HIDDEN.test(shown(p, cwd))) return null;
  if (existsSync(p) && HIDDEN.test(shown(realpathSync(p), realpathSync(cwd)))) return null;
  return p;
}

// whether a run of the agent is active (its run.yaml says so): then nothing outside the scripts touches its git
export function runActive(cwd: string) {
  const runs = path.join(cwd, "runs");
  return existsSync(runs) && readdirSync(runs).some((id) => {
    const f = path.join(runs, id, "run.yaml");
    return existsSync(f) && /^status:\s*['"]?active['"]?\s*$/m.test(readFileSync(f, "utf8"));
  });
}

export function listAgentFolder(cwd: string, dir: string, res: ServerResponse) {
  const d = inAgent(cwd, dir);
  if (!d || !existsSync(d) || !statSync(d).isDirectory()) return send(res, 404, "no such folder");
  const entries = readdirSync(d, { withFileTypes: true })
    .filter((e) => inAgent(cwd, path.relative(cwd, path.join(d, e.name))))
    .map((e) => {
      const s = statSync(path.join(d, e.name));
      const rel = path.relative(cwd, path.join(d, e.name)).split(path.sep).join("/");
      return { name: e.name, dir: s.isDirectory(), size: s.size, modified: s.mtime.toISOString(), editable: s.isFile() && editable(rel) };
    })
    .sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name));
  send(res, 200, { root: cwd, dir: path.relative(cwd, d), path: d, entries });
}

// a file's text for the preview; a binary or large one answers `text: null` (the page offers a download)
export function readAgentFile(cwd: string, rel: string, res: ServerResponse) {
  const f = inAgent(cwd, rel);
  if (!f || !existsSync(f) || !statSync(f).isFile()) return send(res, 404, "no such file");
  const size = statSync(f).size;
  if (size > MAX_PREVIEW) return send(res, 200, { size, text: null });
  const bytes = readFileSync(f);
  send(res, 200, { size, text: bytes.includes(0) ? null : bytes.toString("utf8") });
}

// what the person may change in the page: the plan, programs, gates and the agent's own subagents. Not the files
// prometheus keeps in it (a refresh rewrites them from templates/agent: the role, plan-critic, batcher)
export const TEMPLATE = path.join(REPO, "templates", "agent");
export const editable = (rel: string) => /^(plan\.yaml|(scripts|gates|\.pi\/agents)\/.+)$/.test(rel) && !(rel.startsWith(".pi/") && existsSync(path.join(TEMPLATE, rel)));
export const MAX_EDIT = 1024 * 1024;

// Saves a file of the agent. Once a run exists the edit goes to a change branch (begin-change opens or continues
// revise/<date>-<n>), so main changes only by a reviewed merge; before the first run it is planning, on main.
export function saveAgentFile(user: string, cwd: string, rel: string, req: IncomingMessage, res: ServerResponse) {
  const f = inAgent(cwd, rel);
  if (!f || !editable(path.relative(cwd, f).split(path.sep).join("/")) || !existsSync(f) || !statSync(f).isFile()) return send(res, 400, "this file can't be edited here");
  const chunks: Buffer[] = [];
  let size = 0;
  req.on("data", (c: Buffer) => {
    size += c.length;
    if (size <= MAX_EDIT) chunks.push(c);
  });
  req.on("end", () => {
    if (size > MAX_EDIT) return send(res, 413, "larger than 1 MB");
    void skillScript(cwd, "agent_repo.py", ["--agent", cwd, "begin-change"]).then((r) => {
      let out: { ok?: boolean; error?: string; branch?: string; mode?: string } = {};
      try {
        out = JSON.parse(r.out);
      } catch {}
      if (r.code && !out.error) return send(res, 500, r.err || "agent_repo.py failed");
      if (out.error) return send(res, 409, out.error);
      writeFileSync(f, Buffer.concat(chunks));
      send(res, 200, { ok: true, branch: out.branch, mode: out.mode });
    });
  });
}

// --- what changed: the checked-out branch against main (or, on main, uncommitted edits against HEAD) ----------

export const gitIn = (cwd: string, args: string[]) => runIn(cwd, "git", args); // in its sandbox: the repo's config names commands
export const REVIEW_BRANCH = /^(run|revise|improve)\/[\w.-]+$/;

export async function baseOf(cwd: string) {
  const branch = (await gitIn(cwd, ["branch", "--show-current"])).out.trim();
  return { branch, base: branch && branch !== "main" ? "main" : "HEAD" };
}

export async function changes(cwd: string, res: ServerResponse) {
  const { branch, base } = await baseOf(cwd);
  const files = new Map<string, string>();
  for (const l of (await gitIn(cwd, ["diff", "--name-status", base])).out.split("\n").filter(Boolean)) {
    const [status, ...p] = l.split("\t");
    files.set(p[p.length - 1], status[0]);
  }
  for (const p of (await gitIn(cwd, ["ls-files", "--others", "--exclude-standard"])).out.split("\n").filter(Boolean)) files.set(p, "A");
  send(res, 200, { branch, base, files: [...files].filter(([p]) => inAgent(cwd, p)).map(([p, status]) => ({ path: p, status })) });
}

// a file's two sides for the side-by-side diff: as main (or HEAD) has it and as it is now; null where it doesn't exist
export async function diffOf(cwd: string, asked: string, res: ServerResponse) {
  const f = inAgent(cwd, asked);
  if (!f) return send(res, 400, "bad path");
  const rel = path.relative(cwd, f).split(path.sep).join("/");
  const { base } = await baseOf(cwd);
  const before = await gitIn(cwd, ["show", `${base}:${rel}`]);
  send(res, 200, { before: before.code ? null : before.out, after: existsSync(f) && statSync(f).isFile() ? readFileSync(f, "utf8") : null });
}

// put a file back as main (or HEAD) has it; a file new on the branch is deleted
export async function discardEdits(cwd: string, asked: string, res: ServerResponse) {
  const f = inAgent(cwd, asked);
  const rel = f ? path.relative(cwd, f).split(path.sep).join("/") : "";
  if (!f || !editable(rel)) return send(res, 400, "this file can't be changed here");
  // a wave's writer children are checked against the repo's staged index: leave it alone while a run is active
  if (runActive(cwd)) return send(res, 409, "a run is active: discard edits once it has finished");
  const { base } = await baseOf(cwd);
  const known = (await gitIn(cwd, ["cat-file", "-e", `${base}:${rel}`])).code === 0;
  if (known) {
    const r = await gitIn(cwd, ["restore", `--source=${base}`, "--worktree", "--staged", "--", rel]);
    if (r.code) return send(res, 500, r.err);
  } else {
    await gitIn(cwd, ["rm", "-q", "-f", "--ignore-unmatch", "--", rel]);
    rmSync(f, { force: true });
  }
  send(res, 200, { ok: true });
}

// check out a branch kept for review, so the agent can review it (its own tools work on the checked-out branch).
// Only from a clean main with no run active: nothing in progress may come along or be lost.
export async function checkoutBranch(cwd: string, name: string, res: ServerResponse) {
  if (!REVIEW_BRANCH.test(name)) return send(res, 400, "not a branch to review");
  if (runActive(cwd)) return send(res, 409, "a run is active: review branches once it has finished");
  const { branch } = await baseOf(cwd);
  if (branch === name) return send(res, 200, { ok: true, branch });
  if (branch !== "main") return send(res, 409, `${branch} is checked out: finish its review first (merge, keep or discard)`);
  if ((await gitIn(cwd, ["status", "--porcelain"])).out.trim()) return send(res, 409, "main has uncommitted changes: they would come along to the branch");
  const r = await gitIn(cwd, ["switch", "-q", name]);
  if (r.code) return send(res, 409, r.err.trim() || "git switch failed");
  send(res, 200, { ok: true, branch: name });
}

// the checked-out branch goes through the script (it also switches back to main); another one is just deleted
export function discardBranch(user: string, cwd: string, name: string, res: ServerResponse) {
  if (!REVIEW_BRANCH.test(name)) return send(res, 400, "not a branch to review");
  if (runActive(cwd)) return send(res, 409, "a run is active: discard branches once it has finished");
  void baseOf(cwd).then(({ branch }) => {
    if (branch !== name) return void gitIn(cwd, ["branch", "-q", "-D", name]).then((r) => (r.code ? send(res, 409, r.err) : send(res, 200, { ok: true })));
    void skillScript(cwd, "agent_repo.py", ["--agent", cwd, "merge", "--action", "discard"]).then((r) => {
      let out: { error?: string } = {};
      try {
        out = JSON.parse(r.out);
      } catch {}
      if (out.error || (r.code && !r.out)) return send(res, 409, out.error ?? (r.err || "agent_repo.py failed"));
      send(res, 200, { ok: true });
    });
  });
}
