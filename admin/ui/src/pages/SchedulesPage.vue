<script setup lang="ts">
/**
 * S11 定时任务 /schedules（模板 B：PageHeader + FilterBar + DataTable + FormDrawer）
 *
 * 契约：docs/ui-v2/contracts/schedules.md　设计稿：06-pages-d.md P17
 * 页面职责：定时任务的查看 / 新建 / 编辑 / 启停 / 立即执行 / 删除，以及「全局出口与并发」默认值。
 *
 * 与旧版（admin/console/console-app.js:15528-15947）保持一致的三点：
 *   · 任务列表**不按当前平台过滤**（各平台的签到/保活必须一眼看到），但订阅
 *     usePlatformStore().onChange —— 切平台后重拉一次（后端按 X-Platform 记日志，数据本身全局）。
 *   · 「下次运行」由后端算（next_run_at 是 naive UTC），前端只展示「今天 09:00」。
 *   · 立即执行的结果是后端 run_task 的原样返回值，摘要与落库的 last_result 同源；
 *     执行后刷新该行（有 running 时按 4s 轮询到结束）。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch, type VNodeChild } from 'vue'
import { NCheckbox, NCheckboxGroup, NDatePicker, NInput, NInputNumber, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import { Plus, RefreshCw, Settings2 } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  CodeBlock,
  DataTable,
  DetailDrawer,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  HelpTip,
  KV,
  RowActions,
  type FilterChip,
  type KvItem,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, usePolling, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { FMT_EMPTY, fmtInt, fmtTime } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import {
  CALENDAR_ZONE_HINT,
  DEFAULT_PROXY_FORM,
  INTERVAL_UNIT_OPTIONS,
  MODE_OPTIONS,
  RESULT_FIELD_LABELS,
  WEEKDAY_OPTIONS,
  buildProxyPoolOptions,
  buildScheduleInput,
  createSchedule,
  deleteSchedule,
  emptyScheduleForm,
  filterScheduleRows,
  getGlobalScheduleProxy,
  isCalendarMode,
  isCheckinTask,
  listProxyEndpoints,
  listScheduleTasks,
  listSchedules,
  paginateScheduleRows,
  parseScheduleResult,
  putGlobalScheduleProxy,
  runSchedule,
  scheduleFormOf,
  scheduleStats,
  sortScheduleRows,
  toScheduleRows,
  toggleSchedule,
  updateSchedule,
  validateScheduleForm,
  type ProxyListPayload,
  type ScheduleForm,
  type ScheduleGlobalProxy,
  type ScheduleItem,
  type ScheduleRow,
  type ScheduleTaskOption,
  type ScheduleTiming,
} from '@/api/schedules'

/** 旧版 #panel-schedules 的说明文字（原样保留，收进 ⓘ）。 */
const HELP_TEXT =
  '定时统计积分、同步模型、各平台签到与保活。调度线程随服务启动运行，到点自动执行并回填结果。' +
  '列表按平台分组，不随顶部平台切换隐藏。'

/** 全局出口与并发的说明（旧版「全局出口与并发」弹窗头部）。 */
const GLOBAL_PROXY_HELP =
  '所有「没单独配置」的定时任务统一吃这套默认；单个任务里填了值仍以任务为准。'

const toast = useToast()
const confirm = useConfirm()
const platformStore = usePlatformStore()
const table = useTableQuery('schedules', {
  pageSize: 20,
  filters: { q: '', platform: '', task: '', state: '', result: '' },
})

/* ------------------------------------------------------------------ *
 * 列表数据
 * ------------------------------------------------------------------ */

const schedules = ref<ScheduleItem[] | null>(null)
const globalProxy = ref<ScheduleGlobalProxy | null>(null)
const taskCatalog = ref<ScheduleTaskOption[]>([])
const proxyPayload = ref<ProxyListPayload | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const runningIds = ref<number[]>([])

const allRows = computed(() =>
  toScheduleRows(schedules.value, { globalProxy: globalProxy.value }),
)
const filteredRows = computed(() => filterScheduleRows(allRows.value, table.state.filters))
const sortedRows = computed(() =>
  sortScheduleRows(filteredRows.value, table.state.sortBy, table.state.sortOrder),
)
const pageData = computed(() =>
  paginateScheduleRows(sortedRows.value, table.state.page, table.state.pageSize),
)
const stats = computed(() => scheduleStats(allRows.value))
const filteredStats = computed(() => scheduleStats(filteredRows.value))

const firstLoading = computed(() => loading.value && schedules.value === null)
const hasFilters = computed(() =>
  Boolean(
    table.state.filters.q ||
      table.state.filters.platform ||
      table.state.filters.task ||
      table.state.filters.state ||
      table.state.filters.result,
  ),
)

const anyRunning = computed(() => allRows.value.some((row) => row.running))

