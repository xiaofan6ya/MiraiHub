/**
 * 使用记录（/usage）的接口与数据转换。
 *
 * 契约：docs/ui-v2/contracts/usage.md
 * 后端：
 *   GET /api/stats/usage          → admin/routers/stats.py:usage_stats（356）/ _usage_aggregates（463）
 *   GET /api/stats/usage/options  → admin/routers/stats.py:usage_filter_options（738）
 *
 * 四条必须守住的后端语义：
 *   1. 成功 = error_kind in ('', 'success')；Token / 积分只算**成功**请求，requests 含失败。
 *   2. 分布行只统计成功请求（_ok_filter），所以「按请求数」的图实际是「按成功请求数」。
 *   3. 平台口径：/usage **只认显式 ?platform=**，不看 X-Platform 头（切平台不切这份口径）。
 *   4. 时间窗：有 start 用 start（end 可选）；都没有才用滚动 days。默认「今日」= 北京时间 00:00 起。
 *
 * 页面只做摆放：筛选映射 / 窗口解析 / 统计卡 / BarList / 趋势 option / 日志深链 / CSV 都在这里，
 * 全部是纯函数，可直接单测。
 */
import { http, type Query } from './http'
import { beijingParts, bucketLocalParts, fmtCompact, fmtCredit, fmtDuration, fmtInt, fmtPct } from '@/utils/format'
import { TOOLTIP_TITLES_KEY, bucketTooltipTitle } from '@/components/charts/options'
import { avgDurationText, durationState } from './stats'
import { t as defaultTranslate, type MessageKey, type Translate } from '@/i18n'

/** 文案取当前语言；页面传入 useI18n().t 后随语言切换重算，不传时用默认字典（中文）。 */
function tr(
  translate: Translate | undefined,
  key: MessageKey,
  params?: Record<string, string | number>,
): string {
  return (translate ?? defaultTranslate)(key, params)
}

/* -------------------------------------------------------------------------- */
/* 后端形状                                                                    */
/* -------------------------------------------------------------------------- */

export interface UsageOverview {
  requests: number
  ok_requests: number
  failed_requests: number
  credits: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  avg_latency_ms: number
}

/** 分布行：by_endpoint / by_key 的形状（label 已由后端翻成人话）。 */
export interface UsageDistRow {
  label: string
  requests: number
  tokens: number
  credits: number
}

/** by_model：同一模型在不同平台是两笔账，所以额外带 model / platform。 */
export interface UsageModelRow extends UsageDistRow {
  model: string
  platform: string
  platform_label: string
}

/** by_account：SQL 侧 Top 8 + 「其他 N 个账号」汇总行。 */
export interface UsageAccountRow extends UsageDistRow {
  account_id: number | null
  others?: boolean
}

/** by_result：全部请求（含失败），只有 requests。 */
export interface UsageResultRow {
  label: string
  requests: number
}

export interface UsageTrendPoint {
  bucket: string
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  credits: number
  requests: number
}

export interface UsageAccountModel {
  model: string
  requests: number
  successes: number
  failures: number
  tokens: number
  credits: number
  limit?: unknown
}

export interface UsagePlatformRow {
  value: string
  label: string
  requests: number
  tokens: number
  credits: number
}

export interface UsagePayload {
  range_days: number | null
  start: string | null
  end: string | null
  granularity: string
  filters: {
    key_id: number | null
    model: string | null
    account_id: number | null
    use_case: string | null
    platform: string
  }
  platform: string
  platform_label: string
  platforms: UsagePlatformRow[]
  overview: UsageOverview
  by_model: UsageModelRow[]
  by_endpoint: UsageDistRow[]
  by_key: UsageDistRow[]
  by_account: UsageAccountRow[]
  by_result: UsageResultRow[]
  account_models: UsageAccountModel[]
  usage_scope: string
  trend: UsageTrendPoint[]
}

export interface UsageOptionModel {
  value: string
  label: string
  requests: number
}

