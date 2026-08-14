"""20-question live diagnosis drill. Not part of default pytest."""

from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pen.tests.live_scenarios import BASE, chat_sse, new_session, _req

OUT = Path("/tmp/pen-diag-20")
HID = "swe-agent-v2"

# session_group: same key → reuse session_id
TURNS = [
    {"id": "T01", "g": "S1", "line": 695, "chip": "socratic", "kind": "oneshot-head",
     "selected": "chmod +x hello.sh 和 bash hello.sh 两种运行方式的本质区别是什么？",
     "q": ""},
    {"id": "T02", "g": "S1", "line": 697, "chip": "examples", "kind": "followup",
     "selected": "source hello.sh（. 是简写）= 当前这个 shell 把文件当自己的命令一行行念",
     "q": "source 为什么不看 x 位？给两个例子"},
    {"id": "T03", "g": "S1", "line": 698, "chip": "explain_zero", "kind": "followup",
     "selected": "source 不看 x 位，也不另起进程——文件里的 export 会写进你正在用的这个 shell",
     "q": "export 写进当前 shell 还是子进程？"},
    {"id": "T04", "g": "S2", "line": 1088, "chip": "explain_zero", "kind": "oneshot-head",
     "selected": "Bash 变量赋值 name = \"小明\" 为什么报错？",
     "q": ""},
    {"id": "T05", "g": "S2", "line": 1092, "chip": "examples", "kind": "followup",
     "selected": "记住口诀：赋值没空格，取值加 $，传给孩子加 export",
     "q": "赋值没空格、传给孩子为什么要 export？"},
    {"id": "T06", "g": "S3", "line": 2040, "chip": "examples", "kind": "oneshot",
     "selected": "source .venv/bin/activate 后 which python 指向 .venv 里那个",
     "q": ""},
    {"id": "T07", "g": "S4", "line": 3994, "chip": "socratic", "kind": "oneshot-head",
     "selected": "为什么说 LLM 没有记忆？多轮对话是怎么装出记忆的？",
     "q": ""},
    {"id": "T08", "g": "S4", "line": 3995, "chip": "explain_zero", "kind": "followup",
     "selected": "所谓记忆 = 你的程序把历史手动 append 进 messages、每次全量重发",
     "q": "记忆到底装在哪两行 append？"},
    {"id": "T09", "g": "S4", "line": 4047, "chip": "examples", "kind": "followup",
     "selected": 'messages.append({"role": "user", "content": user_input})',
     "q": "少 append user 会先变成什么笑话？"},
    {"id": "T10", "g": "S4", "line": 4051, "chip": "free", "kind": "followup",
     "selected": 'messages.append({"role": "assistant", "content": reply})',
     "q": "少 append assistant 呢？"},
    {"id": "T11", "g": "S5", "line": 4091, "chip": "explain_zero", "kind": "oneshot",
     "selected": "典型 bug 是把 messages = [...] 初始化写进 while 循环体里",
     "q": ""},
    {"id": "T12", "g": "S6", "line": 6714, "chip": "socratic", "kind": "oneshot-head",
     "selected": "既没给 <bash_action> 也没给 <done> 时，为什么要催它一下而不是直接结束？",
     "q": ""},
    {"id": "T13", "g": "S6", "line": 6716, "chip": "explain_zero", "kind": "followup",
     "selected": "灰色灰的是分类，不是不该发生",
     "q": "灰的是分类还是不该发生？"},
    {"id": "T14", "g": "S6", "line": 6717, "chip": "examples", "kind": "followup",
     "selected": "else 分支回填请继续：要么给 <bash_action>，要么给 <done>。",
     "q": "else 回填那句 user 催的是谁？"},
    {"id": "T15", "g": "S7", "line": 10111, "chip": "explain_zero", "kind": "oneshot-head",
     "selected": "规则链为什么有顺序？把 deny 和白名单调换会怎样？",
     "q": ""},
    {"id": "T16", "g": "S7", "line": 10114, "chip": "socratic", "kind": "followup",
     "selected": "DENY_TOOLS → session_allow → DANGEROUS 检测 → input 询问",
     "q": "deny 和白名单对调会怎样？"},
    {"id": "T17", "g": "S7", "line": 10115, "chip": "free", "kind": "paper-ask",
     "selected": "安全规则必须最先判。记口诀：越不容商量的规则，排越前",
     "q": "这套硬拒绝先于白名单最早是哪篇论文或哪套权限模型？给我出处，不要装搜过"},
    {"id": "T18", "g": "S8", "line": 11495, "chip": "explain_zero", "kind": "oneshot-head",
     "selected": "plan 模式为什么不改执行代码、只改 system prompt + 审批就够用？",
     "q": ""},
    {"id": "T19", "g": "S8", "line": 11498, "chip": "examples", "kind": "followup",
     "selected": "is_allowed 里 if mode == plan 分支拒绝一切非只读工具",
     "q": "plan 下 is_allowed 怎么拒写工具？"},
    {"id": "T20", "g": "S9", "line": 11649, "chip": "socratic", "kind": "oneshot",
     "selected": "模式切换时为什么要立刻把 messages[0] 换成执行版 system prompt？",
     "q": ""},
]

