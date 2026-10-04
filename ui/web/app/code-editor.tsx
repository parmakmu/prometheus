"use client";

import { useEffect, useRef } from "react";
import { EditorView, basicSetup, minimalSetup } from "codemirror";
import { EditorState, type Extension } from "@codemirror/state";
import { keymap } from "@codemirror/view";
import { yaml } from "@codemirror/lang-yaml";
import { python } from "@codemirror/lang-python";
import { markdown } from "@codemirror/lang-markdown";
import { javascript } from "@codemirror/lang-javascript";
import { MergeView } from "@codemirror/merge";

// highlighting by a file's name or a code block's language (JSON reads as JavaScript: no JSON package installed)
const language = (name: string): Extension[] =>
  /(^|\.)ya?ml$/.test(name) ? [yaml()] : /(^|\.)(py|python)$/.test(name) ? [python()] : /(^|\.)(md|markdown)$/.test(name) ? [markdown()] : /(^|\.)(m?js|ts|tsx|jsx|json|javascript|typescript)$/.test(name) ? [javascript()] : [];

// what every view shares, and how a read-only one looks: a plain page, no line numbers or other gutters, a grey
// background, no focus ring
const look = (name: string, editable: boolean): Extension[] => [
  editable ? basicSetup : minimalSetup,
  ...language(name),
  EditorView.lineWrapping,
  EditorView.theme({ "&": { fontSize: "13px", maxHeight: "100%" }, ".cm-scroller": { fontFamily: "var(--font-mono, ui-monospace, monospace)" } }),
  ...(editable ? [] : [EditorView.theme({ "&": { backgroundColor: "color-mix(in oklab, var(--color-muted) 70%, transparent)" }, ".cm-content": { padding: "10px 6px" }, "&.cm-focused": { outline: "none" } })]),
  EditorState.readOnly.of(!editable),
  EditorView.editable.of(editable),
];

// CodeMirror 6 in a box: highlights by file type, and edits when `onChange` is given (Ctrl/Cmd-S calls `onSave`)
export function CodeEditor({ name, value, onChange, onSave, className = "rounded-xl border" }: { name: string; value: string; onChange?: (text: string) => void; onSave?: () => void; className?: string }) {
  const host = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ onChange, onSave });
  callbacks.current = { onChange, onSave };
  const editable = !!onChange;
  useEffect(() => {
    const view = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          ...look(name, editable),
          keymap.of([{ key: "Mod-s", preventDefault: true, run: () => (callbacks.current.onSave?.(), true) }]),
          EditorView.updateListener.of((u) => u.docChanged && callbacks.current.onChange?.(u.state.doc.toString())),
        ],
      }),
    });
    return () => view.destroy();
    // the document is the editor's own once open: a new file (name) or mode makes a new editor; a read-only view
    // shows a new value (a code block still streaming in) in a new one too
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, editable, editable ? null : value]);
  return <div ref={host} data-readonly={!editable} className={`overflow-hidden ${className}`} />;
}

// a code block in markdown (the chat's replies, a subagent's answer), read only
export const CodeBlock = ({ language, code, className }: { language?: string; code: string; className?: string }) => (
  <CodeEditor name={language ?? ""} value={code.replace(/\n$/, "")} className={className} />
);

// a file's two sides next to each other, read only: main's copy on the left, the current one on the right, changes
// marked, unchanged stretches folded to a line you can click open
export function DiffEditor({ name, before, after }: { name: string; before: string; after: string }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const view = new MergeView({
      parent: host.current!,
      a: { doc: before, extensions: look(name, false) },
      b: { doc: after, extensions: look(name, false) },
      collapseUnchanged: { margin: 3, minSize: 6 },
    });
    return () => view.destroy();
  }, [name, before, after]);
  return <div ref={host} className="overflow-hidden rounded-xl border" />;
}
