import { describe, expect, it } from 'vitest'
import {
  DAY_MS,
  USAGE_FILTER_DEFAULTS,
  chartBucketLabel,
  cnDayStart,
  describeWindow,
  logsQueryFor,
  metricOf,
  normalizeGrain,
  reconcileFilters,
  resolveUsageWindow,
  toBarItems,
  toStatItems,
  toTrendOption,
  toUsageParams,
  usageChips,
  usageCsv,
  usageFilename,
  usageHasData,
  usageQuery,
  usageSummary,
  type UsageFilters,
  type UsageOptionsPayload,
  type UsagePayload,
} from '@/api/usage'

/** 后端 UTC 小时桶 → 本地时区 MM-DD HH:mm（与 chartBucketLabel 同口径，和机器时区无关）。 */
function localMDHM(utcBucket: string): string {
  const date = new Date(utcBucket.replace(' ', 'T') + 'Z')
  const pad = (n: number) => String(n).padStart(2, '0')
  return pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes())
}

/**
 * 契约 docs/ui-v2/contracts/usage.md §二/§三/§四 的数据转换。
 * 测试数据按后端真实响应形状造（GET /api/stats/usage → stats.py:usage_stats / _usage_aggregates）。
 */

/** 固定的「现在」：2026-11-23 12:00 北京时间（= 04:00Z）。 */
const NOW = Date.parse('2026-11-23T04:00:00Z')
/** 北京时间 2026-11-23 00:00 = 2026-11-22T16:00:00Z。 */
const CN_MIDNIGHT = Date.parse('2026-11-22T16:00:00Z')

function samplePayload(): UsagePayload {
  return {
    range_days: null,
    start: '2026-11-22T16:00:00',
    end: null,
    granularity: 'hour',
    filters: { key_id: null, model: null, account_id: null, use_case: null, platform: '' },
    platform: '',
    platform_label: '全部平台',
    platforms: [
      { value: 'trae', label: 'Trae 国内版', requests: 175, tokens: 1200, credits: 9.5 },
    ],
    overview: {
      requests: 862,
      ok_requests: 850,
      failed_requests: 12,
      credits: 729.31,
      prompt_tokens: 1_200_000,
      completion_tokens: 340_000,
      total_tokens: 1_540_000,
      cached_tokens: 90_000,
      avg_latency_ms: 3950,
    },
    by_model: [
      {
        label: 'DeepSeek-V4.1-Flash',
        model: 'dfmodel',
        platform: 'qoder',
        platform_label: 'Qoder 中国版',
        requests: 120,
        tokens: 9000,
        credits: 12.5,
      },
      {
        label: 'gpt-5.1',
        model: 'gpt-5.1',
        platform: 'workbuddy',
        platform_label: 'WorkBuddy',
        requests: 40,
        tokens: 3000,
        credits: 0,
      },
    ],
    by_endpoint: [
      { label: 'chat', requests: 800, tokens: 1000, credits: 100.0 },
      { label: 'responses', requests: 50, tokens: 500, credits: 0 },
    ],
    by_key: [
      { label: 'prod-key', requests: 700, tokens: 1000, credits: 100.0 },
      { label: '#12', requests: 2, tokens: 10, credits: 0 },
    ],
    by_account: [
      { label: '193****2486', requests: 340, tokens: 1000, credits: 9.0, account_id: 152 },
      { label: '其他 12 个账号', requests: 60, tokens: 10, credits: 1.0, account_id: null, others: true },
      { label: '未分配账号', requests: 0, tokens: 0, credits: 0, account_id: null },
    ],
    by_result: [
      { label: '成功', requests: 850 },
      { label: 'rate_limit', requests: 12 },
    ],
    account_models: [],
    usage_scope: 'gateway_only',
    trend: [
      {
        bucket: '2026-11-23 10:00',
        prompt_tokens: 1000,
        completion_tokens: 300,
        cached_tokens: 0,
        credits: 1.2,
        requests: 12,
      },
      {
        bucket: '2026-11-23 11:00',
        prompt_tokens: 2000,
        completion_tokens: 600,
        cached_tokens: 100,
        credits: 2.4,
        requests: 20,
      },
    ],
  }
}

function defaultFilters(patch: Partial<UsageFilters> = {}): UsageFilters {
  return { ...USAGE_FILTER_DEFAULTS, ...patch }
}

function sampleOptions(): UsageOptionsPayload {
  return {
    models: [{ value: 'dfmodel', label: 'DeepSeek-V4.1-Flash', requests: 120 }],
    use_cases: ['chat', 'responses'],
    keys: [{ id: 3, name: 'prod-key' }],
    accounts: [{ id: 152, name: '193****2486' }],
    platforms: [{ value: 'qoder', label: 'Qoder 中国版', requests: 120 }],
  }
}

