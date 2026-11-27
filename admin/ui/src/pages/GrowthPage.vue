<script setup lang="ts">
/**
 * S24 成长计划 /growth（v2.1 视觉精修：KPI 行 + 一行工具栏 + 账号表格，点行展开任务清单）
 *
 * 契约：docs/ui-v2/contracts/growth.md（末尾「## 实现对照」）
 * 规范：docs/ui-v2/10-visual-refresh.md（最高优先级）
 *
 * 口径：
 *   1. 平台能力不硬编码：platformStore.supports('growth')；后端 /api/growth-center/accounts
 *      现在只返回 WorkBuddy 账号（数据隔离 D2），前端不再自己过滤平台。
 *   2. 「可做」「可领」只看后端字段：actionable / accept_status / summary.claimable。
 *   3. 任务清单接口单次 1–3.3s 且每次都打上游 → **只拉当前页可见、状态为 active 的账号**
 *      （第 3 轮审查 B1/B2），并发 2；连续 8 个「整体不可用」类失败（5xx / 超时 / 网络）熔断；
 *      409 / 422（凭证缺失）只记在该行，显示「凭证缺失」，不触发熔断、不刷红。
 *   4. 离开页面立即取消：预取队列、行内重读、刷新成长状态的延时重读全部挂在
 *      页面级 AbortController 上，卸载 / 切平台时 abort。
 *   5. 默认只看「可操作」账号（第 2 轮审查 P2）：已禁用账号既不请求也没有按钮，收进筛选项。
 *
 * 文案全部走 useI18n().t（page.growth.*）。
 */
import { computed, effectScope, markRaw, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NDropdown, NInput, NSelect, type DropdownOption } from 'naive-ui'
import {
  BatteryCharging,
  CheckCheck,
  ChevronRight,
  Gift,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Sparkles,
  TriangleAlert,
  Trophy,
  Users,
} from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Empty,
  ErrorBlock,
  JobProgress,
  KpiCard,
  Meter,
  StatusDot,
  TitleTip,
  type JobItem,
} from '@/components'
import { useConfirm, useJob, useTableQuery, useToast, type JobSnapshot } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import { FMT_EMPTY, fmtCredit, fmtInt } from '@/utils/format'
import { useI18n, type MessageKey } from '@/i18n'
import {
  GROWTH_ACTIONS,
  claimGrowth,
  filterGrowthCards,
  getAccountTasks,
  getGrowthAccounts,
  growthKpis,
  isOutageError,
  pageGrowthCards,
  pendingTaskIds,
  postGrowthAction,
  progressOf,
  refreshGrowthCenter,
  runGrowthTasks,
  sortGrowthCards,
  sortTasks,
  summarizeClaim,
  taskErrorKind,
  taskStatusOf,
  toGrowthCards,
  type GrowthAccountTasksPayload,
  type GrowthActionItem,
  type GrowthCard,
  type GrowthCenterAccount,
  type GrowthClaimPayload,
  type GrowthTask,
  type GrowthTaskStatus,
} from '@/api/growth'
import { useRevisit } from '@/pages/_shared/useRevisit'

const { t } = useI18n()
const helpText = computed(() => t('page.growth.help'))

const toast = useToast()
const confirm = useConfirm()
const router = useRouter()
const platformStore = usePlatformStore()

const table = useTableQuery('growth', {
  pageSize: 20,
  filters: { q: '', state: '' },
})
const filters = table.state.filters as unknown as { q: string; state: string }

/** 路由里没有 state 时按「可操作」处理；`all` 才是全部。 */
const DEFAULT_STATE = 'active'
const stateValue = computed(() => filters.state || DEFAULT_STATE)

const supported = computed(() => platformStore.supports('growth'))

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const accounts = ref<GrowthCenterAccount[]>([])
const excluded = ref(0)
const loading = ref(false)
const loaded = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const tasksByAccount = reactive<Record<number, GrowthAccountTasksPayload | undefined>>({})
const tasksLoading = reactive<Record<number, boolean>>({})
const tasksError = reactive<Record<number, string>>({})
/** 409 / 422：凭证缺失（行内灰字，不当作红色错误）。 */
const credentialMissing = reactive<Record<number, boolean>>({})
const expanded = reactive<Record<number, boolean>>({})

let controller: AbortController | null = null
/** 预取队列的取消句柄：翻页 / 卸载 / 切平台 / 新一轮预取都会 abort 它。 */
let tasksAbort: AbortController | null = null
let tasksToken = 0
/** 页面级取消句柄：行内重读、延时重读等零散请求都挂在它上面，卸载 / 切平台时一起 abort。 */
let pageAbort = new AbortController()
let queueIds = new Set<number>()
let alive = true

/** 任务清单并发上限（后端单次 1–3.3s）。 */
const TASK_CONCURRENCY = 2
/** 连续「整体不可用」失败的熔断阈值。 */
const TASK_FAILURE_LIMIT = 8
const bulkTasksError = ref<{ message: string; detail: string } | null>(null)

const firstLoading = computed(() => loading.value && !loaded.value)

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function resetTaskState(): void {
  for (const key of Object.keys(tasksByAccount)) delete tasksByAccount[Number(key)]
  for (const key of Object.keys(tasksError)) delete tasksError[Number(key)]
  for (const key of Object.keys(credentialMissing)) delete credentialMissing[Number(key)]
  for (const key of Object.keys(expanded)) delete expanded[Number(key)]
}

