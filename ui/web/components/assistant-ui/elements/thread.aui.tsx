"use client";

import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/elements/attachment.aui";
import { File } from "@/components/assistant-ui/elements/file";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/elements/follow-up-suggestions.aui";
import { Image } from "@/components/assistant-ui/elements/image";
import { MarkdownText } from "@/components/assistant-ui/elements/markdown-text";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useAgent } from "@/app/agent";
import { sendJson } from "@/app/api";
import { AnsweredQuestion } from "@/app/cards";
import { PRIVATE_THOUGHT, StepRow, Thought, stepWords } from "@/components/assistant-ui/elements/steps";
import {
  ActionBarMorePrimitive,
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type FileMessagePartComponent,
  type ImageMessagePartComponent,
  type TextMessagePartComponent,
  type ToolCallMessagePartComponent,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  AudioLinesIcon,
  CheckIcon,
  ChevronRightIcon,
  CopyIcon,
  PencilIcon,
  DownloadIcon,
  MicIcon,
  MoreHorizontalIcon,
  PhoneIcon,
  SquareIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";
import {
  createContext,
  useContext,
  useMemo,
  useState,
  useSyncExternalStore,
  type ComponentType,
  type ReactNode,
  type FC,
} from "react";

/**
 * Optional component overrides for the thread. `AssistantMessage` and
 * `Welcome` replace whole sections; `ToolFallback` draws a tool call no tool UI is
 * registered for (tool UIs registered by name take precedence).
 */
export type ThreadComponents = {
  AssistantMessage?: ComponentType | undefined;
  Welcome?: ComponentType | undefined;
  ToolFallback?: ToolCallMessagePartComponent | undefined;
};

// tool calls that draw a card for you (a question, a panel, the created agent, a wave's children): shown outside
// the Details fold, where you can see and answer them
const STANDALONE_TOOLS = new Set(["pi_panel", "pi_models", "ask_user_question", "agent_scaffold", "subagent"]);

export type ThreadProps = {
  components?: ThreadComponents | undefined;
  autoFocus?: boolean | undefined;
};

const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext =
  createContext<ThreadComponents>(EMPTY_COMPONENTS);

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  (!s.thread.isLoading || s.threads.isLoading);

// A switched thread that is still fetching its history: skeleton, not welcome.
const isHistoryLoadingView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  s.thread.isLoading &&
  !s.thread.isDisabled &&
  !s.threads.isLoading;

const ThreadHistorySkeleton: FC = () => (
  <div
    data-slot="aui_thread-history-skeleton"
    role="status"
    className="animate-in fade-in fill-mode-both flex flex-col gap-y-6 [animation-delay:150ms] [animation-duration:200ms]"
  >
    <span className="sr-only">Loading conversation</span>
    <Skeleton className="ml-auto h-9 w-2/5 rounded-xl motion-reduce:animate-none" />
    <div className="flex flex-col gap-y-2">
      <Skeleton className="h-4 w-11/12 motion-reduce:animate-none" />
      <Skeleton className="h-4 w-4/5 motion-reduce:animate-none" />
      <Skeleton className="h-4 w-3/5 motion-reduce:animate-none" />
    </div>
    <Skeleton className="ml-auto h-9 w-1/3 rounded-xl motion-reduce:animate-none" />
    <div className="flex flex-col gap-y-2">
      <Skeleton className="h-4 w-10/12 motion-reduce:animate-none" />
      <Skeleton className="h-4 w-2/3 motion-reduce:animate-none" />
    </div>
  </div>
);

export const Thread: FC<ThreadProps> = ({
  components = EMPTY_COMPONENTS,
  autoFocus = true,
}) => {
  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot autoFocus={autoFocus} />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{ autoFocus: boolean }> = ({
  autoFocus,
}) => {
  const { Welcome = ThreadWelcome } = useContext(ThreadComponentsContext);

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-background @container flex h-full flex-col"
      style={{
        ["--thread-max-width" as string]: "44rem",
        ["--composer-bg" as string]:
          "color-mix(in oklab, var(--color-muted) 30%, transparent)",
        ["--composer-radius" as string]: "1rem",
        ["--composer-padding" as string]: "8px",
      }}
    >
      {/* bottom anchoring: follows new output only while you are at the bottom; scrolling up leaves you there
          ("top" re-pinned the latest message on every re-render, pulling the view back while you scrolled) */}
      <ThreadPrimitive.Viewport
        turnAnchor="bottom"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-scroll"
      >
        <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-4 pt-4">
          {/* the composer stays at the bottom in every state (a new chat, history loading, a re-sync); only the
              welcome centres itself in the space above it, so nothing jumps when the history arrives */}
          <AuiIf condition={isNewChatView}>
            <div className="flex flex-1 flex-col justify-center">
              <Welcome />
            </div>
          </AuiIf>
          <AuiIf condition={isHistoryLoadingView}>
            <ThreadHistorySkeleton />
          </AuiIf>

          <div
            data-slot="aui_message-group"
            className="mb-14 flex flex-col gap-y-6 empty:hidden"
          >
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter
            className="aui-thread-viewport-footer bg-background sticky bottom-0 mt-auto flex flex-col gap-4 overflow-visible rounded-t-(--composer-radius) pb-4 md:pb-6"
          >
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            <Composer autoFocus={autoFocus} />
            <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
              <ThreadSuggestions />
            </AuiIf>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

const ThreadMessage: FC = () => {
  const { AssistantMessage: AssistantMessageComponent = AssistantMessage } =
    useContext(ThreadComponentsContext);
  const role = useAuiState((s) => s.message.role);
  const isSpoken = useAuiState((s) => s.message.metadata.modality === "voice");

  const isEditing = useAuiState((s) => s.message.composer.isEditing);

  if (isSpoken) return <SpokenMessage />;
  if (isEditing) return <EditComposer />;
  if (role === "user") return <UserMessage />;
  return <AssistantMessageComponent />;
};

type VoiceRunPosition = "single" | "start" | "middle" | "end";

const useVoiceRunPosition = (): VoiceRunPosition =>
  useAuiState((s) => {
    const before =
      s.thread.messages[s.message.index - 1]?.metadata.modality === "voice";
    const after =
      s.thread.messages[s.message.index + 1]?.metadata.modality === "voice";
    if (before) return after ? "middle" : "end";
    return after ? "start" : "single";
  });

const SpokenText: TextMessagePartComponent = ({ text }) => (
  <p className="aui-spoken-message-text m-0">{text}</p>
);

const SpokenMessage: FC = () => {
  const role = useAuiState((s) => s.message.role);
  const position = useVoiceRunPosition();
  const isSpeaking = useAuiState(
    (s) =>
      s.message.role === "assistant" && s.message.status?.type === "running",
  );
  const opensExchange = position === "start" || position === "single";

  return (
    <MessagePrimitive.Root
      data-slot="aui_spoken-message-root"
      data-role={role}
      data-voice-run={position}
      className={cn(
        "aui-spoken-message bg-muted/40 mx-2 px-3 py-1.5",
        position === "single" && "rounded-xl py-2",
        position === "start" && "rounded-t-xl pt-2",
        position === "middle" && "-mt-6",
        position === "end" && "-mt-6 rounded-b-xl pb-2",
      )}
    >
      {opensExchange && (
        <div
          data-slot="aui_spoken-exchange-header"
          className="text-muted-foreground mb-1.5 flex items-center gap-1.5 text-xs"
        >
          <PhoneIcon className="size-3" aria-hidden />
          <span>Voice conversation</span>
        </div>
      )}
      <div
        data-slot="aui_spoken-message-content"
        className="text-foreground flex items-start gap-2 text-sm leading-relaxed"
      >
        <span className="text-muted-foreground mt-1 shrink-0" aria-hidden>
          {role === "user" ? (
            <MicIcon className="size-3.5" />
          ) : (
            <AudioLinesIcon className="size-3.5" />
          )}
        </span>
        <span className="sr-only">
          {role === "user" ? "You said" : "Assistant said"}
        </span>
        <div className="min-w-0 flex-1 wrap-break-word">
          <MessagePrimitive.Parts components={{ Text: SpokenText }} />
          {isSpeaking && (
            <span
              data-slot="aui_spoken-message-indicator"
              role="status"
              className="text-muted-foreground ms-1 animate-pulse font-sans"
              aria-label="Assistant is speaking"
            >
              ●
            </span>
          )}
        </div>
        <SpokenActionBar />
      </div>
    </MessagePrimitive.Root>
  );
};

const SpokenActionBar: FC = () => {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="always"
      className="aui-spoken-action-bar text-muted-foreground flex shrink-0 gap-1"
    >
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton tooltip="Copy" className="size-6">
          <AuiIf condition={(s) => s.message.isCopied}>
            <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
          </AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>
            <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
          </AuiIf>
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>
    </ActionBarPrimitive.Root>
  );
};

const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      <TooltipIconButton
        tooltip="Scroll to bottom"
        variant="outline"
        className="aui-thread-scroll-to-bottom dark:border-border dark:bg-background dark:hover:bg-accent absolute -top-12 z-10 self-center rounded-full p-4 disabled:invisible"
      >
        <ArrowDownIcon />
      </TooltipIconButton>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-6 flex flex-col px-2">
      <p className="aui-thread-welcome-message-inner fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-2xl font-medium tracking-tight duration-200">
        How can I help you today?
      </p>
    </div>
  );
};

