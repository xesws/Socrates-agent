"""突变检查：把实现改坏，对应的测试必须变红。

为什么要有这个：本轮两次写出**空转断言**——测试是绿的，但把实现改回坏写法它还是绿的。
两次都是同一个病根（`_read_excerpts` 内部自建 shelf，在外面塞返回值没用）。
临时手写突变会漏（第一次漏了「取消唯一命中」这条判据，正好是 D3 的核心），
所以固化成表，每次加防线就往表里加一行。

用法：`python3 -m pen.tests.tools.mutate`
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
# 别在工作区里改：① 脚本跑到一半崩了会留下坏代码；② 别的进程（编辑器、
# 另一个 agent、watch 模式）正好在这一瞬读到坏文件，结果就飘——实测同一条
# 突变连跑三次会出现一次假绿。整个仓库复制一份到 tmp，在副本上折腾。
_SKIP = {".git", "node_modules", ".pytest_cache", "__pycache__", ".pen", "dist", ".venv"}

# (名字, 文件, 原文, 改坏成什么, 该变红的 -k 表达式)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "D1 跨书正文不该短路掉锚点正文",
        "pen/probe.py",
        """        src = "\\n".join(
            x for x in (
                excerpt if has_cross else "",
                anchor_source(raw, job.original_path, job.extra_roots, limits=job.limits),
            ) if x
        )""",
        "        src = excerpt or anchor_source(raw, job.original_path, job.extra_roots)",
        "excerpt_does_not_shadow",
    ),
    (
        "跨书锚必须是本轮真读过的那几段",
        "pen/probe.py",
        """        covered = any(
            b == shown and s <= start and end <= e for b, s, e in read_spans
        )
        if not covered:
            return None  # 没读过就写行号 = 编""",
        "        covered = True",
        "cross_anchor",
    ),
    (
        "每条题至少一条锚落在当前这本",
        "pen/probe.py",
        '''    if not any(m.book == "" for m in marks):
        return False, "needs-an-anchor-in-this-book"''',
        "    pass",
        "anchor_in_this_book",
    ),
    (
        "bridge 按 (书, 关) 去重，不按 level 字符串",
        "pen/probe.py",
        "        if len({m.level_key for m in marks}) < 2:",
        '''        _lv = {str(a.get("level") or "") for a in anchors}
        _lv.discard("")
        if len(_lv) < 2:''',
        "bridge_across_books",
    ),
    (
        "不填 book 的锚不许拿别本正文当出处",
        "pen/probe.py",
        '''                excerpt if has_cross else "",''',
        "                excerpt,",
        "cannot_borrow",
    ),
    (
        "later 投递不许认错书",
        "pen/probe_store.py",
        """        cross = any(str(a.get("book") or "").strip() for a in q.anchors)
        if not cross:
            return True""",
        "        return True",
        "does_not_confuse",
    ),
    (
        "D3 书名歧义时不许猜一本",
        "pen/probe.py",
        "        hit = next(iter(cands.values())) if len(cands) == 1 else None",
        "        hit = next(iter(cands.values())) if cands else None",
        "ambiguous_book_name",
    ),
    (
        "D3 label 用命中那本的真名，不用模型写的 want",
        "pen/probe.py",
        '''    shown = next((k for k, v in shelf.items() if v == hit), want)''',
        "    shown = want",
        "one_book_with_two_keys",
    ),
    (
        "N1 唯一命中数的是书不是 key",
        "pen/probe.py",
        """        cands: dict[Path, Path] = {}
        for k in shelf:
            if want in k or k in want:
                try:
                    cands.setdefault(shelf[k].expanduser().resolve(), shelf[k])
                except Exception:
                    cands.setdefault(shelf[k], shelf[k])""",
        """        cands = {k: shelf[k] for k in shelf if want in k or k in want}""",
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
        "        if spent >= lim.cross_book_chars or reads >= lim.cross_book_reads:",
        "        if reads >= lim.cross_book_reads:",
        "cross_book_budget",
    ),
    (
        "D2 跨书读取次数上限（字节封不住每次只读一行）",
        "pen/agent/tools_impl.py",
        "        if spent >= lim.cross_book_chars or reads >= lim.cross_book_reads:",
        "        if spent >= lim.cross_book_chars:",
        "caps_the_number_of_reads",
    ),
    (
        "近度三档：当前手册自己那棵树优先于其他根",
        "pen/library_scan.py",
        """            if home is not None and (r == home or home in r.parents):
                near = 0
            elif any(r == b or b in r.parents for b in bases):
                near = 1
            else:
                near = 2""",
        """            near = 0 if any(r == b or b in r.parents for b in bases) else 1""",
        "readers_own_tree",
    ),
    (
        "书名匹配要克制：短词会误报",
        "pen/probe.py",
        "            long_enough = (len(w) >= 4) if cjk else (len(w) >= 6)",
        "            long_enough = True",
        "only_fires_when",
    ),
    (
        "读者点名了书架上的书就直接告诉模型",
        "pen/probe.py",
        "        hits = books_mentioned(job.shelf, job.user_text, job.reply)",
        "        hits = []",
        "shelf_hint",
    ),
    (
        "level 只比第一段（模型会把 beat 也写进去）",
        "pen/probe.py",
        '''    claimed = str(a.get("level") or "").split("/")[0].strip()''',
        '''    claimed = str(a.get("level") or "").strip()''',
        "beat_appended",
    ),
    (
        "v0.10.1 跨书闸值必须来自 ctx，不是进程默认",
        "pen/agent/tools_impl.py",
        "        lim = limits_of(ctx)",
        "        lim = config.default_limits()",
        "cross_book_limit_comes_from_ctx",
    ),
    (
        "v0.10.1 并发数必须来自本次请求冻结的那份",
        "pen/probe.py",
        "    if not _take_slot(job.limits.probe_concurrency):",
        "    if not _take_slot(2):",
        "honours_the_job_concurrency",
    ),
    (
        "v0.10.1 翻书轮数必须来自 ctx",
        "pen/tutor.py",
        "    for _step in range(limits_of(ctx).max_tool_rounds):",
        "    for _step in range(100):",
        "tool_rounds_limit_is_honoured",
    ),
    (
        "v0.10.0 缓存命中要认 DeepSeek 那个别名",
        "pen/meter.py",
        '        cached = _num(_get(raw, "prompt_cache_hit_tokens"))',
        "        cached = 0",
        "deepseek_cache_alias",
    ),
    (
        "v0.10.0 深挖的账落账本，不落会话",
        "pen/probe.py",
        "        probe_store.add_questions(job.session_id, probe_id, items, spend=m.to_dict())",
        "        probe_store.add_questions(job.session_id, probe_id, items)",
        "records_the_spend_in_the_ledger",
    ),
    (
        "v0.10.2 孤儿窗口必须盖得住一次探索（短了会双份花钱）",
        "pen/probe_store.py",
        "    return max(ORPHAN_AFTER_SECONDS, timeout_s * 3 * 2 + 30.0)",
        "    return ORPHAN_AFTER_SECONDS",
        "outlasts_the_worst_case",
    ),
    (
        "v0.10.2 配额报表报的是执行时用的那个数",
        "pen/probe_store.py",
        '        "max": led.max_per_session or config.PROBE_MAX_PER_SESSION,\n'
        '        "window_used": quota_count(led.handbook_id) if led.handbook_id else 0,\n'
        '        "window_max": led.max_per_window or config.PROBE_MAX_PER_WINDOW,',
        '        "max": config.PROBE_MAX_PER_SESSION,\n'
        '        "window_used": quota_count(led.handbook_id) if led.handbook_id else 0,\n'
        '        "window_max": config.PROBE_MAX_PER_WINDOW,',
        "snapshots_the_limits_it_actually_used",
    ),
    (
        "v0.10.3 一轮 = 一份跨书预算，审批不让它翻倍",
        "pen/tutor.py",
        '    ctx["cross_book_chars"] = int(pending.get("cross_book_chars") or 0)',
        '    ctx["cross_book_chars"] = 0',
        "one_cross_book_budget",
    ),
    (
        "书架的闸与 read_file 的闸同源",
        "pen/tutor.py",
        "    return [REPO_ROOT, *(extra_roots or [])]",
        "    return list(extra_roots or [])",
        "both_ways or own_semantics",
    ),
]


