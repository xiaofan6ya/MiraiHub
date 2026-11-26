/**
 * 账号池（/accounts）的接口与数据转换。
 *
 * 契约：docs/ui-v2/contracts/accounts.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-b.md P6
 * 后端：admin/routers/accounts.py（list_accounts / bulk_mutate / refresh-batch / health-check-batch /
 *       export-async / cat-travel/batch-async / credits/sync / model-limits / patch / delete）
 *       admin/routers/logs.py:log_summary（今日消耗：一次拿全部账号的当日请求/积分）
 *       admin/router/accounts.py:/api/accounts/job/{job_id}（jobrunner.snapshot，轮询间隔 1500ms）
 *
 * 口径（与后端注释一致）：
 *   · 列表按 X-Platform 收窄（账号池跟平台上下文），GET /api/accounts 支持 page/page_size/search/status/sort，
 *     但 status 只认 active|disabled —— 冷却/熔断必须由 gateway_state 在客户端筛；
 *   · 余额合计只算 active 账号；disabled_balance_remain 单独给；
 *   · 冷却/熔断/降级来自 gateway_state + gateway_reason/until（cool_until / breaker_until / degrade_until）；
 *   · credits_soonest_expire_at 是毫秒时间戳；token_* 只有 token_supported=true 的平台才有意义；
 *   · 账号没有 note 字段：备注列展示绑定 Key，可编辑的是账号名称与并发上限（PATCH /{id}）。
 */
import { http } from './http'
import { EXPORT_CONFIRM_HEADER, EXPORT_CONFIRM_VALUE, isExportConfirmRequired } from './platforms-admin'
import { fmtCredit, fmtExpiry, fmtInt, fmtRelative, expiryTone } from '@/utils/format'
import { t as defaultTranslate, type Translate } from '@/i18n'

export const ACCOUNT_STATUS_TABS = ['all', 'active', 'disabled', 'cooling', 'breaker'] as const
export type AccountStatusTab = (typeof ACCOUNT_STATUS_TABS)[number]

/** ?status= 的收敛：概览页链接写 cooldown，这里统一成 cooling。 */
export function normalizeAccountStatus(value: unknown): AccountStatusTab {
  const text = String(value || '').trim().toLowerCase()
  if (text === 'active' || text === 'enabled') return 'active'
  if (text === 'disabled') return 'disabled'
  if (text === 'breaker') return 'breaker'
  if (text === 'cooling' || text === 'cooldown') return 'cooling'
  return 'all'
}

export const ACCOUNT_STATUS_LABEL: Record<AccountStatusTab, string> = {
  all: '全部',
  active: '已启用',
  disabled: '已禁用',
  cooling: '冷却',
  breaker: '熔断',
}

/* -------------------------------------------------------------------------- */
/* 后端形状                                                                     */
/* -------------------------------------------------------------------------- */

export interface AccountModelLimit {
  model: string
  kind: string
  until: string
  remaining_seconds: number
  reset_source: string
  hits: number
  upstream_code?: string | null
  quota_total?: number | null
  quota_remaining?: number | null
}

export interface AccountCreditPackage {
  package_code?: string
  display_name?: string
  remaining?: number
  total?: number
  expire_at?: number | null
  expire_label?: string
  expire_date?: string
  expiring_soon?: boolean
  expired?: boolean
}

export interface AccountItem {
  id: number
  name: string
  uid: string
  enterprise_id?: string
  /** 落库平台（workbuddy / trae / qoder / loomy …）。 */
  platform: string
  domain?: string
  status: 'active' | 'disabled' | string
  max_in_flight?: number | null
  bound_key_id?: number | null
  bound_key_name?: string
  balance_total: number
  balance_remain: number
  last_sync_at: string | null
  last_used_at: string | null
  created_at: string | null
  model_limits: AccountModelLimit[]
  upstream_error?: Record<string, unknown> | string | null
  post_login_automation?: Record<string, unknown> | null
  resource_initialization?: Record<string, unknown> | null
  /** disabled | cooling | ready */
  gateway_state: 'disabled' | 'cooling' | 'ready' | string
  gateway_until: string | null
  /** cooling | breaker | degraded | manual_disabled … */
  gateway_reason: string
  credits_expiring?: number
  credits_expiring_soon: number
  credits_evergreen: number
  credits_expired: number
  credits_soonest_expire_at: number | null
  credits_soonest_days_left?: number | null
  credits_next_expiring?: AccountCreditPackage[]
  token_supported: boolean
  token_expires_at: string | null
  token_days_left?: number | null
  token_state: string
  renewal_expires_at: string | null
  renewal_days_left?: number | null
  renewal_state: string
  renewable?: boolean
}

