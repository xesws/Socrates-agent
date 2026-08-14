"""对话脑：L2 外环 + 可选只读 read_file。不写盘。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pen.config import REPO_ROOT, resolve_llm
from pen.index import HandbookIndex, neighborhood
from pen.readtool import read_file_report
from pen.session import PenSession

READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "按行读取当前手册原文或同仓库对照文件。offset 从 1 起。"
            "path 优先复制 [来源] handbook_path；相对路径相对手册目录。"
            "读完该回答时用自然语言收工，不要空转同一段。"
            "不要读 ~/.zshrc、/etc、.env。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 80},
            },
            "required": ["path"],
        },
    },
}

MAX_TOOL_ROUNDS = 50
FORCE_ANSWER = (
    "工具次数用完了。根据邻域和你已经读到的内容，直接用自然语言回答读者。"
    "不要再调用任何工具。"
)

CHIP_INTENT = {
    "socratic": "先别揭晓。只问一个问题，帮读者自己想。",
    "explain_zero": "假设读者零基础。按 TL;DR → (a)(b)(c) 讲完，再给两个可运行例子。",
    "examples": "只举两个例子，紧贴本 Level 第七拍的名字。",
    "search": "（未开放）不要假装检索。告诉读者 P2 才有联网。",
    "writeback": "把刚才的实质解答收成一个可插入的 <details> Meta Instance。不要声称已经写入磁盘。",
    "free": "按用户原话回答，仍守师傅人设。",
}


def build_user_packet(
    idx: HandbookIndex,
    original_path: Path,
    *,
    selected_text: str,
    start_line: int,
    end_line: int,
    chip: str,
    user_text: str,
) -> tuple[str, dict[str, Any]]:
    section = idx.locate(start_line)
    nb = neighborhood(original_path, section, (start_line, end_line))
    toc_lines = []
    for t in idx.toc:
        if t.beat is None:
            toc_lines.append(f"- {t.level}  L{t.start_line}  {t.heading}")
        else:
            toc_lines.append(f"  - {t.level} / {t.beat}  L{t.start_line}")
    toc = "\n".join(toc_lines[:80])
    packet = f"""[来源]
handbook_path: {original_path}
level: {section.level}
beat: {section.beat or "—"}
q_title: {section.q_title or "—"}
kind: {section.kind}
lines: {start_line}-{end_line}

[全书目录（不要整本背诵）]
{toc}

[框选]
{selected_text}

[邻域]
{nb}

[意图]
chip = {chip}
{CHIP_INTENT.get(chip, CHIP_INTENT["free"])}

