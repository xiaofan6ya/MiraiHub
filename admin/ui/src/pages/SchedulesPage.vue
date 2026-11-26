<script setup lang="ts">
/**
 * S11 定时任务 /schedules（模板 B：PageHeader + FilterBar + DataTable + FormDrawer）
 *
 * 契约：docs/ui-v2/contracts/schedules.md　设计稿：06-pages-d.md P17
 * 页面职责：定时任务的查看 / 新建 / 编辑 / 启停 / 立即执行 / 删除，以及「全局出口与并发」默认值。
 *
 * 与旧版（admin/console/console-app.js:15528-15947）保持一致的三点：
 *   · 任务列表**不按当前平台过滤**（各平台的签到/保活必须一眼看到），但订阅
 *     usePlatformStore().onChange —— 切平台后重拉一次（后端按 X-Platform 记日志，数据本身全局）。
 *   · 「下次运行」由后端算（next_run_at 是 naive UTC），前端只展示「今天 09:00」。
 *   · 立即执行的结果是后端 run_task 的原样返回值，摘要与落库的 last_result 同源；
 *     执行后刷新该行（有 running 时按 4s 轮询到结束）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NCheckbox, NCheckboxGroup, NDatePicker, NInput, NInputNumber, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import { Plus, RefreshCw, Settings2 } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  HelpTip,
} from '@/components'
import { useConfirm, usePolling, useRevisit, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { FMT_EMPTY } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import { useScheduleColumns } from './schedules/columns'
import { useScheduleFilters } from './schedules/filters'
import ScheduleGlobalProxyDrawer from './schedules/ScheduleGlobalProxyDrawer.vue'
import ScheduleResultDrawer from './schedules/ScheduleResultDrawer.vue'
import { ApiError, isAbortError } from '@/api/http'
import {
  calendarZoneHint,
  INTERVAL_UNIT_OPTIONS,
  MODE_OPTIONS,
  WEEKDAY_OPTIONS,
  buildProxyPoolOptions,
  buildScheduleInput,
  createSchedule,
  deleteSchedule,
  emptyScheduleForm,
  filterScheduleRows,
  getGlobalScheduleProxy,
  isCalendarMode,
  isCheckinTask,
  listProxyEndpoints,
  listScheduleTasks,
  listSchedules,
  paginateScheduleRows,
  parseScheduleResult,
  runSchedule,
  scheduleFormOf,
  scheduleStats,
  sortScheduleRows,
  toScheduleRows,
  toggleSchedule,
  updateSchedule,
  validateScheduleForm,
  type ProxyListPayload,
  type ScheduleForm,
  type ScheduleGlobalProxy,
  type ScheduleItem,
  type ScheduleRow,
  type ScheduleTaskOption,
  type ScheduleTiming,
} from '@/api/schedules'

const { t } = useI18n()

/** 旧版 #panel-schedules 的说明文字（原样保留，收进 ⓘ）。 */
const helpText = computed(() => t('page.schedules.help'))

/** 全局出口与并发的说明（旧版「全局出口与并发」弹窗头部）。 */
const globalProxyHelp = computed(() => t('page.schedules.globalProxyHelp'))

const toast = useToast()
const confirm = useConfirm()
const platformStore = usePlatformStore()
const table = useTableQuery('schedules', {
  pageSize: 20,
  filters: { q: '', platform: '', task: '', state: '', result: '' },
})

/* ------------------------------------------------------------------ *
 * 列表数据
 * ------------------------------------------------------------------ */

const schedules = ref<ScheduleItem[] | null>(null)
const globalProxy = ref<ScheduleGlobalProxy | null>(null)
const taskCatalog = ref<ScheduleTaskOption[]>([])
const proxyPayload = ref<ProxyListPayload | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const runningIds = ref<number[]>([])

