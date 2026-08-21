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
    # libraries.LIBRARIES_DIR 是模块 import 时绑定的另一个名字，只 patch config
    # 那份等于没 patch——register 照样往真 .pen/libraries 写。实测：真实登记表
    # 里的 probe-fx / other-book / shelf-in / shelf-out 全是 pytest 留下的死记录，
    # 每次实时对话扫书架都要把它们遍历一遍。
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", tmp_path / ".pen" / "libraries")
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


def test_read_excerpts_can_reach_another_registered_book(idx, tmp_path, monkeypatch) -> None:
    """「结合 working directory 下的其他教材」不能只停在标题层。"""
    from pen import libraries
    from pen.config import LLMConfig

    other = tmp_path / "other.md"
    other.write_text("# 另一本教材\n\n" + "另一本的正文这一行。\n" * 30, encoding="utf-8")
    libraries.register(str(other), "other-book", extra_roots=[tmp_path])
    monkeypatch.setattr(
        "pen.config.handbook_allow_roots", lambda *a, **k: [tmp_path]
    )

    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=tmp_path / "b.md",
        anchor={"level": "Level 0", "start_line": 5, "end_line": 6}, atom="a", chip="socratic",
        user_text="", reply="x", born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"), extra_roots=[tmp_path],
    )
    got = probe._read_excerpts(job, [{"book": "另一本教材", "start_line": 3}])[0]
    assert "另一本的正文这一行" in got
    assert "〔出自《另一本教材》〕" in got, "读者要能看出这段来自哪本"


def test_read_excerpts_ignores_a_book_not_on_the_shelf(idx, tmp_path, monkeypatch) -> None:
    """点了一本书架上没有的，忽略而不是悄悄回退到当前这本——
    回退会让模型以为自己读到了那本，然后编出跨书联系。"""
    from pen.config import LLMConfig

    monkeypatch.setattr("pen.config.handbook_allow_roots", lambda *a, **k: [tmp_path])
    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=tmp_path / "b.md",
        anchor={"level": "Level 0", "start_line": 5, "end_line": 6}, atom="a", chip="socratic",
        user_text="", reply="x", born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"), extra_roots=[tmp_path],
    )
    assert probe._read_excerpts(job, [{"book": "根本不存在的书", "start_line": 3}])[0] == ""


def test_shelf_paths_excludes_registrations_outside_allowed_roots(tmp_path, monkeypatch) -> None:
    """登记表里躺着指向 /private/var/folders 的 pytest 夹具，不能让模型读到。"""
    from pen import libraries

    monkeypatch.setattr(libraries, "LIBRARIES_DIR", tmp_path / ".pen" / "libraries")
    inside = tmp_path / "vault"
    inside.mkdir()
    (inside / "ok.md").write_text("# 允许根内的书\n", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "bad.md").write_text("# 根外的书\n", encoding="utf-8")
    libraries.register(str(inside / "ok.md"), "shelf-in", extra_roots=[tmp_path])
    libraries.register(str(outside / "bad.md"), "shelf-out", extra_roots=[tmp_path])
    monkeypatch.setattr("pen.config.handbook_allow_roots", lambda *a, **k: [inside])

    cur = inside / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    names = probe._shelf_paths(cur)
    assert any("允许根内" in k for k in names)
    assert not any("根外" in k for k in names), names


def test_system_prompt_is_capped_for_a_much_bigger_handbook(idx) -> None:
    """build_user_packet 早就有 TOC_CHARS 预算，build_system 一直没有——
    换一本五倍厚的手册，system 会从 5.7k 涨到 23k token。"""
    import copy

    big = copy.deepcopy(idx)
    big.toc = idx.toc * 8
    big.sections = idx.sections * 8
    grown = len(probe.build_system(big))
    assert grown < len(probe.build_system(idx)) + config.PROBE_TOC_CHARS + config.PROBE_Q_CHARS