async function loadAccounts(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const payload = await getGrowthAccounts(current.signal)
    if (controller !== current) return
    accounts.value = Array.isArray(payload?.items) ? payload.items : []
    excluded.value = Number(payload?.excluded_enterprise || 0)
    loaded.value = true
    void nextTick(() => loadVisibleTasks())
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: failureText(t('page.growth.action.loadAccounts'), error), detail }
    toast.error(failureText(t('page.growth.action.loadAccounts'), error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

/** 停掉当前预取队列，在途请求由 signal 取消。 */
function abortTaskQueue(): void {
  tasksAbort?.abort()
  tasksAbort = null
  queueIds = new Set<number>()
  tasksToken += 1
  for (const key of Object.keys(tasksLoading)) delete tasksLoading[Number(key)]
}

/** 页面级取消：除了预取队列，再把零散的行内请求也一起停掉。 */
function abortEverything(): void {
  abortTaskQueue()
  pageAbort.abort()
  pageAbort = new AbortController()
}

/** 记下一次失败：凭证类 4xx 与其它错误分开放。 */
function recordTaskError(id: number, error: unknown): string {
  const message = failureText(t('page.growth.action.loadTasks'), error)
  tasksError[id] = message
  if (taskErrorKind(error) === 'credential') credentialMissing[id] = true
  else delete credentialMissing[id]
  return message
}

function clearTaskError(id: number): void {
  delete tasksError[id]
  delete credentialMissing[id]
}

/**
 * 拉指定账号的任务清单：并发 2；连续 8 个「整体不可用」类失败即熔断。
 * 只由 loadVisibleTasks() 调用，ids 永远是当前页需要拉的 active 账号（≤ 20 个）。
 */
async function loadTasksFor(ids: number[], force = false): Promise<void> {
  if (!supported.value) return
  abortTaskQueue()
  if (!ids.length) return
  const abort = new AbortController()
  const token = tasksToken
  tasksAbort = abort
  queueIds = new Set(ids)
  bulkTasksError.value = null

  let cursor = 0
  let attempted = 0
  let consecutiveFailures = 0
  let lastError = ''
  let tripped = false

  const stale = (): boolean => !alive || abort.signal.aborted || token !== tasksToken

  const worker = async (): Promise<void> => {
    while (cursor < ids.length && !tripped && !stale()) {
      const id = ids[cursor++]
      attempted += 1
      tasksLoading[id] = true
      clearTaskError(id)
      try {
        const payload = await getAccountTasks(id, force, abort.signal)
        if (stale()) return
        tasksByAccount[id] = payload
        consecutiveFailures = 0
      } catch (error) {
        if (stale() || isAbortError(error)) return
        lastError = recordTaskError(id, error)
        if (isOutageError(error)) {
          consecutiveFailures += 1
          if (consecutiveFailures >= TASK_FAILURE_LIMIT) tripped = true
        } else {
          consecutiveFailures = 0
        }
      } finally {
        if (!stale()) tasksLoading[id] = false
      }
    }
  }

  await Promise.all(Array.from({ length: TASK_CONCURRENCY }, () => worker()))
  if (tasksAbort === abort) {
    tasksAbort = null
    queueIds = new Set<number>()
  }
  if (!tripped || stale()) return

  // 熔断：逐行 502 只会把表格刷成一片红，改成顶部一条全局提示 + 一个重试入口。
  for (const key of Object.keys(tasksError)) delete tasksError[Number(key)]
  for (const key of Object.keys(credentialMissing)) delete credentialMissing[Number(key)]
  const rest = ids.length - attempted
  bulkTasksError.value = {
    message:
      t('page.growth.bulk.failed', { limit: TASK_FAILURE_LIMIT }) +
      (rest > 0 ? t('page.growth.bulk.remaining', { rest }) : ''),
    detail: lastError,
  }
}

/** 当前页需要拉的账号：active 且还没拿到（force 时已加载 / 已失败的也重拉）。 */
function loadVisibleTasks(force = false): void {
  if (!supported.value || !alive) return
  const items = pageData.value.items
  const ids = pendingTaskIds(items, force)
  if (!ids.length) return
  if (!force && tasksAbort && ids.every((id) => queueIds.has(id))) return
  const inFlight = items.filter((card) => !card.disabled && card.loading).map((card) => card.account.id)
  void loadTasksFor(Array.from(new Set([...inFlight, ...ids])), force)
}

function retryBulkTasks(): void {
  bulkTasksError.value = null
  loadVisibleTasks(false)
}

/** 单行重读（行内重试、做任务 / 领奖之后）：挂页面级 signal，卸载后不再写状态。 */
async function reloadAccountTasks(id: number): Promise<void> {
  const signal = pageAbort.signal
  tasksLoading[id] = true
  clearTaskError(id)
  try {
    const payload = await getAccountTasks(id, true, signal)
    if (!alive || signal.aborted) return
    tasksByAccount[id] = payload
  } catch (error) {
    if (!alive || signal.aborted || isAbortError(error)) return
    recordTaskError(id, error)
  } finally {
    if (alive && !signal.aborted) tasksLoading[id] = false
  }
}

function refresh(): void {
  void loadAccounts()
}

/* ------------------------------------------------------------------ *
 * 行 / KPI
 * ------------------------------------------------------------------ */

const cards = computed<GrowthCard[]>(() =>
  toGrowthCards(accounts.value, tasksByAccount, { loading: tasksLoading, errors: tasksError }),
)
const filtered = computed(() =>
  sortGrowthCards(filterGrowthCards(cards.value, { q: filters.q, state: stateValue.value })),
)
const pageData = computed(() =>
  pageGrowthCards(filtered.value, table.state.page, table.state.pageSize || 20),
)
const kpis = computed(() => growthKpis(cards.value))
const claimableTotal = computed(() => kpis.value.claimable)
const anyTaskLoading = computed(() => Object.values(tasksLoading).some(Boolean))
/** 任务类 KPI 在「一个都还没拿到」时显示骨架，不显示 0（10-visual-refresh §1.7）。 */
const taskKpiLoading = computed(
  () => firstLoading.value || (kpis.value.active > 0 && kpis.value.loadedActive === 0 && anyTaskLoading.value),
)
const partialHint = computed(() =>
  kpis.value.partial
    ? t('page.growth.stat.partial', { loaded: kpis.value.loadedActive, total: kpis.value.active })
    : '',
)

const headerMeta = computed(() =>
  loaded.value
    ? t('page.growth.meta', { active: fmtInt(kpis.value.active), total: fmtInt(kpis.value.accounts) })
    : '',
)

const kpiDoneValue = computed(() =>
  kpis.value.tasksTotal > 0 ? fmtInt(kpis.value.tasksDone) + ' / ' + fmtInt(kpis.value.tasksTotal) : null,
)
const kpiDoneHint = computed(() => {
  if (partialHint.value) return partialHint.value
  if (kpis.value.tasksTotal <= 0) return t('page.growth.kpi.doneEmpty')
  return t('page.growth.kpi.donePct', {
    pct: Math.round((kpis.value.tasksDone / kpis.value.tasksTotal) * 100),
  })
})

const stateOptions = computed(() => [
  { label: t('page.growth.state.active'), value: 'active' },
  { label: t('page.growth.state.claimable'), value: 'claimable' },
  { label: t('page.growth.state.runnable'), value: 'runnable' },
  { label: t('page.growth.state.disabled'), value: 'disabled' },
  { label: t('page.growth.state.all'), value: 'all' },
])

const search = ref(filters.q)
watch(
  () => filters.q,
  (value) => {
    search.value = value
  },
)

const hasFilters = computed(() => Boolean(filters.q) || stateValue.value !== DEFAULT_STATE)

const summary = computed(() =>
  t('page.growth.summaryShort', { count: fmtInt(filtered.value.length) }),
)

function onSearch(value: string): void {
  search.value = value
  table.setFilter('q', value || '')
}

function resetFilters(): void {
  search.value = ''
  table.setFilters({ q: '', state: '' })
}

function setState(value: string | null): void {
  const next = value || DEFAULT_STATE
  table.setFilter('state', next === DEFAULT_STATE ? '' : next)
}

function visibleTasks(card: GrowthCard): GrowthTask[] {
  return sortTasks(card.tasks)
}

function toggleExpand(id: number): void {
  expanded[id] = !expanded[id]
}

function claimableText(card: GrowthCard): string {
  if (!card.loaded || card.claimable <= 0) return FMT_EMPTY
  return t('page.growth.row.claimableValue', { count: card.claimable, credit: fmtCredit(card.claimableCredit) })
}

function progressText(card: GrowthCard): string {
  return progressOf(card.summary, card.profile).label
}

function isCredentialMissing(card: GrowthCard): boolean {
  return Boolean(credentialMissing[card.account.id])
}

/** 能量 / 连续天数：null 与非数值显示 —。 */
function numberOrDash(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(Number(value)) ? FMT_EMPTY : fmtInt(value)
}

const TASK_STATUS_KEYS: Record<string, MessageKey> = {
  completed: 'page.growth.taskStatus.completed',
  claimed: 'page.growth.taskStatus.claimed',
  not_accepted: 'page.growth.taskStatus.notAccepted',
  accepted: 'page.growth.taskStatus.accepted',
}

function taskStatusKey(task: GrowthTask | null | undefined): MessageKey {
  const status = String(task?.accept_status || '').toLowerCase()
  const key = TASK_STATUS_KEYS[status]
  if (key) return key
  return String(task?.level || '') === 'skip'
    ? 'page.growth.taskStatus.skipped'
    : 'page.growth.taskStatus.accepted'
}

function taskBadge(task: GrowthTask): GrowthTaskStatus {
  const status = taskStatusOf(task)
  return { label: t(taskStatusKey(task)), tone: status.tone }
}

function isClaimableTask(task: GrowthTask): boolean {
  return String(task.accept_status || '').toLowerCase() === 'completed'
}

function isClaimedTask(task: GrowthTask): boolean {
  return String(task.accept_status || '').toLowerCase() === 'claimed'
}

/* ------------------------------------------------------------------ *
 * 操作：做任务 / 领奖 / 成长动作
 * ------------------------------------------------------------------ */

interface TrackedJobHandle {
  snapshot: { value: JobSnapshot | null }
  percent: { value: number }
}

const jobHandles = ref<Array<{ id: string; name: string; handle: TrackedJobHandle }>>([])
const jobScopes: Array<ReturnType<typeof effectScope>> = []

function trackJob(id: string, name: string): void {
  const scope = effectScope()
  const handle = scope.run(() =>
    useJob(id, {
      pollUrl: '/api/growth/job/',
      onFinish: (snapshot: JobSnapshot) => {
        if (!alive) return
        const result = (snapshot.result || {}) as { tasks_done?: number; credit?: number; failed?: number[] }
        if (snapshot.status === 'failed') {
          toast.error(
            t('page.growth.job.failed', {
              name,
              reason: snapshot.error || t('page.growth.unknownError'),
            }),
          )
        } else {
          toast.success(
            t('page.growth.job.done', {
              name,
              tasks: Number(result.tasks_done || 0),
              credit: fmtCredit(Number(result.credit || 0)),
            }),
          )
        }
        loadVisibleTasks(true)
      },
    }),
  )
  if (!handle) return
  jobScopes.push(scope)
  // useJob 返回 ref 句柄；不 markRaw 会被深响应式二次解包，读 .value 会 TypeError。
  jobHandles.value.push({ id, name, handle: markRaw(handle) as unknown as TrackedJobHandle })
}

const jobItems = computed<JobItem[]>(() =>
  jobHandles.value
    .filter((entry) => entry.handle.snapshot.value !== null)
    .map((entry) => {
      const snapshot = entry.handle.snapshot.value as JobSnapshot
      const result = (snapshot.result || {}) as { tasks_done?: number; failed?: number[] }
      return {
        id: entry.id,
        name: entry.name,
        status: snapshot.status === 'running' ? 'running' : snapshot.status === 'failed' ? 'failed' : 'done',
        percent: entry.handle.percent.value,
        succeeded: Number(result.tasks_done || 0),
        failed: Array.isArray(result.failed) ? result.failed.length : 0,
        message: snapshot.current || snapshot.error || '',
      } as JobItem
    }),
)

function hideJob(id: string): void {
  jobHandles.value = jobHandles.value.filter((entry) => entry.id !== id)
}

function claimToast(payload: GrowthClaimPayload | null | undefined): string {
  const result = summarizeClaim(payload)
  const credit = fmtCredit(result.credit)
  if (result.failed > 0 && !result.tasks) {
    return t('page.growth.claim.failed', { count: result.failed })
  }
  return result.failed > 0
    ? t('page.growth.claim.okPartial', { count: result.tasks, credit, failed: result.failed })
    : t('page.growth.claim.ok', { count: result.tasks, credit })
}

async function runTasks(card: GrowthCard): Promise<void> {
  if (!card.canRun) {
    toast.info(t('page.growth.toast.noRunnable'))
    return
  }
  try {
    const job = await runGrowthTasks([card.account.id])
    if (job?.job_id) trackJob(job.job_id, t('page.growth.job.name', { name: card.account.name }))
    toast.info(job?.reused ? t('page.growth.toast.attached') : t('page.growth.toast.submitted'))
  } catch (error) {
    toast.error(failureText(t('page.growth.action.submitRun'), error))
  }
}

async function claimAccount(card: GrowthCard): Promise<void> {
  if (card.claimable <= 0) return
  try {
    const payload = await claimGrowth([card.account.id])
    toast.success(claimToast(payload))
    await reloadAccountTasks(card.account.id)
  } catch (error) {
    toast.error(failureText(t('page.growth.action.claim'), error))
  }
}

async function claimTask(card: GrowthCard, task: GrowthTask): Promise<void> {
  try {
    const payload = await claimGrowth([card.account.id], [task.task_code])
    toast.success(claimToast(payload))
    await reloadAccountTasks(card.account.id)
  } catch (error) {
    toast.error(failureText(t('page.growth.action.claim'), error))
  }
}

async function claimAll(): Promise<void> {
  const ids = cards.value.filter((card) => card.claimable > 0 && !card.disabled).map((card) => card.account.id)
  if (!ids.length) {
    toast.info(t('page.growth.toast.noClaimable'))
    return
  }
  const ok = await confirm({
    title: t('page.growth.confirm.claimAllTitle'),
    message: t('page.growth.confirm.claimAllBody', {
      count: ids.length,
      claimable: claimableTotal.value,
    }),
    okText: t('page.growth.confirm.claimAllOk'),
  })
  if (!ok) return
  try {
    const payload = await claimGrowth(ids)
    toast.success(claimToast(payload))
    loadVisibleTasks(true)
  } catch (error) {
    toast.error(failureText(t('page.growth.action.claimAll'), error))
  }
}

async function runAction(card: GrowthCard, item: GrowthActionItem): Promise<void> {
  if (card.disabled) {
    toast.info(t('page.growth.disabledHint'))
    return
  }
  const label = actionLabel(item.key)
  try {
    const result = await postGrowthAction(card.account.id, item.action, item.tier)
    const credit = Number(result?.credit || 0)
    if (result?.ok) {
      const reason = result.reason || t('page.growth.action.defaultReason')
      toast.success(
        credit > 0
          ? t('page.growth.action.done', { label, reason, credit: fmtCredit(credit) })
          : t('page.growth.action.donePlain', { label, reason }),
      )
    } else {
      toast.error(
        t('page.growth.action.donePlain', {
          label,
          reason: result?.reason || t('page.growth.action.unconfirmed'),
        }),
      )
    }
    await reloadAccountTasks(card.account.id)
  } catch (error) {
    toast.error(failureText(label, error))
  }
}

const refreshTimers: Array<ReturnType<typeof setTimeout>> = []

async function refreshCenter(card: GrowthCard): Promise<void> {
  try {
    const result = await refreshGrowthCenter(card.account.id)
    toast.info(result?.running ? t('page.growth.toast.refreshing') : t('page.growth.toast.refreshStarted'))
    const timer = setTimeout(() => {
      if (alive) void reloadAccountTasks(card.account.id)
    }, 2500)
    refreshTimers.push(timer)
  } catch (error) {
    toast.error(failureText(t('page.growth.action.refreshCenter'), error))
  }
}

/* ------------------------------------------------------------------ *
 * 行 ⋯ 菜单
 * ------------------------------------------------------------------ */

const ACTION_LABEL_KEYS: Record<string, MessageKey> = {
  checkin: 'page.growth.growthAction.checkin',
  open: 'page.growth.growthAction.open',
  gift: 'page.growth.growthAction.gift',
  draw: 'page.growth.growthAction.draw',
  'redeem-7': 'page.growth.growthAction.redeem7',
  'redeem-14': 'page.growth.growthAction.redeem14',
  'redeem-28': 'page.growth.growthAction.redeem28',
}

function actionLabel(key: string): string {
  const labelKey = ACTION_LABEL_KEYS[key]
  return labelKey ? t(labelKey) : key
}

function actionOptions(card: GrowthCard): DropdownOption[] {
  const options: DropdownOption[] = GROWTH_ACTIONS.map((item) => ({
    label: actionLabel(item.key),
    key: item.key,
    disabled: card.disabled,
  }))
  options.push({ type: 'divider', key: 'divider-1' })
  options.push({
    label: t('page.growth.action.refreshCenter'),
    key: 'refresh-center',
    disabled: card.disabled,
  })
  options.push({ label: t('page.growth.menu.openTasks'), key: 'open-tasks' })
  return options
}

function onActionSelect(card: GrowthCard, key: string | number): void {
  const name = String(key)
  const item = GROWTH_ACTIONS.find((action) => action.key === name)
  if (item) {
    void runAction(card, item)
    return
  }
  if (name === 'refresh-center') {
    void refreshCenter(card)
    return
  }
  if (name === 'open-tasks') {
    expanded[card.account.id] = true
  }
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null
let lastSelector = ''

onMounted(() => {
  alive = true
  lastSelector = platformStore.selector
  if (supported.value) void loadAccounts()
  offPlatform = platformStore.onChange((detail) => {
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    // 平台已切换：旧请求会带着新的 X-Platform 出去，先全部停掉再重新取。
    abortEverything()
    controller?.abort()
    controller = null
    bulkTasksError.value = null
    resetTaskState()
    if (!supported.value) {
      accounts.value = []
      loaded.value = false
      loading.value = false
      return
    }
    void loadAccounts()
  })
})

onBeforeUnmount(() => {
  alive = false
  offPlatform?.()
  offPlatform = null
  controller?.abort()
  controller = null
  // 离开页面立即取消：预取队列 + 行内请求 + 延时重读（第 3 轮审查 B1）。
  abortEverything()
  for (const timer of refreshTimers) clearTimeout(timer)
  refreshTimers.length = 0
  for (const scope of jobScopes) scope.stop()
  jobScopes.length = 0
})

// 回到 KeepAlive 缓存页：静默重取账号列表（不清任务状态、不出骨架）。任务预取并发低、
// 账号多时很重，离开不到 60s 就回来的不重取（11 §2）。
useRevisit(
  () => {
    if (supported.value) return loadAccounts()
    return undefined
  },
  { minIntervalMs: 60_000 },
)

watch(supported, (value) => {
  if (!value) {
    abortEverything()
    bulkTasksError.value = null
    return
  }
  if (!accounts.value.length) void loadAccounts()
})

/* 翻页 / 筛选 / 账号列表到位时，只补当前页的任务清单（监听 id 串而不是行对象）。 */
watch(
  () => pageData.value.items.map((card) => card.account.id).join(','),
  () => loadVisibleTasks(),
)

/** ErrorBlock 的标题：沿用「加载成长账号失败：原因」。 */
const loadErrorTitle = computed(() => loadError.value?.message || errorMessage(null))
</script>

<template>
  <div class="gw-page">
    <PageHeader :title="t('page.growth.titlePlain')" :help="helpText">
      <template #meta>
        <span v-if="headerMeta" class="gw-meta tnum">{{ headerMeta }}</span>
      </template>
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" :disabled="!supported" @click="refresh">
          {{ t('page.growth.refresh') }}
        </Btn>
        <Btn
          variant="primary"
          :icon="Trophy"
          :disabled="!supported || claimableTotal === 0 || loading"
          @click="claimAll"
        >
          {{ t('page.growth.claimAll') }}
        </Btn>
      </template>
    </PageHeader>

    <Empty v-if="!supported" :icon="Sparkles" :description="t('page.growth.unsupported')">
      <Btn variant="default" size="sm" @click="router.push('/accounts')">{{ t('page.growth.goAccounts') }}</Btn>
    </Empty>

    <ErrorBlock
      v-else-if="loadError && !accounts.length"
      :title="loadErrorTitle"
      :detail="loadError.detail"
      @retry="refresh"
    />

    <template v-else>
      <!-- KPI 行：任务类数字只来自已加载的账号，口径写在 hint 里 -->
      <section class="gw-kpis" :aria-label="t('page.growth.kpi.aria')">
        <KpiCard
          :label="t('page.growth.kpi.accounts')"
          :value="loaded ? fmtInt(kpis.active) : null"
          :unit="loaded ? t('page.growth.kpi.accountsUnit', { total: fmtInt(kpis.accounts) }) : undefined"
          :hint="excluded > 0 ? t('page.growth.stat.excluded', { count: excluded }) : t('page.growth.stat.caption')"
          :icon="Users"
          tone="neutral"
          :loading="firstLoading"
        />
        <KpiCard
          :label="t('page.growth.stat.claimable')"
          :value="taskKpiLoading ? null : fmtInt(kpis.claimable)"
          :unit="t('page.growth.kpi.claimableUnit')"
          :hint="t('page.growth.kpi.claimableHint', { credit: fmtCredit(kpis.claimableCredit) })"
          :icon="Gift"
          :tone="kpis.claimable > 0 ? 'warn' : 'neutral'"
          :loading="taskKpiLoading"
        />
        <KpiCard
          :label="t('page.growth.kpi.done')"
          :value="taskKpiLoading ? null : kpiDoneValue"
          :hint="kpiDoneHint"
          :icon="CheckCheck"
          tone="neutral"
          :loading="taskKpiLoading"
        />
        <KpiCard
          :label="t('page.growth.stat.energy')"
          :value="kpis.energy === null ? null : fmtInt(kpis.energy)"
          :hint="t('page.growth.kpi.energyHint')"
          :icon="BatteryCharging"
          tone="neutral"
          :loading="firstLoading"
        />
      </section>

      <div v-if="bulkTasksError" class="gw-bulk-error" role="alert">
        <TriangleAlert :size="16" class="gw-bulk-error__icon" />
        <div class="gw-bulk-error__body">
          <p class="gw-bulk-error__title">{{ bulkTasksError.message }}</p>
          <p v-if="bulkTasksError.detail" class="gw-bulk-error__detail">{{ bulkTasksError.detail }}</p>
        </div>
        <Btn size="sm" variant="default" @click="retryBulkTasks">{{ t('page.growth.retry') }}</Btn>
      </div>

      <section class="gw-card">
        <!-- 一行工具栏：左搜索 + 状态，右「共 N 个」与重置 -->
        <div class="gw-toolbar">
          <n-input
            :value="search"
            class="gw-search"
            size="small"
            :placeholder="t('page.growth.searchPlaceholder')"
            clearable
            @update:value="onSearch"
          />
          <n-select
            class="gw-select"
            size="small"
            :value="stateValue"
            :options="stateOptions"
            :aria-label="t('page.growth.stateAria')"
            @update:value="setState"
          />
          <Btn v-if="hasFilters" variant="quiet" size="sm" :icon="RotateCcw" @click="resetFilters">
            {{ t('common.reset') }}
          </Btn>
          <span class="gw-toolbar__summary tnum">{{ summary }}</span>
        </div>

        <div v-if="firstLoading" class="gw-skeleton" aria-busy="true" :aria-label="t('page.growth.loadingAria')">
          <div v-for="index in 6" :key="index" class="gw-skeleton__row mh-skeleton" />
        </div>

        <div v-else-if="pageData.items.length" class="gw-table-wrap">
          <table class="gw-table">
            <thead>
              <tr>
                <th class="gw-col-expand"><span class="gw-sr">{{ t('page.growth.col.expand') }}</span></th>
                <th>{{ t('page.growth.col.account') }}</th>
                <th class="gw-col-status">{{ t('page.growth.col.status') }}</th>
                <th class="is-num">{{ t('page.growth.col.streak') }}</th>
                <th class="is-num">{{ t('page.growth.col.energy') }}</th>
                <th class="gw-col-progress">{{ t('page.growth.col.progress') }}</th>
                <th class="is-num">{{ t('page.growth.col.claimable') }}</th>
                <th class="gw-col-actions"><span class="gw-sr">{{ t('page.growth.col.actions') }}</span></th>
              </tr>
            </thead>
            <tbody>
              <template v-for="card in pageData.items" :key="card.account.id">
                <tr
                  class="gw-row"
                  :class="{
                    'is-disabled': card.disabled,
                    'is-open': expanded[card.account.id],
                    'has-claimable': card.claimable > 0,
                  }"
                  :data-account="card.account.id"
                >
                  <td class="gw-col-expand">
                    <button
                      v-if="!card.disabled"
                      type="button"
                      class="gw-expand"
                      :aria-expanded="expanded[card.account.id] ? 'true' : 'false'"
                      :aria-label="
                        expanded[card.account.id]
                          ? t('page.growth.row.collapse', { name: card.account.name })
                          : t('page.growth.row.expand', { name: card.account.name })
                      "
                      @click="toggleExpand(card.account.id)"
                    >
                      <ChevronRight :size="16" />
                    </button>
                  </td>
                  <td class="gw-cell-name" :data-label="t('page.growth.col.account')">
                    <TitleTip :content="card.account.name"><span class="gw-name">{{ card.account.name }}</span></TitleTip>
                    <span v-if="card.progress.levelLabel" class="gw-level">{{ card.progress.levelLabel }}</span>
                  </td>
                  <td class="gw-col-status" :data-label="t('page.growth.col.status')">
                    <StatusDot
                      :tone="card.disabled ? 'neutral' : 'ok'"
                      :label="card.disabled ? t('page.growth.badge.disabled') : t('page.growth.badge.active')"
                    />
                  </td>
                  <td class="is-num tnum" :data-label="t('page.growth.col.streak')">
                    <span :class="{ 'gw-dash': card.account.streak_days == null }">
                      {{ numberOrDash(card.account.streak_days) }}
                    </span>
                  </td>
                  <td class="is-num tnum" :data-label="t('page.growth.col.energy')">
                    <span :class="{ 'gw-dash': card.account.energy == null }">
                      {{ numberOrDash(card.account.energy) }}
                    </span>
                  </td>
                  <td class="gw-col-progress" :data-label="t('page.growth.col.progress')">
                    <span v-if="card.disabled" class="gw-dash">{{ FMT_EMPTY }}</span>
                    <span v-else-if="card.loading" class="gw-muted">{{ t('page.growth.row.loading') }}</span>
                    <TitleTip
                      v-else-if="card.error && isCredentialMissing(card)"
                      :content="card.error"
                    ><span class="gw-muted">{{ t('page.growth.row.credentialMissing') }}</span></TitleTip>
                    <span v-else-if="card.error" class="gw-row-failed">{{ t('page.growth.row.failed') }}</span>
                    <span v-else-if="!card.loaded" class="gw-dash">{{ FMT_EMPTY }}</span>
                    <div v-else class="gw-progress">
                      <Meter
                        class="gw-progress__meter"
                        :value="card.progress.done"
                        :max="card.progress.total || 1"
                        tone="neutral"
                        :aria-label="t('page.growth.progressAria', { name: card.account.name })"
                      />
                      <span class="gw-progress__text tnum">{{ progressText(card) }}</span>
                    </div>
                  </td>
                  <td class="is-num tnum" :data-label="t('page.growth.col.claimable')">
                    <span :class="card.claimable > 0 ? 'gw-claimable' : 'gw-dash'">{{ claimableText(card) }}</span>
                  </td>
                  <td class="gw-col-actions">
                    <!-- 已禁用账号不露任何按钮（第 2 轮审查 P2）；其余行操作悬停 / 聚焦时出现 -->
                    <div v-if="!card.disabled" class="gw-actions">
                      <Btn size="sm" variant="quiet" :icon="Sparkles" :disabled="!card.canRun" @click="runTasks(card)">
                        {{ t('page.growth.card.run') }}
                      </Btn>
                      <Btn
                        size="sm"
                        variant="quiet"
                        :icon="Gift"
                        :disabled="!card.claimable"
                        @click="claimAccount(card)"
                      >
                        {{ card.claimable ? t('page.growth.card.claimCount', { count: card.claimable }) : t('page.growth.card.claim') }}
                      </Btn>
                      <n-dropdown
                        trigger="click"
                        placement="bottom-end"
                        :options="actionOptions(card)"
                        @select="(key: string | number) => onActionSelect(card, key)"
                      >
                        <Btn size="sm" variant="quiet" :icon="MoreHorizontal" :aria-label="t('page.growth.card.moreAria')" />
                      </n-dropdown>
                    </div>
                  </td>
                </tr>

                <tr v-if="!card.disabled && card.error && !isCredentialMissing(card)" class="gw-subrow gw-subrow--error">
                  <td colspan="8">
                    <div class="gw-row-error">
                      <TriangleAlert :size="14" />
                      <p>{{ card.error }}</p>
                      <Btn size="sm" variant="default" @click="reloadAccountTasks(card.account.id)">
                        {{ t('page.growth.retry') }}
                      </Btn>
                    </div>
                  </td>
                </tr>

                <tr v-else-if="!card.disabled && expanded[card.account.id]" class="gw-subrow">
                  <td colspan="8">
                    <div v-if="card.loading" class="gw-skeleton gw-skeleton--inline">
                      <div v-for="index in 3" :key="index" class="gw-skeleton__row mh-skeleton" />
                    </div>
                    <div v-else class="gw-tasks">
                      <div class="gw-tasks__head">
                        <span>{{ t('page.growth.tasks.title', { done: card.summary.done, total: card.summary.total }) }}</span>
                        <span v-if="card.claimable" class="gw-tasks__claimable">
                          {{ t('page.growth.tasks.claimable', { count: card.claimable }) }}
                        </span>
                      </div>
                      <ul v-if="card.tasks.length" class="gw-task-list">
                        <li
                          v-for="task in visibleTasks(card)"
                          :key="task.task_code"
                          class="gw-task"
                          :class="{ 'is-claimed': isClaimedTask(task) }"
                        >
                          <TitleTip :content="task.strategy || task.title || task.task_code">
                            <span class="gw-task__title">{{ task.title || task.task_code }}</span>
                          </TitleTip>
                          <span v-if="isClaimedTask(task)" class="gw-task__claimed">
                            <CheckCheck :size="14" />
                            {{ taskBadge(task).label }}
                          </span>
                          <Badge v-else :tone="taskBadge(task).tone" dot>{{ taskBadge(task).label }}</Badge>
                          <span v-if="task.level_label" class="gw-task__level">{{ task.level_label }}</span>
                          <span v-if="task.progress?.target" class="gw-task__progress tnum">
                            {{ fmtInt(task.progress.current ?? 0) }} / {{ fmtInt(task.progress.target) }}
                          </span>
                          <span class="gw-task__reward tnum">
                            {{ task.reward_credit ? t('page.growth.task.reward', { credit: fmtCredit(task.reward_credit) }) : FMT_EMPTY }}
                          </span>
                          <Btn
                            v-if="isClaimableTask(task)"
                            size="sm"
                            variant="quiet"
                            @click="claimTask(card, task)"
                          >
                            {{ t('page.growth.task.claim') }}
                          </Btn>
                        </li>
                      </ul>
                      <Empty v-else compact :description="t('page.growth.tasks.empty')" />
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>

        <Empty
          v-else
          :icon="Sparkles"
          :description="hasFilters ? t('page.growth.empty.filtered') : t('page.growth.empty.all')"
        >
          <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
            {{ t('page.growth.clearFilters') }}
          </Btn>
          <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="refresh">{{ t('page.growth.refresh') }}</Btn>
        </Empty>

        <div v-if="pageData.pageCount > 1 || excluded > 0" class="gw-foot">
          <p v-if="excluded > 0" class="gw-footnote">
            {{ t('page.growth.footnote.excluded', { count: excluded }) }}
          </p>
          <div v-if="pageData.pageCount > 1" class="gw-pager">
            <Btn variant="quiet" size="sm" :disabled="pageData.page <= 1" @click="table.setPage(pageData.page - 1)">
              {{ t('page.growth.pager.prev') }}
            </Btn>
            <span class="tnum">
              {{ t('page.growth.pager.text', { page: pageData.page, pageCount: pageData.pageCount, total: pageData.total }) }}
            </span>
            <Btn
              variant="quiet"
              size="sm"
              :disabled="pageData.page >= pageData.pageCount"
              @click="table.setPage(pageData.page + 1)"
            >
              {{ t('page.growth.pager.next') }}
            </Btn>
          </div>
        </div>
      </section>
    </template>

    <JobProgress :jobs="jobItems" :title="t('page.growth.job.title')" @hide="hideJob" />
  </div>
</template>

<style scoped>
.gw-page {
  display: grid;
  gap: var(--sp-5);
  min-width: 0;
  align-items: start;
}

.gw-meta {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

/* KPI：≥1280 一行 4 个，≥768 两个，手机一个（10-visual-refresh §1.4） */
.gw-kpis {
  display: grid;
  grid-auto-rows: 1fr;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
  /* 4 张卡都有 hint，拉齐成一条等高带，和下方表格卡上沿形成两条平行线 */
  align-items: stretch;
}

.gw-card {
  min-width: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-1);
  overflow: hidden;
}

.gw-toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--border-soft);
}

