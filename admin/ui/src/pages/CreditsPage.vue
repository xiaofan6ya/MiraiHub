<script setup lang="ts">
/**
 * S23 积分到期 /credits（模板 A）
 *
 * 契约：docs/ui-v2/contracts/credits.md（本步新建，末尾含「实现对照」）
 * 旧版：#panel-credits（admin/console/index.html 2449–2559）+ console-app.js 10532–11010
 * 后端：admin/routers/credits.py（/expiry /selection-policy /selection-order /usage）
 *       admin/routers/accounts.py:sync_credits（POST /api/accounts/credits/sync）
 *
 * 「账号调度策略」与 /gateway 的平台策略**不是同一后端设置**：
 *   平台级 = gateway:routing；账号级 = account_selection_mode / upstream_start_interval_ms。
 * 所以这里用自己的 selection-policy 接口，不与 gateway 共用 store。
 */
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, type VNodeChild } from 'vue'
import { NInputNumber, NSelect, NSkeleton } from 'naive-ui'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  BarList,
  Btn,
  Card,
  Chip,
  DataTable,
  DetailDrawer,
  Empty,
  ErrorBlock,
  HelpTip,
  JobProgress,
  KV,
  Meter,
  StackMeter,
  StatStrip,
  type JobItem,
  type KvItem,
  type SorterPayload,
  type StackMeterItem,
  type StatItem,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import { FMT_EMPTY, fmtCredit, fmtInt, fmtTime } from '@/utils/format'
import {
  accountStatusText,
  creditStatItems,
  getCreditExpiry,
  getCreditUsage,
  paginateRows,
  restoreAutomaticSelection,
  riskView,
  saveSelectionPolicy,
  soonestLabel,
  sortAccountRows,
  syncCredits,
  syncSummaryText,
  toPackageRows,
  usageBars,
  type AccountSortKey,
  type CreditAccountRow,
  type CreditExpiryPayload,
  type CreditUsagePayload,
  type PackageRowView,
  type SelectionPolicy,
} from '@/api/credits'

const { t } = useI18n()

const helpText = computed(() => t('page.credits.help'))
const policyNote = computed(() => t('page.credits.policyNote'))
const intervalNote = computed(() => t('page.credits.intervalNote'))

const toast = useToast()
const confirm = useConfirm()
const platform = usePlatformStore()

interface ErrorState {
  message: string
  detail: string
}

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function toErrorState(error: unknown): ErrorState {
  return {
    message: errorMessage(error),
    detail: error instanceof ApiError && error.detail ? prettyDetail(error.detail) : '',
  }
}

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const expiry = ref<CreditExpiryPayload | null>(null)
const packages = ref<PackageRowView[]>([])
const packagesHasMore = ref(false)
const usage = ref<CreditUsagePayload | null>(null)
const policy = ref<SelectionPolicy | null>(null)
const expiryError = ref<ErrorState | null>(null)
const usageError = ref<ErrorState | null>(null)
const loading = reactive({ expiry: false, usage: false, saving: false, more: false, syncing: false })
const form = reactive({ mode: '', interval: 75 })
const job = ref<JobItem | null>(null)

const dirty = computed(() =>
  policy.value ? policy.value.mode !== form.mode || Number(policy.value.upstream_start_interval_ms) !== Number(form.interval) : false,
)
const firstLoading = computed(() => expiry.value === null && loading.expiry)
const modeOptions = computed(() =>
  (policy.value?.modes || []).map((item) => ({ label: item.label, value: item.value })),
)
const modeDescription = computed(
  () => policy.value?.modes.find((item) => item.value === form.mode)?.description || '',
)
const isManual = computed(() => expiry.value?.selection_mode === 'manual')

function applyPolicyForm(): void {
  if (!policy.value || dirty.value) return
  form.mode = policy.value.mode
  form.interval = Number(policy.value.upstream_start_interval_ms)
}

