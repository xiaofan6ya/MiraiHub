/**
 * IP 管理与访问控制（/api/ip/*）。
 *
 * 后端：admin/routers/ip_management.py
 *       ip_activity（GET /api/ip/activity）/ list_blocked_ips / block_ip / patch_blocked_ip /
 *       delete_blocked_ip / devices / update_device
 *       拦截实现：admin/ip_access.py（IPBlockMiddleware / is_blocked_ip）
 * 契约：docs/ui-v2/contracts/ip.md
 *
 * 三个视图各自的取数方式（页面和单测共用同一份实现）：
 *   · 活跃 IP：服务端分页 + 服务端排序（limit/offset/sort/direction），查询参数由
 *     activityQueryFromState() 一次性算出；状态（已封禁/允许）后端无对应参数，只能过滤当前页。
 *   · 封禁名单 / 设备关联：后端一次返回全部（不分页），筛选/排序/分页在客户端完成。
 */
import { ApiError, http, type Query } from './http'
import { t, type MessageKey } from '@/i18n'

/**
 * 本文件里的界面文案统一走 i18n（r3：英文界面不得出现写死的中文）。
 * 新键先放在 tmp/quick_round/i18n_pagesB.json，合并进字典前 MessageKey 里还没有，这里收窄一次类型。
 */
function tx(key: string, params?: Record<string, string | number>): string {
  return t(key as MessageKey, params)
}

/* ------------------------------------------------------------------ *
 * 接口类型（字段名与后端一致，snake_case 原样保留）
 * ------------------------------------------------------------------ */

/** 旧版 ip-management.js 的 7 个可排序字段（data-ip-sort）。 */
export const IP_SORT_KEYS = [
  'requests',
  'failed',
  'blocked_requests',
  'credits',
  'tokens',
  'last_seen',
  'ip',
] as const

export type IpSortKey = (typeof IP_SORT_KEYS)[number]
export type IpSortDirection = 'asc' | 'desc'

export function isIpSortKey(value: unknown): value is IpSortKey {
  return typeof value === 'string' && (IP_SORT_KEYS as readonly string[]).includes(value)
}

/** 最近一次已认证请求的客户端信息（后端 _client_details，每类最多 3 个）。 */
export interface IpClientDetails {
  user_agent?: string
  device_ids?: string[]
  fingerprints?: string[]
  macs?: string[]
  identity_source?: string
}

/** GET /api/ip/activity 的 items[] 一项。 */
export interface IpActivityItem {
  ip: string
  requests: number
  failed: number
  blocked_requests: number
  credits: number
  tokens: number
  avg_tokens: number | null
  avg_credits: number | null
  known_usage: number
  last_seen: string | null
  blocked: boolean
  blocked_id: number | null
  client_details?: IpClientDetails
}

export interface IpActivityPayload {
  days: number
  sort: IpSortKey | string
  direction: IpSortDirection | string
  items: IpActivityItem[]
  total_ips: number
  total_requests: number
  current_ip: string
  offset: number
  limit: number
  blocked_count: number
}

export interface IpActivityQuery {
  days: number
  sort: IpSortKey
  direction: IpSortDirection
  limit: number
  offset: number
  min_requests: number
  max_avg_tokens?: number
  max_avg_credits?: number
  ip?: string
}

export interface BlockedIpItem {
  id: number
  ip: string
  enabled: boolean
  note: string
  created_at: string | null
  updated_at: string | null
  /** 到期时间（UTC，带 Z）；null = 永久。r3 起后端返回。 */
  expires_at?: string | null
  /** 已过到期时间（记录仍在，已不拦截）。 */
  expired?: boolean
  /** enabled 且未过期 = 正在拦截。 */
  active?: boolean
  /** auto = 风控自动写入；manual = 管理员手动。 */
  source?: 'auto' | 'manual' | string
  /** 后端支持定时封禁（duration_seconds / expires_at / permanent）。 */
  expiry_supported?: boolean
}

export interface BlockedIpListPayload {
  items: BlockedIpItem[]
  expiry_supported?: boolean
}

export type ClientIdentityKind = 'device_id' | 'fingerprint' | 'mac' | 'browser'

export interface ClientIdentityItem {
  kind: ClientIdentityKind | string
  digest: string
  value: string
  blocked: boolean
  note: string
  source: string
  ips: string[]
  last_seen: string | null
}

export interface ClientIdentityListPayload {
  items: ClientIdentityItem[]
  limit: number
  source: string
}

/* ------------------------------------------------------------------ *
 * 接口函数
 * ------------------------------------------------------------------ */

