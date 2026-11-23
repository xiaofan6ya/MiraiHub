<script setup lang="ts">
/**
 * S09 分组管理 /groups（模板 B：PageHeader + FilterBar + DataTable + FormDrawer + 删除确认）
 *
 * 契约：docs/ui-v2/contracts/groups.md
 * 页面职责：分组 CRUD（分组名 / 模型 / 备注），模型候选来自 /api/models/configs。
 * 后端 GET /api/groups 不分页、不搜索，筛选/排序/分页在这里做客户端处理；
 * 视图状态（page/size/sort/order/q/models/keys）通过 useTableQuery 同步路由 query。
 *
 * 这是后续列表页的样板，约定：
 *   · 列定义集中在 columns（含 mobile 位、numeric、sortable、受控 sortOrder）；
 *   · 所有接口响应先经 api/groups.ts 的纯函数转成行数据，页面不做字段换算；
 *   · 首次加载骨架 / 刷新保留旧数据 / 空 / 错误四态齐全；
 *   · 行操作走 RowActions（危险操作进「⋯」），批量操作走 BulkBar；
 *   · 文案按 01-direction §8：按钮动词开头、确认框问句、Toast 过去式。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch, type VNodeChild } from 'vue'
import { NInput, NSelect, NSkeleton, NTransfer } from 'naive-ui'
import { Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Chip,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  HelpTip,
  RowActions,
  type FilterChip,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useTableQuery, useToast } from '@/composables'
import { FMT_EMPTY, fmtInt } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import {
  GROUP_MODEL_PREVIEW,
  buildGroupEditorOptions,
  createGroup,
  deleteGroup,
  filterGroupRows,
  groupStats,
  listGroups,
  listModelConfigs,
  paginateGroupRows,
  sortGroupRows,
  toGroupRows,
  updateGroup,
  type GroupItem,
  type GroupModelOption,
  type GroupRow,
  type ModelConfigItem,
} from '@/api/groups'

/** 旧版页面底部那段说明（原样保留，收进 ⓘ）。 */
const HELP_TEXT =
  '分组用于限定 API Key 可用的模型范围。创建 Key 时选择分组即可；不选分组表示可用全部启用模型。' +
  '绑定分组后该 Key 只能调用组内模型，调用组外模型返回 403（model_not_in_group）。'

const toast = useToast()
const confirm = useConfirm()
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
const sortedRows = computed(() =>
  sortGroupRows(filteredRows.value, table.state.sortBy, table.state.sortOrder),
)
const pageData = computed(() =>
  paginateGroupRows(sortedRows.value, table.state.page, table.state.pageSize),
)
const stats = computed(() => groupStats(allRows.value))
const filteredStats = computed(() => groupStats(filteredRows.value))

const firstLoading = computed(() => loading.value && groups.value === null)
const hasFilters = computed(
  () => Boolean(table.state.filters.q || table.state.filters.models || table.state.filters.keys),
)

const summary = computed(() => {
  const current = filteredStats.value
  if (!hasFilters.value) {
    return '共 ' + current.total + ' 个分组 · 覆盖 ' + current.models + ' 个模型 · 绑定 ' +
      current.boundKeys + ' 个 Key'
  }
  return '筛选出 ' + current.total + ' / 共 ' + stats.value.total + ' 个分组 · 绑定 ' +
    current.boundKeys + ' 个 Key'
})

const emptyDescription = computed(() =>
  hasFilters.value
    ? '没有符合条件的分组。清空筛选后可看到全部分组。'
    : '还没有分组。新建分组，从已启用模型里挑几个。',
)

/** 搜索框是受控的本地副本：输入即时回显，300ms 后由 FilterBar 写进路由 query。 */
const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => {
    search.value = value
  },
)

const modelFilterOptions = [
  { label: '模型：全部', value: '' },
  { label: '含已停用模型', value: 'stale' },
  { label: '全部可用', value: 'ready' },
]
const keyFilterOptions = [
  { label: 'Key：全部', value: '' },
  { label: '已绑定 Key', value: 'bound' },
  { label: '未绑定 Key', value: 'unbound' },
]

const chips = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: '搜索', value: filters.q })
  if (filters.models === 'stale') list.push({ key: 'models', label: '模型', value: '含已停用' })
  if (filters.models === 'ready') list.push({ key: 'models', label: '模型', value: '全部可用' })
  if (filters.keys === 'bound') list.push({ key: 'keys', label: 'Key', value: '已绑定' })
  if (filters.keys === 'unbound') list.push({ key: 'keys', label: 'Key', value: '未绑定' })
  return list
})