async function loadExpiry(): Promise<void> {
  loading.expiry = true
  expiryError.value = null
  try {
    const payload = await getCreditExpiry({ packageLimit: 60 })
    expiry.value = payload
    packages.value = toPackageRows(payload.packages)
    packagesHasMore.value = Boolean(payload.has_more)
    policy.value = payload.routing_policy
    applyPolicyForm()
  } catch (error) {
    expiryError.value = toErrorState(error)
    if (expiry.value !== null) toast.error(failureText(t('page.credits.action.refreshExpiry'), error))
  } finally {
    loading.expiry = false
  }
}

async function loadUsage(): Promise<void> {
  loading.usage = true
  usageError.value = null
  try {
    usage.value = await getCreditUsage({ days: 7, top: 12 })
  } catch (error) {
    usageError.value = toErrorState(error)
    if (usage.value !== null) toast.error(failureText(t('page.credits.action.refreshUsage'), error))
  } finally {
    loading.usage = false
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadExpiry(), loadUsage()])
}

async function loadMorePackages(): Promise<void> {
  if (loading.more || !packagesHasMore.value) return
  loading.more = true
  try {
    const payload = await getCreditExpiry({
      packageLimit: 60,
      packageOffset: packages.value.length,
    })
    packages.value = [...packages.value, ...toPackageRows(payload.packages)]
    packagesHasMore.value = Boolean(payload.has_more)
    if (expiry.value) {
      expiry.value = { ...expiry.value, has_more: payload.has_more, packages_total: payload.packages_total }
    }
  } catch (error) {
    toast.error(failureText(t('page.credits.action.loadMore'), error))
  } finally {
    loading.more = false
  }
}

/* ------------------------------------------------------------------ *
 * 账号调度策略
 * ------------------------------------------------------------------ */

async function savePolicy(): Promise<void> {
  if (!policy.value || !dirty.value || loading.saving) return
  const interval = Number(form.interval)
  if (!Number.isInteger(interval) || interval < 0 || interval > 60000) {
    toast.error(t('page.credits.policy.invalidInterval'))
    return
  }
  loading.saving = true
  try {
    policy.value = await saveSelectionPolicy({ mode: form.mode, upstream_start_interval_ms: interval })
    applyPolicyForm()
    toast.success(t('page.credits.policy.saved'))
    await loadExpiry()
  } catch (error) {
    toast.error(failureText(t('page.credits.action.savePolicy'), error))
  } finally {
    loading.saving = false
  }
}

async function restoreAuto(): Promise<void> {
  if (!expiry.value || !isManual.value || loading.saving) return
  const label =
    policy.value?.modes.find((item) => item.value === policy.value?.mode)?.label || t('page.credits.mode.auto')
  const ok = await confirm({
    title: t('page.credits.confirmRestore.title'),
    message: t('page.credits.confirmRestore.body', { label }),
    okText: t('page.credits.confirmRestore.ok'),
  })
  if (!ok) return
  loading.saving = true
  try {
    await restoreAutomaticSelection(expiry.value.selection_order)
    toast.success(t('page.credits.toast.restored'))
    await loadExpiry()
  } catch (error) {
    toast.error(failureText(t('page.credits.action.restoreAuto'), error))
  } finally {
    loading.saving = false
  }
}

/* ------------------------------------------------------------------ *
 * 重新采集（后台任务样式：同步接口 + JobProgress 卡片）
 * ------------------------------------------------------------------ */

async function syncAll(): Promise<void> {
  if (loading.syncing) return
  loading.syncing = true
  job.value = {
    id: 'credits-sync',
    name: t('page.credits.sync.name'),
    status: 'running',
    percent: 0,
    message: t('page.credits.sync.running'),
  }
  try {
    const result = await syncCredits()
    job.value = {
      id: 'credits-sync',
      name: t('page.credits.sync.name'),
      status: result.failed > 0 ? 'failed' : 'done',
      percent: 100,
      succeeded: result.refreshed,
      failed: result.failed,
      message: syncSummaryText(result),
    }
    toast.success(syncSummaryText(result))
    await loadAll()
  } catch (error) {
    if (job.value) job.value = { ...job.value, status: 'failed', message: errorMessage(error) }
    toast.error(failureText(t('page.credits.action.sync'), error))
  } finally {
    loading.syncing = false
  }
}

