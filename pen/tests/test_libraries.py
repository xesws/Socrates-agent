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
