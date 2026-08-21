from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai
from fastapi.testclient import TestClient

from pen import config, gitops, libraries, snapshots
from pen.app import SEARCH_REPLY, app
from pen.config import DEFAULT_HANDBOOK, REPO_ROOT
from pen.session import STORE

FIXTURE = Path(__file__).parent / "fixtures" / "mini_handbook.md"
FOLD = """<details>

<summary>🔍 实例 1：点读笔补的例子</summary>

```text
伪代码：shell 是一类，Bash 是一个
```

</details>
"""


def _isolate_pen(tmp_path: Path, monkeypatch) -> Path:
    lib = tmp_path / "libraries"
    lib.mkdir()
    monkeypatch.setattr(config, "PEN_DIR", tmp_path)
    monkeypatch.setattr(config, "LIBRARIES_DIR", lib)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    monkeypatch.setattr(snapshots, "LIBRARIES_DIR", lib)
    return lib


def test_health_and_locate_q1() -> None:
    with TestClient(app) as client:
        assert client.get("/v1/health").json()["status"] == "ok"
        books = client.get("/v1/handbooks").json()["handbooks"]
        assert any(b["handbook_id"] == "swe-agent-v2" for b in books)
        text = DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
        line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
        loc = client.get(f"/v1/handbooks/swe-agent-v2/locate?line={line}").json()
        assert loc["level"] == "Level 0"
        assert loc["q_title"] == "**Q1. shell 和 Bash 是什么关系？**"
        assert loc["kind"] == "q"


def test_session_get_and_resume() -> None:
    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()
        sid = created["session_id"]
        got = client.get(f"/v1/sessions/{sid}")
        assert got.status_code == 200
        assert got.json()["session_id"] == sid
        assert got.json()["ui_messages"] == []
        resumed = client.post(
            "/v1/sessions",
            json={"handbook_id": "swe-agent-v2", "session_id": sid},
        ).json()
        assert resumed["session_id"] == sid
        missing = client.get("/v1/sessions/deadbeefdeadbeefdeadbeefdeadbeef")
        assert missing.status_code == 404


def test_create_without_id_mints_fresh_and_keeps_old() -> None:
    with TestClient(app) as client:
        first = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()
        second = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()
        assert first["session_id"] != second["session_id"]
        assert second["ui_messages"] == []
        assert second["last_anchor"] is None
        assert second["has_substantive"] is False
        old = client.get(f"/v1/sessions/{first['session_id']}")
        assert old.status_code == 200
        assert old.json()["session_id"] == first["session_id"]


def test_search_is_friendly_sse_and_skips_trajectory(tmp_path, monkeypatch) -> None:
    from pen import trajectory as trajmod

    monkeypatch.setattr(trajmod.config, "PEN_DIR", tmp_path)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        before = (tmp_path / "trajectories" / "swe-agent-v2.jsonl")
        before_n = before.read_text(encoding="utf-8").count("\n") if before.is_file() else 0
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "查论文",
                "start_line": 695,
                "end_line": 695,
                "chip": "search",
                "user_text": "",
            },
        )
        assert resp.status_code == 200
        assert "P2" in resp.text
        assert SEARCH_REPLY[:8] in resp.text
        after_n = before.read_text(encoding="utf-8").count("\n") if before.is_file() else 0
        assert after_n == before_n
        sess = STORE.get(sid)
        assert sess.ui_messages == []


def test_import_vault_root_without_env(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    with TestClient(app) as client:
        denied = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(book), "handbook_id": "mini-vault"},
        )
        assert denied.status_code == 400
        assert "允许的根" in denied.json()["detail"]
        rooted = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-vault",
                "vault_root": str(tmp_path),
            },
        )
        assert rooted.status_code == 200
        assert rooted.json()["handbook_id"] == "mini-vault"
        assert rooted.json()["allow_root"] == str(tmp_path.resolve())
        text = client.get("/v1/handbooks/mini-vault/content")
        assert text.status_code == 200
        assert "Q1. shell" in text.json()["text"]
        slash = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "slash",
                "vault_root": "/",
            },
        )
        assert slash.status_code == 400
        assert "文件系统根" in slash.json()["detail"]


