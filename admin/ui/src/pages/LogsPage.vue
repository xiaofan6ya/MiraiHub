<script setup lang="ts">
/**
 * S15 用量日志 /logs（v2.1 视觉精修）
 *
 * 布局（10-visual-refresh.md）：
 *   PageHeader（右侧：请求详情记录开关 · 导出 · 刷新）
 *   → 4 张 KPI（请求数 / 成功率 / 积分 / Token，口径 = /api/logs/summary）
 *   → 请求动态（近 15 分钟每分钟小柱图，横轴本地时区 + 最近 5 条）
 *   → 日志表（工具栏一行：搜索 / 结果 / 时间 / 平台 / 账号 / 更多筛选 · 共 N 条 · 重置 · 密度 · 列设置）
 *   → 按账号汇总（默认收起）
 *   → 调用详情抽屉（失败时诊断与尝试记录置顶）
 *
 * 子组件在 src/pages/logs/ 下；本文件只管数据、路由同步、轮询与 SSE。
 *
 * 契约：docs/ui-v2/contracts/logs.md
 * 后端：admin/routers/logs.py（list_logs / log_summary / log_accounts / log_detail / capture_* / export）
 *   · 列表支持 keyset（before_id）；深翻页只走主键索引。快速翻页关闭总数统计。
 *   · log_summary **不接 platform**（logs.py:1128）—— KPI 与按账号汇总按全网关口径，选了平台时 KPI 写明口径。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { NDropdown } from 'naive-ui'
import { ChevronDown, Download, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import { Btn, IconBtn } from '@/components'
import { usePolling, useRevisit, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import { fmtCredit, fmtInt, fmtRelative } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n, type MessageKey } from '@/i18n'
import {
  LOG_RANGE_OPTIONS,
  buildExportQuery,
  buildLiveBars,
  buildLogQuery,
  buildSummaryQuery,
  downloadLogsExport,
  getLiveRequests,
  getUsageSummary,
  listLogAccounts,
  listUsageLogs,
  requestStreamTicket,
  summaryKpis,
  toLogRows,
  type AccountSummaryRow,
  type LiveRequestsPayload,
  type LogAccountOption,
  type LogFilterState,
  type LogRowView,
  type UsageLogSummaryPayload,
} from '@/api/logs'
import LogsCaptureControl from './logs/LogsCaptureControl.vue'
import LogsKpis from './logs/LogsKpis.vue'
import LogsLive from './logs/LogsLive.vue'
import LogsToolbar from './logs/LogsToolbar.vue'
import LogsTable from './logs/LogsTable.vue'
import LogsAccountSummary from './logs/LogsAccountSummary.vue'
import LogsDetailDrawer from './logs/LogsDetailDrawer.vue'

const { t } = useI18n()
const toast = useToast()
const platform = usePlatformStore()

const helpText = computed(() => t('page.logs.help'))

const table = useTableQuery('logs', {
  pageSize: 20,
  filters: {
    platform: '',
    account: '',
    account_id: '',
    model: '',
    key: '',
    result: '',
    ip: '',
    range: 'today',
    start: '',
    end: '',
    fast: '',
  },
})

function currentFilters(): LogFilterState {
  const f = table.state.filters
  return {
    platform: f.platform || '',
    account: f.account || '',
    account_id: f.account_id || '',
    model: f.model || '',
    key: f.key || '',
    result: f.result || '',
    ip: f.ip || '',
    range: f.range || 'today',
    start: f.start || '',
    end: f.end || '',
    fast: f.fast || '',
  }
}

const filters = computed<LogFilterState>(() => currentFilters())

interface ErrorState {
  message: string
  detail: string
}

function toErrorState(error: unknown): ErrorState {
  let detail = ''
  if (error instanceof ApiError && error.detail) {
    try {
      detail = typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail, null, 2)
    } catch {
      detail = String(error.detail)
    }
  }
  return { message: errorMessage(error), detail }
}

/* ------------------------------------------------------------------ *
 * 筛选选项
 * ------------------------------------------------------------------ */

