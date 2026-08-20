import { App, Plugin, PluginSettingTab, Setting } from "obsidian";

export type ThinkingLevel = "off" | "low" | "medium" | "high";

export interface PenSettings {
  sidecarUrl: string;
  apiKey: string;
  baseUrl: string;
  model: string;
  thinking: ThinkingLevel;
}

export const DEFAULT_SETTINGS: PenSettings = {
  sidecarUrl: "http://127.0.0.1:8765",
  apiKey: "",
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  thinking: "off",
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
  return {
    ...(s.apiKey.trim() ? { api_key: s.apiKey.trim() } : {}),
    ...(s.baseUrl.trim() ? { base_url: s.baseUrl.trim() } : {}),
    ...(s.model.trim() ? { model: s.model.trim() } : {}),
    thinking: coerceThinking(s.thinking),
  };
}

type PenHost = Plugin & {
  settings: PenSettings;
  saveSettings: () => Promise<void>;
};

export class PenSettingTab extends PluginSettingTab {
  plugin: PenHost;

  constructor(app: App, plugin: PenHost) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Socrates Pen" });
    containerEl.createEl("p", {
      text: "在这一页填钥匙和节点。不要去配环境变量。当前库的路径会在框选时自动带给本机 sidecar。",
    });
    containerEl.createEl("p", {
      text: "API Key 存在本库 .obsidian/plugins/socrates-pen/data.json。若整个库进了 Sync / iCloud / git，钥匙会跟着走。",
    });

    new Setting(containerEl)
      .setName("API Key")
      .setDesc("OpenAI 兼容节点的钥匙。密码框，不会显示在健康行里。")
      .addText((t) => {
        t.inputEl.type = "password";
        t.inputEl.autocomplete = "off";
        t.setPlaceholder("sk-…")
          .setValue(this.plugin.settings.apiKey)
          .onChange(async (v) => {
            this.plugin.settings.apiKey = v.trim();
            await this.plugin.saveSettings();
          });
      });

    new Setting(containerEl)
      .setName("Base URL")
      .setDesc("Chat Completions 兼容地址，不要带尾斜杠。")
      .addText((t) =>
        t
          .setPlaceholder("https://api.deepseek.com")
          .setValue(this.plugin.settings.baseUrl)
          .onChange(async (v) => {
            this.plugin.settings.baseUrl = v.trim() || DEFAULT_SETTINGS.baseUrl;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("模型名")
      .setDesc("节点上的 model 字符串，例如 deepseek-v4-flash 或 gpt-4.1-mini。")
      .addText((t) =>
        t
          .setPlaceholder("deepseek-v4-flash")
          .setValue(this.plugin.settings.model)
          .onChange(async (v) => {
            this.plugin.settings.model = v.trim() || DEFAULT_SETTINGS.model;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Thinking")
      .setDesc("off 最稳。只有推理模型才打开；不支持的节点开了可能 400。")
      .addDropdown((d) => {
        d.addOption("off", "off（默认）")
          .addOption("low", "low")
          .addOption("medium", "medium")
          .addOption("high", "high")
          .setValue(coerceThinking(this.plugin.settings.thinking))
          .onChange(async (v) => {
            this.plugin.settings.thinking = coerceThinking(v);
            await this.plugin.saveSettings();
          });
      });

    new Setting(containerEl)
      .setName("Sidecar URL")
      .setDesc("本机笔的地址。一般不用改。先在仓库根跑：python -m pen --host 127.0.0.1 --port 8765")
      .addText((t) =>
        t
          .setPlaceholder("http://127.0.0.1:8765")
          .setValue(this.plugin.settings.sidecarUrl)
          .onChange(async (v) => {
            this.plugin.settings.sidecarUrl = v.trim() || DEFAULT_SETTINGS.sidecarUrl;
            await this.plugin.saveSettings();
          }),
      );
  }
}
