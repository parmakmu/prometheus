/**
 * One agent: a Pi SDK session in one folder (prometheus, or a workflow), served as AG-UI on a unix
 * socket. The router (router.ts) starts one per user and agent, checks who may call, and forwards to it;
 * nothing else can reach the socket, so there are no checks here.
 *
 * POST /agent takes a RunAgentInput and answers with an SSE stream of AG-UI events. Pi's events
 * become text, reasoning and tool-call events. An extension dialog (select / confirm / input, which
 * ask_user_question falls back to with mode "rpc") ends the run with an interrupt; the next run's
 * `resume` answers it and the stream carries on from there. GET /status and /commands feed the
 * status bar and the slash menu.
 *
 * Events Pi emits while no run is open (a wave's completion notice) are not delivered yet.
 */
import { createHash, randomUUID } from "node:crypto";
import { rmSync } from "node:fs";
import { createServer, type ServerResponse } from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { EventType } from "@ag-ui/core";
import { EventEncoder } from "@ag-ui/encoder";
// Pi itself: the global install the router found (it names it for pi-subagents too), loaded by path
const PI_ROOT = process.env.PI_SUBAGENTS_PI_CODING_AGENT_PACKAGE_ROOT!;
const { createAgentSession, SessionManager } = await import(pathToFileURL(path.join(PI_ROOT, "dist", "index.js")).href);

const CWD = path.resolve(process.env.AGENT_CWD ?? path.join(import.meta.dirname, ".."));
const SOCKET = process.env.WORKER_SOCKET ?? path.join(CWD, ".pi", "worker.sock");
const encoder = new EventEncoder();

type Dialog = { id: string; reason: string; message: string; options?: string[]; question?: unknown; tool?: string; resolve: (entry: any) => void };

// ask_user_question's questions, kept from its tool call: its dialog fallback flattens each option
// to "1. Label — description", so the interrupt carries the original (header, options) for the card
let questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[] = [];
const questionFor = (title: string) => questions.find((q) => title.includes(q.question));

const seen = new Set<string>(); // user messages already prompted
let lastCommand = ""; // the last slash command sent, which titles the panels it opens
let sink: { res: ServerResponse; threadId: string; runId: string } | null = null;
let pending: Dialog | null = null;

function emit(event: Record<string, unknown>) {
    sink?.res.write(encoder.encodeSSE(event as any));
}

function finish(outcome: Record<string, unknown>) {
    if (!sink) return;
    emit({ type: EventType.RUN_FINISHED, threadId: sink.threadId, runId: sink.runId, outcome });
    sink.res.end();
    sink = null;
}

function interrupt(d: Dialog) {
    finish({
        type: "interrupt",
        interrupts: [{ id: d.id, reason: d.reason, message: d.message, metadata: { options: d.options, question: d.question, tool: d.tool } }],
    });
}

// a dialog blocks Pi until a resume answers it; the open run ends with the interrupt meanwhile
function ask(reason: string, message: string, options: string[] | undefined, read: (entry: any) => unknown) {
    return new Promise<any>((resolve) => {
        // asked by ask_user_question (its tool call shows the question inline), or by another extension
        pending = { id: randomUUID(), reason, message, options, question: questionFor(message), tool: asking ? "ask_user_question" : undefined, resolve: (entry) => resolve(read(entry)) };
        interrupt(pending);
    });
}

function textMessage(text: string, messageId: string = randomUUID()) {
    emit({ type: EventType.TEXT_MESSAGE_START, messageId, role: "assistant" });
    emit({ type: EventType.TEXT_MESSAGE_CONTENT, messageId, delta: text });
    emit({ type: EventType.TEXT_MESSAGE_END, messageId });
}

const value = (e: any) => (e?.status === "resolved" ? (e.payload?.value ?? e.payload) : undefined);
const noop = () => {};
// the dialog methods and notify; the rest are no-ops, as in Pi's own RPC mode. Pi copies these
// properties, so every method must be defined (a Proxy fallback is lost).
// --- panels: an extension's terminal UI (ctx.ui.custom), in the chat -------------------------------
// As pi-web does: the component gets a minimal stand-in for Pi's TUI (its terminal's size and
// requestRender), and each frame is the component's own render(width), sent whole to the page (a card
// in the chat), which takes keys for its handleInput. Whole frames: a page that connects late, or misses
// one, just shows the latest. Pi's theme and keybindings by file (the package's exports leave them out;
// by path they are the very modules the extensions get).
const PI = path.join(PI_ROOT, "dist");
const piModule = (p: string) => import(pathToFileURL(path.join(PI, p)).href);
const { theme, initTheme } = await piModule("modes/interactive/theme/theme.js");
const { KeybindingsManager } = await piModule("core/keybindings.js");
initTheme(process.env.PI_THEME, false);
const keybindings = KeybindingsManager.create();