export interface UsageOptionsPayload {
  models: UsageOptionModel[]
  use_cases: string[]
  keys: Array<{ id: number; name: string }>
  accounts: Array<{ id: number; name: string }>
  platforms: Array<{ value: string; label: string; requests: number }>
}

/* -------------------------------------------------------------------------- */
/* 接口                                                                        */
/* -------------------------------------------------------------------------- */

export type UsageGrain = 'hour' | 'day'

export interface UsageQueryParams {
  start?: string
  end?: string
  days?: number
  granularity?: UsageGrain
  key_id?: number | null
  model?: string
  account_id?: number | null
  use_case?: string
  platform?: string
  /** refresh=1：跳过后端统计缓存（刷新按钮专用）。 */
  refresh?: boolean
}

/** 请求体 → query（纯函数：null/空串一律丢掉，与 http.buildQueryString 行为一致）。 */
export function usageQuery(params: UsageQueryParams): Query {
  const query: Query = { granularity: params.granularity === 'day' ? 'day' : 'hour' }
  if (params.start) query.start = params.start
  if (params.end) query.end = params.end
  if (!params.start && !params.end) query.days = Math.max(1, Math.round(params.days || 1))
  if (params.key_id) query.key_id = params.key_id
  if (params.account_id) query.account_id = params.account_id
  if (params.model) query.model = params.model
  if (params.use_case) query.use_case = params.use_case
  if (params.platform) query.platform = params.platform
  if (params.refresh) query.refresh = 1
  return query
}

export function getUsageStats(params: UsageQueryParams, signal?: AbortSignal): Promise<UsagePayload> {
  return http<UsagePayload>('GET', '/api/stats/usage', { query: usageQuery(params), signal })
}

export function getUsageOptions(
  params: Pick<UsageQueryParams, 'start' | 'end' | 'days'>,
  signal?: AbortSignal,
): Promise<UsageOptionsPayload> {
  return http<UsageOptionsPayload>('GET', '/api/stats/usage/options', {
    query: usageQuery({ ...params, granularity: 'hour' }),
    signal,
  })
}

/* -------------------------------------------------------------------------- */
/* 筛选项 / 时间窗                                                             */
/* -------------------------------------------------------------------------- */

export interface UsageFilters {
  platform: string
  /** Key 的 id（字符串，来自 options.keys[].id） */
  key: string
  /** 落库模型 id（不是展示名） */
  model: string
  /** 账号 id（字符串） */
  account: string
  /** use_case */
  endpoint: string
  /** ISO 8601；空 = 默认今日 */
  start: string
  end: string
  /** '' = 按窗口自动；hour | day */
  grain: string
}

export const USAGE_FILTER_DEFAULTS: UsageFilters = {
  platform: '',
  key: '',
  model: '',
  account: '',
  endpoint: '',
  start: '',
  end: '',
  grain: '',
}

export const DAY_MS = 24 * 60 * 60 * 1000
/** 北京时间固定 UTC+8（后端 usage_logs 存 UTC，展示与自然日都按北京）。 */
const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000

/** 北京时间当天 00:00 的毫秒时间戳（与账号池「今日已用」同口径）。 */
export function cnDayStart(ts: number): number {
  const shifted = ts + BEIJING_OFFSET_MS
  const floored = Math.floor(shifted / DAY_MS) * DAY_MS
  return floored - BEIJING_OFFSET_MS
}

function pad2(value: number): string {
  return String(value).padStart(2, '0')
}

export interface UsageWindow {
  startMs: number
  endMs: number
  startIso: string
  endIso: string
  /** 窗口跨越的天数（≥1），用于默认粒度和 /options 的 days 兜底。 */
  days: number
  /** 用户是否显式选过时间范围（空 = 默认今日）。 */
  explicit: boolean
  /** chips / 卡片说明里的人话。 */
  label: string
}

