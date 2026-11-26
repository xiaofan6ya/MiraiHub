/**
 * S23 积分到期：接口适配 / 数据转换纯函数单测。
 * 响应形状按 admin/routers/credits.py 与 admin/routing_policy.py 的真实返回构造。
 */
import { describe, expect, it } from 'vitest'
import {
  accountStatusText,
  creditStatItems,
  paginateRows,
  riskView,
  selectionPolicyDirty,
  soonestLabel,
  sortAccountRows,
  syncSummaryText,
  toPackageRows,
  usageBars,
  type CreditAccountRow,
  type CreditExpiryPayload,
  type CreditPackageRow,
  type CreditUsagePayload,
} from '@/api/credits'

const PKG: CreditPackageRow = {
  package_code: 'pkg-2026-12',
  display_name: '月度积分包',
  remaining: 500,
  total: 1000,
  expire_at: 1798675200000,
  expire_label: '2026-12-31 到期',
  expire_date: '12-31',
  expired: false,
  expiring_soon: true,
  account_id: 12,
  account_name: '193****2486',
}

const ACCOUNT: CreditAccountRow = {
  account_id: 12,
  account_name: '193****2486',
  uid: 'uid-12',
  status: 'active',
  selection_priority: null,
  balance_remain: 4764,
  total_remaining: 4764,
  total_capacity: 5000,
  expiring_soon_remaining: 11514,
  evergreen_remaining: 0,
  expired_remaining: 0,
  soonest_expire_at: 1798675200000,
  soonest_days_left: 15.2,
  soonest_label: '15 天后到期',
  expiring_soon: true,
  expiring_package_count: 3,
  package_count: 4,
  active_package_count: 4,
  synced_at: 1761858456000,
  next_expiring: [PKG],
}

const ACCOUNT_2: CreditAccountRow = {
  ...ACCOUNT,
  account_id: 13,
  account_name: '137****8653',
  uid: 'uid-13',
  total_remaining: 8192,
  expiring_soon_remaining: 0,
  soonest_expire_at: null,
  soonest_days_left: null,
  expiring_soon: false,
  next_expiring: [],
}

/** GET /api/credits/expiry 的真实响应。 */
const EXPIRY: CreditExpiryPayload = {
  generated_at: 1761858456000,
  expiring_soon_days: 7,
  suggested_days: 7,
  summary: {
    accounts: 3,
    available_remaining: 12954,
    total_remaining: 12956,
    snapshot_accounts: 2,
    expiring_soon_remaining: 11514,
    evergreen_remaining: 0,
    expired_remaining: 0,
    expiring_accounts: 1,
    expiring_packages: 124,
    soonest_expire_at: 1798675200000,
    soonest_days_left: 15.2,
    soonest_account_id: 12,
    soonest_account_name: '193****2486',
    at_risk_ratio: 0.8886,
    packages_total: 124,
  },
  windows: [
    { days: 1, amount: 0, packages: 0 },
    { days: 3, amount: 0, packages: 0 },
    { days: 7, amount: 0, packages: 0 },
    { days: 14, amount: 0, packages: 0 },
    { days: 30, amount: 11514, packages: 124 },
  ],
  accounts: [ACCOUNT, ACCOUNT_2],
  selection_mode: 'automatic',
  selection_order: [12, 13],
  routing_policy: {
    mode: 'expiry_weighted',
    upstream_start_interval_ms: 75,
    modes: [
      { value: 'expiry_weighted', label: '到期排名加权', description: '按到期先后赋予权重。' },
      { value: 'expiring', label: '严格到期优先', description: '先用最早到期的健康账号。' },
    ],
  },
  packages: [PKG],
  packages_total: 124,
  package_offset: 0,
  package_limit: 60,
  has_more: true,
}

describe('最紧迫账号文案', () => {
  it('天 / 小时 / 全部长期有效', () => {
    expect(soonestLabel(15.2)).toBe('15 天后到期')
    expect(soonestLabel(0.5)).toBe('12 小时内到期')
    expect(soonestLabel(0.01)).toBe('1 小时内到期')
    expect(soonestLabel(null)).toBe('全部长期有效')
    expect(soonestLabel(undefined)).toBe('全部长期有效')
  })
})

describe('顶部 StatStrip（5 格，旧版第二行合并去重）', () => {
  it('账号总数 / 可用积分 / 7 天内到期 / 长期有效 / 最紧迫账号', () => {
    const items = creditStatItems(EXPIRY)
    expect(items.map((item) => item.label)).toEqual([
      '账号总数',
      '可用积分',
      '7 天内到期',
      '长期有效',
      '最紧迫账号',
    ])
    expect(items[0].value).toBe('3')
    expect(items[0].foot).toBe('2 个已采集到期快照')
    expect(items[1].value).toBe('12,954')
    expect(items[2].value).toBe('11,514')
    expect(items[2].tone).toBe('warn')
    expect(items[2].foot).toContain('88.9%')
    expect(items[4].value).toBe('193****2486')
    expect(items[4].foot).toBe('15 天后到期')
    expect(creditStatItems(null)).toEqual([])
  })
})

describe('到期风险分布（StackMeter）', () => {
  it('≤30 天取 days=30 档；30 天以外 = 总量 − ≤30 天 − 长期有效', () => {
    const view = riskView(EXPIRY)
    expect(view.items).toEqual([
      { label: '≤30 天（124 个包）', value: 11514, tone: 'warn' },
      { label: '30 天以外', value: 1442, tone: 'neutral' },
      { label: '长期有效', value: 0, tone: 'ok' },
    ])
    expect(view.total).toBe(12956)
    expect(view.hint).toBe('共 12,956 积分，按到期时间分档（已采集 2/3 个账号）')
    expect(view.empty).toBe(false)
  })

  it('全部为 0 时 empty=true；没有 payload 也安全', () => {
    const empty = riskView({
      ...EXPIRY,
      summary: { ...EXPIRY.summary, total_remaining: 0, expiring_soon_remaining: 0, evergreen_remaining: 0, snapshot_accounts: 3 },
      windows: [],
    })
    expect(empty.empty).toBe(true)
    expect(empty.hint).toBe('')
    expect(riskView(null).empty).toBe(true)
  })
})

