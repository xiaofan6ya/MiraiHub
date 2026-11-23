/**
 * 模型广场（/market）的接口与数据转换。
 *
 * 契约：docs/ui-v2/contracts/model-market.md
 * 后端：
 *   GET /api/stats/model-market  → admin/routers/stats.py:model_market（913 行起）
 *   GET /api/models/aggregate    → admin/routers/models.py:list_aggregate + admin/model_alias.py:aggregate_payload
 *   GET /api/stats/dashboard     → admin/routers/stats.py:dashboard_stats（只用它的 trend 分时序列画 sparkline）
 *
 * 三条必须守住的后端语义：
 *   1. model-market 是**归一后**的模型（同一个模型多平台合成一项），定价与平台名分平台列在 pricing 里；
 *      已配置但窗口内没有流量的模型也会出现（requests=0），所以「0 次」不等于「没收录」。
 *   2. available = 有定价 且 (活跃账号 − 该模型冷却中的账号) > 0；没有定价的模型恒为不可用。
 *   3. success_rate / tps / avg_* 在没有样本时是 null 或 0（后端约定），页面显示「—」而不是 0。
 *
 * 页面只做摆放：筛选 / 排序 / 分页 / sparkline 分桶全部在这里的纯函数完成，可直接单测。
 */
import { http } from './http'
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

/** model-market 的 pricing[]：每个平台一条报价。 */
export interface MarketPricingEntry {
  platform: string
  platform_label: string
  /** 该平台的原始模型名（复制调用名时的兜底）。 */
  model_id: string
  multiplier: number
  free: boolean
}

/** model-market 的 items[]：一个归一模型一张卡要的全部数字。 */
export interface MarketItem {
  /** 归一 key（canonical_key），跨平台同一个模型同一个 key。 */
  key: string
  /** 展示名（display_name，可能被人工别名表改写）。 */
  name: string
  /** 覆盖的平台 id（已排序）。 */
  platforms: string[]
  /** 与 platforms 同下标的展示名。 */
  platform_labels: string[]
  /** 各平台的原始模型名（已排序）。 */
  models: string[]
  requests: number
  /** 0–1；无样本时后端回 null。 */
  success_rate: number | null
  tokens: number
  credits: number
  avg_latency_ms: number
  avg_ttfb_ms: number
  /** tokens/秒；无样本时后端回 0。 */
  tps: number
  pricing: MarketPricingEntry[]
  /** 全部平台都免费时才为 true。 */
  free: boolean
  min_multiplier: number
  active_accounts: number
  exhausted_accounts: number
  available: boolean
}

export interface ModelMarketPayload {
  window_hours: number
  start: string
  end: string
  /** rollup = 小时汇总表，raw = 原始日志（后端降级）。 */
  source: string
  total: number
  available_models: number
  items: MarketItem[]
}

/** /api/models/aggregate 的平台明细：agent 页只用到 client_id（完整调用名）。 */
export interface AggregatePlatformEntry {
  platform: string
  platform_label?: string
  model_id: string
  client_id?: string
  multiplier?: number | null
  enabled?: boolean
  billing_mode?: string
  realm?: string
}

export interface AggregateItem {
  key: string
  name: string
  platforms: AggregatePlatformEntry[]
  model_ids: string[]
  platform_count: number
  min_multiplier?: number | null
  free_any?: boolean
  enabled_any?: boolean
}

export interface AggregatePayload {
  items: AggregateItem[]
  total_models: number
  total_groups: number
  multi_platform_groups: number
}

/** /api/stats/dashboard 的 trend：按模型堆叠的分时序列（后端只回 Top N 模型）。 */
export interface TrendSeries {
  name: string
  requests: number[]
  tokens?: number[]
  credits?: number[]
}

export interface DashboardTrend {
  buckets: string[]
  total_requests: number[]
  series: TrendSeries[]
}

export interface DashboardPayload {
  window?: { days: number; granularity: string; start: string; end: string; minutes: number }
  trend?: DashboardTrend
}

/* -------------------------------------------------------------------------- */
/* 接口                                                                        */
/* -------------------------------------------------------------------------- */

/** 时间窗口（小时）。设计稿只保留 24h / 3d / 7d（旧版还有 1h，本次不做）。 */
export const MARKET_HOURS = [24, 72, 168] as const
export type MarketHours = (typeof MARKET_HOURS)[number]
const MARKET_HOURS_LABEL_KEY: Record<number, MessageKey> = {
  24: 'market.hours.24',
  72: 'market.hours.72',
  168: 'market.hours.168',
}

