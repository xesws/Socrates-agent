"""突变检查：把实现改坏，对应的测试必须变红。

为什么要有这个：本轮两次写出**空转断言**——测试是绿的，但把实现改回坏写法它还是绿的。
两次都是同一个病根（`_read_excerpts` 内部自建 shelf，在外面塞返回值没用）。
临时手写突变会漏（第一次漏了「取消唯一命中」这条判据，正好是 D3 的核心），
所以固化成表，每次加防线就往表里加一行。

用法：`python3 -m pen.tests.tools.mutate`
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]

# (名字, 文件, 原文, 改坏成什么, 该变红的 -k 表达式)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "D1 跨书正文不该短路掉锚点正文",
        "pen/probe.py",
        '''        src = "\\n".join(
            x for x in (excerpt, anchor_source(raw, job.original_path, job.extra_roots)) if x
        )''',
        "        src = excerpt or anchor_source(raw, job.original_path, job.extra_roots)",
        "excerpt_does_not_shadow",
    ),
    (
        "D3 书名歧义时不许猜一本",
        "pen/probe.py",
        "                hit = next(iter(cands.values())) if len(cands) == 1 else None",
        "                hit = next(iter(cands.values())) if cands else None",
        "ambiguous_book_name",
    ),
    (
        "D3 label 用命中那本的真名，不用模型写的 want",
        "pen/probe.py",
        '''            shown = next((k for k, v in shelf.items() if v == hit), want)''',
        "            shown = want",
        "one_book_with_two_keys",
    ),
    (
        "N1 唯一命中数的是书不是 key",
        "pen/probe.py",
        """                cands: dict[Path, Path] = {}
                for k in shelf:
                    if want in k or k in want:
                        try:
                            cands.setdefault(shelf[k].expanduser().resolve(), shelf[k])
                        except Exception:
                            cands.setdefault(shelf[k], shelf[k])""",
        """                cands = {k: shelf[k] for k in shelf if want in k or k in want}""",
        "two_keys or two_spellings",
    ),
    (
        "R1 反查表不许摸到模型看不见的书",
        "pen/library_scan.py",
        "        if len(picked) >= MAX_FILES:\n            break",
        "        if False:\n            break",
        "cannot",
    ),
    (
        "metas 的 key 要规范化",
        "pen/probe.py",
        "    metas = {str(Path(m.original_path)): m for m in libraries.list_handbooks()}",
        "    metas = {m.original_path: m for m in libraries.list_handbooks()}",
        "noncanonical",
    ),
    (
        "N2 跨书判定比 inode 不比字符串",
        "pen/agent/tools_impl.py",
        "            return not got.samefile(mine)",
        "            return got != mine",
        "case_typo",
    ),
    (
        "D2 跨书读取字节预算",
        "pen/agent/tools_impl.py",
        "        if spent >= CROSS_BOOK_CHARS or reads >= CROSS_BOOK_READS:",
        "        if reads >= CROSS_BOOK_READS:",
        "cross_book_budget",
    ),
    (
        "D2 跨书读取次数上限（字节封不住每次只读一行）",
        "pen/agent/tools_impl.py",
        "        if spent >= CROSS_BOOK_CHARS or reads >= CROSS_BOOK_READS:",
        "        if spent >= CROSS_BOOK_CHARS:",
        "caps_the_number_of_reads",
    ),
    (
        "书架的闸与 read_file 的闸同源",
        "pen/tutor.py",
        "    return [REPO_ROOT, *(extra_roots or [])]",
        "    return list(extra_roots or [])",
        "both_ways or own_semantics",
    ),
]


def main() -> int:
    bad = 0
    for name, rel, old, new, expr in MUTATIONS:
        p = ROOT / rel
        bak = p.read_text(encoding="utf-8")
        if bak.count(old) != 1:
            print(f"  ?? {name}: 锚点在 {rel} 里出现 {bak.count(old)} 次，改过实现就要更新这张表")
            bad += 1
            continue
        p.write_text(bak.replace(old, new), encoding="utf-8")
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-k", expr],
                capture_output=True, text=True, cwd=ROOT,
            )
        finally:
            p.write_text(bak, encoding="utf-8")
        line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(无输出)"
        if "failed" in line:
            print(f"  ✓ 会红  {name}")
        else:
            print(f"  ✗ 空转  {name}  →  {line}")
            bad += 1
    print(f"\n{len(MUTATIONS)} 项，{'全部会红' if not bad else f'{bad} 项没抓住'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