describe('积分包行转换', () => {
  it('剩余占比、格式化文本与行键（同包名跨账号不冲突）', () => {
    const rows = toPackageRows([PKG])
    expect(rows[0].remainingRatio).toBe(50)
    expect(rows[0].remainingText).toBe('500')
    expect(rows[0].totalText).toBe('1,000')
    expect(rows[0].expireText).toBe('12-31')
    expect(rows[0].rowKey).toBe('12-pkg-2026-12-1798675200000')
  })

  it('总额为 0 时不冒充百分比；空值安全', () => {
    const rows = toPackageRows([{ ...PKG, total: 0, remaining: 0, expire_date: '', expire_label: '', expire_at: null }])
    expect(rows[0].remainingRatio).toBe(0)
    expect(rows[0].totalText).toBe('—')
    expect(rows[0].expireText).toBe('长期有效')
    expect(toPackageRows(null)).toEqual([])
  })
})

describe('账号排行排序 / 分页', () => {
  it('按剩余积分升降序', () => {
    expect(sortAccountRows([ACCOUNT, ACCOUNT_2], 'total_remaining', 'asc').map((row) => row.account_id)).toEqual([12, 13])
    expect(sortAccountRows([ACCOUNT, ACCOUNT_2], 'total_remaining', 'desc').map((row) => row.account_id)).toEqual([13, 12])
  })

  it('无到期日的账号永远排最后（升降序都不翻到最前）', () => {
    expect(sortAccountRows([ACCOUNT, ACCOUNT_2], 'soonest_expire_at', 'asc').map((row) => row.account_id)).toEqual([12, 13])
    expect(sortAccountRows([ACCOUNT, ACCOUNT_2], 'soonest_expire_at', 'desc').map((row) => row.account_id)).toEqual([12, 13])
  })

  it('空排序保持后端返回的紧迫度顺序；分页切片正确', () => {
    expect(sortAccountRows([ACCOUNT_2, ACCOUNT], '', '').map((row) => row.account_id)).toEqual([13, 12])
    expect(paginateRows([1, 2, 3, 4, 5], 2, 2)).toEqual([3, 4])
    expect(paginateRows([1, 2, 3], 9, 2)).toEqual([])
  })
})

describe('账号消耗构成（BarList）', () => {
  const usage: CreditUsagePayload = {
    range_days: 7,
    total_credits: 1234.5,
    accounts: [
      { account_id: 13, account_name: '137****8653', requests: 43, credits: 154, tokens: 900, remaining: 8192, expiring_soon_remaining: 0, soonest_days_left: null },
      { account_id: 12, account_name: '193****2486', requests: 595, credits: 44, tokens: 1200, remaining: 4764, expiring_soon_remaining: 11514, soonest_days_left: 15.2 },
      { account_id: 14, account_name: '零消耗账号', requests: 2, credits: 0, tokens: 0, remaining: 100, expiring_soon_remaining: 0, soonest_days_left: null },
    ],
    models: [],
  }

  it('过滤 0 消耗、label 带次数、hint 带合计', () => {
    const view = usageBars(usage, 12)
    // v2.1：消耗构成是分类条，统一用品牌色（不着色表达状态）
    expect(view.items).toEqual([
      { label: '137****8653 · 43 次', value: 154, tone: 'brand' },
      { label: '193****2486 · 595 次', value: 44, tone: 'brand' },
    ])
    expect(view.hint).toBe('近 7 天 · 合计 1,234.5 积分')
    expect(view.empty).toBe(false)
  })

  it('没有消耗时 empty=true', () => {
    expect(usageBars({ ...usage, accounts: [], total_credits: 0 }).empty).toBe(true)
    expect(usageBars(null).empty).toBe(true)
  })
})

describe('采集结果 / 策略脏判定 / 状态文案', () => {
  it('syncSummaryText 与旧版同一口径', () => {
    expect(
      syncSummaryText({ ok: true, total: 3, refreshed: 2, failed: 1, credits_expiring_soon: 11514, detail: [] }),
    ).toBe('采集完成：成功 2/3，1 个失败，7 天内到期 11,514')
    expect(syncSummaryText({ ok: true, total: 3, refreshed: 3, failed: 0, credits_expiring_soon: 0, detail: [] })).toBe(
      '采集完成：成功 3/3',
    )
    expect(syncSummaryText(null)).toBe('')
  })

  it('selectionPolicyDirty 比较模式与间隔', () => {
    const policy = EXPIRY.routing_policy
    expect(selectionPolicyDirty(policy, 'expiry_weighted', 75)).toBe(false)
    expect(selectionPolicyDirty(policy, 'expiring', 75)).toBe(true)
    expect(selectionPolicyDirty(policy, 'expiry_weighted', 0)).toBe(true)
    expect(selectionPolicyDirty(null, 'expiry_weighted', 75)).toBe(false)
  })

  it('账号状态文案', () => {
    expect(accountStatusText('active')).toBe('活跃')
    expect(accountStatusText('banned')).toBe('封禁')
    expect(accountStatusText('expired')).toBe('已过期')
    expect(accountStatusText('weird')).toBe('weird')
  })
})
