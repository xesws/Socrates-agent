"""异步深挖：给读者想一个真正值得停下来问的问题。

和实时层（tutor.parse_dynamic_chips）的分工：
实时层搭主回复的车，负责基础、局部、秒回；这里另起一次调用，负责跨关跨教材。

三条设计上的硬约束，改代码前先读：

1. **永不给模型 tools。** MAX_TOOL_ROUNDS=100 且没有 token 预算也没有时长墙，
   后台任务一旦能自主循环调工具，读者看不见它在烧钱。定向读正文由 Python 执行，
   最多两段，广度由代码封死。
2. **故意不喂 neighborhood()。** 那 4000 字符里全是手册自带的入门题
   （「heredoc 里 <<'EOF' 的引号起什么作用」），模型盯着它们必然产同构题——
   这才是「echo 加不加引号」的病根。改喂师傅刚讲的话，且剥掉代码块。
3. **绝不碰 PenSession。** 见 probe_store 的模块注释。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from pen import config
from pen.config import LLMConfig
from pen.index import HandbookIndex
from pen.probe_store import DeepQuestion
from pen.questions import clean_candidates, normalize_qkey, similarity

_SEM = threading.BoundedSemaphore(config.PROBE_CONCURRENCY)

# 教材每一关都有一节「第三拍 · 出身」专讲真实框架长什么样。
# 这七处是「拿我们的做法去撞真实系统」这条轴的合法锚点白名单，
# 而且 HandbookIndex.locate() 能确定性反查，所以模型编不了。
THIRD_BEAT = "第三拍"

PROBE_SYSTEM = """你是这本手册的助教，坐在师傅背后。师傅刚跟读者讲完一段——你不接话，不补讲解。
你只干一件事：替读者想出下一个真正值得问的问题，并判断它现在该不该抛。

够不够格，只看一条：把答案反过来想一遍。
如果答案是反的，变的是哪一行字怎么写，还是变的是一个设计决策、一个不变量、一次失败？
只变一行字的，扔掉。

还有一条定死：问题的对象必须是我们正在造的这台机器，或者这本手册的编排本身，
不是拿来演示它的那门语言的语法。引号怎么打、反斜杠管不管用、提示符里那串字是谁——
手册正文里就有答案，读者自己会看到，不用你问。

注意：手册里原生的 Q1 Q2 Q3 大多是入门小题。你在材料里会看到它们。
不要模仿那个粒度。你的活是补上它们没问的那一层。

五条路，每条给你一个真实产出过的样子：

1. bridge 搭桥——把隔着关的两个东西对上号。anchors 至少两条，且分属不同 Level。
   「七块积木里，messages 为什么不算文件？数据流上它又凭什么独立一格？」

2. tradeoff 权衡——为什么是这个做法不是另一个。被否掉那个的名字，写进 alt。
   「为什么第一个参考实现偏偏是 mini-swe-agent，而不是 LangChain？」

3. vs_real 对出身——每一关的「第三拍 · 出身」写了真实框架长什么样，拿我们的做法去撞它。
   anchors 至少一条落在某关的第三拍里。
   「我们用白名单去模拟 Claude Code 的 acceptEdits，会在哪一步漏？」

4. failure 找边界——什么条件下这套东西会炸，炸成什么样。触发条件写进 trigger。
   「白名单排在危险检测前面，那危险命令是不是也会被静默放行？这个洞 Level 6 怎么堵？」

5. altitude 换层——从一行代码抬到一条规矩，或者反过来落地。两处名字对不上号也算。
   「那本手册说的『四根骨头』，和 L16 的三大核心，对不上号怎么数？」

看见就别写的：
- 抠字眼：引号加不加、单引号里怎么打单引号、反斜杠还管不管用、提示符里那串是谁。
- 单个命令或单个函数的用法：这个参数干嘛的、该怎么传。
- 翻一页就完事的事实题。
- 复读：把师傅刚讲完的话、或者读者刚才那句话，换个说法再问一遍。
- 跟固定按钮撞车：「先别揭晓」「当我零基础」「只举例子」这几个意思别再出。
- 纯带路：「接着往下读」「直接去 Level 几」「带我读一下某某」。那是目录干的活。
- 写回手册、校对行号、补占位块这类活儿。那是另一个按钮的事。