function onJobView(): void {
  if (!job.value) return
  if (job.value.status === 'failed') toast.warning(job.value.message || t('page.credits.toast.jobFailed'))
  else toast.info(job.value.message || t('page.credits.toast.jobDone'))
}

/* ------------------------------------------------------------------ *
 * 顶部统计 / 风险分布 / 消耗构成
 * ------------------------------------------------------------------ */

const stats = computed<StatItem[]>(() => creditStatItems(expiry.value) as StatItem[])
const risk = computed(() => riskView(expiry.value))
const riskItems = computed<StackMeterItem[]>(() => risk.value.items)
const usageView = computed(() => usageBars(usage.value, 12))

/* ------------------------------------------------------------------ *
 * 积分包表
 * ------------------------------------------------------------------ */

const packageColumns = computed<TableColumn[]>(() => [
  {
    key: 'remaining',
    title: t('page.credits.col.credits'),
    mobile: 'title',
    width: 96,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'cr-strong tnum' }, (row as unknown as PackageRowView).remainingText),
  },
  { key: 'display_name', title: t('page.credits.col.source'), mobile: 'subtitle', minWidth: 160 },
  {
    key: 'account_name',
    title: t('page.credits.col.account'),
    mobile: 'meta',
    minWidth: 140,
    render: (row: TableRow) => h('span', { class: 'cr-muted' }, (row as unknown as PackageRowView).account_name || FMT_EMPTY),
  },
  {
    key: 'expire',
    title: t('page.credits.col.expireDate'),
    mobile: 'meta',
    width: 150,
    render: (row: TableRow) => {
      const item = row as unknown as PackageRowView
      return h('span', { class: 'cr-expire' }, [
        h('span', { class: 'tnum' }, item.expireText),
        item.expiring_soon ? h(Badge, { tone: 'warn' }, { default: () => t('page.credits.col.expiringSoonBadge') }) : null,
        item.expired ? h(Badge, { tone: 'danger' }, { default: () => t('page.credits.col.expiredBadge') }) : null,
      ])
    },
  },
  {
    key: 'usage',
    title: t('page.credits.col.remainingRatio'),
    mobile: 'meta',
    width: 160,
    render: (row: TableRow) => {
      const item = row as unknown as PackageRowView
      return h(Meter, {
        value: item.remainingRatio,
        max: 100,
        tone: item.expiring_soon ? 'warn' : 'ok',
        showValue: true,
        valueText: Math.round(item.remainingRatio) + '%',
        ariaLabel: t('page.credits.meter.packageRatioAria'),
      })
    },
  },
])

const packageRows = computed<TableRow[]>(() => packages.value as unknown as TableRow[])

/* ------------------------------------------------------------------ *
 * 账号到期排行（客户端分页 + 排序，状态同步路由 query）
 * ------------------------------------------------------------------ */

const table = useTableQuery('credits', {
  pageSize: 20,
  filters: {},
})

const sortedAccounts = computed(() =>
  sortAccountRows(expiry.value?.accounts, table.state.sortBy as AccountSortKey, table.state.sortOrder),
)
const accountTotal = computed(() => sortedAccounts.value.length)
const pagedAccounts = computed(() =>
  paginateRows(sortedAccounts.value, table.state.page, table.state.pageSize),
)
const accountRows = computed<TableRow[]>(() => {
  const offset = (table.state.page - 1) * table.state.pageSize
  return pagedAccounts.value.map((row, index) => ({ ...row, rank: offset + index + 1 })) as unknown as TableRow[]
})

const SORT_KEYS: Record<string, AccountSortKey> = {
  account_name: 'account_name',
  total_remaining: 'total_remaining',
  expiring_soon_remaining: 'expiring_soon_remaining',
  soonest_expire_at: 'soonest_expire_at',
}

function sortOrderOf(key: string): 'ascend' | 'descend' | false {
  if (table.state.sortBy !== SORT_KEYS[key] || !table.state.sortOrder) return false
  return table.state.sortOrder === 'asc' ? 'ascend' : 'descend'
}

function onSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order) {
    table.setSort('', '')
    return
  }
  const key = SORT_KEYS[sorter.columnKey]
  if (!key) {
    table.setSort('', '')
    return
  }
  table.setSort(key, sorter.order === 'ascend' ? 'asc' : 'desc')
}

