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
};

export type Proposal = {
  proposal_id: string;
  original_path: string;
  mode: string;
  level: string;
  q_title: string | null;
  beat?: string | null;
  instance_n: number;
  insert_after_line: number;
  replace_start?: number | null;
  replace_end?: number | null;
  fold_md: string;
  diff: string;
  where?: string;
};

export type NoteOutline = {
  n_lines: number;
  headings: {
    level: number;
    text: string;
    start_line: number;
    end_line: number;
  }[];
  questions: {
    text: string;
    start_line: number;
    end_line: number;
    insert_after_line: number;
  }[];
};

export type RetargetKind =
  | "auto"
  | "caret"
  | "after_line"
  | "after_heading"
  | "after_q"
  | "replace_heading"
  | "replace_range";

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
