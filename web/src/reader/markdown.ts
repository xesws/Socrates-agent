import MarkdownIt from "markdown-it";
import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import plaintext from "highlight.js/lib/languages/plaintext";

hljs.registerLanguage("python", python);
hljs.registerLanguage("py", python);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("sh", bash);
hljs.registerLanguage("shell", bash);
hljs.registerLanguage("json", json);
hljs.registerLanguage("text", plaintext);

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const md: MarkdownIt = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: false,
  highlight(str: string, lang: string): string {
    if (lang === "mermaid") {
      return `<pre class="mermaid-hold"><code>${escapeHtml(str)}</code></pre>`;
    }
    const key = lang && hljs.getLanguage(lang) ? lang : "";
    const html = key
      ? hljs.highlight(str, { language: key }).value
      : escapeHtml(str);
    return `<pre class="hljs"><code>${html}</code></pre>`;
  },
});

md.core.ruler.push("source-line", (state) => {
  for (const token of state.tokens) {
    if (token.map && token.nesting !== -1) {
      token.attrSet("data-source-line", String(token.map[0] + 1));
      token.attrSet("data-source-end", String(token.map[1]));
    }
  }
});

export function renderMarkdown(src: string): string {
  return md.render(src);
}

export async function hydrateMermaid(root: HTMLElement): Promise<void> {
  const holds = [...root.querySelectorAll<HTMLElement>("pre.mermaid-hold")];
  if (!holds.length) return;
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize({
    startOnLoad: false,
    theme: "neutral",
    securityLevel: "strict",
  });
  let i = 0;
  for (const hold of holds) {
    const details = hold.closest("details");
    if (details && !details.open) continue;
    const code = hold.textContent || "";
    const host = document.createElement("div");
    host.className = "mermaid-drawn";
    host.id = `mmd-${Date.now()}-${i++}`;
    hold.replaceWith(host);
    try {
      const { svg } = await mermaid.render(host.id + "-svg", code);
      host.innerHTML = svg;
    } catch (err) {
      host.textContent = String(err);
    }
  }
}
