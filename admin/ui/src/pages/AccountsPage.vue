<script setup lang="ts">
/**
 * S25 账号池（上）/accounts（模板 B）
 *
 * 契约：docs/ui-v2/contracts/accounts.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-b.md P6
 *
 * 本步范围（S26 才做完整表单）：
 *   · 状态页签（全部/已启用/已禁用/冷却/熔断 + 计数，?status=）
 *   · 搜索 / 积分区间 / 排序 / 分页（客户端，保证冷却与熔断页签计数正确）
 *   · 通用列 + 能力声明驱动的行操作；批量工具条；整体后台任务（JobProgress + 1500ms 轮询）
 *   · 页头 导入/扫描本机/导出/添加账号 的入口壳（导入与添加方式提示「S26 实现」）
 *
 * 四态：首次加载骨架 / 刷新保留旧数据 / 空 / 错误 + 重试。
 * 平台上下文：订阅 usePlatformStore().onChange 重新取数（/api/accounts 按 X-Platform 收窄）。
 */
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch, type VNodeChild } from 'vue'
import { NDropdown, NInput, NInputNumber, NSkeleton } from 'naive-ui'
import { ChevronDown, Download, Plus, RefreshCw, ScanLine, Upload } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  JobProgress,
  KV,
  Meter,
  RowActions,
  Segmented,
  type FilterChip,
  type JobItem,
  type SegmentedOption,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useJob, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { fmtCredit, fmtDuration, fmtInt, fmtRelative } from '@/utils/format'
import { failureText } from '@/utils/error'
import { ApiError, isAbortError, download } from '@/api/http'
import {
  ACCOUNT_STATUS_LABEL,
  accountFeatures,
  accountTabCounts,
  addAccountModes,
  bulkAccounts,
  catTravelBatchAsync,
  deleteAccount,
  exportAccountsAsync,
  exportTokenOf,
  filterAccountRows,
  getCreditDetails,
  getModelLimits,
  getTodayUsage,
  growthRunAsync,
  healthCheckAccount,
  listAccounts,
  normalizeAccountStatus,
  paginateAccountRows,
  patchAccount,
  refreshAccount,
  refreshBatch,
  healthCheckBatch,
  runAccountTask,
  sortAccountRows,
  toAccountRows,
  toJobItem,
  jobDoneText,
  type AccountCreditPackage,
  type AccountJobSnapshot,
  type AccountItem,
  type AccountRow,
  type AccountUsageToday,
  type CreditDetailsPayload,
} from '@/api/accounts'

const HELP_TEXT =
  '统一管理账号、可用积分与登录状态。列表跟随顶栏的平台上下文；' +
  '剩余额度与今日消耗只统计成功请求；冷却/熔断按 gateway_state 展示。' +
  '批量与整池操作是后台任务，关闭页面后仍在服务端继续执行。'

const toast = useToast()
const confirm = useConfirm()
const platformStore = usePlatformStore()

const table = useTableQuery('accounts', {
  pageSize: 50,
  sortBy: 'id',
  sortOrder: 'desc',
  filters: { status: 'all', q: '', min: '', max: '' },
})

const statusTab = computed(() => normalizeAccountStatus(table.state.filters.status))

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const items = ref<AccountItem[] | null>(null)
const usageMap = ref<Map<number, AccountUsageToday>>(new Map())
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)

let controller: AbortController | null = null
const firstLoading = computed(() => loading.value && items.value === null)

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const [payload, usage] = await Promise.all([
      listAccounts({}, current.signal),
      getTodayUsage(current.signal).catch((error: unknown) => {
        if (isAbortError(error)) return null
        return null
      }),
    ])
    if (controller !== current) return
    items.value = Array.isArray(payload?.items) ? payload.items : []
    const next = new Map<number, AccountUsageToday>()
    for (const row of usage?.items || []) {
      if (row && row.account_id !== null && row.account_id !== undefined) {
        next.set(Number(row.account_id), row)
      }
    }
    usageMap.value = next
    // 删除/筛选后当前页可能越界：回退到有效页（与列表页一致）。
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / table.state.pageSize))
    if (table.state.page > maxPage) table.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: failureText('加载账号池', error), detail }
    toast.error(failureText(items.value ? '刷新账号池' : '加载账号池', error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

/* ------------------------------------------------------------------ *
 * 行数据 / 页签 / 筛选 / 排序 / 分页
 * ------------------------------------------------------------------ */

const allRows = computed(() => toAccountRows(items.value, usageMap.value))
const counts = computed(() => accountTabCounts(allRows.value))
const filteredRows = computed(() => filterAccountRows(allRows.value, table.state.filters))
const sortedRows = computed(() =>
  sortAccountRows(filteredRows.value, table.state.sortBy, table.state.sortOrder),
)
const pageData = computed(() =>
  paginateAccountRows(sortedRows.value, table.state.page, table.state.pageSize),
)

const hasFilters = computed(() =>
  Boolean(table.state.filters.q || table.state.filters.min || table.state.filters.max || statusTab.value !== 'all'),
)

const statusOptions = computed<Array<SegmentedOption<string>>>(() =>
  (['all', 'active', 'disabled', 'cooling', 'breaker'] as const).map((key) => ({
    label: ACCOUNT_STATUS_LABEL[key] + ' ' + counts.value[key],
    value: key,
  })),
)

const summary = computed(() => {
  const rows = filteredRows.value
  const remain = rows.reduce((sum, row) => sum + row.balanceRemain, 0)
  const available = rows.filter((row) => row.balanceRemain > 0).length
  return '共 ' + rows.length + ' 个账号 · 可用 ' + available + ' · 剩余积分 ' + fmtCredit(remain)
})

const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => {
    search.value = value
  },
)

const chips = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: '搜索', value: filters.q })
  if (statusTab.value !== 'all') {
    list.push({ key: 'status', label: '状态', value: ACCOUNT_STATUS_LABEL[statusTab.value] })
  }
  if (filters.min) list.push({ key: 'min', label: '积分 ≥', value: String(filters.min) })
  if (filters.max) list.push({ key: 'max', label: '积分 ≤', value: String(filters.max) })
  return list
})

