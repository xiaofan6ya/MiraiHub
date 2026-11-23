import { describe, expect, it } from 'vitest'
import {
  lastSignalText,
  pausedKeys,
  rateTone,
  requestTone,
  severityMeta,
  storageText,
  toAccountRows,
  toBanner,
  toLiveRows,
  toPlatformHealth,
  toRibbon,
  toSignalRows,
  toStats,
  type SentinelEvent,
  type SentinelStatus,
  type TrafficPayload,
} from '@/api/sentinel'
import { FMT_EMPTY } from '@/utils/format'

/** 真实响应形状：admin/sentinel.py:status() + sentinel_router.status。 */
function status(overrides: Partial<SentinelStatus> = {}): SentinelStatus {
  return {
    tripped: true,
    accounts_affected: 3,
    accounts_total: 5,
    accounts_risk: 3,
    threshold: 3,
    window_seconds: 300,
    suggested_pause_seconds: 120,
    total_events: 7,
    by_code: { '11140': 4, '403': 3 },
    by_signal: { risk_control: 7 },
    signal_labels: { risk_control: '账号/设备风控' },
    by_severity: { risk: 7, throttle: 0, auth: 0, info: 0 },
    by_platform: [{ platform: 'workbuddy', count: 7, risk: 7, last_ts: 1_800_000_000 }],
    accounts: [
      {
        account_id: 152, count: 3, first_ts: 1_800_000_000, last_ts: 1_800_000_600,
        kind: 'account_fault', code: '11140', label: '账号级授权风控',
        signal: 'risk_control', signal_label: '账号/设备风控', severity: 'risk',
        detail: 'upstream 403', platform: 'workbuddy',
      },
    ],
    recent: [
      {
        ts: 1_800_000_600, account_id: 152, kind: 'account_fault', code: '11140',
        label: '账号级授权风控（11140）', signal: 'risk_control', signal_label: '账号/设备风控',
        severity: 'risk', unmapped: false, detail: 'upstream 403', model: 'deepseek-v4.1-flash',
        platform: 'workbuddy',
      },
      {
        ts: 1_800_000_300, account_id: 0, kind: 'waf', code: '103',
        label: '103 · waf', signal: 'waf', signal_label: '出口 IP 被拦',
        severity: 'risk', unmapped: true, detail: '', model: '', platform: 'loomy',
      },
    ],
    redis: true,
    pause: { workbuddy: { until: Math.floor(Date.now() / 1000) + 600, reason: '手动止损' } },
    now: 1_800_000_700,
    ...overrides,
  }
}

/** 真实响应形状：admin/traffic_stats.py:snapshot()。 */
function traffic(overrides: Partial<TrafficPayload> = {}): TrafficPayload {
  return {
    day: '2026-11-23',
    window_seconds: 3600,
    platforms: [
      { platform: 'workbuddy', label: 'WorkBuddy', requests: 1000, ok: 306, error: 600, timeout: 94, interrupted: 0, last_at: 1_800_000_000, last_status: 200 },
      { platform: 'loomy', label: 'Loomy 国内版', requests: 0, ok: 0, error: 0, timeout: 0, interrupted: 0, last_at: 0, last_status: 0 },
      { platform: 'qoder', label: 'Qoder', requests: 250, ok: 250, error: 0, timeout: 0, interrupted: 0, last_at: 1_800_000_000, last_status: 200 },
    ],
    totals: { requests: 1250, ok: 556, error: 600, timeout: 94, interrupted: 0 },
    recent: [
      { rid: 'r1', ts: 1_800_000_700, model: 'deepseek-v4.1-flash', platform: 'workbuddy', status: 500, outcome: 'completed', error: 'upstream 500' },
      { rid: 'r2', ts: 1_800_000_690, model: 'glm-5.3-flash', platform: 'workbuddy', status: 429, outcome: 'completed' },
      { rid: 'r3', ts: 1_800_000_680, model: '', path: '/v1/chat/completions', platform: 'qoder', status: 200, outcome: 'interrupted' },
      { rid: 'r4', ts: 1_800_000_670, model: 'kling-video-v3', platform: 'loomy', status: null, outcome: 'running' },
      { rid: 'r5', ts: 1_800_000_660, model: 'x', platform: 'workbuddy', status: null, outcome: 'timeout' },
    ],
    running: 1,
    storage: 'redis',
    now: 1_800_000_800,
    ...overrides,
  }
}

