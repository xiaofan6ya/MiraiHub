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
import { bucketLocalParts, fmtCompact, fmtCredit, fmtDuration, fmtInt, fmtPct } from '@/utils/format'
import { TOOLTIP_TITLES_KEY, bucketTooltipTitle } from '@/components/charts/options'
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
  /** 后端口径标记：`gateway` = 全网关（不读 X-Platform）。旧后端没有这个字段。 */
  scope?: string
  /** 耗时字段单位说明（新后端：全部毫秒）。 */
  units?: Record<string, string>
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
  /**
   * 时间窗口径：`day` = 北京时间自然日（与 /api/overview 同口径）；不传 = 后端默认滚动窗口。
   * 旧后端不认这个参数时会忽略，行为退回滚动窗口。
   */
  window?: 'day' | 'rolling'
}

function statsQuery(query: StatsQuery): Record<string, string | number> {
  const out: Record<string, string | number> = {
    days: query.days,
    granularity: query.granularity,
  }
  if (query.platform) out.platform = query.platform
  if (query.model) out.model = query.model
  if (query.top_models) out.top_models = query.top_models
  if (query.window === 'day') out.window = 'day'
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
 * 时间桶标签：小时桶 "2026-11-23 15:00"（UTC）→ 本地时区 "23:00"（UTC+8）；
 * 天桶 "2026-11-23" → "11/23"（没有时刻，无法换算，原样取月日）。
 *
 * 旧实现直接截字符串 slice(11, 16)，把 UTC 桶当本地时间显示，横轴整体差 8 小时
 * （第 1 轮审查 P0-4）。解析不了的小时桶（理论上不会出现）退回截字符串。
 */
export function bucketLabel(key: string): string {
  const text = String(key || '')
  const parts = bucketLocalParts(text)
  if (parts) return String(parts.h).padStart(2, '0') + ':' + String(parts.mi).padStart(2, '0')
  if (text.length >= 13) return text.slice(11, 16)
  return text.slice(5).replace('-', '/')
}

/**
 * 平均耗时上限：超过 1 小时的「平均首字 / 平均耗时」不可能是真的。
 *
 * 第 3 轮审查 C：后端汇总表曾把平均值放大约 1000 倍（fix_plan N1，`avg_ttfb_ms = 14,345,921`），
 * 页面显示「239m 06s」。根因由后端 rollup 修复并重建汇总表；前端**不做 ÷1000**
 * （修好之后正确数据会被缩小），只把明显不可能的值显示成「数据异常」，并在控制台打一次警告。
 */
export const AVG_DURATION_MAX_MS = 60 * 60 * 1000

let durationWarned = false

/** 平均耗时是否可信：>0 且 ≤ 1 小时。0 / 负数 / 非数 = 没有样本。 */
export function durationState(ms: unknown): 'none' | 'ok' | 'anomaly' {
  const value = Number(ms)
  if (ms === null || ms === undefined || !Number.isFinite(value) || value <= 0) return 'none'
  return value > AVG_DURATION_MAX_MS ? 'anomaly' : 'ok'
}

/** 平均耗时文案：无样本 —；不可信「数据异常」；其余 fmtDuration（入参毫秒）。 */
export function avgDurationText(ms: unknown, translate?: Translate): string {
  const state = durationState(ms)
  if (state === 'none') return '—'
  if (state === 'anomaly') {
    if (!durationWarned && typeof console !== 'undefined') {
      durationWarned = true
      console.warn(
        '[stats] 平均耗时超过 1 小时（' + String(ms) + ' ms），疑似后端汇总表放大（fix_plan N1），已显示为「数据异常」',
      )
    }
    return tr(translate, 'stats.duration.anomaly')
  }
  return fmtDuration(ms)
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
      value: avgDurationText(summary.avg_latency_ms, translate),
      foot: tr(translate, 'stats.summary.latencyFoot', {
        ttfb: avgDurationText(summary.avg_ttfb_ms, translate),
        tps: Number(summary.throughput_tps || 0).toFixed(1),
      }),
    },
  ]
}

/** KpiCard 的输入（与 10-visual-refresh §3 的 props 同形，不 import 组件）。 */
export interface StatsKpi {
  key: string
  label: string
  value: string
  unit?: string
  hint?: string
  tone?: StatsTone
  spark?: number[]
}

/** 每桶 Token 合计（/api/stats/usage 的 trend，只算成功请求）。 */
export function tokenSpark(usage: StatsUsagePayload | null | undefined): number[] {
  return (Array.isArray(usage?.trend) ? usage?.trend ?? [] : []).map(
    (row) => (Number(row.prompt_tokens) || 0) + (Number(row.completion_tokens) || 0),
  )
}

/**
 * 数据看板 KPI 行（10-visual-refresh §1.4）：请求 / 成功率 / Token / 积分 / 平均耗时。
 * 与 toSummaryItems 同口径，只是换成 KpiCard 形状并带上迷你折线。
 */
