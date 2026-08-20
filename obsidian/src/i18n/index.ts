/**
 * 词表入口。本版只有中文一张表；v0.7.0 会在这里接上 en 与语言解析。
 *
 * 调用约定：**永远 `t().xxx` 现取现用**，不要把 `t()` 的返回值存进长生命周期的
 * 字段里——否则切换语言后那份引用还是旧表。
 */

import { zh, type Dict } from "./zh";

export type { Dict };

let current: Dict = zh;

export function t(): Dict {
  return current;
}

/**
 * 后端下发的 status 事件带稳定的 `phase` 字段（pen/tutor.py）。
 * 认识就换成本地文案，不认识就照抄后端下发的 text——这样后端将来新增 phase
 * 不会让状态行变空白。
 */
export function phaseText(phase: string, fallback: string): string {
  return t().phases[phase] || fallback;
}
