<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * S23 积分到期 /credits（v2.1 视觉精修：KPI 行 + 风险/消耗双卡 + 到期排行 + 积分包 + 调度策略设置组）
 *
 * 契约：docs/ui-v2/contracts/credits.md（末尾「实现对照」）
 * 规范：docs/ui-v2/10-visual-refresh.md（最高优先级）
 * 后端：admin/routers/credits.py（/expiry /selection-policy /selection-order /usage）
 *       admin/routers/accounts.py:sync_credits（POST /api/accounts/credits/sync）
 *
 * 「账号调度策略」与 /gateway 的平台策略**不是同一后端设置**：
 *   平台级 = gateway:routing；账号级 = account_selection_mode / upstream_start_interval_ms。
 *
 * 本轮修复：
 *   · 选号模式下拉为空（第 1 轮审查 P0-10）：首次加载时表单被误判为「有未保存修改」→ nextPolicyForm；
 *   · 保存方式统一为底部 SaveBar（10-visual-refresh §1.6），页面只剩一个主按钮「重新采集」；
 *   · 加载中 KPI 显示骨架而不是 0；切平台 / 卸载时取消在途请求（AbortController）；
 *   · 状态色只表达状态：可用积分、长期有效、消耗构成不着色；已禁用用灰。
 */
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, type VNodeChild } from 'vue'
import { NInputNumber, NSelect } from 'naive-ui'
import { CalendarClock, InfinityIcon, RefreshCw, TriangleAlert, Wallet } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  BarList,
  Btn,
  Chip,
  DataTable,
  DetailDrawer,
  Empty,
  ErrorBlock,
  HelpTip,
  JobProgress,
  KV,
  KpiCard,
  Meter,
  SaveBar,
  StackMeter,
  StatusDot,
  type JobItem,
  type KvItem,
  type SorterPayload,
  type StackMeterItem,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n, type MessageKey } from '@/i18n'
import { FMT_EMPTY, fmtCredit, fmtInt, fmtTime } from '@/utils/format'
import {
  accountStatusTone,
  creditKpis,
  daysTone,
  getCreditExpiry,
  getCreditUsage,
  modeSelectOptions,
  nextPolicyForm,
  paginateRows,
  restoreAutomaticSelection,
  riskView,
  saveSelectionPolicy,
  selectionPolicyDirty,
  sortAccountRows,
  syncCredits,
  toPackageRows,
  type AccountSortKey,
  type CreditAccountRow,
  type CreditExpiryPayload,
  type CreditUsagePayload,
  type CreditsSyncResult,
  type PackageRowView,
  type SelectionPolicy,
} from '@/api/credits'
import { useRevisit } from '@/pages/_shared/useRevisit'

const { t } = useI18n()

const helpText = computed(() => t('page.credits.help'))
const policyNote = computed(() => t('page.credits.policyNote'))

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
 * 文案（i18n）：剩余天数、账号状态
 * ------------------------------------------------------------------ */

function soonestText(days: number | null | undefined): string {
  if (days === null || days === undefined || !Number.isFinite(Number(days))) return t('page.credits.soonest.none')
  if (Number(days) < 1) return t('page.credits.soonest.hours', { hours: Math.max(1, Math.round(Number(days) * 24)) })
  return t('page.credits.soonest.days', { days: Math.floor(Number(days)) })
}

const STATUS_KEYS: Record<string, MessageKey> = {
  active: 'page.credits.status.active',
  banned: 'page.credits.status.banned',
  expired: 'page.credits.status.expired',
  disabled: 'page.credits.status.disabled',
}

function statusText(status: string): string {
  const key = STATUS_KEYS[String(status || 'active')]
  return key ? t(key) : String(status || FMT_EMPTY)
}

/* ------------------------------------------------------------------ *
 * 取数（全部挂 AbortController：切平台 / 卸载立即取消）
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

let expiryController: AbortController | null = null
let usageController: AbortController | null = null
let moreController: AbortController | null = null
let alive = true

const dirty = computed(() => selectionPolicyDirty(policy.value, form.mode, form.interval))
const dirtyCount = computed(() => {
  if (!policy.value) return 0
  let count = 0
  if (policy.value.mode !== form.mode) count += 1
  if (Number(policy.value.upstream_start_interval_ms) !== Number(form.interval)) count += 1
  return count
})
const firstLoading = computed(() => expiry.value === null && loading.expiry)
const modeOptions = computed(() => modeSelectOptions(policy.value))
const modeDescription = computed(
  () => policy.value?.modes.find((item) => item.value === form.mode)?.description || '',
)
const isManual = computed(() => expiry.value?.selection_mode === 'manual')

/** 用新 policy 更新本地状态；用户没改过表单时表单跟随新值（修 P0-10）。 */
function applyPolicy(next: SelectionPolicy | null | undefined): void {
  const synced = nextPolicyForm(policy.value, form, next)
  policy.value = next ?? policy.value
  if (synced) {
    form.mode = synced.mode
    form.interval = synced.interval
  }
}

