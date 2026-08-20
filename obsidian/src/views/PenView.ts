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
import type { ChatMessage, Chip, PendingEdit, SessionView } from "../types";
import { phaseText, t } from "../i18n";

export const VIEW_TYPE_PEN = "socrates-pen-view";

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

function toolCaption(m: ChatMessage): { ok: boolean; file: string; kicker: string } {
  const ok = m.ok !== false;
  const name = m.text.split(" ")[0] || "tool";
  const path = m.text.split("\u2192").slice(1).join("\u2192").trim();
  const file = path.split("/").filter(Boolean).pop() || (path || t().noPath);
  const kicker = name === "edit_file" ? t().kickerEditTool : t().kickerReadTool;
  return { ok, file, kicker };
}

export class PenView extends ItemView {
  plugin: SocratesPenPlugin;
  private status = "";
  private usage = "";
  private err = "";
  private health = t().healthUnprobed;
  private msgs: ChatMessage[] = [];
  private chips: Chip[] = [];
  private dyn: string[] = [];
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
    this.renderShell();
    await this.probeHealth();
  }

  async onClose(): Promise<void> {
    this.els = null;
    this.chipsSig = "";
  }

  private api() {
    return makeApi(this.plugin.settings.sidecarUrl);
  }

  /**
   * 建骨架。**只在 onOpen 跑一次**——所有元素和事件监听的生命周期等于视图本身，
   * 之后的每次刷新都只改属性、不重建节点。
   *
   * 五层，自上而下：品牌条 / 错误条 / 对话区 / 审批面板 / 底座。
   * 只有对话区 grow，其余按 flex-shrink 权重依次让位，输入行永不压缩。
   */
  private renderShell(): void {
    const root = this.contentEl;
    root.empty();
    root.addClass("socrates-pen");

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
    const status = dock.createDiv({ cls: "sp-status is-off" });
    const form = dock.createDiv({ cls: "sp-form" });
    const pick = form.createEl("button", { cls: "sp-pick", text: t().btnUseSelection });
    const input = form.createEl("input", { cls: "sp-input" });
    const ask = form.createEl("button", { cls: "sp-send mod-cta", text: t().btnAsk });

    this.els = {
      dot, brandSub, alert, log, panel,
      quote, chips, status, input, ask, pick, fresh, undo, redo,
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
    const text = this.quote.slice(0, 180);
    e.quote.classList.toggle("is-off", !this.quote);
    if (e.quote.textContent !== text) e.quote.textContent = text;
  }

  /**
   * 状态行一格两用：忙的时候显示「师傅在想…」，闲下来显示用量。
   * 这样底座高度不会因为状态出现/消失而跳动。
   */
  private setStatus(): void {
    const e = this.els;
    if (!e) return;
    const line = this.busy ? this.status : this.usage;
    e.status.classList.toggle("is-off", !line);
    e.status.classList.toggle("is-usage", !this.busy);
    if (e.status.textContent !== line) e.status.textContent = line;
  }

  /** 只改 disabled，绝不碰 DOM 结构——这是流式期间唯一被高频调用的路径之一。 */
  private setBusy(): void {
    const e = this.els;
    if (!e) return;
    const blocked = this.busy || Boolean(this.pending);
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
      const b = e.chips.createEl("button", { text: c.label });
      b.dataset.off = on ? "0" : "1";
      if (c.hint) setTooltip(b, c.hint);
      b.onclick = () => void this.send(c.id, "");
    }
    for (const d of this.dyn) {
      const b = e.chips.createEl("button", { text: d, cls: "is-dyn" });
      b.dataset.off = "0";
      b.onclick = () => void this.send("free", d);
    }
    e.chips.classList.toggle("is-off", e.chips.childElementCount === 0);
    this.syncChipDisabled();
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
    const pre = e.panel.createEl("pre", { cls: "sp-fold" });
    pre.createDiv({
      cls: "sp-diff is-old",
      text: `${t().approvalOldLabel}\n${(p.args.old_string || "").slice(0, 800)}`,
    });
    pre.createDiv({
      cls: "sp-diff is-new",
      text: `${t().approvalNewLabel}\n${(p.args.new_string || "").slice(0, 800)}`,
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
          // 空态。v0.6.0 会把启动 Logo 放进这个分支。
          const empty = log.createDiv({ cls: "sp-empty" });
          empty.createEl("p", { cls: "sp-hint", text: t().emptyHint });
          return;
        }
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
          turn.createDiv({
            cls: "sp-kicker",
            text: m.role === "user" ? t().kickerYou : t().kickerPen,
          });
          const body = turn.createDiv({ cls: "sp-body" });
          if (m.role === "user") {
            body.setText(m.text);
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
          if (stick) done.scrollTop = done.scrollHeight;
          return;
        }
        g = this.paintGen; // 期间来了更新的请求，整条重画
      }
    } finally {
      this.painting = false;
    }
  }

  private paintStreamBubble(text: string): void {
    // 流式只刷最后一条助手正文；全量 markdown 重绘留给 done/finally
    const log = this.els?.log;
    if (!log || this.painting) return;
    let turn = log.lastElementChild as HTMLElement | null;
    while (turn && !turn.hasClass("is-assistant")) {
      turn = turn.previousElementSibling as HTMLElement | null;
    }
    if (!turn) {
      turn = log.createDiv({ cls: "sp-turn is-assistant" });
      turn.createDiv({ cls: "sp-kicker", text: t().kickerPen });
      turn.createDiv({ cls: "sp-body" });
    }
    const body =
      (turn.querySelector(".sp-body") as HTMLElement | null) ??
      turn.createDiv({ cls: "sp-body" });
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
    this.dyn = [];
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
    this.busy = true;
    this.err = "";
    this.usage = "";
    this.status = phaseText("thinking", "");
    const shown =
      userText.trim() ||
      this.chips.find((c) => c.id === chip)?.label ||
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
        },
        (ev) => {
          if (ev.type === "status") {
            this.status = phaseText(String(ev.phase || ""), String(ev.text || ""));
            this.setStatus();
          } else if (ev.type === "token") {
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
          } else if (ev.type === "done") {
            this.status = "";
            const u = ev.usage as {
              context_tokens?: number;
              prompt_tokens?: number;
              completion_tokens?: number;
            };
            const ctx = u?.context_tokens ?? u?.prompt_tokens;
            const out = u?.completion_tokens;
            this.usage = t().usage(ctx, out);
            this.dyn = (ev.dynamic_chips as string[]) || [];
            this.substantive = Boolean(ev.has_substantive);
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
          } else if (ev.type === "token") {
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
          } else if (ev.type === "done") {
            this.pending = null;
            this.status = "";
            const u = ev.usage as {
              context_tokens?: number;
              completion_tokens?: number;
              prompt_tokens?: number;
            };
            const ctx = u?.context_tokens ?? u?.prompt_tokens;
            const out = u?.completion_tokens;
            this.usage = t().usage(ctx, out);
            this.dyn = (ev.dynamic_chips as string[]) || [];
            this.substantive = Boolean(ev.has_substantive);
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