function soonestTone(row: CreditAccountRow): 'ok' | 'warn' | 'danger' | 'neutral' {
  const days = row.soonest_days_left
  if (days === null || days === undefined) return 'neutral'
  if (days <= 0) return 'danger'
  if (days <= 7) return 'warn'
  return 'ok'
}

function accountPackagesCell(row: TableRow): VNodeChild {
  const item = row as unknown as CreditAccountRow & { rank: number }
  const list = (item.next_expiring || []).slice(0, 2)
  if (!list.length) return h('span', { class: 'cr-muted' }, t('page.credits.noPackages'))
  return h(
    'div',
    { class: 'cr-chips' },
    list.map((pkg) =>
      h(Chip, { key: pkg.package_code || pkg.display_name, title: pkg.expire_label || pkg.expire_date }, {
        default: () => pkg.display_name + ' ' + fmtCredit(pkg.remaining),
      }),
    ),
  )
}

const accountColumns = computed<TableColumn[]>(() => [
  {
    key: 'rank',
    title: '#',
    mobile: 'title',
    width: 48,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'cr-rank tnum' }, (row as unknown as { rank: number }).rank),
  },
  {
    key: 'account_name',
    title: t('page.credits.col.account'),
    mobile: 'title',
    minWidth: 170,
    sortable: true,
    sortOrder: sortOrderOf('account_name'),
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      return h('div', { class: 'cr-account' }, [
        h('span', { class: 'cr-strong' }, item.account_name),
        h('span', { class: 'cr-sub mono' }, item.uid || FMT_EMPTY),
      ])
    },
  },
  {
    key: 'status',
    title: t('page.credits.col.status'),
    mobile: 'meta',
    width: 92,
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      return h(Badge, { tone: item.status === 'active' ? 'ok' : 'neutral', dot: true }, {
        default: () => accountStatusText(item.status),
      })
    },
  },
  {
    key: 'total_remaining',
    title: t('page.credits.col.totalRemaining'),
    mobile: 'meta',
    width: 108,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('total_remaining'),
    render: (row: TableRow) => h('span', { class: 'cr-strong tnum' }, fmtCredit((row as unknown as CreditAccountRow).total_remaining)),
  },
  {
    key: 'expiring_soon_remaining',
    title: t('page.credits.col.expiringSoon'),
    mobile: 'meta',
    width: 116,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('expiring_soon_remaining'),
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      return item.expiring_soon_remaining > 0
        ? h('span', { class: 'cr-warn tnum' }, fmtCredit(item.expiring_soon_remaining))
        : h('span', { class: 'cr-muted tnum' }, FMT_EMPTY)
    },
  },
  {
    key: 'evergreen_remaining',
    title: t('page.credits.col.evergreen'),
    mobile: 'meta',
    width: 104,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'cr-muted tnum' }, fmtCredit((row as unknown as CreditAccountRow).evergreen_remaining)),
  },
  {
    key: 'soonest',
    title: t('page.credits.col.soonest'),
    mobile: 'meta',
    width: 180,
    sortable: true,
    sortOrder: sortOrderOf('soonest_expire_at'),
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      return h('div', { class: 'cr-stack' }, [
        h(Badge, { tone: soonestTone(item) }, { default: () => soonestLabel(item.soonest_days_left) }),
        item.synced_at
          ? h('span', { class: 'cr-sub tnum' }, t('page.credits.col.syncedAt', { time: fmtTime(item.synced_at) }))
          : null,
      ])
    },
  },
  {
    key: 'next_expiring',
    title: t('page.credits.col.nextPackages'),
    mobile: 'meta',
    minWidth: 240,
    render: accountPackagesCell,
  },
  {
    key: 'actions',
    title: t('page.credits.col.actions'),
    mobile: 'actions',
    width: 84,
    hideable: false,
    fixed: 'right',
    render: (row: TableRow) =>
      h('button', {
        type: 'button',
        class: 'cr-link',
        'aria-label': t('page.credits.col.detailAria'),
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          openDetail(row as unknown as CreditAccountRow)
        },
      }, t('page.credits.col.detail')),
  },
])

