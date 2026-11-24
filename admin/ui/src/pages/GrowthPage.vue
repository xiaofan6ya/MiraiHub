<script setup lang="ts">
/**
 * S24 成长计划 /growth（模板 A/B 混合：统计条 + 账号成长卡）
 *
 * 契约：docs/ui-v2/contracts/growth.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-b.md P9
 * 旧版：#panel-growth（#growthCenter）+ growth_center.js / growth_center.css + console-app.js 的 /api/growth/*
 *
 * 三条口径：
 *   1. 平台能力不硬编码：platformStore.supports('growth')（PLATFORM_ONLY_TABS：growth → workbuddy）；
 *      store 里那份映射是唯一真值，本页只读它。
 *   2. 「可做」「可领」只看后端字段：actionable / accept_status / summary.claimable。
 *   3. 任务清单接口单次 1–3.3s（后端 3s 缓存）→ 逐账号并发 2 拉取，先到先渲染，失败的卡单独报错；
 *      但连续 8 个都失败说明上游/网关整体挂了 → 熔断整条队列，顶部只留一条全局提示；
 *      页面卸载 / 切平台 / 新一轮预取都会 abort 旧队列（194 个账号全 502 时不能一路跑完）。
 *
 * 文案全部走 useI18n().t（page.growth.*）：模板里的 t() 在 render 里读 locale，
 * 切语言立即重算；api/growth.ts 的纯函数只管数值与语气，标签由本页按 key 取。
 */
import { computed, effectScope, markRaw, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NDropdown, NInput, NSelect, NSkeleton, type DropdownOption } from 'naive-ui'
import { MoreHorizontal, RefreshCw, Sparkles, TriangleAlert, Trophy } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Empty,
  ErrorBlock,
  FilterBar,
  JobProgress,
  Meter,
  StatStrip,
  type FilterChip,
  type JobItem,
} from '@/components'
import { useConfirm, useJob, useTableQuery, useToast, type JobSnapshot } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import { failureText } from '@/utils/error'
import { FMT_EMPTY, fmtCredit, fmtInt } from '@/utils/format'
import { useI18n, type MessageKey } from '@/i18n'
import {
  GROWTH_ACTIONS,
  claimGrowth,
  filterGrowthCards,
  getAccountTasks,
  getGrowthAccounts,
  growthStats,
  pageGrowthCards,
  postGrowthAction,
  progressOf,
  refreshGrowthCenter,
  runGrowthTasks,
  sortTasks,
  summarizeClaim,
  taskStatusOf,
  toGrowthCards,
  type GrowthAccountTasksPayload,
  type GrowthActionItem,
  type GrowthCard,
  type GrowthCenterAccount,
  type GrowthClaimPayload,
  type GrowthStateFilter,
  type GrowthTask,
  type GrowthTaskStatus,
} from '@/api/growth'

const { t } = useI18n()
/** 页头说明（语种跟随 useI18n）。 */
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

const supported = computed(() => platformStore.supports('growth'))
const platformLabel = computed(
  () => platformStore.currentSpec?.label || platformStore.platform || t('page.growth.platformFallback'),
)
const title = computed(() => t('page.growth.title', { platform: platformLabel.value }))

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const accounts = ref<GrowthCenterAccount[]>([])
const excluded = ref(0)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const tasksByAccount = reactive<Record<number, GrowthAccountTasksPayload | undefined>>({})
const tasksLoading = reactive<Record<number, boolean>>({})
const tasksError = reactive<Record<number, string>>({})
const expanded = reactive<Record<number, boolean>>({})

let controller: AbortController | null = null
/** 任务清单批量预取的取消句柄：卸载 / 切平台 / 新一轮预取都会 abort 它。 */
let tasksAbort: AbortController | null = null
let tasksToken = 0