const RANGE_KEYS: Record<string, MessageKey> = {
  today: 'page.logs.range.today',
  hour: 'page.logs.range.hour',
  '1': 'page.logs.range.d1',
  '3': 'page.logs.range.d3',
  '7': 'page.logs.range.d7',
  '30': 'page.logs.range.d30',
  '0': 'page.logs.range.all',
}

const rangeOptions = computed(() =>
  LOG_RANGE_OPTIONS.map((item) => ({ label: RANGE_KEYS[item.value] ? t(RANGE_KEYS[item.value]) : item.label, value: item.value })),
)

const rangeLabel = computed(() => {
  const f = filters.value
  if (f.start || f.end) return (f.start || '…') + ' ~ ' + (f.end || '…')
  return rangeOptions.value.find((item) => item.value === f.range)?.label || f.range
})

const platformOptions = computed(() => {
  const seen = new Set<string>()
  const options = [{ label: t('page.logs.filter.allPlatforms'), value: '' }]
  for (const item of platform.platforms) {
    const value = String(item.storage_platform || item.id || '')
    const key = value.toLowerCase()
    if (!key || seen.has(key)) continue
    seen.add(key)
    options.push({ label: item.label || value, value })
  }
  return options
})

function platformLabel(value: string): string {
  if (!value) return t('page.logs.filter.allPlatforms')
  return (
    platformOptions.value.find((item) => item.value.toLowerCase() === String(value).toLowerCase())?.label || value
  )
}

const accounts = ref<LogAccountOption[]>([])
const accountsLoading = ref(false)

const accountOptions = computed(() => {
  const options = [{ label: t('page.logs.filter.allAccounts', { count: accounts.value.length }), value: '' }]
  for (const item of accounts.value) {
    const suffix =
      item.requests == null
        ? ''
        : t('page.logs.filter.accountSuffix', { requests: fmtInt(item.requests), credits: fmtCredit(item.credits) })
    options.push({ label: item.name + suffix, value: String(item.id) })
  }
  // 深链接带进来的 account_id 不在窗口内：补一项，下拉不至于显示成裸数字
  const selected = filters.value.account_id
  if (selected && !options.some((item) => item.value === selected)) {
    options.push({ label: '#' + selected, value: selected })
  }
  return options
})

function accountDays(): number {
  const f = currentFilters()
  if (f.start) return 0
  if (f.range === 'today' || f.range === 'hour') return 1
  const days = Number(f.range)
  return Number.isFinite(days) ? days : 0
}

let accountsController: AbortController | null = null
async function loadAccounts(): Promise<void> {
  accountsController?.abort()
  const controller = new AbortController()
  accountsController = controller
  accountsLoading.value = true
  try {
    const payload = await listLogAccounts(accountDays(), true, controller.signal)
    if (controller.signal.aborted) return
    accounts.value = payload.items || []
  } catch (error) {
    if (isAbortError(error)) return
    accounts.value = []
  } finally {
    if (accountsController === controller) {
      accountsController = null
      accountsLoading.value = false
    }
  }
}

/* ------------------------------------------------------------------ *
 * 列表
 * ------------------------------------------------------------------ */

const rows = ref<LogRowView[] | null>(null)
const listLoading = ref(false)
/** 当前这次列表请求是否静默刷新（第 5 轮，配合 DataTable `refreshing`）。 */
const listSilent = ref(false)
const listError = ref<ErrorState | null>(null)
const hasMore = ref(false)
const totalKnown = ref<number | null>(null)
const cursors = new Map<number, number | null>([[1, null]])
let listController: AbortController | null = null

function resetCursors(): void {
  cursors.clear()
  cursors.set(1, null)
}

