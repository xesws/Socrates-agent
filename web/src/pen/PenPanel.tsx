import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { api, streamChat } from "../api";
import type { ChatMessage, Chip, Proposal, Section, SelectionAnchor } from "../types";

type Props = {
  handbookId: string;
  sessionId: string;
  chips: Chip[];
  anchor: SelectionAnchor;
  section: Section | null;
  onClose: () => void;
  onWrote: () => void;
};

const OPACITY_KEY = "pen-opacity";

export function PenPanel({
  handbookId,
  sessionId,
  chips,
  anchor,
  section,
  onClose,
  onWrote,
}: Props) {
  const [opacity, setOpacity] = useState(() => {
    const n = Number(localStorage.getItem(OPACITY_KEY));
    return Number.isFinite(n) && n >= 0.45 && n <= 1 ? n : 0.92;
  });
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [msgs, setMsgs] = useState<ChatMessage[]>([]);
  const [dyn, setDyn] = useState<string[]>([]);
  const [usage, setUsage] = useState<string>("");
  const [err, setErr] = useState<string>("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [commit, setCommit] = useState(false);
  const [substantive, setSubstantive] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem(OPACITY_KEY, String(opacity));
  }, [opacity]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [msgs]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const liveChips = useMemo(() => {
    return chips.map((c) =>
      c.id === "writeback" ? { ...c, enabled: substantive } : c,
    );
  }, [chips, substantive]);

  const sourceLine = section
    ? `${section.level}${section.q_title ? " · " + section.q_title.replace(/^\*\*|\*\*$/g, "") : section.beat ? " · " + section.beat : ""}`
    : `L${anchor.startLine}`;

  async function send(chip: string, userText: string) {
    if (chip === "search") return;
    if (chip === "writeback") {
      await doPropose();
      return;
    }
    setBusy(true);
    setErr("");
    const shown =
      userText.trim() || liveChips.find((c) => c.id === chip)?.label || chip;
    setMsgs((m) => [...m, { role: "user", text: shown }]);
    let acc = "";
    setMsgs((m) => [...m, { role: "assistant", text: "" }]);
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
          if (ev.type === "token") {
            acc += String(ev.text || "");
            const snapshot = acc;
            setMsgs((m) => {
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
            const ok = Boolean(ev.ok);
            const path = String(ev.resolved || ev.detail || "");
            const preview = String(ev.preview || "");
            const row: ChatMessage = {
              role: "tool",
              ok,
              text: `read_file ${ok ? "成功" : "拒绝"} → ${path}${preview ? " · " + preview.replace(/\s+/g, " ").slice(0, 80) : ""}`,
            };
            setMsgs((m) => {
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
            const u = ev.usage as { prompt_tokens?: number; completion_tokens?: number };
            setUsage(
              `prompt ${u?.prompt_tokens ?? "?"} · completion ${u?.completion_tokens ?? "?"}`,
            );
            setDyn((ev.dynamic_chips as string[]) || []);
            setSubstantive(Boolean(ev.has_substantive));
          } else if (ev.type === "error") {
            setErr(String(ev.message));
          }
        },
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setInput("");
    }
  }

  async function doPropose() {
    setBusy(true);
    setErr("");
    try {
      setProposal(await api.propose(sessionId));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doApply() {
    if (!proposal) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.apply(sessionId, proposal.proposal_id, commit);
      setProposal(null);
      onWrote();
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          text: `已写入原文 ${r.original_path}${r.commit ? "，并已 commit。" : "。未 commit。"}`,
        },
      ]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doRollback() {
    setBusy(true);
    try {
      const r = await api.rollback(handbookId);
      onWrote();
      setMsgs((m) => [
        ...m,
        { role: "assistant", text: `已用快照覆盖回原文 ${r.original_path}` },
      ]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const style: CSSProperties = (() => {
    const width = 400;
    const estH = 420;
    const left = Math.min(
      Math.max(anchor.x - width / 2, 16),
      window.innerWidth - width - 16,
    );
    const below = anchor.y + 12;
    const top =
      below + estH > window.innerHeight - 16
        ? Math.max(16, anchor.y - estH - 8)
        : below;
    return {
      left,
      top,
      opacity,
      maxHeight: Math.min(640, window.innerHeight - 32),
    };
  })();

  return (
    <aside className="pen" style={style} role="dialog" aria-label="点读笔">
      <header className="pen-bar">
        <span className="pen-mark" />
        <div className="pen-meta">
          <strong>点读笔</strong>
          <em>{sourceLine}</em>
        </div>
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
        <button className="ghost" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </header>

      <blockquote className="pen-quote">{anchor.text}</blockquote>

      <div className="pen-log" ref={logRef}>
        {msgs.length === 0 && (
          <p className="pen-hint">先选一条芯片。默认是苏格拉底：我先问你。</p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.text}
          </div>
        ))}
      </div>

      {err && <p className="pen-err">{err}</p>}
      {usage && <p className="pen-usage">{usage}</p>}

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
              onChange={(e) => setCommit(e.target.checked)}
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