/** 时间窗口文案（24 小时 / 3 天 / 7 天），未知值回落到「N 小时」。 */
export function marketHoursLabel(hours: unknown, translate?: Translate): string {
  const value = normalizeHours(hours)
  const key = MARKET_HOURS_LABEL_KEY[value]
  if (!key) return tr(translate, 'page.market.hoursFallback', { hours: value })
  return tr(translate, key)
}

/** 主接口：近 N 小时的用量 + 定价 + 可用账号。 */
export function listModelMarket(hours: number, signal?: AbortSignal): Promise<ModelMarketPayload> {
  return http<ModelMarketPayload>('GET', '/api/stats/model-market', {
    query: { hours: normalizeHours(hours) },
    signal,
  })
}

/** 副接口：跨平台归一 + 各平台完整调用名。失败不影响主流程（契约 §8.2）。 */
export function getModelAggregate(signal?: AbortSignal): Promise<AggregatePayload> {
  return http<AggregatePayload>('GET', '/api/models/aggregate', { signal })
}

/**
 * sparkline 的数据源：dashboard 的 trend 分时序列。
 *
 * model-market 只回窗口内的合计，没有分桶；dashboard 是唯一按模型给分时数组的接口。
 * 它只回 Top N（按积分/token 排序），所以广场上没进 Top N 的模型没有 sparkline ——
 * 这一条在契约「实现对照」里记为有差异，不伪造数据。
 */
export function getMarketTrend(hours: number, signal?: AbortSignal): Promise<DashboardPayload> {
  return http<DashboardPayload>('GET', '/api/stats/dashboard', {
    query: {
      days: hoursToDays(hours),
      granularity: 'hour',
      // 卡片视图一屏最多 20 张，取 20 个模型足够；再多也只是白传数据。
      top_models: 12,
    },
    signal,
  })
}

/** 小时窗口 → dashboard 的 days（滚动窗口，口径一致：24h=1 天、3d=3 天、7d=7 天）。 */
export function hoursToDays(hours: unknown): number {
  const n = normalizeHours(hours)
  return Math.max(1, Math.min(90, Math.round(n / 24)))
}

export function normalizeHours(value: unknown): MarketHours {
  const n = Number(value)
  return (MARKET_HOURS as readonly number[]).includes(n) ? (n as MarketHours) : 24
}

/* -------------------------------------------------------------------------- */
/* 视图与窗口偏好（localStorage）                                               */
/* -------------------------------------------------------------------------- */

export const MARKET_VIEW_KEY = 'mh.market.view'
export const MARKET_HOURS_KEY = 'mh.market.hours'

export type MarketView = 'cards' | 'list'

export function readMarketView(): MarketView {
  try {
    const raw = localStorage.getItem(MARKET_VIEW_KEY)
    return raw === 'list' ? 'list' : 'cards'
  } catch {
    return 'cards'
  }
}

export function writeMarketView(view: MarketView): void {
  try {
    localStorage.setItem(MARKET_VIEW_KEY, view === 'list' ? 'list' : 'cards')
  } catch {
    /* 隐私模式写不进去，不影响本次会话 */
  }
}

export function readMarketHours(): MarketHours {
  try {
    return normalizeHours(localStorage.getItem(MARKET_HOURS_KEY))
  } catch {
    return 24
  }
}

export function writeMarketHours(hours: unknown): void {
  try {
    localStorage.setItem(MARKET_HOURS_KEY, String(normalizeHours(hours)))
  } catch {
    /* 同上 */
  }
}

/* -------------------------------------------------------------------------- */
/* 卡片行（接口响应 → 页面数据，纯函数）                                        */
/* -------------------------------------------------------------------------- */

export interface MarketCard {
  key: string
  name: string
  platforms: string[]
  platformLabels: string[]
  platformCount: number
  models: string[]
  /** 主要调用名：aggregate 的 client_id 优先，其次最低倍率平台的 model_id。 */
  callName: string
  requests: number
  successRate: number | null
  tokens: number
  credits: number
  latencyMs: number
  ttfbMs: number
  tps: number
  /** 最低倍率；没有定价时为 null。 */
  multiplier: number | null
  free: boolean
  activeAccounts: number
  exhaustedAccounts: number
  /** 真正可用 = 有定价 且 活跃账号 > 冷却账号（与后端同口径，前端再兜一层）。 */
  available: boolean
  /** sparkline 柱高比例（0–1）；没有分时数据时为空数组。 */
  spark: number[]
  /** 与 spark 同源的窗口内请求合计（tooltip 用），没有分时数据时为 null。 */
  sparkTotal: number | null
}

