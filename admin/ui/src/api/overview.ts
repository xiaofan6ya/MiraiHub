/**
 * 概览页（/overview）的接口与数据转换。
 *
 * 契约：docs/ui-v2/contracts/overview.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-a.md P1
 * 后端：
 *   GET /api/overview          → admin/platform/overview_router.py:overview（399 行起）
 *   GET /api/sentinel/status   → admin/platform/sentinel_router.py:sentinel_status（73 行起）
 *   GET /api/stats/dashboard   → admin/routers/stats.py:dashboard_stats（流量带的每平台请求数/成功率）
 *   /api/platforms 由外壳的 platform store 提供（平台数、图标、禁用状态），本页不重复请求。
 *
 * 口径（与后端注释一致）：
 *   · 额度只统计 status='active' 的账号；used_ratio = 1 - 剩余/总额（总额为 0 时 null）；
 *   · 用量：请求数含失败，token/积分只统计成功；「今日」是本地时区 0 点起；
 *   · cache.rate 的分母只算「有缓存统计之后」的输入 token，无样本时为 null（显示 —）；
 *   · average_credits = 本月消耗 / active 账号数；
 *   · monitor 分桶：disabled > breaker > cooling > no_balance > active（与后端同序）；
 *   · 流量带的平台维度来自 /api/stats/dashboard.platforms（含 success_rate），
 *     它按落库平台聚合，与 /api/platforms 的区级 id 不同（trae 覆盖 trae-cn/intl）。
 */
import { http } from './http'
import { fmtCompact, fmtCredit, fmtInt, fmtPct } from '@/utils/format'
import { getStatsDashboard, statToneOf, type StatsDashboardPayload } from './stats'

export const OVERVIEW_DAYS = [1, 7, 30] as const
export type OverviewDays = (typeof OVERVIEW_DAYS)[number]

/** 只认 1 / 7 / 30（旧版 data-ov-days 的三个按钮）。 */
export function normalizeOverviewDays(value: unknown): OverviewDays {
  const n = Number(value)
  return n === 7 ? 7 : n === 30 ? 30 : 1
}

export function daysLabel(days: number): string {
  if (days === 7) return '近 7 天'
  if (days === 30) return '近 30 天'
  return '今日'
}

/* 后端形状（字段名与 overview_router.py 完全一致） */

export interface OverviewWindow {
  requests: number
  ok_requests: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  credits: number
  /** 仅 window 段带（后端 dict(window, days=days)）。 */
  days?: number
}

export interface OverviewAccounts {
  total: number
  balance_remain: number
  balance_total: number
  /** 已禁用账号上的余额，不参与统计。 */
  disabled_balance: number
  /** 已用比例：1 - 剩余/总额；总额为 0 时 null。 */
  used_ratio: number | null
}

export interface OverviewMonitorItem {
  id: number
  name: string
  platform: string
  domain: string
  /** active | cooling | breaker | no_balance | disabled */
  state: string
  label: string
  balance: number
  until: string | null
  /** 距离自动恢复的秒数。 */
  cool_remaining: number
  last_error: string
  last_error_at: string | null
  signal_code: string
  signal_count: number
  signal_label: string
  signal_last_at: string | null
}

export interface OverviewMonitor {
  active: number
  cooling: number
  breaker: number
  disabled: number
  no_balance: number
  total: number
  items: OverviewMonitorItem[]
}

export interface OverviewCachePlatform {
  platform: string
  label: string
  prompt_tokens: number
  cached_tokens: number
  miss_tokens: number
  rate: number | null
  samples: number
}

export interface OverviewCache {
  cached_tokens: number
  prompt_tokens: number
  miss_tokens: number
  /** 0–1；无输入 token 时 null。 */
  rate: number | null
  samples: number
  note: string
  by_platform: OverviewCachePlatform[]
}

export interface OverviewPayload {
  platform: string
  realm: string
  platform_label: string
  accounts: OverviewAccounts
  /** 启用中的模型数。 */
  models: number
  /** 启用中的 API Key 数。 */
  keys: number
  today: OverviewWindow
  window: OverviewWindow
  /** 本月累计（30 天口径下与 window 相同）。 */
  month: OverviewWindow
  average_credits: number | null
  cache: OverviewCache
  monitor: OverviewMonitor
  sections: string
  now: string
}

/** /api/sentinel/status 里本页用到的字段。 */
export interface SentinelStatusPayload {
  tripped?: boolean
  accounts_affected?: number
  accounts_risk?: number
  total_events?: number
  window_seconds?: number
  /** 平台 -> 暂停信息（router 追加）。 */
  pause?: Record<string, { until?: number; by?: string; reason?: string }>
  now?: number
}

