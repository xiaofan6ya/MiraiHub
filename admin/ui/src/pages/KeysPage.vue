<script setup lang="ts">
/**
 * S14 API Keys /keys（v2.1 视觉精修）
 *
 * 布局（10-visual-refresh.md）：
 *   PageHeader（接入指南 · 唯一主按钮「创建 Key」）
 *   → 对接信息英雄卡（连接地址 / base_url / 鉴权 · 可用模型 · 账号调度策略）
 *   → Key 列表（一行工具栏：搜索 · 分组 · 状态 … 统计 · 重置 · 刷新 · 密度 · 列设置）
 *   → 抽屉：创建 / 编辑、用量、来源 IP、全部模型、接入指南；弹窗：创建成功、查看完整 Key（先提示会记审计）
 *
 * 子组件在 src/pages/keys/；本文件管列表数据、筛选 ↔ 路由、表单与行操作。
 * 契约：docs/ui-v2/contracts/api-keys.md。后端没有 Key 到期字段、也没有来源 IP 绑定：创建抽屉里如实说明，不伪造能力。
 * 安全（fixlog_security §S5）：GET /api/keys/{id}/view 每次调用都写审计 ——「复制 Key」与「查看完整 Key」都会触发，界面上都写明。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NCheckbox, NInput, NModal, NSelect } from 'naive-ui'
import { Plus } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import { registerCommand } from '@/layout/commandRegistry'
import {
  Btn,
  Copyable,
  ErrorBlock,
  FormDrawer,
  FormField,
  Section,
  type SorterPayload,
} from '@/components'
import { useConfirm, useKeepAliveSuspended, useRevisit, useTableQuery, useToast } from '@/composables'
import { copyText } from '@/composables/useCopy'
import { ApiError, isAbortError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import { fmtCredit } from '@/utils/format'
import {
  buildCurlExamples,
  createKey,
  deleteKey,
  draftToInput,
  filterKeyRows,
  getNetwork,
  getSelectionPolicy,
  hasFormErrors,
  keyStats,
  listAccountOptions,
  listAggregateModels,
  listGroupOptions,
  listKeys,
  listPlatformOptions,
  modelChips,
  originOptions,
  paginateKeyRows,
  patchKey,
  sortKeyRows,
  statusPatch,
  toKeyRows,
  validateKeyForm,
  viewKey,
  type AccountOption,
  type AggregateModelsPayload,
  type ApiKeyItem,
  type KeyFormDraft,
  type KeyFormErrors,
  type KeyRow,
  type KeySortKey,
  type NetworkPayload,
  type PlatformOption,
  type SelectionPolicyPayload,
} from '@/api/keys'
import KeysShareCard from './keys/KeysShareCard.vue'
import KeysToolbar from './keys/KeysToolbar.vue'
import KeysTable from './keys/KeysTable.vue'
import KeyRevealModal from './keys/KeyRevealModal.vue'
import KeyUsageDrawer from './keys/KeyUsageDrawer.vue'
import KeyIpsDrawer from './keys/KeyIpsDrawer.vue'
import KeyModelsDrawer from './keys/KeyModelsDrawer.vue'
import KeyGuideDrawer from './keys/KeyGuideDrawer.vue'

const { t } = useI18n()

const helpText = computed(() => t('page.keys.help'))

const router = useRouter()
const toast = useToast()
/** 第 5 轮：KeepAlive 离开时收起本页直接使用的 NModal（Teleport 不随页面移走，否则会盖在下一页上）。 */
const suspended = useKeepAliveSuspended()
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
const hasFilters = computed(() =>
  Boolean(table.state.filters.q || table.state.filters.group || table.state.filters.status),
)

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
  return t('page.keys.summary.filtered', { total: current.total, all: stats.value.total, active: current.active })
})

const emptyDescription = computed(() => (hasFilters.value ? t('page.keys.empty.filtered') : t('page.keys.empty.none')))

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
const auxFailed = computed(() => auxFailures.value.groups || auxFailures.value.platforms || auxFailures.value.accounts)
/** 本次打开抽屉是否已经取过候选；失败后会复位，下次打开或点「重新加载」都会重新取。 */
let auxLoaded = false

