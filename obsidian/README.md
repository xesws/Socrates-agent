# Socrates Pen（Obsidian 客户端）

薄插件。脑子在本机的 `python -m pen`。

模型、钥匙、节点都在 **设置 → Socrates Pen** 里填，不要去配环境变量。

界面支持中文 / English，默认跟随 Obsidian 的界面语言，也可以在设置页单独指定。切成英文时 AI 也会用英文回答——但那是**新开会话**才生效，system prompt 在建会话那一刻就定下来并落盘了。

## 第一次用

1. 用 Obsidian 打开你的库
2. 关掉 Restricted mode，启用 **Socrates Pen**
3. 本机终端运行：`python -m pen --host 127.0.0.1 --port 8765`
4. **设置 → Socrates Pen**：填 API Key；需要换节点就改 Base URL / 模型名 / Thinking
5. 打开一篇笔记，划一段（实时预览或阅读模式都行），点侧栏「用当前选区」或命令面板「Socrates Pen: 用当前选区提问」
6. 要把解答写进原文：跟师傅说清楚插哪/换哪，或点「把刚才的解答写进手册原文」。模型必须先 `read_file`（带行号）再单独 `edit_file`，侧栏弹出审批，点「允许这次编辑」才写盘。侧栏「回到上一版 / 重做」按快照栈整篇回退。

当前库的绝对路径会在框选时自动带给 sidecar，不用设 `PEN_ALLOW_ROOTS`。

API Key 存在本库 `data.json`：若整个库进了 Sync / git，钥匙会跟着走。

## 开发者验收

1. 用编辑器打开 `../socrates-pen.code-workspace`
2. Obsidian → Open folder as vault → 我们的测试库 `/Users/tangyiq/dev/socrates-pen-vault`
3. 启用 **Socrates Pen** 和 **Hot Reload**
4. 本机终端：`python -m pen --host 127.0.0.1 --port 8765`
5. 本目录：`npm run dev`（产物直接写进测试库插件目录）
6. 按上面「第一次用」第 4–5 步走一遍
