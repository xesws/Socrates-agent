"""真跑一次深挖，看问题质量。**会花钱**，所以不叫 test_ 开头，pytest 不会收。

用法：
    python3 -m pen.tests.live_probe_eval --dry-run     # 只打印 prompt 和 token，不发请求
    python3 -m pen.tests.live_probe_eval --live --n 2  # 真跑 n 个锚点

判据（跟 docs/v0.8.0 里那份人工标注一致）：好问题一定把当前这段和「第二样东西」
接上；坏问题只在框选这一段内部打转。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pen import diagnose, libraries, probe, trajectory
from pen.config import resolve_llm
from pen.probe import ProbeJob

# 覆盖不同 Level 和不同 kind 的落点
SPOTS = [
    ("开篇", "全景图：七块积木怎么拼成一个 Agent"),
    ("Level 0", "第三拍 · 出身：真实框架里的 Bash 长什么样"),
    ("Level 2", "第三拍 · 出身：API、OpenAI 兼容协议与 messages"),
    ("Level 5", "第三拍 · 出身：Agent SDK 的 permission 体系"),
    ("Level 6", "第三拍 · 出身：Claude Code 的四档权限"),
    ("Level 3", "第三拍 · 出身：mini-swe-agent 的约 100 行核心"),
]

FAKE_REPLY = (
    "这一段讲的是我们怎么把真实框架的做法搬过来。手册里写的那套顺序不是随便定的，"
    "每一步都对应一个具体的失败场景。你先别急着往下读，回头看一眼前面那一拍立的规矩，"
    "再看这里的实现，就知道它为什么长这样了。真要动手改，先想清楚改完之后哪条不变量会塌。"
)


def _job(hid: str, level: str, beat: str, cfg) -> ProbeJob | None:
    idx = libraries.load_index(hid)
    meta = libraries.get(hid)
    hit = next((t for t in idx.toc if t.level == level and t.beat == beat), None)
    if hit is None or meta is None:
        return None
    sec = idx.locate(hit.start_line + 2)
    anchor = {
        "path": meta.original_path, "level": sec.level, "beat": sec.beat,
        "q_title": sec.q_title, "kind": sec.kind,
        "start_line": hit.start_line + 2, "end_line": hit.start_line + 6,
        "selected_text": "（框选了这一拍开头的几行）",
    }
    turns = trajectory.load_turns(hid)
    rep = diagnose.aggregate(turns)
    rows = []
    recent = [diagnose.label_of(t.get("anchor") or {}) for t in turns[-12:]]
    recent = [r for r in recent if r]
    if recent:
        rows.append("最近读过：" + " / ".join(dict.fromkeys(recent)))
    weak = [w.get("label") for w in (rep.get("weak") or [])[:5] if w.get("label")]
    if weak:
        rows.append("反复回到的地方：" + " / ".join(weak))
    return ProbeJob(
        session_id="live", handbook_id=hid, original_path=Path(meta.original_path),
        anchor=anchor, atom=diagnose.atom_key(anchor), chip="socratic",
        user_text="这一拍到底想让我记住什么？", reply=FAKE_REPLY,
        born_round=1, lang="zh", cfg=cfg, footprint="\n".join(rows),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handbook", default="swe-agent-v2")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = resolve_llm()
    if args.live and cfg is None:
        print("没有 LLM 配置")
        return 1
    idx = libraries.load_index(args.handbook)

    for level, beat in SPOTS[: args.n]:
        job = _job(args.handbook, level, beat, cfg)
        if job is None:
            print(f"跳过 {level} / {beat}：索引里找不到")
            continue
        print("=" * 72)
        print(f"落点：{level} / {beat}")
        if args.dry_run:
            sysmsg = probe.build_system(idx, "zh")
            usr = probe.build_user_message(job)
            print(f"  system {len(sysmsg)} 字符 ≈ {len(sysmsg)//2} tok")
            print(f"  user   {len(usr)} 字符 ≈ {len(usr)//2} tok")
            print(f"  合计   ≈ {(len(sysmsg)+len(usr))//2} tok")
            assert "[邻域]" not in sysmsg and "[邻域]" not in usr, "邻域漏进 prompt 了"
            print("  ✓ 不含邻域原文")
            continue
        try:
            items, reason = probe.explore(job, idx)
        except Exception as exc:  # 一个落点失败不该打断整轮评测
            print(f"  （炸了：{type(exc).__name__}: {exc}）")
            continue
        if not items:
            print(f"  （没产出：{reason}）")
            continue
        for q in items:
            src = "有出处" if q.grounding == "book" else "凭记忆"
            print(f"  [{q.axis}·{src}·{q.timing}·深度{q.depth}] {q.text}")
            if q.why:
                print(f"     └ {q.why}")
            for a in q.anchors:
                print(f"     └ 锚 {a.get('level')} L{a.get('start_line')}-{a.get('end_line')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
