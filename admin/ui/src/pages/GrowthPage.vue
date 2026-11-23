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
 *   3. 任务清单接口单次 1–3.3s（后端 3s 缓存）→ 逐账号并发 2 拉取，先到先渲染，失败的卡单独报错。
 */
import { computed, effectScope, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NDropdown, NInput, NSelect, NSkeleton, type DropdownOption } from 'naive-ui'
import { MoreHorizontal, RefreshCw, Sparkles, Trophy } from 'lucide-vue-next'
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
import { fmtCredit, fmtInt } from '@/utils/format'
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
  type GrowthStateFilter,
  type GrowthTask,
} from '@/api/growth'

const HELP_TEXT =
  '成长计划只对声明支持的平台有意义；任务进度来自上游真实账号，做任务与领奖都会写入积分流水。' +
  '任务清单单次拉取较慢（后端有 3 秒缓存），逐账号加载，失败只影响那一张卡。'

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
  () => platformStore.currentSpec?.label || platformStore.platform || '当前平台',
)
const title = computed(() => '成长计划 · ' + platformLabel.value)

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
let tasksToken = 0

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
    loadError.value = { message: failureText('加载成长账号', error), detail }
    toast.error(failureText('加载成长账号', error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

/** 逐账号拉任务清单：并发 2（后端单次 1–3.3s，串行会等到天荒地老）。 */
async function loadAllTasks(force = false): Promise<void> {
  if (!supported.value) return
  const token = ++tasksToken
  const ids = accounts.value.map((account) => account.id)
  if (!ids.length) return
  let cursor = 0
  const worker = async (): Promise<void> => {
    while (cursor < ids.length) {
      const index = cursor++
      const id = ids[index]
      if (!force && tasksByAccount[id]) continue
      tasksLoading[id] = true
      delete tasksError[id]
      try {
        tasksByAccount[id] = await getAccountTasks(id, force)
      } catch (error) {
        tasksError[id] = failureText('加载任务清单', error)
      } finally {
        tasksLoading[id] = false
      }
      if (token !== tasksToken) return
    }
  }
  await Promise.all([worker(), worker()])
}

async function reloadAccountTasks(id: number): Promise<void> {
  tasksLoading[id] = true
  delete tasksError[id]
  try {
    tasksByAccount[id] = await getAccountTasks(id, true)
  } catch (error) {
    tasksError[id] = failureText('加载任务清单', error)
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
const stats = computed(() => growthStats(cards.value, excluded.value))
const claimableTotal = computed(() =>
  cards.value.reduce((sum, card) => sum + Number(card.claimable || 0), 0),
)
const claimableCredit = computed(() =>
  cards.value.reduce((sum, card) => sum + Number(card.claimableCredit || 0), 0),
)

const stateOptions = [
  { label: '全部账号', value: '' },
  { label: '有可领奖励', value: 'claimable' },
  { label: '有可做任务', value: 'runnable' },
  { label: '已禁用', value: 'disabled' },
]

const search = ref(filters.q)
watch(
  () => filters.q,
  (value) => {
    search.value = value
  },
)

const chips = computed<FilterChip[]>(() => {
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: '搜索', value: filters.q })
  if (filters.state) {
    const hit = stateOptions.find((item) => item.value === filters.state)
    list.push({ key: 'state', label: '状态', value: hit ? hit.label : filters.state })
  }
  return list
})

const summary = computed(
  () =>
    '共 ' + cards.value.length + ' 个账号 · 可领 ' + claimableTotal.value + ' 条 · ' +
    fmtCredit(claimableCredit.value) + ' 积分',
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
        if (snapshot.status === 'failed') toast.error(name + ' 失败：' + (snapshot.error || '未知错误'))
        else {
          toast.success(
            name + ' 完成：' + Number(result.tasks_done || 0) + ' 个任务 · ' +
            fmtCredit(Number(result.credit || 0)) + ' 积分',
          )
        }
        void loadAllTasks(true)
      },
    }),
  )
  if (!handle) return
  jobScopes.push(scope)
  jobHandles.value.push({ id, name, handle: handle as unknown as TrackedJobHandle })
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

async function runTasks(card: GrowthCard): Promise<void> {
  if (!card.canRun) {
    toast.info('这个账号当前没有可自动完成的任务')
    return
  }
  try {
    const job = await runGrowthTasks([card.account.id])
    if (job?.job_id) trackJob(job.job_id, card.account.name + ' 做任务')
    toast.info(job?.reused ? '已有任务在跑，已接上进度' : '做任务已提交后台执行')
  } catch (error) {
    toast.error(failureText('提交做任务', error))
  }
}

