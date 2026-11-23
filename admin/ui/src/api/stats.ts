/**
 * 数据看板（/stats）的接口与数据转换。
 *
 * 契约：docs/ui-v2/contracts/stats.md（本次只在末尾追加「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-a.md P3
 * 后端：
 *   GET /api/stats/dashboard → admin/routers/stats.py:dashboard_stats / _dashboard_agg（880 行起）
 *       一次拿齐 KPI、平台、模型、按模型的时间序列、排行、失败分类。
 *   GET /api/stats/usage     → admin/routers/stats.py:usage_stats / _usage_aggregates（356 行起）
 *       只补两样看板接口没有的数据：① 按桶的 prompt/completion/cached（Token 堆叠）
 *       ② by_key（Key 排行）。
 *
 * 口径（与 dashboard.js / 后端注释一致，测试里逐条验证）：
 *   · summary.requests 含失败；successes = error_kind in ('', 'success')；credits/tokens 只算成功；
 *   · success_rate 无请求时为 null（显示 —，不是 0%）；avg_latency_ms / avg_ttfb_ms 只统计成功请求；
 *   · rpm / tpm 的分母是窗口分钟数（window.minutes），不是固定 1440；
 *   · trend.series[].requests 是「按模型、按桶的请求数」（dashboard.js 的折线口径），
 *     后端**没有**按桶的成功/失败拆分 —— 成功/失败只在 summary 里，页面放在 StatStrip 脚注；
 *   · distribution.series 每条自带 metric（credits 或 tokens），不同模型可能不同；
 *   · platforms / ranking 的 success_rate 含失败，share 是 requests / summary.requests；
 *   · /api/stats/usage 的 trend 只统计成功请求（_ok_filter），cached_tokens 是 prompt 的子集。
 *
 * 页面只做摆放：格式化、分桶标签、图 option、排行条数据全部在这里的纯函数完成。
 */
import { http } from './http'
import { fmtCompact, fmtCredit, fmtDuration, fmtInt, fmtPct } from '@/utils/format'
import { t as defaultTranslate, type Translate } from '@/i18n'

/**
 * 文案取当前语言；页面把 useI18n().t 传进来时，computed 会随语言切换重算。
 * 不传时用默认字典（中文），单测与其它页面无需关心 i18n。
 */
function tr(
  translate: Translate | undefined,
  key: Parameters<Translate>[0],
  params?: Record<string, string | number>,
): string {
  return (translate ?? defaultTranslate)(key, params)
}

/* -------------------------------------------------------------------------- */
/* /api/stats/dashboard                                                        */
/* -------------------------------------------------------------------------- */

export interface StatsWindow {
  days: number
  granularity: string
  start: string
  end: string
  minutes: number
}

export interface StatsSummary {
  requests: number
  successes: number
  failures: number
  /** 0–1；无请求时 null。 */
  success_rate: number | null
  credits: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  /** cached_tokens / prompt_tokens；无输入时 null。 */
  cache_hit_rate: number | null
  rpm: number
  tpm: number
  avg_latency_ms: number
  avg_ttfb_ms: number
  throughput_tps: number
  window_minutes: number
}

export interface StatsModelRow {
  model: string
  label: string
  /** 只在一个平台出现时是平台 id，多平台时为 ""。 */
  platform?: string
  platforms?: string[]
  requests: number
  successes: number
  failures: number
  success_rate: number | null
  tokens: number
  credits: number
  avg_latency_ms: number
  avg_ttfb_ms: number
  tps?: number
}

export interface StatsPlatformRow {
  value: string
  label: string
  requests: number
  success_rate: number | null
  tokens: number
  credits: number
}

export interface StatsDistributionSeries {
  name: string
  metric: 'credits' | 'tokens' | string
  values: number[]
}

export interface StatsTrendSeries {
  name: string
  requests: number[]
  tokens: number[]
  credits: number[]
}