/** 任务清单并发上限（后端单次 1–3.3s，串行会等到天荒地老）。 */
const TASK_CONCURRENCY = 2
/**
 * 连续失败熔断阈值。整站 502 时 194 个账号会一路排队到底（每张卡都留一条 502），
 * 连续 8 个都失败已经能判定上游/网关整体不可用，继续排队只是刷屏。
 */
const TASK_FAILURE_LIMIT = 8
/** 熔断后的全局失败提示（顶部一次），替代逐卡错误。 */
const bulkTasksError = ref<{ message: string; detail: string } | null>(null)

const firstLoading = computed(() => loading.value && !accounts.value.length)

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

async function loadAccounts(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const payload = await getGrowthAccounts(current.signal)
    accounts.value = Array.isArray(payload?.items) ? payload.items : []
    excluded.value = Number(payload?.excluded_enterprise || 0)
    void loadAllTasks()
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

/** 停掉当前预取队列（卸载 / 切平台 / 新一轮预取调用），在途请求由 signal 取消。 */
function abortTaskQueue(): void {
  tasksAbort?.abort()
  tasksAbort = null
  tasksToken += 1
  for (const key of Object.keys(tasksLoading)) delete tasksLoading[Number(key)]
}

/**
 * 逐账号拉任务清单：并发 2（后端单次 1–3.3s，串行会等到天荒地老）。
 *
 * 两个上限缺一个都会在整站 502 时刷屏：
 *   · 并发 2 —— 不把后端打满；
 *   · 连续失败 8 个即熔断 —— 停掉整条队列，卡片级错误收成顶部一条全局提示。
 * 队列挂在 tasksAbort 上，卸载 / 切平台时 abort，迟到的响应直接丢掉。
 */
async function loadAllTasks(force = false): Promise<void> {
  if (!supported.value) return
  abortTaskQueue()
  const abort = new AbortController()
  const token = tasksToken
  const ids = accounts.value
    .map((account) => account.id)
    .filter((id) => force || !tasksByAccount[id])
  if (!ids.length) return
  tasksAbort = abort
  bulkTasksError.value = null

  let cursor = 0
  let attempted = 0
  let consecutiveFailures = 0
  let lastError = ''
  let tripped = false

  const stale = (): boolean => abort.signal.aborted || token !== tasksToken

  const worker = async (): Promise<void> => {
    while (cursor < ids.length && !tripped && !stale()) {
      const id = ids[cursor++]
      attempted += 1
      tasksLoading[id] = true
      delete tasksError[id]
      try {
        const payload = await getAccountTasks(id, force, abort.signal)
        if (stale()) return
        tasksByAccount[id] = payload
        consecutiveFailures = 0
      } catch (error) {
        if (stale() || isAbortError(error)) return
        consecutiveFailures += 1
        lastError = failureText(t('page.growth.action.loadTasks'), error)
        tasksError[id] = lastError
        if (consecutiveFailures >= TASK_FAILURE_LIMIT) tripped = true
      } finally {
        tasksLoading[id] = false
      }
    }
  }

  await Promise.all(Array.from({ length: TASK_CONCURRENCY }, () => worker()))
  if (tasksAbort === abort) tasksAbort = null
  if (!tripped || stale()) return

  // 熔断：逐卡 502 只会把页面刷成一片红，改成顶部一条全局提示 + 一个重试入口。
  for (const key of Object.keys(tasksError)) delete tasksError[Number(key)]
  const rest = ids.length - attempted
  bulkTasksError.value = {
    message:
      t('page.growth.bulk.failed', { limit: TASK_FAILURE_LIMIT }) +
      (rest > 0 ? t('page.growth.bulk.remaining', { rest }) : ''),
    detail: lastError,
  }
}

/** 熔断后的重试：只补没拿到的账号（已加载的跳过），不会再跑一遍全量。 */
function retryBulkTasks(): void {
  bulkTasksError.value = null
  void loadAllTasks(false)
}

async function reloadAccountTasks(id: number): Promise<void> {
  tasksLoading[id] = true
  delete tasksError[id]
  try {
    tasksByAccount[id] = await getAccountTasks(id, true)
  } catch (error) {
    tasksError[id] = failureText(t('page.growth.action.loadTasks'), error)
  } finally {
    tasksLoading[id] = false
  }
}

function refresh(): void {
  void loadAccounts()
}

/* ------------------------------------------------------------------ *
 * 卡片 / 统计
 * ------------------------------------------------------------------ */

const cards = computed<GrowthCard[]>(() =>
  toGrowthCards(accounts.value, tasksByAccount, { loading: tasksLoading, errors: tasksError }),
)
const filtered = computed(() =>
  filterGrowthCards(cards.value, { q: filters.q, state: filters.state }),
)
const pageData = computed(() =>
  pageGrowthCards(filtered.value, table.state.page, table.state.pageSize || 20),
)
const claimableTotal = computed(() =>
  cards.value.reduce((sum, card) => sum + Number(card.claimable || 0), 0),
)
const claimableCredit = computed(() =>
  cards.value.reduce((sum, card) => sum + Number(card.claimableCredit || 0), 0),
)

/** 统计条标签按 api/growth.ts:growthStats 的固定顺序取（数值与语气仍由那份纯函数算）。 */
const STAT_LABEL_KEYS: MessageKey[] = [
  'page.growth.stat.accounts',
  'page.growth.stat.tasks',
  'page.growth.stat.done',
  'page.growth.stat.claimable',
  'page.growth.stat.credit',
  'page.growth.stat.energy',
]

const stats = computed<ReturnType<typeof growthStats>>(() =>
  growthStats(cards.value, excluded.value).map((item, index) => {
    const labelKey = STAT_LABEL_KEYS[index] ?? STAT_LABEL_KEYS[0]
    return {
      ...item,
      label: t(labelKey),
      foot:
        index === 0
          ? excluded.value > 0
            ? t('page.growth.stat.excluded', { count: excluded.value })
            : t('page.growth.stat.caption')
          : item.foot,
    }
  }),
)

const stateOptions = computed(() => [
  { label: t('page.growth.state.all'), value: '' },
  { label: t('page.growth.state.claimable'), value: 'claimable' },
  { label: t('page.growth.state.runnable'), value: 'runnable' },
  { label: t('page.growth.state.disabled'), value: 'disabled' },
])

const search = ref(filters.q)
watch(
  () => filters.q,
  (value) => {
    search.value = value
  },
)

const chips = computed<FilterChip[]>(() => {
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: t('page.growth.chip.search'), value: filters.q })
  if (filters.state) {
    const hit = stateOptions.value.find((item) => item.value === filters.state)
    list.push({ key: 'state', label: t('page.growth.chip.state'), value: hit ? hit.label : filters.state })
  }
  return list
})

