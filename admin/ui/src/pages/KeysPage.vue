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
import { FMT_EMPTY, fmtCredit, fmtInt, fmtTime } from '@/utils/format'
import {
  buildCurlExamples,
  createKey,
  deleteKey,
  draftToInput,
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
  type KeyFormDraft,
  type KeyFormErrors,
  type KeyIpRow,
  type KeyRow,
  type KeySortKey,
  type KeyUsagePayload,
  type KeyViewPayload,
  type NetworkPayload,
  type PlatformOption,
  type SelectionPolicyPayload,
} from '@/api/keys'

/** 旧版 #panel-keys 的副标题与鉴权说明原文。 */
const HELP_TEXT =
  '对外共享的调用凭证：每个 Key 单独计量、限额与封禁，共享出去不影响后台登录。' +
  'OpenAI 与 Anthropic 两套协议共用同一批 Key、同一套配额与用量记账，只是 base_url 约定不同：' +
  'OpenAI SDK 填含 /v1 的那个，Claude Code / Anthropic SDK 填不含 /v1 的那个' +
  '（SDK 会自己拼 /v1/messages，填错会变成 /v1/v1/messages）。'

const AUTH_TEXT =
  'OpenAI 与 Anthropic 两套协议共用同一批 Key、同一套配额与用量记账，只是 base_url 约定不同：' +
  'OpenAI SDK 填 OpenAI 地址（含 /v1），Claude Code / Anthropic SDK 填 Claude 地址（不含 /v1，' +
  'SDK 会自己拼 /v1/messages，填错会变成 /v1/v1/messages）。' +
  'Claude 端的 claude-sonnet-4-5 这类模型名由服务端自动按档次映射到白名单里的模型，无需手动改。'

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
    return '共 ' + current.total + ' 个 Key · 正常 ' + current.active + ' · 已停用 ' + current.revoked +
      ' · 不限额 ' + current.unlimited
  }
  return '筛选出 ' + current.total + ' / 共 ' + stats.value.total + ' 个 Key · 正常 ' + current.active
})

