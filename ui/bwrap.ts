/** A sandbox (sandbox.ts `Spec`) as a bubblewrap command (SANDBOX=bwrap, the default for one person on their own
 * machine): Linux namespaces, starting in about 10 ms. Memory and CPU limits only with a systemd user session. */
import { existsSync, realpathSync } from "node:fs";
import { SANDBOX, works } from "./common.ts";
import type { Spec } from "./sandbox.ts";

// a systemd user session gives each sandbox memory and CPU limits (WSL usually has none)
export const LIMITS = SANDBOX === "bwrap" && works("systemd-run", ["--user", "--scope", "-q", "true"]);
// DNS: /etc/resolv.conf may link outside /etc (WSL: /mnt/wsl/resolv.conf), which must be mounted too
const RESOLV = existsSync("/etc/resolv.conf") ? realpathSync("/etc/resolv.conf") : "";

export function bwrapCommand(spec: Spec, command: string[]): [string, string[]] {
  const a = [
    // --new-session stops a sandbox typing into the router's terminal; a terminal of its own (tty) must
    // keep its pseudo-terminal as the controlling one, for resizes
    "--unshare-all", "--share-net", "--die-with-parent", ...(spec.tty ? [] : ["--new-session"]), "--clearenv",
    "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin",
    "--proc", "/proc", "--dev", "/dev",
    ...(RESOLV && !RESOLV.startsWith("/etc/") ? ["--ro-bind", RESOLV, RESOLV] : []),
  ];
  for (const m of spec.mounts) a.push(...(m.kind === "tmpfs" ? ["--tmpfs", m.dst] : [m.kind === "ro" ? "--ro-bind" : "--bind", m.src, m.dst]));
  for (const [k, v] of Object.entries(spec.env)) a.push("--setenv", k, v);
  a.push("--chdir", spec.cwd);
  const limits = LIMITS ? ["systemd-run", "--user", "--scope", "-q", "-p", `MemoryMax=${process.env.MEMORY_MAX ?? "4G"}`, "-p", `CPUQuota=${process.env.CPU_QUOTA ?? "200%"}`, "--"] : [];
  const all = [...limits, "bwrap", ...a, "--", ...command];
  return [all[0], all.slice(1)];
}