def test_apply_uses_stored_allow_root(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(
        i
        for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1)
        if ln.startswith("**Q1. shell")
    )
    monkeypatch.setattr(
        "pen.app.propose_fold_md",
        lambda _sess, llm=None, allow_env_fallback=True, lang="zh": FOLD,
    )
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-stored",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-stored"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": q1,
            "end_line": q1,
            "selected_text": "shell",
            "kind": "q",
            "level": "Level 0",
            "q_title": "**Q1. shell 和 Bash 是什么关系？**",
        }
        STORE.save(sess)
        proposed = client.post("/v1/writeback/propose", json={"session_id": sid})
        assert proposed.status_code == 200
        body = proposed.json()
        assert isinstance(body["insert_after_line"], int)
        assert body["insert_after_line"] >= 1
        assert body["instance_n"] >= 1
        applied = client.post(
            "/v1/writeback/apply",
            json={
                "session_id": sid,
                "proposal_id": proposed.json()["proposal_id"],
                "commit": False,
            },
        )
        assert applied.status_code == 200
        assert "点读笔补的例子" in book.read_text(encoding="utf-8")


def test_retarget_after_line_and_outline(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "plain.md"
    book.write_text("# 随便\n\nkeep-me\n", encoding="utf-8")
    monkeypatch.setattr(
        "pen.app.propose_fold_md",
        lambda _sess, llm=None, allow_env_fallback=True, lang="zh": FOLD,
    )
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "plain-note",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        ol = client.get("/v1/handbooks/plain-note/outline")
        assert ol.status_code == 200
        assert ol.json()["headings"][0]["text"] == "随便"
        sid = client.post("/v1/sessions", json={"handbook_id": "plain-note"}).json()[
            "session_id"
        ]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": 1,
            "end_line": 1,
            "selected_text": "随便",
            "kind": "other",
            "level": "封面",
            "q_title": None,
        }
        STORE.save(sess)
        proposed = client.post("/v1/writeback/propose", json={"session_id": sid})
        assert proposed.status_code == 200
        pid = proposed.json()["proposal_id"]
        moved = client.post(
            "/v1/writeback/retarget",
            json={"proposal_id": pid, "kind": "after_line", "after_line": 3},
        )
        assert moved.status_code == 200
        assert moved.json()["insert_after_line"] == 3
        assert "where" in moved.json()
        bad = client.post(
            "/v1/writeback/retarget",
            json={"proposal_id": pid, "kind": "after_line", "after_line": 99},
        )
        assert bad.status_code == 400
        applied = client.post(
            "/v1/writeback/apply",
            json={"session_id": sid, "proposal_id": pid, "commit": False},
        )
        assert applied.status_code == 200
        text = book.read_text(encoding="utf-8")
        assert text.index("keep-me") < text.index("点读笔补的例子")


def test_chat_forwards_settings_overrides(monkeypatch) -> None:
    from pen.config import LLMConfig

    seen: dict = {}

    def fake_merge(**kw):
        seen["merge"] = kw
        return LLMConfig(
            base_url=kw.get("base_url") or "https://example.invalid/v1",
            api_key=kw.get("api_key") or "sk-test",
            model=kw.get("model") or "demo-model",
            key_source="settings",
            thinking=kw.get("thinking") or "off",
        )

    def fake_stream(sess, path, packet, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        seen["llm"] = llm
        yield {
            "type": "done",
            "usage": {"context_tokens": 1, "completion_tokens": 1, "prompt_tokens": 1},
            "dynamic_chips": [],
            "has_substantive": False,
        }

    monkeypatch.setattr("pen.app.merge_llm", fake_merge)
    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    text = DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell 和 Bash",
                "start_line": line,
                "end_line": line,
                "chip": "socratic",
                "user_text": "",
                "api_key": "sk-from-page",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
                "thinking": "medium",
            },
        )
        assert resp.status_code == 200
    assert seen["merge"]["api_key"] == "sk-from-page"
    assert seen["merge"]["base_url"] == "https://api.openai.com/v1"
    assert seen["merge"]["model"] == "gpt-4.1-mini"
    assert seen["merge"]["thinking"] == "medium"
    assert seen["llm"] is not None
    assert seen["llm"].api_key == "sk-from-page"
    assert seen["llm"].thinking == "medium"


