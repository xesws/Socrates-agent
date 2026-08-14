import { useEffect, useRef } from "react";
import { hydrateMermaid, renderMarkdown } from "./markdown";
import type { SelectionAnchor } from "../types";

type Props = {
  markdown: string;
  onSelect: (anchor: SelectionAnchor) => void;
};

export function MarkdownView({ markdown, onSelect }: Props) {
  const ref = useRef<HTMLElement>(null);

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
    const el = ref.current;
    if (!el) return;
    const onUp = () => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) return;
      const text = sel.toString().replace(/\s+/g, " ").trim();
      if (text.length < 4) return;
      if (!el.contains(sel.anchorNode)) return;
      const node =
        sel.anchorNode instanceof Element
          ? sel.anchorNode
          : sel.anchorNode?.parentElement;
      const stamped = node?.closest("[data-source-line]") as HTMLElement | null;
      const start = Number(stamped?.dataset.sourceLine || "1");
      const end = Number(stamped?.dataset.sourceEnd || start);
      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      onSelect({
        text,
        startLine: start,
        endLine: Math.max(start, end),
        x: rect.left + rect.width / 2,
        y: rect.bottom,
      });
    };
    el.addEventListener("mouseup", onUp);
    return () => el.removeEventListener("mouseup", onUp);
  }, [onSelect]);

  return <article ref={ref} className="paper-body" />;
}