/** 时间窗：显式 start/end 优先，否则「今日（自然日）」到当前。 */
export function resolveUsageWindow(
  filters: Pick<UsageFilters, 'start' | 'end'>,
  now = Date.now(),
  translate?: Translate,
): UsageWindow {
  const start = filters.start ? Date.parse(filters.start) : Number.NaN
  const end = filters.end ? Date.parse(filters.end) : Number.NaN
  const hasStart = Number.isFinite(start)
  const hasEnd = Number.isFinite(end)
  const explicit = hasStart || hasEnd
  const startMs = hasStart ? start : cnDayStart(now)
  const endMs = Math.max(startMs, hasEnd ? end : now)
  const days = Math.max(1, Math.ceil((endMs - startMs) / DAY_MS))
  return {
    startMs,
    endMs,
    startIso: new Date(startMs).toISOString(),
    endIso: new Date(endMs).toISOString(),
    days,
    explicit,
    label: describeWindow(startMs, endMs, explicit, translate),
  }
}

/** 时间窗人话：今日（自然日）/ 2026-11-23 / 2026-11-20 ~ 2026-11-23。 */
export function describeWindow(
  startMs: number,
  endMs: number,
  explicit: boolean,
  translate?: Translate,
): string {
  if (!explicit) return tr(translate, 'usage.window.today')
  const a = beijingParts(new Date(startMs))
  const b = beijingParts(new Date(endMs))
  const sameDay = a.y === b.y && a.mo === b.mo && a.d === b.d
  const head = a.y + '-' + pad2(a.mo) + '-' + pad2(a.d)
  if (sameDay && a.h === 0 && a.mi === 0) return head
  if (sameDay) return head + ' ' + pad2(a.h) + ':' + pad2(a.mi) + ' ~ ' + pad2(b.h) + ':' + pad2(b.mi)
  return head + ' ~ ' + b.y + '-' + pad2(b.mo) + '-' + pad2(b.d)
}

/** 粒度：用户显式选择优先；否则窗口 > 3 天自动按天（小时桶会挤成一条线）。 */
export function normalizeGrain(grain: string, days: number): UsageGrain {
  if (grain === 'hour' || grain === 'day') return grain
  return days > 3 ? 'day' : 'hour'
}

function toId(value: string): number | null {
  const n = Number(value)
  return Number.isInteger(n) && n > 0 ? n : null
}

/** 表单筛选 → 接口参数（默认窗口会把「北京今天 00:00」显式发给后端）。 */
export function toUsageParams(
  filters: UsageFilters,
  options: { refresh?: boolean; now?: number } = {},
): UsageQueryParams {
  const win = resolveUsageWindow(filters, options.now ?? Date.now())
  return {
    granularity: normalizeGrain(filters.grain, win.days),
    start: filters.start || win.startIso,
    // 只有用户选了结束时间才发 end：默认窗口要「到现在」，不能钉死一个过去时刻。
    end: filters.end || undefined,
    key_id: toId(filters.key),
    account_id: toId(filters.account),
    model: filters.model || undefined,
    use_case: filters.endpoint || undefined,
    platform: filters.platform || undefined,
    refresh: options.refresh ? true : undefined,
  }
}

/** 切换时间窗后，已选值若不在新选项里就回落「全部」（旧版 keep() 的逻辑）。 */
export function reconcileFilters(
  filters: UsageFilters,
  options: UsageOptionsPayload | null | undefined,
): UsageFilters {
  if (!options) return filters
  const has = (list: Set<string>, value: string): boolean => !value || list.has(value)
  const keyIds = new Set((options.keys || []).map((item) => String(item.id)))
  const models = new Set((options.models || []).map((item) => String(item.value)))
  const accounts = new Set((options.accounts || []).map((item) => String(item.id)))
  const cases = new Set((options.use_cases || []).map((item) => String(item)))
  const platforms = new Set((options.platforms || []).map((item) => String(item.value)))
  return {
    ...filters,
    key: has(keyIds, filters.key) ? filters.key : '',
    model: has(models, filters.model) ? filters.model : '',
    account: has(accounts, filters.account) ? filters.account : '',
    endpoint: has(cases, filters.endpoint) ? filters.endpoint : '',
    platform: has(platforms, filters.platform) ? filters.platform : '',
  }
}

