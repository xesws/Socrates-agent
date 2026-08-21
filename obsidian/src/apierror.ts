/**
 * 带 HTTP 状态码的错误。**这个文件不许 import 任何东西。**
 *
 * 为什么必须有：调用方要区分「会话没了」和「sidecar 连不上」，而它们唯一的
 * 区别就是状态码。此前 `api.ts` 抛的是裸 `Error(detail)`，detail 是**本地化**
 * 的服务端文案（「会话不存在」/ "unknown session"）——`deeppoll.ts` 里那句
 * `message.includes("404")` 因此永远为假，一条早就失效的终止条件。
 *
 * 为什么单独一个文件而不是塞进 `api.ts`：`deeppoll.ts` 要用它。而 `api.ts`
 * 依赖 `settings.ts` → `obsidian`，deeppoll 一旦顺着这条链走，它就不再是叶子，
 * `check-poll.mjs`（platform: neutral，不 external obsidian）当场打不动包。
 * 独立成零依赖的叶子，两边都能安全引。
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 这个错是不是「服务端说这东西没了」。会话被清理掉之后走的就是它。 */
export function isGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}