const summary = computed(() =>
  t('page.growth.summary', {
    accounts: cards.value.length,
    claimable: claimableTotal.value,
    credit: fmtCredit(claimableCredit.value),
  }),
)

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) {
    next[name] = value === null || value === undefined ? '' : String(value)
  }
  table.setFilters(next)
}

function resetFilters(): void {
  search.value = ''
  table.setFilters({ q: '', state: '' })
}

function removeChip(key: string): void {
  if (key === 'q') search.value = ''
  table.setFilter(key, '')
}

function setState(value: string | null): void {
  table.setFilter('state', value || '')
}

function visibleTasks(card: GrowthCard): GrowthTask[] {
  const sorted = sortTasks(card.tasks)
  return expanded[card.account.id] ? sorted : sorted.slice(0, 4)
}

function toggleExpand(id: number): void {
  expanded[id] = !expanded[id]
}

function hasMoreTasks(card: GrowthCard): boolean {
  return card.tasks.length > 4
}

function progressText(card: GrowthCard): string {
  const progress = progressOf(card.summary, card.profile)
  return progress.label
}

/** 任务状态文案：语气仍取 api/growth.ts:taskStatusOf，标签按 key 跟随语言。 */
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

/** 单任务领奖按钮只看上游原值（不用译后的文案做判断）。 */
function isClaimableTask(task: GrowthTask): boolean {
  return String(task.accept_status || '').toLowerCase() === 'completed'
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
        void loadAllTasks(true)
      },
    }),
  )
  if (!handle) return
  jobScopes.push(scope)
  // useJob 返回的是 ref 句柄；不 markRaw 会被 ref 深响应式二次解包，
  // jobItems 再读 handle.snapshot.value 就会 TypeError（真实点「做任务」时崩）。
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