const groupOptions = computed(() => [
  { label: t('page.keys.groupOption.none'), value: 0 },
  ...groups.value.map((group) => {
    // 分组里没有「已启用的模型」时，绑上去等于把 Key 变成不能用：
    // 每次调用都会被 effective_group_models 判成组外 → 403。历史上模型改名 /
    // 下架后就会变成这种空分组（issue #120：「分组实际不可选」）。
    // 所以：**别的分组可以选，这个不行**，并写清为什么、去哪修。
    //
    // 例外：编辑一个**已经绑着**这个空分组的 Key 时不能禁选 —— 否则用户连
    // 「改别的字段」都保存不了（Naive UI 对 disabled 选项的当前值处理很别扭）。
    const empty = group.effective_count === 0
    const keepSelectable = draft.value.groupId === group.id
    const suffix = group.effective_count != null
      ? t('page.keys.groupOption.count', { count: group.effective_count })
      : ''
    return {
      label: group.name + suffix + (empty ? t('page.keys.groupOption.empty') : ''),
      value: group.id,
      disabled: empty && !keepSelectable,
    }
  }),
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
      (account.balance_remain != null ? t('page.keys.account.credits', { amount: fmtCredit(account.balance_remain) }) : ''),
    value: account.id,
  })),
)
const drawerTitle = computed(() => (editing.value ? t('page.keys.drawer.edit') : t('page.keys.drawer.create')))
const submitText = computed(() => (editing.value ? t('common.save') : t('page.keys.submit.create')))

