import type { DeepInbox, DynChip } from "./types";

/** 2.5 秒一拍，最多转 90 秒，连失败 3 次放弃。 */
export const DEEP_POLL_MS = 2500;
export const DEEP_POLL_BUDGET_MS = 90_000;
export const DEEP_POLL_MAX_FAILS = 3;

export type PollDeps = {
  /** 拉一次收件箱。 */
  fetch: (since: number) => Promise<DeepInbox>;
  /** 这一拍还该不该继续：代号没变、视图还在、会话没换。 */
  alive: () => boolean;
  /** 拿到新东西了。 */
  onItems: (items: DynChip[], cursor: number) => void;
  sleep: (ms: number) => Promise<void>;
  now: () => number;
  since: () => number;
};

/**
 * 深挖收件箱的轮询循环。
 *
 * 抽成纯函数是为了能在 node 里直接测真代码——它是整个功能里最容易悄悄泄漏的
 * 一段（少一个终止条件，关掉面板它还在后台转），复刻一份来测迟早会漂。
 *
 * 五个终止条件缺一不可：running 空 / 到时间 / 404 / 连失败 3 次 / 视图没了。
 */
export async function pollDeep(deps: PollDeps): Promise<void> {
  const until = deps.now() + DEEP_POLL_BUDGET_MS;
  let fails = 0;
  while (deps.now() < until) {
    await deps.sleep(DEEP_POLL_MS);
    if (!deps.alive()) return;
    try {
      const box = await deps.fetch(deps.since());
      if (!deps.alive()) return;
      fails = 0;
      if (box.items?.length) deps.onItems(box.items, box.cursor);
      // running 为空 = 没有在跑的了。sidecar 重启后走的也是这条路，
      // 所以不需要把「重启了」和「本来就没有」分开处理。
      if (!box.running?.length) return;
    } catch (e) {
      // 会话没了就别再敲；连不上则重试几次
      if (String((e as Error)?.message || "").includes("404")) return;
      if (++fails >= DEEP_POLL_MAX_FAILS) return;
    }
  }
}