// The hijack: pi-tui's standard widgets record themselves while a panel draws a frame, so the frame can
// carry their state (rows, values, choices) and the page can draw native controls for them, acting
// through the widgets' own methods. Panels made of other components fall back to their drawn lines.
const tuiLib = await piModule("../node_modules/@earendil-works/pi-tui/dist/index.js");
const { SettingsList, SelectList, Input } = tuiLib;
let drawing: any[] | null = null;
for (const C of [SettingsList, SelectList, Input]) {
    const render = C.prototype.render;
    C.prototype.render = function (width: number) {
        drawing?.push(this);
        return render.call(this, width);
    };
}
const widgetState = (w: any) =>
    w instanceof SettingsList
        ? {
              kind: "settings",
              selected: w.selectedIndex,
              items: (w.filteredItems ?? w.items).map((x: any) => ({ id: x.id, label: x.label, description: x.description, value: x.currentValue, values: x.values, custom: !x.values?.length || !!x.submenu })),
          }
        : w instanceof SelectList
          ? { kind: "select", selected: w.selectedIndex, items: (w.filteredItems ?? w.items).map((x: any) => ({ value: x.value, label: x.label, description: x.description })) }
          : { kind: "input", value: w.getValue() };

type Panel = { cols: number; rows: number; frame: string; widgets: any[]; clients: Set<ServerResponse>; component?: any; render: () => void };
const panels = new Map<string, Panel>();
let panelWaiters: (() => void)[] = [];
// a run stays open while a panel is: its card belongs to it
const panelsClosed = () => (panels.size ? new Promise<void>((r) => panelWaiters.push(r)) : Promise.resolve());
// a panel's card, into the open run (or the next sync, if it opened while none was)
const panelTitles = new Map<string, string>();
const announce = (id: string) => {
    emit({ type: EventType.TOOL_CALL_START, toolCallId: id, toolCallName: "pi_panel" });
    emit({ type: EventType.TOOL_CALL_ARGS, toolCallId: id, delta: JSON.stringify({ title: panelTitles.get(id) ?? "Panel" }) });
    emit({ type: EventType.TOOL_CALL_END, toolCallId: id });
};
const sendFrame = (res: ServerResponse, frame: string) => res.write(`event: frame\ndata: ${Buffer.from(frame).toString("base64")}\n\n`);

// development: what a panel is made of (class names, fields, children), to map widgets to native controls
function describe(o: any, depth = 0, seen = new Set()): string {
    if (!o || typeof o !== "object" || seen.has(o) || depth > 6) return "";
    seen.add(o);
    const pad = "  ".repeat(depth);
    const fields = Object.entries(o)
        .filter(([, v]) => typeof v !== "function")
        .map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.length}]` : typeof v === "object" && v ? (v as any).constructor?.name : JSON.stringify(v)?.slice(0, 40)}`)
        .slice(0, 16)
        .join(" ");
    let out = `\n${pad}${o.constructor?.name ?? "?"}: ${fields}`;
    for (const [k, v] of Object.entries(o)) {
        if (Array.isArray(v)) for (const x of v.slice(0, 12)) if (x && typeof x === "object" && "render" in x) out += describe(x, depth + 1, seen);
        if (v && typeof v === "object" && !Array.isArray(v) && "render" in (v as any) && k !== "tui") out += `\n${pad} .${k}:` + describe(v, depth + 1, seen);
    }
    return out;
}

// ctx.ui.custom(): the component, drawn frame by frame into a "pi_panel" card until it calls done()
function openPanel(factory: any, _options?: any): Promise<unknown> {
    // ask_user_question's terminal questionnaire: declined, so it falls back to its dialogs, which the chat
    // shows as a question card with clickable options
    if (asking) return Promise.resolve(undefined);
    const id = `panel-${randomUUID()}`;
    let scheduled = false;
    const panel: Panel = {
        cols: 92,
        rows: 24,
        frame: "",
        widgets: [],
        clients: new Set(),
        render: () => {
            if (scheduled) return;
            scheduled = true;
            setImmediate(() => {
                scheduled = false;
                if (!panel.component) return;
                let lines: string[];
                drawing = [];
                try {
                    lines = panel.component.render(panel.cols);
                } catch (err) {
                    lines = [`panel render failed: ${String((err as Error)?.message ?? err)}`];
                }
                panel.widgets = drawing;
                drawing = null;
                // a frame: the drawn lines (the fallback), and the state of the standard widgets in it
                panel.frame = JSON.stringify({ lines, widgets: panel.widgets.map(widgetState) });
                for (const c of panel.clients) sendFrame(c, panel.frame);
            });
        },
    };
    // what the component may ask of "the TUI": its terminal's size, and a redraw
    const tui = {
        terminal: {
            get columns() {
                return panel.cols;
            },
            get rows() {
                return panel.rows;
            },
            kittyProtocolActive: false,
        },
        requestRender: () => panel.render(),
    };
    panels.set(id, panel);
    panelTitles.set(id, lastCommand || "Panel"); // titled after the command that opened it
    announce(id);
    return new Promise((resolve) => {
        let closed = false;
        const close = (result: unknown) => {
            if (closed) return;
            closed = true;
            for (const c of panel.clients) c.end("event: exit\ndata: \n\n");
            panels.delete(id);
            panelTitles.delete(id);
            emit({ type: EventType.TOOL_CALL_RESULT, messageId: randomUUID(), toolCallId: id, content: "closed", role: "tool" });
            if (!panels.size) for (const w of panelWaiters.splice(0)) w();
            resolve(result);
            try {
                panel.component?.dispose?.();
            } catch {}
        };
        Promise.resolve(factory(tui, theme, keybindings, close)).then(
            (c: any) => {
                if (closed) return;
                panel.component = c;
                c.focused = true;
                if (process.env.PANEL_DUMP) console.error("panel tree:", describe(c));
                panel.render();
            },
            (err: unknown) => {
                console.error("panel failed:", err);
                textMessage(`panel failed: ${String((err as Error)?.message ?? err)}`);
                close(undefined);
            },
        );
    });
}

