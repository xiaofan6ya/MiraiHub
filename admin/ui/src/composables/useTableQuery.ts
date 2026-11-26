/**
 * 表格查询状态 ↔ 路由 query（04-components §4）。
 *
 * 规则：
 *   · 分页 / 排序 / 筛选全部写进 hash 路由的 query（/logs?platform=trae&result=error&page=2），
 *     刷新与分享链接都不丢；
 *   · 筛选项的 key 直接用业务字段名（platform / result / keyword…），不额外加前缀；
 *   · 返回浏览器前进/后退时状态跟着变；
 *   · 每页条数按表格 id 记在 localStorage（mh.pageSize.<key>），与 DataTable 的密度偏好同一套做法。
 *
 * KeepAlive（第 5 轮 P1）：页面被缓存时组件不卸载，`useRoute()` 拿到的是**全局当前路由**。
 * 所以只在「本页路由」（setup 时记下的 route.name，没有 name 时比 path）激活时读写地址栏：
 *   · 缓存期间别的页面的同名 query（days / q / page …）不读进来，页面也就不会在后台重拉；
 *   · 缓存期间不写地址栏（调用 setXxx 只改内存状态）；
 *   · 回到本页时：地址栏带了本表格管的键（深链 / 前进后退）→ 按地址栏读；
 *     一个都没带（侧栏 / 面包屑回来）→ 把缓存里的状态写回地址栏（`restoreOnReturn`，默认 true）。
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
  /**
   * 从别的页面回到本页、且地址栏不含本表格管的任何键时，把缓存中的状态写回地址栏（默认 true）。
   * 传 false：此时按地址栏（即默认值）重置。
   */
  restoreOnReturn?: boolean
}

export const PAGE_SIZE_STORAGE_PREFIX = 'mh.pageSize.'

function readFirst(value: LocationQueryValue | LocationQueryValue[]): string {
  if (Array.isArray(value)) return value.length ? String(value[0] ?? '') : ''
  return value === null || value === undefined ? '' : String(value)
}

/**
 * 同一个 router 上「已发出、还没落地」的 query。
 * 一个页面里有多个 useTableQuery（如 Ip 的三个视图）在同一拍里各自 replace 时，
 * 后一个以前一个为底合并，不会把对方刚写的键覆盖掉。
 */
const pendingQueries = new WeakMap<object, LocationQuery>()

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

  // 本页路由的身份：KeepAlive 缓存期间 route 会变成别的页面，靠它判断「现在是不是在本页」。
  const ownName = route.name ?? null
  const ownPath = route.path
  function onOwnRoute(): boolean {
    return ownName !== null ? route.name === ownName : route.path === ownPath
  }
  const managedKeys = ['page', 'size', 'sort', 'order', ...Object.keys(defaults.filters)]
  function hasManagedKey(query: LocationQuery): boolean {
    return managedKeys.some((name) => readFirst(query[name]) !== '')
  }

  const state = reactive<TableQueryState>(readQuery(route.query))
  let writing = false
  let away = false

  function sameQuery(a: LocationQuery, b: LocationQuery): boolean {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    for (const name of keys) {
      if (readFirst(a[name]) !== readFirst(b[name])) return false
    }
    return true
  }

  function push(): void {
    // 缓存期间（别的页面在前台）不碰地址栏；回来时由 restoreOnReturn 补写。
    if (!onOwnRoute()) return
    const base = pendingQueries.get(router) ?? route.query
    const query: LocationQuery = { ...base }
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
    if (sameQuery(query, base)) return
    writing = true
    pendingQueries.set(router, query)
    void Promise.resolve(router.replace({ query })).finally(() => {
      if (pendingQueries.get(router) === query) pendingQueries.delete(router)
      writing = false
    })
  }

  /**
   * 只写真正变了的字段：自己 replace 之后路由回声（writing 已复位时）读回来的是同一组值，
   * 原来整体替换 `state.filters` 会让页面的 deep watch 再触发一次重拉。
   */
  function apply(next: TableQueryState): void {
    if (state.page !== next.page) state.page = next.page
    if (state.pageSize !== next.pageSize) state.pageSize = next.pageSize
    if (state.sortBy !== next.sortBy) state.sortBy = next.sortBy
    if (state.sortOrder !== next.sortOrder) state.sortOrder = next.sortOrder
    const keys = new Set([...Object.keys(state.filters), ...Object.keys(next.filters)])
    let filtersChanged = false
    for (const name of keys) {
      if ((state.filters[name] ?? '') !== (next.filters[name] ?? '')) filtersChanged = true
    }
    if (filtersChanged) state.filters = next.filters
  }

  watch(
    () => [route.name, route.path, route.query] as const,
    ([, , query]) => {
      if (!onOwnRoute()) {
        // 别的页面在前台：同名参数与本表格无关，不读、不触发页面的 state watch（不后台重拉）。
        away = true
        return
      }
      const returning = away
      away = false
      if (writing) return
      if (returning && options.restoreOnReturn !== false && !hasManagedKey(query)) {
        push()
        return
      }
      apply(readQuery(query))
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
