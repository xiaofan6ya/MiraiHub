/**
 * 全站统一的数字 / 时间格式化（docs/ui-v2/05-patterns.md §4）。
 *
 * 三条硬规则：
 *   1. 空值一律 FMT_EMPTY（"—"，颜色由调用方用 --fg-faint），不显示 0 / null / undefined；
 *   2. 数字一律等宽（tabular-nums 由样式负责），本文件只管文本；
 *   3. 与旧版输出对齐的口径写在每个函数上方；旧版有分歧时按 05-patterns §4 的规则，
 *      差异见 docs/ui-v2/contracts/platform-context.md 的"格式化口径"一节。
 *
 * 时区（第 5 轮，11-polish-motion §3「所有时间显示本地时区，悬停显示完整时间」）：
 *   · fmtTime / fmtFullTime / fmtRelative 一律按**浏览器本地时区**（UTC+8 的使用者与旧版输出完全一致）；
 *   · 悬停用 fmtTimeTitle（完整时间 + 时区偏移，如 `2026-11-24 23:05:00 UTC+08:00`），
 *     或直接用 timeText() / <TimeText>；
 *   · 必须与后端日历周期同口径的地方（定时任务「下次执行」、按北京时间切日的统计）显式用
 *     fmtBeijingTime / fmtBeijingFullTime / beijingParts。
 */
import { t } from '@/i18n'

/** 空值的统一占位符（旧版 console-app.js:325 FMT_EMPTY）。 */
export const FMT_EMPTY = '—'

const DAY_MS = 86400000
/** 北京时间偏移：只给显式的 fmtBeijing* / beijingParts 用（后端日历周期按北京时间切）。 */
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
 * **单位约定：入参固定是毫秒**（后端 elapsed_ms / latency_ms / ttfb_ms / avg_*_ms 全是 ms）。
 * 本函数不做任何量级猜测或换算，调用方也不要为了「显示正常」自行 ÷1000 / ×1000。
 *
 * 已知问题（不在这里修）：/api/stats/dashboard、/api/stats/model-market 的 avg_ttfb_ms /
 * avg_latency_ms 曾被后端汇总表放大约 1000 倍（rollup 的 count 被当成布尔，见 fix_plan N1），
 * 页面因此显示「430m 24s」。那是**后端数据错误**，由后端 rollup 修复并重建汇总表；
 * 前端若在这里除以 1000，修好后端之后所有正确数据都会被缩小 1000 倍。
 *
 * 0 原样显示为 0ms；「无样本」请调用方自行判断后显示 FMT_EMPTY（例如 requests === 0 时）。
 * 负数与非数一律 emdash（后端用 -1 / null 表示没测到）。
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

/**
 * 把 Date 拆成**浏览器本地时区**的各段（图表时间轴用）。
 *
 * 图表横轴、列表时间都按使用者本地时区显示（第 1 轮审查 P0-4；第 5 轮起 fmtTime 也走这里）。
 */
export function localParts(date: Date): DateParts {
  return {
    y: date.getFullYear(),
    mo: date.getMonth() + 1,
    d: date.getDate(),
    h: date.getHours(),
    mi: date.getMinutes(),
    s: date.getSeconds(),
  }
}

/** 后端小时桶键："2026-11-23 15:00" / "2026-11-23T15:00:00Z" / 带偏移的 ISO。 */
const HOUR_BUCKET = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/

/**
 * 时间桶键 → 本地时区的各段；不是小时桶（天桶 "2026-11-23"、空串、文案）时返回 null。
 *
 * 后端桶键是对 UTC 的 created_at 直接 strftime 出来的（admin/routers/stats.py:_bucket_expr /
 * _bucket_keys），不带时区后缀 —— 按 toDate 的约定当 UTC 解析，再换成本地时间。
 * 后端将来若改成带偏移的 ISO（如 "+08:00"），这里按偏移解析，结果仍然正确。
 *
 * 天桶没有时刻，无法换算（后端按 UTC 自然日切分），调用方原样显示月日。
 */
