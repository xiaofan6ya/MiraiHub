<script setup lang="ts">
/**
 * S14 API Keys /keys（模板 B：PageHeader + 对接信息卡 + FilterBar + DataTable + 抽屉）。
 *
 * 契约：docs/ui-v2/contracts/api-keys.md（尾部「## 实现对照」）。
 * 旧版：admin/console/index.html#panel-keys + console-app.js 的 Keys 段 + key-ip.js。
 * 后端没有 Key 到期字段、也没有来源 IP 绑定：这两项如实显示只读说明，不伪造能力。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch, type VNodeChild } from 'vue'
import { useRouter } from 'vue-router'
import { NCheckbox, NInput, NModal, NSelect, NSkeleton, NTabPane, NTabs } from 'naive-ui'
import { Copy, Plus, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Chip,
  CodeBlock,
  Copyable,
  DataTable,
  DetailDrawer,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  HelpTip,
  IconBtn,
  KV,
  Meter,
  RowActions,
  Segmented,
  Section,
  type FilterChip,
  type KvItem,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useTableQuery, useToast } from '@/composables'
import { copyText } from '@/composables/useCopy'
import { ApiError, isAbortError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import { FMT_EMPTY, fmtCredit, fmtInt, fmtTime } from '@/utils/format'
import {
  buildCurlExamples,
  createKey,
  deleteKey,
  draftToInput,
  emptyIpOverview,
  filterKeyRows,
  getNetwork,
  getSelectionPolicy,
  hasFormErrors,
  keyIps,
  keyStats,
  keyUsage,
  listAccountOptions,
  listAggregateModels,
  listGroupOptions,
  listKeys,
  listPlatformOptions,
  modelChips,
  originOptions,
  paginateKeyRows,
  patchKey,
  searchModelChips,
  sortKeyRows,
  statusPatch,
  toIpOverview,
  toKeyRows,
  toUsageView,
  validateKeyForm,
  viewKey,
  type AccountOption,
  type AggregateModelsPayload,
  type ApiKeyItem,
  type IpOverviewView,
  type KeyFormDraft,
  type KeyFormErrors,
  type KeyRow,
  type KeySortKey,
  type KeyUsagePayload,
  type KeyViewPayload,
  type NetworkPayload,
  type PlatformOption,
  type SelectionPolicyPayload,
} from '@/api/keys'

const { t } = useI18n()

/** 旧版 #panel-keys 的副标题与鉴权说明原文。 */
const helpText = computed(() => t('page.keys.help'))
const authText = computed(() => t('page.keys.authText'))

const router = useRouter()
const toast = useToast()
const confirm = useConfirm()
const table = useTableQuery('keys', { pageSize: 20, filters: { q: '', group: '', status: '' } })

/* ------------------------------ 列表 ------------------------------ */
const keys = ref<ApiKeyItem[] | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
let controller: AbortController | null = null

const rows = computed(() => toKeyRows(keys.value))
const filteredRows = computed(() => filterKeyRows(rows.value, table.state.filters))
const sortedRows = computed(() => sortKeyRows(filteredRows.value, table.state.sortBy, table.state.sortOrder))
const pageData = computed(() => paginateKeyRows(sortedRows.value, table.state.page, table.state.pageSize))
const stats = computed(() => keyStats(rows.value))
const firstLoading = computed(() => loading.value && keys.value === null)
const hasFilters = computed(() => Boolean(table.state.filters.q || table.state.filters.group || table.state.filters.status))

const summary = computed(() => {
  const current = keyStats(filteredRows.value)
  if (!hasFilters.value) {
    return t('page.keys.summary.all', {
      total: current.total,
      active: current.active,
      revoked: current.revoked,
      unlimited: current.unlimited,
    })
  }
  return t('page.keys.summary.filtered', {
    total: current.total,
    all: stats.value.total,
    active: current.active,
  })
})

const emptyDescription = computed(() =>
  hasFilters.value ? t('page.keys.empty.filtered') : t('page.keys.empty.none'),
)

/* ------------------------------ 对接信息 ------------------------------ */
const policy = ref<SelectionPolicyPayload | null>(null)
const network = ref<NetworkPayload | null>(null)
const models = ref<AggregateModelsPayload | null>(null)
const modelsLoading = ref(false)

const currentAddress = {
  origin: typeof window !== 'undefined' ? window.location.origin : '',
  hostname: typeof window !== 'undefined' ? window.location.hostname : '',
  port: typeof window !== 'undefined' ? window.location.port : '',
}
const origins = computed(() => originOptions(network.value, currentAddress))
const origin = ref(currentAddress.origin)
watch(origins, (list) => {
  if (list.length && !list.some((item) => item.value === origin.value)) origin.value = list[0].value
})
const openaiBase = computed(() => (origin.value || '').replace(/\/+$/, '') + '/v1')
const claudeBase = computed(() => (origin.value || '').replace(/\/+$/, ''))
const chips = computed(() => modelChips(models.value, 12))
const firstModel = computed(() => chips.value.chips[0]?.copyValue || '')
const examples = computed(() => buildCurlExamples(origin.value, firstModel.value))
const policyLabel = computed(() => {
  const item = (policy.value?.modes || []).find((mode) => mode.value === policy.value?.mode)
  return item?.label || t('page.keys.policyFallback')
})
const policyHelp = computed(() => {
  const item = (policy.value?.modes || []).find((mode) => mode.value === policy.value?.mode)
  return (item?.description || t('page.keys.policyFallbackHelp')) + t('page.keys.policySuffix')
})

/* ------------------------------ 表单 ------------------------------ */
function emptyDraft(): KeyFormDraft {
  return {
    name: '',
    note: '',
    groupId: 0,
    unlimited: true,
    creditLimit: '',
    maxConcurrent: '',
    ratePerMinute: '',
    aggregate: true,
    platform: '',
    accountIds: [],
    resetUsed: false,
  }
}

