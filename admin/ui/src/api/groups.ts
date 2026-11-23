/**
 * 分组管理（/api/groups）+ 分组编辑所需的模型目录（/api/models/configs）。
 *
 * 后端：admin/routers/groups.py（list_groups / create_group / update_group / delete_group）
 *       admin/routers/models.py:list_configs（GET /api/models/configs）
 * 契约：docs/ui-v2/contracts/groups.md
 *
 * 两个必须留在这个文件里的规则（页面和单测共用同一份实现）：
 *   1. 分组里保存的是 **上游模型 id**（ModelConfig.model_id，接口里的 upstream_id），
 *      不是可读调用名（model_id）。旧版提交可读名，与库里做精确集合交集后会整组失效
 *      （admin/routers/groups.py:_normalized_members 的注释 F6）。
 *   2. PUT /api/groups/{id} 的 models 是**全量覆盖**：编辑时目录里找不到 / 已停用的
 *      模型必须原样保留，不能在保存时被静默删掉。
 *
 * GET /api/groups 没有 q / page / size / sort 参数，也没有分页结构；
 * 筛选、排序、分页都在客户端完成（见 toGroupRows / filterGroupRows / sortGroupRows / paginateGroupRows）。
 */
import { http } from './http'

/* ------------------------------------------------------------------ *
 * 接口类型（字段名与后端一致，snake_case 原样保留）
 * ------------------------------------------------------------------ */

/** GET /api/groups 的 items[] 一项（admin/routers/groups.py:list_groups）。 */
export interface GroupItem {
  id: number
  name: string
  note: string
  /** 组内登记的原始模型 id（ModelGroupItem.model_id，按 id 升序）。 */
  models: string[]
  /** len(models)。 */
  model_count: number
  /** 组内模型 ∩ 当前启用模型（已归一）。 */
  effective_count: number
  /** 绑定该分组的 ApiKey 数。 */
  key_count: number
  created_at: string | null
}

export interface GroupListPayload {
  items: GroupItem[]
}

/** POST/PUT /api/groups 的请求体（admin/routers/groups.py:GroupIn）。 */
export interface GroupInput {
  name: string
  note: string
  models: string[]
}

export interface GroupCreatePayload {
  id: number
  name: string
  model_count: number
  ok: boolean
}

export interface GroupUpdatePayload {
  id: number
  ok: boolean
}

export interface GroupDeletePayload {
  id: number
  ok: boolean
  /** 被自动解绑（group_id 置 0）的 Key 数。 */
  unbound_keys: number
}

/** GET /api/models/configs 的 items[] 里本页消费的字段（其余字段归 S12 模型配置页）。 */
export interface ModelConfigItem {
  id: number
  /** 可读调用名（/v1/models 口径）。 */
  model_id: string
  /** 上游原生 id（= ModelConfig.model_id），分组存储用的就是它。 */
  upstream_id: string
  /** 完整调用名（总是带平台后缀）；本页不用。 */
  client_id?: string
  /** 区级平台 id（qoder / qoder-intl / trae-cn…）。 */
  platform: string
  platform_label: string
  realm?: string
  enabled: boolean
  is_free?: boolean
  credit_multiplier?: number | null
}

export interface ModelConfigListPayload {
  official_total?: number
  platforms?: Array<{ platform: string; label: string; count: number; enabled: number }>
  items: ModelConfigItem[]
}

/* ------------------------------------------------------------------ *
 * 接口函数
 * ------------------------------------------------------------------ */

/** 分组列表（后端按 ModelGroup.id DESC 返回，不分页）。 */
export function listGroups(signal?: AbortSignal): Promise<GroupListPayload> {
  return http<GroupListPayload>('GET', '/api/groups', signal ? { signal } : {})
}

/** 新建分组。models 传上游模型 id（不是可读名）。 */
export function createGroup(input: GroupInput): Promise<GroupCreatePayload> {
  return http<GroupCreatePayload>('POST', '/api/groups', {
    body: { name: input.name, note: input.note, models: input.models },
  })
}

/** 编辑分组：models 全量覆盖（传什么就是什么）。 */
export function updateGroup(id: number, input: GroupInput): Promise<GroupUpdatePayload> {
  return http<GroupUpdatePayload>('PUT', '/api/groups/' + encodeURIComponent(String(id)), {
    body: { name: input.name, note: input.note, models: input.models },
  })
}

