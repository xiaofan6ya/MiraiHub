/**
 * 模型配置（/models）的接口层 + 纯函数。
 *
 * 后端：
 *   admin/routers/models.py        list_configs / toggle_model / batch_toggle /
 *                                 sync_models / delete_config / speed_test /
 *                                 model_display / save_model_display
 *   admin/health.py                model_health（经 /api/stats/model-health 暴露）
 *   admin/platform/models_router.py _capabilities_of（caps 字段来源）
 *   model_metadata.py              type_tags / model_type（tags 字段来源）
 *   admin/model_alias.py           model_display（上游官方展示名，归一依据）
 * 契约：docs/ui-v2/contracts/models.md
 *
 * 字段名与后端保持一致（snake_case），不在页面里做二次换算：
 *   · 展示名是 `note`（PATCH 的 note 就是它），不是 `name`；
 *   · 可读调用名 `model_id`、完整调用名 `client_id`（总带平台后缀）、
 *     上游原始 id `upstream_id` 三者含义不同，别混用；
 *   · 倍率 `credit_multiplier` 原样输出：>0 收费 / =0 免费 / <0 无量纲 / null 未同步；
 *   · 能力在 `caps`（context_window / max_output_tokens / supports_* / reasoning_efforts）。
 *
 * 页面只消费这里导出的行数据与筛选/排序/分页纯函数，单测直接覆盖它们。
 */
import { http } from './http'

/* ------------------------------------------------------------------ *
 * 接口类型
 * ------------------------------------------------------------------ */

/** `caps` 的已知键（admin/platform/models_router.py:_capabilities_of）。 */
export interface ModelCapabilities {
  context_window?: number
  max_output_tokens?: number
  supports_images?: boolean
  supports_tools?: boolean
  supports_reasoning?: boolean
  can_disable_thinking?: boolean
  reasoning_efforts?: string[]
  default_reasoning_effort?: string
  /** true = 这些值里至少一项是本站手调覆盖（`_override`），不是上游原值。 */
  overridden?: boolean
}

/** GET /api/models/configs 的 items[] 一项。 */
export interface ModelConfigItem {
  /** model_configs.id（PATCH / DELETE 用）。 */
  id: number
  level: string
  /** 区级平台 id（qoder / qoder-intl / trae-cn / trae-intl / workbuddy…）。 */
  platform: string
  realm: string
  platform_label: string
  /** 可读调用名（/v1/models 口径：单区平台不带后缀）。 */
  model_id: string
  /** 完整调用名，总是带平台后缀；复制给客户端用这个。 */
  client_id: string
  /** 上游原始 id（= model_configs.model_id，分组/测速都用它）。 */
  upstream_id: string
  caps: ModelCapabilities
  enabled: boolean
  /** 展示名（可能是中文，用户可改）。 */
  note: string
  sort: number | null
  description: string
  /** upstream_metadata 原样（含 name / tags / maxInputTokens …）。 */
  metadata: Record<string, unknown> | null
  model_type: string
  tags: string[]
  billing_mode: string
  credit_multiplier: number | null
  credits_raw: string
  is_free: boolean
  plan_free: boolean | null
  video_capability: Record<string, unknown> | null
  official_visible: boolean
  official_order: number | null
  created_at: string | null
}

/** 平台分组汇总（后端按区级平台算好，前端不重复聚合）。 */
export interface ModelPlatformSummary {
  platform: string
  label: string
  count: number
  enabled: number
}

export interface ModelConfigListPayload {
  official_total: number
  platforms: ModelPlatformSummary[]
  items: ModelConfigItem[]
}

/** GET/PUT /api/models/display（admin/model_listing.py:display_options）。 */
export interface ModelDisplayPayload {
  show_multiplier: boolean
  show_busy: boolean
  official_only: boolean
}

export interface ModelDisplayInput {
  show_multiplier: boolean
  show_busy: boolean
  official_only: boolean
}

/** PATCH /api/models/{id} 的请求体（ModelToggleIn）。 */
export interface ModelUpdateInput {
  /** 后端有默认 True：不传会顺手把模型启用，所以每次都必须显式给值。 */
  enabled: boolean
  /** 展示名；空串 = 回退到 model_id。 */
  note?: string
  /** 能力覆盖；键传 null 表示恢复上游默认，整体传 {} 表示全部恢复。 */
  caps?: Record<string, unknown> | null
  sort?: number
}

export interface ModelUpdatePayload {
  id: number
  model_id: string
  enabled: boolean
  note: string
  caps: ModelCapabilities
  sort: number | null
}

