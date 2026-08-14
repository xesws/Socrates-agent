"""原文快照。只用于回退，不当阅读/编辑对象。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

from pen.config import LIBRARIES_DIR, SNAPSHOT_KEEP
from pen.sandbox import assert_write_target


def snapshot_dir(handbook_id: str) -> Path:
    d = LIBRARIES_DIR / handbook_id / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def take_snapshot(handbook_id: str, original_path: Path, reason: str) -> Path:
    src = assert_write_target(original_path, original_path)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "-" for c in reason)[:40]
    dest = snapshot_dir(handbook_id) / f"{ts}-{safe_reason}.md"
    shutil.copy2(src, dest)
    _prune(handbook_id)
    return dest


def latest_snapshot(handbook_id: str) -> Path | None:
    files = sorted(snapshot_dir(handbook_id).glob("*.md"))
    return files[-1] if files else None


def rollback(handbook_id: str, original_path: Path) -> Path:
    """用最近一份快照覆盖回原文。"""
    target = assert_write_target(original_path, original_path)
    snap = latest_snapshot(handbook_id)
    if snap is None:
        raise FileNotFoundError(f"没有可回退的快照：{handbook_id}")
    shutil.copy2(snap, target)
    return snap


def _prune(handbook_id: str) -> None:
    files = sorted(snapshot_dir(handbook_id).glob("*.md"))
    extra = files[:-SNAPSHOT_KEEP] if len(files) > SNAPSHOT_KEEP else []
    for f in extra:
        f.unlink(missing_ok=True)