export interface StatsErrorRow {
  kind: string
  count: number
}

export interface StatsRankingRow extends StatsModelRow {
  /** requests / summary.requests，0–1。 */
  share: number
}

export interface StatsDashboardPayload {
  window: StatsWindow
  filters: { platform: string; model: string }
  platform_label: string
  summary: StatsSummary
  models: StatsModelRow[]
  platforms: StatsPlatformRow[]
  distribution: { buckets: string[]; series: StatsDistributionSeries[] }
  trend: { buckets: string[]; total_requests: number[]; series: StatsTrendSeries[] }
  ranking: StatsRankingRow[]
  errors: StatsErrorRow[]
  /** rollup = 小时汇总表；raw = 后端降级到原表。 */
  source?: string
}

/* -------------------------------------------------------------------------- */
/* /api/stats/usage（只看用到的字段）                                            */
/* -------------------------------------------------------------------------- */

export interface StatsUsageTrendBucket {
  bucket: string
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  credits: number
  /** 成功请求数（后端 _ok_filter）。 */
  requests: number
}

export interface StatsUsageOverview {
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

/** by_model / by_key / by_endpoint / by_account 共用的分布行。 */
export interface StatsUsageDistRow {
  label: string
  requests: number
  tokens: number
  credits: number
}

export interface StatsUsagePayload {
  range_days: number | null
  granularity: string
  platform_label: string
  overview: StatsUsageOverview
  platforms: Array<{ value: string; label: string; requests: number; tokens: number; credits: number }>
  by_model: Array<StatsUsageDistRow & { model: string; platform: string; platform_label: string }>
  by_key: StatsUsageDistRow[]
  by_endpoint: StatsUsageDistRow[]
  by_account: StatsUsageDistRow[]
  by_result: Array<{ label: string; requests: number }>
  trend: StatsUsageTrendBucket[]
}

/* -------------------------------------------------------------------------- */
/* 请求                                                                        */
/* -------------------------------------------------------------------------- */

export const STATS_DAYS = [1, 7, 30] as const
export type StatsDays = (typeof STATS_DAYS)[number]

export const STATS_DAYS_LABEL: Record<number, string> = {
  1: '今日',
  7: '7 天',
  30: '30 天',
}

export type StatsGranularity = 'hour' | 'day'

export const STATS_GRAIN_LABEL: Record<StatsGranularity, string> = {
  hour: '按小时',
  day: '按天',
}

/** days 只认 1 / 7 / 30，其它值回落 1（与旧版 Segmented 的三个按钮一致）。 */
export function normalizeDays(value: unknown): StatsDays {
  const n = Number(value)
  return n === 7 ? 7 : n === 30 ? 30 : 1
}

/** 粒度只认 hour / day；days > 7 时强制 day（后端同样会降级）。 */
export function normalizeGranularity(value: unknown, days: number): StatsGranularity {
  const grain: StatsGranularity = String(value || '').trim().toLowerCase() === 'day' ? 'day' : 'hour'
  if (days > 7) return 'day'
  return grain
}

export interface StatsQuery {
  days: number
  granularity: StatsGranularity
  /** 显式平台筛选（本页暂不开放；保留参数便于从别处带 query 进来）。 */
  platform?: string
  model?: string
  top_models?: number
}

function statsQuery(query: StatsQuery): Record<string, string | number> {
  const out: Record<string, string | number> = {
    days: query.days,
    granularity: query.granularity,
  }
  if (query.platform) out.platform = query.platform
  if (query.model) out.model = query.model
  if (query.top_models) out.top_models = query.top_models
  return out
}

/** 主接口：一屏的 KPI / 排行 / 时间序列。 */
export function getStatsDashboard(query: StatsQuery, signal?: AbortSignal): Promise<StatsDashboardPayload> {
  return http<StatsDashboardPayload>('GET', '/api/stats/dashboard', {
    query: statsQuery(query),
    signal,
  })
}

/** 补充接口：Token 构成（输入/输出/缓存）与 Key 维度。 */
export function getStatsUsage(query: StatsQuery, signal?: AbortSignal): Promise<StatsUsagePayload> {
  return http<StatsUsagePayload>('GET', '/api/stats/usage', {
    query: { days: query.days, granularity: query.granularity },
    signal,
  })
}

/* -------------------------------------------------------------------------- */
/* 纯函数：格式化 / 分桶 / 图表 option / 排行                                    */
/* -------------------------------------------------------------------------- */

/** 与 StatItem 结构一致（避免 api 层 import 组件）。 */
export interface StatsStatItem {
  label: string
  value: string
  unit?: string
  tone?: 'default' | 'brand' | 'ok' | 'warn' | 'danger' | 'muted'
  foot?: string
  help?: string
}

export type StatsTone = 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'

/**
 * 成功率语气：与 dashboard.js:rateClass 一致（≥99.5% 绿、≥97% 黄、其余红）。
 * 无样本（null）用 neutral，不冒充 0%。
 */
export function rateTone(
  rate: number | null | undefined,
): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (rate === null || rate === undefined || !Number.isFinite(Number(rate))) return 'neutral'
  const value = Number(rate)
  if (value >= 0.995) return 'ok'
  if (value >= 0.97) return 'warn'
  return 'danger'
}

