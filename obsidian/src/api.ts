import type { PenSettings } from "./settings";
import { llmPayload } from "./settings";
import type { HandbookMeta, LlmStatus, Proposal, SessionView } from "./types";

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/$/, "")}${path}`;
}

async function j<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(joinUrl(base, path), {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = (body as { detail?: string }).detail || JSON.stringify(body);
    } catch {
      /* keep */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function makeApi(baseUrl: string) {
  return {
    health: () => j<{ status: string; llm: LlmStatus }>(baseUrl, "/v1/health"),
    importHandbook: (original_path: string, handbook_id: string, vault_root?: string) =>
      j<HandbookMeta>(baseUrl, "/v1/handbooks/import", {
        method: "POST",
        body: JSON.stringify({ original_path, handbook_id, vault_root }),
      }),
    createSession: (handbook_id: string, session_id?: string) =>
      j<SessionView>(baseUrl, "/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ handbook_id, session_id }),
      }),
    getSession: (session_id: string) =>
      j<SessionView>(baseUrl, `/v1/sessions/${session_id}`),
    propose: (session_id: string, settings?: PenSettings) =>
      j<Proposal>(baseUrl, "/v1/writeback/propose", {
        method: "POST",
        body: JSON.stringify({
          session_id,
          ...(settings ? llmPayload(settings) : {}),
        }),
      }),
    apply: (session_id: string, proposal_id: string) =>
      j<{
        ok: boolean;
        original_path: string;
        snapshot: string;
        commit: string | null;
        commit_error?: string | null;
      }>(baseUrl, "/v1/writeback/apply", {
        method: "POST",
        body: JSON.stringify({ session_id, proposal_id, commit: false }),
      }),
    rollback: (handbook_id: string) =>
      j<{ ok: boolean; restored_from: string; original_path: string }>(
        baseUrl,
        "/v1/writeback/rollback",
        {
          method: "POST",
          body: JSON.stringify({ handbook_id }),
        },
      ),
  };
}

export async function streamChat(
  baseUrl: string,
  body: {
    session_id: string;
    selected_text: string;
    start_line: number;
    end_line: number;
    chip: string;
    user_text: string;
  },
  onEvent: (ev: Record<string, unknown>) => void,
  settings?: PenSettings,
): Promise<void> {
  const res = await fetch(joinUrl(baseUrl, "/v1/chat"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...body,
      ...(settings ? llmPayload(settings) : {}),
    }),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = ((await res.json()) as { detail?: string }).detail || detail;
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