const allRows = computed(() =>
  toScheduleRows(schedules.value, { globalProxy: globalProxy.value }),
)
const filteredRows = computed(() => filterScheduleRows(allRows.value, table.state.filters))
const sortedRows = computed(() =>
  sortScheduleRows(filteredRows.value, table.state.sortBy, table.state.sortOrder),
)
const pageData = computed(() =>
  paginateScheduleRows(sortedRows.value, table.state.page, table.state.pageSize),
)
const stats = computed(() => scheduleStats(allRows.value))
const filteredStats = computed(() => scheduleStats(filteredRows.value))

const firstLoading = computed(() => loading.value && schedules.value === null)
const hasFilters = computed(() =>
  Boolean(
    table.state.filters.q ||
      table.state.filters.platform ||
      table.state.filters.task ||
      table.state.filters.state ||
      table.state.filters.result,
  ),
)

const anyRunning = computed(() => allRows.value.some((row) => row.running))

const summary = computed(() => {
  const current = filteredStats.value
  const base = t('page.schedules.summary.base', {
    total: current.total,
    enabled: current.enabled,
    disabled: current.disabled,
  })
  const extra: string[] = []
  if (current.problem > 0) extra.push(t('page.schedules.summary.failed', { count: current.problem }))
  if (current.running > 0) extra.push(t('page.schedules.summary.running', { count: current.running }))
  const textTail = extra.length ? ' · ' + extra.join(' · ') : ''
  return hasFilters.value
    ? t('page.schedules.summary.filtered', { total: current.total, all: stats.value.total }) + textTail
    : base + textTail
})

const emptyDescription = computed(() =>
  hasFilters.value ? t('page.schedules.empty.filtered') : t('page.schedules.empty.none'),
)

/** 搜索框受控副本：输入即时回显，300ms 后由 FilterBar 写进路由 query。 */
const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => {
    search.value = value
  },
)

const {
  platformFilterOptions,
  taskFilterOptions,
  stateFilterOptions,
  resultFilterOptions,
  chips,
  sortOrderOf,
  onSorter,
  onFilterChange,
  applyFilter,
  removeChip,
  resetFilters,
} = useScheduleFilters({ table, allRows, taskCatalog, search })

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

let controller: AbortController | null = null

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

async function loadSchedules(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const [payload, global] = await Promise.all([
      listSchedules(current.signal),
      getGlobalScheduleProxy(current.signal).catch(() => globalProxy.value),
    ])
    if (controller !== current) return
    schedules.value = Array.isArray(payload?.items) ? payload.items : []
    if (global) globalProxy.value = global
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / table.state.pageSize))
    if (table.state.page > maxPage) table.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: errorMessage(error), detail }
    toast.error(
      failureText(
        schedules.value === null
          ? t('page.schedules.action.load')
          : t('page.schedules.action.refreshList'),
        error,
      ),
    )
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

/* ------------------------------------------------------------------ *
 * 行操作：立即执行 / 启停 / 编辑 / 删除 / 结果详情
 * ------------------------------------------------------------------ */

const togglingIds = ref<number[]>([])

async function onRowAction(row: ScheduleRow, key: string): Promise<void> {
  if (key === 'run') await runOne(row)
  else if (key === 'edit') await openEdit(row)
  else if (key === 'delete') await removeOne(row)
  else if (key === 'result') openResult(row)
}

