/**
 * 成长计划（/growth）的接口与数据转换。
 *
 * 契约：docs/ui-v2/contracts/growth.md
 * 后端：
 *   GET  /api/growth-center/accounts                  admin/routers/growth_center.py:list_accounts
 *   GET  /api/growth-center/accounts/{aid}            growth_center.py:overview
 *   POST /api/growth-center/accounts/{aid}/refresh    growth_center.py:refresh（后台线程，2 并发）
 *   POST /api/growth-center/accounts/{aid}/action     growth_center.py:action（redeem/open/gift/draw/checkin）
 *   GET  /api/growth/accounts/{id}/tasks              admin/routers/growth.py:account_tasks（单次 1–3.3s，3s 缓存）
 *   POST /api/growth/run-async                        growth.py:growth_run_async（job）
 *   GET  /api/growth/job/{id}                         growth.py:growth_job
 *   POST /api/growth/claim                            growth.py:growth_claim（task_codes 省略 = 领全部 completed）
 *   POST /api/growth/accept                           growth.py:growth_accept
 *   GET  /api/growth/plans                            growth.py:growth_plans_view
 *
 * 三条口径：
 *   1. 平台能力不硬编码：页面用 platformStore.supports('growth')（PLATFORM_ONLY_TABS）；
 *   2. 任务「可做 / 可领」只看后端字段：actionable / accept_status / summary.claimable；
 *   3. 任务清单接口慢：逐账号并发拉取（页面限流 2），失败的卡单独报错，不影响其它卡。
 */
import { http } from './http'
import { fmtCredit, fmtInt } from '@/utils/format'

/* -------------------------------------------------------------------------- */
/* 后端形状                                                                    */
/* -------------------------------------------------------------------------- */

/** GET /api/growth-center/accounts 的 items[]。 */
export interface GrowthCenterAccount {
  id: number
  name: string
  status: string
  /** 连续登录天数（快照 streak 分区，缺省 null）。 */
  streak_days?: number | null
  /** 能量余额（快照 energy 分区，缺省 null）。 */
  energy?: number | null
}

export interface GrowthAccountsPayload {
  items: GrowthCenterAccount[]
  /** 企业版账号不参与成长任务，后端只回数量。 */
  excluded_enterprise: number
}

export interface GrowthTaskProgress {
  current?: number
  target?: number
}

/** growth.py:account_tasks 的 tasks[]（growth_plans.classify 之后的形状）。 */
export interface GrowthTask {
  task_code: string
  title?: string
  desc?: string
  reward_credit?: number
  /** not_accepted / accepted / completed / claimed …（上游原值）。 */
  accept_status?: string
  progress?: GrowthTaskProgress
  locked?: boolean
  /** auto / multi / manual / skip。 */
  level?: string
  level_label?: string
  actionable?: boolean
  strategy?: string
  need_times?: number
}

export interface GrowthProfile {
  level?: number
  level_name?: string
  completed?: number
  total?: number
  max_level?: number
  [key: string]: unknown
}

export interface GrowthTaskSummary {
  total: number
  done: number
  claimable: number
  claimable_credit: number
  actionable: number
}

export interface GrowthAccountTasksPayload {
  account_id: number
  name: string
  tasks: GrowthTask[]
  profile: GrowthProfile
  summary: GrowthTaskSummary
  cached?: boolean
  stale?: boolean
  error?: string
}

export interface GrowthClaimItem {
  task_code?: string
  ok?: boolean
  /** 上游回报「已经领过」——不计入本次领取数。 */
  already?: boolean
  credit?: number
  energy?: number
  msg?: string
}

export interface GrowthClaimAccountResult {
  account_id: number
  name?: string
  ok?: boolean
  claimed?: GrowthClaimItem[]
  total_credit?: number
  total_energy?: number
  msg?: string
  balance_remain?: number
}

export interface GrowthClaimPayload {
  results: GrowthClaimAccountResult[]
}

/** /api/growth/job/{id} 的快照（jobrunner.Job.snapshot）。 */
export interface GrowthJobSnapshot {
  job_id: string
  status: string
  percent?: number
  total?: number
  done?: number
  current?: string
  error?: string | null
  reused?: boolean
  result?: {
    accounts?: number
    tasks_done?: number
    credit?: number
    energy?: number
    failed?: number[]
  }
}

/** 成长中心的单次操作（growth_center.py:action 的 action 白名单）。 */
export type GrowthActionName = 'checkin' | 'open' | 'gift' | 'draw' | 'redeem'