async function claimAccount(card: GrowthCard): Promise<void> {
  if (card.claimable <= 0) return
  try {
    const payload = await claimGrowth([card.account.id])
    toast.success(summarizeClaim(payload).text)
    await reloadAccountTasks(card.account.id)
  } catch (error) {
    toast.error(failureText('领取奖励', error))
  }
}

async function claimTask(card: GrowthCard, task: GrowthTask): Promise<void> {
  try {
    const payload = await claimGrowth([card.account.id], [task.task_code])
    toast.success(summarizeClaim(payload).text)
    await reloadAccountTasks(card.account.id)
  } catch (error) {
    toast.error(failureText('领取奖励', error))
  }
}

async function claimAll(): Promise<void> {
  const ids = cards.value.filter((card) => card.claimable > 0).map((card) => card.account.id)
  if (!ids.length) {
    toast.info('当前没有可领取的奖励')
    return
  }
  const ok = await confirm({
    title: '领取全部可领奖励？',
    message: '将对 ' + ids.length + ' 个账号领取已完成任务的奖励，共 ' + claimableTotal.value + ' 条。',
    okText: '全部领取',
  })
  if (!ok) return
  try {
    const payload = await claimGrowth(ids)
    toast.success(summarizeClaim(payload).text)
    await loadAllTasks(true)
  } catch (error) {
    toast.error(failureText('领取全部奖励', error))
  }
}

async function runAction(card: GrowthCard, item: GrowthActionItem): Promise<void> {
  if (card.disabled) {
    toast.info('请先启用账号或重新登录')
    return
  }
  try {
    const result = await postGrowthAction(card.account.id, item.action, item.tier)
    if (result?.ok) {
      toast.success(
        item.label + '：' + (result.reason || '已完成') +
        (Number(result.credit || 0) > 0 ? '（+' + fmtCredit(Number(result.credit)) + ' 积分）' : ''),
      )
    } else {
      toast.error(item.label + '：' + (result?.reason || '操作未确认，请刷新状态后核对'))
    }
    await reloadAccountTasks(card.account.id)
  } catch (error) {
    toast.error(failureText(item.label, error))
  }
}

const refreshTimers: Array<ReturnType<typeof setTimeout>> = []

async function refreshCenter(card: GrowthCard): Promise<void> {
  try {
    const result = await refreshGrowthCenter(card.account.id)
    toast.info(result?.running ? '成长状态刷新中，稍后自动重读任务清单' : '已发起刷新')
    const timer = setTimeout(() => {
      void reloadAccountTasks(card.account.id)
    }, 2500)
    refreshTimers.push(timer)
  } catch (error) {
    toast.error(failureText('刷新成长状态', error))
  }
}

/* ------------------------------------------------------------------ *
 * 卡片 ⋯ 菜单
 * ------------------------------------------------------------------ */

function actionOptions(card: GrowthCard): DropdownOption[] {
  const options: DropdownOption[] = GROWTH_ACTIONS.map((item) => ({
    label: item.label,
    key: item.key,
    disabled: card.disabled,
  }))
  options.push({ type: 'divider', key: 'divider-1' })
  options.push({ label: '刷新成长状态', key: 'refresh-center', disabled: card.disabled })
  options.push({ label: '打开任务清单', key: 'open-tasks' })
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
  for (const timer of refreshTimers) clearTimeout(timer)
  refreshTimers.length = 0
  for (const scope of jobScopes) scope.stop()
  jobScopes.length = 0
})

watch(supported, (value) => {
  if (value && !accounts.value.length) void loadAccounts()
})
</script>