.gw-toolbar__summary {
  margin-left: auto;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.gw-search {
  width: 240px;
  max-width: 100%;
}

.gw-select {
  width: 150px;
  max-width: 100%;
}

.gw-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* ---------------- 表格（一行一个账号） ---------------- */
.gw-table-wrap {
  overflow-x: auto;
}

.gw-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-body);
  line-height: var(--lh-body);
}

.gw-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 0 var(--sp-3);
  height: 36px;
  background: var(--surface-2);
  color: var(--fg-muted);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
}

.gw-table td {
  height: var(--row-h, 44px);
  padding: 0 var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
  vertical-align: middle;
  color: var(--fg);
}

.gw-table tbody tr:last-child td {
  border-bottom: 0;
}

.gw-table .is-num {
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.gw-row td {
  transition: background var(--t-fast) var(--ease);
}

.gw-row:hover td {
  background: var(--surface-hover);
}

.gw-row.is-open td {
  background: var(--surface-hover);
  border-bottom-color: transparent;
}

.gw-row.is-disabled td {
  color: var(--fg-muted);
}

.gw-col-expand {
  width: 40px;
  padding-right: 0 !important;
}

.gw-col-status {
  width: 96px;
  white-space: nowrap;
}

.gw-col-progress {
  width: 200px;
}

.gw-col-actions {
  width: 1%;
  white-space: nowrap;
}

.gw-expand {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg-muted);
  cursor: pointer;
  transition: transform var(--t-base) var(--ease), background var(--t-fast) var(--ease);
}

