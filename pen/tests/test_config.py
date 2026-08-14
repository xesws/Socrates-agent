from __future__ import annotations

from pathlib import Path

from pen.config import parse_dotenv, resolve_llm


def test_parse_strips_inline_comment(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("DEEPSEEK_API_KEY=sk-test # comment\n", encoding="utf-8")
    assert parse_dotenv(p)["DEEPSEEK_API_KEY"] == "sk-test"


def test_deepseek_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    p = tmp_path / ".env"
    p.write_text("DEEPSEEK_API_KEY=sk-ds-demo\n", encoding="utf-8")
    cfg = resolve_llm(p)
    assert cfg is not None
    assert cfg.key_source == "DEEPSEEK_API_KEY"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.api_key == "sk-ds-demo"


def test_openai_triplet_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setenv("MODEL_NAME", "deepseek-v4-flash")
    p = tmp_path / ".env"
    p.write_text("DEEPSEEK_API_KEY=sk-ignored\n", encoding="utf-8")
    cfg = resolve_llm(p)
    assert cfg is not None
    assert cfg.api_key == "sk-from-env"
    assert cfg.key_source == "OPENAI_API_KEY"
    assert cfg.model == "deepseek-v4-flash"


def test_kimi_key_alone_is_not_used(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    p = tmp_path / ".env"
    p.write_text("KIMI_API_KEY=sk-kimi-not-this\n", encoding="utf-8")
    assert resolve_llm(p) is None