function setStatus(value: string | number): void {
  table.setFilter('status', normalizeAccountStatus(value))
}

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) next[name] = String(value ?? '')
  table.setFilters(next)
}

function removeChip(key: string): void {
  if (key === 'q') search.value = ''
  table.setFilter(key, key === 'status' ? 'all' : '')
}

function resetFilters(): void {
  search.value = ''
  table.reset()
}

const SORT_KEY_MAP: Record<string, string> = {
  name: 'name',
  balance: 'credits',
  lastUsed: 'last_used',
  created: 'id',
}

function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
  if (table.state.sortBy !== stateKey) return false
  if (table.state.sortOrder === 'asc') return 'ascend'
  if (table.state.sortOrder === 'desc') return 'descend'
  return false
}

function onSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order) {
    table.setSort('id', 'desc')
    return
  }
  const stateKey = SORT_KEY_MAP[sorter.columnKey] || 'id'
  table.setSort(stateKey, sorter.order === 'ascend' ? 'asc' : 'desc')
}

/* ------------------------------------------------------------------ *
 * 表格列
 * ------------------------------------------------------------------ */

const rowFeatures = computed(() => {
  const map = new Map<number, AccountRow>()
  for (const row of allRows.value) map.set(row.id, row)
  return map
})

function renderAccount(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h('div', { class: 'acc-cell' }, [
    h('span', { class: 'acc-cell__name', title: item.name }, item.name),
    h('span', { class: 'acc-cell__sub mono' }, [
      item.uidMasked || '—',
      item.domain ? ' · ' + item.domain : '',
      item.tokenText ? ' · ' + item.tokenText : '',
    ]),
  ])
}

function renderStatus(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h(Badge, { tone: item.statusTone, title: item.statusTitle, dot: true }, {
    default: () => item.statusLabel,
  })
}

function renderBalance(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h('div', { class: 'acc-balance', title: item.balanceTitle }, [
    h(Meter, {
      value: item.balancePct,
      tone: item.balanceTone,
      'aria-label': item.name + ' 剩余额度占比',
    }),
    h('span', { class: 'acc-balance__text tnum' }, fmtInt(item.balanceRemain) + ' / ' + fmtInt(item.balanceTotal)),
  ])
}

function renderToday(row: TableRow): VNodeChild {
  const item = row as AccountRow
  const zero = item.todayCredits <= 0 && item.todayRequests <= 0
  return h(
    'span',
    { class: zero ? 'acc-zero' : 'acc-num', title: item.todayRequests + ' 次请求' },
    fmtCredit(item.todayCredits),
  )
}

function renderExpire(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h('span', { class: 'acc-expire is-' + item.expireTone, title: item.expireText }, item.expireText)
}

function renderLastUsed(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h('span', { class: 'acc-muted', title: item.lastUsedTitle }, item.lastUsedText)
}

function renderNote(row: TableRow): VNodeChild {
  const item = row as AccountRow
  if (!item.note) return h('span', { class: 'acc-zero' }, '—')
  return h('span', { class: 'acc-muted', title: item.note }, item.boundKeyName)
}

function actionList(item: AccountRow): Array<{ key: string; label: string; danger?: boolean }> {
  const actions: Array<{ key: string; label: string; danger?: boolean }> = [
    { key: 'refresh', label: '刷新额度' },
    { key: 'check', label: '检测' },
    { key: 'edit', label: '编辑' },
    { key: item.status === 'active' ? 'disable' : 'enable', label: item.status === 'active' ? '禁用' : '启用' },
    { key: 'detail', label: '详情' },
  ]
  if (item.features.checkin) actions.push({ key: 'checkin', label: '签到' })
  actions.push({ key: 'delete', label: '删除', danger: true })
  return actions
}