function panelRoute(id: string, what: string, req: any, res: ServerResponse) {
    const panel = panels.get(id);
    if (!panel) {
        res.writeHead(404);
        return res.end("no such panel");
    }
    if (what === "stream") {
        res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
        if (panel.frame) sendFrame(res, panel.frame);
        panel.clients.add(res);
        res.on("close", () => panel.clients.delete(res));
        return;
    }
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
        const body = Buffer.concat(chunks).toString();
        if (what === "input") panel.component?.handleInput?.(body);
        else if (what === "action") {
            // a native control used: through the widget's own methods, as its keys would
            const a = JSON.parse(body || "{}");
            const w = panel.widgets[a.widget];
            if (w instanceof SettingsList && a.op === "set") {
                w.updateValue(a.id, a.value);
                (w as any).onChange?.(a.id, a.value);
            } else if (w instanceof SettingsList && a.op === "edit") {
                w.selectItem(a.id);
                w.handleInput("\r"); // a custom value: its own editor (often an Input) takes over
            } else if (w instanceof SelectList && a.op === "choose") {
                w.setSelectedIndex(a.index);
                const item = ((w as any).filteredItems ?? (w as any).items)[a.index];
                w.onSelect?.(item);
            } else if (w instanceof Input && a.op === "submit") {
                w.setValue(String(a.value ?? ""));
                w.onSubmit?.(String(a.value ?? ""));
            } else if (a.op === "cancel") panel.component?.handleInput?.("\x1b");
        }
        else if (what === "resize") {
            const { cols, rows } = JSON.parse(body || "{}");
            panel.cols = Number(cols) || panel.cols;
            panel.rows = Number(rows) || panel.rows;
            panel.component?.invalidate?.();
        }
        panel.render();
        res.writeHead(200);
        res.end();
    });
}

const uiContext = {
    select: (title: string, options: string[]) => ask("select", title, options, value),
    confirm: (title: string, message: string) =>
        ask("confirm", `${title}\n\n${message}`, ["Yes", "No"], (e) => value(e) === true || value(e) === "Yes"),
    input: (title: string, placeholder?: string) => ask("input", placeholder ? `${title} (${placeholder})` : title, undefined, value),
    editor: (title: string) => ask("input", title, undefined, value),
    // a notification goes into the chat, except while a panel is open: there it only echoes the change the
    // panel already shows (a terminal flashes these in its footer)
    notify: (message: string) => (process.env.DEBUG && console.error("notify:", message), panels.size ? undefined : textMessage(message)),
    onTerminalInput: () => noop,
    getEditorText: () => "",
    getEditorComponent: () => undefined,
    getToolsExpanded: () => false,
    getAllThemes: () => [],
    getTheme: () => undefined,
    setTheme: () => ({ success: false, error: "no themes in the web UI" }),
    custom: (factory: any, options?: any) => openPanel(factory, options),
    get theme() {
        return theme;
    },
    ...Object.fromEntries(
        ["setStatus", "setWorkingMessage", "setWorkingVisible", "setWorkingIndicator", "setHiddenThinkingLabel", "setWidget",
            "setFooter", "setHeader", "setTitle", "pasteToEditor", "setEditorText", "addAutocompleteProvider", "setEditorComponent",
            "setToolsExpanded"].map((k) => [k, noop]),
    ),
};

// the conversation: SESSION_ID names a Pi session ("new-…" starts one); without it, the folder's latest,
// as `pi --continue` does. A restarted worker (idle stop, router restart) carries on where it was.
// Conversations live in the folder (.pi/sessions, the `sessionDir` of its .pi/settings.json), shared
// with the terminal's pi.
const SESSION = process.env.SESSION_ID ?? "";
const SESSIONS = path.join(CWD, ".pi", "sessions");
const openSession = async () => {
    if (!SESSION) return SessionManager.continueRecent(CWD, SESSIONS);
    if (SESSION.startsWith("new-")) return SessionManager.create(CWD, SESSIONS);
    const found = (await SessionManager.list(CWD, SESSIONS)).find((s) => s.id === SESSION);
    if (!found) console.error(`no session ${SESSION}: starting a new one`);
    return found ? SessionManager.open(found.path, SESSIONS) : SessionManager.create(CWD, SESSIONS);
};
const { session } = await createAgentSession({ cwd: CWD, sessionManager: await openSession() });
await (session as any).bindExtensions({ uiContext, mode: "tui", commandContextActions: { waitForIdle: () => session.waitForIdle() } });

