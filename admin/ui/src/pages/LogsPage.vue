<script setup lang="ts">
/**
 * S15 用量日志 /logs（模板 B：PageHeader + FilterBar + 汇总 + 请求动态 + DataTable + DetailDrawer）
 *
 * 契约：docs/ui-v2/contracts/logs.md（末尾「实现对照」是本步交付）
 * 旧版：#panel-logs（admin/console/index.html 2223–2338）+ console-app.js 15990–17172
 * 后端：admin/routers/logs.py（list_logs / log_summary / log_accounts / log_detail / capture_* / export）
 *
 * 关键事实（均已对照后端）：
 *   · 列表支持 keyset（before_id）；深翻页只走主键索引。快速翻页关闭总数统计。
 *   · 请求详情是进程内缓存（admin/request_payload.py），正文按 64 KiB 分页读，不进数据库。
 *   · log_summary **不接 platform**（logs.py:1128）—— 按账号汇总按全网关口径，平台筛选只作用于列表（旧版相同）。
 */
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch, type VNodeChild } from 'vue'
import { NCheckbox, NDatePicker, NInput, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import { ChevronLeft, ChevronRight, Copy, Download, Pause, Play, RefreshCw, Trash2 } from 'lucide-vue-next'
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
  HelpTip,
  KV,
  Meter,
  Section,
  type FilterChip,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useCopy, usePolling, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import { FMT_EMPTY, fmtCredit, fmtDuration, fmtFullTime, fmtInt, fmtTime } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import {
  LOG_RANGE_OPTIONS,
  LOG_RESULT_OPTIONS,
  buildCurl,
  buildExportQuery,
  buildLiveBars,
  buildLogQuery,
  buildSummaryQuery,
  captureStatusText,
  clearCaptureCache,
  downloadLogCapture,
  downloadLogsExport,
  getCaptureBody,
  getCaptureSettings,
  getLiveRequests,
  getUsageLog,
  getUsageSummary,
  listLogAccounts,
  listUsageLogs,
  liveHintText,
  liveStatusLabel,
  liveTone,
  requestStreamTicket,
  setCaptureEnabled,
  summaryHintText,
  summaryStatText,
  toAccountSummaryRows,
  toLogRow,
  toLogRows,
  type AccountSummaryRow,
  type CaptureBodyPage,
  type CaptureSettings,
  type LiveRequest,
  type LiveRequestsPayload,
  type LogAccountOption,
  type LogFilterState,
  type LogRowView,
  type UsageLogDetail,
  type UsageLogRow,
  type UsageLogSummaryPayload,
} from '@/api/logs'

const { t } = useI18n()

/** 旧版 #panel-logs 页头说明（收进 ⓘ）。 */
const helpText = computed(() => t('page.logs.help'))

const captureHelp = computed(() => t('page.logs.captureHelp'))

const exportDiagHint = computed(() => t('page.logs.exportDiagHint'))

const toast = useToast()
const confirm = useConfirm()
const { copy } = useCopy()
const platform = usePlatformStore()

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

const FILTER_KEYS = ['platform', 'account', 'account_id', 'model', 'key', 'result', 'ip', 'range', 'start', 'end', 'fast']

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
 * 平台 / 账号筛选选项
 * ------------------------------------------------------------------ */

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
  return platformOptions.value.find((item) => item.value === value)?.label || value
}

const accounts = ref<LogAccountOption[]>([])
const accountsLoading = ref(false)