def test_chat_request_base_url_disables_env_fallback(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr("pen.app.merge_llm", lambda **kw: None)

    def fake_stream(sess, path, packet, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        seen["llm"] = llm
        seen["allow_env_fallback"] = allow_env_fallback
        yield {
            "type": "error",
            "message": "找不到模型配置。请到设置 → Socrates Pen 填写 API Key。",
        }

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    text = DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell 和 Bash",
                "start_line": line,
                "end_line": line,
                "chip": "socratic",
                "user_text": "",
                "base_url": "https://api.openai.com/v1",
            },
        )
        assert resp.status_code == 200
        assert "API Key" in resp.text
    assert seen["llm"] is None
    assert seen["allow_env_fallback"] is False


def test_chat_forwards_stored_allow_root(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(
        i
        for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1)
        if ln.startswith("**Q1. shell")
    )
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        seen["extra_roots"] = extra_roots
        yield {
            "type": "done",
            "usage": {"context_tokens": 1, "completion_tokens": 1, "prompt_tokens": 1},
            "dynamic_chips": [],
            "has_substantive": False,
        }

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-root",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-root"}).json()["session_id"]
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell 和 Bash",
                "start_line": q1,
                "end_line": q1,
                "chip": "socratic",
                "user_text": "",
            },
        )
        assert resp.status_code == 200
    roots = [Path(r).expanduser().resolve() for r in seen["extra_roots"]]
    assert tmp_path.resolve() in roots


def test_chat_stream_raise_yields_error_and_records_not_ok(tmp_path: Path, monkeypatch) -> None:
    from pen.tutor import ProviderError

    _isolate_pen(tmp_path, monkeypatch)

    def boom_stream(sess, path, packet, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        raise ProviderError("节点不收这把钥匙。请到设置 → Socrates Pen 检查 API Key。")
        yield  # 只是为了让本函数成为生成器：第一次 next 才抛

    monkeypatch.setattr("pen.app.stream_chat", boom_stream)
    text = DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell 和 Bash",
                "start_line": line,
                "end_line": line,
                "chip": "socratic",
                "user_text": "",
            },
        )
        assert resp.status_code == 200
        assert '"type": "error"' in resp.text
        assert "API Key" in resp.text
    turns = [
        json.loads(raw)
        for raw in (tmp_path / "trajectories" / "swe-agent-v2.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if raw.strip()
    ]
    assert turns[-1]["ok"] is False


def test_propose_provider_error_becomes_400(tmp_path: Path, monkeypatch) -> None:
    import httpx

    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(
        i
        for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1)
        if ln.startswith("**Q1. shell")
    )
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    auth_exc = openai.AuthenticationError(
        "bad key", response=httpx.Response(401, request=req), body=None
    )

    class _BoomCompletions:
        def create(self, **_kwargs: Any) -> Any:
            raise auth_exc

    class _BoomClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_BoomCompletions())

    monkeypatch.setattr(openai, "OpenAI", _BoomClient)
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-propose",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-propose"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": q1,
            "end_line": q1,
            "selected_text": "shell",
            "kind": "q",
            "level": "Level 0",
            "q_title": "**Q1. shell 和 Bash 是什么关系？**",
        }
        STORE.save(sess)
        proposed = client.post(
            "/v1/writeback/propose",
            json={"session_id": sid, "api_key": "sk-from-page"},
        )
        assert proposed.status_code == 400
        assert "设置" in proposed.json()["detail"]
        assert "API Key" in proposed.json()["detail"]
        assert "sk-from-page" not in proposed.json()["detail"]


def test_import_rejects_arbitrary_and_unsafe_ids(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    outsider = tmp_path / "secret.md"
    outsider.write_text("# 外面\n", encoding="utf-8")
    with TestClient(app) as client:
        denied = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(outsider), "handbook_id": "evil"},
        )
        assert denied.status_code == 400
        assert "允许的根" in denied.json()["detail"]
        py = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(REPO_ROOT / "pen" / "app.py"), "handbook_id": "py"},
        )
        assert py.status_code == 400
        assert "Markdown" in py.json()["detail"]
        bad_id = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(DEFAULT_HANDBOOK), "handbook_id": "../escape"},
        )
        assert bad_id.status_code == 400
        assert "handbook_id" in bad_id.json()["detail"]
        ok = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(DEFAULT_HANDBOOK), "handbook_id": "swe-agent-v2"},
        )
        assert ok.status_code == 200
        assert ok.json()["handbook_id"] == "swe-agent-v2"
    assert not (tmp_path / "libraries" / "evil" / "meta.json").is_file()
    assert not (tmp_path / "libraries" / "py" / "meta.json").is_file()


