"""Live 苏格拉底 scenarios. Not part of default pytest (needs API + key)."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field

BASE = "http://127.0.0.1:8765"


def _req(method: str, path: str, body: dict | None = None, timeout: int = 30) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def chat_sse(body: dict, timeout: int = 600) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode()
    r = urllib.request.Request(BASE + "/v1/chat", data=data, method="POST")
    r.add_header("Content-Type", "application/json")
    tokens: list[str] = []
    tools: list[dict] = []
    done = None
    err = None
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        buf = b""
        while True:
            chunk = resp.read(256)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                part, buf = buf.split(b"\n\n", 1)
                line = b"".join(
                    ln[6:] for ln in part.split(b"\n") if ln.startswith(b"data: ")
                )
                if not line:
                    continue
                ev = json.loads(line.decode())
                t = ev.get("type")
                if t == "token":
                    tokens.append(ev.get("text") or "")
                elif t == "tool":
                    tools.append(ev)
                elif t == "done":
                    done = ev
                elif t == "error":
                    err = ev.get("message")
    text = "".join(tokens)
    return {"text": text, "tools": tools, "done": done, "error": err}


def new_session() -> str:
    code, raw = _req("POST", "/v1/sessions", {"handbook_id": "swe-agent-v2"})
    if code != 200:
        raise RuntimeError(f"session {code} {raw[:200]!r}")
    return json.loads(raw)["session_id"]


@dataclass
class Case:
    n: int
    line: int
    selected: str
    question: str
    must: list[str]
    forbid: list[str] = field(default_factory=list)


CASES = [
    Case(
        1,
        6086,
        "# 灰色地带（Q8）：催模型，不是 input() 等苏格拉底",
        "这段 else 和路线 b 的 if not tool_calls 差在分类还是差在停法？"
        "如果把 Codex 的 tool_calls 空列表写成 is not None，会不会造出第二种灰？",
        ["灰", "tool_calls"],
    ),
    Case(
        2,
        7254,
        "ALGORITHM: AgentLoopWithBash",
        "DeepSeek Harness 把 model adapter、tool registry、agent loop 做成可插拔插件（Cordis）。"
        "这张伪代码 1–16 行哪几行对应那三块？手册故意焊死成一个 for 的是哪一块？",
        ["循环", "插件"],
    ),
    Case(
        3,
        6714,
        "既没给 <bash_action> 也没给 <done> 时，为什么要「催它一下」而不是直接结束？",
        "路线 b 说没有灰色地带。模型 content 里写了半页思考、tool_calls=[]。"
        "Codex 会不会把这半页当最终答案？这算不算另一种灰？",
        ["tool_calls", "答案"],
    ),
    Case(
        4,
        4047,
        'messages.append({"role": "user", "content": user_input})',
        "手册说记忆靠全量重发。长上下文 + Harness compaction 之后，这两行 append 还是不是命门？"
        "少 append 哪一行会先炸？",
        ["append"],
    ),
    Case(
        5,
        2494,
        "`shell=True` 带来了什么能力、什么风险？",
        "Harness 的 tool 是插件。手册这把 shell=True 的 bash 塞进去，最大的洞是注入还是 cwd？"
        "审批该挂在 adapter 还是 registry？",
        ["注入", "审批"],
    ),
    Case(
        6,
        11622,
        "def is_allowed(name: str, args: dict, mode: str) -> tuple[bool, str]:",
        "Claude Code 四档权限和手册 plan / default / execute-auto 怎么对齐？"
        "Harness 热插拔 tool 时，审批钩子应拦在 schema 进模型之前还是 handler 执行之前？",
        ["plan", "执行"],
    ),
    Case(
        7,
        8191,
        'old_string 在文件中出现多次，请补充更多上下文使其唯一',
        "苏格拉底要往 1.3 万行手册里插 details。old_string 必须唯一，和 Codex apply_patch 比谁更适合写教材？"
        "回读那一行当锚会撞车吗？",
        ["唯一", "回读"],
    ),
    Case(
        8,
        11144,
        'messages[0] = {"role": "system", "content": SYSTEM_EXECUTE}',
        "Cordis 讲 composability。plan 模式只换 SYSTEM、不换引擎，算不算一种 composability？"
        "和 DeepSeek Harness 把整个 loop 换成插件差在哪一层？",
        ["messages[0]", "循环"],
    ),
    Case(
        9,
        5407,
        "mini-swe-agent 的约 100 行核心",
        "SWE-bench / DeepSeek Harness 把 runtime 做成产品之后，手册 v0.1 还算最小 Agent 吗？"
        "少了 registry、审批、plan，缺的是产品还是本质？",
        ["骨架", "审批"],
    ),
    Case(
        10,
        6086,
        "# 灰色地带（Q8）：催模型，不是 input() 等苏格拉底",
        "如果你连着 read_file 翻完全书再报步数用尽，和路线 a 空转到 range(20) 同构吗？"
        "差在分类失败还是不肯收工？",
        ["步数", "灰"],
    ),
]


def infra() -> None:
    code, raw = _req("GET", "/v1/health")
    health = json.loads(raw)
    assert code == 200 and health.get("llm", {}).get("ok"), health
    code, raw = _req("GET", "/v1/handbooks")
    books = json.loads(raw)["handbooks"]
    path = books[0]["original_path"]
    assert path.endswith("Socrates-agent/SWE-Agent通关手册v2.md"), path
    print("INFRA OK", path, health["llm"])


def run_case(case: Case) -> dict:
    sid = new_session()
    result = chat_sse(
        {
            "session_id": sid,
            "selected_text": case.selected,
            "start_line": case.line,
            "end_line": case.line,
            "chip": "free",
            "user_text": case.question,
        }
    )
    text = result["text"]
    low = text
    fail: list[str] = []
    if result["error"] and not result["done"]:
        fail.append(f"error={result['error']}")
    if result["done"] is None and not result["error"]:
        fail.append("no done")
    if "工具步数用尽" in (result["error"] or "") or "工具步数用尽" in text:
        fail.append("保险丝当唯一结局")
    if "权限挡了" in text or "拒绝读取" in text:
        fail.append("读原文被挡")
    for kw in case.must:
        if kw not in low:
            fail.append(f"缺关键词:{kw}")
    for kw in case.forbid:
        if kw in low:
            fail.append(f"不该出现:{kw}")
    tools_ok = [t for t in result["tools"] if t.get("ok")]
    return {
        "n": case.n,
        "pass": not fail,
        "fail": fail,
        "n_tools": len(result["tools"]),
        "n_tools_ok": len(tools_ok),
        "error": result["error"],
        "usage": (result["done"] or {}).get("usage"),
        "summary": text.replace("\n", " ")[:280],
    }


def main() -> None:
    infra()
    rows = []
    for case in CASES:
        print(f"\n===== CASE {case.n} line={case.line} =====")
        row = run_case(case)
        rows.append(row)
        print("PASS" if row["pass"] else "FAIL", row["fail"])
        print("tools", row["n_tools"], "ok", row["n_tools_ok"], "usage", row["usage"])
        print(row["summary"])
    ok = sum(1 for r in rows if r["pass"])
    print(f"\n==== {ok}/{len(rows)} passed ====")
    raise SystemExit(0 if ok == len(rows) else 1)


if __name__ == "__main__":
    main()
