/**
 * S09 分组管理：数据转换 / 筛排分页 / 模型 id 归一 的单测。
 *
 * 数据形状全部照抄后端真实响应：
 *   · GET /api/groups               → admin/routers/groups.py:list_groups
 *   · GET /api/models/configs       → admin/routers/models.py:list_configs
 * 契约见 docs/ui-v2/contracts/groups.md。
 */
import { describe, expect, it } from 'vitest'
import {
  GROUP_MODEL_PREVIEW,
  buildGroupEditorOptions,
  filterGroupRows,
  groupStats,
  paginateGroupRows,
  sortGroupRows,
  toGroupRows,
  type GroupItem,
  type ModelConfigItem,
} from '@/api/groups'

/** list_groups 的真实响应形状（items 按 ModelGroup.id DESC）。 */
const listItems: GroupItem[] = [
  {
    id: 7,
    name: '便宜模型组',
    note: '只放免费模型',
    models: ['glm-5.3', 'hy3', 'dfmodel', 'main-auto', 'deepseek-v4.1-flash', 'gpt-6.1-sol'],
    model_count: 6,
    effective_count: 4,
    key_count: 3,
    created_at: '2026-11-20T09:12:00',
  },
  {
    id: 4,
    name: 'WorkBuddy 全量',
    note: '',
    models: [],
    model_count: 0,
    effective_count: 0,
    key_count: 0,
    created_at: null,
  },
  {
    id: 2,
    name: 'Trae 实验组',
    note: '临时',
    models: ['gpt-6-astra'],
    model_count: 1,
    effective_count: 1,
    key_count: 5,
    created_at: '2026-11-01T00:00:00',
  },
]

describe('toGroupRows：接口 items → 表格行', () => {
  const rows = toGroupRows(listItems)

  it('行键是字符串 id，chips 最多 4 个 +N，停用数按 effective_count 算', () => {
    expect(rows).toHaveLength(3)
    expect(rows[0].key).toBe('7')
    expect(rows[0].id).toBe(7)
    expect(rows[0].models).toHaveLength(6)
    expect(rows[0].modelPreview).toEqual(listItems[0].models.slice(0, GROUP_MODEL_PREVIEW))
    expect(GROUP_MODEL_PREVIEW).toBe(4)
    expect(rows[0].modelPreview).toHaveLength(4)
    expect(rows[0].modelOverflow).toBe(2)
    expect(rows[0].modelCount).toBe(6)
    expect(rows[0].effectiveCount).toBe(4)
    expect(rows[0].staleCount).toBe(2)
    expect(rows[0].keyCount).toBe(3)
    expect(rows[0].createdAt).toBe('2026-11-20T09:12:00')
  })

  it('空分组：无 chips、无停用、备注为空字符串', () => {
    expect(rows[1].models).toEqual([])
    expect(rows[1].modelPreview).toEqual([])
    expect(rows[1].modelOverflow).toBe(0)
    expect(rows[1].staleCount).toBe(0)
    expect(rows[1].note).toBe('')
    expect(rows[1].createdAt).toBeNull()
  })

  it('缺失的 models / 计数按空值兜底，不抛异常', () => {
    const [row] = toGroupRows([
      { id: 9, name: '脏数据' } as unknown as GroupItem,
    ])
    expect(row.models).toEqual([])
    expect(row.modelCount).toBe(0)
    expect(row.effectiveCount).toBe(0)
    expect(row.keyCount).toBe(0)
  })

  it('null / undefined 输入返回空数组', () => {
    expect(toGroupRows(null)).toEqual([])
    expect(toGroupRows(undefined)).toEqual([])
  })
})

describe('filterGroupRows：搜索 / 模型状态 / Key 绑定', () => {
  const rows = toGroupRows(listItems)

  it('搜索匹配分组名、备注与模型 id，大小写不敏感', () => {
    expect(filterGroupRows(rows, { q: '便宜' }).map((row) => row.id)).toEqual([7])
    expect(filterGroupRows(rows, { q: '临时' }).map((row) => row.id)).toEqual([2])
    expect(filterGroupRows(rows, { q: 'DFMODEL' }).map((row) => row.id)).toEqual([7])
    expect(filterGroupRows(rows, { q: '不存在' })).toEqual([])
  })

  it('模型状态：stale 只看含停用模型的分组，ready 排除空分组', () => {
    expect(filterGroupRows(rows, { models: 'stale' }).map((row) => row.id)).toEqual([7])
    expect(filterGroupRows(rows, { models: 'ready' }).map((row) => row.id)).toEqual([2])
  })

  it('Key 绑定：bound / unbound', () => {
    expect(filterGroupRows(rows, { keys: 'bound' }).map((row) => row.id)).toEqual([7, 2])
    expect(filterGroupRows(rows, { keys: 'unbound' }).map((row) => row.id)).toEqual([4])
  })

  it('多个条件叠加，且不修改入参', () => {
    expect(filterGroupRows(rows, { q: 'WorkBuddy', keys: 'unbound' }).map((row) => row.id)).toEqual([4])
    expect(rows).toHaveLength(3)
  })
})

