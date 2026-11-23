<script setup lang="ts">
/**
 * S12 模型配置 /models（模板 B：PageHeader + FilterBar + DataTable + FormDrawer）
 *
 * 契约：docs/ui-v2/contracts/models.md（末尾「## 实现对照」是逐条落点）
 * 旧版：#panel-models + model-pool.js / model-metadata.js / model-icons.js
 *
 * 三条与后端对齐的口径（写错就会显示假数据）：
 *   1. 一「行」= 同一模型跨平台合成（后端 model_alias 的归一方向：上游展示名优先）。
 *      平台映射放在行内折叠，抽屉里逐平台列清楚；
 *   2. 展示名是 `note`，可读调用名是 `model_id`，复制给客户端的是 `client_id`；
 *   3. 倍率 `credit_multiplier` 原样展示：>0 收费 / 0 免费 / <0 无量纲 / null 未同步。
 *      PATCH /api/models/{id} 不接受倍率字段，所以抽屉里倍率只读（由「同步目录」写入）。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch, type VNodeChild } from 'vue'
import { NDropdown, NInput, NInputNumber, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import {
  Brain,
  Image as ImageIcon,
  ListChecks,
  Plus,
  RefreshCw,
  Sparkles,
  Video,
  Wrench,
} from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Chip,
  Copyable,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  HelpTip,
  ModelIcon,
  PlatformLogo,
  RowActions,
  type FilterChip,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { FMT_EMPTY, fmtDuration, fmtInt, fmtPct } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import {
  CAPABILITY_LABELS,
  MODEL_ALIAS_LIMIT,
  batchToggleModels,
  buildModelEditPlan,
  deleteModelConfig,
  describeSpeedTest,
  describeSyncResult,
  filterModelRows,
  fmtMultiplier,
  fmtTokensShort,
  listModelConfigs,
  listModelHealth,
  modelEditForm,
  modelStats,
  multiplierTone,
  paginateModelRows,
  platformOptions,
  sortModelRows,
  speedTestModel,
  syncModels,
  toModelRows,
  updateModelConfig,
  vendorOptions,
  type CapabilityKey,
  type ModelConfigItem,
  type ModelEditForm,
  type ModelHealthItem,
  type ModelPlatformRef,
  type ModelPlatformSummary,
  type ModelRow,
  type ModelSpeedTestPayload,
} from '@/api/models'

/** 旧版 #panel-models 顶部那段提示（收进 ⓘ）。 */
const HELP_TEXT =
  '白名单：只有列在这里且处于启用状态的模型对外可用。未配置任何模型时放行全部；' +
  '配置后请求不在启用名单内的模型会返回「模型不存在」。模型目录来自各平台上游，' +
  '用「同步目录」拉取；展示名、能力覆盖与启停在这里改。'

const toast = useToast()
const confirm = useConfirm()
const platformStore = usePlatformStore()
const table = useTableQuery('models', {
  pageSize: 50,
  sortBy: '',
  sortOrder: '',
  filters: { q: '', vendor: '', platform: '', cap: '', status: '' },
})

/* ------------------------------------------------------------------ *
 * 数据
 * ------------------------------------------------------------------ */

const items = ref<ModelConfigItem[] | null>(null)
const platforms = ref<ModelPlatformSummary[]>([])
const officialTotal = ref(0)
const health = ref<ModelHealthItem[] | null>(null)
const healthError = ref('')

const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const syncing = ref(false)
const bulkRunning = ref(false)

const allRows = computed(() => toModelRows(items.value, health.value))
const filteredRows = computed(() => filterModelRows(allRows.value, table.state.filters))
const sortedRows = computed(() =>
  sortModelRows(filteredRows.value, table.state.sortBy, table.state.sortOrder),
)
const pageData = computed(() =>
  paginateModelRows(sortedRows.value, table.state.page, table.state.pageSize),
)
const stats = computed(() => modelStats(allRows.value))
const filteredStats = computed(() => modelStats(filteredRows.value))

const firstLoading = computed(() => loading.value && items.value === null)
const hasFilters = computed(() =>
  Boolean(
    table.state.filters.q ||
      table.state.filters.vendor ||
      table.state.filters.platform ||
      table.state.filters.cap ||
      table.state.filters.status,
  ),
)

const summary = computed(() => {
  const current = filteredStats.value
  const base =
    '共 ' + current.total + ' 个模型 · ' + current.configs + ' 条平台配置 · 已启用 ' +
    current.enabled
  const tail = current.unavailable > 0 ? ' · 无可用账号 ' + current.unavailable : ''
  if (!hasFilters.value) return base + tail
  return '筛选出 ' + current.total + ' / 共 ' + stats.value.total + ' 个模型 · 已启用 ' +
    current.enabled + tail
})

const emptyDescription = computed(() =>
  hasFilters.value
    ? '没有符合条件的模型。清空筛选后可看到全部模型。'
    : '还没有模型配置。点「同步目录」从各平台上游拉取。',
)

