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
    monkeypatch.setattr("pen.app.propose_fold_md", lambda _sess, llm=None: FOLD)
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

    def fake_stream(sess, path, packet, llm=None, extra_roots=None):
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

    def fake_stream(sess, path, packet, llm=None, extra_roots=None):
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

    def boom_stream(sess, path, packet, llm=None, extra_roots=None):
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
    monkeypatch.setattr("pen.app.propose_fold_md", lambda _sess, llm=None: FOLD)

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
