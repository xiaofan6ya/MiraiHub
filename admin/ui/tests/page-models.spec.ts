/**
 * S12 模型配置：数据转换 / 筛选排序分页 / 编辑计划 的单测。
 *
 * 数据形状全部照抄后端真实响应：
 *   · GET /api/models/configs     → admin/routers/models.py:list_configs
 *   · GET /api/stats/model-health → admin/health.py:_model_health_uncached
 *   · PATCH /api/models/{id}      → admin/routers/models.py:ModelToggleIn
 * 契约见 docs/ui-v2/contracts/models.md。
 */
import { describe, expect, it } from 'vitest'
import {
  MODEL_ALIAS_LIMIT,
  buildModelEditPlan,
  capabilityKeys,
  describeSpeedTest,
  describeSyncResult,
  filterModelRows,
  fmtMultiplier,
  fmtTokensShort,
  modelEditForm,
  modelGroupKey,
  modelStats,
  multiplierTone,
  paginateModelRows,
  sortModelRows,
  squashModelName,
  toModelRows,
  vendorOf,
  type ModelConfigItem,
  type ModelHealthItem,
  type ModelRow,
} from '@/api/models'

/** list_configs 的一条 item（默认值 = WorkBuddy 上的 DeepSeek V4.1 Flash）。 */
function makeItem(overrides: Partial<ModelConfigItem> = {}): ModelConfigItem {
  return {
    id: 1,
    level: 'system',
    platform: 'workbuddy',
    realm: '',
    platform_label: 'WorkBuddy',
    model_id: 'deepseek-v4.1-flash',
    client_id: 'deepseek-v4.1-flash-workbuddy',
    upstream_id: 'deepseek-v4.1-flash',
    caps: {
      context_window: 128000,
      max_output_tokens: 16000,
      supports_images: false,
      supports_reasoning: true,
      supports_tools: true,
    },
    enabled: true,
    note: 'DeepSeek V4.1 Flash',
    sort: null,
    description: '深度求索正式版模型。',
    metadata: { name: 'DeepSeek-V4.1-Flash', tags: ['reasoning'], maxInputTokens: 128000 },
    model_type: 'text',
    tags: ['reasoning', 'text'],
    billing_mode: 'token_multiplier',
    credit_multiplier: 1.0,
    credits_raw: 'x1.00',
    is_free: false,
    plan_free: null,
    video_capability: null,
    official_visible: true,
    official_order: 0,
    created_at: '2026-11-01T12:00:00',
    ...overrides,
  }
}

/**
 * 5 条平台配置 → 3 个模型：
 *   · DeepSeek-V4.1-Flash：workbuddy + loomy（展示名带 (x2.0)）+ qoder-intl（内部代号 dfmodel）
 *   · DeepSeek-Flash：Qoder 的另一个代号（展示名不同 → 独立一行）
 *   · Kling-Video-V3：视频模型，无倍率、无健康数据
 */