/** 删除分组；绑定该分组的 Key 会被自动置为「不限制模型」。 */
export function deleteGroup(id: number): Promise<GroupDeletePayload> {
  return http<GroupDeletePayload>('DELETE', '/api/groups/' + encodeURIComponent(String(id)))
}

/** 模型目录（抽屉里的候选池）。 */
export function listModelConfigs(signal?: AbortSignal): Promise<ModelConfigListPayload> {
  return http<ModelConfigListPayload>('GET', '/api/models/configs', signal ? { signal } : {})
}

/* ------------------------------------------------------------------ *
 * 纯函数：接口响应 → 展示数据 / 表单数据（单测覆盖）
 * ------------------------------------------------------------------ */

/** 表格行：DataTable 需要 Record<string, unknown>，key 固定为字符串 id。 */
export interface GroupRow extends Record<string, unknown> {
  key: string
  id: number
  name: string
  note: string
  models: string[]
  /** 前 N 个模型（chips）。 */
  modelPreview: string[]
  /** 被折叠的模型数（0 表示全部显示）。 */
  modelOverflow: number
  modelCount: number
  effectiveCount: number
  /** modelCount - effectiveCount，>0 表示组内有模型被停用。 */
  staleCount: number
  keyCount: number
  createdAt: string | null
}

/** chips 最多显示几个（设计稿 P16：最多 5 个 +N）。 */
export const GROUP_MODEL_PREVIEW = 5

function text(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value).trim()
}

function int(value: unknown, fallback = 0): number {
  const n = Number(value)
  return Number.isFinite(n) ? Math.trunc(n) : fallback
}

/** 接口 items[] → 表格行。models 缺失时按空数组处理（老数据 / 手改库）。 */
export function toGroupRows(items: GroupItem[] | null | undefined): GroupRow[] {
  return (items || []).map((item) => {
    const models = Array.isArray(item.models) ? item.models.map(text).filter(Boolean) : []
    const modelCount = Math.max(0, int(item.model_count, models.length) || models.length)
    const effectiveCount = Math.min(modelCount, Math.max(0, int(item.effective_count, 0)))
    return {
      key: String(item.id),
      id: item.id,
      name: text(item.name),
      note: text(item.note),
      models,
      modelPreview: models.slice(0, GROUP_MODEL_PREVIEW),
      modelOverflow: Math.max(0, models.length - GROUP_MODEL_PREVIEW),
      modelCount,
      effectiveCount,
      staleCount: Math.max(0, modelCount - effectiveCount),
      keyCount: Math.max(0, int(item.key_count, 0)),
      createdAt: item.created_at ?? null,
    }
  })
}

export interface GroupFilters {
  /** 搜索关键字：匹配分组名 / 备注 / 模型 id。 */
  q?: string
  /** '' | 'stale'（含已停用模型）| 'ready'（全部可用）。 */
  models?: string
  /** '' | 'bound'（已绑定 Key）| 'unbound'（未绑定）。 */
  keys?: string
}

/** 筛选（大小写不敏感）。返回新数组，不修改入参。 */
export function filterGroupRows(rows: GroupRow[], filters: GroupFilters = {}): GroupRow[] {
  const keyword = text(filters.q).toLowerCase()
  const modelFilter = text(filters.models)
  const keyFilter = text(filters.keys)
  return rows.filter((row) => {
    if (keyword) {
      const haystack = [row.name, row.note, ...row.models].join('\n').toLowerCase()
      if (!haystack.includes(keyword)) return false
    }
    if (modelFilter === 'stale' && row.staleCount <= 0) return false
    if (modelFilter === 'ready' && (row.modelCount <= 0 || row.staleCount > 0)) return false
    if (keyFilter === 'bound' && row.keyCount <= 0) return false
    if (keyFilter === 'unbound' && row.keyCount > 0) return false
    return true
  })
}

export type GroupSortOrder = 'asc' | 'desc' | ''

/** 排序字段白名单（与列 key 一致）。 */
export const GROUP_SORT_KEYS = ['name', 'model_count', 'key_count'] as const
export type GroupSortKey = (typeof GROUP_SORT_KEYS)[number]