const drawerOpen = ref(false)
const editing = ref<KeyRow | null>(null)
const draft = ref<KeyFormDraft>(emptyDraft())
const errors = ref<KeyFormErrors>({})
const submitting = ref(false)
const createdKey = ref('')
const createdOpen = ref(false)

const groups = ref<Array<{ id: number; name: string; effective_count?: number }>>([])
const platforms = ref<PlatformOption[]>([])
const accountOptions = ref<AccountOption[]>([])
const accountsLoading = ref(false)
/** 创建 / 编辑抽屉的候选数据：分组、平台、账号。 */
const auxLoading = ref(false)
/** 按来源记失败：分组 / 平台 / 账号任一失败，就给出「重新加载」入口。 */
const auxFailures = ref({ groups: false, platforms: false, accounts: false })
const auxFailed = computed(
  () => auxFailures.value.groups || auxFailures.value.platforms || auxFailures.value.accounts,
)
/** 本次打开抽屉是否已经取过候选；失败后会复位，下次打开或点「重新加载」都会重新取。 */
let auxLoaded = false

const groupOptions = computed(() => [
  { label: t('page.keys.groupOption.none'), value: 0 },
  ...groups.value.map((group) => ({
    label:
      group.name +
      (group.effective_count != null
        ? t('page.keys.groupOption.count', { count: group.effective_count })
        : ''),
    value: group.id,
  })),
])
const platformOptionsForForm = computed(() => [
  { label: t('page.keys.platformOption.none'), value: '' },
  ...platforms.value.map((item) => ({ label: item.label, value: item.storage })),
])
const accountSelectOptions = computed(() =>
  accountOptions.value.map((account) => ({
    label:
      (account.name || account.uid || t('page.keys.account.fallback')) +
      ' #' +
      account.id +
      (account.status && account.status !== 'active' ? t('page.keys.account.disabled') : '') +
      (account.balance_remain != null
        ? t('page.keys.account.credits', { amount: fmtCredit(account.balance_remain) })
        : ''),
    value: account.id,
  })),
)
const drawerTitle = computed(() =>
  editing.value ? t('page.keys.drawer.edit') : t('page.keys.drawer.create'),
)
const submitText = computed(() => (editing.value ? t('common.save') : t('page.keys.submit.create')))

/* ------------------------------ 抽屉：查看 / 用量 / IP / 模型 / 指南 ------------------------------ */
const viewOpen = ref(false)
const viewLoading = ref(false)
const viewError = ref('')
const viewData = ref<KeyViewPayload | null>(null)
const usageOpen = ref(false)
const usageLoading = ref(false)
const usageError = ref('')
const usageData = ref<KeyUsagePayload | null>(null)
const ipOpen = ref(false)
const ipLoading = ref(false)
const ipError = ref('')
const ipDays = ref('1')
const ipKeyName = ref('')
const ipOverview = ref<IpOverviewView>(emptyIpOverview())
let ipKeyId = 0
const modelsOpen = ref(false)
const modelTerm = ref('')
const guideOpen = ref(false)
const guideTab = ref('openai')

const ipColumns = computed<TableColumn[]>(() => [
  { key: 'ip', title: t('page.keys.ipc.ip'), mobile: 'title', width: 150 },
  { key: 'requests', title: t('page.keys.ipc.requests'), mobile: 'meta', width: 100, numeric: true, render: (row: TableRow) => fmtInt(row.requests) },
  { key: 'failed', title: t('page.keys.ipc.failed'), mobile: 'meta', width: 80, numeric: true, render: (row: TableRow) => fmtInt(row.failed) },
  { key: 'credits', title: t('page.keys.ipc.credits'), mobile: 'meta', width: 90, numeric: true, render: (row: TableRow) => fmtCredit(row.credits) },
  { key: 'tokens', title: 'Token', mobile: 'meta', width: 100, numeric: true, render: (row: TableRow) => fmtInt(row.tokens) },
  { key: 'lastSeen', title: t('page.keys.ipc.lastSeen'), mobile: 'meta', width: 150 },
  { key: 'models', title: t('page.keys.ipc.models'), mobile: 'subtitle', minWidth: 180 },
  { key: 'active', title: t('page.keys.ipc.active'), mobile: 'meta', width: 80, numeric: true, render: (row: TableRow) => fmtInt(row.active) },
  {
    key: 'blocked',
    title: t('page.keys.ipc.status'),
    mobile: 'meta',
    width: 90,
    render: (row: TableRow) =>
      h(Badge, { tone: row.blocked ? 'danger' : 'ok' }, {
        default: () => (row.blocked ? t('page.keys.ipc.blocked') : t('page.keys.ipc.ok')),
      }),
  },
])

const modelHits = computed(() => searchModelChips(models.value, modelTerm.value))
/**
 * 明细行必须走 toIpOverview 的 rows：原始 KeyIpRow 的 models 是对象数组、
 * 时间是 last_seen、并发是 active_requests，直接绑给 DataTable 会渲染成
 * 「[object Object]」并把「最近调用 / 并发」两列留空。
 */
const ipRows = computed<TableRow[]>(() => ipOverview.value.rows as unknown as TableRow[])

/* ------------------------------ 取数 ------------------------------ */
function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

async function loadKeys(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const payload = await listKeys(current.signal)
    keys.value = Array.isArray(payload?.items) ? payload.items : []
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / table.state.pageSize))
    if (table.state.page > maxPage) table.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error)) return
    loadError.value = {
      message: errorMessage(error),
      detail: error instanceof ApiError && error.detail ? prettyDetail(error.detail) : '',
    }
    toast.error(
      failureText(keys.value === null ? t('page.keys.action.load') : t('page.keys.action.refreshKeys'), error),
    )
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