/** 活跃 IP（服务端排序 + 分页）。 */
export function getIpActivity(
  query: IpActivityQuery,
  signal?: AbortSignal,
): Promise<IpActivityPayload> {
  const params: Query = {
    days: query.days,
    sort: query.sort,
    direction: query.direction,
    limit: query.limit,
    offset: query.offset,
    min_requests: query.min_requests,
    ip: query.ip || '',
  }
  if (query.max_avg_tokens !== undefined && query.max_avg_tokens !== null) {
    params.max_avg_tokens = query.max_avg_tokens
  }
  if (query.max_avg_credits !== undefined && query.max_avg_credits !== null) {
    params.max_avg_credits = query.max_avg_credits
  }
  return http<IpActivityPayload>('GET', '/api/ip/activity', signal ? { query: params, signal } : { query: params })
}

/** 封禁名单（后端 enabled DESC, created_at DESC，不分页）。 */
export function listBlockedIps(signal?: AbortSignal): Promise<BlockedIpListPayload> {
  return http<BlockedIpListPayload>('GET', '/api/ip/blocked', signal ? { signal } : {})
}

/**
 * 封禁一台客户端。
 *
 * expires_at 为 ISO（UTC）字符串；null / 省略 = 永久（后端 BlockedIPIn 目前只接收 ip/note，
 * 多余字段被 Pydantic 忽略 —— 见契约 §七，后端补字段后此处无需改动）。
 */
export function blockIp(input: {
  ip: string
  note: string
  expires_at?: string | null
  /** 预设时长（秒，≤366 天）；优先于 expires_at，避免浏览器时钟偏差。 */
  duration_seconds?: number | null
}): Promise<{ item: BlockedIpItem }> {
  const body: Record<string, unknown> = { ip: input.ip, note: input.note }
  if (input.duration_seconds && input.duration_seconds > 0) body.duration_seconds = Math.trunc(input.duration_seconds)
  else if (input.expires_at) body.expires_at = input.expires_at
  return http<{ item: BlockedIpItem }>('POST', '/api/ip/blocked', { body })
}

/** 解封 / 重新封禁 / 改备注。 */
export function patchBlockedIp(
  id: number,
  patch: {
    enabled?: boolean
    note?: string
    /** true = 去掉到期时间，改为永久封禁。 */
    permanent?: boolean
    duration_seconds?: number
    expires_at?: string
  },
): Promise<{ item: BlockedIpItem }> {
  const body: Record<string, unknown> = {}
  if (patch.enabled !== undefined) body.enabled = patch.enabled
  if (patch.note !== undefined) body.note = patch.note
  if (patch.permanent) body.permanent = true
  else if (patch.duration_seconds && patch.duration_seconds > 0) body.duration_seconds = Math.trunc(patch.duration_seconds)
  else if (patch.expires_at) body.expires_at = patch.expires_at
  return http<{ item: BlockedIpItem }>('PATCH', '/api/ip/blocked/' + encodeURIComponent(String(id)), {
    body,
  })
}

/** 删除封禁记录（该 IP 恢复访问）。 */
export function deleteBlockedIp(id: number): Promise<{ id: number; ok: boolean }> {
  return http<{ id: number; ok: boolean }>(
    'DELETE',
    '/api/ip/blocked/' + encodeURIComponent(String(id)),
  )
}

/** 客户端上报的设备标识（最多 2000 条，按 last_seen DESC）。 */
export function listIpDevices(signal?: AbortSignal): Promise<ClientIdentityListPayload> {
  return http<ClientIdentityListPayload>('GET', '/api/ip/devices', signal ? { signal } : {})
}

/** 封禁 / 解封设备标识（按 (kind, value) 归一后写 ClientIdentity.blocked）。 */
export function updateIpDevice(input: {
  kind: string
  value: string
  blocked: boolean
  note?: string
}): Promise<{ ok: boolean; kind: string; digest: string; blocked: boolean }> {
  return http<{ ok: boolean; kind: string; digest: string; blocked: boolean }>(
    'POST',
    '/api/ip/devices',
    {
      body: {
        kind: input.kind,
        value: input.value,
        blocked: input.blocked,
        note: input.note || '',
      },
    },
  )
}

/* ------------------------------------------------------------------ *
 * 视图状态 → 接口参数
 * ------------------------------------------------------------------ */

export interface IpTableViewState {
  page: number
  pageSize: number
  sortBy: string
  sortOrder: string
  filters: Record<string, string>
}

/** 时间范围选项（旧版 1/7/30/90 天，文案沿用旧版）。 */
export const IP_DAYS_OPTIONS = [
  { get label() { return tx('page.ip.days.d1') }, value: '1' },
  { get label() { return tx('page.ip.days.d7') }, value: '7' },
  { get label() { return tx('page.ip.days.d30') }, value: '30' },
  { get label() { return tx('page.ip.days.d90') }, value: '90' },
] as const

