/**
 * 概览 v2.1 的纯函数（10-visual-refresh / 第 1、3 轮审查）：
 * 零流量线路、多区平台合并、平台健康、KPI 行、口径标签、最近请求。
 */
import { describe, expect, it } from 'vitest'
import {
  buildLanes,
  buildOverviewKpis,
  buildPlatformHealth,
  buildRecent,
  groupLabel,
  overviewScopeLabel,
  type OverviewPayload,
  type OverviewPlatformSpec,
} from '@/api/overview'
import type { StatsDashboardPayload } from '@/api/stats'

function dashboard(platforms: StatsDashboardPayload['platforms'], totals: number[] = []): StatsDashboardPayload {
  return {
    window: { days: 1, granularity: 'hour', start: '', end: '', minutes: 1440 },
    filters: { platform: '', model: '' },
    platform_label: '',
    summary: {
      requests: 0,
      successes: 0,
      failures: 0,
      success_rate: null,
      credits: 0,
      total_tokens: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      cached_tokens: 0,
      cache_hit_rate: null,
      rpm: 0,
      tpm: 0,
      avg_latency_ms: 0,
      avg_ttfb_ms: 0,
      throughput_tps: 0,
      window_minutes: 1440,
    },
    models: [],
    platforms,
    distribution: { buckets: [], series: [] },
    trend: { buckets: totals.map(String), total_requests: totals, series: [] },
    ranking: [],
    errors: [],
  }
}

const SPECS: OverviewPlatformSpec[] = [
  { id: 'workbuddy', label: 'WorkBuddy', icon: 'workbuddy', enabled: true, storage_platform: 'workbuddy', account_count: 35, active_count: 0 },
  { id: 'trae-cn', label: 'Trae 国内版', icon: 'trae', enabled: true, storage_platform: 'trae', account_count: 2, active_count: 2 },
  { id: 'trae-intl', label: 'Trae 国际版', icon: 'trae', enabled: true, storage_platform: 'trae', account_count: 1, active_count: 1 },
  { id: 'loomy', label: 'Loomy', icon: 'loomy', enabled: true, storage_platform: 'loomy', account_count: 0, active_count: 0 },
  { id: 'minimax', label: 'MiniMax', icon: 'minimax', enabled: false, storage_platform: 'minimax' },
]

describe('groupLabel：多区平台取公共前缀', () => {
  it('Trae 国内版 / 国际版 → Trae；单个原样；空用兜底', () => {
    expect(groupLabel(['Trae 国内版', 'Trae 国际版'], 'trae')).toBe('Trae')
    expect(groupLabel(['Loomy'], 'loomy')).toBe('Loomy')
    expect(groupLabel([], 'x')).toBe('x')
    // 没有像样的公共前缀时退回第一个名称
    expect(groupLabel(['甲', '乙'], 'x')).toBe('甲')
  })
})

describe('buildLanes：零流量平台画细灰线', () => {
  it('只有一个平台有流量时，其余已启用平台按落库平台补成 count=0 的线路（停用的不补）', () => {
    const lanes = buildLanes(
      dashboard([{ value: 'workbuddy', label: 'WorkBuddy', requests: 120, success_rate: 1, tokens: 0, credits: 0 }]),
      SPECS,
    )
    expect(lanes.map((lane) => [lane.name, lane.count])).toEqual([
      ['WorkBuddy', 120],
      ['Loomy', 0],
      ['Trae', 0],
    ])
    // trae 两个区合并成一条，id 用落库名
    expect(lanes.find((lane) => lane.name === 'Trae')?.id).toBe('trae')
  })

  it('dashboard 已有落库平台 trae 时，不再给 trae-cn / trae-intl 各补一条', () => {
    const lanes = buildLanes(
      dashboard([{ value: 'trae', label: 'Trae', requests: 5, success_rate: 1, tokens: 0, credits: 0 }]),
      SPECS,
    )
    expect(lanes.filter((lane) => lane.name.startsWith('Trae'))).toHaveLength(1)
  })
})

