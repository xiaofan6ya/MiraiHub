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
import { useI18n } from '@/i18n'
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

const { t } = useI18n()

/** 旧版 #panel-schedules 的说明文字（原样保留，收进 ⓘ）。 */
const helpText = computed(() => t('page.schedules.help'))

/** 全局出口与并发的说明（旧版「全局出口与并发」弹窗头部）。 */
const globalProxyHelp = computed(() => t('page.schedules.globalProxyHelp'))

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
  const base = t('page.schedules.summary.base', {
    total: current.total,
    enabled: current.enabled,
    disabled: current.disabled,
  })
  const extra: string[] = []
  if (current.problem > 0) extra.push(t('page.schedules.summary.failed', { count: current.problem }))
  if (current.running > 0) extra.push(t('page.schedules.summary.running', { count: current.running }))
  const textTail = extra.length ? ' · ' + extra.join(' · ') : ''
  return hasFilters.value
    ? t('page.schedules.summary.filtered', { total: current.total, all: stats.value.total }) + textTail
    : base + textTail
})

const emptyDescription = computed(() =>
  hasFilters.value ? t('page.schedules.empty.filtered') : t('page.schedules.empty.none'),
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
    { label: t('page.schedules.filter.platformAll'), value: '' },
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
  return [{ label: t('page.schedules.filter.taskAll'), value: '' }, ...options]
})

const stateFilterOptions = computed(() => [
  { label: t('page.schedules.filter.stateAll'), value: '' },
  { label: t('page.schedules.filter.enabled'), value: 'enabled' },
  { label: t('page.schedules.filter.disabled'), value: 'disabled' },
  { label: t('page.schedules.filter.due'), value: 'due' },
])

const resultFilterOptions = computed(() => [
  { label: t('page.schedules.filter.resultAll'), value: '' },
  { label: t('page.schedules.filter.ok'), value: 'ok' },
  { label: t('page.schedules.filter.warn'), value: 'warn' },
  { label: t('page.schedules.filter.danger'), value: 'danger' },
  { label: t('page.schedules.filter.none'), value: 'none' },
])

const stateChipLabel = computed(() => {
  switch (table.state.filters.state) {
    case 'enabled':
      return t('page.schedules.filter.enabled')
    case 'disabled':
      return t('page.schedules.filter.disabled')
    case 'due':
      return t('page.schedules.filter.due')
    default:
      return ''
  }
})

const resultChipLabel = computed(() => {
  switch (table.state.filters.result) {
    case 'ok':
      return t('page.schedules.filter.ok')
    case 'warn':
      return t('page.schedules.filter.warn')
    case 'danger':
      return t('page.schedules.filter.danger')
    case 'none':
      return t('page.schedules.filter.none')
    default:
      return ''
  }
})

const chips = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: t('page.schedules.filter.chipSearch'), value: filters.q })
  if (filters.platform) {
    const hit = platformFilterOptions.value.find((option) => option.value === filters.platform)
    list.push({
      key: 'platform',
      label: t('page.schedules.filter.chipPlatform'),
      value: hit?.label || filters.platform,
    })
  }
  if (filters.task) {
    const hit = taskFilterOptions.value.find((option) => option.value === filters.task)
    list.push({ key: 'task', label: t('page.schedules.filter.chipTask'), value: hit?.label || filters.task })
  }
  if (filters.state) {
    list.push({ key: 'state', label: t('page.schedules.filter.chipState'), value: stateChipLabel.value })
  }
  if (filters.result) {
    list.push({ key: 'result', label: t('page.schedules.filter.chipResult'), value: resultChipLabel.value })
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
    children.push(
      h(
        Badge,
        { tone: 'brand', dot: true, class: 'sc-name__badge' },
        { default: () => t('page.schedules.cell.running') },
      ),
    )
  }
  if (item.stopAfter) {
    children.push(
      h(
        'span',
        { class: 'sc-name__stop', title: t('page.schedules.cell.stopAfterTitle') },
        t('page.schedules.cell.stopAfter', { time: fmtTime(item.stopAfter) }),
      ),
    )
  }
  return h('div', { class: 'sc-name-cell' }, children)
}

