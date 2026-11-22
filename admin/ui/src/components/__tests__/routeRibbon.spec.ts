import { describe, expect, it } from 'vitest'
import {
  RIBBON_MIN_WIDTH,
  healthColor,
  healthOf,
  healthTone,
  isEmptyRibbon,
  laneRatio,
  lineWidth,
  normalizeRate,
  pulseDuration,
  pulseSeconds,
} from '../charts/routeRibbon'

describe('RouteRibbon 线宽纯函数', () => {
  it('线宽 = 2 + 18 × 占比', () => {
    expect(lineWidth(0, 100)).toBe(RIBBON_MIN_WIDTH)
    expect(lineWidth(50, 100)).toBe(2 + 9)
    expect(lineWidth(100, 100)).toBe(2 + 18)
  })

  it('占比超过 1 时封顶，避免溢出', () => {
    expect(lineWidth(300, 100)).toBe(20)
  })

  it('总量为 0 / 负数 / NaN 时退回最细线', () => {
    expect(lineWidth(10, 0)).toBe(2)
    expect(lineWidth(-5, 100)).toBe(2)
    expect(lineWidth(Number.NaN, 100)).toBe(2)
  })

  it('laneRatio 给出 0–1 的占比', () => {
    expect(laneRatio(25, 100)).toBe(0.25)
    expect(laneRatio(0, 100)).toBe(0)
    expect(laneRatio(200, 100)).toBe(1)
  })
})

describe('RouteRibbon 健康度', () => {
  it('≥90 健康、60–90 警告、<60 危险、禁用走灰', () => {
    expect(healthOf({ successRate: 96 })).toBe('ok')
    expect(healthOf({ successRate: 90 })).toBe('ok')
    expect(healthOf({ successRate: 75 })).toBe('warn')
    expect(healthOf({ successRate: 59.9 })).toBe('danger')
    expect(healthOf({ successRate: 20, disabled: true })).toBe('disabled')
    expect(healthOf({ successRate: null })).toBe('disabled')
  })

  it('成功率 0–1 与 0–100 两种写法都认', () => {
    expect(normalizeRate(0.9)).toBe(90)
    expect(normalizeRate(90)).toBe(90)
    expect(normalizeRate(null)).toBeNull()
    expect(normalizeRate(Number.NaN)).toBeNull()
  })

  it('颜色与徽章语气映射固定', () => {
    expect(healthColor('ok')).toBe('var(--ok)')
    expect(healthColor('warn')).toBe('var(--warn)')
    expect(healthColor('danger')).toBe('var(--danger)')
    expect(healthColor('disabled')).toBe('var(--fg-faint)')
    expect(healthTone('ok')).toBe('ok')
    expect(healthTone('disabled')).toBe('neutral')
  })
})

describe('RouteRibbon 流量脉冲', () => {
  it('周期落在 clamp(1.2s, 6s/log(每分钟), 6s)', () => {
    expect(pulseSeconds(0)).toBe(0)
    expect(pulseSeconds(100)).toBeCloseTo(6 / Math.log(100), 5)
    expect(pulseSeconds(1_000_000)).toBe(1.2)
    expect(pulseSeconds(2)).toBe(6)
  })

  it('dur 字符串带 s，无流量为 0s', () => {
    expect(pulseDuration(100)).toMatch(/s$/)
    expect(pulseDuration(0)).toBe('0s')
  })
})

describe('RouteRibbon 空数据', () => {
  it('总量或所有线路为 0 时判定为空', () => {
    expect(isEmptyRibbon(0, [{ count: 0 }])).toBe(true)
    expect(isEmptyRibbon(100, [{ count: 0 }, { count: 0 }])).toBe(true)
    expect(isEmptyRibbon(100, [{ count: 100 }])).toBe(false)
  })
})