const textOf = (content: unknown): string =>
    typeof content === "string"
        ? content
        : Array.isArray(content)
            ? content.map((p: any) => (p?.type === "text" ? p.text : "")).join("")
            : "";

// a command such as /workflow-new returns before the turn it starts: wait for Pi to settle
let busy = false;
let waiting: (() => void)[] = [];
const idle = () => (busy ? new Promise<void>((r) => waiting.push(r)) : Promise.resolve());
const settled = async () => {
    await new Promise((r) => setTimeout(r, 3000));
    await idle();
};

// A command such as /workflow-run reaches Pi's session as the prompt it sends the model, not as what you typed. The
// worker notes which command a prompt came from in the session itself (a custom entry, which the model never sees
// and the terminal ignores), so the history shows "/workflow-run" as you typed it: not the prompt, and not as two
// versions of one message (the page's "/workflow-run" and the session's prompt, which it showed as branches 1/2, 2/2).
const COMMAND_ENTRY = "prometheus-ui-command";
const hashOf = (text: string) => createHash("sha256").update(text.trim()).digest("hex").slice(0, 16);
// The command being run, from when it is sent until it shows something or sends its prompt: noted then, so each
// output carries its own command even when another command starts before this one's run has settled.
let commandRun: { command: string; shown: boolean } | null = null;
// a command's prompt arrived as a user message: the history shows the command in its place (or nothing, when the
// command was already shown above its output)
function noteCommandPrompt(message: any) {
    const prompt = textOf(message?.content).trim();
    if (!commandRun || !prompt || prompt === commandRun.command) return;
    session.sessionManager.appendCustomEntry(COMMAND_ENTRY, { command: commandRun.command, prompt: hashOf(prompt), ...(commandRun.shown ? { hidden: true } : {}) });
    commandRun = null;
}
// A command that shows something (/workflow-list) leaves no user message in Pi's session: noted with the first thing
// it shows (`before`), so the history shows it there as you typed it, instead of it vanishing at the run's end
function noteShownCommand(entry: any) {
    if (!commandRun || commandRun.shown || entry?.type !== "custom_message") return;
    session.sessionManager.appendCustomEntry(COMMAND_ENTRY, { command: commandRun.command, before: entry.id });
    commandRun.shown = true;
}
const shownCommands = () =>
    new Map<string, { id: string; command: string }>(
        session.sessionManager
            .getEntries()
            .filter((e: any) => e.type === "custom" && e.customType === COMMAND_ENTRY && e.data?.before)
            .map((e: any) => [e.data.before, { id: e.id, command: e.data.command }]),
    );
const commandsByPrompt = () =>
    new Map<string, string | null>(
        session.sessionManager
            .getEntries()
            .filter((e: any) => e.type === "custom" && e.customType === COMMAND_ENTRY && e.data?.prompt)
            .map((e: any) => [e.data.prompt, e.data.hidden ? null : e.data.command]),
    );

// What a thinking block says: its text, else the readable summaries in its provider data (OpenRouter keeps them in
// the block's signature as reasoning_details), else a note that the model sent its reasoning encrypted (OpenAI's
// models through OpenRouter often do), so the Details say why there is nothing to read rather than show nothing.
const HIDDEN_THOUGHT = "(The model kept this reasoning private: it sent it encrypted, with no readable summary.)";
function thoughtOf(block: any): string {
    if (block?.thinking?.trim()) return block.thinking;
    try {
        const details = JSON.parse(block?.thinkingSignature ?? "");
        const text = (Array.isArray(details) ? details : [details]).map((d: any) => d?.summary ?? d?.text ?? "").filter(Boolean).join("\n\n");
        if (text.trim()) return text;
    } catch {}
    return HIDDEN_THOUGHT;
}

// The conversation so far, from Pi's session (the record that survives reloads and restarts), as AG-UI
// messages: the current branch of its tree, root to leaf. Ids are "pi-<entry id>": a run whose last user message has
// such an id brings nothing new, and an edit names the entry it rewinds to. An assistant message's thinking comes
// first, as a reasoning message, so the page can show it in the turn's Details.
const branchMessages = () =>
    (session.sessionManager.getBranch() as any[])
        .filter((e) => e.type === "message" || e.type === "custom_message")
        .map((e) => ({ id: e.id, m: e.type === "message" ? e.message : { role: "custom", content: e.content, display: e.display } }));