export interface GrowthActionResult {
  ok: boolean
  credit?: number
  reason?: string
  balance_after?: number | null
  steps?: Array<Record<string, unknown>>
}

export interface GrowthCenterOverview {
  account: { id: number; name: string; status: string }
  snapshot: {
    sections?: Record<string, { data?: Record<string, unknown>; updated_at?: string }>
    errors?: Record<string, string>
    updated_at?: string
    checked_at?: string | null
    refresh_running?: boolean
  }
  refresh: { running: boolean; error?: string }
  latest_run?: Record<string, unknown> | null
  schedule?: Record<string, unknown> | null
  today: string
}

/* -------------------------------------------------------------------------- */
/* 接口                                                                        */
/* -------------------------------------------------------------------------- */

export function getGrowthAccounts(signal?: AbortSignal): Promise<GrowthAccountsPayload> {
  return http<GrowthAccountsPayload>('GET', '/api/growth-center/accounts', { signal })
}

export function getGrowthCenterOverview(accountId: number, signal?: AbortSignal): Promise<GrowthCenterOverview> {
  return http<GrowthCenterOverview>('GET', '/api/growth-center/accounts/' + accountId, { signal })
}

export function refreshGrowthCenter(accountId: number): Promise<{ ok: boolean; running: boolean }> {
  return http<{ ok: boolean; running: boolean }>(
    'POST',
    '/api/growth-center/accounts/' + accountId + '/refresh',
  )
}

export function postGrowthAction(
  accountId: number,
  action: GrowthActionName,
  tier = '',
): Promise<GrowthActionResult> {
  return http<GrowthActionResult>('POST', '/api/growth-center/accounts/' + accountId + '/action', {
    body: tier ? { action, tier } : { action },
  })
}

export function getAccountTasks(
  accountId: number,
  refresh = false,
  signal?: AbortSignal,
): Promise<GrowthAccountTasksPayload> {
  return http<GrowthAccountTasksPayload>('GET', '/api/growth/accounts/' + accountId + '/tasks', {
    query: refresh ? { refresh: 1 } : undefined,
    signal,
  })
}

/** 做任务：后台任务，返回 job 快照（重复提交返回 reused）。 */
export function runGrowthTasks(accountIds: number[], taskCodes?: string[]): Promise<GrowthJobSnapshot> {
  return http<GrowthJobSnapshot>('POST', '/api/growth/run-async', {
    body: taskCodes && taskCodes.length ? { account_ids: accountIds, task_codes: taskCodes } : { account_ids: accountIds },
  })
}

export function getGrowthJob(jobId: string): Promise<GrowthJobSnapshot> {
  return http<GrowthJobSnapshot>('GET', '/api/growth/job/' + encodeURIComponent(jobId))
}

/** 领奖：task_codes 省略 = 后端自动领所有 completed。 */
export function claimGrowth(accountIds: number[], taskCodes?: string[]): Promise<GrowthClaimPayload> {
  return http<GrowthClaimPayload>('POST', '/api/growth/claim', {
    body: taskCodes && taskCodes.length ? { account_ids: accountIds, task_codes: taskCodes } : { account_ids: accountIds },
  })
}

export function acceptGrowthTasks(accountIds: number[], taskCodes: string[]): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>('POST', '/api/growth/accept', {
    body: { account_ids: accountIds, task_codes: taskCodes },
  })
}

export function getGrowthPlans(): Promise<{ levels: Record<string, string>; plans: Array<Record<string, unknown>> }> {
  return http<{ levels: Record<string, string>; plans: Array<Record<string, unknown>> }>('GET', '/api/growth/plans')
}

/* -------------------------------------------------------------------------- */
/* 卡片模型与纯函数                                                            */
/* -------------------------------------------------------------------------- */

export type GrowthTone = 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'

export interface GrowthTaskStatus {
  label: string
  tone: GrowthTone
}

/** 任务状态 → 文案/语气（accept_status 上游原值，未识别的按进行中）。 */
export function taskStatusOf(task: GrowthTask | null | undefined): GrowthTaskStatus {
  const status = String(task?.accept_status || '').toLowerCase()
  if (status === 'completed') return { label: '可领取', tone: 'warn' }
  if (status === 'claimed') return { label: '已领取', tone: 'ok' }
  if (status === 'not_accepted') return { label: '未参与', tone: 'neutral' }
  if (status === 'accepted') return { label: '进行中', tone: 'brand' }
  if (String(task?.level || '') === 'skip') return { label: '已跳过', tone: 'neutral' }
  return { label: '进行中', tone: 'neutral' }
}