const accountOptions = computed(() => {
  const options = [
    { label: t('page.logs.filter.allAccounts', { count: accounts.value.length }), value: '' },
  ]
  for (const item of accounts.value) {
    const suffix =
      item.requests == null
        ? ''
        : t('page.logs.filter.accountSuffix', {
            requests: fmtInt(item.requests),
            credits: fmtCredit(item.credits),
          })
    options.push({ label: item.name + suffix, value: String(item.id) })
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
const listError = ref<ErrorState | null>(null)
const hasMore = ref(false)
const totalKnown = ref<number | null>(null)
const cursors = new Map<number, number | null>([[1, null]])
let listController: AbortController | null = null

const firstLoading = computed(() => listLoading.value && rows.value === null)

function resetCursors(): void {
  cursors.clear()
  cursors.set(1, null)
}

async function loadList(options: { silent?: boolean } = {}): Promise<void> {
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  listLoading.value = true
  if (!options.silent) listError.value = null
  const page = table.state.page
  const beforeId = cursors.get(page) ?? null
  const { query } = buildLogQuery(currentFilters(), {
    page,
    pageSize: table.state.pageSize,
    beforeId,
  })
  try {
    const payload = await listUsageLogs(query, controller.signal)
    if (controller.signal.aborted) return
    rows.value = toLogRows(payload.items)
    hasMore.value = Boolean(payload.has_more)
    if (payload.next_before_id) cursors.set(page + 1, payload.next_before_id)
    if (payload.total != null) totalKnown.value = payload.total
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

/* ------------------------------------------------------------------ *
 * 按账号汇总
 * ------------------------------------------------------------------ */

const summary = ref<UsageLogSummaryPayload | null>(null)
const summaryLoading = ref(false)
const summaryError = ref<ErrorState | null>(null)
const summaryOpen = ref(true)
let summaryController: AbortController | null = null

const summaryRows = computed<AccountSummaryRow[]>(() => toAccountSummaryRows(summary.value?.items))
const maxCredits = computed(() => Math.max(1, ...summaryRows.value.map((row) => row.credits)))

async function loadSummary(): Promise<void> {
  if (!summaryOpen.value) return
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

function toggleSummary(): void {
  summaryOpen.value = !summaryOpen.value
  if (summaryOpen.value) void loadSummary()
}

function applyAccountFilter(row: TableRow): void {
  const item = row as unknown as AccountSummaryRow
  if (!item.account_id) {
    toast.warning(t('page.logs.toast.noAccountId'))
    return
  }
  table.setFilter('account_id', String(item.account_id))
}

/* ------------------------------------------------------------------ *
 * 请求动态（轮询：暂停 / 恢复 / 页面隐藏暂停 / 连续失败 3 次自动暂停）
 * ------------------------------------------------------------------ */

const live = ref<LiveRequestsPayload | null>(null)
const liveError = ref('')
const liveUserPaused = ref(false)

const liveBars = computed(() => buildLiveBars(live.value?.recent, Date.now(), 15))
const liveMax = computed(() => Math.max(1, ...liveBars.value.map((bar) => bar.requests)))
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
  if (livePolling.paused.value) return livePolling.pausedMessage.value
  if (liveError.value) return t('page.logs.live.error', { error: liveError.value })
  return liveHintText(live.value, 15)
})

function toggleLive(): void {
  if (liveUserPaused.value) {
    liveUserPaused.value = false
    livePolling.resume()
    return
  }
  liveUserPaused.value = true
  livePolling.stop()
}

/* ------------------------------------------------------------------ *
 * 表格新行推送（SSE /api/logs/usage/stream，旧版同款；失败退化为轮询）
 * ------------------------------------------------------------------ */

let usageSource: EventSource | null = null
let usageReconnect: ReturnType<typeof setTimeout> | null = null
let usageRevision: number | null = null
let usageBusy = false
let usageFailures = 0

const usageFallback = usePolling(
  async () => {
    const payload = await getLiveRequests({ includeHistory: false })
    if (
      usageRevision !== null &&
      payload.usage_revision != null &&
      payload.usage_revision !== usageRevision
    ) {
      usageRevision = payload.usage_revision
      refreshFromStream()
    }
  },
  8000,
  { immediate: false, pauseWhenHidden: true, maxFailures: 3 },
)

function refreshFromStream(): void {
  if (typeof document !== 'undefined' && document.hidden) return
  if (usageBusy) return
  usageBusy = true
  void loadList({ silent: true }).finally(() => {
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
 * 请求详情记录（内存缓存）
 * ------------------------------------------------------------------ */

const capture = ref<CaptureSettings | null>(null)
const captureLoading = ref(false)
const captureError = ref('')
const captureBusy = ref(false)

async function loadCapture(): Promise<void> {
  captureLoading.value = true
  captureError.value = ''
  try {
    capture.value = await getCaptureSettings()
  } catch (error) {
    captureError.value = errorMessage(error)
  } finally {
    captureLoading.value = false
  }
}

async function toggleCapture(enabled: boolean): Promise<void> {
  if (captureBusy.value) return
  captureBusy.value = true
  try {
    capture.value = await setCaptureEnabled(enabled)
    toast.success(
      enabled ? t('page.logs.toast.captureOn') : t('page.logs.toast.captureOff'),
    )
  } catch (error) {
    toast.error(
      failureText(
        enabled ? t('page.logs.action.enableCapture') : t('page.logs.action.disableCapture'),
        error,
      ),
    )
  } finally {
    captureBusy.value = false
    void loadCapture()
  }
}

async function clearCapture(): Promise<void> {
  const confirmed = await confirm({
    title: t('page.logs.confirmClear.title'),
    message: t('page.logs.confirmClear.body'),
    okText: t('page.logs.confirmClear.ok'),
    danger: true,
  })
  if (!confirmed) return
  captureBusy.value = true
  try {
    capture.value = await clearCaptureCache()
    toast.success(t('page.logs.toast.cacheCleared'))
  } catch (error) {
    toast.error(failureText(t('page.logs.action.clearCache'), error))
  } finally {
    captureBusy.value = false
  }
}

const captureText = computed(() => captureStatusText(capture.value))

/* ------------------------------------------------------------------ *
 * 导出
 * ------------------------------------------------------------------ */

const exporting = ref<'' | 'csv' | 'diagnostics'>('')

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

/* ------------------------------------------------------------------ *
 * 详情抽屉
 * ------------------------------------------------------------------ */

const detailOpen = ref(false)
const detailRow = ref<LogRowView | null>(null)
const detail = ref<UsageLogDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref<ErrorState | null>(null)
const ingressText = ref('')
const ingressPage = ref<CaptureBodyPage | null>(null)
const upstreamBodies = reactive<Record<number, { text: string; page: CaptureBodyPage | null; loading: boolean }>>({})
let detailController: AbortController | null = null

async function openDetail(row: TableRow): Promise<void> {
  const item = row as unknown as LogRowView
  detailRow.value = item
  detailOpen.value = true
  detail.value = null
  detailError.value = null
  ingressText.value = ''
  ingressPage.value = null
  for (const key of Object.keys(upstreamBodies)) delete upstreamBodies[Number(key)]
  detailController?.abort()
  const controller = new AbortController()
  detailController = controller
  detailLoading.value = true
  try {
    const payload = await getUsageLog(item.id, controller.signal)
    if (controller.signal.aborted) return
    detail.value = payload
    if (payload.request_capture) await loadIngress(payload.id, 0)
  } catch (error) {
    if (isAbortError(error)) return
    detailError.value = toErrorState(error)
  } finally {
    if (detailController === controller) {
      detailController = null
      detailLoading.value = false
    }
  }
}

async function loadIngress(logId: number, offset: number): Promise<void> {
  try {
    const page = await getCaptureBody(logId, 'ingress', offset)
    ingressPage.value = page
    ingressText.value = offset === 0 ? page.text : ingressText.value + page.text
  } catch (error) {
    toast.error(failureText(t('page.logs.action.readIngress'), error))
  }
}

async function loadUpstream(index: number, offset = 0): Promise<void> {
  const logId = detail.value?.id
  if (!logId) return
  const current = upstreamBodies[index] || { text: '', page: null, loading: false }
  upstreamBodies[index] = { ...current, loading: true }
  try {
    const page = await getCaptureBody(logId, 'upstream:' + index, offset)
    upstreamBodies[index] = {
      text: offset === 0 ? page.text : current.text + page.text,
      page,
      loading: false,
    }
  } catch (error) {
    upstreamBodies[index] = { ...current, loading: false }
    toast.error(failureText(t('page.logs.action.readUpstream'), error))
  }
}

const curlText = computed(() => (detail.value ? buildCurl(detail.value, ingressText.value) : ''))
const ingressCost = computed(() => {
  const capture = detail.value?.request_capture
  if (!capture) return ''
  const cached = Number(capture.ingress.body_cached_bytes || 0)
  const full = Number(capture.ingress.body_bytes || 0)
  return (
    t('page.logs.detail.ingressCost', { cached: fmtInt(cached), full: fmtInt(full) }) +
    (capture.ingress.body_truncated ? t('page.logs.detail.ingressTruncated') : '')
  )
})

const detailView = computed(() => (detail.value ? toLogRow(detail.value) : null))

const detailKv = computed(() => {
  const item = detailView.value
  if (!item) return []
  return [
    { label: t('page.logs.detail.requestId'), value: item.id, mono: true },
    { label: t('page.logs.detail.time'), value: fmtFullTime(item.created_at) },
    { label: t('page.logs.detail.platform'), value: platformLabel(item.platform) },
    { label: t('page.logs.detail.account'), value: item.account_name || FMT_EMPTY },
    { label: 'Key', value: item.key_name || FMT_EMPTY },
    { label: t('page.logs.detail.model'), value: item.model || FMT_EMPTY, mono: true },
    { label: t('page.logs.detail.useCase'), value: item.use_case || FMT_EMPTY, mono: true },
    {
      label: t('page.logs.detail.result'),
      value: item.isError ? item.error_kind || t('page.logs.detail.failed') : t('page.logs.detail.success'),
    },
    {
      label: 'Token',
      value: t('page.logs.detail.tokenBreakdown', {
        input: fmtInt(item.prompt_tokens),
        output: fmtInt(item.completion_tokens),
        cached: fmtInt(item.cached_tokens),
        total: fmtInt(item.total_tokens),
      }),
    },
    { label: t('page.logs.detail.cacheHit'), value: item.cacheRateText },
    { label: t('page.logs.detail.credits'), value: fmtCredit(item.credits) },
    { label: t('page.logs.detail.ttfb'), value: item.ttfbText },
    { label: t('page.logs.detail.latency'), value: item.latencyText },
    { label: 'TPS', value: item.tpsText },
    { label: t('page.logs.detail.clientIp'), value: item.client_ip || FMT_EMPTY, mono: true },
    { label: t('page.logs.detail.seq'), value: item.seq ?? FMT_EMPTY },
    {
      label: t('page.logs.detail.reasoning'),
      value: item.reasoning_effort || t('page.logs.detail.reasoningDefault'),
    },
  ]
})

const diagnosticJson = computed(() =>
  detail.value?.diagnostic ? JSON.stringify(detail.value.diagnostic, null, 2) : '',
)

async function copyCurl(): Promise<void> {
  if (!curlText.value) {
    toast.warning(t('page.logs.toast.noCurl'))
    return
  }
  const ok = await copy(curlText.value)
  if (ok) toast.success(t('page.logs.toast.curlCopied'))
  else toast.error(t('common.copyFailed'))
}

async function downloadCapture(): Promise<void> {
  const id = detail.value?.id
  if (!id) return
  try {
    const filename = await downloadLogCapture(id)
    toast.success(t('page.logs.toast.downloaded', { name: filename }))
  } catch (error) {
    toast.error(failureText(t('page.logs.action.downloadJsonl'), error))
  }
}

/* ------------------------------------------------------------------ *
 * 列定义
 * ------------------------------------------------------------------ */

function truncate(text: string, max: number): string {
  const value = String(text || '')
  return value.length > max ? value.slice(0, max) + '…' : value
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'time',
    title: t('page.logs.col.time'),
    mobile: 'title',
    width: 96,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('span', { class: 'logs-time tnum', title: item.timeFull }, item.timeShort)
    },
  },
  {
    key: 'key_name',
    title: 'Key',
    mobile: 'meta',
    width: 120,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('span', { class: 'logs-mono', title: item.key_name }, truncate(item.key_name, 14))
    },
  },
  {
    key: 'model',
    title: t('page.logs.col.model'),
    mobile: 'subtitle',
    width: 190,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('div', { class: 'logs-model' }, [
        h('span', { class: 'logs-mono', title: item.model_raw || item.model }, truncate(item.model, 26)),
        h(
          'small',
          { class: 'logs-sub' },
          t('page.logs.col.reasoning', {
            value: item.reasoning_effort || t('page.logs.detail.reasoningDefault'),
          }),
        ),
      ])
    },
  },
  {
    key: 'platform_account',
    title: t('page.logs.col.platformAccount'),
    mobile: 'meta',
    width: 180,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('div', { class: 'logs-stack' }, [
        h(Badge, { tone: item.platform ? 'neutral' : 'neutral' }, { default: () => platformLabel(item.platform) }),
        h('span', { class: 'logs-account', title: item.account_name }, truncate(item.account_name, 16)),
      ])
    },
  },
  {
    key: 'use_case',
    title: t('page.logs.col.useCase'),
    mobile: 'hidden',
    width: 100,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('span', { class: 'logs-sub', title: item.use_case || FMT_EMPTY }, item.useCaseLabel)
    },
  },
  {
    key: 'result',
    title: t('page.logs.col.result'),
    mobile: 'meta',
    width: 104,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h(
        Badge,
        { tone: item.resultTone, dot: true, title: item.error_kind || 'success' },
        { default: () => item.resultLabel },
      )
    },
  },
  {
    key: 'tokens',
    title: t('page.logs.col.tokens'),
    mobile: 'meta',
    width: 190,
    numeric: true,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('div', { class: 'logs-tokens tnum' }, [
        h('span', {}, t('page.logs.col.tokenIn', { value: fmtInt(item.prompt_tokens) })),
        h('span', {}, t('page.logs.col.tokenOut', { value: fmtInt(item.completion_tokens) })),
        h('span', {}, t('page.logs.col.tokenCached', { value: fmtInt(item.cached_tokens) })),
      ])
    },
  },
  {
    key: 'cache_rate',
    title: t('page.logs.col.cacheRate'),
    mobile: 'meta',
    width: 84,
    numeric: true,
    render: (row: TableRow): VNodeChild =>
      h('span', { class: 'logs-sub tnum' }, (row as unknown as LogRowView).cacheRateText),
  },
  {
    key: 'credits',
    title: t('page.logs.col.credits'),
    mobile: 'meta',
    width: 88,
    numeric: true,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('span', { class: item.credits > 0 ? 'logs-credit tnum' : 'logs-sub tnum' },
        item.credits > 0 ? fmtCredit(item.credits) : FMT_EMPTY)
    },
  },
  {
    key: 'latency',
    title: t('page.logs.col.latency'),
    mobile: 'meta',
    width: 150,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('div', { class: 'logs-latency tnum' }, [
        h('span', {}, t('page.logs.col.ttfb', { value: item.ttfbText })),
        h('span', {}, t('page.logs.col.total', { value: item.latencyText })),
        item.tpsText !== FMT_EMPTY
          ? h('span', { class: 'logs-sub' }, t('page.logs.col.tps', { value: item.tpsText }))
          : null,
      ])
    },
  },
  {
    key: 'client_ip',
    title: t('page.logs.col.clientIp'),
    mobile: 'hidden',
    width: 128,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('span', { class: 'logs-mono', title: item.client_ip || FMT_EMPTY }, item.client_ip || FMT_EMPTY)
    },
  },
  {
    key: 'actions',
    title: t('page.logs.col.actions'),
    mobile: 'actions',
    width: 72,
    hideable: false,
    fixed: 'right',
    render: (row: TableRow): VNodeChild =>
      h('button', {
        type: 'button',
        class: 'logs-link',
        'aria-label': t('page.logs.col.detailAria'),
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          void openDetail(row)
        },
      }, t('page.logs.col.detail')),
  },
])