export interface AccountSummary {
  total: number
  active: number
  disabled: number
  available: number
  balance_total: number
  balance_remain: number
  credits_expiring_soon: number
  credits_evergreen: number
  credits_expired: number
  expiring_accounts: number
  disabled_balance_remain?: number
  soonest_expire_at?: string | null
}

export interface AccountListPayload {
  items: AccountItem[]
  summary: AccountSummary
  /** 不带 page 时为 undefined（全量模式）。 */
  paginated?: boolean
  total?: number
  page?: number
  page_size?: number
}

/** /api/logs/summary 的 items[]（今日消耗按 account_id 对）。 */
export interface AccountUsageToday {
  account_id: number | null
  requests: number
  ok_requests?: number
  failed_requests?: number
  credits: number
  total_tokens?: number
}

export interface TodayUsagePayload {
  items: AccountUsageToday[]
}

/** admin/jobrunner.py: Job.snapshot()（与 composables/useJob 的 JobSnapshot 同形）。 */
export interface AccountJobSnapshot {
  job_id: string
  key?: string
  title?: string
  status: 'running' | 'finished' | 'failed' | string
  phase?: string
  total?: number
  done?: number
  elapsed_s?: number
  percent?: number
  current?: string
  task_done?: number
  task_current?: string
  items?: Array<Record<string, unknown>>
  items_total?: number
  items_truncated?: boolean
  result?: Record<string, unknown> | null
  error?: string | null
}

export interface BulkResult {
  ok: boolean
  action: string
  requested: number
  affected: number
  skipped: string[]
  errors: string[]
  note?: string
}

export interface CreditDetailsPayload {
  id: number
  account: string
  source: 'live' | 'cache' | 'none' | string
  error?: string | null
  packages: Array<{
    name?: string
    total?: number
    remain?: number
    used?: number
    deduction_end?: string
  }>
  credits?: Record<string, unknown>
}

/* -------------------------------------------------------------------------- */
/* 接口                                                                         */
/* -------------------------------------------------------------------------- */

export interface AccountListQuery {
  page?: number
  page_size?: number
  search?: string
  status?: string
  sort?: string
  min_credits?: number
  max_credits?: number
}

export function listAccounts(query: AccountListQuery = {}, signal?: AbortSignal): Promise<AccountListPayload> {
  const q: Record<string, string | number> = {}
  if (query.page) q.page = query.page
  if (query.page_size) q.page_size = query.page_size
  if (query.search) q.search = query.search
  if (query.status) q.status = query.status
  if (query.sort) q.sort = query.sort
  if (query.min_credits !== undefined) q.min_credits = query.min_credits
  if (query.max_credits !== undefined) q.max_credits = query.max_credits
  return http<AccountListPayload>('GET', '/api/accounts', { query: q, signal })
}

/** 今日消耗：一次拿全部账号的当日请求/积分（比逐号 request-usage 便宜）。 */
export function getTodayUsage(signal?: AbortSignal): Promise<TodayUsagePayload> {
  return http<TodayUsagePayload>('GET', '/api/logs/summary', { query: { days: 1 }, signal })
}

export function refreshAccount(id: number): Promise<{ id: number; balance_total: number; balance_remain: number }> {
  return http('POST', '/api/accounts/' + id + '/refresh')
}

export function healthCheckAccount(id: number): Promise<AccountJobSnapshot> {
  return http<AccountJobSnapshot>('POST', '/api/accounts/' + id + '/health-check')
}

