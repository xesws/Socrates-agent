"""read_file / edit_file。写只允许登记原文。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pen.readtool import read_file_report
from pen.config import CROSS_BOOK_CHARS
from pen.sandbox import SandboxError, assert_write_target, resolve_read_target
from pen import libraries, snapshots


def _occurrences(text: str, needle: str) -> int:
    """重叠也算：'aaa' 里 'aa' 出现 2 次。"""
    if not needle:
        return 0
    n = 0
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return n
        n += 1
        start = i + 1


def _is_cross_book(original: Path, resolved: str) -> bool:
    """读的是不是当前手册以外的东西。

    比 inode 而不是比路径字符串：APFS 默认大小写不敏感，模型把 handbook_path 的
    大小写抄错（Handbook.md / handbook.md）读到的是**同一个文件**，
    但 `resolve()` 不规范化大小写，字符串比较会判成跨书，白白吃掉预算。
    实测在 macOS 上确实会撞到。samefile 需要两边都存在——读成功了才走到这里，
    所以正常成立；万一 race 掉了就回落到路径比较。
    """
    try:
        got = Path(resolved).expanduser().resolve()
        mine = original.expanduser().resolve()
        try:
            return not got.samefile(mine)
        except OSError:
            return got != mine
    except Exception:
        return False


def handle_read_file(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    original = Path(ctx["original_path"])
    raw_path = str(args.get("path") or "").strip() or str(original)
    report = read_file_report(
        original,
        raw_path,
        offset=int(args.get("offset", 1) or 1),
        limit=int(args.get("limit", 80) or 80),
        extra_roots=ctx.get("extra_roots") or [],
    )
    text = str(report["text"])
    # 读当前手册以外的东西才计预算（别的教材、lab/ 对照文件）。
    # 读当前手册不计，本职阅读一次都不受影响。
    # 超了不报错、不中断：返回一句让它收敛的话。报错会让模型换个 offset 再试，
    # 那正是我们要止住的循环。
    if report["ok"] and _is_cross_book(original, str(report["resolved"])):
        spent = int(ctx.get("cross_book_chars") or 0)
        if spent >= CROSS_BOOK_CHARS:
            return {
                "ok": True,
                "text": (
                    "本轮翻别的教材的额度用完了。用已经读到的内容作答，"
                    "并且告诉读者你只读了哪几段、还有哪些没看——不要接着翻，也不要凭书名猜。"
                ),
                "resolved": str(report["resolved"]),
                "detail": raw_path,
            }
        ctx["cross_book_chars"] = spent + len(text)
    return {
        "ok": bool(report["ok"]),
        "text": text,
        "resolved": str(report["resolved"]),
        "detail": raw_path,
    }


def handle_edit_file(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    original = Path(ctx["original_path"])
    raw_path = str(args.get("path") or "").strip() or str(original)
    old = str(args.get("old_string") or "")
    new = str(args.get("new_string") or "")
    tried = str(resolve_read_target(original, raw_path))
    if not old.strip():
        return {
            "ok": False,
            "text": "错误：old_string 不能为空。先 read_file 看准要换的那一小段。",
            "resolved": tried,
            "detail": raw_path,
        }
    try:
        target = assert_write_target(original, tried)
    except SandboxError as exc:
        return {"ok": False, "text": f"错误：{exc}", "resolved": tried, "detail": raw_path}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "text": f"错误：无法读取 {target.name}：{exc}",
            "resolved": str(target),
            "detail": raw_path,
        }
    if old.strip() == text.strip():
        return {
            "ok": False,
            "text": "错误：禁止把整份原文当 old_string。请只替换需要改的那一小段。",
            "resolved": str(target),
            "detail": raw_path,
        }
    n = _occurrences(text, old)
    if n == 0:
        return {
            "ok": False,
            "text": "错误：原文里找不到这段 old_string。请再 read_file，用去掉行号前缀后的纯原文逐字复制（不要带 12\\t）。",
            "resolved": str(target),
            "detail": raw_path,
        }
    if n > 1:
        return {
            "ok": False,
            "text": f"错误：old_string 在原文里出现了 {n} 次。请加上下文让它只出现一次。",
            "resolved": str(target),
            "detail": raw_path,
        }
    hid = str(ctx.get("handbook_id") or "")
    if hid:
        snapshots.take_snapshot(hid, original, "pre-edit")
    line = text[: text.index(old)].count("\n") + 1
    updated = text.replace(old, new, 1)
    tmp = target.with_suffix(target.suffix + ".pen-tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(target)
    if hid:
        try:
            libraries.refresh_if_stale(hid)
        except Exception:
            pass
    return {
        "ok": True,
        "text": f"已编辑 {target.name}（第 {line} 行起替换 1 处，现 {len(updated.splitlines())} 行）",
        "resolved": str(target),
        "detail": raw_path,
        "line": line,
    }
