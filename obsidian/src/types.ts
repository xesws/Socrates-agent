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
  fold_md: string;
  diff: string;
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