const items: ModelConfigItem[] = [
  makeItem(),
  makeItem({
    id: 2,
    platform: 'loomy',
    platform_label: 'Loomy',
    client_id: 'deepseek-v4.1-flash-loomy',
    credit_multiplier: -1,
    credits_raw: '',
    billing_mode: 'upstream_token',
    metadata: { name: 'DeepSeek V4.1 Flash(x2.0)' },
    caps: { context_window: 256000, supports_images: true },
    enabled: false,
  }),
  makeItem({
    id: 3,
    platform: 'qoder-intl',
    platform_label: 'Qoder 国际版',
    realm: 'intl',
    model_id: 'deepseek-flash-qoder-intl',
    client_id: 'deepseek-flash-qoder-intl',
    upstream_id: 'dfmodel',
    credit_multiplier: 0.5,
    credits_raw: '0.5',
    note: '',
    metadata: { name: 'DeepSeek-V4.1-Flash' },
    caps: { context_window: 64000, max_output_tokens: 8000 },
    official_visible: false,
    tags: [],
  }),
  makeItem({
    id: 4,
    platform: 'qoder',
    platform_label: 'Qoder',
    model_id: 'deepseek-flash-qoder',
    client_id: 'deepseek-flash-qoder',
    upstream_id: 'dmodel',
    credit_multiplier: 1.2,
    credits_raw: '1.2',
    note: '',
    metadata: { name: 'DeepSeek-Flash' },
    caps: { context_window: 128000, max_output_tokens: 16000 },
    description: '',
    official_visible: false,
  }),
  makeItem({
    id: 5,
    platform: 'trae-cn',
    platform_label: 'Trae 中国版',
    model_id: 'kling-video-v3',
    client_id: 'kling-video-v3-trae-cn',
    upstream_id: 'kling-video-v3',
    credit_multiplier: null,
    model_type: 'video',
    tags: ['text-to-video', 'video'],
    note: 'Kling Video V3',
    metadata: { name: 'Kling-Video-V3' },
    caps: {},
    description: '视频生成模型。',
  }),
]

/** /api/stats/model-health 的一条 item。 */
const healthItems: ModelHealthItem[] = [
  {
    model: 'deepseek-v4.1-flash',
    enabled: true,
    free: false,
    eligible_accounts: 5,
    available_accounts: 3,
    limited_accounts: 2,
    earliest_model_recovery: '2026-11-23T12:00:00Z',
    limit_reasons: { model_daily: 2 },
    requests: 120,
    successes: 114,
    failures: 6,
    success_rate: 0.95,
    failure_rate: 0.05,
    failure_reasons: { rate_limit: 6 },
    ttfb_ms: { p50: 900, p95: 3200 },
    latency_ms: { p50: 2400, p95: 9000 },
    tps: { p50: 120, p95: 180, mean: 118.4, samples: 96 },
    latency_sample_count: 96,
  },
]

const rows = toModelRows(items, healthItems)

function rowByName(name: string): ModelRow {
  const row = rows.find((item) => item.name === name)
  if (!row) throw new Error('missing row ' + name)
  return row
}

