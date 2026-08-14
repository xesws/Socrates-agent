import type { DiagnosisReport, HandbookMeta, Proposal, Section, SessionView } from "./types";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export type LlmStatus = {
  ok: boolean;
  base_url: string;
  model: string;
  key_source: string;
};

export const api = {
  health: () => j<{ status: string; llm: LlmStatus }>("/v1/health"),
  handbooks: () => j<{ handbooks: HandbookMeta[] }>("/v1/handbooks"),
  handbook: (id: string) => j<HandbookMeta>(`/v1/handbooks/${id}`),
  content: (id: string) =>
    j<{ original_path: string; text: string; mtime: number }>(
      `/v1/handbooks/${id}/content`,
    ),
  locate: (id: string, line: number) =>
    j<Section>(`/v1/handbooks/${id}/locate?line=${line}`),
  createSession: (handbook_id: string, session_id?: string) =>
    j<SessionView>("/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ handbook_id, session_id }),
    }),
  getSession: (session_id: string) => j<SessionView>(`/v1/sessions/${session_id}`),
  propose: (session_id: string) =>
    j<Proposal>("/v1/writeback/propose", {
      method: "POST",
      body: JSON.stringify({ session_id }),
    }),
  apply: (session_id: string, proposal_id: string, commit: boolean) =>
    j<{ ok: boolean; original_path: string; snapshot: string; commit: string | null }>(
      "/v1/writeback/apply",
      {
        method: "POST",
        body: JSON.stringify({ session_id, proposal_id, commit }),
      },
    ),
  rollback: (handbook_id: string) =>
    j<{ ok: boolean; restored_from: string; original_path: string }>(
      "/v1/writeback/rollback",
      { method: "POST", body: JSON.stringify({ handbook_id }) },
    ),
  diagnosis: (handbook_id: string) =>
    j<DiagnosisReport>(`/v1/handbooks/${handbook_id}/diagnosis`),
  narrateDiagnosis: (handbook_id: string) =>
    j<{ handbook_id: string; narrative: string }>(
      `/v1/handbooks/${handbook_id}/diagnosis/narrate`,
      { method: "POST" },
    ),
};

export async function streamChat(
  body: {
    session_id: string;
    selected_text: string;
    start_line: number;
    end_line: number;
    chip: string;
    user_text: string;
  },
  onEvent: (ev: Record<string, unknown>) => void,
): Promise<void> {
  const res = await fetch("/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const part of parts) {
      const line = part
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("");
      if (!line) continue;
      onEvent(JSON.parse(line) as Record<string, unknown>);
    }
  }
}