export function getOverview(days: number, signal?: AbortSignal): Promise<OverviewPayload> {
  return http<OverviewPayload>('GET', '/api/overview', { query: { days }, signal })
}

export function getSentinelStatus(window = 300, signal?: AbortSignal): Promise<SentinelStatusPayload> {
  return http<SentinelStatusPayload>('GET', '/api/sentinel/status', { query: { window }, signal })
}

/** 流量带的平台维度（与当前时间范围同窗口）。 */
export function getOverviewTraffic(days: number, signal?: AbortSignal): Promise<StatsDashboardPayload> {
  return getStatsDashboard(
    { days, granularity: days > 7 ? 'day' : 'hour', top_models: 1 },
    signal,
  )
}
/* 纯函数：英雄卡 / 用量条 / 账号监测 / 告警 / 缓存 / 流量带 */

export type OverviewTone = 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'

/** 与 StatItem 结构一致（避免 api 层 import 组件）。 */
export interface OverviewStatItem {
  label: string
  value: string
  unit?: string
  tone?: 'default' | 'brand' | 'ok' | 'warn' | 'danger' | 'muted'
  foot?: string
  help?: string
}

export interface OverviewHero {
  /** 剩余额度（千分位整数）。 */
  remainText: string
  /** 总额度（千分位整数）。 */
  totalText: string
  /** 剩余 / 总额，0–1（总额为 0 时 0）。 */
  ratio: number
  ratioText: string
  /** <20% warn、<5% danger、无总额 neutral（设计稿 P1）。 */
  tone: OverviewTone
  /** 「总额度 17,583 · 3 个账号」。 */
  caption: string
  /** 已禁用账号余额的补充说明（没有则为空串）。 */
  note: string
  /** 今日 / N 天 / 户均 三行。 */
  rows: Array<{ label: string; value: string }>
}