WEAK_NEED = {
    "Level 0|Q3": 3,
    "Level 2|Q4": 4,
    "Level 3|Q8": 3,
    "Level 5|Q3": 3,
    "Level 0|Q8": 2,
    "Level 6|Q1": 2,
}
FOOT_NEED = {
    "Level 1|Q1": 1,
    "Level 2|Q5": 1,
    "Level 6|Q3": 1,
}
LEVEL_HITS = {
    "Level 0": 5,
    "Level 2": 5,
    "Level 5": 3,
    "Level 3": 3,
    "Level 6": 3,
    "Level 1": 1,
}


def _spot_tag(spot: dict) -> str:
    label = str(spot.get("label") or "")
    level = str(spot.get("level") or "")
    for prefix in ("Q3", "Q4", "Q8", "Q1", "Q5"):
        if label.startswith(prefix + ".") or label.startswith(prefix + " "):
            return f"{level}|{prefix}"
    if "Q3." in label:
        return f"{level}|Q3"
    if "Q4." in label:
        return f"{level}|Q4"
    if "Q8." in label:
        return f"{level}|Q8"
    if "Q1." in label:
        return f"{level}|Q1"
    if "Q5." in label:
        return f"{level}|Q5"
    return f"{level}|{label[:20]}"


def backup_jsonl() -> Path | None:
    from pen.config import PEN_DIR

    src = PEN_DIR / "trajectories" / f"{HID}.jsonl"
    if not src.is_file():
        return None
    dest = src.with_suffix(
        f".bak-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    )
    shutil.copy2(src, dest)
    src.write_text("", encoding="utf-8")
    print(f"backed up {src} -> {dest} and cleared", flush=True)
    return dest


def jsonl_len() -> int:
    from pen.config import PEN_DIR

    p = PEN_DIR / "trajectories" / f"{HID}.jsonl"
    if not p.is_file():
        return 0
    return sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())