export function toStatsKpis(
  payload: StatsDashboardPayload | null | undefined,
  usage: StatsUsagePayload | null | undefined,
  translate?: Translate,
): StatsKpi[] {
  const items = toSummaryItems(payload?.summary, translate)
  const summary = payload?.summary
  const requestsSpark = (payload?.trend?.total_requests ?? []).map((n) => Number(n) || 0)
  const tokens = tokenSpark(usage)
  const keys = ['requests', 'success', 'tokens', 'credits', 'latency']
  return items.map((item, index) => {
    const tone: StatsTone =
      item.tone === 'ok' || item.tone === 'warn' || item.tone === 'danger' || item.tone === 'brand'
        ? item.tone
        : 'neutral'
    const kpi: StatsKpi = {
      key: keys[index] || String(index),
      label: item.label,
      value: item.value,
      hint: item.foot,
      tone: keys[index] === 'success' ? tone : 'brand',
    }
    if (keys[index] === 'requests' && requestsSpark.length > 1) kpi.spark = requestsSpark
    if (keys[index] === 'tokens' && tokens.length > 1) kpi.spark = tokens
    if (keys[index] === 'latency' && summary && durationState(summary.avg_latency_ms) === 'anomaly') {
      kpi.tone = 'warn'
      kpi.hint = tr(translate, 'stats.duration.anomalyHint')
    }
    return kpi
  })
}

/** 失败分类（dashboard.errors）→ BarList 项；kind 为空记成「未知」。 */
export function errorBarItems(
  payload: StatsDashboardPayload | null | undefined,
  translate?: Translate,
): StatsBarItem[] {
  return (payload?.errors ?? [])
    .map((row) => ({
      label: String(row.kind || tr(translate, 'stats.bar.unknownError')),
      value: Number(row.count) || 0,
      tone: 'danger' as StatsTone,
    }))
    .filter((row) => row.value > 0)
}

/** 口径标签：dashboard 不读 X-Platform，一律是全网关（后端 `scope: "gateway"`）。 */
export function statsScopeLabel(
  payload: StatsDashboardPayload | null | undefined,
  translate?: Translate,
): string {
  const platform = String(payload?.filters?.platform || '')
  if (platform) return String(payload?.platform_label || platform)
  return tr(translate, 'stats.scope.gateway')
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
      lineStyle: { width: 2.5 },
      // 面积用 applyLineDefaults 的同色渐变（顶部 18%）；旧的 opacity .16 会叠在渐变上，几乎看不见。
      areaStyle: {},
      z: 3,
    })
  }
  // 只有一个模型、且它就是全部请求时，模型线与「全部请求」完全重合 —— 不再画（第 1 轮审查 P2）。
  const sameAsTotal = (values: number[]): boolean =>
    values.length === data.total.length && values.every((value, index) => value === data.total[index])
  const modelSeries =
    data.total.length && data.series.length === 1 && sameAsTotal(data.series[0].values) ? [] : data.series
  for (const item of modelSeries) {
    series.push({ name: item.name, type: 'line', data: item.values, areaStyle: null, lineStyle: { width: 1.5 } })
  }
  return {
    legend: { type: 'scroll', data: series.map((item) => String(item.name)), top: 0, left: 0 },
    xAxis: { type: 'category', boundaryGap: false, data: data.labels, axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value' },
    series,
    // tooltip 标题用完整本地时间（Chart.vue 渲染前剥掉这个键）。
    [TOOLTIP_TITLES_KEY]: data.buckets.map(bucketTooltipTitle),
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

/**
 * Token 趋势的 ECharts option（页面再套 applyBarDefaults）。
 *
 * v2.1：输入和输出常差 1–2 个数量级（第 2 轮审查 P0-13 / 第 1 轮 P2），
 * 堆在同一根柱子里输出贴着零看不见。改成**双轴**：
 *   · 左轴：输入 = 未命中 + 缓存命中（两段堆叠柱，合计 = prompt_tokens）；
 *   · 右轴：输出（折线）。
 * 两者量级接近（比值 < 8）时共用左轴，读数更直观。
 */
export function tokenTrendOption(
  usage: StatsUsagePayload | null | undefined,
  translate?: Translate,
): Record<string, unknown> {
  const data = buildTokenTrend(usage)
  const input = tr(translate, 'stats.legend.inputMiss')
  const output = tr(translate, 'stats.legend.output')
  const cached = tr(translate, 'stats.legend.cached')
  const dual = needsDualAxis(
    data.prompt.map((value, index) => value + (data.cached[index] || 0)),
    data.output,
  )
  return {
    legend: { data: [input, cached, output], top: 0, left: 0 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    [TOOLTIP_TITLES_KEY]: (Array.isArray(usage?.trend) ? usage?.trend ?? [] : []).map((row) =>
      bucketTooltipTitle(row.bucket),
    ),
    xAxis: { type: 'category', data: data.labels },
    yAxis: dual
      ? [
          { type: 'value', name: tr(translate, 'stats.axis.input') },
          { type: 'value', name: tr(translate, 'stats.axis.output'), splitLine: { show: false } },
        ]
      : { type: 'value' },
    series: [
      { name: input, type: 'bar', stack: 'tokens', data: data.prompt },
      { name: cached, type: 'bar', stack: 'tokens', data: data.cached },
      {
        name: output,
        type: 'line',
        yAxisIndex: dual ? 1 : 0,
        data: data.output,
        z: 3,
      },
    ],
  }
}

/**
 * 两组数据是否需要分轴：较大一组的峰值是较小一组峰值的 8 倍以上。
 * 任一组全 0 时不分轴（没有可比的东西）。
 */
export function needsDualAxis(a: number[], b: number[], ratio = 8): boolean {
  const peak = (values: number[]): number => values.reduce((max, value) => Math.max(max, Number(value) || 0), 0)
  const pa = peak(a)
  const pb = peak(b)
  if (pa <= 0 || pb <= 0) return false
  return Math.max(pa, pb) / Math.min(pa, pb) >= ratio
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