function snapshot() {
    const out: Record<string, unknown>[] = [];
    const commands = commandsByPrompt();
    const shown = shownCommands();
    for (const { id: entry, m } of branchMessages()) {
        const id = `pi-${entry}`;
        // a pi- id: the page sending it back is a sync, never the command again
        const command = shown.get(entry);
        if (command) out.push({ id: `pi-${command.id}`, role: "user", content: command.command });
        if (m.role === "user") {
            const hash = hashOf(textOf(m.content));
            if (commands.get(hash) !== null) out.push({ id, role: "user", content: commands.get(hash) ?? textOf(m.content) }); // null: shown above
        }
        else if (m.role === "assistant") {
            const parts: any[] = Array.isArray(m.content) ? m.content : [];
            const thinking = [...new Set(parts.filter((p) => p.type === "thinking").map(thoughtOf))].join("\n\n");
            if (thinking) out.push({ id: `${id}-thinking`, role: "reasoning", content: thinking });
            const toolCalls = parts
                .filter((p) => p.type === "toolCall")
                .map((p) => ({ id: p.id, type: "function", function: { name: p.name, arguments: JSON.stringify(p.arguments ?? {}) } }));
            out.push({ id, role: "assistant", content: textOf(parts), ...(toolCalls.length ? { toolCalls } : {}) });
        } else if (m.role === "toolResult") {
            const limit = String(m.toolName).startsWith("workflow_") ? 20000 : 4000;
            out.push({ id, role: "tool", toolCallId: m.toolCallId, content: textOf(m.content).slice(0, limit) || "done" });
        } else if (m.role === "custom" && m.display !== false && textOf(m.content)) out.push({ id, role: "assistant", content: textOf(m.content) });
    }
    return out;
}

// Editing a message of yours: Pi's session is a tree, so an edit is a real branch. POST /edit {entry} rewinds the
// session to just before that message (Pi's navigateTree, as /tree does in the terminal; the old path stays in the
// session as a branch), then the page sends the edited text, which continues from there. Only messages after the
// last /workflow-… command: a workflow command acts on the disk (runs, commits), which rewinding the conversation
// would not undo.
function editRefusal(entry: string): string | null {
    if (busy || session.isStreaming) return "The agent is working: edit once it has finished.";
    const users = branchMessages().filter((x) => x.m.role === "user");
    const at = users.findIndex((x) => x.id === entry);
    if (at < 0) return "That message is not in this conversation's current branch.";
    const commands = commandsByPrompt();
    const lastCommand = users.reduce((n, x, i) => ((commands.get(hashOf(textOf(x.m.content))) ?? textOf(x.m.content)).trim().startsWith("/workflow-") ? i : n), -1);
    if (at <= lastCommand) return "Only messages after the last workflow command can be edited: the command's work (runs, commits) would not be undone.";
    return null;
}
function edit(req: any, res: ServerResponse) {
    let body = "";
    req.on("data", (c: Buffer) => (body += c));
    req.on("end", async () => {
        const entry = String(JSON.parse(body || "{}").entry ?? "").replace(/^pi-/, "");
        const why = editRefusal(entry);
        if (why) {
            res.writeHead(409, { "Content-Type": "text/plain" });
            return res.end(why);
        }
        await session.navigateTree(entry, { summarize: false });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
    });
}

