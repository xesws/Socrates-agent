"""轻量工具箱：registry + 审批 + read_file/edit_file。无 bash、无 write_file。"""

from pen.agent.permissions import decide
from pen.agent.registry import TOOLS, dispatch, schemas

__all__ = ["TOOLS", "decide", "dispatch", "schemas"]