/** claim 结果 → Toast 文案（数量与积分仍由 api/growth.ts:summarizeClaim 汇总）。 */
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
  const ids = cards.value.filter((card) => card.claimable > 0).map((card) => card.account.id)
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
    await loadAllTasks(true)
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
      void reloadAccountTasks(card.account.id)
    }, 2500)
    refreshTimers.push(timer)
  } catch (error) {
    toast.error(failureText(t('page.growth.action.refreshCenter'), error))
  }
}

/* ------------------------------------------------------------------ *
 * 卡片 ⋯ 菜单
 * ------------------------------------------------------------------ */

/** 成长动作文案：动作/档位仍是 api/growth.ts:GROWTH_ACTIONS 的真值，这里只按 key 取标签。 */
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

function currentSelector(): string {
  return platformStore.selector
}

onMounted(() => {
  lastSelector = currentSelector()
  if (supported.value) void loadAccounts()
  offPlatform = platformStore.onChange((detail) => {
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    // 平台上下文已切换：旧队列的请求会带着新的 X-Platform 出去，先停掉再重新取。
    abortTaskQueue()
    bulkTasksError.value = null
    if (!supported.value) {
      accounts.value = []
      return
    }
    for (const key of Object.keys(expanded)) delete expanded[Number(key)]
    void loadAccounts()
  })
})

onBeforeUnmount(() => {
  offPlatform?.()
  offPlatform = null
  controller?.abort()
  controller = null
  // 离开页面就别再把剩下的任务清单请求跑完（会刷到别的页面的控制台里）。
  abortTaskQueue()
  for (const timer of refreshTimers) clearTimeout(timer)
  refreshTimers.length = 0
  for (const scope of jobScopes) scope.stop()
  jobScopes.length = 0
})

watch(supported, (value) => {
  if (!value) {
    abortTaskQueue()
    bulkTasksError.value = null
    return
  }
  if (!accounts.value.length) void loadAccounts()
})
</script>

