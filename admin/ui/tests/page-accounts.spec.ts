/**
 * S25 账号池（上）：接口适配 / 状态归一 / 筛选排序分页 / 后台任务 / 平台能力 的单测。
 *
 * 数据形状照抄后端真实响应：
 *   · GET /api/accounts        → admin/routers/accounts.py:list_accounts（items 1085 行起）
 *   · GET /api/logs/summary    → admin/routers/logs.py:log_summary（items[].account_id/credits/requests）
 *   · GET /api/accounts/job/id → admin/jobrunner.py:Job.snapshot（134 行起）
 *   · GET /api/platforms       → admin/platform/router.py（_serialize：actions/auth_mode/storage_platform）
 * 契约见 docs/ui-v2/contracts/accounts.md。
 */
import { describe, expect, it } from 'vitest'
import {
  ACCOUNT_SORT_KEYS,
  accountFeatures,
  accountStatusMeta,
  accountTabCounts,
  addAccountModes,
  exportTokenOf,
  filterAccountRows,
  jobDoneText,
  maskAccountId,
  normalizeAccountStatus,
  paginateAccountRows,
  sortAccountRows,
  toAccountRows,
  toJobItem,
  type AccountItem,
  type AccountJobSnapshot,
} from '@/api/accounts'

function account(overrides: Partial<AccountItem> & { id: number }): AccountItem {
  const base: AccountItem = {
    id: overrides.id,
    name: '账号 ' + overrides.id,
    uid: '19300001234',
    platform: 'workbuddy',
    domain: '',
    status: 'active',
    max_in_flight: null,
    bound_key_id: null,
    bound_key_name: '',
    balance_total: 5000,
    balance_remain: 4764,
    last_sync_at: '2026-11-23T04:00:00',
    last_used_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
    created_at: '2026-11-01T00:00:00',
    model_limits: [],
    gateway_state: 'ready',
    gateway_until: null,
    gateway_reason: '',
    credits_expiring_soon: 0,
    credits_evergreen: 5000,
    credits_expired: 0,
    credits_soonest_expire_at: Date.now() + 15 * 86400 * 1000,
    token_supported: true,
    token_expires_at: '2026-12-05T00:00:00Z',
    token_days_left: 12,
    token_state: 'ok',
    renewal_expires_at: '2027-01-05T00:00:00Z',
    renewal_days_left: 43,
    renewal_state: 'ok',
    renewable: true,
  }
  return { ...base, ...overrides }
}

const items: AccountItem[] = [
  account({ id: 1, name: '主号', balance_remain: 4764, bound_key_name: '主 Key' }),
  account({
    id: 2,
    name: '冷却号',
    gateway_state: 'cooling',
    gateway_reason: 'breaker',
    gateway_until: '2026-11-23T06:00:00Z',
    balance_remain: 0,
    balance_total: 5000,
  }),
  account({
    id: 3,
    name: '已禁用号',
    status: 'disabled',
    gateway_state: 'disabled',
    gateway_reason: 'manual_disabled',
    balance_remain: 120,
    balance_total: 100,
    platform: 'loomy',
  }),
  account({
    id: 4,
    name: '降级号',
    gateway_state: 'cooling',
    gateway_reason: 'degraded',
    balance_remain: 900,
    balance_total: 1000,
  }),
]

const usage = new Map([
  [1, { account_id: 1, requests: 340, credits: 9.55, total_tokens: 41000 }],
  [2, { account_id: 2, requests: 0, credits: 0, total_tokens: 0 }],
])

describe('normalizeAccountStatus：?status= 收敛', () => {
  it('cooldown 归一到 cooling（概览页链接用的就是 cooldown）', () => {
    expect(normalizeAccountStatus('cooldown')).toBe('cooling')
    expect(normalizeAccountStatus('cooling')).toBe('cooling')
    expect(normalizeAccountStatus('enabled')).toBe('active')
    expect(normalizeAccountStatus('disabled')).toBe('disabled')
    expect(normalizeAccountStatus('breaker')).toBe('breaker')
    expect(normalizeAccountStatus('')).toBe('all')
    expect(normalizeAccountStatus('weird')).toBe('all')
  })
})

