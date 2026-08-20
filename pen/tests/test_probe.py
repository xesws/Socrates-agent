"""异步深挖的离线测试。一个请求都不发。

守三样东西，都是设计里的硬约束：
1. 探索永远不给模型 tools（结构性的成本保证，不是自律）
2. prompt 里永远没有 neighborhood 原文（那是「echo 加引号」的病根）
3. 槽位校验挡得住编造的锚点
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pen import config, probe
from pen.probe import ProbeJob, build_system, parse_probe_json, should_probe, validate_slots


@pytest.fixture()
def idx(tmp_path, monkeypatch):
    from pen import libraries

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    monkeypatch.setattr(config, "LIBRARIES_DIR", tmp_path / ".pen" / "libraries")
    lines = ["# 封面", "", "# Level 0 — 终端", "", "## 第三拍 · 出身：Bash", ""]
    lines += ["Level 0 正文，含 is_allowed 这个名字。"] * 20
    lines += ["", "# Level 6 — 双模式", "", "## 第三拍 · 出身：Claude Code 的四档权限", ""]
    lines += ["Level 6 正文。"] * 20
    book = tmp_path / "b.md"
    book.write_text("\n".join(lines), encoding="utf-8")
    libraries.register(str(book), "probe-fx", extra_roots=[tmp_path])
    return libraries.load_index("probe-fx")


def _third(idx, level: str):
    return next(t for t in idx.toc if t.level == level and (t.beat or "").startswith("第三拍"))


# ── prompt 的两条看门狗 ────────────────────────────────────────


def test_probe_prompt_has_no_copyable_placeholder() -> None:
    assert "下一问" not in probe.PROBE_SYSTEM


def test_probe_prompt_never_carries_neighborhood(idx) -> None:
    """回归防线：谁把 neighborhood() 塞回探索 prompt，这条立刻红。

    邻域里全是手册自带的入门题，模型盯着它们必然产同构题——
    这才是「echo 加不加引号」的病根，不是 prompt 没写清楚。
    """
    assert "[邻域]" not in build_system(idx)


def test_build_system_carries_the_third_beat_whitelist(idx) -> None:
    sysmsg = build_system(idx)
    assert "第三拍" in sysmsg
    assert len(probe.third_beat_sections(idx)) == 2


def test_strip_code_fences_removes_the_shell_snippets() -> None:
    """`echo "$HOME"` 进了 prompt，模型就会去问引号。"""
    out = probe.strip_code_fences('论述。\n```bash\necho "$HOME"\n```\n更多论述。')
    assert "echo" not in out and "（代码块略）" in out


# ── 解析容错 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "彻底不是 JSON", "{坏掉的", "[1,2,3]", "null", '前言 {"questions": []} 后记'],
)
def test_parse_probe_json_never_raises(raw: str) -> None:
    got = parse_probe_json(raw)
    assert got == {"need_read": [], "questions": []}


def test_parse_probe_json_unwraps_code_fence() -> None:
    got = parse_probe_json('```json\n{"questions": [{"text": "甲？"}]}\n```')
    assert got["questions"] == [{"text": "甲？"}]


def test_parse_probe_json_caps_reads_and_questions() -> None:
    raw = json.dumps(
        {
            "need_read": [{"start_line": i} for i in range(9)],
            "questions": [{"text": f"第 {i} 问？"} for i in range(9)],
        }
    )
    got = parse_probe_json(raw)
    assert len(got["need_read"]) == config.PROBE_MAX_READS
    assert len(got["questions"]) == 3


# ── 槽位校验：质量机制的支点 ────────────────────────────────


def test_bridge_needs_two_distinct_levels(idx) -> None:
    one = {
        "axis": "bridge",
        "grounding": "book",
        "text": "甲乙对得上吗？",
        "anchors": [{"level": "Level 0", "start_line": 5, "end_line": 8}],
    }
    ok, why = validate_slots(one, idx)
    assert not ok and why == "bridge-needs-two-levels"


def test_vs_real_must_anchor_in_a_third_beat(idx) -> None:
    good = {
        "axis": "vs_real",
        "grounding": "book",
        "text": "我们和它差在哪？",
        "anchors": [{"level": "Level 6", "start_line": _third(idx, "Level 6").start_line, "end_line": _third(idx, "Level 6").start_line + 2}],
    }
    assert validate_slots(good, idx)[0]
    bad = {**good, "anchors": [{"level": "封面", "start_line": 1, "end_line": 1}]}
    assert validate_slots(bad, idx)[1] in ("vs-real-needs-third-beat", "anchor-invalid")


def test_tradeoff_needs_alt_and_failure_needs_trigger(idx) -> None:
    base = {"grounding": "book", "text": "为什么这样？", "anchors": [{"level": "Level 0", "start_line": 5, "end_line": 8}]}
    assert validate_slots({**base, "axis": "tradeoff"}, idx)[1] == "tradeoff-needs-alt"
    assert validate_slots({**base, "axis": "tradeoff", "alt": "另一种做法"}, idx)[0]
    assert validate_slots({**base, "axis": "failure"}, idx)[1] == "failure-needs-trigger"
    assert validate_slots({**base, "axis": "failure", "trigger": "并发时"}, idx)[0]


def test_fabricated_line_numbers_are_rejected(idx) -> None:
    bad = {
        "axis": "altitude",
        "grounding": "book",
        "text": "这条编了行号？",
        "anchors": [{"level": "Level 0", "start_line": 999999, "end_line": 999999}],
    }
    assert validate_slots(bad, idx)[1] == "anchor-invalid"


def test_level_must_match_where_the_line_actually_is(idx) -> None:
    bad = {
        "axis": "altitude",
        "grounding": "book",
        "text": "关号对不上？",
        "anchors": [{"level": "Level 6", "start_line": 5, "end_line": 8}],
    }
    assert validate_slots(bad, idx)[1] == "anchor-invalid"


def test_open_grounding_must_not_carry_anchors(idx) -> None:
    """声称手册里没出处，却又给锚点，就是在撒谎。"""
    bad = {
        "axis": "failure",
        "grounding": "open",
        "text": "它的沙箱怎么实现？",
        "trigger": "x",
        "anchors": [{"level": "Level 0", "start_line": 5, "end_line": 8}],
    }
    assert validate_slots(bad, idx)[1] == "open-with-anchors"
    good = {"axis": "failure", "grounding": "open", "text": "它的沙箱怎么实现？", "trigger": "x"}
    assert validate_slots(good, idx)[0]


def test_open_cannot_use_cross_reference_axes(idx) -> None:
    bad = {"axis": "bridge", "grounding": "open", "text": "甲乙对得上吗？"}
    assert validate_slots(bad, idx)[1] == "open-needs-anchors"


def test_quoted_tokens_must_appear_in_the_source(idx) -> None:
    src = "这里有 is_allowed 这个函数"
    base = {"axis": "altitude", "grounding": "book", "anchors": [{"level": "Level 0", "start_line": 5, "end_line": 8}]}
    assert validate_slots({**base, "text": "`is_allowed` 这道闸怎么拒写？"}, idx, source=src)[0]
    assert validate_slots({**base, "text": "`并不存在的名字` 是干嘛的？"}, idx, source=src)[1] == "quote-not-in-source"


# ── 触发闸门 ────────────────────────────────────────────────


_BASE = dict(
    enabled=True, ok=True, chip="socratic", pending=False, reply="x" * 200,
    anchor={"level": "Level 0"}, probe_calls=0, pending_pool=0, has_llm=True,
)


@pytest.mark.parametrize(
    "over,reason",
    [
        ({"enabled": False}, "off"),
        ({"has_llm": False}, "no-llm"),
        ({"ok": False}, "turn-failed"),
        ({"pending": True}, "awaiting-approval"),
        ({"chip": "search"}, "not-a-learning-turn"),
        ({"chip": "writeback"}, "not-a-learning-turn"),
        ({"reply": "太短"}, "reply-too-short"),
        ({"anchor": {"level": "封面"}}, "cover-or-appendix"),
        ({"anchor": {"level": "附录"}}, "cover-or-appendix"),
        ({"anchor": None}, "cover-or-appendix"),
        ({"probe_calls": config.PROBE_MAX_PER_SESSION}, "budget"),
        ({"pending_pool": config.PROBE_PENDING_CAP}, "backlog-full"),
    ],
)
def test_gate_blocks_with_a_named_reason(over, reason) -> None:
    ok, why = should_probe(**{**_BASE, **over})
    assert not ok and why == reason


def test_gate_lets_a_normal_turn_through() -> None:
    assert should_probe(**_BASE)[0]


def test_gate_does_not_skip_the_opening_chapter() -> None:
    """真人的有机记录几乎全落在封面和开篇。照搬 diagnose.is_curriculum
    （它把「开篇」也排除）会让这个功能一次都不触发。"""
    from pen import diagnose

    assert "开篇" in diagnose._SKIP_LEVELS
    assert should_probe(**{**_BASE, "anchor": {"level": "开篇"}})[0]


# ── 结构性成本 ──────────────────────────────────────────────


def test_explore_never_passes_tools(idx, monkeypatch, tmp_path) -> None:
    seen: list[dict] = []

    class FakeClient:
        def __init__(self, **kw):
            self.chat = type(
                "X", (), {"completions": type("Y", (), {"create": lambda _s, **kw: _cap(kw)})()}
            )()

    def _cap(kw):
        seen.append(kw)
        R = type("R", (), {})()
        R.choices = [type("C", (), {"message": type("M", (), {"content": '{"questions": []}'})()})()]
        return R

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    from pen.config import LLMConfig

    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=tmp_path / "b.md",
        anchor={"level": "Level 0", "start_line": 5, "end_line": 6}, atom="a", chip="socratic",
        user_text="", reply="讲了一段。" * 30, born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"),
    )
    probe.explore(job, idx)
    assert len(seen) == 1, "没要求读正文时应恰好一次调用"
    assert all("tools" not in kw for kw in seen), "探索绝不能带 tools"
    assert all(kw.get("stream") is False for kw in seen)


def test_explore_makes_exactly_two_calls_when_it_asks_to_read(idx, monkeypatch, tmp_path) -> None:
    """调用次数的硬上限没有常量可改，只有这条断言守着——
    explore() 的结构就是「一次，需要正文时再一次」。"""
    import json as _json

    seen: list[list[dict]] = []
    replies = [
        _json.dumps({"need_read": [{"start_line": 6, "end_line": 20}], "questions": []}),
        _json.dumps({"questions": []}),
        _json.dumps({"questions": []}),
    ]

    def fake_create(cfg, messages):
        seen.append(messages)
        return replies[min(len(seen) - 1, len(replies) - 1)]

    monkeypatch.setattr(probe, "_create", fake_create)
    from pen.config import LLMConfig

    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=tmp_path / "b.md",
        anchor={"level": "Level 0", "start_line": 6, "end_line": 7}, atom="a", chip="socratic",
        user_text="", reply="讲了一段。" * 30, born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"), extra_roots=[tmp_path],
    )
    probe.explore(job, idx)
    assert len(seen) == 2, "要正文时应恰好两次，绝不能变成循环"
    assert "[你要的那几段正文]" in seen[1][-1]["content"]


def test_explore_reads_at_most_two_segments(idx, monkeypatch, tmp_path) -> None:
    import json as _json

    grabbed: list[int] = []

    def fake_create(cfg, messages):
        if not grabbed:
            grabbed.append(1)
            return _json.dumps(
                {"need_read": [{"start_line": i * 3 + 6} for i in range(7)], "questions": []}
            )
        return _json.dumps({"questions": []})

    seen_reads: list[list] = []
    orig = probe._read_excerpts
    monkeypatch.setattr(probe, "_create", fake_create)
    monkeypatch.setattr(
        probe, "_read_excerpts", lambda job, r: (seen_reads.append(list(r)), orig(job, r))[1]
    )
    from pen.config import LLMConfig

    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=tmp_path / "b.md",
        anchor={"level": "Level 0", "start_line": 6, "end_line": 7}, atom="a", chip="socratic",
        user_text="", reply="讲了一段。" * 30, born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"), extra_roots=[tmp_path],
    )
    probe.explore(job, idx)
    assert seen_reads and len(seen_reads[0]) <= config.PROBE_MAX_READS


def test_harvest_drops_near_duplicates_within_one_batch(idx, tmp_path) -> None:
    """逐条过 clean_candidates 时相互去重是失效的（每次只喂一条）。
    「每轮都探」会把近似重复放大，所以 _harvest 必须自己再查一遍。"""
    from pen.config import LLMConfig

    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=tmp_path / "b.md",
        anchor={"level": "Level 0", "start_line": 5, "end_line": 6}, atom="a", chip="socratic",
        user_text="", reply="x", born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"),
    )
    a = {"text": "白名单排在危险检测前面，危险命令会不会被静默放行？", "axis": "failure",
         "grounding": "book", "trigger": "顺序调换", "timing": "now", "depth": 5,
         "anchors": [{"level": "Level 0", "start_line": 5, "end_line": 8}]}
    b = {**a, "text": "白名单排在危险检测前面，危险命令是不是会被静默放行？"}
    got = probe._harvest([a, b], job, idx)
    assert len(got) == 1, [g.text for g in got]


def test_harvest_respects_what_was_already_asked(idx, tmp_path) -> None:
    from pen.config import LLMConfig

    asked = "白名单排在危险检测前面，危险命令会不会被静默放行？"
    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=tmp_path / "b.md",
        anchor={"level": "Level 0", "start_line": 5, "end_line": 6}, atom="a", chip="socratic",
        user_text="", reply="x", born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"), asked=[asked],
    )
    item = {"text": asked, "axis": "failure", "grounding": "book", "trigger": "t",
            "timing": "now", "depth": 5,
            "anchors": [{"level": "Level 0", "start_line": 5, "end_line": 8}]}
    assert probe._harvest([item], job, idx) == []


# ── v0.8.4 审查修复 ────────────────────────────────────────


def test_quote_check_uses_anchor_text_not_the_selection(idx, tmp_path) -> None:
    """prompt 承诺核对的是「anchors 那几行」。第一版拿读者框选的那一小段去比，
    模型照 prompt 给术语加反引号反而被判死——那正是读者举的第一个例子。"""
    item = {
        "axis": "vs_real", "grounding": "book",
        "text": "我们跟 `Level 6 正文` 的差距在哪一层？",
        "anchors": [{"level": "Level 6", "start_line": _third(idx, "Level 6").start_line + 2,
                     "end_line": _third(idx, "Level 6").start_line + 6}],
    }
    src = probe.anchor_source(item, Path(idx.original_path), [])
    assert src, "没读到 anchors 正文"
    assert validate_slots(item, idx, source=src)[0]
    # 拿框选文本当语料 → 误杀
    assert validate_slots(item, idx, source="（框选了几行）")[1] == "quote-not-in-source"


def test_missing_source_skips_the_quote_check_instead_of_failing(idx) -> None:
    """读不到正文时跳过，不判死。宁可漏也别误杀。"""
    item = {"axis": "altitude", "grounding": "book", "text": "`某个词` 怎么理解？",
            "anchors": [{"level": "Level 0", "start_line": 5, "end_line": 8}]}
    assert validate_slots(item, idx, source="")[0]


def test_open_grounding_still_has_to_fill_its_slots(idx) -> None:
    """没出处不等于可以不填槽——读者选了 open 题不加标注，更没法自己分辨。"""
    assert validate_slots({"axis": "tradeoff", "grounding": "open", "text": "为什么不是另一种？"}, idx)[1] == "tradeoff-needs-alt"
    assert validate_slots({"axis": "failure", "grounding": "open", "text": "什么时候会炸？"}, idx)[1] == "failure-needs-trigger"
    assert validate_slots({"axis": "tradeoff", "grounding": "open", "text": "为什么不是另一种？", "alt": "别的做法"}, idx)[0]


def test_probe_prompt_examples_pass_our_own_filter() -> None:
    """给模型看的范例，系统自己不能删。有一条示范题曾经 69 字，超过上限 60。"""
    import re

    from pen.questions import clean_candidates, normalize_qkey
    from pen.session import FIXED_CHIPS

    demos = re.findall(r"^\s{3}「(.+?)」$", probe.PROBE_SYSTEM, re.M)
    assert len(demos) >= 5
    labels = [str(c["label"]) for c in FIXED_CHIPS]
    for d in demos:
        assert clean_candidates([d], fixed_labels=labels, limit=9), f"示范题过不了自己的过滤器：{d} ({len(normalize_qkey(d))} 字)"


def test_reply_threshold_matches_has_substantive() -> None:
    """两边差一个等号的话，正好 80 字那一轮判断相反。"""
    base = dict(enabled=True, ok=True, chip="socratic", pending=False,
                anchor={"level": "Level 0"}, probe_calls=0, pending_pool=0, has_llm=True)
    for n in (79, 80, 81):
        got = should_probe(**base, reply="x" * n)[0]
        assert got is (n > config.PROBE_MIN_REPLY_CHARS), f"{n} 字时不一致"


def test_history_lands_in_the_prompt(tmp_path) -> None:
    """读者明确要求「能看到前面几轮对话」。"""
    from pen.config import LLMConfig

    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=tmp_path / "b.md",
        anchor={"level": "Level 0", "start_line": 5, "end_line": 6}, atom="a", chip="socratic",
        user_text="现在这句", reply="师傅刚讲的", born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"),
        history=[{"role": "user", "text": "上一轮我问的"}, {"role": "assistant", "text": "上一轮师傅答的"}],
    )
    msg = probe.build_user_message(job)
    assert "[前面几轮聊了什么]" in msg
    assert "读者：上一轮我问的" in msg and "师傅：上一轮师傅答的" in msg
