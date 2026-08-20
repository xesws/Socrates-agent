import {
  ItemView,
  MarkdownRenderer,
  MarkdownView,
  Notice,
  TFile,
  WorkspaceLeaf,
} from "obsidian";
import type SocratesPenPlugin from "../main";
import { makeApi, streamApprove, streamChat } from "../api";
import { handbookIdFromPath, vaultRoot, type EditorPick } from "../selection";
import type { ChatMessage, Chip, SessionView } from "../types";

export const VIEW_TYPE_PEN = "socrates-pen-view";

function visibleReply(text: string): string {
  return text.replace(/<!--pen:chips[\s\S]*?-->/g, "").trim();
}

function toolCaption(m: ChatMessage): { ok: boolean; file: string; kicker: string } {
  const ok = m.ok !== false;
  const name = m.text.split(" ")[0] || "tool";
  const path = m.text.split("→").slice(1).join("→").trim();
  const file = path.split("/").filter(Boolean).pop() || (path || "（无路径）");
  const kicker = name === "edit_file" ? "改原文" : "翻手册";
  return { ok, file, kicker };
}

export class PenView extends ItemView {
  plugin: SocratesPenPlugin;
  private status = "";
  private usage = "";
  private err = "";
  private health = "sidecar 未探测";
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
  private pending: {
    pending_id: string;
    name: string;
    args: { path?: string; old_string?: string; new_string?: string };
  } | null = null;
  private approving = false;
  private logEl: HTMLElement | null = null;
  private barEl: HTMLElement | null = null;