export interface ModelBatchInput {
  /** 上游模型 id 列表；空数组 = 该 level 下全部。 */
  model_ids?: string[]
  enabled: boolean
  level?: 'system' | 'user'
}

export interface ModelBatchPayload {
  updated: number
  enabled: boolean
}

export interface ModelDeletePayload {
  id: number
  model_id: string
  ok: boolean
}

/** POST /api/models/sync 的每平台明细。 */
export interface ModelSyncPlatformStat {
  platform: string
  fetched: number
  added: number
  updated: number
}

export interface ModelSyncPayload {
  total_fetched: number
  added: number
  updated: number
  total_in_db: number
  enabled: number
  platforms: Record<string, ModelSyncPlatformStat>
  /** 没取到目录的平台 → 原因（「该平台没有可用（active）账号」等）。 */
  problems: Record<string, string>
}

/** POST /api/models/speed-test（admin/routers/models.py:_speed_test_run）。 */
export interface ModelSpeedTestPayload {
  model: string
  health: string
  observed_at: string
  headers_ms: number | null
  inter_delta_jitter_ms: number | null
  jitter_samples: number
  jitter_scope: string
  prompt_tokens: number | null
  account: string
  ttft_ms: number | null
  completion_tokens: number | null
  total_tokens: number | null
  gen_duration_ms: number | null
  total_duration_ms: number | null
  speed_tps: number | null
  overall_tps: number | null
  sample: string
}

/** GET /api/stats/model-health 的分位数结构（admin/health.py:_percentiles）。 */
export interface ModelPercentiles {
  p50: number | null
  p95: number | null
}

export interface ModelTpsStats extends ModelPercentiles {
  mean?: number | null
  samples?: number
}

export interface ModelHealthItem {
  /** = model_configs.model_id（上游 id）。 */
  model: string
  enabled: boolean
  free: boolean
  eligible_accounts: number
  available_accounts: number
  limited_accounts: number
  earliest_model_recovery: string | null
  limit_reasons: Record<string, number>
  requests: number
  successes: number
  failures: number
  success_rate: number | null
  failure_rate: number | null
  failure_reasons: Record<string, number>
  ttfb_ms: ModelPercentiles
  latency_ms: ModelPercentiles
  tps: ModelTpsStats
  latency_sample_count: number
}

export interface ModelHealthPayload {
  items: ModelHealthItem[]
  range_hours: number
  observed_at: string
  usage_scope: string
}

/* ------------------------------------------------------------------ *
 * 接口函数
 * ------------------------------------------------------------------ */

/** 全部平台的模型配置（后端一次返回所有平台，不分页）。 */
export function listModelConfigs(signal?: AbortSignal): Promise<ModelConfigListPayload> {
  return http<ModelConfigListPayload>('GET', '/api/models/configs', signal ? { signal } : {})
}

/** 展示偏好（倍率 / 繁忙度 / 官方同显）。 */
export function getModelDisplay(signal?: AbortSignal): Promise<ModelDisplayPayload> {
  return http<ModelDisplayPayload>('GET', '/api/models/display', signal ? { signal } : {})
}

/** 保存展示偏好（后端是 PUT，不是 POST）。 */
export function saveModelDisplay(input: ModelDisplayInput): Promise<ModelDisplayPayload> {
  return http<ModelDisplayPayload>('PUT', '/api/models/display', { body: input })
}

/** 改单个模型配置：启停 / 展示名 / 能力覆盖 / 排序。 */
export function updateModelConfig(id: number, body: ModelUpdateInput): Promise<ModelUpdatePayload> {
  return http<ModelUpdatePayload>('PATCH', '/api/models/' + encodeURIComponent(String(id)), {
    body,
  })
}

/** 批量启用/禁用（按 level，可再按上游模型 id 收窄）。 */
export function batchToggleModels(body: ModelBatchInput): Promise<ModelBatchPayload> {
  return http<ModelBatchPayload>('POST', '/api/models/batch-toggle', { body })
}

/** 从后端同步模型目录；platform 传**落库平台**（qoder / trae / workbuddy）。 */
export function syncModels(platform?: string): Promise<ModelSyncPayload> {
  return http<ModelSyncPayload>('POST', '/api/models/sync', {
    query: platform ? { platform } : undefined,
  })
}

/** 删除一条模型配置（= 从白名单移除）。 */
export function deleteModelConfig(id: number): Promise<ModelDeletePayload> {
  return http<ModelDeletePayload>('DELETE', '/api/models/' + encodeURIComponent(String(id)))
}