describe('toModelRows：跨平台合成一行', () => {
  it('按上游展示名归一：三平台合并，展示名不同则各自成行', () => {
    expect(rows.map((row) => row.key)).toEqual([
      'deepseekv41flash',
      'deepseekflash',
      'klingvideov3',
    ])
    const deepseek = rows[0]
    expect(deepseek.platformCount).toBe(3)
    expect(deepseek.platforms.map((ref) => ref.platform)).toEqual([
      'qoder-intl',
      'workbuddy',
      'loomy',
    ])
    expect(deepseek.configIds).toEqual([3, 1, 2])
  })

  it('展示名取 note → 上游展示名；调用名取首个启用且最便宜的落点', () => {
    const deepseek = rows[0]
    expect(deepseek.name).toBe('DeepSeek V4.1 Flash')
    expect(deepseek.callId).toBe('deepseek-flash-qoder-intl')
    expect(deepseek.vendor).toBe('deepseek')
    expect(deepseek.vendorLabel).toBe('深度求索')
    expect(deepseek.aliases).toEqual([
      'dfmodel',
      'deepseek-v4.1-flash-workbuddy',
      'deepseek-v4.1-flash',
      'deepseek-v4.1-flash-loomy',
    ])
    expect(deepseek.aliasOverflow).toBe(deepseek.aliases.length - MODEL_ALIAS_LIMIT)
    expect(deepseek.aliasOverflow).toBe(1)
  })

  it('限制取各平台最小正值；倍率取最小正倍率；能力取并集', () => {
    const deepseek = rows[0]
    expect(deepseek.contextWindow).toBe(64000)
    expect(deepseek.maxOutputTokens).toBe(8000)
    expect(deepseek.multiplier).toBe(0.5)
    expect(deepseek.multiplierText).toBe('0.50x')
    expect(deepseek.capabilityKeys).toEqual(['image', 'tools', 'reasoning'])
  })

  it('启用数 / 部分启用 / 健康数据按上游 id 挂载', () => {
    const deepseek = rows[0]
    expect(deepseek.enabledCount).toBe(2)
    expect(deepseek.disabledCount).toBe(1)
    expect(deepseek.enabled).toBe(false)
    expect(deepseek.partiallyEnabled).toBe(true)
    expect(deepseek.ttftP50).toBe(900)
    expect(deepseek.successRate).toBe(0.95)
    expect(deepseek.availableAccounts).toBe(3)
    expect(deepseek.eligibleAccounts).toBe(5)
    expect(deepseek.requests).toBe(120)
    expect(deepseek.upstreamIds).toEqual(['dfmodel', 'deepseek-v4.1-flash'])
  })

  it('媒体模型：不可测速、能力带 video、无健康数据时为 null', () => {
    const video = rowByName('Kling Video V3')
    expect(video.capabilityKeys).toEqual(['video'])
    expect(video.speedTestable).toBe(false)
    expect(video.health).toBeNull()
    expect(video.multiplier).toBeNull()
    expect(video.multiplierText).toBe('—')
  })

  it('官方同显与免费标记按组内任一条命中', () => {
    expect(rows[0].officialVisible).toBe(true)
    expect(rows[1].officialVisible).toBe(false)
  })

  it('空输入与脏数据不抛异常', () => {
    expect(toModelRows(null)).toEqual([])
    expect(toModelRows([])).toEqual([])
    const [row] = toModelRows([
      { id: 9, upstream_id: 'x', model_id: 'x' } as unknown as ModelConfigItem,
    ])
    expect(row.platformCount).toBe(1)
    expect(row.name).toBe('x')
    expect(row.capabilityKeys).toEqual([])
    expect(row.multiplier).toBeNull()
    expect(row.availableAccounts).toBeNull()
  })
})

describe('展示口径纯函数', () => {
  it('fmtMultiplier：正数两位小数 / 0 免费 / 负数量纲 / null 破折号', () => {
    expect(fmtMultiplier(1.5)).toBe('1.50x')
    expect(fmtMultiplier(0.8)).toBe('0.80x')
    expect(fmtMultiplier(0)).toBe('免费')
    expect(fmtMultiplier(-1)).toBe('无量纲')
    expect(fmtMultiplier(null)).toBe('—')
    expect(fmtMultiplier(undefined)).toBe('—')
  })

  it('multiplierTone：>1 warn / <1 ok / 0 ok / 其它 neutral-muted', () => {
    expect(multiplierTone(1.2)).toBe('warn')
    expect(multiplierTone(0.5)).toBe('ok')
    expect(multiplierTone(0)).toBe('ok')
    expect(multiplierTone(1)).toBe('neutral')
    expect(multiplierTone(-1)).toBe('muted')
    expect(multiplierTone(null)).toBe('muted')
  })

  it('fmtTokensShort：K / M 与非法值', () => {
    expect(fmtTokensShort(128000)).toBe('128K')
    expect(fmtTokensShort(1048576)).toBe('1M')
    expect(fmtTokensShort(1500000)).toBe('1.5M')
    expect(fmtTokensShort(800)).toBe('800')
    expect(fmtTokensShort(0)).toBe('—')
    expect(fmtTokensShort(null)).toBe('—')
  })

  it('capabilityKeys：模型类型在前，能力布尔在后', () => {
    expect(
      capabilityKeys({ supports_images: true, supports_tools: true, supports_reasoning: true }),
    ).toEqual(['image', 'tools', 'reasoning'])
    expect(capabilityKeys({}, 'image')).toEqual(['image-out'])
    expect(capabilityKeys(null, 'video')).toEqual(['video'])
    expect(capabilityKeys({})).toEqual([])
  })

  it('squashModelName / modelGroupKey 抹平倍率后缀与标点', () => {
    expect(squashModelName('DeepSeek V4.1 Flash(x2.0)')).toBe('deepseekv41flash')
    expect(squashModelName('MiniMax M3 （x4.0）')).toBe('minimaxm3')
    expect(squashModelName('DeepSeek-V4.1-Flash x2.0')).toBe('deepseekv41flash')
    expect(modelGroupKey(items[1])).toBe('deepseekv41flash')
    expect(modelGroupKey(items[3])).toBe('deepseekflash')
  })

  it('vendorOf：中文厂商名与常见别名', () => {
    expect(vendorOf('豆包').key).toBe('doubao')
    expect(vendorOf('通义千问').key).toBe('qwen')
    expect(vendorOf('deepseek-v4.1-flash').key).toBe('deepseek')
    expect(vendorOf('glm-5.3').key).toBe('glm')
    expect(vendorOf('unknown-model').key).toBe('default')
  })
})

