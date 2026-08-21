from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from pen.config import LLMConfig
from pen.session import PenSession
from pen.tutor import (
    ProviderError,
    llm_create_kwargs,
    propose_fold_md,
    provider_error_message,
    stream_chat,
    usage_snapshot,
)


def _cfg() -> LLMConfig:
    return LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-secret-do-not-leak",
        model="deepseek-v4-flash",
        key_source="settings",
    )


def _status_exc(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


def _patch_openai_boom(monkeypatch, exc: Exception) -> None:
    """openai.OpenAI 换成假客户端：create 必抛 exc。exc 是 openai 的异常实例。"""

    class _BoomCompletions:
        def create(self, **_kwargs: Any) -> Any:
            raise exc

    class _BoomClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_BoomCompletions())

    monkeypatch.setattr(openai, "OpenAI", _BoomClient)


def test_usage_snapshot_is_last_call_not_a_sum() -> None:
    first = usage_snapshot(100, 20)
    second = usage_snapshot(250, 40)
    assert first == {
        "context_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens": 100,
    }
    assert second["context_tokens"] == 250
    assert second["completion_tokens"] == 40
    assert second["prompt_tokens"] == 250
    merged = {**first, **second}
    assert merged["context_tokens"] == 250
    assert merged["context_tokens"] != first["context_tokens"] + second["context_tokens"]


