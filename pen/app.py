"""FastAPI：阅读原文、就地问、确认后原地写回 original_path。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pen import gitops
from pen import insert as insertmod
from pen import libraries, snapshots
from pen import diagnose as diagnosemod
from pen import proposals as proposalsmod
from pen import trajectory
from pen.config import DEFAULT_HANDBOOK_ID, LLMConfig, llm_public_status, merge_llm
from pen.libraries import RegisterError
from pen.sandbox import SandboxError, assert_handbook_path, parse_vault_root
from pen.session import FIXED_CHIPS, STORE, chip_label
from pen.tutor import build_user_packet, propose_fold_md, stream_chat

SEARCH_REPLY = (
    "论文检索还没开。这是诚实挂起：P2 才有联网，"
    "现在不会假装搜过，也不会往诊断轨迹里记一笔假检索。"
)

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    libraries.ensure_default()
    yield


app = FastAPI(title="Socratic Pen", version="0.2.3", lifespan=lifespan)
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


class ApplyBody(BaseModel):
    session_id: str
    proposal_id: str
    commit: bool = False
    commit_message: str | None = None


class RollbackBody(BaseModel):
    handbook_id: str


def _meta_or_404(handbook_id: str):
    meta = libraries.get(handbook_id)
    if meta is None:
        raise HTTPException(404, f"未知手册 {handbook_id}")
    return libraries.refresh_if_stale(handbook_id)


def _sse(ev: dict[str, Any]) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


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
def import_handbook(body: ImportBody) -> dict[str, Any]:
    try:
        extra = parse_vault_root(body.vault_root)
        meta = libraries.register(
            body.original_path,
            body.handbook_id,
            extra_roots=extra or None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (RegisterError, SandboxError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return meta.__dict__


@app.get("/v1/handbooks/{handbook_id}")
def get_handbook(handbook_id: str) -> dict[str, Any]:
    meta = _meta_or_404(handbook_id)
    idx = libraries.load_index(handbook_id)
    return {
        **meta.__dict__,
        "n_lines": idx.n_lines,
        "toc": [t.__dict__ for t in idx.toc],
    }


@app.get("/v1/handbooks/{handbook_id}/content")
def get_content(handbook_id: str) -> dict[str, Any]:
    meta = _meta_or_404(handbook_id)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "original_path": str(path),
        "text": path.read_text(encoding="utf-8"),
        "mtime": path.stat().st_mtime,
    }


@app.get("/v1/handbooks/{handbook_id}/locate")
def locate(handbook_id: str, line: int) -> dict[str, Any]:
    idx = libraries.load_index(handbook_id)
    try:
        sec = idx.locate(line)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return sec.__dict__


@app.post("/v1/sessions")
def create_session(body: SessionBody) -> dict[str, Any]:
    _meta_or_404(body.handbook_id)
    if body.session_id:
        try:
            sess = STORE.get(body.session_id)
            if sess.handbook_id == body.handbook_id:
                return sess.to_public()
        except KeyError:
            pass
    sess = STORE.create(body.handbook_id)
    return sess.to_public()


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    try:
        sess = STORE.get(session_id)
    except KeyError as exc:
        raise HTTPException(404, "未知会话") from exc
    return sess.to_public()


@app.post("/v1/chat")
def chat(body: ChatBody) -> StreamingResponse:
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise HTTPException(404, "未知会话") from exc
    if body.chip == "search":
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
    meta = _meta_or_404(sess.handbook_id)
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
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    sess.last_anchor = anchor
    shown = (body.user_text or "").strip() or chip_label(body.chip)
    sess.ui_messages.append({"role": "user", "text": shown})
    prior_assistant = sess.last_assistant
    STORE.save(sess)

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
            ):
                if ev.get("type") == "done":
                    has_sub = bool(ev.get("has_substantive"))
                elif ev.get("type") == "error":
                    ok = False
                yield _sse(ev)
        finally:
            if sess.last_assistant and sess.last_assistant != prior_assistant:
                sess.ui_messages.append({"role": "assistant", "text": sess.last_assistant})
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

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/writeback/propose")
def propose(body: ProposeBody) -> dict[str, Any]:
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise HTTPException(404, "未知会话") from exc
    if not sess.last_assistant:
        raise HTTPException(400, "还没有可写回的解答")
    if not sess.last_anchor:
        raise HTTPException(400, "缺少框选锚点")
    meta = _meta_or_404(sess.handbook_id)
    idx = libraries.load_index(sess.handbook_id)
    path = Path(meta.original_path)
    try:
        fold = propose_fold_md(sess, llm=body.merged())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        plan = insertmod.plan_insert(
            idx,
            path,
            line=int(sess.last_anchor["start_line"]),
            fold_md=fold,
            summary_hint=body.summary_hint,
        )
    except insertmod.InsertError as exc:
        raise HTTPException(400, str(exc)) from exc
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
        },
    )
    return {
        "proposal_id": pid,
        "original_path": str(path),
        "mode": plan.mode,
        "level": plan.level,
        "q_title": plan.q_title,
        "instance_n": plan.instance_n,
        "fold_md": plan.fold_md,
        "diff": diff,
    }


@app.post("/v1/writeback/apply")
def apply(body: ApplyBody) -> dict[str, Any]:
    prop = _proposal_get(body.proposal_id)
    if prop is None:
        raise HTTPException(404, "提议不存在或已过期")
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise HTTPException(404, "未知会话") from exc
    if prop["session_id"] != sess.session_id:
        raise HTTPException(403, "提议不属于这个会话")
    path = Path(prop["original_path"])
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(sess.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, str(exc)) from exc
    snap = snapshots.take_snapshot(sess.handbook_id, path, "pre-insert")
    try:
        insertmod.apply_insert(path, prop["plan"])
    except insertmod.InsertError as exc:
        raise HTTPException(400, str(exc)) from exc
    libraries.refresh_if_stale(sess.handbook_id)
    commit_out = None
    commit_error: str | None = None
    if body.commit:
        msg = body.commit_message or (
            f"pen: 写回 {prop['plan'].level} {prop['plan'].q_title or prop['plan'].beat}"
        )
        try:
            commit_out = gitops.commit_original(path, msg)
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


@app.post("/v1/writeback/rollback")
def rollback(body: RollbackBody) -> dict[str, Any]:
    meta = _meta_or_404(body.handbook_id)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(body.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        snap = snapshots.rollback(body.handbook_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    libraries.refresh_if_stale(body.handbook_id)
    return {"ok": True, "restored_from": str(snap), "original_path": str(path)}


@app.get("/v1/chips")
def chips() -> dict[str, Any]:
    return {"chips": FIXED_CHIPS}


@app.get("/v1/handbooks/{handbook_id}/diagnosis")
def get_diagnosis(handbook_id: str) -> dict[str, Any]:
    _meta_or_404(handbook_id)
    turns = trajectory.load_turns(handbook_id)
    report = diagnosemod.aggregate(turns)
    report["handbook_id"] = handbook_id
    return report


@app.post("/v1/handbooks/{handbook_id}/diagnosis/narrate")
def narrate_diagnosis(handbook_id: str) -> dict[str, Any]:
    _meta_or_404(handbook_id)
    turns = trajectory.load_turns(handbook_id)
    report = diagnosemod.aggregate(turns)
    report["handbook_id"] = handbook_id
    try:
        text = diagnosemod.narrate(report)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"handbook_id": handbook_id, "narrative": text}
