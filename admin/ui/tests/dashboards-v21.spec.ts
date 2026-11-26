/**
 * F2 看板类页面 v2.1 新增纯函数（fixlog_frontend_F2.md §1）：
 * 图表默认样式（平滑 + 面积渐变）、线路带行高、数据看板 KPI / 分轴 / 耗时兜底、
 * 使用记录 KPI / 口径、哨兵 KPI / 状态行、广场平台悬停说明。
 */
import { describe, expect, it } from 'vitest'
import { applyBarDefaults, applyLineDefaults, seriesColor } from '@/components/charts/options'
import { RIBBON_LANE_HEIGHT, RIBBON_LANE_MIN_HEIGHT, ribbonLaneHeight } from '@/components/charts/routeRibbon'
import { areaGradient } from '@/theme/echartsTheme'
import {
  avgDurationText,
  durationState,
  needsDualAxis,
  requestTrendOption,
  toStatsKpis,
  type StatsDashboardPayload,
} from '@/api/stats'
import {
  needsDualAxis as usageNeedsDualAxis,
  toUsageKpis,
  usageScopeLabel,
  type UsageOptionsPayload,
  type UsageTrendPoint,
} from '@/api/usage'
import { toSentinelKpis, toStatusLine, type SentinelStatus } from '@/api/sentinel'
import { platformTitles, type MarketPricingEntry } from '@/api/market'

type Series = Record<string, unknown>

function seriesOf(option: Record<string, unknown>): Series[] {
  return option.series as Series[]
}

describe('applyLineDefaults：平滑 + 面积渐变（10-visual-refresh §1.8）', () => {
  it('默认 smooth、不画点，面积用同色 areaGradient', () => {
    const option = applyLineDefaults({ series: [{ name: 'a', data: [1, 2], color: '#3148C8' }] })
    const [line] = seriesOf(option)
    expect(line.smooth).toBe(true)
    expect(line.showSymbol).toBe(false)
    expect(line.areaStyle).toEqual({ color: areaGradient('#3148C8'), opacity: 1 })
  })

  it('areaStyle: null 保留不画面积；显式 areaStyle 覆盖默认值但仍带渐变', () => {
    const option = applyLineDefaults({
      series: [
        { name: 'a', data: [1], areaStyle: null },
        { name: 'b', data: [1], itemStyle: { color: '#FF0000' }, areaStyle: { opacity: 0.5 } },
      ],
    })
    const [a, b] = seriesOf(option)
    expect(a.areaStyle).toBeNull()
    expect(b.areaStyle).toEqual({ color: areaGradient('#FF0000'), opacity: 0.5 })
  })

  it('seriesColor：color > itemStyle.color > 按序号取 --chart-n', () => {
    expect(seriesColor({ color: '#111111', itemStyle: { color: '#222222' } }, 0)).toBe('#111111')
    expect(seriesColor({ itemStyle: { color: '#222222' } }, 0)).toBe('#222222')
    expect(typeof seriesColor({}, 2)).toBe('string')
  })

  it('混合图里的折线也平滑；柱子圆角顶', () => {
    const option = applyBarDefaults({ series: [{ type: 'bar', data: [1] }, { type: 'line', data: [1] }] })
    const [bar, line] = seriesOf(option)
    expect(bar.itemStyle).toEqual({ borderRadius: [3, 3, 0, 0] })
    expect(line.smooth).toBe(true)
  })
})

describe('ribbonLaneHeight：线路带行高 22–36px 自适应', () => {
  it('不给 maxHeight 时固定 36', () => {
    expect(ribbonLaneHeight(3)).toBe(RIBBON_LANE_HEIGHT)
    expect(ribbonLaneHeight(3, null)).toBe(RIBBON_LANE_HEIGHT)
    expect(ribbonLaneHeight(3, 0)).toBe(RIBBON_LANE_HEIGHT)
  })

  it('按线路数压缩，上限 36、下限 22；0 条按 1 条算', () => {
    expect(ribbonLaneHeight(3, 300)).toBe(36)
    expect(ribbonLaneHeight(10, 300)).toBe(30)
    expect(ribbonLaneHeight(20, 200)).toBe(RIBBON_LANE_MIN_HEIGHT)
    expect(ribbonLaneHeight(0, 100)).toBe(36)
  })
})

