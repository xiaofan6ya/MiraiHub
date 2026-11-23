/**
 * 风控哨兵（/api/sentinel/*）。
 *
 * 旧版实现：admin/console/console-app.js 的 loadSentinel / renderSentinel /
 * sentinelPause / sentinelResume / sentinelPollLogs；拓扑动画在 traffic.js。
 * 后端：admin/platform/sentinel_router.py、admin/sentinel.py、admin/traffic_stats.py。
 *
 * 两张数据源分工：
 *   · GET /api/sentinel/status  —— 信号、熔断判定、受影响账号、暂停开关（控制台轮询，低频）；
 *   · GET /api/sentinel/traffic —— 每平台今日累计 + 最近请求明细（实时，2 秒）。
 * status 里也会带一份 traffic（后端合并下发），实时列表优先用它，缺失时再退回 /traffic。
 */
import { http } from './http'
import { FMT_EMPTY, fmtInt, fmtRelative } from '@/utils/format'

/* ------------------------------------------------------------------ *
 * 类型（字段名与后端一致）
 * ------------------------------------------------------------------ */

/** sentinel.record() 落的事件。 */
export interface SentinelEvent {
  /** Unix 秒（带小数）。 */
  ts: number
  account_id: number
  kind: string
  code: string
  label: string
  signal: string
  signal_label: string
  /** risk | throttle | auth | info */
  severity: string
  unmapped: boolean
  detail: string
  model: string
  platform: string
}

/** by_account() 的账号聚合。 */
export interface SentinelAccountSignal {
  account_id: number
  count: number
  first_ts: number
  last_ts: number
  kind: string
  code: string
  label: string
  signal: string
  signal_label: string
  severity: string
  detail: string
  platform: string
}

export interface SentinelPlatformBucket {
  platform: string
  count: number
  risk: number
  last_ts: number
}

/** system_settings['sentinel:pause'] 里的一项。 */
export interface PauseEntry {
  /** Unix 秒；0 = 直到手动恢复。 */
  until?: number
  reason?: string
  at?: number
}

export interface TrafficPlatform {
  platform: string
  label: string
  requests: number
  ok: number
  error: number
  timeout: number
  interrupted: number
  last_at: number
  last_status: number
}

export interface TrafficTotals {
  requests: number
  ok: number
  error: number
  timeout: number
  interrupted: number
}

/** traffic_stats.snapshot() 的一条请求记录。 */
export interface TrafficRequest {
  rid?: string
  /** Unix 秒。 */
  ts?: number
  model?: string
  path?: string
  platform?: string
  status?: number | null
  /** running | completed | ok | timeout | interrupted | error … */
  outcome?: string
  error?: string
  stage?: string
  key_label?: string
  client_ip?: string
}

export interface TrafficPayload {
  day?: string
  window_seconds?: number
  platforms: TrafficPlatform[]
  totals: TrafficTotals
  recent: TrafficRequest[]
  running?: number
  storage?: string
  now?: number
}

/** GET /api/sentinel/status。 */
export interface SentinelStatus {
  tripped: boolean
  /** 参与熔断的真风控账号数（severity=risk）。 */
  accounts_affected: number
  accounts_total: number
  accounts_risk: number
  threshold: number
  window_seconds: number
  suggested_pause_seconds: number
  total_events: number
  by_code: Record<string, number>
  by_signal: Record<string, number>
  signal_labels: Record<string, string>
  by_severity: Record<string, number>
  by_platform: SentinelPlatformBucket[]
  accounts: SentinelAccountSignal[]
  recent: SentinelEvent[]
  /** 信号明细存 Redis 还是进程内存。 */
  redis: boolean
  pause: Record<string, PauseEntry>
  now: number
  traffic?: TrafficPayload
  alert?: Record<string, unknown>
}

