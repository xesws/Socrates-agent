from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai

from pen.agent import READ_FIRST_MSG, TOOLS, decide, dispatch, read_first_block, schemas
from pen.agent.tools_impl import handle_edit_file
from pen.config import LLMConfig
from pen.session import PenSession
from pen.tutor import resume_chat, stream_chat


def test_read_allow_edit_ask_unknown_deny() -> None:
    assert decide("read_file") == "allow"
    assert decide("edit_file") == "ask"
    assert decide("bash") == "deny"
    assert decide("write_file") == "deny"


def test_read_first_block_requires_earlier_read() -> None:
    book = Path("/tmp/note.md").resolve()
    assert read_first_block("read_file", book, set()) is None
    assert read_first_block("edit_file", book, set()) == READ_FIRST_MSG
    assert read_first_block("edit_file", book, {book}) is None
    other = Path("/tmp/other.md").resolve()
    assert read_first_block("edit_file", book, {other}) == READ_FIRST_MSG


def test_schemas_only_read_and_edit() -> None:
    names = [s["function"]["name"] for s in schemas()]
    assert names == ["read_file", "edit_file"]
    assert "write_file" not in TOOLS
    assert "bash" not in TOOLS
    read_desc = next(s["function"]["description"] for s in schemas() if s["function"]["name"] == "read_file")
    edit_desc = next(s["function"]["description"] for s in schemas() if s["function"]["name"] == "edit_file")
    assert "行号" in read_desc
    assert "N\\t原文" in read_desc
    assert "先成功 read_file" in edit_desc
    assert "行号" in edit_desc


