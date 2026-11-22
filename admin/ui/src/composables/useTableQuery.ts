/**
 * 表格查询状态 ↔ 路由 query（04-components §4）。
 *
 * 规则：
 *   · 分页 / 排序 / 筛选全部写进 hash 路由的 query（/logs?platform=trae&result=error&page=2），
 *     刷新与分享链接都不丢；
 *   · 筛选项的 key 直接用业务字段名（platform / result / keyword…），不额外加前缀；
 *   · 返回浏览器前进/后退时状态跟着变；
 *   · 每页条数按表格 id 记在 localStorage（mh.pageSize.<key>），与 DataTable 的密度偏好同一套做法。
 */
import { computed, reactive, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery, type LocationQueryValue } from 'vue-router'

export type SortOrder = 'asc' | 'desc' | ''

export interface TableQueryState {
  page: number
  pageSize: number
  sortBy: string
  sortOrder: SortOrder
  filters: Record<string, string>
}

export interface TableQueryOptions {
  page?: number
  pageSize?: number
  sortBy?: string
  sortOrder?: SortOrder
  /** 业务筛选默认值：{ platform: '', result: '' }。 */
  filters?: Record<string, string>
}

export const PAGE_SIZE_STORAGE_PREFIX = 'mh.pageSize.'

function readFirst(value: LocationQueryValue | LocationQueryValue[]): string {
  if (Array.isArray(value)) return value.length ? String(value[0] ?? '') : ''
  return value === null || value === undefined ? '' : String(value)
}

function toPositiveInt(value: string, fallback: number): number {
  const n = Number(value)
  return Number.isInteger(n) && n > 0 ? n : fallback
}

export function useTableQuery(key: string, options: TableQueryOptions = {}) {
  const route = useRoute()
  const router = useRouter()

  const defaults: TableQueryState = {
    page: options.page ?? 1,
    pageSize: options.pageSize ?? 20,
    sortBy: options.sortBy ?? '',
    sortOrder: options.sortOrder ?? '',
    filters: { ...(options.filters || {}) },
  }

  function savedPageSize(): number {
    try {
      const raw = localStorage.getItem(PAGE_SIZE_STORAGE_PREFIX + key)
      return raw ? toPositiveInt(raw, defaults.pageSize) : defaults.pageSize
    } catch {
      return defaults.pageSize
    }
  }

  function readQuery(query: LocationQuery): TableQueryState {
    const filters: Record<string, string> = { ...defaults.filters }
    for (const name of Object.keys(defaults.filters)) {
      filters[name] = readFirst(query[name])
    }
    const order = readFirst(query.order)
    return {
      page: toPositiveInt(readFirst(query.page), defaults.page),
      pageSize: toPositiveInt(readFirst(query.size), savedPageSize()),
      sortBy: readFirst(query.sort) || defaults.sortBy,
      sortOrder: order === 'asc' || order === 'desc' ? order : defaults.sortOrder,
      filters,
    }
  }

  const state = reactive<TableQueryState>(readQuery(route.query))
  let writing = false

  function sameQuery(a: LocationQuery, b: LocationQuery): boolean {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    for (const name of keys) {
      if (readFirst(a[name]) !== readFirst(b[name])) return false
    }
    return true
  }

  function push(): void {
    const query: LocationQuery = { ...route.query }
    if (state.page > 1) query.page = String(state.page)
    else delete query.page
    if (state.pageSize !== defaults.pageSize) query.size = String(state.pageSize)
    else delete query.size
    if (state.sortBy) {
      query.sort = state.sortBy
      if (state.sortOrder) query.order = state.sortOrder
      else delete query.order
    } else {
      delete query.sort
      delete query.order
    }
    for (const [name, value] of Object.entries(state.filters)) {
      if (value) query[name] = value
      else delete query[name]
    }
    if (sameQuery(query, route.query)) return
    writing = true
    void Promise.resolve(router.replace({ query })).finally(() => {
      writing = false
    })
  }

  watch(
    () => route.query,
    (query) => {
      if (writing) return
      const next = readQuery(query)
      state.page = next.page
      state.pageSize = next.pageSize
      state.sortBy = next.sortBy
      state.sortOrder = next.sortOrder
      state.filters = next.filters
    },
  )

  function setPage(page: number): void {
    state.page = Math.max(1, Math.floor(page || 1))
    push()
  }

  function setPageSize(size: number): void {
    state.pageSize = Math.max(1, Math.floor(size || defaults.pageSize))
    state.page = 1
    try {
      localStorage.setItem(PAGE_SIZE_STORAGE_PREFIX + key, String(state.pageSize))
    } catch {
      /* 存不了不影响本次会话 */
    }
    push()
  }

  function setSort(sortBy: string, sortOrder: SortOrder = ''): void {
    state.sortBy = sortBy
    state.sortOrder = sortOrder
    state.page = 1
    push()
  }

  function setFilter(name: string, value: string): void {
    state.filters[name] = value ?? ''
    state.page = 1
    push()
  }

  function setFilters(values: Record<string, string>): void {
    Object.assign(state.filters, values)
    state.page = 1
    push()
  }

  function reset(): void {
    state.page = defaults.page
    state.pageSize = defaults.pageSize
    state.sortBy = defaults.sortBy
    state.sortOrder = defaults.sortOrder
    state.filters = { ...defaults.filters }
    push()
  }

  /** 当前查询条件快照（页面自己映射成接口参数）。 */
  const query = computed(() => ({
    page: state.page,
    pageSize: state.pageSize,
    sortBy: state.sortBy,
    sortOrder: state.sortOrder,
    filters: { ...state.filters },
  }))

  return { key, state, query, setPage, setPageSize, setSort, setFilter, setFilters, reset }
}
