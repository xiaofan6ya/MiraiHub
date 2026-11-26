/**
 * 积分到期（/api/credits/*）与账号级积分快照。
 *
 * 后端：
 *   · admin/routers/credits.py —— /expiry、/selection-policy、/selection-order、/usage
 *   · admin/routers/accounts.py:sync_credits —— POST /api/accounts/credits/sync（串行同步，几十秒）
 *   · admin/routing_policy.py —— 账号选号模式与启动间隔（不是 gateway 的平台策略）
 *
 * 重要：本页「账号调度策略」与 /gateway 的「调度策略」不是同一后端设置：
 *   · gateway ：system_settings 的 gateway:routing（平台级：先走哪个平台）
 *   · 本页    ：account_selection_mode / upstream_start_interval_ms（账号级：平台内选哪个号）
 * 因此各用各的接口，不能共用 store（共用会互相串改）。契约 credits.md 已写明。
 */
import { http } from './http'
import { FMT_EMPTY, fmtCredit, fmtInt, fmtTime } from '@/utils/format'
import { t, type MessageKey } from '@/i18n'

function tx(key: string, params?: Record<string, string | number>): string {
  return t(key as MessageKey, params)
}

export type SelectionMode = 'manual' | 'automatic'

export interface CreditPackageRow {
  package_code: string
  display_name: string
  remaining: number
  total: number
  /** 毫秒时间戳；null = 长期有效。 */
  expire_at: number | null
  /** 后端格式化好的到期文案（如「2026-12-31 到期」）。 */
  expire_label: string
  /** 短日期（MM-DD）。 */
  expire_date: string
  expired: boolean
  expiring_soon: boolean
  /** 逐包平铺时附带账号。 */
  account_id?: number
  account_name?: string
}

export interface CreditAccountRow {
  account_id: number
  account_name: string
  uid: string
  status: string
  selection_priority: number | null
  balance_remain: number
  total_remaining: number
  total_capacity: number
  expiring_soon_remaining: number
  evergreen_remaining: number
  expired_remaining: number
  soonest_expire_at: number | null
  soonest_days_left: number | null
  soonest_label: string
  expiring_soon: boolean
  expiring_package_count: number
  package_count: number
  active_package_count: number
  synced_at: number | null
  next_expiring: CreditPackageRow[]
}

export interface CreditWindow {
  days: number
  amount: number
  packages: number
}

export interface CreditSummary {
  accounts: number
  /** 余额口径（与账号面板一致）。 */
  available_remaining: number
  /** 快照口径：只覆盖采集过到期数据的账号。 */
  total_remaining: number
  snapshot_accounts: number
  expiring_soon_remaining: number
  evergreen_remaining: number
  expired_remaining: number
  expiring_accounts: number
  expiring_packages: number
  soonest_expire_at: number | null
  soonest_days_left: number | null
  soonest_account_id: number | null
  soonest_account_name: string | null
  at_risk_ratio: number
  packages_total: number
}

export interface RoutingModeOption {
  value: string
  label: string
  description: string
}

export interface SelectionPolicy {
  mode: string
  upstream_start_interval_ms: number
  modes: RoutingModeOption[]
}

export interface CreditExpiryPayload {
  generated_at: number
  /** 平台判定「快到期」的天数（7）。 */
  expiring_soon_days: number
  suggested_days: number
  summary: CreditSummary
  windows: CreditWindow[]
  accounts: CreditAccountRow[]
  selection_mode: SelectionMode
  selection_order: number[]
  routing_policy: SelectionPolicy
  packages: CreditPackageRow[]
  packages_total: number
  package_offset: number
  package_limit: number
  has_more: boolean
}

export interface CreditUsageAccount {
  account_id: number
  account_name: string
  requests: number
  credits: number
  tokens: number
  remaining: number
  expiring_soon_remaining: number
  soonest_days_left: number | null
}

export interface CreditUsagePayload {
  range_days: number
  total_credits: number
  accounts: CreditUsageAccount[]
  models: Array<{ model: string; requests: number; credits: number }>
}

