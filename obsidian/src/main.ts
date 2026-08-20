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

type PluginData = {
  settings?: Partial<PenSettings>;
  notes?: Record<string, NoteBinding>;
};

export default class SocratesPenPlugin extends Plugin {
  settings: PenSettings = { ...DEFAULT_SETTINGS };
  notes: Record<string, NoteBinding> = {};

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
    this.settings = { ...DEFAULT_SETTINGS, ...(raw.settings || {}) };
    this.settings.thinking = coerceThinking(this.settings.thinking);
    this.notes = raw.notes || {};
  }

  async saveSettings(): Promise<void> {
    await this.saveData({ settings: this.settings, notes: this.notes });
  }

  async pingOrNotice(): Promise<boolean> {
    try {
      await makeApi(this.settings.sidecarUrl).health();
      return true;
    } catch {
      new Notice("sidecar 未启动。在仓库根运行 python -m pen；模型在设置 → Socrates Pen 里填");
      return false;
    }
  }
}
