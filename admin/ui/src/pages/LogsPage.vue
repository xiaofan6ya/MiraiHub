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

/** 旧版 #panel-logs 页头说明（收进 ⓘ）。 */
const HELP_TEXT =
  '筛选条件下方是请求动态（自动刷新，正在发生的请求）；再往下是按账号汇总与落库记录表格，可筛选、导出。' +
  '用量在请求结束后写入；实时请求见风控哨兵。' +
  '请求详情只在开启「记录请求详情」后缓存，凭据脱敏、不写数据库，重启 / 过期 / 容量淘汰后无法恢复。'

const CAPTURE_HELP =
  '开启后记录新请求的入站正文和各次发往上游的实际正文，仅供管理员审核。凭据脱敏，正文可能含完整对话；不写数据库。' +
  '关闭停止新记录，已有缓存可手动清空。缓存上限 200 条 / 64 MiB / 单条 8 MiB / 60 分钟过期，重启清空。'

const EXPORT_DIAG_HINT = '含脱敏诊断，最多导出最近 1000 条'

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
  const options = [{ label: '全部平台', value: '' }]
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
  if (!value) return '全部平台'
  return platformOptions.value.find((item) => item.value === value)?.label || value
}

const accounts = ref<LogAccountOption[]>([])
const accountsLoading = ref(false)