export interface PauseInput {
  /** 空串 = 全部平台。 */
  platform?: string
  /** 0 = 直到手动恢复。 */
  seconds?: number
  reason?: string
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

export function getSentinelStatus(
  options: { window?: number; platform?: string } = {},
): Promise<SentinelStatus> {
  return http<SentinelStatus>('GET', '/api/sentinel/status', {
    query: { window: options.window ?? 300, platform: options.platform || '' },
  })
}

export function getTraffic(window = 3600): Promise<TrafficPayload> {
  return http<TrafficPayload>('GET', '/api/sentinel/traffic', { query: { window } })
}

/** 止损：暂停某平台（或全部）的上游请求，期间直接 503，不消耗额度。 */
export function pausePlatform(input: PauseInput = {}): Promise<{ ok: boolean; platform: string; pause: PauseEntry }> {
  return http('POST', '/api/sentinel/pause', {
    body: {
      platform: input.platform || '',
      seconds: input.seconds ?? 600,
      reason: input.reason || '手动止损',
    },
  })
}

export function resumePlatform(platform = ''): Promise<{ ok: boolean; resumed: string }> {
  return http('POST', '/api/sentinel/resume', { query: { platform } })
}

/* ------------------------------------------------------------------ *
 * 纯函数：接口响应 → 展示数据（单测覆盖）
 * ------------------------------------------------------------------ */

/** 四档严重程度（admin/sentinel.py）的展示口径。 */
export interface SeverityMeta {
  label: string
  tone: 'danger' | 'warn' | 'neutral' | 'brand'
  hint: string
}

export const SEVERITY_META: Record<string, SeverityMeta> = {
  risk: { label: '风控', tone: 'danger', hint: '上游在拦（WAF / 渠道 / 账号级风控 / 签名），会计入批次熔断判定。' },
  throttle: { label: '限流', tone: 'warn', hint: '限速 / 限流 / 排队 / 额度耗尽，属于运营态：只记录，不熔断。' },
  auth: { label: '登录', tone: 'neutral', hint: '登录态失效 / 退出登录：不算风控，需要重新登录，只记录。' },
  info: { label: '记录', tone: 'neutral', hint: '留痕用，没有止损动作。' },
}

export function severityMeta(severity: string | null | undefined): SeverityMeta {
  return SEVERITY_META[String(severity || 'info')] || SEVERITY_META.info
}

/** 成功率（0–100）着色：≥98 ok / ≥90 warn / 其余 danger（与旧版 KPI 一致）。 */
export type HealthTone = 'ok' | 'warn' | 'danger'

export function rateTone(rate: number | null | undefined): HealthTone {
  // 没有数据（null/undefined）按「未知」处理为 warn，不能用 Number(null)=0 误判成 danger。
  if (rate === null || rate === undefined) return 'warn'
  const value = Number(rate)
  if (!Number.isFinite(value)) return 'warn'
  if (value >= 98) return 'ok'
  if (value >= 90) return 'warn'
  return 'danger'
}

/** 平台合计成功率（0–100）；没请求给 null（不伪造 0%）。 */
export function successRate(row: Pick<TrafficPlatform, 'requests' | 'ok'>): number | null {
  const requests = Number(row.requests) || 0
  if (!requests) return null
  return (Number(row.ok) || 0) / requests * 100
}

/** 最近一次信号的人话时间（旧版 sentinelLastSignalText）。 */
export function lastSignalText(accounts: SentinelAccountSignal[], nowMs = Date.now()): string {
  const stamps = (accounts || []).map((item) => Number(item.last_ts) || 0).filter(Boolean)
  if (!stamps.length) return FMT_EMPTY
  return fmtRelative(Math.max(...stamps), nowMs)
}

export interface SentinelBanner {
  tripped: boolean
  tone: 'ok' | 'danger'
  title: string
  detail: string
  /** 检测到批次封控时要暂停的平台（取信号最多的那个）。 */
  platform: string
  platformLabel: string
  suggestedSeconds: number
}

/** 结论横幅：批次封控判定 + 阈值说明 + 建议止损平台。 */
export function toBanner(status: SentinelStatus | null, traffic: TrafficPayload | null): SentinelBanner {
  const tripped = Boolean(status?.tripped)
  const windowMinutes = Math.max(1, Math.round(Number(status?.window_seconds || 300) / 60))
  const affected = Number(status?.accounts_affected || 0)
  const threshold = Number(status?.threshold || 3)
  const buckets = status?.by_platform || []
  const worst = [...buckets].sort((a, b) => (Number(b.risk) || 0) - (Number(a.risk) || 0))[0]
  const labelMap = new Map((traffic?.platforms || []).map((row) => [row.platform, row.label]))
  const platform = String(worst?.platform || '')
  return {
    tripped,
    tone: tripped ? 'danger' : 'ok',
    title: tripped
      ? '检测到批次级封控：最近 ' + windowMinutes + ' 分钟内有 ' + affected + ' 个账号被风控'
      : '未检测到批次级封控',
    detail:
      '阈值 ' + threshold + ' 个账号 / ' + windowMinutes + ' 分钟' +
      (tripped ? ' · 建议暂停 ' + Number(status?.suggested_pause_seconds || 0) + ' 秒止损' : ''),
    platform,
    platformLabel: platform ? labelMap.get(platform) || platform : '',
    suggestedSeconds: Number(status?.suggested_pause_seconds || 600),
  }
}

/** 暂停中的平台键（'all' = 全部平台）。 */
export function pausedKeys(status: SentinelStatus | null): string[] {
  const pause = status?.pause || {}
  return Object.keys(pause).filter((key) => {
    const entry = pause[key]
    const until = Number(entry?.until || 0)
    return !until || until * 1000 > Date.now()
  })
}

export function pausedLabel(key: string, traffic: TrafficPayload | null): string {
  if (key === 'all') return '全部平台'
  const row = (traffic?.platforms || []).find((item) => item.platform === key)
  return row?.label || key
}

/** 6 格 StatStrip（P2 的 ASCII：今日请求 / 成功率 / 失败超时 / 信号 / 涉及账号 / 暂停中的平台）。 */
export interface SentinelStat {
  label: string
  value: string
  foot?: string
  tone?: 'default' | 'brand' | 'ok' | 'warn' | 'danger' | 'muted'
  help?: string
}

export function toStats(
  status: SentinelStatus | null,
  traffic: TrafficPayload | null,
): SentinelStat[] {
  const totals = traffic?.totals || { requests: 0, ok: 0, error: 0, timeout: 0, interrupted: 0 }
  const requests = Number(totals.requests) || 0
  const ok = Number(totals.ok) || 0
  const failed = (Number(totals.error) || 0) + (Number(totals.timeout) || 0)
  const rate = requests ? ok / requests * 100 : 100
  const platforms = traffic?.platforms || []
  const activePlatforms = platforms.filter((row) => Number(row.requests) > 0).length
  const worst = platforms
    .map((row) => ({ label: row.label || row.platform, bad: Number(row.error || 0) + Number(row.timeout || 0) }))
    .filter((item) => item.bad > 0)
    .sort((a, b) => b.bad - a.bad)[0]
  const paused = pausedKeys(status)
  return [
    {
      label: '今日请求',
      value: fmtInt(requests),
      foot: activePlatforms + ' / ' + platforms.length + ' 个平台有流量',
    },
    {
      label: '成功率',
      value: requests ? rate.toFixed(1) + '%' : FMT_EMPTY,
      foot: '成功 ' + fmtInt(ok) + ' 次',
      tone: rateTone(requests ? rate : 100),
    },
    {
      label: '失败 / 超时',
      value: fmtInt(failed),
      foot: worst ? '最多：' + worst.label + ' ' + fmtInt(worst.bad) + ' 次' : '暂无失败',
      tone: failed ? 'danger' : 'ok',
    },
    {
      label: '信号',
      value: fmtInt(status?.total_events || 0),
      foot: '最近 ' + lastSignalText(status?.accounts || []),
      help: '窗口内的风控信号条数；明细存 Redis，保留 15 分钟。',
    },
    {
      label: '涉及账号',
      value: fmtInt(status?.accounts_affected || 0),
      foot:
        '原因 ' + Object.keys(status?.by_code || {}).length + ' 种 · 仅真风控参与熔断',
      tone: Number(status?.accounts_affected || 0) ? 'warn' : 'default',
    },
    {
      label: '暂停中的平台',
      value: fmtInt(paused.length),
      foot: paused.length ? paused.map((key) => pausedLabel(key, traffic)).join('、') : '全部平台在跑',
      tone: paused.length ? 'warn' : 'default',
    },
  ]
}

export interface RibbonView {
  source: { label: string; total: number }
  lanes: Array<{
    id: string
    name: string
    logo: string
    count: number
    successRate: number | null
    status: string
  }>
}

/** RouteRibbon 数据：线宽=请求占比、颜色=成功率（设计稿要求按成功率着色）。 */
export function toRibbon(traffic: TrafficPayload | null): RibbonView {
  const platforms = [...(traffic?.platforms || [])]
    .filter((row) => Number(row.requests) > 0)
    .sort((a, b) => Number(b.requests) - Number(a.requests))
  return {
    source: { label: '客户端请求', total: Number(traffic?.totals?.requests || 0) },
    lanes: platforms.map((row) => ({
      id: row.platform,
      name: row.label || row.platform,
      logo: row.platform,
      count: Number(row.requests) || 0,
      successRate: successRate(row),
      status: row.last_status ? 'HTTP ' + row.last_status : '',
    })),
  }
}

export interface PlatformHealthRow {
  platform: string
  label: string
  requests: number
  ok: number
  failed: number
  rate: number | null
  rateText: string
  tone: HealthTone
  items: Array<{ label: string; value: number; tone: 'ok' | 'danger' }>
}

/** 平台健康：StackMeter 两段（成功 / 失败·超时）+ 成功率着色。 */
export function toPlatformHealth(traffic: TrafficPayload | null): PlatformHealthRow[] {
  return [...(traffic?.platforms || [])]
    .filter((row) => Number(row.requests) > 0)
    .map((row) => {
      const requests = Number(row.requests) || 0
      const ok = Number(row.ok) || 0
      const failed = Math.max(0, requests - ok)
      const rate = successRate(row)
      return {
        platform: row.platform,
        label: row.label || row.platform,
        requests,
        ok,
        failed,
        rate,
        rateText: rate == null ? FMT_EMPTY : rate.toFixed(1) + '%',
        tone: rateTone(rate),
        items: [
          { label: '成功', value: ok, tone: 'ok' as const },
          { label: '失败 / 超时', value: failed, tone: 'danger' as const },
        ],
      }
    })
    .sort((a, b) => b.requests - a.requests)
}

export type LiveTone = 'ok' | 'warn' | 'danger' | 'brand'

export interface LiveRow {
  rid: string
  time: string
  model: string
  platform: string
  platformLabel: string
  statusText: string
  tone: LiveTone
  running: boolean
  error: string
}

/**
 * 单条请求的状态→语气（旧版 sentinelToneOf 的口径）：
 * 500+ danger、4xx warn、2xx/3xx ok（流式正常收尾记成 interrupted 也算成功）；
 * 没有状态码时才看 outcome / error；running 单独用 brand。
 */
export function requestTone(item: TrafficRequest): LiveTone {
  const status = Number(item.status || 0)
  if (item.outcome === 'running') return 'brand'
  if (status >= 500) return 'danger'
  if (status >= 400) return 'warn'
  if (status >= 200) return 'ok'
  if (item.outcome === 'timeout') return 'danger'
  const kind = String(item.error || '').trim().toLowerCase()
  if (kind && !['success', 'ok', 'none', '0'].includes(kind)) return 'danger'
  if (!status && !kind) return 'ok'
  return 'warn'
}

/** 实时请求列表：最多 50 行（新的在上），状态码着色。 */
export function toLiveRows(traffic: TrafficPayload | null, limit = 50): LiveRow[] {
  const labelMap = new Map((traffic?.platforms || []).map((row) => [row.platform, row.label]))
  return (traffic?.recent || []).slice(0, Math.max(1, limit)).map((item, index) => {
    const status = Number(item.status || 0)
    const running = item.outcome === 'running'
    const statusText = running
      ? '进行中'
      : item.outcome === 'timeout'
        ? '超时'
        : status
          ? String(status)
          : item.outcome || FMT_EMPTY
    return {
      rid: String(item.rid || item.ts || 'row-' + index),
      time: item.ts ? fmtRelative(Number(item.ts) * 1000, Date.now()) : FMT_EMPTY,
      model: item.model || item.path || FMT_EMPTY,
      platform: item.platform || '',
      platformLabel: (item.platform && labelMap.get(item.platform)) || item.platform || FMT_EMPTY,
      statusText,
      tone: requestTone(item),
      running,
      error: item.error || '',
    }
  })
}

export interface SignalRow extends Record<string, unknown> {
  id: string
  ts: number
  time: string
  platform: string
  platformLabel: string
  accountId: number
  code: string
  label: string
  severity: string
  severityLabel: string
  severityTone: string
  signal: string
  signalLabel: string
  unmapped: boolean
  detail: string
  model: string
}

export interface AccountSignalRow extends Record<string, unknown> {
  id: string
  platform: string
  platformLabel: string
  accountId: number
  label: string
  code: string
  severity: string
  severityLabel: string
  severityTone: string
  signalLabel: string
  count: number
  firstTime: string
  lastTime: string
  lastTs: number
}

function timeText(ts: number): string {
  const value = Number(ts)
  if (!Number.isFinite(value) || value <= 0) return FMT_EMPTY
  return fmtRelative(value * 1000, Date.now())
}

/** 封控信号明细行（设计稿「封控信号」表格：时间 · 平台 · 账号 · 信号码 · 原因 · 操作）。 */
export function toSignalRows(status: SentinelStatus | null, traffic: TrafficPayload | null): SignalRow[] {
  const labelMap = new Map((traffic?.platforms || []).map((row) => [row.platform, row.label]))
  return (status?.recent || []).map((event, index) => {
    const meta = severityMeta(event.severity)
    const signalLabel =
      event.signal_label || (status?.signal_labels || {})[event.signal] || event.signal || FMT_EMPTY
    return {
      id: String(event.ts) + '-' + (event.account_id || 0) + '-' + index,
      ts: Number(event.ts) || 0,
      time: timeText(event.ts),
      platform: event.platform || 'unknown',
      platformLabel: labelMap.get(event.platform) || event.platform || '未知平台',
      accountId: Number(event.account_id) || 0,
      code: event.code || '-',
      label: event.label || event.kind || '',
      severity: event.severity || 'info',
      severityLabel: meta.label,
      severityTone: meta.tone,
      signal: event.signal || '',
      signalLabel,
      unmapped: Boolean(event.unmapped),
      detail: event.detail || '',
      model: event.model || '',
    }
  })
}

/** 受影响账号行（旧版「受影响账号」表，按最近信号排序）。 */
export function toAccountRows(status: SentinelStatus | null, traffic: TrafficPayload | null): AccountSignalRow[] {
  const labelMap = new Map((traffic?.platforms || []).map((row) => [row.platform, row.label]))
  return (status?.accounts || []).map((item, index) => {
    const meta = severityMeta(item.severity)
    return {
      id: 'acc-' + item.account_id + '-' + index,
      platform: item.platform || 'unknown',
      platformLabel: labelMap.get(item.platform) || item.platform || '未知平台',
      accountId: Number(item.account_id) || 0,
      label: item.label || item.kind || '',
      code: item.code || '-',
      severity: item.severity || 'info',
      severityLabel: meta.label,
      severityTone: meta.tone,
      signalLabel: item.signal_label || (status?.signal_labels || {})[item.signal] || item.signal || '',
      count: Number(item.count) || 0,
      firstTime: timeText(item.first_ts),
      lastTime: timeText(item.last_ts),
      lastTs: Number(item.last_ts) || 0,
    }
  })
}

/** 页头「存储：Redis / 内存」文案。 */
export function storageText(status: SentinelStatus | null, traffic: TrafficPayload | null): string {
  if (traffic?.storage) return traffic.storage === 'redis' ? 'Redis' : '内存'
  return status?.redis ? 'Redis' : '内存'
}