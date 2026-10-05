/** A sandbox (sandbox.ts `Spec`) as a rootless Podman container (SANDBOX=podman, for a server): no image, its root an
 * empty folder with the same mounts at the same paths; your uid inside (keep-id), no capabilities, a process limit,
 * memory and CPU limits where cgroups allow, a loopback of its own, and gVisor's kernel with PODMAN_RUNTIME=runsc. The
 * internet by allow-list: workers on an internal network whose only way out is ui/egress_proxy.py. About half a second
 * a start. See ui/README.md, "How a sandbox is built". */
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { SANDBOX, SOCKETS, ownOf, works } from "./common.ts";
import type { Spec } from "./sandbox.ts";

const RUNTIME = process.env.PODMAN_RUNTIME ?? "";
// podman's own options, before `run`: the runtime, and for gVisor as a normal user ignore-cgroups (it can't set them up)
const GLOBAL = RUNTIME ? ["--runtime", RUNTIME, ...(path.basename(RUNTIME) === "runsc" ? ["--runtime-flag", "ignore-cgroups"] : [])] : [];

// the container's root: mount points, and links like bin → usr/bin as Ubuntu has; made once
let rootfs = "";
function rootFolder(): string {
  if (rootfs) return rootfs;
  rootfs = path.join(SOCKETS, "rootfs");
  for (const d of ["usr", "etc", "tmp", "proc", "dev", "sys", "run", "home"]) mkdirSync(path.join(rootfs, d), { recursive: true });
  for (const l of ["bin", "lib", "lib64", "sbin"]) symlinkSync(`usr/${l}`, path.join(rootfs, l));
  return rootfs;
}

// memory and CPU limits need the cgroup controllers delegated to you (often not under WSL)
const LIMITS = (() => {
  if (SANDBOX !== "podman") return [];
  try {
    const have = execFileSync("podman", ["info", "--format", "{{.Host.CgroupControllers}}"], { encoding: "utf8" });
    const cpus = String(Number.parseInt(process.env.CPU_QUOTA ?? "200%", 10) / 100);
    return have.includes("memory") && have.includes("cpu") ? ["--memory", process.env.MEMORY_MAX ?? "4g", "--cpus", cpus] : [];
  } catch {
    return [];
  }
})();

/** `podman` and its arguments for `spec`. `network`: the one it joins; `name`: run detached under that name. */
function podmanArgs(spec: Spec, command: string[], network = "", name = ""): string[] {
  const out = [
    ...GLOBAL, "run", "--rm", "--init", ...(name ? ["-d", "--name", name] : ["-i"]), ...(spec.tty ? ["-t"] : []),
    ...(network ? ["--network", network] : []),
    "--userns=keep-id", "--cap-drop=all", "--security-opt=no-new-privileges", "--pids-limit=2048",
    "--label", `prometheus.router=${process.pid}`, ...LIMITS,
  ];
  for (const m of spec.mounts) {
    if (m.kind === "tmpfs") {
      out.push("--mount", `type=tmpfs,target=${m.dst},U=true`); // yours (keep-id), as under bwrap
      continue;
    }
    if ([m.src, m.dst].some((p) => p.includes(","))) throw new Error(`a path with a comma can't be mounted by podman: ${m.src}`);
    out.push("--mount", `type=bind,source=${m.src},target=${m.dst}${m.kind === "ro" ? ",readonly" : ""}`);
  }
  for (const [k, v] of Object.entries(spec.env)) out.push("--env", `${k}=${v}`);
  // --rootfs is a switch: the root folder is the positional "image", right before the command (:O, an overlay over
  // it, so podman can add its mount points without changing it). DNS is podman's own, which knows the proxy's name.
  return [...out, "--workdir", spec.cwd, "--rootfs", `${rootFolder()}:O`, ...command];
}