function compareRows(a: GroupRow, b: GroupRow, sortBy: GroupSortKey): number {
  if (sortBy === 'name') return a.name.localeCompare(b.name, 'zh-CN')
  if (sortBy === 'model_count') return a.modelCount - b.modelCount
  return a.keyCount - b.keyCount
}

/**
 * 排序。sortBy 为空 / order 为空 → 原样返回后端顺序（ModelGroup.id DESC）。
 * 同值时按 id 倒序，保证结果稳定（与后端默认顺序一致）。
 */
export function sortGroupRows(rows: GroupRow[], sortBy: string, order: GroupSortOrder): GroupRow[] {
  const key = text(sortBy) as GroupSortKey
  if (!key || !GROUP_SORT_KEYS.includes(key) || (order !== 'asc' && order !== 'desc')) {
    return rows.slice()
  }
  const direction = order === 'desc' ? -1 : 1
  return rows.slice().sort((a, b) => {
    const diff = compareRows(a, b, key)
    if (diff !== 0) return diff * direction
    return b.id - a.id
  })
}

export interface PaginatedGroupRows {
  items: GroupRow[]
  total: number
  pageCount: number
  /** 夹紧后的页码（越界时回退到最后一页）。 */
  page: number
}

/** 客户端分页：后端一次返回全部，这里只切当前页。 */
export function paginateGroupRows(
  rows: GroupRow[],
  page: number,
  pageSize: number,
): PaginatedGroupRows {
  const size = Math.max(1, Math.trunc(Number(pageSize) || 20))
  const total = rows.length
  const pageCount = Math.ceil(total / size)
  const current = pageCount ? Math.min(Math.max(1, Math.trunc(Number(page) || 1)), pageCount) : 1
  const start = (current - 1) * size
  return { items: rows.slice(start, start + size), total, pageCount, page: current }
}

export interface GroupStats {
  /** 分组数。 */
  total: number
  /** 去重后的模型数（多个分组共用同一个模型只算一次）。 */
  models: number
  /** 绑定 Key 总数（同一 Key 只属于一个分组，求和即可）。 */
  boundKeys: number
  /** 含已停用模型的分组数。 */
  staleGroups: number
}

/** 汇总数字（FilterBar 右侧统计文字）。 */
export function groupStats(rows: GroupRow[]): GroupStats {
  const models = new Set<string>()
  let boundKeys = 0
  let staleGroups = 0
  for (const row of rows) {
    for (const model of row.models) models.add(model)
    boundKeys += row.keyCount
    if (row.staleCount > 0) staleGroups += 1
  }
  return { total: rows.length, models: models.size, boundKeys, staleGroups }
}

/* -------------------------- 模型穿梭框 -------------------------- */

/** 穿梭框选项（value 就是提交给后端的模型 id）。 */
export interface GroupModelOption {
  value: string
  label: string
  /** 首个平台 id（合并同名模型时取第一个）。 */
  platform: string
  platformLabel: string
  /** 该 value 在目录里出现的全部平台名（同名模型跨平台合并成一条）。 */
  platforms: string[]
  /** 可读调用名。 */
  modelId: string
  upstreamId: string
  free: boolean
  /** false = 目录里存在但已停用（只能移除，不能新增）。 */
  enabled: boolean
  /** false = 目录里完全找不到（旧数据 / 别的平台遗留）。 */
  known: boolean
}

export interface GroupEditorPlan {
  options: GroupModelOption[]
  /** 归一后的选中值（提交用）。 */
  selected: string[]
  /** 目录里找不到的原值（保存时原样保留）。 */
  unknown: string[]
  /** 选中但已停用的值。 */
  disabled: string[]
}

interface CatalogEntry {
  upstreamId: string
  modelId: string
  platforms: string[]
  platformLabels: string[]
  free: boolean
  enabled: boolean
}

function makeLabel(entry: CatalogEntry): string {
  const platforms = entry.platformLabels.join(' / ') || '未知平台'
  const parts = [platforms, entry.modelId]
  if (entry.free) parts.push('免费')
  if (!entry.enabled) parts.push('已停用')
  return parts.join(' · ')
}