/** 用账号池对上游发一次真实请求测速（可能产生积分消耗）。 */
export function speedTestModel(model: string): Promise<ModelSpeedTestPayload> {
  return http<ModelSpeedTestPayload>('POST', '/api/models/speed-test', { body: { model } })
}

/** 最近 N 小时的模型健康（TTFT / 成功率 / 可用账号）。 */
export function listModelHealth(hours = 24, signal?: AbortSignal): Promise<ModelHealthPayload> {
  return http<ModelHealthPayload>('GET', '/api/stats/model-health', {
    query: { hours },
    ...(signal ? { signal } : {}),
  })
}

/* ------------------------------------------------------------------ *
 * 厂商 / 倍率 / 容量：展示口径
 * ------------------------------------------------------------------ */

export type CapabilityKey = 'image' | 'tools' | 'reasoning' | 'video' | 'image-out'

export const CAPABILITY_LABELS: Record<CapabilityKey, string> = {
  image: '图片输入',
  tools: '工具调用',
  reasoning: '推理',
  video: '视频',
  'image-out': '生图',
}

export type CapabilityTone = 'neutral' | 'brand' | 'warn'

/** 厂商 key → 中文名。key 与 components/identity/modelIcons.ts 的图标键一致。 */
export const VENDOR_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  claude: 'Anthropic',
  gemini: 'Google',
  deepseek: '深度求索',
  qwen: '通义千问',
  glm: '智谱',
  kimi: '月之暗面',
  doubao: '豆包',
  hunyuan: '混元',
  minimax: 'MiniMax',
  kling: '可灵',
  stepfun: '阶跃',
  grok: 'xAI',
  default: '其他',
}

/**
 * 厂商识别：匹配顺序与 components/identity/modelIconResolve.ts 的 VENDOR_ALIASES 一致。
 * 这里独立实现一份，是为了让 api 层不反向依赖组件库（页面拿 vendorKey 直接喂 ModelIcon）。
 */
const VENDOR_PATTERNS: Array<[RegExp, string]> = [
  [/(^|[^a-z])gpt|o1|o3|chatgpt/, 'openai'],
  [/gemini|bard/, 'gemini'],
  [/grok|xai/, 'grok'],
  [/qwen|qwq|tongyi|通义/, 'qwen'],
  [/doubao|seed[_-]?m|volc|bytedance|豆包/, 'doubao'],
  [/kling|可灵/, 'kling'],
  [/step[_-]?\d|stepfun|阶跃/, 'stepfun'],
  [/glm|chatglm|zhipu/, 'glm'],
  [/kimi|moonshot/, 'kimi'],
  [/deepseek/, 'deepseek'],
  [/claude|anthropic/, 'claude'],
  [/minimax|abab|mimo/, 'minimax'],
  [/hunyuan|hy\d|tencent|混元/, 'hunyuan'],
]

export interface VendorInfo {
  key: string
  label: string
}

/** 模型名 / 展示名 → 厂商（识别不到就是 default/其他）。 */
export function vendorOf(...texts: Array<string | null | undefined>): VendorInfo {
  const haystack = texts.map((item) => String(item || '')).join(' ').toLowerCase()
  for (const [pattern, key] of VENDOR_PATTERNS) {
    if (pattern.test(haystack)) {
      return { key, label: VENDOR_LABELS[key] || key }
    }
  }
  return { key: 'default', label: VENDOR_LABELS.default }
}

/** 倍率文本：1.50x / 0.80x / 免费 / 无量纲 / —。 */
export function fmtMultiplier(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  if (n < 0) return '无量纲'
  if (n === 0) return '免费'
  return n.toFixed(2) + 'x'
}

export type MultiplierTone = 'ok' | 'warn' | 'neutral' | 'muted'

/** 倍率语义色：>1 偏贵 warn，<1 优惠 ok，0 免费 ok，负值/缺值 muted。 */
export function multiplierTone(value: number | null | undefined): MultiplierTone {
  if (value === null || value === undefined) return 'muted'
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return 'muted'
  if (n === 0) return 'ok'
  if (n > 1) return 'warn'
  if (n < 1) return 'ok'
  return 'neutral'
}

/** 上下文 / 输出上限：131072 → 128K，1048576 → 1M。 */
export function fmtTokensShort(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '—'
  if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M'
  if (n >= 1000) return Math.round(n / 1000) + 'K'
  return String(Math.round(n))
}