/* ------------------------------ 抽屉 / 弹窗开关 ------------------------------ */
const usageOpen = ref(false)
const usageRow = ref<KeyRow | null>(null)
const ipOpen = ref(false)
const ipRow = ref<KeyRow | null>(null)
const modelsOpen = ref(false)
const guideOpen = ref(false)
const revealOpen = ref(false)
const revealRow = ref<KeyRow | null>(null)

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
    toast.error(failureText(keys.value === null ? t('page.keys.action.load') : t('page.keys.action.refreshKeys'), error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

async function loadAccessories(): Promise<void> {
  void getSelectionPolicy()
    .then((payload) => {
      policy.value = payload
    })
    .catch(() => {
      policy.value = null
    })
  void getNetwork()
    .then((payload) => {
      network.value = payload
    })
    .catch(() => {
      network.value = null
    })
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
/** 第 5 轮：远程搜索只认最后一次（快速输入时先发的慢响应不覆盖后发的结果）。 */
let accountOptionsSeq = 0

async function loadAccountOptions(search: string): Promise<boolean> {
  const seq = ++accountOptionsSeq
  accountsLoading.value = true
  try {
    const payload = await listAccountOptions({ search, pageSize: 50 })
    if (seq !== accountOptionsSeq) return true
    accountOptions.value = Array.isArray(payload?.items) ? payload.items : payload?.accounts || []
    auxFailures.value.accounts = false
    return true
  } catch {
    if (seq !== accountOptionsSeq) return true
    accountOptions.value = []
    auxFailures.value.accounts = true
    return false
  } finally {
    if (seq === accountOptionsSeq) accountsLoading.value = false
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

/** 预取创建 / 编辑抽屉的候选数据；失败会复位 auxLoaded，重新打开抽屉或点「重新加载」都会重取。 */
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

async function reloadAux(): Promise<void> {
  if (auxLoading.value) return
  auxLoaded = false
  await ensureAuxLoaded()
}

/* ------------------------------ 筛选 / 排序 ------------------------------ */
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

const SORT_KEYS: Record<string, KeySortKey> = {
  name: 'name',
  quota: 'used',
  used: 'used',
  limit: 'limit',
  concurrency: 'concurrency',
}

function onSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order || !SORT_KEYS[String(sorter.columnKey)]) {
    table.setSort('', '')
    return
  }
  table.setSort(SORT_KEYS[String(sorter.columnKey)], sorter.order === 'ascend' ? 'asc' : 'desc')
}

function setFilter(name: 'q' | 'group' | 'status', value: string): void {
  table.setFilter(name, value)
}

function resetFilters(): void {
  table.reset()
}

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

function openReveal(row: KeyRow): void {
  revealRow.value = row
  revealOpen.value = true
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
    toast.error(failureText(editing.value ? t('page.keys.action.saveKey') : t('page.keys.action.create'), error))
  } finally {
    submitting.value = false
  }
}

function closeCreated(): void {
  createdOpen.value = false
  // 明文只在这一次弹窗里出现：关掉就丢弃
  createdKey.value = ''
}

async function onRowAction(row: KeyRow, action: string): Promise<void> {
  if (action === 'edit') return openEdit(row)
  if (action === 'copy') return copyFullKey(row)
  if (action === 'reveal') return openReveal(row)
  if (action === 'usage') {
    usageRow.value = row
    usageOpen.value = true
    return
  }
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
        failureText(row.status === 'active' ? t('page.keys.action.disableKey') : t('page.keys.action.enableKey'), error),
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

function openIps(row: KeyRow): void {
  ipRow.value = row
  ipOpen.value = true
}

/** 命令面板「创建 Key」（03-shell §2.4）：id 与 layout/commandRegistry.ts 的 SHELL_SHORTCUTS 一致。 */
let offCreateCommand: (() => void) | null = null

/**
 * 第 5 轮：二次进入（KeepAlive）/ 后台轮询属于「静默刷新」——DataTable 用 `refreshing`
 * 保留旧数据、只在工具栏转小圈，不出顶部进度条。用计数，重叠的静默刷新互不打断。
 */
const silentDepth = ref(0)
const silentRefresh = computed(() => silentDepth.value > 0)
async function silently(task: () => Promise<unknown> | void): Promise<void> {
  silentDepth.value += 1
  try {
    await task()
  } finally {
    silentDepth.value -= 1
  }
}

// 二次进入（KeepAlive 命中）：保留旧数据、不出骨架，后台静默刷新（11-polish-motion §2）。
useRevisit(
  () => {
    void silently(() => loadKeys())
    void loadAccessories()
  },
  { minIntervalMs: 10_000 },
)

onMounted(() => {
  void loadKeys()
  void loadAccessories()
  offCreateCommand = registerCommand({
    id: 'keys.create',
    title: t('cmdk.createKey'),
    hint: t('cmdk.createKeyHint'),
    keywords: t('cmdk.keywordsCreateKey'),
    run: () => openCreate(),
  })
})

onBeforeUnmount(() => {
  controller?.abort()
  offCreateCommand?.()
  offCreateCommand = null
})
</script>

<template>
  <div class="keys-page mh-page mh-page--wide">
    <PageHeader title="API Keys" :help="helpText">
      <template #actions>
        <Btn variant="default" @click="guideOpen = true">{{ t('page.keys.action.guide') }}</Btn>
        <Btn variant="primary" :icon="Plus" @click="openCreate">{{ t('page.keys.action.create') }}</Btn>
      </template>
    </PageHeader>

    <KeysShareCard
      v-model:origin="origin"
      :origins="origins"
      :openai-base="openaiBase"
      :claude-base="claudeBase"
      :chips="chips"
      :models-loading="modelsLoading"
      :policy-label="policyLabel"
      :policy-help="policyHelp"
      @refresh="loadAccessories()"
      @show-models="modelsOpen = true"
      @open-policy="router.push('/credits')"
    />

    <section class="keys-list">
      <ErrorBlock
        v-if="loadError && keys === null"
        :title="t('page.keys.error.load', { error: loadError.message })"
        :detail="loadError.detail"
        @retry="loadKeys()"
      />
      <div v-else-if="firstLoading" class="keys-skeleton" aria-busy="true" :aria-label="t('page.keys.loadingAria')">
        <span v-for="index in 6" :key="index" class="mh-skeleton keys-skeleton__row" />
      </div>
      <KeysTable
        v-else
        :rows="pageData.items"
        :total="pageData.total"
        :page="table.state.page"
        :page-size="table.state.pageSize"
        :loading="loading && !silentRefresh"
        :refreshing="loading && silentRefresh"
        :sort-by="table.state.sortBy"
        :sort-order="table.state.sortOrder"
        :has-filters="hasFilters"
        :empty-description="emptyDescription"
        @action="onRowAction"
        @ips="openIps"
        @sorter="onSorter"
        @page="table.setPage"
        @page-size="table.setPageSize"
        @create="openCreate"
        @reset="resetFilters"
      >
        <template #toolbar>
          <KeysToolbar
            :q="table.state.filters.q"
            :group="table.state.filters.group"
            :status="table.state.filters.status"
            :group-options="groupFilterOptions"
            :status-options="statusOptions"
            :summary="summary"
            :has-filters="hasFilters"
            :loading="loading"
            @set="setFilter"
            @reset="resetFilters"
            @refresh="loadKeys()"
          />
        </template>
      </KeysTable>
    </section>

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
            <NInput v-model:value="draft.name" :placeholder="t('page.keys.form.namePlaceholder')" maxlength="120" clearable />
          </FormField>
          <FormField :label="t('page.keys.form.group')" :hint="t('page.keys.form.groupHint')">
            <NSelect v-model:value="draft.groupId" :options="groupOptions" />
          </FormField>
          <FormField :label="t('page.keys.form.note')">
            <NInput
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
          <FormField :label="t('page.keys.form.limit')" :error="errors.creditLimit" :hint="t('page.keys.form.limitHint')">
            <div class="keys-form__inline">
              <NCheckbox v-model:checked="draft.unlimited">{{ t('page.keys.form.unlimited') }}</NCheckbox>
              <NInput
                v-model:value="draft.creditLimit"
                :placeholder="t('page.keys.form.limitPlaceholder')"
                :disabled="draft.unlimited"
              />
            </div>
          </FormField>
          <div class="keys-form__pair">
            <FormField
              :label="t('page.keys.form.concurrency')"
              :error="errors.maxConcurrent"
              :hint="t('page.keys.form.concurrencyHint')"
            >
              <NInput v-model:value="draft.maxConcurrent" :placeholder="t('page.keys.form.concurrencyPlaceholder')" />
            </FormField>
            <FormField :label="t('page.keys.form.rate')" :error="errors.ratePerMinute" :hint="t('page.keys.form.rateHint')">
              <NInput v-model:value="draft.ratePerMinute" :placeholder="t('page.keys.form.concurrencyPlaceholder')" />
            </FormField>
          </div>
          <FormField v-if="editing" :label="t('page.keys.form.resetUsed')">
            <NCheckbox v-model:checked="draft.resetUsed">
              {{ t('page.keys.form.resetUsedLabel', { used: editing?.usedText || '' }) }}
            </NCheckbox>
          </FormField>
        </div>
      </Section>

      <Section :title="t('page.keys.section.scope')">
        <div class="keys-form">
          <FormField :label="t('page.keys.form.aggregate')" :hint="t('page.keys.form.aggregateHint')">
            <NCheckbox v-model:checked="draft.aggregate">{{ t('page.keys.form.aggregateLabel') }}</NCheckbox>
          </FormField>
          <FormField :label="t('page.keys.form.platform')" :hint="t('page.keys.form.platformHint')">
            <NSelect v-model:value="draft.platform" :options="platformOptionsForForm" />
          </FormField>
          <FormField :label="t('page.keys.form.accounts')" :hint="t('page.keys.form.accountsHint')">
            <NSelect
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

    <!-- 创建成功：完整 Key 只在这里出现一次 -->
    <NModal
      :show="createdOpen && !suspended"
      preset="card"
      class="keys-modal"
      :title="t('page.keys.created.title')"
      :style="{ width: '520px', maxWidth: 'calc(100vw - 32px)' }"
      @update:show="(value: boolean) => (value ? (createdOpen = true) : closeCreated())"
    >
      <div class="keys-created">
        <Copyable :value="createdKey" />
        <p class="keys-hint">{{ t('page.keys.created.note') }}</p>
      </div>
      <template #footer>
        <div class="keys-modal__footer">
          <Btn variant="primary" @click="closeCreated">{{ t('page.keys.created.done') }}</Btn>
        </div>
      </template>
    </NModal>

    <KeyRevealModal v-model:show="revealOpen" :row="revealRow" />
    <KeyUsageDrawer v-model:show="usageOpen" :row="usageRow" />
    <KeyIpsDrawer v-model:show="ipOpen" :row="ipRow" />
    <KeyModelsDrawer v-model:show="modelsOpen" :models="models" :total="chips.total" />
    <KeyGuideDrawer v-model:show="guideOpen" :examples="examples" />
  </div>
</template>

<style scoped>
.keys-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.keys-list {
  min-width: 0;
}

.keys-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.keys-skeleton__row {
  height: 36px;
}

.keys-hint {
  margin: 0;
  max-width: 80ch;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  text-wrap: pretty;
}

.keys-form {
  display: grid;
  gap: var(--sp-3);
}

.keys-form__pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-3);
}

.keys-form__inline {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

.keys-form__inline :deep(.n-input) {
  flex: 1;
  min-width: 160px;
}

.keys-created {
  display: grid;
  gap: var(--sp-2);
}

.keys-modal__footer {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 640px) {
  .keys-form__pair {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
