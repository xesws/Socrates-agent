export type LineSpan = {
  startLine: number;
  endLine: number;
};

export type ViewportPoint = {
  x: number;
  y: number;
};

function parseStamp(el: Element): LineSpan | null {
  const start = Number((el as HTMLElement).dataset.sourceLine);
  if (!Number.isFinite(start) || start < 1) return null;
  const rawEnd = Number((el as HTMLElement).dataset.sourceEnd);
  const end = Number.isFinite(rawEnd) && rawEnd >= start ? rawEnd : start;
  return { startLine: start, endLine: end };
}

/** Walk from a text node / element up to the nearest stamped block. */
export function nearestStamped(
  root: HTMLElement,
  from: Node | null,
): HTMLElement | null {
  let node: Node | null = from;
  while (node && node !== root) {
    if (node instanceof HTMLElement) {
      if (node.hasAttribute("data-source-line") && parseStamp(node)) {
        return node;
      }
      const close = node.closest("[data-source-line]");
      if (
        close instanceof HTMLElement &&
        root.contains(close) &&
        parseStamp(close)
      ) {
        return close;
      }
    }
    node = node.parentNode;
  }
  return null;
}

/**
 * If the selection sits in an unstamped hole (highlight.js fence, mermaid
 * swap, raw HTML), pick the last stamped block whose top is still above the
 * range. Never silently invent line 1 — that maps to 封面.
 */
export function stampedNearRect(
  root: HTMLElement,
  rect: DOMRect,
): HTMLElement | null {
  const stamps = root.querySelectorAll<HTMLElement>("[data-source-line]");
  let best: HTMLElement | null = null;
  let bestTop = -Infinity;
  for (const el of stamps) {
    if (!parseStamp(el)) continue;
    const top = el.getBoundingClientRect().top;
    if (top <= rect.top + 4 && top >= bestTop) {
      best = el;
      bestTop = top;
    }
  }
  return best;
}

export function linesFromRange(root: HTMLElement, range: Range): LineSpan {
  const startEl = nearestStamped(root, range.startContainer);
  const endEl = nearestStamped(root, range.endContainer);
  const a = startEl ? parseStamp(startEl) : null;
  const b = endEl ? parseStamp(endEl) : null;
  if (a && b) {
    return {
      startLine: Math.min(a.startLine, b.startLine),
      endLine: Math.max(a.endLine, b.endLine),
    };
  }
  if (a) return a;
  if (b) return b;
  const near = stampedNearRect(root, range.getBoundingClientRect());
  const parsed = near ? parseStamp(near) : null;
  if (parsed) return parsed;
  return { startLine: 1, endLine: 1 };
}

export function selectionPoint(range: Range): ViewportPoint {
  const rects = [...range.getClientRects()].filter(
    (r) => r.width > 0 || r.height > 0,
  );
  const r = rects[rects.length - 1] ?? range.getBoundingClientRect();
  if (!r || (r.width === 0 && r.height === 0 && r.top === 0 && r.left === 0)) {
    return { x: Math.round(window.innerWidth / 2), y: 80 };
  }
  return { x: r.left + r.width / 2, y: r.bottom };
}
