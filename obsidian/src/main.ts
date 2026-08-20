import { Notice, Plugin } from "obsidian";
import { makeApi } from "./api";
import {
  coerceThinking,
  DEFAULT_SETTINGS,
  PenSettingTab,
  type PenSettings,
} from "./settings";
import type { NoteBinding } from "./types";
import { PenView, VIEW_TYPE_PEN } from "./views/PenView";

// 旧版插件把 PenSettings 键直接写在 data.json 顶层，故顶层也要容忍这些键
type PluginData = Partial<PenSettings> & {
  settings?: Partial<PenSettings>;
  notes?: Record<string, NoteBinding>;
};

export default class SocratesPenPlugin extends Plugin {
  settings: PenSettings = { ...DEFAULT_SETTINGS };
  notes: Record<string, NoteBinding> = {};
  private saveTimer: number | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.registerView(VIEW_TYPE_PEN, (leaf) => new PenView(leaf, this));
    this.addSettingTab(new PenSettingTab(this.app, this));
    this.addRibbonIcon("highlighter", "打开点读笔", () => {
      void this.activateView();
    });
    this.addCommand({
      id: "socrates-pen-ask-selection",
      name: "点读笔：用当前选区提问",
      callback: () => {
        void this.activateView().then(async (view) => {
          await view.captureSelection();
        });
      },
    });
    this.addCommand({
      id: "socrates-pen-open",
      name: "点读笔：打开面板",
      callback: () => {
        void this.activateView();
      },
    });
  }

  onunload(): void {
    /* views unregistered by host */
    if (this.saveTimer !== null) {
      window.clearTimeout(this.saveTimer);
      this.saveTimer = null;
      void this.saveSettings();
    }
  }

  async activateView(): Promise<PenView> {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE_PEN);
    const leaf = existing[0] ?? this.app.workspace.getRightLeaf(false);
    if (!leaf) throw new Error("没有可用的右侧叶子");
    await leaf.setViewState({ type: VIEW_TYPE_PEN, active: true });
    this.app.workspace.revealLeaf(leaf);
    const view = leaf.view;
    if (!(view instanceof PenView)) throw new Error("点读笔视图未挂上");
    return view;
  }

  noteBind(path: string): NoteBinding | undefined {
    return this.notes[path];
  }

  async bindNote(path: string, bind: NoteBinding): Promise<void> {
    this.notes[path] = bind;
    await this.saveSettings();
  }

  async loadSettings(): Promise<void> {
    const raw = ((await this.loadData()) || {}) as PluginData;
    // 旧版顶层键收进来当后备；嵌套 settings 里已给的键以嵌套为准
    const legacy: Partial<PenSettings> = {};
    if (raw.sidecarUrl !== undefined) legacy.sidecarUrl = raw.sidecarUrl;
    if (raw.apiKey !== undefined) legacy.apiKey = raw.apiKey;
    if (raw.baseUrl !== undefined) legacy.baseUrl = raw.baseUrl;
    if (raw.model !== undefined) legacy.model = raw.model;
    if (raw.thinking !== undefined) legacy.thinking = raw.thinking;
    this.settings = { ...DEFAULT_SETTINGS, ...legacy, ...(raw.settings || {}) };
    this.settings.thinking = coerceThinking(this.settings.thinking);
    this.notes = raw.notes || {};
  }

  async saveSettings(): Promise<void> {
    await this.saveData({ settings: this.settings, notes: this.notes });
  }

  saveSettingsSoon(): void {
    if (this.saveTimer !== null) window.clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => {
      this.saveTimer = null;
      void this.saveSettings();
    }, 350);
  }

  async pingOrNotice(): Promise<boolean> {
    try {
      await makeApi(this.settings.sidecarUrl).health();
      return true;
    } catch {
      new Notice("sidecar 未启动。先在本机终端运行：python -m pen；模型在设置 → Socrates Pen 里填");
      return false;
    }
  }
}
