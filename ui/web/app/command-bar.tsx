"use client";

import type { LucideIcon } from "lucide-react";
import { Activity, FastForward, GitBranch, ListChecks, Play, Sparkles } from "lucide-react";
import { useAui } from "@assistant-ui/react";
import { useAgent } from "./agent";

// The chat's commands as small chips above the input: an agent's own (run, auto-run, status, revise, improve), or in the
// Forge the list of agents (a new agent is the sidebar's: it starts a fresh Forge conversation from anywhere). One
// that only reads, or is the agent's main action, is sent; one that takes words from you (the change, the focus) or
// starts a long model session waits in the input box. Always clickable: one sent while the agent works waits for its
// turn to end (the worker queues it), and graying them while it ran made them blink whenever a wave woke the agent.
type Command = { label: string; icon: LucideIcon; text: string; send: boolean; title: string };

const AGENT: Command[] = [
  { label: "Run", icon: Play, text: "/agent-run", send: true, title: "Start or continue a run" },
  { label: "Auto-run", icon: FastForward, text: "/agent-auto", send: true, title: "Run the next waves without the model: it stops and asks only when a wave needs you (cheaper)" },
  { label: "Status", icon: Activity, text: "/agent-status", send: true, title: "Where this agent stands (changes nothing)" },
  { label: "Revise", icon: GitBranch, text: "/agent-revise ", send: false, title: "Change the plan on a branch you review: say what to change" },
  { label: "Improve", icon: Sparkles, text: "/agent-improve ", send: false, title: "Propose improvements from past sessions and runs (add a focus, or just send)" },
];
const FORGE: Command[] = [{ label: "My agents", icon: ListChecks, text: "/agent-list", send: true, title: "Your agents and where each stands" }];

export function CommandBar() {
  const aui = useAui();
  const { slug } = useAgent();
  const use = (c: Command) => (c.send ? void aui.thread().append(c.text) : aui.thread().composer().setText(c.text));
  return (
    <div className="bg-background relative z-10 mb-2 flex flex-wrap gap-1.5 pt-1">
      {(slug ? AGENT : FORGE).map((c) => (
        <button
          key={c.label}
          type="button"
          title={c.title}
          onClick={() => use(c)}
          className="text-muted-foreground hover:text-foreground hover:bg-accent/60 flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs"
        >
          <c.icon className="size-3.5" /> {c.label}
        </button>
      ))}
    </div>
  );
}
