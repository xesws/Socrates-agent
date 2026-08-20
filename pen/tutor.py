"""对话脑：L2 外环 + registry 工具。edit_file 须人批；无 bash / write_file。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pen.i18n import msg
from pen.config import LLMConfig, REPO_ROOT, resolve_llm
from pen.index import HandbookIndex, neighborhood
from pen.agent.permissions import decide, read_first_block
from pen.agent.registry import dispatch, schemas
from pen.sandbox import resolve_read_target
from pen.session import PenSession

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
    "writeback": "必须先 read_file 看准带行号的原文，下一轮再单独 edit_file。old_string 去掉 N\\t。不要声称已经写盘。",
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


def usage_snapshot(prompt: int, completion: int) -> dict[str, int]:
    """最后一次 LLM 调用的用量。context_tokens = 此刻喂进去的上下文。"""
    p = int(prompt or 0)
    c = int(completion or 0)
    return {"context_tokens": p, "completion_tokens": c, "prompt_tokens": p}


class ProviderError(RuntimeError):
    """供应商调用失败。message 已是给用户看的中文，不含 key。"""


def provider_error_message(exc: BaseException, lang: str = "zh") -> str:
    """OpenAI / 网络异常 → 给用户看的一句话。不带 key，不贴原始报文。"""
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return msg("provider.bad_key", lang)
    if status == 400:
        return msg("provider.bad_thinking", lang)
    from openai import APIConnectionError, APITimeoutError

    if isinstance(exc, (APIConnectionError, APITimeoutError, OSError, TimeoutError)):
        return msg("provider.unreachable", lang)
    return msg("provider.unexpected", lang, kind=type(exc).__name__)


def _finish_text(session: PenSession, raw: str, usage: dict[str, int]) -> Iterator[dict[str, Any]]:
    visible, dyn = parse_dynamic_chips(raw)
    session.last_assistant = visible
    if len(visible) > 80:
        session.has_substantive = True
    yield {"type": "status", "phase": "writing", "text": "在写…"}
    for i in range(0, len(visible), 48):
        yield {"type": "token", "text": visible[i : i + 48]}
    yield {
        "type": "done",
        "usage": usage,
        "dynamic_chips": dyn,
        "has_substantive": session.has_substantive,
    }


def llm_create_kwargs(
    cfg: LLMConfig,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """组 chat.completions.create 参数。thinking=off 不加推理字段。"""
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        kwargs["tools"] = tools
    if cfg.thinking != "off":
        kwargs["reasoning_effort"] = cfg.thinking
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return kwargs


def _tool_ctx(
    session: PenSession,
    original_path: Path,
    extra_roots: list[Path],
) -> dict[str, Any]:
    return {
        "original_path": original_path,
        "extra_roots": extra_roots,
        "handbook_id": session.handbook_id,
        "read_ok": {Path(p).expanduser().resolve() for p in session.read_ok_paths},
    }


def _remember_read(session: PenSession, ctx: dict[str, Any], out: dict[str, Any]) -> None:
    if not out.get("ok") or not out.get("resolved"):
        return
    got = Path(str(out["resolved"])).expanduser().resolve()
    ctx.setdefault("read_ok", set()).add(got)
    known = {Path(p).expanduser().resolve() for p in session.read_ok_paths}
    known.add(got)
    session.read_ok_paths = [str(p) for p in known]


def stream_chat(
    session: PenSession,
    original_path: Path,
    user_packet: str,
    llm: LLMConfig | None = None,
    extra_roots: list[Path] | None = None,
    allow_env_fallback: bool = True,
    lang: str = "zh",
) -> Iterator[dict[str, Any]]:
    # 请求换了主机却没带 key 时 merge_llm 会返回 None；不能再 or resolve_llm() 把 .env 钥匙挪用过去。
    cfg = llm if llm is not None else (resolve_llm() if allow_env_fallback else None)
    if cfg is None:
        yield {
            "type": "error",
            "message": msg("llm.missing_config", lang),
        }
        return

    session.messages.append({"role": "user", "content": user_packet})

    extra_roots = [REPO_ROOT, *(extra_roots or [])]
    ctx = _tool_ctx(session, original_path, extra_roots)
    yield from _agent_loop(session, ctx, cfg, lang)


def _agent_loop(
    session: PenSession,
    ctx: dict[str, Any],
    cfg: LLMConfig,
    lang: str = "zh",
) -> Iterator[dict[str, Any]]:
    from openai import OpenAI, OpenAIError

    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=120.0)
    tools = schemas()
    usage = usage_snapshot(0, 0)

    def _create(*, with_tools: bool) -> Any:
        kwargs = llm_create_kwargs(
            cfg,
            messages=session.messages,
            tools=tools if with_tools else None,
        )
        try:
            resp = client.chat.completions.create(**kwargs)
        except (OpenAIError, OSError, TimeoutError) as exc:
            raise ProviderError(provider_error_message(exc, lang)) from exc
        if resp.usage:
            usage.update(
                usage_snapshot(
                    resp.usage.prompt_tokens or 0,
                    resp.usage.completion_tokens or 0,
                )
            )
        return resp.choices[0].message

    for _step in range(MAX_TOOL_ROUNDS):
        yield {"type": "status", "phase": "thinking", "text": "师傅在想…"}
        try:
            msg = _create(with_tools=True)
        except ProviderError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        session.messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            raw = (msg.content or "").strip()
            if not raw:
                yield {
                    "type": "error",
                    "message": msg("llm.empty_reply", lang, model=cfg.model, base_url=cfg.base_url),
                }
                return
            yield from _finish_text(session, raw, usage)
            return
        yield {"type": "status", "phase": "reading", "text": "在翻手册…"}
        paused = yield from _run_tool_batch(session, ctx, list(msg.tool_calls))
        if paused:
            return

    session.messages.append({"role": "user", "content": FORCE_ANSWER})
    yield {"type": "status", "phase": "thinking", "text": "师傅在想…"}
    try:
        msg = _create(with_tools=False)
    except ProviderError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    session.messages.append(msg.model_dump(exclude_none=True))
    raw = (msg.content or "").strip()
    if not raw:
        yield {
            "type": "error",
            "message": msg("loop.exhausted", lang),
        }
        return
    yield from _finish_text(session, raw, usage)


def _tool_event(name: str, out: dict[str, Any]) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "type": "tool",
        "name": name,
        "detail": str(out.get("detail") or ""),
        "resolved": str(out.get("resolved") or ""),
        "ok": bool(out.get("ok")),
        "preview": str(out.get("text") or "")[:200],
    }
    if out.get("line"):
        ev["line"] = int(out["line"])
    return ev


def _run_one(
    session: PenSession,
    ctx: dict[str, Any],
    *,
    name: str,
    args: dict[str, Any],
    tool_call_id: str,
) -> dict[str, Any]:
    out = dispatch(name, args, ctx)
    if name == "read_file":
        _remember_read(session, ctx, out)
    session.messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": str(out.get("text") or ""),
        }
    )
    return _tool_event(name, out)


def _run_tool_batch(
    session: PenSession,
    ctx: dict[str, Any],
    calls: list[Any],
) -> Iterator[dict[str, Any]]:
    """执行到第一个需要审批的工具则暂停。yields events；return True 表示已 pause。"""
    paused = False
    read_before = {Path(p).expanduser().resolve() for p in (ctx.get("read_ok") or [])}
    for i, tc in enumerate(calls):
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError as exc:
            result = f"错误：工具参数不是合法 JSON：{exc}"
            yield {"type": "tool", "name": name, "detail": "", "ok": False, "preview": result[:200]}
            session.messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
            continue
        verdict = decide(name)
        if verdict == "deny":
            result = f"错误：未知或禁用的工具 {name}"
            yield {"type": "tool", "name": name, "detail": "", "ok": False, "preview": result[:200]}
            session.messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
            continue
        if verdict == "ask":
            original = Path(ctx["original_path"])
            raw = str(args.get("path") or "").strip() or str(original)
            tried = resolve_read_target(original, raw)
            blocked = read_first_block(name, tried, read_before)
            if blocked:
                yield {
                    "type": "tool",
                    "name": name,
                    "detail": raw,
                    "ok": False,
                    "preview": blocked[:200],
                }
                session.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": blocked}
                )
                continue
            rest = []
            for later in calls[i + 1 :]:
                rest.append(
                    {
                        "id": later.id,
                        "name": later.function.name,
                        "arguments": later.function.arguments or "{}",
                    }
                )
            session.pending = {
                "id": uuid.uuid4().hex,
                "name": name,
                "args": args,
                "tool_call_id": tc.id,
                "rest": rest,
                "original_path": str(Path(ctx["original_path"]).expanduser().resolve()),
            }
            yield {
                "type": "approval",
                "pending_id": session.pending["id"],
                "name": name,
                "args": args,
            }
            paused = True
            break
        yield {"type": "status", "phase": "tool", "text": f"在用 {name}…"}
        yield _run_one(session, ctx, name=name, args=args, tool_call_id=tc.id)
    return paused


def resume_chat(
    session: PenSession,
    original_path: Path,
    *,
    allow: bool,
    pending_id: str,
    llm: LLMConfig | None = None,
    extra_roots: list[Path] | None = None,
    allow_env_fallback: bool = True,
    lang: str = "zh",
) -> Iterator[dict[str, Any]]:
    pending = session.pending
    if not pending or pending.get("id") != pending_id:
        yield {"type": "error", "message": msg("approval.expired", lang)}
        return
    cfg = llm if llm is not None else (resolve_llm() if allow_env_fallback else None)
    if cfg is None:
        yield {
            "type": "error",
            "message": msg("llm.missing_config_short", lang),
        }
        return
    frozen = str(pending.get("original_path") or "").strip()
    if frozen:
        frozen_p = Path(frozen).expanduser().resolve()
        if frozen_p != original_path.expanduser().resolve():
            tcid_bad = str(pending.get("tool_call_id") or "")
            result = msg("approval.path_changed", lang)
            session.messages.append(
                {"role": "tool", "tool_call_id": tcid_bad, "content": result}
            )
            session.pending = None
            yield {"type": "error", "message": result}
            return
    extra_roots = [REPO_ROOT, *(extra_roots or [])]
    ctx = _tool_ctx(session, original_path, extra_roots)
    name = str(pending.get("name") or "")
    args = dict(pending.get("args") or {})
    tcid = str(pending.get("tool_call_id") or "")
    run_it = bool(allow) and decide(name) == "ask"
    if run_it:
        yield {"type": "status", "phase": "writing", "text": "在改原文…"}
        try:
            yield _run_one(session, ctx, name=name, args=args, tool_call_id=tcid)
        except Exception as exc:
            result = f"错误：编辑失败：{type(exc).__name__}"
            session.messages.append(
                {"role": "tool", "tool_call_id": tcid, "content": result}
            )
            yield {
                "type": "tool",
                "name": name,
                "detail": str(args.get("path") or ""),
                "ok": False,
                "preview": result,
            }
    else:
        if allow:
            result = f"错误：不能执行未审批的工具 {name}"
        else:
            result = "师傅不允许这次编辑，原文没动。"
        yield {
            "type": "tool",
            "name": name,
            "detail": str(args.get("path") or ""),
            "ok": False,
            "preview": result,
        }
        session.messages.append(
            {"role": "tool", "tool_call_id": tcid, "content": result}
        )
    session.pending = None
    rest = list(pending.get("rest") or [])
    if rest:
        class _Fn:
            def __init__(self, n: str, a: str) -> None:
                self.name = n
                self.arguments = a

        class _Tc:
            def __init__(self, rec: dict[str, Any]) -> None:
                self.id = rec.get("id") or ""
                self.function = _Fn(str(rec.get("name") or ""), str(rec.get("arguments") or "{}"))

        paused = yield from _run_tool_batch(session, ctx, [_Tc(r) for r in rest])
        if paused:
            return
    yield from _agent_loop(session, ctx, cfg, lang)


def propose_fold_md(
    session: PenSession,
    llm: LLMConfig | None = None,
    allow_env_fallback: bool = True,
    lang: str = "zh",
) -> str:
    cfg = llm if llm is not None else (resolve_llm() if allow_env_fallback else None)
    if cfg is None:
        raise RuntimeError(msg("llm.missing_config_fold", lang))
    from openai import OpenAI, OpenAIError

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
    try:
        resp = client.chat.completions.create(
            **llm_create_kwargs(
                cfg,
                messages=[
                    {"role": "system", "content": "你只输出一个合法的 <details> Markdown 块。"},
                    {"role": "user", "content": prompt},
                ],
            )
        )
    except (OpenAIError, OSError, TimeoutError) as exc:
        raise ProviderError(provider_error_message(exc, lang)) from exc
    return (resp.choices[0].message.content or "").strip()
