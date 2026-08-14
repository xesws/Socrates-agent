"""按 handbook 隔离的 messages 会话。记忆在循环外，对齐 L2。快照落在 .pen/sessions/。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pen import config

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

FIXED_CHIPS: list[dict[str, Any]] = [
    {
        "id": "socratic",
        "label": "先别揭晓，问我一个问题",
        "enabled": True,
    },
    {
        "id": "explain_zero",
        "label": "当我零基础，讲清楚再给两个例子",
        "enabled": True,
    },
    {
        "id": "examples",
        "label": "只举例子",
        "enabled": True,
    },
    {
        "id": "search",
        "label": "查相关论文 / 算法出处",
        "enabled": False,
        "hint": "P2 才开放，现在不会假装搜过",
    },
    {
        "id": "writeback",
        "label": "把刚才的解答写进手册原文",
        "enabled": False,
        "hint": "先有一轮实质解答",
    },
]


SYSTEM_PROMPT = """你是坐在读者旁边的师傅，正在带人读一本手搓 SWE Agent 的通关手册。
读者才是主修这本手册的人。手册里的 Agent 是「记性为零、胆子极大」的实习生——你不要替实习生写作业。

来源定位已经由系统算好，写在用户消息的 [来源] 里。禁止再猜文件名或 Q 号所属 Level。
不要把整本书背进回复。邻域通常已经够用。缺哪一段再用 read_file 去翻；材料够了就用自然语言回答，不要空转。

芯片意图：
- socratic：默认。只给提示卡级方向 + 一个反问。不要把 TL;DR/(a)(b)(c) 倒完，不要给完整答案。
- explain_zero：按手册骨架讲：TL;DR → (a) 概念/对比 → (b) 机制 → (c) 反例 → 两个可运行例子。
- examples：只给两个例子，名字必须对得上该 Level 第七拍（scan.sh、messages.append、dispatch、approve…）。
- writeback：不要自己改文件。把要沉淀的要点写成一个 Meta Instance 折叠块（<details>），等用户在界面确认。
- free：看用户怎么问，仍先确认他卡在哪。

终端实录若不是你亲眼看到的工具输出，必须标注「示意」。
语气：师傅带实习生，口语，短句，别客服腔。
回复末尾另起一行写且只写（界面会剥掉，读者看不到）：
<!--pen:chips
- 下一问 1
- 下一问 2
-->
动态建议必须是下一问，不要重复固定芯片的文案。"""


def sessions_dir() -> Path:
    dest = config.PEN_DIR / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _session_path(session_id: str) -> Path:
    if not _SAFE_ID.match(session_id):
        raise ValueError(f"非法 session_id：{session_id!r}")
    return sessions_dir() / f"{session_id}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def chip_label(chip: str) -> str:
    for item in FIXED_CHIPS:
        if item["id"] == chip:
            return str(item["label"])
    return chip


@dataclass
class PenSession:
    session_id: str
    handbook_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_anchor: dict[str, Any] | None = None
    has_substantive: bool = False
    last_assistant: str = ""
    ui_messages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "handbook_id": self.handbook_id,
            "messages": self.messages,
            "last_anchor": self.last_anchor,
            "has_substantive": self.has_substantive,
            "last_assistant": self.last_assistant,
            "ui_messages": self.ui_messages,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def to_public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "handbook_id": self.handbook_id,
            "chips": FIXED_CHIPS,
            "has_substantive": self.has_substantive,
            "last_anchor": self.last_anchor,
            "ui_messages": self.ui_messages,
            "last_assistant": self.last_assistant,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PenSession:
        return cls(
            session_id=str(data["session_id"]),
            handbook_id=str(data["handbook_id"]),
            messages=list(data.get("messages") or []),
            last_anchor=data.get("last_anchor") if isinstance(data.get("last_anchor"), dict) else None,
            has_substantive=bool(data.get("has_substantive")),
            last_assistant=str(data.get("last_assistant") or ""),
            ui_messages=list(data.get("ui_messages") or []),
        )


def load_session(session_id: str) -> PenSession | None:
    try:
        dest = _session_path(session_id)
    except ValueError:
        return None
    if not dest.is_file():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("session_id"):
        return None
    return PenSession.from_dict(data)


def save_session(sess: PenSession) -> Path:
    config.ensure_pen_dirs()
    dest = _session_path(sess.session_id)
    _atomic_write(dest, json.dumps(sess.to_dict(), ensure_ascii=False))
    return dest


class SessionStore:
    def __init__(self) -> None:
        self._items: dict[str, PenSession] = {}

    def create(self, handbook_id: str) -> PenSession:
        sid = uuid.uuid4().hex
        sess = PenSession(session_id=sid, handbook_id=handbook_id)
        self._items[sid] = sess
        save_session(sess)
        return sess

    def get(self, session_id: str) -> PenSession:
        if session_id in self._items:
            return self._items[session_id]
        loaded = load_session(session_id)
        if loaded is None:
            raise KeyError(session_id)
        self._items[session_id] = loaded
        return loaded

    def save(self, sess: PenSession) -> None:
        self._items[sess.session_id] = sess
        save_session(sess)


STORE = SessionStore()
