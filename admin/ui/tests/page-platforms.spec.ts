import { describe, expect, it } from 'vitest'
import {
  aggregateAccounts,
  countByState,
  exportFilename,
  filterPlatforms,
  importPayloadFor,
  parsePlatformBackup,
  platformActionsOf,
  scanPlanFor,
  successTone,
  toPlatformCards,
  type AccountSnapshotRow,
  type GatewayConfigPayload,
  type PlatformSpeedPayload,
} from '@/api/platforms-admin'
import type { PlatformSpec } from '@/api/platforms'

/**
 * 契约 docs/ui-v2/contracts/platforms.md 的数据转换（末尾「## 实现对照」）。
 * 测试数据按后端真实响应形状造：
 *   GET /api/platforms            → admin/platform/router.py:list_platforms
 *   GET /api/platform/speed       → admin/platform/bench_router.py:platform_speed（成功率 0–100）
 *   GET /api/gateway/config       → admin/platform/gateway_api.py:get_config
 *   GET /api/accounts?all_platforms=1 → admin/routers/accounts.py:list_accounts
 */

function spec(overrides: Partial<PlatformSpec> = {}): PlatformSpec {
  return {
    id: 'trae-cn',
    label: 'Trae 国内版',
    enabled: true,
    supports_local_scan: true,
    auth_mode: 'local-scan',
    default_realm: 'cn',
    notes: '本机 storage.json 可读',
    icon: 'trae',
    storage_platform: 'trae',
    realm: 'cn',
    realm_label: '国内版',
    realms: [],
    proxied: true,
    rating: {},
    extra: {},
    managed: true,
    actions: ['scan', 'import', 'refresh-quota'],
    quota_unit: 'credits',
    quota_unit_label: '积分',
    account_count: 3,
    realm_counts: { cn: 3 },
    ...overrides,
  }
}

function speedPayload(): PlatformSpeedPayload {
  return {
    hours: 24,
    model: '',
    fastest: 'workbuddy',
    steadiest: 'workbuddy',
    observed_at: '2026-11-23T04:00:00Z',
    items: [
      {
        platform: 'workbuddy',
        label: 'WorkBuddy',
        rank: 1,
        requests: 1786,
        ok_requests: 1750,
        success_rate: 98.0,
        ttfb_p50: 900,
        ttfb_avg: 1200,
        latency_avg: 3950,
        latency_p50: 3200,
        latency_p95: 9000,
        tps_p50: 40.5,
        tps_avg: 42.0,
        jitter: 310,
        samples: 1750,
        low_sample: false,
        score: 3.2,
      },
      {
        platform: 'trae',
        label: 'Trae 国内版',
        rank: 2,
        requests: 175,
        ok_requests: 160,
        success_rate: 91.4,
        ttfb_p50: 1500,
        ttfb_avg: 1700,
        latency_avg: 5200,
        latency_p50: 5000,
        latency_p95: 12000,
        tps_p50: 22.1,
        tps_avg: 23.4,
        jitter: 900,
        samples: 160,
        low_sample: false,
        score: 6.1,
      },
      {
        platform: 'qoder',
        label: 'Qoder 中国版',
        rank: 3,
        requests: 2,
        ok_requests: 2,
        success_rate: 100,
        ttfb_p50: 800,
        ttfb_avg: 800,
        latency_avg: 1000,
        latency_p50: 1000,
        latency_p95: 1100,
        tps_p50: 0,
        tps_avg: 0,
        jitter: 0,
        samples: 2,
        low_sample: true,
        score: 5.8,
      },
    ],
  }
}

function gatewayConfig(): GatewayConfigPayload {
  return {
    strategy: 'priority',
    spread_unbound: false,
    priority: { workbuddy: 100, trae: 80 },
    disabled: { loomy: { at: 1763900000, reason: '界面禁用' } },
    platforms: [{ id: 'workbuddy', label: 'WorkBuddy', managed: true }],
  }
}

function accountRows(): AccountSnapshotRow[] {
  return [
    { id: 1, platform: 'workbuddy', status: 'active', balance_remain: 12800 },
    { id: 2, platform: 'workbuddy', status: 'active', balance_remain: 154 },
    { id: 3, platform: 'workbuddy', status: 'disabled', balance_remain: 9999 },
    { id: 4, platform: 'trae', status: 'active', domain: 'cn', balance_remain: 320 },
    { id: 5, platform: '', status: 'active' },
  ]
}