行号说死：
手册里查得到出处的题，把 level 和行区间写准写进 anchors，grounding 填 "book"。
系统会拿确定性索引去核对，也会核对你用反引号引起来的词是不是真在那几行里。
编的会被直接丢掉，你白干一趟。
需要手册以外的知识才答得了的题，grounding 填 "open"，anchors 留空。
我们没有联网，不许假装查过、不许编版本号和函数名。这种题每次最多一条，
能改写成有出处的就改写成有出处的。

材料不够时：
如果你觉得非得看一眼某段正文才敢下笔，就在 need_read 里写行区间，最多两段。
系统会把那几段读给你，再问你一次。不确定就别写，多数时候目录和 Q 清单已经够了。
要看的是【工作目录里还有这些教材】里那本，就在同一条里加 book 字段写它的书名，
系统会去那本里取；不写 book 就默认当前这本。

timing 怎么填：
now = 接着读者刚才那口气就能问，不跳关。
later = 题是好的，但现在问会把人从当前这格拽走。这时在 target 里写清它挂在哪一关，
名字从全书目录里原样抄（例如 "Level 6"）。
拿不准就写 later。早抛一个读者接不住的问题，比晚抛更伤。

只输出一个 JSON 对象，别写解释，别写前言。最多 3 条，宁缺毋滥，一条都想不出就交空数组。
{
  "need_read": [{"book": "只在要读别的教材时才写：书名", "start_line": 0, "end_line": 0}],
  "questions": [
    {
      "text": "问题本身，一句话，12 到 40 个汉字，带问号，用师傅对读者说话的口气",
      "axis": "bridge | tradeoff | vs_real | failure | altitude",
      "grounding": "book | open",
      "anchors": [{"level": "……", "start_line": 0, "end_line": 0}],
      "alt": "只有 axis=tradeoff 才填：被否掉的那个做法叫什么",
      "trigger": "只有 axis=failure 才填：什么条件下会炸",
      "timing": "now | later",
      "target": "只有 timing=later 才填：挂在哪一关",
      "why": "一句话，为什么现在这个读者该被问这个。给系统看的，读者看不到",
      "depth": 4
    }
  ]
}
depth 自己打 1 到 5 分，打到 4 分以下的别写进数组。
上面的字段说明是给你看结构用的，一个字都不要原样抄进 text。抄了整条作废。"""

PROBE_ENGLISH = """

