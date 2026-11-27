<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * 分组管理 · 表格（列定义 + 分页 + 批量选择）。
 *
 * 第 2 轮审查 P2：模型 chip 换行后行高变成两倍 → 一行最多 4 个 chip + 「+N」，不换行；
 * 悬停「+N」弹出全部模型。停用模型用 warn 徽章「N 个已停用」，放在 chip 之后。
 * 行操作：「编辑」直接露出，「删除」收进 ⋯（危险操作在菜单底部）。
 */
import { computed, h, ref, type VNodeChild } from 'vue'
import { NPopover } from 'naive-ui'
import { Layers, Plus } from 'lucide-vue-next'
import {
  Badge,
  Btn,
  Chip,
  DataTable,
  Empty,
  RowActions,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { FMT_EMPTY, fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
import type { GroupRow } from '@/api/groups'

const props = defineProps<{
  rows: GroupRow[]
  total: number
  page: number
  pageSize: number
  loading: boolean
  /** 第 5 轮：二次进入的静默刷新（DataTable `refreshing`：保留旧数据，只在工具栏转小圈）。 */
  refreshing?: boolean
  sortBy: string
  sortOrder: string
  hasFilters: boolean
  emptyDescription: string
}>()

const emit = defineEmits<{
  (event: 'action', row: GroupRow, key: string): void
  (event: 'sorter', sorter: SorterPayload | null): void
  (event: 'page', page: number): void
  (event: 'pageSize', size: number): void
  (event: 'checked', keys: string[]): void
  (event: 'create'): void
  (event: 'reset'): void
}>()

const { t } = useI18n()

const tableRef = ref<InstanceType<typeof DataTable> | null>(null)

function clearSelection(): void {
  tableRef.value?.clearSelection()
}

defineExpose({ clearSelection })

function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
  if (props.sortBy !== stateKey) return false
  if (props.sortOrder === 'asc') return 'ascend'
  if (props.sortOrder === 'desc') return 'descend'
  return false
}

function asRow(row: TableRow): GroupRow {
  return row as GroupRow
}

function renderName(row: TableRow): VNodeChild {
  const group = asRow(row)
  return h(TitleTip, { content: String((group.name) ?? '') }, { default: () => h('span', { class: 'gt-name' }, group.name || FMT_EMPTY) })
}

function modelChip(model: string): VNodeChild {
  return h(TitleTip, { content: String((model) ?? ''), key: 'model-' + model }, { default: () => h(Chip, { key: 'model-' + model, mono: true }, { default: () => model }) })
}

function renderModels(row: TableRow): VNodeChild {
  const group = asRow(row)
  if (!group.models.length) return h('span', { class: 'gt-faint' }, FMT_EMPTY)
  const nodes: VNodeChild[] = group.modelPreview.map(modelChip)
  if (group.modelOverflow > 0) {
    nodes.push(
      h(
        NPopover,
        { key: 'overflow', trigger: 'hover', placement: 'bottom-start', delay: 200, showArrow: false },
        {
          trigger: () =>
            h(
              'span',
              {
                class: 'gt-more',
                tabindex: 0,
                'aria-label': t('page.groups.cell.more', { count: group.modelOverflow }),
              },
              '+' + group.modelOverflow,
            ),
          default: () =>
            h('div', { class: 'gt-pop' }, [
              h('p', { class: 'gt-pop__title' }, t('page.groups.cell.allModels', { count: group.models.length })),
              h('div', { class: 'gt-pop__chips' }, group.models.map(modelChip)),
            ]),
        },
      ),
    )
  }
  if (group.staleCount > 0) {
    nodes.push(
      h(TitleTip, { content: String((t('page.groups.cell.staleTitle', { count: group.effectiveCount })) ?? ''), key: 'stale' }, { default: () => h(
        Badge,
        { key: 'stale', tone: 'warn' },
        { default: () => t('page.groups.cell.staleBadge', { count: group.staleCount }) },
      ) }),
    )
  }
  return h('div', { class: 'gt-models' }, nodes)
}

function renderKeyCount(row: TableRow): VNodeChild {
  const group = asRow(row)
  return h('span', { class: group.keyCount > 0 ? 'gt-num' : 'gt-num is-zero' }, fmtInt(group.keyCount))
}

function renderNote(row: TableRow): VNodeChild {
  const group = asRow(row)
  if (!group.note) return h('span', { class: 'gt-faint' }, FMT_EMPTY)
  return h(TitleTip, { content: String((group.note) ?? '') }, { default: () => h('span', { class: 'gt-note' }, group.note) })
}

function renderActions(row: TableRow): VNodeChild {
  const group = asRow(row)
  return h(RowActions, {
    actions: [
      { key: 'edit', label: t('common.edit') },
      { key: 'delete', label: t('common.delete'), danger: true },
    ],
    max: 1,
    onSelect: (key: string) => emit('action', group, key),
  })
}

const columns = computed<TableColumn[]>(() => [
  { key: 'name', title: t('page.groups.col.name'), mobile: 'title', width: 200, sortable: true, sortOrder: sortOrderOf('name'), render: renderName },
  {
    key: 'models',
    title: t('page.groups.col.models'),
    mobile: 'meta',
    minWidth: 360,
    sortable: true,
    sortOrder: sortOrderOf('model_count'),
    render: renderModels,
  },
  {
    key: 'key_count',
    title: t('page.groups.col.keys'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('key_count'),
    render: renderKeyCount,
  },
  { key: 'note', title: t('page.groups.col.note'), mobile: 'subtitle', minWidth: 180, render: renderNote },
  { key: 'actions', title: '', mobile: 'actions', width: 116, hideable: false, fixed: 'right', render: renderActions },
])

function onChecked(keys: Array<string | number>): void {
  emit('checked', keys.map(String))
}
</script>

<template>
  <DataTable
    ref="tableRef"
    class="gt"
    table-id="groups"
    :columns="columns"
    :data="rows"
    row-key="key"
    remote
    selectable
    :loading="loading"
    :refreshing="refreshing"
    :page="page"
    :page-size="pageSize"
    :item-count="total"
    :page-sizes="[20, 50, 100]"
    :empty-description="emptyDescription"
    @update:page="(value: number) => emit('page', value)"
    @update:page-size="(value: number) => emit('pageSize', value)"
    @update:sorter="(sorter: SorterPayload | null) => emit('sorter', sorter)"
    @update:checked-keys="onChecked"
  >
    <template #toolbar><slot name="toolbar" /></template>
    <template #bulk="{ clear }"><slot name="bulk" :clear="clear" /></template>
    <template #empty>
      <Empty illustration="empty" :description="emptyDescription" :icon="Layers">
        <Btn v-if="hasFilters" variant="default" size="sm" @click="emit('reset')">{{ t('page.groups.clearFilters') }}</Btn>
        <Btn v-else variant="default" size="sm" :icon="Plus" @click="emit('create')">{{ t('page.groups.create') }}</Btn>
      </Empty>
    </template>
  </DataTable>
</template>

<style scoped>
.gt :deep(.mh-table__toolbar-left) {
  flex: 1;
}

.gt :deep(.n-data-table) {
  --n-merged-td-color-sorting: var(--n-merged-td-color) !important;
}

.gt :deep(.gt-name) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: var(--fg);
}

/* 不换行：chip 超出列宽时裁掉，靠「+N」悬停看全部（第 2 轮审查 P2） */
.gt :deep(.gt-models) {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
  overflow: hidden;
}

.gt :deep(.gt-models > *) {
  flex: none;
}

.gt :deep(.gt-more) {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 var(--sp-2);
  border: 1px dashed var(--border);
  border-radius: var(--r-chip);
  font-size: var(--fs-small);
  font-variant-numeric: tabular-nums;
  color: var(--fg-2);
  cursor: default;
}

.gt :deep(.gt-more:focus-visible) {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

.gt :deep(.gt-num) {
  font-variant-numeric: tabular-nums;
  color: var(--fg);
}

.gt :deep(.gt-num.is-zero),
.gt :deep(.gt-faint) {
  color: var(--fg-faint);
}

.gt :deep(.gt-note) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}
</style>

<style>
/* 悬停浮层挂在 body 下，带不上本组件的 scope 属性，所以不用 scoped。 */
.gt-pop {
  display: grid;
  gap: var(--sp-2);
  max-width: 420px;
}

.gt-pop__title {
  margin: 0;
  font-size: var(--fs-small);
  font-weight: 600;
  color: var(--fg);
}

.gt-pop__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1);
}
</style>