const summary = computed(() => {
  const current = filteredStats.value
  const base = '共 ' + current.total + ' 个任务 · 启用 ' + current.enabled + ' · 停用 ' + current.disabled
  const extra: string[] = []
  if (current.problem > 0) extra.push(current.problem + ' 个最近失败')
  if (current.running > 0) extra.push(current.running + ' 个执行中')
  const textTail = extra.length ? ' · ' + extra.join(' · ') : ''
  return hasFilters.value
    ? '筛选出 ' + current.total + ' / 共 ' + stats.value.total + ' 个任务' + textTail
    : base + textTail
})

const emptyDescription = computed(() =>
  hasFilters.value
    ? '没有符合条件的定时任务。清空筛选后可看到全部任务。'
    : '还没有定时任务。新建一个任务，按周期自动刷新积分 / 同步模型 / 跑各平台签到。',
)

/** 搜索框受控副本：输入即时回显，300ms 后由 FilterBar 写进路由 query。 */
const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => {
    search.value = value
  },
)

const platformFilterOptions = computed(() => {
  const seen = new Map<string, string>()
  for (const row of allRows.value) {
    if (row.platform && !seen.has(row.platform)) seen.set(row.platform, row.platformLabel)
  }
  return [
    { label: '平台：全部', value: '' },
    ...Array.from(seen.entries())
      .sort((a, b) => a[1].localeCompare(b[1], 'zh-CN'))
      .map(([value, label]) => ({ label, value })),
  ]
})

const taskFilterOptions = computed(() => {
  const options = (taskCatalog.value.length ? taskCatalog.value : []).map((item) => ({
    label: item.label,
    value: item.value,
  }))
  if (!options.length) {
    for (const row of allRows.value) {
      if (!options.some((option) => option.value === row.task)) {
        options.push({ label: row.taskLabel, value: row.task })
      }
    }
  }
  return [{ label: '类型：全部', value: '' }, ...options]
})

const stateFilterOptions = [
  { label: '状态：全部', value: '' },
  { label: '已启用', value: 'enabled' },
  { label: '已停用', value: 'disabled' },
  { label: '已到期未跑', value: 'due' },
]

const resultFilterOptions = [
  { label: '结果：全部', value: '' },
  { label: '成功', value: 'ok' },
  { label: '部分失败', value: 'warn' },
  { label: '失败', value: 'danger' },
  { label: '尚未运行', value: 'none' },
]

const chips = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: '搜索', value: filters.q })
  if (filters.platform) {
    const hit = platformFilterOptions.value.find((option) => option.value === filters.platform)
    list.push({ key: 'platform', label: '平台', value: hit?.label || filters.platform })
  }
  if (filters.task) {
    const hit = taskFilterOptions.value.find((option) => option.value === filters.task)
    list.push({ key: 'task', label: '类型', value: hit?.label || filters.task })
  }
  if (filters.state) {
    const hit = stateFilterOptions.find((option) => option.value === filters.state)
    list.push({ key: 'state', label: '状态', value: hit?.label.replace('状态：', '') || '' })
  }
  if (filters.result) {
    const hit = resultFilterOptions.find((option) => option.value === filters.result)
    list.push({ key: 'result', label: '结果', value: hit?.label.replace('结果：', '') || '' })
  }
  return list
})

const SORT_KEY_MAP: Record<string, string> = {
  name: 'name',
  platform: 'platform',
  cycle: 'next_run_at',
  last: 'last_run_at',
}

function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
  if (table.state.sortBy !== stateKey) return false
  if (table.state.sortOrder === 'asc') return 'ascend'
  if (table.state.sortOrder === 'desc') return 'descend'
  return false
}

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

function renderName(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  const children: VNodeChild[] = [
    h('span', { class: 'sc-name', title: item.displayName }, item.displayName || FMT_EMPTY),
  ]
  if (item.running) {
    children.push(h(Badge, { tone: 'brand', dot: true, class: 'sc-name__badge' }, { default: () => '执行中' }))
  }
  if (item.stopAfter) {
    children.push(
      h(
        'span',
        { class: 'sc-name__stop', title: '到达该时间后停止自动领取（本地时间）' },
        '停止于 ' + fmtTime(item.stopAfter),
      ),
    )
  }
  return h('div', { class: 'sc-name-cell' }, children)
}

function renderTask(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  return h('div', { class: 'sc-task' }, [
    h('span', { class: 'sc-task__label', title: item.task }, item.taskLabel || FMT_EMPTY),
    h('span', { class: 'sc-task__id mono', title: '任务标识' }, item.task),
  ])
}

function renderScope(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  return h('div', { class: 'sc-scope' }, [
    h('span', { title: '落库平台：' + (item.platform || '—') }, item.platformLabel || FMT_EMPTY),
    h('span', { class: 'sc-scope__hint' }, '该平台全部账号'),
  ])
}