const summaryColumns = computed<TableColumn[]>(() => [
  { key: 'account_name', title: t('page.logs.summaryCol.account'), mobile: 'title', minWidth: 160 },
  {
    key: 'requests', title: t('page.logs.summaryCol.requests'), mobile: 'meta', width: 92, numeric: true,
    render: (row: TableRow) => h('span', { class: 'tnum' }, fmtInt((row as unknown as AccountSummaryRow).requests)),
  },
  {
    key: 'failed_requests', title: t('page.logs.summaryCol.failed'), mobile: 'meta', width: 72, numeric: true,
    render: (row: TableRow) => {
      const item = row as unknown as AccountSummaryRow
      return h('span', { class: item.failed_requests > 0 ? 'logs-credit tnum' : 'logs-sub tnum' }, fmtInt(item.failed_requests))
    },
  },
  {
    key: 'credits', title: t('page.logs.summaryCol.credits'), mobile: 'meta', width: 88, numeric: true,
    render: (row: TableRow) => h('span', { class: 'logs-credit tnum' }, fmtCredit((row as unknown as AccountSummaryRow).credits)),
  },
  {
    key: 'total_tokens', title: t('page.logs.summaryCol.tokens'), mobile: 'meta', width: 92, numeric: true,
    render: (row: TableRow) => h('span', { class: 'logs-sub tnum' }, fmtInt((row as unknown as AccountSummaryRow).total_tokens)),
  },
  {
    key: 'cache_rate', title: t('page.logs.summaryCol.cacheRate'), mobile: 'meta', width: 84, numeric: true,
    render: (row: TableRow) => h('span', { class: 'logs-sub tnum' }, (row as unknown as AccountSummaryRow).cacheRateText),
  },
  {
    key: 'avg_latency_ms', title: t('page.logs.summaryCol.avgLatency'), mobile: 'meta', width: 92, numeric: true,
    render: (row: TableRow) => h('span', { class: 'logs-sub tnum' }, (row as unknown as AccountSummaryRow).avgLatencyText),
  },
  {
    key: 'last_used_at', title: t('page.logs.summaryCol.lastUsed'), mobile: 'meta', width: 120,
    render: (row: TableRow) => h('span', { class: 'logs-time' }, (row as unknown as AccountSummaryRow).lastUsedText),
  },
  {
    key: 'share',
    title: t('page.logs.summaryCol.share'),
    mobile: 'meta',
    width: 130,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as AccountSummaryRow
      return h(Meter, {
        value: item.credits,
        max: maxCredits.value,
        tone: item.unattributed ? 'neutral' : 'brand',
        valueText: item.unattributed
          ? t('page.logs.summaryCol.unattributed')
          : Math.round((item.credits / maxCredits.value) * 100) + '%',
        showValue: true,
        ariaLabel: t('page.logs.summaryCol.share'),
      })
    },
  },
])

