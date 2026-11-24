/**
 * S17 数据看板：接口适配 / 分桶 / 图 option / 排行条 的单测。
 *
 * 数据形状照抄后端真实响应：
 *   · GET /api/stats/dashboard → admin/routers/stats.py:_dashboard_from_rollup（1262 行起）
 *   · GET /api/stats/usage     → admin/routers/stats.py:_usage_aggregates（655 行起）
 * 契约见 docs/ui-v2/contracts/stats.md。
 */
import { describe, expect, it } from 'vitest'
import {
  bucketLabel,
  buildRequestTrend,
  buildTokenTrend,
  cacheFootnote,
  keyBarItems,
  modelBarItems,
  normalizeDays,
  normalizeGranularity,
  platformBarItems,
  rateTone,
  requestTrendOption,
  toSummaryItems,
  tokenTrendOption,
  trendFootnote,
  type StatsDashboardPayload,
  type StatsUsagePayload,
} from '@/api/stats'

/** 后端 UTC 小时桶 → 本地时区 HH:mm（图表横轴口径；断言与运行测试的机器时区无关）。 */
function localHM(utcBucket: string): string {
  const date = new Date(utcBucket.replace(' ', 'T') + 'Z')
  return String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0')
}

/** /api/stats/dashboard 的真实响应形状（summary 口径见后端 _dashboard_from_rollup）。 */
const dashboard: StatsDashboardPayload = {
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
    requests: 12500,
    successes: 12300,
    failures: 200,
    success_rate: 0.984,
    credits: 1234.56,
    total_tokens: 650000,
    prompt_tokens: 500000,
    completion_tokens: 150000,
    cached_tokens: 50000,
    cache_hit_rate: 0.0909,
    rpm: 8.68,
    tpm: 451.39,
    avg_latency_ms: 450,
    avg_ttfb_ms: 120,
    throughput_tps: 95.5,
    window_minutes: 1440,
  },
  models: [
    {
      model: 'deepseek-v4.1-flash',
      label: 'DeepSeek-V4.1-Flash',
      platform: '',
      platforms: ['workbuddy', 'trae-cn'],
      requests: 7000,
      successes: 6900,
      failures: 100,
      success_rate: 0.9857,
      tokens: 400000,
      credits: 900,
      avg_latency_ms: 500,
      avg_ttfb_ms: 100,
      tps: 40,
    },
  ],
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
      value: 'qoder',
      label: 'Qoder 中国版',
      requests: 3000,
      success_rate: 0.912,
      tokens: 150000,
      credits: 200,
    },
    {
      value: '',
      label: '未知平台',
      requests: 1500,
      success_rate: null,
      tokens: 100000,
      credits: 134.56,
    },
  ],
  distribution: {
    buckets: ['2026-11-23 09:00', '2026-11-23 10:00'],
    series: [{ name: 'DeepSeek-V4.1-Flash', metric: 'credits', values: [1, 2] }],
  },
  trend: {
    buckets: ['2026-11-23 09:00', '2026-11-23 10:00'],
    total_requests: [40, 60],
    series: [
      {
        name: 'DeepSeek-V4.1-Flash',
        requests: [25, 40],
        tokens: [1000, 1500],
        credits: [1, 2],
      },
      { name: 'GLM-5.3', requests: [15, 20], tokens: [500, 600], credits: [0.5, 1] },
    ],
  },
  ranking: [
    {
      model: 'deepseek-v4.1-flash',
      label: 'DeepSeek-V4.1-Flash',
      requests: 7000,
      successes: 6900,
      failures: 100,
      success_rate: 0.9857,
      tokens: 400000,
      credits: 900,
      avg_latency_ms: 500,
      avg_ttfb_ms: 100,
      share: 0.56,
    },
    {
      model: 'glm-5.3',
      label: 'GLM-5.3',
      requests: 3000,
      successes: 2950,
      failures: 50,
      success_rate: 0.9833,
      tokens: 150000,
      credits: 200,
      avg_latency_ms: 400,
      avg_ttfb_ms: 90,
      share: 0.24,
    },
  ],
  errors: [{ kind: 'rate_limit', count: 50 }],
  source: 'rollup',
}