async function loadExpiry(): Promise<void> {
  expiryController?.abort()
  moreController?.abort()
  const current = new AbortController()
  expiryController = current
  loading.expiry = true
  expiryError.value = null
  try {
    const payload = await getCreditExpiry({ packageLimit: 60 }, current.signal)
    if (expiryController !== current) return
    expiry.value = payload
    packages.value = toPackageRows(payload.packages)
    packagesHasMore.value = Boolean(payload.has_more)
    applyPolicy(payload.routing_policy)
  } catch (error) {
    if (isAbortError(error) || expiryController !== current) return
    expiryError.value = toErrorState(error)
    if (expiry.value !== null) toast.error(failureText(t('page.credits.action.refreshExpiry'), error))
  } finally {
    if (expiryController === current) {
      expiryController = null
      loading.expiry = false
    }
  }
}

async function loadUsage(): Promise<void> {
  usageController?.abort()
  const current = new AbortController()
  usageController = current
  loading.usage = true
  usageError.value = null
  try {
    const payload = await getCreditUsage({ days: 7, top: 12 }, current.signal)
    if (usageController !== current) return
    usage.value = payload
  } catch (error) {
    if (isAbortError(error) || usageController !== current) return
    usageError.value = toErrorState(error)
    if (usage.value !== null) toast.error(failureText(t('page.credits.action.refreshUsage'), error))
  } finally {
    if (usageController === current) {
      usageController = null
      loading.usage = false
    }
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadExpiry(), loadUsage()])
}

async function loadMorePackages(): Promise<void> {
  if (loading.more || !packagesHasMore.value) return
  const current = new AbortController()
  moreController = current
  loading.more = true
  try {
    const payload = await getCreditExpiry(
      { packageLimit: 60, packageOffset: packages.value.length },
      current.signal,
    )
    if (moreController !== current) return
    packages.value = [...packages.value, ...toPackageRows(payload.packages)]
    packagesHasMore.value = Boolean(payload.has_more)
    if (expiry.value) {
      expiry.value = { ...expiry.value, has_more: payload.has_more, packages_total: payload.packages_total }
    }
  } catch (error) {
    if (isAbortError(error) || moreController !== current) return
    toast.error(failureText(t('page.credits.action.loadMore'), error))
  } finally {
    if (moreController === current) {
      moreController = null
      loading.more = false
    }
  }
}

/* ------------------------------------------------------------------ *
 * 账号调度策略（底部 SaveBar 统一保存）
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
    const saved = await saveSelectionPolicy({ mode: form.mode, upstream_start_interval_ms: interval })
    // 保存成功：表单就是新值，直接对齐（不走「用户改过就保留」的分支）。
    policy.value = saved
    form.mode = saved.mode
    form.interval = Number(saved.upstream_start_interval_ms)
    toast.success(t('page.credits.policy.saved'))
    await loadExpiry()
  } catch (error) {
    toast.error(failureText(t('page.credits.action.savePolicy'), error))
  } finally {
    loading.saving = false
  }
}

function discardPolicy(): void {
  if (!policy.value) return
  form.mode = policy.value.mode
  form.interval = Number(policy.value.upstream_start_interval_ms)
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
 * 重新采集（同步接口 + JobProgress 卡片）
 * ------------------------------------------------------------------ */

function syncText(result: CreditsSyncResult): string {
  return [
    t('page.credits.sync.done', { ok: fmtInt(result.refreshed), total: fmtInt(result.total) }),
    result.failed ? t('page.credits.sync.failedPart', { count: fmtInt(result.failed) }) : '',
    result.credits_expiring_soon
      ? t('page.credits.sync.expiringPart', { amount: fmtCredit(result.credits_expiring_soon) })
      : '',
  ]
    .filter(Boolean)
    .join(t('page.credits.sync.sep'))
}

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
    if (!alive) return
    const text = syncText(result)
    job.value = {
      id: 'credits-sync',
      name: t('page.credits.sync.name'),
      status: result.failed > 0 ? 'failed' : 'done',
      percent: 100,
      succeeded: result.refreshed,
      failed: result.failed,
      message: text,
    }
    toast.success(text)
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
 * KPI / 风险分布 / 消耗构成
 * ------------------------------------------------------------------ */

