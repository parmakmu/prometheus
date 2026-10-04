"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  type ThreadMessage,
} from "@assistant-ui/react";
import { HttpAgent } from "@ag-ui/client";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { API, authHeaders } from "./api";
import { filesAdapter } from "./attachments";

type StoredThread = {
  id: string;
  title?: string;
  messages: readonly ThreadMessage[];
};

// a conversation's title: its first user message, shortened
const titleOf = (messages: readonly ThreadMessage[]) => {
  const first = messages.find((m) => m.role === "user");
  const text = first?.content.map((p) => (p.type === "text" ? p.text : "")).join("").trim();
  return text ? (text.length > 40 ? `${text.slice(0, 39)}…` : text) : undefined;
};

/**
 * AG-UI runtime with threadList adapter for multi-thread support.
 */
export function MyRuntimeProvider({
  children,
  agentPath,
}: Readonly<{ children: ReactNode; agentPath: string }>) {
  const agentUrl = `${API}${agentPath}/agent`;

  // Simple in-memory thread storage
  const threadsRef = useRef<Map<string, StoredThread>>(new Map());
  // no side effect here: React may call an initializer twice (development), which made two threads
  const [currentThreadId, setCurrentThreadId] = useState<string>(() => crypto.randomUUID());
  if (!threadsRef.current.has(currentThreadId)) threadsRef.current.set(currentThreadId, { id: currentThreadId, messages: [] });

  const agent = useMemo(() => {
    return new HttpAgent({
      url: agentUrl,
      threadId: currentThreadId,
      headers: {
        Accept: "text/event-stream",
        ...authHeaders(),
      },
    });
  }, [agentUrl, currentThreadId]);

  const [version, setVersion] = useState(0); // re-render when a title changes
  const threadListAdapter = useMemo(
    () => ({
      threadId: currentThreadId,
      threads: [...threadsRef.current.values()]
        .reverse()
        .map((t) => ({ id: t.id, status: "regular" as const, title: t.title ?? "New conversation" })),
      onSwitchToNewThread: async () => {
        const newId = crypto.randomUUID();
        threadsRef.current.set(newId, { id: newId, messages: [] });
        setCurrentThreadId(newId);
        console.debug("[agui] Switched to new thread:", newId);
      },
      onSwitchToThread: async (threadId: string) => {
        const thread = threadsRef.current.get(threadId);
        if (!thread) {
          throw new Error(`Thread ${threadId} not found`);
        }
        setCurrentThreadId(threadId);
        console.debug("[agui] Switched to thread:", threadId);
        return { messages: thread.messages };
      },
    }),
    [currentThreadId, version],
  );

  const attachments = useMemo(() => filesAdapter(agentPath.startsWith("/w/") ? agentPath.split("/")[2] : null), [agentPath]);
  const runtime = useAgUiRuntime({
    agent,
    logger: {
      debug: (...a: any[]) => console.debug("[agui]", ...a),
      error: (...a: any[]) => console.error("[agui]", ...a),
    },
    adapters: {
      threadList: threadListAdapter,
      attachments,
    },
  });

  // Persist messages to threadsRef when they change
  useEffect(() => {
    return runtime.thread.subscribe(() => {
      const messages = runtime.thread.getState().messages;
      const before = threadsRef.current.get(currentThreadId)?.title;
      const title = before ?? titleOf(messages);
      threadsRef.current.set(currentThreadId, { id: currentThreadId, title, messages });
      if (title !== before) setVersion((v) => v + 1);
    });
  }, [runtime, currentThreadId]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
