/**
 * S13 模型广场：数据转换 / 筛选排序分页 / sparkline 分桶的单测。
 *
 * 数据形状照抄后端真实响应：
 *   · GET /api/stats/model-market → admin/routers/stats.py:model_market（913 行起）
 *   · GET /api/models/aggregate   → admin/model_alias.py:aggregate_payload
 *   · GET /api/stats/dashboard    → admin/routers/stats.py:_dashboard_agg 的 trend
 * 契约见 docs/ui-v2/contracts/model-market.md。
 */
import { describe, expect, it } from 'vitest'
import {
  dedupePricing,
  filterMarketCards,
  marketMultiplier,
  marketPlatforms,
  hoursToDays,
  indexCallNames,
  indexTrendSeries,
  logLinkFor,
  marketStats,
  normalizeModelName,
  paginateMarketCards,
  platformOptions,
  priceText,
  rateTone,
  sortMarketCards,
  sparklineBars,
  toMarketCards,
  type AggregatePayload,
  type MarketItem,
  type ModelMarketPayload,
} from '@/api/market'

/** model_market 的真实响应形状（items 按 requests DESC, name 排序）。 */
const marketItems: MarketItem[] = [
  {
    key: 'deepseekv41flash',
    name: 'DeepSeek-V4.1-Flash',
    platforms: ['qoder', 'workbuddy'],
    platform_labels: ['Qoder 中国版', 'WorkBuddy'],
    models: ['deepseek-flash-qoder', 'deepseek-v4.1-flash'],
    requests: 12400,
    success_rate: 0.982,
    tokens: 5600000,
    credits: 234.56,
    avg_latency_ms: 850,
    avg_ttfb_ms: 120,
    tps: 128.5,
    pricing: [
      {
        platform: 'qoder',
        platform_label: 'Qoder 中国版',
        model_id: 'deepseek-flash-qoder',
        multiplier: 1.2,
        free: false,
      },
      {
        platform: 'workbuddy',
        platform_label: 'WorkBuddy',
        model_id: 'deepseek-v4.1-flash',
        multiplier: 1,
        free: false,
      },
    ],
    free: false,
    min_multiplier: 1,
    active_accounts: 15,
    exhausted_accounts: 2,
    available: true,
  },
  {
    key: 'glm53',
    name: 'GLM-5.3',
    platforms: ['workbuddy'],
    platform_labels: ['WorkBuddy'],
    models: ['glm-5.3'],
    requests: 0,
    success_rate: null,
    tokens: 0,
    credits: 0,
    avg_latency_ms: 0,
    avg_ttfb_ms: 0,
    tps: 0,
    pricing: [
      {
        platform: 'workbuddy',
        platform_label: 'WorkBuddy',
        model_id: 'glm-5.3',
        multiplier: 0,
        free: true,
      },
    ],
    free: true,
    min_multiplier: 0,
    active_accounts: 3,
    exhausted_accounts: 0,
    available: true,
  },
  {
    key: 'gpt6astra',
    name: 'GPT-6 Astra',
    platforms: ['trae'],
    platform_labels: ['Trae 国内版'],
    models: ['gpt-6-astra'],
    requests: 860,
    success_rate: 0.9621,
    tokens: 120000,
    credits: 12.4,
    avg_latency_ms: 2400,
    avg_ttfb_ms: 620,
    tps: 42.2,
    pricing: [
      {
        platform: 'trae',
        platform_label: 'Trae 国内版',
        model_id: 'gpt-6-astra',
        multiplier: 2.5,
        free: false,
      },
    ],
    free: false,
    min_multiplier: 2.5,
    active_accounts: 2,
    exhausted_accounts: 2,
    available: false,
  },
  {
    key: 'nopriced',
    name: '未定价模型',
    platforms: [],
    platform_labels: [],
    models: ['nopriced'],
    requests: 0,
    success_rate: null,
    tokens: 0,
    credits: 0,
    avg_latency_ms: 0,
    avg_ttfb_ms: 0,
    tps: 0,
    pricing: [],
    free: false,
    min_multiplier: 0,
    active_accounts: 0,
    exhausted_accounts: 0,
    available: false,
  },
]

