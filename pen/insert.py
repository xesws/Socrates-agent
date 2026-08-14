"""外科手术插入折叠块。目标永远是 original_path。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pen.index import HandbookIndex, Section
from pen.sandbox import assert_write_target

INSTANCE_RE = re.compile(r"🔍 实例 (\d+)")


class InsertError(ValueError):
    """折叠块或锚点不合法。"""


@dataclass
class InsertPlan:
    mode: str
    level: str
    q_title: str | None
    beat: str | None
    insert_after_line: int  # 1-based：此行之后插入（通常是最后一个 </details>）
    instance_n: int
    fold_md: str


def lint_fold(fold_md: str) -> list[str]:
    errors: list[str] = []
    text = fold_md.replace("\r\n", "\n").strip("\n") + "\n"
    if "<details>" not in text or "</details>" not in text:
        errors.append("缺少 <details> / </details>")
        return errors
    if text.count("<details>") != text.count("</details>"):
        errors.append("<details> 与 </details> 数量不一致")
    # 要求 details 前后、summary 后有空行（GitHub 渲染契约）
    if not re.search(r"\n<details>\n\n", "\n" + text):
        errors.append("<details> 后必须空一行")
    if not re.search(r"</summary>\n\n", text):
        errors.append("</summary> 后必须空一行")
    if not re.search(r"\n\n</details>\n", text + "\n"):
        errors.append("</details> 前必须空一行")
    sm = re.search(r"<summary>(.*?)</summary>", text, re.S)
    if sm:
        inner = sm.group(1)
        if "<<" in inner or re.search(r"<[a-zA-Z/]", inner):
            errors.append("<summary> 内含未转义的 <")
    if "- **TL;DR：**" in text or "- **(a)" in text:
        errors.append("折叠块不得改写 / 复制 TL;DR 或 (a)(b)(c)")
    if "〔回读：" in text:
        errors.append("折叠块不得包含 〔回读〕")
    return errors


def next_instance_n(section_text: str) -> int:
    nums = [int(n) for n in INSTANCE_RE.findall(section_text)]
    return (max(nums) if nums else 0) + 1


def normalize_fold(fold_md: str, instance_n: int, summary_hint: str | None) -> str:
    body = fold_md.replace("\r\n", "\n").strip()
    if "<details>" not in body:
        title = summary_hint or "点读笔补充"
        body = (
            f"<details>\n\n"
            f"<summary>🔍 实例 {instance_n}：{title}</summary>\n\n"
            f"{body}\n\n"
            f"</details>"
        )
    else:
        body = re.sub(
            r"<summary>\s*🔍 实例 \d+：",
            f"<summary>🔍 实例 {instance_n}：",
            body,
            count=1,
        )
        if "<summary>" in body and "🔍 实例" not in body.split("<summary>", 1)[1][:80]:
            body = body.replace("<summary>", f"<summary>🔍 实例 {instance_n}：", 1)
    # 强制空行契约
    body = re.sub(r"<details>\s*", "<details>\n\n", body, count=1)
    body = re.sub(r"</summary>\s*", "</summary>\n\n", body, count=1)
    body = re.sub(r"\s*</details>", "\n\n</details>", body, count=1)
    body = body.strip() + "\n"
    return body


def _last_details_close_line(lines: list[str], start: int, end: int) -> int | None:
    """1-based inclusive range → line number of last </details>."""
    last = None
    for i in range(start, end + 1):
        if "</details>" in lines[i - 1]:
            last = i
    return last


def plan_insert(
    idx: HandbookIndex,
    original_path: Path,
    *,
    line: int,
    fold_md: str,
    summary_hint: str | None = None,
) -> InsertPlan:
    section: Section = idx.locate(line)
    text = original_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    sec_text = "\n".join(lines[section.start_line - 1 : section.end_line])
    n = next_instance_n(sec_text)

    if section.kind == "q":
        mode = "q_append"
        close = _last_details_close_line(lines, section.start_line, section.end_line)
        if close is None:
            # 插在 回读 前一行；若没有 details，插在 (d) 之后、回读之前
            insert_after = section.end_line - 1
        else:
            insert_after = close
    elif section.kind == "teaching":
        mode = "teaching_append"
        insert_after = min(line, section.end_line)
    else:
        mode = "doubt_fold"
        insert_after = min(line, section.end_line)

    fold = normalize_fold(fold_md, n, summary_hint)
    errors = lint_fold(fold)
    if errors:
        raise InsertError("；".join(errors))
    return InsertPlan(
        mode=mode,
        level=section.level,
        q_title=section.q_title,
        beat=section.beat,
        insert_after_line=insert_after,
        instance_n=n,
        fold_md=fold,
    )


def render_new_text(original_text: str, plan: InsertPlan) -> str:
    lines = original_text.splitlines()
    if plan.insert_after_line < 0 or plan.insert_after_line > len(lines):
        raise InsertError(f"插入行越界：{plan.insert_after_line}")
    fold_lines = plan.fold_md.strip("\n").splitlines()
    # 保证与上下文之间有空行
    head = lines[: plan.insert_after_line]
    tail = lines[plan.insert_after_line :]
    block: list[str] = []
    if head and head[-1].strip() != "":
        block.append("")
    block.extend(fold_lines)
    block.append("")
    if tail and tail[0].strip() != "":
        # 已加空行
        pass
    new_lines = head + block + tail
    return "\n".join(new_lines) + "\n"


def unified_diff(original_text: str, new_text: str, path_label: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
        )
    )


def apply_insert(original_path: Path, plan: InsertPlan) -> str:
    """原地写入原文。返回新全文。"""
    target = assert_write_target(original_path, original_path)
    old = target.read_text(encoding="utf-8")
    new = render_new_text(old, plan)
    if new == old:
        raise InsertError("插入后文本没有变化")
    # 只增：新文件必须更长，且去掉新增块后应能对上回读行
    if len(new) < len(old):
        raise InsertError("拒绝缩短原文（只增不删）")
    tmp = target.with_suffix(target.suffix + ".pen-tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(target)
    return new