let turn = 0;
let asking = false; // ask_user_question is running
const opened = new Set<string>(); // text/reasoning blocks whose START this run has sent
const thought = new Set<string>(); // reasoning blocks that have streamed some text
let partial: any = null; // the assistant message being written, for a page that attaches mid-way
session.subscribe((event: any) => {
    if (process.env.DEBUG) console.error("pi:", event.type, event.assistantMessageEvent?.type ?? event.toolName ?? "", event.type === "message_end" ? JSON.stringify({ role: event.message?.role, stop: event.message?.stopReason, err: event.message?.errorMessage, text: textOf(event.message?.content).slice(0, 200) }) : "");
    if (event.type === "agent_start") busy = true;
    if (event.type === "agent_settled") {
        busy = false;
        for (const w of waiting.splice(0)) w();
    }
    if (event.type === "message_start" && event.message?.role === "assistant") turn++;
    if (event.type === "message_update") partial = event.message;
    if (event.type === "message_end") partial = null;
    if (event.type === "message_end" && event.message?.role === "user") noteCommandPrompt(event.message);
    if (event.toolName === "ask_user_question") asking = event.type === "tool_execution_start";
    if (!sink) return;
    if (event.type === "message_update") {
        const e = event.assistantMessageEvent;
        const messageId = `m${turn}-${e.contentIndex}`;
        // a page that attaches mid-block (to the agent's own work) needs the block's start first
        const text = () => opened.has(messageId) || (opened.add(messageId), emit({ type: EventType.TEXT_MESSAGE_START, messageId, role: "assistant" }));
        const reasoning = () =>
            opened.has(messageId) ||
            (opened.add(messageId),
            emit({ type: EventType.REASONING_START, messageId }),
            emit({ type: EventType.REASONING_MESSAGE_START, messageId, role: "reasoning" }));
        if (e.type === "text_start") text();
        else if (e.type === "text_delta") (text(), emit({ type: EventType.TEXT_MESSAGE_CONTENT, messageId, delta: e.delta }));
        else if (e.type === "text_end") (text(), emit({ type: EventType.TEXT_MESSAGE_END, messageId }), opened.delete(messageId));
        else if (e.type === "thinking_start") reasoning();
        else if (e.type === "thinking_delta") (reasoning(), thought.add(messageId), emit({ type: EventType.REASONING_MESSAGE_CONTENT, messageId, delta: e.delta }));
        else if (e.type === "thinking_end") {
            reasoning();
            // nothing streamed: a summary from the block's provider data, or the note that it is encrypted
            if (!thought.delete(messageId)) emit({ type: EventType.REASONING_MESSAGE_CONTENT, messageId, delta: thoughtOf(e.partial?.content?.[e.contentIndex]) });
            emit({ type: EventType.REASONING_MESSAGE_END, messageId });
            emit({ type: EventType.REASONING_END, messageId });
            opened.delete(messageId);
        }
    } else if (event.type === "message_end" && event.message?.role === "custom" && event.message.display !== false) {
        const text = textOf(event.message.content); // an extension's sendMessage, e.g. /workflow-list output
        // Pi records it before announcing it (when idle): named as the snapshot names it (pi-<entry id>), the snapshot
        // that ends the run replaces it instead of the page keeping both
        const entry = (session.sessionManager.getBranch() as any[]).at(-1);
        if (text) textMessage(text, entry?.type === "custom_message" ? `pi-${entry.id}` : undefined);
        noteShownCommand(entry);
    } else if (event.type === "tool_execution_start") {
        if (event.toolName === "ask_user_question") questions = event.args?.questions ?? [];
        emit({ type: EventType.TOOL_CALL_START, toolCallId: event.toolCallId, toolCallName: event.toolName });
        emit({ type: EventType.TOOL_CALL_ARGS, toolCallId: event.toolCallId, delta: JSON.stringify(event.args ?? {}) });
        emit({ type: EventType.TOOL_CALL_END, toolCallId: event.toolCallId });
    } else if (event.type === "tool_execution_end") {
        // workflow tools return JSON the cards parse: keep it whole; trim the rest
        const limit = String(event.toolName).startsWith("workflow_") ? 20000 : 4000;
        const content = textOf(event.result?.content).slice(0, limit) || (event.isError ? "error" : "done");
        emit({ type: EventType.TOOL_CALL_RESULT, messageId: randomUUID(), toolCallId: event.toolCallId, content, role: "tool" });
    }
});

async function run(input: any, res: ServerResponse) {
    const threadId = input.threadId ?? randomUUID();
    const runId = input.runId ?? randomUUID();
    res.writeHead(200, { "Content-Type": encoder.getContentType(), "Cache-Control": "no-cache" });
    sink = { res, threadId, runId };
    // the page went away (reload, closed tab): stop streaming to it; the agent carries on, and the next
    // sync attaches to it
    res.on("close", () => {
        if (sink?.res === res) sink = null;
    });
    opened.clear();
    emit({ type: EventType.RUN_STARTED, threadId, runId });

    const answer = (input.resume ?? []).find((e: any) => e.interruptId === pending?.id);
    if (pending && answer) {
        const d = pending;
        pending = null;
        d.resolve(answer); // Pi carries on; its events stream into this run
        return;
    }
    if (pending) return interrupt(pending); // still waiting: ask again

    const last = [...(input.messages ?? [])].reverse().find((m: any) => m.role === "user");
    const text = textOf(last?.content).trim();
    if (!text || String(last.id ?? "").startsWith("pi-") || seen.has(last.id)) {
        // nothing new: a sync. The conversation so far, then the agent's own work while it is busy
        // (a wave finished, say), until it settles
        emit({ type: EventType.MESSAGES_SNAPSHOT, messages: snapshot() });
        // what the reply being written says so far; the live stream continues from there
        (Array.isArray(partial?.content) ? partial.content : []).forEach((b: any, i: number) => {
            const messageId = `m${turn}-${i}`;
            if (b.type === "text" && b.text) {
                emit({ type: EventType.TEXT_MESSAGE_START, messageId, role: "assistant" });
                emit({ type: EventType.TEXT_MESSAGE_CONTENT, messageId, delta: b.text });
                opened.add(messageId);
            } else if (b.type === "thinking" && b.thinking) {
                emit({ type: EventType.REASONING_START, messageId });
                emit({ type: EventType.REASONING_MESSAGE_START, messageId, role: "reasoning" });
                emit({ type: EventType.REASONING_MESSAGE_CONTENT, messageId, delta: b.thinking });
                opened.add(messageId);
            }
        });
        for (const id of panels.keys()) announce(id);
        await idle();
        await panelsClosed();
        return finish({ type: "success" });
    }
    seen.add(last.id);
    lastCommand = text.startsWith("/") ? text.split(/\s/)[0] : "";
    try {
        if (text.startsWith("/") && (await builtin(text))) return finish({ type: "success" });
        const mine = (commandRun = text.startsWith("/") ? { command: text, shown: false } : null); // an extension's command: noted when it shows something or prompts
        if (process.env.DEBUG) console.error("prompt:", text, "streaming:", session.isStreaming);
        if (session.isStreaming) await session.followUp(text);
        else await session.prompt(text);
        await settled();
        if (commandRun === mine) commandRun = null; // it showed and prompted nothing: nothing later is its
        emit({ type: EventType.MESSAGES_SNAPSHOT, messages: snapshot() }); // the page's messages get Pi's ids (edits name them)
        await panelsClosed();
        finish({ type: "success" });
    } catch (err) {
        emit({ type: EventType.RUN_ERROR, message: String((err as Error)?.message ?? err) });
        sink?.res.end();
        sink = null;
    }
}