describe('时间窗（北京时间自然日）', () => {
  it('cnDayStart 取北京当天 00:00', () => {
    expect(cnDayStart(NOW)).toBe(CN_MIDNIGHT)
    expect(cnDayStart(NOW)).toBeLessThanOrEqual(NOW)
  })

  it('默认窗口 = 今日（自然日）到现在，不含 end', () => {
    const win = resolveUsageWindow(defaultFilters(), NOW)
    expect(win.explicit).toBe(false)
    expect(win.label).toBe('今日（自然日）')
    expect(win.startIso).toBe('2026-11-22T16:00:00.000Z')
    expect(win.endIso).toBe(new Date(NOW).toISOString())
    expect(win.days).toBe(1)
  })

  it('显式区间：跨天标签与天数', () => {
    const win = resolveUsageWindow(
      defaultFilters({ start: '2026-11-20T16:00:00.000Z', end: '2026-11-22T16:00:00.000Z' }),
      NOW,
    )
    expect(win.explicit).toBe(true)
    expect(win.days).toBe(2)
    expect(win.label).toBe('2026-11-21 ~ 2026-11-23')
    expect(describeWindow(CN_MIDNIGHT, CN_MIDNIGHT + DAY_MS - 1, true)).toBe('2026-11-23')
  })

  it('开始晚于结束时按开始时间收口（前端已拦，这里兜底）', () => {
    const win = resolveUsageWindow(
      defaultFilters({ start: '2026-11-22T16:00:00.000Z', end: '2026-11-21T16:00:00.000Z' }),
      NOW,
    )
    expect(win.endMs).toBe(win.startMs)
  })
})

describe('toUsageParams：筛选 → 接口参数', () => {
  it('默认窗口显式发北京今天 00:00 的 start，且不带 end', () => {
    const params = toUsageParams(defaultFilters(), { now: NOW })
    expect(params).toEqual({
      granularity: 'hour',
      start: '2026-11-22T16:00:00.000Z',
      end: undefined,
      key_id: null,
      account_id: null,
      model: undefined,
      use_case: undefined,
      platform: undefined,
      refresh: undefined,
    } satisfies typeof params)
    expect(usageQuery(params)).toEqual({
      granularity: 'hour',
      start: '2026-11-22T16:00:00.000Z',
    })
  })

  it('筛选全部映射到后端字段名（key_id/account_id/model/use_case/platform）', () => {
    const params = toUsageParams(
      defaultFilters({
        platform: 'qoder',
        key: '3',
        model: 'dfmodel',
        account: '152',
        endpoint: 'chat',
        grain: 'day',
      }),
      { now: NOW, refresh: true },
    )
    expect(params).toMatchObject({
      granularity: 'day',
      key_id: 3,
      account_id: 152,
      model: 'dfmodel',
      use_case: 'chat',
      platform: 'qoder',
      refresh: true,
    })
  })

  it('粒度：显式优先，否则窗口 > 3 天自动按天', () => {
    expect(normalizeGrain('', 1)).toBe('hour')
    expect(normalizeGrain('', 7)).toBe('day')
    expect(normalizeGrain('hour', 30)).toBe('hour')
    expect(normalizeGrain('day', 1)).toBe('day')
    expect(toUsageParams(defaultFilters({ start: '2026-11-01T00:00:00.000Z' }), { now: NOW }).granularity).toBe('day')
  })
})

describe('分布口径与 BarList 项', () => {
  it('任意行有积分就按积分，否则按（成功）请求数', () => {
    expect(metricOf(samplePayload().by_model)).toBe('credits')
    expect(metricOf([{ label: 'x', requests: 5, credits: 0 }])).toBe('requests')
    expect(metricOf(null)).toBe('requests')
  })

  it('转 BarList 项：过滤全 0 行，「其他 N 个账号」用 neutral', () => {
    const rows = toBarItems(samplePayload().by_account, 'requests')
    expect(rows.map((row) => row.label)).toEqual(['193****2486', '其他 12 个账号'])
    expect(rows[0]).toMatchObject({ value: 340, tone: 'brand' })
    expect(rows[1].tone).toBe('neutral')

    const credits = toBarItems(samplePayload().by_model, 'credits')
    expect(credits.map((row) => row.value)).toEqual([12.5])
    const requests = toBarItems(samplePayload().by_result, 'requests')
    expect(requests.map((row) => row.value)).toEqual([850, 12])
  })
})

