<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'
/**
 * S12 模型配置 /models（模板 B：PageHeader + FilterBar + DataTable + FormDrawer）
 *
 * 契约：docs/ui-v2/contracts/models.md（末尾「## 实现对照」是逐条落点）
 * 旧版：#panel-models + model-pool.js / model-metadata.js / model-icons.js
 *
 * 三条与后端对齐的口径（写错就会显示假数据）：
 *   1. 一「行」= 同一模型跨平台合成（后端 model_alias 的归一方向：上游展示名优先）。
 *      平台映射放在行内折叠，抽屉里逐平台列清楚；
 *   2. 展示名是 `note`，可读调用名是 `model_id`，复制给客户端的是 `client_id`；
 *   3. 倍率 `credit_multiplier` 原样展示：>0 收费 / 0 免费 / <0 按量 / null 未同步。
 *      PATCH /api/models/{id} 不接受倍率字段，所以抽屉里倍率只读（由「同步目录」写入）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NDropdown, NInput, NInputNumber, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import { ListChecks, Plus, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Copyable,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  HelpTip,
  ModelIcon,
  PlatformLogo,
  type TableRow,
} from '@/components'
import { useConfirm, useRevisit, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import { useModelColumns } from './models/columns'
import { useModelFilters } from './models/filters'
import { ApiError, isAbortError } from '@/api/http'
import {
  batchToggleModels,
  buildModelEditPlan,
  deleteModelConfig,
  describeSpeedTest,
  describeSyncResult,
  filterModelRows,
  fmtMultiplier,
  listModelConfigs,
  listModelHealth,
  modelEditForm,
  modelStats,
  paginateModelRows,
  sortModelRows,
  speedTestModel,
  syncModels,
  toModelRows,
  updateModelConfig,
  type ModelConfigItem,
  type ModelEditForm,
  type ModelHealthItem,
  type ModelPlatformSummary,
  type ModelRow,
  type ModelSpeedTestPayload,
} from '@/api/models'

const { t } = useI18n()

/** 旧版 #panel-models 顶部那段提示（收进 ⓘ）。 */
const helpText = computed(() => t('page.models.help'))

const toast = useToast()
const confirm = useConfirm()
const platformStore = usePlatformStore()
const table = useTableQuery('models', {
  pageSize: 50,
  sortBy: '',
  sortOrder: '',
  filters: { q: '', vendor: '', platform: '', cap: '', status: '' },
})

/* ------------------------------------------------------------------ *
 * 数据
 * ------------------------------------------------------------------ */

const items = ref<ModelConfigItem[] | null>(null)
const platforms = ref<ModelPlatformSummary[]>([])
const officialTotal = ref(0)
const health = ref<ModelHealthItem[] | null>(null)
const healthError = ref('')

const loading = ref(true)
const loadError = ref<{ message: string; detail: string } | null>(null)
const syncing = ref(false)
const bulkRunning = ref(false)

const allRows = computed(() => toModelRows(items.value, health.value))
const filteredRows = computed(() => filterModelRows(allRows.value, table.state.filters))
const sortedRows = computed(() =>
  sortModelRows(filteredRows.value, table.state.sortBy, table.state.sortOrder),
)
const pageData = computed(() =>
  paginateModelRows(sortedRows.value, table.state.page, table.state.pageSize),
)
const stats = computed(() => modelStats(allRows.value))
const filteredStats = computed(() => modelStats(filteredRows.value))

const firstLoading = computed(() => loading.value && items.value === null)
const hasFilters = computed(() =>
  Boolean(
    table.state.filters.q ||
      table.state.filters.vendor ||
      table.state.filters.platform ||
      table.state.filters.cap ||
      table.state.filters.status,
  ),
)

const summary = computed(() => {
  const current = filteredStats.value
  const base = t('page.models.summary.base', {
    total: current.total,
    configs: current.configs,
    enabled: current.enabled,
  })
  const tail =
    current.unavailable > 0
      ? t('page.models.summary.unavailable', { count: current.unavailable })
      : ''
  if (!hasFilters.value) return base + tail
  return (
    t('page.models.summary.filtered', {
      total: current.total,
      all: stats.value.total,
      enabled: current.enabled,
    }) + tail
  )
})

