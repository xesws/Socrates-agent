from __future__ import annotations

from pathlib import Path

from pen import config, libraries

FIXTURE = Path(__file__).parent / "fixtures" / "mini_handbook.md"


def _isolate_pen(tmp_path: Path, monkeypatch) -> Path:
    lib = tmp_path / "libraries"
    lib.mkdir()
    monkeypatch.setattr(config, "PEN_DIR", tmp_path)
    monkeypatch.setattr(config, "LIBRARIES_DIR", lib)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    return lib


def test_register_same_path_skips_reindex_and_updates_allow_root(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    book = vault / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    first = libraries.register(book, "mini", extra_roots=[vault])
    assert first.allow_root == str(vault.resolve())

    calls = 0
    real_build = libraries.build_index

    def counting_build(path):
        nonlocal calls
        calls += 1
        return real_build(path)

    monkeypatch.setattr(libraries, "build_index", counting_build)

    again = libraries.register(book, "mini", extra_roots=[vault])
    assert calls == 0
    assert again.allow_root == str(vault.resolve())

    widened = libraries.register(book, "mini", extra_roots=[tmp_path])
    assert calls == 0
    assert widened.allow_root == str(tmp_path.resolve())
    assert libraries.get("mini").allow_root == str(tmp_path.resolve())


def test_register_same_id_new_path_still_reindexes(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    first_book = tmp_path / "a.md"
    second_book = tmp_path / "b.md"
    first_book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    second_book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    libraries.register(first_book, "mini", extra_roots=[tmp_path])

    calls = 0
    real_build = libraries.build_index

    def counting_build(path):
        nonlocal calls
        calls += 1
        return real_build(path)

    monkeypatch.setattr(libraries, "build_index", counting_build)
    moved = libraries.register(second_book, "mini", extra_roots=[tmp_path])
    assert calls == 1
    assert moved.original_path == str(second_book.resolve())


def test_shelf_digest_rejects_paths_outside_the_allowed_roots(tmp_path, monkeypatch) -> None:
    """登记表里躺着 pytest 临时夹具，其中一本还长得像手册（有 Level 0 / 第三拍）。
    不挡的话模型会当真书去搭桥，而且那是真实的隐私外泄。"""
    from pen import config, library_scan

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    library_scan._CACHE.clear()

    inside = tmp_path / "vault"
    inside.mkdir()
    (inside / "book.md").write_text("# 正经教材\n\n## 第一章\n", encoding="utf-8")
    (inside / "cur.md").write_text("# 当前这本\n", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "fixture.md").write_text("# 封面\n\n# Level 0 — 终端\n", encoding="utf-8")

    got = library_scan.shelf_digest(
        inside / "cur.md",
        [str(inside / "book.md"), str(outside / "fixture.md")],
        allow_roots=[inside],
    )
    assert "正经教材" in got
    assert "Level 0" not in got and "封面" not in got, f"根外的文件泄漏了：{got}"


def test_shelf_digest_is_empty_when_only_one_book(tmp_path, monkeypatch) -> None:
    """只有一本时整段省略——让模型硬编不存在的跨书联系比不提更糟。"""
    from pen import config, library_scan

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    library_scan._CACHE.clear()
    solo = tmp_path / "solo.md"
    solo.write_text("# 唯一一本\n", encoding="utf-8")
    assert library_scan.shelf_digest(solo, [], allow_roots=[tmp_path]) == ""


def test_shelf_digest_with_paths_does_not_share_cache_with_plain(tmp_path, monkeypatch) -> None:
    """两种格式共用一个缓存键的话，先跑的那次会在 TTL 内把自己的格式喂给后来者。
    症状是「有时能读有时读不了」——实时层拿到没 path 的那份，师傅就只能猜文件名。"""
    from pen import config, library_scan

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "other.md").write_text("# 另一本\n\n## 第一章\n", encoding="utf-8")
    cur = vault / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    regs = [str(vault / "other.md")]

    # probe 那条线先跑（不带 path），实时层随后立刻要带 path 的
    plain = library_scan.shelf_digest(cur, regs, allow_roots=[vault])
    rich = library_scan.shelf_digest(cur, regs, allow_roots=[vault], with_paths=True)
    assert "path:" not in plain, f"probe 那份混进了路径：{plain}"
    assert "path:" in rich and str(vault / "other.md") in rich, f"实时层拿到的没有路径：{rich}"

    # 反向也要成立
    library_scan._CACHE.clear()
    rich2 = library_scan.shelf_digest(cur, regs, allow_roots=[vault], with_paths=True)
    plain2 = library_scan.shelf_digest(cur, regs, allow_roots=[vault])
    assert "path:" in rich2 and "path:" not in plain2


def test_shelf_digest_prefers_the_copy_in_the_readers_own_vault(tmp_path, monkeypatch) -> None:
    """同一本书在仓库根和 vault 各有一份，标题一模一样。按标题去重只会留先遇到的，
    实测留下的是仓库根那份旧版，而读者正在编辑的是 vault 那份。"""
    from pen import config, library_scan

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    library_scan._CACHE.clear()

    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    repo.mkdir()
    vault.mkdir()
    body = "# 通关手册\n\n## 开篇\n"
    stale = repo / "handbook.md"
    fresh = vault / "handbook.md"
    stale.write_text(body, encoding="utf-8")
    fresh.write_text(body, encoding="utf-8")
    import os

    os.utime(stale, (1_700_000_000, 1_700_000_000))  # 仓库根那份更旧
    os.utime(fresh, (1_800_000_000, 1_800_000_000))
    cur = vault / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")

    # 登记顺序故意把旧的排前面——这正是真实登记表的样子
    got = library_scan.shelf_digest(
        cur, [str(stale), str(fresh)], allow_roots=[tmp_path], with_paths=True
    )
    assert str(fresh) in got, f"给的是 vault 外那份：{got}"
    assert str(stale) not in got, f"两份都列了，模型会以为书架上有两本：{got}"


def test_shelf_only_lists_books_read_file_can_actually_reach(tmp_path, monkeypatch) -> None:
    """书架的闸曾经是全局 handbook_allow_roots()，比 read_file 的闸宽得多。
    当前手册在仓库根（allow_root 为 None，沙箱根只有仓库根）而 PEN_ALLOW_ROOTS
    指着 vault 时，书架会印出 vault 里那本，师傅照着读 → 「不在本手册允许的根内」。
    印一条读不到的路径比不印更糟：白跑一次工具，还得道歉。"""
    from pen import config, library_scan, readtool
    from pen.sandbox import reading_roots

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    library_scan._CACHE.clear()

    repo = tmp_path / "repo"
    vault = tmp_path / "vault"
    repo.mkdir()
    vault.mkdir()
    cur = repo / "handbook.md"
    cur.write_text("# 手上这本\n", encoding="utf-8")
    far = vault / "other.md"
    far.write_text("# 够不着的那本\n\n## 第一章\n", encoding="utf-8")
    regs = [str(far)]

    # 当前手册没有自己的 allow_root → 沙箱根只有它所在的目录
    roots = reading_roots(cur, None)
    assert roots == [repo.resolve()]
    got = library_scan.shelf_digest(cur, regs, allow_roots=roots, with_paths=True)
    assert got == "", f"印出了读不到的书：{got}"
    # 反证：全局那套闸（含 vault）会把它列出来，而 read_file 会拒
    library_scan._CACHE.clear()
    wide = library_scan.shelf_digest(cur, regs, allow_roots=[tmp_path], with_paths=True)
    assert "够不着" in wide
    assert readtool.read_file_report(cur, str(far), 1, 1, extra_roots=None)["ok"] is False


def test_prefer_nearby_uses_the_reading_root_not_the_parent_dir(tmp_path, monkeypatch) -> None:
    """手册放在 vault/level0/ 这种子目录里时，按父目录算「同一棵树」，
    vault 根下的兄弟书就不算近了，退到 mtime 比大小——仓库那份被 git pull
    刷新过就会赢，又绕回「师傅照着旧版讲」。"""
    from pen import config, library_scan

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    sub = vault / "level0"
    repo = tmp_path / "repo"
    sub.mkdir(parents=True)
    repo.mkdir()
    body = "# 通关手册\n\n## 开篇\n"
    fresh_repo = repo / "handbook.md"  # 仓库那份反而更新（刚 git pull 过）
    vault_copy = vault / "handbook.md"
    fresh_repo.write_text(body, encoding="utf-8")
    vault_copy.write_text(body, encoding="utf-8")
    import os

    os.utime(vault_copy, (1_700_000_000, 1_700_000_000))
    os.utime(fresh_repo, (1_800_000_000, 1_800_000_000))
    cur = sub / "当前.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")

    got = library_scan.shelf_digest(
        cur, [str(fresh_repo), str(vault_copy)], allow_roots=[vault], with_paths=True
    )
    assert str(vault_copy) in got, f"挑了 vault 外那份：{got}"
    assert str(fresh_repo) not in got