  constructor(leaf: WorkspaceLeaf, plugin: SocratesPenPlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType(): string {
    return VIEW_TYPE_PEN;
  }

  getDisplayText(): string {
    return "点读笔";
  }

  getIcon(): string {
    return "highlighter";
  }

  async onOpen(): Promise<void> {
    this.renderShell();
    await this.probeHealth();
  }

  async onClose(): Promise<void> {
    this.logEl = null;
    this.barEl = null;
  }

  private api() {
    return makeApi(this.plugin.settings.sidecarUrl);
  }

  private renderShell(): void {
    const root = this.contentEl;
    root.empty();
    root.addClass("socrates-pen");
    this.barEl = root.createDiv({ cls: "sp-bar" });
    this.logEl = root.createDiv({ cls: "sp-log" });
    this.paintBar();
    this.paintLog();
  }

  private paintBar(): void {
    if (!this.barEl) return;
    this.barEl.empty();
    this.barEl.createDiv({ cls: "sp-health", text: this.health });
    if (this.err) this.barEl.createDiv({ cls: "sp-err", text: this.err });
    if (this.busy && this.status) {
      this.barEl.createDiv({ cls: "sp-status", text: this.status });
    }
    if (!this.busy && this.usage) {
      this.barEl.createDiv({ cls: "sp-usage", text: this.usage });
    }
    if (this.quote) {
      this.barEl.createDiv({ cls: "sp-quote", text: this.quote.slice(0, 180) });
    }
    const row = this.barEl.createDiv({ cls: "sp-actions" });
    this.bindKeepFocus(row.createEl("button", { text: "用当前选区" }), () => {
      void this.captureSelection();
    });
    const ns = row.createEl("button", { text: "新开会话" });
    ns.disabled = this.busy || Boolean(this.pending);
    ns.onclick = () => void this.newSession();
    const undo = row.createEl(
      "button",
      { text: this.undoN > 0 ? `回到上一版（${this.undoN}）` : "回到上一版" },
    );
    undo.disabled = this.busy || this.undoN <= 0;
    undo.title =
      this.undoN <= 0
        ? "还没有可回退的版本。允许一次编辑后才会亮。"
        : "整篇笔记回到上一版";
    undo.onclick = () => void this.doRollback();
    const redo = row.createEl(
      "button",
      { text: this.redoN > 0 ? `重做（${this.redoN}）` : "重做" },
    );
    redo.disabled = this.busy || this.redoN <= 0;
    redo.title =
      this.redoN <= 0 ? "没有可重做的版本" : "把刚才撤销的写回回来";
    redo.onclick = () => void this.doRedo();
    const blocked = this.busy || Boolean(this.pending);
    const chips = this.barEl.createDiv({ cls: "sp-chips" });
    for (const c of this.chips) {
      const on = c.id === "writeback" ? this.substantive : c.enabled;
      const b = chips.createEl("button", { text: c.label });
      b.disabled = !on || blocked;
      if (c.hint) b.title = c.hint;
      b.onclick = () => void this.send(c.id, "");
    }
    for (const d of this.dyn) {
      const b = chips.createEl("button", { text: d, cls: "is-dyn" });
      b.disabled = blocked;
      b.onclick = () => void this.send("free", d);
    }
    const form = this.barEl.createDiv({ cls: "sp-form" });
    const input = form.createEl("input");
    input.placeholder = "自己问一句…";
    input.disabled = blocked;
    const ask = form.createEl("button", { text: "问" });
    ask.disabled = blocked;
    ask.onclick = () => {
      const t = input.value.trim();
      if (t) void this.send("free", t);
    };
    if (this.pending) {
      const box = this.barEl.createDiv({ cls: "sp-preview" });
      box.createEl("h4", { text: "审批这次编辑" });
      box.createDiv({
        cls: "sp-where",
        text: `${this.pending.name} → ${this.pending.args.path || "当前手册"}`,
      });
      box.createEl("pre", {
        cls: "sp-fold",
        text: `--- old ---\n${(this.pending.args.old_string || "").slice(0, 800)}\n\n+++ new ---\n${(this.pending.args.new_string || "").slice(0, 800)}`,
      });
      box.createDiv({
        cls: "sp-warn",
        text: "模型自己选要换的那一小段。点允许才会改这篇笔记。",
      });
      const actions = box.createDiv({ cls: "sp-preview-actions" });
      const yes = actions.createEl("button", { text: "允许这次编辑" });
      yes.disabled = this.approving;
      yes.onclick = () => void this.doApprove(true);
      const no = actions.createEl("button", { text: "拒绝" });
      no.disabled = this.approving;
      no.onclick = () => void this.doApprove(false);
    }
  }

  private async paintLog(): Promise<void> {
    const gen = ++this.paintGen;
    if (!this.logEl || this.painting) return; // 在画的循环看到新 gen 会重画到最新
    this.painting = true;
    try {
      let g = gen;
      for (;;) {
        const log = this.logEl;
        if (!log) return;
        log.empty();
        if (this.msgs.length === 0) {
          log.createEl("p", {
            cls: "sp-hint",
            text: "在笔记里划一段（实时预览或阅读模式都行），再点「用当前选区」。",
          });
          return;
        }
        for (const m of this.msgs) {
          if (g !== this.paintGen || !this.logEl) break;
          if (m.role === "tool") {
            const cap = toolCaption(m);
            const row = log.createDiv({
              cls: cap.ok ? "sp-tool" : "sp-tool is-bad",
            });
            row.createSpan({ cls: "sp-kicker", text: cap.kicker });
            row.createSpan({
              cls: "sp-tool-msg",
              text: `${cap.ok ? "成功" : "拒绝"} · ${cap.file}`,
            });
            continue;
          }
          if (m.role === "assistant" && !m.text && this.pending) continue;
          const turn = log.createDiv({ cls: `sp-turn is-${m.role}` });
          turn.createDiv({
            cls: "sp-kicker",
            text: m.role === "user" ? "你" : "点读笔",
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
        if (g === this.paintGen && this.logEl) {
          this.logEl.scrollTop = this.logEl.scrollHeight;
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
    if (!this.logEl || this.painting) return;
    let turn = this.logEl.lastElementChild as HTMLElement | null;
    while (turn && !turn.hasClass("is-assistant")) {
      turn = turn.previousElementSibling as HTMLElement | null;
    }
    if (!turn) {
      turn = this.logEl.createDiv({ cls: "sp-turn is-assistant" });
      turn.createDiv({ cls: "sp-kicker", text: "点读笔" });
      turn.createDiv({ cls: "sp-body" });
    }
    const body =
      (turn.querySelector(".sp-body") as HTMLElement | null) ??
      turn.createDiv({ cls: "sp-body" });
    body.setText(visibleReply(text) || "…");
    this.logEl.scrollTop = this.logEl.scrollHeight;
  }

  async probeHealth(): Promise<void> {
    try {
      const h = await this.api().health();
      this.sidecarReachable = true;
      const fromPage = this.plugin.settings.apiKey.trim();
      const model = this.plugin.settings.model.trim() || h.llm.model;
      if (fromPage) {
        this.health = `sidecar 正常 · 设置页 · ${model}`;
      } else if (h.llm.ok) {
        this.health = `sidecar 正常 · 开发回退 ${h.llm.key_source} · ${model}`;
      } else {
        this.health = "sidecar 在，请到设置 → Socrates Pen 填写 API Key";
      }
      this.err = "";
    } catch (e) {
      this.sidecarReachable = false;
      this.health = "连不上 sidecar";
      this.err = `连不上 sidecar（CORS / 没启动 / 端口不对）：${e instanceof Error ? e.message : String(e)}`;
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
      new Notice("连不上 sidecar，先看面板上的错误信息");
      return;
    }
    if (!got) {
      this.err = "没读到选区。在笔记里划一段再点「用当前选区」。";
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
    await this.paintLog();
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
    if (this.pending) this.status = "等你批准这次编辑";
  }

  async newSession(): Promise<void> {
    if (!this.handbookId) {
      new Notice("先框选并登记当前笔记");
      return;
    }
    if (!window.confirm("新开会话会丢掉当前这场的模型记忆和选区。确定？")) return;
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
      new Notice("先批准或拒绝这次编辑");
      return;
    }
    if (!this.sessionId || !this.quote) {
      new Notice("先点「用当前选区」");
      return;
    }
    this.busy = true;
    this.err = "";
    this.usage = "";
    this.status = "师傅在想…";
    const shown =
      userText.trim() ||
      this.chips.find((c) => c.id === chip)?.label ||
      chip;
    this.msgs = [...this.msgs, { role: "user", text: shown }, { role: "assistant", text: "" }];
    this.paintBar();
    await this.paintLog();
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
            this.status = String(ev.text || "");
            this.paintBar();
          } else if (ev.type === "token") {
            this.status = "在写…";
            acc += String(ev.text || "");
            const last = this.msgs[this.msgs.length - 1];
            if (last?.role === "assistant") last.text = acc;
            this.paintStreamBubble(acc);
            this.paintBar();
          } else if (ev.type === "tool") {
            this.status = "在翻手册…";
            const ok = Boolean(ev.ok);
            const path = String(ev.resolved || ev.detail || "");
            const name = String(ev.name || "tool");
            this.msgs.splice(this.msgs.length - 1, 0, {
              role: "tool",
              ok,
              text: `${name} ${ok ? "成功" : "拒绝"} → ${path}`,
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
            this.status = "等你批准这次编辑";
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
            const fmt = (n: number | undefined) =>
              typeof n === "number" ? n.toLocaleString("zh-CN") : "?";
            this.usage = `上下文 ${fmt(ctx)} · 回复 ${fmt(out)}`;
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
    this.status = allow ? "在改原文…" : "已拒绝，让师傅收尾…";
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
            this.status = String(ev.text || "");
            this.paintBar();
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
              text: `${name} ${ok ? "成功" : "拒绝"} → ${path}`,
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
            const fmt = (n: number | undefined) =>
              typeof n === "number" ? n.toLocaleString("zh-CN") : "?";
            this.usage = `上下文 ${fmt(ctx)} · 回复 ${fmt(out)}`;
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
      await this.paintLog();
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
        "整篇笔记将回到上一版；这之后你手改的也会没。确定？",
      )
    ) {
      return;
    }
    const rel = this.capturedPath;
    this.busy = true;
    this.err = "";
    this.status = "在回到上一版…";
    this.paintBar();
    try {
      if (rel) await this.saveOpenNote(rel);
      const st = await this.api().rollback(this.handbookId);
      this.applySnapshotStatus(st);
      this.msgs = [
        ...this.msgs,
        { role: "assistant", text: "已回到上一版。" },
      ];
      await this.reloadCapturedNote();
      new Notice("已回到上一版");
    } catch (e) {
      this.err = e instanceof Error ? e.message : String(e);
    } finally {
      this.busy = false;
      this.status = "";
      this.paintBar();
      await this.paintLog();
    }
  }

  private async doRedo(): Promise<void> {
    if (this.busy || !this.handbookId || this.redoN <= 0) return;
    const rel = this.capturedPath;
    this.busy = true;
    this.err = "";
    this.status = "在重做…";
    this.paintBar();
    try {
      if (rel) await this.saveOpenNote(rel);
      const st = await this.api().redo(this.handbookId);
      this.applySnapshotStatus(st);
      this.msgs = [...this.msgs, { role: "assistant", text: "已重做刚才撤销的写入。" }];
      await this.reloadCapturedNote();
      new Notice("已重做");
    } catch (e) {
      this.err = e instanceof Error ? e.message : String(e);
    } finally {
      this.busy = false;
      this.status = "";
      this.paintBar();
      await this.paintLog();
    }
  }
}