/** 单账号平台任务：签到 / 新手任务 / 活跃对话（平台不支持时回 supported=false）。 */
export function runAccountTask(
  id: number,
  task: 'checkin' | 'onboarding' | 'activity',
): Promise<{ ok: boolean; supported?: boolean; summary?: string; reason?: string }> {
  return http('POST', '/api/accounts/' + id + '/task', { body: { task } })
}

export function patchAccount(
  id: number,
  body: { name?: string; status?: 'active' | 'disabled'; max_in_flight?: number | null },
): Promise<{ id: number; ok: boolean }> {
  return http('PATCH', '/api/accounts/' + id, { body })
}

export function deleteAccount(id: number): Promise<{ id: number; ok: boolean }> {
  return http('DELETE', '/api/accounts/' + id)
}

export function bulkAccounts(
  action: 'enable' | 'disable' | 'delete' | 'refresh' | 'check',
  ids: number[],
): Promise<BulkResult> {
  return http<BulkResult>('POST', '/api/accounts/bulk', { body: { action, ids } })
}

/** 整池刷新：job 键带平台，重复提交会返回 reused 的同一份快照。 */
export function refreshBatch(ids?: number[]): Promise<AccountJobSnapshot> {
  return http<AccountJobSnapshot>('POST', '/api/accounts/refresh-batch', {
    body: ids && ids.length ? { account_ids: ids } : {},
  })
}

export function healthCheckBatch(ids: number[]): Promise<AccountJobSnapshot> {
  return http<AccountJobSnapshot>('POST', '/api/accounts/health-check-batch', {
    body: { account_ids: ids },
  })
}

/** 成长任务（WorkBuddy）：后台任务，轮询 /api/accounts/job/{id}。 */
export function growthRunAsync(accountIds: number[]): Promise<AccountJobSnapshot> {
  return http<AccountJobSnapshot>('POST', '/api/growth/run-async', {
    body: { account_ids: accountIds, task_codes: null },
  })
}

/** 猫猫旅行（WorkBuddy）：后台任务。 */
export function catTravelBatchAsync(): Promise<AccountJobSnapshot> {
  return http<AccountJobSnapshot>('POST', '/api/accounts/cat-travel/batch-async', { body: {} })
}

export interface ExportRequest {
  ids?: number[]
  format?: 'raw' | 'client'
  include_disabled?: boolean
  all_platforms?: boolean
  status?: 'active' | 'disabled'
  keyword?: string
}

/**
 * 账号池异步导出（JSONL，含可回灌的凭证）。
 *
 * r3：后端 export_guard 的口径是「完整凭证要二次确认」。`/api/accounts/export-async` 目前尚未接入闸门，
 * 但一旦接入会回 428 `export_confirm_required` —— 页面捕获后弹确认框，再以 `confirmed=true`
 * 带 `X-Export-Confirm: include-secrets` 重发（与平台管理页 exportPlatformsAll 同一约定）。
 */
export function exportAccountsAsync(body: ExportRequest, confirmed = false): Promise<AccountJobSnapshot> {
  return http<AccountJobSnapshot>('POST', '/api/accounts/export-async', {
    body,
    headers: confirmed ? { [EXPORT_CONFIRM_HEADER]: EXPORT_CONFIRM_VALUE } : undefined,
  })
}

/** 后端要求确认才肯导出完整凭证：HTTP 428，detail.code（或 detail.detail.code）= export_confirm_required。 */
export function isAccountExportConfirmRequired(error: unknown): boolean {
  // r4：与平台管理页共用同一判定（api/platforms-admin.ts），不再各写一份
  return isExportConfirmRequired(error)
}

export function getAccountJob(jobId: string): Promise<AccountJobSnapshot> {
  return http<AccountJobSnapshot>('GET', '/api/accounts/job/' + encodeURIComponent(jobId))
}

export function getModelLimits(signal?: AbortSignal): Promise<{ items: Array<Record<string, unknown>> }> {
  return http('GET', '/api/accounts/model-limits', { signal })
}