export function bucketLocalParts(key: unknown): DateParts | null {
  const text = String(key ?? '').trim()
  if (!HOUR_BUCKET.test(text)) return null
  const date = toDate(text)
  return date ? localParts(date) : null
}

/** 本地时区 HH:mm（图表横轴 / 分钟柱标签）。 */
export function fmtLocalHM(date: Date): string {
  const p = localParts(date)
  return pad2(p.h) + ':' + pad2(p.mi)
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

function compactParts(p: DateParts, q: DateParts): string {
  if (p.y === q.y && p.mo === q.mo && p.d === q.d) {
    return pad2(p.h) + ':' + pad2(p.mi) + ':' + pad2(p.s)
  }
  if (p.y === q.y) return pad2(p.mo) + '-' + pad2(p.d) + ' ' + pad2(p.h) + ':' + pad2(p.mi)
  return p.y + '-' + pad2(p.mo) + '-' + pad2(p.d) + ' ' + pad2(p.h) + ':' + pad2(p.mi)
}

function fullParts(p: DateParts): string {
  return (
    p.y + '-' + pad2(p.mo) + '-' + pad2(p.d) + ' ' + pad2(p.h) + ':' + pad2(p.mi) + ':' + pad2(p.s)
  )
}

/**
 * 紧凑时间（浏览器本地时区）：今天 → 22:27:36；今年 → 10-30 22:27；更早 → 2025-10-30 22:27。
 *
 * 第 5 轮起按本地时区（原为固定北京时间；UTC+8 的使用者输出不变）。解析不出但又不是空值时
 * 原样返回，免得把一个后端新格式的时间直接吞成 "—"。悬停请配 fmtTimeTitle。
 */
export function fmtTime(value: unknown, now?: Date): string {
  if (value === null || value === undefined || value === '') return FMT_EMPTY
  const date = toDate(value)
  if (!date) return String(value).trim().replace('T', ' ')
  // 被当回调传（arr.map(fmtTime)）时第二个参数是下标，不能当「现在」用。
  return compactParts(localParts(date), localParts(now instanceof Date ? now : new Date()))
}

/** 完整时间：YYYY-MM-DD HH:mm:ss（浏览器本地时区，不带偏移；悬停请用 fmtTimeTitle）。 */
export function fmtFullTime(value: unknown): string {
  const date = toDate(value)
  if (!date) return FMT_EMPTY
  return fullParts(localParts(date))
}

/**
 * 该时刻浏览器本地时区的 UTC 偏移：`UTC+08:00` / `UTC-04:30` / `UTC±00:00`。
 * 按**该时刻**算（夏令时前后不同）。
 */
export function fmtTzOffset(value: unknown = new Date()): string {
  const date = toDate(value)
  if (!date) return ''
  const minutes = -date.getTimezoneOffset()
  const sign = minutes > 0 ? '+' : minutes < 0 ? '-' : '±'
  const abs = Math.abs(minutes)
  return 'UTC' + sign + pad2(Math.floor(abs / 60)) + ':' + pad2(abs % 60)
}

/** 悬停用的完整时间：`2026-11-24 23:05:00 UTC+08:00`（本地时区 + 偏移）。空值返回空串（不出 tooltip）。 */
export function fmtTimeTitle(value: unknown): string {
  const date = toDate(value)
  if (!date) return ''
  return fullParts(localParts(date)) + ' ' + fmtTzOffset(date)
}

/** 机器可读时间（<time datetime>）：ISO 8601 UTC。解析不出返回空串。 */
export function toIsoString(value: unknown): string {
  const date = toDate(value)
  return date ? date.toISOString() : ''
}

/** 模板一次拿齐：`<time :datetime="x.iso" :title="x.title">{{ x.text }}</time>`。 */
export interface TimeText {
  text: string
  title: string
  iso: string
}

/** 紧凑时间（本地）+ 悬停完整时间（含偏移）+ ISO。relative=true 时 text 用 fmtRelative。 */
export function timeText(value: unknown, options: { relative?: boolean; now?: number } = {}): TimeText {
  const date = toDate(value)
  if (!date) {
    const raw = value === null || value === undefined ? '' : String(value).trim()
    return { text: raw ? raw.replace('T', ' ') : FMT_EMPTY, title: '', iso: '' }
  }
  const now = options.now ?? Date.now()
  return {
    text: options.relative ? fmtRelative(date, now) : fmtTime(date, new Date(now)),
    title: fmtTimeTitle(date),
    iso: date.toISOString(),
  }
}

/**
 * 紧凑时间（**固定北京时间**）：今天 → 22:27:36；今年 → 10-30 22:27；更早 → 2025-10-30 22:27。
 * 只给必须与后端日历周期同口径的地方用（定时任务「下次执行」等），旁边请标注「北京时间」
 * （已有词条 `page.schedules.zoneHint`）。
 */
export function fmtBeijingTime(value: unknown, now?: Date): string {
  if (value === null || value === undefined || value === '') return FMT_EMPTY
  const date = toDate(value)
  if (!date) return String(value).trim().replace('T', ' ')
  return compactParts(beijingParts(date), beijingParts(now instanceof Date ? now : new Date()))
}

/** 完整时间（固定北京时间）：YYYY-MM-DD HH:mm:ss。 */
export function fmtBeijingFullTime(value: unknown): string {
  const date = toDate(value)
  if (!date) return FMT_EMPTY
  return fullParts(beijingParts(date))
}

/**
 * 相对时间：刚刚 / 3 分钟前 / 2 小时前 / 昨天 / 10-30（今年）/ 2025-10-30（「昨天」与日期按本地时区）。
 * tooltip 里请配 fmtTimeTitle（或直接用 relativeTime / timeText({ relative: true })）。
 */
export function fmtRelative(value: unknown, now = Date.now()): string {
  const date = toDate(value)
  if (!date) return FMT_EMPTY
  const diff = now - date.getTime()
  // 将来的时间（下次执行、到期时刻）：1 分钟以上才说「N 分钟后 / N 小时后」，再远显示日期。
  if (diff <= -60000) {
    const ahead = -diff
    if (ahead < 3600000) return t('common.relative.inMinutes', { count: Math.floor(ahead / 60000) })
    if (ahead < DAY_MS) return t('common.relative.inHours', { count: Math.floor(ahead / 3600000) })
    return dayLabel(date, now)
  }
  if (diff < 60000) return t('common.relative.justNow')
  if (diff < 3600000) return t('common.relative.minutes', { count: Math.floor(diff / 60000) })
  if (diff < DAY_MS) return t('common.relative.hours', { count: Math.floor(diff / 3600000) })
  const p = localParts(date)
  const q = localParts(new Date(now))
  const dateDay = Date.UTC(p.y, p.mo - 1, p.d)
  const nowDay = Date.UTC(q.y, q.mo - 1, q.d)
  if (nowDay - dateDay === DAY_MS) return t('common.relative.yesterday')
  return dayLabel(date, now)
}

/** 相对时间超出范围时的日期：今年 MM-DD，更早 / 更晚 YYYY-MM-DD（本地时区）。 */
function dayLabel(date: Date, now: number): string {
  const p = localParts(date)
  const q = localParts(new Date(now))
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
    return days >= 1 ? t('format.expiredDays', { count: days }) : t('format.expired')
  }
  return t('format.expiresIn', { count: Math.ceil(diff / DAY_MS) })
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

/* ------------------------------------------------------------------ *
 * v2.1 补充（10-visual-refresh §5：无效值「—」、负倍率「按量计费」、拼接带分隔、
 * 时间本地时区 + 相对时间悬停显示绝对时间）
 * ------------------------------------------------------------------ */

/** 文案拼接的统一分隔符（第 1 轮审查 P0-7 / 第 2 轮 P0-15：不能直接把两段字连在一起）。 */
export const JOIN_SEP = ' · '

/** 判断「无效值」：null / undefined / 空串 / NaN / ±Infinity / 已经是 FMT_EMPTY。 */
export function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'number') return !Number.isFinite(value)
  if (typeof value === 'string') {
    const text = value.trim()
    return text === '' || text === FMT_EMPTY
  }
  return false
}