/* ------------------------------ 筛选项 ------------------------------ */

const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => {
    search.value = value
  },
)

const vendorSelectOptions = computed(() => vendorOptions(allRows.value))
const platformSelectOptions = computed(() => platformOptions(platforms.value))

const CAPABILITY_OPTIONS = [
  { label: '能力：全部', value: '' },
  { label: '图片输入', value: 'image' },
  { label: '工具调用', value: 'tools' },
  { label: '推理', value: 'reasoning' },
  { label: '视频', value: 'video' },
  { label: '生图', value: 'image-out' },
]

const STATUS_OPTIONS = [
  { label: '状态：全部', value: '' },
  { label: '已启用', value: 'enabled' },
  { label: '含停用平台', value: 'disabled' },
]

function labelOf(
  options: Array<{ label: string; value: string }>,
  value: string,
): string {
  const hit = options.find((option) => option.value === value)
  if (!hit) return value
  return hit.label.replace(/（.*）$/, '').replace(/：全部$/, '')
}

const chips = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: '搜索', value: filters.q })
  if (filters.vendor) {
    list.push({ key: 'vendor', label: '厂商', value: labelOf(vendorSelectOptions.value, filters.vendor) })
  }
  if (filters.platform) {
    list.push({
      key: 'platform',
      label: '平台',
      value: labelOf(platformSelectOptions.value, filters.platform),
    })
  }
  if (filters.cap) {
    list.push({ key: 'cap', label: '能力', value: labelOf(CAPABILITY_OPTIONS, filters.cap) })
  }
  if (filters.status) {
    list.push({ key: 'status', label: '状态', value: labelOf(STATUS_OPTIONS, filters.status) })
  }
  return list
})

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) next[name] = String(value ?? '')
  table.setFilters(next)
}

function applyFilter(name: string, value: string | number | null): void {
  table.setFilter(name, value === null || value === undefined ? '' : String(value))
}

function removeChip(key: string): void {
  if (key === 'q') search.value = ''
  table.setFilter(key, '')
}

function resetFilters(): void {
  search.value = ''
  table.reset()
}

/* ------------------------------ 排序 ------------------------------ */

const SORT_KEY_MAP: Record<string, string> = {
  name: 'name',
  multiplier: 'multiplier',
  platforms: 'platforms',
}

function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
  if (table.state.sortBy !== stateKey) return false
  if (table.state.sortOrder === 'asc') return 'ascend'
  if (table.state.sortOrder === 'desc') return 'descend'
  return false
}

/** 三态：升序 → 降序 → 默认（naive 的 cancel 对应 order=false）。 */
function onSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order) {
    table.setSort('', '')
    return
  }
  const stateKey = SORT_KEY_MAP[sorter.columnKey]
  if (!stateKey) {
    table.setSort('', '')
    return
  }
  table.setSort(stateKey, sorter.order === 'ascend' ? 'asc' : 'desc')
}

/* ------------------------------ 单元格 ------------------------------ */

const CAP_ICONS: Record<CapabilityKey, typeof Brain> = {
  image: ImageIcon,
  tools: Wrench,
  reasoning: Brain,
  video: Video,
  'image-out': Sparkles,
}

const CAP_TONES: Record<CapabilityKey, string> = {
  image: 'models-cap--image',
  tools: 'models-cap--tools',
  reasoning: 'models-cap--reasoning',
  video: 'models-cap--video',
  'image-out': 'models-cap--image',
}

function platformTooltip(row: ModelRow): string {
  return row.platforms
    .map(
      (ref: ModelPlatformRef) =>
        ref.platformLabel + ' · ' + ref.clientId + ' · ' + fmtMultiplier(ref.multiplier) +
        (ref.enabled ? '' : ' · 已停用'),
    )
    .join('\n')
}

function renderModelCell(row: ModelRow): VNodeChild {
  const caps = row.capabilityKeys.map((key) =>
    h(
      'span',
      {
        key: 'cap-' + key,
        class: ['models-cap', CAP_TONES[key]],
        title: CAPABILITY_LABELS[key],
        'aria-label': CAPABILITY_LABELS[key],
      },
      [h(CAP_ICONS[key], { size: 12 })],
    ),
  )
  const aliasChips = row.aliases.slice(0, MODEL_ALIAS_LIMIT).map((alias) =>
    h(Chip, { key: 'alias-' + alias, mono: true, copy: true, value: alias, title: '复制别名' }, {
      default: () => alias,
    }),
  )
  if (row.aliasOverflow > 0) {
    const rest = row.aliases.slice(MODEL_ALIAS_LIMIT)
    aliasChips.push(
      h(
        Chip,
        { key: 'alias-more', title: '还有 ' + row.aliasOverflow + ' 个别名：' + rest.join('、') },
        { default: () => '+' + row.aliasOverflow },
      ),
    )
  }

  return h(
    'div',
    {
      class: 'models-name',
      role: 'button',
      tabindex: 0,
      title: '编辑 ' + row.name,
      onClick: () => void openEdit(row),
      onKeydown: (event: KeyboardEvent) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          void openEdit(row)
        }
      },
    },
    [
      h(ModelIcon, { vendor: row.vendor, size: 20, label: row.vendorLabel }),
      h('div', { class: 'models-name__main' }, [
        h('div', { class: 'models-name__title' }, [
          h('span', { class: 'models-name__text' }, row.name),
          row.free ? h(Badge, { tone: 'ok' }, { default: () => '免费' }) : null,
          row.officialVisible ? null : h(Badge, { tone: 'neutral' }, { default: () => '旧模型' }),
          row.partiallyEnabled
            ? h(Badge, { tone: 'warn' }, { default: () => row.disabledCount + ' 个平台停用' })
            : null,
        ]),
        h(Copyable, { value: row.callId, muted: true }),
        aliasChips.length ? h('div', { class: 'models-name__aliases' }, aliasChips) : null,
        caps.length
          ? h('div', { class: 'models-name__caps', 'aria-label': '模型能力' }, caps)
          : null,
      ]),
    ],
  )
}