function summaryData(): TableRow[] {
  // 数值保持 number：积分占比的 Meter 需要数字；展示格式交给列 render。
  return summaryRows.value.map((row) => ({ ...row }))
}

/* ------------------------------------------------------------------ *
 * 筛选 / 重置 / 查询联动
 * ------------------------------------------------------------------ */

const chips = computed<FilterChip[]>(() => {
  const f = currentFilters()
  const list: FilterChip[] = []
  if (f.platform)
    list.push({ key: 'platform', label: t('page.logs.chip.platform'), value: platformLabel(f.platform) })
  if (f.account_id) {
    const account = accounts.value.find((item) => String(item.id) === f.account_id)
    list.push({
      key: 'account_id',
      label: t('page.logs.chip.account'),
      value: account?.name || '#' + f.account_id,
    })
  }
  if (f.account) list.push({ key: 'account', label: t('page.logs.chip.accountName'), value: f.account })
  if (f.model) list.push({ key: 'model', label: t('page.logs.chip.model'), value: f.model })
  if (f.key) list.push({ key: 'key', label: 'Key', value: f.key })
  if (f.ip) list.push({ key: 'ip', label: t('page.logs.chip.clientIp'), value: f.ip })
  if (f.result) {
    const label = LOG_RESULT_OPTIONS.find((item) => item.value === f.result)?.label
    list.push({ key: 'result', label: t('page.logs.chip.result'), value: label || f.result })
  }
  if (f.start || f.end) {
    list.push({
      key: 'start',
      label: t('page.logs.chip.date'),
      value: (f.start || '…') + ' ~ ' + (f.end || '…'),
    })
  } else {
    const range = LOG_RANGE_OPTIONS.find((item) => item.value === f.range)
    list.push({ key: 'range', label: t('page.logs.chip.time'), value: range?.label || f.range })
  }
  if (f.fast === '1')
    list.push({
      key: 'fast',
      label: t('page.logs.chip.fast'),
      value: t('page.logs.chip.fastValue'),
    })
  return list
})