const SORT_KEY_MAP: Record<string, string> = {
  name: 'name',
  models: 'model_count',
  key_count: 'key_count',
}

function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
  if (table.state.sortBy !== stateKey) return false
  if (table.state.sortOrder === 'asc') return 'ascend'
  if (table.state.sortOrder === 'desc') return 'descend'
  return false
}

function onSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order) {
    table.setSort('', '')
    return
  }
  const stateKey = SORT_KEY_MAP[sorter.columnKey]
  if (!stateKey) {
    table.setSort('', '')
    return
  }
  table.setSort(stateKey, sorter.order === 'ascend' ? 'asc' : 'desc')
}

/* ------------------------------ 单元格 ------------------------------ */

function renderName(row: TableRow): VNodeChild {
  const group = row as GroupRow
  return h('span', { class: 'groups-name', title: group.name }, group.name || FMT_EMPTY)
}

function renderModels(row: TableRow): VNodeChild {
  const group = row as GroupRow
  if (!group.models.length) return h('span', { class: 'groups-dash' }, FMT_EMPTY)
  const nodes: VNodeChild[] = group.modelPreview.map((model) =>
    h(Chip, { key: 'model-' + model, mono: true, title: model }, { default: () => model }),
  )
  if (group.modelOverflow > 0) {
    const rest = group.models.slice(GROUP_MODEL_PREVIEW)
    nodes.push(
      h(
        Chip,
        {
          key: 'overflow',
          title: '还有 ' + group.modelOverflow + ' 个：' + rest.join('、'),
        },
        { default: () => '+' + group.modelOverflow },
      ),
    )
  }
  if (group.staleCount > 0) {
    nodes.push(
      h(
        Badge,
        {
          key: 'stale',
          tone: 'warn',
          title: '部分模型已被停用，实际可用 ' + group.effectiveCount + ' 个',
        },
        { default: () => group.staleCount + ' 个已停用' },
      ),
    )
  }
  return h('div', { class: 'groups-models' }, nodes)
}

function renderKeyCount(row: TableRow): VNodeChild {
  const group = row as GroupRow
  return h(
    'span',
    { class: group.keyCount > 0 ? 'groups-num' : 'groups-num is-zero' },
    fmtInt(group.keyCount),
  )
}

function renderNote(row: TableRow): VNodeChild {
  const group = row as GroupRow
  if (!group.note) return h('span', { class: 'groups-dash' }, FMT_EMPTY)
  return h('span', { class: 'groups-note', title: group.note }, group.note)
}

function renderActions(row: TableRow): VNodeChild {
  const group = row as GroupRow
  return h(RowActions, {
    actions: [
      { key: 'edit', label: '编辑' },
      { key: 'delete', label: '删除', danger: true },
    ],
    max: 1,
    onSelect: (key: string) => {
      void onRowAction(group, key)
    },
  })
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    title: '分组名',
    mobile: 'title',
    width: 200,
    sortable: true,
    sortOrder: sortOrderOf('name'),
    render: renderName,
  },
  {
    key: 'models',
    title: '包含模型',
    mobile: 'meta',
    minWidth: 320,
    sortable: true,
    sortOrder: sortOrderOf('model_count'),
    render: renderModels,
  },
  {
    key: 'key_count',
    title: 'Key 数',
    mobile: 'meta',
    width: 96,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('key_count'),
    render: renderKeyCount,
  },
  {
    key: 'note',
    title: '备注',
    mobile: 'subtitle',
    minWidth: 160,
    render: renderNote,
  },
  {
    key: 'actions',
    title: '操作',
    mobile: 'actions',
    width: 116,
    hideable: false,
    fixed: 'right',
    render: renderActions,
  },
])

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
    toast.error(failureText(groups.value === null ? '加载分组' : '刷新分组', error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

/* ------------------------------------------------------------------ *
 * 筛选 / 排序 / 分页
 * ------------------------------------------------------------------ */

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) next[name] = String(value ?? '')
  table.setFilters(next)
}

function applyFilter(name: string, value: string | number | null): void {
  table.setFilter(name, value === null || value === undefined ? '' : String(value))
}

function removeChip(key: string): void {
  if (key === 'q') search.value = ''
  table.setFilter(key, '')
}

function resetFilters(): void {
  search.value = ''
  table.reset()
}

function onCheckedKeys(keys: Array<string | number>): void {
  checkedKeys.value = keys.map(String)
}