function renderTask(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  return h('div', { class: 'sc-task' }, [
    h('span', { class: 'sc-task__label', title: item.task }, item.taskLabel || FMT_EMPTY),
    h('span', { class: 'sc-task__id mono', title: t('page.schedules.cell.taskIdTitle') }, item.task),
  ])
}

function renderScope(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  return h('div', { class: 'sc-scope' }, [
    h(
      'span',
      { title: t('page.schedules.cell.scopeTitle', { platform: item.platform || '—' }) },
      item.platformLabel || FMT_EMPTY,
    ),
    h('span', { class: 'sc-scope__hint' }, t('page.schedules.cell.scopeHint')),
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
      t('page.schedules.cell.next', { text: item.nextText }),
    ),
  )
  return h('div', { class: 'sc-cycle' }, children)
}

function renderProxy(row: TableRow): VNodeChild {
  const item = row as ScheduleRow
  const hints: string[] = []
  hints.push(
    item.concurrency > 0
      ? t('page.schedules.cell.concurrency', { count: item.concurrency })
      : t('page.schedules.cell.concurrencyAuto'),
  )
  hints.push(
    item.budgetMinutes > 0
      ? t('page.schedules.cell.budget', { count: item.budgetMinutes })
      : t('page.schedules.cell.budgetDefault'),
  )
  hints.push(
    item.staleMinutes > 0
      ? t('page.schedules.cell.stale', { count: item.staleMinutes })
      : t('page.schedules.cell.staleDefault'),
  )
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
        { class: 'sc-result__time', title: t('page.schedules.cell.lastRunTitle') },
        item.lastRunAt ? fmtTime(item.lastRunAt) : t('page.schedules.cell.neverRun'),
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
    'aria-label': item.enabled
      ? t('page.schedules.action.disableTask')
      : t('page.schedules.action.enableTask'),
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
      {
        key: 'run',
        label: t('page.schedules.action.run'),
        disabled: runningIds.value.includes(item.id),
      },
      { key: 'edit', label: t('common.edit') },
      { key: 'result', label: t('page.schedules.action.result') },
      { key: 'delete', label: t('common.delete'), danger: true },
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
    title: t('page.schedules.col.name'),
    mobile: 'title',
    minWidth: 260,
    sortable: true,
    sortOrder: sortOrderOf('name'),
    render: renderName,
  },
  {
    key: 'task',
    title: t('page.schedules.col.task'),
    mobile: 'meta',
    width: 168,
    render: renderTask,
  },
  {
    key: 'platform',
    title: t('page.schedules.col.platform'),
    mobile: 'subtitle',
    width: 176,
    sortable: true,
    sortOrder: sortOrderOf('platform'),
    render: renderScope,
  },
  {
    key: 'cycle',
    title: t('page.schedules.col.cycle'),
    mobile: 'meta',
    minWidth: 200,
    sortable: true,
    sortOrder: sortOrderOf('next_run_at'),
    render: renderCycle,
  },
  {
    key: 'proxy',
    title: t('page.schedules.col.proxy'),
    mobile: 'meta',
    width: 200,
    render: renderProxy,
  },
  {
    key: 'last',
    title: t('page.schedules.col.last'),
    mobile: 'meta',
    minWidth: 220,
    sortable: true,
    sortOrder: sortOrderOf('last_run_at'),
    render: renderResult,
  },
  {
    key: 'enabled',
    title: t('page.schedules.col.enabled'),
    mobile: 'meta',
    width: 72,
    hideable: false,
    render: renderEnabled,
  },
  {
    key: 'actions',
    title: t('page.schedules.col.actions'),
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
    toast.error(
      failureText(
        schedules.value === null
          ? t('page.schedules.action.load')
          : t('page.schedules.action.refreshList'),
        error,
      ),
    )
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
    if (view.tone === 'danger')
      toast.error(t('page.schedules.toast.runFailed', { name: row.displayName, tail: textTail }))
    else if (view.tone === 'warn')
      toast.warning(
        t('page.schedules.toast.ran', { name: row.displayName, label: view.label, tail: textTail }),
      )
    else
      toast.success(
        t('page.schedules.toast.ran', { name: row.displayName, label: view.label, tail: textTail }),
      )
  } catch (error) {
    toast.error(failureText(t('page.schedules.action.run'), error))
  } finally {
    runningIds.value = runningIds.value.filter((id) => id !== row.id)
    await loadSchedules()
  }
}