describe('sortGroupRows：三态排序', () => {
  const rows = toGroupRows([
    { id: 1, name: 'Alpha', note: '', models: ['a'], model_count: 1, effective_count: 1, key_count: 5, created_at: null },
    { id: 2, name: 'beta', note: '', models: ['b', 'c'], model_count: 2, effective_count: 2, key_count: 0, created_at: null },
    { id: 3, name: 'Gamma', note: '', models: [], model_count: 0, effective_count: 0, key_count: 3, created_at: null },
  ])

  it('按分组名升序 / 降序', () => {
    expect(sortGroupRows(rows, 'name', 'asc').map((row) => row.name)).toEqual([
      'Alpha',
      'beta',
      'Gamma',
    ])
    expect(sortGroupRows(rows, 'name', 'desc').map((row) => row.name)).toEqual([
      'Gamma',
      'beta',
      'Alpha',
    ])
  })

  it('按模型数、Key 数排序', () => {
    expect(sortGroupRows(rows, 'model_count', 'desc').map((row) => row.id)).toEqual([2, 1, 3])
    expect(sortGroupRows(rows, 'key_count', 'asc').map((row) => row.id)).toEqual([2, 3, 1])
  })

  it('空排序 / 未知字段 / 非法方向都保持后端原顺序', () => {
    expect(sortGroupRows(rows, '', '').map((row) => row.id)).toEqual([1, 2, 3])
    expect(sortGroupRows(rows, 'note', 'asc').map((row) => row.id)).toEqual([1, 2, 3])
    expect(sortGroupRows(rows, 'name', '').map((row) => row.id)).toEqual([1, 2, 3])
  })

  it('同值时按 id 倒序（稳定），且不改动入参顺序', () => {
    const same = toGroupRows([
      { id: 11, name: 'X', note: '', models: [], model_count: 1, effective_count: 1, key_count: 1, created_at: null },
      { id: 12, name: 'Y', note: '', models: [], model_count: 1, effective_count: 1, key_count: 1, created_at: null },
    ])
    expect(sortGroupRows(same, 'model_count', 'asc').map((row) => row.id)).toEqual([12, 11])
    expect(rows.map((row) => row.id)).toEqual([1, 2, 3])
  })
})

describe('paginateGroupRows：客户端分页', () => {
  const rows = toGroupRows(
    Array.from({ length: 42 }, (_, index) => ({
      id: 100 - index,
      name: '组 ' + (index + 1),
      note: '',
      models: ['a'],
      model_count: 1,
      effective_count: 1,
      key_count: 0,
      created_at: null,
    })),
  )

  it('第 2 页取 20 条，页数向上取整', () => {
    const page = paginateGroupRows(rows, 2, 20)
    expect(page.total).toBe(42)
    expect(page.pageCount).toBe(3)
    expect(page.page).toBe(2)
    expect(page.items).toHaveLength(20)
    expect(page.items[0].id).toBe(80)
  })

  it('页码越界回退到最后一页', () => {
    const page = paginateGroupRows(rows, 99, 20)
    expect(page.page).toBe(3)
    expect(page.items).toHaveLength(2)
  })

  it('空数据：0 页、页码 1、空数组', () => {
    expect(paginateGroupRows([], 3, 20)).toEqual({ items: [], total: 0, pageCount: 0, page: 1 })
  })

  it('每页条数非法时回落到 20', () => {
    expect(paginateGroupRows(rows, 1, 0).items).toHaveLength(20)
  })
})

describe('groupStats：FilterBar 统计文字的数据', () => {
  it('统计分组数、去重模型数、绑定 Key 数、含停用分组数', () => {
    const stats = groupStats(toGroupRows(listItems))
    expect(stats).toEqual({ total: 3, models: 7, boundKeys: 8, staleGroups: 1 })
  })

  it('空列表全为 0', () => {
    expect(groupStats([])).toEqual({ total: 0, models: 0, boundKeys: 0, staleGroups: 0 })
  })
})

/* ------------------------------------------------------------------ *
 * 模型 id 归一（抽屉的 NTransfer 选项）
 * ------------------------------------------------------------------ */