/** 能力键（顺序固定，页面按序渲染小图标；单测按此断言）。 */
export function capabilityKeys(caps: ModelCapabilities | null | undefined, modelType?: string): CapabilityKey[] {
  const keys: CapabilityKey[] = []
  const type = String(modelType || 'text').toLowerCase()
  if (type === 'video') keys.push('video')
  if (type === 'image') keys.push('image-out')
  if (caps?.supports_images === true) keys.push('image')
  if (caps?.supports_tools === true) keys.push('tools')
  if (caps?.supports_reasoning === true) keys.push('reasoning')
  return Array.from(new Set(keys))
}

/* ------------------------------------------------------------------ *
 * 响应 → 表格行
 * ------------------------------------------------------------------ */

/** 一条模型在某平台的配置（抽屉里的「各平台映射」一行）。 */
export interface ModelPlatformRef {
  configId: number
  platform: string
  platformLabel: string
  realm: string
  /** 完整调用名（复制用）。 */
  clientId: string
  /** 可读调用名（/v1/models 口径）。 */
  modelId: string
  upstreamId: string
  multiplier: number | null
  billingMode: string
  enabled: boolean
  caps: ModelCapabilities
  modelType: string
}

export interface ModelRow extends Record<string, unknown> {
  /** 分组键（展示名归一后的串）。 */
  key: string
  /** 展示名：note → 上游展示名 → 可读调用名。 */
  name: string
  vendor: string
  vendorLabel: string
  /** 主调用名（复制按钮用）。 */
  callId: string
  /** 其他调用名（别名 chips）。 */
  aliases: string[]
  aliasOverflow: number
  caps: ModelCapabilities
  capabilityKeys: CapabilityKey[]
  contextWindow: number | null
  maxOutputTokens: number | null
  /** 组内最优倍率（>0 取最小；全 0 取 0；只有负值取 -1；全 null 取 null）。 */
  multiplier: number | null
  multiplierText: string
  billingMode: string
  platforms: ModelPlatformRef[]
  platformCount: number
  enabledCount: number
  disabledCount: number
  enabled: boolean
  partiallyEnabled: boolean
  modelType: string
  officialVisible: boolean
  free: boolean
  /** 各平台上有的上游 id（批量/测速用）。 */
  upstreamIds: string[]
  configIds: number[]
  health: ModelHealthItem | null
  ttftP50: number | null
  successRate: number | null
  requests: number
  availableAccounts: number | null
  eligibleAccounts: number | null
  note: string
  description: string
  tags: string[]
  /** 是否能测速（媒体模型后端会 400）。 */
  speedTestable: boolean
}

/** 别名 chips 最多显示几个（多出来的折叠成 +N）。 */
export const MODEL_ALIAS_LIMIT = 3

function text(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value).trim()
}

function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** 展示名归一：去倍率后缀（(x2.0) / x2.0）、去标点空白、转小写。 */
export function squashModelName(value: unknown): string {
  return String(value || '')
    .toLowerCase()
    .replace(/[（(]\s*[x×]\s*[\d.]+\s*[)）]/g, '')
    .replace(/\s+[x×][\d.]+\s*$/g, '')
    .replace(/[^a-z0-9]+/g, '')
}

/** 上游官方展示名（model_alias.model_display 的口径：metadata.name / display_name）。 */
export function upstreamDisplayName(item: ModelConfigItem): string {
  const meta = item.metadata
  if (!meta || typeof meta !== 'object') return ''
  const record = meta as Record<string, unknown>
  return text(record.name) || text(record.display_name)
}

/** 分组键：优先上游展示名（跨平台同名），否则退回上游 id。 */
export function modelGroupKey(item: ModelConfigItem): string {
  return squashModelName(upstreamDisplayName(item)) || squashModelName(item.upstream_id)
}

function pickMultiplier(values: Array<number | null>): number | null {
  const positives = values.filter((value): value is number => value !== null && value > 0)
  if (positives.length) return Math.min(...positives)
  if (values.some((value) => value !== null && value === 0)) return 0
  if (values.some((value) => value !== null && value < 0)) return -1
  return null
}

function minPositive(values: Array<number | null>): number | null {
  const positives = values.filter((value): value is number => value !== null && value > 0)
  return positives.length ? Math.min(...positives) : null
}

/** 主平台排序：启用优先 → 倍率小的优先 → id 小的优先（保证结果稳定）。 */
function primaryRank(ref: ModelPlatformRef): [number, number, number] {
  return [
    ref.enabled ? 0 : 1,
    typeof ref.multiplier === 'number' && ref.multiplier >= 0 ? ref.multiplier : 999,
    ref.configId,
  ]
}