.gw-expand:hover {
  background: var(--surface-2);
  color: var(--fg);
}

.gw-expand:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

.gw-row.is-open .gw-expand {
  transform: rotate(90deg);
}

.gw-cell-name {
  min-width: 180px;
}

.gw-name {
  display: inline-block;
  max-width: 240px;
  margin-right: var(--sp-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
  font-weight: 500;
}

.gw-level {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.gw-dash {
  color: var(--fg-faint);
}

.gw-muted {
  color: var(--fg-muted);
  font-size: var(--fs-small);
}

.gw-row-failed {
  color: var(--danger);
  font-size: var(--fs-small);
}

.gw-claimable {
  color: var(--warn);
  font-weight: 500;
}

.gw-progress {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.gw-progress__meter {
  flex: 1;
  min-width: 60px;
}

.gw-progress__text {
  flex: none;
  font-size: var(--fs-small);
  color: var(--fg-2);
}

/* 行操作：桌面端悬停 / 键盘聚焦时出现；有可领奖励的行常显；触屏一直显示（§1.5） */
.gw-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-1);
  opacity: 0;
  transition: opacity var(--t-fast) var(--ease);
}

.gw-row:hover .gw-actions,
.gw-row:focus-within .gw-actions,
.gw-row.is-open .gw-actions,
.gw-row.has-claimable .gw-actions {
  opacity: 1;
}

@media (hover: none) {
  .gw-actions {
    opacity: 1;
  }
}

/* 展开行 / 错误行 */
.gw-subrow td {
  padding: var(--sp-3) var(--sp-4) var(--sp-4) 52px;
  background: var(--surface-2);
  height: auto;
}

.gw-row-error {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--danger);
}

