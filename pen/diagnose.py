"""Deterministic weak-spot report from persisted turns. No handbook writes."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

WEAK_MIN_HITS = 2
CHIP_WEIGHT = {
    "explain_zero": 2,
    "examples": 2,
    "free": 2,
    "socratic": 1,
}

_SKIP_LEVELS = {"封面", "开篇", "Capstone", "附录"}
_TICK = re.compile(r"`([^`]+)`")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{1,40}")
_HINT_TOKENS = (
    "export",
    "source",
    "runtime",
    "append",
    "messages",
    "tool_calls",
    "shell=True",
    "chmod",
    "heredoc",
    "dispatch",
    "approval",
    "execute-auto",
    "HARD_DENY",
    "rm -rf",
    "create",
    "subprocess",
)


def is_curriculum(anchor: dict[str, Any] | None) -> bool:
    if not anchor:
        return False
    kind = str(anchor.get("kind") or "")
    level = str(anchor.get("level") or "")
    if level in _SKIP_LEVELS:
        return False
    if kind == "q":
        return True
    return kind == "teaching" and level.startswith("Level")


def atom_key(anchor: dict[str, Any]) -> str:
    kind = str(anchor.get("kind") or "other")
    level = str(anchor.get("level") or "")
    title = str(anchor.get("q_title") or anchor.get("beat") or level)
    return f"{kind}|{level}|{title}"


def label_of(anchor: dict[str, Any]) -> str:
    q = str(anchor.get("q_title") or "").replace("**", "").strip()
    if q:
        return q
    beat = anchor.get("beat")
    level = str(anchor.get("level") or "")
    if beat:
        return f"{level} · {beat}"
    return level or "未定位"


def extract_keywords(*texts: str) -> list[str]:
    blob = "\n".join(t for t in texts if t)
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        token = raw.strip().strip("`")
        if not token or len(token) > 48:
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(token)

    for m in _TICK.finditer(blob):
        add(m.group(1))
    low = blob.lower()
    for hint in _HINT_TOKENS:
        if hint.lower() in low:
            add(hint)
    if not found:
        for m in _IDENT.finditer(blob):
            word = m.group(0)
            if word.lower() in {"the", "and", "for", "with", "this", "that"}:
                continue
            add(word)
            if len(found) >= 8:
                break
    return found[:8]


def aggregate(turns: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    level_hits: dict[str, int] = defaultdict(int)
    n_curriculum = 0

    for ev in turns:
        anchor = ev.get("anchor") if isinstance(ev.get("anchor"), dict) else {}
        if not is_curriculum(anchor):
            continue
        n_curriculum += 1
        key = atom_key(anchor)
        chip = str(ev.get("chip") or "socratic")
        w = CHIP_WEIGHT.get(chip, 1)
        words = extract_keywords(
            str(anchor.get("q_title") or ""),
            str(anchor.get("selected_text") or ""),
            str(ev.get("user_text") or ""),
        )
        slot = buckets.get(key)
        if slot is None:
            slot = {
                "key": key,
                "level": str(anchor.get("level") or ""),
                "kind": str(anchor.get("kind") or ""),
                "label": label_of(anchor),
                "q_title": anchor.get("q_title"),
                "beat": anchor.get("beat"),
                "hits": 0,
                "weight": 0,
                "keywords": [],
                "start_line": anchor.get("start_line"),
            }
            buckets[key] = slot
        slot["hits"] += 1
        slot["weight"] += w
        slot["start_line"] = anchor.get("start_line") or slot["start_line"]
        for word in words:
            if word not in slot["keywords"]:
                slot["keywords"].append(word)
        slot["keywords"] = slot["keywords"][:8]
        level_hits[str(anchor.get("level") or "")] += 1

    denom = max(n_curriculum, 1)
    spots = sorted(
        buckets.values(),
        key=lambda s: (-int(s["hits"]), -int(s["weight"]), str(s["label"])),
    )
    for spot in spots:
        spot["pct"] = round(100.0 * int(spot["hits"]) / denom, 1)

    levels = [
        {
            "level": lv,
            "hits": n,
            "pct": round(100.0 * n / denom, 1),
        }
        for lv, n in sorted(level_hits.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    weak = [s for s in spots if int(s["hits"]) >= WEAK_MIN_HITS]
    footprints = [s for s in spots if int(s["hits"]) < WEAK_MIN_HITS]
    return {
        "n_turns": len(turns),
        "n_curriculum": n_curriculum,
        "levels": levels,
        "weak": weak,
        "footprints": footprints,
    }


def narrate_prompt(report: dict[str, Any]) -> tuple[str, str]:
    slim = {
        "levels": report.get("levels") or [],
        "weak": [
            {
                "level": s.get("level"),
                "label": s.get("label"),
                "hits": s.get("hits"),
                "pct": s.get("pct"),
                "keywords": s.get("keywords") or [],
            }
            for s in (report.get("weak") or [])[:8]
        ],
    }
    system = (
        "你是坐在读者旁边的师傅。根据计数报告写不超过 8 句中文评语，"
        "指出短板挂在哪几道手册 Q / 哪一关上。不要编造报告里没有的题目，"
        "不要索要原话，不要提写回手册。"
    )
    user = "报告（只有计数和关键词）：\n" + json.dumps(
        slim, ensure_ascii=False, indent=2
    )
    return system, user


def narrate(report: dict[str, Any]) -> str:
    from pen.config import resolve_llm

    cfg = resolve_llm()
    if cfg is None:
        raise RuntimeError("没有模型配置，只显示计数报告")
    from openai import OpenAI

    system, user = narrate_prompt(report)
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=60.0)
    resp = client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        stream=False,
    )
    return (resp.choices[0].message.content or "").strip()