const ThreadSuggestions: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestions flex w-full flex-col">
      <ThreadPrimitive.Suggestions>
        {() => <ThreadSuggestionItem />}
      </ThreadPrimitive.Suggestions>
    </div>
  );
};

const ThreadSuggestionItem: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestion-display fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200">
      <SuggestionPrimitive.Trigger send asChild>
        <button
          type="button"
          className="aui-thread-welcome-suggestion group hover:bg-foreground/[0.03] focus-visible:ring-ring/50 flex w-full items-baseline gap-2.5 rounded-md px-2 py-2 text-start text-sm transition-colors outline-none focus-visible:ring-1 motion-reduce:transition-none"
        >
          <span
            aria-hidden
            className="text-muted-foreground/60 group-hover:text-foreground font-mono text-xs transition-colors motion-reduce:transition-none"
          >
            {">"}
          </span>
          <span className="min-w-0 flex-1 truncate">
            <SuggestionPrimitive.Title className="aui-thread-welcome-suggestion-text-1 text-foreground" />{" "}
            <SuggestionPrimitive.Description className="aui-thread-welcome-suggestion-text-2 text-muted-foreground empty:hidden" />
          </span>
        </button>
      </SuggestionPrimitive.Trigger>
    </div>
  );
};

const Composer: FC<{ autoFocus: boolean }> = ({ autoFocus }) => {
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <ComposerPrimitive.AttachmentDropzone asChild>
        <div
          data-slot="aui_composer-shell"
          className="border-foreground/10 focus-within:border-foreground/25 data-[dragging=true]:border-ring flex w-full cursor-text flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) transition-[border-color] data-[dragging=true]:border-dashed data-[dragging=true]:bg-[color-mix(in_oklab,var(--color-accent)_50%,var(--color-background))]"
        >
          <ComposerAttachments />
          <ComposerPrimitive.Input
            placeholder="Send a message..."
            className="aui-composer-input caret-primary placeholder:text-muted-foreground/60 max-h-48 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base leading-6 outline-none"
            rows={1}
            autoFocus={autoFocus}
            enterKeyHint="send"
            aria-label="Message input"
          />
          <ComposerAction />
        </div>
      </ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  );
};