const marketPayload: ModelMarketPayload = {
  window_hours: 24,
  start: '2026-11-22T11:00:00Z',
  end: '2026-11-23T11:00:00Z',
  source: 'rollup',
  total: marketItems.length,
  available_models: 2,
  items: marketItems,
}

/** aggregate_payload 的真实响应形状（同一模型跨平台合成一项，带 client_id）。 */
const aggregatePayload: AggregatePayload = {
  items: [
    {
      key: 'deepseekv41flash',
      name: 'DeepSeek-V4.1-Flash',
      platforms: [
        {
          platform: 'qoder',
          platform_label: 'Qoder 中国版',
          model_id: 'deepseek-flash-qoder',
          client_id: 'deepseek-flash-qoder-intl',
          multiplier: 1.2,
          enabled: true,
        },
        {
          platform: 'workbuddy',
          platform_label: 'WorkBuddy',
          model_id: 'deepseek-v4.1-flash',
          client_id: 'deepseek-v4.1-flash',
          multiplier: 1,
          enabled: true,
        },
      ],
      model_ids: ['deepseek-flash-qoder', 'deepseek-v4.1-flash'],
      platform_count: 2,
      min_multiplier: 1,
      free_any: false,
      enabled_any: true,
    },
    {
      key: 'gpt6astra',
      name: 'GPT-6 Astra',
      platforms: [
        {
          platform: 'trae',
          platform_label: 'Trae 国内版',
          model_id: 'gpt-6-astra',
          client_id: 'gpt-6-astra',
          multiplier: 2.5,
          enabled: true,
        },
      ],
      model_ids: ['gpt-6-astra'],
      platform_count: 1,
      min_multiplier: 2.5,
      free_any: false,
      enabled_any: true,
    },
  ],
  total_models: 3,
  total_groups: 2,
  multi_platform_groups: 1,
}

/** dashboard trend：后端只回 Top N 模型的分时序列（这里给 6 个桶）。 */
const trendPayload = {
  buckets: ['2026-11-23 06:00', '2026-11-23 07:00', '2026-11-23 08:00', '2026-11-23 09:00', '2026-11-23 10:00', '2026-11-23 11:00'],
  total_requests: [300, 420, 180, 900, 640, 60],
  series: [
    { name: 'DeepSeek-V4.1-Flash', requests: [100, 200, 80, 500, 400, 20], tokens: [], credits: [] },
    { name: 'GLM 5.3', requests: [0, 0, 0, 0, 0, 0], tokens: [], credits: [] },
  ],
}

