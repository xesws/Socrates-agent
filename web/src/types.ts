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
  toc?: TocEntry[];
};

export type TocEntry = {
  level: string;
  beat: string | null;
  start_line: number;
  heading: string;
  anchor_id: string;
};

export type Section = {
  kind: string;
  level: string;
  beat: string | null;
  q_title: string | null;
  start_line: number;
  end_line: number;
};

export type SelectionAnchor = {
  text: string;
  startLine: number;
  endLine: number;
  x: number;
  y: number;
};

export type ChatMessage = {
  role: "user" | "assistant" | "tool";
  text: string;
  ok?: boolean;
};

export type DiagnosisSpot = {
  key: string;
  level: string;
  kind: string;
  label: string;
  q_title?: string | null;
  beat?: string | null;
  hits: number;
  weight: number;
  pct: number;
  keywords: string[];
  keyword_src?: { token: string; src: string }[];
  start_line?: number | null;
};

export type DiagnosisLevel = {
  level: string;
  hits: number;
  pct: number;
};

export type DiagnosisReport = {
  handbook_id: string;
  n_turns: number;
  n_curriculum: number;
  levels: DiagnosisLevel[];
  weak: DiagnosisSpot[];
  footprints: DiagnosisSpot[];
};

export type Proposal = {
  proposal_id: string;
  original_path: string;
  mode: string;
  level: string;
  q_title: string | null;
  instance_n: number;
  fold_md: string;
  diff: string;
};