describe('统计条 toStatItems', () => {
  it('6 格口径与脚注（成功/失败/Token/积分）', () => {
    const items = toStatItems(samplePayload().overview)
    expect(items.map((item) => item.label)).toEqual([
      '请求数',
      '成功',
      '失败',
      'Token 输入',
      'Token 输出',
      '积分',
    ])
    expect(items[0]).toMatchObject({ value: '862', foot: '平均耗时 3.95s' })
    expect(items[1]).toMatchObject({ value: '850', tone: 'ok', foot: '成功率 98.6%' })
    expect(items[2]).toMatchObject({ value: '12', tone: 'danger', foot: '失败率 1.4%' })
    expect(items[3]).toMatchObject({ value: '1.2M', foot: '缓存命中 90K' })
    expect(items[4]).toMatchObject({ value: '340K', foot: '合计 1.5M' })
    expect(items[5]).toMatchObject({ value: '729.31', foot: '平均每次 0.85' })
  })

  it('无数据时 6 格都显示 —，失败 0 次不着色', () => {
    expect(toStatItems(null).every((item) => item.value === '—')).toBe(true)
    const zero = toStatItems({
      requests: 0,
      ok_requests: 0,
      failed_requests: 0,
      credits: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
      cached_tokens: 0,
      avg_latency_ms: 0,
    })
    expect(zero[2].tone).toBe('muted')
    expect(zero[0].foot).toBe('平均耗时 —')
    expect(zero[5].foot).toBe('平均每次 —')
  })
})

describe('趋势 option', () => {
  it('输入/输出两条线不堆叠，x 轴裁成 MM-DD HH:00；量级接近时共用一根轴', () => {
    const option = toTrendOption(samplePayload().trend, 'hour') as {
      xAxis: { data: string[] }
      yAxis: unknown
      series: Array<{ name: string; stack?: string; yAxisIndex?: number; data: number[] }>
    }
    expect(option.xAxis.data).toEqual([localMDHM('2026-11-23 10:00'), localMDHM('2026-11-23 11:00')])
    expect(option.series.map((item) => item.name)).toEqual(['输入', '输出'])
    // 第 2 轮审查 P0-13：堆叠会让最上面那条线的高度 = 输入 + 输出，颜色与图例对不上
    expect(option.series.every((item) => item.stack === undefined)).toBe(true)
    expect(option.series[0].data).toEqual([1000, 2000])
    expect(option.series[1].data).toEqual([300, 600])
    expect(Array.isArray(option.yAxis)).toBe(false)
    expect(option.series[1].yAxisIndex).toBe(0)
  })

  it('输入是输出 8 倍以上时输出走右轴', () => {
    const option = toTrendOption(
      [{ bucket: '2026-11-23 10:00', prompt_tokens: 100000, completion_tokens: 100, cached_tokens: 0, credits: 0, requests: 1 }],
      'hour',
    ) as { yAxis: unknown[]; series: Array<{ yAxisIndex?: number }> }
    expect(option.yAxis).toHaveLength(2)
    expect(option.series[1].yAxisIndex).toBe(1)
  })

  it('按天只保留 MM-DD', () => {
    expect(chartBucketLabel('2026-11-23', 'day')).toBe('11-23')
    expect(chartBucketLabel('2026-11-23 10:00', 'hour')).toBe(localMDHM('2026-11-23 10:00'))
    expect(chartBucketLabel('刚刚', 'hour')).toBe('刚刚')
    const empty = toTrendOption(null, 'hour') as { series: Array<{ data: number[] }> }
    expect(empty.series.map((item) => item.data)).toEqual([[], []])
  })
})

describe('汇总 / chips / 选项回落', () => {
  it('FilterBar 汇总文字', () => {
    expect(usageSummary(samplePayload())).toBe('共 862 次 · 成功 850 · 失败 12 · 积分 729.31')
    expect(usageSummary(null)).toBe('')
  })

  it('chips：时间永远第一项，其余用选项里的中文名', () => {
    const options = sampleOptions()
    const plain = usageChips(defaultFilters(), options, resolveUsageWindow(defaultFilters(), NOW))
    expect(plain).toEqual([{ key: 'range', label: '时间', value: '今日（自然日）' }])

    const filters = defaultFilters({
      platform: 'qoder',
      key: '3',
      model: 'dfmodel',
      account: '152',
      endpoint: 'chat',
    })
    const chips = usageChips(filters, options, resolveUsageWindow(filters, NOW))
    expect(chips.map((chip) => chip.key)).toEqual(['range', 'platform', 'key', 'model', 'account', 'endpoint'])
    expect(chips[1].value).toBe('Qoder 中国版')
    expect(chips[2].value).toBe('prod-key')
    expect(chips[3].value).toBe('DeepSeek-V4.1-Flash')
  })

  it('切换窗口后失效的选项回落「全部」，仍有效的保留', () => {
    const filters = defaultFilters({
      platform: 'gone',
      key: '3',
      model: 'removed',
      account: '152',
      endpoint: 'embeddings',
    })
    const next = reconcileFilters(filters, sampleOptions())
    expect(next.platform).toBe('')
    expect(next.model).toBe('')
    expect(next.endpoint).toBe('')
    expect(next.key).toBe('3')
    expect(next.account).toBe('152')
    expect(reconcileFilters(filters, null)).toBe(filters)
  })
})