const emptyDescription = computed(() =>
  hasFilters.value
    ? '没有符合条件的 Key。清空筛选后可看到全部 Key。'
    : '还没有 Key。创建 Key 后即可对外调用网关。',
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
  return item?.label || '账号调度策略'
})
const policyHelp = computed(() => {
  const item = (policy.value?.modes || []).find((mode) => mode.value === policy.value?.mode)
  return (item?.description || '决定网关挑哪一个账号承接请求。') + '（在「积分到期」页修改）'
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
let auxLoaded = false

const groupOptions = computed(() => [
  { label: '不限制（全部可用模型）', value: 0 },
  ...groups.value.map((group) => ({
    label: group.name + (group.effective_count != null ? '（' + group.effective_count + ' 个模型）' : ''),
    value: group.id,
  })),
])
const platformOptionsForForm = computed(() => [
  { label: '不限制（全部平台）', value: '' },
  ...platforms.value.map((item) => ({ label: item.label, value: item.storage })),
])
const accountSelectOptions = computed(() =>
  accountOptions.value.map((account) => ({
    label:
      (account.name || account.uid || '账号') + ' #' + account.id +
      (account.status && account.status !== 'active' ? ' · 已禁用' : '') +
      (account.balance_remain != null ? ' · ' + fmtCredit(account.balance_remain) + ' 积分' : ''),
    value: account.id,
  })),
)
const drawerTitle = computed(() => (editing.value ? '编辑 API Key' : '创建 API Key'))
const submitText = computed(() => (editing.value ? '保存' : '创建'))

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
const ipItems = ref<KeyIpRow[]>([])
const ipOverview = ref(toIpOverview({
  id: 0, name: '', days: 1, sort: 'requests', direction: 'desc', offset: 0, limit: 100,
  total_ips: 0, total_requests: 0, active_ips: 0, active_requests: 0, active_scope: '', active: [], items: [],
}))
let ipKeyId = 0
const modelsOpen = ref(false)
const modelTerm = ref('')
const guideOpen = ref(false)
const guideTab = ref('openai')

const ipColumns = computed<TableColumn[]>(() => [
  { key: 'ip', title: 'IP 地址', mobile: 'title', width: 150 },
  { key: 'requests', title: '调用次数', mobile: 'meta', width: 100, numeric: true, render: (row: TableRow) => fmtInt(row.requests) },
  { key: 'failed', title: '失败', mobile: 'meta', width: 80, numeric: true, render: (row: TableRow) => fmtInt(row.failed) },
  { key: 'credits', title: '积分', mobile: 'meta', width: 90, numeric: true, render: (row: TableRow) => fmtCredit(row.credits) },
  { key: 'tokens', title: 'Token', mobile: 'meta', width: 100, numeric: true, render: (row: TableRow) => fmtInt(row.tokens) },
  { key: 'lastSeen', title: '最近调用', mobile: 'meta', width: 150 },
  { key: 'models', title: '使用模型', mobile: 'subtitle', minWidth: 180 },
  { key: 'active', title: '并发', mobile: 'meta', width: 80, numeric: true, render: (row: TableRow) => fmtInt(row.active) },
  {
    key: 'blocked',
    title: '状态',
    mobile: 'meta',
    width: 90,
    render: (row: TableRow) =>
      h(Badge, { tone: row.blocked ? 'danger' : 'ok' }, { default: () => (row.blocked ? '已封禁' : '正常') }),
  },
])

const modelHits = computed(() => searchModelChips(models.value, modelTerm.value))
const ipRows = computed<TableRow[]>(() => ipItems.value as unknown as TableRow[])

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
    toast.error(failureText(keys.value === null ? '加载 Key' : '刷新 Key', error))
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

async function loadAccountOptions(search: string): Promise<void> {
  accountsLoading.value = true
  try {
    const payload = await listAccountOptions({ search, pageSize: 50 })
    accountOptions.value = Array.isArray(payload?.items) ? payload.items : (payload?.accounts || [])
  } catch {
    accountOptions.value = []
  } finally {
    accountsLoading.value = false
  }
}

async function ensureAuxLoaded(): Promise<void> {
  if (auxLoaded) return
  auxLoaded = true
  void listGroupOptions()
    .then((payload) => { groups.value = Array.isArray(payload?.items) ? payload.items : [] })
    .catch(() => { groups.value = [] })
  void listPlatformOptions()
    .then((payload) => {
      const seen = new Map<string, string>()
      for (const item of payload?.items || []) {
        const storage = String(item.storage_platform || item.id || '').toLowerCase()
        if (!storage || seen.has(storage)) continue
        seen.set(storage, String(item.label || storage))
      }
      platforms.value = [...seen].map(([storage, label]) => ({ storage, label }))
    })
    .catch(() => { platforms.value = [] })
  await loadAccountOptions('')
}

/* ------------------------------ 筛选 / 排序 ------------------------------ */
const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => { search.value = value },
)

const statusOptions = [
  { label: '状态：全部', value: '' },
  { label: '正常', value: 'active' },
  { label: '已停用', value: 'revoked' },
]

const groupFilterOptions = computed(() => [
  { label: '分组：全部', value: '' },
  { label: '未绑定分组', value: 'none' },
  ...groups.value.map((group) => ({ label: group.name, value: String(group.id) })),
])

const chipsList = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: '搜索', value: filters.q })
  if (filters.group) {
    const label = filters.group === 'none'
      ? '未绑定分组'
      : groups.value.find((group) => String(group.id) === filters.group)?.name || filters.group
    list.push({ key: 'group', label: '分组', value: label })
  }
  if (filters.status) list.push({ key: 'status', label: '状态', value: filters.status === 'active' ? '正常' : '已停用' })
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
    h(IconBtn, { icon: Copy, label: '复制完整 Key', onClick: () => void copyFullKey(item) }),
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
      ? h('span', { class: 'keys-quota__hint' }, '不限额')
      : h(Meter, { value: item.usageRatio * 100, tone: item.usageTone, height: 4, ariaLabel: '额度使用率' }),
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
        title: '查看该 Key 的来源 IP 与请求次数，用于识别共享范围与异常来源',
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
    ? h(Badge, { tone: 'brand', title: '该 Key 仅可使用所绑定分组内的模型' }, { default: () => item.groupLabel })
    : h('span', { class: 'keys-muted' }, item.groupLabel)
}

function renderExpire(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h('span', { class: 'keys-muted', title: '后端 Key 模型没有到期字段，到期时间未提供' }, item.expireText)
}

function renderStatus(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h(Badge, { tone: item.statusTone, dot: true }, { default: () => item.statusLabel })
}

