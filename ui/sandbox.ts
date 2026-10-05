/** Sandboxes: what a worker, terminal or router command may see, described once (`describe`: mounts, environment,
 * working folder) and run by the backend SANDBOX names: bwrap.ts (bubblewrap) or podman.ts (rootless Podman, with the
 * internet by allow-list). See ui/README.md, "How a sandbox is built". */
import { execFile, spawn } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { bwrapCommand } from "./bwrap.ts";
import { DEV_USER, EMPTY, HOME, NODE_PREFIX, PI_AGENT, PI_PACKAGE_ROOT, REPO, SANDBOX, SUBAGENTS_ROOT, USERS_ROOT, filesOf, folderName, ownOf, prometheusOf, pythonFor, sessionDirOf } from "./common.ts";
import { podmanCommand, removeOwnContainers } from "./podman.ts";

/** One thing a sandbox sees: a folder or file of your machine at `dst` (read-only or writable), or an empty tmpfs. */
export type Mount = { kind: "ro" | "rw"; src: string; dst: string } | { kind: "tmpfs"; dst: string };
/** A sandbox, whatever runs it: what it sees, its environment (nothing else of the router's), where it starts. */
export type Spec = { mounts: Mount[]; env: Record<string, string>; cwd: string; tty: boolean };

const ro = (p: string, dst = p): Mount[] => (existsSync(p) ? [{ kind: "ro", src: p, dst }] : []);
const rw = (p: string, dst = p): Mount[] => (existsSync(p) ? [{ kind: "rw", src: p, dst }] : []);

// the planner creates and refreshes agents but never needs their secrets or conversations: each
// .env reads as empty, each .pi/sessions as an empty folder
function hideSecrets(agents: string): Mount[] {
  if (!existsSync(agents)) return [];
  return readdirSync(agents).flatMap((s): Mount[] => [
    ...(existsSync(path.join(agents, s, ".env")) ? [{ kind: "ro" as const, src: EMPTY, dst: path.join(agents, s, ".env") }] : []),
    ...(existsSync(path.join(agents, s, ".pi", "sessions")) ? [{ kind: "tmpfs" as const, dst: path.join(agents, s, ".pi", "sessions") }] : []),
  ]);
}

// Pi's state is code that runs outside every sandbox (your terminal's pi loads its extensions, packages, settings,
// MCP servers, models.json's `!command` keys), so sandboxes get it read-only; what Pi and pi-subagents write while
// they run stays writable. settings.json is a copy per user: /model in the web app saves there, not into yours.
const PI_STATE_WRITTEN = ["sessions", "missions", "pi-subagents", "tmp", "auth.json", "mcp-auth.json", "models-store.json", "crashes.json", "mcp.log", "run-history.jsonl"];
function piState(user: string, agentDir: string): Mount[] {
  for (const d of ["sessions", "missions", "pi-subagents", "tmp"]) mkdirSync(path.join(agentDir, d), { recursive: true });
  const settings = path.join(ownOf(user), "pi-settings.json");
  if (existsSync(path.join(agentDir, "settings.json"))) copyFileSync(path.join(agentDir, "settings.json"), settings);
  else writeFileSync(settings, "{}\n");
  // The folder itself is the sandbox's own, empty and thrown away (Pi makes lock files beside what it reads:
  // trust.json.lock; a read-only folder made it fail before loading any package), each entry of yours mounted into it
  return [
    { kind: "tmpfs", dst: PI_AGENT },
    // on a team, npm is the shared packages' (describe), not a user's own
    ...readdirSync(agentDir).filter((n) => n !== "settings.json" && !(USERS_ROOT && n === "npm")).map((n): Mount =>
      ({ kind: PI_STATE_WRITTEN.includes(n) ? "rw" : "ro", src: path.join(agentDir, n), dst: path.join(PI_AGENT, n) })),
    { kind: "rw", src: settings, dst: path.join(PI_AGENT, "settings.json") },
  ];
}

// What in an agent's folder is code something runs outside its sandbox: your terminal's pi loads its .pi/ (extensions,
// skills, settings, mcp.json, subagent profiles, whose `extensions:` load code), git its config and hooks. Read-only
// to its model; what a run writes there (conversations, pi-subagents' state, packages, the role's lessons) stays
// writable.
function agentCodeReadOnly(cwd: string): Mount[] {
  for (const d of [".pi/sessions", ".pi/subagents"]) mkdirSync(path.join(cwd, d), { recursive: true });
  const at = (r: string) => path.join(cwd, r);
  return [
    ...ro(at(".pi")),
    ...[".pi/sessions", ".pi/subagents", ".pi/npm", ".pi/APPEND_SYSTEM.md"].flatMap((r) => rw(at(r))),
    ...[".git/config", ".git/hooks", ".git/info"].flatMap((r) => ro(at(r))),
  ];
}

/** What a sandbox for this user's prometheus (`agent` "prometheus") or agent ("a/<slug>") sees, at the same paths as
 * outside: the system read-only, a home of its own, node and Pi, your Pi state (read-only but what Pi writes), and
 * prometheus read-only with its agents (the planner) or the agent's own folder (its code read-only). */