def test_thinning_keeps_every_level_represented() -> None:
    """超预算时每关均匀减，不砍尾巴。砍尾的教训是现成的：
    build_user_packet 原来写 toc_lines[:80]，砍掉的正好是 Capstone 和附录。"""
    rows = []
    for lv in ("Level 0", "Level 3", "Capstone", "附录"):
        for i in range(30):
            rows.append((lv, f"{lv} | L{i} | 第 {i} 条足够长的标题在这里占位"))
    got = probe._thin_by_level(rows, 800)
    for lv in ("Level 0", "Level 3", "Capstone", "附录"):
        assert lv in got, f"{lv} 被整关砍掉了"
    assert len(got) <= 900


def test_read_excerpts_honours_end_line(idx, tmp_path, monkeypatch) -> None:
    """end_line 以前被忽略，一律读 80 行——模型要的「一段」和拿到的不是一回事。"""
    from pen.config import LLMConfig

    book = tmp_path / "b.md"
    job = ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=book,
        anchor={"level": "Level 0", "start_line": 5, "end_line": 6}, atom="a", chip="socratic",
        user_text="", reply="x", born_round=1, lang="zh",
        cfg=LLMConfig("http://x", "sk", "m", "t", "off"), extra_roots=[tmp_path],
    )
    short = probe._read_excerpts(job, [{"start_line": 7, "end_line": 9}])[0]
    assert len(short.strip().splitlines()) == 3, short
    # 仍受 PROBE_READ_LINES 封顶
    long = probe._read_excerpts(job, [{"start_line": 1, "end_line": 9999}])[0]
    assert len(long.strip().splitlines()) <= config.PROBE_READ_LINES


def test_shelf_paths_and_digest_point_at_the_same_copy(tmp_path, monkeypatch) -> None:
    """同一本书在仓库根和 vault 各有一份、标题一模一样。书架列的是 vault 那份，
    反查（setdefault 先到先得）却给仓库根那份旧副本——模型读到的和它以为在读的
    不是同一个文件，而且它读的那份还是过期的。"""
    from pen import libraries, library_scan

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    library_scan._CACHE.clear()

    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    repo.mkdir()
    vault.mkdir()
    body = "# 通关手册\n\n## 开篇\n"
    stale = repo / "handbook.md"
    fresh = vault / "handbook.md"
    stale.write_text(body, encoding="utf-8")
    fresh.write_text(body, encoding="utf-8")
    import os

    os.utime(stale, (1_700_000_000, 1_700_000_000))
    os.utime(fresh, (1_800_000_000, 1_800_000_000))
    cur = vault / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    libraries.register(str(stale), "copy-stale", extra_roots=[tmp_path])
    libraries.register(str(fresh), "copy-fresh", extra_roots=[tmp_path])
    monkeypatch.setattr("pen.config.handbook_allow_roots", lambda *a, **k: [tmp_path])

    digest = library_scan.shelf_digest(
        cur, [str(stale), str(fresh)], allow_roots=[tmp_path], with_paths=True
    )
    got = probe._shelf_paths(cur)["通关手册"]
    assert str(fresh) in digest, f"书架列的不是 vault 那份：{digest}"
    assert got == fresh, f"反查给的是另一个副本：{got} ≠ {fresh}"