/** 模型名归一：只留字母数字，供跨接口（展示名 / 原始 id / key）匹配。 */
export function normalizeModelName(value: unknown): string {
  return String(value ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '')
}

/** aggregate 的 key → 完整调用名（各平台 client_id 按倍率升序，第一个当主调用名）。 */
export function indexCallNames(aggregate: AggregatePayload | null | undefined): Map<string, string> {
  const out = new Map<string, string>()
  for (const item of Array.isArray(aggregate?.items) ? aggregate.items : []) {
    const key = String(item?.key || '')
    if (!key) continue
    const entries = (Array.isArray(item.platforms) ? item.platforms : [])
      .filter((entry) => String(entry?.client_id || entry?.model_id || '').trim())
      .slice()
      .sort((a, b) => numeric(a.multiplier) - numeric(b.multiplier))
    const name = String(entries[0]?.client_id || entries[0]?.model_id || '')
    if (name) out.set(key, name)
  }
  return out
}

function numeric(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : Number.MAX_SAFE_INTEGER
}

/** trend 的模型名 → 分时请求数组（按归一名字索引）。 */
export function indexTrendSeries(trend: DashboardTrend | null | undefined): Map<string, number[]> {
  const out = new Map<string, number[]>()
  for (const series of Array.isArray(trend?.series) ? trend.series : []) {
    const name = normalizeModelName(series?.name)
    if (!name) continue
    const values = (Array.isArray(series.requests) ? series.requests : []).map((value) => {
      const n = Number(value)
      return Number.isFinite(n) && n > 0 ? n : 0
    })
    if (!values.length) continue
    out.set(name, values)
  }
  return out
}

/**
 * sparkline 分桶：把任意长度的分时序列等分合并成 ≤ buckets 根柱子，再按最大值归一。
 *
 * 后端 24h/小时=24 点、7d/小时=168 点，直接画在 120px 宽里全是毛刺；
 * 这里先把相邻桶求和（总量不变），再归一成 0–1 的柱高。
 */
export function sparklineBars(values: ArrayLike<number> | null | undefined, buckets = 12): number[] {
  const list: number[] = []
  const length = values ? Number(values.length) || 0 : 0
  for (let index = 0; index < length; index += 1) {
    const n = Number(values![index])
    list.push(Number.isFinite(n) && n > 0 ? n : 0)
  }
  if (!list.length) return []
  const total = list.reduce((acc, value) => acc + value, 0)
  if (total <= 0) return []
  const size = Math.max(1, Math.floor(buckets) || 12)
  const groupCount = Math.min(size, list.length)
  const merged: number[] = []
  for (let group = 0; group < groupCount; group += 1) {
    const start = Math.floor((group * list.length) / groupCount)
    const end = Math.floor(((group + 1) * list.length) / groupCount)
    let sum = 0
    for (let index = start; index < end; index += 1) sum += list[index]
    merged.push(sum)
  }
  const max = Math.max(...merged, 0)
  if (max <= 0) return []
  return merged.map((value) => Math.round((value / max) * 1000) / 1000)
}

/** 一张卡片的分时序列：依次用展示名 / key / 原始模型名去 trend 里找。 */
export function trendOfItem(
  item: MarketItem,
  index: Map<string, number[]>,
): number[] | undefined {
  if (!index.size) return undefined
  const candidates = [item?.name, item?.key, ...(Array.isArray(item?.models) ? item.models : [])]
  for (const candidate of candidates) {
    const hit = index.get(normalizeModelName(candidate))
    if (hit && hit.length) return hit
  }
  return undefined
}