const statText = computed(() => summaryStatText(summary.value))

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [key, value] of Object.entries(values)) next[key] = String(value ?? '')
  table.setFilters(next)
}

function removeChip(key: string): void {
  if (key === 'start') {
    table.setFilters({ start: '', end: '' })
    return
  }
  table.setFilter(key, '')
}

function resetFilters(): void {
  table.reset()
}

function setFilterValue(key: string, value: unknown): void {
  table.setFilter(key, value === null || value === undefined ? '' : String(value))
}

/* 账号筛选变化时同步清掉名称筛选（两个入口互斥，与后端 account_id 优先一致） */
watch(
  () => table.state.filters.account_id,
  (value) => {
    if (value && table.state.filters.account) table.setFilter('account', '')
  },
)

const querySignature = computed(() =>
  JSON.stringify({ filters: table.state.filters, page: table.state.page, size: table.state.pageSize }),
)
let lastSignature = ''

watch(
  querySignature,
  () => {
    if (querySignature.value === lastSignature) return
    lastSignature = querySignature.value
    if (table.state.page === 1) resetCursors()
    void loadList()
    void loadSummary()
    void loadAccounts()
  },
  { immediate: true },
)

/* ------------------------------------------------------------------ *
 * 生命周期：平台切换重取、卸载清理
 * ------------------------------------------------------------------ */

let stopPlatformWatch: (() => void) | null = null
let onVisibility: (() => void) | null = null

