"use client";

import { useEffect, useRef } from "react";
import { EditorView, basicSetup } from "codemirror";
import { EditorState, type Extension } from "@codemirror/state";
import { keymap } from "@codemirror/view";
import { yaml } from "@codemirror/lang-yaml";
import { python } from "@codemirror/lang-python";
import { markdown } from "@codemirror/lang-markdown";
import { javascript } from "@codemirror/lang-javascript";

const language = (name: string): Extension[] =>
  /\.ya?ml$/.test(name) ? [yaml()] : name.endsWith(".py") ? [python()] : name.endsWith(".md") ? [markdown()] : /\.(m?js|ts)$/.test(name) ? [javascript()] : [];

// CodeMirror 6 in a box: highlights by file type, and edits when `onChange` is given (Ctrl/Cmd-S calls `onSave`)
export function CodeEditor({ name, value, onChange, onSave }: { name: string; value: string; onChange?: (text: string) => void; onSave?: () => void }) {
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
          basicSetup,
          ...language(name),
          EditorView.lineWrapping,
          EditorView.theme({ "&": { fontSize: "13px", maxHeight: "100%" }, ".cm-scroller": { fontFamily: "var(--font-mono, ui-monospace, monospace)" } }),
          // read-only reads as read-only: a grey page, and no focus ring or line highlight to suggest typing
          ...(editable ? [] : [EditorView.theme({ "&": { backgroundColor: "color-mix(in oklab, var(--color-muted) 70%, transparent)" }, ".cm-gutters": { backgroundColor: "transparent" }, ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "transparent" } })]),
          EditorState.readOnly.of(!editable),
          EditorView.editable.of(editable),
          keymap.of([{ key: "Mod-s", preventDefault: true, run: () => (callbacks.current.onSave?.(), true) }]),
          EditorView.updateListener.of((u) => u.docChanged && callbacks.current.onChange?.(u.state.doc.toString())),
        ],
      }),
    });
    return () => view.destroy();
    // the document is the editor's own once open: a new file (name) or mode makes a new editor
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, editable]);
  return <div ref={host} data-readonly={!editable} className="overflow-hidden rounded-xl border" />;
}
