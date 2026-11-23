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

const HELP_TEXT =
  '按到期时间展示账号风险。实际新会话由下方调度策略分配；平台按 7 天判定「快到期」，这部分额度不用完就会作废。' +
  '概览里的「可用积分」是余额口径（与账号池一致），到期分档条是快照口径（只覆盖采集过到期数据的账号）。'

const POLICY_NOTE =
  '保存后恢复自动选号并清除手动优先级，对新会话生效；已有会话继续保持粘性，健康、冷却、绑定和账号在途限制仍然有效。'

const INTERVAL_NOTE = '仅限制同一上游账号连续发起请求；不同账号可同时请求。已开始的请求继续并行，设为 0 可关闭。'

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
    if (expiry.value !== null) toast.error(failureText('刷新积分到期', error))
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
    if (usage.value !== null) toast.error(failureText('刷新账号消耗构成', error))
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
    toast.error(failureText('加载更多积分包', error))
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
    toast.error('上游启动间隔必须是 0 到 60000 的整数毫秒')
    return
  }
  loading.saving = true
  try {
    policy.value = await saveSelectionPolicy({ mode: form.mode, upstream_start_interval_ms: interval })
    applyPolicyForm()
    toast.success('账号调度策略已保存，新会话立即按新策略分配')
    await loadExpiry()
  } catch (error) {
    toast.error(failureText('保存账号调度策略', error))
  } finally {
    loading.saving = false
  }
}

async function restoreAuto(): Promise<void> {
  if (!expiry.value || !isManual.value || loading.saving) return
  const label = policy.value?.modes.find((item) => item.value === policy.value?.mode)?.label || '自动'
  const ok = await confirm({
    title: '恢复自动选号？',
    message: '会清除手动优先级，恢复「' + label + '」自动调度；下一次选号生效，在途请求继续执行。',
    okText: '恢复自动选号',
  })
  if (!ok) return
  loading.saving = true
  try {
    await restoreAutomaticSelection(expiry.value.selection_order)
    toast.success('已恢复当前自动调度策略')
    await loadExpiry()
  } catch (error) {
    toast.error(failureText('恢复自动选号', error))
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
    name: '重新采集积分快照',
    status: 'running',
    percent: 0,
    message: '串行采集全部启用账号，账号多时需要几十秒…',
  }
  try {
    const result = await syncCredits()
    job.value = {
      id: 'credits-sync',
      name: '重新采集积分快照',
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
    toast.error(failureText('重新采集积分快照', error))
  } finally {
    loading.syncing = false
  }
}

function onJobView(): void {
  if (!job.value) return
  if (job.value.status === 'failed') toast.warning(job.value.message || '部分账号采集失败')
  else toast.info(job.value.message || '采集已完成')
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
    title: '积分',
    mobile: 'title',
    width: 96,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'cr-strong tnum' }, (row as unknown as PackageRowView).remainingText),
  },
  { key: 'display_name', title: '来源', mobile: 'subtitle', minWidth: 160 },
  {
    key: 'account_name',
    title: '账号',
    mobile: 'meta',
    minWidth: 140,
    render: (row: TableRow) => h('span', { class: 'cr-muted' }, (row as unknown as PackageRowView).account_name || FMT_EMPTY),
  },
  {
    key: 'expire',
    title: '到期日',
    mobile: 'meta',
    width: 150,
    render: (row: TableRow) => {
      const item = row as unknown as PackageRowView
      return h('span', { class: 'cr-expire' }, [
        h('span', { class: 'tnum' }, item.expireText),
        item.expiring_soon ? h(Badge, { tone: 'warn' }, { default: () => '快到期' }) : null,
        item.expired ? h(Badge, { tone: 'danger' }, { default: () => '已过期' }) : null,
      ])
    },
  },
  {
    key: 'usage',
    title: '剩余占比',
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
        ariaLabel: '积分包剩余占比',
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
  if (!list.length) return h('span', { class: 'cr-muted' }, '没有仍有剩余的积分包')
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
    title: '账号',
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
    title: '状态',
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
    title: '剩余积分',
    mobile: 'meta',
    width: 108,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('total_remaining'),
    render: (row: TableRow) => h('span', { class: 'cr-strong tnum' }, fmtCredit((row as unknown as CreditAccountRow).total_remaining)),
  },
  {
    key: 'expiring_soon_remaining',
    title: '7 天内到期',
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
    title: '长期有效',
    mobile: 'meta',
    width: 104,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'cr-muted tnum' }, fmtCredit((row as unknown as CreditAccountRow).evergreen_remaining)),
  },
  {
    key: 'soonest',
    title: '最近到期',
    mobile: 'meta',
    width: 180,
    sortable: true,
    sortOrder: sortOrderOf('soonest_expire_at'),
    render: (row: TableRow) => {
      const item = row as unknown as CreditAccountRow
      return h('div', { class: 'cr-stack' }, [
        h(Badge, { tone: soonestTone(item) }, { default: () => soonestLabel(item.soonest_days_left) }),
        item.synced_at ? h('span', { class: 'cr-sub tnum' }, fmtTime(item.synced_at) + ' 采集') : null,
      ])
    },
  },
  {
    key: 'next_expiring',
    title: '最近快到期的积分包',
    mobile: 'meta',
    minWidth: 240,
    render: accountPackagesCell,
  },
  {
    key: 'actions',
    title: '操作',
    mobile: 'actions',
    width: 84,
    hideable: false,
    fixed: 'right',
    render: (row: TableRow) =>
      h('button', {
        type: 'button',
        class: 'cr-link',
        'aria-label': '查看积分明细',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          openDetail(row as unknown as CreditAccountRow)
        },
      }, '明细'),
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
    { label: '账号', value: item.account_name },
    { label: 'UID', value: item.uid || FMT_EMPTY, mono: true },
    { label: '状态', value: accountStatusText(item.status) },
    { label: '剩余积分', value: fmtCredit(item.total_remaining) },
    { label: '可用余额', value: fmtCredit(item.balance_remain) },
    { label: '7 天内到期', value: fmtCredit(item.expiring_soon_remaining) },
    { label: '长期有效', value: fmtCredit(item.evergreen_remaining) },
    { label: '已过期', value: fmtCredit(item.expired_remaining) },
    { label: '最近到期', value: soonestLabel(item.soonest_days_left) },
    { label: '积分包', value: item.active_package_count + ' 个有效 / ' + item.package_count + ' 个' },
    { label: '采集时间', value: item.synced_at ? fmtTime(item.synced_at) : FMT_EMPTY },
  ]
})

