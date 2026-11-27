<script setup lang="ts">
/**
 * S25/S26 账号池 /accounts（v2.1 视觉精修 + 拆分）
 *
 * 契约：docs/ui-v2/contracts/accounts.md（末尾「## 实现对照」）
 * 规范：docs/ui-v2/10-visual-refresh.md（最高优先级）
 *
 * 原来 2239 行的单文件拆成 src/pages/accounts/ 下的子组件（§5）：
 *   AccountsToolbar     状态页签 + 搜索 + 积分区间 + 统计（表格上方唯一一行）
 *   AccountsTable       列定义 / 单元格 / 批量条 / 空状态
 *   AccountDetailDrawer 详情（KV + 积分明细 + 模型冷却 + 操作按钮）
 *   AccountEditDrawer   名称 / 并发上限
 *   AddAccountDrawer    手动 / 导入 / 扫描本机 / 手机号 / 浏览器授权
 *   DedupeDialog        清理重复账号（按 platform/realm 分组，data-scoping D1）
 *   ModelLimitsDrawer   模型额度与冷却
 *   JobDetailDrawer     后台任务逐条结果
 * 本文件只做：取数、筛选 / 排序 / 分页状态、行与批量操作的确认和请求、后台任务、页头。
 *
 * 页头：唯一主按钮「添加账号 ▾」（导入 / 扫描本机都在它的下拉里）；整池任务收进「⋯」；
 * meta 显示「N 个账号 · N 个冷却」。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NDropdown, type DropdownOption } from 'naive-ui'
import { ChevronDown, Download, MoreHorizontal, Plus } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import { Btn, ErrorBlock, JobProgress, type JobItem, type SorterPayload } from '@/components'
import { useConfirm, useJob, useTableQuery, useToast } from '@/composables'
import { registerCommand } from '@/layout/commandRegistry'
import { usePlatformStore } from '@/stores/platform'
import { fmtCredit, fmtInt } from '@/utils/format'
import { failureText } from '@/utils/error'
import { ApiError, isAbortError, download } from '@/api/http'
import {
  accountFeatures,
  accountTabCounts,
  accountsOverview,
  bulkAccounts,
  exportAccountsAsync,
  isAccountExportConfirmRequired,
  exportTokenOf,
  filterAccountRows,
  getTodayUsage,
  jobDoneText,
  listAccounts,
  normalizeAccountStatus,
  paginateAccountRows,
  sortAccountRows,
  toAccountRows,
  toJobItem,
  type AccountItem,
  type AccountJobSnapshot,
  type AccountRow,
  type AccountStatusTab,
  type AccountUsageToday,
} from '@/api/accounts'
import { addMethodsFor, methodEnabled, type AddMethodKey } from '@/api/account-add'
import { useI18n } from '@/i18n'
import AccountsToolbar from './accounts/AccountsToolbar.vue'
import AccountsTable from './accounts/AccountsTable.vue'
import { useRevisit } from '@/pages/_shared/useRevisit'
import AccountDetailDrawer from './accounts/AccountDetailDrawer.vue'
import AccountEditDrawer from './accounts/AccountEditDrawer.vue'
import AddAccountDrawer from './accounts/AddAccountDrawer.vue'
import DedupeDialog from './accounts/DedupeDialog.vue'
import ModelLimitsDrawer from './accounts/ModelLimitsDrawer.vue'
import JobDetailDrawer from './accounts/JobDetailDrawer.vue'
import type { AddDrawerKind, BulkAction, DetailActionKey } from './accounts/types'
import { useAccountActions } from './accounts/useAccountActions'
import AccountsJobBar from './accounts/AccountsJobBar.vue'

const toast = useToast()
const { t } = useI18n()
const helpText = computed(() => t('page.accounts.help'))
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
const loading = ref(true)
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
    // 今日消耗（/api/logs/summary）是慢接口且不是首屏必需：失败只当没有数据，不挡列表（第 3 轮审查 B9）。
    const [payload, usage] = await Promise.all([
      listAccounts({}, current.signal),
      getTodayUsage(current.signal).catch(() => null),
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
    // 删除 / 筛选后当前页可能越界：回退到有效页。
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / table.state.pageSize))
    if (table.state.page > maxPage) table.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error) || controller !== current) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    const action = t(items.value ? 'page.accounts.reloadFailed' : 'page.accounts.loadFailed')
    loadError.value = { message: failureText(action, error), detail }
    toast.error(failureText(action, error))
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

const allRows = computed(() => toAccountRows(items.value, usageMap.value, t))
const counts = computed(() => accountTabCounts(allRows.value))
const filteredRows = computed(() => filterAccountRows(allRows.value, table.state.filters))
const sortedRows = computed(() =>
  sortAccountRows(filteredRows.value, table.state.sortBy, table.state.sortOrder),
)
const pageData = computed(() =>
  paginateAccountRows(sortedRows.value, table.state.page, table.state.pageSize),
)
const showTokenColumn = computed(() => allRows.value.some((row) => row.raw.token_supported))

const hasFilters = computed(() =>
  Boolean(table.state.filters.q || table.state.filters.min || table.state.filters.max || statusTab.value !== 'all'),
)

/** 页头 meta：「35 个账号 · 3 个冷却」（冷却为 0 时不写，常态不显示）。 */
const headerMeta = computed(() => {
  if (items.value === null) return ''
  const all = accountsOverview(allRows.value)
  const parts = [t('page.accounts.metaTotal', { count: fmtInt(all.total) })]
  if (all.cooling > 0) parts.push(t('page.accounts.metaCooling', { count: fmtInt(all.cooling) }))
  if (all.total > 0 && all.disabled === all.total) parts.push(t('page.accounts.metaAllDisabled'))
  return parts.join(' · ')
})

