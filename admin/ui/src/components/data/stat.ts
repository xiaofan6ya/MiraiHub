/** 统计卡数据与语气映射（纯函数，供 StatCard / StatStrip / 单测共用）。 */

export type StatTone = 'default' | 'brand' | 'ok' | 'warn' | 'danger' | 'muted'

export interface StatItem {
  /** 12px --fg-muted 标签 */
  label: string
  /** 数值（28px/600 tabular，--fs-stat） */
  value: string | number
  /** 数值后的 14px 单位 */
  unit?: string
  /** 仅当数值本身表达状态时才着色 */
  tone?: StatTone
  /** 脚注：12px --fg-muted，如「成功 696 次 · 较昨日 +12%」 */
  foot?: string
  /** 标签后的 ? 气泡说明 */
  help?: string
  /** 整格可点击跳转 */
  to?: string
}

const TONE_COLORS: Record<StatTone, string> = {
  default: 'var(--fg)',
  brand: 'var(--brand)',
  ok: 'var(--ok)',
  warn: 'var(--warn)',
  danger: 'var(--danger)',
  muted: 'var(--fg-muted)',
}

export function statToneColor(tone?: StatTone | null): string {
  const key = (tone || 'default') as StatTone
  return TONE_COLORS[key] ?? TONE_COLORS.default
}

/**
 * 栅格列数：2–5 列（04-components：N=2–5）。
 * 6 个排成 3×2、7–8 个排成 4 列，不再出现「5+1」孤零零剩一格（第 1 轮审查 P2 风控哨兵）。
 */
export function stripColumns(count: number): number {
  if (!Number.isFinite(count) || count <= 1) return 1
  const n = Math.round(count)
  if (n <= 5) return Math.max(2, n)
  if (n === 6) return 3
  if (n <= 8) return 4
  return 5
}

/** 中屏（768–1279）列数：最多 3 列，6 个时 3×2，4 个时 2×2。 */
export function stripColumnsMedium(count: number): number {
  const cols = stripColumns(count)
  if (cols <= 3) return cols
  return cols === 4 ? 2 : 3
}