describe('accountStatusMeta：状态页签与 Badge', () => {
  it('active → 活跃 ok；disabled → 已禁用 neutral', () => {
    expect(accountStatusMeta(account({ id: 1 }))).toMatchObject({ key: 'active', label: '活跃', tone: 'ok' })
    expect(accountStatusMeta(account({ id: 2, status: 'disabled' }))).toMatchObject({
      key: 'disabled',
      label: '已禁用',
      tone: 'neutral',
    })
  })

  it('冷却 / 熔断 / 降级按 gateway_reason 区分，标题带恢复时间', () => {
    const cooling = accountStatusMeta(account({ id: 3, gateway_state: 'cooling', gateway_reason: 'cooling', gateway_until: '2026-11-23T06:00:00Z' }))
    expect(cooling).toMatchObject({ key: 'cooling', label: '冷却', tone: 'warn' })
    expect(cooling.title).toContain('至 2026-11-23 06:00')
    expect(accountStatusMeta(account({ id: 4, gateway_state: 'cooling', gateway_reason: 'breaker' })).key).toBe('breaker')
    expect(accountStatusMeta(account({ id: 5, gateway_state: 'cooling', gateway_reason: 'breaker' })).label).toBe('熔断')
    expect(accountStatusMeta(account({ id: 6, gateway_state: 'cooling', gateway_reason: 'degraded' })).key).toBe('degraded')
  })
})

describe('maskAccountId / toAccountRows：行数据', () => {
  it('UID 脱敏保留首 3 末 4；短串原样', () => {
    expect(maskAccountId('19300001234')).toBe('193****1234')
    expect(maskAccountId('123')).toBe('123')
  })

  it('余额占比与语气、到期、今日消耗、绑定 Key、能力声明', () => {
    const rows = toAccountRows(items, usage)
    expect(rows[0].balancePct).toBeCloseTo(95.28, 1)
    // 第 5 轮设计定稿：Meter 不用品牌色装饰，健康余额用中性灰（<20% warn / <5% danger，禁用号 neutral）。
    expect(rows[0].balanceTone).toBe('neutral')
    expect(rows[0].balanceTitle).toBe('4,764 / 5,000')
    expect(rows[0].expireText).toContain('天后到期')
    expect(rows[0].expireTone).toBe('neutral')
    expect(rows[0].todayCredits).toBe(9.55)
    expect(rows[0].todayRequests).toBe(340)
    expect(rows[0].note).toBe('绑定 Key：主 Key')
    expect(rows[0].features.growth).toBe(true)
    expect(rows[0].features.catTravel).toBe(true)
    expect(rows[0].features.checkin).toBe(false)
    expect(rows[0].tokenText).toBe('令牌 12 天')
  })

  it('额度为 0 → danger；禁用号平台能力按落库平台判定；无今日数据为 0', () => {
    const rows = toAccountRows(items, usage)
    expect(rows[1].balanceTone).toBe('danger')
    expect(rows[1].todayCredits).toBe(0)
    expect(rows[2].features.checkin).toBe(true)
    expect(rows[2].features.onboarding).toBe(true)
    expect(rows[2].features.growth).toBe(false)
    expect(rows[3].statusKey).toBe('degraded')
    expect(rows[3].balanceTone).toBe('neutral')
  })

  it('无 usage 时今日消耗为 0；空 items 返回空数组', () => {
    expect(toAccountRows(items)?.[0].todayRequests).toBe(0)
    expect(toAccountRows(null)).toEqual([])
  })
})

describe('accountTabCounts / filterAccountRows：页签与筛选', () => {
  const rows = toAccountRows(items, usage)

  it('计数：全部/活跃/禁用/冷却(含降级)/熔断', () => {
    expect(accountTabCounts(rows)).toEqual({ all: 4, active: 1, disabled: 1, cooling: 1, breaker: 1 })
  })

  it('冷却页签包含降级；熔断只含熔断', () => {
    expect(filterAccountRows(rows, { status: 'cooling' }).map((row) => row.id)).toEqual([4])
    expect(filterAccountRows(rows, { status: 'cooldown' }).map((row) => row.id)).toEqual([4])
    expect(filterAccountRows(rows, { status: 'breaker' }).map((row) => row.id)).toEqual([2])
    expect(filterAccountRows(rows, { status: 'active' }).map((row) => row.id)).toEqual([1])
    expect(filterAccountRows(rows, { status: 'disabled' }).map((row) => row.id)).toEqual([3])
  })

  it('搜索名称 / UID / 绑定 Key；积分区间', () => {
    expect(filterAccountRows(rows, { q: '主号' }).map((row) => row.id)).toEqual([1])
    expect(filterAccountRows(rows, { q: '19300001234' })).toHaveLength(4)
    expect(filterAccountRows(rows, { q: '主 key' }).map((row) => row.id)).toEqual([1])
    expect(filterAccountRows(rows, { min: 500 }).map((row) => row.id)).toEqual([1, 4])
    expect(filterAccountRows(rows, { min: 0, max: 200 }).map((row) => row.id)).toEqual([2, 3])
  })

  it('不修改入参', () => {
    filterAccountRows(rows, { q: 'x' })
    expect(rows).toHaveLength(4)
  })
})

