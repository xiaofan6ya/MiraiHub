/**
 * 全站统一的数字 / 时间格式化（docs/ui-v2/05-patterns.md §4）。
 *
 * 三条硬规则：
 *   1. 空值一律 FMT_EMPTY（"—"，颜色由调用方用 --fg-faint），不显示 0 / null / undefined；
 *   2. 数字一律等宽（tabular-nums 由样式负责），本文件只管文本；
 *   3. 与旧版输出对齐的口径写在每个函数上方；旧版有分歧时按 05-patterns §4 的规则，
 *      差异见 docs/ui-v2/contracts/platform-context.md 的"格式化口径"一节。
 */

/** 空值的统一占位符（旧版 console-app.js:325 FMT_EMPTY）。 */
export const FMT_EMPTY = '—'

const DAY_MS = 86400000
/** 后端时间一律按 UTC 存、统一 +8 展示（旧版 fmtTime 的做法，与服务器时区无关）。 */
const BEIJING_OFFSET_MS = 8 * 3600 * 1000

function looseNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** 千分位整数（不缩写）。表格里的积分 / 余额用它：17,583。 */
export function fmtInt(value: unknown): string {
  const n = looseNumber(value)
  if (n === null) return FMT_EMPTY
  return Math.round(n).toLocaleString('zh-CN')
}

/**
 * 量级缩写：1 万以下原样加千分位；1 万起用 K / M / B，保留 1 位小数并去掉无意义的 .0。
 *
 * 口径与旧版 fmtToken（console-app.js:17395）一致，也就是 05-patterns §4 举的
 * 2.3M / 150.9K：阈值为 1e4（国内习惯看"万"），但单位仍用 K。
 * 注：旧版 fmtK 对 M 保留 2 位小数（2.35M），这里按设计稿统一为 1 位。
 */
export function fmtCompact(value: unknown): string {
  const n = looseNumber(value)
  if (n === null) return FMT_EMPTY
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  const trim = (text: string) => text.replace(/\.0+$/, '')
  if (abs >= 1e9) return sign + trim((abs / 1e9).toFixed(1)) + 'B'
  if (abs >= 1e6) return sign + trim((abs / 1e6).toFixed(1)) + 'M'
  if (abs >= 1e4) return sign + trim((abs / 1e3).toFixed(1)) + 'K'
  return sign + Math.round(abs).toLocaleString('zh-CN')
}

/**
 * 积分：整数显示整数，小数最多 2 位（9.55 / 789.31）。
 *
 * 与旧版 fmtCredit 的口径不同：旧版汇总取整、单包用 fmtCreditExact 保留两位；
 * 新版按设计稿统一成一个函数，汇总处若需要整数请显式套 fmtInt。
 */
export function fmtCredit(value: unknown): string {
  const n = looseNumber(value)
  if (n === null) return FMT_EMPTY
  if (Number.isInteger(n)) return n.toLocaleString('zh-CN')
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}

/**
 * 比率 → 百分比，默认 1 位小数（95.0%）。
 *
 * 入参是 0–1 的比率（后端 success_rate / hit_rate 都是这个口径），
 * 已经是 0–100 的字段（如任务进度的 percent）不要用它。
 */
export function fmtPct(value: unknown, digits = 1): string {
  const n = looseNumber(value)
  if (n === null) return FMT_EMPTY
  return (n * 100).toFixed(digits) + '%'
}

/**
 * 耗时：毫秒进，自动选单位。
 *
 *   < 1s    → 850ms
 *   < 60s   → 3.95s
 *   >= 60s  → 1m 30s / 75m 12s
 *
 * 入参单位固定是毫秒（后端 elapsed_ms / ttfb_ms / total_ms）。
 * 负数与非数一律 emdash：0 会被读成"瞬间"，而实际是没测到。
 */
export function fmtDuration(ms: unknown): string {
  const n = looseNumber(ms)
  if (n === null || n < 0) return FMT_EMPTY
  if (n < 1000) return Math.round(n) + 'ms'
  const totalSec = n / 1000
  if (totalSec < 60) return totalSec.toFixed(2).replace(/\.?0+$/, '') + 's'
  const totalMin = Math.floor(totalSec / 60)
  const sec = Math.round(totalSec - totalMin * 60)
  // 59.6s 四舍五入成 60 → 进位成 1 分钟，避免出现 "1m 60s"
  const minutes = sec === 60 ? totalMin + 1 : totalMin
  const seconds = sec === 60 ? 0 : sec
  return minutes + 'm ' + String(seconds).padStart(2, '0') + 's'
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

export interface DateParts {
  y: number
  mo: number
  d: number
  h: number
  mi: number
  s: number
}

/** 把 Date 拆成北京时间（UTC+8）的各段。 */
export function beijingParts(date: Date): DateParts {
  const shifted = new Date(date.getTime() + BEIJING_OFFSET_MS)
  return {
    y: shifted.getUTCFullYear(),
    mo: shifted.getUTCMonth() + 1,
    d: shifted.getUTCDate(),
    h: shifted.getUTCHours(),
    mi: shifted.getUTCMinutes(),
    s: shifted.getUTCSeconds(),
  }
}

/**
 * 后端时间 → Date。
 *
 * 兼容三种形态：带时区的 ISO、不带时区的 "YYYY-MM-DD HH:mm:ss"（按 UTC 解释，
 * 与旧版 toMs 一致）、以及秒/毫秒时间戳数字（10 位当秒，13 位当毫秒）。
 */
export function toDate(value: unknown): Date | null {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null
    const ms = Math.abs(value) < 1e11 ? value * 1000 : value
    const date = new Date(ms)
    return Number.isNaN(date.getTime()) ? null : date
  }
  const text = String(value).trim()
  if (!text) return null
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text)) {
    const parsed = Date.parse(text)
    return Number.isNaN(parsed) ? null : new Date(parsed)
  }
  const asUtc = Date.parse(text.replace(' ', 'T') + 'Z')
  if (!Number.isNaN(asUtc)) return new Date(asUtc)
  const fallback = Date.parse(text)
  return Number.isNaN(fallback) ? null : new Date(fallback)
}