const selectedRows = computed(() => {
  const keys = new Set(checkedKeys.value)
  return allRows.value.filter((row) => keys.has(row.key))
})

/* ------------------------------------------------------------------ *
 * 表单抽屉
 * ------------------------------------------------------------------ */

const tableRef = ref<InstanceType<typeof DataTable> | null>(null)

const drawerOpen = ref(false)
const editing = ref<GroupRow | null>(null)
const formName = ref('')
const formNote = ref('')
const formErrorName = ref('')
const formErrorModels = ref('')
const submitting = ref(false)

const storedModels = ref<string[]>([])
const selection = ref<string[]>([])
const selectionEdited = ref(false)

const catalog = ref<ModelConfigItem[] | null>(null)
const catalogLoading = ref(false)
const catalogError = ref('')

const drawerTitle = computed(() => (editing.value ? '编辑分组' : '新建分组'))
const submitText = computed(() => (editing.value ? '保存' : '创建分组'))

/** 选项 = 全部启用模型 + 已选中的「停用 / 未在目录」项（保留旧值，避免保存时丢授权）。 */
const editorOptions = computed<GroupModelOption[]>(() => {
  if (!catalog.value) return []
  return buildGroupEditorOptions(catalog.value, selection.value).options
})

const optionIndex = computed(() => {
  const index = new Map<string, GroupModelOption>()
  for (const option of editorOptions.value) index.set(option.value, option)
  return index
})

const outOfCatalog = computed(() => {
  if (!catalog.value) return [] as GroupModelOption[]
  const plan = buildGroupEditorOptions(catalog.value, selection.value)
  return plan.options.filter((option) => !option.known || !option.enabled)
})

const outOfCatalogHint = computed(() =>
  outOfCatalog.value
    .map((option) => (option.known ? option.modelId + '（已停用）' : option.value + '（目录中找不到）'))
    .join('\n'),
)

const transferEmpty = computed(
  () => Boolean(catalog.value) && !catalogLoading.value && editorOptions.value.length === 0,
)