/** 立即执行：提示结果摘要 → 刷新该行（有 running 时轮询到结束）。 */
async function runOne(row: ScheduleRow): Promise<void> {
  if (runningIds.value.includes(row.id)) return
  runningIds.value = [...runningIds.value, row.id]
  try {
    const payload = await runSchedule(row.id)
    const view = parseScheduleResult(JSON.stringify(payload?.result ?? {}))
    const textTail = view.summary ? '：' + view.summary : ''
    if (view.tone === 'danger')
      toast.error(t('page.schedules.toast.runFailed', { name: row.displayName, tail: textTail }))
    else if (view.tone === 'warn')
      toast.warning(
        t('page.schedules.toast.ran', { name: row.displayName, label: view.label, tail: textTail }),
      )
    else
      toast.success(
        t('page.schedules.toast.ran', { name: row.displayName, label: view.label, tail: textTail }),
      )
  } catch (error) {
    // 后端用同一套认领（fixlog_data_scoping D6）：调度器或另一次手动执行正在跑 → 409；数据库忙 → 503。
    if (error instanceof ApiError && error.status === 409) {
      toast.warning(t('page.schedules.toast.alreadyRunning', { name: row.displayName }))
    } else if (error instanceof ApiError && error.status === 503) {
      toast.warning(t('page.schedules.toast.runBusy', { name: row.displayName }))
    } else {
      toast.error(failureText(t('page.schedules.action.run'), error))
    }
  } finally {
    runningIds.value = runningIds.value.filter((id) => id !== row.id)
    await loadSchedules()
  }
}

async function onToggle(row: ScheduleRow, next: boolean): Promise<void> {
  if (togglingIds.value.includes(row.id)) return
  if (!next) {
    const ok = await confirm({
      title: t('page.schedules.confirmDisable.title', { name: row.displayName }),
      message: t('page.schedules.confirmDisable.body'),
      okText: t('page.schedules.action.disable'),
    })
    if (!ok) return
  }
  togglingIds.value = [...togglingIds.value, row.id]
  try {
    const payload = await toggleSchedule(row.id)
    const enabled = Boolean(payload?.enabled)
    toast.success(
      enabled
        ? t('page.schedules.toast.toggledOn', { name: row.displayName })
        : t('page.schedules.toast.toggledOff', { name: row.displayName }),
    )
    await loadSchedules()
  } catch (error) {
    toast.error(
      failureText(
        next ? t('page.schedules.action.enableTask') : t('page.schedules.action.disableTask'),
        error,
      ),
    )
  } finally {
    togglingIds.value = togglingIds.value.filter((id) => id !== row.id)
  }
}