/* -------------------------------------------------------------------------- */
/* 展示数据（结构与组件 props 兼容，但不 import 组件）                          */
/* -------------------------------------------------------------------------- */

export type UsageBarTone = 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'
export type UsageStatTone = 'default' | 'brand' | 'ok' | 'warn' | 'danger' | 'muted'

export interface UsageBarRow {
  label: string
  value: number
  tone?: UsageBarTone
}

export interface UsageStat {
  label: string
  value: string | number
  unit?: string
  tone?: UsageStatTone
  foot?: string
  help?: string
}

export interface UsageChip {
  key: string
  label: string
  value?: string
}

/** 分布图口径：有任意积分就按积分，否则按（成功）请求数。 */
export function metricOf(
  rows: Array<{ label?: string; credits?: number; requests?: number }> | null | undefined,
): 'credits' | 'requests' {
  const list = rows || []
  return list.some((row) => Number(row.credits) > 0) ? 'credits' : 'requests'
}

/** BarList 的最小输入（by_result 只有 label/requests，by_account 还有 account_id）。 */
export interface UsageBarSource {
  label: string
  requests?: number
  credits?: number
  others?: boolean
}

/** 分布行 → BarList 项（过滤全 0 行；「其他 N 个账号」用 neutral）。 */
export function toBarItems(
  rows: UsageBarSource[] | null | undefined,
  metric: 'credits' | 'requests',
  translate?: Translate,
): UsageBarRow[] {
  const list = rows || []
  return list
    .map((row) => {
      const value = metric === 'credits' ? Number(row.credits) || 0 : Number(row.requests) || 0
      const others = Boolean((row as UsageAccountRow).others)
      return {
        label: String(row.label || tr(translate, 'usage.bar.empty')),
        value,
        tone: (others ? 'neutral' : 'brand') as UsageBarTone,
      }
    })
    .filter((row) => row.value > 0)
}

/** 概览 → 6 个统计格（成功口径在 help 里写明）。 */
export function toStatItems(
  overview: UsageOverview | null | undefined,
  translate?: Translate,
): UsageStat[] {
  const o = overview
  if (!o) {
    return [
      { label: tr(translate, 'usage.stat.requests'), value: '—' },
      { label: tr(translate, 'usage.stat.success'), value: '—' },
      { label: tr(translate, 'usage.stat.failed'), value: '—' },
      { label: tr(translate, 'usage.stat.tokenIn'), value: '—' },
      { label: tr(translate, 'usage.stat.tokenOut'), value: '—' },
      { label: tr(translate, 'usage.stat.credits'), value: '—' },
    ]
  }
  const requests = Number(o.requests) || 0
  const ok = Number(o.ok_requests) || 0
  const failed = Number(o.failed_requests) || 0
  const okRate = requests ? ok / requests : 0
  const failRate = requests ? failed / requests : 0
  const avgEach = requests ? (Number(o.credits) || 0) / requests : 0
  return [
    {
      label: tr(translate, 'usage.stat.requests'),
      value: fmtInt(requests),
      foot: durationState(o.avg_latency_ms) !== 'none'
        ? tr(translate, 'usage.stat.requestsFoot', { value: avgDurationText(o.avg_latency_ms, translate) })
        : tr(translate, 'usage.stat.requestsFootNone'),
      help: tr(translate, 'usage.stat.requestsHelp'),
    },
    {
      label: tr(translate, 'usage.stat.success'),
      value: fmtInt(ok),
      tone: 'ok',
      foot: tr(translate, 'usage.stat.successFoot', { rate: fmtPct(okRate) }),
      help: tr(translate, 'usage.stat.successHelp'),
    },
    {
      label: tr(translate, 'usage.stat.failed'),
      value: fmtInt(failed),
      tone: failed > 0 ? 'danger' : 'muted',
      foot: tr(translate, 'usage.stat.failedFoot', { rate: fmtPct(failRate) }),
      help: tr(translate, 'usage.stat.failedHelp'),
    },
    {
      label: tr(translate, 'usage.stat.tokenIn'),
      value: fmtCompact(o.prompt_tokens),
      foot: tr(translate, 'usage.stat.tokenInFoot', { value: fmtCompact(o.cached_tokens) }),
      help: tr(translate, 'usage.stat.tokenInHelp'),
    },
    {
      label: tr(translate, 'usage.stat.tokenOut'),
      value: fmtCompact(o.completion_tokens),
      foot: tr(translate, 'usage.stat.tokenOutFoot', { value: fmtCompact(o.total_tokens) }),
    },
    {
      label: tr(translate, 'usage.stat.credits'),
      value: fmtCredit(o.credits),
      foot: tr(translate, 'usage.stat.creditsFoot', {
        value: requests ? fmtCredit(avgEach) : '—',
      }),
    },
  ]
}