const kpis = computed(() => creditKpis(expiry.value))

const headerMeta = computed(() => {
  const k = kpis.value
  if (!k) return ''
  const parts = [t('page.credits.meta.accounts', { count: fmtInt(k.accounts) })]
  if (k.snapshotAccounts < k.accounts) {
    parts.push(t('page.credits.meta.snapshot', { count: fmtInt(k.snapshotAccounts), total: fmtInt(k.accounts) }))
  }
  return parts.join(' · ')
})

const risk = computed(() => riskView(expiry.value))
const RISK_LABEL_KEYS: MessageKey[] = [
  'page.credits.risk.within30',
  'page.credits.risk.beyond30',
  'page.credits.risk.evergreen',
]
const riskItems = computed<StackMeterItem[]>(() => {
  const within = (expiry.value?.windows || []).find((item) => item.days === 30)
  return risk.value.items.map((item, index) => ({
    ...item,
    label:
      index === 0 && within
        ? t('page.credits.risk.within30Packages', { count: fmtInt(within.packages) })
        : t(RISK_LABEL_KEYS[index] ?? RISK_LABEL_KEYS[0]),
  }))
})
const riskHint = computed(() =>
  risk.value.total > 0 ? t('page.credits.risk.hint', { total: fmtCredit(risk.value.total) }) : '',
)

const usageItems = computed(() =>
  (usage.value?.accounts || [])
    .filter((item) => Number(item.credits) > 0)
    .slice(0, 12)
    .map((item) => ({
      label: item.account_name + ' · ' + t('page.credits.usage.requests', { count: fmtInt(item.requests) }),
      value: Number(item.credits) || 0,
      tone: 'brand' as const,
    })),
)
const usageTotal = computed(() => Number(usage.value?.total_credits) || 0)
const usageHint = computed(() =>
  usage.value
    ? t('page.credits.usage.hint', { days: fmtInt(usage.value.range_days), total: fmtCredit(usageTotal.value) })
    : '',
)

/* ------------------------------------------------------------------ *
 * 积分包表
 * ------------------------------------------------------------------ */

function packageExpireText(item: PackageRowView): string {
  if (item.expire_date || item.expire_label) return item.expire_date || item.expire_label
  return item.expire_at ? fmtTime(item.expire_at) : t('page.credits.col.evergreen')
}