function renderActions(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h(RowActions, {
    actions: actionList(item),
    max: 2,
    onSelect: (key: string) => {
      void onRowAction(item, key)
    },
  })
}

const baseColumns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    title: '账号',
    mobile: 'title',
    minWidth: 190,
    sortable: true,
    sortOrder: sortOrderOf('name'),
    render: renderAccount,
  },
  {
    key: 'status',
    title: '状态',
    mobile: 'title',
    width: 96,
    render: renderStatus,
  },
  {
    key: 'balance',
    title: '剩余额度',
    mobile: 'meta',
    minWidth: 190,
    sortable: true,
    sortOrder: sortOrderOf('credits'),
    render: renderBalance,
  },
  {
    key: 'today',
    title: '今日消耗',
    mobile: 'meta',
    width: 110,
    numeric: true,
    render: renderToday,
  },
  {
    key: 'expire',
    title: '到期',
    mobile: 'meta',
    width: 130,
    render: renderExpire,
  },
  {
    key: 'lastUsed',
    title: '最近使用',
    mobile: 'meta',
    width: 120,
    render: renderLastUsed,
  },
  {
    key: 'note',
    title: '备注',
    mobile: 'subtitle',
    minWidth: 130,
    render: renderNote,
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

/* 平台扩展列：由后端能力字段驱动（token_supported=true 的平台才出现）。 */
const showTokenColumn = computed(() => allRows.value.some((row) => row.raw.token_supported))

function renderToken(row: TableRow): VNodeChild {
  const item = row as AccountRow
  if (!item.raw.token_supported) return h('span', { class: 'acc-zero' }, '—')
  const expired = item.raw.token_state === 'expired' || item.raw.renewal_state === 'expired'
  const expiring = item.raw.token_state === 'expiring' || item.raw.renewal_state === 'expiring'
  const days = item.raw.token_days_left
  const text = days === null || days === undefined ? (item.raw.token_state || '未知') : days + ' 天'
  const title = '令牌：' + text + ' · 续期剩余：' +
    (item.raw.renewal_days_left === null || item.raw.renewal_days_left === undefined ? '未知' : item.raw.renewal_days_left + ' 天')
  return h('span', {
    class: expired ? 'acc-expire is-danger' : expiring ? 'acc-expire is-warn' : 'acc-muted',
    title,
  }, text)
}

const tokenColumn: TableColumn = {
  key: 'token',
  title: '令牌',
  mobile: 'meta',
  width: 96,
  render: renderToken,
}

const columns = computed<TableColumn[]>(() => {
  const list = baseColumns.value.slice()
  if (!showTokenColumn.value) return list
  const index = list.findIndex((column) => column.key === 'lastUsed')
  list.splice(index < 0 ? list.length : index, 0, tokenColumn)
  return list
})

/* ------------------------------------------------------------------ *
 * 选择 / 批量
 * ------------------------------------------------------------------ */

const checkedKeys = ref<string[]>([])
const bulkBusy = ref(false)

function onCheckedKeys(keys: Array<string | number>): void {
  checkedKeys.value = keys.map(String)
}

const selectedRows = computed(() => {
  const keys = new Set(checkedKeys.value)
  return allRows.value.filter((row) => keys.has(row.key))
})

function clearSelection(): void {
  checkedKeys.value = []
  tableRef.value?.clearSelection()
}

async function runBulk(action: 'enable' | 'disable' | 'refresh' | 'check' | 'delete'): Promise<void> {
  const rows = selectedRows.value
  if (!rows.length || bulkBusy.value) return
  if (action === 'delete') {
    const ok = await confirm({
      title: '删除 ' + rows.length + ' 个账号？',
      message: '删除后无法恢复；绑定了 API Key 的账号会先被跳过（需先解绑）。',
      okText: '删除',
      danger: true,
      requireText: '删除',
    })
    if (!ok) return
  }
  bulkBusy.value = true
  try {
    const result = await bulkAccounts(action, rows.map((row) => row.id))
    const label =
      action === 'enable' ? '启用' : action === 'disable' ? '禁用' :
      action === 'refresh' ? '刷新额度' : action === 'check' ? '检测' : '删除'
    if (result.errors?.length) {
      toast.error('已' + label + ' ' + result.affected + ' 个账号，失败 ' + result.errors.length + ' 个：' + result.errors[0])
    } else {
      toast.success('已' + label + ' ' + result.affected + ' 个账号')
    }
    clearSelection()
    await load()
  } catch (error) {
    toast.error(failureText('批量' + action, error))
  } finally {
    bulkBusy.value = false
  }
}

async function exportSelection(): Promise<void> {
  const rows = selectedRows.value
  if (!rows.length) return
  await startExport({ ids: rows.map((row) => row.id) })
}

async function startExport(body: Parameters<typeof exportAccountsAsync>[0]): Promise<void> {
  try {
    const snapshot = await exportAccountsAsync(body)
    startJob(snapshot, { exportJob: true })
  } catch (error) {
    toast.error(failureText('导出账号', error))
  }
}

/* ------------------------------------------------------------------ *
 * 后台任务（整池 / 检测 / 导出）
 * ------------------------------------------------------------------ */

const jobVisible = ref(false)
const jobDetailOpen = ref(false)
const pendingExport = ref(false)

const job = useJob(undefined, {
  pollUrl: '/api/accounts/job/',
  intervalMs: 1500,
  onFinish: (snapshot) => {
    const data = snapshot as unknown as AccountJobSnapshot
    if (data.status === 'failed') toast.error(jobDoneText(data))
    else toast.success(jobDoneText(data))
    if (pendingExport.value) {
      pendingExport.value = false
      const token = exportTokenOf(data)
      if (token) {
        void download('/api/accounts/export-download', { token }).catch((error: unknown) => {
          toast.error(failureText('下载导出文件', error))
        })
      }
    }
    void load()
  },
})

const jobSnapshot = computed<AccountJobSnapshot | null>(
  () => (job.snapshot.value as unknown as AccountJobSnapshot | null) ?? null,
)
const jobItem = computed<JobItem | null>(() => toJobItem(jobSnapshot.value))
const jobTitle = computed(() => jobSnapshot.value?.title || '账号任务')
const jobPercent = computed(() => Number(job.snapshot.value?.percent) || 0)
const jobDone = computed(() => Number(job.snapshot.value?.done) || 0)
const jobTotal = computed(() => Number(job.snapshot.value?.total) || 0)
const jobRunning = computed(() => job.isRunning.value)
const jobItems = computed(() => jobSnapshot.value?.items || [])

function startJob(snapshot: AccountJobSnapshot, options: { exportJob?: boolean } = {}): void {
  if (!snapshot || !snapshot.job_id) {
    toast.error('任务启动失败：后端没有返回任务 ID')
    return
  }
  pendingExport.value = Boolean(options.exportJob)
  jobVisible.value = true
  job.start(snapshot.job_id)
  if (snapshot.status === 'running') toast.info('任务已启动，可在右下角查看进度')
}

async function refreshAll(): Promise<void> {
  const total = allRows.value.length
  if (!total) {
    toast.warning('当前平台没有可刷新的账号')
    return
  }
  const ok = await confirm({
    title: '刷新 ' + total + ' 个账号的额度？',
    message: '将逐个请求上游刷新余额与到期快照，后台执行，可关闭页面。',
    okText: '开始刷新',
  })
  if (!ok) return
  try {
    startJob(await refreshBatch())
  } catch (error) {
    toast.error(failureText('刷新全部额度', error))
  }
}

async function checkAll(): Promise<void> {
  const ids = allRows.value.map((row) => row.id)
  if (!ids.length) {
    toast.warning('当前平台没有可检测的账号')
    return
  }
  const ok = await confirm({
    title: '检测 ' + ids.length + ' 个账号？',
    message: '逐号做余额、模型目录与一次真实对话检查（会消耗少量额度）。',
    okText: '开始检测',
  })
  if (!ok) return
  try {
    startJob(await healthCheckBatch(ids))
  } catch (error) {
    toast.error(failureText('检测账号', error))
  }
}

const features = computed(() => accountFeatures(allRows.value[0]?.platform || platformStore.storagePlatform))

async function runGrowth(): Promise<void> {
  const ids = allRows.value.filter((row) => row.statusKey === 'active').map((row) => row.id)
  if (!ids.length) {
    toast.warning('当前平台没有已启用的账号')
    return
  }
  const ok = await confirm({
    title: '执行成长任务？',
    message: '将自动参与并完成可自动化任务，随后自动领奖（单账号可能需要几分钟）。',
    okText: '开始执行',
  })
  if (!ok) return
  try {
    startJob(await growthRunAsync(ids))
  } catch (error) {
    toast.error(failureText('执行成长任务', error))
  }
}

async function runCatTravel(): Promise<void> {
  const ok = await confirm({
    title: '执行猫猫旅行？',
    message: '流程：同意协议 → 首次领养（+300）→ 派出 → 领奖，按当前平台已启用账号执行。',
    okText: '开始执行',
  })
  if (!ok) return
  try {
    startJob(await catTravelBatchAsync())
  } catch (error) {
    toast.error(failureText('执行猫猫旅行', error))
  }
}

function showJobDetail(): void {
  if (!jobSnapshot.value) return
  jobDetailOpen.value = true
}

async function reloadJob(): Promise<void> {
  const id = jobSnapshot.value?.job_id
  if (!id) return
  try {
    job.start(id)
    jobVisible.value = true
  } catch (error) {
    toast.error(failureText('读取任务进度', error))
  }
}

/* ------------------------------------------------------------------ *
 * 行操作
 * ------------------------------------------------------------------ */

async function onRowAction(row: AccountRow, key: string): Promise<void> {
  if (key === 'refresh') await refreshOne(row)
  else if (key === 'check') await checkOne(row)
  else if (key === 'edit') openEdit(row)
  else if (key === 'enable' || key === 'disable') await toggleOne(row, key === 'enable')
  else if (key === 'detail') await openDetail(row)
  else if (key === 'checkin') await checkinOne(row)
  else if (key === 'delete') await removeOne(row)
}

async function refreshOne(row: AccountRow): Promise<void> {
  try {
    const result = await refreshAccount(row.id)
    toast.success('已刷新「' + row.name + '」：剩余 ' + fmtInt(result.balance_remain))
    await load()
  } catch (error) {
    toast.error(failureText('刷新「' + row.name + '」', error))
  }
}

async function checkOne(row: AccountRow): Promise<void> {
  try {
    startJob(await healthCheckAccount(row.id))
  } catch (error) {
    toast.error(failureText('检测「' + row.name + '」', error))
  }
}

async function toggleOne(row: AccountRow, enable: boolean): Promise<void> {
  const ok = await confirm({
    title: (enable ? '启用「' : '禁用「') + row.name + '」？',
    message: enable
      ? '启用后清除冷却/熔断残留，账号重新参与调度。'
      : '禁用后该账号不再接请求；绑定关系与历史记录保留。',
    okText: enable ? '启用' : '禁用',
    danger: !enable,
  })
  if (!ok) return
  try {
    await patchAccount(row.id, { status: enable ? 'active' : 'disabled' })
    toast.success((enable ? '已启用「' : '已禁用「') + row.name + '」')
    await load()
  } catch (error) {
    toast.error(failureText((enable ? '启用「' : '禁用「') + row.name + '」', error))
  }
}

async function checkinOne(row: AccountRow): Promise<void> {
  try {
    const result = await runAccountTask(row.id, 'checkin')
    if (result.supported === false) toast.warning(result.reason || '该平台没有单账号签到入口')
    else if (result.ok) toast.success('已签到「' + row.name + '」' + (result.summary ? '：' + result.summary : ''))
    else toast.error('签到失败：' + (result.summary || result.reason || '未知原因'))
    await load()
  } catch (error) {
    toast.error(failureText('签到「' + row.name + '」', error))
  }
}

async function removeOne(row: AccountRow): Promise<void> {
  const ok = await confirm({
    title: '删除账号「' + row.name + '」？',
    message: '删除后无法恢复；已绑定 API Key 的账号会被拒绝删除，请先解绑。',
    okText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteAccount(row.id)
    toast.success('已删除「' + row.name + '」')
    clearSelection()
    await load()
  } catch (error) {
    toast.error(failureText('删除「' + row.name + '」', error))
  }
}

/* ------------------------------------------------------------------ *
 * 编辑抽屉（名称 / 并发上限）
 * ------------------------------------------------------------------ */

const tableRef = ref<InstanceType<typeof DataTable> | null>(null)
const editOpen = ref(false)
const editTarget = ref<AccountRow | null>(null)
const editForm = reactive({ name: '', maxInFlight: null as number | null })
const editError = ref('')
const saving = ref(false)

function openEdit(row: AccountRow): void {
  editTarget.value = row
  editForm.name = row.name
  editForm.maxInFlight = row.raw.max_in_flight ?? null
  editError.value = ''
  editOpen.value = true
}

async function saveEdit(): Promise<void> {
  const target = editTarget.value
  if (!target) return
  const name = editForm.name.trim()
  if (!name) {
    editError.value = '请填写账号名称'
    return
  }
  editError.value = ''
  saving.value = true
  try {
    await patchAccount(target.id, {
      name,
      max_in_flight: editForm.maxInFlight === null ? null : Number(editForm.maxInFlight),
    })
    toast.success('已保存「' + name + '」')
    editOpen.value = false
    await load()
  } catch (error) {
    toast.error(failureText('保存账号', error))
  } finally {
    saving.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 详情 / 积分明细
 * ------------------------------------------------------------------ */

const detailOpen = ref(false)
const detailTarget = ref<AccountRow | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const creditDetails = ref<CreditDetailsPayload | null>(null)

const detailKv = computed(() => {
  const row = detailTarget.value
  if (!row) return []
  return [
    { label: '账号 ID', value: String(row.id), mono: true },
    { label: '名称', value: row.name },
    { label: 'UID', value: row.uid || '—', mono: true },
    { label: '平台', value: row.platform || '—', mono: true },
    { label: '域名', value: row.domain || '—', mono: true },
    { label: '状态', value: row.statusLabel + '（' + row.statusTitle + '）' },
    { label: '剩余 / 总额', value: fmtInt(row.balanceRemain) + ' / ' + fmtInt(row.balanceTotal) },
    { label: '积分到期', value: row.expireText },
    { label: '最近同步', value: row.raw.last_sync_at ? fmtRelative(row.raw.last_sync_at) : '—' },
    { label: '最近使用', value: row.lastUsedText },
    { label: '绑定 Key', value: row.boundKeyName || '—' },
    { label: '并发上限', value: row.raw.max_in_flight === null || row.raw.max_in_flight === undefined ? '跟随全局' : String(row.raw.max_in_flight) },
  ]
})

const detailPackages = computed<AccountCreditPackage[]>(() => {
  const payload = creditDetails.value
  if (!payload) return []
  const packages = Array.isArray(payload.packages) ? payload.packages : []
  return packages.map((item) => ({
    display_name: item.name || '积分包',
    remaining: item.remain,
    total: item.total,
    expire_label: item.deduction_end || '',
    expired: false,
  }))
})

async function openDetail(row: AccountRow): Promise<void> {
  detailTarget.value = row
  detailOpen.value = true
  creditDetails.value = null
  detailError.value = ''
  await loadCreditDetails(row.id, false)
}

async function loadCreditDetails(id: number, refresh: boolean): Promise<void> {
  detailLoading.value = true
  detailError.value = ''
  try {
    creditDetails.value = await getCreditDetails(id, refresh)
  } catch (error) {
    detailError.value = failureText('加载积分明细', error)
  } finally {
    detailLoading.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 模型额度与冷却
 * ------------------------------------------------------------------ */

const limitsOpen = ref(false)
const limitsLoading = ref(false)
const limitsError = ref('')
const limitsRows = ref<Array<Record<string, unknown>>>([])

async function openLimits(): Promise<void> {
  limitsOpen.value = true
  limitsLoading.value = true
  limitsError.value = ''
  try {
    const payload = await getModelLimits()
    limitsRows.value = Array.isArray(payload?.items) ? payload.items : []
  } catch (error) {
    limitsError.value = failureText('加载模型额度', error)
  } finally {
    limitsLoading.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 页头入口壳（S26 完整表单）
 * ------------------------------------------------------------------ */

const addModes = computed(() => addAccountModes(platformStore.currentSpec))
const addOptions = computed(() =>
  addModes.value.map((mode) => ({ key: mode.key, label: mode.label })),
)

function onAddMode(key: string): void {
  const mode = addModes.value.find((item) => item.key === key)
  toast.info((mode?.label || '该方式') + '：完整表单将在 S26 实现，本步只保留入口')
}

function shellNotice(label: string): void {
  toast.info(label + '：完整表单将在 S26 实现，本步只保留入口')
}

async function exportFiltered(): Promise<void> {
  const rows = filteredRows.value
  if (!rows.length) {
    toast.warning('当前筛选结果为空，没有可导出的账号')
    return
  }
  await startExport({ ids: rows.map((row) => row.id), format: 'raw' })
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null
let lastSelector = platformStore.selector

onMounted(() => {
  lastSelector = platformStore.selector
  void load()
  offPlatform = platformStore.onChange((detail) => {
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    clearSelection()
    void load()
  })
})

onBeforeUnmount(() => {
  offPlatform?.()
  offPlatform = null
  controller?.abort()
  controller = null
  job.stop()
})

// 页签切换回到第 1 页（其余筛选由 useTableQuery 统一处理）。
watch(statusTab, () => {
  if (table.state.page !== 1) table.setPage(1)
})
</script>

<template>
  <div class="acc-page">
    <PageHeader title="账号池" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="default" :icon="Upload" @click="shellNotice('导入账号')">导入</Btn>
        <Btn variant="quiet" :icon="ScanLine" @click="shellNotice('扫描本机')">扫描本机</Btn>
        <Btn variant="quiet" :icon="Download" @click="exportFiltered">导出</Btn>
        <n-dropdown
          :options="addOptions"
          trigger="click"
          @select="(key: string | number) => onAddMode(String(key))"
        >
          <Btn variant="primary" :icon="Plus">
            添加账号
            <ChevronDown :size="14" />
          </Btn>
        </n-dropdown>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="loadError && items === null"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="load()"
    />

    <div v-else-if="firstLoading" class="acc-skeleton" aria-busy="true" aria-label="账号池加载中">
      <n-skeleton height="40px" />
      <n-skeleton height="420px" />
    </div>

    <template v-else>
      <div class="acc-tabs">
        <Segmented
          :options="statusOptions"
          :model-value="statusTab"
          aria-label="按账号状态筛选"
          scrollable
          @update:model-value="setStatus"
        />
      </div>

      <Card flush>
        <div class="acc-filter">
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
                class="acc-filter__search"
                :value="search"
                placeholder="搜索名称 / UID / 域名"
                clearable
                @update:value="
                  (value: string) => {
                    search = value
                    change('q', value)
                  }
                "
              />
              <n-input
                class="acc-filter__num"
                :value="table.state.filters.min"
                placeholder="积分 ≥"
                clearable
                @update:value="(value: string) => change('min', value)"
              />
              <n-input
                class="acc-filter__num"
                :value="table.state.filters.max"
                placeholder="积分 ≤"
                clearable
                @update:value="(value: string) => change('max', value)"
              />
            </template>
          </FilterBar>
        </div>

        <!-- 后台任务内联条 -->
        <div v-if="jobSnapshot" class="acc-jobbar" :class="{ 'is-done': !jobRunning }">
          <span class="acc-jobbar__name">{{ jobTitle }}</span>
          <Meter :value="jobPercent" :tone="jobRunning ? 'brand' : jobItem?.status === 'failed' ? 'danger' : 'ok'" />
          <span class="acc-jobbar__num tnum">{{ jobDone }} / {{ jobTotal }}</span>
          <Btn variant="quiet" size="sm" @click="showJobDetail">查看详情</Btn>
          <Btn v-if="!jobRunning" variant="quiet" size="sm" @click="jobVisible = false">隐藏</Btn>
        </div>

        <div class="acc-body">
          <DataTable
            ref="tableRef"
            table-id="accounts"
            :columns="columns"
            :data="pageData.items"
            row-key="key"
            remote
            selectable
            :loading="loading"
            :page="table.state.page"
            :page-size="table.state.pageSize"
            :item-count="pageData.total"
            :page-sizes="[50, 100, 200]"
            empty-description="该平台还没有账号"
            @update:page="table.setPage"
            @update:page-size="table.setPageSize"
            @update:sorter="onSorter"
            @update:checked-keys="onCheckedKeys"
          >
            <template #toolbar>
              <Btn variant="quiet" size="sm" :icon="RefreshCw" :loading="jobRunning" @click="refreshAll">
                刷新全部
              </Btn>
              <Btn variant="quiet" size="sm" @click="openLimits">模型额度与冷却</Btn>
              <Btn variant="quiet" size="sm" @click="checkAll">检测全部</Btn>
              <Btn v-if="features.growth" variant="quiet" size="sm" @click="runGrowth">成长任务</Btn>
              <Btn v-if="features.catTravel" variant="quiet" size="sm" @click="runCatTravel">猫猫旅行</Btn>
              <Btn v-if="jobSnapshot" variant="quiet" size="sm" @click="reloadJob">查看任务</Btn>
            </template>

            <template #bulk>
              <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="runBulk('enable')">启用</Btn>
              <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="runBulk('disable')">禁用</Btn>
              <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="runBulk('refresh')">刷新额度</Btn>
              <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="runBulk('check')">检测</Btn>
              <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="exportSelection">导出选中</Btn>
              <Btn variant="danger" size="sm" :disabled="bulkBusy" @click="runBulk('delete')">删除</Btn>
            </template>

            <template #empty>
              <Empty :description="hasFilters ? '没有符合筛选的账号。' : '该平台还没有账号。先导入或添加账号。'">
                <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">清除筛选</Btn>
                <template v-else>
                  <Btn variant="default" size="sm" @click="shellNotice('导入账号')">导入账号</Btn>
                  <Btn variant="quiet" size="sm" @click="load()">刷新</Btn>
                </template>
              </Empty>
            </template>
          </DataTable>
        </div>
      </Card>
    </template>

    <!-- 编辑账号 -->
    <FormDrawer
      v-model:show="editOpen"
      title="编辑账号"
      :submit-loading="saving"
      @submit="saveEdit"
    >
      <FormField label="账号名称" required :error="editError" hint="用于后台展示；原始凭据不会被修改。">
        <n-input v-model:value="editForm.name" placeholder="例如：主号" maxlength="64" clearable />
      </FormField>
      <FormField label="并发上限" hint="留空跟随全局；0 表示不限。">
        <n-input-number v-model:value="editForm.maxInFlight" :min="0" placeholder="跟随全局" clearable />
      </FormField>
      <p class="acc-hint">后端没有账号备注字段：列表的「备注」列展示绑定的 API Key。</p>
    </FormDrawer>

    <!-- 详情 -->
    <FormDrawer v-model:show="detailOpen" :title="'账号详情 · ' + (detailTarget?.name || '')" size="lg" hide-footer>
      <KV v-if="detailTarget" :items="detailKv" label-width="96px" />
      <section class="acc-section">
        <div class="acc-section__head">
          <span>积分明细</span>
          <Btn
            v-if="detailTarget"
            variant="quiet"
            size="sm"
            :loading="detailLoading"
            @click="loadCreditDetails(detailTarget.id, true)"
          >
            重新采集
          </Btn>
        </div>
        <p v-if="detailLoading" class="acc-hint">正在读取积分明细…</p>
        <ErrorBlock v-else-if="detailError" :title="detailError" compact @retry="detailTarget && loadCreditDetails(detailTarget.id, false)" />
        <p v-else-if="!detailPackages.length" class="acc-hint">没有积分包明细（未采集或该平台不返回明细）。</p>
        <ul v-else class="acc-packages">
          <li v-for="(pkg, index) in detailPackages" :key="index" class="acc-packages__row">
            <span class="acc-packages__name">{{ pkg.display_name || '积分包' }}</span>
            <span class="tnum">{{ fmtCredit(pkg.remaining) }} / {{ fmtCredit(pkg.total) }}</span>
            <span class="acc-muted">{{ pkg.expire_label || '长期有效' }}</span>
          </li>
        </ul>
      </section>
      <section v-if="detailTarget?.raw.model_limits?.length" class="acc-section">
        <div class="acc-section__head"><span>模型冷却</span></div>
        <ul class="acc-packages">
          <li v-for="limit in detailTarget.raw.model_limits" :key="limit.model + limit.kind" class="acc-packages__row">
            <span class="acc-packages__name mono">{{ limit.model }}</span>
            <span class="acc-muted">{{ limit.kind }}</span>
            <span class="tnum">{{ fmtDuration(limit.remaining_seconds * 1000) }}</span>
          </li>
        </ul>
      </section>
    </FormDrawer>

    <!-- 模型额度与冷却 -->
    <FormDrawer v-model:show="limitsOpen" title="模型额度与冷却" size="lg" hide-footer>
      <p class="acc-hint">只读：当前窗口内处于冷却/日额度耗尽的账号 × 模型。</p>
      <p v-if="limitsLoading" class="acc-hint">正在加载…</p>
      <ErrorBlock v-else-if="limitsError" :title="limitsError" compact @retry="openLimits" />
      <ul v-else-if="limitsRows.length" class="acc-packages">
        <li v-for="(row, index) in limitsRows" :key="index" class="acc-packages__row">
          <span class="acc-packages__name">{{ row.account_name || ('#' + String(row.account_id)) }}</span>
          <span class="mono">{{ row.model }}</span>
          <span class="tnum">{{ fmtDuration((Number(row.remaining_seconds) || 0) * 1000) }}</span>
        </li>
      </ul>
      <Empty v-else description="当前没有模型冷却记录。" compact />
    </FormDrawer>

    <!-- 任务详情 -->
    <FormDrawer v-model:show="jobDetailOpen" :title="jobTitle" size="lg" hide-footer>
      <p class="acc-hint">{{ jobDoneText(jobSnapshot) }} · {{ jobDone }} / {{ jobTotal }}</p>
      <ul v-if="jobItems.length" class="acc-packages">
        <li v-for="(row, index) in jobItems" :key="index" class="acc-packages__row">
          <span class="acc-packages__name">{{ row.account || row.name || ('#' + String(row.account_id || index)) }}</span>
          <span class="acc-muted">{{ row.summary || row.reason || (row.ok === false ? '失败' : '完成') }}</span>
        </li>
      </ul>
      <p v-else class="acc-hint">任务没有返回逐条明细。</p>
    </FormDrawer>

    <JobProgress
      :jobs="jobVisible && jobItem ? [jobItem] : []"
      @hide="jobVisible = false"
      @view="showJobDetail"
    />
  </div>
</template>

<style scoped>
.acc-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.acc-skeleton {
  display: grid;
  gap: var(--sp-4);
}

.acc-tabs {
  display: flex;
  align-items: center;
  min-width: 0;
}

.acc-filter {
  padding: var(--sp-4) var(--sp-5) var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
}

.acc-filter__search {
  width: 240px;
  max-width: 100%;
}

.acc-filter__num {
  width: 110px;
}

.acc-body {
  padding: var(--sp-3) var(--sp-5) var(--sp-5);
  min-width: 0;
}

.acc-jobbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-5);
  border-bottom: 1px solid var(--border-soft);
  background: var(--brand-soft);
  min-width: 0;
}

.acc-jobbar.is-done {
  background: var(--surface-2);
}

.acc-jobbar__name {
  font-size: var(--fs-small);
  font-weight: 500;
  color: var(--fg);
  flex: none;
}

.acc-jobbar__num {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  flex: none;
}

.acc-jobbar :deep(.mh-meter) {
  flex: 1;
  min-width: 80px;
}

.acc-cell {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.acc-cell__name {
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-cell__sub {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-balance {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.acc-balance__text {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.acc-num {
  font-variant-numeric: tabular-nums;
  color: var(--fg);
}

.acc-zero {
  color: var(--fg-faint);
}

.acc-muted {
  color: var(--fg-muted);
  font-size: var(--fs-small);
}

.acc-expire.is-warn {
  color: var(--warn);
}

.acc-expire.is-danger {
  color: var(--danger);
}

.acc-expire.is-neutral {
  color: var(--fg-muted);
}

.acc-hint {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.acc-section {
  margin-top: var(--sp-4);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}

.acc-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--fs-h2);
  font-weight: 600;
  color: var(--fg);
  margin-bottom: var(--sp-2);
}

.acc-packages {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--sp-2);
  max-height: 280px;
  overflow: auto;
}

.acc-packages__row {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--sp-2);
  align-items: baseline;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.acc-packages__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
}

.acc-hidden {
  display: none;
}

@media (max-width: 640px) {
  .acc-filter,
  .acc-body {
    padding: var(--sp-3) var(--sp-4);
  }

  .acc-filter__search,
  .acc-filter__num {
    width: 100%;
  }

  .acc-jobbar {
    flex-wrap: wrap;
    padding: var(--sp-2) var(--sp-4);
  }
}
</style>