const accountOptions = computed(() => {
  const options = [{ label: '全部账号（' + accounts.value.length + ' 个有记录）', value: '' }]
  for (const item of accounts.value) {
    const suffix = item.requests == null ? '' : ' · ' + fmtInt(item.requests) + ' 次 · ' + fmtCredit(item.credits) + ' 积分'
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
    if (rows.value !== null) toast.error(failureText('刷新用量日志', error))
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
    toast.warning('「未记录账号 / 未选号」没有账号 id，无法按账号筛选；可改用结果或时间范围筛选')
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
  if (liveUserPaused.value) return '已暂停自动刷新'
  if (livePolling.paused.value) return livePolling.pausedMessage.value
  if (liveError.value) return '请求动态读取失败：' + liveError.value
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
    toast.success(enabled ? '已开启请求详情记录' : '已关闭请求详情记录')
  } catch (error) {
    toast.error(failureText(enabled ? '开启请求详情记录' : '关闭请求详情记录', error))
  } finally {
    captureBusy.value = false
    void loadCapture()
  }
}

async function clearCapture(): Promise<void> {
  const confirmed = await confirm({
    title: '清空详情缓存？',
    message: '已缓存的请求正文会立即删除，之后无法恢复；已落库的用量日志不受影响。',
    okText: '清空',
    danger: true,
  })
  if (!confirmed) return
  captureBusy.value = true
  try {
    capture.value = await clearCaptureCache()
    toast.success('已清空详情缓存')
  } catch (error) {
    toast.error(failureText('清空详情缓存', error))
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
  toast.info('正在导出 CSV…')
  try {
    const filename = await downloadLogsExport(buildExportQuery(currentFilters()), diagnostics)
    toast.success('已导出 ' + filename)
  } catch (error) {
    toast.error(failureText('导出', error))
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
    toast.error(failureText('读取请求正文', error))
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
    toast.error(failureText('读取上游正文', error))
  }
}

const curlText = computed(() => (detail.value ? buildCurl(detail.value, ingressText.value) : ''))
const ingressCost = computed(() => {
  const capture = detail.value?.request_capture
  if (!capture) return ''
  const cached = Number(capture.ingress.body_cached_bytes || 0)
  const full = Number(capture.ingress.body_bytes || 0)
  return (
    '正文缓存 ' + fmtInt(cached) + ' / ' + fmtInt(full) + ' 字节' +
    (capture.ingress.body_truncated ? ' · 采集时已截断' : '')
  )
})

const detailView = computed(() => (detail.value ? toLogRow(detail.value) : null))

const detailKv = computed(() => {
  const item = detailView.value
  if (!item) return []
  return [
    { label: '请求编号', value: item.id, mono: true },
    { label: '时间', value: fmtFullTime(item.created_at) },
    { label: '平台', value: platformLabel(item.platform) },
    { label: '账号', value: item.account_name || FMT_EMPTY },
    { label: 'Key', value: item.key_name || FMT_EMPTY },
    { label: '模型', value: item.model || FMT_EMPTY, mono: true },
    { label: '端点 / 用途', value: item.use_case || FMT_EMPTY, mono: true },
    { label: '结果', value: item.isError ? item.error_kind || '失败' : '成功' },
    {
      label: 'Token',
      value:
        '入 ' + fmtInt(item.prompt_tokens) + ' · 出 ' + fmtInt(item.completion_tokens) +
        ' · 缓存 ' + fmtInt(item.cached_tokens) + ' · 合计 ' + fmtInt(item.total_tokens),
    },
    { label: '缓存命中', value: item.cacheRateText },
    { label: '积分', value: fmtCredit(item.credits) },
    { label: '首响应', value: item.ttfbText },
    { label: '总耗时', value: item.latencyText },
    { label: 'TPS', value: item.tpsText },
    { label: '客户端 IP', value: item.client_ip || FMT_EMPTY, mono: true },
    { label: '序号', value: item.seq ?? FMT_EMPTY },
    { label: '思考强度', value: item.reasoning_effort || '默认 / 未记录' },
  ]
})

const diagnosticJson = computed(() =>
  detail.value?.diagnostic ? JSON.stringify(detail.value.diagnostic, null, 2) : '',
)

async function copyCurl(): Promise<void> {
  if (!curlText.value) {
    toast.warning('这条记录没有可用的请求缓存，无法生成 curl')
    return
  }
  const ok = await copy(curlText.value)
  if (ok) toast.success('已复制 curl')
  else toast.error('复制失败：浏览器拒绝了剪贴板访问，请手动选中复制')
}

async function downloadCapture(): Promise<void> {
  const id = detail.value?.id
  if (!id) return
  try {
    const filename = await downloadLogCapture(id)
    toast.success('已下载 ' + filename)
  } catch (error) {
    toast.error(failureText('下载完整 JSONL', error))
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
    title: '时间',
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
    title: '模型',
    mobile: 'subtitle',
    width: 190,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('div', { class: 'logs-model' }, [
        h('span', { class: 'logs-mono', title: item.model_raw || item.model }, truncate(item.model, 26)),
        h('small', { class: 'logs-sub' }, '思考：' + (item.reasoning_effort || '默认 / 未记录')),
      ])
    },
  },
  {
    key: 'platform_account',
    title: '平台 / 账号',
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
    title: '端点',
    mobile: 'hidden',
    width: 100,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('span', { class: 'logs-sub', title: item.use_case || FMT_EMPTY }, item.useCaseLabel)
    },
  },
  {
    key: 'result',
    title: '结果',
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
    title: 'Token 入 / 出 / 缓存',
    mobile: 'meta',
    width: 190,
    numeric: true,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('div', { class: 'logs-tokens tnum' }, [
        h('span', {}, '入 ' + fmtInt(item.prompt_tokens)),
        h('span', {}, '出 ' + fmtInt(item.completion_tokens)),
        h('span', {}, '缓存 ' + fmtInt(item.cached_tokens)),
      ])
    },
  },
  {
    key: 'cache_rate',
    title: '命中率',
    mobile: 'meta',
    width: 84,
    numeric: true,
    render: (row: TableRow): VNodeChild =>
      h('span', { class: 'logs-sub tnum' }, (row as unknown as LogRowView).cacheRateText),
  },
  {
    key: 'credits',
    title: '积分',
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
    title: '耗时（TTFT / 总）',
    mobile: 'meta',
    width: 150,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('div', { class: 'logs-latency tnum' }, [
        h('span', {}, '首 ' + item.ttfbText),
        h('span', {}, '总 ' + item.latencyText),
        item.tpsText !== FMT_EMPTY ? h('span', { class: 'logs-sub' }, 'TPS ' + item.tpsText) : null,
      ])
    },
  },
  {
    key: 'client_ip',
    title: '客户端 IP',
    mobile: 'hidden',
    width: 128,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as LogRowView
      return h('span', { class: 'logs-mono', title: item.client_ip || FMT_EMPTY }, item.client_ip || FMT_EMPTY)
    },
  },
  {
    key: 'actions',
    title: '操作',
    mobile: 'actions',
    width: 72,
    hideable: false,
    fixed: 'right',
    render: (row: TableRow): VNodeChild =>
      h('button', {
        type: 'button',
        class: 'logs-link',
        'aria-label': '查看调用详情',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          void openDetail(row)
        },
      }, '详情'),
  },
])

