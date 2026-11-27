/**
 * 用量日志（/api/logs/*）。
 *
 * 后端：admin/routers/logs.py —— list_logs / log_summary / log_accounts /
 * log_detail / capture_body / capture_settings / export_logs。
 * 字段名与后端完全一致（snake_case 原样保留）。
 *
 * 说明：
 *   · 列表支持 keyset（before_id）与 OFFSET（page）两种翻页；深翻页走 keyset，只扫主键。
 *   · 「按账号汇总」的后端 log_summary 不接 platform/realm（见 logs.py:1128），
 *     所以平台筛选只作用于列表，汇总按全网关口径 —— 旧版也是如此。
 *   · 请求正文是进程内缓存（admin/request_payload.py），只能按 64 KiB 分页读，不进数据库。
 */
import { download, http, type Query } from './http'
import { FMT_EMPTY, fmtCredit, fmtDuration, fmtLocalHM, fmtFullTime, fmtInt, fmtTime, toDate } from '@/utils/format'
import { t, type MessageKey } from '@/i18n'

function tx(key: string, params?: Record<string, string | number>): string {
  return t(key as MessageKey, params)
}

export type LogResultFilter = '' | 'success' | 'error'

/** `GET /api/logs` 的 items[]（_serialize 的真实字段）。 */
export interface UsageLogRow {
  id: number
  key_name: string
  key_id: number
  account_id: number
  account_name: string
  platform: string
  /** 可读模型名（后端 _display_model 已把槽位名翻成对外名）。 */
  model: string
  /** 落库原值（历史行是槽位名）。 */
  model_raw: string
  credits: number
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cached_tokens: number | null
  client_ip: string
  use_case: string
  reasoning_effort: string
  seq: number | null
  ttfb_ms: number | null
  latency_ms: number | null
  /** '' / 'success' = 成功；其他值为失败分类。 */
  error_kind: string
  created_at: string | null
}

export interface UsageLogStats {
  total: number
  success: number
  error: number
}

export interface UsageLogListPayload {
  items: UsageLogRow[]
  /** null = 本次未统计总数（快速翻页 / 非第一页 keyset）。 */
  total: number | null
  stats: UsageLogStats | null
  page: number
  page_size: number
  has_more: boolean
  next_before_id: number | null
}

export interface UsageLogListQuery {
  page?: number
  page_size?: number
  before_id?: number | string
  after_id?: number | string
  platform?: string
  realm?: string
  key_id?: number | string
  account_id?: number | string
  account?: string
  model?: string
  key?: string
  client_ip?: string
  result?: string
  start?: string
  end?: string
  with_total?: boolean
}

export interface UsageLogSummaryItem {
  account_id: number
  account_name: string
  requests: number
  failed_requests: number
  ok_requests: number
  credits: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  avg_latency_ms: number
  last_used_at: string | null
}

export interface UsageLogSummaryPayload {
  range_days: number
  total_requests: number
  ok_requests: number
  failed_requests: number
  account_count: number
  unattributed_requests: number
  total_credits: number
  total_tokens: number
  window_minutes: number
  items: UsageLogSummaryItem[]
}

export interface LogAccountOption {
  id: number
  name: string
  uid: string
  status: string
  balance_remain: number
  expiring_soon: number
  requests: number | null
  credits: number | null
  last_used_at: string | null
}

export interface LogAccountsPayload {
  range_days: number
  items: LogAccountOption[]
}

/** `/api/logs/{id}` 的 request_capture（admin/request_payload.py:_public(include_bodies=False)）。 */
export interface CapturePart {
  headers: Record<string, string>
  body_bytes?: number
  body_cached_bytes?: number
  body_truncated?: boolean
}

export interface CaptureUpstream extends CapturePart {
  attempt: number
  method: string
  url: string
  query_keys: string[]
  account_id: number | null
  dispatch_at: string | null
  http_status: number | null
  body_available: boolean
}

export interface RequestCapture {
  captured_at: string
  ingress: CapturePart
  upstream: CaptureUpstream[]
  upstream_omitted: number
}

export interface UsageLogDetail extends UsageLogRow {
  request_capture: RequestCapture | null
  /** 诊断 JSON（网关错误 / 上游原文），形状随版本变化，按原样展示。 */
  diagnostic: Record<string, unknown> | null
}

export interface CaptureBodyPage {
  text: string
  offset: number
  next_offset: number
  has_more: boolean
  cached_bytes: number
  body_bytes?: number | null
  body_truncated: boolean
}