/** 流量筛选（旧版「高频 · 低 Token / 高频 · 低积分」）。 */
export const IP_USAGE_OPTIONS = [
  { get label() { return tx('page.ip.usageOpt.all') }, value: '' },
  { get label() { return tx('page.ip.usageOpt.tokens') }, value: 'tokens' },
  { get label() { return tx('page.ip.usageOpt.credits') }, value: 'credits' },
] as const

export const IP_STATUS_OPTIONS = [
  { get label() { return tx('page.ip.statusOpt.all') }, value: '' },
  { get label() { return tx('page.ip.statusOpt.blocked') }, value: 'blocked' },
  { get label() { return tx('page.ip.statusOpt.allowed') }, value: 'allowed' },
] as const

function digits(value: unknown, fallback = 0): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const n = Math.trunc(digits(value, fallback))
  if (!Number.isFinite(n)) return fallback
  return Math.min(max, Math.max(min, n))
}

/**
 * 路由 query（useTableQuery 的 state）→ GET /api/ip/activity 的参数。
 *
 * 规则：
 *   · days 夹在 1–90（后端 Query(ge=1, le=90)）；
 *   · 排序字段不在白名单时回落到 requests desc（旧版默认）；
 *   · 流量筛选只在其选项为 tokens / credits 时才带上对应上限，避免把 0 当「筛选全部」；
 *   · ip 为空串时不下发（后端空串会退化成 contains('') 的模糊匹配）。
 */
export function activityQueryFromState(state: IpTableViewState): IpActivityQuery {
  const page = clampInt(state.page, 1, 1_000_000, 1)
  const pageSize = clampInt(state.pageSize, 1, 500, 20)
  const filters = state.filters || {}
  const usage = String(filters.usage || '').trim()
  const query: IpActivityQuery = {
    days: clampInt(filters.days, 1, 90, 1),
    sort: isIpSortKey(state.sortBy) ? state.sortBy : 'requests',
    direction: state.sortOrder === 'asc' ? 'asc' : 'desc',
    limit: pageSize,
    offset: (page - 1) * pageSize,
    min_requests: Math.max(0, Math.trunc(digits(filters.min, 0))),
  }
  if (usage === 'tokens') query.max_avg_tokens = Math.max(0, digits(filters.max, 0))
  else if (usage === 'credits') query.max_avg_credits = Math.max(0, digits(filters.max, 0))
  const ip = String(filters.q || '').trim()
  if (ip) query.ip = ip
  return query
}

/* ------------------------------------------------------------------ *
 * 纯函数：接口响应 → 展示数据（单测覆盖）
 * ------------------------------------------------------------------ */

/** IP 列气泡里的客户端识别结果（旧版 ipTooltip 的同一套口径）。 */
export interface IpClientSummary {
  /** 按 UA 识别出的客户端名。 */
  client: string
  userAgent: string
  deviceIds: string[]
  fingerprints: string[]
  macs: string[]
  /** 多行说明（HelpTip 内容），与旧版逐字一致。 */
  tooltip: string
}

/** label 以 `i18n:` 开头的走字典，其余是产品名原样显示。 */
const CLIENT_UA_RULES: Array<{ pattern: RegExp; label: string }> = [
  { pattern: /claude/i, label: 'i18n:page.ip.client.claude' },
  { pattern: /codex/i, label: 'i18n:page.ip.client.codex' },
  { pattern: /openai/i, label: 'OpenAI SDK' },
  { pattern: /anthropic/i, label: 'Anthropic SDK' },
  { pattern: /curl/i, label: 'curl' },
  { pattern: /python-httpx/i, label: 'Python HTTPX' },
  { pattern: /python-requests/i, label: 'Python Requests' },
  { pattern: /mozilla/i, label: 'i18n:page.ip.client.browser' },
]

function ruleLabel(label: string): string {
  return label.startsWith('i18n:') ? tx(label.slice(5)) : label
}

/** UA → 客户端名（顺序与旧版一致，先匹配先生效）。 */
export function classifyClient(userAgent: unknown): string {
  const ua = String(userAgent ?? '')
  if (!ua.trim()) return tx('page.ip.client.unknown')
  for (const rule of CLIENT_UA_RULES) {
    if (rule.pattern.test(ua)) return ruleLabel(rule.label)
  }
  return tx('page.ip.client.unknown')
}

function textList(values: unknown): string[] {
  if (!Array.isArray(values)) return []
  return values.map((value) => String(value ?? '').trim()).filter(Boolean)
}