function toOption(entry: CatalogEntry): GroupModelOption {
  return {
    value: entry.upstreamId,
    label: makeLabel(entry),
    platform: entry.platforms[0] || '',
    platformLabel: entry.platformLabels[0] || '',
    platforms: entry.platformLabels.slice(),
    modelId: entry.modelId,
    upstreamId: entry.upstreamId,
    free: entry.free,
    enabled: entry.enabled,
    known: true,
  }
}

/**
 * 模型目录 + 分组已存模型 → 穿梭框选项 / 选中值。
 *
 * 解析顺序（与后端 _normalized_members 的宽松方向一致）：
 *   upstream_id 精确 → model_id 精确（旧版存的是可读名）→ 两者大小写不敏感。
 * 都找不到的原样保留（known=false），保证 PUT 全量覆盖时不丢授权。
 * 同名模型（upstream_id 相同）跨平台合并成一条：分组只存字符串，平台区分不了。
 */
export function buildGroupEditorOptions(
  catalog: ModelConfigItem[] | null | undefined,
  storedSelection: string[] | null | undefined,
): GroupEditorPlan {
  const entries: CatalogEntry[] = []
  const byUpstream = new Map<string, CatalogEntry>()
  const byUpstreamLower = new Map<string, CatalogEntry>()
  const byModel = new Map<string, CatalogEntry>()
  const byModelLower = new Map<string, CatalogEntry>()

  for (const item of catalog || []) {
    const upstreamId = text(item.upstream_id) || text(item.model_id)
    if (!upstreamId) continue
    const modelId = text(item.model_id) || upstreamId
    const platform = text(item.platform)
    const platformLabel = text(item.platform_label) || platform
    const enabled = item.enabled !== false
    const free = Boolean(item.is_free)

    let entry = byUpstream.get(upstreamId)
    if (!entry) {
      entry = {
        upstreamId,
        modelId,
        platforms: platform ? [platform] : [],
        platformLabels: platformLabel ? [platformLabel] : [],
        free,
        enabled,
      }
      entries.push(entry)
      byUpstream.set(upstreamId, entry)
      byUpstreamLower.set(upstreamId.toLowerCase(), entry)
    } else {
      // 同名模型跨平台：合并平台名；启用状态取「任一启用」。
      if (platform && !entry.platforms.includes(platform)) entry.platforms.push(platform)
      if (platformLabel && !entry.platformLabels.includes(platformLabel)) {
        entry.platformLabels.push(platformLabel)
      }
      entry.free = entry.free || free
      entry.enabled = entry.enabled || enabled
    }
    if (!byModel.has(modelId)) byModel.set(modelId, entry)
    if (!byModelLower.has(modelId.toLowerCase())) byModelLower.set(modelId.toLowerCase(), entry)
  }

  const stored = Array.from(new Set((storedSelection || []).map(text).filter(Boolean)))
  const selected: string[] = []
  const unknown: string[] = []
  const disabled: string[] = []
  const extra: CatalogEntry[] = []

  for (const raw of stored) {
    const entry =
      byUpstream.get(raw) ||
      byModel.get(raw) ||
      byUpstreamLower.get(raw.toLowerCase()) ||
      byModelLower.get(raw.toLowerCase())
    if (!entry) {
      selected.push(raw)
      unknown.push(raw)
      continue
    }
    if (!selected.includes(entry.upstreamId)) selected.push(entry.upstreamId)
    if (!entry.enabled) {
      disabled.push(entry.upstreamId)
      if (!extra.includes(entry)) extra.push(entry)
    }
  }

  const enabledEntries = entries
    .filter((entry) => entry.enabled)
    .sort((a, b) => {
      const left = [a.platformLabels[0] || '', a.modelId]
      const right = [b.platformLabels[0] || '', b.modelId]
      return left.join('\u0000').localeCompare(right.join('\u0000'), 'zh-CN')
    })

  const options: GroupModelOption[] = enabledEntries.map(toOption)
  for (const entry of extra) {
    if (!options.some((option) => option.value === entry.upstreamId)) options.push(toOption(entry))
  }
  for (const raw of unknown) {
    options.push({
      value: raw,
      label: raw + ' · 未在目录',
      platform: '',
      platformLabel: '',
      platforms: [],
      modelId: raw,
      upstreamId: raw,
      free: false,
      enabled: false,
      known: false,
    })
  }

  return { options, selected, unknown, disabled }
}
