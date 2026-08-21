import type { DiagnosisReport, HandbookMeta, Proposal, Section, SessionView } from "./types";

/**
 * 把 sidecar 的错误 body 抽成一句人话。
 *
 * v0.12.6 起「这场会话没了」那一种 404 的 detail 是 `{code, message}`
 * 而不是一句字符串（插件那边要靠 code 把它和「笔记被改名」那种 404 分开，
 * 见 `obsidian/src/apierror.ts`）。这里照旧只要文案——但 `body.detail || …`
 * 那种写法对着对象会短路取到对象本身，`new Error(对象).message` 是
 * **`"[object Object]"`**，`PenPanel` 直接把它渲染进错误条。
 * 一句本地化的「未知会话」就这么变成了一串乱码。
 */
function detailOf(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string" && d) return d;
  if (d && typeof d === "object") {
    const m = (d as { message?: unknown }).message;
    if (typeof m === "string" && m) return m;
  }
  // FastAPI 的 422 校验错误 detail 是个**数组**，整个 body 倒出来比什么都没有强。
  return body ? JSON.stringify(body) : fallback;
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = detailOf(await res.json(), detail);
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
    j<{
      ok: boolean;
      original_path: string;
      snapshot: string;
      commit: string | null;
      commit_error?: string | null;
      bytes?: number;
    }>(
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
      detail = detailOf(await res.json(), detail);
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