/** client_details（可能缺失）→ IP 列气泡数据。 */
export function buildClientSummary(details?: IpClientDetails | null): IpClientSummary {
  const info = details || {}
  const userAgent = String(info.user_agent || '')
  const deviceIds = textList(info.device_ids)
  const fingerprints = textList(info.fingerprints)
  const macs = textList(info.macs)
  const list = (values: string[]) => (values.length ? values.join(', ') : tx('page.ip.client.notReported'))
  const tooltip = [
    tx('page.ip.client.tipClient', { client: classifyClient(userAgent) }),
    tx('page.ip.client.tipUa', { ua: userAgent || tx('page.ip.client.uaMissing') }),
    tx('page.ip.client.tipDeviceIds', { list: list(deviceIds) }),
    tx('page.ip.client.tipFingerprints', { list: list(fingerprints) }),
    tx('page.ip.client.tipMacs', { list: list(macs) }),
    tx('page.ip.client.tipDisclaimer'),
    tx('page.ip.client.tipFoot'),
  ].join('\n')
  return { client: classifyClient(userAgent), userAgent, deviceIds, fingerprints, macs, tooltip }
}

export interface IpActivityRow extends Record<string, unknown> {
  /** 行键 = ip。 */
  key: string
  ip: string
  requests: number
  failed: number
  blockedRequests: number
  credits: number
  tokens: number
  avgCredits: number | null
  avgTokens: number | null
  knownUsage: number
  lastSeen: string | null
  blocked: boolean
  blockedId: number | null
  /** 是否就是管理员当前出口 IP（旧版标「当前访问 IP」）。 */
  isCurrent: boolean
  /** 失败率 0–1（仅用于着色判断）。 */
  failureRate: number
  client: IpClientSummary
}

function nullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** GET /api/ip/activity → 表格行。 */
export function toActivityRows(
  items: IpActivityItem[] | null | undefined,
  currentIp?: string | null,
): IpActivityRow[] {
  const current = String(currentIp || '')
  return (items || []).map((item) => {
    const ip = String(item.ip || '').trim()
    const requests = Math.max(0, Math.trunc(digits(item.requests)))
    const failed = Math.max(0, Math.trunc(digits(item.failed)))
    return {
      key: ip,
      ip,
      requests,
      failed,
      blockedRequests: Math.max(0, Math.trunc(digits(item.blocked_requests))),
      credits: digits(item.credits),
      tokens: Math.max(0, Math.trunc(digits(item.tokens))),
      avgCredits: nullableNumber(item.avg_credits),
      avgTokens: nullableNumber(item.avg_tokens),
      knownUsage: Math.max(0, Math.trunc(digits(item.known_usage))),
      lastSeen: item.last_seen ?? null,
      blocked: Boolean(item.blocked),
      blockedId: item.blocked_id === null || item.blocked_id === undefined
        ? null
        : Math.trunc(digits(item.blocked_id)),
      isCurrent: Boolean(current) && ip === current,
      failureRate: requests > 0 ? Math.min(1, failed / requests) : 0,
      client: buildClientSummary(item.client_details),
    }
  })
}

export interface IpActivityFilters {
  /** '' | 'blocked' | 'allowed' */
  status?: string
}

/**
 * 状态筛选（后端无该参数，只能过滤当前页）。
 * 其余筛选项都已下发给后端，这里不再重复过滤。
 */
export function filterActivityRows(
  rows: IpActivityRow[],
  filters: IpActivityFilters = {},
): IpActivityRow[] {
  const status = String(filters.status || '').trim()
  if (status === 'blocked') return rows.filter((row) => row.blocked)
  if (status === 'allowed') return rows.filter((row) => !row.blocked)
  return rows.slice()
}

export interface BlockedIpRow extends Record<string, unknown> {
  key: string
  id: number
  ip: string
  enabled: boolean
  note: string
  createdAt: string | null
  updatedAt: string | null
  expiresAt: string | null
  /** 'permanent' = expires_at 为空（后端语义：永久）。 */
  expiry: 'permanent' | 'timed'
  /** 已过到期时间（后端 expired；老后端按 expiresAt 与当前时间推算）。 */
  expired: boolean
  /** 正在拦截 = enabled 且未过期。 */
  active: boolean
  /** 来源：后端 source 优先，缺失时按 auto: 备注前缀判断。 */
  source: 'auto' | 'manual'
}