async function loadList(options: { silent?: boolean } = {}): Promise<void> {
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  listLoading.value = true
  // 第 5 轮：静默刷新（回访 / SSE 推送 / 兜底轮询）→ 表格走 refreshing，不出顶部进度条。
  listSilent.value = Boolean(options.silent)
  if (!options.silent) listError.value = null
  const page = table.state.page
  const beforeId = cursors.get(page) ?? null
  const { query } = buildLogQuery(currentFilters(), { page, pageSize: table.state.pageSize, beforeId })
  try {
    const payload = await listUsageLogs(query, controller.signal)
    if (controller.signal.aborted) return
    rows.value = toLogRows(payload.items)
    hasMore.value = Boolean(payload.has_more)
    if (payload.next_before_id) cursors.set(page + 1, payload.next_before_id)
    if (payload.total != null) totalKnown.value = payload.total
    else if (page === 1) totalKnown.value = null
  } catch (error) {
    if (isAbortError(error)) return
    listError.value = toErrorState(error)
    if (rows.value !== null) toast.error(failureText(t('page.logs.action.refreshList'), error))
  } finally {
    if (listController === controller) {
      listController = null
      listLoading.value = false
    }
  }
}

const pagerInfo = computed(() => {
  let text = t('page.logs.pager.page', { page: table.state.page })
  if (totalKnown.value != null) text += t('page.logs.pager.total', { total: fmtInt(totalKnown.value) })
  else if (table.state.filters.fast === '1') text += t('page.logs.pager.fast')
  else if (!hasMore.value) text += t('page.logs.pager.end')
  return text
})

/** 工具栏右侧的计数：第一页有总数时「共 N 条」，否则「第 N 页」。 */
const countText = computed(() => {
  if (totalKnown.value != null) return t('page.logs.toolbar.total', { total: fmtInt(totalKnown.value) })
  if (rows.value === null) return ''
  return t('page.logs.pager.page', { page: table.state.page })
})

const filtered = computed(() => {
  const f = filters.value
  return Boolean(
    f.platform || f.account || f.account_id || f.model || f.key || f.result || f.ip || f.start || f.end ||
      (f.range && f.range !== 'today'),
  )
})

const lastSeenText = computed(() => {
  const first = rows.value?.[0]
  return first?.created_at ? fmtRelative(first.created_at) : ''
})

/* ------------------------------------------------------------------ *
 * 汇总（KPI + 按账号汇总共用一次请求）
 * ------------------------------------------------------------------ */

const summary = ref<UsageLogSummaryPayload | null>(null)
const summaryLoading = ref(false)
const summaryError = ref<ErrorState | null>(null)
let summaryController: AbortController | null = null

const kpis = computed(() => summaryKpis(summary.value))

async function loadSummary(): Promise<void> {
  summaryController?.abort()
  const controller = new AbortController()
  summaryController = controller
  summaryLoading.value = true
  summaryError.value = null
  try {
    const payload = await getUsageSummary(buildSummaryQuery(currentFilters()), controller.signal)
    if (controller.signal.aborted) return
    summary.value = payload
  } catch (error) {
    if (isAbortError(error)) return
    summaryError.value = toErrorState(error)
  } finally {
    if (summaryController === controller) {
      summaryController = null
      summaryLoading.value = false
    }
  }
}

function applyAccountFilter(item: AccountSummaryRow): void {
  if (!item.account_id) {
    toast.warning(t('page.logs.toast.noAccountId'))
    return
  }
  table.setFilters({ account_id: String(item.account_id), account: '' })
}

/* ------------------------------------------------------------------ *
 * 请求动态（轮询：暂停 / 恢复 / 页面隐藏暂停 / 连续失败 3 次自动暂停）
 * ------------------------------------------------------------------ */

const live = ref<LiveRequestsPayload | null>(null)
const liveError = ref('')
const liveUserPaused = ref(false)

const liveBars = computed(() => buildLiveBars(live.value?.recent, Date.now(), 15))
const liveRecent = computed(() => (live.value?.recent || []).slice(0, 5))

