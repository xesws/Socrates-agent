"""深挖问题的池子与投递游标。落在 .pen/probes/sessions/。

这里的所有写入都发生在**后台探索线程**里，所以它刻意不碰 PenSession——
那个对象由请求线程独占，两边都 save 会抢同一个 to_dict() 快照，后写的赢，
丢掉的是一整轮对话。深题只进这个 store，要拼给前端时在 app 层现拼。

投递语义是「至少一次」：服务端返回 seq > since 的全部成熟项，前端把 since
推到 max(seq)。丢一个响应不会丢问题，重复请求同一个 since 返回同样的东西。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pen import config
from pen.questions import normalize_qkey

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

# 一条 later 问题等多少轮还没成熟就丢掉。真实会话 5~15 轮，
# 太短则总也等不到，太长则读者早走远了。
ITEM_TTL_TURNS = 6
# 进程被杀时 running 会留在盘上。不回收的话 try_claim 永远抢不到坑，
# 前端也会对着一个永远不会完成的幽灵轮询到超时。
ORPHAN_AFTER_SECONDS = 300.0
# 一次最多放出几条，以及同时可见几条。深题是「跳出来」的，多了就成噪音。
MAX_RELEASE_PER_TURN = 1
MAX_VISIBLE = 2

_LOCK = threading.RLock()


def probes_dir() -> Path:
    dest = config.PEN_DIR / "probes" / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _path(session_id: str) -> Path:
    if not _SAFE_ID.match(session_id):
        raise ValueError(f"非法 session_id：{session_id!r}")
    return probes_dir() / f"{session_id}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeepQuestion:
    id: str
    text: str
    why: str = ""
    axis: str = ""
    grounding: str = "book"          # book | open
    anchors: list[dict[str, Any]] = field(default_factory=list)
    timing: str = "later"            # now | later（确定性闸门只能把 now 降成 later）
    target: str = ""                 # later 时挂在哪一关
    depth: int = 0
    atom: str = ""                   # 探索那一刻的 atom_key
    born_round: int = 0
    seq: int = 0
    state: str = "pending"           # pending | shown | clicked | dropped
    created_at: str = field(default_factory=_now)

    def to_chip(self) -> dict[str, Any]:
        return {"id": self.id, "kind": "deep", "text": self.text, "why": self.why}


@dataclass
class SessionLedger:
    session_id: str
    handbook_id: str = ""
    seq: int = 0
    probe_calls: int = 0
    last_probe_round: int = -99
    pool: list[DeepQuestion] = field(default_factory=list)
    running: list[str] = field(default_factory=list)
    running_since: str = ""
    asked_qkeys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "handbook_id": self.handbook_id,
            "seq": self.seq,
            "probe_calls": self.probe_calls,
            "last_probe_round": self.last_probe_round,
            "pool": [asdict(q) for q in self.pool],
            "running": list(self.running),
            "running_since": self.running_since,
            "asked_qkeys": list(self.asked_qkeys)[-60:],
            "updated_at": _now(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SessionLedger:
        pool = []
        for q in raw.get("pool") or []:
            if not isinstance(q, dict):
                continue
            known = {k: v for k, v in q.items() if k in DeepQuestion.__dataclass_fields__}
            try:
                pool.append(DeepQuestion(**known))
            except TypeError:
                continue
        return cls(
            session_id=str(raw.get("session_id") or ""),
            handbook_id=str(raw.get("handbook_id") or ""),
            seq=int(raw.get("seq") or 0),
            probe_calls=int(raw.get("probe_calls") or 0),
            last_probe_round=int(raw.get("last_probe_round") or -99),
            pool=pool,
            running=[str(x) for x in raw.get("running") or []],
            running_since=str(raw.get("running_since") or ""),
            asked_qkeys=[str(x) for x in raw.get("asked_qkeys") or []],
        )

    def pending_count(self) -> int:
        return sum(1 for q in self.pool if q.state == "pending")

    def visible_count(self) -> int:
        return sum(1 for q in self.pool if q.state in ("shown", "clicked"))


def load(session_id: str, handbook_id: str = "") -> SessionLedger:
    try:
        dest = _path(session_id)
    except ValueError:
        return SessionLedger(session_id=session_id, handbook_id=handbook_id)
    if not dest.is_file():
        return SessionLedger(session_id=session_id, handbook_id=handbook_id)
    try:
        raw = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SessionLedger(session_id=session_id, handbook_id=handbook_id)
    if not isinstance(raw, dict):
        return SessionLedger(session_id=session_id, handbook_id=handbook_id)
    led = SessionLedger.from_dict(raw)
    led.session_id = session_id
    if handbook_id:
        led.handbook_id = handbook_id
    _reap_orphan(led)
    return led


def _reap_orphan(led: SessionLedger) -> None:
    """就地清掉进程被杀时留下的 running。不清的话前端会白轮询到超时。"""
    if not led.running or not led.running_since:
        return
    try:
        started = datetime.fromisoformat(led.running_since)
    except ValueError:
        led.running = []
        led.running_since = ""
        return
    if (datetime.now(timezone.utc) - started).total_seconds() > ORPHAN_AFTER_SECONDS:
        led.running = []
        led.running_since = ""


def save(led: SessionLedger) -> None:
    config.ensure_pen_dirs()
    _atomic_write(_path(led.session_id), json.dumps(led.to_dict(), ensure_ascii=False))


def try_claim(session_id: str, handbook_id: str, now_round: int) -> str | None:
    """占坑。同一会话同时只允许一个 probe 在跑——抢不到就跳过，不排队：
    排队意味着上下文已经过期了还要再花一次钱。"""
    with _LOCK:
        led = load(session_id, handbook_id)
        if led.running:
            return None
        pid = uuid.uuid4().hex[:12]
        led.running = [pid]
        led.running_since = _now()
        led.probe_calls += 1
        led.last_probe_round = now_round
        save(led)
        return pid


def release(session_id: str, probe_id: str) -> None:
    with _LOCK:
        led = load(session_id)
        led.running = [x for x in led.running if x != probe_id]
        if not led.running:
            led.running_since = ""
        save(led)


def add_questions(session_id: str, probe_id: str, items: list[DeepQuestion]) -> None:
    """探索线程唯一的写入口。"""
    with _LOCK:
        led = load(session_id)
        known = {normalize_qkey(q.text) for q in led.pool} | set(led.asked_qkeys)
        for q in items:
            key = normalize_qkey(q.text)
            if not key or key in known:
                continue
            known.add(key)
            led.seq += 1
            q.seq = led.seq
            led.pool.append(q)
        led.running = [x for x in led.running if x != probe_id]
        if not led.running:
            led.running_since = ""
        save(led)


def budget(session_id: str) -> dict[str, int]:
    led = load(session_id)
    return {"used": led.probe_calls, "max": config.PROBE_MAX_PER_SESSION}


def _ripe(q: DeepQuestion, *, atom: str, level: str, now_round: int) -> bool:
    """成熟度闸门。纯确定性，零 LLM。"""
    if q.state != "pending":
        return False
    if now_round - q.born_round > ITEM_TTL_TURNS:
        q.state = "dropped"
        return False
    if q.timing == "now":
        # 读者还在生它的那块地上，或者就是刚生出来的这一轮
        return q.atom == atom or now_round <= q.born_round
    # later：等读者自己走到那一关，或者等够两轮
    if q.target and level and q.target == level:
        return True
    return now_round - q.born_round >= 2


def inbox(
    session_id: str,
    *,
    since: int = 0,
    atom: str = "",
    level: str = "",
    now_round: int = 0,
) -> dict[str, Any]:
    """给前端的收件箱。只读，不碰会话锁。"""
    with _LOCK:
        led = load(session_id)
        out: list[dict[str, Any]] = []
        touched = False
        fresh = 0
        room = max(0, MAX_VISIBLE - led.visible_count())
        for q in sorted(led.pool, key=lambda x: x.seq):
            if q.seq <= since or q.state in ("clicked", "dropped"):
                continue
            if q.state == "shown":
                # 已经放行过、但前端还没把游标推过去：照样再给一遍。
                # 投递必须是「至少一次」——丢一个响应不该丢掉一条问题。
                out.append(q.to_chip())
                continue
            if fresh >= min(MAX_RELEASE_PER_TURN, room):
                continue
            was = q.state
            if not _ripe(q, atom=atom, level=level, now_round=now_round):
                touched = touched or q.state != was
                continue
            out.append(q.to_chip())
            q.state = "shown"
            fresh += 1
            touched = True
        cursor = max([q.seq for q in led.pool] + [since]) if out else since
        if touched:
            save(led)
        return {
            "session_id": session_id,
            "items": out,
            "cursor": cursor,
            "running": list(led.running),
            "budget": {"used": led.probe_calls, "max": config.PROBE_MAX_PER_SESSION},
        }


def mark_clicked(session_id: str, text: str) -> DeepQuestion | None:
    """读者点了幽灵按钮——前端以 chip="free" 把原文当 user_text 发回来，
    在这里精确匹配即可。这是整个功能唯一的真实质量反馈信号。
    顺带认出 open 题，好在回答时注入诚实指令。"""
    key = normalize_qkey(text)
    if not key:
        return None
    with _LOCK:
        led = load(session_id)
        for q in led.pool:
            if normalize_qkey(q.text) == key:
                q.state = "clicked"
                if key not in led.asked_qkeys:
                    led.asked_qkeys.append(key)
                save(led)
                return q
    return None


def asked(session_id: str) -> list[str]:
    led = load(session_id)
    return [q.text for q in led.pool if q.state in ("shown", "clicked")][-20:]