describe('buildPlatformHealth', () => {
  it('状态只表达异常；0 账号且无流量的折叠进 idle；排序 有流量 > 有账号', () => {
    const health = buildPlatformHealth(
      dashboard([
        { value: 'trae', label: 'Trae', requests: 100, success_rate: 0.5, tokens: 10, credits: 0 },
      ]),
      SPECS,
    )
    expect(health.rows.map((row) => [row.name, row.tone])).toEqual([
      ['Trae', 'danger'],
      ['WorkBuddy', 'warn'],
    ])
    expect(health.rows[0]).toMatchObject({ accounts: 3, active: 3, share: 100, rateText: '50.0%' })
    expect(health.rows[1]).toMatchObject({ status: '无可用账号', requests: 0, rateText: '—' })
    expect(health.idle).toEqual(['Loomy'])
  })

  it('没有任何数据时返回空', () => {
    expect(buildPlatformHealth(null, [])).toEqual({ rows: [], idle: [] })
  })
})

function payload(overrides: Partial<OverviewPayload> = {}): OverviewPayload {
  const win = { requests: 10, ok_requests: 9, prompt_tokens: 1000, completion_tokens: 200, total_tokens: 1200, cached_tokens: 100, credits: 1 }
  return {
    platform: 'workbuddy',
    realm: '',
    platform_label: 'WorkBuddy',
    accounts: { total: 1, balance_remain: 50, balance_total: 100, disabled_balance: 0, used_ratio: 0.5 },
    models: 1,
    keys: 1,
    today: win,
    window: { ...win, days: 1 },
    month: win,
    average_credits: 1,
    cache: { cached_tokens: 0, prompt_tokens: 0, miss_tokens: 0, rate: null, samples: 0, note: '', by_platform: [] },
    monitor: { active: 1, cooling: 0, breaker: 0, disabled: 0, no_balance: 0, total: 1, items: [] },
    sections: 'all',
    now: '',
    ...overrides,
  }
}

describe('buildOverviewKpis', () => {
  it('4 张卡：剩余额度 / 请求（带折线）/ 成功率 / Token', () => {
    const kpis = buildOverviewKpis(payload(), dashboard([], [1, 2, 3]), 1)
    expect(kpis.map((item) => item.key)).toEqual(['balance', 'requests', 'success', 'tokens'])
    expect(kpis[0]).toMatchObject({ value: '50', hint: '总额度 100 · 1 个账号' })
    expect(kpis[1]).toMatchObject({ value: '10', hint: '成功 9 · 失败 1', spark: [1, 2, 3] })
    expect(kpis[2].value).toBe('90.0%')
    expect(kpis[3].hint).toBe('输入 1,000 · 输出 200 · 缓存命中 100')
  })

  it('没有数据时数字是 —，不画折线', () => {
    const kpis = buildOverviewKpis(null, null, 1)
    expect(kpis[0].value).toBe('—')
    expect(kpis[1].spark).toBeUndefined()
  })
})

describe('overviewScopeLabel：口径标签', () => {
  it('新后端 scopes 优先；旧后端按 platform 推断；流量带恒为全网关', () => {
    expect(overviewScopeLabel(payload(), 'usage')).toBe('WorkBuddy')
    expect(overviewScopeLabel(payload({ platform: '' }), 'usage')).toBe('全网关')
    expect(overviewScopeLabel(payload({ scopes: { usage: 'gateway' } }), 'usage')).toBe('全网关')
    expect(overviewScopeLabel(payload(), 'traffic')).toBe('全网关')
  })
})

describe('buildRecent：最近请求', () => {
  it('取 sentinel.traffic.recent，最多 N 条；没有 traffic 时为空', () => {
    expect(buildRecent(null)).toEqual([])
    const rows = buildRecent(
      {
        traffic: {
          platforms: [],
          totals: { requests: 0, ok: 0, error: 0, timeout: 0, interrupted: 0 },
          recent: [
            { rid: 'a', ts: 1_800_000_000, model: 'm1', status: 200 },
            { rid: 'b', ts: 1_800_000_001, model: 'm2', status: 502 },
          ],
        },
      },
      1,
    )
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ rid: 'a', model: 'm1', tone: 'ok' })
    expect(rows[0].at).not.toBe('')
  })
})
