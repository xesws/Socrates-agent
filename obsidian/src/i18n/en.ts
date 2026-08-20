import type { Dict } from "./zh";

const NF = new Intl.NumberFormat("en-US");
const n = (v: number | undefined): string =>
  typeof v === "number" ? NF.format(v) : "?";

/**
 * 这一行的 `: Dict` 就是全部的强制力：
 *   缺键   -> TS2741 Property 'x' is missing
 *   多键   -> TS2353 excess property
 *   参数错 -> TS2322 签名不匹配
 * 函数参数由 Dict 上下文推断，不用手写注解（noImplicitAny 也不会报）。
 *
 * 语气对齐中文表：师傅带实习生，口语，短句，不要客服腔。
 */
export const en: Dict = {
  appName: "Socrates Pen",
  viewTitle: "Socrates Pen",

  ribbonTooltip: "Open Socrates Pen",
  cmdAskSelection: "Ask about the current selection",
  cmdOpenPanel: "Open the panel",

  btnUseSelection: "Use selection",
  btnAsk: "Ask",
  askPlaceholder: "Ask something…",
  tipUseSelection: "Highlight a passage in your note, then hand it over here",

  tipNewSession: "Start over — drops this session's memory and selection",
  tipUndoEmpty: "Nothing to roll back yet. Approve an edit first.",
  tipUndo: (count) => `Roll the whole note back one version (${count} left)`,
  tipRedoEmpty: "Nothing to redo",
  tipRedo: (count) => `Put back what you just undid (${count} left)`,

  healthUnprobed: "sidecar not checked yet",
  healthOkSettings: (model) => `sidecar ok · from settings · ${model}`,
  healthOkFallback: (source, model) => `sidecar ok · dev fallback ${source} · ${model}`,
  healthNoKey: "sidecar is up — add your API key under Settings → Socrates Pen",
  healthDown: "can't reach sidecar",

  errUnreachable: (detail) =>
    `Can't reach the sidecar (CORS / not running / wrong port): ${detail}`,
  errNoSelection:
    "Didn't catch a selection. Highlight a passage in the note, then hit “Use selection”.",

  usage: (ctx, out) => `context ${n(ctx)} · reply ${n(out)}`,

  kickerYou: "You",
  kickerPen: "Pen",
  kickerReadTool: "reading",
  kickerEditTool: "editing",
  toolOk: "ok",
  toolDenied: "blocked",
  noPath: "(no path)",
  streamPlaceholder: "…",
  emptyHint:
    "Highlight a passage in a note — Live Preview or Reading view, either works — then hit “Use selection”.",

  splashTagline: "The Socratic Method",
  splashSubline: "Socrates-agent",

  phases: {
    thinking: "Thinking it over…",
    writing: "Writing…",
    reading: "Flipping through the manual…",
    tool: "Working on it…",
  },
  statusEditing: "Editing the note…",
  statusDeclined: "Declined — letting it wrap up…",
  statusAwaitApproval: "Waiting for you to approve this edit",
  statusRollingBack: "Rolling back…",
  statusRedoing: "Redoing…",

  approvalTitle: "Approve this edit",
  approvalTarget: (tool, path) => `${tool} → ${path}`,
  approvalCurrentHandbook: "current manual",
  approvalTruncated: (n) => `… ${n} more characters not shown`,
  approvalWarn:
    "The model picked this snippet itself. Nothing touches your note until you allow it.",
  approvalOldLabel: "--- before ---",
  approvalNewLabel: "+++ after +++",
  btnApprove: "Allow this edit",
  btnReject: "Reject",

  noticeUnreachable: "Can't reach the sidecar — check the error up in the panel",
  noticeRegisterFirst: "Pick a passage first so this note gets registered",
  noticeResolveApproval: "Allow or reject the pending edit first",
  noticeUseSelectionFirst: "Hit “Use selection” first",
  noticeRolledBack: "Rolled back one version",
  noticeRedone: "Redone",

  confirmNewSession:
    "A new session drops what the model remembers from this one, and the current selection. Go ahead?",
  confirmRollback:
    "The whole note goes back one version — anything you edited by hand after that goes too. Sure?",

  msgRolledBack: "Rolled back one version.",
  msgRedone: "Put back the edit you undid.",

  errNoRightLeaf: "No pane available in the right sidebar",
  errViewNotMounted: "The Socrates Pen view didn't mount",
  errNeedDesktopVault: "Needs a desktop vault (FileSystemAdapter)",
  noticeSidecarDown:
    "The sidecar isn't running. Start it in a terminal: python -m pen — the model goes under Settings → Socrates Pen",

  chips: {
    socratic: { label: "Don't tell me yet — ask me something", hint: "" },
    explain_zero: { label: "Assume I know nothing, then give me two examples", hint: "" },
    examples: { label: "Just show me examples", hint: "" },
    search: {
      label: "Find the paper / where this came from",
      hint: "Lands in P2. It won't pretend it searched.",
    },
    writeback: {
      label: "Write that answer back into the manual",
      hint: "Needs one real answer first",
    },
  },

  setLangName: "语言 / Language",
  setLangDesc: "Follows Obsidian's interface language by default.",
  setLangAuto: "Auto (follow Obsidian)",
  setIntro1:
    "Key and endpoint go here — no environment variables needed. This vault's path is handed to the local sidecar the moment you pick a passage.",
  setIntro2:
    "The API key lives in this vault, at .obsidian/plugins/socrates-pen/data.json. If the whole vault goes into Sync / iCloud / git, the key rides along.",
  setApiKeyDesc:
    "Key for your OpenAI-compatible endpoint. Masked here, never shown in the status line.",
  setBaseUrlDesc: "A Chat Completions-compatible address. No trailing slash.",
  setModelName: "Model",
  setModelDesc:
    "The model string your endpoint expects, e.g. deepseek-v4-flash or gpt-4.1-mini.",
  setThinkingDesc:
    "off is the safe bet. Turn it up only for reasoning models — endpoints that don't support it may hand you a 400.",
  setThinkingOff: "off (default)",
  deepQuotaSpent: "deep dives used up",
  setDeepName: "Dig deeper in the background",
  setDeepDesc: "After each answer, spend one more call looking for a question that reaches across chapters. It only appears if one turns up. Turn this off to keep just the two instant ones.",
  tipDeepPrefix: "\u25c6 ",
  setSidecarDesc:
    "Where the local pen listens. You rarely need to touch this. Start it with: python -m pen --host 127.0.0.1 --port 8765",
};
