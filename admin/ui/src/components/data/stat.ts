/** 统计卡数据与语气映射（纯函数，供 StatCard / StatStrip / 单测共用）。 */

export type StatTone = 'default' | 'brand' | 'ok' | 'warn' | 'danger' | 'muted'

export interface StatItem {
  /** 12px --fg-muted 标签 */
  label: string
  /** 数值（26px/600 tabular） */
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

/** 栅格列数：2–5 列，超出的按 5 列排（04-components：N=2–5）。 */
export function stripColumns(count: number): number {
  if (!Number.isFinite(count) || count <= 1) return 1
  return Math.min(5, Math.max(2, Math.round(count)))
}