const ComposerAction: FC = () => {
  // The stop control only cancels the send while no run it could stop is going.
  const isSending = useAuiState(
    (s) =>
      s.composer.submission !== undefined &&
      !(s.thread.isRunning && s.thread.capabilities.cancel),
  );

  return (
    <div className="aui-composer-action-wrapper relative flex items-center justify-between">
      <ComposerAddAttachment />
      <div className="flex items-center gap-1.5">
        <AuiIf condition={(s) => s.thread.capabilities.dictation}>
          <AuiIf condition={(s) => s.composer.dictation == null}>
            <ComposerPrimitive.Dictate asChild>
              <TooltipIconButton
                tooltip="Voice input"
                side="bottom"
                type="button"
                variant="ghost"
                size="icon"
                className="aui-composer-dictate text-muted-foreground hover:text-foreground size-7 rounded-full"
                aria-label="Start voice input"
              >
                <MicIcon className="aui-composer-dictate-icon size-4" />
              </TooltipIconButton>
            </ComposerPrimitive.Dictate>
          </AuiIf>
          <AuiIf condition={(s) => s.composer.dictation != null}>
            <ComposerPrimitive.StopDictation asChild>
              <TooltipIconButton
                tooltip="Stop dictation"
                side="bottom"
                type="button"
                variant="ghost"
                size="icon"
                className="aui-composer-stop-dictation text-destructive size-7 rounded-full"
                aria-label="Stop voice input"
              >
                <SquareIcon className="aui-composer-stop-dictation-icon size-3.5 animate-pulse fill-current" />
              </TooltipIconButton>
            </ComposerPrimitive.StopDictation>
          </AuiIf>
        </AuiIf>
        <AuiIf
          condition={(s) =>
            !s.composer.canCancel ||
            (s.thread.voice !== undefined &&
              s.composer.submission === undefined)
          }
        >
          <ComposerPrimitive.Send asChild>
            <TooltipIconButton
              tooltip="Send message"
              side="bottom"
              type="button"
              variant="default"
              size="icon"
              className="aui-composer-send size-7 rounded-full"
              aria-label="Send message"
            >
              <ArrowUpIcon className="aui-composer-send-icon size-4" />
            </TooltipIconButton>
          </ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf
          condition={(s) =>
            s.composer.canCancel &&
            (s.thread.voice === undefined ||
              s.composer.submission !== undefined)
          }
        >
          <ComposerPrimitive.Cancel asChild>
            <Button
              type="button"
              variant="default"
              size="icon"
              className="aui-composer-cancel size-7 rounded-full"
              aria-label={isSending ? "Cancel sending" : "Stop generating"}
            >
              <SquareIcon className="aui-composer-cancel-icon size-3.5 fill-current" />
            </Button>
          </ComposerPrimitive.Cancel>
        </AuiIf>
      </div>
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root border-destructive bg-destructive/10 text-destructive dark:bg-destructive/5 mt-2 rounded-md border p-3 text-sm dark:text-red-200">
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-2" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

// A turn reads like Cowork's: from your message to your next one the agent writes several messages (one per model
// call), and what it did on the way folds away: each stretch of steps (thoughts, tool calls, notes between them) is one
// "Thought process" fold, drawn at the message where the stretch starts, with its steps from the later messages too.
// Between the folds, in order, the things for you: cards (a question to answer, a panel, a launched wave) and replies
// (a message's text after its last step).
type Part = { type: string; toolName?: string; text?: string; args?: { async?: unknown; [k: string]: unknown }; argsText?: string; result?: unknown; isError?: boolean };
type Msg = { id: string; role: string; content: readonly Part[] };

// (a subagent call is a card only when it launches work, a wave or the critic, its status checks being steps; a
// question only while it waits for you: answered, it is a step in the fold, "You answered: …")
const isCard = (c: Part | undefined) =>
  c?.type === "tool-call" &&
  STANDALONE_TOOLS.has(c.toolName ?? "") &&
  (c.toolName !== "subagent" || !!c.args?.async) &&
  (c.toolName !== "ask_user_question" || c.result === undefined);

// a message's parts: the steps (in the fold), the cards and the answer
function split(content: readonly Part[]) {
  let answerFrom = content.length;
  while (answerFrom > 0 && content[answerFrom - 1]?.type === "text") answerFrom--;
  const before = Array.from({ length: answerFrom }, (_, i) => i);
  return {
    steps: before.filter((i) => !isCard(content[i]) && !(content[i]?.type === "text" && !content[i]?.text?.trim())),
    cards: before.filter((i) => isCard(content[i])),
    answer: Array.from({ length: content.length - answerFrom }, (_, i) => answerFrom + i).filter((i) => content[i]?.text?.trim()),
  };
}

// which turn a message is in: the messages after your last message before it, up to your next one
function turnOf(messages: readonly Msg[], id: string) {
  const i = messages.findIndex((m) => m.id === id);
  let start = i;
  while (start > 0 && messages[start - 1]?.role !== "user") start--;
  let end = i;
  while (end + 1 < messages.length && messages[end + 1]?.role !== "user") end++;
  return { key: messages[start]?.id ?? id, first: i === start, messages: messages.slice(start, end + 1), last: end === messages.length - 1 };
}

// whether a turn's fold is open, shared by its messages
const openTurns = new Set<string>();
const turnListeners = new Set<() => void>();
const subscribeTurns = (f: () => void) => (turnListeners.add(f), () => void turnListeners.delete(f));
function useTurnOpen(key: string): [boolean, () => void] {
  const open = useSyncExternalStore(subscribeTurns, () => openTurns.has(key), () => false);
  const toggle = () => {
    if (!openTurns.delete(key)) openTurns.add(key);
    turnListeners.forEach((f) => f());
  };
  return [open, toggle];
}

// a tool call no tool UI is registered for, where one is drawn from the message itself (a step drawn in the fold
// goes through StepRow from the turn's data instead)
const CompactTool: ToolCallMessagePartComponent = ({ toolName, args, argsText, result, status }) => (
  <StepRow toolName={toolName} args={args} argsText={argsText} result={result} running={status.type === "running"} failed={status.type === "incomplete"} />
);

// The fold's content: every step of the turn, from the turn's messages, in order (thoughts, notes between steps, tool
// calls, answered questions), all under the fold's line, so none sits under a card or a reply that came between them
function FoldSteps({ parts, working, bare = false }: { parts: readonly Part[]; working: boolean; bare?: boolean }) {
  return (
    <div className={bare ? "my-1 space-y-1" : "ms-2 space-y-1 border-s-2 py-1 ps-4"}>
      {parts.map((p, i) => {
        if (p.type === "reasoning") return <Thought key={i} text={p.text ?? ""} />;
        if (p.type === "text") return <p key={i} className="text-muted-foreground text-sm whitespace-pre-wrap">{p.text}</p>;
        if (p.type !== "tool-call") return null;
        if (p.toolName === "ask_user_question") return <AnsweredQuestion key={i} args={p.args as never} result={p.result} />;
        return <StepRow key={i} toolName={p.toolName ?? ""} args={p.args} argsText={p.argsText} result={p.result} running={working && p.result === undefined} failed={!!p.isError} />;
      })}
    </div>
  );
}

// What a turn shows, in order: stretches of steps (each one fold) and the things for you between them (cards and
// replies). A stretch runs across messages until a card or a reply interrupts it, so the timeline holds: the steps
// before a wave's line fold above it, the steps after it fold below it. Thoughts the model kept private (they say
// nothing) and the record-keeping around a wave are left out; a stretch with nothing else is no fold at all.
type Item = { kind: "step" | "card" | "answer"; msg: string; i: number; part: Part };

// The agent's own record-keeping around a wave (writing its launch and its end into run.yaml, checking on it,
// closing its mission): the wave's line already says all of it, so as steps they only read as separate events
const BOOKKEEPING = new Set(["agent_log_start", "agent_log_finish"]);
const isBookkeeping = (p: Part) =>
  p.type === "tool-call" && (BOOKKEEPING.has(p.toolName ?? "") || (p.toolName === "subagent" && ["status", "mission.close"].includes(String(p.args?.action ?? ""))));
const isPrivate = (p: Part) => p.type === "reasoning" && (p.text ?? "").startsWith(PRIVATE_THOUGHT);
function turnItems(messages: readonly Msg[]): Item[] {
  return messages
    .filter((m) => m.role === "assistant")
    .flatMap((m) => {
      const { steps, cards, answer } = split(m.content);
      return m.content.flatMap((part, i): Item[] => {
        if (steps.includes(i)) return isPrivate(part) || isBookkeeping(part) ? [] : [{ kind: "step", msg: m.id, i, part }];
        if (cards.includes(i)) return [{ kind: "card", msg: m.id, i, part }];
        if (answer.includes(i)) return [{ kind: "answer", msg: m.id, i, part }];
        return [];
      });
    });
}

// one stretch of steps: "Working on it" while it is the agent's current stretch (the step under way beneath it), else
// "Thought process"; opened, its steps on one indented bar
// (a short finished stretch, one or two steps such as "Noting the wave's launch", needs no fold: its steps show as
// plain lines, each still opening on its own)
const FOLD_FROM = 3;
function Fold({ id, steps, working }: { id: string; steps: Part[]; working: boolean }) {
  const [open, toggle] = useTurnOpen(id);
  if (!working && steps.length < FOLD_FROM) return <FoldSteps parts={steps} working={false} bare />;
  const last = steps[steps.length - 1];
  const now = last?.type === "tool-call" ? stepWords(last.toolName ?? "", (last.args ?? {}) as Record<string, unknown>) : last?.type === "reasoning" ? "Thinking" : "";
  return (
    <div>
      <button type="button" onClick={toggle} className="text-muted-foreground hover:text-foreground my-1 flex items-center gap-1.5 text-sm">
        {working && <span className="bg-primary size-1.5 animate-pulse rounded-full" />}
        <span>{working ? "Working on it" : "Thought process"}</span>
        <ChevronRightIcon className={cn("size-3.5 transition-transform", open && "rotate-90")} />
      </button>
      {working && !open && now && <p className="text-muted-foreground/80 -mt-0.5 mb-1 ps-3 text-xs">{now}</p>}
      {open && <FoldSteps parts={steps} working={working} />}
    </div>
  );
}

const AssistantMessage: FC = () => {
  const { ToolFallback: ToolFallbackComponent = CompactTool } = useContext(ThreadComponentsContext);
  const id = useAuiState((s) => s.message.id);
  const messages = useAuiState((s) => s.thread.messages) as unknown as readonly Msg[];
  const threadRunning = useAuiState((s) => s.thread.isRunning);
  const turn = useMemo(() => turnOf(messages, id), [messages, id]);
  const items = useMemo(() => turnItems(turn.messages), [turn]);
  const components = { Text: MarkdownText, Reasoning: ({ text }: { text: string }) => <Thought text={text} />, tools: { Fallback: ToolFallbackComponent } };
  const working = threadRunning && turn.last;

  // what this message draws: the folds of the stretches that start in it (with their steps from later messages), its
  // cards, its reply
  const blocks: ReactNode[] = [];
  let answerStart = -1;
  items.forEach((it, k) => {
    if (it.msg !== id) return;
    if (it.kind === "step") {
      if (items[k - 1]?.kind === "step") return; // drawn by the fold its stretch started
      let end = k;
      while (items[end + 1]?.kind === "step") end++;
      const isLast = end === items.length - 1;
      blocks.push(<Fold key={`fold-${it.i}`} id={`${id}:${it.i}`} steps={items.slice(k, end + 1).map((x) => x.part)} working={working && isLast} />);
    } else if (it.kind === "card") blocks.push(<MessagePrimitive.PartByIndex key={`card-${it.i}`} index={it.i} components={components} />);
    else if (answerStart < 0) answerStart = blocks.push(null) - 1; // the reply's place (its parts follow together)
  });
  const answer = items.filter((it) => it.msg === id && it.kind === "answer");
  if (answerStart >= 0)
    blocks[answerStart] = (
      // the reply: set apart from a fold or card above it, so it never reads as part of a step
      <div key="answer" className={cn(answerStart > 0 && "mt-4")}>
        {answer.map((it) => (
          <MessagePrimitive.PartByIndex key={it.i} index={it.i} components={components} />
        ))}
      </div>
    );
  // the agent at work before anything shows: a fold line with nothing in it yet
  if (working && !items.length && turn.first) blocks.push(<Fold key="starting" id={`${id}:start`} steps={[]} working />);
  if (!blocks.length) return null; // its steps are in a fold above: nothing of its own

  const ACTION_BAR_PT = "pt-1.5";
  // Keep the action bar inside the contained root's paint box, then cancel its reserved space in flow.
  const ACTION_BAR_HEIGHT = `min-h-7.5 ${ACTION_BAR_PT}`;

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className={cn("fade-in slide-in-from-bottom-1 animate-in relative duration-150", answer.length > 0 && "-mb-7.5 pb-7.5")}
    >
      <div data-slot="aui_assistant-message-content" className="text-foreground px-2 leading-relaxed wrap-break-word">
        {blocks}
        <MessageError />
      </div>

      {answer.length > 0 && (
        <div data-slot="aui_assistant-message-footer" className={cn("ms-2 flex items-center", ACTION_BAR_HEIGHT)}>
          <AssistantActionBar />
        </div>
      )}
    </MessagePrimitive.Root>
  );
};

const AssistantActionBar: FC = () => {
  // a message waiting for an answer (a question) isn't finished: no copy/regenerate until it is
  const waiting = useAuiState((s) => s.message.status?.type === "requires-action");
  if (waiting) return null;
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-assistant-action-bar-root text-muted-foreground animate-in fade-in col-start-3 row-start-2 -ms-1 flex gap-1 duration-200"
    >
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton tooltip="Copy">
          <AuiIf condition={(s) => s.message.isCopied}>
            <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
          </AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>
            <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
          </AuiIf>
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>
      <AuiIf condition={(s) => s.thread.capabilities.feedback}>
        <ActionBarPrimitive.FeedbackPositive asChild>
          <TooltipIconButton
            tooltip="Helpful"
            className="data-[submitted=true]:bg-accent data-[submitted=true]:text-accent-foreground"
          >
            <ThumbsUpIcon />
          </TooltipIconButton>
        </ActionBarPrimitive.FeedbackPositive>
        <ActionBarPrimitive.FeedbackNegative asChild>
          <TooltipIconButton
            tooltip="Not helpful"
            className="data-[submitted=true]:bg-accent data-[submitted=true]:text-accent-foreground"
          >
            <ThumbsDownIcon />
          </TooltipIconButton>
        </ActionBarPrimitive.FeedbackNegative>
      </AuiIf>
      <ActionBarMorePrimitive.Root>
        <ActionBarMorePrimitive.Trigger asChild>
          <TooltipIconButton
            tooltip="More"
            className="data-[state=open]:bg-accent"
          >
            <MoreHorizontalIcon />
          </TooltipIconButton>
        </ActionBarMorePrimitive.Trigger>
        <ActionBarMorePrimitive.Content
          side="bottom"
          align="start"
          sideOffset={6}
          className="aui-action-bar-more-content bg-popover text-popover-foreground data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:animate-out data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 min-w-[8rem] overflow-hidden rounded-xl border p-1.5"
        >
          <ActionBarPrimitive.ExportMarkdown asChild>
            <ActionBarMorePrimitive.Item className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none">
              <DownloadIcon className="size-4" />
              Export as Markdown
            </ActionBarMorePrimitive.Item>
          </ActionBarPrimitive.ExportMarkdown>
        </ActionBarMorePrimitive.Content>
      </ActionBarMorePrimitive.Root>
    </ActionBarPrimitive.Root>
  );
};

