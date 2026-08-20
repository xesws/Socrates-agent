/**
 * 简体中文词表——唯一允许写用户可见中文字面量的地方。
 *
 * 两条规约：
 *  1. **不要加 `as const`**。加了之后 `typeof zh` 全是字面量类型，
 *     将来的 `const en: Dict = {...}` 就永远无法满足（英文串 ≠ 中文字面量）。
 *  2. 静态文案写裸字符串，带参数的写箭头函数。函数式让 TS 原生检查参数的
 *     个数 / 顺序 / 类型，且中英文可以各自决定语序。
 *
 * `phases` 和 `chips` 的键是**后端下发的稳定 id**，不是随手起的名字：
 *   - `phases` ← pen/tutor.py 的 `{"type":"status","phase":...}`
 *   - `chips`  ← pen/session.py 的 `FIXED_CHIPS[].id`
 * 查表命中就本地化，没命中就照抄后端下发的文案（见 i18n/index.ts）。
 */

const NF = new Intl.NumberFormat("zh-CN");
const n = (v: number | undefined): string =>
  typeof v === "number" ? NF.format(v) : "?";

export const zh = {
  // ── 品牌 / 视图元数据 ──
  appName: "点读笔",
  viewTitle: "点读笔",

  // ── 命令 / ribbon（Obsidian 会自动加「Socrates Pen: 」前缀，这里别再写一遍）──
  ribbonTooltip: "打开点读笔",
  cmdAskSelection: "用当前选区提问",
  cmdOpenPanel: "打开面板",

  // ── 底座按钮 ──
  btnUseSelection: "用当前选区",
  btnAsk: "问",
  askPlaceholder: "自己问一句…",
  tipUseSelection: "在笔记里划一段，再点这里登记给师傅",

  // ── 品牌条工具按钮 ──
  btnNewSession: "新开会话",
  tipNewSession: "另起一场，丢掉这场的模型记忆和选区",
  btnUndo: "回到上一版",
  btnRedo: "重做",
  tipUndoEmpty: "还没有可回退的版本。允许一次编辑后才会亮。",
  tipUndo: (count: number): string => `整篇笔记回到上一版（还能退 ${count} 次）`,
  tipRedoEmpty: "没有可重做的版本",
  tipRedo: (count: number): string => `把刚才撤销的写回回来（还能重做 ${count} 次）`,

  // ── 健康行 ──
  healthUnprobed: "sidecar 未探测",
  healthOkSettings: (model: string): string => `sidecar 正常 · 设置页 · ${model}`,
  healthOkFallback: (source: string, model: string): string =>
    `sidecar 正常 · 开发回退 ${source} · ${model}`,
  healthNoKey: "sidecar 在，请到设置 → Socrates Pen 填写 API Key",
  healthDown: "连不上 sidecar",

  // ── 错误 ──
  errUnreachable: (detail: string): string =>
    `连不上 sidecar（CORS / 没启动 / 端口不对）：${detail}`,
  errNoSelection: "没读到选区。在笔记里划一段再点「用当前选区」。",

  // ── 用量 ──
  usage: (ctx: number | undefined, out: number | undefined): string =>
    `上下文 ${n(ctx)} · 回复 ${n(out)}`,

  // ── 对话气泡 ──
  kickerYou: "你",
  kickerPen: "点读笔",
  kickerReadTool: "翻手册",
  kickerEditTool: "改原文",
  toolOk: "成功",
  toolDenied: "拒绝",
  noPath: "（无路径）",
  streamPlaceholder: "…",
  emptyHint: "在笔记里划一段（实时预览或阅读模式都行），再点「用当前选区」。",

  // ── 启动 Logo ──
  splashTagline: "苏格拉底学习法",
  splashSubline: "Socrates-agent",

  // ── 状态行。键对齐 pen/tutor.py 的 ev.phase ──
  phases: {
    thinking: "师傅在想…",
    writing: "在写…",
    reading: "在翻手册…",
    tool: "在动手…",
  } as Record<string, string>,
  statusEditing: "在改原文…",
  statusDeclined: "已拒绝，让师傅收尾…",
  statusAwaitApproval: "等你批准这次编辑",
  statusRollingBack: "在回到上一版…",
  statusRedoing: "在重做…",

  // ── 审批面板 ──
  approvalTitle: "审批这次编辑",
  approvalTarget: (tool: string, path: string): string => `${tool} → ${path}`,
  approvalCurrentHandbook: "当前手册",
  approvalWarn: "模型自己选要换的那一小段。点允许才会改这篇笔记。",
  approvalOldLabel: "--- 原文 ---",
  approvalNewLabel: "+++ 换成 +++",
  btnApprove: "允许这次编辑",
  btnReject: "拒绝",

  // ── Notice ──
  noticeUnreachable: "连不上 sidecar，先看面板上的错误信息",
  noticeRegisterFirst: "先框选并登记当前笔记",
  noticeResolveApproval: "先批准或拒绝这次编辑",
  noticeUseSelectionFirst: "先点「用当前选区」",
  noticeRolledBack: "已回到上一版",
  noticeRedone: "已重做",

  // ── confirm ──
  confirmNewSession: "新开会话会丢掉当前这场的模型记忆和选区。确定？",
  confirmRollback: "整篇笔记将回到上一版；这之后你手改的也会没。确定？",

  // ── 写进对话流的系统消息 ──
  msgRolledBack: "已回到上一版。",
  msgRedone: "已重做刚才撤销的写入。",

  // ── main.ts ──
  errNoRightLeaf: "没有可用的右侧叶子",
  errViewNotMounted: "点读笔视图未挂上",
  errNeedDesktopVault: "需要桌面库（FileSystemAdapter）",
  noticeSidecarDown:
    "sidecar 未启动。先在本机终端运行：python -m pen；模型在设置 → Socrates Pen 里填",

  // ── 芯片。键对齐后端 pen/session.py 的 FIXED_CHIPS[].id ──
  chips: {
    socratic: { label: "先别揭晓，问我一个问题", hint: "" },
    explain_zero: { label: "当我零基础，讲清楚再给两个例子", hint: "" },
    examples: { label: "只举例子", hint: "" },
    search: { label: "查相关论文 / 算法出处", hint: "P2 才开放，现在不会假装搜过" },
    writeback: { label: "把刚才的解答写进手册原文", hint: "先有一轮实质解答" },
  } as Record<string, { label: string; hint: string }>,

  // ── 设置页 ──
  setLangName: "语言 / Language",
  setLangDesc: "默认跟随 Obsidian 的界面语言。",
  setLangAuto: "自动（跟随 Obsidian）",
  setIntro1:
    "在这一页填钥匙和节点。不要去配环境变量。当前库的路径会在框选时自动带给本机 sidecar。",
  setIntro2:
    "API Key 存在本库 .obsidian/plugins/socrates-pen/data.json。若整个库进了 Sync / iCloud / git，钥匙会跟着走。",
  setApiKeyDesc: "OpenAI 兼容节点的钥匙。密码框，不会显示在健康行里。",
  setBaseUrlDesc: "Chat Completions 兼容地址，不要带尾斜杠。",
  setModelName: "模型名",
  setModelDesc: "节点上的 model 字符串，例如 deepseek-v4-flash 或 gpt-4.1-mini。",
  setThinkingDesc: "off 最稳。只有推理模型才打开；不支持的节点开了可能 400。",
  setThinkingOff: "off（默认）",
  setSidecarDesc:
    "本机点读笔地址。一般不用改。先在本机终端运行：python -m pen --host 127.0.0.1 --port 8765",
};

export type Dict = typeof zh;
