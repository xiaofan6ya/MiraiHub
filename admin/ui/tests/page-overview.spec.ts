/**
 * S21 概览：接口适配 / 英雄卡 / 用量条 / 账号监测 / 告警 / 缓存 / 流量带 的单测。
 *
 * 数据形状照抄后端真实响应：
 *   · GET /api/overview        → admin/platform/overview_router.py:overview（payload 538 行起）
 *   · GET /api/sentinel/status → admin/sentinel.py:status + sentinel_router.py
 *   · GET /api/stats/dashboard → admin/routers/stats.py:_dashboard_from_rollup（platforms[]）
 * 契约见 docs/ui-v2/contracts/overview.md。
 */
import { describe, expect, it } from 'vitest'
import {
  buildAlerts,
  buildBalanceHero,
  buildCache,
  buildLanes,
  buildMonitorRows,
  buildUsageItems,
  daysLabel,
  lanesTotal,
  monitorConclusion,
  normalizeOverviewDays,
  type OverviewMonitor,
  type OverviewPayload,
  type SentinelStatusPayload,
} from '@/api/overview'
import type { StatsDashboardPayload } from '@/api/stats'

const today = {
  requests: 801,
  ok_requests: 800,
  prompt_tokens: 32000,
  completion_tokens: 9000,
  total_tokens: 41000,
  cached_tokens: 3000,
  credits: 9.55,
}

const window7 = {
  requests: 1250,
  ok_requests: 1200,
  prompt_tokens: 50000,
  completion_tokens: 15000,
  total_tokens: 65000,
  cached_tokens: 5000,
  credits: 198.91,
  days: 7,
}

const monitor: OverviewMonitor = {
  active: 3,
  cooling: 0,
  breaker: 0,
  disabled: 0,
  no_balance: 0,
  total: 3,
  items: [],
}

/** /api/overview 的真实响应形状（只列本页消费的字段，值全部来自后端字段名）。 */
const payload: OverviewPayload = {
  platform: '',
  realm: '',
  platform_label: '全部平台',
  accounts: {
    total: 3,
    balance_remain: 12954,
    balance_total: 17583,
    disabled_balance: 0,
    used_ratio: null,
  },
  models: 41,
  keys: 5,
  today,
  window: window7,
  month: { ...window7, days: 30 },
  average_credits: 66.3,
  cache: {
    cached_tokens: 5000,
    prompt_tokens: 50000,
    miss_tokens: 45000,
    rate: 0.0909,
    samples: 1200,
    note: '',
    by_platform: [],
  },
  monitor,
  sections: 'all',
  now: '2026-11-23T15:01:00Z',
}

const traffic: StatsDashboardPayload = {
  window: {
    days: 1,
    granularity: 'hour',
    start: '2026-11-23T00:00:00Z',
    end: '2026-11-24T00:00:00Z',
    minutes: 1440,
  },
  filters: { platform: '', model: '' },
  platform_label: '全部平台',
  summary: {
    requests: 10000,
    successes: 9900,
    failures: 100,
    success_rate: 0.99,
    credits: 1234,
    total_tokens: 650000,
    prompt_tokens: 500000,
    completion_tokens: 150000,
    cached_tokens: 50000,
    cache_hit_rate: 0.1,
    rpm: 6.9,
    tpm: 451,
    avg_latency_ms: 450,
    avg_ttfb_ms: 120,
    throughput_tps: 95.5,
    window_minutes: 1440,
  },
  models: [],
  platforms: [
    {
      value: 'workbuddy',
      label: 'WorkBuddy',
      requests: 8000,
      success_rate: 0.998,
      tokens: 400000,
      credits: 900,
    },
    {
      value: 'trae',
      label: 'Trae',
      requests: 2000,
      success_rate: null,
      tokens: 100000,
      credits: 200,
    },
    {
      value: 'loomy',
      label: 'Loomy',
      requests: 0,
      success_rate: 0.5,
      tokens: 0,
      credits: 0,
    },
  ],
  distribution: { buckets: [], series: [] },
  trend: { buckets: [], total_requests: [], series: [] },
  ranking: [],
  errors: [],
  source: 'rollup',
}

const sentinelEmpty: SentinelStatusPayload = {
  tripped: false,
  accounts_affected: 0,
  pause: {},
}

describe('normalizeOverviewDays / daysLabel', () => {
  it('只认 1 / 7 / 30', () => {
    expect(normalizeOverviewDays('7')).toBe(7)
    expect(normalizeOverviewDays(30)).toBe(30)
    expect(normalizeOverviewDays('abc')).toBe(1)
    expect(normalizeOverviewDays('90')).toBe(1)
  })

  it('范围文案', () => {
    expect(daysLabel(1)).toBe('今日')
    expect(daysLabel(7)).toBe('近 7 天')
    expect(daysLabel(30)).toBe('近 30 天')
  })
})