/** /api/stats/usage 的真实响应（只列本页消费的字段）。 */
const usage: StatsUsagePayload = {
  range_days: 1,
  granularity: 'hour',
  platform_label: '全部平台',
  overview: {
    requests: 12500,
    ok_requests: 12300,
    failed_requests: 200,
    credits: 1234.56,
    prompt_tokens: 500000,
    completion_tokens: 150000,
    total_tokens: 650000,
    cached_tokens: 50000,
    avg_latency_ms: 450,
  },
  platforms: [],
  by_model: [],
  by_key: [
    { label: '生产 Key', requests: 9000, tokens: 500000, credits: 900 },
    { label: '#12', requests: 3000, tokens: 150000, credits: 300 },
  ],
  by_endpoint: [],
  by_account: [],
  by_result: [
    { label: '成功', requests: 12300 },
    { label: 'rate_limit', requests: 200 },
  ],
  trend: [
    {
      bucket: '2026-11-23 09:00',
      prompt_tokens: 1000,
      completion_tokens: 500,
      cached_tokens: 200,
      credits: 1,
      requests: 20,
    },
    {
      bucket: '2026-11-23 10:00',
      prompt_tokens: 2000,
      completion_tokens: 600,
      cached_tokens: 500,
      credits: 2,
      requests: 30,
    },
  ],
}

describe('normalizeDays / normalizeGranularity：视图状态收敛', () => {
  it('days 只认 1 / 7 / 30', () => {
    expect(normalizeDays('7')).toBe(7)
    expect(normalizeDays(30)).toBe(30)
    expect(normalizeDays('abc')).toBe(1)
    expect(normalizeDays('90')).toBe(1)
  })

  it('粒度只认 hour / day，且超过 7 天强制按天（后端同口径降级）', () => {
    expect(normalizeGranularity('hour', 1)).toBe('hour')
    expect(normalizeGranularity('day', 1)).toBe('day')
    expect(normalizeGranularity('', 7)).toBe('hour')
    expect(normalizeGranularity('hour', 30)).toBe('day')
    expect(normalizeGranularity('', 30)).toBe('day')
  })
})

describe('bucketLabel：小时桶 HH:mm（本地时区）/ 天桶 MM/DD', () => {
  it('两种桶格式都能出标签', () => {
    expect(bucketLabel('2026-11-23 15:00')).toBe(localHM('2026-11-23 15:00'))
    expect(bucketLabel('2026-11-23')).toBe('11/23')
    expect(bucketLabel('')).toBe('')
  })

  it('小时桶按 UTC 解析再换成本地时间（不再直接截 UTC 字符串）', () => {
    const date = new Date(Date.UTC(2026, 10, 23, 15, 0))
    const expected = String(date.getHours()).padStart(2, '0') + ':00'
    expect(bucketLabel('2026-11-23 15:00')).toBe(expected)
    // 带偏移的 ISO 按偏移解析：+08:00 的 23:00 = UTC 15:00
    expect(bucketLabel('2026-11-23T23:00:00+08:00')).toBe(expected)
    expect(bucketLabel('2026-11-23T15:00:00Z')).toBe(expected)
  })
})

describe('rateTone：与 dashboard.js 一致的 99.5% / 97% 三档', () => {
  it('无样本是 neutral，不冒充 0%', () => {
    expect(rateTone(null)).toBe('neutral')
    expect(rateTone(undefined)).toBe('neutral')
  })

  it('≥99.5% 绿、≥97% 黄、其余红', () => {
    expect(rateTone(0.998)).toBe('ok')
    expect(rateTone(0.995)).toBe('ok')
    expect(rateTone(0.98)).toBe('warn')
    expect(rateTone(0.9699)).toBe('danger')
    expect(rateTone(0)).toBe('danger')
  })
})