def test_probe_shelf_matches_what_read_excerpts_can_actually_read(tmp_path, monkeypatch) -> None:
    """probe 那条线同一个病：书架用全局闸列书，_read_excerpts 用
    `job.extra_roots or [REPO_ROOT]` 去读。列了一本读不到的，模型点名去读就
    静默落空——它以为引了别的教材，实际什么都没读到，还照着书名编。"""
    from pen import config, libraries, library_scan, probe

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    library_scan._CACHE.clear()

    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    repo.mkdir()
    vault.mkdir()
    cur = repo / "handbook.md"
    cur.write_text("# 手上这本\n", encoding="utf-8")
    far = vault / "other.md"
    far.write_text("# 够不着的那本\n\n## 第一章\n", encoding="utf-8")
    libraries.register(str(far), "far-book", extra_roots=[tmp_path])
    monkeypatch.setattr(config, "REPO_ROOT", repo)

    job = probe.ProbeJob(
        session_id="s", handbook_id="h", original_path=cur, anchor={}, atom="a",
        chip="free", user_text="", reply="", born_round=1, lang="zh",
        cfg=config.LLMConfig(base_url="", api_key="", model="m", key_source="t"),
        extra_roots=[],
    )
    roots = probe._reading_roots(job)
    assert roots == [repo.resolve()], roots
    digest = library_scan.shelf_digest(cur, [str(far)], allow_roots=roots)
    assert digest == "", f"书架列了读不到的书：{digest}"
    assert "够不着的那本" not in probe._shelf_paths(cur, roots)


def test_cross_book_excerpt_does_not_shadow_the_anchor_source(idx, tmp_path) -> None:
    """`src = excerpt or anchor_source(...)` 会短路：跨书真读到正文时，当前手册
    锚点行的正文根本不取，而题面每个反引号词都要在 source 里出现——
    跨教材题天然两边各引一个词，于是**读了反而归零，不读倒能过**。
    probe.py 里那条「拿不到正文时跳过检查，宁可漏也别误杀」的注释记着它修过一次。"""
    from pen import config, probe

    third0 = _third(idx, "Level 0")
    item = {
        "text": "另一本手册把 messages 画成一格数据流，本册这一拍说 `is_allowed` 是闸——两处对同一层抽象的命名对得上号吗？",
        "axis": "bridge",
        "depth": 4,
        "grounding": "book",
        "why": "把两本书对同一件事的命名摆到一起",
        "timing": "now",
        "anchors": [
            {"level": "Level 0", "start_line": third0.start_line, "end_line": third0.start_line + 6},
            {"level": "Level 6", "start_line": _third(idx, "Level 6").start_line, "end_line": _third(idx, "Level 6").start_line + 3},
        ],
    }
    job = probe.ProbeJob(
        session_id="s", handbook_id="probe-fx", original_path=Path(idx.original_path),
        anchor={"level": "Level 0", "start_line": third0.start_line}, atom="a",
        chip="free", user_text="", reply="", born_round=1, lang="zh",
        cfg=config.LLMConfig(base_url="", api_key="", model="m", key_source="t"),
        extra_roots=[],
    )
    cross = "〔出自《另一本》〕\n60\tmessages 是一格数据流。\n"
    got_cross = probe._harvest([item], job, idx, excerpt=cross)
    got_plain = probe._harvest([item], job, idx, excerpt="")
    assert len(got_plain) == 1, "前提变了：不跨书本来就该过"
    assert len(got_cross) == 1, "读了跨书正文反而一条都不留——短路又复活了"


