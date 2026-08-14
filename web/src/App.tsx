import { useCallback, useEffect, useRef, useState } from "react";
import { api, type LlmStatus } from "./api";
import { ReportPanel } from "./diagnose/ReportPanel";
import { MarkdownView } from "./reader/MarkdownView";
import { PenPanel } from "./pen/PenPanel";
import type { Chip, HandbookMeta, Section, SelectionAnchor, TocEntry } from "./types";

type View = "reader" | "diagnose";

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
  const [view, setView] = useState<View>("reader");
  const selGen = useRef(0);
  const viewRef = useRef<View>("reader");
  viewRef.current = view;

  const openDiagnose = useCallback(() => {
    window.getSelection()?.removeAllRanges();
    setSel(null);
    setSection(null);
    setPenFloat(false);
    setView("diagnose");
  }, []);

  const openReader = useCallback(() => {
    window.getSelection()?.removeAllRanges();
    setView("reader");
  }, []);

  const loadBook = useCallback(async (id: string) => {
    setLoading(true);
    setErr("");
    setSel(null);
    setView("reader");
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
      if (viewRef.current !== "reader") return;
      const gen = ++selGen.current;
      setSel(anchor);
      setPenFloat(false);
      setSection(null);
      if (!current) return;
      try {
        const sec = await api.locate(current.handbook_id, anchor.startLine);
        if (selGen.current === gen && viewRef.current === "reader") {
          setSection(sec);
        }
      } catch {
        if (selGen.current === gen) setSection(null);
      }
    },
    [current],
  );

  const jumpToLine = useCallback((line: number) => {
    setView("reader");
    setSel(null);
    window.getSelection()?.removeAllRanges();
    window.setTimeout(() => {
      const node = document.querySelector(`[data-source-line="${line}"]`);
      node?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 40);
  }, []);

  const diagnosing = view === "diagnose";

  return (
    <div
      className={[
        "desk",
        diagnosing ? "is-diagnose" : "",
        !diagnosing && sel && !penFloat ? "is-pen-open" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <aside className="rail">
        <p className="eyebrow">师傅的工作台</p>
        <h1>{diagnosing ? "诊断舱" : "点读笔"}</h1>
        <p className="lede">
          {diagnosing
            ? "看轨迹、看短板、看关键词。这里不是点读笔。"
            : "框选原文，就地追问。写回改的是磁盘上那一份。"}
        </p>
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
        <div className="view-switch">
          <button
            type="button"
            className={diagnosing ? "" : "is-on"}
            onClick={openReader}
          >
            手册
          </button>
          <button
            type="button"
            className={diagnosing ? "is-on" : ""}
            onClick={openDiagnose}
          >
            诊断
          </button>
        </div>
        {!diagnosing && (
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
                {t.heading}
              </button>
            ))}
          </nav>
        )}
      </aside>

      <main className="stage">
        {err && <p className="banner">{err}</p>}
        {loading ? (
          <p className="banner">正在摊开手册…</p>
        ) : diagnosing && current ? (
          <ReportPanel
            handbookId={current.handbook_id}
            onBack={openReader}
            onJump={jumpToLine}
          />
        ) : (
          <div className="paper">
            <div className="paper-rule" />
            <MarkdownView markdown={text} onSelect={onSelect} />
          </div>
        )}
      </main>

      {view === "reader" && sel && sessionId && current && (
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
            window.getSelection()?.removeAllRanges();
          }}
          onWrote={() => void loadBook(current.handbook_id)}
        />
      )}
    </div>
  );
}