describe('结论横幅与 6 格统计', () => {
  it('批次封控：danger 横幅 + 建议暂停的平台与秒数', () => {
    const banner = toBanner(status(), traffic())
    expect(banner.tripped).toBe(true)
    expect(banner.tone).toBe('danger')
    expect(banner.title).toContain('3 个账号被风控')
    expect(banner.detail).toContain('阈值 3 个账号 / 5 分钟')
    expect(banner.detail).toContain('建议暂停 120 秒')
    expect(banner.platform).toBe('workbuddy')
    expect(banner.platformLabel).toBe('WorkBuddy')
  })

  it('未触发：ok 横幅，没有建议平台时为空串', () => {
    const banner = toBanner(status({ tripped: false, by_platform: [] }), traffic())
    expect(banner.tone).toBe('ok')
    expect(banner.title).toBe('未检测到批次级封控')
    expect(banner.platform).toBe('')
    expect(banner.platformLabel).toBe('')
  })

  it('6 格：今日请求 / 成功率 / 失败超时 / 信号 / 涉及账号 / 暂停中的平台', () => {
    const stats = toStats(status(), traffic())
    expect(stats.map((item) => item.label)).toEqual([
      '今日请求', '成功率', '失败 / 超时', '信号', '涉及账号', '暂停中的平台',
    ])
    expect(stats[0].value).toBe('1,250')
    expect(stats[0].foot).toBe('2 / 3 个平台有流量')
    expect(stats[1].value).toBe('44.5%')
    expect(stats[1].tone).toBe('danger')
    expect(stats[2].value).toBe('694')
    expect(stats[2].tone).toBe('danger')
    expect(stats[2].foot).toContain('WorkBuddy')
    expect(stats[3].value).toBe('7')
    expect(stats[4].tone).toBe('warn')
    expect(stats[5].value).toBe('1')
    expect(stats[5].foot).toBe('WorkBuddy')
  })

  it('无流量时成功率显示 —，暂停过期项不计入', () => {
    const empty = traffic({ platforms: [], totals: { requests: 0, ok: 0, error: 0, timeout: 0, interrupted: 0 } })
    const stats = toStats(
      status({ total_events: 0, accounts_affected: 0, pause: { workbuddy: { until: 1, reason: 'x' } } }),
      empty,
    )
    expect(stats[1].value).toBe(FMT_EMPTY)
    expect(stats[3].value).toBe('0')
    expect(stats[5].value).toBe('0')
    expect(stats[5].foot).toBe('全部平台在跑')
  })
})

describe('线路带与平台健康', () => {
  it('线路带只含有流量的平台，成功率按 0–100 归一', () => {
    const ribbon = toRibbon(traffic())
    expect(ribbon.source).toEqual({ label: '客户端请求', total: 1250 })
    expect(ribbon.lanes.map((lane) => lane.id)).toEqual(['workbuddy', 'qoder'])
    expect(ribbon.lanes[0].successRate).toBeCloseTo(30.6, 1)
    expect(ribbon.lanes[1].successRate).toBe(100)
    expect(ribbon.lanes[0].status).toBe('HTTP 200')
  })

  it('平台健康：成功 / 失败·超时两段 + 成功率着色', () => {
    const rows = toPlatformHealth(traffic())
    expect(rows.map((row) => row.platform)).toEqual(['workbuddy', 'qoder'])
    expect(rows[0].failed).toBe(694)
    expect(rows[0].rateText).toBe('30.6%')
    expect(rows[0].tone).toBe('danger')
    expect(rows[0].items).toEqual([
      { label: '成功', value: 306, tone: 'ok' },
      { label: '失败 / 超时', value: 694, tone: 'danger' },
    ])
    expect(rows[1].tone).toBe('ok')
  })

  it('成功率着色分界：≥98 ok / ≥90 warn / 其余 danger', () => {
    expect(rateTone(98)).toBe('ok')
    expect(rateTone(97.9)).toBe('warn')
    expect(rateTone(90)).toBe('warn')
    expect(rateTone(89.9)).toBe('danger')
    expect(rateTone(null)).toBe('warn')
  })
})