function compareRank(a: ModelPlatformRef, b: ModelPlatformRef): number {
  const ra = primaryRank(a)
  const rb = primaryRank(b)
  for (let index = 0; index < ra.length; index += 1) {
    if (ra[index] !== rb[index]) return ra[index] - rb[index]
  }
  return 0
}

function toPlatformRef(item: ModelConfigItem): ModelPlatformRef {
  return {
    configId: item.id,
    platform: text(item.platform),
    platformLabel: text(item.platform_label) || text(item.platform),
    realm: text(item.realm),
    clientId: text(item.client_id) || text(item.model_id),
    modelId: text(item.model_id),
    upstreamId: text(item.upstream_id),
    multiplier: num(item.credit_multiplier),
    billingMode: text(item.billing_mode),
    enabled: item.enabled !== false,
    caps: item.caps && typeof item.caps === 'object' ? item.caps : {},
    modelType: text(item.model_type) || 'text',
  }
}

/** 健康数据按上游 id 建索引（/api/stats/model-health 的 model 就是上游 id）。 */
export function indexHealth(
  items: ModelHealthItem[] | null | undefined,
): Map<string, ModelHealthItem> {
  const map = new Map<string, ModelHealthItem>()
  for (const item of items || []) {
    const key = text(item.model).toLowerCase()
    if (key) map.set(key, item)
  }
  return map
}

/**
 * 接口 items[] → 表格行（同一模型跨平台合成一行）。
 *
 * 归一依据与后端 model_alias.canonical_key 同源：**上游官方展示名**优先，
 * 因为 Qoder 的 `dfmodel`、Loomy 的 `DeepSeek V4.1 Flash(x2.0)` 只有展示名能对上。
 * note 是用户可改的展示名，不参与归一（后端注释里已经踩过这个坑）。
 */
export function toModelRows(
  items: ModelConfigItem[] | null | undefined,
  health?: ModelHealthItem[] | Map<string, ModelHealthItem> | null,
): ModelRow[] {
  const healthMap = health instanceof Map ? health : indexHealth(health || [])
  const buckets = new Map<string, ModelConfigItem[]>()
  const order: string[] = []

  for (const item of items || []) {
    const key = modelGroupKey(item)
    if (!key) continue
    if (!buckets.has(key)) {
      buckets.set(key, [])
      order.push(key)
    }
    buckets.get(key)!.push(item)
  }

  const rows: ModelRow[] = []
  for (const key of order) {
    const group = buckets.get(key) || []
    if (!group.length) continue
    const refs = group.map(toPlatformRef).sort(compareRank)
    const primary = refs[0]
    const primaryItem = group.find((item) => item.id === primary.configId) || group[0]

    const displayName =
      text(group.find((item) => text(item.note))?.note) ||
      text(upstreamDisplayName(group[0])) ||
      primary.modelId ||
      primary.upstreamId

    const aliasSet = new Set<string>()
    for (const ref of refs) {
      for (const value of [ref.clientId, ref.modelId, ref.upstreamId]) {
        if (!value) continue
        if (value === primary.clientId) continue
        if (value === displayName) continue
        aliasSet.add(value)
      }
    }
    const aliases = Array.from(aliasSet)
    const vendor = vendorOf(displayName, primary.upstreamId, primary.modelId)

    // 聚合口径：数值取「各平台最小值」（别把最宽的当通用值），
    // 布尔能力取「任一平台支持」，让能力图标反映这个模型组**至少能做到什么**。
    const caps: ModelCapabilities = { ...primary.caps }
    caps.context_window = minPositive(refs.map((ref) => num(ref.caps.context_window))) ?? undefined
    caps.max_output_tokens =
      minPositive(refs.map((ref) => num(ref.caps.max_output_tokens))) ?? undefined
    const flag = (
      key: 'supports_images' | 'supports_tools' | 'supports_reasoning' | 'can_disable_thinking',
    ): boolean | undefined => {
      const defined = refs.filter((ref) => typeof ref.caps[key] === 'boolean')
      if (!defined.length) return undefined
      return defined.some((ref) => ref.caps[key] === true)
    }
    for (const key of [
      'supports_images',
      'supports_tools',
      'supports_reasoning',
      'can_disable_thinking',
    ] as const) {
      const value = flag(key)
      if (value === undefined) delete caps[key]
      else caps[key] = value
    }
    const efforts = Array.from(
      new Set(refs.flatMap((ref) => (Array.isArray(ref.caps.reasoning_efforts) ? ref.caps.reasoning_efforts : []))),
    )
    if (efforts.length) caps.reasoning_efforts = efforts
    if (refs.some((ref) => ref.caps.overridden === true)) caps.overridden = true

    const enabledCount = refs.filter((ref) => ref.enabled).length
    const platformHealth =
      refs.map((ref) => healthMap.get(ref.upstreamId.toLowerCase())).find(Boolean) || null
    const multiplier = pickMultiplier(refs.map((ref) => ref.multiplier))
    const modelType = primary.modelType

    rows.push({
      key,
      name: displayName,
      vendor: vendor.key,
      vendorLabel: vendor.label,
      callId: primary.clientId,
      aliases,
      aliasOverflow: Math.max(0, aliases.length - MODEL_ALIAS_LIMIT),
      caps,
      capabilityKeys: capabilityKeys(caps, modelType),
      contextWindow: num(caps.context_window),
      maxOutputTokens: num(caps.max_output_tokens),
      multiplier,
      multiplierText: fmtMultiplier(multiplier),
      billingMode: primary.billingMode,
      platforms: refs,
      platformCount: refs.length,
      enabledCount,
      disabledCount: refs.length - enabledCount,
      enabled: enabledCount === refs.length,
      partiallyEnabled: enabledCount > 0 && enabledCount < refs.length,
      modelType,
      officialVisible: group.some((item) => item.official_visible !== false),
      free: group.some((item) => item.is_free === true),
      upstreamIds: Array.from(new Set(refs.map((ref) => ref.upstreamId).filter(Boolean))),
      configIds: refs.map((ref) => ref.configId),
      health: platformHealth,
      ttftP50: platformHealth?.ttfb_ms ? num(platformHealth.ttfb_ms.p50) : null,
      successRate: platformHealth ? num(platformHealth.success_rate) : null,
      requests: platformHealth ? Number(platformHealth.requests) || 0 : 0,
      availableAccounts: platformHealth ? Number(platformHealth.available_accounts) || 0 : null,
      eligibleAccounts: platformHealth ? Number(platformHealth.eligible_accounts) || 0 : null,
      note: text(group.find((item) => text(item.note))?.note) || '',
      description: text(primaryItem.description),
      tags: Array.from(new Set(group.flatMap((item) => (Array.isArray(item.tags) ? item.tags : [])))),
      speedTestable: modelType !== 'video' && modelType !== 'image',
    })
  }
  return rows
}