/** GET /api/ip/blocked → 表格行。 */
export function toBlockedRows(items: BlockedIpItem[] | null | undefined): BlockedIpRow[] {
  return (items || []).map((item) => {
    const expiresAt = item.expires_at ? String(item.expires_at) : null
    const expiredByClock = expiresAt ? Date.parse(expiresAt) <= Date.now() : false
    const expired = typeof item.expired === 'boolean' ? item.expired : expiredByClock
    const source: 'auto' | 'manual' =
      item.source === 'auto' || item.source === 'manual'
        ? item.source
        : String(item.note || '').trim().toLowerCase().startsWith('auto:')
          ? 'auto'
          : 'manual'
    return {
      key: String(item.id),
      id: item.id,
      ip: String(item.ip || '').trim(),
      enabled: Boolean(item.enabled),
      note: String(item.note || '').trim(),
      createdAt: item.created_at ?? null,
      updatedAt: item.updated_at ?? null,
      expiresAt,
      expiry: expiresAt ? 'timed' : 'permanent',
      expired,
      active: typeof item.active === 'boolean' ? item.active : Boolean(item.enabled) && !expired,
      source,
    }
  })
}

export interface BlockedFilters {
  /** 搜索 IP / 原因。 */
  q?: string
  /** '' | 'enabled' | 'disabled' */
  status?: string
}

/** 封禁名单筛选（大小写不敏感）。 */
export function filterBlockedRows(rows: BlockedIpRow[], filters: BlockedFilters = {}): BlockedIpRow[] {
  const keyword = String(filters.q || '').trim().toLowerCase()
  const status = String(filters.status || '').trim()
  return rows.filter((row) => {
    if (keyword) {
      const haystack = (row.ip + '\n' + row.note).toLowerCase()
      if (!haystack.includes(keyword)) return false
    }
    if (status === 'enabled' && !row.enabled) return false
    if (status === 'disabled' && row.enabled) return false
    return true
  })
}

export const BLOCKED_SORT_KEYS = ['ip', 'enabled', 'createdAt'] as const
export type BlockedSortKey = (typeof BLOCKED_SORT_KEYS)[number]

/** 封禁名单排序；未知字段 / 空方向保持后端顺序。 */
export function sortBlockedRows(rows: BlockedIpRow[], sortBy: string, order: string): BlockedIpRow[] {
  const key = String(sortBy || '') as BlockedSortKey
  if (!BLOCKED_SORT_KEYS.includes(key) || (order !== 'asc' && order !== 'desc')) return rows.slice()
  const direction = order === 'desc' ? -1 : 1
  return rows.slice().sort((a, b) => {
    if (key === 'ip') return a.ip.localeCompare(b.ip, 'zh-CN') * direction
    if (key === 'enabled') return (Number(a.enabled) - Number(b.enabled)) * direction
    const left = a.createdAt ? Date.parse(a.createdAt) : 0
    const right = b.createdAt ? Date.parse(b.createdAt) : 0
    return (left - right) * direction
  })
}

export const CLIENT_KIND_LABELS: Record<string, string> = {
  get device_id() { return tx('page.ip.kind.deviceId') },
  get fingerprint() { return tx('page.ip.kind.fingerprint') },
  mac: 'MAC',
  get browser() { return tx('page.ip.kind.browser') },
}

export function clientKindLabel(kind: unknown): string {
  const key = String(kind || '').trim()
  return CLIENT_KIND_LABELS[key] || key
}

export interface IpDeviceRow extends Record<string, unknown> {
  key: string
  kind: string
  kindLabel: string
  digest: string
  value: string
  blocked: boolean
  note: string
  source: string
  ips: string[]
  lastSeen: string | null
}

/** GET /api/ip/devices → 表格行。 */
export function toDeviceRows(items: ClientIdentityItem[] | null | undefined): IpDeviceRow[] {
  return (items || []).map((item) => {
    const kind = String(item.kind || '').trim()
    const digest = String(item.digest || '').trim()
    return {
      key: kind + ':' + digest,
      kind,
      kindLabel: clientKindLabel(kind),
      digest,
      value: String(item.value || '').trim(),
      blocked: Boolean(item.blocked),
      note: String(item.note || '').trim(),
      source: String(item.source || 'client_reported'),
      ips: textList(item.ips),
      lastSeen: item.last_seen ?? null,
    }
  })
}

export interface DeviceFilters {
  /** 搜索标识 / 关联 IP。 */
  q?: string
  /** '' | 'blocked' | 'allowed' */
  status?: string
}

export function filterDeviceRows(rows: IpDeviceRow[], filters: DeviceFilters = {}): IpDeviceRow[] {
  const keyword = String(filters.q || '').trim().toLowerCase()
  const status = String(filters.status || '').trim()
  return rows.filter((row) => {
    if (keyword) {
      const haystack = [row.value, row.kindLabel, row.kind, ...row.ips].join('\n').toLowerCase()
      if (!haystack.includes(keyword)) return false
    }
    if (status === 'blocked' && !row.blocked) return false
    if (status === 'allowed' && row.blocked) return false
    return true
  })
}