const detailPackages = computed<TableRow[]>(() =>
  toPackageRows(selected.value?.next_expiring || []) as unknown as TableRow[],
)

const detailPackageColumns = computed<TableColumn[]>(() => [
  { key: 'display_name', title: '来源', mobile: 'title', minWidth: 140 },
  { key: 'remaining', title: '剩余 / 总额', mobile: 'meta', width: 140, numeric: true },
  { key: 'expire', title: '到期日', mobile: 'meta', width: 150 },
  {
    key: 'usage',
    title: '剩余占比',
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
    <PageHeader title="积分到期" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="primary" :loading="loading.syncing" @click="syncAll">重新采集</Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading.expiry" @click="loadAll">刷新</Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="expiryError && !expiry"
      title="加载积分到期失败"
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
        <template #title>账号调度策略</template>
        <template #extra>
          <Badge v-if="isManual" tone="warn" dot>手动优先级</Badge>
          <Badge v-else tone="brand" dot>自动选号</Badge>
          <HelpTip :content="POLICY_NOTE" label="调度策略说明" />
        </template>

        <div class="cr-policy__row">
          <label class="cr-field">
            <span class="cr-field__label">选号模式</span>
            <NSelect
              class="cr-mode"
              :value="form.mode"
              :options="modeOptions"
              :disabled="loading.saving"
              aria-label="选号模式"
              @update:value="(value: string) => (form.mode = value)"
            />
          </label>
          <label class="cr-field">
            <span class="cr-field__label">同账号上游启动间隔（毫秒）</span>
            <NInputNumber
              class="cr-interval"
              :value="form.interval"
              :min="0"
              :max="60000"
              :step="1"
              :show-button="false"
              :disabled="loading.saving"
              aria-label="同账号上游启动间隔（毫秒）"
              @update:value="(value: number | null) => (form.interval = typeof value === 'number' ? value : 0)"
            />
          </label>
          <Btn variant="primary" :disabled="!dirty || loading.saving" :loading="loading.saving" @click="savePolicy">
            保存策略
          </Btn>
          <Btn variant="quiet" :disabled="!isManual || loading.saving" @click="restoreAuto">恢复自动选号</Btn>
        </div>
        <p class="cr-hint">{{ modeDescription }}</p>
        <p class="cr-note">{{ INTERVAL_NOTE }}</p>
        <p class="cr-note">{{ POLICY_NOTE }}</p>
      </Card>

      <!-- 到期风险分布 -->
      <Card class="cr-risk" :loading="loading.expiry">
        <template #title>到期风险分布</template>
        <template #extra><span class="cr-sub">{{ risk.hint }}</span></template>

        <StackMeter v-if="!risk.empty" :items="riskItems" :total="risk.total" unit=" 积分" />
        <Empty v-else description="没有任何即将到期的额度 —— 全部积分包都是长期有效或已清空。" />
        <p class="cr-note">
          ≤30 天是「快到期」合计（按到期时间分档）；30 天以外是有到期日但还很远的额度；长期有效没有作废风险。
        </p>
      </Card>

      <div class="cr-grid">
        <!-- 最近快到期的积分包 -->
        <Card class="cr-packages" :loading="loading.expiry || loading.more">
          <template #title>最近快到期的积分包</template>
          <template #extra>
            <span class="cr-sub">按到期升序 · 已显示 {{ packages.length }}/{{ expiry?.packages_total || 0 }} 个</span>
          </template>

          <DataTable
            table-id="credits-packages"
            :columns="packageColumns"
            :data="packageRows"
            row-key="rowKey"
            :pagination="false"
            :virtual-scroll="true"
            empty-description="没有仍有剩余的积分包。点右上「重新采集」从上游拉取最新明细。"
          >
            <template #empty>
              <Empty description="没有仍有剩余的积分包。点右上「重新采集」从上游拉取最新明细。" />
            </template>
          </DataTable>
          <div v-if="packagesHasMore" class="cr-more">
            <Btn variant="quiet" size="sm" :loading="loading.more" @click="loadMorePackages">加载更多</Btn>
          </div>
        </Card>

        <!-- 账号消耗构成 -->
        <Card class="cr-usage" :loading="loading.usage">
          <template #title>账号消耗构成（近 7 天）</template>
          <template #extra><span class="cr-sub">{{ usageView.hint }}</span></template>

          <ErrorBlock
            v-if="usageError"
            compact
            :title="'加载账号消耗构成失败：' + usageError.message"
            :detail="usageError.detail"
            @retry="loadUsage"
          />
          <BarList
            v-else-if="!usageView.empty"
            :items="usageView.items"
            unit=" 积分"
            :total="usageView.total"
            :format="fmtCredit"
            :limit="12"
          />
          <Empty v-else description="近 7 天没有产生积分消耗。" />
        </Card>
      </div>

      <!-- 账号到期排行 -->
      <Card class="cr-ranking" :loading="loading.expiry">
        <template #title>账号到期排行</template>
        <template #extra>
          <span class="cr-sub">
            {{ accountTotal }} 个账号 · {{ isManual ? '手动优先级（已覆盖自动策略）' : '自动：' + (policy?.modes.find((m) => m.value === policy?.mode)?.label || '—') }} · 排名用于观察到期风险
          </span>
        </template>

        <ErrorBlock
          v-if="expiryError"
          compact
          :title="'刷新积分到期失败：' + expiryError.message"
          :detail="expiryError.detail"
          @retry="loadExpiry"
        />
        <Empty
          v-else-if="!accountTotal"
          description="没有启用账号。先在「账号池」里添加账号。"
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
          empty-description="没有启用账号。先在「账号池」里添加账号。"
          @update:page="table.setPage"
          @update:page-size="table.setPageSize"
          @update:sorter="onSorter"
          @row-click="(row: TableRow) => openDetail(row as unknown as CreditAccountRow)"
        />
        <p class="cr-note">名次按到期紧迫度排序（后端 ordered_accounts）；实际选号仍由上方「账号调度策略」决定。</p>
      </Card>
    </template>

    <JobProgress :jobs="job ? [job] : []" title="积分采集" @hide="job = null" @view="onJobView" />

    <DetailDrawer
      v-model:show="detailOpen"
      :title="selected ? '积分明细 · ' + selected.account_name : '积分明细'"
      :badge="selected ? { tone: selected.expiring_soon ? 'warn' : 'ok', label: soonestLabel(selected.soonest_days_left) } : null"
      :subtitle="selected?.uid || ''"
    >
      <template v-if="selected">
        <KV :items="detailKv" label-width="96px" />
        <p class="cr-sub">最近到期的 {{ detailPackages.length }} 个积分包（其余明细请在账号池查看）：</p>
        <DataTable
          table-id="credits-detail"
          :columns="detailPackageColumns"
          :data="detailPackages"
          row-key="rowKey"
          :pagination="false"
          :virtual-scroll="false"
          empty-description="没有仍有剩余的积分包。"
        />
      </template>
      <template #footer>
        <Btn variant="default" @click="detailOpen = false">关闭</Btn>
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