/* ------------------------------------------------------------------ *
 * 账号明细抽屉（只含后端给的最近 4 个包）
 * ------------------------------------------------------------------ */

const detailOpen = ref(false)
const selected = ref<CreditAccountRow | null>(null)

const detailKv = computed<KvItem[]>(() => {
  const item = selected.value
  if (!item) return []
  return [
    { label: t('page.credits.detail.account'), value: item.account_name },
    { label: 'UID', value: item.uid || FMT_EMPTY, mono: true },
    { label: t('page.credits.col.status'), value: accountStatusText(item.status) },
    { label: t('page.credits.col.totalRemaining'), value: fmtCredit(item.total_remaining) },
    { label: t('page.credits.detail.balance'), value: fmtCredit(item.balance_remain) },
    { label: t('page.credits.col.expiringSoon'), value: fmtCredit(item.expiring_soon_remaining) },
    { label: t('page.credits.col.evergreen'), value: fmtCredit(item.evergreen_remaining) },
    { label: t('page.credits.detail.expired'), value: fmtCredit(item.expired_remaining) },
    { label: t('page.credits.col.soonest'), value: soonestLabel(item.soonest_days_left) },
    {
      label: t('page.credits.detail.packagesLabel'),
      value: t('page.credits.detail.packages', {
        active: item.active_package_count,
        total: item.package_count,
      }),
    },
    { label: t('page.credits.detail.syncedAt'), value: item.synced_at ? fmtTime(item.synced_at) : FMT_EMPTY },
  ]
})

const detailPackages = computed<TableRow[]>(() =>
  toPackageRows(selected.value?.next_expiring || []) as unknown as TableRow[],
)

const detailPackageColumns = computed<TableColumn[]>(() => [
  { key: 'display_name', title: t('page.credits.col.source'), mobile: 'title', minWidth: 140 },
  { key: 'remaining', title: t('page.credits.detail.remainingOfTotal'), mobile: 'meta', width: 140, numeric: true },
  { key: 'expire', title: t('page.credits.col.expireDate'), mobile: 'meta', width: 150 },
  {
    key: 'usage',
    title: t('page.credits.col.remainingRatio'),
    mobile: 'meta',
    width: 140,
    render: (row: TableRow) => {
      const item = row as unknown as PackageRowView
      return h(Meter, { value: item.remainingRatio, max: 100, tone: 'ok', showValue: true, valueText: Math.round(item.remainingRatio) + '%' })
    },
  },
])

function openDetail(row: CreditAccountRow): void {
  selected.value = row
  detailOpen.value = true
}

const policyModeLabel = computed(() =>
  isManual.value
    ? t('page.credits.ranking.modeManual')
    : t('page.credits.ranking.modeAuto', {
        label: policy.value?.modes.find((m) => m.value === policy.value?.mode)?.label || '—',
      }),
)

/* ------------------------------------------------------------------ *
 * 生命周期：平台切换重取、卸载取消
 * ------------------------------------------------------------------ */

let stopPlatformWatch: (() => void) | null = null

onMounted(() => {
  void loadAll()
  stopPlatformWatch = platform.onChange(() => {
    void loadAll()
  })
})

onBeforeUnmount(() => {
  stopPlatformWatch?.()
  stopPlatformWatch = null
})
</script>