// the status bar, as pi-cc-extensions' footer shows it: model, thinking, context, cache, tokens, cost
function status(res: ServerResponse) {
    const stats = session.getSessionStats();
    const m: any = session.model;
    const last: any = [...session.messages].reverse().find((x: any) => x.role === "assistant" && x.usage);
    const u = last?.usage;
    const prompt = u ? u.input + u.cacheRead + u.cacheWrite : 0;
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
        JSON.stringify({
            model: m ? { provider: m.provider, id: m.id, name: m.name ?? m.id, contextWindow: m.contextWindow } : null,
            thinking: session.thinkingLevel,
            thinkingLevels: session.getAvailableThinkingLevels(),
            busy,
            panels: panels.size,
            sessionId: stats.sessionId,
            context: stats.contextUsage ?? session.getContextUsage() ?? null,
            cache: { lastPercent: prompt ? Math.round((u.cacheRead * 100) / prompt) : null, read: stats.tokens.cacheRead, write: stats.tokens.cacheWrite },
            tokens: { input: stats.tokens.input, output: stats.tokens.output },
            cost: stats.cost,
        }),
    );
}

// the slash commands that work here: the extensions' (/workflow-new, …). Pi's own TUI commands
// (/model, /compact, …) are not run by session.prompt(), so they are not offered.
// --- Pi's built-in commands: part of its terminal app, not extensions, so done here with the SDK ----

const BUILTIN: Record<string, string> = {
    model: "Choose the model: /model, or /model provider/id",
    thinking: "Set the thinking level: /thinking, or /thinking <level>",
    compact: "Compact the context: /compact [instructions]",
    session: "This conversation: model, messages, tokens, cache, cost",
    name: "Name this conversation: /name <name>",
    reload: "Reload extensions, skills and context files",
    new: "Start a new conversation (the + in Conversations)",
    resume: "Open another conversation (the Conversations list)",
};
const modelKey = (m: any) => `${m.provider}/${m.id}`;
const fmt = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n));

// true when `text` was one of them (and is done)
async function builtin(text: string): Promise<boolean> {
    const [word, ...rest] = text.trim().split(/\s+/);
    const arg = rest.join(" ").trim();
    const name = word.slice(1);
    if (!(name in BUILTIN)) return false;
    if (name === "model") {
        if (!arg) {
            // the picker: a card on the canvas (a long list, searchable)
            const id = `models-${randomUUID()}`;
            emit({ type: EventType.TOOL_CALL_START, toolCallId: id, toolCallName: "pi_models" });
            emit({ type: EventType.TOOL_CALL_END, toolCallId: id });
            return true;
        }
        const models = await session.modelRuntime.getAvailable();
        const m = models.find((x: any) => modelKey(x) === arg || x.id === arg);
        if (!m) textMessage(`No model "${arg}" among the ${models.length} you can use.`);
        else await session.setModel(m), textMessage(`Model: ${m.name ?? m.id}`);
    } else if (name === "thinking") {
        const levels = session.getAvailableThinkingLevels();
        const level = arg || (await ask("select", `Thinking level (now ${session.thinkingLevel})`, levels, value));
        if (!level) return true;
        if (!levels.includes(level)) textMessage(`"${level}" isn't one of: ${levels.join(", ")}.`);
        else session.setThinkingLevel(level), textMessage(`Thinking: ${level}`);
    } else if (name === "compact") {
        textMessage("Compacting the context…");
        const r = await session.compact(arg || undefined);
        textMessage(`Compacted: ${fmt(r.tokensBefore)} → ${r.estimatedTokensAfter ? fmt(r.estimatedTokensAfter) : "?"} tokens.`);
    } else if (name === "session") {
        const s = session.getSessionStats();
        const m: any = session.model;
        const c = s.contextUsage;
        textMessage(
            [
                `**Conversation** \`${s.sessionId}\``,
                `- model: ${m ? `${m.name ?? m.id} (${modelKey(m)})` : "none"}, thinking: ${session.thinkingLevel}`,
                `- messages: ${s.userMessages} from you, ${s.assistantMessages} replies, ${s.toolCalls} tool calls`,
                `- tokens: ${fmt(s.tokens.input)} in, ${fmt(s.tokens.output)} out; cache ${fmt(s.tokens.cacheRead)} read, ${fmt(s.tokens.cacheWrite)} written`,
                `- context: ${c?.tokens != null ? `${fmt(c.tokens)} of ${fmt(c.contextWindow)} (${Math.round(c.percent ?? 0)}%)` : "unknown"}`,
                `- cost: $${s.cost.toFixed(4)}`,
            ].join("\n"),
        );
    } else if (name === "name") {
        const n = arg || (await ask("input", "Name this conversation", undefined, value));
        if (n) session.setSessionName(String(n)), textMessage(`Named: ${n}`);
    } else if (name === "reload") {
        await (session as any).reload();
        textMessage("Reloaded extensions, skills and context files.");
    } else textMessage(name === "new" ? "Start a new conversation with the + next to Conversations, on the left." : "Pick a conversation from the Conversations list, on the left.");
    return true;
}