/** StatItem 的语气不含 neutral（无样本用 muted）。 */
export function statToneOf(rate: number | null | undefined): 'ok' | 'warn' | 'danger' | 'muted' {
  const tone = rateTone(rate)
  return tone === 'neutral' ? 'muted' : tone
}

/**
 * 时间桶标签：小时桶 "2026-11-23 15:00" → "15:00"；
 * 天桶 "2026-11-23" → "11/23"（与 dashboard.js:bucketLabel 同口径）。
 */
export function bucketLabel(key: string): string {
  const text = String(key || '')
  if (text.length >= 13) return text.slice(11, 16)
  return text.slice(5).replace('-', '/')
}

/** StatStrip 的 5 格：请求 / 成功率 / Token 合计 / 积分 / 平均耗时（设计稿 P3）。 */
export function toSummaryItems(
  summary: StatsSummary | null | undefined,
  translate?: Translate,
): StatsStatItem[] {
  if (!summary) {
    return [
      { label: tr(translate, 'stats.summary.requests'), value: '—' },
      { label: tr(translate, 'stats.summary.successRate'), value: '—', tone: 'muted' },
      { label: tr(translate, 'stats.summary.tokens'), value: '—' },
      { label: tr(translate, 'stats.summary.credits'), value: '—' },
      { label: tr(translate, 'stats.summary.latency'), value: '—' },
    ]
  }
  const rate = summary.success_rate
  return [
    {
      label: tr(translate, 'stats.summary.requests'),
      value: fmtInt(summary.requests),
      foot: tr(translate, 'stats.summary.requestsFoot', {
        ok: fmtInt(summary.successes),
        failed: fmtInt(summary.failures),
      }),
      help: tr(translate, 'stats.summary.requestsHelp', {
        rpm: fmtCompact(summary.rpm),
        tpm: fmtCompact(summary.tpm),
      }),
    },
    {
      label: tr(translate, 'stats.summary.successRate'),
      value: rate === null || rate === undefined ? '—' : fmtPct(rate, 1),
      tone: statToneOf(rate),
      foot: tr(translate, 'stats.summary.successFoot', { count: fmtInt(summary.successes) }),
    },
    {
      label: tr(translate, 'stats.summary.tokens'),
      value: fmtCompact(summary.total_tokens),
      foot: tr(translate, 'stats.summary.tokensFoot', {
        input: fmtCompact(summary.prompt_tokens),
        output: fmtCompact(summary.completion_tokens),
      }),
    },
    {
      label: tr(translate, 'stats.summary.credits'),
      value: fmtCredit(summary.credits),
      foot:
        summary.cache_hit_rate === null || summary.cache_hit_rate === undefined
          ? tr(translate, 'stats.summary.cacheNone')
          : tr(translate, 'stats.summary.cacheFoot', {
              rate: fmtPct(summary.cache_hit_rate, 1),
            }),
    },
    {
      label: tr(translate, 'stats.summary.latency'),
      value: fmtDuration(summary.avg_latency_ms),
      foot: tr(translate, 'stats.summary.latencyFoot', {
        ttfb: fmtDuration(summary.avg_ttfb_ms),
        tps: Number(summary.throughput_tps || 0).toFixed(1),
      }),
    },
  ]
}