/**
 * 无效值显示「—」，其余交给 formatter（默认 String）。
 * 注意 0 不是无效值：0 原样显示；「没有样本」请调用方自己判断后传 null。
 */
export function orDash<T>(value: T | null | undefined, formatter?: (value: T) => string): string {
  if (isBlank(value)) return FMT_EMPTY
  const text = formatter ? formatter(value as T) : String(value)
  return isBlank(text) ? FMT_EMPTY : text
}

/**
 * 把若干段文字用「 · 」连起来，自动跳过空段和「—」。
 *   joinParts(['上下文 128K', '输出 16K'])   → '上下文 128K · 输出 16K'
 *   joinParts(['每 1 小时', null, '下次 23:30']) → '每 1 小时 · 下次 23:30'
 * 全部为空时返回 FMT_EMPTY（而不是空串，免得单元格里什么都没有）。
 */
export function joinParts(parts: ReadonlyArray<unknown>, sep: string = JOIN_SEP): string {
  const kept = parts
    .filter((part) => !isBlank(part) && part !== false)
    .map((part) => String(part).trim())
    .filter(Boolean)
  return kept.length ? kept.join(sep) : FMT_EMPTY
}

/**
 * 倍率：×1.50；负数（后端约定 -1 = 该平台没有倍率概念，按 token 计量）显示「按量计费」；
 * 无效值「—」（第 3 轮审查 A2：别再显示 ×-1.00）。
 */