function renderCycle(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  const children: VNodeChild[] = [
    h('span', { class: 'sc-cycle__main' }, item.cycleText || FMT_EMPTY),
  ]
  if (item.zoneHint) {
    children.push(h('span', { class: 'sc-cycle__zone' }, item.zoneHint))
  }
  children.push(
    h(
      'span',
      { class: item.enabled && item.dueSoon ? 'sc-cycle__next is-due' : 'sc-cycle__next' },
      '下次：' + item.nextText,
    ),
  )
  return h('div', { class: 'sc-cycle' }, children)
}

function renderProxy(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  const hints: string[] = []
  hints.push(item.concurrency > 0 ? '并发 ' + item.concurrency : '并发：自动')
  hints.push(
    item.budgetMinutes > 0 ? '单轮 ' + item.budgetMinutes + ' 分钟' : '单轮预算：默认 8 分钟',
  )
  hints.push(item.staleMinutes > 0 ? '只刷 ' + item.staleMinutes + ' 分钟未更新' : 'stale：默认 60 分钟')
  return h(
    'div',
    { class: 'sc-proxy', title: hints.join('\n') },
    [
      h('span', { class: item.proxyPool ? 'sc-proxy__main' : 'sc-proxy__main is-global' }, item.proxyText),
      h('span', { class: 'sc-proxy__hint' }, hints[0] + ' · ' + hints[2]),
    ],
  )
}

function renderResult(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  const result = item.result
  const children: VNodeChild[] = [
    h('div', { class: 'sc-result__head' }, [
      h(Badge, { tone: result.tone }, { default: () => result.label }),
      h(
        'span',
        { class: 'sc-result__time', title: '上次运行时间' },
        item.lastRunAt ? fmtTime(item.lastRunAt) : '未运行',
      ),
    ]),
  ]
  if (result.summary) {
    children.push(h('span', { class: 'sc-result__summary', title: result.summary }, result.summary))
  }
  return h('div', { class: 'sc-result' }, children)
}

function renderEnabled(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  return h(NSwitch, {
    value: item.enabled,
    size: 'small',
    'aria-label': item.enabled ? '停用任务' : '启用任务',
    loading: togglingIds.value.includes(item.id),
    'onUpdate:value': (value: boolean) => {
      void onToggle(item, value)
    },
  })
}