export const DEVICE_SORT_KEYS = ['kindLabel', 'lastSeen', 'blocked'] as const
export type DeviceSortKey = (typeof DEVICE_SORT_KEYS)[number]

/** 设备关联排序；未知字段 / 空方向保持后端顺序（last_seen DESC）。 */
export function sortDeviceRows(rows: IpDeviceRow[], sortBy: string, order: string): IpDeviceRow[] {
  const key = String(sortBy || '') as DeviceSortKey
  if (!DEVICE_SORT_KEYS.includes(key) || (order !== 'asc' && order !== 'desc')) return rows.slice()
  const direction = order === 'desc' ? -1 : 1
  return rows.slice().sort((a, b) => {
    if (key === 'kindLabel') return a.kindLabel.localeCompare(b.kindLabel, 'zh-CN') * direction
    if (key === 'blocked') return (Number(a.blocked) - Number(b.blocked)) * direction
    const left = a.lastSeen ? Date.parse(a.lastSeen) : 0
    const right = b.lastSeen ? Date.parse(b.lastSeen) : 0
    return (left - right) * direction
  })
}

export interface PaginatedRows<T> {
  items: T[]
  total: number
  pageCount: number
  page: number
}

/** 客户端分页（封禁名单 / 设备关联共用）。 */
export function paginateRows<T>(rows: T[], page: number, pageSize: number): PaginatedRows<T> {
  const size = Math.max(1, Math.trunc(Number(pageSize) || 20))
  const total = rows.length
  const pageCount = Math.ceil(total / size)
  const current = pageCount ? Math.min(Math.max(1, Math.trunc(Number(page) || 1)), pageCount) : 1
  const start = (current - 1) * size
  return { items: rows.slice(start, start + size), total, pageCount, page: current }
}

/* ------------------------------------------------------------------ *
 * 封禁表单：地址解析 + 时长 → 到期时间
 * ------------------------------------------------------------------ */

export type BlockTargetKind = 'empty' | 'ip' | 'cidr' | 'invalid'

export interface BlockTarget {
  kind: BlockTargetKind
  /** 去空白后的原值。 */
  value: string
  /** 校验失败时的说明（跟随界面语言；空串 = 通过）。 */
  message: string
}

const IPV4_PATTERN = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/
const IPV6_PATTERN = /^[0-9a-f:]+$/i

function isIpv4(value: string): boolean {
  const match = IPV4_PATTERN.exec(value)
  if (!match) return false
  return match.slice(1).every((part) => Number(part) <= 255)
}

const IPV6_GROUP = /^[0-9a-f]{1,4}$/i

function isIpv6(value: string): boolean {
  if (!value.includes(':') || !IPV6_PATTERN.test(value)) return false
  const halves = value.split('::')
  if (halves.length > 2) return false
  const validPart = (part: string): boolean =>
    part === '' || part.split(':').every((group) => IPV6_GROUP.test(group))
  if (halves.length === 2) {
    const [head, tail] = halves
    if (!validPart(head) || !validPart(tail)) return false
    const headCount = head ? head.split(':').length : 0
    const tailCount = tail ? tail.split(':').length : 0
    return headCount + tailCount <= 7
  }
  const groups = value.split(':')
  return groups.length === 8 && groups.every((group) => IPV6_GROUP.test(group))
}

/**
 * 封禁目标解析。
 *
 * 后端 _normalize_ip 走 ipaddress.ip_address（单地址），CIDR 会被 400；
 * 这里把 CIDR 单独标出来，由表单给出「需要后端支持」的说明，而不是让用户吃一个 400。
 */
export function parseBlockTarget(raw: unknown): BlockTarget {
  const value = String(raw ?? '').trim()
  if (!value) return { kind: 'empty', value, message: tx('page.ip.form.ipRequired') }
  if (value.includes('/')) {
    const [base, prefix] = value.split('/')
    const size = /^\d{1,3}$/.test(prefix || '') ? Number(prefix) : -1
    const limit = base.includes(':') ? 128 : 32
    if ((isIpv4(base) || isIpv6(base)) && size >= 0 && size <= limit) {
      return {
        kind: 'cidr',
        value,
        message: tx('page.ip.form.cidrUnsupported'),
      }
    }
    return { kind: 'invalid', value, message: tx('page.ip.form.ipInvalid') }
  }
  if (isIpv4(value) || isIpv6(value)) return { kind: 'ip', value, message: '' }
  return { kind: 'invalid', value, message: tx('page.ip.form.ipInvalid') }
}

export type BlockDuration = 'permanent' | '1h' | '1d' | '7d' | '30d' | 'custom'

