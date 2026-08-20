"""界面语言：Accept-Language 决定错误文案与回复语言。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pen import libraries
from pen.app import app
from pen.i18n import MESSAGES, msg, norm_lang
from pen.session import STORE, load_session, system_prompt


@pytest.mark.parametrize(
    "raw,want",
    [
        ("zh-CN,zh;q=0.9", "zh"),
        ("zh", "zh"),
        ("zh-TW", "zh"),
        ("en-US,en;q=0.9", "en"),
        ("en", "en"),
        ("fr-FR", "en"),   # 认不出的语言走英文，不是崩
        (None, "zh"),      # 没带头 -> 回落中文
        ("", "zh"),
    ],
)
def test_norm_lang(raw, want):
    assert norm_lang(raw) == want


def test_every_message_has_both_languages():
    """两张表的键必须齐——漏一条就会在英文界面里冒出中文。"""
    for key, table in MESSAGES.items():
        assert set(table) == {"zh", "en"}, key
        assert table["zh"] and table["en"], key


def test_unknown_key_returns_key_itself():
    """没登记的 key 原样返回，开发期一眼看出来，不会静默变空串。"""
    assert msg("no.such.key", "en") == "no.such.key"


def test_placeholders_match_across_languages():
    """同一条文案的占位符在中英两版必须一致，否则 .format 会 KeyError。"""
    import string

    for key, table in MESSAGES.items():
        fields = {
            lang: {f for _, f, _, _ in string.Formatter().parse(text) if f}
            for lang, text in table.items()
        }
        assert fields["zh"] == fields["en"], f"{key}: {fields}"


def test_http_error_follows_accept_language():
    c = TestClient(app)
    zh = c.get("/v1/sessions/nope", headers={"Accept-Language": "zh-CN"})
    en = c.get("/v1/sessions/nope", headers={"Accept-Language": "en-US"})
    assert zh.status_code == en.status_code == 404
    assert zh.json()["detail"] == "未知会话"
    assert en.json()["detail"] == "Unknown session"


def test_no_header_falls_back_to_chinese():
    c = TestClient(app)
    assert c.get("/v1/sessions/nope").json()["detail"] == "未知会话"


def test_system_prompt_appends_english_instruction():
    """英文版必须是「中文人设 + 追加一句」，不是整体重写——人设的语气是内容的一部分。"""
    zh, en = system_prompt("zh"), system_prompt("en")
    assert en.startswith(zh)
    assert "Reply in English" in en
    assert "Reply in English" not in zh


def test_session_records_lang_and_survives_reload():
    libraries.ensure_default()
    c = TestClient(app)
    hid = c.get("/v1/handbooks").json()["handbooks"][0]["handbook_id"]
    sid = c.post(
        "/v1/sessions", json={"handbook_id": hid}, headers={"Accept-Language": "en-US"}
    ).json()["session_id"]

    sess = STORE.get(sid)
    assert sess.lang == "en"
    assert "Reply in English" in sess.messages[0]["content"]
    # 落盘再读出来，语言不能丢
    assert load_session(sid).lang == "en"