function renderPlatforms(row: ModelRow): VNodeChild {
  const shown = row.platforms.slice(0, 4)
  const nodes: VNodeChild[] = shown.map((ref) =>
    h(
      'span',
      { key: 'logo-' + ref.platform, class: 'models-platform', title: ref.platformLabel },
      [h(PlatformLogo, { id: ref.platform, size: 18, label: ref.platformLabel })],
    ),
  )
  if (row.platforms.length > shown.length) {
    nodes.push(
      h('span', { key: 'logo-more', class: 'models-platform is-more' }, [
        '+' + (row.platforms.length - shown.length),
      ]),
    )
  }
  return h('div', { class: 'models-platforms', title: platformTooltip(row) }, nodes)
}

function renderMultiplier(row: ModelRow): VNodeChild {
  const tone = multiplierTone(row.multiplier)
  const hint =
    row.multiplier === null
      ? '尚未同步倍率'
      : row.multiplier < 0
        ? '该平台按实际 token 计量，没有倍率概念'
        : row.multiplier === 0
          ? '本站不计费'
          : '相对基准倍率'
  return h('span', { class: ['models-mult', 'is-' + tone], title: hint }, row.multiplierText)
}

function renderLimits(row: ModelRow): VNodeChild {
  const lines: VNodeChild[] = [
    h('span', { class: 'models-limits__line' }, [
      h('span', { class: 'models-limits__label' }, '上下文'),
      h('span', { class: 'tnum' }, fmtTokensShort(row.contextWindow)),
    ]),
    h('span', { class: 'models-limits__line' }, [
      h('span', { class: 'models-limits__label' }, '输出'),
      h('span', { class: 'tnum' }, fmtTokensShort(row.maxOutputTokens)),
    ]),
  ]
  if (row.availableAccounts !== null) {
    lines.push(
      h(
        'span',
        {
          class: 'models-limits__line',
          title: '可用账号 / 有资格账号（最近 24 小时，来自 /api/stats/model-health）',
        },
        [
          h('span', { class: 'models-limits__label' }, '账号'),
          h('span', { class: 'tnum' }, row.availableAccounts + '/' + (row.eligibleAccounts ?? 0)),
        ],
      ),
    )
  }
  return h('div', { class: 'models-limits' }, lines)
}

function renderHealth(row: ModelRow): VNodeChild {
  if (!row.health) return h('span', { class: 'models-dash' }, FMT_EMPTY)
  const successRate = row.successRate
  const tone = successRate === null ? 'neutral' : successRate >= 0.95 ? 'ok' : successRate >= 0.8 ? 'warn' : 'danger'
  return h('div', { class: 'models-health' }, [
    h('span', { class: 'models-health__line' }, [
      h('span', { class: 'models-health__label' }, 'TTFT'),
      h('span', { class: 'tnum' }, fmtDuration(row.ttftP50)),
    ]),
    h('span', { class: 'models-health__line' }, [
      h(Badge, { tone }, { default: () => (successRate === null ? '无样本' : fmtPct(successRate)) }),
      h('span', { class: 'models-health__sub tnum' }, fmtInt(row.requests) + ' 次'),
    ]),
    row.availableAccounts === 0 && (row.eligibleAccounts || 0) > 0
      ? h(Badge, { tone: 'danger' }, { default: () => '无可用账号' })
      : null,
  ])
}

function renderStatus(row: ModelRow): VNodeChild {
  const tone = row.enabled ? 'ok' : row.partiallyEnabled ? 'warn' : 'neutral'
  const text = row.enabled ? '已启用' : row.partiallyEnabled ? '部分启用' : '已停用'
  return h('div', { class: 'models-status' }, [
    h(Badge, { tone, dot: true }, { default: () => text }),
    h('span', { class: 'models-status__sub' }, row.enabledCount + '/' + row.platformCount + ' 平台'),
  ])
}