def check_oracle(report: dict) -> list[str]:
    fails: list[str] = []
    if report.get("n_turns") != 20:
        fails.append(f"n_turns={report.get('n_turns')} want 20")
    if report.get("n_curriculum") != 20:
        fails.append(f"n_curriculum={report.get('n_curriculum')} want 20")
    levels = {lv["level"]: lv["hits"] for lv in report.get("levels") or []}
    for name, hits in LEVEL_HITS.items():
        if levels.get(name) != hits:
            fails.append(f"level {name} hits={levels.get(name)} want {hits}")
    for bad in ("封面", "开篇", "Capstone", "附录"):
        if bad in levels:
            fails.append(f"{bad} leaked into levels")
    weak_map = {_spot_tag(s): s for s in report.get("weak") or []}
    foot_map = {_spot_tag(s): s for s in report.get("footprints") or []}
    for tag, hits in WEAK_NEED.items():
        spot = weak_map.get(tag)
        if not spot:
            fails.append(f"missing weak {tag}")
        elif spot["hits"] != hits:
            fails.append(f"weak {tag} hits={spot['hits']} want {hits}")
    for tag, hits in FOOT_NEED.items():
        if tag in weak_map:
            fails.append(f"oneshot {tag} wrongly in weak")
        spot = foot_map.get(tag)
        if not spot:
            fails.append(f"missing footprint {tag}")
        elif spot["hits"] != hits:
            fails.append(f"foot {tag} hits={spot['hits']} want {hits}")
    return fails


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    code, raw = _req("GET", "/v1/health")
    health = json.loads(raw)
    if code != 200 or not health.get("llm", {}).get("ok"):
        print("health fail", code, health, flush=True)
        return 2
    backup_jsonl()
    sessions: dict[str, str] = {}
    log: list[dict] = []
    for i, t in enumerate(TURNS, 1):
        sid = sessions.get(t["g"])
        if not sid:
            sid = new_session()
            sessions[t["g"]] = sid
        body = {
            "session_id": sid,
            "selected_text": t["selected"],
            "start_line": t["line"],
            "end_line": t["line"],
            "chip": t["chip"],
            "user_text": t["q"],
        }
        print(f"\n=== {t['id']} {t['kind']} chip={t['chip']} L{t['line']} ===", flush=True)
        rec = {
            "id": t["id"],
            "kind": t["kind"],
            "chip": t["chip"],
            "line": t["line"],
            "q": t["q"],
        }
        try:
            ev = chat_sse(body, timeout=420)
            rec["error"] = ev.get("error")
            rec["chars"] = len(ev.get("text") or "")
            rec["tools"] = len(ev.get("tools") or [])
            rec["preview"] = (ev.get("text") or "")[:180]
            print(
                f"ok chars={rec['chars']} tools={rec['tools']} err={rec['error']!r}",
                flush=True,
            )
        except Exception as exc:
            rec["error"] = repr(exc)
            rec["chars"] = 0
            rec["tools"] = 0
            rec["preview"] = ""
            print(f"FAIL {exc!r}", flush=True)
        log.append(rec)
        (OUT / "turns.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    before = jsonl_len()
    search_body = {
        "session_id": sessions["S7"],
        "selected_text": TURNS[16]["selected"],
        "start_line": 10115,
        "end_line": 10115,
        "chip": "search",
        "user_text": "查相关论文 / 算法出处",
    }
    data = json.dumps(search_body, ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + "/v1/chat", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    search_code = 0
    search_detail = ""
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            search_code = resp.status
            search_detail = resp.read()[:200].decode()
    except urllib.error.HTTPError as exc:
        search_code = exc.code
        search_detail = exc.read()[:200].decode()
    after = jsonl_len()
    search_ok = search_code == 400 and after == before
    print(f"\nT21 search HTTP {search_code} jsonl {before}->{after} ok={search_ok}", flush=True)
    print(search_detail, flush=True)

    code, raw = _req("GET", f"/v1/handbooks/{HID}/diagnosis")
    report = json.loads(raw)
    report["_search_probe"] = {
        "http": search_code,
        "jsonl_delta": after - before,
        "pass": search_ok,
    }
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    fails = check_oracle(report)
    if not search_ok:
        fails.append("T21 search should 400 and not append jsonl")
    (OUT / "oracle.txt").write_text(
        "PASS\n" if not fails else "FAIL\n" + "\n".join(fails) + "\n",
        encoding="utf-8",
    )
    print("\n===== ORACLE =====", flush=True)
    if fails:
        print("FAIL", flush=True)
        for f in fails:
            print(" -", f, flush=True)
        return 1
    print("PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