const livePolling = usePolling(
  async () => {
    try {
      const payload = await getLiveRequests({ limit: 40, includeHistory: true })
      live.value = payload
      liveError.value = ''
    } catch (error) {
      liveError.value = errorMessage(error)
      throw error
    }
  },
  5000,
  { immediate: true, pauseWhenHidden: true, maxFailures: 3 },
)

const liveHint = computed(() => {
  if (liveUserPaused.value) return t('page.logs.live.pausedByUser')
  if (livePolling.paused.value) return t('page.logs.live.pausedAuto')
  if (liveError.value) return t('page.logs.live.error', { error: liveError.value })
  if (!live.value) return t('page.logs.live.loading')
  const total = (live.value.recent || []).length
  const running = Number(live.value.active_count || 0)
  return running > 0
    ? t('page.logs.live.hintRunning', { running, total })
    : t('page.logs.live.hint', { total })
})

function toggleLive(): void {
  if (liveUserPaused.value || livePolling.paused.value) {
    liveUserPaused.value = false
    livePolling.resume()
    return
  }
  liveUserPaused.value = true
  livePolling.stop()
}

/* ------------------------------------------------------------------ *
 * 表格新行推送（SSE /api/logs/usage/stream；失败退化为轮询）
 * ------------------------------------------------------------------ */

let usageSource: EventSource | null = null
let usageReconnect: ReturnType<typeof setTimeout> | null = null
let usageRevision: number | null = null
let usageBusy = false
let usageFailures = 0

const usageFallback = usePolling(
  async () => {
    const payload = await getLiveRequests({ includeHistory: false })
    if (payload.usage_revision == null) return
    if (usageRevision === null) {
      usageRevision = payload.usage_revision
      return
    }
    if (payload.usage_revision !== usageRevision) {
      usageRevision = payload.usage_revision
      refreshFromStream()
    }
  },
  8000,
  { immediate: false, pauseWhenHidden: true, maxFailures: 3 },
)

/** r4：页面被 KeepAlive 缓存中（离开）。SSE 已关；后台来的变化只记 dirty，回来再刷。 */
let pageInactive = false
let dirtyWhileAway = false

function refreshFromStream(): void {
  if (pageInactive) {
    dirtyWhileAway = true
    return
  }
  if (typeof document !== 'undefined' && document.hidden) return
  if (usageBusy) return
  // 只有第一页需要自动刷新：后面的页按 keyset 游标取，插入新行会让页码错位
  if (table.state.page !== 1) return
  usageBusy = true
  void Promise.all([loadList({ silent: true }), loadSummary()]).finally(() => {
    usageBusy = false
  })
}

function closeUsageStream(): void {
  if (usageReconnect) {
    clearTimeout(usageReconnect)
    usageReconnect = null
  }
  if (usageSource) {
    try {
      usageSource.close()
    } catch {
      /* 已关闭 */
    }
    usageSource = null
  }
  usageFallback.stop()
}

async function openUsageStream(): Promise<void> {
  closeUsageStream()
  if (typeof EventSource === 'undefined') {
    usageFallback.start()
    return
  }
  let ticket = ''
  try {
    const granted = await requestStreamTicket()
    ticket = granted.ticket || ''
  } catch {
    /* 拿不到票就靠同源 Cookie */
  }
  // 取票期间离开了页面（KeepAlive 缓存）：不再开流，回来时 onActivated 会重开
  if (pageInactive) return
  let source: EventSource
  try {
    source = new EventSource('/api/logs/usage/stream' + (ticket ? '?ticket=' + encodeURIComponent(ticket) : ''))
  } catch {
    usageFallback.start()
    return
  }
  usageSource = source
  source.addEventListener('usage', () => {
    usageFailures = 0
    refreshFromStream()
  })
  source.addEventListener('reauth', () => {
    closeUsageStream()
    usageReconnect = setTimeout(() => {
      void openUsageStream()
    }, 500)
  })
  source.addEventListener('open', () => {
    usageFailures = 0
  })
  source.onerror = () => {
    usageFailures += 1
    if (source.readyState === EventSource.CLOSED) {
      closeUsageStream()
      if (usageFailures >= 3) {
        usageFallback.start()
        return
      }
      usageReconnect = setTimeout(() => {
        void openUsageStream()
      }, 3000)
    }
  }
}

