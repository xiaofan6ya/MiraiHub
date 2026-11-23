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
import { t as defaultTranslate, type Translate } from '@/i18n'

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

/** 四档的语气固定；label / hint 走字典（默认中文与旧文案逐字一致）。 */
const SEVERITY_TONE: Record<string, SeverityMeta['tone']> = {
  risk: 'danger',
  throttle: 'warn',
  auth: 'neutral',
  info: 'neutral',
}

const SEVERITY_KEYS: Record<string, { label: Parameters<Translate>[0]; hint: Parameters<Translate>[0] }> = {
  risk: { label: 'page.sentinel.severity.risk', hint: 'page.sentinel.severity.riskHint' },
  throttle: { label: 'page.sentinel.severity.throttle', hint: 'page.sentinel.severity.throttleHint' },
  auth: { label: 'page.sentinel.severity.auth', hint: 'page.sentinel.severity.authHint' },
  info: { label: 'page.sentinel.severity.info', hint: 'page.sentinel.severity.infoHint' },
}

export function severityMeta(severity: string | null | undefined, translate?: Translate): SeverityMeta {
  const tr = translate ?? defaultTranslate
  const key = SEVERITY_KEYS[String(severity || 'info')] ? String(severity || 'info') : 'info'
  return {
    label: tr(SEVERITY_KEYS[key].label),
    tone: SEVERITY_TONE[key] || 'neutral',
    hint: tr(SEVERITY_KEYS[key].hint),
  }
}

