import { useEffect, useState } from "react";
import { api } from "../api";
import type { DiagnosisReport, DiagnosisSpot } from "../types";

type Props = {
  handbookId: string;
  onClose: () => void;
  onJump?: (line: number) => void;
};

function SpotList({
  title,
  hint,
  spots,
  tone,
  onJump,
}: {
  title: string;
  hint: string;
  spots: DiagnosisSpot[];
  tone: "weak" | "foot";
  onJump?: (line: number) => void;
}) {
  if (spots.length === 0) {
    return (
      <section className="diag-block">
        <h3>{title}</h3>
        <p className="pen-hint">{hint}</p>
      </section>
    );
  }
  return (
    <section className="diag-block">
      <h3>{title}</h3>
      <p className="pen-hint">{hint}</p>
      <ul className="diag-spots">
        {spots.map((s) => (
          <li key={s.key} className={`diag-spot diag-spot-${tone}`}>
            <button
              className="diag-spot-head"
              type="button"
              onClick={() => {
                if (s.start_line) onJump?.(s.start_line);
              }}
            >
              <span className="diag-pct">{s.pct.toFixed(0)}%</span>
              <span className="diag-label">
                <em>{s.level}</em>
                {s.label}
              </span>
              <span className="diag-hits">×{s.hits}</span>
            </button>
            {s.keywords.length > 0 && (
              <div className="chips">
                {s.keywords.map((k) => (
                  <span key={k} className="chip chip-dyn">
                    {k}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ReportPanel({ handbookId, onClose, onJump }: Props) {
  const [report, setReport] = useState<DiagnosisReport | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  const [narrative, setNarrative] = useState("");
  const [narrating, setNarrating] = useState(false);

  useEffect(() => {
    let live = true;
    setBusy(true);
    setErr("");
    void (async () => {
      try {
        const r = await api.diagnosis(handbookId);
        if (live) setReport(r);
      } catch (e) {
        if (live) setErr(e instanceof Error ? e.message : String(e));
      } finally {
        if (live) setBusy(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [handbookId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function tell() {
    setNarrating(true);
    setErr("");
    try {
      const r = await api.narrateDiagnosis(handbookId);
      setNarrative(r.narrative);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setNarrating(false);
    }
  }

  return (
    <aside className="pen is-docked report" role="dialog" aria-label="诊断报告">
      <header className="pen-bar">
        <span className="pen-mark" />
        <div className="pen-meta">
          <strong>诊断</strong>
          <em>只存在这台机器的 .pen 里，不写回手册</em>
        </div>
        <button className="ghost pen-close" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </header>

      <div className="pen-log">
        {busy && <p className="pen-hint">正在翻轨迹…</p>}
        {err && <p className="pen-err">{err}</p>}
        {report && !busy && (
          <>
            <p className="diag-sum">
              有效提问 {report.n_curriculum} 次 · 总轨迹 {report.n_turns} 条
            </p>
            {report.levels.length > 0 && (
              <section className="diag-block">
                <h3>关卡分布</h3>
                <ul className="diag-levels">
                  {report.levels.map((lv) => (
                    <li key={lv.level}>
                      <span>{lv.level}</span>
                      <span className="diag-bar">
                        <i style={{ width: `${Math.min(100, lv.pct)}%` }} />
                      </span>
                      <strong>{lv.pct.toFixed(0)}%</strong>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <SpotList
              title="短板"
              hint="同一道 Q 问过至少两次，才算短板。问一次只记足迹。"
              spots={report.weak}
              tone="weak"
              onJump={onJump}
            />
            <SpotList
              title="足迹"
              hint="只点过一次的地方。可能是好奇，先不判弱。"
              spots={report.footprints}
              tone="foot"
              onJump={onJump}
            />
            {narrative && <blockquote className="pen-quote">{narrative}</blockquote>}
          </>
        )}
      </div>

      <footer className="pen-foot">
        <button
          className="ghost"
          disabled={narrating || !report || report.n_curriculum === 0}
          onClick={() => void tell()}
        >
          {narrating ? "师傅在看…" : "写成师傅评语"}
        </button>
      </footer>
    </aside>
  );
}