/** KpiCard 的输入（与 10-visual-refresh §3 同形，不 import 组件）。 */
export interface UsageKpi {
  key: string
  label: string
  value: string
  hint?: string
  help?: string
  tone?: 'brand' | 'ok' | 'warn' | 'danger' | 'neutral'
  spark?: number[]
}

/**
 * 6 格统计 → KpiCard（与 toStatItems 同口径），并按趋势桶带上迷你折线：
 * 请求 / 成功用每桶成功请求数（后端 trend 只统计成功请求）、输入 / 输出用每桶 token、积分用每桶积分。
 */
export function toUsageKpis(
  overview: UsageOverview | null | undefined,
  trend: UsageTrendPoint[] | null | undefined,
  translate?: Translate,
): UsageKpi[] {
  const items = toStatItems(overview, translate)
  const rows = trend || []
  const pick = (fn: (row: UsageTrendPoint) => number): number[] | undefined =>
    rows.length > 1 ? rows.map((row) => Number(fn(row)) || 0) : undefined
  const keys = ['requests', 'success', 'failed', 'tokenIn', 'tokenOut', 'credits'] as const
  const sparks: Record<string, number[] | undefined> = {
    requests: pick((row) => row.requests),
    tokenIn: pick((row) => row.prompt_tokens),
    tokenOut: pick((row) => row.completion_tokens),
    credits: pick((row) => row.credits),
  }
  return items.map((item, index) => {
    const key = keys[index] || String(index)
    const tone: UsageKpi['tone'] =
      item.tone === 'ok' || item.tone === 'danger' || item.tone === 'warn' || item.tone === 'brand'
        ? item.tone
        : item.tone === 'muted'
          ? 'neutral'
          : 'brand'
    return {
      key,
      label: item.label,
      value: String(item.value),
      hint: item.foot,
      help: item.help,
      tone,
      spark: sparks[key],
    }
  })
}

/** 页头口径徽章：没选平台 = 全网关；选了 = 该平台名（后端只认显式 ?platform=）。 */
export function usageScopeLabel(
  filters: Pick<UsageFilters, 'platform'>,
  options: UsageOptionsPayload | null | undefined,
  translate?: Translate,
): string {
  if (!filters.platform) return tr(translate, 'usage.scope.gateway')
  const hit = (options?.platforms || []).find((item) => String(item.value) === filters.platform)
  return hit?.label || filters.platform
}

/**
 * 趋势 x 轴标签：小时桶按**本地时区**显示（后端桶键是 UTC，"2026-11-23 10:00" 在 UTC+8 下是
 * "11-23 18:00"）；天桶 "2026-11-23" → "11-23"（没有时刻，无法换算）。
 *
 * 第 2 轮审查 P0-13：筛选是今天 00:00–23:26，横轴却是 16:00 → 18:00，就是把 UTC 当本地。
 */