def test_import_allows_extra_root(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.setenv("PEN_ALLOW_ROOTS", str(tmp_path))
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    with TestClient(app) as client:
        r = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(book), "handbook_id": "mini-v011"},
        )
        assert r.status_code == 200
        assert r.json()["handbook_id"] == "mini-v011"
        text = client.get("/v1/handbooks/mini-v011/content")
        assert text.status_code == 200
        assert "Q1. shell" in text.json()["text"]


def test_apply_commit_failure_consumes_proposal(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.setenv("PEN_ALLOW_ROOTS", str(tmp_path))
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(i for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1) if ln.startswith("**Q1. shell"))
    monkeypatch.setattr(
        "pen.app.propose_fold_md",
        lambda _sess, llm=None, allow_env_fallback=True, lang="zh": FOLD,
    )

    def boom(_path, _msg):
        raise gitops.GitError("gpg failed")

    monkeypatch.setattr(gitops, "commit_original", boom)
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(book), "handbook_id": "mini-v011"},
        )
        assert imported.status_code == 200
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-v011"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": q1,
            "end_line": q1,
            "selected_text": "shell",
            "kind": "q",
            "level": "Level 0",
            "q_title": "**Q1. shell 和 Bash 是什么关系？**",
        }
        STORE.save(sess)
        proposed = client.post("/v1/writeback/propose", json={"session_id": sid})
        assert proposed.status_code == 200
        pid = proposed.json()["proposal_id"]
        before = book.read_text(encoding="utf-8")
        applied = client.post(
            "/v1/writeback/apply",
            json={"session_id": sid, "proposal_id": pid, "commit": True},
        )
        assert applied.status_code == 200
        body = applied.json()
        assert body["ok"] is True
        assert body["commit"] is None
        assert "gpg" in (body.get("commit_error") or "")
        mid = book.read_text(encoding="utf-8")
        assert len(mid) > len(before)
        assert "点读笔补的例子" in mid
        again = client.post(
            "/v1/writeback/apply",
            json={"session_id": sid, "proposal_id": pid, "commit": True},
        )
        assert again.status_code == 404
        assert book.read_text(encoding="utf-8") == mid


def _q1_line() -> int:
    text = DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    return next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))


def test_chat_blocks_when_pending() -> None:
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.pending = {
            "id": "pend1",
            "name": "edit_file",
            "args": {"path": "x.md", "old_string": "a", "new_string": "b"},
            "tool_call_id": "c1",
            "rest": [],
        }
        STORE.save(sess)
        public = client.get(f"/v1/sessions/{sid}").json()
        assert public["pending"]["pending_id"] == "pend1"
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell",
                "start_line": _q1_line(),
                "end_line": _q1_line(),
                "chip": "socratic",
                "user_text": "",
            },
        )
        assert resp.status_code == 400
        assert "审批" in resp.json()["detail"]


def test_approve_wrong_id_and_missing_session() -> None:
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        missing = client.post(
            "/v1/chat/approve",
            json={"session_id": sid, "pending_id": "nope", "allow": True},
        )
        assert missing.status_code == 400
        ghost = client.post(
            "/v1/chat/approve",
            json={
                "session_id": "deadbeefdeadbeefdeadbeefdeadbeef",
                "pending_id": "x",
                "allow": True,
            },
        )
        assert ghost.status_code == 404


