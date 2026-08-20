export type Chip = {
  id: string;
  label: string;
  enabled: boolean;
  hint?: string;
};

export type HandbookMeta = {
  handbook_id: string;
  title: string;
  original_path: string;
  imported_at: string;
  mtime: number;
  n_lines?: number;
  toc?: { level: string; beat: string | null; start_line: number; heading: string }[];
};

export type PendingEdit = {
  pending_id: string;
  name: string;
  args: { path?: string; old_string?: string; new_string?: string };
};

export type SessionView = {
  session_id: string;
  handbook_id: string;
  chips: Chip[];
  has_substantive: boolean;
  last_anchor: {
    selected_text?: string;
    start_line?: number;
    end_line?: number;
    level?: string;
    q_title?: string | null;
    kind?: string;
    beat?: string | null;
  } | null;
  ui_messages: ChatMessage[];
  last_assistant?: string;
  pending?: PendingEdit | null;
};

export type ChatMessage = {
  role: "user" | "assistant" | "tool";
  text: string;
  ok?: boolean;
  /**
   * 点芯片发起时后端会带上芯片 id（sidecar >= v0.7.1）。
   * 有它就能把落盘的中文 label 换成当前语言，旧快照没有则回落 text。
   */
  chip?: string;
};

export type SnapshotStatus = {
  can_undo: boolean;
  can_redo: boolean;
  undo_n: number;
  redo_n: number;
};

export type LlmStatus = {
  ok: boolean;
  base_url: string;
  model: string;
  key_source: string;
  thinking?: string;
};

export type NoteBinding = {
  handbook_id: string;
  session_id: string;
};