async function loadAccessories(): Promise<void> {
  void getSelectionPolicy()
    .then((payload) => { policy.value = payload })
    .catch(() => { policy.value = null })
  void getNetwork()
    .then((payload) => { network.value = payload })
    .catch(() => { network.value = null })
  modelsLoading.value = true
  try {
    models.value = await listAggregateModels()
  } catch {
    models.value = null
  } finally {
    modelsLoading.value = false
  }
}

/** 账号候选（服务端搜索）；返回 false 表示这次取数失败。 */
async function loadAccountOptions(search: string): Promise<boolean> {
  accountsLoading.value = true
  try {
    const payload = await listAccountOptions({ search, pageSize: 50 })
    accountOptions.value = Array.isArray(payload?.items) ? payload.items : (payload?.accounts || [])
    auxFailures.value.accounts = false
    return true
  } catch {
    accountOptions.value = []
    auxFailures.value.accounts = true
    return false
  } finally {
    accountsLoading.value = false
  }
}

async function loadGroups(): Promise<void> {
  try {
    const payload = await listGroupOptions()
    groups.value = Array.isArray(payload?.items) ? payload.items : []
    auxFailures.value.groups = false
  } catch {
    groups.value = []
    auxFailures.value.groups = true
  }
}

async function loadPlatforms(): Promise<void> {
  try {
    const payload = await listPlatformOptions()
    const seen = new Map<string, string>()
    for (const item of payload?.items || []) {
      const storage = String(item.storage_platform || item.id || '').toLowerCase()
      if (!storage || seen.has(storage)) continue
      seen.set(storage, String(item.label || storage))
    }
    platforms.value = [...seen].map(([storage, label]) => ({ storage, label }))
    auxFailures.value.platforms = false
  } catch {
    platforms.value = []
    auxFailures.value.platforms = true
  }
}

/**
 * 预取创建 / 编辑抽屉的候选数据。
 *
 * 失败后必须复位 auxLoaded：新版第一版把它置 true 就再没复位，
 * 一次接口抖动（分组 / 平台 / 账号任一失败）会让下拉在整个页面生命周期里一直是空的，
 * 只能刷新整页。现在失败会保留 auxFailed 并复位，重新打开抽屉或点「重新加载」都会重取。
 */
async function ensureAuxLoaded(): Promise<void> {
  if (auxLoaded || auxLoading.value) return
  auxLoaded = true
  auxLoading.value = true
  auxFailures.value = { groups: false, platforms: false, accounts: false }
  try {
    await Promise.all([loadGroups(), loadPlatforms(), loadAccountOptions('')])
  } finally {
    auxLoading.value = false
    if (auxFailed.value) auxLoaded = false
  }
}

/** 抽屉里的「重新加载」入口：强制重取一次，不看 auxLoaded。 */
async function reloadAux(): Promise<void> {
  if (auxLoading.value) return
  auxLoaded = false
  await ensureAuxLoaded()
}

/* ------------------------------ 筛选 / 排序 ------------------------------ */
const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => { search.value = value },
)

const statusOptions = computed(() => [
  { label: t('page.keys.filter.statusAll'), value: '' },
  { label: t('page.keys.filter.active'), value: 'active' },
  { label: t('page.keys.filter.revoked'), value: 'revoked' },
])

const groupFilterOptions = computed(() => [
  { label: t('page.keys.filter.groupAll'), value: '' },
  { label: t('page.keys.filter.groupNone'), value: 'none' },
  ...groups.value.map((group) => ({ label: group.name, value: String(group.id) })),
])

const chipsList = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: t('page.keys.filter.chipSearch'), value: filters.q })
  if (filters.group) {
    const label =
      filters.group === 'none'
        ? t('page.keys.filter.groupNone')
        : groups.value.find((group) => String(group.id) === filters.group)?.name || filters.group
    list.push({ key: 'group', label: t('page.keys.filter.chipGroup'), value: label })
  }
  if (filters.status)
    list.push({
      key: 'status',
      label: t('page.keys.filter.chipStatus'),
      value: filters.status === 'active' ? t('page.keys.filter.active') : t('page.keys.filter.revoked'),
    })
  return list
})

const SORT_KEYS: Record<string, KeySortKey> = {
  name: 'name',
  limit: 'limit',
  used: 'used',
  concurrency: 'concurrency',
}

function sortOrderOf(stateKey: KeySortKey): 'ascend' | 'descend' | false {
  if (table.state.sortBy !== stateKey) return false
  if (table.state.sortOrder === 'asc') return 'ascend'
  if (table.state.sortOrder === 'desc') return 'descend'
  return false
}

function onSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order || !SORT_KEYS[String(sorter.columnKey)]) {
    table.setSort('', '')
    return
  }
  table.setSort(SORT_KEYS[String(sorter.columnKey)], sorter.order === 'ascend' ? 'asc' : 'desc')
}

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

/* ------------------------------ 单元格 ------------------------------ */
function renderName(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h('div', { class: 'keys-name' }, [
    h('span', { class: 'keys-name__main', title: item.name }, item.name),
    h('span', { class: 'keys-name__sub' }, item.accountLabel),
  ])
}

function renderKey(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h('div', { class: 'keys-key' }, [
    h('code', { class: 'mono keys-key__text', title: item.masked }, item.masked),
    h(IconBtn, {
      icon: Copy,
      label: t('page.keys.cell.copyKey'),
      onClick: () => void copyFullKey(item),
    }),
  ])
}

function renderQuota(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h('div', { class: 'keys-quota' }, [
    h('div', { class: 'keys-quota__line tnum' }, [
      h('span', {}, item.usedText),
      h('span', { class: 'keys-quota__sep' }, ' / '),
      h('span', {}, item.limitText),
    ]),
    item.unlimited
      ? h('span', { class: 'keys-quota__hint' }, t('page.keys.cell.unlimited'))
      : h(Meter, {
          value: item.usageRatio * 100,
          tone: item.usageTone,
          height: 4,
          ariaLabel: t('page.keys.cell.meterAria'),
        }),
  ])
}