describe('sortAccountRows / paginateAccountRows', () => {
  const rows = toAccountRows(items, usage)

  it('默认 id 倒序；积分升序；未知字段保持原顺序', () => {
    expect(sortAccountRows(rows, 'id', 'desc').map((row) => row.id)).toEqual([4, 3, 2, 1])
    expect(sortAccountRows(rows, 'credits', 'asc').map((row) => row.id)).toEqual([2, 3, 4, 1])
    expect(sortAccountRows(rows, 'unknown', 'asc').map((row) => row.id)).toEqual([1, 2, 3, 4])
    expect(sortAccountRows(rows, 'name', '').map((row) => row.id)).toEqual([1, 2, 3, 4])
    expect(ACCOUNT_SORT_KEYS).toContain('last_used')
  })

  it('分页：越界回退最后一页；空数据 0 页', () => {
    expect(paginateAccountRows(rows, 2, 3)).toEqual({
      items: [rows[3]],
      total: 4,
      pageCount: 2,
      page: 2,
    })
    expect(paginateAccountRows(rows, 9, 3).page).toBe(2)
    expect(paginateAccountRows([], 1, 50)).toEqual({ items: [], total: 0, pageCount: 0, page: 1 })
  })
})

describe('后台任务：snapshot → JobItem / 文案 / 导出 token', () => {
  const finished: AccountJobSnapshot = {
    job_id: 'job-1',
    title: '刷新账号',
    status: 'finished',
    total: 3,
    done: 3,
    percent: 100,
    items: [{ ok: true }, { ok: true }, { ok: false }],
    result: { summary: '刷新完成：成功 2 个，失败 1 个', refreshed: 2, failed: 1 },
  }

  it('完成态 → done + 成功/失败数 + summary', () => {
    expect(toJobItem(finished)).toEqual({
      id: 'job-1',
      name: '刷新账号',
      status: 'done',
      percent: 100,
      succeeded: 2,
      failed: 1,
      message: '刷新完成：成功 2 个，失败 1 个',
    })
    expect(jobDoneText(finished)).toBe('刷新完成：成功 2 个，失败 1 个')
  })

  it('运行中 / 失败态', () => {
    expect(toJobItem({ ...finished, status: 'running', percent: 40 })).toMatchObject({ status: 'running', percent: 40 })
    const failed = toJobItem({ ...finished, status: 'failed', error: '上游 401', result: null })
    expect(failed).toMatchObject({ status: 'failed', message: '上游 401' })
    expect(jobDoneText({ ...finished, status: 'failed', error: '上游 401', result: null })).toBe('任务失败：上游 401')
  })

  it('没有 result.summary 时按完成数拼；空快照返回 null', () => {
    expect(jobDoneText({ ...finished, result: null })).toBe('任务完成：3/3')
    expect(toJobItem(null)).toBeNull()
  })

  it('导出任务 token 只在 result.file_token 时返回', () => {
    expect(exportTokenOf({ ...finished, result: { file_token: 'tok-9' } })).toBe('tok-9')
    expect(exportTokenOf(finished)).toBe('')
    expect(exportTokenOf(null)).toBe('')
  })
})

describe('平台能力声明：accountFeatures / addAccountModes', () => {
  it('能力按落库平台给，未登记平台全 false', () => {
    expect(accountFeatures('workbuddy')).toEqual({
      checkin: false,
      onboarding: false,
      activity: false,
      growth: true,
      catTravel: true,
    })
    expect(accountFeatures('loomy')).toMatchObject({ checkin: true, onboarding: true, growth: false })
    expect(accountFeatures('wps-lingxi')).toMatchObject({ checkin: true, activity: true })
    expect(accountFeatures('qoder')).toMatchObject({ checkin: true })
    expect(accountFeatures('unknown')).toMatchObject({ checkin: false, growth: false })
    expect(accountFeatures(null)).toMatchObject({ catTravel: false })
  })

  it('添加方式来自平台声明：WorkBuddy 有手机号/手动/导入，web-login 平台有浏览器授权', () => {
    expect(addAccountModes({ id: 'workbuddy', storage_platform: 'workbuddy', actions: ['refresh-quota'] })).toEqual([
      { key: 'phone', label: '手机号登录' },
      { key: 'manual', label: '手动添加' },
      { key: 'import', label: '导入账号' },
    ])
    expect(
      addAccountModes({
        id: 'trae-cn',
        storage_platform: 'trae',
        auth_mode: 'web-login',
        actions: ['begin-auth', 'scan', 'import'],
      }).map((mode) => mode.key),
    ).toEqual(['authorize', 'import'])
    expect(addAccountModes(null).map((mode) => mode.key)).toEqual(['import'])
  })
})