import { App, Plugin, PluginSettingTab, Setting } from "obsidian";
import { coerceLangPref, t, type LangPref } from "./i18n";

export type ThinkingLevel = "off" | "low" | "medium" | "high";

export interface PenSettings {
  /** 界面语言。"auto" 跟随 Obsidian。 */
  lang: LangPref;
  sidecarUrl: string;
  apiKey: string;
  baseUrl: string;
  model: string;
  thinking: ThinkingLevel;
  /** 后台深挖。关掉时前端不轮询，且请求带 deep:false 让后端也不起线程。 */
  deepQuestions: boolean;
}

export const DEFAULT_SETTINGS: PenSettings = {
  lang: "auto",
  sidecarUrl: "http://127.0.0.1:8765",
  apiKey: "",
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  thinking: "off",
  deepQuestions: true,
};

const THINKING: ThinkingLevel[] = ["off", "low", "medium", "high"];

export function coerceThinking(raw: unknown): ThinkingLevel {
  return THINKING.includes(raw as ThinkingLevel) ? (raw as ThinkingLevel) : "off";
}

export function llmPayload(s: PenSettings): {
  api_key?: string;
  base_url?: string;
  model?: string;
  thinking: ThinkingLevel;
} {
  const base = s.baseUrl.trim().replace(/\/+$/, "");
  return {
    ...(s.apiKey.trim() ? { api_key: s.apiKey.trim() } : {}),
    ...(base ? { base_url: base } : {}),
    ...(s.model.trim() ? { model: s.model.trim() } : {}),
    thinking: coerceThinking(s.thinking),
  };
}

type PenHost = Plugin & {
  settings: PenSettings;
  saveSettingsSoon: () => void;
  /** 语言改了之后重刷 ribbon tooltip、命令名、已打开的侧栏。 */
  applyLanguage: () => void;
};

export class PenSettingTab extends PluginSettingTab {
  plugin: PenHost;

  constructor(app: App, plugin: PenHost) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    const s = t();
    containerEl.empty();
    // 标题用 setHeading 而不是裸 h2：Obsidian 现行插件规范。
    // 也不重复插件名——设置侧栏已经写着 Socrates Pen 了。
    containerEl.createEl("p", { cls: "setting-item-description", text: s.setIntro1 });
    containerEl.createEl("p", { cls: "setting-item-description", text: s.setIntro2 });

    new Setting(containerEl)
      .setName(s.setLangName) // 两张表都写成双语，切错了还找得回来
      .setDesc(s.setLangDesc)
      .addDropdown((d) => {
        d.addOption("auto", s.setLangAuto)
          .addOption("zh", "中文") // 语言选项按惯例各用本语言书写，不翻译
          .addOption("en", "English")
          .setValue(coerceLangPref(this.plugin.settings.lang))
          .onChange((v) => {
            this.plugin.settings.lang = coerceLangPref(v);
            this.plugin.saveSettingsSoon();
            this.plugin.applyLanguage();
            this.display(); // 原地重画，设置页自己也要跟着变
          });
      });

    new Setting(containerEl)
      .setName("API Key")
      .setDesc(s.setApiKeyDesc)
      .addText((c) => {
        c.inputEl.type = "password";
        c.inputEl.autocomplete = "off";
        c.setPlaceholder("sk-…")
          .setValue(this.plugin.settings.apiKey)
          .onChange((v) => {
            this.plugin.settings.apiKey = v.trim();
            this.plugin.saveSettingsSoon();
          });
      });

    new Setting(containerEl)
      .setName("Base URL")
      .setDesc(s.setBaseUrlDesc)
      .addText((c) =>
        c
          .setPlaceholder("https://api.deepseek.com")
          .setValue(this.plugin.settings.baseUrl)
          .onChange((v) => {
            this.plugin.settings.baseUrl = (v.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.baseUrl);
            this.plugin.saveSettingsSoon();
          }),
      );

    new Setting(containerEl)
      .setName(s.setModelName)
      .setDesc(s.setModelDesc)
      .addText((c) =>
        c
          .setPlaceholder("deepseek-v4-flash")
          .setValue(this.plugin.settings.model)
          .onChange((v) => {
            this.plugin.settings.model = v.trim() || DEFAULT_SETTINGS.model;
            this.plugin.saveSettingsSoon();
          }),
      );

    new Setting(containerEl)
      .setName("Thinking")
      .setDesc(s.setThinkingDesc)
      .addDropdown((d) => {
        d.addOption("off", s.setThinkingOff)
          .addOption("low", "low")
          .addOption("medium", "medium")
          .addOption("high", "high")
          .setValue(coerceThinking(this.plugin.settings.thinking))
          .onChange((v) => {
            this.plugin.settings.thinking = coerceThinking(v);
            this.plugin.saveSettingsSoon();
          });
      });

    new Setting(containerEl)
      .setName(s.setDeepName)
      .setDesc(s.setDeepDesc)
      .addToggle((c) =>
        c.setValue(this.plugin.settings.deepQuestions !== false).onChange((v) => {
          this.plugin.settings.deepQuestions = v;
          this.plugin.saveSettingsSoon();
        }),
      );

    new Setting(containerEl)
      .setName("Sidecar URL")
      .setDesc(s.setSidecarDesc)
      .addText((c) =>
        c
          .setPlaceholder("http://127.0.0.1:8765")
          .setValue(this.plugin.settings.sidecarUrl)
          .onChange((v) => {
            this.plugin.settings.sidecarUrl = v.trim() || DEFAULT_SETTINGS.sidecarUrl;
            this.plugin.saveSettingsSoon();
          }),
      );
  }
}