/* ------------------------------------------------------------------ *
 * 导出
 * ------------------------------------------------------------------ */

const exporting = ref<'' | 'csv' | 'diagnostics'>('')

const exportOptions = computed(() => [
  { label: t('page.logs.action.exportCsv'), key: 'csv' },
  { label: t('page.logs.action.exportDiagnostics') + ' · ' + t('page.logs.exportDiagHint'), key: 'diagnostics' },
])

async function exportLogs(diagnostics = false): Promise<void> {
  if (exporting.value) return
  exporting.value = diagnostics ? 'diagnostics' : 'csv'
  toast.info(t('page.logs.toast.exporting'))
  try {
    const filename = await downloadLogsExport(buildExportQuery(currentFilters()), diagnostics)
    toast.success(t('page.logs.toast.exported', { name: filename }))
  } catch (error) {
    toast.error(failureText(t('page.logs.action.export'), error))
  } finally {
    exporting.value = ''
  }
}

function onExportSelect(key: string | number): void {
  void exportLogs(key === 'diagnostics')
}

/* ------------------------------------------------------------------ *
 * 详情抽屉
 * ------------------------------------------------------------------ */

const detailOpen = ref(false)
const detailRow = ref<LogRowView | null>(null)

function openDetail(row: LogRowView): void {
  detailRow.value = row
  detailOpen.value = true
}

/* ------------------------------------------------------------------ *
 * 筛选 ↔ 路由
 * ------------------------------------------------------------------ */

function setFilter(key: keyof LogFilterState, value: string): void {
  // 账号下拉与账号名两个入口互斥（与后端 account_id 优先一致）
  if (key === 'account_id' && value) {
    table.setFilters({ account_id: value, account: '' })
    return
  }
  table.setFilter(key, value)
}

function setFilters(values: Partial<LogFilterState>): void {
  const next: Record<string, string> = {}
  for (const [key, value] of Object.entries(values)) next[key] = String(value ?? '')
  table.setFilters(next)
}

function resetFilters(): void {
  table.reset()
}

function refreshAll(): void {
  void loadList()
  void loadSummary()
}

const querySignature = computed(() =>
  JSON.stringify({ filters: table.state.filters, page: table.state.page, size: table.state.pageSize }),
)
const filterSignature = computed(() => JSON.stringify(table.state.filters))
let lastSignature = ''
let lastFilterSignature = ''

watch(
  querySignature,
  () => {
    if (querySignature.value === lastSignature) return
    lastSignature = querySignature.value
    if (table.state.page === 1) resetCursors()
    void loadList()
    // 汇总与账号下拉只跟筛选有关：翻页不重取
    if (filterSignature.value !== lastFilterSignature) {
      lastFilterSignature = filterSignature.value
      void loadSummary()
      void loadAccounts()
    }
  },
  { immediate: true },
)

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let stopPlatformWatch: (() => void) | null = null
let onVisibility: (() => void) | null = null
let platformChangedWhileAway = false


// 二次进入（KeepAlive 命中）：保留旧数据、不出骨架，后台静默刷新（11-polish-motion §2）。
useRevisit(
  () => {
    dirtyWhileAway = false
    void loadList({ silent: true })
    void loadSummary()
  },
  { minIntervalMs: 10_000 },
)

// 注意顺序：必须注册在 useRevisit 之后 —— 回来时 useRevisit 先刷新并清掉 dirty，这里就不再重复请求。
/*
 * r4（P1）：KeepAlive 下页面离开不卸载，原来 SSE 一直开着，每条新用量都在后台重拉列表 + 汇总。
 * 离开：关 SSE（连同兜底轮询与重连定时器）；回来：重开 SSE，离开期间有变化（新用量 / 切平台）时
 * 立即补一次刷新 —— 不受 useRevisit 的 10s 最小间隔限制，否则 10s 内回来会看到旧数据。
 */