async function removeOne(row: ScheduleRow): Promise<void> {
  const ok = await confirm({
    title: t('page.schedules.confirmDelete.title', { name: row.displayName }),
    message: t('page.schedules.confirmDelete.body'),
    okText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  try {
    await deleteSchedule(row.id)
    toast.success(t('page.schedules.toast.deleted', { name: row.displayName }))
    await loadSchedules()
  } catch (error) {
    toast.error(failureText(t('page.schedules.action.deleteTask'), error))
  }
}

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

/* ------------------------------------------------------------------ *
 * 轮询：有任务在执行时每 4s 刷新到结束（05-patterns §3）
 * ------------------------------------------------------------------ */

const poll = usePolling(() => silently(() => loadSchedules()), 4000, { immediate: false })

watch(anyRunning, (running) => {
  if (running) poll.start()
  else poll.stop()
})

/* ------------------------------------------------------------------ *
 * 表格列（src/pages/schedules/columns.ts；togglingIds 在上面声明，必须在它之后调用）
 * ------------------------------------------------------------------ */

const columns = useScheduleColumns({ runningIds, togglingIds, sortOrderOf, onToggle, onRowAction })

/* ------------------------------------------------------------------ *
 * 结果详情抽屉（src/pages/schedules/ScheduleResultDrawer.vue）
 * ------------------------------------------------------------------ */

const resultOpen = ref(false)
const resultRow = ref<ScheduleRow | null>(null)

function openResult(row: ScheduleRow): void {
  resultRow.value = row
  resultOpen.value = true
}

const proxyHint = computed(() =>
  t('page.schedules.form.proxyHint', {
    target: globalProxy.value?.proxy_pool
      ? t('page.schedules.form.proxyPoolName', { name: globalProxy.value.proxy_pool })
      : t('page.schedules.form.proxyDirect'),
  }),
)

/* ------------------------------------------------------------------ *
 * 表单抽屉（新建 / 编辑）
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const form = ref<ScheduleForm>(emptyScheduleForm())
const formErrors = ref<Record<string, string>>({})
const submitting = ref(false)
const catalogLoading = ref(false)
const catalogError = ref('')

const isEdit = computed(() => form.value.id !== null)
const drawerTitle = computed(() =>
  isEdit.value ? t('page.schedules.drawer.edit') : t('page.schedules.drawer.create'),
)
const submitText = computed(() =>
  isEdit.value ? t('common.save') : t('page.schedules.submit.create'),
)
const showStopAfter = computed(() => isCheckinTask(form.value.task))
const showCalendar = computed(() => isCalendarMode(form.value.mode))

const modeOptions = MODE_OPTIONS
const unitOptions = INTERVAL_UNIT_OPTIONS
const weekdayOptions = WEEKDAY_OPTIONS

const taskOptions = computed(() => {
  const base = taskCatalog.value.map((item) => ({
    label: item.label,
    value: item.value,
  }))
  if (!form.value.task || base.some((option) => option.value === form.value.task)) return base
  // 任务目录里已没有该任务（平台下线 / 改名）：保留当前值，别让它显示成空白。
  return [
    { label: form.value.task + t('page.schedules.taskOption.missing'), value: form.value.task },
    ...base,
  ]
})

const selectedTask = computed(() =>
  taskCatalog.value.find((item) => item.value === form.value.task) || null,
)

const selectedTaskPlatform = computed(() => {
  const hit = selectedTask.value
  if (!hit) return ''
  const regions = hit.platforms?.length ? hit.platforms : [hit.platform]
  return hit.platform_label + t('page.schedules.platformScope', { regions: regions.join(' / ') })
})

const catalogHint = computed(() => {
  if (catalogError.value) return catalogError.value
  if (catalogLoading.value) return t('page.schedules.catalog.loading')
  if (!taskCatalog.value.length) return t('page.schedules.catalog.empty')
  if (!selectedTask.value) return t('page.schedules.catalog.missing')
  return t('page.schedules.catalog.platform', {
    platform: selectedTask.value.platform_label || selectedTask.value.platform,
  })
})

async function ensureCatalog(force = false): Promise<void> {
  if (catalogLoading.value) return
  if (taskCatalog.value.length && !force) return
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const payload = await listScheduleTasks()
    taskCatalog.value = Array.isArray(payload?.items) ? payload.items : []
  } catch (error) {
    catalogError.value = failureText(t('page.schedules.action.loadCatalog'), error)
  } finally {
    catalogLoading.value = false
  }
}

async function ensureProxies(force = false): Promise<void> {
  if (proxyPayload.value && !force) return
  try {
    proxyPayload.value = await listProxyEndpoints()
  } catch {
    // 拉不到代理组不影响保存：下拉里会只剩「跟随全局」，任务级 proxy_pool 原值保留。
    proxyPayload.value = null
  }
}

const proxyOptions = computed(() =>
  buildProxyPoolOptions(proxyPayload.value, globalProxy.value, form.value.proxyPool),
)

function resetFormErrors(): void {
  formErrors.value = {}
}

async function openCreate(): Promise<void> {
  form.value = emptyScheduleForm(taskCatalog.value[0]?.value || 'refresh_balances')
  resetFormErrors()
  drawerOpen.value = true
  await Promise.all([ensureCatalog(), ensureProxies()])
}

async function openEdit(row: ScheduleRow): Promise<void> {
  const item = schedules.value?.find((schedule) => schedule.id === row.id)
  if (!item) {
    toast.warning(t('page.schedules.toast.taskMissing'))
    return
  }
  form.value = scheduleFormOf(item)
  resetFormErrors()
  drawerOpen.value = true
  await Promise.all([ensureCatalog(), ensureProxies()])
}

function onTaskChange(value: string): void {
  form.value.task = value
  formErrors.value = { ...formErrors.value, task: '' }
  if (!isCheckinTask(value)) form.value.stopAfter = ''
}

function onModeChange(value: ScheduleTiming['mode'] | string): void {
  form.value.mode = value as ScheduleTiming['mode']
  formErrors.value = { ...formErrors.value, time: '', weekdays: '', monthday: '' }
}

function onWeekdaysChange(value: Array<string | number>): void {
  form.value.weekdays = value.map((day) => Number(day)).filter((day) => day >= 0 && day <= 6)
  formErrors.value = { ...formErrors.value, weekdays: '' }
}

async function submit(): Promise<void> {
  if (submitting.value) return
  const errors = validateScheduleForm(form.value)
  formErrors.value = errors
  if (Object.keys(errors).length) return
  const input = buildScheduleInput(form.value, taskCatalog.value)
  submitting.value = true
  try {
    if (isEdit.value && form.value.id !== null) {
      await updateSchedule(form.value.id, input)
      toast.success(
        t('page.schedules.toast.saved', { name: input.name || t('page.schedules.untitled') }),
      )
    } else {
      await createSchedule(input)
      toast.success(
        t('page.schedules.toast.created', { name: input.name || t('page.schedules.untitled') }),
      )
      table.setPage(1)
    }
    drawerOpen.value = false
    await loadSchedules()
  } catch (error) {
    toast.error(
      failureText(
        isEdit.value ? t('page.schedules.action.saveTask') : t('page.schedules.submit.create'),
        error,
      ),
    )
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 全局出口与并发（src/pages/schedules/ScheduleGlobalProxyDrawer.vue）
 * ------------------------------------------------------------------ */

const globalOpen = ref(false)

async function openGlobalProxy(): Promise<void> {
  await ensureProxies(true)
  globalOpen.value = true
}

async function onGlobalSaved(next: ScheduleGlobalProxy): Promise<void> {
  globalProxy.value = next
  await loadSchedules()
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let unsubscribePlatform: (() => void) | null = null

// 二次进入（KeepAlive 命中）：保留旧数据、不出骨架，后台静默刷新（11-polish-motion §2）。
useRevisit(() => silently(() => loadSchedules()), { minIntervalMs: 10_000 })

onMounted(() => {
  void loadSchedules()
  void ensureCatalog()
  unsubscribePlatform = platformStore.onChange(() => {
    // 列表本身不按平台过滤（各平台任务都要看得到），切平台后重拉一次即可。
    void loadSchedules()
  })
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
  unsubscribePlatform?.()
  unsubscribePlatform = null
})
</script>

<template>
  <div class="schedules-page">
    <PageHeader :title="t('page.schedules.title')" :help="helpText">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadSchedules()">
          {{ t('common.refresh') }}
        </Btn>
        <Btn variant="quiet" :icon="Settings2" @click="openGlobalProxy">
          {{ t('page.schedules.action.globalProxy') }}
        </Btn>
        <Btn variant="primary" :icon="Plus" @click="openCreate">
          {{ t('page.schedules.action.create') }}
        </Btn>
      </template>
    </PageHeader>

    <p v-if="poll.paused.value" class="schedules-banner" role="status">
      {{ poll.pausedMessage.value }}
      <Btn variant="quiet" size="sm" @click="poll.resume()">{{ t('page.schedules.poll.resume') }}</Btn>
    </p>

    <Card flush class="schedules-card">
      <div class="schedules-filter">
        <FilterBar
          :chips="chips"
          :summary="summary"
          :loading="loading"
          @change="onFilterChange"
          @reset="resetFilters"
          @remove="removeChip"
        >
          <template #default="{ change }">
            <n-input
              class="schedules-filter__search"
              :value="search"
              :placeholder="t('page.schedules.filter.searchPlaceholder')"
              clearable
              @update:value="
                (value: string) => {
                  search = value
                  change('q', value)
                }
              "
            />
            <n-select
              class="schedules-filter__select"
              :value="table.state.filters.platform"
              :options="platformFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('platform', value)"
            />
            <n-select
              class="schedules-filter__select"
              :value="table.state.filters.task"
              :options="taskFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('task', value)"
            />
            <n-select
              class="schedules-filter__select"
              :value="table.state.filters.state"
              :options="stateFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('state', value)"
            />
            <n-select
              class="schedules-filter__select"
              :value="table.state.filters.result"
              :options="resultFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('result', value)"
            />
          </template>
        </FilterBar>
      </div>

      <div class="schedules-body">
        <ErrorBlock
          v-if="loadError && schedules === null"
          :title="t('page.schedules.error.load', { error: loadError.message })"
          :detail="loadError.detail"
          @retry="loadSchedules()"
        />

        <div
          v-else-if="firstLoading"
          class="schedules-skeleton"
          aria-busy="true"
          :aria-label="t('page.schedules.loadingAria')"
        >
          <n-skeleton v-for="index in 6" :key="index" height="44px" />
        </div>

        <DataTable
          v-else
          table-id="schedules"
          :columns="columns"
          :data="pageData.items"
          row-key="key"
          remote
          :loading="loading && !silentRefresh"
          :refreshing="loading && silentRefresh"
          :page="table.state.page"
          :page-size="table.state.pageSize"
          :item-count="pageData.total"
          :page-sizes="[20, 50, 100]"
          :empty-description="emptyDescription"
          @update:page="table.setPage"
          @update:page-size="table.setPageSize"
          @update:sorter="onSorter"
        >
          <template #empty>
            <Empty :description="emptyDescription">
              <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
                {{ t('page.schedules.clearFilters') }}
              </Btn>
              <Btn v-else variant="primary" size="sm" :icon="Plus" @click="openCreate">
                {{ t('page.schedules.action.create') }}
              </Btn>
            </Empty>
          </template>
        </DataTable>
      </div>
    </Card>

    <!-- 新建 / 编辑 -->
    <FormDrawer
      v-model:show="drawerOpen"
      :title="drawerTitle"
      size="lg"
      :submit-text="submitText"
      :submit-loading="submitting"
      @submit="submit"
    >
      <FormField
        :label="t('page.schedules.form.name')"
        :error="formErrors.name"
        :hint="t('page.schedules.form.nameHint')"
      >
        <n-input
          v-model:value="form.name"
          :placeholder="t('page.schedules.form.namePlaceholder')"
          maxlength="64"
          clearable
        />
      </FormField>

      <FormField
        :label="t('page.schedules.form.task')"
        required
        :error="formErrors.task"
        :hint="catalogHint"
      >
        <n-select
          :value="form.task"
          :options="taskOptions"
          :loading="catalogLoading"
          filterable
          :placeholder="t('page.schedules.form.taskPlaceholder')"
          @update:value="onTaskChange"
        />
      </FormField>

      <FormField :label="t('page.schedules.form.scope')" :hint="t('page.schedules.form.scopeHint')">
        <span class="schedules-readonly">{{ selectedTaskPlatform || FMT_EMPTY }}</span>
      </FormField>

      <FormField :label="t('page.schedules.form.cycle')" required>
        <n-select :value="form.mode" :options="modeOptions" @update:value="onModeChange" />
      </FormField>

      <FormField
        v-if="!showCalendar"
        :label="t('page.schedules.form.interval')"
        required
        :error="formErrors.interval"
        :hint="
          form.mode === 'preset'
            ? t('page.schedules.form.intervalPresetHint')
            : t('page.schedules.form.intervalHint')
        "
      >
        <div class="schedules-interval">
          <n-input-number v-model:value="form.intervalValue" :min="1" :max="5256000" :step="1" />
          <n-select v-model:value="form.intervalUnit" :options="unitOptions" class="schedules-interval__unit" />
        </div>
      </FormField>

      <template v-if="showCalendar">
        <FormField
          :label="t('page.schedules.form.time')"
          required
          :error="formErrors.time"
          :hint="t('page.schedules.form.timeHint', { zone: calendarZoneHint() })"
        >
          <n-input v-model:value="form.time" placeholder="09:00" maxlength="5" class="schedules-time" />
        </FormField>

        <FormField
          v-if="form.mode === 'weekly'"
          :label="t('page.schedules.form.weekdays')"
          required
          :error="formErrors.weekdays"
        >
          <n-checkbox-group :value="form.weekdays" @update:value="onWeekdaysChange">
            <div class="schedules-weekdays">
              <n-checkbox v-for="day in weekdayOptions" :key="day.value" :value="day.value" :label="day.label" />
            </div>
          </n-checkbox-group>
        </FormField>

        <FormField
          v-if="form.mode === 'monthly'"
          :label="t('page.schedules.form.monthday')"
          required
          :error="formErrors.monthday"
          :hint="t('page.schedules.form.monthdayHint')"
        >
          <n-input-number v-model:value="form.monthday" :min="1" :max="31" :step="1" />
        </FormField>
      </template>

      <FormField
        :label="t('page.schedules.form.proxy')"
        :hint="proxyHint"
      >
        <n-select
          :value="form.proxyPool"
          :options="proxyOptions"
          :placeholder="t('page.schedules.form.proxyPlaceholder')"
          @update:value="(value: string) => (form.proxyPool = value)"
        />
      </FormField>

      <div class="schedules-grid2">
        <FormField
          :label="t('page.schedules.form.concurrency')"
          :error="formErrors.concurrency"
          :hint="t('page.schedules.form.concurrencyHint')"
        >
          <n-input-number v-model:value="form.concurrency" :min="0" :max="64" :step="1" />
        </FormField>
        <FormField
          :label="t('page.schedules.form.budget')"
          :error="formErrors.budget"
          :hint="t('page.schedules.form.budgetHint')"
        >
          <n-input-number v-model:value="form.budgetMinutes" :min="0" :max="720" :step="1" />
        </FormField>
        <FormField
          :label="t('page.schedules.form.stale')"
          :error="formErrors.stale"
          :hint="t('page.schedules.form.staleHint')"
        >
          <n-input-number v-model:value="form.staleMinutes" :min="0" :max="10080" :step="1" />
        </FormField>
      </div>

      <FormField
        v-if="showStopAfter"
        :label="t('page.schedules.form.stopAfter')"
        :error="formErrors.stopAfter"
        :hint="t('page.schedules.form.stopAfterHint')"
      >
        <n-date-picker
          type="datetime"
          :formatted-value="form.stopAfter || null"
          value-format="yyyy-MM-dd'T'HH:mm"
          clearable
          :placeholder="t('page.schedules.form.stopAfterPlaceholder')"
          @update:formatted-value="(value: string | null) => (form.stopAfter = value || '')"
        />
      </FormField>

      <FormField :label="t('page.schedules.form.enabled')">
        <div class="schedules-enabled">
          <n-switch v-model:value="form.enabled" size="small" />
          <span>{{ t('page.schedules.form.enabledHint') }}</span>
        </div>
      </FormField>

      <p class="schedules-hint">
        <HelpTip
          :content="t('page.schedules.note.growthHelp')"
          :label="t('page.schedules.note.growthHelpLabel')"
        />
        {{ t('page.schedules.note.proxyRisk') }}
      </p>
    </FormDrawer>

    <!-- 全局出口与并发 -->
    <ScheduleGlobalProxyDrawer
      v-model:show="globalOpen"
      :global-proxy="globalProxy"
      :proxy-payload="proxyPayload"
      :help="globalProxyHelp"
      @saved="onGlobalSaved"
    />

    <!-- 结果详情 -->
    <ScheduleResultDrawer v-model:show="resultOpen" :row="resultRow" />
  </div>
</template>

<style scoped src="./schedules/SchedulesPage.css"></style>