def test_llm_create_kwargs_thinking_off_vs_high() -> None:
    off = LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk",
        model="deepseek-v4-flash",
        key_source="settings",
        thinking="off",
    )
    kw_off = llm_create_kwargs(off, messages=[{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in kw_off
    assert "extra_body" not in kw_off
    assert kw_off["model"] == "deepseek-v4-flash"
    high = LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk",
        model="deepseek-v4-flash",
        key_source="settings",
        thinking="high",
    )
    kw_high = llm_create_kwargs(high, messages=[], tools=[{"type": "function"}])
    assert kw_high["reasoning_effort"] == "high"
    assert kw_high["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kw_high["tools"] == [{"type": "function"}]


def test_stream_chat_error_points_to_settings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pen.tutor.resolve_llm", lambda *a, **k: None)
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    events = list(stream_chat(sess, book, "packet"))
    assert events[0]["type"] == "error"
    assert "设置 → Socrates Pen" in events[0]["message"]
    assert "环境变量" not in events[0]["message"]


def test_provider_error_message_maps_common_failures() -> None:
    auth = provider_error_message(_status_exc(openai.AuthenticationError, 401))
    assert "设置" in auth and "API Key" in auth
    denied = provider_error_message(_status_exc(openai.PermissionDeniedError, 403))
    assert "设置" in denied and "API Key" in denied
    bad = provider_error_message(_status_exc(openai.BadRequestError, 400))
    assert "Thinking" in bad and "off" in bad
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    conn = provider_error_message(openai.APIConnectionError(request=req))
    assert "Base URL" in conn
    timeout = provider_error_message(openai.APITimeoutError(request=req))
    assert "Base URL" in timeout
    assert "Base URL" in provider_error_message(OSError(" refused"))
    assert "Base URL" in provider_error_message(TimeoutError())
    other = provider_error_message(_status_exc(openai.RateLimitError, 429))
    assert "RateLimitError" in other
    assert "sk-secret-do-not-leak" not in other


def test_stream_chat_auth_error_yields_error_event(monkeypatch, tmp_path: Path) -> None:
    _patch_openai_boom(monkeypatch, _status_exc(openai.AuthenticationError, 401))
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    events = list(stream_chat(sess, book, "packet", llm=_cfg()))
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "设置" in errors[0]["message"] and "API Key" in errors[0]["message"]
    assert "sk-secret-do-not-leak" not in errors[0]["message"]


def test_stream_chat_thinking_rejected_points_to_off(monkeypatch, tmp_path: Path) -> None:
    _patch_openai_boom(monkeypatch, _status_exc(openai.BadRequestError, 400))
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    events = list(stream_chat(sess, book, "packet", llm=_cfg()))
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "Thinking" in errors[0]["message"]


def test_propose_fold_md_provider_error_raises_runtime_error(monkeypatch) -> None:
    _patch_openai_boom(monkeypatch, _status_exc(openai.AuthenticationError, 401))
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    sess.last_assistant = "讲了一段。"
    with pytest.raises(ProviderError, match="API Key") as excinfo:
        propose_fold_md(sess, llm=_cfg())
    assert isinstance(excinfo.value, RuntimeError)
    assert "sk-secret-do-not-leak" not in str(excinfo.value)


# ── v0.8.0：动态芯片的清洗 ──────────────────────────────────────────


def test_parse_dynamic_chips_returns_rich_dicts() -> None:
    from pen.tutor import parse_dynamic_chips

    reply = (
        "正文。\n\n<!--pen:chips\n"
        "- 七块积木里 messages 为什么不算文件？数据流上它凭什么独立一格？\n"
        "-->"
    )
    visible, chips = parse_dynamic_chips(reply)
    assert visible == "正文。"
    assert chips == [
        {
            "id": "q0",
            "kind": "quick",
            "text": "七块积木里 messages 为什么不算文件？数据流上它凭什么独立一格？",
        }
    ]


def test_parse_dynamic_chips_drops_placeholders_and_navigation() -> None:
    from pen.tutor import parse_dynamic_chips

    reply = (
        "答。\n<!--pen:chips\n"
        "- 下一问 1\n"
        "- 下一问 2\n"
        "- ...\n"
        "- 带我读一下本书玩法说明\n"
        "- 那步数上限设多少合适？任务没跑完就被熔断了怎么办？\n"
        "-->"
    )
    _visible, chips = parse_dynamic_chips(reply)
    assert [c["text"] for c in chips] == ["那步数上限设多少合适？任务没跑完就被熔断了怎么办？"]


def test_parse_dynamic_chips_caps_at_two() -> None:
    from pen.tutor import parse_dynamic_chips

    # 用真正不同的五条：只差一个数字的问题会被相互去重合并掉，
    # 那样测的就不是 limit 而是去重了。
    lines = "\n".join(
        "- " + q
        for q in (
            "为什么审批闸门要单独算一件，不能塞进工具里？",
            "那步数上限设多少合适？任务没跑完就被熔断了怎么办？",
            "白名单排在危险检测前面，危险命令会不会被静默放行？",
            "工具输出被截断之后，实习生看不到完整结果，会不会瞎猜？",
            "为什么第一个参考实现偏偏是 mini-swe-agent，而不是 LangChain？",
        )
    )
    _visible, chips = parse_dynamic_chips(f"答。\n<!--pen:chips\n{lines}\n-->")
    assert len(chips) == 2


def test_parse_dynamic_chips_without_block_is_untouched() -> None:
    from pen.tutor import parse_dynamic_chips

    visible, chips = parse_dynamic_chips("就是一段普通回复。")
    assert visible == "就是一段普通回复。"
    assert chips == []


def test_finish_text_emits_both_chip_shapes() -> None:
    """dynamic_chips 保持 list[str]（web/ 那个前端还在吃它），富格式走 dyn_chips。"""
    from pen.session import PenSession
    from pen.tutor import _finish_text

    sess = PenSession(session_id="s1", handbook_id="h1")
    raw = "答案很长" * 30 + "\n<!--pen:chips\n- 那步数上限设多少合适？没跑完被熔断怎么办？\n-->"
    done = [ev for ev in _finish_text(sess, raw, {"prompt_tokens": 1}) if ev["type"] == "done"][0]
    assert done["dynamic_chips"] == ["那步数上限设多少合适？没跑完被熔断怎么办？"]
    assert done["dyn_chips"][0]["kind"] == "quick"
    assert sess.last_chips == done["dyn_chips"]
    assert sess.has_substantive is True


def test_build_user_packet_keeps_whole_toc_and_lists_asked() -> None:
    """toc 以前是 [:80]，那本手册有 87 条——砍掉的正好是 Capstone 和附录。"""
    from pathlib import Path

    from pen import libraries
    from pen.tutor import build_user_packet

    idx = libraries.load_index("swe-agent-v2")
    packet, _anchor = build_user_packet(
        idx,
        Path(idx.original_path),
        selected_text="x",
        start_line=544,
        end_line=545,
        chip="socratic",
        user_text="",
        asked=["上一轮抛过的那个问题？"],
    )
    toc_seg = packet.split("[全书目录（不要整本背诵）]")[1].split("[框选]")[0]
    assert len([l for l in toc_seg.strip().splitlines() if l.strip()]) == len(idx.toc)
    assert "附录" in toc_seg
    assert "上一轮抛过的那个问题？" in packet


def test_packet_omits_the_shelf_block_when_there_is_only_one_book() -> None:
    """写「（无）」会让模型以为我们替它确认过没有别的书。整段不在时，
    它答「另一本我没读到」是对的——那本来就是实情。"""
    from pathlib import Path

    from pen import libraries
    from pen.tutor import build_user_packet

    idx = libraries.load_index("swe-agent-v2")
    packet, _ = build_user_packet(
        idx, Path(idx.original_path), selected_text="x",
        start_line=544, end_line=545, chip="free", user_text="",
    )
    assert "[工作目录里的其他教材]" not in packet


def test_packet_carries_the_shelf_with_paths_so_the_tutor_can_read_file() -> None:
    """v0.8.1 把跨教材整个挂在 probe 上，实时这条线一个字都没有。
    师傅手里有 read_file、沙箱也放行，却不知道有那本书、更不知道路径。"""
    from pathlib import Path

    from pen import libraries
    from pen.tutor import build_user_packet

    idx = libraries.load_index("swe-agent-v2")
    shelf = "- 《另一本》  path: /tmp/vault/other.md\n  大纲：开篇 / 第一章"
    packet, _ = build_user_packet(
        idx, Path(idx.original_path), selected_text="x",
        start_line=544, end_line=545, chip="free",
        user_text="另一本讲什么", shelf=shelf,
    )
    assert "[工作目录里的其他教材]" in packet
    assert "/tmp/vault/other.md" in packet, "光给书名，师傅只会去猜文件名"
    assert "read_file" in packet, "得明说怎么读，否则它照着大纲吹"
    # 书架排在目录之前：先说手上这本、库里还有哪些，再展开当前这本的目录。
    # 插在目录和框选之间会污染 test_build_user_packet_keeps_whole_toc 的切片。
    assert packet.index("[工作目录里的其他教材]") < packet.index("[全书目录（不要整本背诵）]")


def test_packet_drops_the_shelf_block_when_the_budget_eats_every_row() -> None:
    """预算截完一行不剩时，段头还在、条目是空的——等于向模型断言「有别的教材」
    然后一本都不给，它只能凭空编。宁可整段不出现。"""
    from pathlib import Path

    from pen import libraries, tutor
    from pen.tutor import build_user_packet

    idx = libraries.load_index("swe-agent-v2")
    huge = "- 《" + "长" * 4000 + "》  path: /x.md"
    packet, _ = build_user_packet(
        idx, Path(idx.original_path), selected_text="x",
        start_line=544, end_line=545, chip="free", user_text="", shelf=huge,
    )
    assert len(huge) > tutor.SHELF_CHARS
    assert "[工作目录里的其他教材]" not in packet