/* ------------------------------------------------------------------ *
 * 筛选 / 排序 / 分页 / 汇总
 * ------------------------------------------------------------------ */

export interface ModelFilters {
  /** 搜索：模型名 / 调用名 / 别名 / 备注 / 厂商 / 平台 / 能力中文名。 */
  q?: string
  /** 厂商 key（vendorOf 的 key）。 */
  vendor?: string
  /** 区级平台 id。 */
  platform?: string
  /** 能力 key（image / tools / reasoning / video / image-out）。 */
  cap?: string
  /** '' | 'enabled' | 'disabled'。 */
  status?: string
}

/** 筛选（大小写不敏感）。返回新数组，不修改入参。 */
export function filterModelRows(rows: ModelRow[], filters: ModelFilters = {}): ModelRow[] {
  const keyword = text(filters.q).toLowerCase()
  const vendor = text(filters.vendor)
  const platform = text(filters.platform)
  const cap = text(filters.cap)
  const status = text(filters.status)

  return rows.filter((row) => {
    if (vendor && row.vendor !== vendor) return false
    if (platform && !row.platforms.some((ref) => ref.platform === platform)) return false
    if (cap && !row.capabilityKeys.includes(cap as CapabilityKey)) return false
    if (status === 'enabled' && !row.enabled) return false
    if (status === 'disabled' && row.disabledCount === 0) return false
    if (!keyword) return true
    const haystack = [
      row.name,
      row.note,
      row.description,
      row.callId,
      ...row.aliases,
      row.vendorLabel,
      ...row.platforms.map((ref) => ref.platformLabel),
      ...row.platforms.map((ref) => ref.platform),
      ...row.upstreamIds,
      ...row.tags,
      ...row.capabilityKeys.map((key) => CAPABILITY_LABELS[key]),
      ...row.capabilityKeys,
    ]
      .filter(Boolean)
      .join('\n')
      .toLowerCase()
    return haystack.includes(keyword)
  })
}

export type ModelSortOrder = 'asc' | 'desc' | ''

