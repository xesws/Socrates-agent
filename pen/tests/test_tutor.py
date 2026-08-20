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