describe('buildBalanceHero：剩余额度英雄卡', () => {
  it('剩余/总额、三行消耗与总额度说明', () => {
    const hero = buildBalanceHero(payload.accounts, payload.today, payload.window, payload.average_credits)
    expect(hero.remainText).toBe('12,954')
    expect(hero.totalText).toBe('17,583')
    expect(hero.ratio).toBeCloseTo(12954 / 17583, 6)
    expect(hero.ratioText).toBe('74%')
    expect(hero.tone).toBe('brand')
    expect(hero.caption).toBe('总额度 17,583 · 3 个账号')
    expect(hero.note).toBe('')
    expect(hero.rows).toEqual([
      { label: '今日', value: '9.55' },
      { label: '7 天', value: '198.91' },
      { label: '户均', value: '66.3' },
    ])
  })

  it('used_ratio 优先：剩 5% warn，剩 3% danger', () => {
    const warn = buildBalanceHero({ ...payload.accounts, used_ratio: 0.95 }, today, window7, 1)
    expect(warn.ratio).toBeCloseTo(0.05, 6)
    expect(warn.tone).toBe('warn')
    const danger = buildBalanceHero({ ...payload.accounts, used_ratio: 0.97 }, today, window7, 1)
    expect(danger.ratio).toBeCloseTo(0.03, 6)
    expect(danger.tone).toBe('danger')
  })

  it('总额度为 0：ratio 0、neutral，不冒充百分比', () => {
    const hero = buildBalanceHero(
      { total: 0, balance_remain: 0, balance_total: 0, disabled_balance: 0, used_ratio: null },
      today,
      window7,
      null,
    )
    expect(hero.ratio).toBe(0)
    expect(hero.ratioText).toBe('0%')
    expect(hero.tone).toBe('neutral')
    expect(hero.rows[2].value).toBe('—')
  })

  it('已禁用账号余额单独说明', () => {
    const hero = buildBalanceHero({ ...payload.accounts, disabled_balance: 125 }, today, window7, 66.3)
    expect(hero.note).toBe('另有 125 在已禁用账号上，不参与统计')
  })

  it('空 payload 五格兜底为 —', () => {
    const hero = buildBalanceHero(null, null, null, null)
    expect(hero.remainText).toBe('0')
    expect(hero.rows.map((row) => row.value)).toEqual(['—', '—', '—'])
  })
})

describe('buildUsageItems：右侧用量 StatStrip', () => {
  it('请求 / 成功率 / 输入 / 输出 / 合计', () => {
    const items = buildUsageItems(window7, 7)
    expect(items.map((item) => item.label)).toEqual([
      '请求数',
      '成功率',
      '输入 Token',
      '输出 Token',
      'Token 合计',
    ])
    expect(items[0].value).toBe('1,250')
    expect(items[0].foot).toBe('成功 1,200 · 失败 50')
    expect(items[1].value).toBe('96.0%')
    expect(items[1].tone).toBe('danger')
    expect(items[2].value).toBe('50K')
    expect(items[3].value).toBe('15K')
    expect(items[4].value).toBe('65K')
    expect(items[4].foot).toBe('缓存命中 5,000')
  })

  it('无请求：成功率 —，tone muted；空窗口五格 —', () => {
    const items = buildUsageItems({ ...window7, requests: 0, ok_requests: 0 }, 7)
    expect(items[1].value).toBe('—')
    expect(items[1].tone).toBe('muted')
    expect(buildUsageItems(null, 1).map((item) => item.value)).toEqual(['—', '—', '—', '—', '—'])
  })
})

describe('buildMonitorRows / monitorConclusion：账号状态', () => {
  it('5 行固定顺序、占比与 0 值灰字', () => {
    const rows = buildMonitorRows({ ...monitor, active: 3, cooling: 1, total: 4 })
    expect(rows.map((row) => row.label)).toEqual(['活跃', '冷却', '熔断', '额度耗尽', '已禁用'])
    expect(rows[0]).toMatchObject({ count: 3, pct: 75, faint: false, tone: 'ok' })
    expect(rows[1]).toMatchObject({ count: 1, pct: 25, faint: false, tone: 'warn' })
    expect(rows[2]).toMatchObject({ count: 0, pct: 0, faint: true })
  })

  it('结论：全部正常 / 冷却 / 熔断 / 额度耗尽 / 已禁用 / 无账号', () => {
    expect(monitorConclusion(monitor)).toEqual({ text: '全部账号正常', tone: 'ok', to: '' })
    expect(monitorConclusion({ ...monitor, active: 1, cooling: 2 })).toEqual({
      text: '2 个账号冷却中，查看',
      tone: 'warn',
      to: '/accounts?status=cooldown',
    })
    expect(monitorConclusion({ ...monitor, active: 1, breaker: 2 })).toEqual({
      text: '2 个账号熔断中，查看',
      tone: 'warn',
      to: '/accounts',
    })
    expect(monitorConclusion({ ...monitor, active: 2, no_balance: 1 })).toEqual({
      text: '1 个账号额度耗尽，查看',
      tone: 'warn',
      to: '/credits',
    })
    expect(monitorConclusion({ ...monitor, active: 2, disabled: 1 })).toEqual({
      text: '1 个账号已禁用，查看',
      tone: 'warn',
      to: '/accounts',
    })
    expect(monitorConclusion({ ...monitor, active: 0, total: 0 })).toEqual({
      text: '还没有账号，先去账号池添加',
      tone: 'warn',
      to: '/accounts',
    })
  })
})

