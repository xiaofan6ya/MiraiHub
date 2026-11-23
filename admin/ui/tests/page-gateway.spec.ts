/**
 * S19 平台网关：接口适配 / 策略与预演纯函数单测。
 * 响应形状按 admin/platform/gateway_api.py、gateway_router.py、bench_router.py 的真实返回构造。
 */
import { describe, expect, it } from 'vitest'
import {
  distributeByWeight,
  formatBalance,
  priorityOf,
  priorityPayload,
  simulateRouting,
  simulationText,
  speedPayloadText,
  speedTone,
  strategyDescription,
  strategyOf,
  toSpeedRows,
  type GatewayConfig,
  type GatewayOrderItem,
  type PlatformSpeedPayload,
} from '@/api/gateway'

const CONFIG: GatewayConfig = {
  strategy: 'priority',
  priority: { workbuddy: 10, loomy: 20 },
  disabled: {},
  spread_unbound: false,
  strategies: [
    { value: 'priority', label: '优先级', description: '按手动设置的优先级排序，数字小的平台先用。适合「主力平台 + 兜底平台」。' },
    { value: 'balance', label: '额度优先', description: '剩余额度多的平台先用，避免某一个平台先被耗干。' },
    { value: 'expiring', label: '到期优先', description: '快到期/快结束的平台先用 —— 先把即将浪费的额度用掉。' },
    { value: 'throughput', label: '吞吐优先', description: '当前在途请求少的平台先用，降低排队延迟。' },
    { value: 'round_robin', label: '轮转', description: '各平台平均分摊，不偏向任何一个。' },
    { value: 'random', label: '随机', description: '随机选择，最简单也最均匀。' },
  ],
  platforms: [
    { id: 'workbuddy', label: 'WorkBuddy', managed: true },
    { id: 'loomy', label: 'Loomy', managed: true },
  ],
}

/** GET /api/gateway/order 的 items[]（health + available + reason）。 */
function orderItem(platform: string, label: string, usable: number, extra: Partial<GatewayOrderItem> = {}): GatewayOrderItem {
  return {
    platform,
    label,
    ok: usable > 0,
    accounts: usable + 1,
    active: usable,
    cooling: 0,
    usable,
    balance_remain: usable * 4000,
    balance_total: usable * 5000,
    balance_ratio: 0.8,
    soonest_expire_at: null,
    disabled: false,
    available: usable > 0,
    reason: '优先级 ' + (platform === 'workbuddy' ? 10 : 100),
    ...extra,
  }
}

const ORDER: GatewayOrderItem[] = [
  orderItem('workbuddy', 'WorkBuddy', 3),
  orderItem('loomy', 'Loomy', 1),
  orderItem('qoder', 'Qoder', 0, { available: false, reason: '无可用账号' }),
  orderItem('trae', 'Trae', 2, { disabled: true, available: false, reason: '已禁用' }),
]

describe('策略与优先级', () => {
  it('strategyOf / strategyDescription 按后端返回的策略列表查', () => {
    expect(strategyOf(CONFIG, 'random')?.label).toBe('随机')
    expect(strategyOf(CONFIG)?.value).toBe('priority')
    expect(strategyDescription(CONFIG, 'balance')).toContain('剩余额度多')
    expect(strategyOf(CONFIG, 'nope')).toBeNull()
    expect(strategyOf(null, 'priority')).toBeNull()
  })

  it('priorityOf：显式值优先，未设置按 100（与 gateway_router 一致）', () => {
    expect(priorityOf(CONFIG, 'workbuddy')).toBe(10)
    expect(priorityOf(CONFIG, 'qoder')).toBe(100)
    expect(priorityOf(CONFIG, 'qoder', 1000)).toBe(1000)
  })

  it('priorityPayload：列表顺序 → 10/20/30…（整表重排）', () => {
    expect(priorityPayload([{ platform: 'loomy' }, { platform: 'workbuddy' }, { platform: 'trae' }])).toEqual({
      loomy: 10,
      workbuddy: 20,
      trae: 30,
    })
  })
})