/** 策略分级（growth_plans.classify 的 level）→ 徽章语气；文案用后端 level_label。 */
export function levelTone(level: string | undefined | null): GrowthTone {
  const key = String(level || '').toLowerCase()
  if (key === 'auto' || key === 'multi') return 'brand'
  if (key === 'manual') return 'warn'
  return 'neutral'
}

/** 进度：优先 summary.done/total，其次 profile.completed/total。 */
export function progressOf(
  summary: Partial<GrowthTaskSummary> | null | undefined,
  profile: GrowthProfile | null | undefined,
): { done: number; total: number; percent: number; label: string; levelLabel: string } {
  const done = Number(summary?.done ?? profile?.completed ?? 0) || 0
  const total = Number(summary?.total ?? profile?.total ?? 0) || 0
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  const levelLabel = String(profile?.level_name || (profile?.level !== undefined ? 'Lv.' + profile.level : ''))
  return {
    done,
    total,
    percent,
    label: total > 0 ? fmtInt(done) + '/' + fmtInt(total) : '—',
    levelLabel,
  }
}

/** 已完成待领取的任务码（单任务领奖按钮用）。 */
export function claimableCodes(tasks: GrowthTask[] | null | undefined): string[] {
  return (tasks || [])
    .filter((task) => String(task.accept_status || '').toLowerCase() === 'completed' && task.task_code)
    .map((task) => task.task_code)
}

/** 任务清单排序：可领取 → 可做 → 已领取 → 其它（同组保持后端顺序）。 */
export function sortTasks(tasks: GrowthTask[] | null | undefined): GrowthTask[] {
  const rank = (task: GrowthTask): number => {
    const status = String(task.accept_status || '').toLowerCase()
    if (status === 'completed') return 0
    if (task.actionable && status !== 'claimed') return 1
    if (status === 'claimed') return 3
    return 2
  }
  return [...(tasks || [])].sort((a, b) => rank(a) - rank(b))
}

export interface GrowthCard {
  account: GrowthCenterAccount
  tasks: GrowthTask[]
  summary: GrowthTaskSummary
  profile: GrowthProfile
  progress: ReturnType<typeof progressOf>
  claimable: number
  claimableCredit: number
  canRun: boolean
  disabled: boolean
  loading: boolean
  error: string
}

const EMPTY_SUMMARY: GrowthTaskSummary = { total: 0, done: 0, claimable: 0, claimable_credit: 0, actionable: 0 }

/** 账号列表 + 任务清单 → 卡片（任务清单还没回来的卡片保留 loading 骨架）。 */
export function toGrowthCards(
  accounts: GrowthCenterAccount[] | null | undefined,
  tasksByAccount: Record<number, GrowthAccountTasksPayload | undefined> | null | undefined,
  options: { loading?: Record<number, boolean>; errors?: Record<number, string> } = {},
): GrowthCard[] {
  const map = tasksByAccount || {}
  const loading = options.loading || {}
  const errors = options.errors || {}
  return (accounts || []).map((account) => {
    const payload = map[account.id]
    const tasks = payload?.tasks || []
    const summary = { ...EMPTY_SUMMARY, ...(payload?.summary || {}) }
    const profile = payload?.profile || {}
    const disabled = String(account.status || '') !== 'active'
    return {
      account,
      tasks,
      summary,
      profile,
      progress: progressOf(summary, profile),
      claimable: Number(summary.claimable) || 0,
      claimableCredit: Number(summary.claimable_credit) || 0,
      canRun: !disabled && tasks.some((task) => task.actionable && String(task.accept_status || '') !== 'claimed'),
      disabled,
      loading: Boolean(loading[account.id]) && !payload,
      error: String(errors[account.id] || ''),
    }
  })
}

export type GrowthStateFilter = 'all' | 'claimable' | 'runnable' | 'disabled'

/** 搜索 + 状态筛选（账号名 / 状态）。 */
export function filterGrowthCards(
  cards: GrowthCard[],
  options: { q?: string; state?: GrowthStateFilter | string } = {},
): GrowthCard[] {
  const keyword = String(options.q || '').trim().toLowerCase()
  const state = (options.state || 'all') as GrowthStateFilter
  return cards.filter((card) => {
    if (state === 'claimable' && card.claimable <= 0) return false
    if (state === 'runnable' && !card.canRun) return false
    if (state === 'disabled' && !card.disabled) return false
    if (!keyword) return true
    return String(card.account.name || '').toLowerCase().includes(keyword)
  })
}