function renderConcurrency(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h('div', { class: 'keys-concurrency' }, [
    h('span', { class: 'tnum' }, item.concurrencyText),
    h('span', { class: 'keys-concurrency__rate' }, item.rateText),
  ])
}

function renderIps(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h('div', { class: 'keys-ips' }, [
    h(
      'button',
      {
        type: 'button',
        class: 'keys-ips__count',
        title: t('page.keys.cell.ipsTitle'),
        onClick: () => void openIps(item),
      },
      item.ipText,
    ),
    item.ipBadges.length
      ? h(
          'div',
          { class: 'keys-ips__badges' },
          item.ipBadges.map((entry) =>
            h(Badge, { key: entry.ip, tone: 'brand', title: entry.text }, { default: () => entry.ip }),
          ),
        )
      : null,
  ])
}

function renderGroup(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return item.groupId
    ? h(Badge, { tone: 'brand', title: t('page.keys.cell.groupTitle') }, { default: () => item.groupLabel })
    : h('span', { class: 'keys-muted' }, item.groupLabel)
}

function renderExpire(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h(
    'span',
    { class: 'keys-muted', title: t('page.keys.cell.expireTitle') },
    item.expireText,
  )
}

function renderStatus(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h(Badge, { tone: item.statusTone, dot: true }, { default: () => item.statusLabel })
}

function renderActions(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h(RowActions, {
    actions: [
      { key: 'edit', label: t('common.edit') },
      { key: 'copy', label: t('page.keys.action.copy') },
      { key: 'reset', label: t('page.keys.action.reset') },
      {
        key: 'toggle',
        label: item.status === 'active' ? t('page.keys.action.disable') : t('page.keys.action.enable'),
      },
      { key: 'usage', label: t('page.keys.action.usage') },
      { key: 'delete', label: t('common.delete'), danger: true },
    ],
    max: 2,
    onSelect: (key: string) => void onRowAction(item, key),
  })
}

const columns = computed<TableColumn[]>(() => [
  { key: 'name', title: t('page.keys.col.name'), mobile: 'title', minWidth: 180, sortable: true, sortOrder: sortOrderOf('name'), render: renderName },
  { key: 'key', title: 'Key', mobile: 'subtitle', minWidth: 220, render: renderKey },
  { key: 'quota', title: t('page.keys.col.quota'), mobile: 'meta', minWidth: 170, sortable: true, sortOrder: sortOrderOf('used'), render: renderQuota },
  { key: 'concurrency', title: t('page.keys.col.concurrency'), mobile: 'meta', width: 120, sortable: true, sortOrder: sortOrderOf('concurrency'), render: renderConcurrency },
  { key: 'ips', title: t('page.keys.col.ips'), mobile: 'meta', minWidth: 170, render: renderIps },
  { key: 'group', title: t('page.keys.col.group'), mobile: 'meta', width: 130, render: renderGroup },
  { key: 'expire', title: t('page.keys.col.expire'), mobile: 'meta', width: 90, render: renderExpire },
  { key: 'status', title: t('page.keys.col.status'), mobile: 'meta', width: 96, render: renderStatus },
  { key: 'actions', title: t('page.keys.col.actions'), mobile: 'actions', width: 150, hideable: false, fixed: 'right', render: renderActions },
])

/* ------------------------------ 行为 ------------------------------ */
function onAccountsChange(value: string | number | Array<string | number> | null): void {
  const list = Array.isArray(value) ? value : value === null || value === '' ? [] : [value]
  draft.value.accountIds = list.map(Number).filter((id) => Number.isFinite(id) && id > 0)
}

async function copyFullKey(row: KeyRow): Promise<void> {
  try {
    const data = await viewKey(row.id)
    const ok = await copyText(data.key)
    if (ok) toast.success(t('page.keys.toast.copied'))
    else toast.error(t('common.copyFailed'))
  } catch (error) {
    toast.error(failureText(t('page.keys.action.read'), error))
  }
}

function openCreate(): void {
  editing.value = null
  draft.value = emptyDraft()
  errors.value = {}
  drawerOpen.value = true
  void ensureAuxLoaded()
}

function openEdit(row: KeyRow): void {
  editing.value = row
  draft.value = {
    name: row.name,
    note: row.note,
    groupId: row.groupId,
    unlimited: row.unlimited,
    creditLimit: row.unlimited ? '' : String(row.creditLimit),
    maxConcurrent: row.maxConcurrent == null ? '' : String(row.maxConcurrent),
    ratePerMinute: row.ratePerMinute == null ? '' : String(row.ratePerMinute),
    aggregate: row.aggregate,
    platform: row.platform,
    accountIds: row.accountIds.slice(),
    resetUsed: false,
  }
  errors.value = {}
  drawerOpen.value = true
  void ensureAuxLoaded()
}

async function submit(): Promise<void> {
  errors.value = validateKeyForm(draft.value, Boolean(editing.value))
  if (hasFormErrors(errors.value)) return
  submitting.value = true
  try {
    const input = draftToInput(draft.value)
    if (editing.value) {
      await patchKey(editing.value.id, { ...input, reset_used: draft.value.resetUsed })
      toast.success(t('page.keys.toast.saved'))
      drawerOpen.value = false
      await loadKeys()
    } else {
      const created = await createKey({ ...input, credit_limit: input.credit_limit ?? null })
      drawerOpen.value = false
      createdKey.value = created.key
      createdOpen.value = true
      await loadKeys()
    }
  } catch (error) {
    toast.error(
      failureText(editing.value ? t('page.keys.action.saveKey') : t('page.keys.action.create'), error),
    )
  } finally {
    submitting.value = false
  }
}