def test_approve_allow_runs_resume(monkeypatch) -> None:
    seen: dict = {}

    def fake_resume(sess, path, *, allow, pending_id, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        seen["allow"] = allow
        seen["pending_id"] = pending_id
        seen["path"] = path
        yield {
            "type": "tool",
            "name": "edit_file",
            "ok": True,
            "resolved": str(path),
            "detail": "",
            "preview": "已编辑",
            "line": 3,
        }
        yield {
            "type": "done",
            "usage": {"context_tokens": 1, "completion_tokens": 1, "prompt_tokens": 1},
            "dynamic_chips": [],
            "has_substantive": True,
        }

    monkeypatch.setattr("pen.app.resume_chat", fake_resume)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.pending = {
            "id": "pend2",
            "name": "edit_file",
            "args": {"old_string": "a", "new_string": "b"},
            "tool_call_id": "c1",
            "rest": [],
        }
        STORE.save(sess)
        resp = client.post(
            "/v1/chat/approve",
            json={"session_id": sid, "pending_id": "pend2", "allow": True},
        )
        assert resp.status_code == 200
        assert "edit_file" in resp.text
        assert '"type": "done"' in resp.text
    assert seen["allow"] is True
    assert seen["pending_id"] == "pend2"


def test_chat_409_when_session_busy() -> None:
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        lock = STORE.lock_for(sid)
        assert lock.acquire(blocking=False)
        try:
            resp = client.post(
                "/v1/chat",
                json={
                    "session_id": sid,
                    "selected_text": "shell",
                    "start_line": _q1_line(),
                    "end_line": _q1_line(),
                    "chip": "socratic",
                    "user_text": "",
                },
            )
            assert resp.status_code == 409
        finally:
            lock.release()


def test_snapshot_status_undo_redo_api(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.setenv("PEN_ALLOW_ROOTS", str(tmp_path))
    book = tmp_path / "mini.md"
    book.write_text("# t\n\nA\n", encoding="utf-8")
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-snap",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        hid = imported.json()["handbook_id"]
        empty = client.get(f"/v1/handbooks/{hid}/snapshots").json()
        assert empty["undo_n"] == 0
        assert empty["can_undo"] is False
        snapshots.take_snapshot(hid, book, "pre-edit")
        book.write_text("# t\n\nB\n", encoding="utf-8")
        st = client.get(f"/v1/handbooks/{hid}/snapshots").json()
        assert st["can_undo"] is True
        rolled = client.post("/v1/writeback/rollback", json={"handbook_id": hid})
        assert rolled.status_code == 200
        assert book.read_text(encoding="utf-8") == "# t\n\nA\n"
        assert rolled.json()["can_redo"] is True
        redone = client.post("/v1/writeback/redo", json={"handbook_id": hid})
        assert redone.status_code == 200
        assert book.read_text(encoding="utf-8") == "# t\n\nB\n"
        again = client.post("/v1/writeback/rollback", json={"handbook_id": hid})
        assert again.status_code == 200
        assert book.read_text(encoding="utf-8") == "# t\n\nA\n"
        missing = client.post("/v1/writeback/rollback", json={"handbook_id": hid})
        assert missing.status_code == 400



# ── v0.8.1：深挖收件箱 ──────────────────────────────────────


def test_deep_inbox_unknown_session_is_404() -> None:
    with TestClient(app) as client:
        assert client.get("/v1/sessions/deadbeefdeadbeefdeadbeefdeadbeef/deep").status_code == 404


def test_deep_inbox_starts_empty_and_reports_no_runner() -> None:
    """sidecar 刚起来时返回 running: []，语义明确「没有在跑的，停轮询」——
    这正是选会话为键而不是 probe 为键的理由之一。"""
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        got = client.get(f"/v1/sessions/{sid}/deep").json()
        assert got["items"] == [] and got["running"] == []
        assert got["budget"]["max"] > 0


def test_deep_inbox_does_not_take_the_session_lock() -> None:
    """那把锁在 /v1/chat 整个请求期间被持有。轮询去抢会把读者的下一次
    提问顶成 409——所以这个端点在会话被锁着时也必须照常 200。"""
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        lock = STORE.lock_for(sid)
        assert lock.acquire(blocking=False)
        try:
            assert client.get(f"/v1/sessions/{sid}/deep").status_code == 200
        finally:
            lock.release()


def test_deep_inbox_surfaces_a_ripe_question(tmp_path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    from pen import diagnose, probe_store
    from pen.probe_store import DeepQuestion

    monkeypatch.setattr(probe_store, "probes_dir", lambda: tmp_path / "probes")
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_anchor = {"level": "Level 0", "kind": "q", "q_title": "**Q1. 甲？**"}
        sess.turns = 1
        STORE.save(sess)
        pid = probe_store.try_claim(sid, "swe-agent-v2", 1)
        probe_store.add_questions(
            sid,
            pid,
            [
                DeepQuestion(
                    id="d1",
                    text="白名单排在危险检测前面，危险命令会不会被静默放行？",
                    why="读者刚碰到权限",
                    timing="now",
                    atom=diagnose.atom_key(sess.last_anchor),
                    born_round=1,
                )
            ],
        )
        got = client.get(f"/v1/sessions/{sid}/deep?since=0").json()
        assert len(got["items"]) == 1
        assert got["items"][0]["kind"] == "deep"
        assert got["items"][0]["why"]
        assert got["cursor"] > 0


def test_get_session_restores_deep_questions(tmp_path, monkeypatch) -> None:
    """关掉侧栏再打开，已经花钱挖出来、也给读者看过的深题必须还在。

    深题不进 PenSession（后台线程碰它会和请求线程抢 to_dict() 快照），
    所以恢复只能在 app 层现拼。这条断言就是为了守住那次拼接——
    最初的实现漏了它，深题关一次侧栏就永久丢失。
    """
    _isolate_pen(tmp_path, monkeypatch)
    from pen import diagnose, probe_store
    from pen.probe_store import DeepQuestion

    monkeypatch.setattr(probe_store, "probes_dir", lambda: tmp_path / "probes")
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_anchor = {"level": "Level 0", "kind": "q", "q_title": "**Q1. 甲？**"}
        sess.turns = 1
        sess.last_chips = [{"id": "q0", "kind": "quick", "text": "实时层那条？"}]
        STORE.save(sess)
        pid = probe_store.try_claim(sid, "swe-agent-v2", 1)
        probe_store.add_questions(
            sid,
            pid,
            [
                DeepQuestion(
                    id="d1", text="白名单排在危险检测前面，危险命令会不会被静默放行？",
                    why="跨关", timing="now",
                    atom=diagnose.atom_key(sess.last_anchor), born_round=1,
                )
            ],
        )
        # 先抛给读者看过
        assert client.get(f"/v1/sessions/{sid}/deep?since=0").json()["items"]

        restored = client.get(f"/v1/sessions/{sid}").json()["dyn_chips"]
        kinds = [c["kind"] for c in restored]
        assert "deep" in kinds, f"深题没恢复：{restored}"
        assert kinds[0] == "deep", "深题应排在实时层前面"
        assert any(c["kind"] == "quick" for c in restored), "实时层那条不该被挤掉"


def test_pending_deep_questions_are_not_restored_early(tmp_path, monkeypatch) -> None:
    """还没成熟、没抛给读者看过的，不能因为重开侧栏就提前冒出来。"""
    _isolate_pen(tmp_path, monkeypatch)
    from pen import probe_store
    from pen.probe_store import DeepQuestion

    monkeypatch.setattr(probe_store, "probes_dir", lambda: tmp_path / "probes")
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        pid = probe_store.try_claim(sid, "swe-agent-v2", 1)
        probe_store.add_questions(
            sid, pid,
            [DeepQuestion(id="d9", text="挂在很后面那一关的问题？", timing="later",
                          target="Level 6", born_round=1)],
        )
        assert client.get(f"/v1/sessions/{sid}").json()["dyn_chips"] == []


def test_failed_spawn_gives_the_claim_back(tmp_path, monkeypatch) -> None:
    """占了坑却没起成线程，坑必须立刻还回去。

    不还的话要等五分钟孤儿回收，期间这个会话一次都探不了，而正在轮询的
    前端会对着一个永远不会完成的幽灵白等满 90 秒。
    """
    _isolate_pen(tmp_path, monkeypatch)
    from pen import probe as probemod, probe_store
    from pen.app import _maybe_probe
    from pen.config import LLMConfig
    from pen.session import PenSession

    monkeypatch.setattr(probe_store, "probes_dir", lambda: tmp_path / "probes")
    monkeypatch.setattr(probemod, "spawn", lambda job, pid: (_ for _ in ()).throw(RuntimeError("no thread")))

    sess = PenSession(session_id="spawnfail", handbook_id="swe-agent-v2")
    sess.last_assistant = "回答够长以判为实质。" * 12
    sess.turns = 1
    body = SimpleNamespace(
        chip="socratic", user_text="", base_url="",
        merged=lambda: LLMConfig("http://x", "sk", "m", "t", "off"),
    )
    got = _maybe_probe(sess, body, {"level": "Level 0"}, DEFAULT_HANDBOOK, "zh")
    assert got is False
    assert probe_store.load("spawnfail").running == [], "坑没还回去"


# ── v0.10.0 计量 ────────────────────────────────────────────────


def test_done_event_carries_the_merged_session_spend(monkeypatch, tmp_path) -> None:
    """done 是自愈通道：轮询漏掉的、面板关着那段时间发生的深挖花销，
    下一轮 done 一定会补齐。所以它必须是**合并后**的三格。"""
    from pen import probe_store
    from pen.meter import Meter

    _isolate_pen(tmp_path, monkeypatch)

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.spend["chat"] = {"calls": 2, "in_tokens": 900, "out_tokens": 40,
                              "cached_tokens": 0, "reasoning_tokens": 0}
        yield {
            "type": "done",
            "usage": {"context_tokens": 1, "completion_tokens": 1, "prompt_tokens": 1},
            "dynamic_chips": [],
            "has_substantive": False,
        }

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    monkeypatch.setattr("pen.app.probemod.spawn", lambda job, pid: None)
    text = DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        # 先往账本里塞一笔深挖花销，模拟「面板关着的时候后台花了钱」
        pid = probe_store.try_claim(sid, "swe-agent-v2", 0)
        m = Meter()
        m.add({"prompt_tokens": 6000, "completion_tokens": 300})
        probe_store.release(sid, pid, spend=m.to_dict())

        resp = client.post("/v1/chat", json={
            "session_id": sid, "selected_text": "shell 和 Bash",
            "start_line": line, "end_line": line, "chip": "socratic", "user_text": "",
        })
        done = next(
            json.loads(ln[6:])
            for ln in resp.text.splitlines()
            if ln.startswith("data: ") and json.loads(ln[6:]).get("type") == "done"
        )
    assert done["spend"]["chat"]["in_tokens"] == 900
    assert done["spend"]["probe"]["in_tokens"] == 6000, "深挖那格从账本合进来"
    assert done["spend"]["fold"]["in_tokens"] == 0


def test_search_chip_done_carries_every_usage_key(tmp_path, monkeypatch) -> None:
    """search 分支不调 LLM 也发 done。context_tokens 一直缺着，
    前端靠 ?? prompt_tokens 才没露馅。"""
    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post("/v1/chat", json={
            "session_id": sid, "selected_text": "x", "start_line": 1, "end_line": 1,
            "chip": "search", "user_text": "",
        })
        done = next(
            json.loads(ln[6:])
            for ln in resp.text.splitlines()
            if ln.startswith("data: ") and json.loads(ln[6:]).get("type") == "done"
        )
    assert set(done["usage"]) == {"context_tokens", "prompt_tokens", "completion_tokens"}
    assert done["spend"]["chat"]["in_tokens"] == 0, "这一轮没花钱，但会话累计不该被清零"


def test_deep_inbox_never_takes_the_session_lock(tmp_path, monkeypatch) -> None:
    """那把锁在 /v1/chat 整个请求期间被持有。轮询端点去抢，就会把读者
    下一次提问顶成 409。这条测试今天之前是缺的——而往 inbox() 里加字段
    正是最容易有人顺手在端点里 STORE.get() 拿点别的东西的时刻。"""
    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        lock = STORE.lock_for(sid)
        assert lock.acquire(blocking=False)
        try:
            got = client.get(f"/v1/sessions/{sid}/deep?since=0")
        finally:
            lock.release()
    assert got.status_code == 200, "持锁期间也必须能查"
    assert "spend" in got.json()


def test_session_get_restores_spend_after_reopening_the_panel(tmp_path, monkeypatch) -> None:
    """关掉侧栏再打开，第三格要从这里恢复而不是归零。"""
    from pen import probe_store
    from pen.meter import Meter

    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        pid = probe_store.try_claim(sid, "swe-agent-v2", 0)
        m = Meter()
        m.add({"prompt_tokens": 1234, "completion_tokens": 56})
        probe_store.release(sid, pid, spend=m.to_dict())
        got = client.get(f"/v1/sessions/{sid}").json()
    assert got["spend"]["probe"]["in_tokens"] == 1234
    assert got["spend"]["chat"]["in_tokens"] == 0


# ── v0.10.1 参数透传 ────────────────────────────────────────────


def _chat_body(sid: str, line: int, **over) -> dict:
    body = {
        "session_id": sid, "selected_text": "shell 和 Bash",
        "start_line": line, "end_line": line, "chip": "socratic", "user_text": "",
    }
    body.update(over)
    return body


def _q1_line() -> int:
    text = DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    return next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))


def test_chat_forwards_limits_to_stream_chat(monkeypatch) -> None:
    """透传是活的。没有这条，「默认值 = 现状」和「现有测试全绿」可以同时成立
    而整条管道是空的。"""
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **kw):
        seen["limits"] = kw.get("limits")
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        client.post("/v1/chat", json=_chat_body(
            sid, _q1_line(), limits={"max_tool_rounds": 7, "cross_book_reads": 3}))
    got = seen["limits"]
    assert got is not None, "limits 根本没传到 stream_chat"
    assert (got.max_tool_rounds, got.cross_book_reads) == (7, 3)
    assert got.probe_max_per_window == 40, "没填的那些必须还是默认"