describe('筛选 / 排序 / 分页 / 汇总', () => {
  it('关键词命中调用名、平台名与能力中文名', () => {
    expect(filterModelRows(rows, { q: 'kling' }).map((row) => row.name)).toEqual([
      'Kling Video V3',
    ])
    expect(filterModelRows(rows, { q: '视频' }).map((row) => row.name)).toEqual([
      'Kling Video V3',
    ])
    expect(filterModelRows(rows, { q: '国际版' }).map((row) => row.name)).toEqual([
      'DeepSeek V4.1 Flash',
    ])
    expect(filterModelRows(rows, { q: '深度求索' })).toHaveLength(2)
  })

  it('厂商 / 平台 / 能力 / 状态筛选', () => {
    expect(filterModelRows(rows, { vendor: 'deepseek' })).toHaveLength(2)
    expect(filterModelRows(rows, { platform: 'trae-cn' })).toHaveLength(1)
    expect(filterModelRows(rows, { cap: 'tools' }).map((row) => row.name)).toEqual([
      'DeepSeek V4.1 Flash',
    ])
    expect(filterModelRows(rows, { cap: 'image' })).toHaveLength(1)
    expect(filterModelRows(rows, { status: 'enabled' })).toHaveLength(2)
    expect(filterModelRows(rows, { status: 'disabled' }).map((row) => row.name)).toEqual([
      'DeepSeek V4.1 Flash',
    ])
  })

  it('排序三态：默认原序 / 升序 / 降序', () => {
    const defaultOrder = rows.map((row) => row.name)
    expect(sortModelRows(rows, 'name', '').map((row) => row.name)).toEqual(defaultOrder)
    expect(sortModelRows(rows, 'name', 'asc').map((row) => row.name)).toEqual([
      'DeepSeek V4.1 Flash',
      'DeepSeek-Flash',
      'Kling Video V3',
    ])
    expect(sortModelRows(rows, 'name', 'desc').map((row) => row.name)).toEqual([
      'Kling Video V3',
      'DeepSeek-Flash',
      'DeepSeek V4.1 Flash',
    ])
    expect(sortModelRows(rows, 'bogus', 'asc').map((row) => row.name)).toEqual(defaultOrder)
    expect(sortModelRows(rows, 'multiplier', 'asc').map((row) => row.name)).toEqual([
      'DeepSeek V4.1 Flash',
      'DeepSeek-Flash',
      'Kling Video V3',
    ])
  })

  it('分页夹紧页码与每页条数', () => {
    const first = paginateModelRows(rows, 1, 2)
    expect(first.total).toBe(3)
    expect(first.pageCount).toBe(2)
    expect(first.items).toHaveLength(2)
    expect(paginateModelRows(rows, 99, 2).page).toBe(2)
    expect(paginateModelRows(rows, 0, 2).page).toBe(1)
    expect(paginateModelRows([], 3, 50)).toEqual({ items: [], total: 0, pageCount: 0, page: 1 })
    expect(paginateModelRows(rows, 1, 0).items).toHaveLength(3)
  })

  it('汇总：模型数 / 平台配置数 / 启用数 / 部分停用 / 无可用账号', () => {
    expect(modelStats(rows)).toEqual({
      total: 3,
      configs: 5,
      enabled: 2,
      partial: 1,
      unavailable: 0,
    })
  })
})

