import { useEffect, useState } from "react";
import { api } from "../api";
import type { DiagnosisReport, DiagnosisSpot } from "../types";

type Props = {
  handbookId: string;
  onBack: () => void;
  onJump?: (line: number) => void;
};

function SpotCard({
  spot,
  tone,
  onJump,
}: {
  spot: DiagnosisSpot;
  tone: "weak" | "foot";
  onJump?: (line: number) => void;
}) {
  return (
    <article className={`dash-card dash-card-${tone}`}>
      <header className="dash-card-head">
        <span className="dash-pct">{spot.pct.toFixed(0)}%</span>
        <div className="dash-card-meta">
          <em>{spot.level}</em>
          <h4>{spot.label}</h4>
        </div>
        <span className="dash-hits">问过 {spot.hits} 次</span>
      </header>
      {(spot.keyword_src?.length ? spot.keyword_src : spot.keywords.map((token) => ({ token, src: "" }))).length >
        0 && (
        <ul className="dash-keys">
          {(spot.keyword_src?.length
            ? spot.keyword_src
            : spot.keywords.map((token) => ({ token, src: "" }))
          ).map((k) => (
            <li
              key={k.token}
              title={
                k.src === "title"
                  ? "来自题干"
                  : k.src === "selected"
                    ? "来自框选"
                    : k.src === "user"
                      ? "来自追问"
                      : undefined
              }
            >
              {k.token}
            </li>
          ))}
        </ul>
      )}
      {spot.start_line ? (
        <button
          type="button"
          className="dash-jump"
          onClick={() => onJump?.(spot.start_line as number)}
        >
          回手册第 {spot.start_line} 行
        </button>
      ) : null}
    </article>
  );
}

export function ReportPanel({ handbookId, onBack, onJump }: Props) {
  const [report, setReport] = useState<DiagnosisReport | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  const [narrative, setNarrative] = useState("");
  const [narrating, setNarrating] = useState(false);

  useEffect(() => {
    let live = true;
    setBusy(true);
    setErr("");
    setNarrative("");
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
      if (e.key === "Escape") onBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

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
    <section className="dash" aria-label="诊断仪表盘">
      <header className="dash-top">
        <div>
          <p className="dash-kicker">独立舱 · 不经过苏格拉底</p>
          <h2>短板诊断</h2>
          <p className="dash-lede">
            按手册门禁题统计你问过的轨迹。只存在这台机器的 .pen
            里，不写回原文。
          </p>
        </div>
        <div className="dash-top-actions">
          <button
            type="button"
            className="dash-narrate"
            disabled={narrating || !report || report.n_curriculum === 0}
            onClick={() => void tell()}
          >
            {narrating ? "苏格拉底在看…" : "写成苏格拉底的评语"}
          </button>
          <button type="button" className="dash-back" onClick={onBack}>
            回手册
          </button>
        </div>
      </header>

      {busy && <p className="dash-empty">正在翻轨迹…</p>}
      {err && <p className="dash-err">{err}</p>}

      {report && !busy && (
        <>
          <p className="dash-sum">
            有效提问 <strong>{report.n_curriculum}</strong> 次 · 总轨迹{" "}
            <strong>{report.n_turns}</strong> 条
          </p>

          {report.levels.length > 0 && (
            <section className="dash-block">
              <h3>关卡分布</h3>
              <ul className="dash-levels">
                {report.levels.map((lv) => (
                  <li key={lv.level}>
                    <span>{lv.level}</span>
                    <span className="dash-bar">
                      <i style={{ width: `${Math.min(100, lv.pct)}%` }} />
                    </span>
                    <strong>{lv.pct.toFixed(0)}%</strong>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {narrative && <aside className="dash-note">{narrative}</aside>}

          <div className="dash-split">
            <section className="dash-block">
              <h3>短板</h3>
              <p className="dash-hint">
                同一道 Q 问过至少两次才算。关键词是题干和追问里冒出来的。
              </p>
              {report.weak.length === 0 ? (
                <p className="dash-empty">还没有短板。多问几次同一处才会出现。</p>
              ) : (
                report.weak.map((s) => (
                  <SpotCard key={s.key} spot={s} tone="weak" onJump={onJump} />
                ))
              )}
            </section>
            <section className="dash-block">
              <h3>足迹</h3>
              <p className="dash-hint">只点过一次，先当好奇，不判弱。</p>
              {report.footprints.length === 0 ? (
                <p className="dash-empty">还没有单次足迹。</p>
              ) : (
                report.footprints.map((s) => (
                  <SpotCard key={s.key} spot={s} tone="foot" onJump={onJump} />
                ))
              )}
            </section>
          </div>
        </>
      )}
    </section>
  );
}