describe('aggregateAccounts：账号池快照 → 每平台可用账号/剩余额度', () => {
  it('只统计 active 的额度；空平台行忽略', () => {
    const agg = aggregateAccounts(accountRows())
    expect(agg.workbuddy).toEqual({ total: 3, active: 2, credits: 12954 })
    expect(agg.trae).toEqual({ total: 1, active: 1, credits: 320 })
    expect(Object.keys(agg)).toEqual(['workbuddy', 'trae'])
    expect(aggregateAccounts(null)).toEqual({})
  })
})

describe('toPlatformCards：平台列表 + 跑分 + 网关 + 账号聚合', () => {
  it('合并字段：跑分按落库平台匹配（trae-cn → trae），禁用来自 gateway.disabled', () => {
    const cards = toPlatformCards({
      platforms: [
        spec(),
        spec({
          id: 'loomy',
          label: 'Loomy',
          storage_platform: 'loomy',
          realm: '',
          realm_label: '',
          supports_local_scan: true,
          actions: ['scan', 'import', 'refresh-quota'],
          account_count: 0,
        }),
      ],
      speed: speedPayload(),
      gateway: gatewayConfig(),
      accounts: aggregateAccounts(accountRows()),
    })
    const trae = cards[0]
    expect(trae).toMatchObject({
      id: 'trae-cn',
      storage: 'trae',
      enabled: true,
      priority: 80,
      requests: 175,
      successRate: 91.4,
      ttfbMs: 1700,
      tps: 23.4,
      lowSample: false,
      realmLabel: '国内版',
      accountCount: 3,
      activeAccounts: 1,
      credits: 320,
    })
    expect(trae.actions.map((item) => item.name)).toEqual(['scan', 'import', 'refresh-quota'])

    const loomy = cards[1]
    expect(loomy.enabled).toBe(false)
    expect(loomy.disabledReason).toBe('界面禁用')
    expect(loomy.successRate).toBeNull()
    expect(loomy.credits).toBeNull()
    expect(loomy.activeAccounts).toBeNull()
  })

  it('r4：平台分级已删除——后端仍下发 rating.tier 时卡片模型里也没有分级字段', () => {
    const cards = toPlatformCards({
      platforms: [
        spec({ rating: { tier: 'elite', reason: '快', source: 'platform' } }),
        spec({ id: 'loomy', label: 'Loomy', storage_platform: 'loomy', realm: '', rating: {} }),
      ],
    })
    expect(cards).toHaveLength(2)
    for (const card of cards) {
      expect('tier' in card).toBe(false)
      expect(JSON.stringify(card)).not.toMatch(/人上人|拉完了|夯/)
    }
  })

  it('speed 完全缺失时字段为 null（显示 —，不冒充 0）', () => {
    const cards = toPlatformCards({ platforms: [spec()], speed: null, gateway: null, accounts: null })
    expect(cards[0].requests).toBe(0)
    expect(cards[0].successRate).toBeNull()
    expect(cards[0].ttfbMs).toBeNull()
    expect(cards[0].tps).toBeNull()
    expect(cards[0].priority).toBeNull()
  })
})

describe('状态页签 / 搜索 / 成功率着色', () => {
  const cards = toPlatformCards({
    platforms: [
      spec(),
      spec({ id: 'workbuddy', label: 'WorkBuddy', storage_platform: 'workbuddy', realm: '', realm_label: '' }),
      spec({ id: 'loomy', label: 'Loomy', storage_platform: 'loomy', realm: '', realm_label: '' }),
    ],
    gateway: gatewayConfig(),
  })

  it('countByState 与 filterPlatforms', () => {
    expect(countByState(cards)).toEqual({ all: 3, enabled: 2, disabled: 1 })
    expect(filterPlatforms(cards, { state: 'enabled' }).map((card) => card.id)).toEqual(['trae-cn', 'workbuddy'])
    expect(filterPlatforms(cards, { state: 'disabled' }).map((card) => card.id)).toEqual(['loomy'])
    expect(filterPlatforms(cards, { q: '国内' }).map((card) => card.id)).toEqual(['trae-cn'])
    expect(filterPlatforms(cards, { q: ' loomy ' }).map((card) => card.id)).toEqual(['loomy'])
    expect(filterPlatforms(cards, { q: 'trae' }).map((card) => card.id)).toEqual(['trae-cn'])
    expect(filterPlatforms(cards, {})).toHaveLength(3)
  })

  it('成功率阈值：≥90 ok / ≥60 warn / 其余 danger / 无样本 muted', () => {
    expect(successTone(98)).toBe('ok')
    expect(successTone(90)).toBe('ok')
    expect(successTone(89.9)).toBe('warn')
    expect(successTone(60)).toBe('warn')
    expect(successTone(12.5)).toBe('danger')
    expect(successTone(null)).toBe('muted')
    expect(successTone(Number.NaN)).toBe('muted')
  })
})

