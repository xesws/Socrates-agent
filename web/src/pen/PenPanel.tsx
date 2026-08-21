import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type PointerEvent as ReactPointerEvent,
  type SetStateAction,
} from "react";
import { api, streamChat } from "../api";
import { hydrateMermaid, renderMarkdown } from "../reader/markdown";
import type { ChatMessage, Chip, Proposal, Section, SelectionAnchor } from "../types";

function visibleReply(text: string): string {
  return text.replace(/<!--pen:chips[\s\S]*?-->/g, "").trim();
}

function MdBubble({ role, text }: { role: ChatMessage["role"]; text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const allowHtml = role === "assistant";
  const src = role === "assistant" ? visibleReply(text) : text;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.innerHTML = renderMarkdown(src, { html: allowHtml });
    const onToggle = (ev: Event) => {
      const t = ev.target;
      if (t instanceof HTMLDetailsElement && t.open) {
        void hydrateMermaid(t);
      }
    };
    el.addEventListener("toggle", onToggle, true);
    const later = window.setTimeout(() => {
      void hydrateMermaid(el);
    }, 200);
    return () => {
      window.clearTimeout(later);
      el.removeEventListener("toggle", onToggle, true);
    };
  }, [src, allowHtml]);
  return <div ref={ref} className={`bubble ${role}`} />;
}

type Props = {
  handbookId: string;
  sessionId: string;
  chips: Chip[];
  anchor: SelectionAnchor;
  section: Section | null;
  docked: boolean;
  onFloat: () => void;
  onClose: () => void;
  onWrote: () => void;
  onNewSession: () => void;
  msgs: ChatMessage[];
  onMsgs: Dispatch<SetStateAction<ChatMessage[]>>;
  substantive: boolean;
  onSubstantive: (v: boolean) => void;
};

function excerpt(text: string, n = 160): string {
  const one = text.replace(/\s+/g, " ").trim();
  return one.length <= n ? one : `${one.slice(0, n - 1)}…`;
}

const OPACITY_KEY = "pen-opacity";
const MARGIN = 12;
const DEFAULT_H = 420;

type Pos = { left: number; top: number };

function panelWidth(vw = window.innerWidth): number {
  return Math.min(400, Math.max(200, vw - MARGIN * 2));
}

function clampPos(left: number, top: number, w: number, h: number): Pos {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const maxLeft = Math.max(MARGIN, vw - w - MARGIN);
  const maxTop = Math.max(MARGIN, vh - Math.min(h, vh - MARGIN * 2) - MARGIN);
  return {
    left: Math.min(Math.max(MARGIN, left), maxLeft),
    top: Math.min(Math.max(MARGIN, top), maxTop),
  };
}

function placeFromAnchor(anchor: SelectionAnchor, w: number, h: number): Pos {
  const left = anchor.x - w / 2;
  const below = anchor.y + 10;
  const top =
    below + h > window.innerHeight - MARGIN ? anchor.y - h - 10 : below;
  return clampPos(left, top, w, h);
}

function formatSection(section: Section): string {
  const q = section.q_title?.replace(/^\*\*|\*\*$/g, "") ?? "";
  if (q) return `${section.level} · ${q}`;
  if (section.beat) return `${section.level} · ${section.beat}`;
  return section.level;
}

