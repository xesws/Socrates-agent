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