def test_approve_forwards_limits_too(monkeypatch) -> None:
    """一轮跨两个请求。approve 不带 limits 的话，批准之后的后半轮
    就变成一场没有上限的对话。"""
    seen: dict = {}

    def fake_resume(sess, path, *, allow, pending_id, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **kw):
        seen["limits"] = kw.get("limits")
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.resume_chat", fake_resume)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.pending = {"id": "pid1", "name": "edit_file", "args": {},
                        "original_path": str(DEFAULT_HANDBOOK)}
        client.post("/v1/chat/approve", json={
            "session_id": sid, "pending_id": "pid1", "allow": False,
            "limits": {"max_tool_rounds": 5},
        })
    assert seen["limits"] is not None and seen["limits"].max_tool_rounds == 5


def test_probe_job_freezes_the_request_limits(monkeypatch, tmp_path) -> None:
    """probe 在守护线程里跑，请求早结束了——限流值必须在 done 那一刻
    当场冻进 job，理由和 cfg 一样。"""
    _isolate_pen(tmp_path, monkeypatch)
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.last_assistant = "讲了一大段。" * 30
        sess.has_substantive = True
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": True}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    monkeypatch.setattr("pen.app.probemod.spawn", lambda job, pid: seen.update(job=job))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        client.post("/v1/chat", json=_chat_body(
            sid, _q1_line(), api_key="sk-x",
            limits={"probe_max_per_window": 7, "probe_read_lines": 33}))
    job = seen.get("job")
    assert job is not None, "深挖根本没起——这条测试就白写了"
    assert job.limits.probe_max_per_window == 7
    assert job.limits.probe_read_lines == 33
    assert job.limits.max_tool_rounds == 100, "没填的还是默认"