describe('toSummaryItems：StatStrip 五格', () => {
  it('请求 / 成功率 / Token / 积分 / 耗时，脚注带成功失败与输入输出', () => {
    const items = toSummaryItems(dashboard.summary)
    expect(items.map((item) => item.label)).toEqual([
      '请求数',
      '成功率',
      'Token 合计',
      '积分消耗',
      '平均耗时',
    ])
    expect(items[0].value).toBe('12,500')
    expect(items[0].foot).toBe('成功 12,300 · 失败 200')
    expect(items[1].value).toBe('98.4%')
    expect(items[1].tone).toBe('warn')
    expect(items[2].value).toBe('650K')
    expect(items[2].foot).toBe('入 500K · 出 150K')
    expect(items[3].value).toBe('1,234.56')
    expect(items[3].foot).toBe('缓存命中 9.1%')
    expect(items[4].value).toBe('450ms')
    expect(items[4].foot).toBe('首字 120ms · 95.5 t/s')
  })

  it('无请求时成功率显示 —（不是 0%），空 payload 五格全 —', () => {
    const items = toSummaryItems({
      ...dashboard.summary,
      requests: 0,
      success_rate: null,
      cache_hit_rate: null,
    })
    expect(items[1].value).toBe('—')
    // StatItem 的语气不含 neutral：无样本映射为 muted（灰字），不冒充 0%
    expect(items[1].tone).toBe('muted')
    expect(items[3].foot).toBe('缓存命中率 —')
    expect(toSummaryItems(null).map((item) => item.value)).toEqual(['—', '—', '—', '—', '—'])
  })
})

describe('buildRequestTrend / requestTrendOption：请求趋势折线', () => {
  it('全部请求 + 各模型序列，桶标签按时/日格式化', () => {
    const trend = buildRequestTrend(dashboard)
    expect(trend.labels).toEqual([localHM('2026-11-23 09:00'), localHM('2026-11-23 10:00')])
    expect(trend.total).toEqual([40, 60])
    expect(trend.series).toEqual([
      { name: 'DeepSeek-V4.1-Flash', values: [25, 40] },
      { name: 'GLM-5.3', values: [15, 20] },
    ])
  })

  it('option 的第一条线是「全部请求」，其余按模型', () => {
    const option = requestTrendOption(dashboard) as {
      xAxis: { data: string[] }
      series: Array<{ name: string; data: number[]; lineStyle?: { width: number } }>
    }
    expect(option.xAxis.data).toEqual([localHM('2026-11-23 09:00'), localHM('2026-11-23 10:00')])
    expect(option.series).toHaveLength(3)
    expect(option.series[0].name).toBe('全部请求')
    expect(option.series[0].data).toEqual([40, 60])
    expect(option.series[0].lineStyle?.width).toBe(2.5)
    expect(option.series[1].name).toBe('DeepSeek-V4.1-Flash')
  })

  it('空 payload 不产生系列', () => {
    const trend = buildRequestTrend(null)
    expect(trend).toEqual({ buckets: [], labels: [], series: [], total: [] })
    const option = requestTrendOption(null) as { series: unknown[] }
    expect(option.series).toEqual([])
  })
})

