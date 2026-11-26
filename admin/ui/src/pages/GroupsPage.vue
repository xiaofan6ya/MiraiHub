<script setup lang="ts">
/**
 * S09 分组管理 /groups —— 入口（数据、筛选 ↔ 路由、删除）。视觉按 10-visual-refresh.md：
 *
 *   PageHeader（唯一主按钮「新建分组」）
 *   KPI × 4：分组 / 覆盖模型 / 绑定 Key / 含停用模型
 *   一张表：表格上方一行工具栏（groups/GroupsToolbar），列定义在 groups/GroupsTable
 *   新建 / 编辑：groups/GroupEditorDrawer
 *
 * 契约：docs/ui-v2/contracts/groups.md。后端 GET /api/groups 不分页不搜索，筛排分页走 api/groups.ts 的纯函数。
 * 本页不随平台切换重载（groups 不在 PLATFORM_SCOPED_KEYS）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Boxes, KeyRound, Layers, Plus, Trash2, TriangleAlert } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import { Btn, ErrorBlock, KpiCard, type SorterPayload } from '@/components'
import { useConfirm, useRevisit, useTableQuery, useToast } from '@/composables'
import { fmtInt } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import { ApiError, isAbortError } from '@/api/http'
import {
  createGroup,
  deleteGroup,
  filterGroupRows,
  groupStats,
  listGroups,
  paginateGroupRows,
  sortGroupRows,
  toGroupRows,
  updateGroup,
  type GroupInput,
  type GroupItem,
  type GroupRow,
} from '@/api/groups'
import GroupsToolbar from './groups/GroupsToolbar.vue'
import GroupsTable from './groups/GroupsTable.vue'
import GroupEditorDrawer from './groups/GroupEditorDrawer.vue'

const { t } = useI18n()
const toast = useToast()
const confirm = useConfirm()
const helpText = computed(() => t('page.groups.help'))

const table = useTableQuery('groups', {
  pageSize: 20,
  filters: { q: '', models: '', keys: '' },
})

/* ------------------------------------------------------------------ *
 * 列表数据
 * ------------------------------------------------------------------ */

const groups = ref<GroupItem[] | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const checkedKeys = ref<string[]>([])
const bulkRunning = ref(false)

const allRows = computed(() => toGroupRows(groups.value))
const filteredRows = computed(() => filterGroupRows(allRows.value, table.state.filters))
const sortedRows = computed(() => sortGroupRows(filteredRows.value, table.state.sortBy, normalizeOrder(table.state.sortOrder)))
const pageData = computed(() => paginateGroupRows(sortedRows.value, table.state.page, table.state.pageSize))
const stats = computed(() => groupStats(allRows.value))
const filteredStats = computed(() => groupStats(filteredRows.value))

const firstLoading = computed(() => loading.value && groups.value === null)
const hasFilters = computed(() => Boolean(table.state.filters.q || table.state.filters.models || table.state.filters.keys))

function normalizeOrder(order: string): 'asc' | 'desc' | '' {
  return order === 'asc' || order === 'desc' ? order : ''
}

const summary = computed(() => {
  const current = filteredStats.value
  if (!hasFilters.value) {
    return t('page.groups.summary.all', { total: current.total, models: current.models, keys: current.boundKeys })
  }
  return t('page.groups.summary.filtered', { total: current.total, all: stats.value.total, keys: current.boundKeys })
})

const emptyDescription = computed(() => (hasFilters.value ? t('page.groups.empty.filtered') : t('page.groups.empty.all')))

const modelFilterOptions = computed(() => [
  { label: t('page.groups.filter.modelAll'), value: '' },
  { label: t('page.groups.filter.modelStale'), value: 'stale' },
  { label: t('page.groups.filter.modelReady'), value: 'ready' },
])
const keyFilterOptions = computed(() => [
  { label: t('page.groups.filter.keyAll'), value: '' },
  { label: t('page.groups.filter.keyBound'), value: 'bound' },
  { label: t('page.groups.filter.keyUnbound'), value: 'unbound' },
])

const SORT_KEY_MAP: Record<string, string> = { name: 'name', models: 'model_count', key_count: 'key_count' }

function onSorter(sorter: SorterPayload | null): void {
  const key = sorter && sorter.order ? SORT_KEY_MAP[sorter.columnKey] : ''
  if (!sorter || !sorter.order || !key) {
    table.setSort('', '')
    return
  }
  table.setSort(key, sorter.order === 'ascend' ? 'asc' : 'desc')
}

