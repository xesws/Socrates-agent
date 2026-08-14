import { useCallback, useEffect, useRef, useState } from "react";
import { api, type LlmStatus } from "./api";
import { MarkdownView } from "./reader/MarkdownView";
import { ReportPanel } from "./diagnose/ReportPanel";
import { PenPanel } from "./pen/PenPanel";
import type { Chip, HandbookMeta, Section, SelectionAnchor, TocEntry } from "./types";

export function App() {
  const [books, setBooks] = useState<HandbookMeta[]>([]);
  const [current, setCurrent] = useState<HandbookMeta | null>(null);
  const [text, setText] = useState("");
  const [toc, setToc] = useState<TocEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [chips, setChips] = useState<Chip[]>([]);
  const [sel, setSel] = useState<SelectionAnchor | null>(null);
  const [section, setSection] = useState<Section | null>(null);
  const [llm, setLlm] = useState<LlmStatus | null>(null);
  const [penFloat, setPenFloat] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const selGen = useRef(0);

  const loadBook = useCallback(async (id: string) => {
    setLoading(true);
    setErr("");
    setSel(null);
    setReportOpen(false);
    try {
      const [meta, content, sess] = await Promise.all([
        api.handbook(id),
        api.content(id),
        api.createSession(id),
      ]);
      setCurrent(meta);
      setText(content.text);
      setToc(meta.toc || []);
      setSessionId(sess.session_id);
      setChips(sess.chips);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const [{ handbooks }, health] = await Promise.all([
          api.handbooks(),
          api.health(),
        ]);
        setLlm(health.llm);
        setBooks(handbooks);
        if (handbooks[0]) await loadBook(handbooks[0].handbook_id);
        else setLoading(false);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
        setLoading(false);
      }
    })();
  }, [loadBook]);

  const onPenFloat = useCallback(() => setPenFloat(true), []);

  const onSelect = useCallback(
    async (anchor: SelectionAnchor) => {
      const gen = ++selGen.current;
      setSel(anchor);
      setPenFloat(false);
      setReportOpen(false);
      setSection(null);
      if (!current) return;
      try {
        const sec = await api.locate(current.handbook_id, anchor.startLine);
        if (selGen.current === gen) setSection(sec);
      } catch {
        if (selGen.current === gen) setSection(null);
      }
    },
    [current],
  );

  return (
    <div
      className={
        reportOpen || (sel && !penFloat) ? "desk is-pen-open" : "desk"
      }
    >
      <aside className="rail">
        <p className="eyebrow">师傅的工作台</p>
        <h1>点读笔</h1>
        <p className="lede">框选原文，就地追问。写回改的是磁盘上那一份。</p>
        <label className="book-pick">
          手册
          <select
            value={current?.handbook_id || ""}
            onChange={(e) => void loadBook(e.target.value)}
          >
            {books.map((b) => (
              <option key={b.handbook_id} value={b.handbook_id}>
                {b.title}
              </option>
            ))}
          </select>
        </label>
        {current && (
          <p className="path" title={current.original_path}>
            {current.original_path}
          </p>
        )}
        <p className="path" title={llm?.base_url || ""}>
          {llm?.ok
            ? `模型 ${llm.model} · ${llm.key_source}`
            : "模型未配置（需要 DEEPSEEK_API_KEY 或 OPENAI_*）"}
        </p>
        <button
          className="diag-open"
          type="button"
          onClick={() => {
            setReportOpen(true);
            setSel(null);
            setPenFloat(false);
          }}
        >
          诊断 · 看我的短板
        </button>
        <nav className="toc">
          {toc.map((t) => (
            <button
              key={t.anchor_id}
              className={t.beat ? "toc-beat" : "toc-h1"}
              onClick={() => {
                const node = document.querySelector(
                  `[data-source-line="${t.start_line}"]`,
                );
                node?.scrollIntoView({ block: "start", behavior: "smooth" });
              }}
            >
              {t.beat ? t.heading : t.heading}
            </button>
          ))}
        </nav>
      </aside>

      <main className="stage">
        {err && <p className="banner">{err}</p>}
        {loading ? (
          <p className="banner">正在摊开手册…</p>
        ) : (
          <div className="paper">
            <div className="paper-rule" />
            <MarkdownView markdown={text} onSelect={onSelect} />
          </div>
        )}
      </main>

      {reportOpen && current && (
        <ReportPanel
          handbookId={current.handbook_id}
          onClose={() => setReportOpen(false)}
          onJump={(line) => {
            const node = document.querySelector(`[data-source-line="${line}"]`);
            node?.scrollIntoView({ block: "start", behavior: "smooth" });
          }}
        />
      )}

      {sel && sessionId && current && !reportOpen && (
        <PenPanel
          handbookId={current.handbook_id}
          sessionId={sessionId}
          chips={chips}
          anchor={sel}
          section={section}
          docked={!penFloat}
          onFloat={onPenFloat}
          onClose={() => {
            setSel(null);
            setPenFloat(false);
          }}
          onWrote={() => void loadBook(current.handbook_id)}
        />
      )}
    </div>
  );
}
