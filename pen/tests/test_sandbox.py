from __future__ import annotations

from pathlib import Path

import pytest

from pen.config import DEFAULT_HANDBOOK, REPO_ROOT
from pen.sandbox import SandboxError, assert_handbook_path, assert_readable, assert_write_target


def test_write_only_original(tmp_path: Path) -> None:
    original = tmp_path / "book.md"
    other = tmp_path / "other.md"
    original.write_text("a", encoding="utf-8")
    other.write_text("b", encoding="utf-8")
    assert assert_write_target(original, original) == original.resolve()
    with pytest.raises(SandboxError):
        assert_write_target(original, other)
    with pytest.raises(SandboxError):
        assert_write_target(original, "/etc/passwd")


def test_read_allowlist(tmp_path: Path) -> None:
    original = tmp_path / "book.md"
    sibling = tmp_path / "lab" / "notes.txt"
    sibling.parent.mkdir()
    original.write_text("a", encoding="utf-8")
    sibling.write_text("b", encoding="utf-8")
    outsider = tmp_path.parent / "nope.md"
    outsider.write_text("x", encoding="utf-8")
    assert assert_readable(original, original)
    assert assert_readable(original, sibling, extra_roots=[tmp_path])
    with pytest.raises(SandboxError):
        assert_readable(original, outsider, extra_roots=[tmp_path])
    with pytest.raises(SandboxError):
        assert_readable(original, tmp_path / ".env")


def test_relative_not_tied_to_cwd(tmp_path: Path, monkeypatch) -> None:
    original = tmp_path / "book.md"
    original.write_text("a", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    got = assert_readable(original, "book.md")
    assert got == original.resolve()


def test_handbook_path_allows_default_and_rejects_outsiders(tmp_path: Path, monkeypatch) -> None:
    assert assert_handbook_path(DEFAULT_HANDBOOK) == DEFAULT_HANDBOOK.resolve()
    outsider = tmp_path / "secret.md"
    outsider.write_text("# x\n", encoding="utf-8")
    with pytest.raises(SandboxError, match="允许的根"):
        assert_handbook_path(outsider)
    py = REPO_ROOT / "pen" / "app.py"
    with pytest.raises(SandboxError, match="Markdown"):
        assert_handbook_path(py)
    env = tmp_path / ".env"
    env.write_text("K=1\n", encoding="utf-8")
    with pytest.raises(SandboxError, match="受保护"):
        assert_handbook_path(env)
    monkeypatch.setenv("PEN_ALLOW_ROOTS", str(tmp_path))
    assert assert_handbook_path(outsider) == outsider.resolve()