def test_edit_file_unique_replace(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n内容。\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    out = handle_edit_file(
        {"path": str(book), "old_string": "内容。", "new_string": "真内容"},
        ctx,
    )
    assert out["ok"] is True
    assert out["line"] == 3
    assert "真内容" in book.read_text(encoding="utf-8")
    assert book.read_text(encoding="utf-8").count("内容。") == 0


def test_edit_file_relative_path_same_as_original(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一一段\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    out = handle_edit_file(
        {"path": "note.md", "old_string": "唯一一段", "new_string": "换成了"},
        ctx,
    )
    assert out["ok"] is True
    assert "换成了" in book.read_text(encoding="utf-8")
    assert "唯一一段" not in book.read_text(encoding="utf-8")


def test_edit_file_rejects_non_unique_and_missing(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("aa\naa\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    twice = handle_edit_file({"path": str(book), "old_string": "aa", "new_string": "bb"}, ctx)
    assert twice["ok"] is False
    miss = handle_edit_file({"path": str(book), "old_string": "nope", "new_string": "x"}, ctx)
    assert miss["ok"] is False
    empty = handle_edit_file({"path": str(book), "old_string": "", "new_string": "x"}, ctx)
    assert empty["ok"] is False
    space = handle_edit_file({"path": str(book), "old_string": "\n", "new_string": "x"}, ctx)
    assert space["ok"] is False
    whole = handle_edit_file(
        {"path": str(book), "old_string": "aa\naa\n", "new_string": "zz"},
        ctx,
    )
    assert whole["ok"] is False
    assert book.read_text(encoding="utf-8") == "aa\naa\n"


def test_edit_file_rejects_line_number_prefix(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n内容。\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    numbered = handle_edit_file(
        {"path": str(book), "old_string": "3\t内容。", "new_string": "真内容"},
        ctx,
    )
    assert numbered["ok"] is False
    assert "行号" in numbered["text"]
    assert "内容。" in book.read_text(encoding="utf-8")


def test_edit_file_rejects_overlapping_old_string(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# t\n\naaa\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    out = handle_edit_file({"path": str(book), "old_string": "aa", "new_string": "Z"}, ctx)
    assert out["ok"] is False
    assert book.read_text(encoding="utf-8") == "# t\n\naaa\n\n尾\n"


def test_edit_file_rejects_other_path(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    other = tmp_path / "other.md"
    book.write_text("a\n", encoding="utf-8")
    other.write_text("b\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    out = dispatch(
        "edit_file",
        {"path": str(other), "old_string": "b", "new_string": "c"},
        ctx,
    )
    assert out["ok"] is False
    assert other.read_text(encoding="utf-8") == "b\n"
    rel = dispatch(
        "edit_file",
        {"path": "other.md", "old_string": "b", "new_string": "c"},
        ctx,
    )
    assert rel["ok"] is False
    assert other.read_text(encoding="utf-8") == "b\n"
    escaped = dispatch(
        "edit_file",
        {"path": "../other.md", "old_string": "b", "new_string": "c"},
        {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""},
    )
    assert escaped["ok"] is False


def test_dispatch_unknown() -> None:
    out = dispatch("bash", {"command": "ls"}, {"original_path": Path("."), "extra_roots": []})
    assert out["ok"] is False


def test_edit_takes_pre_edit_snapshot(tmp_path: Path, monkeypatch) -> None:
    from pen import config, snapshots

    lib = tmp_path / "libraries"
    lib.mkdir()
    monkeypatch.setattr(config, "LIBRARIES_DIR", lib)
    monkeypatch.setattr(snapshots, "LIBRARIES_DIR", lib)
    book = tmp_path / "note.md"
    book.write_text("# t\n\nhello unique\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": "hid"}
    out = handle_edit_file(
        {"path": str(book), "old_string": "hello unique", "new_string": "changed"},
        ctx,
    )
    assert out["ok"] is True
    snaps = list((lib / "hid" / "snapshots").glob("*.md"))
    assert len(snaps) == 1
    assert "hello unique" in snaps[0].read_text(encoding="utf-8")
    assert "changed" in book.read_text(encoding="utf-8")
    assert "hello unique" not in book.read_text(encoding="utf-8")


def _cfg() -> LLMConfig:
    return LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        model="deepseek-v4-flash",
        key_source="settings",
    )


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _Tc:
    def __init__(self, cid: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = cid
        self.function = _Fn(name, json.dumps(arguments, ensure_ascii=False))


class _Msg:
    def __init__(self, content: str | None = None, tool_calls: list[_Tc] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {"role": "assistant"}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in self.tool_calls
            ]
        return d


def _patch_script(monkeypatch, replies: list[_Msg]) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    queue = list(replies)

    class _Completions:
        def create(self, **kwargs: Any) -> Any:
            seen.append(kwargs)
            msg = queue.pop(0)
            usage = SimpleNamespace(prompt_tokens=8, completion_tokens=3)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)

    class _Client:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(openai, "OpenAI", _Client)
    return seen


def test_read_file_then_answer_no_write(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    seen = _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(content="看过了，这是邻域里的那一句。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert any(e["type"] == "tool" and e["name"] == "read_file" and e["ok"] for e in events)
    assert any(e["type"] == "done" for e in events)
    assert not any(e["type"] == "approval" for e in events)
    assert book.read_text(encoding="utf-8") == "# 题\n\n唯一段。\n"
    assert seen[0].get("tools")


def test_edit_file_without_read_is_blocked(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# 题\n\n旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc(
                        "c1",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
            _Msg(content="好，我先去 read。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    tools = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert tools and tools[0]["ok"] is False
    assert "必须先成功 read_file" in str(tools[0]["preview"])
    assert not any(e["type"] == "approval" for e in events)
    assert sess.pending is None
    assert book.read_text(encoding="utf-8") == original
    assert any(e["type"] == "done" for e in events)


def test_read_round_then_edit_pauses(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# 题\n\n旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert any(e["type"] == "tool" and e["name"] == "read_file" and e["ok"] for e in events)
    approvals = [e for e in events if e["type"] == "approval"]
    assert len(approvals) == 1
    assert book.read_text(encoding="utf-8") == original
    assert sess.pending is not None
    assert any(str(book) in p or Path(p).name == "note.md" for p in sess.read_ok_paths)


def test_resume_allow_writes_then_finishes(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n旧段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    pid = sess.pending["id"]
    _patch_script(
        monkeypatch,
        [_Msg(content="已经按你批准写进去了。\n<!--pen:chips\n- 下一问\n-->")],
    )
    events = list(
        resume_chat(
            sess,
            book,
            allow=True,
            pending_id=pid,
            llm=_cfg(),
            extra_roots=[tmp_path],
            allow_env_fallback=False,
        )
    )
    assert "新段。" in book.read_text(encoding="utf-8")
    assert "旧段。" not in book.read_text(encoding="utf-8")
    assert any(e["type"] == "tool" and e["name"] == "edit_file" and e["ok"] for e in events)
    assert any(e["type"] == "done" for e in events)
    assert sess.pending is None


def test_resume_deny_does_not_write(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# 题\n\n旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    pid = sess.pending["id"]
    _patch_script(
        monkeypatch,
        [_Msg(content="好，原文没动。\n<!--pen:chips\n- 下一问\n-->")],
    )
    events = list(
        resume_chat(
            sess,
            book,
            allow=False,
            pending_id=pid,
            llm=_cfg(),
            extra_roots=[tmp_path],
            allow_env_fallback=False,
        )
    )
    assert book.read_text(encoding="utf-8") == original
    denied = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert denied and denied[0]["ok"] is False
    assert any(e["type"] == "done" for e in events)


def test_failed_read_does_not_unlock_edit(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# 题\n\n旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "read_file", {"path": "/etc/passwd"})]),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
            _Msg(content="读失败了，不能改。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert any(e["type"] == "tool" and e["name"] == "read_file" and e["ok"] is False for e in events)
    edits = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert edits and edits[0]["ok"] is False
    assert not any(e["type"] == "approval" for e in events)
    assert sess.pending is None
    assert book.read_text(encoding="utf-8") == original


def test_write_file_denied_then_continues(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "keep\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "write_file", {"path": str(book), "content": "hack"})]),
            _Msg(content="我没有 write_file。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    tools = [e for e in events if e["type"] == "tool"]
    assert tools and tools[0]["ok"] is False
    assert book.read_text(encoding="utf-8") == original
    assert any(e["type"] == "done" for e in events)


def test_read_then_edit_in_one_batch_bounces_edit(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 10}),
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    ),
                ]
            ),
            _Msg(content="看到读结果了，下一轮再 edit。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert any(e["type"] == "tool" and e["name"] == "read_file" and e["ok"] for e in events)
    edits = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert edits and edits[0]["ok"] is False
    assert not any(e["type"] == "approval" for e in events)
    assert sess.pending is None
    assert book.read_text(encoding="utf-8") == original
    assert any(e["type"] == "done" for e in events)


def test_bash_denied_in_stream(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# t\n\nkeep\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "bash", {"command": "rm -rf /"})]),
            _Msg(content="没有 bash。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    tools = [e for e in events if e["type"] == "tool"]
    assert tools and tools[0]["ok"] is False
    assert book.read_text(encoding="utf-8") == original
    assert any(e["type"] == "done" for e in events)


def test_second_edit_in_rest_asks_again(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# t\n\n第一段。\n\n第二段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c0", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c1",
                        "edit_file",
                        {"path": str(book), "old_string": "第一段。", "new_string": "一改。"},
                    ),
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "第二段。", "new_string": "二改。"},
                    ),
                ]
            ),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    assert sess.pending is not None
    pid = sess.pending["id"]
    events = list(
        resume_chat(
            sess,
            book,
            allow=True,
            pending_id=pid,
            llm=_cfg(),
            extra_roots=[tmp_path],
            allow_env_fallback=False,
        )
    )
    assert "一改。" in book.read_text(encoding="utf-8")
    assert "第二段。" in book.read_text(encoding="utf-8")
    assert any(e["type"] == "approval" and e["name"] == "edit_file" for e in events)
    assert sess.pending is not None
    assert sess.pending["args"]["old_string"] == "第二段。"
    assert not any(e["type"] == "done" for e in events)


# ── v0.10.0 计量 ────────────────────────────────────────────────


def test_spend_event_fires_once_per_llm_call_and_only_grows(monkeypatch, tmp_path: Path) -> None:
    """实时计量的整条链：每打一枪就报一次，数字只增不减。

    读者要看的就是这个——翻书翻到一半时数字还在往上爬，那是失控循环
    唯一看得见的信号。
    """
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(tool_calls=[_Tc("c2", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(content="看过了。" * 30),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    spends = [e for e in events if e["type"] == "spend"]
    # 假 client 每枪报 prompt_tokens=8 / completion_tokens=3
    # 上一行钉死了确切序列，再断言一次「有序」是空转的，不写。
    assert [s["turn"] for s in spends] == [11, 22, 33], "三枪，每枪 11，逐枪累加"
    assert sess.spend["chat"]["calls"] == 3
    assert sess.turn_spend["in_tokens"] == 24


def test_turn_spend_survives_the_approval_pause(monkeypatch, tmp_path: Path) -> None:
    """一轮从 /v1/chat 开始，到 /v1/chat/approve 那一枪结束，中间隔着两个
    HTTP 请求和一次落盘。turn_spend 必须跨过去——它在会话上而不是在
    _agent_loop 的闭包里，就是为了这个。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n第二段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("r1", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(tool_calls=[_Tc("e1", "edit_file", {"path": str(book), "old_string": "第二段。", "new_string": "改过。"})]),
        ],
    )
    sess = PenSession(session_id="p" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    assert sess.pending is not None
    before = dict(sess.turn_spend)
    assert before["calls"] == 2

    _patch_script(monkeypatch, [_Msg(content="改完了。" * 30)])
    list(
        resume_chat(
            sess, book, allow=True, pending_id=sess.pending["id"],
            llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False,
        )
    )
    assert sess.turn_spend["calls"] == 3, "续跑那一枪要加在同一轮上，不是从头数"
    assert sess.turn_spend["in_tokens"] > before["in_tokens"]


def test_usage_and_spend_are_two_different_things(monkeypatch, tmp_path: Path) -> None:
    """done.usage 是「最后一枪」的快照（此刻窗口占多大），
    spend 是累加器（一共花了多少）。同一轮里这两个数必须不同，
    否则说明有人把它们合并了。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(content="看过了。" * 30),
        ],
    )
    sess = PenSession(session_id="u" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    done = next(e for e in events if e["type"] == "done")
    assert done["usage"]["prompt_tokens"] == 8, "最后一枪的窗口占用"
    assert sess.spend["chat"]["in_tokens"] == 16, "两枪累加"
