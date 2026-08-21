import {
  ItemView,
  MarkdownRenderer,
  MarkdownView,
  Notice,
  setIcon,
  setTooltip,
  TFile,
  WorkspaceLeaf,
} from "obsidian";
import type SocratesPenPlugin from "../main";
import { makeApi, streamApprove, streamChat } from "../api";
import { handbookIdFromPath, vaultRoot, type EditorPick } from "../selection";
import type {
  ChatMessage,
  Chip,
  DynChip,
  PendingEdit,
  SessionView,
  SpendBook,
  TokenRow,
} from "../types";
import { chipHint, chipLabel, phaseText, t } from "../i18n";
import { dropAsked, keepDeep, mergeDeep, pollDeep } from "../deeppoll";
import { measureMonoAdvance, renderSplash, type SplashLevel } from "./splash";
import { AVATAR } from "../logo";

export const VIEW_TYPE_PEN = "socrates-pen-view";

const sleep = (ms: number): Promise<void> =>
  new Promise((r) => window.setTimeout(r, ms));

/**
 * 面板里长生命周期的元素句柄。
 *
 * 用一个 bag 而不是十几个字段，是为了让 onClose() 只写一行就不可能漏。
 * 漏掉任何一个的后果是静默的：重开面板后往已脱离文档的节点上写，不报错，
 * 只表现为「面板一片空白」。
 */
type Els = {
  dot: HTMLElement;
  brandSub: HTMLElement;
  alert: HTMLElement;
  log: HTMLElement;
  panel: HTMLElement;
  quote: HTMLElement;
  chips: HTMLElement;
  bar: HTMLElement;
  status: HTMLElement;
  input: HTMLInputElement;
  ask: HTMLButtonElement;
  pick: HTMLButtonElement;
  fresh: HTMLButtonElement;
  undo: HTMLButtonElement;
  redo: HTMLButtonElement;
};

function visibleReply(text: string): string {
  return text.replace(/<!--pen:chips[\s\S]*?-->/g, "").trim();
}

/** 深挖的排前面。同类保持后端给的顺序。 */
function dynRank(c: DynChip): number {
  return c.kind === "deep" ? 0 : 1;
}

/**
 * 从 done 事件里取动态追问。
 *
 * sidecar >= v0.8.0 发富格式 dyn_chips；更早的只有 dynamic_chips: string[]。
 * 插件和 sidecar 是两个进程，版本可能对不上，所以两种都要认。
 */
function readDynChips(ev: Record<string, unknown>): DynChip[] {
  const rich = ev.dyn_chips;
  if (Array.isArray(rich)) {
    return rich
      .filter((c): c is DynChip => Boolean(c) && typeof (c as DynChip).text === "string")
      .map((c, i) => ({ id: c.id || `d${i}`, kind: c.kind === "deep" ? "deep" : "quick", text: c.text, why: c.why }));
  }
  const flat = ev.dynamic_chips;
  if (!Array.isArray(flat)) return [];
  return flat
    .filter((x): x is string => typeof x === "string" && x.trim().length > 0)
    .map((text, i) => ({ id: `q${i}`, kind: "quick" as const, text }));
}

function toolCaption(m: ChatMessage): { ok: boolean; file: string; kicker: string } {
  const ok = m.ok !== false;
  const name = m.text.split(" ")[0] || "tool";
  const path = m.text.split("\u2192").slice(1).join("\u2192").trim();
  const file = path.split("/").filter(Boolean).pop() || (path || t().noPath);
  const kicker = name === "edit_file" ? t().kickerEditTool : t().kickerReadTool;
  return { ok, file, kicker };
}

/**
 * 助手气泡的外壳：左边挂一个微缩苏格拉底，正文另起一列。
 *
 * 头像整幅用**一个** div + white-space:pre 承载，不逐行拆——它没有逐行动画，
 * 而对话里可能有几十条助手消息，逐行拆会平白多出几百个节点。
 */
function assistantShell(turn: HTMLElement): HTMLElement {
  const av = turn.createDiv({ cls: "sp-avatar", text: AVATAR.lines.join("\n") });
  av.setAttr("aria-hidden", "true");
  return turn.createDiv({ cls: "sp-turn-main" });
}

export class PenView extends ItemView {
  plugin: SocratesPenPlugin;
  private status = "";
  /** 结构化存放，成文推迟到 setStatus——否则切语言后这一行还是旧语言。 */
  private usage: { ctx?: number; out?: number } | null = null;
  private err = "";
  private health = t().healthUnprobed;
  private msgs: ChatMessage[] = [];
  private chips: Chip[] = [];
  private dyn: DynChip[] = [];
  private busy = false;
  private substantive = false;
  private sessionId: string | null = null;
  private handbookId: string | null = null;
  private capturedPath: string | null = null;
  private sidecarReachable = false;
  private paintGen = 0;
  private painting = false;
  private quote = "";
  private startLine = 1;
  private endLine = 1;
  private undoN = 0;
  private redoN = 0;
  private pending: PendingEdit | null = null;
  private approving = false;
  private chipsSig = "";
  private splashLevel: SplashLevel = "none";
  /** paintLog 正在跑时新来的 force 请求存这里，别让它被吞掉。 */
  private forceStick = false;
  /** 深挖轮询的代号。切笔记/关面板时 ++ 一下，在途的那一拍自己早退。 */
  private deepGen = 0;
  private deepCursor = 0;
  /** 配额用满时挂在用量那一格后面。不说的话读者只看到「深题突然不来了」。 */
  private deepNote = "";
  /** 有新深题刚到：这一轮画完把它滚进可视区。 */
  private deepArrived = false;
  /**
   * 本会话累计花掉的 token，按用途分格。和 usage 是**两个口径**：
   * usage 是「此刻窗口占多大」（诊断用），这个是「一共烧了多少」（花费）。
   * 服务端每轮 done 给权威值，中途由 spend 事件和深挖轮询各推各的那一格。
   */
  private spend: SpendBook = {};
  /** 本轮已花。忙的时候挂在状态行尾巴上，看着它爬。 */
  private turnTokens = 0;
  /** tooltip 的内容签名。没变就一个 DOM 都不碰。 */
  private statusTipSig = "";
  /**
   * 本轮推理已经流下来多少字。0 表示不在推理阶段。
   *
   * 只存数不存内容：实测一次回复有 1633 个推理分片，把正文塞进窄侧栏是灾难，
   * 而读者要的是「没卡住」，不是「让我读它的草稿」。
   */
  private thinkChars = 0;
  private els: Els | null = null;

