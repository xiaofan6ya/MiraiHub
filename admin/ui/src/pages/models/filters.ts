/**
 * 模型配置页的筛选项 / chip / 排序（从 ModelsPage.vue 拆出，10-visual-refresh §5）。
 * 只搬家、不改逻辑：search 仍由页面持有（模板里有直接赋值），这里只读写它。
 */
import { computed, type ComputedRef, type Ref } from 'vue'
import type { FilterChip, SorterPayload } from '@/components'
import type { useTableQuery } from '@/composables'
import { useI18n } from '@/i18n'
import { platformOptions, vendorOptions, type ModelPlatformSummary, type ModelRow } from '@/api/models'

export interface ModelFilterDeps {
  table: ReturnType<typeof useTableQuery>
  allRows: ComputedRef<ModelRow[]>
  platforms: Ref<ModelPlatformSummary[]>
  search: Ref<string>
}

export function useModelFilters(deps: ModelFilterDeps) {
  const { table, allRows, platforms, search } = deps
  const { t } = useI18n()

  const vendorSelectOptions = computed(() => vendorOptions(allRows.value))
  const platformSelectOptions = computed(() => platformOptions(platforms.value))

  const CAP_LABELS = computed<Record<string, string>>(() => ({
    image: t('page.models.cap.image'),
    tools: t('page.models.cap.tools'),
    reasoning: t('page.models.cap.reasoning'),
    video: t('page.models.cap.video'),
    'image-out': t('page.models.cap.imageOut'),
  }))

  const CAPABILITY_OPTIONS = computed(() => [
    { label: t('page.models.filter.capAll'), value: '' },
    ...Object.entries(CAP_LABELS.value).map(([value, label]) => ({ label, value })),
  ])

  const STATUS_OPTIONS = computed(() => [
    { label: t('page.models.filter.statusAll'), value: '' },
    { label: t('page.models.filter.statusEnabled'), value: 'enabled' },
    { label: t('page.models.filter.statusMixed'), value: 'disabled' },
  ])

  const STATUS_LABELS = computed<Record<string, string>>(() => ({
    enabled: t('page.models.filter.statusEnabled'),
    disabled: t('page.models.filter.statusMixed'),
  }))

  function labelOf(
    options: Array<{ label: string; value: string }>,
    value: string,
  ): string {
    const hit = options.find((option) => option.value === value)
    if (!hit) return value
    // 选择项的标签带「（N 个模型）」或「：全部」后缀，chip 里只留主体。
    return hit.label.replace(/\uFF08.*\uFF09$/, '').replace(/\uFF1A\u5168\u90E8$/, '')
  }

  const chips = computed<FilterChip[]>(() => {
    const filters = table.state.filters
    const list: FilterChip[] = []
    if (filters.q) list.push({ key: 'q', label: t('page.models.filter.chipSearch'), value: filters.q })
    if (filters.vendor) {
      list.push({
        key: 'vendor',
        label: t('page.models.filter.chipVendor'),
        value: labelOf(vendorSelectOptions.value, filters.vendor),
      })
    }
    if (filters.platform) {
      list.push({
        key: 'platform',
        label: t('page.models.filter.chipPlatform'),
        value: labelOf(platformSelectOptions.value, filters.platform),
      })
    }
    if (filters.cap) {
      list.push({
        key: 'cap',
        label: t('page.models.filter.chipCap'),
        value: CAP_LABELS.value[filters.cap] || filters.cap,
      })
    }
    if (filters.status) {
      list.push({
        key: 'status',
        label: t('page.models.filter.chipStatus'),
        value: STATUS_LABELS.value[filters.status] || filters.status,
      })
    }
    return list
  })

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

  /* ------------------------------ 排序 ------------------------------ */

  const SORT_KEY_MAP: Record<string, string> = {
    name: 'name',
    multiplier: 'multiplier',
    platforms: 'platforms',
  }

  function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
    if (table.state.sortBy !== stateKey) return false
    if (table.state.sortOrder === 'asc') return 'ascend'
    if (table.state.sortOrder === 'desc') return 'descend'
    return false
  }

  /** 三态：升序 → 降序 → 默认（naive 的 cancel 对应 order=false）。 */
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

  return {
    vendorSelectOptions,
    platformSelectOptions,
    CAP_LABELS,
    CAPABILITY_OPTIONS,
    STATUS_OPTIONS,
    chips,
    onFilterChange,
    applyFilter,
    removeChip,
    resetFilters,
    sortOrderOf,
    onSorter,
  }
}