/** 主要调用名：aggregate → 最低倍率平台 model_id → 任一模型名 → key。 */
export function primaryCallName(item: MarketItem, callNames: Map<string, string>): string {
  const fromAggregate = callNames.get(String(item?.key || ''))
  if (fromAggregate) return fromAggregate
  const prices = (Array.isArray(item?.pricing) ? item.pricing : [])
    .filter((entry) => String(entry?.model_id || '').trim())
    .slice()
    .sort((a, b) => numeric(a.multiplier) - numeric(b.multiplier))
  const fromPricing = String(prices[0]?.model_id || '').trim()
  if (fromPricing) return fromPricing
  const fromModels = (Array.isArray(item?.models) ? item.models : []).find((value) =>
    String(value || '').trim(),
  )
  return String(fromModels || item?.key || '')
}

/** 接口响应 → 卡片行。aggregate / trend 缺失时降级，不抛异常。 */
export function toMarketCards(
  payload: ModelMarketPayload | null | undefined,
  aggregate?: AggregatePayload | null,
  trend?: DashboardTrend | null,
  translate?: Translate,
): MarketCard[] {
  const items = Array.isArray(payload?.items) ? payload!.items : []
  const callNames = indexCallNames(aggregate)
  const trendIndex = indexTrendSeries(trend)
  return items
    .filter((item) => item && (item.key || item.name))
    .map((item) => {
      const platforms = Array.isArray(item.platforms) ? item.platforms.filter(Boolean) : []
      const labels = Array.isArray(item.platform_labels) ? item.platform_labels.filter(Boolean) : []
      const pricing = Array.isArray(item.pricing) ? item.pricing.filter(Boolean) : []
      const series = trendOfItem(item, trendIndex)
      const multiplier = pricing.length ? Number(item.min_multiplier || 0) : null
      const active = Math.max(0, Number(item.active_accounts) || 0)
      const exhausted = Math.max(0, Number(item.exhausted_accounts) || 0)
      return {
        key: String(item.key || item.name || ''),
        name: String(item.name || item.key || tr(translate, 'market.row.unknownModel')),
        platforms,
        platformLabels: labels.length ? labels : platforms,
        platformCount: platforms.length,
        models: Array.isArray(item.models) ? item.models.filter(Boolean) : [],
        callName: primaryCallName(item, callNames),
        requests: Math.max(0, Number(item.requests) || 0),
        successRate:
          item.success_rate === null || item.success_rate === undefined
            ? null
            : Number(item.success_rate),
        tokens: Math.max(0, Number(item.tokens) || 0),
        credits: Number(item.credits) || 0,
        latencyMs: Math.max(0, Number(item.avg_latency_ms) || 0),
        ttfbMs: Math.max(0, Number(item.avg_ttfb_ms) || 0),
        tps: Math.max(0, Number(item.tps) || 0),
        multiplier: multiplier === null ? null : Number.isFinite(multiplier) ? multiplier : null,
        free: Boolean(item.free) && pricing.length > 0,
        activeAccounts: active,
        exhaustedAccounts: exhausted,
        available: pricing.length > 0 && active - exhausted > 0,
        spark: sparklineBars(series),
        sparkTotal: series ? series.reduce((acc, value) => acc + value, 0) : null,
      }
    })
}

/* -------------------------------------------------------------------------- */
/* 筛选 / 排序 / 分页 / 汇总（纯函数）                                          */
/* -------------------------------------------------------------------------- */

export interface MarketFilters {
  /** 搜索：模型名 / key / 调用名 / 各平台原始模型名 */
  q?: string
  /** 平台 id（空 = 全部） */
  platform?: string
  /** 'available' = 仅看可用 */
  available?: string
}

export function filterMarketCards(cards: MarketCard[], filters: MarketFilters = {}): MarketCard[] {
  const keyword = String(filters.q || '').trim().toLowerCase()
  const platform = String(filters.platform || '').trim().toLowerCase()
  const onlyAvailable = String(filters.available || '') === 'available'
  return (cards || []).filter((card) => {
    if (onlyAvailable && !card.available) return false
    if (platform && !card.platforms.some((value) => String(value).toLowerCase() === platform)) {
      return false
    }
    if (!keyword) return true
    const haystack = [card.name, card.key, card.callName, ...card.models]
      .join(' ')
      .toLowerCase()
    return haystack.includes(keyword)
  })
}

export const MARKET_SORTS = ['requests', 'price', 'tps', 'success_rate', 'name'] as const
export type MarketSort = (typeof MARKET_SORTS)[number]

export function normalizeSort(value: unknown): MarketSort {
  const text = String(value || '')
  return (MARKET_SORTS as readonly string[]).includes(text) ? (text as MarketSort) : 'requests'
}