export function getCreditDetails(
  id: number,
  refresh = false,
  signal?: AbortSignal,
): Promise<CreditDetailsPayload> {
  return http<CreditDetailsPayload>('GET', '/api/accounts/' + id + '/credit-details', {
    query: refresh ? { refresh: 1 } : undefined,
    signal,
  })
}

/* -------------------------------------------------------------------------- */
/* 去重（D1：分组键改为 (平台, 区, uid)）                                        */
/* -------------------------------------------------------------------------- */

export interface DuplicateAccountRef {
  id: number
  name: string
  created_at: string
}

/** GET /api/accounts/duplicates 的 groups[]（data-scoping D1 起多了 platform / realm）。 */
export interface DuplicateGroup {
  uid: string
  /** 落库平台（workbuddy / codebuddy 归一成 workbuddy）。 */
  platform?: string
  /** 'cn' | 'intl' | ''（WorkBuddy 的 domain 是主机名，不参与分组，恒为空）。 */
  realm?: string
  keep: DuplicateAccountRef
  remove: DuplicateAccountRef[]
}

export interface DuplicatesPayload {
  total: number
  unique: number
  groups: DuplicateGroup[]
  redundant: number
}

export interface DedupeResult {
  removed: number
  kept: number
  items: Array<{ id: number; name: string; uid: string; platform?: string; realm?: string }>
}

/** 跟随 X-Platform：只在当前平台作用域内查重。 */
export function getDuplicates(signal?: AbortSignal): Promise<DuplicatesPayload> {
  return http<DuplicatesPayload>('GET', '/api/accounts/duplicates', { signal })
}

/** 清理重复：每组保留 id 最小的那条；被 API Key 绑定（单选或多选）的号永不删除。 */
export function dedupeAccounts(): Promise<DedupeResult> {
  return http<DedupeResult>('POST', '/api/accounts/dedupe')
}

export interface DuplicateSection {
  /** 分组键 platform:realm，用作 v-for key。 */
  key: string
  platform: string
  realm: string
  groups: DuplicateGroup[]
  /** 本分组里可清理的冗余账号数。 */
  redundant: number
}

/**
 * 按 (platform, realm) 把重复组分段展示（fixlog_data_scoping §6.5）。
 * 旧后端没有 platform / realm 字段时落到同一段（platform=''），不猜。
 * 段内按冗余数降序，段与段按平台名、区排序，保证同一平台的两个区相邻。
 */
export function groupDuplicates(payload: DuplicatesPayload | null | undefined): DuplicateSection[] {
  const map = new Map<string, DuplicateSection>()
  for (const group of payload?.groups || []) {
    const platform = String(group.platform || '').toLowerCase()
    const realm = String(group.realm || '').toLowerCase()
    const key = platform + ':' + realm
    let section = map.get(key)
    if (!section) {
      section = { key, platform, realm, groups: [], redundant: 0 }
      map.set(key, section)
    }
    section.groups.push(group)
    section.redundant += Array.isArray(group.remove) ? group.remove.length : 0
  }
  const sections = Array.from(map.values())
  for (const section of sections) {
    section.groups.sort((a, b) => (b.remove?.length || 0) - (a.remove?.length || 0))
  }
  return sections.sort((a, b) => a.platform.localeCompare(b.platform) || a.realm.localeCompare(b.realm))
}

/* -------------------------------------------------------------------------- */
/* 页头 meta / 工具栏统计                                                       */
/* -------------------------------------------------------------------------- */

export interface AccountsOverview {
  total: number
  /** 冷却 + 熔断 + 降级（「N 个冷却」）。 */
  cooling: number
  disabled: number
  /** 可用 = 未禁用、未冷却且剩余积分 > 0（第 1 轮审查 P0-9：原来把已禁用也算成可用）。 */
  available: number
  /** 剩余积分合计只算未禁用的账号（与后端 summary.balance_remain 同口径）。 */
  remain: number
}

export function accountsOverview(rows: AccountRow[]): AccountsOverview {
  const out: AccountsOverview = { total: rows.length, cooling: 0, disabled: 0, available: 0, remain: 0 }
  for (const row of rows) {
    if (row.statusKey === 'disabled') {
      out.disabled += 1
      continue
    }
    if (row.statusKey !== 'active') out.cooling += 1
    else if (row.balanceRemain > 0) out.available += 1
    out.remain += row.balanceRemain
  }
  return out
}