/**
 * 紧凑时间：今天 → 22:27:36；今年 → 10-30 22:27；更早 → 2025-10-30 22:27。
 *
 * 展示时区固定为北京时间（与旧版一致）。解析不出但又不是空值时原样返回，
 * 免得把一个后端新格式的时间直接吞成 "—"。
 */
export function fmtTime(value: unknown): string {
  if (value === null || value === undefined || value === '') return FMT_EMPTY
  const date = toDate(value)
  if (!date) return String(value).trim().replace('T', ' ')
  const p = beijingParts(date)
  const now = beijingParts(new Date())
  if (p.y === now.y && p.mo === now.mo && p.d === now.d) {
    return pad2(p.h) + ':' + pad2(p.mi) + ':' + pad2(p.s)
  }
  if (p.y === now.y) {
    return pad2(p.mo) + '-' + pad2(p.d) + ' ' + pad2(p.h) + ':' + pad2(p.mi)
  }
  return p.y + '-' + pad2(p.mo) + '-' + pad2(p.d) + ' ' + pad2(p.h) + ':' + pad2(p.mi)
}

/** 完整时间：YYYY-MM-DD HH:mm:ss（北京时间）。悬停 tooltip 用。 */
export function fmtFullTime(value: unknown): string {
  const date = toDate(value)
  if (!date) return FMT_EMPTY
  const p = beijingParts(date)
  return (
    p.y +
    '-' +
    pad2(p.mo) +
    '-' +
    pad2(p.d) +
    ' ' +
    pad2(p.h) +
    ':' +
    pad2(p.mi) +
    ':' +
    pad2(p.s)
  )
}

/**
 * 相对时间：刚刚 / 3 分钟前 / 2 小时前 / 昨天 / 10-30（今年）/ 2025-10-30。
 * tooltip 里请配 fmtFullTime。
 */
export function fmtRelative(value: unknown, now = Date.now()): string {
  const date = toDate(value)
  if (!date) return FMT_EMPTY
  const diff = now - date.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + ' 分钟前'
  if (diff < DAY_MS) return Math.floor(diff / 3600000) + ' 小时前'
  const p = beijingParts(date)
  const q = beijingParts(new Date(now))
  const dateDay = Date.UTC(p.y, p.mo - 1, p.d)
  const nowDay = Date.UTC(q.y, q.mo - 1, q.d)
  if (diff > 0 && nowDay - dateDay === DAY_MS) return '昨天'
  if (p.y === q.y) return pad2(p.mo) + '-' + pad2(p.d)
  return p.y + '-' + pad2(p.mo) + '-' + pad2(p.d)
}

/** 到期文案：15 天后到期 / 已过期 2 天 / 已过期。 */
export function fmtExpiry(value: unknown, now = Date.now()): string {
  const date = toDate(value)
  if (!date) return FMT_EMPTY
  const diff = date.getTime() - now
  if (diff <= 0) {
    const days = Math.floor(-diff / DAY_MS)
    return days >= 1 ? '已过期 ' + days + ' 天' : '已过期'
  }
  return Math.ceil(diff / DAY_MS) + ' 天后到期'
}

export type ExpiryTone = 'neutral' | 'warn' | 'danger'

/** 到期语义色：>7 天 neutral，≤7 天 warn，已过期 danger（09 一致）。 */
export function expiryTone(value: unknown, now = Date.now()): ExpiryTone {
  const date = toDate(value)
  if (!date) return 'neutral'
  const diff = date.getTime() - now
  if (diff <= 0) return 'danger'
  const days = Math.ceil(diff / DAY_MS)
  return days <= 7 ? 'warn' : 'neutral'
}

/**
 * 密钥打码：保留前 6 后 4（sk-mh3…9f2e）。
 *
 * 太短的串（≤10）中段全遮，只留首尾各 1 个字符；≤4 个字符只留一个省略号，
 * 免得"打码"之后反而把整串露出来。
 */
export function maskSecret(value: unknown): string {
  if (value === null || value === undefined) return FMT_EMPTY
  const text = String(value)
  if (!text) return FMT_EMPTY
  if (text.length <= 4) return '…'
  if (text.length <= 10) return text.charAt(0) + '…' + text.charAt(text.length - 1)
  return text.slice(0, 6) + '…' + text.slice(-4)
}