export function PenPanel({
  handbookId,
  sessionId,
  chips,
  anchor,
  section,
  docked,
  onFloat,
  onClose,
  onWrote,
  onNewSession,
  msgs,
  onMsgs,
  substantive,
  onSubstantive,
}: Props) {
  const [opacity, setOpacity] = useState(() => {
    const n = Number(localStorage.getItem(OPACITY_KEY));
    return Number.isFinite(n) && n >= 0.45 && n <= 1 ? n : 0.92;
  });
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [dyn, setDyn] = useState<string[]>([]);
  const [usage, setUsage] = useState<string>("");
  const [status, setStatus] = useState("");
  const [err, setErr] = useState<string>("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [commit, setCommit] = useState(false);
  const [pos, setPos] = useState<Pos>(() =>
    placeFromAnchor(anchor, panelWidth(), DEFAULT_H),
  );
  const [dragging, setDragging] = useState(false);
  const [vw, setVw] = useState(() => window.innerWidth);

  const logRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const userMoved = useRef(false);
  const dragRef = useRef<{
    id: number;
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);
  const selKey = `${anchor.startLine}:${anchor.endLine}:${anchor.text}:${anchor.x}:${anchor.y}`;

  useEffect(() => {
    localStorage.setItem(OPACITY_KEY, String(opacity));
  }, [opacity]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [msgs]);

  useEffect(() => {
    setInput("");
    setDyn([]);
    setUsage("");
    setStatus("");
    setErr("");
    setProposal(null);
  }, [sessionId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    userMoved.current = false;
    setPos(placeFromAnchor(anchor, panelWidth(), DEFAULT_H));
  }, [selKey, anchor]);

  useEffect(() => {
    const onResize = () => {
      setVw(window.innerWidth);
      const el = panelRef.current;
      const w = el?.offsetWidth ?? panelWidth();
      const h = el?.offsetHeight ?? DEFAULT_H;
      setPos((p) => clampPos(p.left, p.top, w, h));
    };
    const applyDrag = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d || d.id !== e.pointerId) return;
      if (Math.abs(e.clientX - d.x) < 6 && Math.abs(e.clientY - d.y) < 6) return;
      const el = panelRef.current;
      const w = el?.offsetWidth ?? panelWidth();
      const h = el?.offsetHeight ?? DEFAULT_H;
      userMoved.current = true;
      setDragging(true);
      if (docked) onFloat();
      setPos(clampPos(d.left + e.clientX - d.x, d.top + e.clientY - d.y, w, h));
    };
    const endDrag = (e: PointerEvent) => {
      if (dragRef.current?.id === e.pointerId) {
        dragRef.current = null;
        setDragging(false);
      }
    };
    window.addEventListener("resize", onResize);
    window.visualViewport?.addEventListener("resize", onResize);
    window.addEventListener("pointermove", applyDrag);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    return () => {
      window.removeEventListener("resize", onResize);
      window.visualViewport?.removeEventListener("resize", onResize);
      window.removeEventListener("pointermove", applyDrag);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
    };
  }, [docked, onFloat]);

  useEffect(() => {
    const el = panelRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      setPos((p) => {
        const next = userMoved.current
          ? clampPos(p.left, p.top, w, h)
          : placeFromAnchor(anchor, w, h);
        return p.left === next.left && p.top === next.top ? p : next;
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [anchor]);

  const liveChips = useMemo(() => {
    return chips.map((c) =>
      c.id === "writeback" ? { ...c, enabled: substantive } : c,
    );
  }, [chips, substantive]);

  const sourceLine = section
    ? formatSection(section)
    : `L${anchor.startLine} · 定位中`;

  async function send(chip: string, userText: string) {
    if (chip === "search") return;
    if (chip === "writeback") {
      await doPropose();
      return;
    }
    setBusy(true);
    setErr("");
    setUsage("");
    setStatus("苏格拉底在想…");
    const shown =
      userText.trim() || liveChips.find((c) => c.id === chip)?.label || chip;
    onMsgs((m) => [...m, { role: "user", text: shown }]);
    let acc = "";
    onMsgs((m) => [...m, { role: "assistant", text: "" }]);
    try {
      await streamChat(
        {
          session_id: sessionId,
          selected_text: anchor.text,
          start_line: anchor.startLine,
          end_line: anchor.endLine,
          chip,
          user_text: userText,
        },
        (ev) => {
          if (ev.type === "status") {
            setStatus(String(ev.text || ""));
          } else if (ev.type === "token") {
            setStatus("在写…");
            acc += String(ev.text || "");
            const snapshot = acc;
            onMsgs((m) => {
              const copy = m.slice();
              for (let i = copy.length - 1; i >= 0; i--) {
                if (copy[i].role === "assistant") {
                  copy[i] = { role: "assistant", text: snapshot };
                  break;
                }
              }
              return copy;
            });
          } else if (ev.type === "tool") {
            setStatus("在翻手册…");
            const ok = Boolean(ev.ok);
            const path = String(ev.resolved || ev.detail || "");
            const preview = String(ev.preview || "");
            const row: ChatMessage = {
              role: "tool",
              ok,
              text: `read_file ${ok ? "成功" : "拒绝"} → ${path}${preview ? " · " + preview.replace(/\s+/g, " ").slice(0, 80) : ""}`,
            };
            onMsgs((m) => {
              const copy = m.slice();
              const last = copy.length - 1;
              if (last >= 0 && copy[last].role === "assistant") {
                copy.splice(last, 0, row);
              } else {
                copy.push(row);
              }
              return copy;
            });
          } else if (ev.type === "done") {
            setStatus("");
            const u = ev.usage as {
              context_tokens?: number;
              prompt_tokens?: number;
              completion_tokens?: number;
            };
            const ctx = u?.context_tokens ?? u?.prompt_tokens;
            const out = u?.completion_tokens;
            const fmt = (n: number | undefined) =>
              typeof n === "number" ? n.toLocaleString("zh-CN") : "?";
            setUsage(`上下文 ${fmt(ctx)} · 回复 ${fmt(out)}`);
            setDyn((ev.dynamic_chips as string[]) || []);
            onSubstantive(Boolean(ev.has_substantive));
          } else if (ev.type === "error") {
            setStatus("");
            setErr(String(ev.message));
          }
        },
      );
    } catch (e) {
      setStatus("");
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setStatus("");
      setInput("");
    }
  }

  async function doPropose() {
    setBusy(true);
    setErr("");
    setStatus("在收折叠块…");
    try {
      setProposal(await api.propose(sessionId));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  async function doApply() {
    if (!proposal) return;
    setBusy(true);
    setErr("");
    setStatus("在写入…");
    try {
      const r = await api.apply(sessionId, proposal.proposal_id, commit);
      setProposal(null);
      onWrote();
      const commitNote = r.commit
        ? "，并已 commit。"
        : r.commit_error
          ? `。原文已写入，但 commit 失败：${r.commit_error}`
          : "。未 commit。";
      onMsgs((m) => [
        ...m,
        {
          role: "assistant",
          text: `已写入原文 ${r.original_path}${commitNote}`,
        },
      ]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  async function doRollback() {
    setBusy(true);
    setStatus("在回滚…");
    try {
      const r = await api.rollback(handbookId);
      onWrote();
      onMsgs((m) => [
        ...m,
        { role: "assistant", text: `已用快照覆盖回原文 ${r.original_path}` },
      ]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  function onBarPointerDown(e: ReactPointerEvent<HTMLElement>) {
    if (e.button !== 0) return;
    const t = e.target as HTMLElement;
    if (t.closest("button, input, label, a")) return;
    const el = panelRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    dragRef.current = {
      id: e.pointerId,
      x: e.clientX,
      y: e.clientY,
      left: r.left,
      top: r.top,
    };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic events and lost pointers have no capture target */
    }
    e.preventDefault();
  }

  function onBarPointerMove(e: ReactPointerEvent<HTMLElement>) {
    const d = dragRef.current;
    if (!d || d.id !== e.pointerId) return;
    const el = panelRef.current;
    const w = el?.offsetWidth ?? panelWidth(vw);
    const h = el?.offsetHeight ?? DEFAULT_H;
    if (Math.abs(e.clientX - d.x) < 6 && Math.abs(e.clientY - d.y) < 6) return;
    userMoved.current = true;
    setDragging(true);
    if (docked) onFloat();
    setPos(clampPos(d.left + e.clientX - d.x, d.top + e.clientY - d.y, w, h));
  }

  function onBarPointerUp(e: ReactPointerEvent<HTMLElement>) {
    if (dragRef.current?.id === e.pointerId) {
      dragRef.current = null;
      setDragging(false);
    }
  }

  const width = panelWidth(vw);
  const style: CSSProperties = docked
    ? { ["--pen-alpha" as string]: String(opacity) }
    : {
        left: pos.left,
        top: pos.top,
        width,
        maxHeight: window.innerHeight - MARGIN * 2,
        ["--pen-alpha" as string]: String(opacity),
      };
  const cls = [
    "pen",
    docked ? "is-docked" : "is-float",
    dragging ? "is-dragging" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <aside ref={panelRef} className={cls} style={style} role="dialog" aria-label="苏格拉底">
      <header
        className="pen-bar"
        onPointerDown={onBarPointerDown}
        onPointerMove={onBarPointerMove}
        onPointerUp={onBarPointerUp}
        onPointerCancel={onBarPointerUp}
      >
        <span className={`pen-mark${busy ? " is-busy" : ""}`} />
        <div className="pen-meta">
          <strong>苏格拉底</strong>
          <em title={sourceLine}>{sourceLine}</em>
        </div>
        <button className="ghost pen-close" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </header>

      <blockquote className="pen-quote" title={anchor.text}>
        {excerpt(anchor.text)}
      </blockquote>

      <div className="pen-log" ref={logRef}>
        {msgs.length === 0 && (
          <p className="pen-hint">先选一条芯片。默认是苏格拉底：我先问你。</p>
        )}
        {msgs.map((m, i) => (
          <MdBubble key={i} role={m.role} text={m.text} />
        ))}
      </div>

      {err && <p className="pen-err">{err}</p>}
      {busy && status && <p className="pen-status">{status}</p>}
      {!busy && usage && <p className="pen-usage">{usage}</p>}

      <div className="chips">
        {liveChips.map((c) => (
          <button
            key={c.id}
            className={`chip ${c.id === "socratic" ? "chip-default" : ""}`}
            disabled={!c.enabled || busy}
            title={c.hint}
            onClick={() => void send(c.id, "")}
          >
            {c.label}
          </button>
        ))}
        {dyn.map((d) => (
          <button
            key={d}
            className="chip chip-dyn"
            disabled={busy}
            onClick={() => void send("free", d)}
          >
            {d}
          </button>
        ))}
      </div>

      <form
        className="pen-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!input.trim() || busy) return;
          void send("free", input.trim());
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="自己问一句…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          问
        </button>
      </form>

      <footer className="pen-foot">
        <label className="pen-opacity">
          透明度
          <input
            type="range"
            min={0.45}
            max={1}
            step={0.01}
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
          />
        </label>
        <button
          type="button"
          className="ghost"
          disabled={busy}
          onClick={onNewSession}
          title="换一个 session_id，上一场不再进入模型上下文"
        >
          新开会话
        </button>
        <button className="ghost" disabled={busy} onClick={() => void doRollback()}>
          撤销上次写回
        </button>
      </footer>

      {proposal && (
        <div className="preview">
          <h3>将写入原文</h3>
          <p className="pen-usage">{proposal.original_path}</p>
          <pre className="diff">{proposal.diff}</pre>
          <label className="commit-opt">
            <input
              type="checkbox"
              checked={commit}
              onChange={(e) => setCommit(e.currentTarget.checked)}
            />
            一并 git commit 这一份原文（不 push）
          </label>
          <div className="preview-actions">
            <button disabled={busy} onClick={() => void doApply()}>
              确认写入原文
            </button>
            <button className="ghost" onClick={() => setProposal(null)}>
              取消
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