  constructor(leaf: WorkspaceLeaf, plugin: SocratesPenPlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType(): string {
    return VIEW_TYPE_PEN;
  }

  getDisplayText(): string {
    return t().viewTitle;
  }

  getIcon(): string {
    return "highlighter";
  }

  async onOpen(): Promise<void> {
    // 换主题可能换掉等宽字体，字宽比要重测。registerEvent 由 Component 自动注销。
    this.registerEvent(this.app.workspace.on("css-change", () => this.refreshAdvance()));
    this.renderShell();
    await this.probeHealth();
  }

  /** 框架回调：侧栏被拖动，或从折叠状态展开（那时首次测量拿到的是 0）。 */
  onResize(): void {
    this.refreshAdvance();
  }

  /**
   * 字符画的字号由 CSS 反解：字号 = 可用宽 / (列数 × 字宽比)。
   * 列数写在元素上，字宽比是全局的，在这里量一次存进 CSS 变量。
   */
  private refreshAdvance(): void {
    const root = this.contentEl;
    if (root.clientWidth === 0) return; // 折叠时量不出来，保留上次的值
    root.style.setProperty("--sp-adv", String(measureMonoAdvance(root)));
  }

  async onClose(): Promise<void> {
    this.stopDeepPoll();
    this.els = null;
    this.chipsSig = "";
  }

  private api() {
    return makeApi(this.plugin.settings.sidecarUrl);
  }

  /**
   * 语言切换后重画整个面板。
   *
   * 绝大多数文案是渲染时现取 t() 的，重画即完成本地化。两个例外：
   *  - `health` 存的是**已成文**的字符串，所以这里重新探测一次；
   *  - `err` 可能是 sidecar 下发的原文（本就不翻），保留不动。
   * `usage` 已经改成结构化，成文推迟到 setStatus，不受影响。
   */
  relocalize(): void {
    this.chipsSig = ""; // 芯片文案变了，作废签名缓存，强制重建
    this.splashLevel = "none";
    this.renderShell();
    void this.probeHealth();
  }

  /**
   * 建骨架。正常情况下只在 onOpen 跑一次——所有元素和事件监听的生命周期等于视图
   * 本身，之后的每次刷新都只改属性、不重建节点。**唯一的例外是 relocalize()**，
   * 切语言时会重跑一遍；`root.empty()` 会连带清掉这里绑的监听，所以不会泄漏，
   * 但如果以后往这里加 registerDomEvent 之类挂在 document 上的监听，要自己去重。
   *
   * 五层，自上而下：品牌条 / 错误条 / 对话区 / 审批面板 / 底座。
   * 只有对话区 grow，其余按 flex-shrink 权重依次让位，输入行永不压缩。
   */
  private renderShell(): void {
    const root = this.contentEl;
    root.empty();
    root.addClass("socrates-pen");
    root.style.setProperty("--sp-adv", String(measureMonoAdvance(root)));

    const brand = root.createDiv({ cls: "sp-brand" });
    const dot = brand.createSpan({ cls: "sp-dot" });
    brand.createSpan({ cls: "sp-brand-name", text: t().appName });
    const brandSub = brand.createSpan({ cls: "sp-brand-sub" });
    const tools = brand.createDiv({ cls: "sp-brand-tools" });
    const fresh = tools.createEl("button", { cls: "sp-icon" });
    setIcon(fresh, "square-pen");
    const undo = tools.createEl("button", { cls: "sp-icon" });
    setIcon(undo, "undo-2");
    const redo = tools.createEl("button", { cls: "sp-icon" });
    setIcon(redo, "redo-2");

    const alert = root.createDiv({ cls: "sp-alert is-off" });
    const log = root.createDiv({ cls: "sp-log" });
    const panel = root.createDiv({ cls: "sp-panel is-off" });

    const dock = root.createDiv({ cls: "sp-dock" });
    const quote = dock.createDiv({ cls: "sp-quote is-off" });
    const chips = dock.createDiv({ cls: "sp-chips" });
    // 忙的时候横在状态行上面的一条进度条。**不确定型**——我们不知道模型
    // 还要写多久，假装知道就是在骗人。它只回答一件事：还在动。
    // 真实的进度数字在下面那行状态行里（推理字数是真从流里数出来的）。
    const bar = dock.createDiv({ cls: "sp-bar is-off" });
    bar.createDiv({ cls: "sp-bar-fill" });
    const status = dock.createDiv({ cls: "sp-status is-off" });
    const form = dock.createDiv({ cls: "sp-form" });
    const pick = form.createEl("button", { cls: "sp-pick", text: t().btnUseSelection });
    const input = form.createEl("input", { cls: "sp-input" });
    const ask = form.createEl("button", { cls: "sp-send mod-cta", text: t().btnAsk });

    this.els = {
      dot, brandSub, alert, log, panel,
      quote, chips, bar, status, input, ask, pick, fresh, undo, redo,
    };

    // 事件只绑一次。pick 用 bindKeepFocus：pointerdown 阶段就取选区，
    // 否则点击导致的焦点转移会先把编辑器选区清掉。
    this.bindKeepFocus(pick, () => {
      void this.captureSelection();
    });
    setTooltip(pick, t().tipUseSelection);
    setTooltip(fresh, t().tipNewSession);
    fresh.onclick = () => void this.newSession();
    undo.onclick = () => void this.doRollback();
    redo.onclick = () => void this.doRedo();
    input.placeholder = t().askPlaceholder;
    ask.onclick = () => this.submitAsk();
    input.addEventListener("keydown", (ev: KeyboardEvent) => {
      // isComposing 必须挡：中文输入法里回车是确认候选词，不是发送。
      if (ev.key !== "Enter" || ev.isComposing) return;
      ev.preventDefault();
      this.submitAsk();
    });

    this.paintBar();
    void this.paintLog();
  }

  /** 扇出壳。既有的 paintBar() 调用点全部保留语义，内部改成只更新各自那一块。 */
  private paintBar(): void {
    this.paintBrand();
    this.paintAlert();
    this.paintQuote();
    this.paintChips();
    this.paintPanel();
    this.setStatus();
    this.setBusy();
  }

  private paintBrand(): void {
    const e = this.els;
    if (!e) return;
    e.dot.classList.toggle("is-ok", this.sidecarReachable);
    e.dot.classList.toggle("is-down", !this.sidecarReachable && Boolean(this.err));
    if (e.brandSub.textContent !== this.health) e.brandSub.textContent = this.health;
    setTooltip(e.brandSub, this.health);
  }

  private paintAlert(): void {
    const e = this.els;
    if (!e) return;
    e.alert.classList.toggle("is-off", !this.err);
    if (this.err && e.alert.textContent !== this.err) e.alert.textContent = this.err;
  }

  private paintQuote(): void {
    const e = this.els;
    if (!e) return;
    // 宽栏下 CSS 会放开到 4-6 行，文本上限也跟着放宽；真正的裁剪交给 line-clamp
    const text = this.quote.slice(0, 420);
    e.quote.classList.toggle("is-off", !this.quote);
    if (e.quote.textContent !== text) e.quote.textContent = text;
  }

  private static row(r: TokenRow | undefined): number {
    return (r?.in_tokens ?? 0) + (r?.out_tokens ?? 0);
  }

  private sessionTokens(): number {
    const s = this.spend;
    return PenView.row(s.chat) + PenView.row(s.probe) + PenView.row(s.fold);
  }

  /** 悬停明细。只在闲下来时求值——拼字符串的活别挂在 token 事件的频率上。 */
  private spendTip(): string {
    const s = this.spend;
    const cached =
      (s.chat?.cached_tokens ?? 0) + (s.probe?.cached_tokens ?? 0) + (s.fold?.cached_tokens ?? 0);
    const lines = [
      t().spendTipTotal(this.sessionTokens()),
      t().spendTipRow(t().spendKindChat, s.chat?.in_tokens, s.chat?.out_tokens),
      t().spendTipRow(t().spendKindProbe, s.probe?.in_tokens, s.probe?.out_tokens),
      t().spendTipRow(t().spendKindFold, s.fold?.in_tokens, s.fold?.out_tokens),
    ];
    if (cached > 0) lines.push(t().spendTipCached(cached));
    lines.push(t().spendTipNote);
    return lines.join("\n");
  }

  /**
   * 状态行一格三用：
   *   忙 → 苏格拉底在想… · 本轮 3.2k
   *   闲 → 上下文 12.4k · 回复 0.8k · 本会话 128k [· 深挖已用满]
   *
   * 忙的那条尾巴不是装饰：实时计量最有价值的时刻恰恰是忙的时候——看着数字
   * 往上爬，是失控循环唯一看得见的信号。没有它这功能只在事后有效。
   *
   * 一轮对话跑完之后用量一直在，所以后续来回不会因为状态出现/消失而跳高度；
   * 只有全新会话发第一问之前这一行是空的（隐藏）。
   *
   * **三条路径都只往同一个文本节点写。绝不能在这里调 paintBar()**——
   * token 事件每 48 字符来一次，那会把整条底座重建几十次。
   */
  private setStatus(): void {
    const e = this.els;
    if (!e) return;
    const total = this.sessionTokens();
    const parts = this.busy
      ? [
          // 推理阶段用活体计数顶掉「在想…」那句死文案：它在跳，读者才知道没卡住。
          this.thinkChars ? t().thinkTick(this.thinkChars) : this.status,
          this.turnTokens ? t().spendTurn(this.turnTokens) : "",
        ]
      : [
          this.usage ? t().usage(this.usage.ctx, this.usage.out) : "",
          total ? t().spendSession(total) : "",
          this.deepNote,
        ];
    const line = parts.filter(Boolean).join(" \u00b7 ");
    e.status.classList.toggle("is-off", !line);
    e.status.classList.toggle("is-usage", !this.busy);
    if (e.status.textContent !== line) e.status.textContent = line;
    // tooltip 会建 DOM，忙的时候一律不碰；内容没变也不碰。
    if (this.busy) return;
    const sig = JSON.stringify(this.spend);
    if (sig === this.statusTipSig) return;
    this.statusTipSig = sig;
    setTooltip(e.status, total ? this.spendTip() : "");
  }

  /**
   * done 事件的收尾。**两处消费点（chat / approve）共用这一份。**
   * 它们本来是逐字复制的两坨，这次要各改两行，下次还是各改两行。
   */
  private takeDone(ev: Record<string, unknown>): void {
    this.status = "";
    const u = ev.usage as {
      context_tokens?: number;
      prompt_tokens?: number;
      completion_tokens?: number;
    };
    this.usage = { ctx: u?.context_tokens ?? u?.prompt_tokens, out: u?.completion_tokens };
    // 服务端给的是权威值（主对话 + 写回 + 从账本合进来的深挖）。
    // 这条路径同时是自愈通道：轮询漏掉的、面板关着那阵子的深挖花销，
    // 下一轮 done 一定补齐。
    if (ev.spend) this.spend = ev.spend as SpendBook;
    this.turnTokens = 0;
    // 只换掉实时层那两条。深题是单独花一次调用挖出来的，服务端已经
    // 标成 shown、游标也推过去了，整体覆盖等于点一次就再也找不回来。
    this.dyn = [...keepDeep(this.dyn), ...readDynChips(ev)];
    // done 每轮都捎着池子里成熟的题——这条路不依赖探索跑不跑，
    // 所以点过一条之后下一条当轮就顶上来。
    const ripe = (ev.deep_items || []) as DynChip[];
    if (ripe.length) {
      this.dyn = mergeDeep(this.dyn, ripe);
      this.deepCursor = Math.max(this.deepCursor, Number(ev.deep_cursor) || 0);
      this.deepArrived = true;
    }
    this.substantive = Boolean(ev.has_substantive);
    // 后台在挖。轮询自己会因为 running 变空而停，不用管它。
    // approve 那条线不下发这个键，于是这里是个空操作。
    if (ev.deep_running) void this.pollDeep();
  }

  /** 每打完一枪服务端报一次。只刷状态行那一个文本节点。 */
  private takeSpend(ev: Record<string, unknown>): void {
    this.turnTokens = Number(ev.turn) || 0;
    // 只推 chat 那一格：深挖走轮询那条通道，写回有自己的轮次。
    if (ev.chat) this.spend = { ...this.spend, chat: ev.chat as TokenRow };
    this.setStatus();
  }

  /** 只改 disabled，绝不碰 DOM 结构——这是流式期间唯一被高频调用的路径之一。 */
  private setBusy(): void {
    const e = this.els;
    if (!e) return;
    const blocked = this.busy || Boolean(this.pending);
    // 忙就亮。以前那十几秒里读者唯一能看到的是底栏一行 10px 小字，
    // 等于没有——这条横在整条底座上，扫一眼就知道它还在动。
    e.bar.classList.toggle("is-off", !this.busy);
    e.input.disabled = blocked;
    e.ask.disabled = blocked;
    e.pick.disabled = this.busy;
    e.fresh.disabled = blocked;
    e.undo.disabled = this.busy || this.undoN <= 0;
    e.redo.disabled = this.busy || this.redoN <= 0;
    setTooltip(e.undo, this.undoN > 0 ? t().tipUndo(this.undoN) : t().tipUndoEmpty);
    setTooltip(e.redo, this.redoN > 0 ? t().tipRedo(this.redoN) : t().tipRedoEmpty);
    this.syncChipDisabled();
  }

  private syncChipDisabled(): void {
    const e = this.els;
    if (!e) return;
    const blocked = this.busy || Boolean(this.pending);
    const btns = e.chips.querySelectorAll("button");
    for (let i = 0; i < btns.length; i++) {
      const b = btns[i] as HTMLButtonElement;
      b.disabled = blocked || b.dataset.off === "1";
    }
  }

  /** 内容没变就只翻 disabled，不重建按钮——否则流式期间每 48 字符重建一次芯片。 */
  private paintChips(): void {
    const e = this.els;
    if (!e) return;
    const sig = JSON.stringify([
      this.chips.map((c) => [c.id, c.label, c.enabled, c.hint ?? ""]),
      this.dyn,
      this.substantive,
    ]);
    if (sig === this.chipsSig) {
      this.syncChipDisabled();
      return;
    }
    this.chipsSig = sig;
    e.chips.empty();
    for (const c of this.chips) {
      const on = c.id === "writeback" ? this.substantive : c.enabled;
      // 后端下发的是中文 label，但带稳定的英文 id。查得到就本地化，
      // 查不到（后端新增了芯片）就照抄它下发的原文，不会变成空按钮。
      const b = e.chips.createEl("button", { text: chipLabel(c.id, c.label) });
      b.dataset.off = on ? "0" : "1";
      const hint = chipHint(c.id, c.hint ?? "");
      if (hint) setTooltip(b, hint);
      b.onclick = () => void this.send(c.id, "");
    }
    // 深挖的排在前面：它们是「跳出来」的问题，读者最容易忽略，别沉到底下。
    for (const d of [...this.dyn].sort((a, b2) => dynRank(a) - dynRank(b2))) {
      const deep = d.kind === "deep";
      const cls = deep ? "is-dyn is-deep" : "is-dyn";
      const b = e.chips.createEl("button", {
        text: deep ? `${t().tipDeepPrefix}${d.text}` : d.text,
        cls,
      });
      b.dataset.off = "0";
      if (d.why) setTooltip(b, d.why);
      b.onclick = () => void this.send("free", d.text);
    }
    e.chips.classList.toggle("is-off", e.chips.childElementCount === 0);
    this.syncChipDisabled();
    // 窄侧栏下三个固定芯片就占满了 6.4em，深题「排在最前」也还是在它们之后，
    // 实测 300px 时完全落在滚动区外——等于没抛。滚一下才真的看得见。
    if (this.deepArrived) {
      this.deepArrived = false;
      const first = e.chips.querySelector(".is-deep");
      if (first) first.scrollIntoView({ block: "nearest" });
    }
  }

  private paintPanel(): void {
    const e = this.els;
    if (!e) return;
    const p = this.pending;
    e.panel.empty();
    e.panel.classList.toggle("is-off", !p);
    if (!p) return;
    e.panel.createEl("h4", { text: t().approvalTitle });
    e.panel.createDiv({
      cls: "sp-where",
      text: t().approvalTarget(p.name, p.args.path || t().approvalCurrentHandbook),
    });
    // 超长时必须标出来：用户是看着这段决定要不要放行写盘的，
    // 静默截断会让他以为自己看全了。
    const clip = (raw: string): string => {
      const text = raw || "";
      return text.length <= 800
        ? text
        : `${text.slice(0, 800)}\n${t().approvalTruncated(text.length - 800)}`;
    };
    const pre = e.panel.createEl("pre", { cls: "sp-fold" });
    pre.createDiv({
      cls: "sp-diff is-old",
      text: `${t().approvalOldLabel}\n${clip(p.args.old_string || "")}`,
    });
    pre.createDiv({
      cls: "sp-diff is-new",
      text: `${t().approvalNewLabel}\n${clip(p.args.new_string || "")}`,
    });
    e.panel.createDiv({ cls: "sp-warn", text: t().approvalWarn });
    const actions = e.panel.createDiv({ cls: "sp-panel-actions" });
    const yes = actions.createEl("button", { cls: "mod-cta", text: t().btnApprove });
    yes.disabled = this.approving;
    yes.onclick = () => void this.doApprove(true);
    const no = actions.createEl("button", { text: t().btnReject });
    no.disabled = this.approving;
    no.onclick = () => void this.doApprove(false);
  }

  private submitAsk(): void {
    const e = this.els;
    if (!e || e.input.disabled) return;
    const text = e.input.value.trim();
    if (!text) return;
    e.input.value = "";
    // 只有从输入框发起时才收回焦点；点芯片不抢编辑器光标。
    void this.send("free", text).then(() => this.els?.input.focus());
  }

  /**
   * 只有原本就贴在底部时才自动滚到底，否则用户往上翻历史会被一路拽回来。
   * 24px 容差吃掉一行的抖动。
   */
  private atBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight <= 24;
  }

