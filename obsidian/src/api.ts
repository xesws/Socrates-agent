import { currentLang } from "./i18n";
import type { PenSettings } from "./settings";
import { llmPayload } from "./settings";
import type { HandbookMeta, LlmStatus, SessionView } from "./types";

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/$/, "")}${path}`;
}

async function j<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(joinUrl(base, path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      // sidecar 的错误文案按这个头选语言。走 header 而不是 body 字段，
      // 是为了让没有 body 的 GET 路由也能覆盖。
      "Accept-Language": currentLang(),
      ...(init?.headers || {}),
    },
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
    snapshots: (handbook_id: string) =>
      j<{
        can_undo: boolean;
        can_redo: boolean;
        undo_n: number;
        redo_n: number;
      }>(baseUrl, `/v1/handbooks/${handbook_id}/snapshots`),
    rollback: (handbook_id: string) =>
      j<{
        ok: boolean;
        restored_from: string;
        original_path: string;
        can_undo: boolean;
        can_redo: boolean;
        undo_n: number;
        redo_n: number;
      }>(baseUrl, "/v1/writeback/rollback", {
        method: "POST",
        body: JSON.stringify({ handbook_id }),
      }),
    redo: (handbook_id: string) =>
      j<{
        ok: boolean;
        restored_from: string;
        original_path: string;
        can_undo: boolean;
        can_redo: boolean;
        undo_n: number;
        redo_n: number;
      }>(baseUrl, "/v1/writeback/redo", {
        method: "POST",
        body: JSON.stringify({ handbook_id }),
      }),
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
    headers: {
      "Content-Type": "application/json",
      "Accept-Language": currentLang(),
    },
    body: JSON.stringify({
      ...body,
      ...(settings ? llmPayload(settings) : {}),
    }),
  });
  await readSse(res, onEvent);
}

export async function streamApprove(
  baseUrl: string,
  body: { session_id: string; pending_id: string; allow: boolean },
  onEvent: (ev: Record<string, unknown>) => void,
  settings?: PenSettings,
): Promise<void> {
  const res = await fetch(joinUrl(baseUrl, "/v1/chat/approve"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept-Language": currentLang(),
    },
    body: JSON.stringify({
      ...body,
      ...(settings ? llmPayload(settings) : {}),
    }),
  });
  await readSse(res, onEvent);
}

async function readSse(
  res: Response,
  onEvent: (ev: Record<string, unknown>) => void,
): Promise<void> {
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
  const takeFrames = (chunk: string): string => {
    const parts = chunk.split("\n\n");
    const rest = parts.pop() || "";
    for (const part of parts) {
      const line = part
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("");
      if (!line) continue;
      onEvent(JSON.parse(line) as Record<string, unknown>);
    }
    return rest;
  };
  while (true) {
    const { done, value } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    if (done) {
      buf += dec.decode();
      if (buf && !buf.endsWith("\n\n")) buf += "\n\n";
      takeFrames(buf);
      break;
    }
    buf = takeFrames(buf);
  }
}