export function chartBucketLabel(bucket: string, grain: UsageGrain): string {
  const text = String(bucket || '')
  if (!/^\d{4}-/.test(text)) return text
  const cut = text.slice(5)
  if (grain === 'day') return cut.slice(0, 5)
  const parts = bucketLocalParts(text)
  if (!parts) return cut
  return pad2(parts.mo) + '-' + pad2(parts.d) + ' ' + pad2(parts.h) + ':' + pad2(parts.mi)
}

/**
 * Token 趋势 → ECharts option（输入 / 输出两条折线，**不堆叠**）。
 *
 * 第 2 轮审查 P0-13：原来两条线 stack 在一起，最上面那条（输出色）的高度其实是「输入 + 输出」，
 * 看上去就是「青色线却是输入的量级」—— 系列颜色和图例对不上；输出又比输入小 100 倍，
 * 贴着零看不见。现在：不堆叠；量级相差 ≥8 倍时输出走右轴（needsDualAxis）。
 */
export function toTrendOption(
  trend: UsageTrendPoint[] | null | undefined,
  grain: UsageGrain,
  translate?: Translate,
): Record<string, unknown> {
  const rows = trend || []
  const labels = rows.map((row) => chartBucketLabel(row.bucket, grain))
  const input = rows.map((row) => Number(row.prompt_tokens) || 0)
  const output = rows.map((row) => Number(row.completion_tokens) || 0)
  const dual = needsDualAxis(input, output)
  const inputName = tr(translate, 'usage.legend.input')
  const outputName = tr(translate, 'usage.legend.output')
  const axisLabel = { formatter: (value: number) => fmtCompact(value) }
  return {
    tooltip: { trigger: 'axis' },
    legend: {
      data: [inputName, outputName],
      top: 0,
      right: 0,
    },
    grid: { left: 8, right: 8, top: 36, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      boundaryGap: false,
      axisLabel: { hideOverlap: true },
    },
    yAxis: dual
      ? [
          { type: 'value', name: inputName, axisLabel },
          { type: 'value', name: outputName, axisLabel, splitLine: { show: false } },
        ]
      : { type: 'value', axisLabel },
    series: [
      { name: inputName, type: 'line', yAxisIndex: 0, data: input },
      { name: outputName, type: 'line', yAxisIndex: dual ? 1 : 0, data: output, areaStyle: null },
    ],
    // tooltip 标题：完整本地时间（小时桶）/ 原样日期（天桶）。
    [TOOLTIP_TITLES_KEY]: rows.map((row) => bucketTooltipTitle(row.bucket)),
  }
}

/** 与 api/stats.ts:needsDualAxis 同口径（这里复制一份，避免 usage → stats 的依赖）。 */
export function needsDualAxis(a: number[], b: number[], ratio = 8): boolean {
  const peak = (values: number[]): number => values.reduce((max, value) => Math.max(max, Number(value) || 0), 0)
  const pa = peak(a)
  const pb = peak(b)
  if (pa <= 0 || pb <= 0) return false
  return Math.max(pa, pb) / Math.min(pa, pb) >= ratio
}

/** FilterBar 的汇总文字（与旧版「已筛选」那行同口径）。 */
export function usageSummary(
  payload: UsagePayload | null | undefined,
  translate?: Translate,
): string {
  if (!payload) return ''
  const o = payload.overview
  return tr(translate, 'usage.summary', {
    requests: fmtInt(o.requests),
    ok: fmtInt(o.ok_requests),
    failed: fmtInt(o.failed_requests),
    credits: fmtCredit(o.credits),
  })
}

export function usageHasData(payload: UsagePayload | null | undefined): boolean {
  if (!payload) return false
  return (Number(payload.overview?.requests) || 0) > 0 || (payload.trend || []).length > 0
}