export interface CreditsSyncResult {
  ok: boolean
  total: number
  refreshed: number
  failed: number
  credits_expiring_soon: number
  detail: Array<{
    id: number
    account: string
    ok: boolean
    error?: string
    expiring_soon?: number
    soonest_days_left?: number | null
    packages?: number
  }>
}

export interface CreditExpiryQuery {
  includeDisabled?: boolean
  packageLimit?: number
  packageOffset?: number
  days?: number
}

export interface CreditUsageQuery {
  days?: number
  top?: number
  account_id?: number
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

export function getCreditExpiry(
  query: CreditExpiryQuery = {},
  signal?: AbortSignal,
): Promise<CreditExpiryPayload> {
  return http<CreditExpiryPayload>('GET', '/api/credits/expiry', {
    query: {
      include_disabled: query.includeDisabled ? true : undefined,
      package_limit: query.packageLimit,
      package_offset: query.packageOffset,
      days: query.days,
    },
    signal,
  })
}

export function getSelectionPolicy(signal?: AbortSignal): Promise<SelectionPolicy> {
  return http<SelectionPolicy>('GET', '/api/credits/selection-policy', { signal })
}

export function saveSelectionPolicy(input: {
  mode: string
  upstream_start_interval_ms: number
}): Promise<SelectionPolicy> {
  return http<SelectionPolicy>('PUT', '/api/credits/selection-policy', { body: input })
}

/**
 * 恢复自动选号：清掉手动优先级（后端要求 expected_ids 与当前排序一致，防并发改序）。
 */
export function restoreAutomaticSelection(
  expectedIds: number[],
): Promise<{ ok: boolean; mode: string; ids: number[] }> {
  return http<{ ok: boolean; mode: string; ids: number[] }>(
    'POST',
    '/api/credits/selection-order',
    { body: { mode: 'automatic', ids: [], expected_ids: expectedIds, confirm: true } },
  )
}

export function getCreditUsage(
  query: CreditUsageQuery = {},
  signal?: AbortSignal,
): Promise<CreditUsagePayload> {
  return http<CreditUsagePayload>('GET', '/api/credits/usage', {
    query: { days: query.days ?? 7, top: query.top ?? 12, account_id: query.account_id },
    signal,
  })
}

/** 全部账号串行重新采集积分快照（同步请求；账号多时几十秒）。 */
export function syncCredits(): Promise<CreditsSyncResult> {
  return http<CreditsSyncResult>('POST', '/api/accounts/credits/sync')
}

/* ------------------------------------------------------------------ *
 * 纯函数：概览与风险分布
 * ------------------------------------------------------------------ */

export interface CreditsStatItem {
  label: string
  value: string | number
  unit?: string
  tone?: 'default' | 'brand' | 'ok' | 'warn' | 'danger' | 'muted'
  foot?: string
  help?: string
}

/** 最紧迫账号的剩余时间文案（与旧版面一致）。 */
export function soonestLabel(days: number | null | undefined): string {
  if (days === null || days === undefined || !Number.isFinite(days)) return '全部长期有效'
  if (days < 1) return Math.max(1, Math.round(days * 24)) + ' 小时内到期'
  return Math.floor(days) + ' 天后到期'
}

/** 顶部 StatStrip 的 5 格（设计稿 P7 与旧版第二行合并去重）。 */
export function creditStatItems(payload: CreditExpiryPayload | null | undefined): CreditsStatItem[] {
  if (!payload) return []
  const s = payload.summary
  const atRisk = (Number(s.at_risk_ratio) || 0) * 100
  return [
    {
      label: '账号总数',
      value: fmtInt(s.accounts),
      foot: fmtInt(s.snapshot_accounts) + ' 个已采集到期快照',
      help: '只统计当前平台上下文内启用的账号；已禁用账号不参与到期风险。',
    },
    {
      label: '可用积分',
      value: fmtCredit(s.available_remaining),
      // 不着色：数字本身不表达状态（01-direction §3.2；第 1 轮审查 P1-2）。
      foot: fmtInt(s.accounts) + ' 个启用账号 · ' + fmtInt(s.expiring_packages) + ' 个快到期包',
      help: '余额口径（Account.balance_remain），与账号池的「可用积分」一致。',
    },
    {
      label: payload.expiring_soon_days + ' 天内到期',
      value: fmtCredit(s.expiring_soon_remaining),
      tone: s.expiring_soon_remaining > 0 ? 'warn' : undefined,
      foot:
        s.expiring_soon_remaining > 0
          ? '占全部剩余 ' + atRisk.toFixed(1) + '% · 不用完会作废'
          : '暂无即将作废的额度',
    },
    {
      label: '长期有效',
      value: fmtCredit(s.evergreen_remaining),
      foot: '没有作废风险，留到最后用',
    },
    {
      label: '最紧迫账号',
      value: s.soonest_account_name || FMT_EMPTY,
      tone: s.expiring_soon_remaining > 0 ? 'warn' : undefined,
      foot: soonestLabel(s.soonest_days_left),
      help: '按到期时间排最前的账号；实际流量仍由「账号调度策略」决定。',
    },
  ]
}

export interface RiskSegment {
  label: string
  value: number
  tone: 'danger' | 'warn' | 'ok' | 'neutral'
}

export interface RiskView {
  items: RiskSegment[]
  /** 快照口径总量（StackMeter 的分母）。 */
  total: number
  hint: string
  empty: boolean
}

/**
 * 到期风险分布：≤30 天 / 30 天以外 / 长期有效。
 *
 * windows 是累计分档（≤1、≤3、≤7、≤14、≤30），所以「≤30 天」直接取 days=30 那档；
 * 「30 天以外」= 快照总量 − ≤30 天 − 长期有效（旧版同一口径，避免条形少一截）。
 */
export function riskView(payload: CreditExpiryPayload | null | undefined): RiskView {
  if (!payload) return { items: [], total: 0, hint: '', empty: true }
  const s = payload.summary
  const total = Number(s.total_remaining) || 0
  const within30Window = (payload.windows || []).find((item) => item.days === 30)
  const within30 = Number(within30Window?.amount) || 0
  const evergreen = Number(s.evergreen_remaining) || 0
  const beyond30 = Math.max(0, total - within30 - evergreen)
  const cover =
    s.snapshot_accounts && s.accounts && s.snapshot_accounts < s.accounts
      ? '（已采集 ' + s.snapshot_accounts + '/' + s.accounts + ' 个账号）'
      : ''
  return {
    items: [
      {
        label: '≤30 天' + (within30Window ? '（' + within30Window.packages + ' 个包）' : ''),
        value: within30,
        // ≤30 天是「快到期」而不是「已过期」：琥珀；30 天以外没有风险，用中性灰（01-direction §3.3）。
        tone: 'warn',
      },
      { label: '30 天以外', value: beyond30, tone: 'neutral' },
      { label: '长期有效', value: evergreen, tone: 'ok' },
    ],
    total,
    hint: total > 0 ? '共 ' + fmtCredit(total) + ' 积分，按到期时间分档' + cover : '',
    empty: within30 + beyond30 + evergreen <= 0,
  }
}

/* ------------------------------------------------------------------ *
 * 纯函数：表格 / 排行 / 消耗构成
 * ------------------------------------------------------------------ */

export interface PackageRowView extends CreditPackageRow {
  /** 剩余占比（0–100）。 */
  remainingRatio: number
  remainingText: string
  totalText: string
  expireText: string
  /** 表格行键：同一 package_code 可能出现在多个账号下。 */
  rowKey: string
}

export function toPackageRows(packages: CreditPackageRow[] | null | undefined): PackageRowView[] {
  return (packages || []).map((item) => {
    const total = Number(item.total) || 0
    const remaining = Number(item.remaining) || 0
    const ratio = total > 0 ? Math.min(100, (remaining / total) * 100) : 0
    return {
      ...item,
      remainingRatio: ratio,
      remainingText: fmtCredit(remaining),
      totalText: total > 0 ? fmtCredit(total) : FMT_EMPTY,
      expireText: item.expire_date || item.expire_label || (item.expire_at ? fmtTime(item.expire_at) : tx('page.credits.pkg.evergreen')),
      rowKey: String(item.account_id ?? '') + '-' + String(item.package_code || item.display_name) + '-' + String(item.expire_at ?? 'ever'),
    }
  })
}

export type AccountSortKey =
  | 'total_remaining'
  | 'expiring_soon_remaining'
  | 'soonest_expire_at'
  | 'account_name'
  | ''

/** 账号排行排序；未知字段或空排序时保持后端给出的紧迫度顺序。 */
export function sortAccountRows(
  rows: CreditAccountRow[] | null | undefined,
  sortBy: AccountSortKey,
  order: 'asc' | 'desc' | '',
): CreditAccountRow[] {
  const list = [...(rows || [])]
  if (!sortBy || !order) return list
  const direction = order === 'asc' ? 1 : -1
  return list.sort((a, b) => {
    if (sortBy === 'account_name') {
      return a.account_name.localeCompare(b.account_name, 'zh') * direction
    }
    const left = a[sortBy]
    const right = b[sortBy]
    // 空值（无到期日）永远排在最后，不因升降序翻到最前。
    if (left === null || left === undefined) return right === null || right === undefined ? 0 : 1
    if (right === null || right === undefined) return -1
    return ((Number(left) || 0) - (Number(right) || 0)) * direction
  })
}

export function paginateRows<T>(rows: T[], page: number, pageSize: number): T[] {
  const size = Math.max(1, Math.floor(pageSize || 1))
  const current = Math.max(1, Math.floor(page || 1))
  return rows.slice((current - 1) * size, current * size)
}

export interface UsageBarsView {
  /** 分类条形统一用品牌色（--chart-1），不拿 warn 琥珀当装饰（第 1 轮审查 P1-2）。 */
  items: Array<{ label: string; value: number; tone: 'brand' }>
  hint: string
  total: number
  empty: boolean
}

/** 账号消耗构成（近 N 天）：BarList 项 + 标题提示。 */
export function usageBars(
  payload: CreditUsagePayload | null | undefined,
  limit = 12,
): UsageBarsView {
  if (!payload) return { items: [], hint: '', total: 0, empty: true }
  const rows = (payload.accounts || []).filter((item) => Number(item.credits) > 0)
  const total = Number(payload.total_credits) || 0
  return {
    items: rows.slice(0, Math.max(1, limit)).map((item) => ({
      label: item.account_name + ' · ' + fmtInt(item.requests) + ' 次',
      value: Number(item.credits) || 0,
      tone: 'brand' as const,
    })),
    hint: '近 ' + fmtInt(payload.range_days) + ' 天 · 合计 ' + fmtCredit(total) + ' 积分',
    total,
    empty: rows.length === 0,
  }
}

/** 采集结果 Toast 文案（旧版同一口径）。 */
export function syncSummaryText(result: CreditsSyncResult | null | undefined): string {
  if (!result) return ''
  return (
    '采集完成：成功 ' + fmtInt(result.refreshed) + '/' + fmtInt(result.total) +
    (result.failed ? '，' + fmtInt(result.failed) + ' 个失败' : '') +
    (result.credits_expiring_soon ? '，7 天内到期 ' + fmtCredit(result.credits_expiring_soon) : '')
  )
}

/** 策略是否有未保存修改。 */
export function selectionPolicyDirty(
  policy: SelectionPolicy | null | undefined,
  mode: string,
  interval: number,
): boolean {
  if (!policy) return false
  return policy.mode !== mode || Number(policy.upstream_start_interval_ms) !== Number(interval)
}

/**
 * 取数之后表单该显示什么（修第 1 轮审查 P0-10「选号模式下拉是空的」）。
 *
 * 原实现先把新 policy 赋值，再用「新 policy vs 表单」判断 dirty：首次加载时表单是空串，
 * 和新 policy 必然不同 → 被当成「有未保存修改」直接跳过，下拉永远停在空值。
 * 正确做法是拿**旧** policy 判断用户有没有改过：没改过（或者根本还没有旧值）就跟随新值。
 * 返回 null 表示保留用户正在编辑的内容。
 */
export function nextPolicyForm(
  previous: SelectionPolicy | null | undefined,
  form: { mode: string; interval: number },
  next: SelectionPolicy | null | undefined,
): { mode: string; interval: number } | null {
  if (!next) return null
  const userEdited = previous ? selectionPolicyDirty(previous, form.mode, form.interval) : false
  if (userEdited) return null
  return { mode: String(next.mode || ''), interval: Number(next.upstream_start_interval_ms) || 0 }
}

/** 选号模式的选项；当前值不在列表里时补一项，避免下拉显示空白。 */
export function modeSelectOptions(
  policy: SelectionPolicy | null | undefined,
): Array<{ label: string; value: string }> {
  const list = (policy?.modes || []).map((item) => ({ label: item.label, value: String(item.value) }))
  const current = String(policy?.mode || '')
  if (current && !list.some((item) => item.value === current)) list.unshift({ label: current, value: current })
  return list
}

/** KPI 行用的原始数值（文案由页面按 i18n 取）。 */
export interface CreditKpis {
  accounts: number
  snapshotAccounts: number
  available: number
  expiringSoon: number
  expiringDays: number
  expiringPackages: number
  /** 快到期占快照总量的百分比（0–100）。 */
  atRiskPct: number
  evergreen: number
  soonestName: string
  soonestDays: number | null
}

export function creditKpis(payload: CreditExpiryPayload | null | undefined): CreditKpis | null {
  if (!payload) return null
  const s = payload.summary || ({} as CreditSummary)
  const days = s.soonest_days_left
  return {
    accounts: Number(s.accounts) || 0,
    snapshotAccounts: Number(s.snapshot_accounts) || 0,
    available: Number(s.available_remaining) || 0,
    expiringSoon: Number(s.expiring_soon_remaining) || 0,
    expiringDays: Number(payload.expiring_soon_days) || 7,
    expiringPackages: Number(s.expiring_packages) || 0,
    atRiskPct: Math.max(0, Math.min(100, (Number(s.at_risk_ratio) || 0) * 100)),
    evergreen: Number(s.evergreen_remaining) || 0,
    soonestName: String(s.soonest_account_name || ''),
    soonestDays: days === null || days === undefined || !Number.isFinite(Number(days)) ? null : Number(days),
  }
}

/** 剩余天数 → 语气：已过期 danger、7 天内 warn、其余 / 长期有效 neutral（不再用 ok 绿装饰）。 */
export function daysTone(days: number | null | undefined): 'warn' | 'danger' | 'neutral' {
  if (days === null || days === undefined || !Number.isFinite(Number(days))) return 'neutral'
  if (Number(days) <= 0) return 'danger'
  if (Number(days) <= 7) return 'warn'
  return 'neutral'
}

/** 账号状态 → 圆点语气：只有 active 是 ok；已禁用是灰，不用红（第 1 轮审查 P1-2）。 */
export function accountStatusTone(status: string): 'ok' | 'neutral' | 'warn' {
  const value = String(status || 'active')
  if (value === 'active') return 'ok'
  if (value === 'banned' || value === 'expired') return 'warn'
  return 'neutral'
}

/** 账号排行的状态标签（旧版 badge 的语义）。 */
export function accountStatusText(status: string): string {
  const value = String(status || 'active')
  if (value === 'active') return '活跃'
  if (value === 'banned') return '封禁'
  if (value === 'expired') return '已过期'
  if (value === 'disabled') return '已禁用'
  return value
}