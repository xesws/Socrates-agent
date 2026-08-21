"""工作目录里还有哪些教材。只给标题和大纲，不给正文。

隐私边界：**只看已登记的手册**，而且路径必须落在允许根之内。

第一版还扫「当前手册同目录的 .md 兄弟」，那是错的——教材在真实 vault 里通常
就跟私人笔记躺在同一个文件夹，实测送出去过《2026 年 8 月的复盘 / 和 A 公司的
合同条款》这种标题。「已登记」意味着读者确实用点读笔打开过它，那才是教材。
allow_roots 那道过滤也是必须的：登记表里躺着不少指向 /private/var/folders 的
pytest 临时夹具，其中一本还长得像手册（有 Level 0 / 第三拍），模型会当真书去搭桥。

不复用 outline.file_outline：它为写回规划而写，要读整个文件算 end_line；
这里只要读到前 400 行或凑够 8 条标题就停。

本文件一律 `except Exception` 而不是 `except OSError`：登记表里什么脏数据都有，
而 `Path.resolve()` 对含 \x00 的路径抛 ValueError、对 symlink 环抛 RuntimeError，
两个都不是 OSError。一条坏记录逃出去就掀掉整张书架，调用方只看得到一个空串，
症状和「没有别的书」一模一样，没法从现象倒推。
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
    except Exception:
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
    except Exception:
        return None
    if heads:
        title = heads[0]
    return {"title": title, "path": str(path), "headings": heads}


def _prefer_nearby(
    registered: list[str],
    current_path: Path | None,
    roots: list[Path] | None = None,
) -> list[str]:
    """同一本书常在仓库根和 vault 里各有一份，标题一模一样，去重只会留先遇到的那份。
    实测留下的是仓库根那份（8-13），而读者眼前编辑的是 vault 那份（8-17）——
    师傅照着旧版讲，还讲得理直气壮。

    「近」按**读取根**算，不按当前手册的父目录：手册放在 `vault/level0/` 这种子目录
    里时，`vault/` 根下的兄弟书就不算同一棵树了，于是退到 mtime 比大小——仓库那份
    被 git pull 刷新过就会赢，又绕回原来那个 bug。
    用 stat 而不是 meta.json 里的 mtime：那是登记那一刻的快照，写回之后就过期了。
    """
    bases: list[Path] = []
    for r in roots or []:
        try:
            bases.append(Path(r).expanduser().resolve())
        except Exception:
            continue
    if not bases and current_path is not None:
        try:
            bases.append(current_path.expanduser().resolve().parent)
        except Exception:
            pass

    def key(raw: str) -> tuple[int, float]:
        try:
            r = Path(raw).expanduser().resolve()
            near = 0 if any(r == b or b in r.parents for b in bases) else 1
            return (near, -r.stat().st_mtime)
        except Exception:
            # OSError 不够：resolve() 对含 \x00 的路径抛 ValueError，对 symlink 环
            # 抛 RuntimeError。key 抛异常会掀掉整个 sorted()，一条坏登记记录
            # 就让整张书架消失——而调用方只看得到一个空字符串。
            return (2, 0.0)

    return sorted(registered, key=key)


def _within_allowed(path: Path, roots: list[Path]) -> bool:
    try:
        got = path.expanduser().resolve()
    except Exception:
        return False
    for root in roots:
        try:
            r = root.expanduser().resolve()
        except Exception:
            continue
        if got == r or r in got.parents:
            return True
    return False


def shelf_digest(
    current_path: Path,
    registered: list[str] | None = None,
    allow_roots: list[Path] | None = None,
    *,
    with_paths: bool = False,
) -> str:
    """工作目录里还有哪些教材。只有一本书时返回空串——
    让模型硬编不存在的跨书联系比不提这一段更糟。

    `allow_roots` 必须传 **read_file 实际认的根**（`sandbox.reading_roots()`），
    不能传全局 `handbook_allow_roots()`：后者宽得多，会印出师傅读不到的路径，
    师傅照着去读就撞在「不在本手册允许的根内」上。

    `with_paths` 给**实时对话**用：师傅手上有 read_file，沙箱也放行同一个 vault，
    但光给书名它读不了——只会去猜文件名，猜错就退回「你把路径给我」。
    probe 那条线不传：它靠 `book` 字段点书名、`_shelf_paths()` 反查，不需要路径，
    而它的 system 尾部是前缀缓存区，格式一动重复探索的边际成本就从 1/5 涨回 1。
    """
    # 缓存键必须带上**全部**入参，不只是 current_path：
    # · with_paths——两种格式共用一个键，先跑的会把自己的格式喂给后来者，
    #   症状是「有时能读有时读不了」。
    # · registered——读者刚在 Obsidian 里打开另一本书（登记），60 秒内书架里
    #   看不见它，师傅照旧答「我没读到」。要修的那个症状原样复发。
    # · allow_roots——实时层传 vault 根、probe 传 REPO_ROOT，同一本书两条线
    #   的结果本来就该不同，共用一个键就是互相投毒。
    from pen.config import handbook_allow_roots

    key = "\x00".join(
        [
            str(current_path),
            str(int(with_paths)),
            "*" if allow_roots is None else "|".join(sorted(str(r) for r in allow_roots)),
            "|".join(registered or []),
        ]
    )
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]

    roots = allow_roots if allow_roots is not None else handbook_allow_roots()
    try:
        seen: set[Path] = {current_path.resolve()}
    except Exception:
        seen = set()
    # 光按路径去重不够：同一本书常常在仓库根和 vault 里各有一份拷贝，
    # 路径不同但内容同源，列两遍会让模型以为书架上真有两本。
    cur_digest = _digest(current_path)
    titles: set[str] = {cur_digest["title"]} if cur_digest else set()
    picked: list[dict[str, Any]] = []
    for raw in _prefer_nearby(registered or [], current_path, roots):
        if len(picked) >= MAX_FILES:
            break
        p = Path(raw)
        try:
            r = p.resolve()
        except Exception:
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
        if with_paths:
            rows.append(f"- 《{d['title']}》  path: {d['path']}")
            rows.append(f"  大纲：{heads or '（没有标题）'}")
        else:
            rows.append(f"- 《{d['title']}》：{heads or '（没有标题）'}")
    text = "\n".join(rows)
    _remember(key, now, text)
    return text


def _remember(key: str, now: float, text: str) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (now, text)
