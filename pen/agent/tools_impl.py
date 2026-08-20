"""read_file / edit_file。写只允许登记原文。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pen.readtool import read_file_report
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
    return {
        "ok": bool(report["ok"]),
        "text": str(report["text"]),
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
            "text": "错误：原文里找不到这段 old_string。请再 read_file，用文件里的原文逐字复制。",
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