export interface GrowthStat {
  label: string
  value: string | number
  foot?: string
  tone?: 'default' | 'brand' | 'ok' | 'warn' | 'danger' | 'muted'
}

/** 统计条：账号 / 任务总数 / 已完成 / 可领奖励 / 可领积分 / 能量合计。 */
export function growthStats(cards: GrowthCard[], excludedEnterprise = 0): GrowthStat[] {
  const accounts = cards.length
  const total = cards.reduce((sum, card) => sum + Number(card.summary.total || 0), 0)
  const done = cards.reduce((sum, card) => sum + Number(card.summary.done || 0), 0)
  const claimable = cards.reduce((sum, card) => sum + Number(card.claimable || 0), 0)
  const credit = cards.reduce((sum, card) => sum + Number(card.claimableCredit || 0), 0)
  const energy = cards.reduce((sum, card) => sum + (Number(card.account.energy) || 0), 0)
  return [
    {
      label: '账号',
      value: fmtInt(accounts),
      foot: excludedEnterprise > 0 ? '已排除 ' + excludedEnterprise + ' 个企业版账号' : '成长任务账号',
    },
    { label: '任务总数', value: fmtInt(total) },
    { label: '已完成', value: fmtInt(done), tone: 'ok' },
    { label: '可领奖励', value: fmtInt(claimable), tone: claimable > 0 ? 'warn' : 'default' },
    { label: '可领积分', value: fmtCredit(credit), tone: credit > 0 ? 'warn' : 'default' },
    { label: '能量合计', value: fmtInt(energy) },
  ]
}

export interface ClaimSummary {
  accounts: number
  tasks: number
  credit: number
  failed: number
  text: string
}

/** POST /api/growth/claim 的结果 → 一句 Toast。 */
export function summarizeClaim(payload: GrowthClaimPayload | null | undefined): ClaimSummary {
  const results = payload?.results || []
  let tasks = 0
  let credit = 0
  let failed = 0
  for (const result of results) {
    if (result.ok === false) failed += 1
    credit += Number(result.total_credit || 0)
    for (const item of result.claimed || []) {
      // 上游回报 already=true 的条目不算本次领取（否则「领了 0 个」也会说领了 N 个）
      if (item.ok && !item.already) tasks += 1
    }
  }
  const count = tasks || results.filter((item) => item.ok !== false && (item.claimed || []).length).length
  const text =
    failed > 0 && !count
      ? '领取失败：' + failed + ' 个账号未成功，请稍后重试'
      : '已领取 ' + count + ' 个任务，共 ' + fmtCredit(credit) + ' 积分' + (failed ? '（' + failed + ' 个账号失败）' : '')
  return { accounts: results.length, tasks: count, credit, failed, text }
}

/** 成长中心的单次动作菜单（旧版 growth_center.js 的全部动作）。 */
export interface GrowthActionItem {
  key: string
  action: GrowthActionName
  label: string
  tier: string
}

export const GROWTH_ACTIONS: GrowthActionItem[] = [
  { key: 'checkin', action: 'checkin', label: '每日打卡', tier: '' },
  { key: 'open', action: 'open', label: '开启盲盒', tier: '' },
  { key: 'gift', action: 'gift', label: '领取礼物', tier: '' },
  { key: 'draw', action: 'draw', label: '限时抽奖', tier: '' },
  { key: 'redeem-7', action: 'redeem', label: '连登兑换 · 7 天', tier: '7' },
  { key: 'redeem-14', action: 'redeem', label: '连登兑换 · 14 天', tier: '14' },
  { key: 'redeem-28', action: 'redeem', label: '连登兑换 · 28 天', tier: '28' },
]

/** 本地分页（账号量级很小；仍然同步 query 以便分享链接）。 */
export function pageGrowthCards(cards: GrowthCard[], page = 1, pageSize = 20): {
  items: GrowthCard[]
  page: number
  pageCount: number
  total: number
} {
  const size = Math.max(1, Math.floor(pageSize) || 20)
  const total = cards.length
  const pageCount = Math.max(1, Math.ceil(total / size))
  const current = Math.min(Math.max(1, Math.floor(page) || 1), pageCount)
  return { items: cards.slice((current - 1) * size, current * size), page: current, pageCount, total }
}