<template>
  <div class="credits-page">
    <PageHeader :title="t('page.credits.title')" :help="helpText">
      <template #actions>
        <Btn variant="primary" :loading="loading.syncing" @click="syncAll">
          {{ t('page.credits.action.sync') }}
        </Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading.expiry" @click="loadAll">
          {{ t('common.refresh') }}
        </Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="expiryError && !expiry"
      :title="t('page.credits.error.expiry')"
      :detail="expiryError.detail"
      @retry="loadAll"
    />
    <div v-else-if="firstLoading" class="cr-skeleton" aria-busy="true">
      <NSkeleton v-for="index in 6" :key="index" height="48px" />
    </div>

    <template v-else>
      <StatStrip :items="stats" />

      <!-- 账号调度策略 -->
      <Card class="cr-policy" :loading="loading.saving">
        <template #title>{{ t('page.credits.policy.title') }}</template>
        <template #extra>
          <Badge v-if="isManual" tone="warn" dot>{{ t('page.credits.policy.manual') }}</Badge>
          <Badge v-else tone="brand" dot>{{ t('page.credits.policy.auto') }}</Badge>
          <HelpTip :content="policyNote" :label="t('page.credits.policy.helpLabel')" />
        </template>

        <div class="cr-policy__row">
          <label class="cr-field">
            <span class="cr-field__label">{{ t('page.credits.policy.mode') }}</span>
            <NSelect
              class="cr-mode"
              :value="form.mode"
              :options="modeOptions"
              :disabled="loading.saving"
              :aria-label="t('page.credits.policy.mode')"
              @update:value="(value: string) => (form.mode = value)"
            />
          </label>
          <label class="cr-field">
            <span class="cr-field__label">{{ t('page.credits.policy.interval') }}</span>
            <NInputNumber
              class="cr-interval"
              :value="form.interval"
              :min="0"
              :max="60000"
              :step="1"
              :show-button="false"
              :disabled="loading.saving"
              :aria-label="t('page.credits.policy.interval')"
              @update:value="(value: number | null) => (form.interval = typeof value === 'number' ? value : 0)"
            />
          </label>
          <Btn variant="primary" :disabled="!dirty || loading.saving" :loading="loading.saving" @click="savePolicy">
            {{ t('page.credits.policy.save') }}
          </Btn>
          <Btn variant="quiet" :disabled="!isManual || loading.saving" @click="restoreAuto">
            {{ t('page.credits.policy.restore') }}
          </Btn>
        </div>
        <p class="cr-hint">{{ modeDescription }}</p>
        <p class="cr-note">{{ intervalNote }}</p>
        <p class="cr-note">{{ policyNote }}</p>
      </Card>

      <!-- 到期风险分布 -->
      <Card class="cr-risk" :loading="loading.expiry">
        <template #title>{{ t('page.credits.risk.title') }}</template>
        <template #extra><span class="cr-sub">{{ risk.hint }}</span></template>

        <StackMeter
          v-if="!risk.empty"
          :items="riskItems"
          :total="risk.total"
          :unit="' ' + t('page.credits.unit.credits')"
        />
        <Empty v-else :description="t('page.credits.risk.empty')" />
        <p class="cr-note">{{ t('page.credits.risk.note') }}</p>
      </Card>

      <div class="cr-grid">
        <!-- 最近快到期的积分包 -->
        <Card class="cr-packages" :loading="loading.expiry || loading.more">
          <template #title>{{ t('page.credits.packages.title') }}</template>
          <template #extra>
            <span class="cr-sub">
              {{ t('page.credits.packages.counter', { shown: packages.length, total: expiry?.packages_total || 0 }) }}
            </span>
          </template>

          <DataTable
            table-id="credits-packages"
            :columns="packageColumns"
            :data="packageRows"
            row-key="rowKey"
            :pagination="false"
            :virtual-scroll="true"
            :empty-description="t('page.credits.packages.empty')"
          >
            <template #empty>
              <Empty :description="t('page.credits.packages.empty')" />
            </template>
          </DataTable>
          <div v-if="packagesHasMore" class="cr-more">
            <Btn variant="quiet" size="sm" :loading="loading.more" @click="loadMorePackages">
              {{ t('page.credits.action.loadMore') }}
            </Btn>
          </div>
        </Card>

        <!-- 账号消耗构成 -->
        <Card class="cr-usage" :loading="loading.usage">
          <template #title>{{ t('page.credits.usage.title') }}</template>
          <template #extra><span class="cr-sub">{{ usageView.hint }}</span></template>

          <ErrorBlock
            v-if="usageError"
            compact
            :title="t('page.credits.error.usage', { error: usageError.message })"
            :detail="usageError.detail"
            @retry="loadUsage"
          />
          <BarList
            v-else-if="!usageView.empty"
            :items="usageView.items"
            :unit="' ' + t('page.credits.unit.credits')"
            :total="usageView.total"
            :format="fmtCredit"
            :limit="12"
          />
          <Empty v-else :description="t('page.credits.usage.empty')" />
        </Card>
      </div>

      <!-- 账号到期排行 -->
      <Card class="cr-ranking" :loading="loading.expiry">
        <template #title>{{ t('page.credits.ranking.title') }}</template>
        <template #extra>
          <span class="cr-sub">
            {{ t('page.credits.ranking.meta', { total: accountTotal, mode: policyModeLabel }) }}
          </span>
        </template>

        <ErrorBlock
          v-if="expiryError"
          compact
          :title="t('page.credits.error.ranking', { error: expiryError.message })"
          :detail="expiryError.detail"
          @retry="loadExpiry"
        />
        <Empty
          v-else-if="!accountTotal"
          :description="t('page.credits.ranking.empty')"
        />
        <DataTable
          v-else
          table-id="credits-accounts"
          :columns="accountColumns"
          :data="accountRows"
          row-key="account_id"
          remote
          :page="table.state.page"
          :page-size="table.state.pageSize"
          :item-count="accountTotal"
          :page-sizes="[20, 50, 100]"
          clickable-rows
          :empty-description="t('page.credits.ranking.empty')"
          @update:page="table.setPage"
          @update:page-size="table.setPageSize"
          @update:sorter="onSorter"
          @row-click="(row: TableRow) => openDetail(row as unknown as CreditAccountRow)"
        />
        <p class="cr-note">{{ t('page.credits.ranking.note') }}</p>
      </Card>
    </template>

    <JobProgress
      :jobs="job ? [job] : []"
      :title="t('page.credits.job.title')"
      @hide="job = null"
      @view="onJobView"
    />

    <DetailDrawer
      v-model:show="detailOpen"
      :title="
        selected ? t('page.credits.drawer.titleOf', { name: selected.account_name }) : t('page.credits.drawer.title')
      "
      :badge="selected ? { tone: selected.expiring_soon ? 'warn' : 'ok', label: soonestLabel(selected.soonest_days_left) } : null"
      :subtitle="selected?.uid || ''"
    >
      <template v-if="selected">
        <KV :items="detailKv" label-width="96px" />
        <p class="cr-sub">
          {{ t('page.credits.drawer.recentPackages', { count: detailPackages.length }) }}
        </p>
        <DataTable
          table-id="credits-detail"
          :columns="detailPackageColumns"
          :data="detailPackages"
          row-key="rowKey"
          :pagination="false"
          :virtual-scroll="false"
          :empty-description="t('page.credits.noPackages')"
        />
      </template>
      <template #footer>
        <Btn variant="default" @click="detailOpen = false">{{ t('common.close') }}</Btn>
      </template>
    </DetailDrawer>
  </div>