/** 已生效筛选的 chips（时间窗永远是第一项）。 */
export function usageChips(
  filters: UsageFilters,
  options: UsageOptionsPayload | null | undefined,
  window: UsageWindow,
  translate?: Translate,
): UsageChip[] {
  const chips: UsageChip[] = [
    { key: 'range', label: tr(translate, 'usage.chip.time'), value: window.label },
  ]
  const keyName = (options?.keys || []).find((item) => String(item.id) === filters.key)?.name
  const modelLabel = (options?.models || []).find((item) => String(item.value) === filters.model)?.label
  const accountName = (options?.accounts || []).find((item) => String(item.id) === filters.account)?.name
  const platformLabel = (options?.platforms || []).find((item) => String(item.value) === filters.platform)?.label
  if (filters.platform) {
    chips.push({
      key: 'platform',
      label: tr(translate, 'usage.chip.platform'),
      value: platformLabel || filters.platform,
    })
  }
  if (filters.key) {
    chips.push({ key: 'key', label: tr(translate, 'usage.chip.key'), value: keyName || '#' + filters.key })
  }
  if (filters.model) {
    chips.push({
      key: 'model',
      label: tr(translate, 'usage.chip.model'),
      value: modelLabel || filters.model,
    })
  }
  if (filters.account) {
    chips.push({
      key: 'account',
      label: tr(translate, 'usage.chip.account'),
      value: accountName || '#' + filters.account,
    })
  }
  if (filters.endpoint) {
    chips.push({
      key: 'endpoint',
      label: tr(translate, 'usage.chip.endpoint'),
      value: filters.endpoint,
    })
  }
  return chips
}

/* -------------------------------------------------------------------------- */
/* /logs 深链                                                                  */
/* -------------------------------------------------------------------------- */

export type UsageLogKind = 'model' | 'key' | 'account' | 'result'

/**
 * 分布行 → /logs 的 query。
 *
 * 返回 null 表示这一行没有可跳的维度（端点没有 use_case 参数、`#id` 的 Key 查不到、
 * 「其他 N 个账号」不是单一账号），页面据此不做跳转。
 */
export function logsQueryFor(
  kind: UsageLogKind,
  row: UsageDistRow | UsageModelRow | UsageAccountRow | UsageResultRow | null | undefined,
  filters: Pick<UsageFilters, 'start' | 'end'>,
  now = Date.now(),
): Record<string, string> | null {
  if (!row) return null
  const query: Record<string, string> = {}
  if (kind === 'model') {
    const model = String((row as UsageModelRow).model || '')
    if (!model) return null
    query.model = model
  } else if (kind === 'key') {
    const label = String(row.label || '')
    // 后端查不到 Key 名时会回落 '#<id>'，/api/logs 按名称模糊查，这种值必然空结果。
    if (!label || label.startsWith('#')) return null
    query.key = label
  } else if (kind === 'account') {
    const account = row as UsageAccountRow
    if (account.account_id === null || account.account_id === undefined || account.others) return null
    if (!account.label) return null
    query.account = String(account.label)
  } else {
    // 后端 by_result 的 label 是中文原值（成功 / 失败…），也兼容英文 / 状态码键，切英文界面不误判。
    const label = String(row.label || '').trim().toLowerCase()
    query.result = ['成功', 'success', 'ok', 'succeeded'].includes(label) ? 'success' : 'error'
  }
  const win = resolveUsageWindow(filters, now)
  query.start = win.startIso
  // 与旧版一致：只有用户显式给了结束时间才带 end；默认「今日」窗口要到当前。
  if (filters.end) query.end = win.endIso
  return query
}

/* -------------------------------------------------------------------------- */
/* 导出                                                                        */
/* -------------------------------------------------------------------------- */

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value)
  return /[",\n\r]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text
}

function csvRow(cells: Array<string | number>): string {
  return cells.map(csvCell).join(',')
}

