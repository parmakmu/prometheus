"use client";

import { useState, type ReactNode } from "react";
import { makeAssistantToolUI, useAuiState } from "@assistant-ui/react";
import { useAgUiInterrupts, useAgUiSubmitInterruptResponses } from "@assistant-ui/react-ag-ui";
import { ShowOnCanvas, useCanvas } from "./canvas";
import { PanelView } from "./panel";
import { ModelPicker } from "./models";
import { WaveLine } from "./subagents";
import { useAgent } from "./agent";
import { CheckCircle2, ChevronRight, FolderPlus, PanelRight, ListChecks, Loader2, MessageCircleQuestion, ShieldCheck, XCircle } from "lucide-react";

// Prometheus's tools return JSON as their result text
const parse = (result: unknown): any => {
  if (typeof result !== "string") return result ?? undefined;
  try {
    return JSON.parse(result);
  } catch {
    return undefined;
  }
};

function Card({ icon, title, children, busy }: { icon: ReactNode; title: string; children?: ReactNode; busy?: boolean }) {
  return (
    <div className="bg-card my-3 w-full max-w-(--thread-max-width) rounded-xl border p-4 shadow-sm">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <span className="text-primary">{icon}</span>
        {title}
        {busy && <Loader2 className="text-muted-foreground ml-auto size-4 animate-spin" />}
      </div>
      {children && <div className="text-muted-foreground mt-2 text-sm">{children}</div>}
    </div>
  );
}

export const ScaffoldCard = makeAssistantToolUI<{ slug?: string; request?: string }, string>({
  toolName: "workflow_scaffold",
  render: ({ args, status, toolCallId }) => (
    <ShowOnCanvas
      id={`created:${args?.slug ?? toolCallId}`}
      title={`Workflow ${args?.slug ?? ""}`}
      running={status.type === "running"}
      deps={[status.type]}
      node={
        <Card icon={<FolderPlus className="size-4" />} title={`Workflow created: ${args?.slug ?? "…"}`} busy={status.type === "running"}>
          {args?.request && <p className="italic">“{args.request}”</p>}
          {args?.slug && <p className="mt-1 font-mono text-xs">workflows/{args.slug}/</p>}
        </Card>
      }
    />
  ),
});

export const CheckCard = makeAssistantToolUI<Record<string, unknown>, string>({
  toolName: "workflow_check",
  render: ({ result, status }) => {
    const r = parse(result);
    if (!r) return <ShowOnCanvas id="plan" title="Plan" running deps={[status.type]} node={<Card icon={<ListChecks className="size-4" />} title="Checking the plan" busy />} />;
    const waves: { id: string; stages: string[]; ends_with?: string }[] = r.summary?.waves ?? [];
    const errors: string[] = r.errors ?? [];
    return (
      <ShowOnCanvas id="plan" title="Plan" running={status.type === "running"} deps={[status.type, result]} node={
      <Card icon={r.valid ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />} title={`Plan checked: ${r.valid ? "valid" : `${errors.length} problem(s)`}, ${r.status ?? "draft"}`}>
        {waves.length > 0 && (
          <ol className="mt-1 space-y-1">
            {waves.map((w, i) => (
              <li key={w.id} className="flex gap-2">
                <span className="bg-muted rounded px-1.5 text-xs">{i + 1}</span>
                <span>
                  {w.stages.join(", ")}
                  {w.ends_with && w.ends_with !== "none" && <span className="text-primary"> · then {w.ends_with === "approval" ? "asks your approval" : w.ends_with}</span>}
                </span>
              </li>
            ))}
          </ol>
        )}
        {errors.slice(0, 3).map((e) => (
          <p key={e} className="mt-1 text-red-600">{e}</p>
        ))}
      </Card>
      } />
    );
  },
});