// the model picker's data, and its choice
async function models(res: ServerResponse) {
    const list = await session.modelRuntime.getAvailable();
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ current: session.model ? modelKey(session.model) : null, models: list.map((m: any) => ({ key: modelKey(m), provider: m.provider, id: m.id, name: m.name ?? m.id, contextWindow: m.contextWindow, reasoning: !!m.reasoning })) }));
}
function setModel(req: any, res: ServerResponse) {
    let body = "";
    req.on("data", (c: Buffer) => (body += c));
    req.on("end", async () => {
        const key = JSON.parse(body || "{}").key;
        const m = (await session.modelRuntime.getAvailable()).find((x: any) => modelKey(x) === key);
        if (m) await session.setModel(m);
        res.writeHead(m ? 200 : 404);
        res.end();
    });
}

// whether a command needs text after it, read from the usage its description shows: `<x>` required, `[x]` or none
// optional. The page runs a command with nothing required at once, and fills in one that needs input.
const argsOf = (description: string) => (/<[^>]+>/.test(description.replace(/\[[^\]]*\]/g, "")) ? "required" : /\[[^\]]+\]/.test(description) ? "optional" : "none");

function commands(res: ServerResponse) {
    const runner = (session as any)._extensionRunner;
    const list = (runner?.getRegisteredCommands?.() ?? []).map((c: any) => ({
        name: c.invocationName ?? c.name,
        description: c.description ?? "",
        args: argsOf(c.description ?? ""),
    }));
    res.writeHead(200, { "Content-Type": "application/json" });
    // this agent's own first (/workflow-…); the other extensions' (some only make sense in the terminal) after
    const ours = (c: any) => (c.name.startsWith("workflow-") ? 0 : 1);
    for (const c of list) c.group = ours(c) ? "Other extensions" : "main"; // main: this agent's own
    // Pi's own all run without text: what they need they ask (/model's picker, /thinking's choice, /name's field)
    const pi = Object.entries(BUILTIN).map(([name, description]) => ({ name, description, group: "pi", args: "optional" }));
    res.end(JSON.stringify([...list.filter((c: any) => !ours(c)), ...pi, ...list.filter((c: any) => ours(c))].sort((a: any, b: any) => (a.group === "main" ? 0 : a.group === "pi" ? 1 : 2) - (b.group === "main" ? 0 : b.group === "pi" ? 1 : 2) || a.name.localeCompare(b.name))));
}

createServer((req, res) => {
    req.url = (req.url ?? "/").split("?")[0];
    if (req.method === "GET" && req.url === "/status") return status(res);
    if (req.method === "GET" && req.url === "/commands") return commands(res);
    if (req.method === "GET" && req.url === "/models") return void models(res);
    if (req.method === "POST" && req.url === "/model") return setModel(req, res);
    if (req.method === "POST" && req.url === "/thinking") {
        let body = "";
        req.on("data", (c) => (body += c));
        req.on("end", () => {
            const level = JSON.parse(body || "{}").level;
            const ok = session.getAvailableThinkingLevels().includes(level);
            if (ok) session.setThinkingLevel(level); // this conversation only
            res.writeHead(ok ? 200 : 400);
            res.end();
        });
        return;
    }
    if (req.method === "POST" && req.url === "/edit") return edit(req, res);
    const pm = req.url.match(/^\/panel\/([\w-]+)\/(stream|input|resize|action)$/);
    if (pm) return panelRoute(pm[1], pm[2], req, res);
    if (req.method !== "POST" || req.url !== "/agent") {
        res.writeHead(404);
        return res.end();
    }
    if (sink) {
        res.writeHead(409);
        return res.end("a run is open");
    }
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => void run(JSON.parse(body || "{}"), res));
}).listen((rmSync(SOCKET, { force: true }), SOCKET), () => console.log(`worker ready: ${CWD} on ${SOCKET}`));
