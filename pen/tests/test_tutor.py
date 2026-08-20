from __future__ import annotations

from pathlib import Path

from pen.config import LLMConfig
from pen.session import PenSession
from pen.tutor import llm_create_kwargs, stream_chat, usage_snapshot


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
