"""路径与环境。默认 DeepSeek（OPENAI_* 或 DEEPSEEK_API_KEY）。不猜 Kimi。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
PEN_DIR: Path = REPO_ROOT / ".pen"
LIBRARIES_DIR: Path = PEN_DIR / "libraries"
DEFAULT_HANDBOOK_ID = "swe-agent-v2"
DEFAULT_HANDBOOK: Path = REPO_ROOT / "SWE-Agent通关手册v2.md"

MAX_OUTPUT = 5000
NEIGHBORHOOD_CHARS = 4000
SNAPSHOT_KEEP = 20

ENV_BASE_URL = "OPENAI_BASE_URL"
ENV_API_KEY = "OPENAI_API_KEY"
ENV_MODEL = "MODEL_NAME"
ENV_ALLOW_ROOTS = "PEN_ALLOW_ROOTS"
ENV_PEN_HOME = "PEN_HOME"

# 钥匙只认 lab 三件套，以及 DeepSeek 自己的名字。不把 KIMI_API_KEY 当成默认供应商。
_KEY_ALIASES = ("OPENAI_API_KEY", "DEEPSEEK_API_KEY")
DEEPSEEK_BASE = "https://api.deepseek.com"
# 与 lab/level2 README、Socrates-agent 注释一致
DEEPSEEK_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    key_source: str


def apply_pen_home(env_file: Path | None = None) -> Path:
    """若设置 PEN_HOME，把 PEN_DIR / LIBRARIES_DIR 挪到该目录。测试仍可 monkeypatch PEN_DIR。"""
    global PEN_DIR, LIBRARIES_DIR
    file_vals = parse_dotenv(env_file)
    raw = (os.environ.get(ENV_PEN_HOME) or file_vals.get(ENV_PEN_HOME) or "").strip()
    if raw:
        PEN_DIR = Path(raw).expanduser().resolve()
        LIBRARIES_DIR = PEN_DIR / "libraries"
    return PEN_DIR


def handbook_allow_roots(env_file: Path | None = None) -> list[Path]:
    """登记/写回教材必须落在这些根之下。默认只有仓库根；PEN_ALLOW_ROOTS 追加。"""
    roots = [REPO_ROOT.resolve()]
    file_vals = parse_dotenv(env_file)
    raw = (os.environ.get(ENV_ALLOW_ROOTS) or file_vals.get(ENV_ALLOW_ROOTS) or "").strip()
    for part in raw.split(os.pathsep):
        piece = part.strip()
        if not piece:
            continue
        roots.append(Path(piece).expanduser().resolve())
    return roots


def ensure_pen_dirs() -> None:
    LIBRARIES_DIR.mkdir(parents=True, exist_ok=True)
    (PEN_DIR / "trajectories").mkdir(parents=True, exist_ok=True)
    (PEN_DIR / "sessions").mkdir(parents=True, exist_ok=True)
    (PEN_DIR / "proposals").mkdir(parents=True, exist_ok=True)


def parse_dotenv(path: Path | None = None) -> dict[str, str]:
    dest = path or (REPO_ROOT / ".env")
    out: dict[str, str] = {}
    if not dest.is_file():
        return out
    for raw in dest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        else:
            value = value.split("#", 1)[0].strip()
        if key:
            out[key] = value
    return out


def _get(name: str, file_vals: dict[str, str]) -> str:
    return (os.environ.get(name) or file_vals.get(name) or "").strip()


def resolve_llm(env_file: Path | None = None) -> LLMConfig | None:
    file_vals = parse_dotenv(env_file)
    key = ""
    source = ""
    for name in _KEY_ALIASES:
        val = _get(name, file_vals)
        if val:
            key = val
            source = name
            break
    if not key:
        return None

    base = _get(ENV_BASE_URL, file_vals)
    model = _get(ENV_MODEL, file_vals)
    if not base:
        base = DEEPSEEK_BASE
    if not model:
        model = DEEPSEEK_MODEL
    return LLMConfig(base_url=base, api_key=key, model=model, key_source=source)


def openai_config() -> tuple[str | None, str | None, str | None]:
    cfg = resolve_llm()
    if cfg is None:
        return None, None, None
    return cfg.base_url, cfg.api_key, cfg.model


def llm_public_status() -> dict[str, str | bool]:
    """给前端看的配置摘要，不含密钥。"""
    cfg = resolve_llm()
    if cfg is None:
        return {"ok": False, "base_url": "", "model": "", "key_source": ""}
    return {
        "ok": True,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "key_source": cfg.key_source,
    }


apply_pen_home()