describe('编辑计划：行 + 表单 → PATCH 列表', () => {
  const deepseek = rows[0]

  it('没改动时 changed=false，不产生请求', () => {
    const plan = buildModelEditPlan(deepseek, modelEditForm(deepseek))
    expect(plan.changed).toBe(false)
    expect(plan.updates).toEqual([])
    expect(plan.error).toBe('')
  })

  it('改展示名 → 每个平台各一条，且都带 enabled（后端默认 True）', () => {
    const form = { ...modelEditForm(deepseek), note: 'DeepSeek 闪电版' }
    const plan = buildModelEditPlan(deepseek, form)
    expect(plan.updates).toHaveLength(3)
    expect(plan.updates[0]).toEqual({
      id: 3,
      body: { enabled: true, note: 'DeepSeek 闪电版' },
    })
    expect(plan.updates[2]).toEqual({
      id: 2,
      body: { enabled: false, note: 'DeepSeek 闪电版' },
    })
  })

  it('只切某个平台的开关 → 只提交那一条', () => {
    const form = modelEditForm(deepseek)
    form.enabled[2] = true
    const plan = buildModelEditPlan(deepseek, form)
    expect(plan.updates).toEqual([{ id: 2, body: { enabled: true } }])
  })

  it('改能力 → caps 携带数值与布尔覆盖', () => {
    const form = { ...modelEditForm(deepseek), contextWindow: 200000, supportsImages: true }
    const plan = buildModelEditPlan(deepseek, form)
    expect(plan.updates).toHaveLength(3)
    expect(plan.updates[0].body.caps).toEqual({
      context_window: 200000,
      max_output_tokens: 8000,
      supports_images: true,
      supports_tools: true,
      supports_reasoning: true,
    })
  })

  it('全部平台停用 → 报错且不提交', () => {
    const form = modelEditForm(deepseek)
    for (const ref of deepseek.platforms) form.enabled[ref.configId] = false
    const plan = buildModelEditPlan(deepseek, form)
    expect(plan.error).toBe('至少要保留一个平台处于启用状态')
    expect(plan.updates).toEqual([])
    expect(plan.changed).toBe(false)
  })
})

describe('结果文案', () => {
  it('describeSyncResult 拼新增/更新/总数与问题平台', () => {
    expect(
      describeSyncResult({
        total_fetched: 42,
        added: 5,
        updated: 3,
        total_in_db: 42,
        enabled: 30,
        platforms: {},
        problems: { qoder: '该平台没有可用（active）账号' },
      }),
    ).toBe('+5 新增，~3 更新，共 42 个；未取到：qoder')
    expect(describeSyncResult(null)).toBe('同步完成')
  })

  it('describeSpeedTest 拼首字/速度/token/账号', () => {
    expect(
      describeSpeedTest({
        model: 'deepseek-v4.1-flash',
        health: 'healthy',
        observed_at: '2026-11-23T03:00:00Z',
        headers_ms: 320.5,
        inter_delta_jitter_ms: 12.3,
        jitter_samples: 40,
        jitter_scope: 'inter_delta_including_generation',
        prompt_tokens: 150,
        account: 'account-123',
        ttft_ms: 450.4,
        completion_tokens: 500,
        total_tokens: 650,
        gen_duration_ms: 3980,
        total_duration_ms: 4430,
        speed_tps: 125.5,
        overall_tps: 112.9,
        sample: '我是一个模型。',
      }),
    ).toBe('首字 450ms · 125.5 t/s · 提示 150 / 补全 500 tok · 账号 account-123')
  })
})
