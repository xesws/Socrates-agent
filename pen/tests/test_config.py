from __future__ import annotations

from pathlib import Path

from pen import config
from pen.config import merge_llm, parse_dotenv, resolve_llm


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


def test_pen_home_moves_pen_dir(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "pen-home"
    dest.mkdir()
    monkeypatch.setenv("PEN_HOME", str(dest))
    got = config.apply_pen_home(tmp_path / "missing.env")
    assert got == dest.resolve()
    assert config.PEN_DIR == dest.resolve()
    assert config.LIBRARIES_DIR == dest.resolve() / "libraries"
    monkeypatch.delenv("PEN_HOME", raising=False)
    config.PEN_DIR = config.REPO_ROOT / ".pen"
    config.LIBRARIES_DIR = config.PEN_DIR / "libraries"


def test_merge_llm_request_wins_and_key_alone_works(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    assert merge_llm(env_file=empty) is None
    only_req = merge_llm(
        api_key="sk-from-settings",
        base_url="https://api.openai.com/v1",
        model="gpt-4.1-mini",
        thinking="high",
        env_file=empty,
    )
    assert only_req is not None
    assert only_req.api_key == "sk-from-settings"
    assert only_req.base_url == "https://api.openai.com/v1"
    assert only_req.model == "gpt-4.1-mini"
    assert only_req.thinking == "high"
    assert only_req.key_source == "settings"

    envf = tmp_path / ".env"
    envf.write_text("DEEPSEEK_API_KEY=sk-env\n", encoding="utf-8")
    mixed = merge_llm(model="override-model", thinking="nope", env_file=envf)
    assert mixed is not None
    assert mixed.api_key == "sk-env"
    assert mixed.model == "override-model"
    assert mixed.thinking == "off"
    assert mixed.key_source == "DEEPSEEK_API_KEY"
    assert mixed.base_url == "https://api.deepseek.com"


def _clear_llm_env(monkeypatch) -> None:
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)


def test_merge_llm_same_host_model_override_keeps_env_key(tmp_path: Path, monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text("DEEPSEEK_API_KEY=sk-env\n", encoding="utf-8")
    cfg = merge_llm(
        base_url="https://api.deepseek.com",
        model="deepseek-reasoner",
        env_file=envf,
    )
    assert cfg is not None
    assert cfg.api_key == "sk-env"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.model == "deepseek-reasoner"
    assert cfg.key_source == "DEEPSEEK_API_KEY"
    with_userinfo = merge_llm(
        base_url="https://user:pass@api.deepseek.com/v1",
        env_file=envf,
    )
    assert with_userinfo is not None
    assert with_userinfo.api_key == "sk-env"


def test_merge_llm_different_host_without_request_key_returns_none(tmp_path: Path, monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text("DEEPSEEK_API_KEY=sk-env\n", encoding="utf-8")
    assert merge_llm(base_url="https://api.openai.com/v1", env_file=envf) is None


def test_merge_llm_different_host_with_request_key_uses_request_key(tmp_path: Path, monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text("DEEPSEEK_API_KEY=sk-env\n", encoding="utf-8")
    cfg = merge_llm(
        api_key="sk-from-page",
        base_url="https://api.openai.com/v1",
        env_file=envf,
    )
    assert cfg is not None
    assert cfg.api_key == "sk-from-page"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.key_source == "settings"