<template>
  <div class="gw-page">
    <PageHeader :title="title" :help="helpText">
      <template #actions>
        <Btn
          variant="primary"
          :icon="Trophy"
          :disabled="!supported || claimableTotal === 0 || loading"
          @click="claimAll"
        >
          {{ t('page.growth.claimAll') }}
        </Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" :disabled="!supported" @click="refresh">
          {{ t('page.growth.refresh') }}
        </Btn>
      </template>
    </PageHeader>

    <Empty v-if="!supported" :description="t('page.growth.unsupported')">
      <Btn variant="default" size="sm" @click="router.push('/accounts')">{{ t('page.growth.goAccounts') }}</Btn>
    </Empty>

    <ErrorBlock
      v-else-if="loadError && !accounts.length"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="refresh"
    />

    <div
      v-else-if="firstLoading"
      class="gw-skeleton"
      aria-busy="true"
      :aria-label="t('page.growth.loadingAria')"
    >
      <div class="gw-skeleton__strip"><n-skeleton text :repeat="2" /></div>
      <div class="gw-skeleton__grid">
        <div v-for="index in 4" :key="index" class="gw-skeleton__card"><n-skeleton text :repeat="5" /></div>
      </div>
    </div>

    <template v-else>
      <div v-if="bulkTasksError" class="gw-bulk-error" role="alert">
        <TriangleAlert :size="16" class="gw-bulk-error__icon" />
        <div class="gw-bulk-error__body">
          <p class="gw-bulk-error__title">{{ bulkTasksError.message }}</p>
          <p v-if="bulkTasksError.detail" class="gw-bulk-error__detail">{{ bulkTasksError.detail }}</p>
        </div>
        <Btn size="sm" variant="default" @click="retryBulkTasks">{{ t('page.growth.retry') }}</Btn>
      </div>

      <StatStrip :items="stats" :columns="6" />

      <Card>
        <FilterBar
          :chips="chips"
          :summary="summary"
          :loading="loading"
          @change="onFilterChange"
          @reset="resetFilters"
          @remove="removeChip"
        >
          <template #default>
            <n-input
              v-model:value="search"
              class="gw-search"
              :placeholder="t('page.growth.searchPlaceholder')"
              clearable
              @update:value="(value: string) => onFilterChange({ q: value })"
            />
            <n-select
              class="gw-select"
              :value="filters.state"
              :options="stateOptions"
              :aria-label="t('page.growth.stateAria')"
              @update:value="setState"
            />
          </template>
        </FilterBar>

        <div v-if="pageData.items.length" class="gw-grid">
          <article v-for="card in pageData.items" :key="card.account.id" class="gw-card" :class="{ 'is-disabled': card.disabled }">
            <header class="gw-card__head">
              <div class="gw-card__title">
                <h3 :title="card.account.name">{{ card.account.name }}</h3>
                <div class="gw-card__chips">
                  <Badge :tone="card.disabled ? 'neutral' : 'ok'">
                    {{ card.disabled ? t('page.growth.badge.disabled') : t('page.growth.badge.active') }}
                  </Badge>
                  <Badge tone="neutral">
                    {{ t('page.growth.badge.streak', { days: card.account.streak_days ?? FMT_EMPTY }) }}
                  </Badge>
                  <Badge tone="neutral">
                    {{ t('page.growth.badge.energy', { value: card.account.energy ?? FMT_EMPTY }) }}
                  </Badge>
                  <Badge v-if="card.progress.levelLabel" tone="brand">{{ card.progress.levelLabel }}</Badge>
                </div>
              </div>
              <span class="gw-card__progress-text tnum">{{ progressText(card) }}</span>
            </header>

            <div class="gw-card__meter">
              <Meter
                :value="card.progress.done"
                :max="card.progress.total || 1"
                tone="brand"
                :aria-label="t('page.growth.progressAria', { name: card.account.name })"
              />
              <span class="gw-card__meter-note">
                {{ t('page.growth.meterNote', { percent: card.progress.percent }) }}
              </span>
            </div>

            <div v-if="card.loading" class="gw-card__loading"><n-skeleton text :repeat="3" /></div>

            <div v-else-if="card.error" class="gw-card__error">
              <p>{{ card.error }}</p>
              <Btn size="sm" variant="default" @click="reloadAccountTasks(card.account.id)">
                {{ t('page.growth.retry') }}
              </Btn>
            </div>

            <div v-else class="gw-tasks">
              <div class="gw-tasks__head">
                <span>{{ t('page.growth.tasks.title', { done: card.summary.done, total: card.summary.total }) }}</span>
                <span v-if="card.claimable" class="gw-tasks__claimable">
                  {{ t('page.growth.tasks.claimable', { count: card.claimable }) }}
                </span>
                <Btn v-if="hasMoreTasks(card)" size="sm" variant="quiet" @click="toggleExpand(card.account.id)">
                  {{ expanded[card.account.id] ? t('page.growth.tasks.collapse') : t('page.growth.tasks.expand') }}
                </Btn>
              </div>
              <ul v-if="card.tasks.length" class="gw-task-list">
                <li v-for="task in visibleTasks(card)" :key="task.task_code" class="gw-task">
                  <span class="gw-task__title" :title="task.strategy || task.title || task.task_code">
                    {{ task.title || task.task_code }}
                  </span>
                  <Badge :tone="taskBadge(task).tone">{{ taskBadge(task).label }}</Badge>
                  <Badge v-if="task.level_label" tone="neutral">{{ task.level_label }}</Badge>
                  <span v-if="task.progress?.target" class="gw-task__progress tnum">
                    {{ task.progress.current ?? 0 }}/{{ task.progress.target }}
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

            <footer class="gw-card__actions">
              <Btn size="sm" variant="default" :icon="Sparkles" :disabled="card.disabled || !card.canRun" @click="runTasks(card)">
                {{ t('page.growth.card.run') }}
              </Btn>
              <Btn size="sm" variant="quiet" :disabled="card.disabled || !card.claimable" @click="claimAccount(card)">
                {{ t('page.growth.card.claim') }}{{ card.claimable ? ' ' + card.claimable : '' }}
              </Btn>
              <n-dropdown
                trigger="click"
                placement="bottom-end"
                :options="actionOptions(card)"
                @select="(key: string | number) => onActionSelect(card, key)"
              >
                <Btn size="sm" variant="quiet" :icon="MoreHorizontal" :aria-label="t('page.growth.card.moreAria')">
                  {{ t('page.growth.card.more') }}
                </Btn>
              </n-dropdown>
            </footer>
          </article>
        </div>

        <Empty
          v-else
          :description="filters.q || filters.state ? t('page.growth.empty.filtered') : t('page.growth.empty.all')"
        >
          <Btn v-if="filters.q || filters.state" variant="default" size="sm" @click="resetFilters">
            {{ t('page.growth.clearFilters') }}
          </Btn>
          <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="refresh">{{ t('page.growth.refresh') }}</Btn>
        </Empty>

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

        <p v-if="excluded > 0" class="gw-footnote">
          {{ t('page.growth.footnote.excluded', { count: excluded }) }}
        </p>
      </Card>
    </template>

    <JobProgress :jobs="jobItems" :title="t('page.growth.job.title')" @hide="hideJob" />
  </div>
