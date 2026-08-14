import { useEffect, useRef } from "react";
import { hydrateMermaid, renderMarkdown } from "./markdown";
import { linesFromRange, selectionPoint } from "./sourceLine";
import type { SelectionAnchor } from "../types";

type Props = {
  markdown: string;
  onSelect: (anchor: SelectionAnchor) => void;
};

export function MarkdownView({ markdown, onSelect }: Props) {
  const ref = useRef<HTMLElement>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.innerHTML = renderMarkdown(markdown);
    const onToggle = (ev: Event) => {
      const t = ev.target;
      if (t instanceof HTMLDetailsElement && t.open) {
        void hydrateMermaid(t);
      }
    };
    el.addEventListener("toggle", onToggle, true);
    void hydrateMermaid(el);
    return () => el.removeEventListener("toggle", onToggle, true);
  }, [markdown]);

  useEffect(() => {
    const readSel = () => {
      const el = ref.current;
      if (!el) return;
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount < 1) return;
      const range = sel.getRangeAt(0);
      const raw = sel.toString() || range.toString();
      const text = raw.replace(/\s+/g, " ").trim();
      if (text.length < 4) return;
      if (!el.contains(sel.anchorNode) && !el.contains(sel.focusNode)) return;
      if (!el.contains(range.commonAncestorContainer)) return;
      const lines = linesFromRange(el, range);
      const pt = selectionPoint(range);
      onSelectRef.current({
        text,
        startLine: lines.startLine,
        endLine: lines.endLine,
        x: pt.x,
        y: pt.y,
      });
    };
    let later = 0;
    const onUp = (e: Event) => {
      const t = e.target;
      if (t instanceof Element) {
        if (t.closest(".pen, .dash, .rail, .view-switch, button, input, select, textarea, label")) {
          return;
        }
      }
      readSel();
      window.clearTimeout(later);
      later = window.setTimeout(readSel, 16);
    };
    document.addEventListener("pointerup", onUp);
    return () => {
      window.clearTimeout(later);
      document.removeEventListener("pointerup", onUp);
    };
  }, []);

  return <article ref={ref} className="paper-body" />;
}
