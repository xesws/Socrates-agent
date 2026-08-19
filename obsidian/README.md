# Socrates Pen（Obsidian 客户端）

薄插件。脑子在仓库根的 `python -m pen`。

## 当天怎么验

1. 用编辑器打开 `../socrates-pen.code-workspace`
2. Obsidian → Open folder as vault → `/Users/tangyiq/dev/socrates-pen-vault`
3. 关掉 Restricted mode，启用 **Socrates Pen** 和 **Hot Reload**
4. 仓库根：`python -m pen --host 127.0.0.1 --port 8765`
5. 这里：`npm run dev`（产物直接写进测试库插件目录）
6. 打开手册笔记，框选一段，命令面板「点读笔：用当前选区提问」

不要 apply 写回。钥匙只在 sidecar `.env`。