/** 工具栏右侧统计：只算当前筛选结果；「可用」不含已禁用 / 冷却（第 1 轮审查 P0-9）。 */
const summary = computed(() => {
  if (items.value === null) return ''
  const view = accountsOverview(filteredRows.value)
  return t('page.accounts.summary', {
    total: fmtInt(view.total),
    available: fmtInt(view.available),
    remain: fmtCredit(view.remain),
  })
})

function setStatus(value: AccountStatusTab): void {
  table.setFilter('status', normalizeAccountStatus(value))
}

function setFilter(name: 'q' | 'min' | 'max', value: string): void {
  table.setFilter(name, value)
}

function resetFilters(): void {
  table.reset()
}

const SORT_KEY_MAP: Record<string, string> = {
  name: 'name',
  balance: 'credits',
  lastUsed: 'last_used',
  created: 'id',
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
 * 选择 / 批量
 * ------------------------------------------------------------------ */

const tableRef = ref<InstanceType<typeof AccountsTable> | null>(null)
const bulkBusy = ref(false)

function clearSelection(): void {
  tableRef.value?.clearSelection()
}

const BULK_LABEL_KEY = {
  enable: 'page.accounts.bulkEnable',
  disable: 'page.accounts.bulkDisable',
  refresh: 'page.accounts.bulkRefresh',
  check: 'page.accounts.bulkCheck',
  delete: 'common.delete',
} as const

async function runBulk(action: BulkAction, ids: number[]): Promise<void> {
  if (!ids.length || bulkBusy.value) return
  if (action === 'delete') {
    const ok = await confirm({
      title: t('page.accounts.deleteManyTitle', { count: ids.length }),
      message: t('page.accounts.deleteManyBody'),
      okText: t('common.delete'),
      danger: true,
      requireText: t('page.accounts.deleteRequire'),
    })
    if (!ok) return
  }
  bulkBusy.value = true
  const label = t(BULK_LABEL_KEY[action])
  try {
    const result = await bulkAccounts(action, ids)
    if (result.errors?.length) {
      toast.error(
        t('page.accounts.bulkPartial', {
          label,
          affected: result.affected,
          failed: result.errors.length,
          first: result.errors[0],
        }),
      )
    } else {
      toast.success(t('page.accounts.bulkDone', { label, affected: result.affected }))
    }
    clearSelection()
    await load()
  } catch (error) {
    toast.error(failureText(t('page.accounts.bulkFailed', { action: label }), error))
  } finally {
    bulkBusy.value = false
  }
}

async function exportSelection(ids: number[]): Promise<void> {
  if (!ids.length) return
  await startExport({ ids })
}

/** 导出进行中：防止连点重复创建导出任务。 */
const exportBusy = ref(false)

/**
 * r4：与平台管理页「备份（含凭证）」同一流程——JSONL 导出必含可回灌凭证，所以**先确认再请求**，
 * 确认后带 EXPORT_CONFIRM_HEADER（api/platforms-admin.ts）；万一后端仍回 428，再补一次确认重发。
 */
async function confirmSecretExport(body: Parameters<typeof exportAccountsAsync>[0]): Promise<boolean> {
  const ok = await confirm({
    title: t('page.accounts.exportSecrets.title'),
    message: t('page.accounts.exportSecrets.body', { count: body.ids?.length ?? 0 }),
    okText: t('page.accounts.exportSecrets.ok'),
    danger: true,
  })
  if (!ok) toast.info(t('page.accounts.exportSecrets.cancelled'))
  return ok
}

async function startExport(body: Parameters<typeof exportAccountsAsync>[0]): Promise<void> {
  if (exportBusy.value) return
  if (!(await confirmSecretExport(body))) return
  exportBusy.value = true
  try {
    let snapshot: Awaited<ReturnType<typeof exportAccountsAsync>>
    try {
      snapshot = await exportAccountsAsync(body, true)
    } catch (error) {
      if (!isAccountExportConfirmRequired(error)) throw error
      if (!(await confirmSecretExport(body))) return
      snapshot = await exportAccountsAsync(body, true)
    }
    startJob(snapshot, { exportJob: true })
  } catch (error) {
    toast.error(failureText(t('page.accounts.exportFailed'), error))
  } finally {
    exportBusy.value = false
  }
}

async function exportFiltered(): Promise<void> {
  const rows = filteredRows.value
  if (!rows.length) {
    toast.warning(t('page.accounts.exportEmpty'))
    return
  }
  await startExport({ ids: rows.map((row) => row.id), format: 'raw' })
}

/* ------------------------------------------------------------------ *
 * 后台任务（整池 / 检测 / 导出 / 导入）
 * ------------------------------------------------------------------ */

const jobVisible = ref(false)
const jobDetailOpen = ref(false)
const pendingExport = ref(false)

const job = useJob(undefined, {
  pollUrl: '/api/accounts/job/',
  intervalMs: 1500,
  onFinish: (snapshot) => {
    const data = snapshot as unknown as AccountJobSnapshot
    if (data.status === 'failed') toast.error(jobDoneText(data, t))
    else toast.success(jobDoneText(data, t))
    if (pendingExport.value) {
      pendingExport.value = false
      const token = exportTokenOf(data)
      if (token) {
        void download('/api/accounts/export-download', { token }).catch((error: unknown) => {
          toast.error(failureText(t('page.accounts.downloadFailed'), error))
        })
      }
    }
    void load()
  },
})

const jobSnapshot = computed<AccountJobSnapshot | null>(
  () => (job.snapshot.value as unknown as AccountJobSnapshot | null) ?? null,
)
const jobItem = computed<JobItem | null>(() => toJobItem(jobSnapshot.value, t))
const jobTitle = computed(() => jobSnapshot.value?.title || t('page.accounts.jobTitle'))
const jobPercent = computed(() => Number(job.snapshot.value?.percent) || 0)
const jobDone = computed(() => Number(job.snapshot.value?.done) || 0)
const jobTotal = computed(() => Number(job.snapshot.value?.total) || 0)
const jobRunning = computed(() => job.isRunning.value)

function startJob(snapshot: AccountJobSnapshot, options: { exportJob?: boolean } = {}): void {
  if (!snapshot || !snapshot.job_id) {
    toast.error(t('page.accounts.jobNoId'))
    return
  }
  pendingExport.value = Boolean(options.exportJob)
  jobVisible.value = true
  job.start(snapshot.job_id)
  if (snapshot.status === 'running') toast.info(t('page.accounts.jobStarted'))
}

function showJobDetail(): void {
  if (!jobSnapshot.value) return
  jobDetailOpen.value = true
}

function hideJobBar(): void {
  jobVisible.value = false
}

const actions = useAccountActions({
  rows: allRows,
  load,
  startJob: (snapshot) => startJob(snapshot),
  onDeleted: (id) => {
    if (detailId.value === id) detailOpen.value = false
    clearSelection()
  },
})
const { refreshAll, checkAll, runGrowth, runCatTravel } = actions

/** 平台能力：按当前平台上下文判断（原来取列表第一行的平台，空列表时会判错）。 */
const features = computed(() => accountFeatures(platformStore.storagePlatform))

/* ------------------------------------------------------------------ *
 * 行操作（表格行与详情抽屉共用）
 * ------------------------------------------------------------------ */

const detailOpen = ref(false)
const detailId = ref<number | null>(null)
const detailRow = computed<AccountRow | null>(
  () => allRows.value.find((row) => row.id === detailId.value) ?? null,
)

const editOpen = ref(false)
const editTarget = ref<AccountRow | null>(null)

function openDetail(row: AccountRow): void {
  detailId.value = row.id
  detailOpen.value = true
}

function openEdit(row: AccountRow): void {
  editTarget.value = row
  editOpen.value = true
}

async function onRowAction(row: AccountRow, key: string): Promise<void> {
  if (key === 'edit') openEdit(row)
  else if (key === 'detail') openDetail(row)
  else await actions.runRowAction(row, key)
}

function onDetailAction(key: DetailActionKey): void {
  const row = detailRow.value
  if (!row) return
  void onRowAction(row, key)
}


/* ------------------------------------------------------------------ *
 * 页头：添加账号 ▾（唯一主按钮）+ ⋯ 整池任务
 * ------------------------------------------------------------------ */

const platformSpec = computed(() => platformStore.currentSpec)
const platformName = computed(
  () => platformStore.currentSpec?.label || platformStore.currentId || t('page.accounts.currentPlatform'),
)

const addOptions = computed<DropdownOption[]>(() =>
  addMethodsFor(platformSpec.value, t).map((method) => ({
    key: method.key,
    label: method.label,
    disabled: Boolean(method.disabledReason),
  })),
)

const addOpen = ref(false)
const addKind = ref<AddDrawerKind>('manual')

function openAddDrawer(kind: AddDrawerKind): void {
  addKind.value = kind
  addOpen.value = true
}

function onAddMode(key: string): void {
  const mode = key as AddMethodKey
  if (!methodEnabled(platformSpec.value, mode)) {
    toast.warning(t('page.accounts.addUnsupported', { key }))
    return
  }
  openAddDrawer(mode as AddDrawerKind)
}

const limitsOpen = ref(false)
const dedupeOpen = ref(false)

const moreOptions = computed<DropdownOption[]>(() => {
  const list: DropdownOption[] = [
    { key: 'refresh-all', label: t('page.accounts.toolbarRefreshAll') },
    { key: 'check-all', label: t('page.accounts.toolbarCheckAll') },
    { key: 'limits', label: t('page.accounts.toolbarLimits') },
    { key: 'dedupe', label: t('page.accounts.dedupe.menu') },
  ]
  if (features.value.growth || features.value.catTravel) {
    list.push({ type: 'divider', key: 'divider-wb' })
    if (features.value.growth) list.push({ key: 'growth', label: t('page.accounts.toolbarGrowth') })
    if (features.value.catTravel) list.push({ key: 'cat', label: t('page.accounts.toolbarCat') })
  }
  if (jobSnapshot.value) {
    list.push({ type: 'divider', key: 'divider-job' })
    list.push({ key: 'job', label: t('page.accounts.viewJob') })
  }
  return list
})

function onMore(key: string | number): void {
  const name = String(key)
  if (name === 'refresh-all') void refreshAll()
  else if (name === 'check-all') void checkAll()
  else if (name === 'limits') limitsOpen.value = true
  else if (name === 'dedupe') dedupeOpen.value = true
  else if (name === 'growth') void runGrowth()
  else if (name === 'cat') void runCatTravel()
  else if (name === 'job') {
    jobVisible.value = true
    showJobDetail()
  }
}

function onDeduped(removed: number): void {
  if (removed > 0) {
    clearSelection()
    void load()
  }
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null
let lastSelector = platformStore.selector
let offAddCommand: (() => void) | null = null
let offImportCommand: (() => void) | null = null

onMounted(() => {
  lastSelector = platformStore.selector
  void load()
  offAddCommand = registerCommand({
    id: 'accounts.add',
    title: t('page.accounts.commandAdd'),
    hint: t('page.accounts.commandHint'),
    keywords: t('page.accounts.commandAddKeywords'),
    run: () => openAddDrawer('manual'),
  })
  offImportCommand = registerCommand({
    id: 'accounts.import',
    title: t('page.accounts.commandImport'),
    hint: t('page.accounts.commandHint'),
    keywords: t('page.accounts.commandImportKeywords'),
    run: () => openAddDrawer('import'),
  })
  offPlatform = platformStore.onChange((detail) => {
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    clearSelection()
    detailOpen.value = false
    void load()
  })
})

onBeforeUnmount(() => {
  offAddCommand?.()
  offAddCommand = null
  offImportCommand?.()
  offImportCommand = null
  offPlatform?.()
  offPlatform = null
  controller?.abort()
  controller = null
  job.stop()
})

/**
 * 回到 KeepAlive 缓存页：静默刷新 —— 不出骨架，也不给表格盖加载遮罩（11 §2「二次进入不闪」），
 * 数据回来直接替换；手动刷新 / 切平台仍走普通 load()，表格照常显示加载态。
 */
const silentRefresh = ref(false)
useRevisit(async () => {
  silentRefresh.value = true
  try {
    await load()
  } finally {
    silentRefresh.value = false
  }
})

// 页签切换回到第 1 页（其余筛选由 useTableQuery 统一处理）。
watch(statusTab, () => {
  if (table.state.page !== 1) table.setPage(1)
})
</script>

<template>
  <div class="acc-page">
    <PageHeader :help="helpText">
      <template #meta>
        <span v-if="headerMeta" class="acc-meta tnum">{{ headerMeta }}</span>
      </template>
      <template #actions>
        <n-dropdown trigger="click" placement="bottom-end" :options="moreOptions" @select="onMore">
          <Btn variant="quiet" :icon="MoreHorizontal" :aria-label="t('page.accounts.moreAria')">
            {{ t('page.accounts.more') }}
          </Btn>
        </n-dropdown>
        <Btn variant="quiet" :icon="Download" @click="exportFiltered">
          {{ t('page.accounts.export') }}
        </Btn>
        <n-dropdown
          :options="addOptions"
          trigger="click"
          placement="bottom-end"
          @select="(key: string | number) => onAddMode(String(key))"
        >
          <Btn variant="primary" :icon="Plus">
            {{ t('page.accounts.add') }}
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

    <section v-else class="acc-card">
      <!-- 后台任务内联条：只在有任务时出现，完成后可隐藏 -->
      <AccountsJobBar
        v-if="jobSnapshot && jobVisible"
        :title="jobTitle"
        :percent="jobPercent"
        :done="jobDone"
        :total="jobTotal"
        :running="jobRunning"
        :failed="jobItem?.status === 'failed'"
        @view="showJobDetail"
        @hide="hideJobBar"
      />

      <!-- 骨架按表格节奏排：工具栏 34px + 表头 + 10 行 × --row-h（44px），数据到来不跳动（11 §4-1）。 -->
      <div v-if="firstLoading" class="acc-skeleton" aria-busy="true" :aria-label="t('page.accounts.loadingAria')">
        <div class="acc-skeleton__bar mh-skeleton" />
        <div class="acc-skeleton__head" />
        <div v-for="index in 10" :key="index" class="acc-skeleton__line">
          <span class="acc-skeleton__row mh-skeleton" />
        </div>
      </div>

      <AccountsTable
        v-else
        ref="tableRef"
        :rows="pageData.items"
        :total="pageData.total"
        :page="table.state.page"
        :page-size="table.state.pageSize"
        :loading="loading && !silentRefresh"
        :refreshing="loading && silentRefresh"
        :sort-by="table.state.sortBy"
        :sort-order="table.state.sortOrder"
        :show-token="showTokenColumn"
        :has-filters="hasFilters"
        :bulk-busy="bulkBusy"
        @update:page="table.setPage"
        @update:page-size="table.setPageSize"
        @sorter="onSorter"
        @action="onRowAction"
        @bulk="runBulk"
        @export-selected="exportSelection"
        @reset-filters="resetFilters"
        @add="openAddDrawer('import')"
        @reload="load()"
      >
        <template #toolbar>
          <AccountsToolbar
            :status="statusTab"
            :counts="counts"
            :q="table.state.filters.q"
            :min="table.state.filters.min"
            :max="table.state.filters.max"
            :summary="summary"
            :has-filters="hasFilters"
            @update:status="setStatus"
            @update:q="(value: string) => setFilter('q', value)"
            @update:min="(value: string) => setFilter('min', value)"
            @update:max="(value: string) => setFilter('max', value)"
            @reset="resetFilters"
          />
        </template>
      </AccountsTable>
    </section>

    <AccountDetailDrawer v-model:show="detailOpen" :row="detailRow" @action="onDetailAction" />
    <AccountEditDrawer v-model:show="editOpen" :row="editTarget" @saved="load()" />
    <AddAccountDrawer
      v-model:show="addOpen"
      :kind="addKind"
      :spec="platformSpec"
      :platform-name="platformName"
      @added="load()"
      @job="(snapshot: AccountJobSnapshot) => startJob(snapshot)"
    />
    <DedupeDialog v-model:show="dedupeOpen" @done="onDeduped" />
    <ModelLimitsDrawer v-model:show="limitsOpen" />
    <JobDetailDrawer v-model:show="jobDetailOpen" :snapshot="jobSnapshot" />

    <JobProgress :jobs="jobVisible && jobItem ? [jobItem] : []" @hide="hideJobBar" @view="showJobDetail" />
  </div>
</template>

<style scoped>
.acc-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
  align-items: start;
}

.acc-meta {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.acc-card {
  min-width: 0;
  display: grid;
  gap: 0;
  padding: var(--sp-3) var(--sp-4) var(--sp-4);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-1);
}

.acc-skeleton {
  display: grid;
  padding: var(--sp-2) 0 0;
}

.acc-skeleton__bar {
  height: 34px;
  width: 70%;
  margin-bottom: var(--sp-3);
  border-radius: var(--r-ctl);
}

.acc-skeleton__head {
  height: 40px;
  border-radius: var(--r-ctl) var(--r-ctl) 0 0;
  background: var(--surface-2);
}

.acc-skeleton__line {
  display: flex;
  align-items: center;
  height: var(--row-h, 44px);
  padding: 0 var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
}

.acc-skeleton__line:nth-child(odd) .acc-skeleton__row {
  width: 72%;
}

.acc-skeleton__row {
  display: block;
  width: 86%;
  height: 14px;
  border-radius: var(--r-chip);
}

@media (max-width: 767px) {
  .acc-card {
    padding: var(--sp-3);
  }
}
</style>