onMounted(() => {
  void loadCapture()
  livePolling.start()
  void openUsageStream()
  stopPlatformWatch = platform.onChange(() => {
    // 日志页有自己的平台筛选（默认全部平台）；切换平台只按旧版行为重取当前视图。
    void loadList({ silent: true })
    void loadSummary()
    void loadAccounts()
    void loadCapture()
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
  detailController?.abort()
  closeUsageStream()
  livePolling.stop()
  stopPlatformWatch?.()
  stopPlatformWatch = null
  if (onVisibility) document.removeEventListener('visibilitychange', onVisibility)
  onVisibility = null
})

function systemCols(): TableColumn[] {
  return columns.value
}

/* 让模板可引用常量 */
const RANGE_OPTIONS = LOG_RANGE_OPTIONS
const RESULT_OPTIONS = LOG_RESULT_OPTIONS
const FILTER_HELP = computed(() => t('page.logs.filterHelp'))

const pagerInfo = computed(() => {
  let text = t('page.logs.pager.page', { page: table.state.page })
  if (totalKnown.value != null) text += t('page.logs.pager.total', { total: fmtInt(totalKnown.value) })
  else if (table.state.filters.fast === '1') text += t('page.logs.pager.fast')
  else if (!hasMore.value) text += t('page.logs.pager.end')
  return text
})

const ingressLabel = computed(() =>
  ingressPage.value
    ? t('page.logs.detail.ingressRead', {
        read: fmtInt(ingressPage.value.next_offset),
        total: fmtInt(ingressPage.value.cached_bytes),
      })
    : t('page.logs.detail.ingressCollapsed'),
)

const detailTitle = computed(() =>
  detail.value
    ? t('page.logs.detail.titleOf', { model: detail.value.model || '' })
    : t('page.logs.detail.title'),
)
</script>

<template>
  <div class="logs-page">
    <PageHeader :title="t('page.logs.title')" :help="helpText">
      <template #actions>
        <Btn
          variant="quiet"
          :icon="Download"
          :loading="exporting === 'diagnostics'"
          :disabled="Boolean(exporting)"
          :title="exportDiagHint"
          @click="exportLogs(true)"
        >
          {{ t('page.logs.action.exportDiagnostics') }}
        </Btn>
        <Btn
          variant="quiet"
          :icon="Download"
          :loading="exporting === 'csv'"
          :disabled="Boolean(exporting)"
          @click="exportLogs(false)"
        >
          {{ t('page.logs.action.export') }}
        </Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="listLoading" @click="loadList()">
          {{ t('common.refresh') }}
        </Btn>
      </template>
    </PageHeader>

    <!-- 请求详情记录 -->
    <Card class="logs-capture" :loading="captureLoading">
      <template #title>{{ t('page.logs.capture.title') }}</template>
      <template #extra>
        <HelpTip :content="captureHelp" :label="t('page.logs.capture.helpLabel')" />
      </template>
      <div class="logs-capture__row">
        <NSwitch
          :value="capture?.enabled === true"
          :loading="captureLoading || captureBusy"
          :disabled="captureLoading || captureBusy"
          :aria-label="t('page.logs.capture.switchAria')"
          @update:value="toggleCapture"
        />
        <span class="logs-capture__label">{{ t('page.logs.capture.label') }}</span>
        <span class="logs-capture__status" :class="{ 'is-error': Boolean(captureError) }">
          {{
            captureError
              ? t('page.logs.capture.statusError', { error: captureError })
              : captureText
          }}
        </span>
        <Btn variant="quiet" size="sm" :loading="captureLoading" @click="loadCapture">
          {{ t('page.logs.capture.refreshStatus') }}
        </Btn>
        <Btn
          variant="quiet"
          size="sm"
          :icon="Trash2"
          :disabled="captureBusy || !capture"
          @click="clearCapture"
        >
          {{ t('page.logs.capture.clear') }}
        </Btn>
      </div>
    </Card>

    <!-- 筛选 -->
    <Card class="logs-filter-card">
      <FilterBar
        :chips="chips"
        :summary="statText"
        :loading="listLoading || summaryLoading"
        @change="onFilterChange"
        @reset="resetFilters"
        @remove="removeChip"
      >
        <template #default="{ change }">
          <NSelect
            class="logs-filter__select"
            :value="table.state.filters.platform"
            :options="platformOptions"
            :aria-label="t('page.logs.filter.platformAria')"
            @update:value="(value: string | number | null) => setFilterValue('platform', value)"
          />
          <NSelect
            class="logs-filter__select logs-filter__select--account"
            :value="table.state.filters.account_id"
            :options="accountOptions"
            :loading="accountsLoading"
            :aria-label="t('page.logs.filter.accountAria')"
            @update:value="(value: string | number | null) => setFilterValue('account_id', value)"
          />
          <NSelect
            class="logs-filter__select"
            :value="table.state.filters.range"
            :options="RANGE_OPTIONS.map((item) => ({ label: item.label, value: item.value }))"
            :aria-label="t('page.logs.filter.rangeAria')"
            @update:value="(value: string | number | null) => setFilterValue('range', value)"
          />
          <NDatePicker
            class="logs-filter__date"
            type="date"
            value-format="yyyy-MM-dd"
            :formatted-value="table.state.filters.start || null"
            clearable
            :aria-label="t('page.logs.filter.startAria')"
            :placeholder="t('page.logs.filter.startPlaceholder')"
            @update:formatted-value="(value: string | null) => setFilterValue('start', value)"
          />
          <NDatePicker
            class="logs-filter__date"
            type="date"
            value-format="yyyy-MM-dd"
            :formatted-value="table.state.filters.end || null"
            clearable
            :aria-label="t('page.logs.filter.endAria')"
            :placeholder="t('page.logs.filter.endPlaceholder')"
            @update:formatted-value="(value: string | null) => setFilterValue('end', value)"
          />
          <NInput
            class="logs-filter__input"
            :value="table.state.filters.model"
            :placeholder="t('page.logs.filter.modelPlaceholder')"
            clearable
            :aria-label="t('page.logs.filter.modelAria')"
            @update:value="(value: string) => change('model', value)"
          />
          <NInput
            class="logs-filter__input"
            :value="table.state.filters.key"
            :placeholder="t('page.logs.filter.keyPlaceholder')"
            clearable
            aria-label="Key"
            @update:value="(value: string) => change('key', value)"
          />
          <NSelect
            class="logs-filter__select"
            :value="table.state.filters.result"
            :options="RESULT_OPTIONS.map((item) => ({ label: item.label, value: item.value }))"
            :aria-label="t('page.logs.filter.resultAria')"
            @update:value="(value: string | number | null) => setFilterValue('result', value)"
          />
          <NInput
            class="logs-filter__input logs-filter__input--ip"
            :value="table.state.filters.ip"
            :placeholder="t('page.logs.filter.ipPlaceholder')"
            clearable
            :aria-label="t('page.logs.filter.clientIpAria')"
            @update:value="(value: string) => change('ip', value)"
          />
          <NCheckbox
            class="logs-filter__fast"
            :checked="table.state.filters.fast === '1'"
            :title="t('page.logs.filter.fastTitle')"
            @update:checked="(checked: boolean) => setFilterValue('fast', checked ? '1' : '')"
          >
            {{ t('page.logs.filter.fastLabel') }}
          </NCheckbox>
          <HelpTip :content="FILTER_HELP" :label="t('page.logs.filter.helpLabel')" />
        </template>
      </FilterBar>
    </Card>

    <!-- 请求动态 -->
    <Card class="logs-live" :loading="livePolling.running.value && !liveUserPaused">
      <template #title>{{ t('page.logs.live.title') }}</template>
      <template #extra>
        <span class="logs-live__hint" :class="{ 'is-error': livePolling.paused.value }">{{ liveHint }}</span>
        <Btn
          variant="quiet"
          size="sm"
          :icon="liveUserPaused || livePolling.paused.value ? Play : Pause"
          @click="toggleLive"
        >
          {{ liveUserPaused || livePolling.paused.value ? t('page.logs.live.resume') : t('page.logs.live.pause') }}
        </Btn>
      </template>

      <div v-if="livePolling.paused.value" class="logs-live__paused">
        <ErrorBlock
          compact
          :title="livePolling.pausedMessage.value || t('page.logs.live.pausedAuto')"
          :retry="false"
        />
        <Btn variant="default" size="sm" @click="toggleLive">
          {{ t('page.logs.live.resumeAuto') }}
        </Btn>
      </div>

      <div class="logs-bars" role="img" :aria-label="t('page.logs.live.barsAria')">
        <div
          v-for="bar in liveBars"
          :key="bar.at"
          class="logs-bar"
          :title="
            t('page.logs.live.barTitle', {
              label: bar.label,
              requests: bar.requests,
              errors: bar.errors,
            })
          "
        >
          <div class="logs-bar__track">
            <div class="logs-bar__requests" :style="{ height: Math.max(2, (bar.requests / liveMax) * 100) + '%' }" />
            <div class="logs-bar__errors" :style="{ height: Math.max(bar.errors ? 2 : 0, (bar.errors / liveMax) * 100) + '%' }" />
          </div>
          <span class="logs-bar__label">{{ bar.label }}</span>
        </div>
      </div>

      <div v-if="liveRecent.length" class="logs-live__rows">
        <div v-for="item in liveRecent" :key="item.request_id" class="logs-live__row">
          <span class="logs-time tnum">{{ fmtTime(item.started_at) }}</span>
          <span class="logs-mono" :title="item.path">{{ truncate(item.model || item.path, 28) }}</span>
          <span class="logs-sub" :title="item.key_name">{{ truncate(item.key_name || '—', 12) }}</span>
          <Badge :tone="liveTone(item)" :dot="true">{{ liveStatusLabel(item) }}</Badge>
          <span class="logs-sub tnum">{{ item.elapsed_ms > 0 ? fmtDuration(item.elapsed_ms) : '—' }}</span>
        </div>
      </div>
      <p v-else class="logs-sub">{{ t('page.logs.live.empty') }}</p>
    </Card>

    <!-- 按账号汇总 -->
    <Card class="logs-summary" :loading="summaryLoading">
      <template #title>{{ t('page.logs.summary.title') }}</template>
      <template #extra>
        <span class="logs-summary__hint">{{ summaryHintText(summary) }}</span>
        <Btn variant="quiet" size="sm" :aria-expanded="summaryOpen" @click="toggleSummary">
          {{ summaryOpen ? t('page.logs.summary.collapse') : t('page.logs.summary.expand') }}
        </Btn>
      </template>
      <template v-if="summaryOpen">
        <ErrorBlock
          v-if="summaryError"
          compact
          :title="t('page.logs.summary.error', { error: summaryError.message })"
          :detail="summaryError.detail"
          @retry="loadSummary()"
        />
        <div v-else-if="summaryLoading && !summary" class="logs-skeleton" aria-busy="true">
          <NSkeleton v-for="index in 3" :key="index" height="40px" />
        </div>
        <Empty
          v-else-if="!summaryRows.length"
          :description="t('page.logs.summary.empty')"
        />
        <DataTable
          v-else
          table-id="logs-summary"
          :columns="summaryColumns"
          :data="summaryData()"
          row-key="account_id"
          :pagination="false"
          :virtual-scroll="false"
          clickable-rows
          :empty-description="t('page.logs.summary.empty')"
          @row-click="applyAccountFilter"
        />
      </template>
    </Card>

    <!-- 日志表 -->
    <Card flush class="logs-table-card">
      <ErrorBlock
        v-if="listError && rows === null"
        :title="t('page.logs.error.loadList', { error: listError.message })"
        :detail="listError.detail"
        @retry="loadList()"
      />
      <div v-else-if="firstLoading" class="logs-skeleton" aria-busy="true">
        <NSkeleton v-for="index in 8" :key="index" height="44px" />
      </div>
      <template v-else>
        <DataTable
          table-id="logs"
          :columns="systemCols()"
          :data="rows || []"
          row-key="id"
          :loading="listLoading"
          :pagination="false"
          :virtual-scroll="true"
          clickable-rows
          :empty-description="t('page.logs.table.empty')"
          @row-click="openDetail"
        >
          <template #empty>
            <Empty :description="t('page.logs.table.emptyFiltered')">
              <Btn variant="default" size="sm" @click="resetFilters">
                {{ t('page.logs.clearFilters') }}
              </Btn>
            </Empty>
          </template>
        </DataTable>
        <div class="logs-pager">
          <span class="logs-sub">{{ pagerInfo }}</span>
          <div class="logs-pager__actions">
            <Btn size="sm" :icon="ChevronLeft" :disabled="table.state.page < 2" @click="table.setPage(table.state.page - 1)">
              {{ t('page.logs.pager.prev') }}
            </Btn>
            <Btn size="sm" :disabled="!hasMore" @click="table.setPage(table.state.page + 1)">
              {{ t('page.logs.pager.next') }}
              <ChevronRight :size="14" />
            </Btn>
          </div>
        </div>
      </template>
    </Card>

    <!-- 详情 -->
    <DetailDrawer
      v-model:show="detailOpen"
      :title="detailTitle"
      :badge="detailRow ? { tone: detailRow.resultTone, label: detailRow.resultLabel } : null"
      :subtitle="detailRow ? fmtFullTime(detailRow.created_at) : ''"
    >
      <div v-if="detailLoading" class="logs-skeleton" aria-busy="true">
        <NSkeleton v-for="index in 5" :key="index" height="28px" />
      </div>
      <ErrorBlock
        v-else-if="detailError"
        :title="t('page.logs.detail.error', { error: detailError.message })"
        :detail="detailError.detail"
        @retry="detailRow ? openDetail(detailRow) : undefined"
      />
      <template v-else-if="detail">
        <Section :title="t('page.logs.detail.sectionSummary')" first>
          <KV :items="detailKv" label-width="96px" />
        </Section>

        <Section
          v-if="detail.diagnostic || detail.error_kind"
          :title="t('page.logs.detail.sectionErrors')"
        >
          <p v-if="detail.error_kind" class="logs-sub">
            {{ t('page.logs.detail.errorKind', { kind: detail.error_kind }) }}
          </p>
          <CodeBlock
            v-if="diagnosticJson"
            :code="diagnosticJson"
            language="json"
            max-height="240px"
          />
          <p v-else class="logs-sub">{{ t('page.logs.detail.noDiagnostic') }}</p>
        </Section>

        <Section :title="t('page.logs.detail.sectionBody')">
          <template v-if="detail.request_capture">
            <p class="logs-sub">
              {{
                t('page.logs.detail.capturedAt', {
                  time: fmtFullTime(detail.request_capture.captured_at),
                  cost: ingressCost,
                })
              }}
            </p>
            <p class="logs-sub">{{ t('page.logs.detail.ingressHeaders') }}</p>
            <CodeBlock :code="JSON.stringify(detail.request_capture.ingress.headers, null, 2)" language="json" max-height="160px" />
            <p class="logs-sub">
              {{ t('page.logs.detail.ingressLine', { value: ingressLabel }) }}
            </p>
            <CodeBlock v-if="ingressText" :code="ingressText" language="http" max-height="320px" />
            <div class="logs-body-actions">
              <Btn
                v-if="!ingressText"
                variant="quiet"
                size="sm"
                @click="detail && loadIngress(detail.id, 0)"
              >
                {{ t('page.logs.detail.expandIngress') }}
              </Btn>
              <Btn
                v-if="ingressPage?.has_more"
                variant="quiet"
                size="sm"
                @click="detail && loadIngress(detail.id, ingressPage?.next_offset || 0)"
              >
                {{ t('page.logs.detail.continueReading') }}
              </Btn>
              <Btn variant="quiet" size="sm" :icon="Copy" @click="copyCurl">
                {{ t('page.logs.detail.copyCurl') }}
              </Btn>
            </div>

            <p class="logs-sub">
              {{ t('page.logs.detail.upstreamTitle', { count: detail.request_capture.upstream.length }) }}
            </p>
            <div
              v-for="(attempt, index) in detail.request_capture.upstream"
              :key="attempt.attempt"
              class="logs-upstream"
            >
              <p class="logs-sub">
                {{
                  t('page.logs.detail.attemptLine', {
                    index: attempt.attempt,
                    method: attempt.method,
                    url: attempt.url,
                    status: attempt.http_status || t('page.logs.detail.noStatus'),
                  })
                }}
              </p>
              <CodeBlock
                v-if="upstreamBodies[index]?.text"
                :code="upstreamBodies[index].text"
                language="http"
                max-height="240px"
              />
              <div class="logs-body-actions">
                <Btn
                  v-if="!upstreamBodies[index]?.text"
                  variant="quiet"
                  size="sm"
                  :loading="upstreamBodies[index]?.loading"
                  @click="loadUpstream(index, 0)"
                >
                  {{ t('page.logs.detail.expandUpstream') }}
                </Btn>
                <Btn
                  v-if="upstreamBodies[index]?.page?.has_more"
                  variant="quiet"
                  size="sm"
                  :loading="upstreamBodies[index]?.loading"
                  @click="loadUpstream(index, upstreamBodies[index]?.page?.next_offset || 0)"
                >
                  {{ t('page.logs.detail.continueReading') }}
                </Btn>
              </div>
            </div>
            <p v-if="detail.request_capture.upstream_omitted" class="logs-sub">
              {{ t('page.logs.detail.upstreamOmitted', { count: detail.request_capture.upstream_omitted }) }}
            </p>
          </template>
          <p v-else class="logs-sub">{{ t('page.logs.detail.noCapture') }}</p>
        </Section>
      </template>

      <template #footer>
        <Btn variant="quiet" :disabled="!detail" @click="downloadCapture">
          {{ t('page.logs.action.downloadJsonl') }}
        </Btn>
        <Btn variant="default" @click="detailOpen = false">{{ t('common.close') }}</Btn>
      </template>
    </DetailDrawer>
  </div>
</template>

<style scoped>
.logs-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.logs-capture__row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.logs-capture__label {
  font-size: var(--fs-body);
  color: var(--fg);
}

.logs-capture__status {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  min-width: 0;
}

.logs-capture__status.is-error {
  color: var(--danger);
}

.logs-filter-card :deep(.mh-card__body) {
  padding: var(--sp-3) var(--sp-4);
}

.logs-filter__select {
  width: 160px;
}

.logs-filter__select--account {
  width: 220px;
}

.logs-filter__date {
  width: 150px;
  max-width: 100%;
}

.logs-filter__input {
  width: 160px;
}

.logs-filter__input--ip {
  width: 140px;
}

.logs-filter__fast {
  white-space: nowrap;
}

.logs-live__hint {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.logs-live__hint.is-error {
  color: var(--warn);
}

.logs-live__paused {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.logs-bars {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 72px;
  margin-bottom: var(--sp-2);
}

.logs-bar {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  height: 100%;
  gap: 2px;
}

.logs-bar__track {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  background: var(--surface-2);
  border-radius: var(--r-chip);
  overflow: hidden;
}

.logs-bar__requests {
  width: 100%;
  background: var(--brand);
}

.logs-bar__errors {
  width: 100%;
  background: var(--danger);
}

.logs-bar__label {
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
  text-align: center;
  white-space: nowrap;
}

.logs-live__rows {
  display: grid;
  gap: var(--sp-1);
}

.logs-live__row {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr) 110px 90px 64px;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-small);
  border-top: 1px solid var(--border-soft);
  padding-top: var(--sp-1);
}

.logs-summary__hint {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.logs-table-card :deep(.mh-card__body) {
  display: grid;
  gap: var(--sp-2);
}

.logs-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-4);
  border-top: 1px solid var(--border-soft);
}

.logs-pager__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
}

.logs-skeleton {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
}

.logs-time {
  font-size: var(--fs-small);
  color: var(--fg-2);
  white-space: nowrap;
}

.logs-mono {
  font-family: var(--font-mono);
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logs-sub {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.logs-model,
.logs-stack,
.logs-latency {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.logs-tokens {
  display: grid;
  gap: 0;
  font-size: var(--fs-tiny);
  color: var(--fg-2);
}

.logs-account {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logs-credit {
  color: var(--warn);
  font-weight: 500;
}

.logs-link {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--brand);
  font-size: var(--fs-small);
  cursor: pointer;
}

.logs-link:hover {
  text-decoration: underline;
}

.logs-body-actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.logs-upstream {
  display: grid;
  gap: var(--sp-1);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--border-soft);
}

@media (max-width: 900px) {
  .logs-live__row {
    grid-template-columns: 72px minmax(0, 1fr) 80px;
  }
}

@media (max-width: 640px) {
  .logs-filter__select,
  .logs-filter__select--account,
  .logs-filter__date,
  .logs-filter__input,
  .logs-filter__input--ip {
    width: 100%;
  }

  .logs-pager {
    flex-wrap: wrap;
  }
}
</style>
