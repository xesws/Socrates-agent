"""审批：只读自动过；edit_file 每次问人；未知工具拒绝。"""

from __future__ import annotations


def decide(name: str) -> str:
    if name == "read_file":
        return "allow"
    if name == "edit_file":
        return "ask"
    return "deny"