def test_shelf_cache_key_covers_registered_and_roots(tmp_path, monkeypatch) -> None:
    """缓存键只有 current_path 时：读者刚在 Obsidian 里打开另一本书（登记），
    60 秒内书架里看不见它，师傅照旧答「我没读到」——要修的症状原样复发。
    roots 同理：实时层传 vault 根、probe 传 REPO_ROOT，共用一个键就是互相投毒。"""
    from pen import config, library_scan

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    cur = vault / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    first = vault / "a.md"
    first.write_text("# 先有的那本\n", encoding="utf-8")
    later = vault / "b.md"
    later.write_text("# 刚打开的那本\n", encoding="utf-8")

    one = library_scan.shelf_digest(cur, [str(first)], allow_roots=[vault])
    assert "先有的" in one and "刚打开" not in one
    # 读者刚登记了 b.md，TTL 还没到
    two = library_scan.shelf_digest(cur, [str(first), str(later)], allow_roots=[vault])
    assert "刚打开" in two, f"新登记的书被旧缓存挡住了：{two}"

    # 换一套读取根，结果必须重算（repo 根下看不到 vault 里的书）
    repo = tmp_path / "repo"
    repo.mkdir()
    narrow = library_scan.shelf_digest(cur, [str(first), str(later)], allow_roots=[repo])
    assert narrow == "", f"换了根却吃到上一套根的缓存：{narrow}"