/** /api/models/configs 的真实响应形状：同一个上游 id 会被展开成国内 / 国际两条。 */
const catalog: ModelConfigItem[] = [
  {
    id: 1,
    model_id: 'glm-5.3',
    upstream_id: 'glm-5.3',
    client_id: 'glm-5.3-workbuddy',
    platform: 'workbuddy',
    platform_label: 'WorkBuddy',
    realm: '',
    enabled: true,
    is_free: false,
    credit_multiplier: 1,
  },
  {
    id: 2,
    model_id: 'deepseek-flash-qoder',
    upstream_id: 'dfmodel',
    client_id: 'deepseek-flash-qoder',
    platform: 'qoder',
    platform_label: 'Qoder 中国版',
    realm: 'cn',
    enabled: true,
    is_free: false,
    credit_multiplier: null,
  },
  {
    id: 3,
    model_id: 'deepseek-flash-qoder-intl',
    upstream_id: 'dfmodel',
    client_id: 'deepseek-flash-qoder-intl',
    platform: 'qoder-intl',
    platform_label: 'Qoder 国际版',
    realm: 'intl',
    enabled: true,
    is_free: false,
    credit_multiplier: null,
  },
  {
    id: 4,
    model_id: 'hy3',
    upstream_id: 'hy3',
    client_id: 'hy3-workbuddy',
    platform: 'workbuddy',
    platform_label: 'WorkBuddy',
    realm: '',
    enabled: false,
    is_free: true,
    credit_multiplier: 0,
  },
]

describe('buildGroupEditorOptions：上游 id 归一 / 跨平台合并 / 旧值保留', () => {
  it('目录里只有启用模型；同名模型（upstream_id 相同）合并成一条并列出全部平台', () => {
    const plan = buildGroupEditorOptions(catalog, [])
    const values = plan.options.map((option) => option.value)
    expect(values).toEqual(['dfmodel', 'glm-5.3'])
    expect(values).not.toContain('hy3')

    const merged = plan.options.find((option) => option.value === 'dfmodel')
    expect(merged?.platforms).toEqual(['Qoder 中国版', 'Qoder 国际版'])
    expect(merged?.label).toBe('Qoder 中国版 / Qoder 国际版 · deepseek-flash-qoder')
    expect(merged?.known).toBe(true)
    expect(merged?.enabled).toBe(true)
  })

  it('旧数据存可读名（旧版写的 model_id）也能解析回上游 id', () => {
    const plan = buildGroupEditorOptions(catalog, ['deepseek-flash-qoder'])
    expect(plan.selected).toEqual(['dfmodel'])
    expect(plan.unknown).toEqual([])
    expect(plan.disabled).toEqual([])
  })

  it('大小写不一致的旧值按不敏感匹配归一', () => {
    const plan = buildGroupEditorOptions(catalog, ['GLM-5.3'])
    expect(plan.selected).toEqual(['glm-5.3'])
    expect(plan.unknown).toEqual([])
  })

  it('已停用 / 目录里找不到的旧值原样保留（PUT 全量覆盖，不能静默删）', () => {
    const plan = buildGroupEditorOptions(catalog, ['hy3', 'legacy-model', 'hy3'])
    expect(plan.selected).toEqual(['hy3', 'legacy-model'])
    expect(plan.unknown).toEqual(['legacy-model'])
    expect(plan.disabled).toEqual(['hy3'])

    const disabledOption = plan.options.find((option) => option.value === 'hy3')
    expect(disabledOption?.known).toBe(true)
    expect(disabledOption?.enabled).toBe(false)
    expect(disabledOption?.label).toContain('已停用')

    const unknownOption = plan.options.find((option) => option.value === 'legacy-model')
    expect(unknownOption?.known).toBe(false)
    expect(unknownOption?.label).toBe('legacy-model · 未在目录')
    // 选项值唯一（NTransfer 的 value 必须唯一）
    expect(new Set(plan.options.map((option) => option.value)).size).toBe(plan.options.length)
  })

  it('免费模型在 label 里标注「免费」', () => {
    const plan = buildGroupEditorOptions(
      catalog.map((item) => (item.model_id === 'glm-5.3' ? { ...item, is_free: true } : item)),
      [],
    )
    expect(plan.options.find((option) => option.value === 'glm-5.3')?.label).toBe(
      'WorkBuddy · glm-5.3 · 免费',
    )
  })

  it('目录为空时把已选值全部当成未知项（不丢值）', () => {
    const plan = buildGroupEditorOptions(null, ['dfmodel'])
    expect(plan.selected).toEqual(['dfmodel'])
    expect(plan.unknown).toEqual(['dfmodel'])
    expect(plan.options).toHaveLength(1)
  })
})
