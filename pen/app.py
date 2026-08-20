"""FastAPI：阅读原文、就地问、确认后原地写回 original_path。"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pen import gitops
from pen import insert as insertmod
from pen.outline import file_outline
from pen import libraries, snapshots
from pen import diagnose as diagnosemod
from pen import proposals as proposalsmod
from pen import trajectory
from pen.config import DEFAULT_HANDBOOK_ID, LLMConfig, llm_public_status, merge_llm
from pen.i18n import localized, msg, norm_lang
from pen.libraries import RegisterError
from pen.sandbox import SandboxError, assert_handbook_path, parse_vault_root
from pen.session import FIXED_CHIPS, STORE, chip_label
from pen.tutor import ProviderError, build_user_packet, propose_fold_md, resume_chat, stream_chat

SEARCH_REPLY = (
    "论文检索还没开。这是诚实挂起：P2 才有联网，"
    "现在不会假装搜过，也不会往诊断轨迹里记一笔假检索。"
)

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    libraries.ensure_default()
    yield


app = FastAPI(title="Socratic Pen", version="0.8.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        "app://obsidian.md",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_proposals: dict[str, dict[str, Any]] = {}


class LlmOverrideBody(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    thinking: str | None = None

    def merged(self) -> LLMConfig | None:
        return merge_llm(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            thinking=self.thinking,
        )


class ImportBody(BaseModel):
    original_path: str
    handbook_id: str | None = None
    vault_root: str | None = None


class SessionBody(BaseModel):
    handbook_id: str = DEFAULT_HANDBOOK_ID
    session_id: str | None = None


class ApproveBody(LlmOverrideBody):
    session_id: str
    pending_id: str
    allow: bool


class ChatBody(LlmOverrideBody):
    session_id: str
    selected_text: str
    start_line: int
    end_line: int
    chip: str = "socratic"
    user_text: str = ""


class ProposeBody(LlmOverrideBody):
    session_id: str
    summary_hint: str | None = None


class RetargetBody(BaseModel):
    proposal_id: str
    kind: str = "auto"
    after_line: int | None = None
    heading_start_line: int | None = None
    q_start_line: int | None = None
    range_start: int | None = None
    range_end: int | None = None


class ApplyBody(BaseModel):
    session_id: str
    proposal_id: str
    commit: bool = False
    commit_message: str | None = None


class RollbackBody(BaseModel):
    handbook_id: str


def req_lang(accept_language: str | None = Header(default=None)) -> str:
    """请求语言。走 Accept-Language 头而不是 body 字段——GET 路由也能覆盖，
    而且不用给七个 Pydantic 模型各加一遍。"""
    return norm_lang(accept_language)


def _meta_or_404(handbook_id: str, lang: str = "zh"):
    meta = libraries.get(handbook_id)
    if meta is None:
        raise HTTPException(404, msg("handbook.unknown", lang, handbook_id=handbook_id))
    try:
        return libraries.refresh_if_stale(handbook_id)
    except FileNotFoundError as exc:
        # 用户在 Obsidian 里重命名或移走了已登记的笔记。以前这里没人接，
        # 直接冒成 500 Internal Server Error。
        raise HTTPException(404, localized(exc, lang)) from exc


def _sse(ev: dict[str, Any]) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def _try_lock_session(session_id: str, lang: str = "zh"):
    lock = STORE.lock_for(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, msg("session.busy", lang))
    return lock


def _content_fp(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _span(path: Path, start: int, end: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[max(0, start - 1) : end])


def _public_proposal(pid: str, path: Path, plan: insertmod.InsertPlan, diff: str) -> dict[str, Any]:
    return {
        "proposal_id": pid,
        "original_path": str(path),
        "mode": plan.mode,
        "level": plan.level,
        "q_title": plan.q_title,
        "beat": plan.beat,
        "instance_n": plan.instance_n,
        "insert_after_line": plan.insert_after_line,
        "replace_start": plan.replace_start,
        "replace_end": plan.replace_end,
        "fold_md": plan.fold_md,
        "diff": diff,
        "where": insertmod.describe_plan(plan),
    }


def _plan_for_target(
    path: Path,
    fold_md: str,
    sess,
    body: RetargetBody,
) -> insertmod.InsertPlan:
    kind = (body.kind or "auto").strip()
    if kind == "auto":
        if not sess.last_anchor:
            raise insertmod.InsertError("缺少框选锚点")
        idx = libraries.load_index(sess.handbook_id)
        return insertmod.plan_insert(
            idx,
            path,
            line=int(sess.last_anchor["start_line"]),
            fold_md=fold_md,
        )
    if kind == "after_line":
        if body.after_line is None:
            raise insertmod.InsertError("需要 after_line")
        return insertmod.plan_after_line(path, fold_md, int(body.after_line))
    ol = file_outline(path)
    if kind == "after_heading":
        hit = next(
            (h for h in ol["headings"] if h["start_line"] == body.heading_start_line),
            None,
        )
        if hit is None:
            raise insertmod.InsertError("找不到该标题")
        span = _span(path, int(hit["start_line"]), int(hit["end_line"]))
        return insertmod.plan_after_line(
            path,
            fold_md,
            int(hit["end_line"]),
            mode="after_heading",
            beat=str(hit["text"]),
            count_in=span,
        )
    if kind == "after_q":
        hit = next(
            (q for q in ol["questions"] if q["start_line"] == body.q_start_line),
            None,
        )
        if hit is None:
            raise insertmod.InsertError("找不到该问")
        span = _span(path, int(hit["start_line"]), int(hit["end_line"]))
        return insertmod.plan_after_line(
            path,
            fold_md,
            int(hit["insert_after_line"]),
            mode="after_q",
            q_title=str(hit["text"]),
            count_in=span,
        )
    if kind == "replace_heading":
        hit = next(
            (h for h in ol["headings"] if h["start_line"] == body.heading_start_line),
            None,
        )
        if hit is None:
            raise insertmod.InsertError("找不到该标题")
        start, end = int(hit["start_line"]), int(hit["end_line"])
        if end <= start:
            return insertmod.plan_after_line(
                path, fold_md, start, mode="after_heading", beat=str(hit["text"])
            )
        return insertmod.plan_replace_range(
            path,
            fold_md,
            start + 1,
            end,
            mode="replace_heading",
            beat=str(hit["text"]),
        )
    if kind == "replace_range":
        if body.range_start is None or body.range_end is None:
            raise insertmod.InsertError("需要 range_start 和 range_end")
        return insertmod.plan_replace_range(
            path, fold_md, int(body.range_start), int(body.range_end)
        )
    raise insertmod.InsertError(f"未知目标 kind：{kind}")


def _proposal_put(pid: str, rec: dict[str, Any]) -> None:
    _proposals[pid] = rec
    proposalsmod.put(pid, rec)


def _proposal_get(pid: str) -> dict[str, Any] | None:
    rec = _proposals.get(pid)
    if rec is not None:
        return rec
    rec = proposalsmod.get(pid)
    if rec is not None:
        _proposals[pid] = rec
    return rec


def _proposal_del(pid: str) -> None:
    _proposals.pop(pid, None)
    proposalsmod.delete(pid)


@app.get("/v1/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "llm": llm_public_status()}


@app.get("/v1/handbooks")
def list_handbooks() -> dict[str, Any]:
    libraries.ensure_default()
    return {"handbooks": [m.__dict__ for m in libraries.list_handbooks()]}


@app.post("/v1/handbooks/import")
def import_handbook(body: ImportBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    try:
        extra = parse_vault_root(body.vault_root)
        meta = libraries.register(
            body.original_path,
            body.handbook_id,
            extra_roots=extra or None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    except (RegisterError, SandboxError) as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return meta.__dict__


@app.get("/v1/handbooks/{handbook_id}")
def get_handbook(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(handbook_id, lang)
    idx = libraries.load_index(handbook_id)
    return {
        **meta.__dict__,
        "n_lines": idx.n_lines,
        "toc": [t.__dict__ for t in idx.toc],
    }


@app.get("/v1/handbooks/{handbook_id}/content")
def get_content(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(handbook_id, lang)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return {
        "original_path": str(path),
        "text": path.read_text(encoding="utf-8"),
        "mtime": path.stat().st_mtime,
    }


@app.get("/v1/handbooks/{handbook_id}/locate")
def locate(handbook_id: str, line: int, lang: str = Depends(req_lang)) -> dict[str, Any]:
    idx = libraries.load_index(handbook_id)
    try:
        sec = idx.locate(line)
    except ValueError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return sec.__dict__


@app.post("/v1/sessions")
def create_session(body: SessionBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    _meta_or_404(body.handbook_id, lang)
    if body.session_id:
        try:
            sess = STORE.get(body.session_id)
            if sess.handbook_id == body.handbook_id:
                return sess.to_public()
        except KeyError:
            pass
    sess = STORE.create(body.handbook_id, lang=lang)
    return sess.to_public()


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    try:
        sess = STORE.get(session_id)
    except KeyError as exc:
        raise HTTPException(404, msg("session.unknown", lang)) from exc
    return sess.to_public()


@app.post("/v1/chat")
def chat(body: ChatBody, lang: str = Depends(req_lang)) -> StreamingResponse:
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise HTTPException(404, msg("session.unknown", lang)) from exc
    if body.chip == "search":
        if sess.pending:
            raise HTTPException(400, msg("approval.pending", lang))

        def search_gen() -> Any:
            yield _sse({"type": "token", "text": SEARCH_REPLY})
            yield _sse(
                {
                    "type": "done",
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                    "dynamic_chips": [],
                    "has_substantive": False,
                }
            )

        return StreamingResponse(search_gen(), media_type="text/event-stream")
    lock = _try_lock_session(sess.session_id, lang)
    try:
        if sess.pending:
            raise HTTPException(400, msg("approval.pending", lang))
        meta = _meta_or_404(sess.handbook_id, lang)
        idx = libraries.load_index(sess.handbook_id)
        path = Path(meta.original_path)
        try:
            packet, anchor = build_user_packet(
                idx,
                path,
                selected_text=body.selected_text,
                start_line=body.start_line,
                end_line=body.end_line,
                chip=body.chip,
                user_text=body.user_text,
                asked=[str(c.get("text") or "") for c in sess.last_chips],
            )
        except ValueError as exc:
            raise HTTPException(400, localized(exc, lang)) from exc
        sess.last_anchor = anchor
        typed = (body.user_text or "").strip()
        shown = typed or chip_label(body.chip)
        # 点芯片时多存一个 chip id：label 是中文且会落盘，只存文本的话，
        # 英文用户恢复旧会话时自己的历史气泡会是中文。存了 id，前端就能查表。
        row: dict[str, Any] = {"role": "user", "text": shown}
        if not typed:
            row["chip"] = body.chip
        sess.ui_messages.append(row)
        prior_assistant = sess.last_assistant
        STORE.save(sess)
    except Exception:
        lock.release()
        raise

    def gen():
        ok = True
        has_sub = False
        try:
            for ev in stream_chat(
                sess,
                path,
                packet,
                llm=body.merged(),
                extra_roots=libraries.extra_roots_for(sess.handbook_id),
                allow_env_fallback=not bool((body.base_url or "").strip()),
                lang=lang,
                user_text=body.user_text,
            ):
                if ev.get("type") == "done":
                    has_sub = bool(ev.get("has_substantive"))
                elif ev.get("type") == "error":
                    ok = False
                yield _sse(ev)
        except ProviderError as exc:
            ok = False
            yield _sse({"type": "error", "message": str(exc)})
        except Exception:
            ok = False
            yield _sse({"type": "error", "message": msg("chat.unexpected", lang)})
        finally:
            if sess.last_assistant and sess.last_assistant != prior_assistant:
                sess.ui_messages.append({"role": "assistant", "text": sess.last_assistant})
            # 轮次不能挂在「回复内容变了」上：模型偶尔会把同一段话再说一遍，
            # 那仍然是走完的一轮。ui_messages 用内容去重是对的，turns 不是。
            if ok and sess.last_assistant:
                sess.turns += 1
            try:
                STORE.save(sess)
            except Exception:
                pass
            try:
                preview = (sess.last_assistant or "")[:200]
                trajectory.append_turn(
                    sess.handbook_id,
                    {
                        "session_id": sess.session_id,
                        "chip": body.chip,
                        "user_text": (body.user_text or "")[:240],
                        "anchor": anchor,
                        "assistant_chars": len(sess.last_assistant or ""),
                        "assistant_preview": preview,
                        "has_substantive": has_sub or sess.has_substantive,
                        "ok": ok,
                    },
                )
            except Exception:
                pass
            lock.release()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/chat/approve")
def chat_approve(body: ApproveBody, lang: str = Depends(req_lang)) -> StreamingResponse:
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise HTTPException(404, msg("session.unknown", lang)) from exc
    lock = _try_lock_session(sess.session_id, lang)
    try:
        if not sess.pending or sess.pending.get("id") != body.pending_id:
            raise HTTPException(400, msg("approval.none", lang))
        meta = _meta_or_404(sess.handbook_id, lang)
        path = Path(meta.original_path)
        prior_assistant = sess.last_assistant
    except Exception:
        lock.release()
        raise

    def gen():
        ok = True
        try:
            for ev in resume_chat(
                sess,
                path,
                allow=body.allow,
                pending_id=body.pending_id,
                llm=body.merged(),
                extra_roots=libraries.extra_roots_for(sess.handbook_id),
                allow_env_fallback=not bool((body.base_url or "").strip()),
                lang=lang,
            ):
                if ev.get("type") == "error":
                    ok = False
                yield _sse(ev)
        except ProviderError as exc:
            ok = False
            yield _sse({"type": "error", "message": str(exc)})
        except Exception:
            ok = False
            yield _sse({"type": "error", "message": msg("approve.unexpected", lang)})
        finally:
            if sess.last_assistant and sess.last_assistant != prior_assistant:
                sess.ui_messages.append({"role": "assistant", "text": sess.last_assistant})
            try:
                STORE.save(sess)
            except Exception:
                pass
            lock.release()
            _ = ok

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/writeback/propose")
def propose(body: ProposeBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise HTTPException(404, msg("session.unknown", lang)) from exc
    if not sess.last_assistant:
        raise HTTPException(400, msg("writeback.no_answer", lang))
    if not sess.last_anchor:
        raise HTTPException(400, msg("writeback.no_anchor", lang))
    meta = _meta_or_404(sess.handbook_id, lang)
    idx = libraries.load_index(sess.handbook_id)
    path = Path(meta.original_path)
    try:
        fold = propose_fold_md(
            sess,
            llm=body.merged(),
            allow_env_fallback=not bool((body.base_url or "").strip()),
            lang=lang,
        )
    except RuntimeError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    try:
        plan = insertmod.plan_insert(
            idx,
            path,
            line=int(sess.last_anchor["start_line"]),
            fold_md=fold,
            summary_hint=body.summary_hint,
        )
    except insertmod.InsertError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    old = path.read_text(encoding="utf-8")
    new = insertmod.render_new_text(old, plan)
    diff = insertmod.unified_diff(old, new, path.name)
    pid = uuid.uuid4().hex
    _proposal_put(
        pid,
        {
            "handbook_id": sess.handbook_id,
            "session_id": sess.session_id,
            "plan": plan,
            "diff": diff,
            "original_path": str(path),
            "content_fp": _content_fp(path),
        },
    )
    return _public_proposal(pid, path, plan, diff)


@app.get("/v1/handbooks/{handbook_id}/outline")
def handbook_outline(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(handbook_id, lang)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return file_outline(path)


@app.post("/v1/writeback/retarget")
def retarget(body: RetargetBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    prop = _proposal_get(body.proposal_id)
    if prop is None:
        raise HTTPException(404, msg("proposal.unknown", lang))
    try:
        sess = STORE.get(prop["session_id"])
    except KeyError as exc:
        raise HTTPException(404, msg("session.unknown", lang)) from exc
    path = Path(prop["original_path"])
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(sess.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    fold = prop["plan"].fold_md
    try:
        plan = _plan_for_target(path, fold, sess, body)
    except insertmod.InsertError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    old = path.read_text(encoding="utf-8")
    new = insertmod.render_new_text(old, plan)
    diff = insertmod.unified_diff(old, new, path.name)
    prop["plan"] = plan
    prop["diff"] = diff
    prop["content_fp"] = _content_fp(path)
    _proposal_put(body.proposal_id, prop)
    return _public_proposal(body.proposal_id, path, plan, diff)


@app.post("/v1/writeback/apply")
def apply(body: ApplyBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    prop = _proposal_get(body.proposal_id)
    if prop is None:
        raise HTTPException(404, msg("proposal.unknown", lang))
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise HTTPException(404, msg("session.unknown", lang)) from exc
    if prop["session_id"] != sess.session_id:
        raise HTTPException(403, msg("proposal.wrong_session", lang))
    path = Path(prop["original_path"])
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(sess.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    expected = prop.get("content_fp")
    if expected and _content_fp(path) != expected:
        raise HTTPException(400, msg("writeback.stale", lang))
    snap = snapshots.take_snapshot(sess.handbook_id, path, "pre-insert")
    try:
        insertmod.apply_insert(path, prop["plan"])
    except insertmod.InsertError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    libraries.refresh_if_stale(sess.handbook_id)
    commit_out = None
    commit_error: str | None = None
    if body.commit:
        commit_msg = body.commit_message or (
            f"pen: 写回 {prop['plan'].level} {prop['plan'].q_title or prop['plan'].beat}"
        )
        try:
            commit_out = gitops.commit_original(path, commit_msg)
        except gitops.GitError as exc:
            commit_error = str(exc)
    _proposal_del(body.proposal_id)
    return {
        "ok": True,
        "original_path": str(path),
        "snapshot": str(snap),
        "commit": commit_out,
        "commit_error": commit_error,
        "bytes": path.stat().st_size,
    }


@app.get("/v1/handbooks/{handbook_id}/snapshots")
def snapshot_status(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    _meta_or_404(handbook_id, lang)
    return snapshots.status(handbook_id)


@app.post("/v1/writeback/rollback")
def rollback(body: RollbackBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(body.handbook_id, lang)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(body.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    try:
        snap = snapshots.undo(body.handbook_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    libraries.refresh_if_stale(body.handbook_id)
    st = snapshots.status(body.handbook_id)
    return {
        "ok": True,
        "restored_from": str(snap),
        "original_path": str(path),
        **st,
    }


@app.post("/v1/writeback/redo")
def redo(body: RollbackBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(body.handbook_id, lang)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(body.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    try:
        snap = snapshots.redo(body.handbook_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    libraries.refresh_if_stale(body.handbook_id)
    st = snapshots.status(body.handbook_id)
    return {
        "ok": True,
        "restored_from": str(snap),
        "original_path": str(path),
        **st,
    }


@app.get("/v1/chips")
def chips() -> dict[str, Any]:
    return {"chips": FIXED_CHIPS}


@app.get("/v1/handbooks/{handbook_id}/diagnosis")
def get_diagnosis(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    _meta_or_404(handbook_id, lang)
    turns = trajectory.load_turns(handbook_id)
    report = diagnosemod.aggregate(turns)
    report["handbook_id"] = handbook_id
    return report


@app.post("/v1/handbooks/{handbook_id}/diagnosis/narrate")
def narrate_diagnosis(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    _meta_or_404(handbook_id, lang)
    turns = trajectory.load_turns(handbook_id)
    report = diagnosemod.aggregate(turns)
    report["handbook_id"] = handbook_id
    try:
        text = diagnosemod.narrate(report)
    except RuntimeError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return {"handbook_id": handbook_id, "narrative": text}