async function onRowAction(row: KeyRow, action: string): Promise<void> {
  if (action === 'edit') return openEdit(row)
  if (action === 'copy') return copyFullKey(row)
  if (action === 'usage') return openUsage(row)
  if (action === 'reset') {
    const ok = await confirm({
      title: t('page.keys.confirmReset.title'),
      message: t('page.keys.confirmReset.body'),
      okText: t('page.keys.action.reset'),
    })
    if (!ok) return
    try {
      await patchKey(row.id, { reset_used: true })
      toast.success(t('page.keys.toast.resetDone'))
      await loadKeys()
    } catch (error) {
      toast.error(failureText(t('page.keys.action.resetUsed'), error))
    }
    return
  }
  if (action === 'toggle') {
    try {
      await patchKey(row.id, statusPatch(row.status))
      toast.success(row.status === 'active' ? t('page.keys.toast.disabled') : t('page.keys.toast.enabled'))
      await loadKeys()
    } catch (error) {
      toast.error(
        failureText(
          row.status === 'active' ? t('page.keys.action.disableKey') : t('page.keys.action.enableKey'),
          error,
        ),
      )
    }
    return
  }
  if (action === 'delete') {
    const ok = await confirm({
      title: t('page.keys.confirmDelete.title'),
      message: t('page.keys.confirmDelete.body'),
      danger: true,
      okText: t('common.delete'),
    })
    if (!ok) return
    try {
      await deleteKey(row.id)
      toast.success(t('page.keys.toast.deleted'))
      await loadKeys()
    } catch (error) {
      toast.error(failureText(t('page.keys.action.deleteKey'), error))
    }
  }
}

async function openUsage(row: KeyRow): Promise<void> {
  usageOpen.value = true
  usageLoading.value = true
  usageError.value = ''
  usageData.value = null
  try {
    usageData.value = await keyUsage(row.id)
  } catch (error) {
    usageError.value = errorMessage(error)
  } finally {
    usageLoading.value = false
  }
}

async function loadIps(): Promise<void> {
  if (!ipKeyId) return
  ipLoading.value = true
  ipError.value = ''
  try {
    const payload = await keyIps(ipKeyId, { days: Number(ipDays.value) || 1 })
    ipOverview.value = toIpOverview(payload)
  } catch (error) {
    ipError.value = errorMessage(error)
    ipOverview.value = emptyIpOverview()
  } finally {
    ipLoading.value = false
  }
}

async function openIps(row: KeyRow): Promise<void> {
  ipKeyId = row.id
  ipKeyName.value = row.name
  ipDays.value = '1'
  // 先清空：汇总与明细都是上一个 Key 的，等新数据回来再填（失败也保持空态）。
  ipOverview.value = emptyIpOverview()
  ipError.value = ''
  ipOpen.value = true
  await loadIps()
}

const ipRangeOptions = computed(() => [
  { label: t('page.keys.ips.range24'), value: '1' },
  { label: t('page.keys.ips.range7'), value: '7' },
  { label: t('page.keys.ips.range30'), value: '30' },
])

const ipSummaryItems = computed<KvItem[]>(() => [
  { label: t('page.keys.ipSummary.ips'), value: fmtInt(ipOverview.value.totalIps) },
  { label: t('page.keys.ipc.requests'), value: fmtInt(ipOverview.value.requests) },
  { label: t('page.keys.ipSummary.activeIps'), value: fmtInt(ipOverview.value.activeIps) },
  { label: t('page.keys.ipSummary.activeRequests'), value: fmtInt(ipOverview.value.activeRequests) },
])

const usageView = computed(() => (usageData.value ? toUsageView(usageData.value) : null))
const viewItems = computed<KvItem[]>(() => [
  { label: t('page.keys.view.name'), value: viewData.value?.name || FMT_EMPTY },
  { label: t('page.keys.view.prefix'), value: viewData.value?.key_prefix || FMT_EMPTY, mono: true },
  { label: t('page.keys.view.createdAt'), value: viewData.value?.created_at ? fmtTime(viewData.value.created_at) : FMT_EMPTY },
])

onMounted(() => {
  void loadKeys()
  void loadAccessories()
})

onBeforeUnmount(() => {
  controller?.abort()
})
</script>