async function onToggle(row: ScheduleRow, next: boolean): Promise<void> {
  if (togglingIds.value.includes(row.id)) return
  if (!next) {
    const ok = await confirm({
      title: t('page.schedules.confirmDisable.title', { name: row.displayName }),
      message: t('page.schedules.confirmDisable.body'),
      okText: t('page.schedules.action.disable'),
    })
    if (!ok) return
  }
  togglingIds.value = [...togglingIds.value, row.id]
  try {
    const payload = await toggleSchedule(row.id)
    const enabled = Boolean(payload?.enabled)
    toast.success(
      enabled
        ? t('page.schedules.toast.toggledOn', { name: row.displayName })
        : t('page.schedules.toast.toggledOff', { name: row.displayName }),
    )
    await loadSchedules()
  } catch (error) {
    toast.error(
      failureText(
        next ? t('page.schedules.action.enableTask') : t('page.schedules.action.disableTask'),
        error,
      ),
    )
  } finally {
    togglingIds.value = togglingIds.value.filter((id) => id !== row.id)
  }
}

async function removeOne(row: ScheduleRow): Promise<void> {
  const ok = await confirm({
    title: t('page.schedules.confirmDelete.title', { name: row.displayName }),
    message: t('page.schedules.confirmDelete.body'),
    okText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  try {
    await deleteSchedule(row.id)
    toast.success(t('page.schedules.toast.deleted', { name: row.displayName }))
    await loadSchedules()
  } catch (error) {
    toast.error(failureText(t('page.schedules.action.deleteTask'), error))
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

const resultSubtitle = computed(() => {
  const row = resultRow.value
  if (!row) return undefined
  return t('page.schedules.result.subtitle', {
    state: row.enabled
      ? t('page.schedules.result.subtitleEnabled')
      : t('page.schedules.result.subtitleDisabled'),
    time: row.lastRunAt ? fmtTime(row.lastRunAt) : t('page.schedules.cell.neverRun'),
  })
})

const resultTitle = computed(() =>
  resultRow.value
    ? t('page.schedules.result.titleOf', { name: resultRow.value.displayName })
    : t('page.schedules.result.title'),
)

const proxyHint = computed(() =>
  t('page.schedules.form.proxyHint', {
    target: globalProxy.value?.proxy_pool
      ? t('page.schedules.form.proxyPoolName', { name: globalProxy.value.proxy_pool })
      : t('page.schedules.form.proxyDirect'),
  }),
)

const resultKv = computed<KvItem[]>(() => {
  const parsed = resultRow.value?.result.parsed
  if (!parsed) return []
  const items: KvItem[] = []
  for (const [key, value] of Object.entries(parsed)) {
    if (Array.isArray(value) || (value && typeof value === 'object')) continue
    const label = RESULT_FIELD_LABELS[key] || key
    const textValue =
      typeof value === 'boolean'
        ? value
          ? t('page.schedules.result.yes')
          : t('page.schedules.result.no')
        : value === null || value === ''
          ? ''
          : String(value)
    items.push({ label, value: textValue.length > 240 ? textValue.slice(0, 240) + '…' : textValue })
  }
  return items
})

const resultDetailColumns = computed<TableColumn[]>(() => [
  { key: 'account', title: t('page.schedules.result.colAccount'), mobile: 'title', minWidth: 160 },
  { key: 'status', title: t('page.schedules.result.colStatus'), mobile: 'meta', width: 96 },
  { key: 'credit', title: t('page.schedules.result.colCredit'), mobile: 'meta', width: 88, numeric: true },
  { key: 'reason', title: t('page.schedules.result.colReason'), mobile: 'meta', minWidth: 200 },
])

function openResult(row: ScheduleRow): void {
  resultRow.value = row
  resultDetailRows.value = row.result.detail.map((item, index) => ({
    key: String(item.account_id ?? index),
    account: String(item.account ?? item.name ?? '#' + String(item.account_id ?? index)),
    status:
      item.ok === false
        ? t('page.schedules.result.statusFailed')
        : String(item.status || t('page.schedules.result.statusOk')),
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
const drawerTitle = computed(() =>
  isEdit.value ? t('page.schedules.drawer.edit') : t('page.schedules.drawer.create'),
)
const submitText = computed(() =>
  isEdit.value ? t('common.save') : t('page.schedules.submit.create'),
)
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
  return [
    { label: form.value.task + t('page.schedules.taskOption.missing'), value: form.value.task },
    ...base,
  ]
})

const selectedTask = computed(() =>
  taskCatalog.value.find((item) => item.value === form.value.task) || null,
)

const selectedTaskPlatform = computed(() => {
  const hit = selectedTask.value
  if (!hit) return ''
  const regions = hit.platforms?.length ? hit.platforms : [hit.platform]
  return hit.platform_label + t('page.schedules.platformScope', { regions: regions.join(' / ') })
})

const catalogHint = computed(() => {
  if (catalogError.value) return catalogError.value
  if (catalogLoading.value) return t('page.schedules.catalog.loading')
  if (!taskCatalog.value.length) return t('page.schedules.catalog.empty')
  if (!selectedTask.value) return t('page.schedules.catalog.missing')
  return t('page.schedules.catalog.platform', {
    platform: selectedTask.value.platform_label || selectedTask.value.platform,
  })
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
    catalogError.value = failureText(t('page.schedules.action.loadCatalog'), error)
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
    toast.warning(t('page.schedules.toast.taskMissing'))
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
      toast.success(
        t('page.schedules.toast.saved', { name: input.name || t('page.schedules.untitled') }),
      )
    } else {
      await createSchedule(input)
      toast.success(
        t('page.schedules.toast.created', { name: input.name || t('page.schedules.untitled') }),
      )
      table.setPage(1)
    }
    drawerOpen.value = false
    await loadSchedules()
  } catch (error) {
    toast.error(
      failureText(
        isEdit.value ? t('page.schedules.action.saveTask') : t('page.schedules.submit.create'),
        error,
      ),
    )
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
    toast.success(t('page.schedules.toast.globalSaved'))
    globalOpen.value = false
    await loadSchedules()
  } catch (error) {
    toast.error(failureText(t('page.schedules.action.saveGlobal'), error))
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
    <PageHeader :title="t('page.schedules.title')" :help="helpText">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadSchedules()">
          {{ t('common.refresh') }}
        </Btn>
        <Btn variant="quiet" :icon="Settings2" @click="openGlobalProxy">
          {{ t('page.schedules.action.globalProxy') }}
        </Btn>
        <Btn variant="primary" :icon="Plus" @click="openCreate">
          {{ t('page.schedules.action.create') }}
        </Btn>
      </template>
    </PageHeader>

    <p v-if="poll.paused.value" class="schedules-banner" role="status">
      {{ poll.pausedMessage.value }}
      <Btn variant="quiet" size="sm" @click="poll.resume()">{{ t('page.schedules.poll.resume') }}</Btn>
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
              :placeholder="t('page.schedules.filter.searchPlaceholder')"
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
          :title="t('page.schedules.error.load', { error: loadError.message })"
          :detail="loadError.detail"
          @retry="loadSchedules()"
        />

        <div
          v-else-if="firstLoading"
          class="schedules-skeleton"
          aria-busy="true"
          :aria-label="t('page.schedules.loadingAria')"
        >
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
              <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
                {{ t('page.schedules.clearFilters') }}
              </Btn>
              <Btn v-else variant="primary" size="sm" :icon="Plus" @click="openCreate">
                {{ t('page.schedules.action.create') }}
              </Btn>
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
        :label="t('page.schedules.form.name')"
        :error="formErrors.name"
        :hint="t('page.schedules.form.nameHint')"
      >
        <n-input
          v-model:value="form.name"
          :placeholder="t('page.schedules.form.namePlaceholder')"
          maxlength="64"
          clearable
        />
      </FormField>

      <FormField
        :label="t('page.schedules.form.task')"
        required
        :error="formErrors.task"
        :hint="catalogHint"
      >
        <n-select
          :value="form.task"
          :options="taskOptions"
          :loading="catalogLoading"
          filterable
          :placeholder="t('page.schedules.form.taskPlaceholder')"
          @update:value="onTaskChange"
        />
      </FormField>

      <FormField :label="t('page.schedules.form.scope')" :hint="t('page.schedules.form.scopeHint')">
        <span class="schedules-readonly">{{ selectedTaskPlatform || FMT_EMPTY }}</span>
      </FormField>

      <FormField :label="t('page.schedules.form.cycle')" required>
        <n-select :value="form.mode" :options="modeOptions" @update:value="onModeChange" />
      </FormField>

      <FormField
        v-if="!showCalendar"
        :label="t('page.schedules.form.interval')"
        required
        :error="formErrors.interval"
        :hint="
          form.mode === 'preset'
            ? t('page.schedules.form.intervalPresetHint')
            : t('page.schedules.form.intervalHint')
        "
      >
        <div class="schedules-interval">
          <n-input-number v-model:value="form.intervalValue" :min="1" :max="5256000" :step="1" />
          <n-select v-model:value="form.intervalUnit" :options="unitOptions" class="schedules-interval__unit" />
        </div>
      </FormField>

      <template v-if="showCalendar">
        <FormField
          :label="t('page.schedules.form.time')"
          required
          :error="formErrors.time"
          :hint="t('page.schedules.form.timeHint', { zone: CALENDAR_ZONE_HINT })"
        >
          <n-input v-model:value="form.time" placeholder="09:00" maxlength="5" class="schedules-time" />
        </FormField>

        <FormField
          v-if="form.mode === 'weekly'"
          :label="t('page.schedules.form.weekdays')"
          required
          :error="formErrors.weekdays"
        >
          <n-checkbox-group :value="form.weekdays" @update:value="onWeekdaysChange">
            <div class="schedules-weekdays">
              <n-checkbox v-for="day in weekdayOptions" :key="day.value" :value="day.value" :label="day.label" />
            </div>
          </n-checkbox-group>
        </FormField>

        <FormField
          v-if="form.mode === 'monthly'"
          :label="t('page.schedules.form.monthday')"
          required
          :error="formErrors.monthday"
          :hint="t('page.schedules.form.monthdayHint')"
        >
          <n-input-number v-model:value="form.monthday" :min="1" :max="31" :step="1" />
        </FormField>
      </template>

      <FormField
        :label="t('page.schedules.form.proxy')"
        :hint="proxyHint"
      >
        <n-select
          :value="form.proxyPool"
          :options="proxyOptions"
          :placeholder="t('page.schedules.form.proxyPlaceholder')"
          @update:value="(value: string) => (form.proxyPool = value)"
        />
      </FormField>

      <div class="schedules-grid2">
        <FormField
          :label="t('page.schedules.form.concurrency')"
          :error="formErrors.concurrency"
          :hint="t('page.schedules.form.concurrencyHint')"
        >
          <n-input-number v-model:value="form.concurrency" :min="0" :max="64" :step="1" />
        </FormField>
        <FormField
          :label="t('page.schedules.form.budget')"
          :error="formErrors.budget"
          :hint="t('page.schedules.form.budgetHint')"
        >
          <n-input-number v-model:value="form.budgetMinutes" :min="0" :max="720" :step="1" />
        </FormField>
        <FormField
          :label="t('page.schedules.form.stale')"
          :error="formErrors.stale"
          :hint="t('page.schedules.form.staleHint')"
        >
          <n-input-number v-model:value="form.staleMinutes" :min="0" :max="10080" :step="1" />
        </FormField>
      </div>

      <FormField
        v-if="showStopAfter"
        :label="t('page.schedules.form.stopAfter')"
        :error="formErrors.stopAfter"
        :hint="t('page.schedules.form.stopAfterHint')"
      >
        <n-date-picker
          type="datetime"
          :formatted-value="form.stopAfter || null"
          value-format="yyyy-MM-dd'T'HH:mm"
          clearable
          :placeholder="t('page.schedules.form.stopAfterPlaceholder')"
          @update:formatted-value="(value: string | null) => (form.stopAfter = value || '')"
        />
      </FormField>

      <FormField :label="t('page.schedules.form.enabled')">
        <div class="schedules-enabled">
          <n-switch v-model:value="form.enabled" size="small" />
          <span>{{ t('page.schedules.form.enabledHint') }}</span>
        </div>
      </FormField>

      <p class="schedules-hint">
        <HelpTip
          :content="t('page.schedules.note.growthHelp')"
          :label="t('page.schedules.note.growthHelpLabel')"
        />
        {{ t('page.schedules.note.proxyRisk') }}
      </p>
    </FormDrawer>

    <!-- 全局出口与并发 -->
    <FormDrawer
      v-model:show="globalOpen"
      :title="t('page.schedules.global.title')"
      :submit-text="t('page.schedules.global.submit')"
      :submit-loading="globalSaving"
      @submit="saveGlobalProxy"
    >
      <p class="schedules-hint">{{ globalProxyHelp }}</p>

      <FormField
        :label="t('page.schedules.global.proxy')"
        :hint="t('page.schedules.global.proxyHint')"
      >
        <n-select
          :value="globalForm.proxy_pool"
          :options="globalProxyOptions"
          :placeholder="t('page.schedules.global.proxyPlaceholder')"
          @update:value="(value: string) => (globalForm.proxy_pool = value)"
        />
      </FormField>

      <FormField
        :label="t('page.schedules.global.concurrency')"
        :hint="t('page.schedules.global.concurrencyHint')"
      >
        <n-input-number v-model:value="globalForm.concurrency" :min="0" :max="64" :step="1" />
      </FormField>

      <FormField
        :label="t('page.schedules.global.budget')"
        :hint="t('page.schedules.global.budgetHint')"
      >
        <n-input-number v-model:value="globalForm.time_budget_minutes" :min="0" :max="720" :step="1" />
      </FormField>

      <FormField
        :label="t('page.schedules.global.stale')"
        :hint="t('page.schedules.global.staleHint')"
      >
        <n-input-number v-model:value="globalForm.stale_minutes" :min="0" :max="10080" :step="1" />
      </FormField>
    </FormDrawer>

    <!-- 结果详情 -->
    <DetailDrawer
      v-model:show="resultOpen"
      :title="resultTitle"
      :badge="resultRow ? { tone: resultRow.result.tone, label: resultRow.result.label } : null"
      :subtitle="resultSubtitle"
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
        <p v-else class="schedules-hint">{{ t('page.schedules.result.empty') }}</p>
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