/* -------------------------------------------------------------------------- */
/* 纯函数：行数据 / 状态 / 筛选 / 排序 / 分页 / 能力声明                          */
/* -------------------------------------------------------------------------- */

export type AccountTone = 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'

export type AccountStatusKey = 'active' | 'cooling' | 'breaker' | 'degraded' | 'disabled'

export interface AccountStatusMeta {
  key: AccountStatusKey
  label: string
  tone: AccountTone
  /** 悬停说明：冷却原因 + 恢复时间。 */
  title: string
}

/** 状态：后端 gateway_state + gateway_reason → 5 个页签之一 + Badge 文案。 */
const REASON_KEY: Record<string, string> = {
  cooling: 'status.reason.cooling',
  breaker: 'status.reason.breaker',
  degraded: 'status.reason.degraded',
  manual_disabled: 'status.reason.manual_disabled',
  session_dead: 'status.reason.session_dead',
  hard_credit: 'status.reason.hard_credit',
  account_fault: 'status.reason.account_fault',
  upstream_5xx: 'status.reason.upstream_5xx',
  transport: 'status.reason.transport',
}

export function accountStatusMeta(
  item: Pick<AccountItem, 'status' | 'gateway_state' | 'gateway_reason' | 'gateway_until'>,
  translate?: Translate,
): AccountStatusMeta {
  const tr = translate ?? defaultTranslate
  if (String(item.status || '') !== 'active') {
    return {
      key: 'disabled',
      label: tr('status.account.disabled'),
      tone: 'neutral',
      title: tr('status.account.disabledTitle'),
    }
  }
  const reason = String(item.gateway_reason || '').trim().toLowerCase()
  if (String(item.gateway_state || '') === 'cooling') {
    const key: AccountStatusKey =
      reason === 'breaker' ? 'breaker' : reason === 'degraded' ? 'degraded' : 'cooling'
    const label =
      reason === 'breaker'
        ? tr('status.account.breakerShort')
        : reason === 'degraded'
          ? tr('status.account.degradedShort')
          : tr('status.account.coolingShort')
    const reasonKey = REASON_KEY[reason]
    const reasonText = reasonKey
      ? tr(reasonKey as Parameters<Translate>[0])
      : reason || tr('status.reason.cooling')
    const title =
      reasonText +
      (item.gateway_until
        ? ' · ' +
          tr('status.account.until', {
            time: String(item.gateway_until).replace('T', ' ').slice(0, 16),
          })
        : '')
    return { key, label, tone: 'warn', title }
  }
  if (String(item.gateway_state || '') === 'disabled') {
    return {
      key: 'disabled',
      label: tr('status.account.disabled'),
      tone: 'neutral',
      title: tr('status.account.disabledTitleOther'),
    }
  }
  return {
    key: 'active',
    label: tr('status.account.activeShort'),
    tone: 'ok',
    title: tr('status.account.activeTitle'),
  }
}

/** 账号 / UID 脱敏：保留首 3 与末 4（与旧版账号列的展示口径一致）。 */
export function maskAccountId(value: string): string {
  const text = String(value || '')
  if (text.length <= 7) return text
  return text.slice(0, 3) + '****' + text.slice(-4)
}

export interface AccountRow extends Record<string, unknown> {
  key: string
  id: number
  name: string
  uid: string
  uidMasked: string
  platform: string
  domain: string
  statusKey: AccountStatusKey
  statusLabel: string
  statusTone: AccountTone
  statusTitle: string
  status: string
  balanceRemain: number
  balanceTotal: number
  balancePct: number
  balanceTone: AccountTone
  balanceTitle: string
  expireAt: number | null
  expireText: string
  expireTone: 'neutral' | 'warn' | 'danger'
  lastUsedText: string
  lastUsedTitle: string
  todayCredits: number
  todayRequests: number
  note: string
  boundKeyName: string
  /** 单账号支持的平台任务（能力声明驱动，页面不写 if(id==)）。 */
  features: AccountFeatures
  tokenText: string
  raw: AccountItem
}