describe('toMarketCards：接口响应 → 卡片行', () => {
  const cards = toMarketCards(marketPayload, aggregatePayload, trendPayload)

  it('保留后端字段并做驼峰映射', () => {
    expect(cards).toHaveLength(4)
    const first = cards[0]
    expect(first.key).toBe('deepseekv41flash')
    expect(first.name).toBe('DeepSeek-V4.1-Flash')
    expect(first.platformCount).toBe(2)
    expect(first.requests).toBe(12400)
    expect(first.successRate).toBe(0.982)
    expect(first.latencyMs).toBe(850)
    expect(first.ttfbMs).toBe(120)
    expect(first.tps).toBe(128.5)
    expect(first.credits).toBe(234.56)
    expect(first.activeAccounts).toBe(15)
    expect(first.exhaustedAccounts).toBe(2)
    expect(first.available).toBe(true)
  })

  it('调用名优先取 aggregate 里倍率最低平台的 client_id', () => {
    // WorkBuddy 的倍率 1.0 低于 Qoder 的 1.2
    expect(cards[0].callName).toBe('deepseek-v4.1-flash')
    // aggregate 缺项时退回最低倍率平台的 model_id
    expect(cards[1].callName).toBe('glm-5.3')
    // 没有 pricing 时退回 models[0]
    expect(cards[3].callName).toBe('nopriced')
  })

  it('可用性按「有定价 且 活跃账号 > 冷却账号」重算（与后端同口径）', () => {
    expect(cards[2].available).toBe(false) // 2 - 2 = 0
    expect(cards[3].available).toBe(false) // 没有定价
    expect(cards[3].multiplier).toBeNull()
  })

  it('倍率与免费：有定价才有倍率，全平台免费才叫免费', () => {
    expect(cards[0].multiplier).toBe(1)
    expect(cards[0].free).toBe(false)
    expect(cards[1].free).toBe(true)
    expect(priceText(cards[1])).toBe('免费')
    expect(priceText(cards[0])).toBe('×1.00')
    expect(priceText(cards[3])).toBe('未定价')
  })

  it('sparkline：只画 trend 里有的模型，没有分时数据时是空数组', () => {
    expect(cards[0].spark.length).toBeGreaterThan(0)
    expect(cards[0].sparkTotal).toBe(1300)
    expect(cards[0].spark.every((value) => value >= 0 && value <= 1)).toBe(true)
    // 'GLM 5.3' 归一后与 key 'glm53' 命中，但序列全 0 → 不画
    expect(cards[1].spark).toEqual([])
    expect(cards[1].sparkTotal).toBe(0)
    expect(cards[2].spark).toEqual([])
    expect(cards[2].sparkTotal).toBeNull()
  })

  it('null / 缺字段输入不抛异常', () => {
    expect(toMarketCards(null)).toEqual([])
    expect(toMarketCards(undefined, null, null)).toEqual([])
    const [row] = toMarketCards({ ...marketPayload, items: [{ key: 'x' } as MarketItem] })
    expect(row.name).toBe('x')
    expect(row.platformCount).toBe(0)
    expect(row.available).toBe(false)
    expect(row.successRate).toBeNull()
  })
})

describe('sparklineBars：分桶与归一', () => {
  it('24 个点合并成 12 根柱，总量守恒、峰值归一为 1', () => {
    const values = Array.from({ length: 24 }, (_, index) => index + 1)
    const bars = sparklineBars(values, 12)
    expect(bars).toHaveLength(12)
    expect(Math.max(...bars)).toBe(1)
    expect(bars[0]).toBeLessThan(bars[11])
    // 前两桶 = (1+2) / (23+24)
    expect(bars[0]).toBeCloseTo(3 / 47, 3)
  })

  it('点数少于桶数时保持原粒度', () => {
    expect(sparklineBars([1, 2, 4], 12)).toEqual([0.25, 0.5, 1])
  })

  it('全 0 / 空 / 非法输入返回空数组（不画假数据）', () => {
    expect(sparklineBars([0, 0, 0])).toEqual([])
    expect(sparklineBars([])).toEqual([])
    expect(sparklineBars(null)).toEqual([])
    expect(sparklineBars([Number.NaN, -1, 0])).toEqual([])
  })

  it('负数与 NaN 按 0 处理，不影响其余柱高', () => {
    expect(sparklineBars([Number.NaN, 4, -3, 8])).toEqual([0, 0.5, 0, 1])
  })
})

describe('filterMarketCards：搜索 / 平台 / 可用性', () => {
  const cards = toMarketCards(marketPayload, aggregatePayload, trendPayload)

  it('搜索匹配展示名、key、调用名与各平台原始模型名，大小写不敏感', () => {
    expect(filterMarketCards(cards, { q: 'deepseek' }).map((card) => card.key)).toEqual([
      'deepseekv41flash',
    ])
    expect(filterMarketCards(cards, { q: 'DEEPSEEK-FLASH-QODER' }).map((card) => card.key)).toEqual([
      'deepseekv41flash',
    ])
    expect(filterMarketCards(cards, { q: 'glm' }).map((card) => card.key)).toEqual(['glm53'])
    expect(filterMarketCards(cards, { q: '不存在' })).toEqual([])
  })

  it('平台筛选按 platforms 精确匹配，大小写不敏感', () => {
    expect(filterMarketCards(cards, { platform: 'workbuddy' }).map((card) => card.key)).toEqual([
      'deepseekv41flash',
      'glm53',
    ])
    expect(filterMarketCards(cards, { platform: 'TRAE' }).map((card) => card.key)).toEqual([
      'gpt6astra',
    ])
    expect(filterMarketCards(cards, { platform: 'qoder' }).map((card) => card.key)).toEqual([
      'deepseekv41flash',
    ])
  })

  it('仅看可用 + 多条件叠加，且不修改入参', () => {
    expect(filterMarketCards(cards, { available: 'available' }).map((card) => card.key)).toEqual([
      'deepseekv41flash',
      'glm53',
    ])
    expect(
      filterMarketCards(cards, { available: 'available', platform: 'workbuddy' }).map(
        (card) => card.key,
      ),
    ).toEqual(['deepseekv41flash', 'glm53'])
    expect(cards).toHaveLength(4)
  })
})