function renderActions(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  return h(RowActions, {
    actions: [
      { key: 'run', label: '立即执行', disabled: runningIds.value.includes(item.id) },
      { key: 'edit', label: '编辑' },
      { key: 'result', label: '结果详情' },
      { key: 'delete', label: '删除', danger: true },
    ],
    max: 2,
    onSelect: (key: string) => {
      void onRowAction(item, key)
    },
  })
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    title: '任务名',
    mobile: 'title',
    minWidth: 260,
    sortable: true,
    sortOrder: sortOrderOf('name'),
    render: renderName,
  },
  {
    key: 'task',
    title: '类型',
    mobile: 'meta',
    width: 168,
    render: renderTask,
  },
  {
    key: 'platform',
    title: '平台 / 账号范围',
    mobile: 'subtitle',
    width: 176,
    sortable: true,
    sortOrder: sortOrderOf('platform'),
    render: renderScope,
  },
  {
    key: 'cycle',
    title: '执行时间',
    mobile: 'meta',
    minWidth: 200,
    sortable: true,
    sortOrder: sortOrderOf('next_run_at'),
    render: renderCycle,
  },
  {
    key: 'proxy',
    title: '出口代理组',
    mobile: 'meta',
    width: 200,
    render: renderProxy,
  },
  {
    key: 'last',
    title: '最近结果',
    mobile: 'meta',
    minWidth: 220,
    sortable: true,
    sortOrder: sortOrderOf('last_run_at'),
    render: renderResult,
  },
  {
    key: 'enabled',
    title: '启用',
    mobile: 'meta',
    width: 72,
    hideable: false,
    render: renderEnabled,
  },
  {
    key: 'actions',
    title: '操作',
    mobile: 'actions',
    width: 168,
    hideable: false,
    fixed: 'right',
    render: renderActions,
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

async function loadSchedules(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const [payload, global] = await Promise.all([
      listSchedules(current.signal),
      getGlobalScheduleProxy(current.signal).catch(() => globalProxy.value),
    ])
    if (controller !== current) return
    schedules.value = Array.isArray(payload?.items) ? payload.items : []
    if (global) globalProxy.value = global
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / table.state.pageSize))
    if (table.state.page > maxPage) table.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: errorMessage(error), detail }
    toast.error(failureText(schedules.value === null ? '加载定时任务' : '刷新定时任务', error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

/* ------------------------------------------------------------------ *
 * 筛选 / 排序 / 分页
 * ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ *
 * 行操作：立即执行 / 启停 / 编辑 / 删除 / 结果详情
 * ------------------------------------------------------------------ */

const togglingIds = ref<number[]>([])

async function onRowAction(row: ScheduleRow, key: string): Promise<void> {
  if (key === 'run') await runOne(row)
  else if (key === 'edit') await openEdit(row)
  else if (key === 'delete') await removeOne(row)
  else if (key === 'result') openResult(row)
}

/** 立即执行：提示结果摘要 → 刷新该行（有 running 时轮询到结束）。 */
async function runOne(row: ScheduleRow): Promise<void> {
  if (runningIds.value.includes(row.id)) return
  runningIds.value = [...runningIds.value, row.id]
  try {
    const payload = await runSchedule(row.id)
    const view = parseScheduleResult(JSON.stringify(payload?.result ?? {}))
    const textTail = view.summary ? '：' + view.summary : ''
    if (view.tone === 'danger') toast.error('执行「' + row.displayName + '」失败' + textTail)
    else if (view.tone === 'warn') toast.warning('「' + row.displayName + '」已执行：' + view.label + textTail)
    else toast.success('「' + row.displayName + '」已执行：' + view.label + textTail)
  } catch (error) {
    toast.error(failureText('立即执行', error))
  } finally {
    runningIds.value = runningIds.value.filter((id) => id !== row.id)
    await loadSchedules()
  }
}

async function onToggle(row: ScheduleRow, next: boolean): Promise<void> {
  if (togglingIds.value.includes(row.id)) return
  if (!next) {
    const ok = await confirm({
      title: '停用任务「' + row.displayName + '」？',
      message: '停用后不再按周期执行，下次运行时间会被清空；已有执行结果保留。随时可以再启用。',
      okText: '停用',
    })
    if (!ok) return
  }
  togglingIds.value = [...togglingIds.value, row.id]
  try {
    const payload = await toggleSchedule(row.id)
    const enabled = Boolean(payload?.enabled)
    toast.success(enabled ? '已启用「' + row.displayName + '」' : '已停用「' + row.displayName + '」')
    await loadSchedules()
  } catch (error) {
    toast.error(failureText(next ? '启用任务' : '停用任务', error))
  } finally {
    togglingIds.value = togglingIds.value.filter((id) => id !== row.id)
  }
}

async function removeOne(row: ScheduleRow): Promise<void> {
  const ok = await confirm({
    title: '删除定时任务「' + row.displayName + '」？',
    message: '删除后该任务不再执行，已产生的执行结果与历史记录保留在日志里。',
    okText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteSchedule(row.id)
    toast.success('已删除任务「' + row.displayName + '」')
    await loadSchedules()
  } catch (error) {
    toast.error(failureText('删除任务', error))
  }
}

/* ------------------------------------------------------------------ *
 * 轮询：有任务在执行时每 4s 刷新到结束（05-patterns §3）
 * ------------------------------------------------------------------ */

const poll = usePolling(() => loadSchedules(), 4000, { immediate: false })

watch(anyRunning, (running) => {
  if (running) poll.start()
  else poll.stop()
})

/* ------------------------------------------------------------------ *
 * 结果详情抽屉
 * ------------------------------------------------------------------ */

const resultOpen = ref(false)
const resultRow = ref<ScheduleRow | null>(null)
const resultDetailRows = ref<TableRow[]>([])

const resultKv = computed<KvItem[]>(() => {
  const parsed = resultRow.value?.result.parsed
  if (!parsed) return []
  const items: KvItem[] = []
  for (const [key, value] of Object.entries(parsed)) {
    if (Array.isArray(value) || (value && typeof value === 'object')) continue
    const label = RESULT_FIELD_LABELS[key] || key
    const textValue =
      typeof value === 'boolean' ? (value ? '是' : '否') : value === null || value === '' ? '' : String(value)
    items.push({ label, value: textValue.length > 240 ? textValue.slice(0, 240) + '…' : textValue })
  }
  return items
})

const resultDetailColumns: TableColumn[] = [
  { key: 'account', title: '账号', mobile: 'title', minWidth: 160 },
  { key: 'status', title: '结果', mobile: 'meta', width: 96 },
  { key: 'credit', title: '积分', mobile: 'meta', width: 88, numeric: true },
  { key: 'reason', title: '说明', mobile: 'meta', minWidth: 200 },
]

function openResult(row: ScheduleRow): void {
  resultRow.value = row
  resultDetailRows.value = row.result.detail.map((item, index) => ({
    key: String(item.account_id ?? index),
    account: String(item.account ?? item.name ?? '#' + String(item.account_id ?? index)),
    status: item.ok === false ? '失败' : String(item.status || '成功'),
    credit: item.credit === undefined || item.credit === null ? '' : fmtInt(item.credit),
    reason: String(item.reason ?? item.error ?? ''),
  }))
  resultOpen.value = true
}

/* ------------------------------------------------------------------ *
 * 表单抽屉（新建 / 编辑）
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const form = ref<ScheduleForm>(emptyScheduleForm())
const formErrors = ref<Record<string, string>>({})
const submitting = ref(false)
const catalogLoading = ref(false)
const catalogError = ref('')

const isEdit = computed(() => form.value.id !== null)
const drawerTitle = computed(() => (isEdit.value ? '编辑定时任务' : '新建定时任务'))
const submitText = computed(() => (isEdit.value ? '保存' : '创建任务'))
const showStopAfter = computed(() => isCheckinTask(form.value.task))
const showCalendar = computed(() => isCalendarMode(form.value.mode))

const modeOptions = MODE_OPTIONS
const unitOptions = INTERVAL_UNIT_OPTIONS
const weekdayOptions = WEEKDAY_OPTIONS

const taskOptions = computed(() => {
  const base = taskCatalog.value.map((item) => ({
    label: item.label,
    value: item.value,
  }))
  if (!form.value.task || base.some((option) => option.value === form.value.task)) return base
  // 任务目录里已没有该任务（平台下线 / 改名）：保留当前值，别让它显示成空白。
  return [{ label: form.value.task + '（目录中已无此任务）', value: form.value.task }, ...base]
})

const selectedTask = computed(() =>
  taskCatalog.value.find((item) => item.value === form.value.task) || null,
)

const selectedTaskPlatform = computed(() => {
  const hit = selectedTask.value
  if (!hit) return ''
  const regions = hit.platforms?.length ? hit.platforms : [hit.platform]
  return hit.platform_label + '（' + regions.join(' / ') + '）'
})

const catalogHint = computed(() => {
  if (catalogError.value) return catalogError.value
  if (catalogLoading.value) return '正在加载任务清单…'
  if (!taskCatalog.value.length) return '任务清单为空：只能提交后端已注册的任务'
  if (!selectedTask.value) return '该任务已不在后端的可选清单里，后端会拒绝保存'
  return '平台由任务决定：' + (selectedTask.value.platform_label || selectedTask.value.platform)
})

async function ensureCatalog(force = false): Promise<void> {
  if (catalogLoading.value) return
  if (taskCatalog.value.length && !force) return
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const payload = await listScheduleTasks()
    taskCatalog.value = Array.isArray(payload?.items) ? payload.items : []
  } catch (error) {
    catalogError.value = failureText('加载任务清单', error)
  } finally {
    catalogLoading.value = false
  }
}

async function ensureProxies(force = false): Promise<void> {
  if (proxyPayload.value && !force) return
  try {
    proxyPayload.value = await listProxyEndpoints()
  } catch {
    // 拉不到代理组不影响保存：下拉里会只剩「跟随全局」，任务级 proxy_pool 原值保留。
    proxyPayload.value = null
  }
}

const proxyOptions = computed(() =>
  buildProxyPoolOptions(proxyPayload.value, globalProxy.value, form.value.proxyPool),
)

function resetFormErrors(): void {
  formErrors.value = {}
}

async function openCreate(): Promise<void> {
  form.value = emptyScheduleForm(taskCatalog.value[0]?.value || 'refresh_balances')
  resetFormErrors()
  drawerOpen.value = true
  await Promise.all([ensureCatalog(), ensureProxies()])
}

async function openEdit(row: ScheduleRow): Promise<void> {
  const item = schedules.value?.find((schedule) => schedule.id === row.id)
  if (!item) {
    toast.warning('该任务已不存在，请刷新列表')
    return
  }
  form.value = scheduleFormOf(item)
  resetFormErrors()
  drawerOpen.value = true
  await Promise.all([ensureCatalog(), ensureProxies()])
}

function onTaskChange(value: string): void {
  form.value.task = value
  formErrors.value = { ...formErrors.value, task: '' }
  if (!isCheckinTask(value)) form.value.stopAfter = ''
}

function onModeChange(value: ScheduleTiming['mode'] | string): void {
  form.value.mode = value as ScheduleTiming['mode']
  formErrors.value = { ...formErrors.value, time: '', weekdays: '', monthday: '' }
}

function onWeekdaysChange(value: Array<string | number>): void {
  form.value.weekdays = value.map((day) => Number(day)).filter((day) => day >= 0 && day <= 6)
  formErrors.value = { ...formErrors.value, weekdays: '' }
}

async function submit(): Promise<void> {
  if (submitting.value) return
  const errors = validateScheduleForm(form.value)
  formErrors.value = errors
  if (Object.keys(errors).length) return
  const input = buildScheduleInput(form.value, taskCatalog.value)
  submitting.value = true
  try {
    if (isEdit.value && form.value.id !== null) {
      await updateSchedule(form.value.id, input)
      toast.success('已保存任务「' + (input.name || '定时任务') + '」')
    } else {
      await createSchedule(input)
      toast.success('已创建任务「' + (input.name || '定时任务') + '」')
      table.setPage(1)
    }
    drawerOpen.value = false
    await loadSchedules()
  } catch (error) {
    toast.error(failureText(isEdit.value ? '保存任务' : '创建任务', error))
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 全局出口与并发
 * ------------------------------------------------------------------ */

const globalOpen = ref(false)
const globalForm = ref<ScheduleGlobalProxy>({ ...DEFAULT_PROXY_FORM, proxy_pool: '', concurrency: 0, time_budget_minutes: 0, stale_minutes: 0 })
const globalSaving = ref(false)

const globalProxyOptions = computed(() =>
  buildProxyPoolOptions(proxyPayload.value, globalProxy.value, globalForm.value.proxy_pool),
)

async function openGlobalProxy(): Promise<void> {
  await ensureProxies(true)
  globalForm.value = {
    proxy_pool: globalProxy.value?.proxy_pool || '',
    concurrency: Number(globalProxy.value?.concurrency || 0),
    time_budget_minutes: Number(globalProxy.value?.time_budget_minutes || 0),
    stale_minutes: Number(globalProxy.value?.stale_minutes || 0),
  }
  globalOpen.value = true
}

async function saveGlobalProxy(): Promise<void> {
  if (globalSaving.value) return
  globalSaving.value = true
  try {
    const payload = await putGlobalScheduleProxy(globalForm.value)
    globalProxy.value = {
      proxy_pool: payload?.proxy_pool ?? globalForm.value.proxy_pool,
      concurrency: Number(payload?.concurrency ?? globalForm.value.concurrency),
      time_budget_minutes: Number(payload?.time_budget_minutes ?? globalForm.value.time_budget_minutes),
      stale_minutes: Number(payload?.stale_minutes ?? globalForm.value.stale_minutes),
    }
    toast.success('已保存全局出口与并发，下一轮任务执行即生效')
    globalOpen.value = false
    await loadSchedules()
  } catch (error) {
    toast.error(failureText('保存全局出口与并发', error))
  } finally {
    globalSaving.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let unsubscribePlatform: (() => void) | null = null

onMounted(() => {
  void loadSchedules()
  void ensureCatalog()
  unsubscribePlatform = platformStore.onChange(() => {
    // 列表本身不按平台过滤（各平台任务都要看得到），切平台后重拉一次即可。
    void loadSchedules()
  })
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
  unsubscribePlatform?.()
  unsubscribePlatform = null
})
</script>

<template>
  <div class="schedules-page">
    <PageHeader title="定时任务" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadSchedules()">刷新</Btn>
        <Btn variant="quiet" :icon="Settings2" @click="openGlobalProxy">全局出口与并发</Btn>
        <Btn variant="primary" :icon="Plus" @click="openCreate">新建任务</Btn>
      </template>
    </PageHeader>

    <p v-if="poll.paused.value" class="schedules-banner" role="status">
      {{ poll.pausedMessage.value }}
      <Btn variant="quiet" size="sm" @click="poll.resume()">恢复</Btn>
    </p>

    <Card flush class="schedules-card">
      <div class="schedules-filter">
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
              class="schedules-filter__search"
              :value="search"
              placeholder="搜索任务名 / 类型 / 平台"
              clearable
              @update:value="
                (value: string) => {
                  search = value
                  change('q', value)
                }
              "
            />
            <n-select
              class="schedules-filter__select"
              :value="table.state.filters.platform"
              :options="platformFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('platform', value)"
            />
            <n-select
              class="schedules-filter__select"
              :value="table.state.filters.task"
              :options="taskFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('task', value)"
            />
            <n-select
              class="schedules-filter__select"
              :value="table.state.filters.state"
              :options="stateFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('state', value)"
            />
            <n-select
              class="schedules-filter__select"
              :value="table.state.filters.result"
              :options="resultFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('result', value)"
            />
          </template>
        </FilterBar>
      </div>

      <div class="schedules-body">
        <ErrorBlock
          v-if="loadError && schedules === null"
          :title="'加载定时任务失败：' + loadError.message"
          :detail="loadError.detail"
          @retry="loadSchedules()"
        />

        <div v-else-if="firstLoading" class="schedules-skeleton" aria-busy="true" aria-label="定时任务加载中">
          <n-skeleton v-for="index in 6" :key="index" height="44px" />
        </div>

        <DataTable
          v-else
          table-id="schedules"
          :columns="columns"
          :data="pageData.items"
          row-key="key"
          remote
          :loading="loading"
          :page="table.state.page"
          :page-size="table.state.pageSize"
          :item-count="pageData.total"
          :page-sizes="[20, 50, 100]"
          :empty-description="emptyDescription"
          @update:page="table.setPage"
          @update:page-size="table.setPageSize"
          @update:sorter="onSorter"
        >
          <template #empty>
            <Empty :description="emptyDescription">
              <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">清除筛选</Btn>
              <Btn v-else variant="primary" size="sm" :icon="Plus" @click="openCreate">新建任务</Btn>
            </Empty>
          </template>
        </DataTable>
      </div>
    </Card>

    <!-- 新建 / 编辑 -->
    <FormDrawer
      v-model:show="drawerOpen"
      :title="drawerTitle"
      size="lg"
      :submit-text="submitText"
      :submit-loading="submitting"
      @submit="submit"
    >
      <FormField
        label="任务名称"
        :error="formErrors.name"
        hint="留空按执行周期自动命名（例如「每 1 天 · 刷新平台总积分」）。"
      >
        <n-input v-model:value="form.name" placeholder="留空自动命名" maxlength="64" clearable />
      </FormField>

      <FormField label="任务类型" required :error="formErrors.task" :hint="catalogHint">
        <n-select
          :value="form.task"
          :options="taskOptions"
          :loading="catalogLoading"
          filterable
          placeholder="选择任务"
          @update:value="onTaskChange"
        />
      </FormField>

      <FormField label="平台 / 账号范围" hint="由任务类型决定，作用于该平台的全部账号。">
        <span class="schedules-readonly">{{ selectedTaskPlatform || FMT_EMPTY }}</span>
      </FormField>

      <FormField label="执行周期" required>
        <n-select :value="form.mode" :options="modeOptions" @update:value="onModeChange" />
      </FormField>

      <FormField
        v-if="!showCalendar"
        label="执行间隔"
        required
        :error="formErrors.interval"
        :hint="
          form.mode === 'preset'
            ? '系统预设：沿用这里填的间隔；token 保活另有系统整点时间。'
            : '按固定间隔执行，实际触发时间有分钟级随机抖动（防准点机器人特征）。'
        "
      >
        <div class="schedules-interval">
          <n-input-number v-model:value="form.intervalValue" :min="1" :max="5256000" :step="1" />
          <n-select v-model:value="form.intervalUnit" :options="unitOptions" class="schedules-interval__unit" />
        </div>
      </FormField>

      <template v-if="showCalendar">
        <FormField
          label="执行时间"
          required
          :error="formErrors.time"
          :hint="'时区口径：' + CALENDAR_ZONE_HINT + '（与服务器时区无关）'"
        >
          <n-input v-model:value="form.time" placeholder="09:00" maxlength="5" class="schedules-time" />
        </FormField>

        <FormField v-if="form.mode === 'weekly'" label="执行日" required :error="formErrors.weekdays">
          <n-checkbox-group :value="form.weekdays" @update:value="onWeekdaysChange">
            <div class="schedules-weekdays">
              <n-checkbox v-for="day in weekdayOptions" :key="day.value" :value="day.value" :label="day.label" />
            </div>
          </n-checkbox-group>
        </FormField>

        <FormField
          v-if="form.mode === 'monthly'"
          label="每月几号"
          required
          :error="formErrors.monthday"
          hint="当月没有该日期时，在当月最后一天执行。"
        >
          <n-input-number v-model:value="form.monthday" :min="1" :max="31" :step="1" />
        </FormField>
      </template>

      <FormField
        label="出口代理组"
        :hint="
          '留空 = 跟随全局默认（当前：' +
          (globalProxy?.proxy_pool ? '代理组 ' + globalProxy.proxy_pool : '直连') +
          '）。在「设置 › 出口代理节点」里配好组名后这里才能选。'
        "
      >
        <n-select
          :value="form.proxyPool"
          :options="proxyOptions"
          placeholder="跟随全局默认"
          @update:value="(value: string) => (form.proxyPool = value)"
        />
      </FormField>

      <div class="schedules-grid2">
        <FormField label="并发数" :error="formErrors.concurrency" hint="0 = 自动（各节点并发之和）。">
          <n-input-number v-model:value="form.concurrency" :min="0" :max="64" :step="1" />
        </FormField>
        <FormField label="单轮时间预算（分钟）" :error="formErrors.budget" hint="0 = 默认 8 分钟，到点就停。">
          <n-input-number v-model:value="form.budgetMinutes" :min="0" :max="720" :step="1" />
        </FormField>
        <FormField
          label="只刷超过 N 分钟未更新的号"
          :error="formErrors.stale"
          hint="0 = 默认 60 分钟。刚刷过的号直接跳过。"
        >
          <n-input-number v-model:value="form.staleMinutes" :min="0" :max="10080" :step="1" />
        </FormField>
      </div>

      <FormField
        v-if="showStopAfter"
        label="停止领取时间"
        :error="formErrors.stopAfter"
        hint="到达该时间后停止自动领取（本地时间）。留空不限制，活动结束会自动停止。"
      >
        <n-date-picker
          type="datetime"
          :formatted-value="form.stopAfter || null"
          value-format="yyyy-MM-dd'T'HH:mm"
          clearable
          placeholder="留空不限制"
          @update:formatted-value="(value: string | null) => (form.stopAfter = value || '')"
        />
      </FormField>

      <FormField label="启用任务">
        <div class="schedules-enabled">
          <n-switch v-model:value="form.enabled" size="small" />
          <span>按设置的周期自动执行</span>
        </div>
      </FormField>

      <p class="schedules-hint">
        <HelpTip :content="'成长计划建议每 60 分钟巡检：逐账号补每日活跃、签到、领取旅行礼物、兑换连登奖励并使用免费抽奖次数。每天最多一次成功活跃对话；每日最多开启一批盲盒（5 个以内）、抽奖 5 次。进度持久化，重复运行会跳过已完成项。'" label="成长计划执行说明" />
        一个出口 IP 上跑几十个号会被上游拉黑：配好出口代理组后，本任务会分散到该组节点执行。
      </p>
    </FormDrawer>

    <!-- 全局出口与并发 -->
    <FormDrawer
      v-model:show="globalOpen"
      title="全局出口与并发"
      :submit-text="'保存全局默认'"
      :submit-loading="globalSaving"
      @submit="saveGlobalProxy"
    >
      <p class="schedules-hint">{{ GLOBAL_PROXY_HELP }}</p>

      <FormField
        label="默认代理组"
        hint="留空 = 直连。优先级：任务显式配置 > 全局默认 > 环境变量 > 内置默认。"
      >
        <n-select
          :value="globalForm.proxy_pool"
          :options="globalProxyOptions"
          placeholder="留空 = 直连"
          @update:value="(value: string) => (globalForm.proxy_pool = value)"
        />
      </FormField>

      <FormField label="默认并发数" hint="0 = 自动（各可用代理节点的 max_concurrency 之和，无代理时 4）。">
        <n-input-number v-model:value="globalForm.concurrency" :min="0" :max="64" :step="1" />
      </FormField>

      <FormField label="默认单轮时间预算（分钟）" hint="0 = 内置 8 分钟。到点就停，剩下的下一轮继续。">
        <n-input-number v-model:value="globalForm.time_budget_minutes" :min="0" :max="720" :step="1" />
      </FormField>

      <FormField label="默认只刷超过 N 分钟未更新的号" hint="0 = 内置 60 分钟。刚刷过的直接跳过。">
        <n-input-number v-model:value="globalForm.stale_minutes" :min="0" :max="10080" :step="1" />
      </FormField>
    </FormDrawer>

    <!-- 结果详情 -->
    <DetailDrawer
      v-model:show="resultOpen"
      :title="resultRow ? '最近结果 — ' + resultRow.displayName : '最近结果'"
      :badge="resultRow ? { tone: resultRow.result.tone, label: resultRow.result.label } : null"
      :subtitle="
        resultRow
          ? (resultRow.enabled ? '启用中' : '已停用') +
            ' · 上次运行 ' +
            (resultRow.lastRunAt ? fmtTime(resultRow.lastRunAt) : '未运行')
          : undefined
      "
    >
      <template v-if="resultRow">
        <p v-if="resultRow.result.summary" class="schedules-result__summary">
          {{ resultRow.result.summary }}
        </p>

        <KV v-if="resultKv.length" :items="resultKv" />

        <DataTable
          v-if="resultDetailRows.length"
          table-id="schedules-result"
          :columns="resultDetailColumns"
          :data="resultDetailRows"
          row-key="key"
          pagination
          :page-size="10"
        />

        <CodeBlock
          v-if="resultRow.result.raw"
          :code="resultRow.result.raw"
          language="json"
          max-height="280px"
        />
        <p v-else class="schedules-hint">该任务还没有运行过，没有结果。</p>
      </template>
    </DetailDrawer>
  </div>
</template>

<style scoped>
.schedules-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.schedules-banner {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--warn-soft);
  border-radius: var(--r-ctl);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.schedules-card {
  min-width: 0;
}

.schedules-filter {
  padding: var(--sp-4) var(--sp-5) var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
}

.schedules-filter__search {
  width: 240px;
  max-width: 100%;
}

.schedules-filter__select {
  width: 160px;
}

.schedules-body {
  padding: var(--sp-3) var(--sp-5) var(--sp-5);
  min-width: 0;
}

.schedules-skeleton {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) 0;
}

.sc-name-cell {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
}

.sc-name {
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.sc-name__stop {
  flex-basis: 100%;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--warn);
}

.sc-task,
.sc-scope,
.sc-cycle,
.sc-proxy,
.sc-result {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.sc-task__label,
.sc-scope {
  color: var(--fg);
}

.sc-task__id,
.sc-scope__hint,
.sc-cycle__zone,
.sc-cycle__next,
.sc-proxy__hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.sc-cycle__next.is-due {
  color: var(--warn);
}

.sc-proxy__main.is-global {
  color: var(--fg-muted);
}

.sc-result__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.sc-result__time {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.sc-result__summary {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.schedules-readonly {
  display: inline-block;
  padding: var(--sp-1) var(--sp-2);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.schedules-interval {
  display: flex;
  gap: var(--sp-2);
  min-width: 0;
}

.schedules-interval__unit {
  width: 120px;
  flex: none;
}

.schedules-time {
  width: 120px;
}

.schedules-weekdays {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3);
}

.schedules-grid2 {
  display: grid;
  gap: var(--sp-4);
}

.schedules-enabled {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.schedules-hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.schedules-result__summary {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

@media (min-width: 720px) {
  .schedules-grid2 {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .schedules-filter {
    padding: var(--sp-3) var(--sp-4);
  }

  .schedules-body {
    padding: var(--sp-2) var(--sp-4) var(--sp-4);
  }

  .schedules-filter__search,
  .schedules-filter__select {
    width: 100%;
  }

  .schedules-interval {
    flex-wrap: wrap;
  }
}
</style>