export function podmanCommand(user: string, agent: string, spec: Spec, command: string[]): [string, string[]] {
  if (EGRESS) spec = { ...spec, env: { ...spec.env, ...proxyEnv(user, agent) } };
  return ["podman", podmanArgs(spec, command, EGRESS ? NET_IN : "")];
}

// A runtime you asked for (gVisor) must work: running without it would quietly drop the boundary you chose
if (SANDBOX === "podman" && RUNTIME) {
  try {
    const probe: Spec = { mounts: [{ kind: "ro", src: "/usr", dst: "/usr" }], env: { PATH: "/usr/bin:/bin" }, cwd: "/", tty: false };
    execFileSync("podman", podmanArgs(probe, ["true"]), { stdio: "pipe", timeout: 60_000 });
  } catch (e: any) {
    console.error(`PODMAN_RUNTIME=${RUNTIME} can't start a container: ${String(e.stderr ?? e.message).trim().split("\n").at(-1)}`);
    process.exit(1);
  }
}

// containers a router that died (SIGKILL) left behind; its live ones it stops itself
if (SANDBOX === "podman") {
  try {
    const listed = execFileSync("podman", ["ps", "-a", "--filter", "label=prometheus.router", "--format", '{{.ID}} {{index .Labels "prometheus.router"}}'], { encoding: "utf8" });
    const orphans = listed.split("\n").map((l) => l.split(" ")).filter(([id, pid]) => id && !alive(Number(pid))).map(([id]) => id);
    if (orphans.length) execFileSync("podman", ["rm", "-f", ...orphans], { stdio: "ignore" });
  } catch {
    console.error("SANDBOX=podman: podman doesn't answer; is it installed (podman info)?");
  }
}

// this router's containers (its workers', its proxy): a signal to `podman run` may not reach them before we exit
export function removeOwnContainers() {
  if (SANDBOX !== "podman") return;
  try {
    const ids = execFileSync("podman", ["ps", "-aq", "--filter", `label=prometheus.router=${process.pid}`], { encoding: "utf8" }).split("\n").filter(Boolean);
    if (ids.length) execFileSync("podman", ["rm", "-f", "-t", "1", ...ids], { stdio: "ignore" });
  } catch {}
}

function alive(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e: any) {
    return e?.code === "EPERM";
  }
}

// --- the way out: an allow-list per agent ----------------------------------------------------------------------------
// Workers sit on an internal network with no route out; the only way out is ui/egress_proxy.py, on that network and
// an outbound one, which lets each agent reach the hosts allowed for it: the base (model providers, PyPI) plus those
// you allowed for that agent, kept outside every sandbox (<your folder>/network/<agent>.json). An agent tells the
// proxy who it is by a token in its HTTPS_PROXY. Refusals go to a log the web app shows, with an Allow button.

export const EGRESS = SANDBOX === "podman" && process.env.EGRESS !== "off";
export const EGRESS_BASE = (process.env.EGRESS_BASE ?? [
  "api.anthropic.com", "console.anthropic.com", "api.openai.com", "chatgpt.com", "auth.openai.com",
  "generativelanguage.googleapis.com", "oauth2.googleapis.com", "openrouter.ai", "api.mistral.ai", "api.groq.com",
  "api.x.ai", "api.deepseek.com", "pypi.org", "files.pythonhosted.org",
].join(",")).split(",").map((h) => h.trim()).filter(Boolean);
const EGRESS_DIR = path.join(SOCKETS, "egress"); // allow.json and refused.jsonl, shared with the proxy
const uid = process.getuid?.() ?? 0;
const NET_IN = `prometheus-${uid}-internal`, NET_OUT = `prometheus-${uid}-out`;
const PROXY = `prometheus-egress-${process.pid}`;
export const HOST_RE = /^\.?([a-z0-9-]+\.)+[a-z]{2,}$/i;