describe('sortMarketCards：调用量 / 价格 / 速度 / 成功率 / 名称', () => {
  const cards = toMarketCards(marketPayload, aggregatePayload, trendPayload)

  it('默认按调用量降序', () => {
    // 同值时按名称（zh-CN）兜底：'未定价模型' 排在中英混排的 'GLM-5.3' 之前
    expect(sortMarketCards(cards, 'requests', 'desc').map((card) => card.key)).toEqual([
      'deepseekv41flash',
      'gpt6astra',
      'nopriced',
      'glm53',
    ])
    expect(sortMarketCards(cards, 'requests', 'asc').map((card) => card.key)).toEqual([
      'nopriced',
      'glm53',
      'gpt6astra',
      'deepseekv41flash',
    ])
  })

  it('价格升序：免费在前，未定价排最后', () => {
    expect(sortMarketCards(cards, 'price', 'asc').map((card) => card.key)).toEqual([
      'glm53',
      'deepseekv41flash',
      'gpt6astra',
      'nopriced',
    ])
  })

  it('速度与成功率降序，无样本（null / 0）排在后面', () => {
    expect(sortMarketCards(cards, 'tps', 'desc').map((card) => card.key)).toEqual([
      'deepseekv41flash',
      'gpt6astra',
      'nopriced',
      'glm53',
    ])
    expect(sortMarketCards(cards, 'success_rate', 'desc').map((card) => card.key)[0]).toBe(
      'deepseekv41flash',
    )
  })

  it('名称按中文 locale 排序，未知字段回落调用量', () => {
    const names = sortMarketCards(cards, 'name', 'asc').map((card) => card.name)
    expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b, 'zh-CN')))
    expect(sortMarketCards(cards, 'wat', 'desc').map((card) => card.key)).toEqual(
      sortMarketCards(cards, 'requests', 'desc').map((card) => card.key),
    )
    expect(cards).toHaveLength(4)
  })
})

describe('paginateMarketCards：客户端分页', () => {
  const cards = toMarketCards(marketPayload, aggregatePayload, trendPayload)

  it('按每页条数切片，页数向上取整', () => {
    const page = paginateMarketCards(cards, 2, 2)
    expect(page.total).toBe(4)
    expect(page.pageCount).toBe(2)
    expect(page.page).toBe(2)
    expect(page.items.map((card) => card.key)).toEqual(['gpt6astra', 'nopriced'])
  })

  it('页码越界回退到最后一页，非法每页条数回落 20', () => {
    expect(paginateMarketCards(cards, 99, 3).page).toBe(2)
    expect(paginateMarketCards(cards, 1, 0).items).toHaveLength(4)
  })

  it('空数据：0 页、页码 1', () => {
    expect(paginateMarketCards([], 3, 20)).toEqual({ items: [], total: 0, pageCount: 0, page: 1 })
  })
})