function transferFilter(pattern: string, option: { label: string; value: string | number }): boolean {
  const keyword = pattern.trim().toLowerCase()
  if (!keyword) return true
  const meta = optionIndex.value.get(String(option.value))
  const haystack = [
    option.label,
    String(option.value),
    meta?.modelId,
    meta?.platform,
    ...(meta?.platforms || []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  return haystack.includes(keyword)
}

async function ensureCatalog(force = false): Promise<void> {
  if (catalogLoading.value) return
  if (catalog.value && !force) return
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const payload = await listModelConfigs()
    catalog.value = Array.isArray(payload?.items) ? payload.items : []
    if (!selectionEdited.value) {
      selection.value = buildGroupEditorOptions(catalog.value, storedModels.value).selected
    }
  } catch (error) {
    catalogError.value = failureText('加载模型目录', error)
  } finally {
    catalogLoading.value = false
  }
}

function resetFormErrors(): void {
  formErrorName.value = ''
  formErrorModels.value = ''
}

async function openCreate(): Promise<void> {
  editing.value = null
  storedModels.value = []
  selection.value = []
  selectionEdited.value = false
  formName.value = ''
  formNote.value = ''
  resetFormErrors()
  drawerOpen.value = true
  await ensureCatalog()
}

async function openEdit(row: GroupRow): Promise<void> {
  const item = groups.value?.find((group) => group.id === row.id)
  if (!item) {
    toast.warning('该分组已不存在，请刷新列表')
    return
  }
  editing.value = row
  storedModels.value = Array.isArray(item.models) ? item.models.slice() : []
  selection.value = buildGroupEditorOptions(catalog.value, storedModels.value).selected
  selectionEdited.value = false
  formName.value = item.name
  formNote.value = item.note || ''
  resetFormErrors()
  drawerOpen.value = true
  await ensureCatalog()
}

function onTransferUpdate(value: Array<string | number>): void {
  selection.value = value.map(String)
  selectionEdited.value = true
  formErrorModels.value = ''
}

function validate(): boolean {
  resetFormErrors()
  const name = formName.value.trim()
  if (!name) {
    formErrorName.value = '请填写分组名称'
    return false
  }
  const editingId = editing.value ? editing.value.id : -1
  const duplicated = (groups.value || []).some((group) => group.name === name && group.id !== editingId)
  if (duplicated) {
    formErrorName.value = '分组「' + name + '」已存在'
    return false
  }
  if (!selection.value.length) {
    formErrorModels.value = '请至少选择一个模型'
    return false
  }
  return true
}

async function submit(): Promise<void> {
  if (submitting.value) return
  if (!validate()) return
  const name = formName.value.trim()
  const payload = { name, note: formNote.value.trim(), models: selection.value.slice() }
  const isEdit = Boolean(editing.value)
  submitting.value = true
  try {
    if (isEdit && editing.value) {
      await updateGroup(editing.value.id, payload)
      toast.success('已保存分组「' + name + '」')
    } else {
      await createGroup(payload)
      toast.success('已创建分组「' + name + '」')
    }
    drawerOpen.value = false
    if (!isEdit) table.setPage(1)
    await loadGroups()
  } catch (error) {
    toast.error(failureText(isEdit ? '保存分组' : '创建分组', error))
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 删除
 * ------------------------------------------------------------------ */

async function onRowAction(row: GroupRow, key: string): Promise<void> {
  if (key === 'edit') await openEdit(row)
  else if (key === 'delete') await removeOne(row)
}

async function removeOne(row: GroupRow): Promise<void> {
  const ok = await confirm({
    title: '删除分组「' + row.name + '」？',
    message:
      row.keyCount > 0
        ? '删除后无法恢复；绑定该分组的 ' + row.keyCount + ' 个 Key 会自动变为「不限制模型」。'
        : '删除后无法恢复。该分组当前没有绑定 Key。',
    okText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    const result = await deleteGroup(row.id)
    const unbound = Number(result?.unbound_keys) || 0
    toast.success(
      unbound > 0
        ? '已删除分组「' + row.name + '」，' + unbound + ' 个 Key 已改为不限制模型'
        : '已删除分组「' + row.name + '」',
    )
    tableRef.value?.clearSelection()
    checkedKeys.value = []
    await loadGroups()
  } catch (error) {
    toast.error(failureText('删除分组', error))
  }
}

async function bulkDelete(clear?: () => void): Promise<void> {
  const rows = selectedRows.value
  if (!rows.length || bulkRunning.value) return
  const unbound = rows.reduce((total, row) => total + row.keyCount, 0)
  const ok = await confirm({
    title: '删除 ' + rows.length + ' 个分组？',
    message:
      unbound > 0
        ? '删除后无法恢复；这些分组绑定的 ' + unbound + ' 个 Key 会自动变为「不限制模型」。'
        : '删除后无法恢复。这些分组当前没有绑定 Key。',
    okText: '删除',
    danger: true,
    requireText: '删除',
  })
  if (!ok) return
  bulkRunning.value = true
  let failed = 0
  let firstError = ''
  for (const row of rows) {
    try {
      await deleteGroup(row.id)
    } catch (error) {
      failed += 1
      if (!firstError) firstError = errorMessage(error)
    }
  }
  bulkRunning.value = false
  const removed = rows.length - failed
  if (failed === 0) toast.success('已删除 ' + removed + ' 个分组')
  else toast.error('已删除 ' + removed + ' 个分组，' + failed + ' 个删除失败：' + firstError)
  clear?.()
  tableRef.value?.clearSelection()
  checkedKeys.value = []
  await loadGroups()
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

onMounted(() => {
  void loadGroups()
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
})
</script>

<template>
  <div class="groups-page">
    <PageHeader title="分组管理" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadGroups()">刷新</Btn>
        <Btn variant="primary" :icon="Plus" @click="openCreate">新建分组</Btn>
      </template>
    </PageHeader>

    <Card flush class="groups-card">
      <div class="groups-filter">
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
              class="groups-filter__search"
              :value="search"
              placeholder="搜索分组名 / 备注 / 模型"
              clearable
              @update:value="
                (value: string) => {
                  search = value
                  change('q', value)
                }
              "
            />
            <n-select
              class="groups-filter__select"
              :value="table.state.filters.models"
              :options="modelFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('models', value)"
            />
            <n-select
              class="groups-filter__select"
              :value="table.state.filters.keys"
              :options="keyFilterOptions"
              @update:value="(value: string | number | null) => applyFilter('keys', value)"
            />
          </template>
        </FilterBar>
      </div>

      <div class="groups-body">
        <ErrorBlock
          v-if="loadError && groups === null"
          :title="'加载分组失败：' + loadError.message"
          :detail="loadError.detail"
          @retry="loadGroups()"
        />

        <div v-else-if="firstLoading" class="groups-skeleton" aria-busy="true" aria-label="分组加载中">
          <n-skeleton v-for="index in 6" :key="index" height="44px" />
        </div>

        <DataTable
          v-else
          ref="tableRef"
          table-id="groups"
          :columns="columns"
          :data="pageData.items"
          row-key="key"
          remote
          selectable
          :loading="loading"
          :page="table.state.page"
          :page-size="table.state.pageSize"
          :item-count="pageData.total"
          :page-sizes="[20, 50, 100]"
          :empty-description="emptyDescription"
          @update:page="table.setPage"
          @update:page-size="table.setPageSize"
          @update:sorter="onSorter"
          @update:checked-keys="onCheckedKeys"
        >
          <template #bulk="{ clear }">
            <Btn
              variant="danger"
              size="sm"
              :icon="Trash2"
              :loading="bulkRunning"
              @click="bulkDelete(clear)"
            >
              批量删除
            </Btn>
          </template>

          <template #empty>
            <Empty :description="emptyDescription">
              <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
                清除筛选
              </Btn>
              <Btn v-else variant="primary" size="sm" :icon="Plus" @click="openCreate">
                新建分组
              </Btn>
            </Empty>
          </template>
        </DataTable>
      </div>
    </Card>

    <FormDrawer
      v-model:show="drawerOpen"
      :title="drawerTitle"
      size="lg"
      :submit-text="submitText"
      :submit-loading="submitting"
      @submit="submit"
    >
      <FormField
        label="分组名"
        required
        :error="formErrorName"
        hint="创建 Key 时按分组选择可用模型。"
      >
        <n-input
          v-model:value="formName"
          placeholder="例如：便宜模型组"
          maxlength="64"
          show-count
          clearable
          @update:value="formErrorName = ''"
        />
      </FormField>

      <FormField
        label="模型"
        required
        :error="formErrorModels"
        hint="只列出已启用的模型。分组保存的是上游模型 id，停用模型会从可用范围自动排除。"
      >
        <div class="groups-transfer">
          <ErrorBlock
            v-if="catalogError && !catalog"
            :title="catalogError"
            retry-label="重试"
            compact
            @retry="ensureCatalog(true)"
          />
          <n-skeleton v-else-if="catalogLoading && !catalog" :repeat="4" text />
          <n-transfer
            v-else
            :value="selection"
            :options="editorOptions"
            source-filterable
            source-title="全部启用模型"
            target-title="已选模型"
            source-filter-placeholder="搜索平台 / 模型"
            :filter="transferFilter"
            size="small"
            @update:value="onTransferUpdate"
          />
          <p v-if="catalogLoading && catalog" class="groups-hint">正在刷新模型目录…</p>
          <p v-if="transferEmpty" class="groups-hint">
            模型目录为空。请先在「模型配置」同步模型目录后再建分组。
          </p>
          <p v-if="outOfCatalog.length" class="groups-hint is-warn">
            <Badge tone="warn">{{ outOfCatalog.length }} 个模型不在启用目录</Badge>
            <HelpTip :content="outOfCatalogHint" label="查看这些模型" />
            保存时原样保留，不会静默删除。
          </p>
        </div>
      </FormField>

      <FormField label="备注" hint="可选。">
        <n-input v-model:value="formNote" placeholder="可选" maxlength="200" />
      </FormField>
    </FormDrawer>
  </div>
</template>

<style scoped>
.groups-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.groups-card {
  min-width: 0;
}

.groups-filter {
  padding: var(--sp-4) var(--sp-5) var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
}

.groups-filter__search {
  width: 240px;
  max-width: 100%;
}

.groups-filter__select {
  width: 160px;
}

.groups-body {
  padding: var(--sp-3) var(--sp-5) var(--sp-5);
  min-width: 0;
}

.groups-skeleton {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) 0;
}

.groups-name {
  font-weight: 500;
  color: var(--fg);
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.groups-models {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
}

.groups-num {
  font-variant-numeric: tabular-nums;
}

.groups-num.is-zero {
  color: var(--fg-faint);
}

.groups-note {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.groups-dash {
  color: var(--fg-faint);
}

.groups-transfer {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

.groups-transfer :deep(.n-transfer) {
  height: 280px;
}

.groups-hint {
  margin: 0;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.groups-hint.is-warn {
  color: var(--warn);
}

@media (max-width: 640px) {
  .groups-filter {
    padding: var(--sp-3) var(--sp-4);
  }

  .groups-body {
    padding: var(--sp-2) var(--sp-4) var(--sp-4);
  }

  .groups-filter__search,
  .groups-filter__select {
    width: 100%;
  }
}
</style>
