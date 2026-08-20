"""写权限只允许一本手册的 original_path；读可放宽到同仓库白名单。"""

from __future__ import annotations

import re
from pathlib import Path

from pen.config import handbook_allow_roots

HANDBOOK_SUFFIXES = {".md", ".markdown"}

_WRAP = re.compile(r"^[`'\"](.*)[`'\"]$")
_LINE_SUFFIX = re.compile(r":\d+(-\d+)?$")


class SandboxError(PermissionError):
    """路径越权。"""


def _unwrap(s: str) -> str:
    m = _WRAP.match(s)
    return m.group(1).strip() if m else s


def normalize_model_path(raw: str | Path) -> str:
    """剥模型常加的引号、反引号、:行号。空串表示「就读原文」。"""
    s = _unwrap(str(raw).strip())
    s = _LINE_SUFFIX.sub("", s)
    return _unwrap(s).strip()


def resolve_read_target(original_path: Path, target: str | Path) -> Path:
    """
    相对路径锚在手册所在目录，不看进程 cwd。
    空 / 空白 → 原文自己。
    """
    allowed = original_path.expanduser().resolve()
    raw = normalize_model_path(target)
    if not raw:
        return allowed
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = allowed.parent / p
    return p.resolve()


def resolve_existing_or_new(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def parse_vault_root(raw: str | None) -> list[Path]:
    """请求体带来的库根。空 → 不加。拒绝文件系统根与非目录。"""
    if raw is None or not str(raw).strip():
        return []
    p = Path(str(raw).strip()).expanduser().resolve()
    if p == p.parent:
        raise SandboxError("vault_root 不能是文件系统根")
    if not p.is_dir():
        raise SandboxError(f"vault_root 不是目录：{p}")
    return [p]


def assert_handbook_path(
    path: str | Path,
    extra_roots: list[Path] | None = None,
) -> Path:
    """登记和写回共用：必须是允许根下的 Markdown，且不是 .env / .git。"""
    got = Path(path).expanduser().resolve()
    if got.name == ".env" or ".git" in got.parts:
        raise SandboxError(f"拒绝受保护路径：{got}")
    if got.suffix.lower() not in HANDBOOK_SUFFIXES:
        raise SandboxError(f"只接受 Markdown 教材（.md / .markdown）：{got}")
    roots = handbook_allow_roots(extra_roots=extra_roots)
    if not any(got == root or root in got.parents for root in roots):
        raise SandboxError(f"教材不在允许的根内：{got}")
    return got


def assert_write_target(original_path: Path, target: str | Path) -> Path:
    """写 / 回退 / commit 的目标必须就是登记的原文。"""
    allowed = original_path.expanduser().resolve()
    got = Path(target).expanduser().resolve()
    if got != allowed:
        raise SandboxError(f"拒绝写入 {got}：本手册只能改 {allowed}")
    if ".git" in got.parts or got.name == ".env":
        raise SandboxError(f"拒绝写入受保护路径：{got}")
    return got


def assert_readable(
    original_path: Path,
    target: str | Path,
    *,
    extra_roots: list[Path] | None = None,
) -> Path:
    """
    读：原文本身，或 extra_roots 之下（默认原文所在目录，用于对照 lab/）。
    相对路径相对手册目录解析。禁止 .env / .git。
    """
    allowed_file = original_path.expanduser().resolve()
    got = resolve_read_target(original_path, target)
    if got.name == ".env" or ".git" in got.parts:
        raise SandboxError(f"拒绝读取受保护路径：{got}")
    if got == allowed_file:
        return got
    roots = extra_roots if extra_roots is not None else [allowed_file.parent]
    for root in roots:
        root_r = root.expanduser().resolve()
        if got == root_r or root_r in got.parents:
            return got
    raise SandboxError(f"拒绝读取 {got}：不在本手册允许的根内")