[用户补充]
{user_text or "（无，按芯片意图行动）"}
"""
    anchor = {
        "path": str(original_path),
        "level": section.level,
        "beat": section.beat,
        "q_title": section.q_title,
        "kind": section.kind,
        "start_line": start_line,
        "end_line": end_line,
        "selected_text": selected_text,
    }
    return packet, anchor


def parse_dynamic_chips(reply: str) -> tuple[str, list[str]]:
    marker = "<!--pen:chips"
    if marker not in reply:
        return reply.strip(), []
    head, rest = reply.split(marker, 1)
    end = rest.find("-->")
    block = rest[:end] if end >= 0 else rest
    visible = head.strip()
    chips: list[str] = []
    for line in block.splitlines():
        line = line.strip().lstrip("-").strip()
        if line:
            chips.append(line)
    return visible, chips[:4]


def _finish_text(session: PenSession, raw: str, usage: dict[str, int]) -> Iterator[dict[str, Any]]:
    visible, dyn = parse_dynamic_chips(raw)
    session.last_assistant = visible
    if len(visible) > 80:
        session.has_substantive = True
    for i in range(0, len(visible), 48):
        yield {"type": "token", "text": visible[i : i + 48]}
    yield {
        "type": "done",
        "usage": usage,
        "dynamic_chips": dyn,
        "has_substantive": session.has_substantive,
    }


def stream_chat(session: PenSession, original_path: Path, user_packet: str) -> Iterator[dict[str, Any]]:
    cfg = resolve_llm()
    if cfg is None:
        yield {
            "type": "error",
            "message": (
                "找不到模型配置。请在仓库根 .env 写入 DEEPSEEK_API_KEY，"
                "或 export OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME（DeepSeek）。"
            ),
        }
        return

    from openai import OpenAI

    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=120.0)
    session.messages.append({"role": "user", "content": user_packet})

    extra_roots = [REPO_ROOT]
    tools = [READ_FILE_SCHEMA]
    usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def _create(*, with_tools: bool) -> Any:
        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "messages": session.messages,
            "stream": False,
        }
        if with_tools:
            kwargs["tools"] = tools
        resp = client.chat.completions.create(**kwargs)
        if resp.usage:
            usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
            usage["completion_tokens"] += resp.usage.completion_tokens or 0
        return resp.choices[0].message

    for _step in range(MAX_TOOL_ROUNDS):
        msg = _create(with_tools=True)
        session.messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            raw = (msg.content or "").strip()
            if not raw:
                yield {
                    "type": "error",
                    "message": f"模型 {cfg.model} 返回了空正文（已接上 {cfg.base_url}）。",
                }
                return
            yield from _finish_text(session, raw, usage)
            return
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                result = f"错误：工具参数不是合法 JSON：{exc}"
                yield {
                    "type": "tool",
                    "name": tc.function.name,
                    "detail": "",
                    "ok": False,
                    "preview": result[:200],
                }
                session.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
                continue
            if tc.function.name == "read_file":
                raw_path = (args.get("path") or "").strip() or str(original_path)
                report = read_file_report(
                    original_path,
                    raw_path,
                    offset=int(args.get("offset", 1) or 1),
                    limit=int(args.get("limit", 80) or 80),
                    extra_roots=extra_roots,
                )
                result = str(report["text"])
                yield {
                    "type": "tool",
                    "name": "read_file",
                    "detail": raw_path,
                    "resolved": report["resolved"],
                    "ok": bool(report["ok"]),
                    "preview": result[:200],
                }
            else:
                result = f"错误：未知工具 {tc.function.name}"
                yield {
                    "type": "tool",
                    "name": tc.function.name,
                    "detail": str(args.get("path") or ""),
                    "ok": False,
                    "preview": result[:200],
                }
            session.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

    session.messages.append({"role": "user", "content": FORCE_ANSWER})
    msg = _create(with_tools=False)
    session.messages.append(msg.model_dump(exclude_none=True))
    raw = (msg.content or "").strip()
    if not raw:
        yield {
            "type": "error",
            "message": "翻了几页还没收工。请把问题问得更具体一点，或再点一次芯片。",
        }
        return
    yield from _finish_text(session, raw, usage)


def propose_fold_md(session: PenSession) -> str:
    cfg = resolve_llm()
    if cfg is None:
        raise RuntimeError("找不到模型配置，无法生成折叠块")
    from openai import OpenAI

    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=120.0)
    prompt = f"""把下面师傅刚讲的内容收成一个可插入手册的 Meta Instance。
只输出一个 <details> 块，不要 TL;DR/(a)(b)(c)，不要 〔回读〕。
<summary> 用「🔍 实例 N：一句话看点」（N 可先写 1，后端会改号）。
<details> 后空行，</summary> 后空行，</details> 前空行。
深度至少有一段 ```text 伪代码；有概念关系就加 ```mermaid。
教学代码函数要有 -> 返回类型。终端输出若不是实测，标「示意」。

解答正文：
{session.last_assistant}
"""
    resp = client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": "你只输出一个合法的 <details> Markdown 块。"},
            {"role": "user", "content": prompt},
        ],
    )
    return (resp.choices[0].message.content or "").strip()