<template>
  <div class="keys-page">
    <PageHeader title="API Keys" :help="helpText">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadKeys()">
          {{ t('common.refresh') }}
        </Btn>
        <span class="keys-policy">
          <Btn variant="quiet" @click="router.push('/credits')">{{ policyLabel }}</Btn>
          <HelpTip :content="policyHelp" :label="t('page.keys.policyHelpLabel')" />
        </span>
        <Btn variant="default" @click="guideOpen = true">{{ t('page.keys.action.guide') }}</Btn>
        <Btn variant="primary" :icon="Plus" @click="openCreate">{{ t('page.keys.action.create') }}</Btn>
      </template>
    </PageHeader>

    <Card class="keys-share" :title="t('page.keys.share.title')">
      <template #extra>
        <Btn variant="quiet" size="sm" :loading="modelsLoading" @click="loadAccessories()">
          {{ t('common.refresh') }}
        </Btn>
      </template>

      <div class="keys-share__grid">
        <div class="keys-share__row">
          <span class="keys-share__label">{{ t('page.keys.share.address') }}</span>
          <n-select
            class="keys-share__select"
            :value="origin"
            :options="origins"
            @update:value="(value: string) => (origin = value)"
          />
        </div>
        <div class="keys-share__row">
          <span class="keys-share__label">OpenAI<em>{{ t('page.keys.share.openaiHint') }}</em></span>
          <Copyable :value="openaiBase" />
        </div>
        <div class="keys-share__row">
          <span class="keys-share__label">Claude<em>{{ t('page.keys.share.claudeHint') }}</em></span>
          <Copyable :value="claudeBase" />
        </div>
        <div class="keys-share__row">
          <span class="keys-share__label">{{ t('page.keys.share.auth') }}</span>
          <Copyable :value="t('page.keys.share.authValue')" />
          <HelpTip :content="authText" :label="t('page.keys.share.authHelpLabel')" />
        </div>
        <div class="keys-share__row keys-share__row--models">
          <span class="keys-share__label">{{ t('page.keys.share.models') }}</span>
          <div class="keys-share__models">
            <span class="keys-muted">{{ t('page.keys.share.groups', { count: chips.total }) }}</span>
            <Chip
              v-for="chip in chips.chips"
              :key="chip.key"
              mono
              copy
              :value="chip.copyValue"
              :title="chip.title"
            >
              {{ chip.name }}<span v-if="chip.platformCount > 1" class="keys-chip-count">{{ chip.platformCount }}</span>
            </Chip>
            <Btn v-if="chips.total" variant="quiet" size="sm" @click="modelsOpen = true">
              {{ t('page.keys.share.allModels', { count: chips.total }) }}
            </Btn>
            <span v-if="!chips.total && !modelsLoading" class="keys-muted">
              {{ t('page.keys.share.noWhitelist') }}
            </span>
          </div>
        </div>
      </div>
    </Card>

    <Card flush class="keys-card">
      <div class="keys-filter">
        <FilterBar
          :chips="chipsList"
          :summary="summary"
          :loading="loading"
          @change="onFilterChange"
          @reset="resetFilters"
          @remove="removeChip"
        >
          <template #default="{ change }">
            <n-input
              class="keys-filter__search"
              :value="search"
              :placeholder="t('page.keys.filter.searchPlaceholder')"
              clearable
              @update:value="
                (value: string) => {
                  search = value
                  change('q', value)
                }
              "
            />
            <n-select
              class="keys-filter__select"
              :value="table.state.filters.group"
              :options="groupFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('group', value)"
            />
            <n-select
              class="keys-filter__select"
              :value="table.state.filters.status"
              :options="statusOptions"
              @update:value="(value: string | number | null) => applyFilter('status', value)"
            />
          </template>
        </FilterBar>
      </div>

      <div class="keys-body">
        <ErrorBlock
          v-if="loadError && keys === null"
          :title="t('page.keys.error.load', { error: loadError.message })"
          :detail="loadError.detail"
          @retry="loadKeys()"
        />

        <div
          v-else-if="firstLoading"
          class="keys-skeleton"
          aria-busy="true"
          :aria-label="t('page.keys.loadingAria')"
        >
          <n-skeleton v-for="index in 6" :key="index" height="44px" />
        </div>

        <DataTable
          v-else
          table-id="keys"
          :columns="columns"
          :data="pageData.items"
          row-key="id"
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
                {{ t('page.keys.clearFilters') }}
              </Btn>
              <Btn v-else variant="primary" size="sm" :icon="Plus" @click="openCreate">
                {{ t('page.keys.action.create') }}
              </Btn>
            </Empty>
          </template>
        </DataTable>
      </div>
    </Card>

    <!-- 创建 / 编辑 -->
    <FormDrawer
      v-model:show="drawerOpen"
      :title="drawerTitle"
      size="lg"
      :submit-text="submitText"
      :submit-loading="submitting"
      @submit="submit"
    >
      <ErrorBlock
        v-if="auxFailed"
        compact
        :title="t('page.keys.form.auxFailed')"
        :retry-label="t('page.keys.form.reload')"
        @retry="reloadAux"
      />
      <Section :title="t('page.keys.section.basic')" first>
        <div class="keys-form">
          <FormField
            :label="t('page.keys.form.name')"
            :required="Boolean(editing)"
            :error="errors.name"
            :hint="t('page.keys.form.nameHint')"
          >
            <n-input
              v-model:value="draft.name"
              :placeholder="t('page.keys.form.namePlaceholder')"
              maxlength="120"
              clearable
            />
          </FormField>
          <FormField
            :label="t('page.keys.form.group')"
            :hint="t('page.keys.form.groupHint')"
          >
            <n-select v-model:value="draft.groupId" :options="groupOptions" />
          </FormField>
          <FormField :label="t('page.keys.form.note')">
            <n-input
              v-model:value="draft.note"
              type="textarea"
              :rows="2"
              maxlength="255"
              :placeholder="t('page.keys.form.notePlaceholder')"
            />
          </FormField>
        </div>
      </Section>

      <Section :title="t('page.keys.section.quota')">
        <div class="keys-form">
          <FormField
            :label="t('page.keys.form.limit')"
            :error="errors.creditLimit"
            :hint="t('page.keys.form.limitHint')"
          >
            <div class="keys-form__inline">
              <n-checkbox v-model:checked="draft.unlimited">
                {{ t('page.keys.form.unlimited') }}
              </n-checkbox>
              <n-input
                v-model:value="draft.creditLimit"
                :placeholder="t('page.keys.form.limitPlaceholder')"
                :disabled="draft.unlimited"
              />
            </div>
          </FormField>
          <FormField
            :label="t('page.keys.form.concurrency')"
            :error="errors.maxConcurrent"
            :hint="t('page.keys.form.concurrencyHint')"
          >
            <n-input
              v-model:value="draft.maxConcurrent"
              :placeholder="t('page.keys.form.concurrencyPlaceholder')"
            />
          </FormField>
          <FormField
            :label="t('page.keys.form.rate')"
            :error="errors.ratePerMinute"
            :hint="t('page.keys.form.rateHint')"
          >
            <n-input
              v-model:value="draft.ratePerMinute"
              :placeholder="t('page.keys.form.concurrencyPlaceholder')"
            />
          </FormField>
          <FormField v-if="editing" :label="t('page.keys.form.resetUsed')">
            <n-checkbox v-model:checked="draft.resetUsed">
              {{ t('page.keys.form.resetUsedLabel', { used: editing?.usedText || '' }) }}
            </n-checkbox>
          </FormField>
        </div>
      </Section>

      <Section :title="t('page.keys.section.scope')">
        <div class="keys-form">
          <FormField
            :label="t('page.keys.form.aggregate')"
            :hint="t('page.keys.form.aggregateHint')"
          >
            <n-checkbox v-model:checked="draft.aggregate">
              {{ t('page.keys.form.aggregateLabel') }}
            </n-checkbox>
          </FormField>
          <FormField
            :label="t('page.keys.form.platform')"
            :hint="t('page.keys.form.platformHint')"
          >
            <n-select v-model:value="draft.platform" :options="platformOptionsForForm" />
          </FormField>
          <FormField
            :label="t('page.keys.form.accounts')"
            :hint="t('page.keys.form.accountsHint')"
          >
            <n-select
              :value="draft.accountIds"
              multiple
              filterable
              remote
              clearable
              :loading="accountsLoading"
              :options="accountSelectOptions"
              :placeholder="t('page.keys.form.accountsPlaceholder')"
              @update:value="onAccountsChange"
              @search="(value: string) => loadAccountOptions(value)"
            />
          </FormField>
        </div>
      </Section>

      <Section :title="t('page.keys.section.access')">
        <p class="keys-hint">{{ t('page.keys.access.ipNote') }}</p>
        <p class="keys-hint">{{ t('page.keys.access.expireNote') }}</p>
      </Section>
    </FormDrawer>

    <!-- 创建成功：完整 Key（旧版同样可随时在列表里再次查看） -->
    <n-modal
      v-model:show="createdOpen"
      preset="card"
      class="keys-modal"
      :title="t('page.keys.created.title')"
      :style="{ width: '520px' }"
    >
      <p class="keys-hint">{{ t('page.keys.created.note') }}</p>
      <Copyable :value="createdKey" />
      <template #footer>
        <div class="keys-modal__footer">
          <Btn variant="primary" @click="createdOpen = false">
            {{ t('page.keys.created.done') }}
          </Btn>
        </div>
      </template>
    </n-modal>

    <!-- 查看用量 -->
    <DetailDrawer
      v-model:show="usageOpen"
      :title="t('page.keys.usage.title')"
      :subtitle="t('page.keys.usage.subtitle')"
    >
      <ErrorBlock
        v-if="usageError"
        :title="t('page.keys.usage.error', { error: usageError })"
        :retry="false"
        compact
      />
      <div v-else-if="usageLoading" class="keys-skeleton"><n-skeleton :repeat="3" text /></div>
      <template v-else-if="usageView">
        <div class="keys-usage-grid">
          <div class="keys-usage-cell">
            <span>{{ t('page.keys.usage.limit') }}</span>
            <strong class="tnum">{{ usageView.limitText }}</strong>
          </div>
          <div class="keys-usage-cell">
            <span>{{ t('page.keys.usage.used') }}</span>
            <strong class="tnum">{{ usageView.usedText }}</strong>
          </div>
          <div class="keys-usage-cell">
            <span>{{ t('page.keys.usage.remaining') }}</span>
            <strong class="tnum">{{ usageView.remainingText }}</strong>
          </div>
          <div class="keys-usage-cell">
            <span>{{ t('page.keys.usage.rate') }}</span>
            <strong class="tnum">{{ usageView.percentText }}</strong>
          </div>
        </div>
        <Meter
          v-if="!usageView.unlimited"
          :value="usageView.ratio * 100"
          :tone="usageView.tone"
          :height="8"
          :aria-label="t('page.keys.usage.meterAria')"
        />
        <p class="keys-hint">{{ t('page.keys.usage.createdAt', { time: usageView.createdText }) }}</p>
      </template>
      <template #footer>
        <Btn variant="default" @click="usageOpen = false">{{ t('common.close') }}</Btn>
      </template>
    </DetailDrawer>

    <!-- 来源 IP -->
    <DetailDrawer
      v-model:show="ipOpen"
      :title="t('page.keys.ips.title', { name: ipKeyName })"
      :subtitle="t('page.keys.ips.subtitle')"
    >
      <div class="keys-ipbar">
        <Segmented
          :options="ipRangeOptions"
          :model-value="ipDays"
          :aria-label="t('page.keys.ips.rangeAria')"
          @update:model-value="
            (value: string | number) => {
              ipDays = String(value)
              void loadIps()
            }
          "
        />
        <Btn variant="quiet" size="sm" :loading="ipLoading" @click="loadIps()">
          {{ t('common.refresh') }}
        </Btn>
      </div>
      <KV :items="ipSummaryItems" />
      <ErrorBlock
        v-if="ipError"
        :title="t('page.keys.ips.error', { error: ipError })"
        :retry="false"
        compact
      />
      <DataTable
        v-else
        table-id="keys-ips"
        :columns="ipColumns"
        :data="ipRows"
        row-key="ip"
        :pagination="false"
        :virtual-scroll="false"
        max-height="360px"
        :empty-description="t('page.keys.ips.empty')"
      />
      <p class="keys-hint">{{ t('page.keys.ips.note') }}</p>
      <template #footer>
        <Btn variant="default" @click="ipOpen = false">{{ t('common.close') }}</Btn>
      </template>
    </DetailDrawer>

    <!-- 全部模型 -->
    <DetailDrawer
      v-model:show="modelsOpen"
      :title="t('page.keys.models.title', { count: chips.total })"
      :subtitle="t('page.keys.models.subtitle')"
    >
      <n-input
        v-model:value="modelTerm"
        :placeholder="t('page.keys.models.searchPlaceholder')"
        clearable
      />
      <div v-if="!modelHits.length" class="keys-muted">{{ t('page.keys.models.noHits') }}</div>
      <div v-for="hit in modelHits" :key="hit.key" class="keys-model">
        <div class="keys-model__head">
          <strong>{{ hit.name }}</strong>
          <span class="keys-muted">{{ t('page.keys.models.platformCount', { count: hit.platformCount }) }}</span>
        </div>
        <div class="keys-model__rows">
          <div v-for="row in hit.rows" :key="row.callId" class="keys-model__row">
            <span class="keys-muted">{{ row.platformLabel }}</span>
            <Chip
              mono
              copy
              :value="row.callId"
              :title="t('page.keys.models.upstreamId', { id: row.upstreamId })"
            >
              {{ row.callId }}
            </Chip>
            <span class="keys-muted tnum">{{ row.multiplierText }}</span>
          </div>
        </div>
      </div>
      <template #footer>
        <Btn variant="default" @click="modelsOpen = false">{{ t('common.close') }}</Btn>
      </template>
    </DetailDrawer>

    <!-- 接入指南 -->
    <DetailDrawer
      v-model:show="guideOpen"
      :title="t('page.keys.guide.title')"
      :subtitle="t('page.keys.guide.subtitle')"
    >
      <n-tabs v-model:value="guideTab" type="line" animated>
        <n-tab-pane name="openai" :tab="t('page.keys.guide.openai')">
          <CodeBlock :code="examples.openai" language="bash" />
        </n-tab-pane>
        <n-tab-pane name="claude" tab="Claude Code / Anthropic">
          <CodeBlock :code="examples.claude" language="bash" />
        </n-tab-pane>
        <n-tab-pane name="audio" :tab="t('page.keys.guide.audio')">
          <p class="keys-hint">{{ t('page.keys.guide.audioNote') }}</p>
          <CodeBlock :code="examples.audioCurl" language="bash" />
          <CodeBlock :code="examples.audioPython" language="python" />
        </n-tab-pane>
        <n-tab-pane name="images" :tab="t('page.keys.guide.images')">
          <p class="keys-hint">{{ t('page.keys.guide.imagesNote') }}</p>
          <CodeBlock :code="examples.imagesPython" language="python" />
        </n-tab-pane>
        <n-tab-pane name="videos" :tab="t('page.keys.guide.videos')">
          <p class="keys-hint">{{ t('page.keys.guide.videosNote') }}</p>
          <CodeBlock :code="examples.videoCurl" language="bash" />
          <CodeBlock :code="examples.videoPython" language="python" />
        </n-tab-pane>
      </n-tabs>
      <template #footer>
        <Btn variant="default" @click="guideOpen = false">{{ t('common.close') }}</Btn>
      </template>
    </DetailDrawer>
  </div>
