import type { DeepInbox, DynChip } from "./types";

/**
 * 3 秒一拍，最多转 5 分钟，连失败 3 次放弃。
 *
 * 预算不是拍脑袋定的：真跑一次深挖实测约 2 分半（6k token 输入 + 一段 JSON），
 * 原来的 90 秒等不到结果就走了，等于白花那次调用。轮询本身是本地 HTTP，
 * 而且这时 busy 早已是 false，界面不受影响，所以宁可等久一点。
 */
export const DEEP_POLL_MS = 3000;
export const DEEP_POLL_BUDGET_MS = 300_000;
export const DEEP_POLL_MAX_FAILS = 3;

export type PollDeps = {
  /** 拉一次收件箱。 */
  fetch: (since: number) => Promise<DeepInbox>;
  /** 这一拍还该不该继续：代号没变、视图还在、会话没换。 */
  alive: () => boolean;
  /** 拿到新东西了。 */
  onItems: (items: DynChip[], cursor: number) => void;
  /** 每拍报一次配额。配额用满时读者需要知道为什么深题不来了。 */
  onBudget?: (budget: DeepInbox["budget"]) => void;
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
      if (box.budget) deps.onBudget?.(box.budget);
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


/** 同时可见几条深题。多了就成噪音。 */
export const MAX_VISIBLE_DEEP = 2;

/** 换轮时保留下来的深题。 */
export function keepDeep(cur: DynChip[]): DynChip[] {
  return cur.filter((c) => c.kind === "deep").slice(-MAX_VISIBLE_DEEP);
}

/**
 * 新到的深题并进现有列表，按文本去重，深题排前面并守显示上限。
 *
 * 上限管在这一层而不是服务端：那边一旦按「终身可见数」卡放行，池子就永远
 * 不衰减——pending 堆满之后 TTL 走不到、backlog 闸门永久关死，功能静默停摆。
 */
export function mergeDeep(cur: DynChip[], items: DynChip[]): DynChip[] {
  const seen = new Set(cur.map((c) => c.text));
  const next = [...cur];
  for (const it of items) {
    if (!it?.text || seen.has(it.text)) continue;
    seen.add(it.text);
    next.push({ ...it, kind: "deep" });
  }
  const deep = next.filter((c) => c.kind === "deep").slice(-MAX_VISIBLE_DEEP);
  const quick = next.filter((c) => c.kind !== "deep");
  return [...deep, ...quick];
}
