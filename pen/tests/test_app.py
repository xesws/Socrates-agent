from __future__ import annotations

from pathlib import Path

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
    monkeypatch.setattr("pen.app.propose_fold_md", lambda _sess: FOLD)

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