const packageColumns = computed<TableColumn[]>(() => [
  {
    key: 'display_name',
    title: t('page.credits.col.source'),
    mobile: 'title',
    minWidth: 180,
    render: (row: TableRow) =>
      h('span', { class: 'cr-strong' }, (row as unknown as PackageRowView).display_name || FMT_EMPTY),
  },
  {
    key: 'account_name',
    title: t('page.credits.col.account'),
    mobile: 'subtitle',
    minWidth: 160,
    render: (row: TableRow) =>
      h('span', { class: 'cr-muted' }, (row as unknown as PackageRowView).account_name || FMT_EMPTY),
  },
  {
    key: 'remaining',
    title: t('page.credits.col.credits'),
    mobile: 'meta',
    width: 110,
    numeric: true,
    render: (row: TableRow) =>
      h('span', { class: 'cr-strong tnum' }, (row as unknown as PackageRowView).remainingText),
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
        tone: item.expiring_soon ? 'warn' : 'neutral',
        showValue: true,
        valueText: Math.round(item.remainingRatio) + '%',
        ariaLabel: t('page.credits.meter.packageRatioAria'),
      })
    },
  },
  {
    key: 'expire',
    title: t('page.credits.col.expireDate'),
    mobile: 'meta',
    width: 170,
    render: (row: TableRow) => {
      const item = row as unknown as PackageRowView
      return h('span', { class: 'cr-expire' }, [
        h(TitleTip, { content: String((item.expire_label || '') ?? '') }, { default: () => h('span', { class: 'tnum' }, packageExpireText(item)) }),
        item.expired
          ? h(Badge, { tone: 'danger' }, { default: () => t('page.credits.col.expiredBadge') })
          : item.expiring_soon
            ? h(Badge, { tone: 'warn' }, { default: () => t('page.credits.col.expiringSoonBadge') })
            : null,
      ])
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
  soonest: 'soonest_expire_at',
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

function accountPackagesCell(row: TableRow): VNodeChild {
  const item = row as unknown as CreditAccountRow & { rank: number }
  const list = (item.next_expiring || []).slice(0, 2)
  if (!list.length) return h('span', { class: 'cr-faint' }, FMT_EMPTY)
  const rest = (item.next_expiring || []).length - list.length
  return h('div', { class: 'cr-chips' }, [
    ...list.map((pkg) =>
      h(TitleTip, { content: String((pkg.expire_label || pkg.expire_date) ?? ''), key: pkg.package_code || pkg.display_name }, { default: () => h(Chip, { key: pkg.package_code || pkg.display_name }, {
        default: () => pkg.display_name + ' · ' + fmtCredit(pkg.remaining),
      }) }),
    ),
    rest > 0 ? h('span', { class: 'cr-faint' }, '+' + rest) : null,
  ])
}

const accountColumns = computed<TableColumn[]>(() => [
  {
    key: 'rank',
    title: '#',
    mobile: 'hidden',
    width: 52,
    numeric: true,
    hideable: false,
    render: (row: TableRow) => h('span', { class: 'cr-rank tnum' }, (row as unknown as { rank: number }).rank),
  },
  {
    key: 'account_name',
    title: t('page.credits.col.account'),
    mobile: 'title',
    minWidth: 220,
    sortable: true,
    sortOrder: sortOrderOf('account_name'),
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      return h('div', { class: 'cr-account' }, [
        h(TitleTip, { content: String((item.account_name) ?? '') }, { default: () => h('span', { class: 'cr-strong cr-ellipsis' }, item.account_name) }),
        item.uid ? h('span', { class: 'cr-sub mono cr-ellipsis' }, item.uid) : null,
      ])
    },
  },
  {
    key: 'status',
    title: t('page.credits.col.status'),
    mobile: 'subtitle',
    width: 96,
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      return h(StatusDot, { tone: accountStatusTone(item.status), label: statusText(item.status) })
    },
  },
  {
    key: 'total_remaining',
    title: t('page.credits.col.totalRemaining'),
    mobile: 'meta',
    width: 116,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('total_remaining'),
    render: (row: TableRow) =>
      h('span', { class: 'cr-strong tnum' }, fmtCredit((row as unknown as CreditAccountRow).total_remaining)),
  },
  {
    key: 'expiring_soon_remaining',
    title: t('page.credits.col.expiringSoon'),
    mobile: 'meta',
    width: 120,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('expiring_soon_remaining'),
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      return item.expiring_soon_remaining > 0
        ? h('span', { class: 'cr-warn tnum' }, fmtCredit(item.expiring_soon_remaining))
        : h('span', { class: 'cr-faint tnum' }, FMT_EMPTY)
    },
  },
  {
    key: 'evergreen_remaining',
    title: t('page.credits.col.evergreen'),
    mobile: 'meta',
    width: 108,
    numeric: true,
    render: (row: TableRow) => {
      const value = (row as unknown as CreditAccountRow).evergreen_remaining
      return value > 0
        ? h('span', { class: 'tnum' }, fmtCredit(value))
        : h('span', { class: 'cr-faint tnum' }, FMT_EMPTY)
    },
  },
  {
    key: 'soonest',
    title: t('page.credits.col.soonest'),
    mobile: 'meta',
    width: 180,
    sortable: true,
    sortOrder: sortOrderOf('soonest'),
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      const tone = daysTone(item.soonest_days_left)
      return h('div', { class: 'cr-stack' }, [
        tone === 'neutral'
          ? h('span', { class: 'cr-muted' }, soonestText(item.soonest_days_left))
          : h(Badge, { tone, dot: true }, { default: () => soonestText(item.soonest_days_left) }),
        item.synced_at
          ? h(TitleTip, { content: String((fmtTime(item.synced_at)) ?? '') }, { default: () => h('span', { class: 'cr-sub tnum' }, t('page.credits.col.syncedAt', { time: fmtTime(item.synced_at) })) })
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
    { label: 'UID', value: item.uid || FMT_EMPTY, mono: true, copy: Boolean(item.uid) },
    { label: t('page.credits.col.status'), value: statusText(item.status) },
    { label: t('page.credits.col.totalRemaining'), value: fmtCredit(item.total_remaining) },
    { label: t('page.credits.detail.balance'), value: fmtCredit(item.balance_remain) },
    { label: t('page.credits.col.expiringSoon'), value: fmtCredit(item.expiring_soon_remaining) },
    { label: t('page.credits.col.evergreen'), value: fmtCredit(item.evergreen_remaining) },
    { label: t('page.credits.detail.expired'), value: fmtCredit(item.expired_remaining) },
    { label: t('page.credits.col.soonest'), value: soonestText(item.soonest_days_left) },
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
  {
    key: 'remaining',
    title: t('page.credits.detail.remainingOfTotal'),
    mobile: 'meta',
    width: 150,
    numeric: true,
    render: (row: TableRow) => {
      const item = row as unknown as PackageRowView
      return h('span', { class: 'tnum' }, item.remainingText + ' / ' + item.totalText)
    },
  },
  {
    key: 'expire',
    title: t('page.credits.col.expireDate'),
    mobile: 'meta',
    width: 130,
    render: (row: TableRow) => h('span', { class: 'tnum' }, packageExpireText(row as unknown as PackageRowView)),
  },
  {
    key: 'usage',
    title: t('page.credits.col.remainingRatio'),
    mobile: 'meta',
    width: 140,
    render: (row: TableRow) => {
      const item = row as unknown as PackageRowView
      return h(Meter, {
        value: item.remainingRatio,
        max: 100,
        tone: item.expiring_soon ? 'warn' : 'neutral',
        showValue: true,
        valueText: Math.round(item.remainingRatio) + '%',
      })
    },
  },
])

function openDetail(row: CreditAccountRow): void {
  selected.value = row
  detailOpen.value = true
}

const detailBadge = computed(() => {
  const item = selected.value
  if (!item) return null
  const tone = daysTone(item.soonest_days_left)
  return { tone, label: soonestText(item.soonest_days_left) }
})

const policyModeLabel = computed(() =>
  isManual.value
    ? t('page.credits.ranking.modeManual')
    : t('page.credits.ranking.modeAuto', {
        label: policy.value?.modes.find((m) => m.value === policy.value?.mode)?.label || FMT_EMPTY,
      }),
)

/* ------------------------------------------------------------------ *
 * 生命周期：平台切换重取、卸载取消
 * ------------------------------------------------------------------ */

let stopPlatformWatch: (() => void) | null = null
let lastSelector = ''

function abortAll(): void {
  expiryController?.abort()
  usageController?.abort()
  moreController?.abort()
  expiryController = null
  usageController = null
  moreController = null
}

onMounted(() => {
  alive = true
  lastSelector = platform.selector
  void loadAll()
  stopPlatformWatch = platform.onChange((detail) => {
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    abortAll()
    if (table.state.page !== 1) table.setPage(1)
    void loadAll()
  })
})

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

// 回到 KeepAlive 缓存页：保留旧数据静默刷新，不出骨架（11 §2）。
useRevisit(() => silently(() => loadAll()))

onBeforeUnmount(() => {
  alive = false
  stopPlatformWatch?.()
  stopPlatformWatch = null
  abortAll()
})
</script>

<template>
  <div class="credits-page">
    <PageHeader :title="t('page.credits.title')" :help="helpText">
      <template #meta>
        <span v-if="headerMeta" class="cr-meta tnum">{{ headerMeta }}</span>
      </template>
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading.expiry" @click="loadAll">
          {{ t('common.refresh') }}
        </Btn>
        <Btn variant="primary" :loading="loading.syncing" @click="syncAll">
          {{ t('page.credits.action.sync') }}
        </Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="expiryError && !expiry"
      :title="t('page.credits.error.expiry')"
      :detail="expiryError.detail || expiryError.message"
      @retry="loadAll"
    />

    <template v-else>
      <!-- KPI 行（加载中显示骨架，不显示 0） -->
      <section class="cr-kpis" :aria-label="t('page.credits.kpi.aria')">
        <KpiCard
          :label="t('page.credits.kpi.available')"
          :value="kpis ? fmtCredit(kpis.available) : null"
          :unit="t('page.credits.unit.credits')"
          :hint="kpis ? t('page.credits.kpi.availableHint', { count: fmtInt(kpis.accounts) }) : undefined"
          :icon="Wallet"
          tone="neutral"
          :loading="firstLoading"
        />
        <KpiCard
          :label="kpis ? t('page.credits.kpi.expiring', { days: kpis.expiringDays }) : t('page.credits.col.expiringSoon')"
          :value="kpis ? fmtCredit(kpis.expiringSoon) : null"
          :unit="t('page.credits.unit.credits')"
          :hint="
            kpis
              ? kpis.expiringSoon > 0
                ? t('page.credits.kpi.expiringHint', { pct: kpis.atRiskPct.toFixed(1), count: fmtInt(kpis.expiringPackages) })
                : t('page.credits.kpi.expiringNone')
              : undefined
          "
          :icon="CalendarClock"
          :tone="kpis && kpis.expiringSoon > 0 ? 'warn' : 'neutral'"
          :loading="firstLoading"
        />
        <KpiCard
          :label="t('page.credits.col.evergreen')"
          :value="kpis ? fmtCredit(kpis.evergreen) : null"
          :unit="t('page.credits.unit.credits')"
          :hint="t('page.credits.kpi.evergreenHint')"
          :icon="InfinityIcon"
          tone="neutral"
          :loading="firstLoading"
        />
        <KpiCard
          :label="t('page.credits.kpi.soonest')"
          :value="kpis && kpis.soonestName ? kpis.soonestName : null"
          :hint="kpis ? soonestText(kpis.soonestDays) : undefined"
          :icon="TriangleAlert"
          :tone="kpis ? daysTone(kpis.soonestDays) : 'neutral'"
          :loading="firstLoading"
        />
      </section>

      <div class="cr-grid">
        <!-- 到期风险分布 -->
        <section class="cr-card cr-risk">
          <header class="cr-card__head">
            <h2 class="cr-card__title">{{ t('page.credits.risk.title') }}</h2>
            <span class="cr-sub tnum">{{ riskHint }}</span>
          </header>
          <div v-if="firstLoading" class="cr-skeleton"><div class="cr-skeleton__row mh-skeleton" /></div>
          <StackMeter
            v-else-if="!risk.empty"
            :items="riskItems"
            :total="risk.total"
            :unit="' ' + t('page.credits.unit.credits')"
          />
          <Empty v-else compact :icon="CalendarClock" :description="t('page.credits.risk.empty')" />
          <p class="cr-note">{{ t('page.credits.risk.note') }}</p>
        </section>

        <!-- 账号消耗构成 -->
        <section class="cr-card cr-usage">
          <header class="cr-card__head">
            <h2 class="cr-card__title">{{ t('page.credits.usage.title') }}</h2>
            <span class="cr-sub tnum">{{ usageHint }}</span>
          </header>
          <!-- 第 5 轮：已有数据时刷新失败只 toast（loadUsage 里已弹），不把旧数据换成错误块。 -->
          <ErrorBlock
            v-if="usageError && !usage"
            compact
            :title="t('page.credits.error.usage', { error: usageError.message })"
            :detail="usageError.detail"
            @retry="loadUsage"
          />
          <div v-else-if="usage === null && loading.usage" class="cr-skeleton">
            <div v-for="index in 4" :key="index" class="cr-skeleton__row mh-skeleton" />
          </div>
          <BarList
            v-else-if="usageItems.length"
            :items="usageItems"
            :unit="' ' + t('page.credits.unit.credits')"
            :total="usageTotal"
            :format="fmtCredit"
            :limit="8"
          />
          <Empty v-else compact :description="t('page.credits.usage.empty')" />
        </section>
      </div>

      <!-- 账号到期排行 -->
      <section class="cr-card cr-card--flush">
        <header class="cr-card__head cr-card__head--pad">
          <h2 class="cr-card__title">{{ t('page.credits.ranking.title') }}</h2>
          <span class="cr-sub">
            {{ t('page.credits.ranking.meta', { total: accountTotal, mode: policyModeLabel }) }}
          </span>
        </header>
        <div class="cr-card__body">
          <!-- 第 5 轮：同上，排行表刷新失败保留旧表（loadExpiry 已 toast）。 -->
          <ErrorBlock
            v-if="expiryError && !expiry"
            compact
            :title="t('page.credits.error.ranking', { error: expiryError.message })"
            :detail="expiryError.detail"
            @retry="loadExpiry"
          />
          <div v-else-if="firstLoading" class="cr-skeleton">
            <div v-for="index in 6" :key="index" class="cr-skeleton__row mh-skeleton" />
          </div>
          <Empty v-else-if="!accountTotal" :description="t('page.credits.ranking.empty')" />
          <DataTable
            v-else
            table-id="credits-accounts"
            :columns="accountColumns"
            :data="accountRows"
            row-key="account_id"
            remote
            :loading="loading.expiry && !silentRefresh"
            :refreshing="loading.expiry && silentRefresh"
            :page="table.state.page"
            :page-size="table.state.pageSize"
            :item-count="accountTotal"
            :page-sizes="[20, 50, 100]"
            :max-height="'none'"
            :virtual-scroll="false"
            clickable-rows
            :empty-description="t('page.credits.ranking.empty')"
            @update:page="table.setPage"
            @update:page-size="table.setPageSize"
            @update:sorter="onSorter"
            @row-click="(row: TableRow) => openDetail(row as unknown as CreditAccountRow)"
          />
          <p class="cr-note">{{ t('page.credits.ranking.note') }}</p>
        </div>
      </section>

      <!-- 最近快到期的积分包 -->
      <section class="cr-card cr-card--flush">
        <header class="cr-card__head cr-card__head--pad">
          <h2 class="cr-card__title">{{ t('page.credits.packages.title') }}</h2>
          <span class="cr-sub tnum">
            {{ t('page.credits.packages.counter', { shown: fmtInt(packages.length), total: fmtInt(expiry?.packages_total || 0) }) }}
          </span>
        </header>
        <div class="cr-card__body">
          <div v-if="firstLoading" class="cr-skeleton">
            <div v-for="index in 4" :key="index" class="cr-skeleton__row mh-skeleton" />
          </div>
          <DataTable
            v-else
            table-id="credits-packages"
            :columns="packageColumns"
            :data="packageRows"
            row-key="rowKey"
            :loading="loading.more"
            :pagination="false"
            :virtual-scroll="true"
            :max-height="'420px'"
            :empty-description="t('page.credits.packages.empty')"
          >
            <template #empty>
              <Empty compact :description="t('page.credits.packages.empty')" />
            </template>
          </DataTable>
          <div v-if="packagesHasMore" class="cr-more">
            <Btn variant="quiet" size="sm" :loading="loading.more" @click="loadMorePackages">
              {{ t('page.credits.action.loadMore') }}
            </Btn>
          </div>
        </div>
      </section>

      <!-- 账号调度策略：设置组（左标签 + 说明，右控件），保存走底部 SaveBar -->
      <section class="cr-card cr-policy" :aria-busy="loading.saving ? 'true' : 'false'">
        <header class="cr-card__head">
          <h2 class="cr-card__title">
            {{ t('page.credits.policy.title') }}
            <HelpTip :content="policyNote" :label="t('page.credits.policy.helpLabel')" />
          </h2>
          <StatusDot
            :tone="isManual ? 'warn' : 'ok'"
            :label="isManual ? t('page.credits.policy.manual') : t('page.credits.policy.auto')"
          />
        </header>

        <div class="cr-setting">
          <div class="cr-setting__label">
            <span class="cr-setting__name">{{ t('page.credits.policy.mode') }}</span>
            <span class="cr-setting__desc">{{ modeDescription || t('page.credits.policy.modeDesc') }}</span>
          </div>
          <div class="cr-setting__control">
            <NSelect
              class="cr-mode"
              :value="form.mode || null"
              :options="modeOptions"
              :placeholder="t('page.credits.policy.modePlaceholder')"
              :disabled="loading.saving || !policy"
              :aria-label="t('page.credits.policy.mode')"
              @update:value="(value: string) => (form.mode = value)"
            />
          </div>
        </div>

        <div class="cr-setting">
          <div class="cr-setting__label">
            <span class="cr-setting__name">{{ t('page.credits.policy.interval') }}</span>
            <span class="cr-setting__desc">{{ t('page.credits.intervalNote') }}</span>
          </div>
          <div class="cr-setting__control">
            <NInputNumber
              class="cr-interval"
              :value="form.interval"
              :min="0"
              :max="60000"
              :step="1"
              :show-button="false"
              :disabled="loading.saving || !policy"
              :aria-label="t('page.credits.policy.interval')"
              @update:value="(value: number | null) => (form.interval = typeof value === 'number' ? value : 0)"
            >
              <template #suffix>ms</template>
            </NInputNumber>
          </div>
        </div>

        <div class="cr-setting">
          <div class="cr-setting__label">
            <span class="cr-setting__name">{{ t('page.credits.policy.restore') }}</span>
            <span class="cr-setting__desc">
              {{ isManual ? t('page.credits.policy.restoreDescManual') : t('page.credits.policy.restoreDescAuto') }}
            </span>
          </div>
          <div class="cr-setting__control">
            <Btn variant="default" size="sm" :disabled="!isManual || loading.saving" @click="restoreAuto">
              {{ t('page.credits.policy.restore') }}
            </Btn>
          </div>
        </div>
      </section>
    </template>

    <SaveBar
      :dirty="dirtyCount"
      :saving="loading.saving"
      :disabled="!policy"
      @save="savePolicy"
      @discard="discardPolicy"
    />

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
      :badge="detailBadge"
      :subtitle="selected?.uid || ''"
    >
      <template v-if="selected">
        <KV :items="detailKv" label-width="96px" />
        <p class="cr-sub cr-drawer-sub">
          {{ t('page.credits.drawer.recentPackages', { count: detailPackages.length }) }}
        </p>
        <DataTable
          table-id="credits-detail"
          :columns="detailPackageColumns"
          :data="detailPackages"
          row-key="rowKey"
          :pagination="false"
          :virtual-scroll="false"
          :max-height="'none'"
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
  gap: var(--sp-5);
  min-width: 0;
  align-items: start;
}

.cr-meta {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.cr-kpis {
  display: grid;
  grid-auto-rows: 1fr;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
  align-items: stretch;
}

.cr-grid {
  display: grid;
  grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
  gap: var(--sp-4);
  align-items: start;
}

.cr-card {
  min-width: 0;
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-5);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-1);
}

