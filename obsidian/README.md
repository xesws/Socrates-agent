# Socrates Pen（Obsidian 客户端）

薄插件。脑子在仓库根的 `python -m pen`。

模型、钥匙、节点都在 **设置 → Socrates Pen** 里填，不要去配环境变量。

## 当天怎么验

1. 用编辑器打开 `../socrates-pen.code-workspace`
2. Obsidian → Open folder as vault → `/Users/tangyiq/dev/socrates-pen-vault`
3. 关掉 Restricted mode，启用 **Socrates Pen** 和 **Hot Reload**
4. 仓库根：`python -m pen --host 127.0.0.1 --port 8765`
5. 这里：`npm run dev`（产物直接写进测试库插件目录）
6. **设置 → Socrates Pen**：填 API Key；需要换节点就改 Base URL / 模型名 / Thinking
7. 打开手册笔记，框选一段，命令面板「点读笔：用当前选区提问」

当前库的绝对路径会在框选时自动带给 sidecar，不用设 `PEN_ALLOW_ROOTS`。

不要 apply 写回。API Key 存在本库 `data.json`：若整个库进了 Sync / git，钥匙会跟着走。