describe('logsQueryFor：/logs 深链参数', () => {
  const rows = samplePayload()

  it('模型用落库 id + 当前窗口起点（默认不带 end）', () => {
    expect(logsQueryFor('model', rows.by_model[0], defaultFilters(), NOW)).toEqual({
      model: 'dfmodel',
      start: '2026-11-22T16:00:00.000Z',
    })
  })

  it('显式结束时间时带上 end', () => {
    const query = logsQueryFor(
      'key',
      rows.by_key[0],
      defaultFilters({ start: '2026-11-20T16:00:00.000Z', end: '2026-11-22T16:00:00.000Z' }),
      NOW,
    )
    expect(query).toEqual({
      key: 'prod-key',
      start: '2026-11-20T16:00:00.000Z',
      end: '2026-11-22T16:00:00.000Z',
    })
  })

  it('查不到 Key 名的 #id 行不可点', () => {
    expect(logsQueryFor('key', rows.by_key[1], defaultFilters(), NOW)).toBeNull()
  })

  it('账号：其他汇总行 / 未分配账号不可点，真实账号按名称查', () => {
    expect(logsQueryFor('account', rows.by_account[1], defaultFilters(), NOW)).toBeNull()
    expect(logsQueryFor('account', rows.by_account[2], defaultFilters(), NOW)).toBeNull()
    expect(logsQueryFor('account', rows.by_account[0], defaultFilters(), NOW)).toEqual({
      account: '193****2486',
      start: '2026-11-22T16:00:00.000Z',
    })
  })

  it('结果：成功 → success，其它 error_kind → error', () => {
    expect(logsQueryFor('result', rows.by_result[0], defaultFilters(), NOW)?.result).toBe('success')
    expect(logsQueryFor('result', rows.by_result[1], defaultFilters(), NOW)?.result).toBe('error')
  })

  it('没有 model 字段的模型行不跳', () => {
    expect(
      logsQueryFor('model', { label: 'x', requests: 1, tokens: 1, credits: 0, model: '', platform: '', platform_label: '' }, defaultFilters(), NOW),
    ).toBeNull()
  })
})

describe('CSV 导出', () => {
  it('包含概览与各分布段，逗号/引号按 RFC4180 转义', () => {
    const payload = samplePayload()
    payload.by_key = [{ label: 'key,with"quote', requests: 3, tokens: 1, credits: 0.5 }]
    const csv = usageCsv(payload, { windowLabel: '今日（自然日）', grain: 'hour' })
    const lines = csv.split('\n')
    expect(lines[0]).toBe('# 使用记录')
    expect(lines[1]).toBe('时间范围,今日（自然日）,粒度,按小时')
    expect(csv).toContain('请求数,862')
    expect(csv).toContain('Token 输入,1200000')
    expect(csv).toContain('# 模型分布')
    expect(csv).toContain('DeepSeek-V4.1-Flash,Qoder 中国版,120,9000,12.5')
    expect(csv).toContain('"key,with""quote",3,1,0.5')
    expect(csv).toContain('# Token 趋势')
    expect(csv).toContain('2026-11-23 10:00,1000,300,0,12,1.2')
  })

  it('空分布段写「（无数据）」而不是留白', () => {
    const payload = samplePayload()
    payload.by_result = []
    const csv = usageCsv(payload, { windowLabel: '2026-11-23', grain: 'day' })
    expect(csv).toContain('# 请求结果分布\n结果,请求(全部)\n（无数据）')
  })

  it('文件名按北京时间', () => {
    expect(usageFilename(NOW)).toBe('miraihub-usage-20261123-120000.csv')
  })

  it('usageHasData 区分「没量」与「有趋势无请求」', () => {
    expect(usageHasData(null)).toBe(false)
    const empty = samplePayload()
    empty.overview.requests = 0
    empty.trend = []
    expect(usageHasData(empty)).toBe(false)
    expect(usageHasData(samplePayload())).toBe(true)
  })
})