function clampRatio(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

/** 剩余额度英雄卡。`used_ratio` 有值时用 1-used 反推（避免口径分叉）。 */
export function buildBalanceHero(
  accounts: OverviewAccounts | null | undefined,
  today: OverviewWindow | null | undefined,
  win: OverviewWindow | null | undefined,
  averageCredits: number | null | undefined,
): OverviewHero {
  const acc = accounts || {
    total: 0,
    balance_remain: 0,
    balance_total: 0,
    disabled_balance: 0,
    used_ratio: null,
  }
  const remain = Number(acc.balance_remain) || 0
  const total = Number(acc.balance_total) || 0
  const used = acc.used_ratio
  const ratio = total > 0
    ? clampRatio(used === null || used === undefined ? remain / total : 1 - Number(used))
    : 0
  const tone: OverviewTone =
    total <= 0 ? 'neutral' : ratio < 0.05 ? 'danger' : ratio < 0.2 ? 'warn' : 'brand'
  const days = win?.days || 1
  return {
    remainText: fmtInt(remain),
    totalText: fmtInt(total),
    ratio,
    ratioText: Math.round(ratio * 100) + '%',
    tone,
    caption: '总额度 ' + fmtInt(total) + ' · ' + (Number(acc.total) || 0) + ' 个账号',
    note:
      Number(acc.disabled_balance) > 0
        ? '另有 ' + fmtInt(acc.disabled_balance) + ' 在已禁用账号上，不参与统计'
        : '',
    rows: [
      { label: '今日', value: fmtCredit(today?.credits) },
      { label: days + ' 天', value: fmtCredit(win?.credits) },
      {
        label: '户均',
        value:
          averageCredits === null || averageCredits === undefined
            ? '—'
            : fmtCredit(averageCredits),
      },
    ],
  }
}

/** 右侧用量 StatStrip：请求 / 成功率 / 输入 / 输出 / 合计。 */
export function buildUsageItems(
  win: OverviewWindow | null | undefined,
  days: number,
): OverviewStatItem[] {
  if (!win) {
    return [
      { label: '请求数', value: '—' },
      { label: '成功率', value: '—', tone: 'muted' },
      { label: '输入 Token', value: '—' },
      { label: '输出 Token', value: '—' },
      { label: 'Token 合计', value: '—' },
    ]
  }
  const requests = Number(win.requests) || 0
  const ok = Number(win.ok_requests) || 0
  const rate = requests > 0 ? ok / requests : null
  return [
    {
      label: '请求数',
      value: fmtInt(requests),
      foot: '成功 ' + fmtInt(ok) + ' · 失败 ' + fmtInt(Math.max(0, requests - ok)),
      help: daysLabel(days) + '：请求数含失败，Token 只算成功请求',
    },
    {
      label: '成功率',
      value: rate === null ? '—' : fmtPct(rate, 1),
      tone: statToneOf(rate),
    },
    { label: '输入 Token', value: fmtCompact(win.prompt_tokens) },
    { label: '输出 Token', value: fmtCompact(win.completion_tokens) },
    {
      label: 'Token 合计',
      value: fmtCompact(win.total_tokens),
      foot: '缓存命中 ' + fmtCompact(win.cached_tokens),
    },
  ]
}

/** 账号状态的 5 行固定顺序（与后端 _account_monitor 的优先级一致）。 */
export const MONITOR_ROWS: ReadonlyArray<{
  key: 'active' | 'cooling' | 'breaker' | 'no_balance' | 'disabled'
  label: string
  tone: OverviewTone
}> = [
  { key: 'active', label: '活跃', tone: 'ok' },
  { key: 'cooling', label: '冷却', tone: 'warn' },
  { key: 'breaker', label: '熔断', tone: 'warn' },
  { key: 'no_balance', label: '额度耗尽', tone: 'neutral' },
  { key: 'disabled', label: '已禁用', tone: 'danger' },
]

export interface MonitorRowData {
  key: string
  label: string
  count: number
  /** 0–100（Meter 的入参）。 */
  pct: number
  tone: OverviewTone
  /** 数量为 0：文字用 --fg-faint（设计稿 P1）。 */
  faint: boolean
}

export function buildMonitorRows(monitor: OverviewMonitor | null | undefined): MonitorRowData[] {
  const total = Number(monitor?.total) || 0
  return MONITOR_ROWS.map((row) => {
    const count = Number(monitor?.[row.key]) || 0
    return {
      key: row.key,
      label: row.label,
      count,
      pct: total > 0 ? (count / total) * 100 : 0,
      tone: row.tone,
      faint: count === 0,
    }
  })
}

export interface MonitorConclusion {
  text: string
  tone: 'ok' | 'warn'
  /** 空串表示不需要跳转。 */
  to: string
}

/** 结论一句：全部正常，或指出最该看的那类账号并给出落点。 */
export function monitorConclusion(
  monitor: OverviewMonitor | null | undefined,
): MonitorConclusion {
  const total = Number(monitor?.total) || 0
  if (total <= 0) return { text: '还没有账号，先去账号池添加', tone: 'warn', to: '/accounts' }
  const active = Number(monitor?.active) || 0
  if (active >= total) return { text: '全部账号正常', tone: 'ok', to: '' }
  const cooling = Number(monitor?.cooling) || 0
  if (cooling > 0) {
    return { text: cooling + ' 个账号冷却中，查看', tone: 'warn', to: '/accounts?status=cooldown' }
  }
  const breaker = Number(monitor?.breaker) || 0
  if (breaker > 0) return { text: breaker + ' 个账号熔断中，查看', tone: 'warn', to: '/accounts' }
  const noBalance = Number(monitor?.no_balance) || 0
  if (noBalance > 0) return { text: noBalance + ' 个账号额度耗尽，查看', tone: 'warn', to: '/credits' }
  const disabled = Number(monitor?.disabled) || 0
  return { text: disabled + ' 个账号已禁用，查看', tone: 'warn', to: '/accounts' }
}

export interface OverviewAlert {
  level: 'danger' | 'warn'
  title: string
  detail: string
  actionLabel: string
  to: string
}

/**
 * 告警横幅（设计稿 P1：danger/warn soft + 「查看」）。
 *
 * 判定顺序与旧版 renderOverview 一致：批次封控 → 平台暂停 → 账号冷却 → 额度不足。
 * 数据全部来自已取到的两段（overview / sentinel），不再额外打接口。
 */
export function buildAlerts(
  payload: OverviewPayload | null | undefined,
  sentinel: SentinelStatusPayload | null | undefined,
): OverviewAlert[] {
  const alerts: OverviewAlert[] = []
  const affected = Number(sentinel?.accounts_affected) || 0
  if (sentinel?.tripped) {
    alerts.push({
      level: 'danger',
      title: affected + ' 个账号被上游封控，已达批次阈值',
      detail: '最近 5 分钟 · 建议暂停该平台，避免连坐',
      actionLabel: '浏览详情',
      to: '/sentinel',
    })
  } else if (affected > 0) {
    alerts.push({
      level: 'danger',
      title: affected + ' 个账号被上游拒绝过（403 / 11140 / 4015）',
      detail: '最近 5 分钟 · 去风控哨兵看信号明细',
      actionLabel: '浏览详情',
      to: '/sentinel',
    })
  }
  const paused = Object.keys(sentinel?.pause || {}).length
  if (paused > 0) {
    alerts.push({
      level: 'warn',
      title: '有 ' + paused + ' 个平台处于暂停状态',
      detail: '暂停期间请求直接返回 503，不打上游',
      actionLabel: '查看',
      to: '/sentinel',
    })
  }
  const monitor = payload?.monitor
  const cooling = (Number(monitor?.cooling) || 0) + (Number(monitor?.breaker) || 0)
  if (cooling > 0) {
    alerts.push({
      level: 'warn',
      title: cooling + ' 个账号冷却 / 熔断中',
      detail: '冷却结束后自动重试；可在账号池查看剩余时间',
      actionLabel: '查看',
      to: '/accounts?status=cooldown',
    })
  }
  const used = payload?.accounts?.used_ratio
  if (used !== null && used !== undefined && Number(used) >= 0.8) {
    alerts.push({
      level: 'warn',
      title: '剩余额度不足 ' + Math.round((1 - Number(used)) * 100) + '%',
      detail:
        '剩余 ' + fmtInt(payload?.accounts?.balance_remain) +
        ' / 总额度 ' + fmtInt(payload?.accounts?.balance_total),
      actionLabel: '查看',
      to: '/credits',
    })
  }
  return alerts
}

export interface CacheBlock {
  /** Ring 的 0–100 数值。 */
  value: number
  text: string
  tone: 'ok' | 'warn' | 'neutral'
  kv: Array<{ label: string; value: string }>
  note: string
}

/** 缓存命中卡：Ring + 3 个 KV + 说明。命中率着色按契约 §3.2（≥10% ok / ≥5% warn）。 */
export function buildCache(cache: OverviewCache | null | undefined): CacheBlock {
  const rate = cache?.rate
  const value =
    rate === null || rate === undefined ? 0 : Math.min(100, Math.max(0, Number(rate) * 100))
  const tone: CacheBlock['tone'] =
    rate === null || rate === undefined
      ? 'neutral'
      : Number(rate) >= 0.1
        ? 'ok'
        : Number(rate) >= 0.05
          ? 'warn'
          : 'neutral'
  return {
    value,
    text: rate === null || rate === undefined ? '—' : Math.round(value) + '%',
    tone,
    kv: [
      { label: '输入命中', value: fmtCompact(cache?.cached_tokens) },
      { label: '输入总量', value: fmtCompact(cache?.prompt_tokens) },
      { label: '样本请求', value: fmtInt(cache?.samples) },
    ],
    note: cache?.note || '只统计含缓存字段的上游响应',
  }
}

/** /api/platforms 的最小字段（PlatformSpec 的切片，便于单测）。 */
export interface OverviewPlatformSpec {
  id: string
  label: string
  icon?: string
  enabled?: boolean
  storage_platform?: string
}

export interface OverviewLane {
  id: string
  name: string
  logo: string
  count: number
  successRate: number | null
  disabled: boolean
}

/**
 * 流量带线路：/api/stats/dashboard 的 platforms[]（按落库平台聚合）
 * + /api/platforms 的图标与禁用状态。按请求数降序。
 */
export function buildLanes(
  traffic: StatsDashboardPayload | null | undefined,
  specs: OverviewPlatformSpec[] | null | undefined,
): OverviewLane[] {
  const list = specs || []
  const rows = traffic?.platforms || []
  return rows
    .map((row) => {
      const id = String(row.value || '').trim()
      const lower = id.toLowerCase()
      const spec =
        list.find((item) => String(item.id || '').toLowerCase() === lower) ||
        list.find((item) => String(item.storage_platform || '').toLowerCase() === lower)
      return {
        id: id || String(row.label || ''),
        name: String(row.label || id || '未知平台'),
        logo: String(spec?.icon || id || ''),
        count: Number(row.requests) || 0,
        successRate: row.success_rate ?? null,
        disabled: spec ? spec.enabled === false : false,
      }
    })
    .sort((a, b) => b.count - a.count)
}

export function lanesTotal(lanes: OverviewLane[]): number {
  return lanes.reduce((sum, lane) => sum + (Number(lane.count) || 0), 0)
}