const summaryColumns = computed<TableColumn[]>(() => [
  { key: 'account_name', title: '账号', mobile: 'title', minWidth: 160 },
  {
    key: 'requests', title: '请求数', mobile: 'meta', width: 92, numeric: true,
    render: (row: TableRow) => h('span', { class: 'tnum' }, fmtInt((row as unknown as AccountSummaryRow).requests)),
  },
  {
    key: 'failed_requests', title: '失败', mobile: 'meta', width: 72, numeric: true,
    render: (row: TableRow) => {
      const item = row as unknown as AccountSummaryRow
      return h('span', { class: item.failed_requests > 0 ? 'logs-credit tnum' : 'logs-sub tnum' }, fmtInt(item.failed_requests))
    },
  },
  {
    key: 'credits', title: '积分', mobile: 'meta', width: 88, numeric: true,
    render: (row: TableRow) => h('span', { class: 'logs-credit tnum' }, fmtCredit((row as unknown as AccountSummaryRow).credits)),
  },
  {
    key: 'total_tokens', title: 'Token', mobile: 'meta', width: 92, numeric: true,
    render: (row: TableRow) => h('span', { class: 'logs-sub tnum' }, fmtInt((row as unknown as AccountSummaryRow).total_tokens)),
  },
  {
    key: 'cache_rate', title: '命中率', mobile: 'meta', width: 84, numeric: true,
    render: (row: TableRow) => h('span', { class: 'logs-sub tnum' }, (row as unknown as AccountSummaryRow).cacheRateText),
  },
  {
    key: 'avg_latency_ms', title: '平均耗时', mobile: 'meta', width: 92, numeric: true,
    render: (row: TableRow) => h('span', { class: 'logs-sub tnum' }, (row as unknown as AccountSummaryRow).avgLatencyText),
  },
  {
    key: 'last_used_at', title: '最近使用', mobile: 'meta', width: 120,
    render: (row: TableRow) => h('span', { class: 'logs-time' }, (row as unknown as AccountSummaryRow).lastUsedText),
  },
  {
    key: 'share',
    title: '积分占比',
    mobile: 'meta',
    width: 130,
    render: (row: TableRow): VNodeChild => {
      const item = row as unknown as AccountSummaryRow
      return h(Meter, {
        value: item.credits,
        max: maxCredits.value,
        tone: item.unattributed ? 'neutral' : 'brand',
        valueText: item.unattributed ? '未记录账号' : Math.round((item.credits / maxCredits.value) * 100) + '%',
        showValue: true,
        ariaLabel: '积分占比',
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
  if (f.platform) list.push({ key: 'platform', label: '平台', value: platformLabel(f.platform) })
  if (f.account_id) {
    const account = accounts.value.find((item) => String(item.id) === f.account_id)
    list.push({ key: 'account_id', label: '账号', value: account?.name || '#' + f.account_id })
  }
  if (f.account) list.push({ key: 'account', label: '账号名', value: f.account })
  if (f.model) list.push({ key: 'model', label: '模型', value: f.model })
  if (f.key) list.push({ key: 'key', label: 'Key', value: f.key })
  if (f.ip) list.push({ key: 'ip', label: '客户端 IP', value: f.ip })
  if (f.result) {
    const label = LOG_RESULT_OPTIONS.find((item) => item.value === f.result)?.label
    list.push({ key: 'result', label: '结果', value: label || f.result })
  }
  if (f.start || f.end) {
    list.push({ key: 'start', label: '日期', value: (f.start || '…') + ' ~ ' + (f.end || '…') })
  } else {
    const range = LOG_RANGE_OPTIONS.find((item) => item.value === f.range)
    list.push({ key: 'range', label: '时间', value: range?.label || f.range })
  }
  if (f.fast === '1') list.push({ key: 'fast', label: '快速翻页', value: '不统计总数' })
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
const FILTER_HELP = '筛选条件会同步到地址栏；快速翻页不统计总数，适合日志量大的窗口。'
</script>

<template>
  <div class="logs-page">
    <PageHeader title="用量日志" :help="HELP_TEXT">
      <template #actions>
        <Btn
          variant="quiet"
          :icon="Download"
          :loading="exporting === 'diagnostics'"
          :disabled="Boolean(exporting)"
          title="含脱敏诊断，最多导出最近 1000 条"
          @click="exportLogs(true)"
        >
          导出诊断
        </Btn>
        <Btn variant="quiet" :icon="Download" :loading="exporting === 'csv'" :disabled="Boolean(exporting)" @click="exportLogs(false)">
          导出
        </Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="listLoading" @click="loadList()">刷新</Btn>
      </template>
    </PageHeader>

    <!-- 请求详情记录 -->
    <Card class="logs-capture" :loading="captureLoading">
      <template #title>请求详情记录</template>
      <template #extra><HelpTip :content="CAPTURE_HELP" label="请求详情记录说明" /></template>
      <div class="logs-capture__row">
        <NSwitch
          :value="capture?.enabled === true"
          :loading="captureLoading || captureBusy"
          :disabled="captureLoading || captureBusy"
          aria-label="记录请求详情"
          @update:value="toggleCapture"
        />
        <span class="logs-capture__label">记录请求详情（内存缓存）</span>
        <span class="logs-capture__status" :class="{ 'is-error': Boolean(captureError) }">
          {{ captureError ? '读取开关状态失败：' + captureError : captureText }}
        </span>
        <Btn variant="quiet" size="sm" :loading="captureLoading" @click="loadCapture">刷新状态</Btn>
        <Btn
          variant="quiet"
          size="sm"
          :icon="Trash2"
          :disabled="captureBusy || !capture"
          @click="clearCapture"
        >
          清空详情缓存
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
            aria-label="平台"
            @update:value="(value: string | number | null) => setFilterValue('platform', value)"
          />
          <NSelect
            class="logs-filter__select logs-filter__select--account"
            :value="table.state.filters.account_id"
            :options="accountOptions"
            :loading="accountsLoading"
            aria-label="按账号（窗口内有记录的）"
            @update:value="(value: string | number | null) => setFilterValue('account_id', value)"
          />
          <NSelect
            class="logs-filter__select"
            :value="table.state.filters.range"
            :options="RANGE_OPTIONS.map((item) => ({ label: item.label, value: item.value }))"
            aria-label="时间范围"
            @update:value="(value: string | number | null) => setFilterValue('range', value)"
          />
          <NDatePicker
            class="logs-filter__date"
            type="date"
            value-format="yyyy-MM-dd"
            :formatted-value="table.state.filters.start || null"
            clearable
            aria-label="起始日期"
            placeholder="起始日期"
            @update:formatted-value="(value: string | null) => setFilterValue('start', value)"
          />
          <NDatePicker
            class="logs-filter__date"
            type="date"
            value-format="yyyy-MM-dd"
            :formatted-value="table.state.filters.end || null"
            clearable
            aria-label="结束日期"
            placeholder="结束日期"
            @update:formatted-value="(value: string | null) => setFilterValue('end', value)"
          />
          <NInput
            class="logs-filter__input"
            :value="table.state.filters.model"
            placeholder="全部模型"
            clearable
            aria-label="模型"
            @update:value="(value: string) => change('model', value)"
          />
          <NInput
            class="logs-filter__input"
            :value="table.state.filters.key"
            placeholder="全部 Key"
            clearable
            aria-label="Key"
            @update:value="(value: string) => change('key', value)"
          />
          <NSelect
            class="logs-filter__select"
            :value="table.state.filters.result"
            :options="RESULT_OPTIONS.map((item) => ({ label: item.label, value: item.value }))"
            aria-label="调用结果"
            @update:value="(value: string | number | null) => setFilterValue('result', value)"
          />
          <NInput
            class="logs-filter__input logs-filter__input--ip"
            :value="table.state.filters.ip"
            placeholder="全部"
            clearable
            aria-label="客户端 IP"
            @update:value="(value: string) => change('ip', value)"
          />
          <NCheckbox
            class="logs-filter__fast"
            :checked="table.state.filters.fast === '1'"
            title="日志量大时，精确总数需要额外扫一遍索引；关掉可让翻页更快"
            @update:checked="(checked: boolean) => setFilterValue('fast', checked ? '1' : '')"
          >
            快速翻页（不统计总数）
          </NCheckbox>
          <HelpTip :content="FILTER_HELP" label="筛选说明" />
        </template>
      </FilterBar>
    </Card>

    <!-- 请求动态 -->
    <Card class="logs-live" :loading="livePolling.running.value && !liveUserPaused">
      <template #title>请求动态</template>
      <template #extra>
        <span class="logs-live__hint" :class="{ 'is-error': livePolling.paused.value }">{{ liveHint }}</span>
        <Btn
          variant="quiet"
          size="sm"
          :icon="liveUserPaused || livePolling.paused.value ? Play : Pause"
          @click="toggleLive"
        >
          {{ liveUserPaused || livePolling.paused.value ? '继续' : '暂停' }}
        </Btn>
      </template>

      <div v-if="livePolling.paused.value" class="logs-live__paused">
        <ErrorBlock
          compact
          :title="livePolling.pausedMessage.value || '自动刷新已暂停'"
          :retry="false"
        />
        <Btn variant="default" size="sm" @click="toggleLive">恢复自动刷新</Btn>
      </div>

      <div class="logs-bars" role="img" aria-label="最近 15 分钟每分钟请求数">
        <div v-for="bar in liveBars" :key="bar.at" class="logs-bar" :title="bar.label + ' · ' + bar.requests + ' 次 · 错误 ' + bar.errors">
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
      <p v-else class="logs-sub">最近 15 分钟还没有请求。</p>
    </Card>

    <!-- 按账号汇总 -->
    <Card class="logs-summary" :loading="summaryLoading">
      <template #title>按账号汇总</template>
      <template #extra>
        <span class="logs-summary__hint">{{ summaryHintText(summary) }}</span>
        <Btn variant="quiet" size="sm" :aria-expanded="summaryOpen" @click="toggleSummary">
          {{ summaryOpen ? '收起' : '展开' }}
        </Btn>
      </template>
      <template v-if="summaryOpen">
        <ErrorBlock
          v-if="summaryError"
          compact
          :title="'加载按账号汇总失败：' + summaryError.message"
          :detail="summaryError.detail"
          @retry="loadSummary()"
        />
        <div v-else-if="summaryLoading && !summary" class="logs-skeleton" aria-busy="true">
          <NSkeleton v-for="index in 3" :key="index" height="40px" />
        </div>
        <Empty
          v-else-if="!summaryRows.length"
          description="该时间范围内没有账号产生请求。"
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
          empty-description="该时间范围内没有账号产生请求。"
          @row-click="applyAccountFilter"
        />
      </template>
    </Card>

    <!-- 日志表 -->
    <Card flush class="logs-table-card">
      <ErrorBlock
        v-if="listError && rows === null"
        :title="'加载用量日志失败：' + listError.message"
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
          empty-description="还没有请求记录（代理产生调用后这里会出现）"
          @row-click="openDetail"
        >
          <template #empty>
            <Empty description="当前筛选条件下没有记录，试试放宽时间范围或清除筛选">
              <Btn variant="default" size="sm" @click="resetFilters">清除筛选</Btn>
            </Empty>
          </template>
        </DataTable>
        <div class="logs-pager">
          <span class="logs-sub">
            第 {{ table.state.page }} 页
            <template v-if="totalKnown != null"> · 共 {{ fmtInt(totalKnown) }} 条</template>
            <template v-else-if="table.state.filters.fast === '1'"> · 快速翻页（未统计总数）</template>
            <template v-else-if="!hasMore"> · 已到末尾</template>
          </span>
          <div class="logs-pager__actions">
            <Btn size="sm" :icon="ChevronLeft" :disabled="table.state.page < 2" @click="table.setPage(table.state.page - 1)">
              上一页
            </Btn>
            <Btn size="sm" :disabled="!hasMore" @click="table.setPage(table.state.page + 1)">
              下一页
              <ChevronRight :size="14" />
            </Btn>
          </div>
        </div>
      </template>
    </Card>

    <!-- 详情 -->
    <DetailDrawer
      v-model:show="detailOpen"
      :title="detail ? '调用详情 · ' + (detail.model || '') : '调用详情'"
      :badge="detailRow ? { tone: detailRow.resultTone, label: detailRow.resultLabel } : null"
      :subtitle="detailRow ? fmtFullTime(detailRow.created_at) : ''"
    >
      <div v-if="detailLoading" class="logs-skeleton" aria-busy="true">
        <NSkeleton v-for="index in 5" :key="index" height="28px" />
      </div>
      <ErrorBlock
        v-else-if="detailError"
        :title="'读取调用详情失败：' + detailError.message"
        :detail="detailError.detail"
        @retry="detailRow ? openDetail(detailRow) : undefined"
      />
      <template v-else-if="detail">
        <Section title="概要" first>
          <KV :items="detailKv" label-width="96px" />
        </Section>

        <Section v-if="detail.diagnostic || detail.error_kind" title="错误与诊断">
          <p v-if="detail.error_kind" class="logs-sub">网关分类：{{ detail.error_kind }}</p>
          <CodeBlock
            v-if="diagnosticJson"
            :code="diagnosticJson"
            language="json"
            max-height="240px"
          />
          <p v-else class="logs-sub">这条记录没有保存诊断信息。</p>
        </Section>

        <Section title="请求正文（内存缓存）">
          <template v-if="detail.request_capture">
            <p class="logs-sub">
              捕获于 {{ fmtFullTime(detail.request_capture.captured_at) }} · {{ ingressCost }}
            </p>
            <p class="logs-sub">入站请求头（凭据脱敏）：</p>
            <CodeBlock :code="JSON.stringify(detail.request_capture.ingress.headers, null, 2)" language="json" max-height="160px" />
            <p class="logs-sub">
              入站正文：{{ ingressPage ? '已读取 ' + fmtInt(ingressPage.next_offset) + ' / ' + fmtInt(ingressPage.cached_bytes) + ' 字节' : '展开后读取' }}
            </p>
            <CodeBlock v-if="ingressText" :code="ingressText" language="http" max-height="320px" />
            <div class="logs-body-actions">
              <Btn
                v-if="!ingressText"
                variant="quiet"
                size="sm"
                @click="detail && loadIngress(detail.id, 0)"
              >
                展开入站正文
              </Btn>
              <Btn
                v-if="ingressPage?.has_more"
                variant="quiet"
                size="sm"
                @click="detail && loadIngress(detail.id, ingressPage?.next_offset || 0)"
              >
                继续读取
              </Btn>
              <Btn variant="quiet" size="sm" :icon="Copy" @click="copyCurl">复制 curl</Btn>
            </div>

            <p class="logs-sub">实际发往上游（{{ detail.request_capture.upstream.length }} 次尝试）：</p>
            <div
              v-for="(attempt, index) in detail.request_capture.upstream"
              :key="attempt.attempt"
              class="logs-upstream"
            >
              <p class="logs-sub">
                第 {{ attempt.attempt }} 次 · {{ attempt.method }} {{ attempt.url }} · HTTP {{ attempt.http_status || '未返回' }}
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
                  展开上游正文
                </Btn>
                <Btn
                  v-if="upstreamBodies[index]?.page?.has_more"
                  variant="quiet"
                  size="sm"
                  :loading="upstreamBodies[index]?.loading"
                  @click="loadUpstream(index, upstreamBodies[index]?.page?.next_offset || 0)"
                >
                  继续读取
                </Btn>
              </div>
            </div>
            <p v-if="detail.request_capture.upstream_omitted" class="logs-sub">
              另有 {{ detail.request_capture.upstream_omitted }} 次尝试因上限省略。
            </p>
          </template>
          <p v-else class="logs-sub">
            这条记录没有可用的内存请求缓存。请打开「记录请求详情」后再发起请求；缓存重启、过期或容量淘汰后无法恢复。
          </p>
        </Section>
      </template>

      <template #footer>
        <Btn variant="quiet" :disabled="!detail" @click="downloadCapture">下载完整 JSONL</Btn>
        <Btn variant="default" @click="detailOpen = false">关闭</Btn>
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