describe('平台动作（platform-actions.js 的移植）', () => {
  it('后端声明优先，同组只留一个主按钮', () => {
    const actions = platformActionsOf(spec({ actions: ['begin-auth', 'scan', 'import', 'refresh-quota'] }))
    expect(actions.map((item) => item.name)).toEqual(['begin-auth', 'scan', 'import', 'refresh-quota'])
    expect(actions.filter((item) => item.primary)).toHaveLength(1)
    expect(actions[0]).toMatchObject({ name: 'begin-auth', label: '授权登录', primary: true })
  })

  it('import-only：去掉 scan / begin-auth，并保证有 import', () => {
    const actions = platformActionsOf(spec({ auth_mode: 'import-only', actions: ['begin-auth', 'scan'] }))
    expect(actions.map((item) => item.name)).toEqual(['import'])
  })

  it('不支持本地扫描：去掉 scan/import，补上授权登录', () => {
    const actions = platformActionsOf(spec({ supports_local_scan: false, actions: ['scan', 'import', 'checkin'] }))
    expect(actions.map((item) => item.name)).toEqual(['begin-auth', 'checkin'])
  })

  it('未知动作名直接用原名兜底', () => {
    expect(platformActionsOf(spec({ actions: ['foo-bar'] }))[0]).toMatchObject({ name: 'foo-bar', label: 'foo-bar' })
    expect(platformActionsOf(null)).toEqual([])
  })
})

describe('扫描计划（旧版 platformScanAll 的分支）', () => {
  it('workbuddy 一步导入；qoder 只扫描；其它平台扫描+入库', () => {
    expect(scanPlanFor(spec({ storage_platform: 'workbuddy', id: 'workbuddy' }))).toMatchObject({
      kind: 'accounts-import',
      prefix: '/api/accounts',
    })
    expect(scanPlanFor(spec({ storage_platform: 'qoder', id: 'qoder', realm: 'cn' }))).toMatchObject({
      kind: 'scan-only',
      prefix: '/api/qoder',
      realm: 'cn',
    })
    expect(scanPlanFor(spec({ storage_platform: 'loomy', id: 'loomy', realm: '' }))).toMatchObject({
      kind: 'scan-then-import',
      prefix: '/api/loomy',
    })
    expect(scanPlanFor(null)).toBeNull()
  })
})

describe('导入全部平台：文件解析与请求体', () => {
  it('解析「导出全部平台」文件，多文件合并；坏文件记 note 后跳过', () => {
    const good = JSON.stringify({
      format: 'miraihub-all-platforms',
      version: 1,
      platforms: {
        workbuddy: { label: 'WorkBuddy', count: 2, accounts: [{ name: 'A' }, { name: 'B' }] },
        trae: { label: 'Trae', count: 1, accounts: [{ name: 'C' }] },
      },
    })
    const parsed = parsePlatformBackup([
      { name: 'a.json', text: '\uFEFF' + good },
      { name: 'b.json', text: 'not json' },
      { name: 'c.json', text: JSON.stringify({ format: 'other' }) },
      { name: 'd.json', text: '' },
    ])
    expect(parsed.byPlatform.workbuddy).toHaveLength(2)
    expect(parsed.byPlatform.trae).toHaveLength(1)
    expect(parsed.notes).toHaveLength(3)
    expect(parsed.notes[0]).toContain('b.json')
    expect(parsed.notes[1]).toContain('缺 platforms 字段')
    expect(parsed.notes[2]).toContain('空文件')
  })

  it('workbuddy 走 items(auth_json)，其它平台走 JSONL 原文', () => {
    const accounts = [{ name: 'A', token: 'x' }, { name: 'B' }]
    const wb = importPayloadFor('workbuddy', accounts)
    expect(wb).toMatchObject({ storage: 'workbuddy', verify: true })
    expect((wb.items as Array<{ auth_json: string }>)[0].auth_json).toContain('"token":"x"')

    const loomy = importPayloadFor('loomy', accounts)
    expect(loomy).toMatchObject({ storage: 'loomy' })
    expect(String(loomy.text).split('\n')).toHaveLength(2)
  })
})

describe('导出文件名', () => {
  it('miraihub-<scope>-<mode>-<时间戳>.json', () => {
    const now = new Date(2026, 10, 23, 12, 0, 0)
    expect(exportFilename('backup', 'all-platforms', now)).toBe('miraihub-all-platforms-backup-20261123-120000.json')
    expect(exportFilename('plain', 'trae', now)).toBe('miraihub-trae-plain-20261123-120000.json')
  })
})