describe('needsDualAxis：量级差 ≥8 倍才分轴', () => {
  it('stats / usage 两份实现同口径', () => {
    for (const fn of [needsDualAxis, usageNeedsDualAxis]) {
      expect(fn([1000, 2000], [300, 600])).toBe(false)
      expect(fn([8000], [1000])).toBe(true)
      expect(fn([100], [100000])).toBe(true)
      // 任一组全 0 不分轴
      expect(fn([0, 0], [5])).toBe(false)
      expect(fn([], [])).toBe(false)
      expect(fn([1000], [300], 3)).toBe(true)
    }
  })
})

describe('平均耗时兜底（第 3 轮审查 C）', () => {
  it('durationState：0 / 负数 / 非数 = none；>1 小时 = anomaly', () => {
    expect(durationState(0)).toBe('none')
    expect(durationState(-5)).toBe('none')
    expect(durationState(null)).toBe('none')
    expect(durationState('x')).toBe('none')
    expect(durationState(450)).toBe('ok')
    expect(durationState(3_600_000)).toBe('ok')
    expect(durationState(3_600_001)).toBe('anomaly')
  })

  it('avgDurationText：无样本 —，异常显示「数据异常」', () => {
    expect(avgDurationText(0)).toBe('—')
    expect(avgDurationText(450_000_000)).toBe('数据异常')
  })
})

function dashboard(summary: Partial<StatsDashboardPayload['summary']> = {}, totals: number[] = []): StatsDashboardPayload {
  return {
    window: { days: 1, granularity: 'hour', start: '', end: '', minutes: 1440 },
    filters: { platform: '', model: '' },
    platform_label: '',
    summary: {
      requests: 10,
      successes: 9,
      failures: 1,
      success_rate: 0.9,
      credits: 0,
      total_tokens: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      cached_tokens: 0,
      cache_hit_rate: null,
      rpm: 0,
      tpm: 0,
      avg_latency_ms: 450,
      avg_ttfb_ms: 0,
      throughput_tps: 0,
      window_minutes: 1440,
      ...summary,
    },
    models: [],
    platforms: [],
    distribution: { buckets: [], series: [] },
    trend: { buckets: totals.map(String), total_requests: totals, series: [] },
    ranking: [],
    errors: [],
  }
}

describe('toStatsKpis：数据看板 KPI 行', () => {
  it('5 张卡，key 固定；无数据时 — 且不画折线，成功率语气 neutral', () => {
    const kpis = toStatsKpis(null, null)
    expect(kpis.map((item) => item.key)).toEqual(['requests', 'success', 'tokens', 'credits', 'latency'])
    expect(kpis.every((item) => item.value === '—')).toBe(true)
    expect(kpis.every((item) => item.spark === undefined)).toBe(true)
    expect(kpis[1].tone).toBe('neutral')
    expect(kpis[0].tone).toBe('brand')
  })

  it('请求卡带趋势折线（≥2 个桶）；耗时异常时语气 warn 并换说明', () => {
    const kpis = toStatsKpis(dashboard({ avg_latency_ms: 9_000_000 }, [3, 5]), null)
    expect(kpis[0].spark).toEqual([3, 5])
    expect(kpis[4].tone).toBe('warn')
    expect(kpis[4].hint).toBe('平均耗时超过 1 小时，疑似汇总表数据异常')
    expect(kpis[4].value).toBe('数据异常')
  })
})

describe('requestTrendOption：单模型去重', () => {
  it('只有一个模型且与全部请求重合时不再画模型线', () => {
    const payload = dashboard({}, [3, 5])
    payload.trend.series = [{ name: 'GLM-5.3', requests: [3, 5], tokens: [0, 0], credits: [0, 0] }]
    const option = requestTrendOption(payload) as { series: Array<{ name: string }> }
    expect(option.series.map((item) => item.name)).toEqual(['全部请求'])
  })
})