  /** mode="force"：用户自己刚发了话，无论翻到哪都拉回底部。 */
  private async paintLog(mode: "auto" | "force" = "auto"): Promise<void> {
    // 先落到实例字段：下面可能因为已有重画在跑而直接返回，那时这个意图必须留住。
    // 否则「用户在助手还在流式时发了一句」会静默丢掉滚到底，正是 force 要解决的场景。
    if (mode === "force") this.forceStick = true;
    const gen = ++this.paintGen;
    const first = this.els?.log;
    if (!first || this.painting) return; // 在画的循环看到新 gen 会重画到最新
    // 必须在 empty() 之前测——之后 scrollTop / scrollHeight 已经没有意义了。
    const stick = mode === "force" || this.atBottom(first);
    this.painting = true;
    try {
      let g = gen;
      for (;;) {
        const log = this.els?.log;
        if (!log) return;
        log.empty();
        if (this.msgs.length === 0) {
          // 还没抓选区 -> 完整开屏；抓了但还没问 -> 只留字标，把高度让给引文和芯片。
          const level: SplashLevel = this.quote ? "mini" : "hero";
          const empty = log.createDiv({ cls: "sp-empty" });
          // 只在档位变化时播入场动画，否则空态被重画几次就会闪几次
          renderSplash(empty, { level, animate: level !== this.splashLevel });
          this.splashLevel = level;
          empty.createEl("p", { cls: "sp-hint", text: t().emptyHint });
          return;
        }
        this.splashLevel = "none";
        for (const m of this.msgs) {
          if (g !== this.paintGen || !this.els) break;
          if (m.role === "tool") {
            const cap = toolCaption(m);
            const row = log.createDiv({
              cls: cap.ok ? "sp-tool" : "sp-tool is-bad",
            });
            row.createSpan({ cls: "sp-kicker", text: cap.kicker });
            row.createSpan({
              cls: "sp-tool-msg",
              text: `${cap.ok ? t().toolOk : t().toolDenied} \u00b7 ${cap.file}`,
            });
            continue;
          }
          if (m.role === "assistant" && !m.text && this.pending) continue;
          const turn = log.createDiv({ cls: `sp-turn is-${m.role}` });
          const host = m.role === "assistant" ? assistantShell(turn) : turn;
          host.createDiv({
            cls: "sp-kicker",
            text: m.role === "user" ? t().kickerYou : t().kickerPen,
          });
          const body = host.createDiv({ cls: "sp-body" });
          if (m.role === "user") {
            // 历史气泡：后端存的 label 是建会话时那个语言的，有 chip id 就重查一次
            body.setText(m.chip ? chipLabel(m.chip, m.text) : m.text);
          } else {
            await MarkdownRenderer.render(
              this.app,
              visibleReply(m.text) || " ",
              body,
              "/",
              this,
            );
          }
        }
        const done = this.els?.log;
        if (g === this.paintGen && done) {
          // forceStick 现读：期间可能有新的 force 请求被上面那个早退吞掉了
          if (stick || this.forceStick) done.scrollTop = done.scrollHeight;
          this.forceStick = false;
          return;
        }
        g = this.paintGen; // 期间来了更新的请求，整条重画
      }
    } finally {
      this.painting = false;
    }
  }