function renderActions(row: ModelRow): VNodeChild {
  return h(
    'div',
    { class: 'models-actions', onClick: (event: Event) => event.stopPropagation() },
    [
      h(RowActions, {
        actions: [
          { key: 'edit', label: '编辑' },
          { key: 'toggle', label: row.enabled ? '全部停用' : '全部启用' },
          { key: 'speed', label: '测速', disabled: !row.speedTestable },
          { key: 'delete', label: '删除', danger: true },
        ],
        max: 1,
        onSelect: (key: string) => {
          void onRowAction(row, key)
        },
      }),
    ],
  )
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    title: '模型',
    mobile: 'title',
    minWidth: 320,
    sortable: true,
    sortOrder: sortOrderOf('name'),
    render: (row: TableRow) => renderModelCell(row as ModelRow),
  },
  {
    key: 'platforms',
    title: '可用平台',
    mobile: 'meta',
    width: 140,
    sortable: true,
    sortOrder: sortOrderOf('platforms'),
    render: (row: TableRow) => renderPlatforms(row as ModelRow),
  },
  {
    key: 'multiplier',
    title: '倍率',
    mobile: 'meta',
    width: 100,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('multiplier'),
    render: (row: TableRow) => renderMultiplier(row as ModelRow),
  },
  {
    key: 'limits',
    title: '限制',
    mobile: 'meta',
    width: 156,
    render: (row: TableRow) => renderLimits(row as ModelRow),
  },
  {
    key: 'health',
    title: '延迟与健康',
    mobile: 'title',
    width: 186,
    render: (row: TableRow) => renderHealth(row as ModelRow),
  },
  {
    key: 'status',
    title: '状态',
    mobile: 'meta',
    width: 116,
    render: (row: TableRow) => renderStatus(row as ModelRow),
  },
  {
    key: 'actions',
    title: '操作',
    mobile: 'actions',
    width: 132,
    hideable: false,
    fixed: 'right',
    render: (row: TableRow) => renderActions(row as ModelRow),
  },
])

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

let controller: AbortController | null = null

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