function renderActions(row: TableRow): VNodeChild {
  const item = row as KeyRow
  return h(RowActions, {
    actions: [
      { key: 'edit', label: '编辑' },
      { key: 'copy', label: '复制 Key' },
      { key: 'reset', label: '重置用量' },
      { key: 'toggle', label: item.status === 'active' ? '停用' : '启用' },
      { key: 'usage', label: '查看用量' },
      { key: 'delete', label: '删除', danger: true },
    ],
    max: 2,
    onSelect: (key: string) => void onRowAction(item, key),
  })
}

const columns = computed<TableColumn[]>(() => [
  { key: 'name', title: '名称', mobile: 'title', minWidth: 180, sortable: true, sortOrder: sortOrderOf('name'), render: renderName },
  { key: 'key', title: 'Key', mobile: 'subtitle', minWidth: 220, render: renderKey },
  { key: 'quota', title: '限额 / 已用', mobile: 'meta', minWidth: 170, sortable: true, sortOrder: sortOrderOf('used'), render: renderQuota },
  { key: 'concurrency', title: '并发', mobile: 'meta', width: 120, sortable: true, sortOrder: sortOrderOf('concurrency'), render: renderConcurrency },
  { key: 'ips', title: '来源 IP', mobile: 'meta', minWidth: 170, render: renderIps },
  { key: 'group', title: '分组', mobile: 'meta', width: 130, render: renderGroup },
  { key: 'expire', title: '到期', mobile: 'meta', width: 90, render: renderExpire },
  { key: 'status', title: '状态', mobile: 'meta', width: 96, render: renderStatus },
  { key: 'actions', title: '操作', mobile: 'actions', width: 150, hideable: false, fixed: 'right', render: renderActions },
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
    if (ok) toast.success('已复制完整 Key')
    else toast.error('复制失败：浏览器拒绝了剪贴板访问，请手动选中复制')
  } catch (error) {
    toast.error(failureText('读取 Key', error))
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
      toast.success('已保存')
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
    toast.error(failureText(editing.value ? '保存 Key' : '创建 Key', error))
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
      title: '重置已用额度？',
      message: '已用积分归零，历史日志保留。该 Key 立即恢复全部额度。',
      okText: '重置用量',
    })
    if (!ok) return
    try {
      await patchKey(row.id, { reset_used: true })
      toast.success('已重置已用额度')
      await loadKeys()
    } catch (error) {
      toast.error(failureText('重置用量', error))
    }
    return
  }
  if (action === 'toggle') {
    try {
      await patchKey(row.id, statusPatch(row.status))
      toast.success(row.status === 'active' ? '已停用' : '已启用')
      await loadKeys()
    } catch (error) {
      toast.error(failureText(row.status === 'active' ? '停用 Key' : '启用 Key', error))
    }
    return
  }
  if (action === 'delete') {
    const ok = await confirm({
      title: '删除该 Key？',
      message: '删除后该 Key 立即无法调用网关，用量记录保留。如需临时停止调用，请使用停用。',
      danger: true,
      okText: '删除',
    })
    if (!ok) return
    try {
      await deleteKey(row.id)
      toast.success('已删除')
      await loadKeys()
    } catch (error) {
      toast.error(failureText('删除 Key', error))
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
    ipItems.value = payload.items || []
    ipOverview.value = toIpOverview(payload)
  } catch (error) {
    ipError.value = errorMessage(error)
    ipItems.value = []
  } finally {
    ipLoading.value = false
  }
}

async function openIps(row: KeyRow): Promise<void> {
  ipKeyId = row.id
  ipKeyName.value = row.name
  ipDays.value = '1'
  ipItems.value = []
  ipOpen.value = true
  await loadIps()
}

const ipSummaryItems = computed<KvItem[]>(() => [
  { label: '来源 IP', value: fmtInt(ipOverview.value.totalIps) },
  { label: '调用次数', value: fmtInt(ipOverview.value.requests) },
  { label: '当前活跃 IP', value: fmtInt(ipOverview.value.activeIps) },
  { label: '当前并发', value: fmtInt(ipOverview.value.activeRequests) },
])