</template>

<style scoped>
.gw-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.gw-search {
  width: 240px;
  max-width: 100%;
}

.gw-select {
  width: 160px;
  max-width: 100%;
}

.gw-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--sp-4);
  margin-top: var(--sp-4);
}

.gw-card {
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  min-width: 0;
}

.gw-card.is-disabled {
  opacity: 0.6;
}

.gw-card__head {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  min-width: 0;
}

.gw-card__title {
  flex: 1;
  min-width: 0;
}

.gw-card__title h3 {
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gw-card__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1);
  margin-top: var(--sp-1);
}

.gw-card__progress-text {
  font-size: var(--fs-small);
  color: var(--fg-2);
  flex: none;
}

.gw-card__meter {
  display: grid;
  gap: 2px;
}

.gw-card__meter-note {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.gw-card__error {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--danger);
}

.gw-bulk-error {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  border: 1px solid var(--danger-soft);
  border-radius: var(--r-ctl);
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
  gap: var(--sp-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.gw-task {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-width: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.gw-task__title {
  flex: 1 1 140px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
}

.gw-task__progress {
  color: var(--fg-muted);
}

.gw-task__reward {
  color: var(--fg-2);
}

.gw-card__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.gw-skeleton {
  display: grid;
  gap: var(--sp-4);
}

.gw-skeleton__strip,
.gw-skeleton__card {
  padding: var(--sp-5);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
}

.gw-skeleton__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--sp-4);
}

.gw-pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.gw-footnote {
  margin-top: var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
}

@media (max-width: 640px) {
  .gw-grid,
  .gw-skeleton__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .gw-search,
  .gw-select {
    width: 100%;
  }
}
</style>
