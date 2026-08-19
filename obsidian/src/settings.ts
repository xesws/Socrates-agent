import { App, Plugin, PluginSettingTab, Setting } from "obsidian";

export interface PenSettings {
  sidecarUrl: string;
}

export const DEFAULT_SETTINGS: PenSettings = {
  sidecarUrl: "http://127.0.0.1:8765",
};

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
      text: "钥匙只放在 sidecar 的 .env 里。这里只填本机服务地址。先跑：python -m pen --host 127.0.0.1 --port 8765",
    });
    containerEl.createEl("p", {
      text: "sidecar 的 .env 必须写 PEN_ALLOW_ROOTS=/绝对路径/到/vault（macOS 上多个根用冒号分隔），否则 vault 不在 git 仓里的笔记 import 会 400。",
    });
    new Setting(containerEl)
      .setName("Sidecar URL")
      .setDesc("不要带尾斜杠。SSE 走这个地址的 /v1/chat。")
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