/** 排序字段白名单。 */
export const MODEL_SORT_KEYS = ['name', 'multiplier', 'platforms'] as const
export type ModelSortKey = (typeof MODEL_SORT_KEYS)[number]

function compareRows(a: ModelRow, b: ModelRow, sortBy: ModelSortKey): number {
  if (sortBy === 'name') return a.name.localeCompare(b.name, 'zh-CN')
  if (sortBy === 'multiplier') {
    const left = a.multiplier === null ? 999 : a.multiplier
    const right = b.multiplier === null ? 999 : b.multiplier
    return left - right
  }
  return a.platformCount - b.platformCount
}

/**
 * 排序。`sortBy` 或 `order` 为空 → 原样返回（后端 id 顺序 = 默认）。
 * 同值时按名字升序，保证分页稳定。
 */
export function sortModelRows(rows: ModelRow[], sortBy: string, order: ModelSortOrder): ModelRow[] {
  const key = text(sortBy) as ModelSortKey
  if (!key || !MODEL_SORT_KEYS.includes(key) || (order !== 'asc' && order !== 'desc')) {
    return rows.slice()
  }
  const direction = order === 'desc' ? -1 : 1
  return rows.slice().sort((a, b) => {
    const diff = compareRows(a, b, key)
    if (diff !== 0) return diff * direction
    return a.name.localeCompare(b.name, 'zh-CN')
  })
}

export interface PaginatedModelRows {
  items: ModelRow[]
  total: number
  pageCount: number
  /** 夹紧后的页码（越界回退到最后一页）。 */
  page: number
}

/** 客户端分页（后端一次返回全部）。 */
export function paginateModelRows(
  rows: ModelRow[],
  page: number,
  pageSize: number,
): PaginatedModelRows {
  const size = Math.max(1, Math.trunc(Number(pageSize) || 50))
  const total = rows.length
  const pageCount = Math.ceil(total / size)
  const current = pageCount ? Math.min(Math.max(1, Math.trunc(Number(page) || 1)), pageCount) : 1
  const start = (current - 1) * size
  return { items: rows.slice(start, start + size), total, pageCount, page: current }
}

export interface ModelStats {
  /** 模型数（一行 = 一个跨平台模型）。 */
  total: number
  /** 平台配置条数（每条平台映射算一条）。 */
  configs: number
  enabled: number
  /** 部分平台被停用的模型数。 */
  partial: number
  /** 无可用账号（会被网关跳过）的模型数。 */
  unavailable: number
}

/** FilterBar 右侧统计。 */
export function modelStats(rows: ModelRow[]): ModelStats {
  let configs = 0
  let enabled = 0
  let partial = 0
  let unavailable = 0
  for (const row of rows) {
    configs += row.platformCount
    if (row.enabled) enabled += 1
    if (row.partiallyEnabled) partial += 1
    if (row.health && row.availableAccounts === 0 && (row.eligibleAccounts || 0) > 0) {
      unavailable += 1
    }
  }
  return { total: rows.length, configs, enabled, partial, unavailable }
}

/** 平台筛选下拉选项（来自后端 platforms 汇总）。 */
export function platformOptions(
  platforms: ModelPlatformSummary[] | null | undefined,
): Array<{ label: string; value: string }> {
  const seen = new Set<string>()
  const options: Array<{ label: string; value: string }> = [{ label: '平台：全部', value: '' }]
  for (const item of platforms || []) {
    const value = text(item.platform)
    if (!value || seen.has(value)) continue
    seen.add(value)
    options.push({
      label: (text(item.label) || value) + '（' + (Number(item.count) || 0) + '）',
      value,
    })
  }
  return options
}

/** 厂商筛选下拉选项（从行数据动态生成，按中文名排序）。 */
export function vendorOptions(rows: ModelRow[]): Array<{ label: string; value: string }> {
  const map = new Map<string, string>()
  for (const row of rows) {
    if (!row.vendor) continue
    if (!map.has(row.vendor)) map.set(row.vendor, row.vendorLabel)
  }
  const list = Array.from(map.entries())
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
  return [{ label: '厂商：全部', value: '' }, ...list]
}

/* ------------------------------------------------------------------ *
 * 编辑抽屉：表单 ↔ PATCH 请求体
 * ------------------------------------------------------------------ */

/** 抽屉里可编辑的字段（其余一律只读：倍率由同步写入，平台映射不可增删）。 */
export interface ModelEditForm {
  note: string
  /** 按 configId 记录的启用状态。 */
  enabled: Record<number, boolean>
  contextWindow: number | null
  maxOutputTokens: number | null
  supportsImages: boolean
  supportsTools: boolean
  supportsReasoning: boolean
}