describe('调度预演', () => {
  it('distributeByWeight 用最大余额法，和恰好等于总数', () => {
    expect(distributeByWeight([3, 1], 100)).toEqual([75, 25])
    expect(distributeByWeight([1, 1, 1], 7)).toEqual([3, 2, 2])
    expect(distributeByWeight([1, 1, 1], 100).reduce((a, b) => a + b, 0)).toBe(100)
    expect(distributeByWeight([0, 0], 100)).toEqual([0, 0])
    expect(distributeByWeight([], 100)).toEqual([])
  })

  it('priority/balance 等按可用账号数加权；不可用与已禁用平台不参与', () => {
    const lanes = simulateRouting(ORDER, 'priority', 100)
    expect(lanes.map((lane) => lane.platform)).toEqual(['workbuddy', 'loomy'])
    expect(lanes.map((lane) => lane.count)).toEqual([75, 25])
  })

  it('round_robin 平均分摊，余数给靠前的平台', () => {
    expect(simulateRouting(ORDER, 'round_robin', 100).map((lane) => lane.count)).toEqual([50, 50])
    expect(simulateRouting(ORDER, 'round_robin', 7).map((lane) => lane.count)).toEqual([4, 3])
  })

  it('random 可注入 rng，便于确定性断言', () => {
    expect(simulateRouting(ORDER, 'random', 100, () => 0).map((lane) => lane.count)).toEqual([100, 0])
    expect(simulateRouting(ORDER, 'random', 100, () => 0.9999).map((lane) => lane.count)).toEqual([0, 100])
  })

  it('没有可调度平台时返回空', () => {
    expect(simulateRouting([], 'priority', 100)).toEqual([])
    expect(simulateRouting([ORDER[2], ORDER[3]], 'priority', 100)).toEqual([])
  })

  it('simulationText：100 个请求中：WorkBuddy 62 · Loomy 38', () => {
    expect(
      simulationText(
        [
          { platform: 'workbuddy', label: 'WorkBuddy', count: 62, usable: 3, disabled: false },
          { platform: 'loomy', label: 'Loomy', count: 38, usable: 1, disabled: false },
        ],
        100,
      ),
    ).toBe('100 个请求中：WorkBuddy 62 · Loomy 38')
    expect(simulationText([], 100)).toBe('没有可调度的平台')
  })
})

describe('平台速度表（/api/platform/speed?hours=24）', () => {
  const payload: PlatformSpeedPayload = {
    hours: 24,
    model: '',
    items: [
      {
        platform: 'workbuddy', label: 'WorkBuddy', requests: 1786, ok_requests: 1780, success_rate: 99.2,
        ttfb_avg: 1204, ttfb_p50: 1100, latency_avg: 3950, latency_p50: 3800, latency_p95: 9200,
        tps_avg: 42.4, tps_p50: 40.1, jitter: 120, samples: 1780, low_sample: false, score: 4.1, rank: 1,
      },
      {
        platform: 'loomy', label: 'Loomy', requests: 210, ok_requests: 190, success_rate: 90.5,
        ttfb_avg: 0, ttfb_p50: 0, latency_avg: 0, latency_p50: 0, latency_p95: 0,
        tps_avg: 0, tps_p50: 0, jitter: 0, samples: 0, low_sample: true, score: 9.9, rank: 2,
      },
      {
        platform: 'qoder', label: 'Qoder', requests: 50, ok_requests: 40, success_rate: 80,
        ttfb_avg: 2100, latency_avg: 5000, tps_avg: 12.5, samples: 40, low_sample: false, rank: 3,
      },
    ],
    fastest: 'workbuddy',
    steadiest: 'workbuddy',
    observed_at: '2026-03-10T04:00:00Z',
  }

  it('成功率着色：≥98 ok / ≥90 warn / 其它 danger', () => {
    expect(speedTone(99.2)).toBe('ok')
    expect(speedTone(90.5)).toBe('warn')
    expect(speedTone(80)).toBe('danger')
  })

  it('文本格式化：均值 ms / tok/s / 总耗时；没有样本显示 —', () => {
    const rows = toSpeedRows(payload.items)
    expect(rows[0]).toMatchObject({
      successText: '99.2%', ttfbText: '1204ms', tpsText: '42 tok/s', latencyText: '3.95s', tone: 'ok',
    })
    expect(rows[1]).toMatchObject({
      successText: '90.5%', ttfbText: '—', tpsText: '—', latencyText: '—', tone: 'warn',
    })
    expect(rows[2].tone).toBe('danger')
    expect(rows[2].successText).toBe('80%')
  })

  it('表头提示：近 24 小时 · N 个平台有样本', () => {
    expect(speedPayloadText(payload)).toBe('近 24 小时 · 3 个平台有样本')
    expect(speedPayloadText(null)).toBe('')
  })

  it('额度用千分位（不缩写）', () => {
    expect(formatBalance(12954)).toBe('12,954')
    expect(formatBalance(null)).toBe('—')
  })
})