async function loadConfigs(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const payload = await listModelConfigs(current.signal)
    items.value = Array.isArray(payload?.items) ? payload.items : []
    platforms.value = Array.isArray(payload?.platforms) ? payload.platforms : []
    officialTotal.value = Number(payload?.official_total) || 0
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / table.state.pageSize))
    if (table.state.page > maxPage) table.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: errorMessage(error), detail }
    toast.error(failureText(items.value === null ? '加载模型' : '刷新模型', error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

async function loadHealth(): Promise<void> {
  healthError.value = ''
  try {
    const payload = await listModelHealth(24)
    health.value = Array.isArray(payload?.items) ? payload.items : []
  } catch (error) {
    if (isAbortError(error)) return
    healthError.value = failureText('加载模型健康', error)
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadConfigs(), loadHealth()])
}

/* ------------------------------------------------------------------ *
 * 工具栏：同步 / 批量 / 添加
 * ------------------------------------------------------------------ */

async function onSync(): Promise<void> {
  if (syncing.value) return
  syncing.value = true
  toast.info('正在同步全部平台的模型目录…')
  try {
    const payload = await syncModels()
    toast.success('同步完成：' + describeSyncResult(payload))
    await loadConfigs()
    await loadHealth()
  } catch (error) {
    toast.error(failureText('同步模型目录', error))
  } finally {
    syncing.value = false
  }
}

const bulkOptions = [
  { label: '全部启用', key: 'enable' },
  { label: '全部禁用', key: 'disable' },
]

async function onBulkSelect(key: string | number): Promise<void> {
  const enable = String(key) === 'enable'
  if (bulkRunning.value) return
  const ok = await confirm({
    title: enable ? '启用全部系统级模型？' : '禁用全部系统级模型？',
    message: enable
      ? '把 level=system 的所有模型配置置为启用。禁用状态的旧模型也会一起打开。'
      : '把 level=system 的所有模型配置置为禁用。禁用后网关会拒绝调用全部模型，直到重新启用。',
    okText: enable ? '全部启用' : '全部禁用',
    danger: !enable,
    requireText: enable ? undefined : '禁用',
  })
  if (!ok) return
  bulkRunning.value = true
  try {
    const payload = await batchToggleModels({ enabled: enable, level: 'system' })
    const count = Number(payload?.updated) || 0
    toast.success((enable ? '已启用 ' : '已禁用 ') + count + ' 个模型配置')
    await loadConfigs()
    await loadHealth()
  } catch (error) {
    toast.error(failureText(enable ? '批量启用' : '批量禁用', error))
  } finally {
    bulkRunning.value = false
  }
}

/* ------------------------------ 添加模型 ------------------------------ */

const addOpen = ref(false)
const addPlatform = ref('')
const addSubmitting = ref(false)

const addPlatformOptions = computed(() => {
  const options: Array<{ label: string; value: string }> = [{ label: '全部平台', value: '' }]
  for (const item of platforms.value) {
    options.push({
      label: (item.label || item.platform) + '（' + (Number(item.count) || 0) + ' 个模型）',
      value: item.platform,
    })
  }
  return options
})

function openAdd(): void {
  addPlatform.value = ''
  addOpen.value = true
}

async function submitAdd(): Promise<void> {
  if (addSubmitting.value) return
  addSubmitting.value = true
  try {
    const payload = await syncModels(addPlatform.value || undefined)
    toast.success('同步完成：' + describeSyncResult(payload))
    addOpen.value = false
    await loadConfigs()
    await loadHealth()
  } catch (error) {
    toast.error(failureText('同步模型目录', error))
  } finally {
    addSubmitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 行操作：启用/禁用、测速、删除
 * ------------------------------------------------------------------ */

async function onRowAction(row: ModelRow, key: string): Promise<void> {
  if (key === 'edit') await openEdit(row)
  else if (key === 'toggle') await toggleRow(row)
  else if (key === 'speed') await runSpeedTest(row)
  else if (key === 'delete') await removeOne(row)
}

async function toggleRow(row: ModelRow): Promise<void> {
  const enable = !row.enabled
  const results = await Promise.allSettled(
    row.platforms.map((ref) => updateModelConfig(ref.configId, { enabled: enable })),
  )
  const failed = results.filter((result) => result.status === 'rejected').length
  if (failed === 0) {
    toast.success((enable ? '已启用「' : '已停用「') + row.name + '」')
  } else {
    const first = results.find((result) => result.status === 'rejected') as
      | PromiseRejectedResult
      | undefined
    toast.error(
      (enable ? '启用' : '停用') + '「' + row.name + '」部分平台失败：' + errorMessage(first?.reason),
    )
  }
  await loadConfigs()
}

const speedTesting = ref<number | null>(null)

async function runSpeedTest(row: ModelRow): Promise<void> {
  if (speedTesting.value !== null) return
  const model = row.upstreamIds[0]
  if (!model) {
    toast.warning('该模型没有可测速的上游 id')
    return
  }
  speedTesting.value = row.configIds[0] ?? 0
  try {
    const payload: ModelSpeedTestPayload = await speedTestModel(model)
    toast.success(row.name + ' 测速：' + describeSpeedTest(payload))
    await loadHealth()
  } catch (error) {
    toast.error(failureText('测速「' + row.name + '」', error))
  } finally {
    speedTesting.value = null
  }
}

async function removeOne(row: ModelRow): Promise<void> {
  const ok = await confirm({
    title: '删除「' + row.name + '」？',
    message:
      '会删除该模型在 ' + row.platformCount + ' 个平台上的配置，删除后该模型立即从白名单移除（调用返回模型不存在）。' +
      '下次「同步目录」时如果上游仍在，它会以停用状态重新出现。',
    okText: '删除',
    danger: true,
  })
  if (!ok) return
  const results = await Promise.allSettled(
    row.platforms.map((ref) => deleteModelConfig(ref.configId)),
  )
  const failed = results.filter((result) => result.status === 'rejected').length
  if (failed === 0) toast.success('已删除「' + row.name + '」')
  else toast.error('删除「' + row.name + '」失败 ' + failed + ' 个平台配置')
  await loadConfigs()
  await loadHealth()
}

/* ------------------------------------------------------------------ *
 * 编辑抽屉
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const editing = ref<ModelRow | null>(null)

/** 抽屉表单的空初值（打开时才会被真实行覆盖）。 */
function emptyEditForm(): ModelEditForm {
  return {
    note: '',
    enabled: {},
    contextWindow: null,
    maxOutputTokens: null,
    supportsImages: false,
    supportsTools: false,
    supportsReasoning: false,
  }
}

const form = ref<ModelEditForm>(emptyEditForm())
const formError = ref('')
const submitting = ref(false)

const multiplierNote = computed(() => {
  const row = editing.value
  if (!row) return ''
  if (row.multiplier === null) return '尚未同步倍率，先跑一次「同步目录」。'
  if (row.multiplier < 0) return '该平台按实际 token 计量，没有倍率概念（显示为「无量纲」）。'
  return '倍率由「同步目录」按上游目录写入（后端 PATCH 不接受该字段），这里按平台只读展示。'
})

async function openEdit(row: ModelRow): Promise<void> {
  editing.value = row
  form.value = modelEditForm(row)
  formError.value = ''
  drawerOpen.value = true
}

function setPlatformEnabled(configId: number, value: boolean): void {
  form.value.enabled = { ...form.value.enabled, [configId]: value }
  formError.value = ''
}

async function submitEdit(): Promise<void> {
  if (submitting.value) return
  const row = editing.value
  if (!row) return
  const plan = buildModelEditPlan(row, form.value)
  if (plan.error) {
    formError.value = plan.error
    return
  }
  if (!plan.changed) {
    toast.info('没有改动')
    drawerOpen.value = false
    return
  }
  submitting.value = true
  try {
    const results = await Promise.allSettled(
      plan.updates.map((update) => updateModelConfig(update.id, update.body)),
    )
    const failed = results.filter((result) => result.status === 'rejected').length
    if (failed === 0) {
      toast.success('已保存「' + row.name + '」的 ' + plan.updates.length + ' 个平台配置')
    } else {
      const first = results.find((result) => result.status === 'rejected') as
        | PromiseRejectedResult
        | undefined
      toast.error('保存「' + row.name + '」失败 ' + failed + ' 个：' + errorMessage(first?.reason))
    }
    drawerOpen.value = false
    await loadConfigs()
    await loadHealth()
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 生命周期：平台切换要重取（健康数据按 X-Platform 收窄）
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null

onMounted(() => {
  void loadAll()
  offPlatform = platformStore.onChange(() => {
    void loadHealth()
    if (table.state.filters.platform) table.setFilter('platform', '')
  })
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
  offPlatform?.()
  offPlatform = null
})
</script>

<template>
  <div class="models-page">
    <PageHeader title="模型配置" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadAll()">刷新</Btn>
        <Btn variant="default" :icon="RefreshCw" :loading="syncing" @click="onSync">
          同步目录
        </Btn>
        <n-dropdown trigger="click" :options="bulkOptions" @select="onBulkSelect">
          <Btn variant="default" :icon="ListChecks" :loading="bulkRunning">批量操作</Btn>
        </n-dropdown>
        <Btn variant="primary" :icon="Plus" @click="openAdd">添加模型</Btn>
      </template>
    </PageHeader>

    <Card flush class="models-card">
      <div class="models-filter">
        <FilterBar
          :chips="chips"
          :summary="summary"
          :loading="loading"
          @change="onFilterChange"
          @reset="resetFilters"
          @remove="removeChip"
        >
          <template #default="{ change }">
            <n-input
              class="models-filter__search"
              :value="search"
              placeholder="搜索模型 ID / 别名 / 厂商 / 平台 / 能力"
              clearable
              @update:value="
                (value: string) => {
                  search = value
                  change('q', value)
                }
              "
            />
            <n-select
              class="models-filter__select"
              :value="table.state.filters.vendor"
              :options="vendorSelectOptions"
              @update:value="(value: string | number | null) => applyFilter('vendor', value)"
            />
            <n-select
              class="models-filter__select"
              :value="table.state.filters.platform"
              :options="platformSelectOptions"
              @update:value="(value: string | number | null) => applyFilter('platform', value)"
            />
            <n-select
              class="models-filter__select is-narrow"
              :value="table.state.filters.cap"
              :options="CAPABILITY_OPTIONS"
              @update:value="(value: string | number | null) => applyFilter('cap', value)"
            />
            <n-select
              class="models-filter__select is-narrow"
              :value="table.state.filters.status"
              :options="STATUS_OPTIONS"
              @update:value="(value: string | number | null) => applyFilter('status', value)"
            />
          </template>
        </FilterBar>
      </div>

      <div class="models-body">
        <ErrorBlock
          v-if="loadError && items === null"
          :title="'加载模型失败：' + loadError.message"
          :detail="loadError.detail"
          @retry="loadConfigs()"
        />

        <div v-else-if="firstLoading" class="models-skeleton" aria-busy="true" aria-label="模型加载中">
          <n-skeleton v-for="index in 6" :key="index" height="44px" />
        </div>

        <template v-else>
          <div v-if="healthError" class="models-health-error">
            <Badge tone="warn">健康数据</Badge>
            <span>{{ healthError }}</span>
            <Btn variant="quiet" size="sm" @click="loadHealth()">重试</Btn>
          </div>

          <DataTable
            table-id="models"
            :columns="columns"
            :data="pageData.items"
            row-key="key"
            remote
            clickable-rows
            :loading="loading"
            :page="table.state.page"
            :page-size="table.state.pageSize"
            :item-count="pageData.total"
            :page-sizes="[20, 50, 100]"
            :empty-description="emptyDescription"
            @update:page="table.setPage"
            @update:page-size="table.setPageSize"
            @update:sorter="onSorter"
            @row-click="(row: TableRow) => openEdit(row as ModelRow)"
          >
            <template #empty>
              <Empty :description="emptyDescription">
                <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
                  清除筛选
                </Btn>
                <Btn v-else variant="primary" size="sm" :icon="RefreshCw" :loading="syncing" @click="onSync">
                  同步目录
                </Btn>
              </Empty>
            </template>
          </DataTable>

          <p v-if="officialTotal > 0 && stats.total < officialTotal" class="models-footnote">
            官方目录共 {{ officialTotal }} 个模型，本站已配置 {{ stats.total }} 个。
            未配置的模型对客户端不可见，用「同步目录」补齐。
          </p>
        </template>
      </div>
    </Card>

    <!-- 编辑：别名 / 限制 / 各平台映射 / 启用状态 -->
    <FormDrawer
      v-model:show="drawerOpen"
      title="编辑模型"
      size="lg"
      submit-text="保存"
      :submit-loading="submitting"
      @submit="submitEdit"
    >
      <template v-if="editing">
        <div class="models-edit-head">
          <ModelIcon :vendor="editing.vendor" :size="22" :label="editing.vendorLabel" />
          <div class="models-edit-head__main">
            <span class="models-edit-head__name">{{ editing.name }}</span>
            <Copyable :value="editing.callId" />
            <span class="models-edit-head__meta">
              {{ editing.vendorLabel }} · {{ editing.platformCount }} 个平台 · {{ editing.multiplierText }}
            </span>
          </div>
        </div>

        <FormField
          label="展示名（别名）"
          hint="客户端与 /v1/models 里显示的名字，可中文；留空回退到调用名。"
        >
          <n-input v-model:value="form.note" placeholder="例如 DeepSeek V4.1 Flash" maxlength="255" clearable />
        </FormField>

        <FormField
          label="启用状态"
          :error="formError"
          hint="按平台分别启停；全部停用后该模型从对外白名单里消失。"
        >
          <div class="models-edit-platforms">
            <div
              v-for="ref in editing.platforms"
              :key="'en-' + ref.configId"
              class="models-edit-platform"
            >
              <PlatformLogo :id="ref.platform" :size="18" :label="ref.platformLabel" />
              <span class="models-edit-platform__name">{{ ref.platformLabel }}</span>
              <span class="models-edit-platform__mult tnum">{{ fmtMultiplier(ref.multiplier) }}</span>
              <n-switch
                :value="form.enabled[ref.configId] !== false"
                size="small"
                @update:value="(value: boolean) => setPlatformEnabled(ref.configId, value)"
              />
            </div>
          </div>
        </FormField>

        <FormField label="倍率" :hint="multiplierNote">
          <div class="models-edit-multipliers">
            <div
              v-for="ref in editing.platforms"
              :key="'mult-' + ref.configId"
              class="models-edit-multiplier"
            >
              <span class="models-edit-multiplier__name">{{ ref.platformLabel }}</span>
              <span class="models-edit-multiplier__value tnum">{{ fmtMultiplier(ref.multiplier) }}</span>
              <span class="models-edit-multiplier__raw mono">{{ ref.clientId }}</span>
            </div>
          </div>
        </FormField>

        <FormField label="能力与限制" hint="写在本站覆盖层（_override），同步目录不会冲掉；留空表示沿用上游值。">
          <div class="models-edit-caps">
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">上下文窗口</span>
              <n-input-number
                v-model:value="form.contextWindow"
                :min="0"
                :step="1000"
                placeholder="128000"
                class="models-edit-caps__number"
              />
            </div>
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">最大输出</span>
              <n-input-number
                v-model:value="form.maxOutputTokens"
                :min="0"
                :step="1000"
                placeholder="16000"
                class="models-edit-caps__number"
              />
            </div>
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">支持图片输入</span>
              <n-switch v-model:value="form.supportsImages" size="small" />
            </div>
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">支持工具调用</span>
              <n-switch v-model:value="form.supportsTools" size="small" />
            </div>
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">支持推理</span>
              <n-switch v-model:value="form.supportsReasoning" size="small" />
            </div>
            <p v-if="editing.caps.overridden" class="models-edit-caps__hint">
              <Badge tone="brand">本站覆盖</Badge>
              这些值里至少一项是本站手调的，不是上游原值。
            </p>
          </div>
        </FormField>

        <FormField label="各平台映射" hint="同一模型在不同平台的调用名与落点；复制任意一条给客户端都能直接调用。">
          <div class="models-edit-map">
            <div v-for="ref in editing.platforms" :key="'map-' + ref.configId" class="models-edit-map__row">
              <span class="models-edit-map__platform">
                <PlatformLogo :id="ref.platform" :size="16" :label="ref.platformLabel" />
                {{ ref.platformLabel }}
              </span>
              <Copyable :value="ref.clientId" muted />
              <span class="models-edit-map__upstream mono" :title="'上游 id：' + ref.upstreamId">
                {{ ref.upstreamId }}
              </span>
              <Badge :tone="ref.enabled ? 'ok' : 'neutral'" dot>
                {{ ref.enabled ? '启用' : '停用' }}
              </Badge>
            </div>
          </div>
        </FormField>

        <p v-if="editing.description" class="models-edit-desc">{{ editing.description }}</p>
        <p class="models-edit-hint">
          <HelpTip
            content="删除模型请回到列表用行操作的「删除」；这里只改配置，不删记录。"
            label="关于删除"
          />
        </p>
      </template>
    </FormDrawer>

    <!-- 添加模型：后端没有手工新增端点，新模型只能从平台目录同步进来 -->
    <FormDrawer
      v-model:show="addOpen"
      title="添加模型"
      submit-text="同步该平台"
      :submit-loading="addSubmitting"
      @submit="submitAdd"
    >
      <FormField label="平台" hint="选一个平台只同步它；选「全部平台」等于点一次「同步目录」。">
        <n-select
          :value="addPlatform"
          :options="addPlatformOptions"
          @update:value="(value: string | number | null) => (addPlatform = value === null ? '' : String(value))"
        />
      </FormField>
      <p class="models-edit-desc">
        模型目录由各平台上游下发，本站不手工造条目：同步会把上游新增的模型写进白名单（默认启用），
        已存在的模型只刷新倍率、描述与能力，启停状态与手调覆盖保持不变。
      </p>
      <p class="models-edit-desc">
        同步失败的平台会在结果里点名（例如「该平台没有可用（active）账号」），不会静默少一批模型。
      </p>
    </FormDrawer>
  </div>
</template>

<style scoped>
.models-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.models-card {
  min-width: 0;
}

.models-filter {
  padding: var(--sp-4) var(--sp-5) var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
}

.models-filter__search {
  width: 260px;
  max-width: 100%;
}

.models-filter__select {
  width: 168px;
}

.models-filter__select.is-narrow {
  width: 132px;
}

.models-body {
  padding: var(--sp-3) var(--sp-5) var(--sp-5);
  min-width: 0;
}

.models-skeleton {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) 0;
}

.models-health-error {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.models-footnote {
  margin: var(--sp-3) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-faint);
}

.models-name {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  min-width: 0;
  cursor: pointer;
  border-radius: var(--r-sm);
}

.models-name:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.models-name__main {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.models-name__title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  min-width: 0;
}

.models-name__text {
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.models-name__aliases {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1);
  margin-top: 2px;
}

.models-name__caps {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  margin-top: 2px;
}

.models-cap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
  color: var(--fg-muted);
}

.models-cap--reasoning {
  color: var(--brand);
}

.models-cap--video {
  color: var(--warn);
}

.models-platforms {
  display: flex;
  align-items: center;
  gap: 2px;
}

.models-platform {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
}

.models-platform + .models-platform {
  margin-left: -8px;
}

.models-platform.is-more {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.models-mult {
  font-variant-numeric: tabular-nums;
  color: var(--fg-2);
}

.models-mult.is-warn {
  color: var(--warn);
}

.models-mult.is-ok {
  color: var(--ok);
}

.models-mult.is-muted {
  color: var(--fg-faint);
}

.models-limits {
  display: grid;
  gap: 1px;
}

.models-limits__line {
  display: flex;
  align-items: baseline;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.models-limits__label {
  color: var(--fg-faint);
}

.models-health {
  display: grid;
  gap: 2px;
}

.models-health__line {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.models-health__label {
  color: var(--fg-faint);
}

.models-health__sub {
  color: var(--fg-faint);
}

.models-status {
  display: grid;
  gap: 2px;
}

.models-status__sub {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-faint);
}

.models-actions {
  display: flex;
  justify-content: flex-end;
}

.models-dash {
  color: var(--fg-faint);
}

/* 编辑抽屉 */
.models-edit-head {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  padding-bottom: var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
}

.models-edit-head__main {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.models-edit-head__name {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.models-edit-head__meta {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.models-edit-platforms,
.models-edit-multipliers,
.models-edit-map {
  display: grid;
  gap: var(--sp-1);
}

.models-edit-platform,
.models-edit-multiplier {
  display: grid;
  grid-template-columns: 18px 1fr auto auto;
  align-items: center;
  gap: var(--sp-2);
  padding: 4px 0;
}

.models-edit-platform__name,
.models-edit-multiplier__name {
  color: var(--fg);
}

.models-edit-platform__mult,
.models-edit-multiplier__value {
  color: var(--fg-2);
  font-variant-numeric: tabular-nums;
}

.models-edit-multiplier__raw {
  grid-column: 2 / -1;
  color: var(--fg-faint);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.models-edit-caps {
  display: grid;
  gap: var(--sp-2);
}

.models-edit-caps__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}

.models-edit-caps__label {
  color: var(--fg-2);
}

.models-edit-caps__number {
  width: 160px;
}

.models-edit-caps__hint {
  margin: 0;
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.models-edit-map__row {
  display: grid;
  grid-template-columns: 128px minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--sp-2);
  padding: 4px 0;
  border-bottom: 1px solid var(--border-soft);
}

.models-edit-map__platform {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  color: var(--fg-2);
}

.models-edit-map__upstream {
  color: var(--fg-faint);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.models-edit-desc {
  margin: 0;
  font-size: var(--fs-small);
  line-height: 1.6;
  color: var(--fg-muted);
}

.models-edit-hint {
  margin: 0;
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  color: var(--fg-faint);
}

@media (max-width: 640px) {
  .models-filter {
    padding: var(--sp-3) var(--sp-4);
  }

  .models-body {
    padding: var(--sp-2) var(--sp-4) var(--sp-4);
  }

  .models-filter__search,
  .models-filter__select,
  .models-filter__select.is-narrow {
    width: 100%;
  }

  .models-edit-map__row {
    grid-template-columns: 1fr auto;
  }

  .models-edit-map__upstream {
    grid-column: 1 / -1;
  }
}
</style>