export interface AccountUsageMap {
  get(id: number): AccountUsageToday | undefined
}

function clampPct(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, value))
}

/** 接口 items[] + 今日用量 → 表格行。 */
export function toAccountRows(
  items: AccountItem[] | null | undefined,
  usage?: Map<number, AccountUsageToday> | null,
  translate?: Translate,
): AccountRow[] {
  const tr = translate ?? defaultTranslate
  return (items || []).map((item) => {
    const status = accountStatusMeta(item, translate)
    const remain = Number(item.balance_remain) || 0
    const total = Math.max(Number(item.balance_total) || 0, remain)
    const pct = total > 0 ? clampPct((remain / total) * 100) : 0
    const expireAt = item.credits_soonest_expire_at ?? null
    const used = usage?.get(item.id)
    const tokenText =
      item.token_supported && item.token_state
        ? tr('page.accounts.colToken') +
          ' ' +
          (item.token_days_left === null || item.token_days_left === undefined
            ? item.token_state
            : tr('page.accounts.tokenDays', { days: item.token_days_left }))
        : ''
    return {
      key: String(item.id),
      id: item.id,
      name: String(item.name || item.uid || ('#' + item.id)),
      uid: String(item.uid || ''),
      uidMasked: maskAccountId(String(item.uid || '')),
      platform: String(item.platform || ''),
      domain: String(item.domain || ''),
      statusKey: status.key,
      statusLabel: status.label,
      statusTone: status.tone,
      statusTitle: status.title,
      status: String(item.status || ''),
      balanceRemain: remain,
      balanceTotal: total,
      balancePct: pct,
      // 只有「余额紧张」才着色；健康余额与已禁用账号都用中性灰（第 1 轮审查 P1-2：Meter 不用品牌色装饰）。
      balanceTone:
        status.key === 'disabled' ? 'neutral' : pct < 5 ? 'danger' : pct < 20 ? 'warn' : 'neutral',
      balanceTitle: fmtInt(remain) + ' / ' + fmtInt(total),
      expireAt,
      expireText: expireAt ? fmtExpiry(expireAt) : '—',
      expireTone: expiryTone(expireAt),
      lastUsedText: item.last_used_at ? fmtRelative(item.last_used_at) : '—',
      lastUsedTitle: item.last_used_at || '',
      todayCredits: Number(used?.credits) || 0,
      todayRequests: Number(used?.requests) || 0,
      note: item.bound_key_name
        ? tr('page.accounts.boundKeyNote', { name: item.bound_key_name })
        : '',
      boundKeyName: String(item.bound_key_name || ''),
      features: accountFeatures(item.platform),
      tokenText,
      raw: item,
    }
  })
}

export interface AccountTabCounts {
  all: number
  active: number
  disabled: number
  cooling: number
  breaker: number
}

/** 页签计数（一屏全量数据算，保证 tab 上的数字与筛选结果一致）。 */
export function accountTabCounts(rows: AccountRow[]): AccountTabCounts {
  const counts: AccountTabCounts = { all: rows.length, active: 0, disabled: 0, cooling: 0, breaker: 0 }
  for (const row of rows) {
    if (row.statusKey === 'active') counts.active += 1
    else if (row.statusKey === 'disabled') counts.disabled += 1
    else if (row.statusKey === 'breaker') counts.breaker += 1
    else counts.cooling += 1
  }
  return counts
}

export interface AccountFilters {
  status?: string
  q?: string
  min?: string | number
  max?: string | number
}