const tokens = new Map<string, { user: string; agent: string }>(); // one per user and agent, for this router's life
function tokenFor(user: string, agent: string) {
  for (const [t, v] of tokens) if (v.user === user && v.agent === agent) return t;
  const t = randomBytes(18).toString("base64url");
  tokens.set(t, { user, agent });
  writeAllow();
  return t;
}
export const allowFile = (user: string, agent: string) => path.join(ownOf(user), "network", `${agent === "prometheus" ? "prometheus" : agent.slice(2)}.json`);
export function allowedFor(user: string, agent: string): string[] {
  try {
    const list = JSON.parse(readFileSync(allowFile(user, agent), "utf8")).allow;
    return Array.isArray(list) ? list.filter((h: unknown) => typeof h === "string" && HOST_RE.test(h)) : [];
  } catch {
    return [];
  }
}
/** Rewrites what the proxy reads (it rereads it on every connection): every token's agent and hosts. */
export function writeAllow() {
  if (!EGRESS) return;
  const out: Record<string, { agent: string; hosts: string[] }> = {};
  for (const [t, { user, agent }] of tokens) out[t] = { agent: `${user}/${agent}`, hosts: [...EGRESS_BASE, ...allowedFor(user, agent)] };
  writeFileSync(path.join(EGRESS_DIR, "allow.json.tmp"), JSON.stringify(out));
  renameSync(path.join(EGRESS_DIR, "allow.json.tmp"), path.join(EGRESS_DIR, "allow.json"));
}
function proxyEnv(user: string, agent: string): Record<string, string> {
  const url = `http://${tokenFor(user, agent)}:x@${PROXY}:3128`;
  // Node's fetch honours the proxy only with NODE_USE_ENV_PROXY; a tool that ignores it has no route out at all
  return { HTTPS_PROXY: url, https_proxy: url, HTTP_PROXY: url, http_proxy: url, NO_PROXY: "localhost,127.0.0.1", no_proxy: "localhost,127.0.0.1", NODE_USE_ENV_PROXY: "1" };
}
export function refusedFor(user: string, agent: string): string[] {
  try {
    const hosts = readFileSync(path.join(EGRESS_DIR, "refused.jsonl"), "utf8").split("\n").filter(Boolean)
      .map((l) => JSON.parse(l)).filter((r) => r.agent === `${user}/${agent}`).map((r) => String(r.host));
    return [...new Set(hosts)].filter((h) => HOST_RE.test(h));
  } catch {
    return [];
  }
}
// the two networks (once per user), and the proxy on both, in a container like the workers'
if (EGRESS) {
  mkdirSync(EGRESS_DIR, { recursive: true });
  writeFileSync(path.join(EGRESS_DIR, "refused.jsonl"), "");
  writeAllow();
  for (const [net, internal] of [[NET_IN, true], [NET_OUT, false]] as const)
    if (!works("podman", ["network", "exists", net])) works("podman", ["network", "create", ...(internal ? ["--internal"] : []), net]);
  const proxy: Spec = {
    mounts: [
      { kind: "ro", src: "/usr", dst: "/usr" }, { kind: "ro", src: "/etc", dst: "/etc" },
      { kind: "ro", src: path.join(import.meta.dirname, "egress_proxy.py"), dst: "/egress_proxy.py" },
      { kind: "rw", src: EGRESS_DIR, dst: "/egress" },
    ],
    env: { PATH: "/usr/bin:/bin" }, cwd: "/", tty: false,
  };
  try {
    execFileSync("podman", podmanArgs(proxy, ["python3", "/egress_proxy.py", "/egress/allow.json", "/egress/refused.jsonl", "3128"], NET_OUT, PROXY), { stdio: "pipe" });
    execFileSync("podman", ["network", "connect", NET_IN, PROXY], { stdio: "pipe" });
  } catch (e: any) {
    console.error(`the egress proxy didn't start: ${String(e.stderr ?? e.message).trim().split("\n").at(-1)}; start the router with EGRESS=off to run without an allow-list`);
    process.exit(1);
  }
}