/** 时长选项（设计稿 P13）。label 是 getter：每次读取都按当前语言取词（放进 computed 即可随语言刷新）。 */
export const BLOCK_DURATION_OPTIONS = [
  { get label() { return tx('page.ip.duration.permanent') }, value: 'permanent' },
  { get label() { return tx('page.ip.duration.h1') }, value: '1h' },
  { get label() { return tx('page.ip.duration.d1') }, value: '1d' },
  { get label() { return tx('page.ip.duration.d7') }, value: '7d' },
  { get label() { return tx('page.ip.duration.d30') }, value: '30d' },
  { get label() { return tx('page.ip.duration.custom') }, value: 'custom' },
] as const

/** 后端 duration_seconds / expires_at 的上限：366 天。 */
export const MAX_BLOCK_MS = 366 * 86400000

const HOUR_MS = 3600000
const DAY_MS = 86400000

const DURATION_MS: Record<'1h' | '1d' | '7d' | '30d', number> = {
  '1h': HOUR_MS,
  '1d': DAY_MS,
  '7d': 7 * DAY_MS,
  '30d': 30 * DAY_MS,
}

export interface ExpiryPlan {
  /** 提交给后端的 expires_at（ISO UTC）；null = 永久。 */
  expiresAt: string | null
  /** 预设时长对应的秒数（提交时优先用它）；永久 / 自定义为 null。 */
  durationSeconds: number | null
  /** 校验失败的说明（跟随界面语言；空串 = 通过）。 */
  error: string
}

/**
 * 时长 + 自定义时间 → expires_at。
 *
 * customMs 为自定义到期时间的时间戳（毫秒）；必须晚于 now，否则报错。
 */
export function expiryFromDuration(
  duration: BlockDuration,
  now: number,
  customMs?: number | null,
): ExpiryPlan {
  if (duration === 'permanent') return { expiresAt: null, durationSeconds: null, error: '' }
  if (duration === 'custom') {
    const value = Number(customMs)
    if (!Number.isFinite(value) || value <= 0) return { expiresAt: null, durationSeconds: null, error: tx('page.ip.form.expiryRequired') }
    if (value <= now) return { expiresAt: null, durationSeconds: null, error: tx('page.ip.form.expiryPast') }
    if (value - now > MAX_BLOCK_MS) return { expiresAt: null, durationSeconds: null, error: tx('page.ip.form.expiryTooFar') }
    return { expiresAt: new Date(value).toISOString(), durationSeconds: null, error: '' }
  }
  const span = DURATION_MS[duration]
  return { expiresAt: new Date(now + span).toISOString(), durationSeconds: Math.round(span / 1000), error: '' }
}

/** 到期文案（expires_at 为空 = 永久）。 */
export function durationLabel(duration: BlockDuration): string {
  return BLOCK_DURATION_OPTIONS.find((option) => option.value === duration)?.label || tx('page.ip.duration.permanent')
}


/* ------------------------------------------------------------------ *
 * v2.1 视觉精修：自动封禁识别 / 一键清理 / 能力探测
 * ------------------------------------------------------------------ */

/** 风控自动封禁写入的备注前缀（admin/abuse_guard.py:_ban → note = "auto: {reason}"）。 */
export const AUTO_BLOCK_PREFIX = 'auto:'

/** 自动封禁的原因（去掉 "auto:" 前缀）；手动封禁返回 null。 */
export function autoBlockReason(note: string | null | undefined): string | null {
  const text = String(note || '').trim()
  if (!text.toLowerCase().startsWith(AUTO_BLOCK_PREFIX)) return null
  return text.slice(AUTO_BLOCK_PREFIX.length).trim()
}

export function isAutoBlock(row: Pick<BlockedIpRow, 'note'>): boolean {
  return autoBlockReason(row.note) !== null
}

export interface BlockedSourceStats {
  total: number
  enabled: number
  auto: number
  autoEnabled: number
  manual: number
}

export function blockedSourceStats(rows: BlockedIpRow[] | null | undefined): BlockedSourceStats {
  const list = rows || []
  const auto = list.filter(isAutoBlock)
  return {
    total: list.length,
    enabled: list.filter((row) => row.enabled).length,
    auto: auto.length,
    autoEnabled: auto.filter((row) => row.enabled).length,
    manual: list.length - auto.length,
  }
}

/** 按来源筛选：'' 全部 / 'auto' 自动 / 'manual' 手动。 */
export function filterBlockedBySource(rows: BlockedIpRow[], source: string): BlockedIpRow[] {
  if (source === 'auto') return rows.filter(isAutoBlock)
  if (source === 'manual') return rows.filter((row) => !isAutoBlock(row))
  return rows
}