export interface TrendSeriesData {
  name: string
  values: number[]
}

export interface RequestTrendData {
  buckets: string[]
  labels: string[]
  series: TrendSeriesData[]
  /** 「全部请求」一条线（trend.total_requests，含 top N 之外的模型）。 */
  total: number[]
}

/**
 * 请求趋势：全部请求 + 每个模型各一条线（dashboard.js 的「调用趋势」口径）。
 *
 * 后端没有按桶的成功/失败拆分（successes 只有窗口汇总），
 * 所以成功/失败只能放在 StatStrip 脚注里（实现对照里标注了这条差异）。
 */
export function buildRequestTrend(
  payload: StatsDashboardPayload | null | undefined,
  translate?: Translate,
): RequestTrendData {
  const trend = payload?.trend
  const buckets = Array.isArray(trend?.buckets) ? trend?.buckets ?? [] : []
  const total = Array.isArray(trend?.total_requests) ? (trend?.total_requests ?? []).map((n) => Number(n) || 0) : []
  const series: TrendSeriesData[] = (trend?.series ?? []).map((item) => ({
    name: String(item.name || tr(translate, 'stats.trend.unknownModel')),
    values: Array.isArray(item.requests) ? item.requests.map((n) => Number(n) || 0) : [],
  }))
  return { buckets, labels: buckets.map(bucketLabel), series, total }
}

/** 请求趋势的 ECharts option（页面再套 applyLineDefaults）。 */
export function requestTrendOption(
  payload: StatsDashboardPayload | null | undefined,
  translate?: Translate,
): Record<string, unknown> {
  const data = buildRequestTrend(payload, translate)
  const series: Array<Record<string, unknown>> = []
  if (data.total.length) {
    series.push({
      name: tr(translate, 'stats.trend.all'),
      type: 'line',
      data: data.total,
      lineStyle: { width: 2.4 },
      z: 3,
    })
  }
  for (const item of data.series) {
    series.push({ name: item.name, type: 'line', data: item.values })
  }
  return {
    legend: { type: 'scroll', data: series.map((item) => String(item.name)), top: 0 },
    xAxis: { type: 'category', boundaryGap: false, data: data.labels },
    yAxis: { type: 'value' },
    series,
  }
}

export interface TokenTrendData {
  labels: string[]
  /** 输入（已扣除缓存命中，保证堆叠合计 = Token 总量）。 */
  prompt: number[]
  output: number[]
  cached: number[]
  /** 汇总的缓存命中率（cached / prompt；无输入时 null）。 */
  cacheHitRate: number | null
}

/**
 * Token 趋势（设计稿 P3 的「输入 / 输出 / 缓存命中」堆叠柱）。
 *
 * 数据源是 /api/stats/usage 的 trend[]（按桶的 prompt/completion/cached）。
 * cached_tokens 是 prompt 的子集，所以「输入」取 prompt - cached，
 * 三根堆起来刚好等于 total_tokens —— 否则堆叠高度会重复计数。
 */