[Language] The reader's interface is in English. Write "text", "why", "alt" and
"trigger" in English, same voice. Keep every key name, every enum value
(axis / grounding / timing) and the JSON shape exactly as specified above.
English length band: 8 to 28 words, must end with a question mark.
Same rules apply: no syntax trivia, no navigation, no echoing the reader,
no fabricated line numbers, and never pretend you searched the web."""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_TICKED = re.compile(r"`([^`\n]{1,40})`|「([^」\n]{1,40})」")


def probe_system(lang: str = "zh") -> str:
    return PROBE_SYSTEM + (PROBE_ENGLISH if lang == "en" else "")


def third_beat_sections(idx: HandbookIndex) -> list[Any]:
    """七处「第三拍 · 出身」。vs_real 轴的合法锚点白名单。"""
    return [t for t in idx.toc if (t.beat or "").startswith(THIRD_BEAT)]


def strip_code_fences(text: str) -> str:
    """剥掉代码块。废问题就是从回复里的代码块里抠出来的——
    模型看见 `echo "$x"` 就会去问引号，看不见就不会。"""
    out = re.sub(r"```.*?```", "\n（代码块略）\n", text, flags=re.S)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def parse_probe_json(raw: str) -> dict[str, Any]:
    """容错解析。模型爱把 JSON 包在围栏里，也爱在前面写一句废话。
    解析失败一律当空——后台任务的异常绝不能冒到读者路径上。"""
    if not raw:
        return {"need_read": [], "questions": []}
    text = raw.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            return {"need_read": [], "questions": []}
        text = text[a : b + 1]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {"need_read": [], "questions": []}
    if not isinstance(data, dict):
        return {"need_read": [], "questions": []}
    qs = data.get("questions")
    reads = data.get("need_read")
    return {
        "need_read": [r for r in (reads or []) if isinstance(r, dict)][: config.PROBE_MAX_READS],
        "questions": [q for q in (qs or []) if isinstance(q, dict)][:3],
    }


def _anchor_ok(a: dict[str, Any], idx: HandbookIndex) -> bool:
    try:
        start = int(a.get("start_line") or 0)
        end = int(a.get("end_line") or start)
    except (TypeError, ValueError):
        return False
    if start < 1 or end < start or end > max(idx.n_lines, 1):
        return False
    try:
        sec = idx.locate(start)
    except ValueError:
        return False
    claimed = str(a.get("level") or "").strip()
    return not claimed or claimed == sec.level


def _quoted_tokens(text: str) -> list[str]:
    out = []
    for m in _TICKED.finditer(text):
        tok = (m.group(1) or m.group(2) or "").strip()
        if tok:
            out.append(tok)
    return out


def validate_slots(
    item: dict[str, Any],
    idx: HandbookIndex,
    *,
    source: str = "",
) -> tuple[bool, str]:
    """强制填槽 + 确定性校验。

    这是质量机制的支点：「echo 加不加引号」填不出任何一个槽——它没有第二个
    Level 的锚，没有被否掉的替代方案，没有触发条件。判据从形容词变成了 schema。

    诚实说：这挡得住编造行号，挡不住编造 alt。那一环只能靠 prompt 和点击率兜。
    """
    axis = str(item.get("axis") or "").strip()
    if axis not in ("bridge", "tradeoff", "vs_real", "failure", "altitude"):
        return False, "axis-unknown"
    grounding = str(item.get("grounding") or "book").strip()
    anchors = [a for a in (item.get("anchors") or []) if isinstance(a, dict)]

    if grounding == "open":
        # 声称没出处却又给锚点，就是在撒谎
        if anchors:
            return False, "open-with-anchors"
        if axis in ("bridge", "vs_real"):
            return False, "open-needs-anchors"
        # 没出处不等于可以不填槽。以前这里直接放行，等于 open 这条路上只剩
        # 「axis 拼写正确」一道检查——而读者选了 open 题不加标注，更没法自己分辨。
        if axis == "tradeoff" and not str(item.get("alt") or "").strip():
            return False, "tradeoff-needs-alt"
        if axis == "failure" and not str(item.get("trigger") or "").strip():
            return False, "failure-needs-trigger"
        return True, ""

    if not anchors:
        return False, "no-anchors"
    for a in anchors:
        if not _anchor_ok(a, idx):
            return False, "anchor-invalid"

    if axis == "bridge":
        levels = {str(a.get("level") or "") for a in anchors}
        levels.discard("")
        if len(levels) < 2:
            return False, "bridge-needs-two-levels"
    if axis == "tradeoff" and not str(item.get("alt") or "").strip():
        return False, "tradeoff-needs-alt"
    if axis == "failure" and not str(item.get("trigger") or "").strip():
        return False, "failure-needs-trigger"
    if axis == "vs_real":
        hit = False
        for a in anchors:
            try:
                sec = idx.locate(int(a.get("start_line") or 0))
            except (ValueError, TypeError):
                continue
            if (sec.beat or "").startswith(THIRD_BEAT):
                hit = True
                break
        if not hit:
            return False, "vs-real-needs-third-beat"

    # 反引号引起来的词必须真在那几行里，否则就是「看着像有出处」
    # source 必须是 anchors 指向的正文。以前传的是读者框选的那一小段，
    # 而 prompt 承诺核对的是「那几行」——于是模型照 prompt 给术语加反引号，
    # 反而被判死。拿不到正文时**跳过**这条检查，不判死：宁可漏也别误杀。
    toks = _quoted_tokens(str(item.get("text") or ""))
    if toks and source:
        for tok in toks:
            if tok not in source:
                return False, "quote-not-in-source"
    return True, ""


def anchor_source(item: dict[str, Any], original_path: Path, extra_roots: list[Path]) -> str:
    """把 anchors 指向的正文行取出来，给反引号校验当语料。读不到就空串。"""
    from pen.readtool import read_file_report

    out: list[str] = []
    for a in list(item.get("anchors") or [])[:2]:
        if not isinstance(a, dict):
            continue
        try:
            start = max(1, int(a.get("start_line") or 1))
            end = int(a.get("end_line") or start)
        except (TypeError, ValueError):
            continue
        span = max(1, min(end - start + 1, config.PROBE_READ_LINES))
        try:
            got = read_file_report(
                original_path, str(original_path), offset=start, limit=span,
                extra_roots=extra_roots or [config.REPO_ROOT],
            )
        except Exception:
            continue
        if got.get("ok"):
            out.append(str(got.get("text") or ""))
    return "\n".join(out)


def _thin_by_level(rows: list[tuple[str, str]], budget: int) -> str:
    """超预算时**每个 Level 均匀减**，不是砍尾巴。

    砍尾巴的教训是现成的：build_user_packet 原来写 toc_lines[:80]，
    而那本手册有 87 条，被砍掉的正好是 Capstone 和附录——跨关问题的必需品。
    这里改成一轮轮往下削每关的条数，保证每关都还有代表。
    """
    total = sum(len(r[1]) + 1 for r in rows)
    if total <= budget:
        return "\n".join(r[1] for r in rows)
    keep = 12
    while keep > 1:
        seen: dict[str, int] = {}
        out: list[str] = []
        used = 0
        for level, line in rows:
            n = seen.get(level, 0)
            if n >= keep:
                continue
            seen[level] = n + 1
            used += len(line) + 1
            out.append(line)
        if used <= budget:
            return "\n".join(out) + f"\n（每关只列了前 {keep} 条，全书更长）"
        keep -= 2
    return "\n".join(r[1] for r in rows)[:budget] + "\n（已截断）"


def _compact_toc(idx: HandbookIndex) -> str:
    rows = []
    for t in idx.toc:
        if t.beat is None:
            rows.append((t.level, f"{t.level} | L{t.start_line} | {t.heading}"))
        else:
            rows.append((t.level, f"{t.level} / {t.beat} | L{t.start_line}"))
    return _thin_by_level(rows, config.PROBE_TOC_CHARS)


def _compact_questions(idx: HandbookIndex) -> str:
    rows = []
    for s in idx.sections:
        if s.kind != "q" or not s.q_title:
            continue
        title = s.q_title.strip().strip("*")
        rows.append((s.level, f"{s.level} | L{s.start_line}-{s.end_line} | {title}"))
    return _thin_by_level(rows, config.PROBE_Q_CHARS)


def build_system(idx: HandbookIndex, lang: str = "zh") -> str:
    """固定家底放 system 尾部——它对同一本书永远一样，
    自动前缀缓存能把重复探索的边际成本压掉一个数量级。变量部分全放 user。"""
    thirds = "\n".join(
        f"{t.level} | L{t.start_line} | {t.beat}" for t in third_beat_sections(idx)
    )
    return (
        probe_system(lang)
        + "\n\n────────────────────────────\n以下是这本书的固定家底，每次都一样，拿它当地图。\n\n"
        + f"【全书目录】\n{_compact_toc(idx)}\n\n"
        + f"【全书的问题清单】\n这些是手册自带的题，不是让你重复的题——把它们当跳板：\n"
          f"读者刚碰的那道题，和哪几道在讲同一件事？哪两道摆在一起就矛盾了？\n{_compact_questions(idx)}\n\n"
        + f"【各关的「第三拍 · 出身」】\nvs_real 这条轴的锚点只能落在这几处：\n{thirds}\n"
    )


@dataclass
class ProbeJob:
    """冻结的一份快照。探索线程只认它，不看 PenSession——
    读者在探索期间又发一轮也不会把它改掉。"""

    session_id: str
    handbook_id: str
    original_path: Path
    anchor: dict[str, Any]
    atom: str
    chip: str
    user_text: str
    reply: str
    born_round: int
    lang: str
    cfg: LLMConfig
    extra_roots: list[Path] = field(default_factory=list)
    footprint: str = ""
    asked: list[str] = field(default_factory=list)
    shelf: str = ""
    # 前面几轮的对话摘要。只带 ui_messages 的角色和截断文本——
    # 不能带 session.messages（那里面有 tool_call 配对和 reasoning_content，
    # 塞进来 token 翻三倍，注意力还会被拉回代码细节）。
    history: list[dict[str, str]] = field(default_factory=list)


def build_user_message(job: ProbeJob, excerpt: str = "") -> str:
    a = job.anchor
    parts = [
        "[读者刚才在读哪儿]",
        f"位置：{a.get('level') or '—'} / {a.get('beat') or '—'} / {a.get('q_title') or '—'}"
        f"（第 {a.get('start_line')}-{a.get('end_line')} 行）",
        f"框选的原话：{str(a.get('selected_text') or '')[:300]}",
        "",
        "[读者自己说了什么]",
        f"芯片：{job.chip}",
        f"原话：{job.user_text or '（没打字，直接点的芯片）'}",
        "",
        "[师傅刚讲了什么]",
        "（已剥掉代码块，只留论述。细节在代码里，不在这儿——别去问代码里那些符号怎么写）",
        strip_code_fences(job.reply)[:1200],
    ]
    if job.history:
        parts += ["", "[前面几轮聊了什么]", "（越靠下越近。看看他一路在纠结哪条线）"]
        for h in job.history:
            who = "读者" if h.get("role") == "user" else "师傅"
            parts.append(f"  {who}：{str(h.get('text') or '')[:120]}")
    if job.footprint:
        parts += ["", "[读者这段时间的足迹]", job.footprint]
    if job.shelf:
        parts += ["", "[工作目录里还有这些教材]",
                  "（只给标题和大纲。想引用就明说是哪一本，别假装读过正文）", job.shelf]
    if job.asked:
        parts += ["", "[已经问过的，别撞车]"] + [f"- {x}" for x in job.asked[:20]]
    if excerpt:
        parts += ["", "[你要的那几段正文]", excerpt]
    return "\n".join(parts)


def should_probe(
    *,
    enabled: bool,
    # 调用点（_maybe_probe 挂在 done 事件上）恒为 True——出了 error 就不会有
    # done。留着这个参数是为了让这个纯函数自己说得清完整的判定条件，
    # 也让「失败轮不花钱」这条规则有测试可断言。
    ok: bool,
    chip: str,
    pending: bool,
    reply: str,
    anchor: dict[str, Any] | None,
    probe_calls: int,
    pending_pool: int,
    has_llm: bool,
) -> tuple[bool, str]:
    """触发闸门。纯同步、零成本，在 done 那一刻判。

    读者选的是「每轮实质回复都探」，所以这里**没有轮次冷却**——
    下面每一条都是失控保护或语义排除，不是降频手段。
    """
    if not enabled:
        return False, "off"
    if not has_llm:
        return False, "no-llm"
    if not ok:
        return False, "turn-failed"
    if pending:
        return False, "awaiting-approval"
    if chip in ("search", "writeback"):
        # 写回场景下「这段要不要沉淀进第三拍」本身没错，只是不是学习问题。
        # 治法不是禁止模型这么说，是不在那个场景触发。
        return False, "not-a-learning-turn"
    # 注意是 <=：tutor._finish_text 里 has_substantive 的判据是 len(visible) > 80。
    # 写成 < 的话，正好 80 字那一轮两边判断相反。
    if len(reply or "") <= config.PROBE_MIN_REPLY_CHARS:
        return False, "reply-too-short"
    level = str((anchor or {}).get("level") or "")
    # 不照搬 diagnose.is_curriculum：它把「开篇」也排除，而真人的有机记录
    # 几乎全落在封面和开篇——照搬会让这个功能一次都不触发。
    # 开篇的「全景图：七块积木」恰恰是全书最适合搭桥的地方。
    if level in ("封面", "附录") or not level:
        return False, "cover-or-appendix"
    if probe_calls >= config.PROBE_MAX_PER_SESSION:
        return False, "budget"
    if pending_pool >= config.PROBE_PENDING_CAP:
        return False, "backlog-full"
    return True, ""


def _create(cfg: LLMConfig, messages: list[dict[str, str]]) -> str:
    from openai import OpenAI

    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=config.PROBE_TIMEOUT)
    resp = client.chat.completions.create(
        model=cfg.model,
        messages=messages,
        stream=False,
        # 刻意不传 tools。见模块注释——这是结构性的成本保证，不是自律。
    )
    return (resp.choices[0].message.content or "").strip()


def _reading_roots(job: ProbeJob) -> list[Path]:
    """probe 读正文时实际认的根。和 anchor_source / _read_excerpts 里那个
    `job.extra_roots or [config.REPO_ROOT]` 是同一个表达式——书架的可见性
    必须跟它一致，列出一本读不到的书等于让模型对着空气搭桥。"""
    from pen.sandbox import reading_roots

    return reading_roots(job.original_path, job.extra_roots or [config.REPO_ROOT])


def _shelf_paths(current_path: Path, roots: list[Path] | None = None) -> dict[str, Path]:
    """书名 → 路径。只收**已登记且落在允许根内**的，跟 shelf_digest 同一道闸——
    登记表里躺着指向 /private/var/folders 的 pytest 夹具。

    `current_path` 是必需的：它既定排序又定「排除哪一本」。给默认值会让漏传
    静默退化成一张全表或空表，而空表和「书架上真的没有别的书」长得一模一样。

    顺序必须和 shelf_digest 一致（同一棵目录树优先、再按 mtime）：同一本书在仓库根
    和 vault 各有一份、标题一模一样，`setdefault` 是先到先得。书架上列的是 vault
    那份，反查却给仓库根那份旧副本，模型读到的和它以为在读的不是同一个文件。
    """
    from pen import libraries
    from pen.config import handbook_allow_roots
    from pen.library_scan import pick_books

    if roots is None:
        roots = handbook_allow_roots()
    # key 用 str(Path(...)) 规范化过的形式：pick_books 回传的 d["path"] 是
    # str(Path(raw))，会把 `//` 和尾斜杠吃掉。登记表现在存的是 resolve 过的干净
    # 路径所以对得上，但拿原始字符串当 key 是在赌这一点——赌输了就静默少一个
    # meta.title 的 key，表现为「某个简称突然反查不到」。
    metas = {str(Path(m.original_path)): m for m in libraries.list_handbooks()}
    # 和 shelf_digest 共用 pick_books：它决定模型「看得见哪几本」，这里决定它
    # 点名时「能反查到哪几本」。两边各筛一遍就会错位——原来这边没有 MAX_FILES
    # 上限，第 9 本以后模型从没见过的书照样在「书名沾边的有几本」里投票，
    # 能把它唯一看得见的那本否决掉。排除当前这本也由 pick_books 统一负责。
    out: dict[str, Path] = {}
    for d in pick_books(current_path, list(metas), roots):
        p = Path(str(d["path"]))
        out.setdefault(str(d["title"]), p)
        meta = metas.get(str(d["path"]))
        if meta is not None:
            out.setdefault(meta.title, p)
    return out


def _read_excerpts(job: ProbeJob, reads: Sequence[dict[str, Any]]) -> str:
    """定向读正文。由 Python 执行，不给模型自主循环的机会。

    支持点名读书架上的别本（`book` 字段）——不然「结合其他教材」这条就只能
    停在标题层。目标必须是已登记且落在允许根内的，读取次数上限跨不跨书都一样。
    """
    from pen.readtool import read_file_report

    shelf: dict[str, Path] | None = None
    chunks = []
    for r in list(reads)[: config.PROBE_MAX_READS]:
        try:
            start = max(1, int(r.get("start_line") or 1))
        except (TypeError, ValueError):
            continue
        target = job.original_path
        label = ""
        want = str(r.get("book") or "").strip()
        if want:
            if shelf is None:
                try:
                    shelf = _shelf_paths(job.original_path, _reading_roots(job))
                except Exception:
                    shelf = {}
            hit = shelf.get(want)
            if hit is None:
                # 模糊匹配只认**唯一**命中。「手册」「教材」这种词谁都沾边，
                # 猜中哪本纯看 dict 顺序，猜错就是拿别的书冒充。
                # 数的是**书**不是 key：_shelf_paths 给每本登记两个 key
                # （正文 H1 和 meta.title），Obsidian 笔记带 YAML frontmatter 时
                # H1 被推离第 1 行、build_index 退回文件名，两个 key 就不一样了——
                # 同一本书占两条候选，按 key 数会误判成歧义，把简称全毙掉。
                # 而 frontmatter 正是 vault 里第三方教材的默认形态。
                # 按 resolve 后的路径去重：out 里存的是未 resolve 的 Path(raw)，
                # 两条登记记录指向同一个文件却写法不同（一条带 ..）时，
                # 裸 Path 比较不相等，又会退回「同一本书被当成两本」。
                cands: dict[Path, Path] = {}
                for k in shelf:
                    if want in k or k in want:
                        try:
                            cands.setdefault(shelf[k].expanduser().resolve(), shelf[k])
                        except Exception:
                            cands.setdefault(shelf[k], shelf[k])
                hit = next(iter(cands.values())) if len(cands) == 1 else None
            if hit is None:
                continue  # 点了一本书架上没有的，忽略而不是回退到当前这本
            target = hit
            # 标签用**书架上印的那个名字**（_shelf_paths 先插正文 H1、后插 meta.title，
            # 所以第一个指向它的 key 就是书架印的），不用模型写的 want：
            # 它写「教材」，题面就会引用一本叫《教材》的书，书架上根本没有这本。
            shown = next((k for k, v in shelf.items() if v == hit), want)
            label = f"〔出自《{shown}》〕\n"
        # end_line 以前被忽略，一律读 80 行——模型要的「一段」和拿到的
        # 不一定是一回事。给了就按它要的算，仍受 PROBE_READ_LINES 封顶。
        try:
            end = int(r.get("end_line") or 0)
        except (TypeError, ValueError):
            end = 0
        span = end - start + 1 if end >= start else config.PROBE_READ_LINES
        out = read_file_report(
            job.original_path,
            str(target),
            offset=start,
            limit=max(1, min(span, config.PROBE_READ_LINES)),
            extra_roots=job.extra_roots or [config.REPO_ROOT],
        )
        if out.get("ok"):
            chunks.append(label + str(out.get("text") or ""))
    return "\n\n".join(chunks)


def explore(job: ProbeJob, idx: HandbookIndex) -> tuple[list[DeepQuestion], str]:
    """跑一次探索。返回 (问题, 原因)。最多两次调用，最多两段读取。"""
    system = build_system(idx, job.lang)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": build_user_message(job)},
    ]
    raw = _create(job.cfg, messages)
    data = parse_probe_json(raw)
    excerpt = ""
    if data["need_read"]:
        excerpt = _read_excerpts(job, data["need_read"])
        if excerpt:
            messages[-1] = {"role": "user", "content": build_user_message(job, excerpt)}
            raw = _create(job.cfg, messages)
            data = parse_probe_json(raw)
    items = _harvest(data["questions"], job, idx, excerpt)
    return items, ("" if items else "no-candidate")


def _harvest(
    raw_items: Sequence[dict[str, Any]],
    job: ProbeJob,
    idx: HandbookIndex,
    excerpt: str = "",
) -> list[DeepQuestion]:
    from pen.session import FIXED_CHIPS, PROMPT_EXAMPLE_LINES


    kept: list[DeepQuestion] = []
    seen: set[str] = {normalize_qkey(a) for a in job.asked}
    open_used = 0
    for raw in raw_items:
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        try:
            depth = int(raw.get("depth") or 0)
        except (TypeError, ValueError):
            depth = 0
        if depth and depth < 4:
            continue
        # 不能短路。excerpt 非空（跨书真读到了）时 `or` 会让当前手册锚点行的正文
        # 根本不取，而 validate_slots 要求题面每个反引号词都在 source 里——
        # 跨教材题天然两边各引一个词，于是**读了反而归零，不读倒能过**。
        # probe.py 上面那段注释记着这个 bug 修过一次（「拿不到正文时跳过，
        # 宁可漏也别误杀」），`excerpt or` 在跨书这条新路上原样复活了它。
        # 两段都是合法出处，拼起来。
        src = "\n".join(
            x for x in (excerpt, anchor_source(raw, job.original_path, job.extra_roots)) if x
        )
        ok, _why = validate_slots(raw, idx, source=src)
        if not ok:
            continue
        grounding = str(raw.get("grounding") or "book")
        if grounding == "open":
            if open_used >= 1:
                continue
            open_used += 1
        # 复用实时层那套硬过滤：占位符、导航、复读、撞车、长度带
        cleaned = clean_candidates(
            [text],
            example_lines=PROMPT_EXAMPLE_LINES,
            fixed_labels=[str(c["label"]) for c in FIXED_CHIPS],
            user_text=job.user_text,
            asked=list(seen),
            limit=1,
            kind="deep",
        )
        if not cleaned:
            continue
        final = cleaned[0]["text"]
        key = normalize_qkey(final)
        if key in seen:
            continue
        # 逐条过 clean_candidates 时相互去重是失效的（每次只喂一条），
        # 这里补上近似检查——「每轮都探」会把近似重复放大。
        if any(similarity(final, k.text) >= 0.72 for k in kept):
            continue
        if any(similarity(final, a) >= 0.72 for a in job.asked):
            continue
        seen.add(key)
        timing = "now" if str(raw.get("timing") or "") == "now" else "later"
        kept.append(
            DeepQuestion(
                id=f"d{job.born_round}-{len(kept)}",
                text=final,
                why=str(raw.get("why") or "")[:160],
                axis=str(raw.get("axis") or ""),
                grounding=grounding,
                anchors=[a for a in (raw.get("anchors") or []) if isinstance(a, dict)],
                timing=timing,
                target=str(raw.get("target") or "")[:40],
                depth=depth,
                atom=job.atom,
                born_round=job.born_round,
            )
        )
        if len(kept) >= 2:
            break
    return kept


def run_probe(job: ProbeJob, probe_id: str) -> None:
    """后台线程的入口。异常绝不能冒出去——这条路径上没有人在等它。"""
    from pen import libraries, probe_store

    acquired = _SEM.acquire(blocking=False)
    if not acquired:
        # 抢不到就跳过，不排队：排队意味着上下文已经过期还要再花一次钱。
        # 一次调用都没打，配额要退。
        probe_store.release(job.session_id, probe_id, refund=True)
        return
    try:
        idx = libraries.load_index(job.handbook_id)
        # 书架要扫盘（登记表 + 逐本读前 400 行），放在这儿而不是 done 事件里。
        # 之前它顶在 done 前面同步跑，跟「绝不延长这条流」的设计目标直接相冲。
        if not job.shelf:
            try:
                from pen import library_scan

                job.shelf = library_scan.shelf_digest(
                    job.original_path,
                    [m.original_path for m in libraries.list_handbooks()],
                    # 和 _read_excerpts 实际读取用的根保持一致，否则书架上列着
                    # 一本 _read_excerpts 读不到的书，模型点名去读就静默落空。
                    allow_roots=_reading_roots(job),
                )
            except Exception:
                job.shelf = ""
        items, _reason = explore(job, idx)
        probe_store.add_questions(job.session_id, probe_id, items)
    except Exception:
        try:
            probe_store.release(job.session_id, probe_id)
        except Exception:
            pass
    finally:
        _SEM.release()


def spawn(job: ProbeJob, probe_id: str) -> None:
    """daemon 线程，不用 ThreadPoolExecutor——后者注册了 atexit join，
    一个卡在 30 秒调用上的 worker 会让 Ctrl-C 停 30 秒。"""
    t = threading.Thread(
        target=run_probe, args=(job, probe_id), name=f"probe-{probe_id}", daemon=True
    )
    t.start()


# 读者点了 open 题之后，回答那一刻必须再说一次实话。
# 光在生成问题时标记没用——模型侃侃而谈报一堆假 API，前面的诚实全白搭。
OPEN_INTENT_ZH = (
    "这题手册里没有出处。凭你自己的知识讲，开头就挑明这是记忆不是检索。"
    "不确定的地方直接说不确定。不要编版本号、行号、函数名、文件路径。"
)
OPEN_INTENT_EN = (
    "This one has no source in the manual. Answer from your own knowledge and say up "
    "front that this is memory, not a lookup. Say so when you are unsure. "
    "Do not invent version numbers, line numbers, function names or file paths."
)


def open_intent(lang: str = "zh") -> str:
    return OPEN_INTENT_EN if lang == "en" else OPEN_INTENT_ZH
