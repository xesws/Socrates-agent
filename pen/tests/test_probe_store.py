"""深挖池子的语义测试。

投递是「至少一次」：丢一个响应不会丢问题，重复请求同一个 since 返回同样的东西。
成熟度闸门是纯确定性的，只能把 now 降成 later，永远不能反过来。
"""

from __future__ import annotations

import pytest

from pen import config, probe_store
from pen.probe_store import DeepQuestion


@pytest.fixture(autouse=True)
def _tmp_pen(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    monkeypatch.setattr(config, "LIBRARIES_DIR", tmp_path / ".pen" / "libraries")
    config.ensure_pen_dirs()


def _q(text: str, **kw) -> DeepQuestion:
    base = dict(id="d1", text=text, timing="now", atom="A", born_round=0)
    base.update(kw)
    return DeepQuestion(**base)


def test_empty_ledger_reads_as_blank() -> None:
    led = probe_store.load("nobody")
    assert led.pool == [] and led.probe_calls == 0 and led.running == []


def test_claim_is_exclusive_per_session() -> None:
    """同一会话同时只允许一个 probe。抢不到就跳过，不排队——
    排队意味着上下文已经过期还要再花一次钱。"""
    a = probe_store.try_claim("s1", "h", 1)
    assert a
    assert probe_store.try_claim("s1", "h", 1) is None
    probe_store.release("s1", a)
    assert probe_store.try_claim("s1", "h", 2)


def test_claim_counts_against_the_session_budget() -> None:
    pid = probe_store.try_claim("s2", "h", 1)
    probe_store.release("s2", pid)
    assert probe_store.budget("s2") == {"used": 1, "max": config.PROBE_MAX_PER_SESSION}


def test_add_questions_assigns_monotonic_seq_and_dedupes() -> None:
    pid = probe_store.try_claim("s3", "h", 0)
    probe_store.add_questions("s3", pid, [_q("甲问题够长吗？"), _q("甲问题够长吗？"), _q("乙问题也够长？")])
    led = probe_store.load("s3")
    assert [q.seq for q in led.pool] == [1, 2]
    assert led.running == [], "add_questions 要顺手放掉占坑"


def test_inbox_releases_at_most_one_per_call() -> None:
    pid = probe_store.try_claim("s4", "h", 0)
    probe_store.add_questions("s4", pid, [_q("甲问题够长吗？"), _q("乙问题也够长？")])
    got = probe_store.inbox("s4", since=0, atom="A", level="Level 0", now_round=0)
    assert len(got["items"]) == probe_store.MAX_RELEASE_PER_TURN == 1
    assert got["running"] == []


def test_inbox_is_idempotent_for_the_same_cursor() -> None:
    """至少一次投递：同一个 since 重复问，答案一样。"""
    pid = probe_store.try_claim("s5", "h", 0)
    probe_store.add_questions("s5", pid, [_q("甲问题够长吗？")])
    first = probe_store.inbox("s5", since=0, atom="A", now_round=0)
    again = probe_store.inbox("s5", since=0, atom="A", now_round=0)
    assert first["items"] == again["items"] != []


def test_now_items_wait_when_the_reader_has_moved_on() -> None:
    """timing=now 但读者已经走到别的地方了 → 压着不抛。"""
    pid = probe_store.try_claim("s6", "h", 0)
    probe_store.add_questions("s6", pid, [_q("甲问题够长吗？", atom="A", born_round=0)])
    moved = probe_store.inbox("s6", since=0, atom="B", level="Level 3", now_round=3)
    assert moved["items"] == []
    back = probe_store.inbox("s6", since=0, atom="A", level="Level 0", now_round=3)
    assert len(back["items"]) == 1


def test_later_items_surface_when_the_reader_arrives() -> None:
    pid = probe_store.try_claim("s7", "h", 0)
    probe_store.add_questions(
        "s7", pid, [_q("挂在六关的那个问题？", timing="later", target="Level 6", born_round=0)]
    )
    early = probe_store.inbox("s7", since=0, atom="A", level="Level 0", now_round=0)
    assert early["items"] == [], "读者还没走到那一关"
    arrived = probe_store.inbox("s7", since=0, atom="A", level="Level 6", now_round=1)
    assert len(arrived["items"]) == 1


def test_stale_items_are_dropped_not_shown() -> None:
    pid = probe_store.try_claim("s8", "h", 0)
    probe_store.add_questions("s8", pid, [_q("过期的那条问题？", born_round=0)])
    late = probe_store.inbox("s8", since=0, atom="A", now_round=probe_store.ITEM_TTL_TURNS + 1)
    assert late["items"] == []
    assert probe_store.load("s8").pool[0].state == "dropped"


def test_cursor_only_advances_past_what_was_actually_delivered() -> None:
    """一次探索产两条、每轮只放一条。游标要是推到池子最大 seq，
    第二条就永远够不着了——later 通道会整条死掉。"""
    pid = probe_store.try_claim("s9", "h", 0)
    probe_store.add_questions("s9", pid, [_q("甲问题够长吗？"), _q("乙问题也够长？")])
    first = probe_store.inbox("s9", since=0, atom="A", now_round=0)
    assert len(first["items"]) == 1
    assert first["cursor"] == 1, f"只投递了 seq=1，游标不该跳过 seq=2：{first['cursor']}"
    second = probe_store.inbox("s9", since=first["cursor"], atom="A", now_round=1)
    assert len(second["items"]) == 1, "第二条必须够得着"
    assert second["items"][0]["text"] != first["items"][0]["text"]


def test_pool_still_drains_after_two_have_been_shown() -> None:
    """服务端不该拿「同时可见 2 条」当终身闸门：visible_count 只增不减，
    抛满之后每一条 pending 都会在进 _ripe 之前被跳过，池子再也不衰减，
    pending 一路堆到 PROBE_PENDING_CAP，从第四轮起永久停探。"""
    pid = probe_store.try_claim("s9b", "h", 0)
    probe_store.add_questions(
        "s9b", pid, [_q(f"第 {i} 个够长的问题在这里？") for i in range(4)]
    )
    cur = 0
    seen = []
    for r in range(4):
        got = probe_store.inbox("s9b", since=cur, atom="A", now_round=r)
        cur = got["cursor"]
        seen += [i["text"] for i in got["items"]]
    assert len(set(seen)) == 4, f"四条都该轮到，实际只放出 {len(set(seen))} 条"


def test_stale_items_drop_even_when_others_were_already_shown() -> None:
    """TTL 以前藏在 _ripe 里，放行名额用完就走不到，等于永不生效。"""
    pid = probe_store.try_claim("s9c", "h", 0)
    probe_store.add_questions(
        "s9c", pid, [_q("先放出去这条够长吗？"), _q("这条会过期掉的吧？")]
    )
    probe_store.inbox("s9c", since=0, atom="A", now_round=0)
    probe_store.inbox("s9c", since=0, atom="A", now_round=probe_store.ITEM_TTL_TURNS + 1)
    states = {q.text: q.state for q in probe_store.load("s9c").pool}
    assert states["这条会过期掉的吧？"] == "dropped", states
    assert probe_store.load("s9c").pending_count() == 0


def test_mark_clicked_matches_after_normalisation() -> None:
    pid = probe_store.try_claim("s10", "h", 0)
    probe_store.add_questions("s10", pid, [_q("白名单和危险检测的顺序为什么不能换？", grounding="open")])
    hit = probe_store.mark_clicked("s10", "白名单和危险检测的顺序为什么不能换？")
    assert hit is not None and hit.state == "clicked" and hit.grounding == "open"
    assert probe_store.mark_clicked("s10", "毫不相干的一句话") is None


def test_asked_only_lists_what_was_actually_shown() -> None:
    pid = probe_store.try_claim("s11", "h", 0)
    probe_store.add_questions("s11", pid, [_q("甲问题够长吗？"), _q("乙问题也够长？")])
    assert probe_store.asked("s11") == []
    probe_store.inbox("s11", since=0, atom="A", now_round=0)
    assert len(probe_store.asked("s11")) == 1


def test_ledger_survives_a_corrupt_file(tmp_path) -> None:
    probe_store.probes_dir().joinpath("s12.json").write_text("{坏掉的", encoding="utf-8")
    assert probe_store.load("s12").pool == []


def test_unknown_fields_in_a_stored_item_are_ignored() -> None:
    """将来加字段时，旧盘上的记录不能让整个池子读不出来。"""
    led = probe_store.SessionLedger.from_dict(
        {"session_id": "s13", "pool": [{"id": "x", "text": "甲？", "未来字段": 1}]}
    )
    assert len(led.pool) == 1 and led.pool[0].text == "甲？"


def test_advancing_the_cursor_stops_redelivery() -> None:
    """幂等的另一半：前端把 since 推过去之后就不能再收到同一条，
    否则芯片会一直重复冒出来。"""
    pid = probe_store.try_claim("s14", "h", 0)
    probe_store.add_questions("s14", pid, [_q("甲问题够长吗？")])
    first = probe_store.inbox("s14", since=0, atom="A", now_round=0)
    assert first["items"] and first["cursor"] > 0
    after = probe_store.inbox("s14", since=first["cursor"], atom="A", now_round=0)
    assert after["items"] == []


def test_clicked_items_are_never_redelivered() -> None:
    pid = probe_store.try_claim("s15", "h", 0)
    probe_store.add_questions("s15", pid, [_q("甲问题够长吗？")])
    probe_store.inbox("s15", since=0, atom="A", now_round=0)
    probe_store.mark_clicked("s15", "甲问题够长吗？")
    assert probe_store.inbox("s15", since=0, atom="A", now_round=0)["items"] == []


def test_orphaned_running_is_reaped_after_a_restart() -> None:
    """进程被杀时 running 留在盘上。不回收的话 try_claim 永远抢不到坑，
    前端也会对着一个不会完成的幽灵轮询到超时。"""
    from datetime import datetime, timedelta, timezone

    pid = probe_store.try_claim("s16", "h", 0)
    assert pid and probe_store.try_claim("s16", "h", 0) is None
    led = probe_store.load("s16")
    stale = datetime.now(timezone.utc) - timedelta(seconds=probe_store.ORPHAN_AFTER_SECONDS + 60)
    led.running_since = stale.isoformat()
    probe_store.save(led)
    assert probe_store.load("s16").running == [], "孤儿没被回收"
    assert probe_store.try_claim("s16", "h", 1), "回收后应能重新占坑"


def test_unparsable_running_since_is_reaped_too() -> None:
    probe_store.try_claim("s17", "h", 0)
    led = probe_store.load("s17")
    led.running_since = "不是时间"
    probe_store.save(led)
    assert probe_store.load("s17").running == []


def test_daily_quota_survives_new_sessions(monkeypatch) -> None:
    """每会话 8 次挡不住「开一堆新会话」。日配额才是真正的成本上限——
    这个常量以前定义了却没人读，等于没有上限。"""
    monkeypatch.setattr(config, "PROBE_MAX_PER_DAY", 3)
    got = []
    for i in range(6):
        pid = probe_store.try_claim(f"day-{i}", "bk", 0)
        got.append(bool(pid))
        if pid:
            probe_store.release(f"day-{i}", pid)
    assert got == [True, True, True, False, False, False]
    assert probe_store.daily_count("bk") == 3


def test_daily_count_resets_on_a_new_day(monkeypatch) -> None:
    import json

    probe_store.try_claim("day-x", "bk2", 0)
    stale = probe_store._daily_path("bk2")
    stale.write_text(json.dumps({"date": "1999-01-01", "count": 99}), encoding="utf-8")
    assert probe_store.daily_count("bk2") == 0


def test_release_with_refund_gives_the_quota_back() -> None:
    """一次 LLM 都没打就失败了（起线程失败、抢不到信号量），不该扣配额。
    失败越多能用的次数越少，那是反的。"""
    for i in range(3):
        pid = probe_store.try_claim(f"refund-{i}", "rb", 0)
        probe_store.release(f"refund-{i}", pid, refund=True)
    assert probe_store.daily_count("rb") == 0
    led = probe_store.load("refund-0")
    assert led.probe_calls == 0


def test_release_without_refund_keeps_the_charge() -> None:
    pid = probe_store.try_claim("norefund", "rb2", 0)
    probe_store.release("norefund", pid)
    assert probe_store.load("norefund").probe_calls == 1
    assert probe_store.daily_count("rb2") == 1


def test_concurrent_writers_leave_no_temp_files() -> None:
    """固定用 <path>.tmp 的话，两个写者会互相 replace 掉对方的临时文件，
    抛出未捕获的 FileNotFoundError。"""
    import threading

    errs: list[str] = []

    def worker() -> None:
        try:
            for _ in range(25):
                led = probe_store.load("conc", "cb")
                led.seq += 1
                probe_store.save(led)
        except Exception as exc:  # noqa: BLE001
            errs.append(repr(exc))

    ths = [threading.Thread(target=worker) for _ in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert not errs, errs
    assert not list(probe_store.probes_dir().glob("*.tmp"))