def test_ambiguous_book_name_never_falls_back_to_the_current_handbook(tmp_path, monkeypatch) -> None:
    """`want in k or k in want` 会命中自己：book='手册' 拿到当前这本，
    模型照着**自己那本书**的正文写「跨教材」题，出处是伪造的。
    多义词命中多本时也不能靠 dict 顺序猜。"""
    from pen import config, libraries, library_scan, probe

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    cur = vault / "cur.md"
    cur.write_text("# 手搓测试手册\n\n## 一\n", encoding="utf-8")
    a = vault / "a.md"
    a.write_text("# 通关手册甲\n\n## 一\n", encoding="utf-8")
    b = vault / "b.md"
    b.write_text("# 通关手册乙\n\n## 一\n", encoding="utf-8")
    for f, hid in ((cur, "cur"), (a, "bk-a"), (b, "bk-b")):
        libraries.register(str(f), hid, extra_roots=[vault])
    monkeypatch.setattr(config, "handbook_allow_roots", lambda *x, **k: [vault])

    shelf = probe._shelf_paths(cur, [vault])
    assert "手搓测试手册" not in shelf, "当前这本还在书架反查表里"
    # 「通关手册」同时沾**两本不同的书** → 放弃，不靠 dict 顺序猜
    targets = {shelf[k] for k in shelf if "通关手册" in k or k in "通关手册"}
    assert len(targets) == 2, "前提变了：这两本本来就该都沾边"
    assert shelf.get("通关手册") is None
    # job 必须带上 vault：_read_excerpts 内部用 _reading_roots(job) 自建 shelf，
    # extra_roots=[] 时那是仓库根，tmp vault 里的书全被滤掉，shelf 恒为 {}——
    # 那样 got=="" 跟歧义毫无关系，是条空转断言。和 v0.8.9.2 揪出的那条同一个病根。
    job = _job(cur, [vault])
    # 正对照：先证明这条链路本来读得到，下面那条 assert 才有意义
    ok = probe._read_excerpts(job, [{"book": "通关手册甲", "start_line": 1, "end_line": 2}])[0]
    assert "《通关手册甲》" in ok, f"链路本身就不通，下面那条是空转：{ok!r}"
    got = probe._read_excerpts(job, [{"book": "通关手册", "start_line": 1, "end_line": 2}])[0]
    assert got == "", f"歧义时不该猜一本读：{got!r}"


def _job(cur: Path, extra=None):
    from pen import config, probe

    return probe.ProbeJob(
        session_id="s", handbook_id="h", original_path=cur, anchor={}, atom="a",
        chip="free", user_text="", reply="", born_round=1, lang="zh",
        cfg=config.LLMConfig(base_url="", api_key="", model="m", key_source="t"),
        extra_roots=extra if extra is not None else [],
    )


def test_one_book_with_two_keys_is_not_mistaken_for_two_books(tmp_path, monkeypatch) -> None:
    """`_shelf_paths` 给每本书登记两个 key：正文 H1 和 meta.title。
    Obsidian 笔记带 YAML frontmatter 时 H1 被推离第 1 行，build_index 退回文件名，
    两个 key 就不一样了——同一本书占两条候选，按 **key** 数会误判成歧义，
    把「Prompt」「工程手册」这种简称全毙掉。而 frontmatter 正是 vault 里
    第三方教材的默认形态，不是边角料。"""
    from pen import config, libraries, library_scan, probe

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    cur = vault / "cur.md"
    cur.write_text("# 手搓当前这本\n\n## 一\n", encoding="utf-8")
    other = vault / "Prompt工程手册.md"
    other.write_text(
        "---\ntags: [教材]\ndate: 2026-08-20\n---\n\n# Prompt 工程手册\n\n## 第一章\n",
        encoding="utf-8",
    )
    libraries.register(str(cur), "cur", extra_roots=[vault])
    libraries.register(str(other), "pw", extra_roots=[vault])
    monkeypatch.setattr(config, "handbook_allow_roots", lambda *a, **k: [vault])

    shelf = probe._shelf_paths(cur, [vault])
    keys = [k for k in shelf if "Prompt" in k or "工程" in k]
    assert len(keys) == 2, f"前提变了，不再是一本书两个 key：{keys}"
    assert len({shelf[k] for k in keys}) == 1, "两个 key 应指向同一个文件"

    job = _job(cur, [vault])
    for want in ("Prompt", "工程手册"):
        got = probe._read_excerpts(job, [{"book": want, "start_line": 6, "end_line": 7}])[0]
        assert got, f"book={want!r} 落空了——同一本书被当成两本"
        assert "《Prompt 工程手册》" in got, f"标签没印书架上那个名字：{got[:60]!r}"