/** `GET /api/logs/capture/settings`（admin/request_payload.py:status）。 */
export interface CaptureSettings {
  enabled: boolean
  ttl_seconds: number
  max_cache_bytes: number
  max_entries: number
  cached_requests: number
  cache_bytes: number
  active_bytes: number
  record_max_bytes: number
  body_max_bytes: number
  storage: string
  restart_clears: boolean
  source: string
}

/** `GET /api/logs/requests` 的一帧（_activity_probe 的 active/recent 项）。 */
export interface LiveRequest {
  request_id: number
  key_id: number
  account_id: number | null
  client_ip: string
  path: string
  model: string
  started_at: string
  status: number | null
  outcome: string
  elapsed_ms: number
  phase: string
  reason: string
  upstream_attempts: number
  key_name?: string
  account_name?: string
}

export interface LiveRequestsPayload {
  active: LiveRequest[]
  active_count: number
  recent: LiveRequest[]
  usage_revision: number | null
  active_scope?: string
  history_persistent?: boolean
  degraded?: boolean
  mode?: string
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

export function listUsageLogs(query: UsageLogListQuery, signal?: AbortSignal): Promise<UsageLogListPayload> {
  return http<UsageLogListPayload>('GET', '/api/logs', { query: query as Query, signal })
}

export function getUsageSummary(
  query: Record<string, string | number | undefined>,
  signal?: AbortSignal,
): Promise<UsageLogSummaryPayload> {
  return http<UsageLogSummaryPayload>('GET', '/api/logs/summary', { query: query as Query, signal })
}

/** 账号下拉：只列窗口内真实产生过请求的账号（后端 /api/logs/accounts）。 */
export function listLogAccounts(
  days: number,
  compact = true,
  signal?: AbortSignal,
): Promise<LogAccountsPayload> {
  return http<LogAccountsPayload>('GET', '/api/logs/accounts', {
    query: { days, compact },
    signal,
  })
}

export function getUsageLog(id: number, signal?: AbortSignal): Promise<UsageLogDetail> {
  return http<UsageLogDetail>('GET', '/api/logs/' + id, { signal })
}

/** 读请求正文的一页（最多 64 KiB）。part = 'ingress' | 'upstream:<index>'。 */
export function getCaptureBody(
  logId: number,
  part: string,
  offset = 0,
  signal?: AbortSignal,
): Promise<CaptureBodyPage> {
  return http<CaptureBodyPage>('GET', '/api/logs/' + logId + '/capture/body', {
    query: { part, offset, limit: 65536 },
    signal,
  })
}

export function getCaptureSettings(signal?: AbortSignal): Promise<CaptureSettings> {
  return http<CaptureSettings>('GET', '/api/logs/capture/settings', { signal })
}

export function setCaptureEnabled(enabled: boolean): Promise<CaptureSettings> {
  return http<CaptureSettings>('PUT', '/api/logs/capture/settings', { body: { enabled } })
}

export function clearCaptureCache(): Promise<CaptureSettings> {
  return http<CaptureSettings>('DELETE', '/api/logs/capture/cache')
}

export function getLiveRequests(
  params: { limit?: number; includeHistory?: boolean; key?: string } = {},
  signal?: AbortSignal,
): Promise<LiveRequestsPayload> {
  return http<LiveRequestsPayload>('GET', '/api/logs/requests', {
    query: {
      limit: params.limit ?? 40,
      include_history: params.includeHistory !== false,
      key: params.key || undefined,
    },
    signal,
  })
}

/** 换一张 2 分钟有效、绑定会话与 IP 的 SSE 票据（EventSource 不能带请求头）。 */
export function requestStreamTicket(): Promise<{ ticket: string; expires_in: number }> {
  return http<{ ticket: string; expires_in: number }>('GET', '/api/logs/requests/ticket')
}

export async function downloadLogsExport(
  query: Record<string, string | number | boolean | undefined>,
  diagnostics = false,
): Promise<string> {
  const result = await download(
    '/api/logs/export',
    { ...query, ...(diagnostics ? { diagnostics: true, limit: 1000 } : {}) } as Query,
    'usage_logs.csv',
  )
  return result.filename
}

export async function downloadLogCapture(logId: number): Promise<string> {
  const result = await download('/api/logs/' + logId + '/capture/download')
  return result.filename
}

/* ------------------------------------------------------------------ *
 * 筛选 → 请求
 * ------------------------------------------------------------------ */

/** FilterBar 的全部筛选（key 与路由 query 一致）。 */
export interface LogFilterState {
  platform: string
  /** 账号名模糊筛选（其他页带 ?account= 进来时用）。 */
  account: string
  /** 账号下拉选中的精确 id。 */
  account_id: string
  model: string
  key: string
  result: string
  ip: string
  /** today | hour | 1 | 3 | 7 | 30 | 0 */
  range: string
  /** YYYY-MM-DD，显式日期优先于 range。 */
  start: string
  end: string
  /** '1' = 快速翻页（不统计总数）。 */
  fast: string
}

export const LOG_RANGE_OPTIONS = [
  { get label() { return tx('page.logs.rangeOpt.today') }, value: 'today' },
  { get label() { return tx('page.logs.rangeOpt.hour') }, value: 'hour' },
  { get label() { return tx('page.logs.rangeOpt.d1') }, value: '1' },
  { get label() { return tx('page.logs.rangeOpt.d3') }, value: '3' },
  { get label() { return tx('page.logs.rangeOpt.d7') }, value: '7' },
  { get label() { return tx('page.logs.rangeOpt.d30') }, value: '30' },
  { get label() { return tx('page.logs.rangeOpt.all') }, value: '0' },
] as const

export const LOG_RESULT_OPTIONS = [
  { get label() { return tx('page.logs.resultOpt.all') }, value: '' },
  { get label() { return tx('page.logs.resultOpt.error') }, value: 'error' },
  { get label() { return tx('page.logs.resultOpt.success') }, value: 'success' },
] as const

export interface TimeWindow {
  start: string
  end: string
}

/** 北京时间的「今天 00:00」→ UTC ISO 串（与旧版 _logQuery 同一口径）。 */
export function beijingMidnightIso(now: number): string {
  const shifted = new Date(now + 8 * 3600 * 1000)
  const midnight = Date.UTC(
    shifted.getUTCFullYear(),
    shifted.getUTCMonth(),
    shifted.getUTCDate(),
  ) - 8 * 3600 * 1000
  return new Date(midnight).toISOString()
}

/** 日期输入（YYYY-MM-DD）→ 后端要的 'YYYY-MM-DD HH:MM:SS'。 */
function dateWithTime(date: string, time: string): string {
  const value = String(date || '').trim()
  if (!value) return ''
  // 支持 <input type="date"> 的 YYYY-MM-DD；带时间时原样保留。
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value + ' ' + time : value
}

/**
 * 时间范围 → start/end。
 *
 * 语义（与旧版一致）：
 *   · 显式起止日期优先（起始 00:00:00 / 结束 23:59:59）；
 *   · 「今日」= 北京时间自然日 00:00 起（ISO，带 Z）；
 *   · 「近 1 小时」/「近 N 天」= 从 now 倒退滚动窗口；
 *   · 「不限时间」= 不带 start。
 */
export function resolveTimeWindow(state: Pick<LogFilterState, 'range' | 'start' | 'end'>, now = Date.now()): TimeWindow {
  const end = dateWithTime(state.end, '23:59:59')
  if (state.start) return { start: dateWithTime(state.start, '00:00:00'), end }
  const range = String(state.range || 'today')
  if (range === 'today') return { start: beijingMidnightIso(now), end }
  if (range === 'hour') return { start: new Date(now - 3600 * 1000).toISOString(), end }
  const days = Number(range)
  if (Number.isFinite(days) && days > 0) {
    return { start: new Date(now - days * 86400 * 1000).toISOString(), end }
  }
  return { start: '', end }
}

function commonFilters(state: LogFilterState): Record<string, string | number> {
  const query: Record<string, string | number> = {}
  if (state.platform) query.platform = state.platform
  if (state.account_id) query.account_id = Number(state.account_id)
  if (state.account) query.account = state.account
  if (state.model) query.model = state.model
  if (state.key) query.key = state.key
  if (state.ip) query.client_ip = state.ip
  if (state.result) query.result = state.result
  return query
}

export interface LogListQueryOptions {
  page: number
  pageSize: number
  /** keyset 游标（第 2 页起优先用它；没有则退回 OFFSET）。 */
  beforeId?: number | null
  now?: number
}

/** 列表查询：keyset 优先；第一页且非快速模式才统计总数。 */
export function buildLogQuery(
  state: LogFilterState,
  options: LogListQueryOptions,
): { query: UsageLogListQuery; window: TimeWindow } {
  const now = options.now ?? Date.now()
  const window = resolveTimeWindow(state, now)
  const page = Math.max(1, Math.floor(options.page || 1))
  const query: UsageLogListQuery = {
    page,
    page_size: Math.max(1, Math.floor(options.pageSize || 20)),
    ...commonFilters(state),
  }
  if (window.start) query.start = window.start
  if (window.end) query.end = window.end
  if (options.beforeId) query.before_id = options.beforeId
  const fast = state.fast === '1'
  if (!fast && page === 1 && !options.beforeId) query.with_total = true
  return { query, window }
}

/**
 * 汇总查询。
 *
 * 注意：后端 log_summary **没有** platform 参数（logs.py:1128），
 * 所以这里刻意不带 platform —— 带了也只会被忽略。契约里已注明。
 */
export function buildSummaryQuery(
  state: LogFilterState,
  now = Date.now(),
): Record<string, string | number> {
  const window = resolveTimeWindow(state, now)
  const query: Record<string, string | number> = commonFilters(state)
  delete query.platform
  const explicit = Boolean(state.start)
  const range = String(state.range || 'today')
  if (explicit || range === 'today' || range === 'hour') {
    // 固定起点：交给 start；days=0 让后端不要再叠加一个滚动窗口。
    if (window.start) query.start = window.start
    query.days = 0
  } else {
    // 滚动窗口：只给 days（后端自己算 window_start），不要再带 start，
    // 否则两个口径叠加，汇总会少算窗口前段。
    const days = Number(range)
    query.days = Number.isFinite(days) && days > 0 ? days : 0
  }
  if (window.end) query.end = window.end
  return query
}

/** 导出查询：与列表同口径，去掉分页参数。 */
export function buildExportQuery(
  state: LogFilterState,
  now = Date.now(),
): Record<string, string | number> {
  const window = resolveTimeWindow(state, now)
  const query: Record<string, string | number> = commonFilters(state)
  if (window.start) query.start = window.start
  if (window.end) query.end = window.end
  return query
}

/* ------------------------------------------------------------------ *
 * 展示转换
 * ------------------------------------------------------------------ */

export interface LogRowView extends UsageLogRow {
  timeShort: string
  timeFull: string
  isError: boolean
  resultLabel: string
  resultTone: 'ok' | 'danger'
  /** 缓存命中率（0–100）；null = 上游没给缓存字段或没有输入 token。 */
  cacheRate: number | null
  cacheRateText: string
  ttfbText: string
  latencyText: string
  tpsText: string
  tokensText: string
  useCaseLabel: string
}

/** 用途列短名（旧版 useCaseLabel 的可读映射，tooltip 保留原值）。 */
export function useCaseLabel(value: string): string {
  const raw = String(value || '').trim()
  if (!raw) return FMT_EMPTY
  const stripped = raw.replace(/^(qoder|loomy|trae|wb|workbuddy|anthropic|openai)[-_]?/i, '')
  const key = stripped.toLowerCase()
  const names: Record<string, string> = {
    chat: tx('page.logs.useCase.chat'),
    completions: tx('page.logs.useCase.completions'),
    messages: tx('page.logs.useCase.messages'),
    responses: tx('page.logs.useCase.responses'),
    embeddings: tx('page.logs.useCase.embeddings'),
    images: tx('page.logs.useCase.images'),
    videos: tx('page.logs.useCase.videos'),
    audio: tx('page.logs.useCase.audio'),
    moderation: tx('page.logs.useCase.moderation'),
    'chat-completions': tx('page.logs.useCase.chat'),
    'messages/count_tokens': tx('page.logs.useCase.countTokens'),
  }
  return names[key] || names[stripped] || stripped || raw
}

/**
 * 网关分类（`error_kind`）的可读名。
 *
 * 结果列原来直接 `slice(0, 16)` 显示原始 kind，`endpoint_gone` 这种新分类
 * 就只能看到一串下划线英文名（2026-11-26 Qoder 空 404 事故）。
 * 没收录的 kind 仍原样显示 —— 宁可见到生词，也不要把它藏起来。
 */
export function errorKindLabel(value: string): string {
  const raw = String(value || '').trim()
  if (!raw) return tx('page.logs.result.fail')
  // 键统一用**平台原始 kind**（下划线），与 i18n 里已有的 page.logs.kind.* 对齐
  const known = [
    'endpoint_gone', 'model_unknown', 'quota_exceeded', 'hard_credit',
    'rate_limit', 'soft_rate', 'model_rate', 'model_block', 'auth',
    'session_dead', 'signature_invalid', 'upstream_business', 'upstream_error',
    'upstream_internal', 'upstream_auth_anomaly', 'network', 'transport',
    'timeout', 'client_disconnected', 'gateway_rejected', 'no_account',
  ]
  if (known.includes(raw)) return tx(`page.logs.kind.${raw}`)
  return raw.slice(0, 16)
}

/**
 * 缓存命中率（0–100）。
 *
 * 三种情况都为 null，界面显示 —（**不能显示 0%**）：
 *   1. prompt_tokens 为 0 / 缺失（分母不存在）；
 *   2. cached_tokens 为 null/undefined（上游没回缓存统计）；
 *   3. 只有显式 0 才表示「真的没命中」。
 */
export function cacheRateOf(row: Pick<UsageLogRow, 'prompt_tokens' | 'cached_tokens'> | null | undefined): number | null {
  if (!row) return null
  const prompt = Number(row.prompt_tokens || 0)
  const cached = row.cached_tokens
  if (!prompt || cached === null || cached === undefined) return null
  return Math.min(100, (Number(cached) || 0) / prompt * 100)
}

/** 生成速度：completion / 纯解码段；边界与 bench_router 一致（<300ms 或 >300 tok/s 视为不可信）。 */
export function tpsOf(row: Pick<UsageLogRow, 'completion_tokens' | 'latency_ms' | 'ttfb_ms'>): number | null {
  const completion = Number(row.completion_tokens || 0)
  const decode = Number(row.latency_ms || 0) - Number(row.ttfb_ms || 0)
  if (completion <= 0 || decode < 300) return null
  const tps = completion / (decode / 1000)
  return tps > 0 && tps <= 300 ? tps : null
}

export function toLogRow(item: UsageLogRow): LogRowView {
  const rate = cacheRateOf(item)
  const tps = tpsOf(item)
  const errorKind = String(item.error_kind || '')
  const isError = Boolean(errorKind) && errorKind !== 'success'
  const tokens = [item.prompt_tokens, item.completion_tokens, item.cached_tokens].some(
    (value) => value !== null && value !== undefined,
  )
    ? [
        tx('page.logs.tokens.in', { n: fmtInt(item.prompt_tokens) }),
        tx('page.logs.tokens.out', { n: fmtInt(item.completion_tokens) }),
        tx('page.logs.tokens.cached', { n: fmtInt(item.cached_tokens) }),
      ].join(' · ')
    : FMT_EMPTY
  return {
    ...item,
    timeShort: fmtTime(item.created_at),
    timeFull: fmtFullTime(item.created_at),
    isError,
    resultLabel: isError ? errorKindLabel(errorKind) : tx('page.logs.result.ok'),
    resultTone: isError ? 'danger' : 'ok',
    cacheRate: rate,
    cacheRateText: rate === null ? FMT_EMPTY : rate.toFixed(2) + '%',
    ttfbText: fmtDuration(item.ttfb_ms),
    latencyText: fmtDuration(item.latency_ms),
    tpsText: tps === null ? FMT_EMPTY : tps.toFixed(1),
    tokensText: tokens,
    useCaseLabel: useCaseLabel(item.use_case),
  }
}

export function toLogRows(items: UsageLogRow[] | null | undefined): LogRowView[] {
  return (items || []).map(toLogRow)
}

/** 汇总行（账号）：未记录账号 / 未选号固定最后（设计稿 P11）。 */
export interface AccountSummaryRow extends UsageLogSummaryItem {
  cacheRate: number | null
  cacheRateText: string
  avgLatencyText: string
  lastUsedText: string
  unattributed: boolean
}

export function toAccountSummaryRows(items: UsageLogSummaryItem[] | null | undefined): AccountSummaryRow[] {
  const rows = (items || []).map((item) => {
    const rate = cacheRateOf({
      prompt_tokens: item.prompt_tokens,
      cached_tokens: item.cached_tokens,
    })
    return {
      ...item,
      cacheRate: rate,
      cacheRateText: rate === null ? FMT_EMPTY : rate.toFixed(2) + '%',
      avgLatencyText: item.avg_latency_ms ? (item.avg_latency_ms / 1000).toFixed(2) + 's' : FMT_EMPTY,
      lastUsedText: fmtTime(item.last_used_at),
      unattributed: !item.account_id,
    }
  })
  const attributed = rows
    .filter((row) => !row.unattributed)
    .sort((a, b) => b.credits - a.credits || b.requests - a.requests)
  const orphan = rows.filter((row) => row.unattributed)
  return [...attributed, ...orphan]
}

/** 汇总卡头那句：账号数 / 成功 / 错误 / 积分 / 未记录次数。 */
export function summaryHintText(payload: UsageLogSummaryPayload | null | undefined): string {
  if (!payload) return ''
  const parts = [
    tx('page.logs.summaryHint.accounts', { n: fmtInt(payload.account_count) }),
    tx('page.logs.summaryHint.okFail', { ok: fmtInt(payload.ok_requests), fail: fmtInt(payload.failed_requests) }),
    tx('page.logs.summaryHint.credits', { n: fmtCredit(payload.total_credits) }),
  ]
  if (payload.unattributed_requests) {
    parts.push(tx('page.logs.summaryHint.unattributed', { n: fmtInt(payload.unattributed_requests) }))
  }
  return parts.join(' · ')
}

/** FilterBar 下方「已筛选」的统计文字。 */
export function summaryStatText(payload: UsageLogSummaryPayload | null | undefined): string {
  if (!payload) return ''
  return tx('page.logs.summaryStat', {
    total: fmtInt(payload.total_requests),
    ok: fmtInt(payload.ok_requests),
    fail: fmtInt(payload.failed_requests),
  })
}

/** 请求详情记录卡的状态行（旧版 captureStatus 的同一口径）。 */
export function captureStatusText(settings: CaptureSettings | null | undefined): string {
  if (!settings) return tx('page.logs.capture.loading')
  const state = settings.enabled ? tx('page.logs.capture.on') : tx('page.logs.capture.off')
  const mib = (Number(settings.cache_bytes || 0) / 1024 / 1024).toFixed(1)
  const recordMib = Math.round(Number(settings.record_max_bytes || 0) / 1024 / 1024)
  const minutes = Math.round(Number(settings.ttl_seconds || 0) / 60)
  return tx('page.logs.capture.status', {
    state,
    cached: fmtInt(settings.cached_requests),
    max: fmtInt(settings.max_entries),
    mib,
    record: recordMib,
    minutes,
  })
}

/* ------------------------------------------------------------------ *
 * 请求动态（每分钟请求 / 错误小柱图）
 * ------------------------------------------------------------------ */

export interface LiveBar {
  /** 分钟起点（毫秒）。 */
  at: number
  /** 本地时区 HH:mm（图表横轴口径，见 utils/format.ts:localParts）。 */
  label: string
  requests: number
  errors: number
}

function isLiveError(item: LiveRequest): boolean {
  const status = Number(item.status || 0)
  if (status >= 400) return true
  const outcome = String(item.outcome || '').toLowerCase()
  return outcome === 'error' || outcome === 'timeout' || outcome === 'failed'
}

/** 把 recent 帧按分钟聚合成小柱图（默认最近 15 分钟，含空分钟）。 */
export function buildLiveBars(items: LiveRequest[] | null | undefined, now = Date.now(), minutes = 15): LiveBar[] {
  const span = Math.max(1, Math.floor(minutes))
  const endMinute = Math.floor(now / 60000) * 60000
  const buckets = new Map<number, LiveBar>()
  for (let index = span - 1; index >= 0; index -= 1) {
    const at = endMinute - index * 60000
    buckets.set(at, {
      at,
      label: fmtLocalHM(new Date(at)),
      requests: 0,
      errors: 0,
    })
  }
  for (const item of items || []) {
    const date = toDate(item.started_at)
    if (!date) continue
    const minute = Math.floor(date.getTime() / 60000) * 60000
    const bucket = buckets.get(minute)
    if (!bucket) continue
    bucket.requests += 1
    if (isLiveError(item)) bucket.errors += 1
  }
  return Array.from(buckets.values())
}

/** 请求动态的状态文案：进行中 N 个 · 最近 15 分钟 M 条。 */
export function liveHintText(payload: LiveRequestsPayload | null | undefined, minutes = 15): string {
  if (!payload) return tx('page.logs.live.loading')
  const total = (payload.recent || []).length
  const running = Number(payload.active_count || 0)
  return (
    (running > 0 ? tx('page.logs.live.running', { n: running }) + ' · ' : '') +
    tx('page.logs.live.recent', { minutes, n: total })
  )
}

export type LiveTone = 'ok' | 'warn' | 'danger' | 'brand' | 'neutral'

export function liveTone(item: LiveRequest | null | undefined): LiveTone {
  if (!item) return 'neutral'
  const outcome = String(item.outcome || '').toLowerCase()
  if (outcome === 'running') return 'brand'
  if (outcome === 'timeout') return 'warn'
  if (outcome === 'interrupted') return 'warn'
  if (isLiveError(item)) return 'danger'
  return 'ok'
}

/** 请求动态单行状态：进行中显示阶段，其它显示状态码/outcome。 */
export function liveStatusLabel(item: LiveRequest | null | undefined): string {
  if (!item) return FMT_EMPTY
  if (String(item.outcome || '').toLowerCase() === 'running') {
    const stages: Record<string, string> = {
      received: tx('page.logs.stage.received'),
      waiting: tx('page.logs.stage.waiting'),
      upstream: tx('page.logs.stage.upstream'),
      streaming: tx('page.logs.stage.streaming'),
    }
    return stages[String(item.phase || '')] || tx('page.logs.stage.running')
  }
  const status = Number(item.status || 0)
  if (String(item.outcome || '').toLowerCase() === 'timeout') return tx('page.logs.stage.timeout')
  if (status) return String(status)
  return item.outcome || FMT_EMPTY
}

/* ------------------------------------------------------------------ *
 * 详情：复制 curl
 * ------------------------------------------------------------------ */

function shellQuote(value: string): string {
  return "'" + String(value).replace(/'/g, "'\\''") + "'"
}

/**
 * 用请求详情里缓存的入站请求头 + 第一次上游尝试的 URL 拼一条 curl。
 *
 * 正文只有打开过正文预览时才有（ingressText）；没有正文时只输出头部与 URL，
 * 并留一行注释说明，避免复制出一条"看起来完整"却少了 body 的命令。
 * 被脱敏的头（值为 [REDACTED]）不输出。
 */
export function buildCurl(detail: UsageLogDetail | null | undefined, ingressText = ''): string {
  const capture = detail?.request_capture
  const attempt = capture?.upstream?.[0]
  if (!capture || !attempt || !attempt.url) return ''
  const headers = capture.ingress?.headers || {}
  const lines = ['curl -X ' + (attempt.method || 'POST') + ' ' + shellQuote(attempt.url)]
  for (const [name, value] of Object.entries(headers)) {
    if (!name || name.startsWith('_') || value === '[REDACTED]') continue
    lines.push('  -H ' + shellQuote(name + ': ' + value))
  }
  if (ingressText) lines.push('  --data-raw ' + shellQuote(ingressText))
  else lines.push('  # ' + tx('page.logs.curl.noBody'))
  return lines.join(' \\\n')
}


/* ------------------------------------------------------------------ *
 * v2.1 视觉精修：错误分类 / 诊断结构化 / KPI（纯函数，页面与单测共用）
 * ------------------------------------------------------------------ */

/**
 * 已知的错误分类：admin/request_metrics.py:mark_upstream_error 的 allowed 集合，
 * 加上网关自己写入 error_kind 的几种（gateway_rejected / timeout / rate_limit / upstream_5xx）。
 * 页面用 `page.logs.kind.<kind>` 翻译成管理员能看懂的话；不在表里的原样显示。
 */
export const KNOWN_ERROR_KINDS = [
  'hard_credit',
  'soft_rate',
  'model_rate',
  'model_block',
  'account_fault',
  'session_dead',
  'waf',
  'server',
  'not_found',
  'transport',
  'client',
  'no_account',
  'model_queue',
  'retry_exhausted',
  'upstream_internal',
  'model_behavior',
  'upstream_auth_anomaly',
  'unknown',
  'timeout',
  'rate_limit',
  'upstream_5xx',
  'gateway_rejected',
] as const

export type KnownErrorKind = (typeof KNOWN_ERROR_KINDS)[number]

export function knownErrorKind(value: unknown): KnownErrorKind | null {
  const key = String(value ?? '').trim().toLowerCase()
  return (KNOWN_ERROR_KINDS as readonly string[]).includes(key) ? (key as KnownErrorKind) : null
}

/** HTTP 状态码徽章语气：2xx ok / 4xx warn / 5xx danger / 其它 neutral。 */
export function httpStatusTone(status: unknown): 'ok' | 'warn' | 'danger' | 'neutral' {
  const code = Number(status)
  if (!Number.isFinite(code) || code < 100) return 'neutral'
  if (code < 400) return 'ok'
  if (code < 500) return 'warn'
  return 'danger'
}

/** 诊断里的一次尝试：账号路由事件（account_policy_events）或网络错误（network_errors）。 */
export interface DiagnosticAttempt {
  index: number
  source: 'account' | 'network'
  accountId: number | null
  /** 路由事件是 kind（soft_rate / hard_credit…），网络错误是异常类型（ReadTimeout…）。 */
  kind: string
  httpStatus: number | null
  model: string
  /** 上游错误码（upstream_code）。 */
  code: string
  /** 上游原文 / 网络错误说明（已脱敏）。 */
  message: string
  /** 网络错误发生的阶段。 */
  phase: string
  /** 冷却 / 熔断 / 降级截止时间（取最早的一个，ISO）。 */
  until: string
}

export interface DiagnosticStage {
  name: string
  ms: number
}

export interface DiagnosticView {
  gatewayStatus: number | null
  upstreamStatus: number | null
  upstreamError: string
  gatewayError: string
  networkError: string
  attemptCount: number | null
  lastAccountId: number | null
  lastModel: string
  recoveries: number
  firstDeltaMs: number | null
  elapsedMs: number | null
  truncated: boolean
  upstreamRequestIds: string[]
  attempts: DiagnosticAttempt[]
  stages: DiagnosticStage[]
}

function diagNum(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function diagStr(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function asRecordList(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
}

/**
 * 把诊断 JSON（admin/request_metrics.py:usage_evidence 的字段）拆成可读的结构。
 *
 * 形状随版本变化，所以每个字段都容错：缺字段 → null / 空串 / 空数组，不抛错。
 * 原始 JSON 仍由页面折叠展示，这里只挑管理员排障需要的部分。
 */
export function diagnosticView(diagnostic: Record<string, unknown> | null | undefined): DiagnosticView | null {
  if (!diagnostic || typeof diagnostic !== 'object') return null
  const d = diagnostic
  const attempts: DiagnosticAttempt[] = []
  for (const event of asRecordList(d.account_policy_events)) {
    const untils = ['cool_until', 'breaker_until', 'degrade_until']
      .map((name) => diagStr(event[name]))
      .filter(Boolean)
      .sort()
    attempts.push({
      index: attempts.length + 1,
      source: 'account',
      accountId: diagNum(event.account_id),
      kind: diagStr(event.kind),
      httpStatus: diagNum(event.http_status),
      model: diagStr(event.model),
      code: diagStr(event.upstream_code),
      message: diagStr(event.upstream_message),
      phase: '',
      until: untils[0] || '',
    })
  }
  for (const event of asRecordList(d.network_errors)) {
    attempts.push({
      index: attempts.length + 1,
      source: 'network',
      accountId: diagNum(event.account_id),
      kind: diagStr(event.type),
      httpStatus: diagNum(event.http_status),
      model: '',
      code: '',
      message: diagStr(event.message),
      phase: diagStr(event.phase),
      until: '',
    })
  }
  const stages: DiagnosticStage[] = asRecordList(d.stages)
    .map((stage) => ({ name: diagStr(stage.name), ms: diagNum(stage.ms) ?? 0 }))
    .filter((stage) => stage.name && stage.ms > 0)
    .slice(0, 16)
  const ids = Array.isArray(d.upstream_request_ids)
    ? d.upstream_request_ids.map((item) => diagStr(item)).filter(Boolean)
    : []
  return {
    gatewayStatus: diagNum(d.gateway_result_status) ?? diagNum(d.status),
    upstreamStatus: diagNum(d.upstream_status),
    upstreamError: diagStr(d.upstream_error),
    gatewayError: diagStr(d.gateway_error),
    networkError: diagStr(d.network_error),
    attemptCount: diagNum(d.upstream_attempts),
    lastAccountId: diagNum(d.last_account_id),
    lastModel: diagStr(d.last_model),
    recoveries: diagNum(d.connection_recoveries) ?? 0,
    firstDeltaMs: diagNum(d.first_delta_ms),
    elapsedMs: diagNum(d.elapsed_ms),
    truncated: d.diagnostic_truncated === true,
    upstreamRequestIds: ids,
    attempts,
    stages,
  }
}

/** 顶部 KPI（来自 /api/logs/summary，跟筛选走，与分页无关）。 */
export interface LogKpis {
  requests: number
  ok: number
  failed: number
  /** 0–1；没有请求时为 null（显示「—」，不显示 0%）。 */
  successRate: number | null
  credits: number
  tokens: number
  accounts: number
  unattributed: number
}

export function summaryKpis(payload: UsageLogSummaryPayload | null | undefined): LogKpis | null {
  if (!payload) return null
  const requests = Math.max(0, Number(payload.total_requests) || 0)
  const ok = Math.max(0, Number(payload.ok_requests) || 0)
  const failed = Math.max(0, Number(payload.failed_requests) || 0)
  return {
    requests,
    ok,
    failed,
    successRate: requests > 0 ? Math.min(1, ok / requests) : null,
    credits: Number(payload.total_credits) || 0,
    tokens: Number(payload.total_tokens) || 0,
    accounts: Math.max(0, Number(payload.account_count) || 0),
    unattributed: Math.max(0, Number(payload.unattributed_requests) || 0),
  }
}

/** 成功率语气：≥98% ok，≥90% warn，其余 danger；没有样本 neutral。 */
export function successTone(rate: number | null | undefined): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return 'neutral'
  if (rate >= 0.98) return 'ok'
  if (rate >= 0.9) return 'warn'
  return 'danger'
}

/** 小柱图纵轴刻度：取不小于最大值的「好看」整数（1 / 2 / 5 × 10^n）。 */
export function niceCeil(value: number): number {
  if (!Number.isFinite(value) || value <= 1) return 1
  const exp = Math.pow(10, Math.floor(Math.log10(value)))
  for (const step of [1, 2, 5, 10]) {
    if (step * exp >= value) return step * exp
  }
  return 10 * exp
}
