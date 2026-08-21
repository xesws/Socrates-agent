"""会话按时间清理。读者的电脑不该被几千个空会话撑爆。

实测：`.pen/sessions/` 攒到 3389 个文件 / 10.4 MB，其中 **3371 个是空的**——
每划一次词就建一场，大部分没等到第一句话就被下一次划词换掉了。此前**完全没有**
任何清理机制：快照有 SNAPSHOT_KEEP、深挖配额按小时窗口滚、写回提案是内存的，
只有会话是纯 append，进程活多久涨多久。

三条规则（读者拍板的两档 + 一条硬保护）：

| 类型            | 判据                 | 保留   |
| --------------- | -------------------- | ------ |
| 空会话          | `len(messages) <= 1` | 1 天   |
| 聊过的          | 其余                 | 7 天   |
| 挂着 pending 的 | `pending.id` 非空    | 永不删 |

`pending` 是「读者点了写回、审批弹窗还没结果」的状态。那一场删掉，读者点
「同意」就会撞 404，手里已生成的提案当场作废——不管它多老都不碰。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pen import config, probe_store, session as sessionmod

_DAY = 86400.0


def _is_empty(data: dict) -> bool:
    """只有 system prompt = 从没说过话。

    `PenSession.__post_init__` 保证 messages 至少有那一条，所以判据是 `<= 1`
    而不是 `== 0`。
    """
    msgs = data.get("messages")
    return not isinstance(msgs, list) or len(msgs) <= 1


def _has_pending(data: dict) -> bool:
    pending = data.get("pending")
    return isinstance(pending, dict) and bool(pending.get("id"))


def _keep_seconds(path: Path) -> float:
    """这个文件该留多久（秒）。返回 `inf` 表示永不删。

    读不出来的（截断、手改坏了、正在写）按**长**的那档算：宁可多留 7 天，
    也不要因为一次解析失败就把读者聊过的东西删了。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return config.SESSION_KEEP_DAYS_CHATTED * _DAY
    if not isinstance(data, dict):
        return config.SESSION_KEEP_DAYS_CHATTED * _DAY
    if _has_pending(data):
        return float("inf")
    days = (
        config.SESSION_KEEP_DAYS_EMPTY
        if _is_empty(data)
        else config.SESSION_KEEP_DAYS_CHATTED
    )
    return float(days) * _DAY


def purge_expired_sessions(*, now: float | None = None) -> dict[str, int]:
    """扫一遍会话目录，删过期的。返回 `{"scanned", "removed"}`。

    **绝不抛异常。** 它挂在 lifespan 上，也挂在插件 onload 打的那个端点上——
    目录不存在、文件被并发删、权限不对，任何一样都不该让 sidecar 起不来。

    glob 用 `*.json` **不要用 `*.json*`**：`_atomic_write` 的临时文件叫
    `<sid>.json.<pid>.<hex>.tmp`，后者会命中另一个进程**正在写**的那个。
    """
    stamp = time.time() if now is None else now
    scanned = removed = 0
    try:
        files = sorted(sessionmod.sessions_dir().glob("*.json"))
    except OSError:
        return {"scanned": 0, "removed": 0}
    for f in files:
        scanned += 1
        try:
            age = stamp - f.stat().st_mtime
        except OSError:
            continue  # 被别人删掉了，正好
        if age <= _keep_seconds(f):
            continue
        sid = f.stem
        try:
            f.unlink(missing_ok=True)
        except OSError:
            continue
        removed += 1
        # 成对删。深挖账本是懒创建的子集（实测 3389 ↔ 3），missing_ok 兜住。
        # **只删这一个**——`.pen/probes/<hid>/quota.json` 是按 handbook 索引的
        # 每小时配额，和会话没关系，碰它等于把成本闸门清零。
        try:
            (probe_store.probes_dir() / f"{sid}.json").unlink(missing_ok=True)
        except OSError:
            pass
    return {"scanned": scanned, "removed": removed}


def touch(session_id: str) -> None:
    """把 mtime 推到现在，语义从「最后写过」改成「最后碰过」。

    为什么需要：`GET /v1/sessions/{sid}`、`POST /v1/sessions` 的恢复分支、
    `GET /deep`、`retarget`、`apply` **全都不 save**。读者天天开面板、每次划词
    都命中并复用同一个会话，只要不真发一轮对话，mtime 就冻在最后一次 chat 上——
    7 天不聊天就被清掉，下次划词静默换成新会话，历史全丢。

    静默失败：它只是让清理更保守，不该成为读会话的失败原因。
    """
    try:
        path = sessionmod.sessions_dir() / f"{session_id}.json"
        if path.is_file():
            path.touch()
    except (OSError, ValueError):
        pass