</template>

<style scoped>
.credits-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.cr-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.cr-sub {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.cr-hint {
  margin-top: var(--sp-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  max-width: 68ch;
}

.cr-note {
  margin-top: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 78ch;
}

.cr-policy__row {
  display: flex;
  align-items: flex-end;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

.cr-field {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.cr-field__label {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.cr-mode {
  width: 220px;
}

.cr-interval {
  width: 160px;
}

.cr-grid {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: var(--sp-4);
  align-items: start;
}

.cr-more {
  display: flex;
  justify-content: center;
  padding-top: var(--sp-2);
}

.cr-strong {
  font-weight: 500;
  color: var(--fg);
}

.cr-muted {
  color: var(--fg-muted);
}

.cr-warn {
  color: var(--warn);
  font-weight: 500;
}

.cr-sub {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.cr-account {
  display: grid;
  gap: 0;
  min-width: 0;
}

.cr-stack {
  display: grid;
  gap: 2px;
}

.cr-expire {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  flex-wrap: wrap;
}

.cr-chips {
  display: flex;
  gap: var(--sp-1);
  flex-wrap: wrap;
}

.cr-rank {
  color: var(--fg-faint);
}

.cr-link {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--brand);
  font-size: var(--fs-small);
  cursor: pointer;
}

.cr-link:hover {
  text-decoration: underline;
}

@media (max-width: 1024px) {
  .cr-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .cr-mode,
  .cr-interval {
    width: 100%;
  }

  .cr-policy__row {
    align-items: stretch;
  }
}
</style>