describe('buildAlerts：告警横幅', () => {
  it('无异常时不出横幅', () => {
    expect(buildAlerts(payload, sentinelEmpty)).toEqual([])
    expect(buildAlerts(null, null)).toEqual([])
  })

  it('批次封控是 danger，平台暂停 / 冷却 / 额度不足是 warn，顺序固定', () => {
    const alerts = buildAlerts(
      {
        ...payload,
        accounts: { ...payload.accounts, used_ratio: 0.85 },
        monitor: { ...monitor, active: 1, cooling: 2, total: 3 },
      },
      { tripped: true, accounts_affected: 5, pause: { trae: { until: 1 } } },
    )
    expect(alerts.map((alert) => [alert.level, alert.title])).toEqual([
      ['danger', '5 个账号被上游封控，已达批次阈值'],
      ['warn', '有 1 个平台处于暂停状态'],
      ['warn', '2 个账号冷却 / 熔断中'],
      ['warn', '剩余额度不足 15%'],
    ])
    expect(alerts[3].detail).toBe('剩余 12,954 / 总额度 17,583')
  })

  it('被封控但未达阈值：danger + 「被上游拒绝过」文案', () => {
    const alerts = buildAlerts(payload, { tripped: false, accounts_affected: 2 })
    expect(alerts).toHaveLength(1)
    expect(alerts[0].level).toBe('danger')
    expect(alerts[0].title).toContain('2 个账号被上游拒绝过')
  })
})

describe('buildCache：缓存命中卡', () => {
  it('命中率 → Ring 数值 / 文案 / 语气，KV 与默认说明', () => {
    const block = buildCache(payload.cache)
    expect(block.value).toBeCloseTo(9.09, 2)
    expect(block.text).toBe('9%')
    expect(block.tone).toBe('warn')
    expect(block.kv).toEqual([
      { label: '输入命中', value: '5,000' },
      { label: '输入总量', value: '50K' },
      { label: '样本请求', value: '1,200' },
    ])
    expect(block.note).toBe('只统计含缓存字段的上游响应')
  })

  it('≥10% ok、<5% neutral；后端 note 优先', () => {
    expect(buildCache({ ...payload.cache, rate: 0.12 }).tone).toBe('ok')
    expect(buildCache({ ...payload.cache, rate: 0.12 }).text).toBe('12%')
    expect(buildCache({ ...payload.cache, rate: 0.03 }).tone).toBe('neutral')
    const noted = buildCache({ ...payload.cache, note: '自 2026-11-01 起' })
    expect(noted.note).toBe('自 2026-11-01 起')
  })

  it('无样本：value 0、文字 —、neutral', () => {
    const block = buildCache({ ...payload.cache, rate: null })
    expect(block.value).toBe(0)
    expect(block.text).toBe('—')
    expect(block.tone).toBe('neutral')
    expect(buildCache(null).kv[0].value).toBe('—')
  })
})

describe('buildLanes / lanesTotal：流量带', () => {
  const specs = [
    { id: 'workbuddy', label: 'WorkBuddy', icon: 'workbuddy', enabled: true },
    { id: 'trae-cn', label: 'Trae 国内版', icon: 'trae', enabled: false, storage_platform: 'trae' },
  ]

  it('按请求数降序，图标 / 禁用状态来自平台清单（storage_platform 兜底）', () => {
    const lanes = buildLanes(traffic, specs)
    expect(lanes.map((lane) => [lane.id, lane.count])).toEqual([
      ['workbuddy', 8000],
      ['trae', 2000],
      ['loomy', 0],
    ])
    expect(lanes[0].logo).toBe('workbuddy')
    expect(lanes[0].disabled).toBe(false)
    expect(lanes[1].logo).toBe('trae')
    expect(lanes[1].disabled).toBe(true)
    expect(lanes[1].successRate).toBeNull()
    expect(lanes[2].logo).toBe('loomy')
    expect(lanes[2].disabled).toBe(false)
  })

  it('总量 = 各线路之和；空数据返回空数组', () => {
    expect(lanesTotal(buildLanes(traffic, specs))).toBe(10000)
    expect(buildLanes(null, null)).toEqual([])
    expect(lanesTotal([])).toBe(0)
  })
})