describe('marketStats / platformOptions / rateTone / logLinkFor', () => {
  const cards = toMarketCards(marketPayload, aggregatePayload, trendPayload)

  it('汇总模型数、可用数、请求数、免费数、平台数', () => {
    expect(marketStats(cards)).toEqual({
      total: 4,
      available: 2,
      requests: 13260,
      free: 1,
      platforms: 3,
    })
  })

  it('平台下拉只列数据集里出现过的平台，值为 id、标签为展示名', () => {
    const options = platformOptions(cards)
    expect(options.map((item) => item.value).sort()).toEqual(['qoder', 'trae', 'workbuddy'])
    expect(options.find((item) => item.value === 'trae')?.label).toBe('Trae 国内版')
  })

  it('成功率着色：≥99.5% ok、≥97% warn、<97% danger、无数据 neutral', () => {
    expect(rateTone(0.998)).toBe('ok')
    expect(rateTone(0.982)).toBe('warn')
    expect(rateTone(0.9621)).toBe('danger')
    expect(rateTone(null)).toBe('neutral')
    expect(rateTone(undefined)).toBe('neutral')
  })

  it('查看日志带 model 查询；没有调用名时只跳页面', () => {
    expect(logLinkFor({ callName: 'gpt-6-astra', key: 'gpt6astra' })).toEqual({
      path: '/logs',
      query: { model: 'gpt-6-astra' },
    })
    expect(logLinkFor({ callName: '', key: '' })).toEqual({ path: '/logs', query: {} })
  })
})

describe('索引与窗口换算', () => {
  it('normalizeModelName 抹平展示名/ id 的差异', () => {
    expect(normalizeModelName('DeepSeek-V4.1-Flash')).toBe('deepseekv41flash')
    expect(normalizeModelName('deepseek v4.1 flash')).toBe('deepseekv41flash')
    expect(normalizeModelName(null)).toBe('')
  })

  it('indexCallNames：同名模型取倍率最低平台的 client_id', () => {
    const map = indexCallNames(aggregatePayload)
    expect(map.get('deepseekv41flash')).toBe('deepseek-v4.1-flash')
    expect(indexCallNames(null).size).toBe(0)
  })

  it('indexTrendSeries：按归一名字索引，空序列跳过', () => {
    const map = indexTrendSeries(trendPayload)
    expect(map.get('deepseekv41flash')).toHaveLength(6)
    expect(indexTrendSeries(null).size).toBe(0)
  })

  it('hoursToDays：24h → 1 天、3d → 3 天、7d → 7 天，非法值回落 1', () => {
    expect(hoursToDays(24)).toBe(1)
    expect(hoursToDays(72)).toBe(3)
    expect(hoursToDays(168)).toBe(7)
    expect(hoursToDays(5)).toBe(1)
  })
})

/* ------------------------------------------------------------------ *
 * 修复轮（fix_plan N3 / 第 3 轮审查 A1、A2）：按量倍率、平台并集、定价去重
 * 同时覆盖「旧后端」（min_multiplier=-1、platforms 只含有流量的平台）与
 * 「新后端」（min_multiplier=null + metered、platforms 已是并集、pricing 带 realm）两种形状。
 * ------------------------------------------------------------------ */

function meteredItem(overrides: Partial<MarketItem> = {}): MarketItem {
  return {
    key: 'deepseekv41flash',
    name: 'DeepSeek-V4.1-Flash',
    platforms: ['marvis'],
    platform_labels: ['腾讯 Marvis'],
    models: ['deepseek-v4.1-flash'],
    requests: 120,
    success_rate: 1,
    tokens: 1000,
    credits: 0,
    avg_latency_ms: 800,
    avg_ttfb_ms: 0,
    tps: 10,
    pricing: [
      { platform: 'marvis', platform_label: '腾讯 Marvis', model_id: 'ds-flash', multiplier: -1, free: false },
      { platform: 'marvis', platform_label: '腾讯 Marvis', model_id: 'ds-flash', multiplier: -1, free: false },
      { platform: 'loomy', platform_label: 'Loomy', model_id: 'ds-flash-loomy', multiplier: -1, free: false },
    ],
    free: false,
    min_multiplier: -1,
    active_accounts: 3,
    exhausted_accounts: 0,
    available: true,
    ...overrides,
  }
}