def test_prefer_nearby_survives_a_broken_registry_entry(tmp_path, monkeypatch) -> None:
    """sorted() 的 key 抛异常会掀掉整张书架，调用方只看得到一个空串。
    resolve() 对含 \\x00 的路径抛的是 ValueError，不是 OSError。"""
    from pen import config, library_scan

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    cur = vault / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    good = vault / "good.md"
    good.write_text("# 好书\n", encoding="utf-8")

    got = library_scan.shelf_digest(
        cur, ["/bad\x00path.md", str(good)], allow_roots=[vault], with_paths=True
    )
    assert "好书" in got, f"一条坏记录掀掉了整张书架：{got!r}"


def test_plugin_deployment_without_env_still_gets_a_shelf(tmp_path, monkeypatch) -> None:
    """插件那套部署不配 PEN_ALLOW_ROOTS，靠请求体的 vault_root 登记。
    书架的闸如果还挂在全局 handbook_allow_roots() 上，那里只有 REPO_ROOT，
    vault 里的教材整本被滤掉——功能在标准部署下是空转。
    改用 reading_roots 之后，闸走的是每本手册自己的 allow_root。"""
    from pen import config, libraries, library_scan
    from pen.sandbox import parse_vault_root, reading_roots

    lib = tmp_path / ".pen" / "libraries"
    lib.mkdir(parents=True)
    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    monkeypatch.setattr(config, "LIBRARIES_DIR", lib)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    monkeypatch.setattr(config, "parse_dotenv", lambda *a, **k: {})
    library_scan._CACHE.clear()

    vault = tmp_path / "vault"
    vault.mkdir()
    cur = vault / "手册甲.md"
    cur.write_text("# 手册甲\n\n## 开篇\n", encoding="utf-8")
    other = vault / "手册乙.md"
    other.write_text("# 手册乙\n\n## 第一章\n", encoding="utf-8")

    # 插件登记：allow_root 来自请求体的 vault_root，不是环境变量
    roots = parse_vault_root(str(vault))
    libraries.register(str(cur), "book-a", extra_roots=roots)
    libraries.register(str(other), "book-b", extra_roots=roots)
    assert libraries.get("book-a").allow_root == str(vault.resolve())
    assert config.handbook_allow_roots() == [config.REPO_ROOT.resolve()], "前提变了"

    got = library_scan.shelf_digest(
        cur,
        [m.original_path for m in libraries.list_handbooks()],
        allow_roots=reading_roots(cur, libraries.extra_roots_for("book-a")),
        with_paths=True,
    )
    assert "手册乙" in got and str(other) in got, f"标准部署下书架是空的：{got!r}"