  /**
   * 深挖收件箱的轮询。
   *
   * 用代号而不是 setInterval：切笔记的一瞬间 deepGen++ 就能让在途的那一拍
   * 自己早退，不用去追一个 timer id。五个终止条件缺一不可——少任何一个，
   * 关掉面板之后它还会在后台转。
   */
  private async pollDeep(): Promise<void> {
    if (!this.plugin.settings.deepQuestions) return;
    const gen = ++this.deepGen;
    const sid = this.sessionId;
    if (!sid) return;
    const alive = (): boolean =>
      gen === this.deepGen && Boolean(this.els) && this.sessionId === sid;
    await pollDeep({
      fetch: (since) => this.api().deepInbox(sid, since),
      alive,
      since: () => this.deepCursor,
      sleep,
      now: () => Date.now(),
      onItems: (items, cursor) => {
        this.deepCursor = cursor;
        this.mergeDeep(items);
        this.deepArrived = true;
        this.paintChips();
      },
      onSpend: (row) => {
        // 深挖那一格由这条通道推进。花钱的窗口恰好就是 running 非空的窗口，
        // 所以停轮询不会漏账；真漏了下一轮 done 也会补齐。
        if (JSON.stringify(this.spend.probe) === JSON.stringify(row)) return;
        this.spend = { ...this.spend, probe: row };
        this.setStatus();
      },
      onBudget: (b) => {
        const spent =
          (b.window_max ?? 0) > 0 && (b.window_used ?? 0) >= (b.window_max ?? 0);
        const note = spent ? t().deepQuotaSpent : "";
        if (note !== this.deepNote) {
          this.deepNote = note;
          this.setStatus();
        }
      },
    });
  }