describe('按量计费（负倍率）', () => {
  it('旧后端 min_multiplier=-1：显示「按量计费」，不显示 ×-1.00', () => {
    const [card] = toMarketCards({ ...marketPayload, items: [meteredItem()] })
    expect(card.metered).toBe(true)
    expect(card.multiplier).toBeNull()
    expect(priceText(card)).toBe('按量计费')
    expect(priceText(card)).not.toContain('-1')
  })

  it('新后端 min_multiplier=null + metered=true：同样是「按量计费」', () => {
    const [card] = toMarketCards({
      ...marketPayload,
      items: [meteredItem({ min_multiplier: null, metered: true })],
    })
    expect(card.metered).toBe(true)
    expect(priceText(card)).toBe('按量计费')
  })

  it('有倍率与按量混合时取 ≥0 的最小倍率', () => {
    expect(
      marketMultiplier({
        min_multiplier: -1,
        pricing: [
          { platform: 'marvis', platform_label: 'Marvis', model_id: 'a', multiplier: -1, free: false },
          { platform: 'workbuddy', platform_label: 'WorkBuddy', model_id: 'b', multiplier: 1.5, free: false },
          { platform: 'qoder', platform_label: 'Qoder', model_id: 'c', multiplier: 0.8, free: false },
        ],
      }),
    ).toEqual({ multiplier: 0.8, metered: false })
    expect(marketMultiplier({ min_multiplier: null, pricing: [] })).toEqual({ multiplier: null, metered: false })
  })

  it('价格排序：免费 < 有倍率 < 按量 < 未定价', () => {
    const cards = toMarketCards({
      ...marketPayload,
      items: [...marketItems, meteredItem({ key: 'metered', name: 'Metered' })],
    })
    const order = sortMarketCards(cards, 'price', 'asc').map((card) => card.key)
    expect(order.indexOf('glm53')).toBeLessThan(order.indexOf('deepseekv41flash'))
    expect(order.indexOf('gpt6astra')).toBeLessThan(order.indexOf('metered'))
    expect(order.indexOf('metered')).toBeLessThan(order.indexOf('nopriced'))
  })

  it('无样本的 TTFT 是 0：卡片保留 0，页面负责显示 —', () => {
    const [card] = toMarketCards({ ...marketPayload, items: [meteredItem()] })
    expect(card.ttfbMs).toBe(0)
  })
})

describe('平台并集与定价去重', () => {
  it('旧后端：platforms 只有流量平台 → 合并定价里的平台', () => {
    const [card] = toMarketCards({ ...marketPayload, items: [meteredItem()] })
    expect(card.platforms).toEqual(['marvis', 'loomy'])
    expect(card.platformLabels).toEqual(['腾讯 Marvis', 'Loomy'])
    expect(card.platformCount).toBe(2)
  })

  it('只有定价、没有流量的平台也算进来（requests=0 的平台不丢）', () => {
    const merged = marketPlatforms(
      { platforms: [], platform_labels: [] },
      [{ platform: 'trae', platform_label: 'Trae 国内版', model_id: 'x', multiplier: 1, free: false }],
    )
    expect(merged).toEqual({ ids: ['trae'], labels: ['Trae 国内版'] })
  })

  it('新后端：platforms 已是并集时不重复', () => {
    const [card] = toMarketCards({
      ...marketPayload,
      items: [meteredItem({ platforms: ['loomy', 'marvis'], platform_labels: ['Loomy', '腾讯 Marvis'] })],
    })
    expect(card.platforms).toEqual(['loomy', 'marvis'])
  })

  it('定价按 (platform, realm, model_id) 去重；realm 不同的 Trae 两区各留一条', () => {
    const rows = dedupePricing([
      { platform: 'marvis', platform_label: 'Marvis', model_id: 'a', multiplier: 1, free: false },
      { platform: 'marvis', platform_label: 'Marvis', model_id: 'a', multiplier: 1, free: false },
      { platform: 'trae', platform_label: 'Trae 国内版', model_id: 'b', multiplier: 1, free: false, realm: 'cn' },
      { platform: 'trae', platform_label: 'Trae 国际版', model_id: 'b', multiplier: 1, free: false, realm: 'intl' },
    ])
    expect(rows).toHaveLength(3)
    expect(dedupePricing(null)).toEqual([])
  })
})