def test_same_file_registered_under_two_spellings_is_still_one_book(tmp_path, monkeypatch) -> None:
    """_shelf_paths 存的是未 resolve 的 Path(raw)。两条登记记录指向同一个文件
    却写法不同（一条绕 ..），裸 Path 比较不相等，又会退回「同一本书当成两本」。"""
    from pen import config, libraries, library_scan, probe

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    sub = vault / "sub"
    sub.mkdir(parents=True)
    cur = vault / "cur.md"
    cur.write_text("# 手搓当前这本\n\n## 一\n", encoding="utf-8")
    other = vault / "别的教材.md"
    other.write_text("# 别的教材\n\n## 第一章\n", encoding="utf-8")
    libraries.register(str(cur), "cur", extra_roots=[vault])
    libraries.register(str(other), "o1", extra_roots=[vault])
    monkeypatch.setattr(config, "handbook_allow_roots", lambda *a, **k: [vault])

    # _read_excerpts 内部会自己调 _shelf_paths 重建，所以必须 patch 掉，
    # 光在外面改返回值是没用的（这条测试第一版就是这么写假的）。
    fake = {
        "别的教材": other,
        "别的教材（登记表里的另一种写法）": Path(str(sub / ".." / "别的教材.md")),
    }
    assert len(set(fake.values())) == 2, "前提：裸 Path 比较不相等"
    assert len({v.resolve() for v in fake.values()}) == 1, "前提：其实是同一个文件"
    monkeypatch.setattr(probe, "_shelf_paths", lambda *a, **k: fake)

    got = probe._read_excerpts(
        _job(cur, [vault]), [{"book": "别的教材（登记", "start_line": 1, "end_line": 2}]
    )[0]
    assert got, "同一个文件的两种写法被当成两本书了"
    assert "别的教材" in got


def test_shelf_reverse_lookup_never_sees_a_book_the_model_cannot(tmp_path, monkeypatch) -> None:
    """`shelf_digest` 只印 MAX_FILES 本，`_shelf_paths` 原来一本不落。
    于是模型**从没见过**的书也在「书名沾边的有几本」这场投票里有一票，
    能把它唯一看得见的那本否决掉。两边的候选集合本来就该是同一批。"""
    import re

    from pen import config, libraries, library_scan, probe

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    cur = vault / "cur.md"
    cur.write_text("# 手搓当前这本\n\n## 一\n", encoding="utf-8")
    libraries.register(str(cur), "cur", extra_roots=[vault])
    names = [
        "Prompt 注入攻防", "向量库实战", "推理加速手册", "算子优化手记",
        "多模态入门", "强化学习小抄", "分布式训练札记", "评测方法论",
        "检索增强笔记", "Prompt 工程手册",
    ]
    import os

    for i, name in enumerate(names):
        f = vault / f"b{i}.md"
        f.write_text(f"# {name}\n\n## 第一章\n", encoding="utf-8")
        libraries.register(str(f), f"bk{i}", extra_roots=[vault])
        # _prefer_nearby 按 mtime 新的优先，所以显式定序：靠前的 8 本进书架，
        # 《检索增强笔记》和《Prompt 工程手册》被 MAX_FILES 截掉、模型看不见。
        os.utime(f, (2_000_000_000 - i, 2_000_000_000 - i))
    monkeypatch.setattr(config, "handbook_allow_roots", lambda *a, **k: [vault])

    regs = [m.original_path for m in libraries.list_handbooks()]
    shelf_text = library_scan.shelf_digest(cur, regs, allow_roots=[vault], with_paths=True)
    visible = {Path(p) for p in re.findall(r"path: (\S.*)", shelf_text)}
    assert len(visible) == library_scan.MAX_FILES, f"前提变了：{len(visible)} 本"

    reachable = set(probe._shelf_paths(cur, [vault]).values())
    assert reachable <= visible, f"反查能摸到模型看不见的书：{reachable - visible}"

    assert not any("工程手册" in str(v) or "检索增强" in str(v) for v in visible), \
        "前提变了：被截掉的那两本反而可见了"
    # 「Prompt」在可见的 8 本里只沾《Prompt 注入攻防》一本 → 该命中，
    # 而不是被看不见的第 10 本《Prompt 工程手册》投票否决
    got = probe._read_excerpts(
        _job(cur, [vault]), [{"book": "Prompt", "start_line": 1, "end_line": 2}]
    )[0]
    assert "《Prompt 注入攻防》" in got, f"被看不见的书否决了：{got!r}"