onDeactivated(() => {
  pageInactive = true
  closeUsageStream()
})
onActivated(() => {
  if (!pageInactive) return
  pageInactive = false
  void openUsageStream()
  if (platformChangedWhileAway) {
    platformChangedWhileAway = false
    void loadAccounts()
  }
  if (dirtyWhileAway) {
    dirtyWhileAway = false
    refreshFromStream()
  }
})

onMounted(() => {
  livePolling.start()
  void openUsageStream()
  stopPlatformWatch = platform.onChange(() => {
    // 缓存中切了平台：不在后台连发 3 个请求，回到页面时再重取（见 onActivated）。
    if (pageInactive) {
      dirtyWhileAway = true
      platformChangedWhileAway = true
      return
    }
    // 日志页有自己的平台筛选（默认全部平台）；切换顶栏平台只按旧版行为重取当前视图。
    void loadList({ silent: true })
    void loadSummary()
    void loadAccounts()
  })
  if (typeof document !== 'undefined') {
    onVisibility = () => {
      if (!document.hidden) refreshFromStream()
    }
    document.addEventListener('visibilitychange', onVisibility)
  }
})

onBeforeUnmount(() => {
  listController?.abort()
  summaryController?.abort()
  accountsController?.abort()
  closeUsageStream()
  livePolling.stop()
  stopPlatformWatch?.()
  stopPlatformWatch = null
  if (onVisibility) document.removeEventListener('visibilitychange', onVisibility)
  onVisibility = null
})
</script>

<template>
  <div class="logs-page mh-page mh-page--wide">
    <PageHeader :title="t('page.logs.title')" :help="helpText">
      <template #actions>
        <LogsCaptureControl />
        <NDropdown trigger="click" placement="bottom-end" :options="exportOptions" @select="onExportSelect">
          <Btn variant="default" :icon="Download" :loading="Boolean(exporting)" :disabled="Boolean(exporting)">
            {{ t('page.logs.action.export') }}
            <ChevronDown :size="14" />
          </Btn>
        </NDropdown>
        <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="listLoading" @click="refreshAll" />
      </template>
    </PageHeader>

    <LogsKpis
      :kpis="kpis"
      :loading="summaryLoading"
      :range-label="rangeLabel"
      :platform-scoped="Boolean(filters.platform)"
    />

    <LogsLive
      :bars="liveBars"
      :recent="liveRecent"
      :hint="liveHint"
      :auto-paused="livePolling.paused.value"
      :auto-paused-message="livePolling.pausedMessage.value"
      :user-paused="liveUserPaused"
      :loading="livePolling.running.value && !live"
      :last-seen-text="lastSeenText"
      @toggle="toggleLive"
    />

    <LogsTable
      :rows="rows"
      :loading="listLoading && !listSilent"
      :refreshing="listLoading && listSilent"
      :error="listError"
      :page="table.state.page"
      :has-more="hasMore"
      :pager-info="pagerInfo"
      :platform-label="platformLabel"
      :filtered="filtered"
      @open="openDetail"
      @page="(page: number) => table.setPage(page)"
      @retry="loadList()"
      @reset="resetFilters"
    >
      <template #toolbar>
        <LogsToolbar
          :filters="filters"
          :platform-options="platformOptions"
          :account-options="accountOptions"
          :range-options="rangeOptions"
          :accounts-loading="accountsLoading"
          :count-text="countText"
          @set="setFilter"
          @set-many="setFilters"
          @reset="resetFilters"
        />
      </template>
    </LogsTable>

    <LogsAccountSummary
      :summary="summary"
      :loading="summaryLoading"
      :error="summaryError"
      @pick="applyAccountFilter"
      @retry="loadSummary()"
    />

    <LogsDetailDrawer v-model:show="detailOpen" :row="detailRow" :platform-label="platformLabel" />
  </div>
</template>

<style scoped>
.logs-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
</style>