.cr-card--flush {
  padding: 0;
  gap: 0;
  overflow: hidden;
}

.cr-card__head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
  min-height: 24px;
}

.cr-card__head--pad {
  padding: var(--sp-4) var(--sp-5) var(--sp-3);
}

.cr-card__head > .cr-sub {
  margin-left: auto;
}

.cr-card__head > :last-child:not(.cr-card__title) {
  margin-left: auto;
}

.cr-card__title {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.cr-card__body {
  display: grid;
  gap: var(--sp-2);
  padding: 0 var(--sp-5) var(--sp-4);
  min-width: 0;
}

.cr-sub {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.cr-note {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 80ch;
  text-wrap: pretty;
}

.cr-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.cr-skeleton__row {
  height: 14px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
}

/* 设置组：左 280px 标签 + 说明，右控件，组间 --border-soft 分隔（§1.6） */
.cr-policy {
  gap: 0;
}

.cr-policy > .cr-card__head {
  padding-bottom: var(--sp-3);
}

.cr-setting {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--sp-4);
  align-items: center;
  padding: var(--sp-3) 0;
  border-top: 1px solid var(--border-soft);
}

.cr-setting__label {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.cr-setting__name {
  font-weight: 500;
  color: var(--fg);
}

.cr-setting__desc {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  text-wrap: pretty;
}

.cr-setting__control {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.cr-mode {
  width: 260px;
  max-width: 100%;
}

.cr-interval {
  width: 180px;
}

.cr-more {
  display: flex;
  justify-content: center;
  padding-top: var(--sp-1);
}

.cr-strong {
  font-weight: 500;
  color: var(--fg);
}

.cr-muted {
  color: var(--fg-muted);
}

.cr-faint {
  color: var(--fg-faint);
}

.cr-warn {
  color: var(--warn);
  font-weight: 500;
}

.cr-account {
  display: grid;
  gap: 0;
  min-width: 0;
}

.cr-ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cr-stack {
  display: grid;
  gap: 2px;
  justify-items: start;
}

.cr-expire {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.cr-chips {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  flex-wrap: nowrap;
  overflow: hidden;
}

.cr-rank {
  color: var(--fg-faint);
}

.cr-drawer-sub {
  margin: var(--sp-4) 0 var(--sp-2);
}

@media (max-width: 1279px) {
  .cr-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1023px) {
  .cr-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .cr-setting {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sp-2);
  }
}

@media (max-width: 767px) {
  .cr-kpis {
    grid-template-columns: minmax(0, 1fr);
  }

  .cr-card {
    padding: var(--sp-3) var(--sp-4);
  }

  .cr-card--flush {
    padding: 0;
  }

  .cr-card__head--pad {
    padding: var(--sp-3) var(--sp-4) var(--sp-2);
  }

  .cr-card__body {
    padding: 0 var(--sp-4) var(--sp-3);
  }

  .cr-mode,
  .cr-interval {
    width: 100%;
  }
}
</style>