def test_meta_title_key_survives_a_noncanonical_registered_path(tmp_path, monkeypatch) -> None:
    """`pick_books` 回传的 `d["path"]` 是 `str(Path(raw))`，会把 `//` 和尾斜杠吃掉。
    拿原始 `m.original_path` 当 metas 的 key 就是在赌登记表里存的一定是规范形式——
    赌输了静默少一个 meta.title 的 key，表现为「某个简称突然反查不到」。"""
    import json

    from pen import config, libraries, library_scan, probe

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    cur = vault / "cur.md"
    cur.write_text("# 手搓当前这本\n", encoding="utf-8")
    other = vault / "别的.md"
    # frontmatter 把 H1 推离第 1 行 → meta.title 退回文件名，两个 key 才不同
    other.write_text("---\nx: 1\n---\n\n# 别的教材\n\n## 一\n", encoding="utf-8")
    libraries.register(str(cur), "c", extra_roots=[vault])
    libraries.register(str(other), "o", extra_roots=[vault])
    mp = lib / "o" / "meta.json"
    d = json.loads(mp.read_text(encoding="utf-8"))
    d["original_path"] = str(other).replace("/vault/", "/vault//")  # 非规范写法
    mp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "handbook_allow_roots", lambda *a, **k: [vault])

    shelf = probe._shelf_paths(cur, [vault])
    assert "别的教材" in shelf, f"正文 H1 那个 key 丢了：{list(shelf)}"
    assert "别的.md" in shelf, f"meta.title 那个 key 丢了：{list(shelf)}"


def _cross_fixture(tmp_path, monkeypatch):
    """当前手册 + 书架上另一本，返回 (job, idx, shelf, 书名)。"""
    from pen import config, libraries, library_scan, probe

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    cur = vault / "cur.md"
    cur.write_text(
        "# 手搓当前这本\n\n# Level 0 — 终端\n\n## 第三拍 · 出身：真实框架里的 Bash\n"
        + "\n".join(f"本册第 {i} 行" for i in range(6, 60))
        + "\n\n# Level 5 — 审批\n"
        + "\n".join(f"本册第 {i} 行" for i in range(62, 90)),
        encoding="utf-8",
    )
    other = vault / "别本.md"
    other.write_text(
        "# 另一本教材\n\n# Level 0 — 它自己的开头\n"
        + "\n".join(f"别本第 {i} 行，里面有 approval_gate 这个词" for i in range(4, 200)),
        encoding="utf-8",
    )
    libraries.register(str(cur), "cur", extra_roots=[vault])
    libraries.register(str(other), "oth", extra_roots=[vault])
    monkeypatch.setattr(config, "handbook_allow_roots", lambda *a, **k: [vault])

    job = probe.ProbeJob(
        session_id="s", handbook_id="cur", original_path=cur, anchor={}, atom="a",
        chip="free", user_text="", reply="", born_round=1, lang="zh",
        cfg=config.LLMConfig(base_url="", api_key="", model="m", key_source="t"),
        extra_roots=[vault],
    )
    return job, libraries.load_index("cur"), probe._shelf_or_empty(job), "另一本教材"


def test_cross_anchor_must_be_in_a_span_we_actually_read(tmp_path, monkeypatch) -> None:
    """跨书行号是我们自己 read_file 读给模型的，要验的不是「这行存不存在」
    （查别本索引那条路要付并发写、索引中毒、mtime 陷阱一整套代价），
    而是「这行是不是我们本轮真读过的那几段之一」。读了 L4-40 却写 L3000 就是编。"""
    from pen import probe

    job, idx, shelf, book = _cross_fixture(tmp_path, monkeypatch)
    _, spans = probe._read_excerpts(job, [{"book": book, "start_line": 4, "end_line": 20}])
    assert spans and spans[0][0] == book

    here = {"level": "Level 0", "start_line": 3, "end_line": 5}
    ok = probe.validate_slots(
        {"axis": "altitude", "grounding": "book",
         "anchors": [here, {"book": book, "start_line": 6, "end_line": 8}]},
        idx, shelf=shelf, current=job.original_path, read_spans=spans,
    )
    assert ok == (True, ""), ok
    bad = probe.validate_slots(
        {"axis": "altitude", "grounding": "book",
         "anchors": [here, {"book": book, "start_line": 3000, "end_line": 3005}]},
        idx, shelf=shelf, current=job.original_path, read_spans=spans,
    )
    assert bad == (False, "anchor-invalid"), bad