export interface ModelEditUpdate {
  id: number
  body: ModelUpdateInput
}

export interface ModelEditPlan {
  updates: ModelEditUpdate[]
  /** 是否有任何改动（false 时页面提示「没有改动」并直接关闭抽屉）。 */
  changed: boolean
  /** 至少保留一个平台启用；否则给出错误文案。 */
  error: string
}

/** 行 + 表单 → 待提交的 PATCH 列表（只带真正变化的字段）。 */
export function buildModelEditPlan(row: ModelRow, form: ModelEditForm): ModelEditPlan {
  const note = text(form.note)
  const noteChanged = note !== text(row.note)

  const caps = row.caps || {}
  const originalContext = num(caps.context_window)
  const originalOutput = num(caps.max_output_tokens)
  const context = form.contextWindow === null ? originalContext : num(form.contextWindow)
  const output = form.maxOutputTokens === null ? originalOutput : num(form.maxOutputTokens)
  const capsChanged =
    context !== originalContext ||
    output !== originalOutput ||
    form.supportsImages !== Boolean(caps.supports_images) ||
    form.supportsTools !== Boolean(caps.supports_tools) ||
    form.supportsReasoning !== Boolean(caps.supports_reasoning)

  const capsBody: Record<string, unknown> = {}
  if (context !== null) capsBody.context_window = context
  if (output !== null) capsBody.max_output_tokens = output
  capsBody.supports_images = form.supportsImages
  capsBody.supports_tools = form.supportsTools
  capsBody.supports_reasoning = form.supportsReasoning

  const updates: ModelEditUpdate[] = []
  let anyEnabled = false
  for (const ref of row.platforms) {
    const desired = form.enabled[ref.configId] ?? ref.enabled
    if (desired) anyEnabled = true
    const enabledChanged = desired !== ref.enabled
    if (!enabledChanged && !noteChanged && !capsChanged) continue
    const body: ModelUpdateInput = { enabled: desired }
    if (noteChanged) body.note = note
    if (capsChanged) body.caps = capsBody
    updates.push({ id: ref.configId, body })
  }

  if (row.platforms.length > 0 && !anyEnabled) {
    return { updates: [], changed: false, error: '至少要保留一个平台处于启用状态' }
  }
  return { updates, changed: updates.length > 0, error: '' }
}

/** 行的表单初值。 */
export function modelEditForm(row: ModelRow): ModelEditForm {
  const enabled: Record<number, boolean> = {}
  for (const ref of row.platforms) enabled[ref.configId] = ref.enabled
  return {
    note: row.note,
    enabled,
    contextWindow: row.contextWindow,
    maxOutputTokens: row.maxOutputTokens,
    supportsImages: Boolean(row.caps.supports_images),
    supportsTools: Boolean(row.caps.supports_tools),
    supportsReasoning: Boolean(row.caps.supports_reasoning),
  }
}

/** 同步结果 → 一句人话（成功/部分失败共用）。 */
export function describeSyncResult(payload: ModelSyncPayload | null | undefined): string {
  if (!payload) return '同步完成'
  const parts = [
    '+' + (Number(payload.added) || 0) + ' 新增',
    '~' + (Number(payload.updated) || 0) + ' 更新',
    '共 ' + (Number(payload.total_in_db) || 0) + ' 个',
  ]
  const problems = Object.keys(payload.problems || {})
  const tail = problems.length ? '；未取到：' + problems.join('、') : ''
  return parts.join('，') + tail
}

/** 测速结果 → 一句人话（Toast 用）。 */
export function describeSpeedTest(payload: ModelSpeedTestPayload | null | undefined): string {
  if (!payload) return '测速完成'
  const parts: string[] = []
  if (payload.ttft_ms !== null && payload.ttft_ms !== undefined) {
    parts.push('首字 ' + Math.round(payload.ttft_ms) + 'ms')
  }
  if (payload.speed_tps !== null && payload.speed_tps !== undefined) {
    parts.push(payload.speed_tps + ' t/s')
  }
  if (payload.prompt_tokens !== null && payload.prompt_tokens !== undefined) {
    parts.push('提示 ' + payload.prompt_tokens + ' / 补全 ' + (payload.completion_tokens ?? 0) + ' tok')
  }
  if (payload.account) parts.push('账号 ' + payload.account)
  return parts.length ? parts.join(' · ') : '测速完成'
}