.gw-row-error p {
  margin: 0;
}

.gw-bulk-error {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  border: 1px solid var(--danger-soft);
  border-radius: var(--r-card);
  background: var(--danger-soft);
}

.gw-bulk-error__icon {
  flex: none;
  color: var(--danger);
  margin-top: 2px;
}

.gw-bulk-error__body {
  flex: 1;
  min-width: 0;
}

.gw-bulk-error__title {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg);
}

.gw-bulk-error__detail {
  margin: 2px 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gw-tasks {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

.gw-tasks__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.gw-tasks__claimable {
  color: var(--warn);
}

.gw-task-list {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
  background: var(--surface);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
}

.gw-task {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
  min-width: 0;
  min-height: var(--row-h-compact, 36px);
  padding: var(--sp-1) var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.gw-task:last-child {
  border-bottom: 0;
}

.gw-task.is-claimed .gw-task__title {
  color: var(--fg-muted);
}

.gw-task__title {
  flex: 1 1 220px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
}

.gw-task__claimed {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--fg-faint);
}

.gw-task__level,
.gw-task__progress {
  color: var(--fg-muted);
}

.gw-task__reward {
  min-width: 72px;
  text-align: right;
  color: var(--fg-2);
}

.gw-skeleton {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-4);
}

.gw-skeleton--inline {
  padding: 0;
}

.gw-skeleton__row {
  height: 14px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
}

.gw-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--border-soft);
}