function setFilter(name: 'q' | 'models' | 'keys', value: string): void {
  table.setFilter(name, value)
}

function resetFilters(): void {
  table.reset()
}

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

async function loadGroups(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const payload = await listGroups(current.signal)
    groups.value = Array.isArray(payload?.items) ? payload.items : []
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / table.state.pageSize))
    if (table.state.page > maxPage) table.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: errorMessage(error), detail }
    // 已有数据时刷新失败：只提示，不清空表格（05-patterns §3）。
    toast.error(failureText(groups.value === null ? t('page.groups.action.load') : t('page.groups.action.refresh'), error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

/* ------------------------------------------------------------------ *
 * 新建 / 编辑
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const editing = ref<GroupItem | null>(null)
const submitting = ref(false)

function openCreate(): void {
  editing.value = null
  drawerOpen.value = true
}

function openEdit(row: GroupRow): void {
  const item = groups.value?.find((group) => group.id === row.id)
  if (!item) {
    toast.warning(t('page.groups.toast.missing'))
    return
  }
  editing.value = item
  drawerOpen.value = true
}

async function submit(payload: GroupInput): Promise<void> {
  if (submitting.value) return
  const target = editing.value
  submitting.value = true
  try {
    if (target) {
      await updateGroup(target.id, payload)
      toast.success(t('page.groups.toast.saved', { name: payload.name }))
    } else {
      await createGroup(payload)
      toast.success(t('page.groups.toast.created', { name: payload.name }))
    }
    drawerOpen.value = false
    if (!target) table.setPage(1)
    await loadGroups()
  } catch (error) {
    toast.error(failureText(target ? t('page.groups.action.save') : t('page.groups.action.create'), error))
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 删除
 * ------------------------------------------------------------------ */

const tableRef = ref<InstanceType<typeof GroupsTable> | null>(null)

const selectedRows = computed(() => {
  const keys = new Set(checkedKeys.value)
  return allRows.value.filter((row) => keys.has(row.key))
})

function clearChecked(): void {
  tableRef.value?.clearSelection()
  checkedKeys.value = []
}

async function onRowAction(row: GroupRow, key: string): Promise<void> {
  if (key === 'edit') openEdit(row)
  else if (key === 'delete') await removeOne(row)
}

async function removeOne(row: GroupRow): Promise<void> {
  const ok = await confirm({
    title: t('page.groups.confirm.deleteTitle', { name: row.name }),
    message: row.keyCount > 0 ? t('page.groups.confirm.deleteBound', { count: row.keyCount }) : t('page.groups.confirm.deleteFree'),
    okText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  try {
    const result = await deleteGroup(row.id)
    const unbound = Number(result?.unbound_keys) || 0
    toast.success(
      unbound > 0
        ? t('page.groups.toast.deletedUnbound', { name: row.name, count: unbound })
        : t('page.groups.toast.deleted', { name: row.name }),
    )
    clearChecked()
    await loadGroups()
  } catch (error) {
    toast.error(failureText(t('page.groups.action.delete'), error))
  }
}

async function bulkDelete(clear?: () => void): Promise<void> {
  const rows = selectedRows.value
  if (!rows.length || bulkRunning.value) return
  const unbound = rows.reduce((total, row) => total + row.keyCount, 0)
  const ok = await confirm({
    title: t('page.groups.confirm.bulkTitle', { count: rows.length }),
    message: unbound > 0 ? t('page.groups.confirm.bulkBound', { count: unbound }) : t('page.groups.confirm.bulkFree'),
    okText: t('common.delete'),
    danger: true,
    requireText: t('page.groups.confirm.bulkRequire'),
  })
  if (!ok) return
  bulkRunning.value = true
  let failed = 0
  let firstError = ''
  try {
    for (const row of rows) {
      try {
        await deleteGroup(row.id)
      } catch (error) {
        failed += 1
        if (!firstError) firstError = errorMessage(error)
      }
    }
  } finally {
    bulkRunning.value = false
  }
  const removed = rows.length - failed
  if (failed === 0) toast.success(t('page.groups.toast.bulkDone', { count: removed }))
  else toast.error(t('page.groups.toast.bulkPartial', { removed, failed, reason: firstError }))
  clear?.()
  clearChecked()
  await loadGroups()
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

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

useRevisit(() => silently(() => loadGroups()), { minIntervalMs: 10_000, onLeave: () => controller?.abort() })

onMounted(() => {
  void loadGroups()
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
})
</script>

<template>
  <div class="groups-page mh-page mh-page--wide">
    <PageHeader :title="t('page.groups.title')" :help="helpText">
      <template #actions>
        <Btn variant="primary" :icon="Plus" @click="openCreate">{{ t('page.groups.create') }}</Btn>
      </template>
    </PageHeader>

    <section class="groups-kpis" :aria-label="t('page.groups.kpi.aria')">
      <KpiCard
        :label="t('page.groups.kpi.groups')"
        :value="groups ? fmtInt(stats.total) : null"
        :hint="t('page.groups.kpi.groupsHint')"
        :icon="Layers"
        tone="brand"
        :loading="firstLoading"
      />
      <KpiCard
        :label="t('page.groups.kpi.models')"
        :value="groups ? fmtInt(stats.models) : null"
        :hint="t('page.groups.kpi.modelsHint')"
        :icon="Boxes"
        tone="neutral"
        :loading="firstLoading"
      />
      <KpiCard
        :label="t('page.groups.kpi.keys')"
        :value="groups ? fmtInt(stats.boundKeys) : null"
        :hint="t('page.groups.kpi.keysHint')"
        :icon="KeyRound"
        tone="neutral"
        :loading="firstLoading"
      />
      <KpiCard
        :label="t('page.groups.kpi.stale')"
        :value="groups ? fmtInt(stats.staleGroups) : null"
        :hint="t('page.groups.kpi.staleHint')"
        :icon="TriangleAlert"
        :tone="stats.staleGroups > 0 ? 'warn' : 'neutral'"
        :loading="firstLoading"
      />
    </section>

    <section class="groups-list mh-card">
      <ErrorBlock
        v-if="loadError && groups === null"
        :title="t('page.groups.loadFailed', { reason: loadError.message })"
        :detail="loadError.detail"
        @retry="loadGroups()"
      />
      <div v-else-if="firstLoading" class="groups-skeleton" aria-busy="true" :aria-label="t('page.groups.loadingAria')">
        <span v-for="index in 6" :key="index" class="mh-skeleton groups-skeleton__row" />
      </div>
      <GroupsTable
        v-else
        ref="tableRef"
        :rows="pageData.items"
        :total="pageData.total"
        :page="table.state.page"
        :page-size="table.state.pageSize"
        :loading="loading && !silentRefresh"
        :refreshing="loading && silentRefresh"
        :sort-by="table.state.sortBy"
        :sort-order="table.state.sortOrder"
        :has-filters="hasFilters"
        :empty-description="emptyDescription"
        @action="onRowAction"
        @sorter="onSorter"
        @page="table.setPage"
        @page-size="table.setPageSize"
        @checked="(keys: string[]) => (checkedKeys = keys)"
        @create="openCreate"
        @reset="resetFilters"
      >
        <template #toolbar>
          <GroupsToolbar
            :q="table.state.filters.q"
            :models="table.state.filters.models"
            :keys="table.state.filters.keys"
            :model-options="modelFilterOptions"
            :key-options="keyFilterOptions"
            :summary="summary"
            :has-filters="hasFilters"
            :loading="loading"
            @set="setFilter"
            @reset="resetFilters"
            @refresh="loadGroups()"
          />
        </template>
        <template #bulk="{ clear }">
          <Btn variant="danger" size="sm" :icon="Trash2" :loading="bulkRunning" @click="bulkDelete(clear)">
            {{ t('page.groups.bulkDelete') }}
          </Btn>
        </template>
      </GroupsTable>
    </section>

    <GroupEditorDrawer
      v-model:show="drawerOpen"
      :editing="editing"
      :groups="groups || []"
      :submitting="submitting"
      @submit="submit"
    />
  </div>
</template>

<style scoped>
.groups-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.groups-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
  align-items: start;
  min-width: 0;
}

.groups-list {
  min-width: 0;
  padding: var(--sp-3) var(--sp-4) var(--sp-4);
}

.groups-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.groups-skeleton__row {
  height: 36px;
}

@media (max-width: 1279px) {
  .groups-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .groups-kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