export function fmtMultiplier(value: unknown, digits = 2): string {
  const n = looseNumber(value)
  if (n === null) return FMT_EMPTY
  if (n < 0) return t('common.metered')
  return '×' + n.toFixed(digits)
}

/** 指标耗时的合理上限：平均首字 / 平均耗时超过 1 小时，基本可以断定是汇总数据错了。 */
export const LATENCY_SANITY_MAX_MS = 3600 * 1000

/**
 * 指标类耗时（平均首字 / 平均耗时 / P90）：与 fmtDuration 同口径，但超过 1 小时显示「数据异常」
 * 并在控制台警告一次（第 3 轮审查 C：后端汇总表曾放大约 1000 倍）。
 * 单条请求的耗时继续用 fmtDuration（真实的长请求可以超过 1 小时）。
 */
export function fmtLatency(ms: unknown, maxMs: number = LATENCY_SANITY_MAX_MS): string {
  const n = looseNumber(ms)
  if (n === null || n < 0) return FMT_EMPTY
  if (n > maxMs) {
    warnOnce('latency', '[format] 指标耗时 ' + n + 'ms 超过 ' + maxMs + 'ms，疑似后端汇总数据错误')
    return t('common.dataAnomaly')
  }
  return fmtDuration(n)
}

const warned = new Set<string>()
function warnOnce(key: string, message: string): void {
  if (warned.has(key)) return
  warned.add(key)
  if (typeof console !== 'undefined') console.warn(message)
}

/** 紧凑时间（浏览器本地时区）。第 5 轮起与 fmtTime 相同，保留旧名兼容。 */
export function fmtLocalTime(value: unknown, now?: Date): string {
  return fmtTime(value, now)
}

/** 完整时间（浏览器本地时区）：YYYY-MM-DD HH:mm:ss。第 5 轮起与 fmtFullTime 相同，保留旧名兼容。 */
export function fmtLocalFullTime(value: unknown): string {
  return fmtFullTime(value)
}

/** 相对时间 + 悬停用的完整时间（本地时区 + 偏移），模板里写 `:title="r.title"`。 */
export interface RelativeTime {
  text: string
  title: string
}

export function relativeTime(value: unknown, now = Date.now()): RelativeTime {
  const date = toDate(value)
  if (!date) return { text: FMT_EMPTY, title: '' }
  return { text: fmtRelative(date, now), title: fmtTimeTitle(date) }
}