def test_every_question_needs_an_anchor_in_this_book(tmp_path, monkeypatch) -> None:
    """少了这条，altitude / tradeoff / failure 在跨书下就是免检通道——
    随便指另一本任意一行都能过，而读者手里拿的是当前这本。"""
    from pen import probe

    job, idx, shelf, book = _cross_fixture(tmp_path, monkeypatch)
    _, spans = probe._read_excerpts(job, [{"book": book, "start_line": 4, "end_line": 20}])
    got = probe.validate_slots(
        {"axis": "altitude", "grounding": "book",
         "anchors": [{"book": book, "start_line": 6, "end_line": 8}]},
        idx, shelf=shelf, current=job.original_path, read_spans=spans,
    )
    assert got == (False, "needs-an-anchor-in-this-book"), got


def test_bridge_across_books_is_not_killed_by_two_level_zeros(tmp_path, monkeypatch) -> None:
    """两本书都有「Level 0」。按 level 字符串去重会把「本册 Level 0 + 另一本
    Level 0」判成同一关，跨书搭桥直接被毙——而那正是最该抛的那种题。"""
    from pen import probe

    job, idx, shelf, book = _cross_fixture(tmp_path, monkeypatch)
    _, spans = probe._read_excerpts(job, [{"book": book, "start_line": 4, "end_line": 20}])
    got = probe.validate_slots(
        {"axis": "bridge", "grounding": "book",
         # 别本那一关**也叫 Level 0**——这正是按 level 字符串去重会踩的地方
         "anchors": [{"level": "Level 0", "start_line": 3, "end_line": 5},
                     {"book": book, "level": "Level 0", "start_line": 6, "end_line": 8}]},
        idx, shelf=shelf, current=job.original_path, read_spans=spans,
    )
    assert got == (True, ""), got


def test_an_anchor_without_book_cannot_borrow_the_other_books_source(tmp_path, monkeypatch) -> None:
    """不填 book 的锚是本册锚，它引用的词就该在本册那几行里。
    一律把跨书正文拼进语料的话，一条「不填 book 却在讲另一本书」的题会拿别人的
    正文蒙混过关——那是最难堵的那种假出处。"""
    from pen import probe

    job, idx, shelf, book = _cross_fixture(tmp_path, monkeypatch)
    excerpt, spans = probe._read_excerpts(job, [{"book": book, "start_line": 4, "end_line": 20}])
    assert "approval_gate" in excerpt
    assert "approval_gate" not in job.original_path.read_text(encoding="utf-8")

    # axis 用 altitude：它只要求一条合法锚。用 bridge 的话第一条会因为
    # 「需要两个不同的关」被拒，测试就通过了却跟语料隔离毫无关系——
    # 突变表抓到过这条假测试。
    item = {"text": "那本书讲的 `approval_gate` 跟这本第三拍对得上吗？",
            "axis": "altitude", "grounding": "book", "depth": 4, "why": "w", "timing": "now",
            "anchors": [{"level": "Level 0", "start_line": 3, "end_line": 5}]}
    assert probe._harvest([item], job, idx, excerpt, spans) == [], "借了别本的正文当出处"
    item2 = dict(item)
    item2["anchors"] = [{"level": "Level 0", "start_line": 3, "end_line": 5},
                        {"book": book, "start_line": 6, "end_line": 8}]
    assert probe._harvest([item2], job, idx, excerpt, spans), "老实填了 book 反而被毙"