const UserFilePart: FileMessagePartComponent = (part) => (
  <div data-slot="aui_user-message-file" className="py-1">
    <File {...part} />
  </div>
);

const UserImagePart: ImageMessagePartComponent = (part) => (
  <div data-slot="aui_user-message-image" className="py-1">
    <Image {...part} />
  </div>
);

const UserMessage: FC = () => {
  const editable = useEditable();
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 duration-150 [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        <div className="aui-user-message-content peer bg-muted text-foreground rounded-(--composer-radius) px-4 py-2 wrap-break-word empty:hidden">
          <MessagePrimitive.Parts
            components={{ File: UserFilePart, Image: UserImagePart }}
          />
        </div>
        {editable && (
          <div className="aui-user-action-bar-wrapper absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 rtl:translate-x-full">
            <ActionBarPrimitive.Root hideWhenRunning autohide="always" className="aui-user-action-bar-root flex flex-col items-end">
              <ActionBarPrimitive.Edit asChild>
                <TooltipIconButton tooltip="Edit: continue the conversation from here" className="aui-user-action-edit">
                  <PencilIcon />
                </TooltipIconButton>
              </ActionBarPrimitive.Edit>
            </ActionBarPrimitive.Root>
          </div>
        )}
      </div>
    </MessagePrimitive.Root>
  );
};