  private stopDeepPoll(): void {
    this.deepGen++;
  }

  /** 深题追加进来，按文本去重。实时层那两条不动。 */
  private mergeDeep(items: DynChip[]): void {
    this.dyn = mergeDeep(this.dyn, items);
  }

  private paintStreamBubble(text: string): void {
    // 流式只刷最后一条助手正文；全量 markdown 重绘留给 done/finally
    const log = this.els?.log;
    if (!log || this.painting) return;
    // 只认最后一个元素。以前是向前遍历找最近的 is-assistant，但审批流里
    // 那条空占位会被 paintLog 跳过（见 msgs 循环里的 pending 判断），于是
    // 这里会一路找到**上一轮**的助手气泡，把它已渲染的 Markdown 覆盖掉。
    const last = log.lastElementChild as HTMLElement | null;
    let turn = last && last.hasClass("is-assistant") ? last : null;
    if (!turn) {
      turn = log.createDiv({ cls: "sp-turn is-assistant" });
      const host = assistantShell(turn);
      host.createDiv({ cls: "sp-kicker", text: t().kickerPen });
      host.createDiv({ cls: "sp-body" });
    }
    const main = (turn.querySelector(".sp-turn-main") as HTMLElement | null) ?? turn;
    const body =
      (main.querySelector(".sp-body") as HTMLElement | null) ??
      main.createDiv({ cls: "sp-body" });
    const stick = this.atBottom(log);
    body.setText(visibleReply(text) || t().streamPlaceholder);
    if (stick) log.scrollTop = log.scrollHeight;
  }