export function sortMarketCards(
  cards: MarketCard[],
  sortBy: string,
  order: string,
): MarketCard[] {
  const key = normalizeSort(sortBy)
  const desc = order === 'desc' || order === ''
  const rows = (cards || []).slice()
  const compare = (a: MarketCard, b: MarketCard): number => {
    if (key === 'name') return a.name.localeCompare(b.name, 'zh-CN')
    if (key === 'price') {
      // 免费 = 0，排在最低；没有定价的排最后（契约 §5.4：价格升序）。
      const av = a.multiplier === null ? Number.MAX_SAFE_INTEGER : a.free ? 0 : a.multiplier
      const bv = b.multiplier === null ? Number.MAX_SAFE_INTEGER : b.free ? 0 : b.multiplier
      return av - bv
    }
    if (key === 'success_rate') {
      const av = a.successRate === null ? -1 : a.successRate
      const bv = b.successRate === null ? -1 : b.successRate
      return av - bv
    }
    const field = key === 'tps' ? 'tps' : 'requests'
    return (Number(a[field]) || 0) - (Number(b[field]) || 0)
  }
  rows.sort((a, b) => {
    const result = compare(a, b)
    if (result !== 0) return desc ? -result : result
    return a.name.localeCompare(b.name, 'zh-CN')
  })
  return rows
}

export interface MarketPageData {
  items: MarketCard[]
  total: number
  page: number
  pageCount: number
}

export function paginateMarketCards(
  cards: MarketCard[],
  page: number,
  pageSize: number,
): MarketPageData {
  const size = Math.max(1, Math.floor(Number(pageSize) || 20))
  const total = (cards || []).length
  const pageCount = Math.ceil(total / size)
  const safePage = Math.min(Math.max(1, Math.floor(Number(page) || 1)), Math.max(1, pageCount))
  const start = (safePage - 1) * size
  return {
    items: (cards || []).slice(start, start + size),
    total,
    page: safePage,
    pageCount,
  }
}

export interface MarketStats {
  total: number
  available: number
  requests: number
  free: number
  platforms: number
}

export function marketStats(cards: MarketCard[]): MarketStats {
  const platformSet = new Set<string>()
  let available = 0
  let requests = 0
  let free = 0
  for (const card of cards || []) {
    if (card.available) available += 1
    if (card.free) free += 1
    requests += Number(card.requests) || 0
    for (const platform of card.platforms) platformSet.add(platform)
  }
  return { total: (cards || []).length, available, requests, free, platforms: platformSet.size }
}

/** 平台筛选下拉的选项（只列数据集里出现过的平台，契约 §5.2）。 */
export function platformOptions(cards: MarketCard[]): Array<{ label: string; value: string }> {
  const labels = new Map<string, string>()
  for (const card of cards || []) {
    card.platforms.forEach((value, index) => {
      if (!value) return
      const label = card.platformLabels[index] || value
      if (!labels.has(value)) labels.set(value, label)
    })
  }
  return [...labels.entries()]
    .sort((a, b) => a[1].localeCompare(b[1], 'zh-CN'))
    .map(([value, label]) => ({ value, label }))
}

/** 成功率着色（契约 §4.2）：≥99.5% ok、≥97% warn、<97% danger、无数据 neutral。 */
export function rateTone(rate: number | null | undefined): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (rate === null || rate === undefined || !Number.isFinite(Number(rate))) return 'neutral'
  const value = Number(rate)
  if (value >= 0.995) return 'ok'
  if (value >= 0.97) return 'warn'
  return 'danger'
}

/** 倍率文案（契约 §3.2）：免费 / ×1.00 / 未定价。 */
export function priceText(
  card: Pick<MarketCard, 'free' | 'multiplier'>,
  translate?: Translate,
): string {
  if (card.free) return tr(translate, 'market.price.free')
  if (card.multiplier === null) return tr(translate, 'market.price.none')
  return '×' + Number(card.multiplier).toFixed(2)
}

/** 查看日志：跳到 /logs 并预设 model 查询（S15 那页认这个 query）。 */
export function logLinkFor(card: Pick<MarketCard, 'callName' | 'key'>): {
  path: string
  query: Record<string, string>
} {
  const model = String(card.callName || card.key || '').trim()
  return { path: '/logs', query: model ? { model } : {} }
}