describe('实时请求列表', () => {
  it('状态码优先：500 danger / 429 warn / 200 流式收尾也算成功', () => {
    expect(requestTone({ status: 500 })).toBe('danger')
    expect(requestTone({ status: 429 })).toBe('warn')
    expect(requestTone({ status: 200, outcome: 'interrupted' })).toBe('ok')
    expect(requestTone({ outcome: 'running' })).toBe('brand')
    expect(requestTone({ status: null, outcome: 'timeout' })).toBe('danger')
    expect(requestTone({ status: null, outcome: 'completed', error: 'ECONNRESET' })).toBe('danger')
    expect(requestTone({ status: null, outcome: 'completed' })).toBe('ok')
  })

  it('最多 50 行，平台名换成展示名，模型缺失时退回 path', () => {
    const rows = toLiveRows(traffic())
    expect(rows).toHaveLength(5)
    expect(rows[0].statusText).toBe('500')
    expect(rows[0].tone).toBe('danger')
    expect(rows[1].statusText).toBe('429')
    expect(rows[2].model).toBe('/v1/chat/completions')
    expect(rows[2].tone).toBe('ok')
    expect(rows[3].statusText).toBe('进行中')
    expect(rows[3].tone).toBe('brand')
    expect(rows[3].platformLabel).toBe('Loomy 国内版')
    expect(rows[4].statusText).toBe('超时')
    expect(rows[4].tone).toBe('danger')

    const many = traffic({ recent: Array.from({ length: 80 }, (_, index) => ({ rid: 'x' + index, ts: 1_800_000_000 - index, status: 200 })) })
    expect(toLiveRows(many, 50)).toHaveLength(50)
  })
})

describe('封控信号与受影响账号', () => {
  it('信号行：时间 · 平台 · 账号 · 码 · 原因 · 严重程度', () => {
    const rows = toSignalRows(status(), traffic())
    expect(rows).toHaveLength(2)
    expect(rows[0].platformLabel).toBe('WorkBuddy')
    expect(rows[0].accountId).toBe(152)
    expect(rows[0].code).toBe('11140')
    expect(rows[0].severityLabel).toBe('风控')
    expect(rows[0].severityTone).toBe('danger')
    expect(rows[0].signalLabel).toBe('账号/设备风控')
    expect(rows[0].time).not.toBe('')
    expect(rows[1].unmapped).toBe(true)
    expect(rows[1].platformLabel).toBe('Loomy 国内版')
  })

  it('账号行：最近信号、类型、次数、首次/最近', () => {
    const rows = toAccountRows(status(), traffic())
    expect(rows).toHaveLength(1)
    expect(rows[0].accountId).toBe(152)
    expect(rows[0].count).toBe(3)
    expect(rows[0].signalLabel).toBe('账号/设备风控')
    expect(rows[0].severityTone).toBe('danger')
    expect(rows[0].firstTime).not.toBe('')
  })

  it('严重程度兜底与存储文案', () => {
    expect(severityMeta('risk').label).toBe('风控')
    expect(severityMeta('unknown').label).toBe('记录')
    expect(storageText(status(), traffic())).toBe('Redis')
    expect(storageText(status({ redis: false }), null)).toBe('内存')
    expect(storageText(status({ redis: false }), traffic({ storage: 'redis' }))).toBe('Redis')
  })

  it('最近信号相对时间与暂停键过滤', () => {
    const nowMs = 1_800_001_000_000
    expect(lastSignalText([{ last_ts: nowMs / 1000 - 120 } as never], nowMs)).toBe('2 分钟前')
    expect(lastSignalText([], nowMs)).toBe(FMT_EMPTY)
    const future = Math.floor(Date.now() / 1000) + 60
    expect(pausedKeys(status({ pause: { workbuddy: { until: future }, all: { until: 0 }, old: { until: 1 } } }))).toEqual([
      'workbuddy', 'all',
    ])
  })
})