export function buildTokenTrend(usage: StatsUsagePayload | null | undefined): TokenTrendData {
  const rows = Array.isArray(usage?.trend) ? usage?.trend ?? [] : []
  let promptSum = 0
  let cachedSum = 0
  const data: TokenTrendData = {
    labels: rows.map((row) => bucketLabel(String(row.bucket || ''))),
    prompt: [],
    output: [],
    cached: [],
    cacheHitRate: null,
  }
  for (const row of rows) {
    const prompt = Number(row.prompt_tokens) || 0
    const cached = Math.min(prompt, Number(row.cached_tokens) || 0)
    const output = Number(row.completion_tokens) || 0
    data.prompt.push(Math.max(0, prompt - cached))
    data.output.push(output)
    data.cached.push(cached)
    promptSum += prompt
    cachedSum += cached
  }
  data.cacheHitRate = promptSum > 0 ? cachedSum / promptSum : null
  return data
}

/** Token 趋势的 ECharts option（页面再套 applyBarDefaults）。 */
export function tokenTrendOption(
  usage: StatsUsagePayload | null | undefined,
  translate?: Translate,
): Record<string, unknown> {
  const data = buildTokenTrend(usage)
  const input = tr(translate, 'stats.legend.input')
  const output = tr(translate, 'stats.legend.output')
  const cached = tr(translate, 'stats.legend.cached')
  return {
    legend: { data: [input, output, cached], top: 0 },
    xAxis: { type: 'category', data: data.labels },
    yAxis: { type: 'value' },
    series: [
      { name: input, type: 'bar', stack: 'tokens', data: data.prompt },
      { name: output, type: 'bar', stack: 'tokens', data: data.output },
      { name: cached, type: 'bar', stack: 'tokens', data: data.cached },
    ],
  }
}

export interface StatsBarItem {
  label: string
  value: number
  tone?: StatsTone
}

/** 平台排行：按请求数（dashboard.platforms 已按 requests DESC）。 */
export function platformBarItems(
  payload: StatsDashboardPayload | null | undefined,
  translate?: Translate,
): StatsBarItem[] {
  return (payload?.platforms ?? []).map((row) => ({
    label: String(row.label || row.value || tr(translate, 'stats.bar.unknownPlatform')),
    value: Number(row.requests) || 0,
    tone: rateTone(row.success_rate),
  }))
}

/** 模型排行：用 ranking（含 top N 之外的模型，按 requests DESC）。 */
export function modelBarItems(
  payload: StatsDashboardPayload | null | undefined,
  translate?: Translate,
): StatsBarItem[] {
  const rows = payload?.ranking?.length ? payload.ranking : payload?.models ?? []
  return rows.map((row) => ({
    label: String(row.label || row.model || tr(translate, 'stats.trend.unknownModel')),
    value: Number(row.requests) || 0,
    tone: rateTone(row.success_rate),
  }))
}

/** Key 排行：dashboard 没有 Key 维度，用 /api/stats/usage 的 by_key。 */
export function keyBarItems(
  usage: StatsUsagePayload | null | undefined,
  translate?: Translate,
): StatsBarItem[] {
  return (usage?.by_key ?? []).map((row) => ({
    label: String(row.label || tr(translate, 'stats.bar.unnamedKey')),
    value: Number(row.requests) || 0,
    tone: 'brand' as StatsTone,
  }))
}

/** 卡片标题右侧的一句话（空数据时给「—」，不显示 0 冒充无数据）。 */
export function trendFootnote(
  payload: StatsDashboardPayload | null | undefined,
  translate?: Translate,
): string {
  const summary = payload?.summary
  if (!summary) return ''
  return tr(translate, 'stats.note.successFail', {
    ok: fmtInt(summary.successes),
    failed: fmtInt(summary.failures),
  })
}

export function cacheFootnote(
  usage: StatsUsagePayload | null | undefined,
  translate?: Translate,
): string {
  const data = buildTokenTrend(usage)
  if (data.cacheHitRate === null) return tr(translate, 'stats.note.cacheNone')
  return tr(translate, 'stats.note.cacheHit', { rate: fmtPct(data.cacheHitRate, 1) })
}