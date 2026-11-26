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
import { toLiveRows, type LiveRow, type TrafficPayload } from './sentinel'
import { t as defaultTranslate, type Translate } from '@/i18n'

export const OVERVIEW_DAYS = [1, 7, 30] as const

/**
 * 文案取当前语言；页面把 useI18n().t 传进来时，computed 会随语言切换重算。
 * 不传时用默认字典（中文），单测无需关心 i18n。
 */
function tr(
  translate: Translate | undefined,
  key: Parameters<Translate>[0],
  params?: Record<string, string | number>,
): string {
  return (translate ?? defaultTranslate)(key, params)
}
export type OverviewDays = (typeof OVERVIEW_DAYS)[number]

/** 只认 1 / 7 / 30（旧版 data-ov-days 的三个按钮）。 */
export function normalizeOverviewDays(value: unknown): OverviewDays {
  const n = Number(value)
  return n === 7 ? 7 : n === 30 ? 30 : 1
}

export function daysLabel(days: number, translate?: Translate): string {
  if (days === 7) return tr(translate, 'overview.days.last7')
  if (days === 30) return tr(translate, 'overview.days.last30')
  return tr(translate, 'overview.days.today')
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
  /**
   * 每段数字的时间窗（新后端，N5）：全部是北京时间自然日。旧后端没有这个字段。
   */
  windows?: {
    kind?: string
    tz?: string
    today?: { start?: string; days?: number }
    window?: { start?: string; days?: number }
    month?: { start?: string; days?: number }
  }
  /**
   * 每块数据的作用域（新后端，N5）：`platform` = 跟随 X-Platform；`gateway` = 全网关。
   * 前端据此在卡片上标口径；旧后端没有时按「账号 / 用量跟随当前平台，流量带全网关」处理。
   */
  scopes?: Partial<
    Record<'accounts' | 'usage' | 'cache' | 'monitor' | 'models' | 'keys' | 'traffic' | 'sentinel', string>
  >
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
  /** router 合并下发的实时流量快照（含最近 1 小时请求明细），「最近请求」卡直接用它。 */
  traffic?: TrafficPayload
}

export function getOverview(days: number, signal?: AbortSignal): Promise<OverviewPayload> {
  return http<OverviewPayload>('GET', '/api/overview', { query: { days }, signal })
}

export function getSentinelStatus(window = 300, signal?: AbortSignal): Promise<SentinelStatusPayload> {
  return http<SentinelStatusPayload>('GET', '/api/sentinel/status', { query: { window }, signal })
}

/**
 * 流量带的平台维度（与当前时间范围同窗口）。
 *
 * `window: 'day'`：北京时间自然日，与 /api/overview 的「今日 / 近 N 天」同口径（N5）。
 * 原来走默认滚动窗口，同一屏左边是自然日、右边是滚动 24 小时，两个数字永远对不上。
 */
export function getOverviewTraffic(days: number, signal?: AbortSignal): Promise<StatsDashboardPayload> {
  return getStatsDashboard(
    { days, granularity: days > 7 ? 'day' : 'hour', top_models: 1, window: 'day' },
    signal,
  )
}

/**
 * KPI 迷你折线用的分时序列：**与 KPI 同口径**（当前平台，自然日）。
 * 流量带是全网关，不能拿它的序列画「当前平台」的折线。拿不到时页面不画折线，不报错。
 */