/** 保留旧导出：默认中文的四档表（页面/单测直接读时行为不变）。 */
export const SEVERITY_META: Record<string, SeverityMeta> = {
  risk: severityMeta('risk'),
  throttle: severityMeta('throttle'),
  auth: severityMeta('auth'),
  info: severityMeta('info'),
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
export function toBanner(
  status: SentinelStatus | null,
  traffic: TrafficPayload | null,
  translate?: Translate,
): SentinelBanner {
  const tr = translate ?? defaultTranslate
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
      ? tr('page.sentinel.banner.tripped', { minutes: windowMinutes, accounts: affected })
      : tr('page.sentinel.banner.ok'),
    detail:
      tr('page.sentinel.banner.detail', { threshold, minutes: windowMinutes }) +
      (tripped
        ? tr('page.sentinel.banner.detailPause', { seconds: Number(status?.suggested_pause_seconds || 0) })
        : ''),
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

export function pausedLabel(key: string, traffic: TrafficPayload | null, translate?: Translate): string {
  if (key === 'all') return (translate ?? defaultTranslate)('page.sentinel.allPlatforms')
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
  translate?: Translate,
): SentinelStat[] {
  const tr = translate ?? defaultTranslate
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
      label: tr('page.sentinel.stat.today'),
      value: fmtInt(requests),
      foot: tr('page.sentinel.stat.requestsFoot', { active: activePlatforms, total: platforms.length }),
    },
    {
      label: tr('page.sentinel.stat.rate'),
      value: requests ? rate.toFixed(1) + '%' : FMT_EMPTY,
      foot: tr('page.sentinel.stat.rateFoot', { count: fmtInt(ok) }),
      tone: rateTone(requests ? rate : 100),
    },
    {
      label: tr('page.sentinel.stat.failed'),
      value: fmtInt(failed),
      foot: worst
        ? tr('page.sentinel.stat.failedFootWorst', { label: worst.label, count: fmtInt(worst.bad) })
        : tr('page.sentinel.stat.failedFootNone'),
      tone: failed ? 'danger' : 'ok',
    },
    {
      label: tr('page.sentinel.stat.signals'),
      value: fmtInt(status?.total_events || 0),
      foot: tr('page.sentinel.stat.signalsFoot', { text: lastSignalText(status?.accounts || []) }),
      help: tr('page.sentinel.stat.signalsHelp'),
    },
    {
      label: tr('page.sentinel.stat.accounts'),
      value: fmtInt(status?.accounts_affected || 0),
      foot: tr('page.sentinel.stat.accountsFoot', {
        count: Object.keys(status?.by_code || {}).length,
      }),
      tone: Number(status?.accounts_affected || 0) ? 'warn' : 'default',
    },
    {
      label: tr('page.sentinel.stat.paused'),
      value: fmtInt(paused.length),
      foot: paused.length
        ? paused.map((key) => pausedLabel(key, traffic, translate)).join(tr('page.sentinel.listSeparator'))
        : tr('page.sentinel.stat.pausedFoot'),
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
export function toRibbon(traffic: TrafficPayload | null, translate?: Translate): RibbonView {
  const tr = translate ?? defaultTranslate
  const platforms = [...(traffic?.platforms || [])]
    .filter((row) => Number(row.requests) > 0)
    .sort((a, b) => Number(b.requests) - Number(a.requests))
  return {
    source: { label: tr('page.sentinel.ribbonSource'), total: Number(traffic?.totals?.requests || 0) },
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
export function toPlatformHealth(
  traffic: TrafficPayload | null,
  translate?: Translate,
): PlatformHealthRow[] {
  const tr = translate ?? defaultTranslate
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
          { label: tr('page.sentinel.health.segOk'), value: ok, tone: 'ok' as const },
          { label: tr('page.sentinel.stat.failed'), value: failed, tone: 'danger' as const },
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
export function toLiveRows(
  traffic: TrafficPayload | null,
  limit = 50,
  translate?: Translate,
): LiveRow[] {
  const tr = translate ?? defaultTranslate
  const labelMap = new Map((traffic?.platforms || []).map((row) => [row.platform, row.label]))
  return (traffic?.recent || []).slice(0, Math.max(1, limit)).map((item, index) => {
    const status = Number(item.status || 0)
    const running = item.outcome === 'running'
    const statusText = running
      ? tr('page.sentinel.live.running')
      : item.outcome === 'timeout'
        ? tr('page.sentinel.live.timeout')
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
export function toSignalRows(
  status: SentinelStatus | null,
  traffic: TrafficPayload | null,
  translate?: Translate,
): SignalRow[] {
  const tr = translate ?? defaultTranslate
  const unknown = tr('page.sentinel.unknownPlatform')
  const labelMap = new Map((traffic?.platforms || []).map((row) => [row.platform, row.label]))
  return (status?.recent || []).map((event, index) => {
    const meta = severityMeta(event.severity, translate)
    const signalLabel =
      event.signal_label || (status?.signal_labels || {})[event.signal] || event.signal || FMT_EMPTY
    return {
      id: String(event.ts) + '-' + (event.account_id || 0) + '-' + index,
      ts: Number(event.ts) || 0,
      time: timeText(event.ts),
      platform: event.platform || 'unknown',
      platformLabel: labelMap.get(event.platform) || event.platform || unknown,
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
export function toAccountRows(
  status: SentinelStatus | null,
  traffic: TrafficPayload | null,
  translate?: Translate,
): AccountSignalRow[] {
  const tr = translate ?? defaultTranslate
  const unknown = tr('page.sentinel.unknownPlatform')
  const labelMap = new Map((traffic?.platforms || []).map((row) => [row.platform, row.label]))
  return (status?.accounts || []).map((item, index) => {
    const meta = severityMeta(item.severity, translate)
    return {
      id: 'acc-' + item.account_id + '-' + index,
      platform: item.platform || 'unknown',
      platformLabel: labelMap.get(item.platform) || item.platform || unknown,
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
export function storageText(
  status: SentinelStatus | null,
  traffic: TrafficPayload | null,
  translate?: Translate,
): string {
  const memory = (translate ?? defaultTranslate)('page.sentinel.storage.memory')
  if (traffic?.storage) return traffic.storage === 'redis' ? 'Redis' : memory
  return status?.redis ? 'Redis' : memory
}