const emptyDescription = computed(() =>
  hasFilters.value ? t('page.models.empty.filtered') : t('page.models.empty.none'),
)

/* ------------------------------ 筛选项 ------------------------------ */

const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => {
    search.value = value
  },
)

const {
  vendorSelectOptions,
  platformSelectOptions,
  CAPABILITY_OPTIONS,
  STATUS_OPTIONS,
  chips,
  onFilterChange,
  applyFilter,
  removeChip,
  resetFilters,
  sortOrderOf,
  onSorter,
} = useModelFilters({ table, allRows, platforms, search })

/* ------------------------------ 单元格（src/pages/models/columns.ts） ------------------------------ */

const columns = useModelColumns({ health, sortOrderOf, openEdit, onRowAction })

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

async function loadConfigs(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const payload = await listModelConfigs(current.signal)
    if (controller !== current) return
    items.value = Array.isArray(payload?.items) ? payload.items : []
    platforms.value = Array.isArray(payload?.platforms) ? payload.platforms : []
    officialTotal.value = Number(payload?.official_total) || 0
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / table.state.pageSize))
    if (table.state.page > maxPage) table.setPage(maxPage)
  } catch (error) {
    if (controller !== current || isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: errorMessage(error), detail }
    toast.error(
      failureText(
        items.value === null ? t('page.models.action.load') : t('page.models.action.refresh'),
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

async function loadHealth(): Promise<void> {
  healthError.value = ''
  try {
    const payload = await listModelHealth(24)
    health.value = Array.isArray(payload?.items) ? payload.items : []
  } catch (error) {
    if (isAbortError(error)) return
    healthError.value = failureText(t('page.models.action.loadHealth'), error)
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadConfigs(), loadHealth()])
}

/* ------------------------------------------------------------------ *
 * 工具栏：同步 / 批量 / 添加
 * ------------------------------------------------------------------ */

async function onSync(): Promise<void> {
  if (syncing.value) return
  syncing.value = true
  toast.info(t('page.models.toast.syncing'))
  try {
    const payload = await syncModels()
    toast.success(t('page.models.toast.synced', { result: describeSyncResult(payload) }))
    await loadConfigs()
    await loadHealth()
  } catch (error) {
    toast.error(failureText(t('page.models.action.sync'), error))
  } finally {
    syncing.value = false
  }
}

const bulkOptions = computed(() => [
  { label: t('page.models.bulk.enable'), key: 'enable' },
  { label: t('page.models.bulk.disable'), key: 'disable' },
])

async function onBulkSelect(key: string | number): Promise<void> {
  const enable = String(key) === 'enable'
  if (bulkRunning.value) return
  const ok = await confirm({
    title: enable
      ? t('page.models.bulk.titleEnable')
      : t('page.models.bulk.titleDisable'),
    message: enable ? t('page.models.bulk.bodyEnable') : t('page.models.bulk.bodyDisable'),
    okText: enable ? t('page.models.bulk.enable') : t('page.models.bulk.disable'),
    danger: !enable,
    requireText: enable ? undefined : t('page.models.bulk.requireText'),
  })
  if (!ok) return
  bulkRunning.value = true
  try {
    const payload = await batchToggleModels({ enabled: enable, level: 'system' })
    const count = Number(payload?.updated) || 0
    toast.success(
      enable
        ? t('page.models.toast.bulkEnable', { count })
        : t('page.models.toast.bulkDisable', { count }),
    )
    await loadConfigs()
    await loadHealth()
  } catch (error) {
    toast.error(
      failureText(
        enable ? t('page.models.action.bulkEnable') : t('page.models.action.bulkDisable'),
        error,
      ),
    )
  } finally {
    bulkRunning.value = false
  }
}

/* ------------------------------ 添加模型 ------------------------------ */

const addOpen = ref(false)
const addPlatform = ref('')
const addSubmitting = ref(false)

const addPlatformOptions = computed(() => {
  const options: Array<{ label: string; value: string }> = [
    { label: t('page.models.add.platformAll'), value: '' },
  ]
  for (const item of platforms.value) {
    options.push({
      label: t('page.models.add.platformCount', {
        label: item.label || item.platform,
        count: Number(item.count) || 0,
      }),
      value: item.platform,
    })
  }
  return options
})

function openAdd(): void {
  addPlatform.value = ''
  addOpen.value = true
}

async function submitAdd(): Promise<void> {
  if (addSubmitting.value) return
  addSubmitting.value = true
  try {
    const payload = await syncModels(addPlatform.value || undefined)
    toast.success(t('page.models.toast.synced', { result: describeSyncResult(payload) }))
    addOpen.value = false
    await loadConfigs()
    await loadHealth()
  } catch (error) {
    toast.error(failureText(t('page.models.action.sync'), error))
  } finally {
    addSubmitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 行操作：启用/禁用、测速、删除
 * ------------------------------------------------------------------ */

async function onRowAction(row: ModelRow, key: string): Promise<void> {
  if (key === 'edit') await openEdit(row)
  else if (key === 'toggle') await toggleRow(row)
  else if (key === 'speed') await runSpeedTest(row)
  else if (key === 'delete') await removeOne(row)
}

async function toggleRow(row: ModelRow): Promise<void> {
  const enable = !row.enabled
  const results = await Promise.allSettled(
    row.platforms.map((ref) => updateModelConfig(ref.configId, { enabled: enable })),
  )
  const failed = results.filter((result) => result.status === 'rejected').length
  if (failed === 0) {
    toast.success(
      enable
        ? t('page.models.toggle.enabled', { name: row.name })
        : t('page.models.toggle.disabled', { name: row.name }),
    )
  } else {
    const first = results.find((result) => result.status === 'rejected') as
      | PromiseRejectedResult
      | undefined
    toast.error(
      t('page.models.toggle.partialFail', {
        action: enable ? t('page.models.action.enable') : t('page.models.action.disable'),
        name: row.name,
        error: errorMessage(first?.reason),
      }),
    )
  }
  await loadConfigs()
}

const speedTesting = ref<number | null>(null)

async function runSpeedTest(row: ModelRow): Promise<void> {
  if (speedTesting.value !== null) return
  const model = row.upstreamIds[0]
  if (!model) {
    toast.warning(t('page.models.speed.noUpstream'))
    return
  }
  speedTesting.value = row.configIds[0] ?? 0
  try {
    const payload: ModelSpeedTestPayload = await speedTestModel(model)
    toast.success(t('page.models.speed.result', { name: row.name, result: describeSpeedTest(payload) }))
    await loadHealth()
  } catch (error) {
    toast.error(failureText(t('page.models.action.speedTest', { name: row.name }), error))
  } finally {
    speedTesting.value = null
  }
}

async function removeOne(row: ModelRow): Promise<void> {
  const ok = await confirm({
    title: t('page.models.delete.title', { name: row.name }),
    message: t('page.models.delete.body', { count: row.platformCount }),
    okText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  const results = await Promise.allSettled(
    row.platforms.map((ref) => deleteModelConfig(ref.configId)),
  )
  const failed = results.filter((result) => result.status === 'rejected').length
  if (failed === 0) toast.success(t('page.models.delete.done', { name: row.name }))
  else
    toast.error(t('page.models.delete.partialFail', { name: row.name, count: failed }))
  await loadConfigs()
  await loadHealth()
}

/* ------------------------------------------------------------------ *
 * 编辑抽屉
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const editing = ref<ModelRow | null>(null)

/** 抽屉表单的空初值（打开时才会被真实行覆盖）。 */
function emptyEditForm(): ModelEditForm {
  return {
    note: '',
    enabled: {},
    contextWindow: null,
    maxOutputTokens: null,
    supportsImages: false,
    supportsTools: false,
    supportsReasoning: false,
  }
}

const form = ref<ModelEditForm>(emptyEditForm())
const formError = ref('')
const submitting = ref(false)

const multiplierNote = computed(() => {
  const row = editing.value
  if (!row) return ''
  if (row.multiplier === null) return t('page.models.edit.multiplierPending')
  if (row.multiplier < 0) return t('page.models.edit.multiplierTokenBilled')
  return t('page.models.edit.multiplierReadonly')
})

async function openEdit(row: ModelRow): Promise<void> {
  editing.value = row
  form.value = modelEditForm(row)
  formError.value = ''
  drawerOpen.value = true
}

function setPlatformEnabled(configId: number, value: boolean): void {
  form.value.enabled = { ...form.value.enabled, [configId]: value }
  formError.value = ''
}

async function submitEdit(): Promise<void> {
  if (submitting.value) return
  const row = editing.value
  if (!row) return
  const plan = buildModelEditPlan(row, form.value)
  if (plan.error) {
    formError.value = plan.error
    return
  }
  if (!plan.changed) {
    toast.info(t('page.models.toast.noChanges'))
    drawerOpen.value = false
    return
  }
  submitting.value = true
  try {
    const results = await Promise.allSettled(
      plan.updates.map((update) => updateModelConfig(update.id, update.body)),
    )
    const failed = results.filter((result) => result.status === 'rejected').length
    if (failed === 0) {
      toast.success(
        t('page.models.edit.saved', { name: row.name, count: plan.updates.length }),
      )
    } else {
      const first = results.find((result) => result.status === 'rejected') as
        | PromiseRejectedResult
        | undefined
      toast.error(
        t('page.models.edit.saveFailed', {
          name: row.name,
          count: failed,
          error: errorMessage(first?.reason),
        }),
      )
    }
    drawerOpen.value = false
    await loadConfigs()
    await loadHealth()
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 生命周期：平台切换要重取（健康数据按 X-Platform 收窄）
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null

// 二次进入（KeepAlive 命中）：保留旧数据、不出骨架，后台静默刷新（11-polish-motion §2）。
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

useRevisit(() => silently(() => loadAll()), { minIntervalMs: 10_000 })

onMounted(() => {
  void loadAll()
  offPlatform = platformStore.onChange(() => {
    void loadHealth()
    if (table.state.filters.platform) table.setFilter('platform', '')
  })
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
  offPlatform?.()
  offPlatform = null
})
</script>

<template>
  <div class="models-page">
    <PageHeader :title="t('page.models.title')" :help="helpText">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadAll()">
          {{ t('common.refresh') }}
        </Btn>
        <Btn variant="default" :icon="RefreshCw" :loading="syncing" @click="onSync">
          {{ t('page.models.action.syncShort') }}
        </Btn>
        <n-dropdown trigger="click" :options="bulkOptions" @select="onBulkSelect">
          <Btn variant="default" :icon="ListChecks" :loading="bulkRunning">
            {{ t('page.models.action.bulk') }}
          </Btn>
        </n-dropdown>
        <Btn variant="primary" :icon="Plus" @click="openAdd">
          {{ t('page.models.action.add') }}
        </Btn>
      </template>
    </PageHeader>

    <Card flush class="models-card" :motion-pending="firstLoading">
      <div class="models-filter">
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
              class="models-filter__search"
              :value="search"
              :placeholder="t('page.models.filter.searchPlaceholder')"
              clearable
              @update:value="
                (value: string) => {
                  search = value
                  change('q', value)
                }
              "
            />
            <n-select
              class="models-filter__select"
              :value="table.state.filters.vendor"
              :options="vendorSelectOptions"
              @update:value="(value: string | number | null) => applyFilter('vendor', value)"
            />
            <n-select
              class="models-filter__select"
              :value="table.state.filters.platform"
              :options="platformSelectOptions"
              @update:value="(value: string | number | null) => applyFilter('platform', value)"
            />
            <n-select
              class="models-filter__select is-narrow"
              :value="table.state.filters.cap"
              :options="CAPABILITY_OPTIONS"
              @update:value="(value: string | number | null) => applyFilter('cap', value)"
            />
            <n-select
              class="models-filter__select is-narrow"
              :value="table.state.filters.status"
              :options="STATUS_OPTIONS"
              @update:value="(value: string | number | null) => applyFilter('status', value)"
            />
          </template>
        </FilterBar>
      </div>

      <div class="models-body">
        <ErrorBlock
          v-if="loadError && items === null"
          :title="t('page.models.error.load', { error: loadError.message })"
          :detail="loadError.detail"
          @retry="loadConfigs()"
        />

        <div
          v-else-if="firstLoading"
          class="models-skeleton"
          aria-busy="true"
          :aria-label="t('page.models.loadingAria')"
        >
          <n-skeleton v-for="index in 6" :key="index" height="44px" />
        </div>

        <template v-else>
          <div v-if="healthError" class="models-health-error">
            <Badge tone="warn">{{ t('page.models.health.badge') }}</Badge>
            <span>{{ healthError }}</span>
            <Btn variant="quiet" size="sm" @click="loadHealth()">{{ t('common.retry') }}</Btn>
          </div>

          <DataTable
            table-id="models"
            :columns="columns"
            :data="pageData.items"
            row-key="key"
            remote
            clickable-rows
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
            @row-click="(row: TableRow) => openEdit(row as ModelRow)"
          >
            <template #empty>
              <Empty :description="emptyDescription">
                <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
                  {{ t('page.models.clearFilters') }}
                </Btn>
                <Btn
                  v-else
                  variant="primary"
                  size="sm"
                  :icon="RefreshCw"
                  :loading="syncing"
                  @click="onSync"
                >
                  {{ t('page.models.action.syncShort') }}
                </Btn>
              </Empty>
            </template>
          </DataTable>

          <p v-if="officialTotal > 0 && stats.total < officialTotal" class="models-footnote">
            {{ t('page.models.footnote', { official: officialTotal, configured: stats.total }) }}
          </p>
        </template>
      </div>
    </Card>

    <!-- 编辑：别名 / 限制 / 各平台映射 / 启用状态 -->
    <FormDrawer
      v-model:show="drawerOpen"
      :title="t('page.models.edit.title')"
      size="lg"
      :submit-text="t('common.save')"
      :submit-loading="submitting"
      :mask-closable="!submitting"
      @submit="submitEdit"
    >
      <template v-if="editing">
        <div class="models-edit-head">
          <ModelIcon :vendor="editing.vendor" :size="22" :label="editing.vendorLabel" />
          <div class="models-edit-head__main">
            <span class="models-edit-head__name">{{ editing.name }}</span>
            <Copyable :value="editing.callId" />
            <span class="models-edit-head__meta">
              {{
                t('page.models.edit.platformMeta', {
                  vendor: editing.vendorLabel,
                  count: editing.platformCount,
                  multiplier: editing.multiplierText,
                })
              }}
            </span>
          </div>
        </div>

        <FormField
          :label="t('page.models.edit.name')"
          :hint="t('page.models.edit.nameHint')"
        >
          <n-input
            v-model:value="form.note"
            :placeholder="t('page.models.edit.namePlaceholder')"
            maxlength="255"
            clearable
          />
        </FormField>

        <FormField
          :label="t('page.models.edit.enabled')"
          :error="formError"
          :hint="t('page.models.edit.enabledHint')"
        >
          <div class="models-edit-platforms">
            <div
              v-for="ref in editing.platforms"
              :key="'en-' + ref.configId"
              class="models-edit-platform"
            >
              <PlatformLogo :id="ref.platform" :size="18" :label="ref.platformLabel" />
              <span class="models-edit-platform__name">{{ ref.platformLabel }}</span>
              <span class="models-edit-platform__mult tnum">{{ fmtMultiplier(ref.multiplier) }}</span>
              <n-switch
                :value="form.enabled[ref.configId] !== false"
                size="small"
                @update:value="(value: boolean) => setPlatformEnabled(ref.configId, value)"
              />
            </div>
          </div>
        </FormField>

        <FormField :label="t('page.models.edit.multiplier')" :hint="multiplierNote">
          <div class="models-edit-multipliers">
            <div
              v-for="ref in editing.platforms"
              :key="'mult-' + ref.configId"
              class="models-edit-multiplier"
            >
              <span class="models-edit-multiplier__name">{{ ref.platformLabel }}</span>
              <span class="models-edit-multiplier__value tnum">{{ fmtMultiplier(ref.multiplier) }}</span>
              <span class="models-edit-multiplier__raw mono">{{ ref.clientId }}</span>
            </div>
          </div>
        </FormField>

        <FormField :label="t('page.models.edit.caps')" :hint="t('page.models.edit.capsHint')">
          <div class="models-edit-caps">
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">{{ t('page.models.edit.contextWindow') }}</span>
              <n-input-number
                v-model:value="form.contextWindow"
                :min="0"
                :step="1000"
                placeholder="128000"
                class="models-edit-caps__number"
              />
            </div>
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">{{ t('page.models.edit.maxOutput') }}</span>
              <n-input-number
                v-model:value="form.maxOutputTokens"
                :min="0"
                :step="1000"
                placeholder="16000"
                class="models-edit-caps__number"
              />
            </div>
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">{{ t('page.models.edit.supportsImages') }}</span>
              <n-switch v-model:value="form.supportsImages" size="small" />
            </div>
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">{{ t('page.models.edit.supportsTools') }}</span>
              <n-switch v-model:value="form.supportsTools" size="small" />
            </div>
            <div class="models-edit-caps__row">
              <span class="models-edit-caps__label">{{ t('page.models.edit.supportsReasoning') }}</span>
              <n-switch v-model:value="form.supportsReasoning" size="small" />
            </div>
            <p v-if="editing.caps.overridden" class="models-edit-caps__hint">
              <Badge tone="brand">{{ t('page.models.edit.overriddenBadge') }}</Badge>
              {{ t('page.models.edit.overriddenHint') }}
            </p>
          </div>
        </FormField>

        <FormField :label="t('page.models.edit.mapping')" :hint="t('page.models.edit.mappingHint')">
          <div class="models-edit-map">
            <div v-for="ref in editing.platforms" :key="'map-' + ref.configId" class="models-edit-map__row">
              <span class="models-edit-map__platform">
                <PlatformLogo :id="ref.platform" :size="16" :label="ref.platformLabel" />
                {{ ref.platformLabel }}
              </span>
              <Copyable :value="ref.clientId" muted />
              <TitleTip :content="t('page.models.edit.upstreamTitle', { id: ref.upstreamId })"><span
                class="models-edit-map__upstream mono"
              >
                {{ ref.upstreamId }}
              </span></TitleTip>
              <Badge :tone="ref.enabled ? 'ok' : 'neutral'" dot>
                {{ ref.enabled ? t('page.models.edit.platformOn') : t('page.models.edit.platformOff') }}
              </Badge>
            </div>
          </div>
        </FormField>

        <p v-if="editing.description" class="models-edit-desc">{{ editing.description }}</p>
        <p class="models-edit-hint">
          <HelpTip
            :content="t('page.models.edit.deleteTip')"
            :label="t('page.models.edit.deleteTipLabel')"
          />
        </p>
      </template>
    </FormDrawer>

    <!-- 添加模型：后端没有手工新增端点，新模型只能从平台目录同步进来 -->
    <FormDrawer
      v-model:show="addOpen"
      :title="t('page.models.add.title')"
      :submit-text="t('page.models.add.submit')"
      :submit-loading="addSubmitting"
      @submit="submitAdd"
    >
      <FormField :label="t('page.models.add.platform')" :hint="t('page.models.add.platformHint')">
        <n-select
          :value="addPlatform"
          :options="addPlatformOptions"
          @update:value="(value: string | number | null) => (addPlatform = value === null ? '' : String(value))"
        />
      </FormField>
      <p class="models-edit-desc">{{ t('page.models.add.note1') }}</p>
      <p class="models-edit-desc">{{ t('page.models.add.note2') }}</p>
    </FormDrawer>
  </div>
</template>

<style scoped src="./models/ModelsPage.css"></style>