const usageView = computed(() => (usageData.value ? toUsageView(usageData.value) : null))
const viewItems = computed<KvItem[]>(() => [
  { label: '名称', value: viewData.value?.name || FMT_EMPTY },
  { label: '前缀', value: viewData.value?.key_prefix || FMT_EMPTY, mono: true },
  { label: '创建于', value: viewData.value?.created_at ? fmtTime(viewData.value.created_at) : FMT_EMPTY },
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
    <PageHeader title="API Keys" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadKeys()">刷新</Btn>
        <span class="keys-policy">
          <Btn variant="quiet" @click="router.push('/credits')">{{ policyLabel }}</Btn>
          <HelpTip :content="policyHelp" label="账号调度策略说明" />
        </span>
        <Btn variant="default" @click="guideOpen = true">接入指南</Btn>
        <Btn variant="primary" :icon="Plus" @click="openCreate">创建 Key</Btn>
      </template>
    </PageHeader>

    <Card class="keys-share" title="对接信息">
      <template #extra>
        <Btn variant="quiet" size="sm" :loading="modelsLoading" @click="loadAccessories()">刷新</Btn>
      </template>

      <div class="keys-share__grid">
        <div class="keys-share__row">
          <span class="keys-share__label">连接地址</span>
          <n-select
            class="keys-share__select"
            :value="origin"
            :options="origins"
            @update:value="(value: string) => (origin = value)"
          />
        </div>
        <div class="keys-share__row">
          <span class="keys-share__label">OpenAI<em>含 /v1</em></span>
          <Copyable :value="openaiBase" />
        </div>
        <div class="keys-share__row">
          <span class="keys-share__label">Claude<em>不含 /v1</em></span>
          <Copyable :value="claudeBase" />
        </div>
        <div class="keys-share__row">
          <span class="keys-share__label">鉴权</span>
          <Copyable value="Authorization: Bearer <你的 Key>　或　x-api-key: <你的 Key>" />
          <HelpTip :content="AUTH_TEXT" label="鉴权说明" />
        </div>
        <div class="keys-share__row keys-share__row--models">
          <span class="keys-share__label">可用模型</span>
          <div class="keys-share__models">
            <span class="keys-muted">{{ chips.total }} 组</span>
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
              全部 {{ chips.total }} ▸
            </Btn>
            <span v-if="!chips.total && !modelsLoading" class="keys-muted">未配置模型白名单（放行全部模型）</span>
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
              placeholder="搜索名称 / 备注 / 账号"
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
          :title="'加载 Key 失败：' + loadError.message"
          :detail="loadError.detail"
          @retry="loadKeys()"
        />

        <div v-else-if="firstLoading" class="keys-skeleton" aria-busy="true" aria-label="Key 加载中">
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
              <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">清除筛选</Btn>
              <Btn v-else variant="primary" size="sm" :icon="Plus" @click="openCreate">创建 Key</Btn>
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
      <Section title="基本" first>
        <div class="keys-form">
          <FormField label="名称" :required="Boolean(editing)" :error="errors.name" hint="创建时可留空，后端会填「未命名」。">
            <n-input v-model:value="draft.name" placeholder="例如：给设计组的 Key" maxlength="120" clearable />
          </FormField>
          <FormField label="模型分组" hint="选择后该 Key 只能调用组内模型，调用组外模型返回 403。">
            <n-select v-model:value="draft.groupId" :options="groupOptions" />
          </FormField>
          <FormField label="备注">
            <n-input v-model:value="draft.note" type="textarea" :rows="2" maxlength="255" placeholder="可选" />
          </FormField>
        </div>
      </Section>

      <Section title="额度与流量">
        <div class="keys-form">
          <FormField
            label="总额度（积分）"
            :error="errors.creditLimit"
            hint="勾选「不限额」时忽略；填 0 表示零额度。"
          >
            <div class="keys-form__inline">
              <n-checkbox v-model:checked="draft.unlimited">不限额（∞）</n-checkbox>
              <n-input
                v-model:value="draft.creditLimit"
                placeholder="留空不限额；0 表示零额度"
                :disabled="draft.unlimited"
              />
            </div>
          </FormField>
          <FormField
            label="并发上限"
            :error="errors.maxConcurrent"
            hint="空 = 跟随全局；0 = 不限；N = 该 Key 所有对话合计最多 N 个在途请求。"
          >
            <n-input v-model:value="draft.maxConcurrent" placeholder="空 = 全局，0 = 不限" />
          </FormField>
          <FormField
            label="每分钟请求数"
            :error="errors.ratePerMinute"
            hint="频率与并发独立；积分不限额也不会解除流量限制。"
          >
            <n-input v-model:value="draft.ratePerMinute" placeholder="空 = 全局，0 = 不限" />
          </FormField>
          <FormField v-if="editing" label="重置已用额度">
            <n-checkbox v-model:checked="draft.resetUsed">
              勾选后把已用积分归零（当前 {{ editing?.usedText }}，不影响历史日志）
            </n-checkbox>
          </FormField>
        </div>
      </Section>

      <Section title="模型与账号">
        <div class="keys-form">
          <FormField label="聚合模型路由" hint="开启后 /v1/models 返回跨平台聚合列表，可由网关挑平台转发。">
            <n-checkbox v-model:checked="draft.aggregate">使用聚合模型（跨平台负载分摊）</n-checkbox>
          </FormField>
          <FormField label="独占平台" hint="绑定后只在该平台的账号池里选号；不限制则按请求自报平台。">
            <n-select v-model:value="draft.platform" :options="platformOptionsForForm" />
          </FormField>
          <FormField
            label="独占绑定账号（多选）"
            hint="不选 = 使用公共账号池；选中后只用这些账号，账号不可用时不换号。搜索走服务端。"
          >
            <n-select
              :value="draft.accountIds"
              multiple
              filterable
              remote
              clearable
              :loading="accountsLoading"
              :options="accountSelectOptions"
              placeholder="不限制（使用公共账号池）"
              @update:value="onAccountsChange"
              @search="(value: string) => loadAccountOptions(value)"
            />
          </FormField>
        </div>
      </Section>

      <Section title="访问与有效期">
        <p class="keys-hint">
          来源 IP 只用于统计（表格「来源 IP」列可查看），后端没有「绑定来源 IP」字段，因此这里不提供绑定位。
        </p>
        <p class="keys-hint">后端 Key 模型没有到期字段，因此不提供有效期设置，表格「到期」列显示 —。</p>
      </Section>
    </FormDrawer>

    <!-- 创建成功：完整 Key（旧版同样可随时在列表里再次查看） -->
    <n-modal v-model:show="createdOpen" preset="card" class="keys-modal" title="Key 已创建" :style="{ width: '520px' }">
      <p class="keys-hint">可随时在列表里点「复制 Key」再次查看完整值。</p>
      <Copyable :value="createdKey" />
      <template #footer>
        <div class="keys-modal__footer">
          <Btn variant="primary" @click="createdOpen = false">完成</Btn>
        </div>
      </template>
    </n-modal>

    <!-- 查看用量 -->
    <DetailDrawer v-model:show="usageOpen" title="Key 用量" subtitle="按额度统计">
      <ErrorBlock v-if="usageError" :title="'读取用量失败：' + usageError" :retry="false" compact />
      <div v-else-if="usageLoading" class="keys-skeleton"><n-skeleton :repeat="3" text /></div>
      <template v-else-if="usageView">
        <div class="keys-usage-grid">
          <div class="keys-usage-cell"><span>限额</span><strong class="tnum">{{ usageView.limitText }}</strong></div>
          <div class="keys-usage-cell"><span>已用</span><strong class="tnum">{{ usageView.usedText }}</strong></div>
          <div class="keys-usage-cell"><span>剩余</span><strong class="tnum">{{ usageView.remainingText }}</strong></div>
          <div class="keys-usage-cell"><span>使用率</span><strong class="tnum">{{ usageView.percentText }}</strong></div>
        </div>
        <Meter
          v-if="!usageView.unlimited"
          :value="usageView.ratio * 100"
          :tone="usageView.tone"
          :height="8"
          aria-label="额度使用率"
        />
        <p class="keys-hint">创建于 {{ usageView.createdText }}</p>
      </template>
      <template #footer>
        <Btn variant="default" @click="usageOpen = false">关闭</Btn>
      </template>
    </DetailDrawer>

    <!-- 来源 IP -->
    <DetailDrawer v-model:show="ipOpen" :title="'来源 IP · ' + ipKeyName" subtitle="并发为当前服务进程的在途请求">
      <div class="keys-ipbar">
        <Segmented
          :options="[
            { label: '24 小时', value: '1' },
            { label: '7 天', value: '7' },
            { label: '30 天', value: '30' },
          ]"
          :model-value="ipDays"
          aria-label="时间范围"
          @update:model-value="
            (value: string | number) => {
              ipDays = String(value)
              void loadIps()
            }
          "
        />
        <Btn variant="quiet" size="sm" :loading="ipLoading" @click="loadIps()">刷新</Btn>
      </div>
      <KV :items="ipSummaryItems" />
      <ErrorBlock v-if="ipError" :title="'读取来源 IP 失败：' + ipError" :retry="false" compact />
      <DataTable
        v-else
        table-id="keys-ips"
        :columns="ipColumns"
        :data="ipRows"
        row-key="ip"
        :pagination="false"
        :virtual-scroll="false"
        max-height="360px"
        empty-description="所选时间内暂无调用记录"
      />
      <p class="keys-hint">封禁 / 解封在「IP 管理」页操作；这里只读展示统计。</p>
      <template #footer>
        <Btn variant="default" @click="ipOpen = false">关闭</Btn>
      </template>
    </DetailDrawer>

    <!-- 全部模型 -->
    <DetailDrawer v-model:show="modelsOpen" :title="'可用模型 · ' + chips.total + ' 组'" subtitle="点击模型名复制调用名（带平台后缀）">
      <n-input v-model:value="modelTerm" placeholder="搜索模型名 / 调用名 / 上游 id" clearable />
      <div v-if="!modelHits.length" class="keys-muted">没有匹配的模型。</div>
      <div v-for="hit in modelHits" :key="hit.key" class="keys-model">
        <div class="keys-model__head">
          <strong>{{ hit.name }}</strong>
          <span class="keys-muted">{{ hit.platformCount }} 平台</span>
        </div>
        <div class="keys-model__rows">
          <div v-for="row in hit.rows" :key="row.callId" class="keys-model__row">
            <span class="keys-muted">{{ row.platformLabel }}</span>
            <Chip mono copy :value="row.callId" :title="'上游 id：' + row.upstreamId">{{ row.callId }}</Chip>
            <span class="keys-muted tnum">{{ row.multiplierText }}</span>
          </div>
        </div>
      </div>
      <template #footer>
        <Btn variant="default" @click="modelsOpen = false">关闭</Btn>
      </template>
    </DetailDrawer>

    <!-- 接入指南 -->
    <DetailDrawer v-model:show="guideOpen" title="接入指南" subtitle="地址与模型已按当前连接地址填好">
      <n-tabs v-model:value="guideTab" type="line" animated>
        <n-tab-pane name="openai" tab="OpenAI 协议">
          <CodeBlock :code="examples.openai" language="bash" />
        </n-tab-pane>
        <n-tab-pane name="claude" tab="Claude Code / Anthropic">
          <CodeBlock :code="examples.claude" language="bash" />
        </n-tab-pane>
        <n-tab-pane name="audio" tab="语音转文字">
          <p class="keys-hint">
            POST /v1/audio/transcriptions，使用同一 API Key：multipart/form-data 上传 file，
            model=workbuddy-asr；response_format 可选 json（默认）或 text。网关文件上限 25 MiB，
            不支持 language / prompt / 时间戳 / 流式转写；不保存音频与转写内容。
          </p>
          <CodeBlock :code="examples.audioCurl" language="bash" />
          <CodeBlock :code="examples.audioPython" language="python" />
        </n-tab-pane>
        <n-tab-pane name="images" tab="AI 生图">
          <p class="keys-hint">
            文生图 POST /v1/images/generations，图生图 POST /v1/images/edits（单张 image，最大 20 MiB；
            也支持 image_url / base64 image_data）。返回 url（默认）或 b64_json；暂不支持 mask、流式、多图。
          </p>
          <CodeBlock :code="examples.imagesPython" language="python" />
        </n-tab-pane>
        <n-tab-pane name="videos" tab="AI 生视频（异步任务）">
          <p class="keys-hint">
            创建 POST /v1/videos 只到「上游已入队」就返回 queued；轮询 GET /v1/videos/{id}，
            下载 GET /v1/videos/{id}/content。生成通常需要 1~5 分钟，按上游 usage.credit 实际积分记账。
          </p>
          <CodeBlock :code="examples.videoCurl" language="bash" />
          <CodeBlock :code="examples.videoPython" language="python" />
        </n-tab-pane>
      </n-tabs>
      <template #footer>
        <Btn variant="default" @click="guideOpen = false">关闭</Btn>
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