/**
 * Runs one wave of a released agent's run, with no model and no one to ask: `pi -p "/prometheus-run <run folder> <wave>"`
 * (prometheus/run_release.py starts it, in the release's checkout, with pi-subagents loaded beside this file, once per wave).
 *
 * It launches the call run_compile.py --frozen saved in <run>/.compiled/<wave>.json through pi-subagents' RPC, waits
 * for its status.json, and appends one JSON line per event to <run>/events.jsonl: launched, child (a child's state
 * changed), wave_end (the wave's state and cost) or error. The exit code is 0 when the wave completed, 1 when it ended
 * otherwise, 2 when it could not launch. The run's own end is run_release.py's to write. SIGTERM or SIGINT stop the run (pi-subagents' `stop`) before exiting:
 * killing pi alone would leave pi-subagents' runner going, since it is a process of its own.
 *
 * No model runs here: pi-subagents wakes a headless session when its run ends (a completion message, which would
 * start a model turn), so every turn is aborted and logged (model_turn_blocked), and pi exits as soon as the end
 * is recorded.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";

const ENDS = ["complete", "completed", "failed", "stopped", "paused", "partial", "rejected"];

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e: any) {
    return e?.code === "EPERM";
  }
}

export default function frozenRunner(pi: ExtensionAPI) {
  let blockedLog: ((event: object) => void) | undefined;
  pi.on("before_agent_start", (_event, ctx) => {
    blockedLog?.({ event: "model_turn_blocked" });
    ctx.abort();
  });

  const rpc = (method: string, params: object, timeoutMs = 30_000) =>
    new Promise<any>((resolve, reject) => {
      const requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const timer = setTimeout(() => {
        off();
        reject(new Error(`pi-subagents did not answer ${method} (is it installed? it is loaded with -e)`));
      }, timeoutMs);
      const off = pi.events.on(`subagents:rpc:v1:reply:${requestId}`, (reply: any) => {
        clearTimeout(timer);
        off();
        if (reply?.success) resolve(reply.data);
        else reject(new Error(reply?.error?.message ?? `${method} failed`));
      });
      pi.events.emit("subagents:rpc:v1:request", { version: 1, requestId, method, params });
    });

  pi.registerCommand("prometheus-run", {
    description: "Run a wave of a released agent's run: /prometheus-run <run folder> <wave>",
    handler: async (args) => {
      const trimmed = args.trim();
      const cut = trimmed.lastIndexOf(" ");
      const runDir = path.resolve(trimmed.slice(0, cut));
      const wave = trimmed.slice(cut + 1);
      const log = (event: object) =>
        fs.appendFileSync(path.join(runDir, "events.jsonl"), JSON.stringify({ time: new Date().toISOString(), ...event }) + "\n");
      blockedLog = log;
      let runId: string | undefined;
      let stopping = false;
      const stop = () => {
        if (stopping) return;
        stopping = true;
        log({ event: "stopping" });
        if (runId) rpc("stop", { runId }).catch((e) => log({ event: "error", message: `stop: ${e.message}` }));
      };
      process.once("SIGTERM", stop);
      process.once("SIGINT", stop);
      try {
        const compiled = JSON.parse(fs.readFileSync(path.join(runDir, ".compiled", `${wave}.json`), "utf8"));
        await rpc("ping", {});
        const launched = await rpc("spawn", { ...compiled.call, cwd: process.cwd() });
        runId = launched?.details?.runId ?? launched?.details?.asyncId;
        const dir: string | undefined = launched?.details?.asyncDir;
        if (!runId || !dir) throw new Error(`pi-subagents launched no run: ${JSON.stringify(launched?.details ?? {}).slice(0, 500)}`);
        log({ event: "launched", wave, subagent_run_id: runId, status_dir: dir });
        if (stopping) stop(); // asked to stop while launching

        const seen = new Map<string, string>();
        const began = Date.now();
        let dead = 0;
        let wake: (() => void) | undefined;
        const watcher = fs.watch(dir, () => wake?.()); // status.json is rewritten in place: wake at once, poll as a backstop
        try {
          for (;;) {
            let st: any;
            try {
              st = JSON.parse(fs.readFileSync(path.join(dir, "status.json"), "utf8"));
            } catch {
              st = undefined; // not written yet, or caught mid-write
            }
            for (const c of st?.workflowChildren?.children ?? []) {
              if (c?.childId && seen.get(c.childId) !== c.state) {
                seen.set(c.childId, c.state);
                log({ event: "child", key: c.childId, state: c.state, agent: c.agent, child_run_id: c.runId });
              }
            }
            const lost = st === undefined ? Date.now() - began > 120_000 : typeof st.pid === "number" && !alive(st.pid) && ++dead >= 2;
            if (st && typeof st.pid === "number" && alive(st.pid)) dead = 0;
            if (ENDS.includes(st?.state) || lost) {
              const state = lost ? "lost" : st.state;
              log({ event: "wave_end", wave, state, subagent_run_id: runId, status_dir: dir, cost: st?.totalCost ?? null });
              // exit now: left to finish, pi would deliver pi-subagents' completion message into a model turn
              process.exit(state === "complete" || state === "completed" ? 0 : 1);
            }
            await new Promise<void>((resolve) => {
              wake = resolve;
              setTimeout(resolve, 1_000);
            });
          }
        } finally {
          watcher.close();
        }
      } catch (e: any) {
        log({ event: "error", wave, message: e?.message ?? String(e) });
        process.exit(2);
      } finally {
        process.off("SIGTERM", stop);
        process.off("SIGINT", stop);
      }
    },
  });
}