.gw-pager {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.gw-footnote {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

@media (max-width: 1279px) {
  .gw-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .gw-col-progress {
    width: 160px;
  }
}

@media (max-width: 767px) {
  .gw-kpis {
    grid-template-columns: minmax(0, 1fr);
  }

  .gw-search,
  .gw-select {
    width: 100%;
  }

  .gw-toolbar__summary {
    margin-left: 0;
  }

  .gw-table thead {
    display: none;
  }

  .gw-table,
  .gw-table tbody,
  .gw-table tr,
  .gw-table td {
    display: block;
    width: 100%;
    height: auto;
  }

  .gw-row {
    display: grid !important;
    grid-template-columns: 32px minmax(0, 1fr);
    gap: 2px var(--sp-2);
    padding: var(--sp-3);
    border-bottom: 1px solid var(--border-soft);
  }

  .gw-row td {
    grid-column: 2;
    padding: 0;
    border: 0;
    background: transparent !important;
    text-align: left !important;
  }

  .gw-row td.gw-col-expand {
    grid-column: 1;
    grid-row: 1;
  }

  .gw-row td[data-label]:not(.gw-cell-name)::before {
    content: attr(data-label) '：';
    color: var(--fg-muted);
    font-size: var(--fs-small);
  }

  .gw-col-progress,
  .gw-col-actions,
  .gw-col-status {
    width: auto;
  }

  .gw-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
    margin-top: var(--sp-1);
    opacity: 1;
  }

  .gw-name {
    max-width: 100%;
  }

  .gw-subrow td {
    padding: var(--sp-3);
  }
}

@media (prefers-reduced-motion: reduce) {
  .gw-expand,
  .gw-actions,
  .gw-row td {
    transition: none;
  }
}
</style>
