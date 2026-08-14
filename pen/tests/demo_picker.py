"""Pick the most stunning live demo reply. Not part of default pytest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pen.tests.live_scenarios import chat_sse, new_session

OUT = Path("/tmp/pen-demo-picker")
OUT.mkdir(parents=True, exist_ok=True)

CASES = [
    {
        "n": 1,
        "title": "开篇 · 师傅带实习生",
        "line": 28,
        "selected": (
            "你是师傅，Agent 是你带的实习生。这个实习生聪明、勤快、什么都敢试，"
            "但有两个致命特点：记性为零（每干一步都要重读一遍工作日志），"
            "胆子极大（rm -rf 也照敲不误）。"
        ),
        "chip": "explain_zero",
        "question": "观众只能带走一句。这句话到底在防哪两种事故？各给一个会炸的最小反例。",
    },
    {
        "n": 2,
        "title": "Level 2 · 全量重发 vs 桌面有限",
        "line": 3728,
        "selected": (
            "记忆靠全量重发（决策④），而全量重发会无限烧钱、无限占桌面（决策⑤）。"
            "Agent 工程里至少一半的设计——输出截断、步数上限、context 压缩、历史总结"
            "——都是在这两个决策的夹缝里长出来的。"
        ),
        "chip": "free",
        "question": "不要反问。画一张两列表：左边全量重发，右边桌面有限。截断、步数上限、压缩各自缝哪一边？再给两个会把桌面撑爆的反例。",
    },
    {
        "n": 3,
        "title": "Level 2 · Q4 LLM 没有记忆",
        "line": 3994,
        "selected": "Q4. 为什么说 LLM 没有记忆？多轮对话是怎么\"装\"出记忆的？",
        "chip": "explain_zero",
        "question": "",
    },
    {
        "n": 4,
        "title": "Level 2 · 两条 append",
        "line": 4047,
        "selected": (
            'messages.append({"role": "user", "content": user_input})\n'
            "resp = client.chat.completions.create(model=MODEL, messages=messages)\n"
            'messages.append({"role": "assistant", "content": reply})'
        ),
        "chip": "free",
        "question": "不要反问。少 append 上面哪一行，下一轮会先变成什么笑话？给我两个最小反例，点名是哪一行没写。",
    },
    {
        "n": 5,
        "title": "开篇 · 铁序",
        "line": 100,
        "selected": (
            "铁序：LLM 返回 message → main 拆返回值 → permissions 审批 → "
            "registry dispatch → tools_impl 执行 → 结果回填；模型从不自己去调审批台或工具。"
            "审批永远在分发之前。"
        ),
        "chip": "free",
        "question": "不要反问。用『实习生自己拿钥匙开保险柜』讲清楚：为什么模型不能直接调工具？如果把审批挪到 dispatch 之后，会炸出什么事故？",
    },
    {
        "n": 6,
        "title": "Level 3 · 灰色地带 Q8",
        "line": 6086,
        "selected": (
            "# 灰色地带（Q8）：催模型，不是 input() 等师傅\n"
            'messages.append({"role": "user", "content": "请继续：要么给 <bash_action>，要么给 <done>。"})'
        ),
        "chip": "free",
        "question": "不要反问。这段 else 在催谁？如果改成 input() 等师傅，循环会退化成哪一关？给一个『模型只写了半页思考、两张标签都没有』的走位。",
    },
    {
        "n": 7,
        "title": "Level 1 · Q8 shell=True",
        "line": 2494,
        "selected": "Q8. shell=True 带来了什么能力、什么风险？",
        "chip": "explain_zero",
        "question": "",
    },
    {
        "n": 8,
        "title": "Level 6 · 为什么要两种工作模式",
        "line": 11025,
        "selected": (
            "Level 5 的审批台解决了「每个动作签字」的问题，但用几次你就会发现两个新痛点："
            "新任务不敢直接开干；沙盒任务审批纯属浪费。"
            "审批规则不该只有一套，而该按「环境风险」切换。"
        ),
        "chip": "explain_zero",
        "question": "plan / default / execute-auto 三档绳子，各举一个只属于它的任务。不要反问。",
    },
    {
        "n": 9,
        "title": "Level 5/6 · is_allowed 规则链",
        "line": 11622,
        "selected": (
            "def is_allowed(name: str, args: dict, mode: str) -> tuple[bool, str]:\n"
            "    # HARD_DENY 必须先于 execute-auto 放行"
        ),
        "chip": "free",
        "question": "不要反问。保安门口：同一条 rm -rf /，在 execute-auto 为什么仍要拦？把规则顺序画成四步，再给一个『顺序写反就放行核弹』的反例。",
    },
    {
        "n": 10,
        "title": "开篇 · 最终回答不是积木",
        "line": 100,
        "selected": (
            "最终回答不是一块积木——图上没有独立的「最终回答」终端块，"
            "它只是 LLM → 主循环 → 师傅 的两条边。"
        ),
        "chip": "free",
        "question": "不要反问。用修计算器那四步，指出最终回答是从哪两条边流出来的。为什么不能给它单独一块积木？",
    },
]


def run_one(case: dict) -> dict:
    sid = new_session()
    ev = chat_sse(
        {
            "session_id": sid,
            "selected_text": case["selected"],
            "start_line": case["line"],
            "end_line": case["line"],
            "chip": case["chip"],
            "user_text": case["question"],
        },
        timeout=420,
    )
    text = ev.get("text") or ""
    rec = {
        "n": case["n"],
        "title": case["title"],
        "line": case["line"],
        "chip": case["chip"],
        "question": case["question"],
        "selected": case["selected"],
        "chars": len(text),
        "tools": len(ev.get("tools") or []),
        "error": ev.get("error"),
        "has_mermaid": "```mermaid" in text or "```text" in text,
        "has_table": "|" in text and "---" in text,
        "has_code": "```" in text,
        "looks_socratic": text.count("？") >= 3 and "TL;DR" not in text and "tldr" not in text.lower(),
        "text": text,
    }
    (OUT / f"case-{case['n']:02d}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / f"case-{case['n']:02d}.md").write_text(text, encoding="utf-8")
    return rec


def main() -> None:
    only = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else None
    cases = [c for c in CASES if only is None or c["n"] in only]
    print(f"running {len(cases)} cases → {OUT}", flush=True)
    for case in cases:
        print(f"\n=== #{case['n']} {case['title']} L{case['line']} ===", flush=True)
        try:
            rec = run_one(case)
            preview = (rec["text"] or "")[:240].replace("\n", " / ")
            print(
                f"chars={rec['chars']} tools={rec['tools']} err={rec['error']!r} "
                f"table={rec['has_table']} code={rec['has_code']} socratic={rec['looks_socratic']}",
                flush=True,
            )
            print(preview, flush=True)
        except Exception as exc:
            print(f"FAIL {exc!r}", flush=True)
            (OUT / f"case-{case['n']:02d}.err").write_text(repr(exc), encoding="utf-8")


if __name__ == "__main__":
    main()
