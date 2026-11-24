/**
 * 定时任务页的筛选项 / chip / 排序 / 筛选操作（从 SchedulesPage.vue 拆出，10-visual-refresh §5）。
 * 只搬家、不改逻辑：search 仍由页面持有（模板里有直接赋值），这里只读写它。
 */
import { computed, type ComputedRef, type Ref } from 'vue'
import type { FilterChip, SorterPayload } from '@/components'
import type { useTableQuery } from '@/composables'
import { useI18n } from '@/i18n'
import type { ScheduleRow, ScheduleTaskOption } from '@/api/schedules'

export interface ScheduleFilterDeps {
  table: ReturnType<typeof useTableQuery>
  allRows: ComputedRef<ScheduleRow[]>
  taskCatalog: Ref<ScheduleTaskOption[]>
  search: Ref<string>
}

export function useScheduleFilters(deps: ScheduleFilterDeps) {
  const { table, allRows, taskCatalog, search } = deps
  const { t } = useI18n()

  const platformFilterOptions = computed(() => {
    const seen = new Map<string, string>()
    for (const row of allRows.value) {
      if (row.platform && !seen.has(row.platform)) seen.set(row.platform, row.platformLabel)
    }
    return [
      { label: t('page.schedules.filter.platformAll'), value: '' },
      ...Array.from(seen.entries())
        .sort((a, b) => a[1].localeCompare(b[1], 'zh-CN'))
        .map(([value, label]) => ({ label, value })),
    ]
  })

  const taskFilterOptions = computed(() => {
    const options = (taskCatalog.value.length ? taskCatalog.value : []).map((item) => ({
      label: item.label,
      value: item.value,
    }))
    if (!options.length) {
      for (const row of allRows.value) {
        if (!options.some((option) => option.value === row.task)) {
          options.push({ label: row.taskLabel, value: row.task })
        }
      }
    }
    return [{ label: t('page.schedules.filter.taskAll'), value: '' }, ...options]
  })

  const stateFilterOptions = computed(() => [
    { label: t('page.schedules.filter.stateAll'), value: '' },
    { label: t('page.schedules.filter.enabled'), value: 'enabled' },
    { label: t('page.schedules.filter.disabled'), value: 'disabled' },
    { label: t('page.schedules.filter.due'), value: 'due' },
  ])

  const resultFilterOptions = computed(() => [
    { label: t('page.schedules.filter.resultAll'), value: '' },
    { label: t('page.schedules.filter.ok'), value: 'ok' },
    { label: t('page.schedules.filter.warn'), value: 'warn' },
    { label: t('page.schedules.filter.danger'), value: 'danger' },
    { label: t('page.schedules.filter.none'), value: 'none' },
  ])

  const stateChipLabel = computed(() => {
    switch (table.state.filters.state) {
      case 'enabled':
        return t('page.schedules.filter.enabled')
      case 'disabled':
        return t('page.schedules.filter.disabled')
      case 'due':
        return t('page.schedules.filter.due')
      default:
        return ''
    }
  })

  const resultChipLabel = computed(() => {
    switch (table.state.filters.result) {
      case 'ok':
        return t('page.schedules.filter.ok')
      case 'warn':
        return t('page.schedules.filter.warn')
      case 'danger':
        return t('page.schedules.filter.danger')
      case 'none':
        return t('page.schedules.filter.none')
      default:
        return ''
    }
  })

  const chips = computed<FilterChip[]>(() => {
    const filters = table.state.filters
    const list: FilterChip[] = []
    if (filters.q) list.push({ key: 'q', label: t('page.schedules.filter.chipSearch'), value: filters.q })
    if (filters.platform) {
      const hit = platformFilterOptions.value.find((option) => option.value === filters.platform)
      list.push({
        key: 'platform',
        label: t('page.schedules.filter.chipPlatform'),
        value: hit?.label || filters.platform,
      })
    }
    if (filters.task) {
      const hit = taskFilterOptions.value.find((option) => option.value === filters.task)
      list.push({ key: 'task', label: t('page.schedules.filter.chipTask'), value: hit?.label || filters.task })
    }
    if (filters.state) {
      list.push({ key: 'state', label: t('page.schedules.filter.chipState'), value: stateChipLabel.value })
    }
    if (filters.result) {
      list.push({ key: 'result', label: t('page.schedules.filter.chipResult'), value: resultChipLabel.value })
    }
    return list
  })

  const SORT_KEY_MAP: Record<string, string> = {
    name: 'name',
    platform: 'platform',
    cycle: 'next_run_at',
    last: 'last_run_at',
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

  return {
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
  }
}