export function getOverviewTrend(
  days: number,
  platform: string,
  signal?: AbortSignal,
): Promise<StatsDashboardPayload> {
  return getStatsDashboard(
    { days, granularity: days > 7 ? 'day' : 'hour', top_models: 1, window: 'day', platform },
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
  translate?: Translate,
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
    caption: tr(translate, 'overview.hero.caption', {
      total: fmtInt(total),
      accounts: Number(acc.total) || 0,
    }),
    note:
      Number(acc.disabled_balance) > 0
        ? tr(translate, 'overview.hero.disabledNote', { amount: fmtInt(acc.disabled_balance) })
        : '',
    rows: [
      { label: tr(translate, 'overview.hero.today'), value: fmtCredit(today?.credits) },
      { label: tr(translate, 'overview.days.nDays', { days }), value: fmtCredit(win?.credits) },
      {
        label: tr(translate, 'overview.hero.average'),
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
  translate?: Translate,
): OverviewStatItem[] {
  if (!win) {
    return [
      { label: tr(translate, 'overview.usage.requests'), value: '—' },
      { label: tr(translate, 'overview.usage.successRate'), value: '—', tone: 'muted' },
      { label: tr(translate, 'overview.usage.input'), value: '—' },
      { label: tr(translate, 'overview.usage.output'), value: '—' },
      { label: tr(translate, 'overview.usage.total'), value: '—' },
    ]
  }
  const requests = Number(win.requests) || 0
  const ok = Number(win.ok_requests) || 0
  const rate = requests > 0 ? ok / requests : null
  return [
    {
      label: tr(translate, 'overview.usage.requests'),
      value: fmtInt(requests),
      foot: tr(translate, 'overview.usage.requestsFoot', {
        ok: fmtInt(ok),
        failed: fmtInt(Math.max(0, requests - ok)),
      }),
      help: tr(translate, 'overview.usage.requestsHelp', { range: daysLabel(days, translate) }),
    },
    {
      label: tr(translate, 'overview.usage.successRate'),
      value: rate === null ? '—' : fmtPct(rate, 1),
      tone: statToneOf(rate),
    },
    { label: tr(translate, 'overview.usage.input'), value: fmtCompact(win.prompt_tokens) },
    { label: tr(translate, 'overview.usage.output'), value: fmtCompact(win.completion_tokens) },
    {
      label: tr(translate, 'overview.usage.total'),
      value: fmtCompact(win.total_tokens),
      foot: tr(translate, 'overview.usage.cacheFoot', { count: fmtCompact(win.cached_tokens) }),
    },
  ]
}

/**
 * 账号状态的 5 行固定顺序（与后端 _account_monitor 的优先级一致）。
 *
 * 语气按 01-direction §3.3 的固定映射：熔断 = danger，已禁用 = 灰（neutral）。
 * 旧值把「已禁用」画成朱色条、「熔断」画成琥珀（第 1 轮审查 P1-2：状态色当装饰）。
 */
export const MONITOR_ROWS: ReadonlyArray<{
  key: 'active' | 'cooling' | 'breaker' | 'no_balance' | 'disabled'
  label: string
  tone: OverviewTone
}> = [
  { key: 'active', label: '活跃', tone: 'ok' },
  { key: 'cooling', label: '冷却', tone: 'warn' },
  { key: 'breaker', label: '熔断', tone: 'danger' },
  { key: 'no_balance', label: '额度耗尽', tone: 'danger' },
  { key: 'disabled', label: '已禁用', tone: 'neutral' },
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

const MONITOR_LABEL_KEY = {
  active: 'overview.monitor.active',
  cooling: 'overview.monitor.cooling',
  breaker: 'overview.monitor.breaker',
  no_balance: 'overview.monitor.noBalance',
  disabled: 'overview.monitor.disabled',
} as const

export function buildMonitorRows(
  monitor: OverviewMonitor | null | undefined,
  translate?: Translate,
): MonitorRowData[] {
  const total = Number(monitor?.total) || 0
  return MONITOR_ROWS.map((row) => {
    const count = Number(monitor?.[row.key]) || 0
    return {
      key: row.key,
      label: tr(translate, MONITOR_LABEL_KEY[row.key]),
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
  translate?: Translate,
): MonitorConclusion {
  const total = Number(monitor?.total) || 0
  if (total <= 0) {
    return { text: tr(translate, 'overview.conclusion.noAccounts'), tone: 'warn', to: '/accounts' }
  }
  const active = Number(monitor?.active) || 0
  if (active >= total) {
    return { text: tr(translate, 'overview.conclusion.allOk'), tone: 'ok', to: '' }
  }
  const cooling = Number(monitor?.cooling) || 0
  if (cooling > 0) {
    return {
      text: tr(translate, 'overview.conclusion.cooling', { count: cooling }),
      tone: 'warn',
      to: '/accounts?status=cooldown',
    }
  }
  const breaker = Number(monitor?.breaker) || 0
  if (breaker > 0) {
    return {
      text: tr(translate, 'overview.conclusion.breaker', { count: breaker }),
      tone: 'warn',
      to: '/accounts',
    }
  }
  const noBalance = Number(monitor?.no_balance) || 0
  if (noBalance > 0) {
    return {
      text: tr(translate, 'overview.conclusion.noBalance', { count: noBalance }),
      tone: 'warn',
      to: '/credits',
    }
  }
  const disabled = Number(monitor?.disabled) || 0
  return {
    text: tr(translate, 'overview.conclusion.disabled', { count: disabled }),
    tone: 'warn',
    to: '/accounts',
  }
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
  translate?: Translate,
): OverviewAlert[] {
  const alerts: OverviewAlert[] = []
  const affected = Number(sentinel?.accounts_affected) || 0
  if (sentinel?.tripped) {
    alerts.push({
      level: 'danger',
      title: tr(translate, 'overview.alert.trippedTitle', { count: affected }),
      detail: tr(translate, 'overview.alert.trippedDetail'),
      actionLabel: tr(translate, 'overview.alert.actionDetails'),
      to: '/sentinel',
    })
  } else if (affected > 0) {
    alerts.push({
      level: 'danger',
      title: tr(translate, 'overview.alert.rejectedTitle', { count: affected }),
      detail: tr(translate, 'overview.alert.rejectedDetail'),
      actionLabel: tr(translate, 'overview.alert.actionDetails'),
      to: '/sentinel',
    })
  }
  const paused = Object.keys(sentinel?.pause || {}).length
  if (paused > 0) {
    alerts.push({
      level: 'warn',
      title: tr(translate, 'overview.alert.pausedTitle', { count: paused }),
      detail: tr(translate, 'overview.alert.pausedDetail'),
      actionLabel: tr(translate, 'overview.alert.actionView'),
      to: '/sentinel',
    })
  }
  const monitor = payload?.monitor
  const cooling = (Number(monitor?.cooling) || 0) + (Number(monitor?.breaker) || 0)
  if (cooling > 0) {
    alerts.push({
      level: 'warn',
      title: tr(translate, 'overview.alert.coolingTitle', { count: cooling }),
      detail: tr(translate, 'overview.alert.coolingDetail'),
      actionLabel: tr(translate, 'overview.alert.actionView'),
      to: '/accounts?status=cooldown',
    })
  }
  const used = payload?.accounts?.used_ratio
  if (used !== null && used !== undefined && Number(used) >= 0.8) {
    alerts.push({
      level: 'warn',
      title: tr(translate, 'overview.alert.lowBalanceTitle', {
        percent: Math.round((1 - Number(used)) * 100),
      }),
      detail: tr(translate, 'overview.alert.lowBalanceDetail', {
        remain: fmtInt(payload?.accounts?.balance_remain),
        total: fmtInt(payload?.accounts?.balance_total),
      }),
      actionLabel: tr(translate, 'overview.alert.actionView'),
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
export function buildCache(
  cache: OverviewCache | null | undefined,
  translate?: Translate,
): CacheBlock {
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
      { label: tr(translate, 'overview.cache.inputHit'), value: fmtCompact(cache?.cached_tokens) },
      { label: tr(translate, 'overview.cache.inputTotal'), value: fmtCompact(cache?.prompt_tokens) },
      { label: tr(translate, 'overview.cache.samples'), value: fmtInt(cache?.samples) },
    ],
    note: cache?.note || tr(translate, 'overview.cache.defaultNote'),
  }
}

/** /api/platforms 的最小字段（PlatformSpec 的切片，便于单测）。 */
export interface OverviewPlatformSpec {
  id: string
  label: string
  icon?: string
  enabled?: boolean
  storage_platform?: string
  account_count?: number | null
  active_count?: number | null
}

export interface OverviewLane {
  id: string
  name: string
  logo: string
  count: number
  successRate: number | null
  disabled: boolean
}

/** 落库平台 → 该平台的全部对外 spec（trae → trae-cn / trae-intl）。只取已启用的。 */
function specGroups(specs: OverviewPlatformSpec[]): Map<string, OverviewPlatformSpec[]> {
  const groups = new Map<string, OverviewPlatformSpec[]>()
  for (const spec of specs) {
    if (!spec || !spec.id || spec.enabled === false) continue
    const key = String(spec.storage_platform || spec.id).toLowerCase()
    const list = groups.get(key) || []
    list.push(spec)
    groups.set(key, list)
  }
  return groups
}

/** 多区平台的组名：取各区名称的公共前缀（「Trae 国内版」「Trae 国际版」→「Trae」）。 */
export function groupLabel(labels: string[], fallback: string): string {
  const list = labels.map((label) => String(label || '').trim()).filter(Boolean)
  if (!list.length) return fallback
  if (list.length === 1) return list[0]
  let prefix = list[0]
  for (const label of list.slice(1)) {
    let i = 0
    while (i < prefix.length && i < label.length && prefix[i] === label[i]) i += 1
    prefix = prefix.slice(0, i)
  }
  // 公共前缀停在词中间（「Trae 国」）时退回到最后一个分隔符之前（「Trae」）。
  let cut = prefix
  if (!list.includes(prefix)) {
    const match = /^(.*)[\s·\-(（]/u.exec(prefix)
    if (match) cut = match[1]
  }
  const trimmed = cut.replace(/[\s·（(-]+$/u, '').trim()
  return trimmed.length >= 2 ? trimmed : list[0]
}

function findSpec(list: OverviewPlatformSpec[], id: string): OverviewPlatformSpec | undefined {
  const lower = id.toLowerCase()
  return (
    list.find((item) => String(item.id || '').toLowerCase() === lower) ||
    list.find((item) => String(item.storage_platform || '').toLowerCase() === lower)
  )
}

/**
 * 流量带线路：/api/stats/dashboard 的 platforms[]（按落库平台聚合）
 * + /api/platforms 的图标与禁用状态。按请求数降序。
 *
 * v2.1：**已启用但窗口内没有流量的平台也画出来**（count=0，RouteRibbon 画 1px 细灰线），
 * 只有一个平台有流量时整体仍是扇形，不会退化成一根实心粗条（10-visual-refresh §1.8）。
 * dashboard 按落库平台聚合（trae 覆盖 trae-cn/intl），补零流量线路时也按落库平台合并成一条。
 */
export function buildLanes(
  traffic: StatsDashboardPayload | null | undefined,
  specs: OverviewPlatformSpec[] | null | undefined,
  translate?: Translate,
): OverviewLane[] {
  const list = specs || []
  const rows = traffic?.platforms || []
  const covered = new Set<string>()
  const lanes: OverviewLane[] = rows.map((row) => {
    const id = String(row.value || '').trim()
    const spec = findSpec(list, id)
    covered.add(id.toLowerCase())
    if (spec) covered.add(String(spec.storage_platform || spec.id).toLowerCase())
    return {
      id: id || String(row.label || ''),
      name: String(row.label || id || tr(translate, 'overview.lane.unknownPlatform')),
      logo: String(spec?.icon || id || ''),
      count: Number(row.requests) || 0,
      successRate: row.success_rate ?? null,
      disabled: spec ? spec.enabled === false : false,
    }
  })
  for (const [storage, group] of specGroups(list)) {
    if (covered.has(storage) || group.some((spec) => covered.has(String(spec.id).toLowerCase()))) continue
    lanes.push({
      id: group.length === 1 ? group[0].id : storage,
      name: groupLabel(group.map((spec) => spec.label), storage),
      logo: String(group[0].icon || group[0].id || storage),
      count: 0,
      successRate: null,
      disabled: false,
    })
  }
  return lanes.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN'))
}

export function lanesTotal(lanes: OverviewLane[]): number {
  return lanes.reduce((sum, lane) => sum + (Number(lane.count) || 0), 0)
}

/* ------------------------------------------------------------------ *
 * v2.1：平台健康列表 / 最近请求 / KPI 行 / 口径标签
 * ------------------------------------------------------------------ */

export interface OverviewHealthRow {
  id: string
  name: string
  logo: string
  requests: number
  /** 0–1；无请求时 null。 */
  successRate: number | null
  rateText: string
  /** 请求占比 0–100（Meter 入参）。 */
  share: number
  tokens: number
  /** 账号数；/api/platforms 没给时 null。 */
  accounts: number | null
  /** 可用账号数；后端还没给这个字段时 null。 */
  active: number | null
  tone: 'ok' | 'warn' | 'danger' | 'neutral'
  status: string
}

export interface OverviewHealth {
  rows: OverviewHealthRow[]
  /** 已启用、0 账号、窗口内也没有流量的平台：折叠成一行「未接入 N 个」。 */
  idle: string[]
}

/**
 * 平台健康：每个落库平台一行（流量 + 成功率 + 账号可用情况），状态只表达异常：
 *   · 有请求且成功率 < 90%        → danger「成功率偏低」
 *   · 有账号但 0 个可用           → warn「无可用账号」
 *   · 有请求                      → ok「正常」
 *   · 有账号、窗口内没请求         → neutral「空闲」
 *   · 0 账号且没请求              → 收进 idle（「未接入」）
 * 排序：有流量 > 有账号 > 其他（第 3 轮审查 A3 的同一排序原则）。
 */
export function buildPlatformHealth(
  traffic: StatsDashboardPayload | null | undefined,
  specs: OverviewPlatformSpec[] | null | undefined,
  translate?: Translate,
): OverviewHealth {
  const list = specs || []
  const groups = specGroups(list)
  const rows = traffic?.platforms || []
  const total = rows.reduce((sum, row) => sum + (Number(row.requests) || 0), 0)
  const seen = new Set<string>()
  const out: OverviewHealthRow[] = []
  const idle: string[] = []

  const accountsOf = (group: OverviewPlatformSpec[]): { accounts: number | null; active: number | null } => {
    const counts = group.map((spec) => spec.account_count).filter((n) => n !== null && n !== undefined)
    const actives = group.map((spec) => spec.active_count).filter((n) => n !== null && n !== undefined)
    return {
      accounts: counts.length ? counts.reduce((a, b) => a + (Number(b) || 0), 0) : null,
      active: actives.length ? actives.reduce((a, b) => a + (Number(b) || 0), 0) : null,
    }
  }

  const push = (
    id: string,
    name: string,
    logo: string,
    requests: number,
    rate: number | null,
    tokens: number,
    group: OverviewPlatformSpec[],
  ): void => {
    const { accounts, active } = accountsOf(group)
    let tone: OverviewHealthRow['tone'] = 'neutral'
    let status = tr(translate, 'overview.health.idle')
    if (requests > 0 && rate !== null && rate < 0.9) {
      tone = 'danger'
      status = tr(translate, 'overview.health.lowRate')
    } else if (accounts !== null && accounts > 0 && active === 0) {
      tone = 'warn'
      status = tr(translate, 'overview.health.noActive')
    } else if (requests > 0) {
      tone = 'ok'
      status = tr(translate, 'overview.health.ok')
    }
    if (requests <= 0 && (accounts === null || accounts <= 0) && tone === 'neutral') {
      idle.push(name)
      return
    }
    out.push({
      id,
      name,
      logo,
      requests,
      successRate: rate,
      rateText: rate === null ? '—' : fmtPct(rate, 1),
      share: total > 0 ? (requests / total) * 100 : 0,
      tokens,
      accounts,
      active,
      tone,
      status,
    })
  }

  for (const row of rows) {
    const id = String(row.value || '').trim()
    const spec = findSpec(list, id)
    const storage = String(spec?.storage_platform || spec?.id || id).toLowerCase()
    seen.add(storage)
    const group = groups.get(storage) || (spec ? [spec] : [])
    const requests = Number(row.requests) || 0
    const rate = row.success_rate === null || row.success_rate === undefined ? null : Number(row.success_rate)
    push(
      id,
      String(row.label || groupLabel(group.map((item) => item.label), id)),
      String(spec?.icon || id),
      requests,
      requests > 0 ? rate : null,
      Number(row.tokens) || 0,
      group,
    )
  }
  for (const [storage, group] of groups) {
    if (seen.has(storage)) continue
    push(
      group.length === 1 ? group[0].id : storage,
      groupLabel(group.map((spec) => spec.label), storage),
      String(group[0].icon || group[0].id),
      0,
      null,
      0,
      group,
    )
  }
  const rank = (row: OverviewHealthRow): number =>
    (row.requests > 0 ? 2 : 0) + (row.accounts !== null && row.accounts > 0 ? 1 : 0)
  out.sort((a, b) => rank(b) - rank(a) || b.requests - a.requests || a.name.localeCompare(b.name, 'zh-CN'))
  return { rows: out, idle }
}

/** 最近请求（/api/sentinel/status 合并下发的 traffic.recent，近 1 小时，全网关）。 */
export function buildRecent(
  sentinel: SentinelStatusPayload | null | undefined,
  limit = 8,
  translate?: Translate,
): LiveRow[] {
  if (!sentinel?.traffic) return []
  return toLiveRows(sentinel.traffic, limit, translate)
}

export interface OverviewKpi {
  key: string
  label: string
  value: string
  hint?: string
  tone?: 'brand' | 'ok' | 'warn' | 'danger' | 'neutral'
  spark?: number[]
}

/**
 * KPI 行（10-visual-refresh §1.4）：剩余额度 / 请求数 / 成功率 / Token 合计。
 * 数字与 buildBalanceHero / buildUsageItems 同口径（当前平台、自然日窗口）；
 * 迷你折线来自 getOverviewTrend（同平台同窗口），拿不到就不画。
 */
export function buildOverviewKpis(
  payload: OverviewPayload | null | undefined,
  trend: StatsDashboardPayload | null | undefined,
  days: number,
  translate?: Translate,
): OverviewKpi[] {
  const hero = buildBalanceHero(
    payload?.accounts,
    payload?.today,
    payload?.window,
    payload?.average_credits,
    translate,
  )
  const usage = buildUsageItems(payload?.window, days, translate)
  const spark = (trend?.trend?.total_requests ?? []).map((n) => Number(n) || 0)
  const win = payload?.window
  const toneOf = (tone: OverviewStatItem['tone']): OverviewKpi['tone'] =>
    tone === 'ok' || tone === 'warn' || tone === 'danger' || tone === 'brand' ? tone : 'neutral'
  return [
    {
      key: 'balance',
      label: tr(translate, 'page.overview.balance.remaining'),
      value: payload ? hero.remainText : '—',
      hint: hero.caption,
      tone: hero.tone,
    },
    {
      key: 'requests',
      label: usage[0].label,
      value: usage[0].value,
      hint: usage[0].foot,
      tone: 'brand',
      spark: spark.length > 1 ? spark : undefined,
    },
    {
      key: 'success',
      label: usage[1].label,
      value: usage[1].value,
      hint: win ? tr(translate, 'overview.kpi.successHint', { range: daysLabel(days, translate) }) : undefined,
      tone: toneOf(usage[1].tone),
    },
    {
      key: 'tokens',
      label: usage[4].label,
      value: usage[4].value,
      hint: win
        ? tr(translate, 'overview.kpi.tokenHint', {
            input: fmtCompact(win.prompt_tokens),
            output: fmtCompact(win.completion_tokens),
            cached: fmtCompact(win.cached_tokens),
          })
        : undefined,
      tone: 'brand',
    },
  ]
}

/**
 * 口径标签（第 1 轮审查 P0-6 / N5）：KPI 与账号段跟随当前平台，流量带 / 最近请求是全网关。
 * 新后端带 `scopes` 时以它为准；旧后端按「有 platform 就是该平台」推断。
 */
export function overviewScopeLabel(
  payload: OverviewPayload | null | undefined,
  section: 'usage' | 'accounts' | 'traffic',
  translate?: Translate,
): string {
  const declared = payload?.scopes?.[section]
  const gateway =
    section === 'traffic'
      ? declared !== 'platform'
      : declared
        ? declared === 'gateway'
        : !payload?.platform
  if (gateway) return tr(translate, 'overview.scope.gateway')
  return String(payload?.platform_label || payload?.platform || tr(translate, 'overview.scope.gateway'))
}
