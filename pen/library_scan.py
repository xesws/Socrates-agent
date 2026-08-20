"""工作目录里还有哪些教材。只给标题和大纲，不给正文。

隐私边界：**只看已登记的手册**，而且路径必须落在允许根之内。

第一版还扫「当前手册同目录的 .md 兄弟」，那是错的——教材在真实 vault 里通常
就跟私人笔记躺在同一个文件夹，实测送出去过《2026 年 8 月的复盘 / 和 A 公司的
合同条款》这种标题。「已登记」意味着读者确实用点读笔打开过它，那才是教材。
allow_roots 那道过滤也是必须的：登记表里躺着不少指向 /private/var/folders 的
pytest 临时夹具，其中一本还长得像手册（有 Level 0 / 第三拍），模型会当真书去搭桥。

不复用 outline.file_outline：它为写回规划而写，要读整个文件算 end_line；
这里只要读到前 400 行或凑够 8 条标题就停。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

MAX_FILES = 8
MAX_HEADINGS = 8
MAX_SCAN_LINES = 400
MAX_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules", ".pen"}

_H = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_CACHE: dict[str, tuple[float, str]] = {}
_TTL = 60.0
# sidecar 长期开着，读者会打开很多篇笔记，每篇一条缓存。数目不大但也不该无界。
_CACHE_MAX = 64


def _digest(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
    except OSError:
        return None
    title = path.stem
    heads: list[str] = []
    in_fence = False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh):
                if i >= MAX_SCAN_LINES or len(heads) >= MAX_HEADINGS:
                    break
                line = raw.rstrip("\n")
                if line.startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                m = _H.match(line)
                if m:
                    heads.append(m.group(2))
    except OSError:
        return None
    if heads:
        title = heads[0]
    return {"title": title, "path": str(path), "headings": heads}


def _within_allowed(path: Path, roots: list[Path]) -> bool:
    try:
        got = path.expanduser().resolve()
    except OSError:
        return False
    for root in roots:
        try:
            r = root.expanduser().resolve()
        except OSError:
            continue
        if got == r or r in got.parents:
            return True
    return False


def shelf_digest(
    current_path: Path,
    registered: list[str] | None = None,
    allow_roots: list[Path] | None = None,
) -> str:
    """给 probe prompt 的一段。只有一本书时返回空串——
    让模型硬编不存在的跨书联系比不提这一段更糟。"""
    key = str(current_path)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]

    from pen.config import handbook_allow_roots

    roots = allow_roots if allow_roots is not None else handbook_allow_roots()
    try:
        seen: set[Path] = {current_path.resolve()}
    except OSError:
        seen = set()
    # 光按路径去重不够：同一本书常常在仓库根和 vault 里各有一份拷贝，
    # 路径不同但内容同源，列两遍会让模型以为书架上真有两本。
    cur_digest = _digest(current_path)
    titles: set[str] = {cur_digest["title"]} if cur_digest else set()
    picked: list[dict[str, Any]] = []
    for raw in registered or []:
        if len(picked) >= MAX_FILES:
            break
        p = Path(raw)
        try:
            r = p.resolve()
        except OSError:
            continue
        # 登记表里躺着不少死记录（指向已删除的临时目录）和 pytest 夹具
        # （/private/var/folders 下、系统还没清理掉的），两道都要挡
        if r in seen or not p.is_file() or not _within_allowed(p, roots):
            continue
        seen.add(r)
        d = _digest(p)
        if d and d["title"] not in titles:
            titles.add(d["title"])
            picked.append(d)

    if not picked:
        _remember(key, now, "")
        return ""
    rows = []
    for d in picked:
        heads = " / ".join(d["headings"][:5])
        rows.append(f"- 《{d['title']}》：{heads or '（没有标题）'}")
    text = "\n".join(rows)
    _remember(key, now, text)
    return text


def _remember(key: str, now: float, text: str) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (now, text)