/** 客户端筛选：状态页签 + 搜索（名称/UID/域名/绑定 Key）+ 积分区间。 */
export function filterAccountRows(rows: AccountRow[], filters: AccountFilters = {}): AccountRow[] {
  const status = normalizeAccountStatus(filters.status)
  const keyword = String(filters.q || '').trim().toLowerCase()
  const min = filters.min === '' || filters.min === undefined || filters.min === null ? null : Number(filters.min)
  const max = filters.max === '' || filters.max === undefined || filters.max === null ? null : Number(filters.max)
  return rows.filter((row) => {
    if (status === 'active' && row.statusKey !== 'active') return false
    if (status === 'disabled' && row.statusKey !== 'disabled') return false
    if (status === 'cooling' && row.statusKey !== 'cooling' && row.statusKey !== 'degraded') return false
    if (status === 'breaker' && row.statusKey !== 'breaker') return false
    if (keyword) {
      const haystack = [row.name, row.uid, row.domain, row.boundKeyName].join('\n').toLowerCase()
      if (!haystack.includes(keyword)) return false
    }
    if (min !== null && Number.isFinite(min) && row.balanceRemain < min) return false
    if (max !== null && Number.isFinite(max) && row.balanceRemain > max) return false
    return true
  })
}

export type AccountSortKey = 'id' | 'name' | 'credits' | 'synced' | 'last_used'
export type AccountSortOrder = 'asc' | 'desc' | ''
export const ACCOUNT_SORT_KEYS: AccountSortKey[] = ['id', 'name', 'credits', 'synced', 'last_used']

function compareText(a: string, b: string): number {
  return String(a || '').localeCompare(String(b || ''), 'zh-CN')
}

function compareTime(a: string | null, b: string | null): number {
  const left = a ? Date.parse(a) : 0
  const right = b ? Date.parse(b) : 0
  return (Number.isFinite(left) ? left : 0) - (Number.isFinite(right) ? right : 0)
}

/** 排序（客户端）：id / 名称 / 剩余积分 / 最近同步 / 最近使用。 */
export function sortAccountRows(
  rows: AccountRow[],
  sortBy: string,
  order: AccountSortOrder,
): AccountRow[] {
  const key = String(sortBy || '') as AccountSortKey
  if (!ACCOUNT_SORT_KEYS.includes(key) || (order !== 'asc' && order !== 'desc')) return rows.slice()
  const direction = order === 'desc' ? -1 : 1
  return rows.slice().sort((a, b) => {
    let diff = 0
    if (key === 'name') diff = compareText(a.name, b.name)
    else if (key === 'credits') diff = a.balanceRemain - b.balanceRemain
    else if (key === 'synced') diff = compareTime(a.raw.last_sync_at, b.raw.last_sync_at)
    else if (key === 'last_used') diff = compareTime(a.raw.last_used_at, b.raw.last_used_at)
    else diff = a.id - b.id
    return diff === 0 ? b.id - a.id : diff * direction
  })
}

export interface PaginatedAccountRows {
  items: AccountRow[]
  total: number
  pageCount: number
  page: number
}

export function paginateAccountRows(
  rows: AccountRow[],
  page: number,
  pageSize: number,
): PaginatedAccountRows {
  const size = Math.max(1, Math.trunc(Number(pageSize) || 50))
  const total = rows.length
  const pageCount = Math.ceil(total / size)
  const current = pageCount ? Math.min(Math.max(1, Math.trunc(Number(page) || 1)), pageCount) : 1
  const start = (current - 1) * size
  return { items: rows.slice(start, start + size), total, pageCount, page: current }
}

/* ------------------------------ 后台任务 ------------------------------ */

export interface AccountJobItem {
  id: string
  name: string
  status: 'running' | 'done' | 'failed'
  percent: number
  succeeded?: number
  failed?: number
  message?: string
}

/** jobrunner.snapshot → JobProgress 的 JobItem。 */
export function toJobItem(
  snapshot: AccountJobSnapshot | null | undefined,
  translate?: Translate,
): AccountJobItem | null {
  if (!snapshot || !snapshot.job_id) return null
  const result = (snapshot.result || {}) as Record<string, unknown>
  const rows = Array.isArray(snapshot.items) ? snapshot.items : []
  const failed =
    Number(result.failed) || rows.filter((row) => row && row.ok === false).length || 0
  const succeeded =
    Number(result.refreshed) || Number(result.succeeded) || Number(result.affected) ||
    Math.max(0, Number(snapshot.done) || 0) - failed
  const running = snapshot.status === 'running'
  const failedStatus = snapshot.status === 'failed'
  return {
    id: snapshot.job_id,
    name: String(snapshot.title || snapshot.phase || (translate ?? defaultTranslate)('page.accounts.jobTitle')),
    status: running ? 'running' : failedStatus ? 'failed' : 'done',
    percent: Math.min(100, Math.max(0, Number(snapshot.percent) || 0)),
    succeeded: succeeded || undefined,
    failed: failed || undefined,
    message: String(result.summary || snapshot.error || snapshot.current || ''),
  }
}