  async probeHealth(): Promise<void> {
    try {
      const h = await this.api().health();
      this.sidecarReachable = true;
      const fromPage = this.plugin.settings.apiKey.trim();
      const model = this.plugin.settings.model.trim() || h.llm.model;
      if (fromPage) {
        this.health = t().healthOkSettings(model);
      } else if (h.llm.ok) {
        this.health = t().healthOkFallback(h.llm.key_source, model);
      } else {
        this.health = t().healthNoKey;
      }
      this.err = "";
    } catch (e) {
      this.sidecarReachable = false;
      this.health = t().healthDown;
      this.err = t().errUnreachable(e instanceof Error ? e.message : String(e));
    }
    this.paintBar();
  }

  private bindKeepFocus(el: HTMLElement, fn: () => void): void {
    let fromPointer = false;
    el.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      fromPointer = true;
      fn();
    });
    el.addEventListener("click", () => {
      if (fromPointer) {
        fromPointer = false;
        return;
      }
      fn();
    });
  }

  async captureSelection(pick?: EditorPick | null): Promise<void> {
    const got = pick ?? this.plugin.takePick();
    await this.probeHealth();
    if (!this.sidecarReachable) {
      new Notice(t().noticeUnreachable);
      return;
    }
    if (!got) {
      this.err = t().errNoSelection;
      new Notice(this.err);
      this.paintBar();
      return;
    }
    try {
      const hid = handbookIdFromPath(got.absPath);
      await this.api().importHandbook(got.absPath, hid, vaultRoot(this.app));
      this.handbookId = hid;
      this.capturedPath = got.file.path;
      this.quote = got.text;
      this.startLine = got.startLine;
      this.endLine = got.endLine;
      this.err = "";
      const bind = this.plugin.noteBind(got.file.path);
      let sess: SessionView;
      if (bind?.session_id && bind.handbook_id === hid) {
        try {
          sess = await this.api().getSession(bind.session_id);
        } catch {
          sess = await this.api().createSession(hid);
        }
      } else {
        sess = await this.api().createSession(hid);
      }
      this.adopt(sess);
      await this.plugin.bindNote(got.file.path, {
        handbook_id: hid,
        session_id: sess.session_id,
      });
      await this.refreshSnapshots();
    } catch (e) {
      this.err = e instanceof Error ? e.message : String(e);
    }
    this.paintBar();
    await this.paintLog("force");
  }

  private adopt(sess: SessionView): void {
    this.sessionId = sess.session_id;
    this.chips = sess.chips;
    this.msgs = sess.ui_messages || [];
    this.substantive = Boolean(sess.has_substantive);
    // 换会话就换了一个收件箱，游标必须归零，在途的那一拍也要作废
    this.stopDeepPoll();
    this.deepCursor = 0;
    // 花销跟着会话走：换会话就换一本账，重开侧栏则从服务端把旧账取回来
    // ——不取的话，关一次面板第三格就归零，读者以为钱没花过。
    this.spend = sess.spend || {};
    this.turnTokens = 0;
    this.usage = null;
    this.statusTipSig = "";
    // 以前这里无条件清空，刷新一次上一轮的追问就永久丢了。
    this.dyn = (sess.dyn_chips || []).filter((c) => c && c.text);
    const p = sess.pending;
    this.pending =
      p && p.pending_id
        ? {
            pending_id: p.pending_id,
            name: p.name || "edit_file",
            args: p.args || {},
          }
        : null;
    if (this.pending) this.status = t().statusAwaitApproval;
  }

  async newSession(): Promise<void> {
    if (!this.handbookId) {
      new Notice(t().noticeRegisterFirst);
      return;
    }
    if (!window.confirm(t().confirmNewSession)) return;
    try {
      const sess = await this.api().createSession(this.handbookId);
      this.adopt(sess);
      this.quote = "";
      this.startLine = 1;
      this.endLine = 1;
      this.plugin.clearPick();
      if (this.capturedPath) {
        await this.plugin.bindNote(this.capturedPath, {
          handbook_id: this.handbookId,
          session_id: sess.session_id,
        });
      }
    } catch (e) {
      this.err = e instanceof Error ? e.message : String(e);
    }
    this.paintBar();
    await this.paintLog();
  }

  private async send(chip: string, userText: string): Promise<void> {
    if (chip === "search") return;
    if (this.pending) {
      new Notice(t().noticeResolveApproval);
      return;
    }
    if (!this.sessionId || !this.quote) {
      new Notice(t().noticeUseSelectionFirst);
      return;
    }
    // 正要问的这条深题，当场从列表里摘掉。**不能等服务端回话**：
    // 收尾时 keepDeep 只认 kind==="deep"，会把刚点过的那条原样留下，
    // 于是它一直挂在那儿，读者对着一个自己刚问过的问题看——跟复读机没区别。
    // 读者点下去的那一刻它就该消失，那是本地就知道的事实。
    this.dyn = dropAsked(this.dyn, userText);
    this.busy = true;
    this.err = "";
    this.usage = null;
    // 本轮从零数起。**this.spend 不清**——那是会话累计，清了第三格就每轮归零。
    this.turnTokens = 0;
    this.status = phaseText("thinking", "");
    const shown =
      userText.trim() ||
      chipLabel(chip, this.chips.find((c) => c.id === chip)?.label ?? "") ||
      chip;
    this.msgs = [...this.msgs, { role: "user", text: shown }, { role: "assistant", text: "" }];
    this.paintBar();
    await this.paintLog("force");
    let acc = "";
    try {
      await streamChat(
        this.plugin.settings.sidecarUrl,
        {
          session_id: this.sessionId,
          selected_text: this.quote,
          start_line: this.startLine,
          end_line: this.endLine,
          chip,
          user_text: userText,
          deep: this.plugin.settings.deepQuestions !== false,
        },
        (ev) => {
          if (ev.type === "status") {
            this.status = phaseText(String(ev.phase || ""), String(ev.text || ""));
            this.setStatus();
          } else if (ev.type === "think") {
            this.thinkChars = Number(ev.chars) || 0;
            this.setStatus();
          } else if (ev.type === "token") {
            // 正文开始了，推理阶段结束——计数让位给「在写…」
            this.thinkChars = 0;
            this.status = phaseText("writing", "");
            acc += String(ev.text || "");
            const last = this.msgs[this.msgs.length - 1];
            if (last?.role === "assistant") last.text = acc;
            this.paintStreamBubble(acc);
            // 后端每 48 字符发一个 token 事件。这里绝不能调 paintBar()——
            // 那会把整条底座重建几十次。只有状态行那一个文本节点需要动。
            this.setStatus();
          } else if (ev.type === "tool") {
            this.status = phaseText("reading", "");
            const ok = Boolean(ev.ok);
            const path = String(ev.resolved || ev.detail || "");
            const name = String(ev.name || "tool");
            this.msgs.splice(this.msgs.length - 1, 0, {
              role: "tool",
              ok,
              text: `${name} ${ok ? t().toolOk : t().toolDenied} → ${path}`,
            });
            if (name === "edit_file" && ok) void this.refreshSnapshots();
            void this.paintLog();
            this.paintBar();
          } else if (ev.type === "approval") {
            const args = (ev.args || {}) as {
              path?: string;
              old_string?: string;
              new_string?: string;
            };
            this.pending = {
              pending_id: String(ev.pending_id || ""),
              name: String(ev.name || "edit_file"),
              args,
            };
            this.status = t().statusAwaitApproval;
            this.paintBar();
          } else if (ev.type === "spend") {
            this.takeSpend(ev);
          } else if (ev.type === "done") {
            this.takeDone(ev);
          } else if (ev.type === "error") {
            this.status = "";
            this.err = String(ev.message);
          }
        },
        this.plugin.settings,
      );
    } catch (e) {
      this.err = e instanceof Error ? e.message : String(e);
    } finally {
      if (!this.pending) {
        this.busy = false;
        this.status = "";
      }
      this.paintBar();
      await this.paintLog();
    }
  }

  private async doApprove(allow: boolean): Promise<void> {
    if (!this.sessionId || !this.pending || this.approving) return;
    const sid = this.sessionId;
    const pid = this.pending.pending_id;
    this.approving = true;
    this.busy = true;
    this.err = "";
    this.status = allow ? t().statusEditing : t().statusDeclined;
    this.paintBar();
    if (allow && this.capturedPath) {
      try {
        await this.saveOpenNote(this.capturedPath);
      } catch {
        /* 仍尝试写盘：sidecar 读的是磁盘 */
      }
    }
    let acc = "";
    const last = this.msgs[this.msgs.length - 1];
    if (last?.role === "assistant") acc = last.text;
    try {
      await streamApprove(
        this.plugin.settings.sidecarUrl,
        { session_id: sid, pending_id: pid, allow },
        (ev) => {
          if (ev.type === "status") {
            this.status = phaseText(String(ev.phase || ""), String(ev.text || ""));
            this.setStatus();
          } else if (ev.type === "think") {
            this.thinkChars = Number(ev.chars) || 0;
            this.setStatus();
          } else if (ev.type === "token") {
            this.thinkChars = 0;
            acc += String(ev.text || "");
            const row = this.msgs[this.msgs.length - 1];
            if (row?.role === "assistant") row.text = acc;
            this.paintStreamBubble(acc);
          } else if (ev.type === "tool") {
            const ok = Boolean(ev.ok);
            const path = String(ev.resolved || ev.detail || "");
            const name = String(ev.name || "tool");
            this.msgs.splice(this.msgs.length - 1, 0, {
              role: "tool",
              ok,
              text: `${name} ${ok ? t().toolOk : t().toolDenied} → ${path}`,
            });
            if (name === "edit_file" && ok) {
              void this.refreshSnapshots();
              const line = Number(ev.line) || this.startLine;
              if (this.capturedPath) void this.revealInsert(this.capturedPath, line);
            }
            void this.paintLog();
          } else if (ev.type === "approval") {
            const args = (ev.args || {}) as {
              path?: string;
              old_string?: string;
              new_string?: string;
            };
            this.pending = {
              pending_id: String(ev.pending_id || ""),
              name: String(ev.name || "edit_file"),
              args,
            };
            // 不更新状态行的话，它会一直停在「在改原文…」，而下面的面板
            // 却写着「审批这次编辑」，两句话互相打架。
            this.status = t().statusAwaitApproval;
            this.paintBar();
          } else if (ev.type === "spend") {
            this.takeSpend(ev);
          } else if (ev.type === "done") {
            this.pending = null;
            this.takeDone(ev);
          } else if (ev.type === "error") {
            this.err = String(ev.message);
          }
        },
        this.plugin.settings,
      );
    } catch (e) {
      this.err = e instanceof Error ? e.message : String(e);
    } finally {
      this.approving = false;
      if (!this.pending) {
        this.busy = false;
        this.status = "";
      }
      this.paintBar();
      await this.paintLog("force");
    }
  }

  private async saveOpenNote(rel: string): Promise<void> {
    for (const leaf of this.app.workspace.getLeavesOfType("markdown")) {
      const v = leaf.view;
      if (v instanceof MarkdownView && v.file?.path === rel) {
        await v.save();
      }
    }
  }

  private async revealInsert(rel: string, line1: number): Promise<void> {
    const file = this.app.vault.getAbstractFileByPath(rel);
    if (!(file instanceof TFile)) return;
    const leaf =
      this.app.workspace.getLeavesOfType("markdown").find((l) => {
        const v = l.view;
        return v instanceof MarkdownView && v.file?.path === rel;
      }) ?? this.app.workspace.getLeaf(false);
    await leaf.openFile(file);
    const view = leaf.view;
    if (!(view instanceof MarkdownView)) return;
    const line = Math.max(0, line1 - 1);
    const pos = { line, ch: 0 };
    view.editor.setCursor(pos);
    view.editor.scrollIntoView({ from: pos, to: pos }, true);
  }

  private applySnapshotStatus(st: {
    undo_n?: number;
    redo_n?: number;
  }): void {
    this.undoN = Number(st.undo_n) || 0;
    this.redoN = Number(st.redo_n) || 0;
  }

  private async refreshSnapshots(): Promise<void> {
    if (!this.handbookId) {
      this.undoN = 0;
      this.redoN = 0;
      return;
    }
    try {
      this.applySnapshotStatus(await this.api().snapshots(this.handbookId));
    } catch {
      /* 侧栏仍画出按钮，只是保持当前计数 */
    }
    this.paintBar();
  }

  private async reloadCapturedNote(): Promise<void> {
    const rel = this.capturedPath;
    if (!rel) return;
    const file = this.app.vault.getAbstractFileByPath(rel);
    if (file instanceof TFile) {
      const leaf = this.app.workspace.getLeavesOfType("markdown").find((l) => {
        const v = l.view;
        return v instanceof MarkdownView && v.file?.path === rel;
      });
      if (leaf) await leaf.openFile(file);
    }
  }

  private async doRollback(): Promise<void> {
    if (this.busy || !this.handbookId || this.undoN <= 0) return;
    if (
      !window.confirm(
        t().confirmRollback,
      )
    ) {
      return;
    }
    const rel = this.capturedPath;
    this.busy = true;
    this.err = "";
    this.status = t().statusRollingBack;
    this.paintBar();
    try {
      if (rel) await this.saveOpenNote(rel);
      const st = await this.api().rollback(this.handbookId);
      this.applySnapshotStatus(st);
      this.msgs = [
        ...this.msgs,
        { role: "assistant", text: t().msgRolledBack },
      ];
      await this.reloadCapturedNote();
      new Notice(t().noticeRolledBack);
    } catch (e) {
      this.err = e instanceof Error ? e.message : String(e);
    } finally {
      this.busy = false;
      this.status = "";
      this.paintBar();
      await this.paintLog("force");
    }
  }

  private async doRedo(): Promise<void> {
    if (this.busy || !this.handbookId || this.redoN <= 0) return;
    const rel = this.capturedPath;
    this.busy = true;
    this.err = "";
    this.status = t().statusRedoing;
    this.paintBar();
    try {
      if (rel) await this.saveOpenNote(rel);
      const st = await this.api().redo(this.handbookId);
      this.applySnapshotStatus(st);
      this.msgs = [...this.msgs, { role: "assistant", text: t().msgRedone }];
      await this.reloadCapturedNote();
      new Notice(t().noticeRedone);
    } catch (e) {
      this.err = e instanceof Error ? e.message : String(e);
    } finally {
      this.busy = false;
      this.status = "";
      this.paintBar();
      await this.paintLog("force");
    }
  }
}