// Editable: a message stored in Pi's session (id "pi-<entry>", so the worker can rewind to it) that comes after the
// last /agent-… command; the command and what precedes it stay as they are (the worker checks the same rule).
function useEditable() {
  const id = useAuiState((s) => s.message.id);
  const messages = useAuiState((s) => s.thread.messages) as unknown as readonly Msg[];
  return useMemo(() => {
    if (!id.startsWith("pi-")) return false;
    const textOf = (m: Msg) => m.content.map((p) => (p.type === "text" ? (p.text ?? "") : "")).join("").trim();
    const at = messages.findIndex((m) => m.id === id);
    const lastCommand = messages.reduce((n, m, i) => (m.role === "user" && textOf(m).startsWith("/agent-") ? i : n), -1);
    return at > lastCommand && !textOf(messages[at]!).startsWith("/agent-");
  }, [id, messages]);
}

// The edit box: Update first asks the worker to rewind Pi's session to just before this message, then sends the new
// text, which the agent continues from (the old path stays in Pi's session as a branch; the terminal's /tree shows it)
const EditComposer: FC = () => {
  const aui = useAui();
  const id = useAuiState((s) => s.message.id);
  const { base } = useAgent();
  const [error, setError] = useState<string | null>(null);
  const update = async () => {
    setError(null);
    try {
      await sendJson(`${base}/edit`, "POST", JSON.stringify({ entry: id }));
      aui.message().composer().send(); // this message's edit composer: the edited text, as a branch from here
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <MessagePrimitive.Root data-slot="aui_edit-composer-wrapper" className="flex flex-col px-2">
      <ComposerPrimitive.Root className="aui-edit-composer-root border-foreground/10 focus-within:border-foreground/25 ms-auto flex w-full max-w-[85%] cursor-text flex-col rounded-(--composer-radius) border bg-(--composer-bg) transition-[border-color]">
        <ComposerPrimitive.Input className="aui-edit-composer-input text-foreground min-h-14 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-base outline-none" autoFocus />
        {error && <p className="mx-4 text-xs text-red-600">{error}</p>}
        <div className="aui-edit-composer-footer mx-2.5 mb-2.5 flex items-center gap-1.5 self-end">
          <ComposerPrimitive.Cancel asChild>
            <Button variant="ghost" size="sm" className="h-8 px-3">
              Cancel
            </Button>
          </ComposerPrimitive.Cancel>
          <Button size="sm" className="h-8 px-3" onClick={() => void update()}>
            Update
          </Button>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
};