/** 任务完成文案：后端 result.summary 优先，否则按完成/总数拼。 */
export function jobDoneText(
  snapshot: AccountJobSnapshot | null | undefined,
  translate?: Translate,
): string {
  const tr = translate ?? defaultTranslate
  if (!snapshot) return tr('page.accounts.jobFinished')
  const result = (snapshot.result || {}) as Record<string, unknown>
  if (typeof result.summary === 'string' && result.summary.trim()) return result.summary.trim()
  if (snapshot.status === 'failed') {
    return tr('page.accounts.jobFailedText', {
      error: snapshot.error || tr('page.accounts.jobUnknownError'),
    })
  }
  const total = Number(snapshot.total) || 0
  const done = Number(snapshot.done) || 0
  return tr('page.accounts.jobDoneText', { done, total })
}

/** 导出任务完成后的下载 token（result.file_token）。 */
export function exportTokenOf(snapshot: AccountJobSnapshot | null | undefined): string {
  const result = (snapshot?.result || {}) as Record<string, unknown>
  return typeof result.file_token === 'string' ? result.file_token : ''
}

/* ---------------------------- 平台能力声明 ---------------------------- */

export interface AccountFeatures {
  checkin: boolean
  onboarding: boolean
  activity: boolean
  /** 成长任务（WorkBuddy 专属）。 */
  growth: boolean
  /** 猫猫旅行（WorkBuddy 专属）。 */
  catTravel: boolean
}

const FEATURES_BY_STORAGE: Record<string, Partial<AccountFeatures>> = {
  workbuddy: { growth: true, catTravel: true },
  codebuddy: { growth: true, catTravel: true },
  loomy: { checkin: true, onboarding: true },
  'wps-lingxi': { checkin: true, activity: true },
  qoder: { checkin: true },
}

/** 按落库平台给能力（唯一判断点，页面里不再散落 if(platform==)。 */
export function accountFeatures(platform: string | null | undefined): AccountFeatures {
  const base: AccountFeatures = {
    checkin: false,
    onboarding: false,
    activity: false,
    growth: false,
    catTravel: false,
  }
  return { ...base, ...(FEATURES_BY_STORAGE[String(platform || '').toLowerCase()] || {}) }
}

export interface AddAccountMode { key: 'phone' | 'authorize' | 'manual' | 'import'; label: string }

/** 「添加账号 ▾」的模式：来自平台能力声明（不支持的方式不出现）。 */
export function addAccountModes(
  spec: { id?: string; storage_platform?: string; auth_mode?: string; actions?: string[] } | null | undefined,
  translate?: Translate,
): AddAccountMode[] {
  const tr = translate ?? defaultTranslate
  const storage = String(spec?.storage_platform || spec?.id || '').toLowerCase()
  const actions = Array.isArray(spec?.actions) ? spec!.actions! : []
  const authMode = String(spec?.auth_mode || '').toLowerCase()
  const modes: AddAccountMode[] = []
  if (storage === 'workbuddy' || storage === 'codebuddy') {
    modes.push({ key: 'phone', label: tr('add.method.phone') })
  }
  if (actions.includes('begin-auth') || authMode.includes('web')) {
    modes.push({ key: 'authorize', label: tr('add.method.authorize') })
  }
  if (storage === 'workbuddy' || storage === 'codebuddy' || authMode.includes('manual')) {
    modes.push({ key: 'manual', label: tr('add.method.manual') })
  }
  // 导入是所有平台都支持的入口（旧版「添加与迁移账号」区对所有平台都给了导入）。
  modes.push({ key: 'import', label: tr('add.method.import') })
  return modes
}
