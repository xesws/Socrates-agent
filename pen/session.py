"""按 handbook 隔离的 messages 会话。记忆在循环外，对齐 L2。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


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


@dataclass
class PenSession:
    session_id: str
    handbook_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_anchor: dict[str, Any] | None = None
    has_substantive: bool = False
    last_assistant: str = ""

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]


class SessionStore:
    def __init__(self) -> None:
        self._items: dict[str, PenSession] = {}

    def create(self, handbook_id: str) -> PenSession:
        sid = uuid.uuid4().hex
        sess = PenSession(session_id=sid, handbook_id=handbook_id)
        self._items[sid] = sess
        return sess

    def get(self, session_id: str) -> PenSession:
        if session_id not in self._items:
            raise KeyError(session_id)
        return self._items[session_id]


STORE = SessionStore()