</template>

<style scoped>
.keys-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
.keys-policy {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}
.keys-share__grid {
  display: grid;
  gap: var(--sp-2);
}
.keys-share__row {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
  min-width: 0;
}
.keys-share__label {
  flex: none;
  display: inline-flex;
  align-items: baseline;
  gap: var(--sp-1);
  width: 128px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
.keys-share__label em {
  font-style: normal;
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
}
.keys-share__select { width: 260px; }
.keys-share__row--models { align-items: flex-start; }
.keys-share__models {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-width: 0;
  flex: 1;
}
.keys-chip-count {
  margin-left: var(--sp-1);
  color: var(--fg-muted);
}
.keys-filter { padding: var(--sp-4) var(--sp-5) 0; }
.keys-filter__search { width: 240px; }
.keys-filter__select { width: 160px; }
.keys-body { padding: var(--sp-3) var(--sp-5) var(--sp-5); }
.keys-skeleton { display: grid; gap: var(--sp-2); padding: var(--sp-3); }
.keys-name { display: grid; gap: 2px; min-width: 0; }
.keys-name__main { font-weight: 500; color: var(--fg); overflow: hidden; text-overflow: ellipsis; }
.keys-name__sub { font-size: var(--fs-small); line-height: var(--lh-small); color: var(--fg-muted); }
.keys-key { display: flex; align-items: center; gap: var(--sp-1); min-width: 0; }
.keys-key__text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg-2); }
.keys-quota { display: grid; gap: var(--sp-1); min-width: 0; }
.keys-quota__line { font-size: var(--fs-small); color: var(--fg-2); }
.keys-quota__sep { color: var(--fg-faint); }
.keys-quota__hint { font-size: var(--fs-small); color: var(--fg-muted); }
.keys-concurrency { display: grid; gap: 2px; font-size: var(--fs-small); }
.keys-concurrency__rate { color: var(--fg-muted); }
.keys-ips { display: grid; gap: var(--sp-1); min-width: 0; }
.keys-ips__count {
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--brand);
  font-size: var(--fs-small);
  cursor: pointer;
  text-align: left;
}
.keys-ips__count:hover { text-decoration: underline; }
.keys-ips__badges { display: flex; gap: var(--sp-1); flex-wrap: wrap; }
.keys-muted { color: var(--fg-muted); font-size: var(--fs-small); }
.keys-hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
}
.keys-form { display: grid; gap: var(--sp-3); }
.keys-form__inline { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.keys-form__inline :deep(.n-input) { flex: 1; min-width: 160px; }
.keys-modal__footer { display: flex; justify-content: flex-end; }
.keys-usage-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-2);
}
.keys-usage-cell {
  display: grid;
  gap: 2px;
  padding: var(--sp-3);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
}
.keys-usage-cell span { font-size: var(--fs-small); color: var(--fg-muted); }
.keys-usage-cell strong { font-size: var(--fs-stat); line-height: var(--lh-stat); }
.keys-ipbar { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2); flex-wrap: wrap; }
.keys-model { display: grid; gap: var(--sp-1); padding: var(--sp-2) 0; border-top: 1px solid var(--border-soft); }
.keys-model__head { display: flex; align-items: baseline; gap: var(--sp-2); }
.keys-model__rows { display: grid; gap: var(--sp-1); }
.keys-model__row { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
@media (max-width: 640px) {
  .keys-share__label { width: auto; }
  .keys-share__select { width: 100%; }
  .keys-filter__search, .keys-filter__select { width: 100%; }
  .keys-usage-grid { grid-template-columns: minmax(0, 1fr); }
}
</style>