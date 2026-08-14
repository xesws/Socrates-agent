"""登记本机原始教材路径。不复制正文。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from pen.config import (
    DEFAULT_HANDBOOK,
    DEFAULT_HANDBOOK_ID,
    LIBRARIES_DIR,
    ensure_pen_dirs,
)
from pen.index import HandbookIndex, build_index, save_index


@dataclass
class HandbookMeta:
    handbook_id: str
    title: str
    original_path: str
    imported_at: str
    mtime: float


def _meta_path(handbook_id: str) -> Path:
    return LIBRARIES_DIR / handbook_id / "meta.json"


def _index_path(handbook_id: str) -> Path:
    return LIBRARIES_DIR / handbook_id / "index.json"


def register(original_path: str | Path, handbook_id: str | None = None) -> HandbookMeta:
    ensure_pen_dirs()
    path = Path(original_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到教材：{path}")
    hid = handbook_id or _suggest_id(path)
    idx = build_index(path)
    meta = HandbookMeta(
        handbook_id=hid,
        title=idx.title,
        original_path=str(path),
        imported_at=datetime.now(timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
    )
    dest = LIBRARIES_DIR / hid
    dest.mkdir(parents=True, exist_ok=True)
    _meta_path(hid).write_text(
        json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    save_index(idx, _index_path(hid))
    return meta


def ensure_default() -> HandbookMeta:
    if DEFAULT_HANDBOOK.is_file():
        existing = get(DEFAULT_HANDBOOK_ID)
        if existing and Path(existing.original_path).resolve() == DEFAULT_HANDBOOK.resolve():
            return refresh_if_stale(DEFAULT_HANDBOOK_ID)
        return register(DEFAULT_HANDBOOK, DEFAULT_HANDBOOK_ID)
    raise FileNotFoundError(f"默认手册不存在：{DEFAULT_HANDBOOK}")


def list_handbooks() -> list[HandbookMeta]:
    ensure_pen_dirs()
    out: list[HandbookMeta] = []
    for meta_file in LIBRARIES_DIR.glob("*/meta.json"):
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        out.append(HandbookMeta(**data))
    out.sort(key=lambda m: m.imported_at)
    return out


def get(handbook_id: str) -> HandbookMeta | None:
    p = _meta_path(handbook_id)
    if not p.is_file():
        return None
    return HandbookMeta(**json.loads(p.read_text(encoding="utf-8")))


def load_index(handbook_id: str) -> HandbookIndex:
    meta = get(handbook_id)
    if meta is None:
        raise KeyError(handbook_id)
    refresh_if_stale(handbook_id)
    return HandbookIndex.from_json(json.loads(_index_path(handbook_id).read_text(encoding="utf-8")))


def refresh_if_stale(handbook_id: str) -> HandbookMeta:
    meta = get(handbook_id)
    if meta is None:
        raise KeyError(handbook_id)
    path = Path(meta.original_path)
    if not path.is_file():
        raise FileNotFoundError(f"原文消失：{path}")
    mtime = path.stat().st_mtime
    if abs(mtime - meta.mtime) > 0.001 or not _index_path(handbook_id).is_file():
        idx = build_index(path)
        meta.mtime = mtime
        meta.title = idx.title
        _meta_path(handbook_id).write_text(
            json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        save_index(idx, _index_path(handbook_id))
    return meta


def _suggest_id(path: Path) -> str:
    stem = path.stem
    slug = "".join(c.lower() if c.isalnum() else "-" for c in stem).strip("-")
    return slug or "handbook"