describe('buildTokenTrend / tokenTrendOption：输入/输出/缓存命中堆叠柱', () => {
  it('输入已扣除缓存命中，堆叠合计 = Token 总量；缓存率按窗口汇总', () => {
    const trend = buildTokenTrend(usage)
    expect(trend.labels).toEqual([localHM('2026-11-23 09:00'), localHM('2026-11-23 10:00')])
    // 1000-200 / 2000-500
    expect(trend.prompt).toEqual([800, 1500])
    expect(trend.output).toEqual([500, 600])
    expect(trend.cached).toEqual([200, 500])
    expect(trend.cacheHitRate).toBeCloseTo(700 / 3000, 6)
  })

  it('缓存超过输入时夹紧，不出现负的输入', () => {
    const trend = buildTokenTrend({
      ...usage,
      trend: [
        { bucket: '2026-11-23 09:00', prompt_tokens: 100, completion_tokens: 10, cached_tokens: 150, credits: 0, requests: 1 },
      ],
    })
    expect(trend.prompt).toEqual([0])
    expect(trend.cached).toEqual([100])
  })

  it('option：输入（未命中 + 缓存命中）两段堆叠柱 + 输出折线；量级接近时共用左轴', () => {
    const option = tokenTrendOption(usage) as {
      yAxis: unknown
      series: Array<{ name: string; type: string; stack?: string; yAxisIndex?: number; data: number[] }>
    }
    expect(option.series.map((item) => item.name)).toEqual(['输入（未命中缓存）', '缓存命中', '输出'])
    expect(option.series.map((item) => item.type)).toEqual(['bar', 'bar', 'line'])
    expect(option.series[0].stack).toBe('tokens')
    expect(option.series[1].stack).toBe('tokens')
    expect(option.series[2].stack).toBeUndefined()
    expect(option.series[1].data).toEqual([200, 500])
    expect(option.series[2].data).toEqual([500, 600])
    // 输入峰值 2000 / 输出峰值 600 < 8 倍 → 单轴
    expect(Array.isArray(option.yAxis)).toBe(false)
    expect(option.series[2].yAxisIndex).toBe(0)
  })

  it('option：输入是输出 8 倍以上时输出走右轴', () => {
    const option = tokenTrendOption({
      ...usage,
      trend: [
        { bucket: '2026-11-23 09:00', prompt_tokens: 100000, completion_tokens: 100, cached_tokens: 0, credits: 0, requests: 1 },
      ],
    }) as { yAxis: unknown[]; series: Array<{ yAxisIndex?: number }> }
    expect(option.yAxis).toHaveLength(2)
    expect(option.series[2].yAxisIndex).toBe(1)
  })

  it('空 payload：无桶、无缓存率', () => {
    expect(buildTokenTrend(null)).toEqual({
      labels: [],
      prompt: [],
      output: [],
      cached: [],
      cacheHitRate: null,
    })
  })
})

describe('排行条数据（BarList）', () => {
  it('平台排行按请求数，语气按成功率', () => {
    const items = platformBarItems(dashboard)
    expect(items.map((item) => [item.label, item.value, item.tone])).toEqual([
      ['WorkBuddy', 8000, 'ok'],
      ['Qoder 中国版', 3000, 'danger'],
      ['未知平台', 1500, 'neutral'],
    ])
  })

  it('模型排行优先用 ranking（含 top N 之外的模型）', () => {
    const items = modelBarItems(dashboard)
    expect(items.map((item) => [item.label, item.value])).toEqual([
      ['DeepSeek-V4.1-Flash', 7000],
      ['GLM-5.3', 3000],
    ])
    expect(items[0].tone).toBe('warn')
  })

  it('Key 排行来自 /api/stats/usage 的 by_key', () => {
    const items = keyBarItems(usage)
    expect(items).toEqual([
      { label: '生产 Key', value: 9000, tone: 'brand' },
      { label: '#12', value: 3000, tone: 'brand' },
    ])
  })

  it('空数据返回空数组', () => {
    expect(platformBarItems(null)).toEqual([])
    expect(modelBarItems(null)).toEqual([])
    expect(keyBarItems(null)).toEqual([])
  })
})

describe('卡片副标题文案', () => {
  it('请求趋势脚注给出成功/失败；缓存脚注给出命中率', () => {
    expect(trendFootnote(dashboard)).toBe('成功 12,300 · 失败 200')
    expect(trendFootnote(null)).toBe('')
    expect(cacheFootnote(usage)).toBe('缓存命中 23.3%')
    expect(cacheFootnote(null)).toBe('缓存命中 —')
  })
})