// A launch (a wave, the plan critic) as a live line in the chat that opens to its subagents (subagents.tsx); the other
// subagent calls (status, closing the mission) are steps, shown in the reply's Details.
export const SubagentCard = makeAssistantToolUI<{ async?: boolean; action?: string; workflow?: string } & Record<string, unknown>, string>({
  toolName: "subagent",
  render: ({ args, result }) => {
    const id = typeof result === "string" ? result.match(/\[([0-9a-f-]{36})\]/)?.[1] : undefined;
    if (!args?.async) return <p className="text-muted-foreground text-xs">subagent {String(args?.action ?? "")}</p>;
    const critic = JSON.stringify(args).includes("plan-critic");
    const wave = typeof args.workflow === "string" ? args.workflow.match(/\/(w\d+)\.js$/)?.[1] : undefined;
    const title = critic ? "Plan critic" : wave ? `Wave ${wave}` : "Background work";
    if (!id) return <Card icon={<ShieldCheck className="size-4" />} title={`${title}: starting`} busy />;
    return <WaveLine id={id} title={title} />;
  },
});

type Question = { header?: string; question: string; options?: { label: string; description?: string }[] };

// A question (an AG-UI interrupt from an extension dialog): its options as buttons, or a text box. `options`
// are the dialog's raw values (what the answer must be); `question` is ask_user_question's original, for
// labels and descriptions.
type Interrupt = ReturnType<typeof useAgUiInterrupts>[number];
type Meta = { options?: string[]; question?: Question; tool?: string };

function QuestionForm({ q, onDone }: { q: Interrupt; onDone: (id: string) => void }) {
  const submit = useAgUiSubmitInterruptResponses();
  const [text, setText] = useState("");
  const meta = (q.metadata ?? {}) as Meta;
  const values = meta.options ?? [];
  const asked = meta.question;
  const answer = (value: string) => {
    setText("");
    onDone(q.id);
    void submit([{ interruptId: q.id, status: "resolved", payload: { value } }]);
  };
  return (
    <div className="bg-card rounded-xl border p-4 shadow-sm">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <MessageCircleQuestion className="text-primary size-4" />
        {asked?.header ?? "Prometheus asks"}
      </div>
      <p className="mt-2 text-sm whitespace-pre-wrap">{asked?.question ?? q.message}</p>
      {values.length > 0 ? (
        <div className="mt-3 space-y-2">
          {values.map((value, i) => {
            const option = asked?.options?.[i];
            // the package adds "N. Type something." for an answer of your own
            const own = !option && /type something/i.test(value);
            const label = option?.label ?? (own ? "Something else…" : value.replace(/^\d+\.\s*/, ""));
            const recommended = /\(recommended\)/i.test(label);
            return (
              <button key={value} type="button" onClick={() => answer(value)} className="hover:border-primary hover:bg-accent w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors">
                <span className="font-medium">{label.replace(/\s*\(recommended\)/i, "")}</span>
                {recommended && <span className="bg-primary text-primary-foreground ml-2 rounded px-1.5 py-0.5 text-xs">Recommended</span>}
                {(option?.description || own) && <span className="text-muted-foreground block text-xs">{option?.description ?? "Write your own answer"}</span>}
              </button>
            );
          })}
        </div>
      ) : (
        <form className="mt-3 flex gap-2" onSubmit={(e) => (e.preventDefault(), answer(text))}>
          <input value={text} onChange={(e) => setText(e.target.value)} className="flex-1 rounded-lg border px-3 py-2 text-sm" autoFocus />
          <button type="submit" className="bg-primary text-primary-foreground rounded-lg px-3 py-2 text-sm">Send</button>
        </form>
      )}
      <button type="button" onClick={() => (onDone(q.id), void submit([{ interruptId: q.id, status: "cancelled" }]))} className="text-muted-foreground mt-2 text-xs">
        Skip this question
      </button>
    </div>
  );
}

// answered questions leave at once, without waiting for the server
const useOpenQuestions = () => {
  const interrupts = useAgUiInterrupts();
  const [answered, setAnswered] = useState<string[]>([]);
  return [interrupts.filter((i) => !answered.includes(i.id)), (id: string) => setAnswered((a) => [...a, id])] as const;
};

// above the composer: questions from other extensions (ask_user_question's show inline at its tool call)
export function QuestionCard() {
  const [open, done] = useOpenQuestions();
  const q = open.find((i) => (i.metadata as Meta | undefined)?.tool !== "ask_user_question");
  if (!q) return null;
  return (
    <div className="absolute inset-x-4 bottom-28 z-10 shadow-lg">
      <QuestionForm q={q} onDone={done} />
    </div>
  );
}