def test_shelf_hint_only_fires_when_the_book_is_actually_named() -> None:
    """prompt 里泛泛地说「可以读别的教材」没用——两次真跑模型都回 need_read: []，
    照着书架大纲就出题了。确定性信号优先于模型自觉：读者真点了名就直接说。

    但匹配必须克制：「教材」「开篇」「Agent」这种短词谁都沾边，一误报就是白花
    一次跨书读取（读者选的是每小时 40 次的预算，不是无限）。"""
    from pen import probe

    shelf = (
        "- 《手搓 SWE Agent 通关手册 v2 · 教材级（全册：开篇 + Level 0~6 + Capstone）》"
        "（共 13083 行）：手搓 SWE Agent 通关手册 v2 / 开篇：你即将带一个实习生"
    )
    assert probe.books_mentioned(shelf, "这本跟那本《通关手册》什么关系？")
    assert probe.books_mentioned(shelf, "", "Capstone 那一关要交什么？")
    for noise in ("shell 和 Bash 有什么区别", "这教材写得挺好", "开篇讲了什么",
                  "Agent 是怎么跑起来的", ""):
        assert not probe.books_mentioned(shelf, noise), f"误报了：{noise!r}"
    assert not probe.books_mentioned("", "《通关手册》"), "没有书架就不该提示"


def test_shelf_hint_is_dropped_once_the_excerpt_is_in_hand() -> None:
    """第二轮已经把正文读回来了，再喊「快去读」就是噪音。"""
    from pathlib import Path

    from pen import config, probe

    job = probe.ProbeJob(
        session_id="s", handbook_id="h", original_path=Path("/x.md"),
        anchor={"level": "封面", "start_line": 1, "end_line": 2}, atom="a", chip="free",
        user_text="那本《通关手册》怎么讲的？", reply="", born_round=1, lang="zh",
        cfg=config.LLMConfig(base_url="", api_key="", model="m", key_source="t"),
        shelf="- 《手搓 SWE Agent 通关手册 v2 · 教材级》（共 13083 行）：开篇",
    )
    assert "点到了书架上的书" in probe.build_user_message(job)
    assert "点到了书架上的书" not in probe.build_user_message(job, excerpt="205\t正文")


def test_level_with_beat_appended_is_still_a_valid_anchor(tmp_path, monkeypatch) -> None:
    """模型常把 level 写成「封面 / 概念对比：一类 vs 实现」——它抄的是 prompt 里
    「位置：level / beat / q_title」那个格式。真跑撞到过：一条模型自己产出的、
    完全正确的跨书 bridge 题，就因为本册那个锚多写了 beat 被整条毙掉，
    351 秒的探索白走一趟。level 是用来交叉验证行号的，不是考格式。"""
    from pen import probe

    job, idx, shelf, book = _cross_fixture(tmp_path, monkeypatch)
    _, spans = probe._read_excerpts(job, [{"book": book, "start_line": 4, "end_line": 20}])
    sec = idx.locate(3)
    for lv in (sec.level, f"{sec.level} / 第三拍 · 出身：真实框架里的 Bash", f"{sec.level}/随便什么"):
        got = probe.validate_slots(
            {"axis": "altitude", "grounding": "book",
             "anchors": [{"level": lv, "start_line": 3, "end_line": 5}]},
            idx, shelf=shelf, current=job.original_path, read_spans=spans,
        )
        assert got == (True, ""), f"level={lv!r} 被毙了：{got}"
    # 但真写错关号还是要拒——宽容的是格式，不是内容
    bad = probe.validate_slots(
        {"axis": "altitude", "grounding": "book",
         "anchors": [{"level": "Level 9 / 不存在", "start_line": 3, "end_line": 5}]},
        idx, shelf=shelf, current=job.original_path, read_spans=spans,
    )
    assert bad == (False, "anchor-invalid"), bad
