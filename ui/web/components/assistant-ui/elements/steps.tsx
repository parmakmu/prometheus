"use client";

import { useState } from "react";
import { CheckIcon, ChevronRightIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// What the main agent's Details and a subagent's log in the drawer share: a thought, and a tool call as one line in
// words, opening to its arguments and result.

// a thought; one the model kept private (the worker's note) is a single small line, not a repeated sentence. A
// summarized thought often starts with a "**Title**" line (OpenAI's reasoning summaries): the title reads as one.
export const PRIVATE_THOUGHT = "(The model kept this reasoning private";
export function Thought({ text }: { text: string }) {
  if (text.startsWith(PRIVATE_THOUGHT)) return <p className="text-muted-foreground/70 text-xs" title={text}>Thought privately</p>;
  const titled = text.match(/^\*\*(.+?)\*\*\s*\n+([\s\S]*)$/);
  return (
    <p className="text-muted-foreground text-sm leading-relaxed whitespace-pre-wrap">
      {titled && <span className="text-foreground/80 block font-medium">{titled[1]}</span>}
      {titled ? titled[2] : text}
    </p>
  );
}

// What a step did, in words (as Cowork writes "Checking your task files"); the tool's name stays on hover
const base = (p: unknown) => String(p ?? "").split("/").pop() ?? "";
const STEP_WORDS: Record<string, (a: Record<string, unknown>) => string> = {
  read: (a) => `Reading ${base(a.path ?? a.file_path)}`,
  write: (a) => `Writing ${base(a.path ?? a.file_path)}`,
  edit: (a) => `Editing ${base(a.path ?? a.file_path)}`,
  bash: () => "Running a command",
  ls: (a) => `Listing ${base(a.path) || "files"}`,
  find: () => "Looking for files",
  grep: () => "Searching files",
  workflow_check: () => "Checking the plan",
  workflow_new_run: () => "Starting a run",
  workflow_compile: () => "Preparing the next wave",
  workflow_log_start: () => "Noting the wave's launch",
  workflow_log_finish: () => "Noting how the wave ended",
  workflow_record_decision: () => "Recording your decision",
  workflow_replan: () => "Adjusting the plan",
  workflow_record_critique: () => "Recording the critic's review",
  workflow_stop_run: () => "Stopping the run",
  workflow_merge: (a) => (a.action === "merge" ? "Merging the changes" : a.action === "discard" ? "Discarding the branch" : "Keeping the branch for later"),
  workflow_set_params: () => "Setting values",
  workflow_add_package: () => "Adding a package",
  workflow_sessions: () => "Reading earlier sessions",
  workflow_scaffold: () => "Creating the workflow",
  subagent: (a) => (a.action === "status" ? "Checking background work" : a.action === "mission.close" ? "Closing the background work" : "Starting background work"),
};
export const stepWords = (toolName: string, a: Record<string, unknown>) => STEP_WORDS[toolName]?.(a) ?? toolName.replace(/_/g, " ");

// a tool call in Details: one line in words, opening to its arguments and result
export function StepRow({ toolName, args, argsText, result, running, failed }: { toolName: string; args?: unknown; argsText?: string; result?: unknown; running: boolean; failed: boolean }) {
  const [open, setOpen] = useState(false);
  const a = (args ?? {}) as Record<string, unknown>;
  return (
    <div>
      {/* expandable, so it says so: a chevron (› closed, ⌄ open) and a hover background, like the fold's own line */}
      <button type="button" title={`${toolName}: show the call and its result`} onClick={() => setOpen((o) => !o)} className="text-muted-foreground hover:text-foreground hover:bg-accent/60 group -mx-1 flex max-w-full min-w-0 items-center gap-1.5 rounded px-1 text-left text-sm">
        {running ? <span className="bg-primary size-1.5 shrink-0 animate-pulse rounded-full" /> : failed ? <span className="text-red-600">✗</span> : <CheckIcon className="size-3.5 shrink-0" />}
        <span className="truncate">{stepWords(toolName, a)}</span>
        <ChevronRightIcon className={cn("size-3.5 shrink-0 opacity-60 transition-transform group-hover:opacity-100", open && "rotate-90")} />
      </button>
      {open && (
        <div className="mt-1 mb-1.5 space-y-1 ps-4">
          <pre className="bg-muted overflow-auto rounded-md p-2 text-xs whitespace-pre-wrap">{argsText || JSON.stringify(a, null, 2)}</pre>
          {result !== undefined && <pre className="bg-muted/60 max-h-72 overflow-auto rounded-md p-2 text-xs whitespace-pre-wrap">{typeof result === "string" ? result : JSON.stringify(result, null, 2)}</pre>}
        </div>
      )}
    </div>
  );
}