// in the chat, where the command was typed, while it is open (the panel's own card, with its Done); then a
// line saying it closed
function PanelChip({ id, title, open }: { id: string; title: string; open: boolean }) {
  const { base: agent } = useAgent(); // the panel lives in this conversation's worker
  if (!open)
    return (
      <p className="text-muted-foreground my-2 flex items-center gap-2 text-xs">
        <PanelRight className="size-3.5" /> {title} closed
      </p>
    );
  return (
    <div className="my-3 w-full max-w-(--thread-max-width)">
      <PanelView base={`${agent}/panel/${id}`} />
    </div>
  );
}

// /model: the model picker on the canvas
export const ModelsCard = makeAssistantToolUI<Record<string, unknown>, string>({
  toolName: "pi_models",
  render: ({ toolCallId }) => <ModelsChip id={toolCallId} />,
});
function ModelsChip({ id }: { id: string }) {
  const { base } = useAgent();
  const { remove } = useCanvas();
  return <ShowOnCanvas id={id} title="Model" deps={[]} node={<ModelPicker base={base} onClose={() => remove(id)} />} />;
}

export const PiPanelCard = makeAssistantToolUI<{ title?: string }, string>({
  toolName: "pi_panel",
  render: ({ toolCallId, args, result }) => <PanelChip id={toolCallId} title={args?.title ?? "Panel"} open={result === undefined} />,
});

// ask_user_question in the conversation: while it waits, the question itself (with its options); then
// the questions and the answers given
function AskInline({ args, result }: { args?: { questions?: Question[] }; result?: string }) {
  const [open, done] = useOpenQuestions();
  const running = useAuiState((s) => s.thread.isRunning) || open.length > 0;
  const q = result === undefined ? open.find((i) => (i.metadata as Meta | undefined)?.tool === "ask_user_question") : undefined;
  if (q)
    return (
      <div className="my-3 w-full max-w-(--thread-max-width)">
        <QuestionForm q={q} onDone={done} />
      </div>
    );
  return <AnsweredQuestion args={args} result={result} />;
}

// an answer in the tool's result text, "<question>"="<answer>" (quotes escaped)
const answerIn = (text: string, qu: Question) => {
  const m = text.match(new RegExp(`"${qu.question.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"="((?:[^"\\\\]|\\\\.)*)"`));
  return m?.[1]?.replace(/\\(.)/g, "$1");
};

// an answered (or past) question: a step in the "Thought process" fold like the others, "You answered: Start fresh,
// Same input ›", opening to each question and its answer
export function AnsweredQuestion({ args, result }: { args?: { questions?: Question[] }; result?: unknown }) {
  const text = typeof result === "string" ? result : "";
  return <AnsweredStep items={(args?.questions ?? []).map((qu) => ({ question: qu, answer: answerIn(text, qu) }))} skipped={result !== undefined} />;
}

function AnsweredStep({ items, skipped }: { items: { question: Question; answer?: string }[]; skipped: boolean }) {
  const [open, setOpen] = useState(false);
  const answers = items.map((i) => i.answer).filter(Boolean);
  return (
    <div>
      <button type="button" onClick={() => setOpen((o) => !o)} className="text-muted-foreground hover:text-foreground hover:bg-accent/60 group -mx-1 flex max-w-full min-w-0 items-center gap-1.5 rounded px-1 text-left text-sm">
        <MessageCircleQuestion className="size-3.5 shrink-0" />
        <span className="truncate">{answers.length ? `You answered: ${answers.join(", ")}` : skipped ? "Question skipped" : "Question not answered"}</span>
        <ChevronRight className={`size-3.5 shrink-0 opacity-60 transition-transform group-hover:opacity-100 ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <ul className="mt-1 mb-1.5 space-y-1.5 ps-5 text-sm">
          {items.map(({ question: q, answer }) => (
            <li key={q.question}>
              <p className="text-muted-foreground">{q.header ? <span className="text-foreground/80 font-medium">{q.header}: </span> : null}{q.question}</p>
              <p className="font-medium">{answer ?? "no answer"}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const AskCard = makeAssistantToolUI<{ questions?: Question[] }, string>({
  toolName: "ask_user_question",
  render: ({ args, result }) => <AskInline args={args} result={typeof result === "string" ? result : undefined} />,
});