export function describe(user: string, agent: string, cwd: string, env: Record<string, string> = {}, writable: string[] = [], tty = false, readable: string[] = []): Spec {
  const root = prometheusOf(user);
  const own = ownOf(user);
  const home = path.join(own, "home");
  mkdirSync(home, { recursive: true });
  const agentDir = USERS_ROOT ? path.join(own, "pi-agent") : PI_AGENT; // one person: your own Pi state
  mkdirSync(filesOf(user), { recursive: true });
  const files = [filesOf(user), ...(process.env.SANDBOX_READ ?? "").split(",").filter(Boolean)];
  const mounts: Mount[] = [
    ...ro("/usr"), ...ro("/etc"), { kind: "tmpfs", dst: "/tmp" },
    { kind: "rw", src: home, dst: HOME }, // a home of its own; what follows is mounted into it
    ...ro(NODE_PREFIX), ...ro(path.dirname(path.dirname(PI_PACKAGE_ROOT))), ...ro(path.join(REPO, "ui")), ...ro(path.join(REPO, "node_modules")),
    ...piState(user, agentDir), ...(USERS_ROOT ? ro(path.join(PI_AGENT, "npm")) : []), // shared Pi packages
    ...(agent === "prometheus"
      ? [...ro(root), ...rw((mkdirSync(sessionDirOf(root), { recursive: true }), sessionDirOf(root))), ...rw(path.join(root, "agents")), ...hideSecrets(path.join(root, "agents"))]
      : [...ro(path.join(root, ".venv")), ...rw(cwd), ...agentCodeReadOnly(cwd)]),
    ...files.flatMap((f) => ro(f)),
    ...readable.flatMap((f) => ro(f)),
    ...writable.flatMap((f) => rw(f)),
  ];
  return {
    mounts, cwd, tty,
    env: {
      HOME, USER: user, LANG: process.env.LANG ?? "C.UTF-8", TERM: "xterm-256color",
      PATH: `${path.join(NODE_PREFIX, "bin")}:/usr/local/bin:/usr/bin:/bin`,
      PYTHONDONTWRITEBYTECODE: "1", // the code is read-only
      PI_SUBAGENTS_TEMP_ROOT: SUBAGENTS_ROOT,
      ...env,
    },
  };
}

/** [command, args] that run `command` for this user's prometheus or agent in its sandbox (as is, with SANDBOX=off). */
export function sandboxed(user: string, agent: string, cwd: string, command: string[], env: Record<string, string>, writable: string[] = [], tty = false, readable: string[] = []): [string, string[]] {
  if (SANDBOX === "off") return [command[0], command.slice(1)];
  const spec = describe(user, agent, cwd, env, writable, tty, readable);
  return SANDBOX === "podman" ? podmanCommand(user, agent, spec, command) : bwrapCommand(spec, command);
}

// the environment `bwrap` or `podman` itself starts with: bwrap clears it anyway; podman finds its storage through the router's
export const launcherEnv = () => (SANDBOX === "bwrap" ? {} : process.env);

// A command the router runs on a user's prometheus or one of its agents, inside that one's sandbox: the router itself is
// unsandboxed, and an agent can write its folder, git's config there included, which names commands git runs (on
// `git status`, `diff`, …). Prometheus's skill scripts are mounted read-only for it; the agent's own copy is never run.
export type Ran = { code: number; out: string; err: string };
export function runIn(cwd: string, cmd: string, args: string[], alsoReadable: string[] = []): Promise<Ran> {
  const user = USERS_ROOT ? path.relative(USERS_ROOT, cwd).split(path.sep)[0] : folderName(DEV_USER);
  const root = prometheusOf(user);
  const agent = path.resolve(cwd) === path.resolve(root) ? "prometheus" : `a/${path.basename(cwd)}`;
  const [c, a] = sandboxed(user, agent, cwd, [cmd, ...args], {}, [], false, [path.join(root, ".pi", "skills"), ...alsoReadable]);
  return new Promise((resolve) =>
    execFile(c, a, { cwd, env: launcherEnv(), maxBuffer: 16 * 1024 * 1024 }, (e, out, err) =>
      resolve({ code: e ? ((e as { code?: number }).code ?? 1) : 0, out, err })),
  );
}
// a skill script of prometheus's, run on `cwd` in its sandbox
export const skillScript = (cwd: string, script: string, args: string[]) => {
  const root = USERS_ROOT ? path.join(USERS_ROOT, path.relative(USERS_ROOT, cwd).split(path.sep)[0], "prometheus") : REPO;
  const python = pythonFor(root); // its .venv, mounted read-only (it may be this repository's, not the user's copy's)
  return runIn(cwd, python, [path.join(root, ".pi/skills/agent/scripts", script), ...args], python.includes(".venv") ? [path.dirname(path.dirname(python))] : []);
};

// node ui/router.ts sandbox-run <user> <p | a/slug> -- <command…>: for checking what a sandbox can do
if (process.argv[2] === "sandbox-run") {
  const [user, which] = process.argv.slice(3, 5);
  const cmd = process.argv.slice(process.argv.indexOf("--") + 1);
  const cwd = which === "p" ? prometheusOf(user) : path.join(prometheusOf(user), "agents", which.slice(2));
  const [c, args] = sandboxed(user, which === "p" ? "prometheus" : which, cwd, cmd, {});
  const r = spawn(c, args, { stdio: "inherit", env: launcherEnv() });
  r.on("exit", (code) => (removeOwnContainers(), process.exit(code ?? 1)));
  await new Promise(() => {});
}