<template>
  <div class="gw-page">
    <PageHeader :title="title" :help="HELP_TEXT">
      <template #actions>
        <Btn
          variant="primary"
          :icon="Trophy"
          :disabled="!supported || claimableTotal === 0 || loading"
          @click="claimAll"
        >
          全部领奖
        </Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" :disabled="!supported" @click="refresh">刷新</Btn>
      </template>
    </PageHeader>

    <Empty v-if="!supported" description="当前平台没有成长计划。切换到支持成长体系的平台后再回来。">
      <Btn variant="default" size="sm" @click="router.push('/accounts')">去账号池</Btn>
    </Empty>

    <ErrorBlock
      v-else-if="loadError && !accounts.length"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="refresh"
    />

    <div v-else-if="firstLoading" class="gw-skeleton" aria-busy="true" aria-label="成长账号加载中">
      <div class="gw-skeleton__strip"><n-skeleton text :repeat="2" /></div>
      <div class="gw-skeleton__grid">
        <div v-for="index in 4" :key="index" class="gw-skeleton__card"><n-skeleton text :repeat="5" /></div>
      </div>
    </div>

    <template v-else>
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
              placeholder="搜索账号"
              clearable
              @update:value="(value: string) => onFilterChange({ q: value })"
            />
            <n-select
              class="gw-select"
              :value="filters.state"
              :options="stateOptions"
              aria-label="账号状态筛选"
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
                  <Badge :tone="card.disabled ? 'neutral' : 'ok'">{{ card.disabled ? '已禁用' : '可用' }}</Badge>
                  <Badge tone="neutral">连续 {{ card.account.streak_days ?? '—' }} 天</Badge>
                  <Badge tone="neutral">能量 {{ card.account.energy ?? '—' }}</Badge>
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
                :aria-label="card.account.name + ' 任务进度'"
              />
              <span class="gw-card__meter-note">任务完成度 {{ card.progress.percent }}%</span>
            </div>

            <div v-if="card.loading" class="gw-card__loading"><n-skeleton text :repeat="3" /></div>

            <div v-else-if="card.error" class="gw-card__error">
              <p>{{ card.error }}</p>
              <Btn size="sm" variant="default" @click="reloadAccountTasks(card.account.id)">重试</Btn>
            </div>

            <div v-else class="gw-tasks">
              <div class="gw-tasks__head">
                <span>任务清单（{{ card.summary.done }}/{{ card.summary.total }}）</span>
                <span v-if="card.claimable" class="gw-tasks__claimable">可领 {{ card.claimable }} 条</span>
                <Btn v-if="hasMoreTasks(card)" size="sm" variant="quiet" @click="toggleExpand(card.account.id)">
                  {{ expanded[card.account.id] ? '收起' : '展开全部' }}
                </Btn>
              </div>
              <ul v-if="card.tasks.length" class="gw-task-list">
                <li v-for="task in visibleTasks(card)" :key="task.task_code" class="gw-task">
                  <span class="gw-task__title" :title="task.strategy || task.title || task.task_code">
                    {{ task.title || task.task_code }}
                  </span>
                  <Badge :tone="taskStatusOf(task).tone">{{ taskStatusOf(task).label }}</Badge>
                  <Badge v-if="task.level_label" tone="neutral">{{ task.level_label }}</Badge>
                  <span v-if="task.progress?.target" class="gw-task__progress tnum">
                    {{ task.progress.current ?? 0 }}/{{ task.progress.target }}
                  </span>
                  <span class="gw-task__reward tnum">
                    {{ task.reward_credit ? fmtCredit(task.reward_credit) + ' 积分' : '—' }}
                  </span>
                  <Btn
                    v-if="taskStatusOf(task).label === '可领取'"
                    size="sm"
                    variant="quiet"
                    @click="claimTask(card, task)"
                  >
                    领奖
                  </Btn>
                </li>
              </ul>
              <Empty v-else compact description="这个账号还没有拉到任务清单。" />
            </div>

            <footer class="gw-card__actions">
              <Btn size="sm" variant="default" :icon="Sparkles" :disabled="card.disabled || !card.canRun" @click="runTasks(card)">
                做任务
              </Btn>
              <Btn size="sm" variant="quiet" :disabled="card.disabled || !card.claimable" @click="claimAccount(card)">
                领奖{{ card.claimable ? ' ' + card.claimable : '' }}
              </Btn>
              <n-dropdown
                trigger="click"
                placement="bottom-end"
                :options="actionOptions(card)"
                @select="(key: string | number) => onActionSelect(card, key)"
              >
                <Btn size="sm" variant="quiet" :icon="MoreHorizontal" aria-label="更多成长操作">更多</Btn>
              </n-dropdown>
            </footer>
          </article>
        </div>

        <Empty v-else :description="filters.q || filters.state ? '没有符合条件的账号。' : '当前平台还没有可管理的成长账号。'">
          <Btn v-if="filters.q || filters.state" variant="default" size="sm" @click="resetFilters">清除筛选</Btn>
          <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="refresh">刷新</Btn>
        </Empty>

        <div v-if="pageData.pageCount > 1" class="gw-pager">
          <Btn variant="quiet" size="sm" :disabled="pageData.page <= 1" @click="table.setPage(pageData.page - 1)">上一页</Btn>
          <span class="tnum">第 {{ pageData.page }} / {{ pageData.pageCount }} 页 · 共 {{ pageData.total }} 个账号</span>
          <Btn variant="quiet" size="sm" :disabled="pageData.page >= pageData.pageCount" @click="table.setPage(pageData.page + 1)">下一页</Btn>
        </div>

        <p v-if="excluded > 0" class="gw-footnote">
          已排除 {{ excluded }} 个企业版账号（不参与成长任务）。
        </p>
      </Card>
    </template>

    <JobProgress :jobs="jobItems" title="成长任务" @hide="hideJob" />
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