/** 当前聚合 → CSV（后端没有 usage 导出接口，明细导出在 /logs）。 */
export function usageCsv(
  payload: UsagePayload,
  meta: { windowLabel: string; grain: UsageGrain },
  translate?: Translate,
): string {
  const lines: string[] = []
  const o = payload.overview
  const t = (key: MessageKey): string => tr(translate, key)
  lines.push(csvRow(['# ' + t('page.usage.title')]))
  lines.push(
    csvRow([
      t('usage.csv.range'),
      meta.windowLabel,
      t('usage.csv.grain'),
      meta.grain === 'day' ? t('page.usage.grain.day') : t('page.usage.grain.hour'),
    ]),
  )
  lines.push('')
  lines.push(csvRow(['# ' + t('usage.csv.sectionOverview')]))
  lines.push(csvRow([t('usage.csv.metrics'), t('usage.csv.value')]))
  lines.push(csvRow([t('usage.stat.requests'), o.requests]))
  lines.push(csvRow([t('usage.stat.success'), o.ok_requests]))
  lines.push(csvRow([t('usage.stat.failed'), o.failed_requests]))
  lines.push(csvRow([t('usage.stat.tokenIn'), o.prompt_tokens]))
  lines.push(csvRow([t('usage.stat.tokenOut'), o.completion_tokens]))
  lines.push(csvRow([t('usage.csv.tokenTotal'), o.total_tokens]))
  lines.push(csvRow([t('usage.csv.tokenCached'), o.cached_tokens]))
  lines.push(csvRow([t('usage.stat.credits'), o.credits]))
  lines.push(csvRow([t('usage.csv.latency'), o.avg_latency_ms]))
  lines.push('')
  const section = (title: string, header: string[], rows: Array<Array<string | number>>) => {
    lines.push(csvRow(['# ' + title]))
    lines.push(csvRow(header))
    if (!rows.length) lines.push(csvRow([t('usage.csv.none')]))
    for (const row of rows) lines.push(csvRow(row))
    lines.push('')
  }
  const distHeader = (first: string): string[] => [
    first,
    t('usage.csv.requestsOk'),
    t('usage.csv.token'),
    t('usage.stat.credits'),
  ]
  section(
    t('page.usage.card.models'),
    [t('usage.csv.model'), t('usage.csv.platform'), t('usage.csv.requestsOk'), t('usage.csv.token'), t('usage.stat.credits')],
    (payload.by_model || []).map((row) => [row.label, row.platform_label || row.platform, row.requests, row.tokens, row.credits]),
  )
  section(
    t('page.usage.card.keys'),
    distHeader(t('usage.csv.key')),
    (payload.by_key || []).map((row) => [row.label, row.requests, row.tokens, row.credits]),
  )
  section(
    t('page.usage.card.endpoints'),
    distHeader(t('usage.csv.endpoint')),
    (payload.by_endpoint || []).map((row) => [row.label, row.requests, row.tokens, row.credits]),
  )
  section(
    t('page.usage.card.accounts'),
    distHeader(t('usage.csv.account')),
    (payload.by_account || []).map((row) => [row.label, row.requests, row.tokens, row.credits]),
  )
  section(
    t('page.usage.card.results'),
    [t('usage.csv.result'), t('usage.csv.requestsAll')],
    (payload.by_result || []).map((row) => [row.label, row.requests]),
  )
  section(
    t('usage.csv.sectionTrend'),
    [
      t('usage.csv.bucket'),
      t('usage.legend.input'),
      t('usage.legend.output'),
      t('stats.legend.cached'),
      t('usage.stat.requests'),
      t('usage.stat.credits'),
    ],
    (payload.trend || []).map((row) => [row.bucket, row.prompt_tokens, row.completion_tokens, row.cached_tokens, row.requests, row.credits]),
  )
  return lines.join('\n')
}

/** 导出文件名（北京时间）。 */
export function usageFilename(now = Date.now()): string {
  const p = beijingParts(new Date(now))
  return (
    'miraihub-usage-' + p.y + pad2(p.mo) + pad2(p.d) + '-' + pad2(p.h) + pad2(p.mi) + pad2(p.s) + '.csv'
  )
}

/** 触发浏览器保存（与此前 api/maintenance.ts 的做法一致）。 */
export function saveText(text: string, filename: string): void {
  if (typeof document === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return
  const blob = new Blob(['\ufeff' + text], { type: 'text/csv;charset=utf-8' })
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(href), 0)
}