/**
 * 后端是否支持「定时封禁」：GET /api/ip/blocked 的条目里出现 expires_at 字段才算支持。
 *
 * 现状（admin/routers/ip_management.py）：BlockedIPIn 只有 ip / note，_blocked_item 不返回 expires_at ——
 * 前端提交的 expires_at 会被静默丢弃，封禁实际是永久的。所以没探测到支持时，封禁抽屉只允许「永久」，
 * 列表也不显示「到期」列，避免界面写着「1 小时」而后端永久封禁。
 */
export function blockedExpirySupported(
  items: BlockedIpItem[] | null | undefined,
  payloadFlag?: boolean | null,
): boolean {
  if (payloadFlag === true) return true
  return (items || []).some(
    (item) => item.expiry_supported === true || Object.prototype.hasOwnProperty.call(item, 'expires_at'),
  )
}

/** POST /api/ip/blocked/batch-delete 的响应（r3 后端）。 */
export interface BatchDeleteResponse {
  deleted: number
  deleted_ids: number[]
  active_deleted?: number
  not_found_ids?: number[]
}

/**
 * 一次请求批量删除封禁记录：按 ids（≤2000）或按备注前缀（如 auto:）。
 * 后端没有这个接口时抛 404 —— 调用方回落逐条 DELETE（见 cleanBlockedRecords）。
 */
export function batchDeleteBlocked(
  input: { ids?: number[]; note_prefix?: string; only_inactive?: boolean },
): Promise<BatchDeleteResponse> {
  const body: Record<string, unknown> = {}
  if (input.ids && input.ids.length) body.ids = input.ids.slice(0, 2000)
  if (input.note_prefix) body.note_prefix = input.note_prefix
  if (input.only_inactive) body.only_inactive = true
  return http<BatchDeleteResponse>('POST', '/api/ip/blocked/batch-delete', { body })
}

export interface CleanBlockedResult extends BulkDeleteResult {
  /** 'batch' = 一次 POST 完成；'each' = 后端无批量接口，逐条 DELETE。 */
  via: 'batch' | 'each'
}

/**
 * 清理封禁记录：先走一次批量接口；只有 404 / 405（老后端没有该路由）才回落逐条删除。
 * ids 超过 2000 条时按 2000 一批顺序提交。
 */
export async function cleanBlockedRecords(
  ids: number[],
  options: {
    notePrefix?: string
    onProgress?: (done: number, total: number) => void
    batch?: typeof batchDeleteBlocked
    remove?: (id: number) => Promise<unknown>
  } = {},
): Promise<CleanBlockedResult> {
  const total = ids.length
  if (!total) return { deleted: [], failed: [], via: 'batch' }
  const batch = options.batch || batchDeleteBlocked
  const deleted: number[] = []
  try {
    for (let start = 0; start < total; start += 2000) {
      const chunk = ids.slice(start, start + 2000)
      const res = await batch({ ids: chunk })
      const done = Array.isArray(res?.deleted_ids) ? res.deleted_ids.map(Number) : chunk
      deleted.push(...done)
      options.onProgress?.(Math.min(total, start + chunk.length), total)
    }
    // not_found 视为已删除（别处已删掉），不算失败
    return { deleted, failed: [], via: 'batch' }
  } catch (error) {
    const routeMissing = error instanceof ApiError && (error.status === 404 || error.status === 405)
    if (!routeMissing || deleted.length) throw error
    const rest = await bulkDeleteBlocked(ids, { onProgress: options.onProgress, remove: options.remove })
    return { ...rest, via: 'each' }
  }
}

export interface BulkDeleteResult {
  deleted: number[]
  failed: Array<{ id: number; error: unknown }>
}

/**
 * 逐条删除封禁记录（后端没有批量接口）。并发 4 条，单条失败不影响其他条。
 * onProgress(done, total) 每完成一条回调一次。
 */
export async function bulkDeleteBlocked(
  ids: number[],
  options: { concurrency?: number; onProgress?: (done: number, total: number) => void; remove?: (id: number) => Promise<unknown> } = {},
): Promise<BulkDeleteResult> {
  const queue = ids.slice()
  const total = queue.length
  const remove = options.remove || deleteBlockedIp
  const result: BulkDeleteResult = { deleted: [], failed: [] }
  let done = 0
  const workers = Array.from({ length: Math.max(1, Math.min(options.concurrency ?? 4, total || 1)) }, async () => {
    while (queue.length) {
      const id = queue.shift() as number
      try {
        await remove(id)
        result.deleted.push(id)
      } catch (error) {
        result.failed.push({ id, error })
      } finally {
        done += 1
        options.onProgress?.(done, total)
      }
    }
  })
  await Promise.all(workers)
  return result
}