def _clone(dest: pathlib.Path) -> pathlib.Path:
    shutil.copytree(
        ROOT, dest / "repo",
        ignore=lambda d, names: [n for n in names if n in _SKIP],
        symlinks=True,
    )
    return dest / "repo"


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        work = _clone(pathlib.Path(td))
        for name, rel, old, new, expr in MUTATIONS:
            p = work / rel
            bak = p.read_text(encoding="utf-8")
            if bak.count(old) != 1:
                print(f"  ?? {name}: 锚点在 {rel} 里出现 {bak.count(old)} 次，改过实现就要更新这张表")
                bad += 1
                continue
            p.write_text(bak.replace(old, new), encoding="utf-8")
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", expr],
                    capture_output=True, text=True, cwd=work,
                )
            finally:
                p.write_text(bak, encoding="utf-8")
            line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(无输出)"
            if "failed" not in line and " passed" in line:
                # 复查一次。实测出现过假阴性：同一条突变连跑三次有一次报绿，
                # 手工在同样的副本上跑却是红的。方向比假阳性安全（会去查而不是放过），
                # 但每次都要人手工排查就白搭了。
                p.write_text(bak.replace(old, new), encoding="utf-8")
                try:
                    r2 = subprocess.run(
                        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", expr],
                        capture_output=True, text=True, cwd=work,
                    )
                finally:
                    p.write_text(bak, encoding="utf-8")
                line2 = r2.stdout.strip().splitlines()[-1] if r2.stdout.strip() else "(无输出)"
                if "failed" in line2:
                    print(f"  ✓ 会红  {name}  （第一次报绿，复查是红的）")
                    continue
                line = line2
            if "failed" in line:
                print(f"  ✓ 会红  {name}")
            elif " passed" not in line:
                # 只有 deselected、一条都没跑 = -k 表达式没选中任何用例。
                # 这和「测试是空转」一样危险，实测栽过两次（测试名和表里的
                # 表达式对不上，表却报绿）。
                print(f"  ?? {name}: -k {expr!r} 没选中任何用例 → {line}")
                bad += 1
            elif False:
                # 既没红也没绿：多半是 collect 出错或 -k 没选中任何用例，
                # 那和「测试没抓住」一样危险，不能当通过。
                print(f"  ?? {name}: 跑不起来 → {line}")
                bad += 1
            else:
                print(f"  ✗ 空转  {name}  →  {line}")
                bad += 1
    print(f"\n{len(MUTATIONS)} 项，{'全部会红' if not bad else f'{bad} 项没抓住'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