def test_a_bogus_limits_payload_is_clamped_not_a_422(monkeypatch) -> None:
    """设置页填错一个字符，读者该看到夹紧后的正常回复，不是一个红色 422。
    这就是请求体用 dict 而不是子模型的原因。"""
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **kw):
        seen["limits"] = kw.get("limits")
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post("/v1/chat", json=_chat_body(
            sid, _q1_line(), limits={"max_tool_rounds": "一百", "probe_concurrency": 99999}))
    assert resp.status_code == 200, "填错不该 422"
    assert seen["limits"].max_tool_rounds == 100, "看不懂的当没给"
    assert seen["limits"].probe_concurrency == 8, "超范围的夹紧"


def test_old_client_without_limits_still_works(monkeypatch) -> None:
    """旧插件、web/ 那个客户端、curl 都不带 limits 字段。"""
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **kw):
        seen["limits"] = kw.get("limits")
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post("/v1/chat", json=_chat_body(sid, _q1_line()))
    assert resp.status_code == 200
    from pen.config import default_limits

    assert seen["limits"] == default_limits()


def test_cooldown_is_wired_from_the_ledger_to_the_gate(monkeypatch, tmp_path) -> None:
    """冷却要真的接上：app 得把 sess.turns 和账本里的 last_probe_round
    一起递给闸门。不接的话闸门代码在跑而参数恒为默认，等于没有。"""
    from pen import probe_store

    _isolate_pen(tmp_path, monkeypatch)
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.last_assistant = "讲了一大段。" * 30
        sess.has_substantive = True
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": True}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    monkeypatch.setattr("pen.app.probemod.spawn", lambda job, pid: seen.update(job=job))

    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        # 假装刚刚探过（账本上留下 last_probe_round）
        pid = probe_store.try_claim(sid, "swe-agent-v2", 0)
        probe_store.release(sid, pid, refund=True)
        assert probe_store.load(sid).last_probe_round == 0

        body = _chat_body(sid, _q1_line(), api_key="sk-x",
                          limits={"probe_every_n_rounds": 5})
        resp = client.post("/v1/chat", json=body)
        assert resp.status_code == 200
        assert "job" not in seen, "隔得不够，这一轮不该起深挖"

        seen.clear()
        body2 = _chat_body(sid, _q1_line(), api_key="sk-x")  # 不设冷却
        client.post("/v1/chat", json=body2)
    assert "job" in seen, "不设冷却时照常探——证明拦住上一次的是冷却，不是别的"
