/** KpiCard 的纯函数（数值 / 环比格式化与好坏判定），供组件与单测共用。 */
import { FMT_EMPTY } from '@/utils/format'

export type KpiTone = 'brand' | 'ok' | 'warn' | 'danger' | 'neutral'
export type KpiDeltaGood = 'up' | 'down'

export interface KpiDeltaView {
  /** 「+12.3%」「−4%」「0%」 */
  text: string
  /** 箭头方向。 */
  dir: 'up' | 'down' | 'flat'
  /** 好 / 坏 / 持平（决定 chip 颜色：ok / danger / neutral）。 */
  verdict: 'good' | 'bad' | 'flat'
}

/** 环比：百分比数值（12.3 = +12.3%）；null / 非数返回 null（不显示 chip）。 */
export function kpiDelta(delta: unknown, deltaGood: KpiDeltaGood = 'up'): KpiDeltaView | null {
  if (delta === null || delta === undefined || delta === '') return null
  const n = typeof delta === 'number' ? delta : Number(delta)
  if (!Number.isFinite(n)) return null
  const abs = Math.abs(n)
  // 很小的变化四舍五入成 0 时按持平处理，避免「+0.0%」却标成绿色。
  const digits = abs >= 100 ? 0 : 1
  const rounded = Number(abs.toFixed(digits))
  if (rounded === 0) return { text: '0%', dir: 'flat', verdict: 'flat' }
  const dir: 'up' | 'down' = n > 0 ? 'up' : 'down'
  const body = rounded.toFixed(digits).replace(/\.0$/, '') + '%'
  const text = (dir === 'up' ? '+' : '\u2212') + body
  const verdict = dir === deltaGood ? 'good' : 'bad'
  return { text, dir, verdict }
}

/**
 * 大数字：数字按语言加千分位（整数不带小数，小数最多 2 位）；字符串原样；
 * null / undefined / 空串 / 非数显示「—」。
 */
export function kpiValueText(value: unknown, localeTag = 'zh-CN'): string {
  if (value === null || value === undefined) return FMT_EMPTY
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return FMT_EMPTY
    return Number.isInteger(value)
      ? value.toLocaleString(localeTag)
      : value.toLocaleString(localeTag, { maximumFractionDigits: 2 })
  }
  const text = String(value).trim()
  return text ? text : FMT_EMPTY
}

export const KPI_TONE_COLORS: Record<KpiTone, { fg: string; bg: string }> = {
  brand: { fg: 'var(--brand)', bg: 'var(--brand-soft)' },
  ok: { fg: 'var(--ok)', bg: 'var(--ok-soft)' },
  warn: { fg: 'var(--warn)', bg: 'var(--warn-soft)' },
  danger: { fg: 'var(--danger)', bg: 'var(--danger-soft)' },
  neutral: { fg: 'var(--fg-2)', bg: 'var(--neutral-soft)' },
}

export function kpiToneColors(tone?: string | null): { fg: string; bg: string } {
  const key = String(tone || 'brand') as KpiTone
  return KPI_TONE_COLORS[key] ?? KPI_TONE_COLORS.brand
}

/**
 * 把「已经格式化好的字符串」拆成数字 + 单位（第 4 轮，KpiCard `split-unit`）：
 *   '98.5%' → ['98.5', '%']、'4.6s' → ['4.6', 's']、'1.2 万' → ['1.2', '万']、'12,345 次' → ['12,345', '次']。
 * 只在**以数字开头、以非数字结尾**时拆；拆不出来（纯文字、'—'、中间夹字母的 '1h 20m'）原样返回、单位为空。
 */
export function kpiSplitUnit(text: string): { num: string; unit: string } {
  const raw = String(text ?? '').trim()
  const match = /^([+\-\u2212]?\d(?:[\d,.\u00a0\u202f]*\d)?)\s*([^\d\s.,][^\d]{0,11})$/.exec(raw)
  if (!match) return { num: raw, unit: '' }
  return { num: match[1], unit: match[2].trim() }
}
