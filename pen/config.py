"""路径与环境。默认 DeepSeek（OPENAI_* 或 DEEPSEEK_API_KEY）。不猜 Kimi。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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


THINKING_LEVELS = ("off", "low", "medium", "high")

# ── 异步深挖（v0.8.1）──────────────────────────────────────────
# 总开关。PEN_PROBE=off 一刀关掉后台探索。
ENV_PROBE = "PEN_PROBE"
# 「实质回复」的门槛，和 tutor._finish_text 里 has_substantive 的判据保持一致。
PROBE_MIN_REPLY_CHARS = 80
# 每次探索的硬上限。不给模型 tools，读取由 Python 执行——
# 广度必须由代码封死，后台任务自主循环烧钱是看不见的。
# 调用次数不设常量：explore() 的结构就是「一次，需要正文时再一次」，
# 摆一个 PROBE_MAX_CALLS 在这里而代码不读它，改的人会以为改了有用。
# 上限由 test_probe.py 的两条断言锁住。
PROBE_MAX_READS = 2
PROBE_READ_LINES = 80
# 后台任务，没人在等它，超时给宽一点。30 秒实测不够：单次输入约 6k token，
# 模型还要吐一段 JSON，真跑时直接 APITimeoutError，探索会静默失败，
# 读者永远看不到深题。主对话那条是 120 秒。
PROBE_TIMEOUT = 90.0
# 配额。读者选的是「每轮实质回复都探」，所以不设轮次冷却，
# 这几个只是失控保护，不是降频手段。
PROBE_MAX_PER_SESSION = 8
PROBE_MAX_PER_DAY = 40
PROBE_CONCURRENCY = 2
# 手里还有这么多没抛出去的好问题时就别再探了——省的不是频率，是浪费。
PROBE_PENDING_CAP = 3


def probe_enabled(env_file: Path | None = None) -> bool:
    raw = (os.environ.get(ENV_PROBE) or parse_dotenv(env_file).get(ENV_PROBE) or "").strip().lower()
    return raw not in ("off", "0", "false", "no")


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    key_source: str
    thinking: str = "off"


def apply_pen_home(env_file: Path | None = None) -> Path:
    """若设置 PEN_HOME，把 PEN_DIR / LIBRARIES_DIR 挪到该目录。测试仍可 monkeypatch PEN_DIR。"""
    global PEN_DIR, LIBRARIES_DIR
    file_vals = parse_dotenv(env_file)
    raw = (os.environ.get(ENV_PEN_HOME) or file_vals.get(ENV_PEN_HOME) or "").strip()
    if raw:
        PEN_DIR = Path(raw).expanduser().resolve()
        LIBRARIES_DIR = PEN_DIR / "libraries"
    return PEN_DIR


def handbook_allow_roots(
    env_file: Path | None = None,
    extra_roots: list[Path] | None = None,
) -> list[Path]:
    """登记/写回教材必须落在这些根之下。默认仓库根；env 与请求体 extra_roots 追加。"""
    roots = [REPO_ROOT.resolve()]
    file_vals = parse_dotenv(env_file)
    raw = (os.environ.get(ENV_ALLOW_ROOTS) or file_vals.get(ENV_ALLOW_ROOTS) or "").strip()
    for part in raw.split(os.pathsep):
        piece = part.strip()
        if not piece:
            continue
        roots.append(Path(piece).expanduser().resolve())
    if extra_roots:
        for piece in extra_roots:
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
    return LLMConfig(base_url=base, api_key=key, model=model, key_source=source, thinking="off")


def normalize_thinking(raw: str | None) -> str:
    got = (raw or "").strip().lower()
    return got if got in THINKING_LEVELS else "off"


def _host_of(url: str) -> str:
    """小写 netloc，剥掉 userinfo。钥匙不跨主机挪用。"""
    return urlparse(url).netloc.lower().rsplit("@", 1)[-1]


def merge_llm(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    env_file: Path | None = None,
) -> LLMConfig | None:
    """请求体非空字段优先，缺的回退 resolve_llm()。两边都没有 key → None。
    请求把 base_url 换到别的主机却没自带 key → 不挪用 env 钥匙，返回 None，
    让设置页自己填那台主机的 key。"""
    env = resolve_llm(env_file)
    req_key = (api_key or "").strip()
    req_url = (base_url or "").strip()
    if req_key:
        key = req_key
        source = "settings"
    elif env and req_url and _host_of(req_url) != _host_of(env.base_url):
        return None
    elif env:
        key = env.api_key
        source = env.key_source
    else:
        return None
    url = req_url or (env.base_url if env else DEEPSEEK_BASE)
    name = (model or "").strip() or (env.model if env else DEEPSEEK_MODEL)
    return LLMConfig(
        base_url=url,
        api_key=key,
        model=name,
        key_source=source,
        thinking=normalize_thinking(thinking),
    )


def openai_config() -> tuple[str | None, str | None, str | None]:
    cfg = resolve_llm()
    if cfg is None:
        return None, None, None
    return cfg.base_url, cfg.api_key, cfg.model


def llm_public_status() -> dict[str, str | bool]:
    """给前端看的配置摘要，不含密钥。"""
    cfg = resolve_llm()
    if cfg is None:
        return {
            "ok": False,
            "base_url": "",
            "model": "",
            "key_source": "",
            "thinking": "off",
        }
    return {
        "ok": True,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "key_source": cfg.key_source,
        "thinking": cfg.thinking,
    }


apply_pen_home()