describe('toUsageKpis / usageScopeLabel', () => {
  const trend: UsageTrendPoint[] = [
    { bucket: '2026-11-23 10:00', prompt_tokens: 10, completion_tokens: 1, cached_tokens: 0, credits: 0.5, requests: 2 },
    { bucket: '2026-11-23 11:00', prompt_tokens: 20, completion_tokens: 2, cached_tokens: 0, credits: 1, requests: 3 },
  ]

  it('6 张卡；请求 / 输入 / 输出 / 积分带折线，成功 / 失败不带', () => {
    const kpis = toUsageKpis(null, trend)
    expect(kpis.map((item) => item.key)).toEqual(['requests', 'success', 'failed', 'tokenIn', 'tokenOut', 'credits'])
    expect(kpis[0].spark).toEqual([2, 3])
    expect(kpis[3].spark).toEqual([10, 20])
    expect(kpis[4].spark).toEqual([1, 2])
    expect(kpis[5].spark).toEqual([0.5, 1])
    expect(kpis[1].spark).toBeUndefined()
    expect(kpis[2].spark).toBeUndefined()
    const tones = new Set(['brand', 'ok', 'warn', 'danger', 'neutral'])
    expect(kpis.every((item) => tones.has(String(item.tone)))).toBe(true)
  })

  it('只有 1 个桶时不画折线', () => {
    expect(toUsageKpis(null, trend.slice(0, 1)).every((item) => item.spark === undefined)).toBe(true)
    expect(toUsageKpis(null, null)).toHaveLength(6)
  })

  it('口径：没选平台 = 全网关；选了用选项里的名称，查不到原样', () => {
    expect(usageScopeLabel({ platform: '' }, null)).toBe('全网关')
    const options: UsageOptionsPayload = {
      models: [],
      use_cases: [],
      keys: [],
      accounts: [],
      platforms: [{ value: 'qoder', label: 'Qoder 中国版', requests: 1 }],
    }
    expect(usageScopeLabel({ platform: 'qoder' }, options)).toBe('Qoder 中国版')
    expect(usageScopeLabel({ platform: 'trae' }, options)).toBe('trae')
  })
})

function sentinel(overrides: Partial<SentinelStatus> = {}): SentinelStatus {
  return {
    tripped: false,
    accounts_affected: 0,
    accounts_total: 0,
    accounts_risk: 0,
    threshold: 3,
    window_seconds: 300,
    suggested_pause_seconds: 120,
    total_events: 0,
    by_code: {},
    by_signal: {},
    signal_labels: {},
    by_severity: {},
    by_platform: [],
    accounts: [],
    recent: [],
    redis: false,
    pause: {},
    now: 0,
    ...overrides,
  }
}

describe('toSentinelKpis / toStatusLine', () => {
  it('4 张卡：今日请求 / 成功率 / 失败超时 / 信号；首张固定 brand', () => {
    const kpis = toSentinelKpis(null, null)
    expect(kpis.map((item) => item.key)).toEqual(['today', 'rate', 'failed', 'signals'])
    expect(kpis[0].label).toBe('今日请求')
    expect(kpis[0].tone).toBe('brand')
    expect(kpis[1].value).toBe('—')
    expect(kpis[1].tone).toBe('neutral')
    expect(kpis[2].tone).toBe('ok')
    expect(kpis[3].tone).toBe('neutral')
  })

  it('常态 ok：涉及账号 0 · 原因 0 种 · 全部平台在跑', () => {
    const line = toStatusLine(sentinel(), null)
    expect(line.tone).toBe('ok')
    expect(line.facts[0]).toBe('0 个账号被风控')
    expect(line.facts[1]).toBe('原因 0 种')
  })

  it('有风控账号或暂停 → warn；批次封控 → danger，并列出暂停中的平台', () => {
    expect(toStatusLine(sentinel({ accounts_affected: 2 }), null).tone).toBe('warn')
    const line = toStatusLine(
      sentinel({ tripped: true, accounts_affected: 3, by_code: { '403': 2, '11140': 1 }, pause: { trae: { until: 0 } } }),
      null,
    )
    expect(line.tone).toBe('danger')
    expect(line.facts).toEqual(['3 个账号被风控', '原因 2 种', '暂停中：trae'])
  })
})

describe('platformTitles：多区平台悬停说明', () => {
  const entry = (platform: string, label: string): MarketPricingEntry => ({
    platform,
    platform_label: label,
    model_id: 'm',
    multiplier: 1,
    free: false,
  })

  it('同平台各区名称去重后用 · 连接；没有标签时用 id', () => {
    const titles = platformTitles(
      ['trae', 'loomy'],
      ['Trae', ''],
      [entry('trae', 'Trae 国内版'), entry('trae', 'Trae 国际版'), entry('trae', 'Trae'), entry('qoder', 'Qoder')],
    )
    expect(titles).toEqual(['Trae · Trae 国内版 · Trae 国际版', 'loomy'])
  })
})
