/**
 * 当前平台上下文（对应旧版 admin/console/platform-switch.js）。
 *
 * 语义必须与旧版一致，不能"简化成账号列表的筛选器"：
 *   · 切平台 = 换全局上下文：每个后台请求都带 X-Platform，后端据此收窄统计口径；
 *   · 两个粒度：平台（trae）与区域（trae:cn）；点平台 = 整个平台，再点区域 = 只看那个区；
 *   · 平台与区域都由 /api/platforms 下发，前端不硬编码平台列表；
 *   · 持久化两处：localStorage['wb_platform_current'] = {platform, realm}（旧版行为）
 *     + 后端 GET/PUT /api/platforms/current（03-shell §2.2 要求"两处都要读写"）；
 *   · 切换后派发 document 事件 wb-platform-change（旧版各面板靠它重新取数）。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getCurrentPlatform,
  listPlatforms,
  putCurrentPlatform,
  type PlatformSpec,
} from '@/api/platforms'
import { PLATFORM_HEADER_NAME, PLATFORM_STORAGE_KEY, setHeaderProvider } from '@/api/http'

/** 后端 registry.DEFAULT_PLATFORM，恒为 workbuddy。 */
export const DEFAULT_PLATFORM = 'workbuddy'

/**
 * 平台专属页面：这些页面只在指定**落库平台**下存在。
 *
 * 与旧版 console-app.js:1366 的 _PLATFORM_ONLY_TABS 完全一致（目前只有成长计划）。
 * 切到不支持的平台时，外壳应跳到 fallbackTab()，不要停在空页面。
 */
export const PLATFORM_ONLY_TABS: Readonly<Record<string, string>> = {
  growth: 'workbuddy',
}

/** 顶部平台选择器派发的事件名（旧版同名，页面订阅它即可）。 */
export const PLATFORM_CHANGE_EVENT = 'wb-platform-change'

export interface PlatformSelection {
  platform: string
  realm: string
}

export interface PlatformChangeDetail {
  platform: string
  realm: string
  /** 当前平台对象（找不到时为 null）。 */
  spec: PlatformSpec | null
  /** 选择器：trae:cn / trae / workbuddy（旧版 WBPlatform.route()）。 */
  selector: string
}

type ChangeListener = (detail: PlatformChangeDetail) => void

/** localStorage 里的 {platform, realm}；损坏时当没有。 */
export function readSavedSelection(): PlatformSelection | null {
  try {
    const raw = JSON.parse(localStorage.getItem(PLATFORM_STORAGE_KEY) || 'null') as
      | { platform?: unknown; realm?: unknown }
      | null
    if (raw && typeof raw.platform === 'string' && raw.platform.trim()) {
      return { platform: raw.platform.trim(), realm: typeof raw.realm === 'string' ? raw.realm.trim() : '' }
    }
  } catch {
    /* 本地值损坏时忽略 */
  }
  return null
}

export function writeSavedSelection(selection: PlatformSelection): void {
  try {
    localStorage.setItem(
      PLATFORM_STORAGE_KEY,
      JSON.stringify({ platform: selection.platform, realm: selection.realm }),
    )
  } catch {
    /* 隐私模式写不进去，不影响使用 */
  }
}

/** 加载失败时的兜底：与旧版一致，只认 WorkBuddy 一个平台。 */
function fallbackPlatformSpec(): PlatformSpec {
  return {
    id: DEFAULT_PLATFORM,
    label: 'WorkBuddy',
    enabled: true,
    supports_local_scan: false,
    auth_mode: '',
    default_realm: '',
    notes: '',
    icon: DEFAULT_PLATFORM,
    storage_platform: DEFAULT_PLATFORM,
    realm: '',
    realm_label: '',
    realms: [],
    proxied: false,
    rating: {},
    extra: {},
    managed: false,
    actions: [],
    quota_unit: 'credits',
    quota_unit_label: '',
    account_count: null,
    realm_counts: {},
  }
}

export const usePlatformStore = defineStore('platform', () => {
  const items = ref<PlatformSpec[]>([])
  const platform = ref(DEFAULT_PLATFORM)
  const realm = ref('')
  /** 计数缓存：平台 id 或 "平台:区域" → 账号数（取不到就是 undefined，显示 —）。 */
  const counts = ref<Record<string, number>>({})
  const loaded = ref(false)
  const busy = ref(false)

  const listeners = new Set<ChangeListener>()
  let loadPromise: Promise<void> | null = null

  /** 已加载的平台清单（/api/platforms 的 items，顺序即后端登记顺序）。 */
  const platforms = computed<PlatformSpec[]>(() => items.value)
  const current = computed<PlatformSelection>(() => ({
    platform: platform.value,
    realm: realm.value,
  }))
  /** 对外平台 id（trae-cn / trae-intl 与 trae 是不同 id）。 */
  const currentId = computed<string>(() => platform.value)
  const currentSpec = computed<PlatformSpec | null>(() => specOf(platform.value))
  /** 选择器：trae:cn（有区域）/ trae（无区域）。 */
  const selector = computed<string>(() =>
    realm.value ? platform.value + ':' + realm.value : platform.value,
  )
  /** 落库平台：trae-cn / trae-intl 都是 trae。 */
  const storagePlatform = computed<string>(() => currentSpec.value?.storage_platform || platform.value)

  /**
   * 注入 X-Platform：http 层每个请求都会问这里一次。
   * （旧版是 _apiRaw 里直接读 window.WBPlatform.header()；这里换成注册回调，避免循环依赖。）
   */
  setHeaderProvider(() => ({ [PLATFORM_HEADER_NAME]: selector.value }))

  function specOf(id: string): PlatformSpec | null {
    const key = String(id || '').trim().toLowerCase()
    if (!key) return null
    return items.value.find((item) => String(item.id).toLowerCase() === key) || null
  }

  /**
   * 按 id 取平台对象，找不到时回落：先按落库平台匹配（trae → 第一个 trae 区），
   * 最后才用当前平台。给"把落库平台 id 翻成中文名"的场景用（旧版 WBPlatform.specOf）。
   */
  function specFor(id: string): PlatformSpec | null {
    const exact = specOf(id)
    if (exact) return exact
    const storage = String(id || '').trim().toLowerCase()
    const byStorage = items.value.find(
      (item) => String(item.storage_platform || '').toLowerCase() === storage,
    )
    return byStorage || currentSpec.value
  }

  function countOf(id: string): number | null {
    const value = counts.value[id]
    if (typeof value === 'number') return value
    const spec = specOf(id)
    return typeof spec?.account_count === 'number' ? spec.account_count : null
  }

  function realmCountOf(id: string, realmKey: string): number | null {
    const value = counts.value[id + ':' + realmKey]
    if (typeof value === 'number') return value
    const spec = specOf(id)
    const perRealm = spec?.realm_counts || {}
    return typeof perRealm[realmKey] === 'number' ? perRealm[realmKey] : null
  }

  function applyCounts(list: PlatformSpec[]): void {
    const next: Record<string, number> = {}
    for (const item of list) {
      if (item.account_count === undefined || item.account_count === null) continue
      next[item.id] = item.account_count
      for (const [realmKey, value] of Object.entries(item.realm_counts || {})) {
        next[item.id + ':' + realmKey] = value
      }
    }
    counts.value = next
  }

  function emit(): void {
    const detail: PlatformChangeDetail = {
      platform: platform.value,
      realm: realm.value,
      spec: specOf(platform.value),
      selector: selector.value,
    }
    for (const listener of listeners) {
      try {
        listener(detail)
      } catch (error) {
        console.error('[platform] listener failed', error)
      }
    }
    if (typeof document !== 'undefined' && typeof CustomEvent === 'function') {
      document.dispatchEvent(new CustomEvent(PLATFORM_CHANGE_EVENT, { detail }))
    }
  }

  interface ApplyOptions {
    /** 即使选择没变也通知一次（首屏加载必须发一次，否则概览一直是空的）。 */
    force?: boolean
    /** 跳过切换后的账号数刷新（load() 已经拉过一次列表时用）。 */
    skipCounts?: boolean
  }

  /** 内部切换：只改状态、持久化、通知，不写后端。 */
  function applySelection(nextPlatform: string, nextRealm: string, options: ApplyOptions = {}): boolean {
    const spec = specOf(nextPlatform)
    if (spec) {
      let normalized = nextRealm || ''
      if (normalized && !spec.realms.some((item) => item.key === normalized)) normalized = ''
      const changed = spec.id !== platform.value || normalized !== realm.value
      platform.value = spec.id
      realm.value = normalized
      writeSavedSelection({ platform: spec.id, realm: normalized })
      if (changed || options.force) {
        emit()
        if (!options.skipCounts) void refreshCounts()
      }
      return changed
    }
    return false
  }

  async function fetchList(): Promise<PlatformSpec[] | null> {
    try {
      const payload = await listPlatforms()
      const list = Array.isArray(payload?.items) ? payload.items : []
      return list.length ? list : [fallbackPlatformSpec()]
    } catch (error) {
      // 网络失败时保留旧数据（旧版：applyCounts(state.items)，不清零）
      console.warn('[platform] 平台列表加载失败', error)
      return null
    }
  }

  /** 重新拉 /api/platforms 并刷新账号数（导入/删除/刷新账号后调用）。 */
  async function refreshCounts(): Promise<void> {
    const list = await fetchList()
    if (!list) return
    items.value = list
    applyCounts(list)
  }

  /**
   * 首屏加载平台列表并确定当前平台（旧版 WBPlatform.load()）。
   *
   * 当前平台的来源顺序：localStorage → 后端 /api/platforms/current → 列表第一个。
   * 并发调用共享同一次请求。
   */
  async function load(): Promise<void> {
    if (loadPromise) return loadPromise
    loadPromise = (async () => {
      busy.value = true
      try {
        const list = (await fetchList()) || [fallbackPlatformSpec()]
        items.value = list
        applyCounts(list)

        let picked = readSavedSelection()
        if (!picked) {
          const remote = await getCurrentPlatform().catch(() => null)
          if (remote && remote.platform) picked = { platform: remote.platform, realm: remote.realm || '' }
        }
        const exists = Boolean(picked && specOf(picked.platform))
        const nextPlatform = exists && picked ? picked.platform : list[0]?.id || DEFAULT_PLATFORM
        const nextRealm = exists && picked ? picked.realm : ''
        loaded.value = true
        // 首屏也要 emit 一次：默认面板是平台管理，概览卡片等它解析完才有数据。
        applySelection(nextPlatform, nextRealm, { force: true, skipCounts: true })
      } finally {
        busy.value = false
        loadPromise = null
      }
    })()
    return loadPromise
  }

  /** 重新加载列表（保留当前平台；当前平台没了就回默认）。 */
  async function reload(): Promise<void> {
    if (!loaded.value) {
      await load()
      return
    }
    const list = (await fetchList()) || items.value
    items.value = list
    applyCounts(list)
    if (!specOf(platform.value)) {
      applySelection(list[0]?.id || DEFAULT_PLATFORM, '', { force: true, skipCounts: true })
      return
    }
    emit()
  }

  /**
   * 用户切换平台（等价旧版 WBPlatform.select）。
   *
   * 本地立即生效 + 通知，随后把选择写进后端（失败只告警，不回滚 —— 旧版
   * 后端那个值只用于"下次打开时恢复"，写不进去不影响本次会话）。
   * 返回是否真的发生了变化。
   */
  function setCurrent(nextPlatform: string, nextRealm = ''): boolean {
    // 未知平台直接忽略（旧版 select 同样 return）：不要拿它去写后端
    const target = specOf(nextPlatform)
    if (!target) return false
    const changed = applySelection(target.id, nextRealm)
    const spec = specOf(platform.value)
    if (spec) {
      void putCurrentPlatform({ platform: spec.id, realm: realm.value }).catch((error) => {
        console.warn('[platform] 记住当前平台失败', error)
      })
    }
    return changed
  }

  /** 旧版方法名，等价于 setCurrent（保留给按旧文档写的代码）。 */
  const select = setCurrent

  /** 订阅平台变化，返回取消函数。 */
  function onChange(callback: ChangeListener): () => void {
    if (typeof callback !== 'function') return () => undefined
    listeners.add(callback)
    return () => {
      listeners.delete(callback)
    }
  }

  /** 04-components 里的命名（onPlatformChange），与 onChange 等价。 */
  const onPlatformChange = onChange

  /** 该页面在当前平台下是否存在（旧版 _visibleTabsOf / activateTab 的判定）。 */
  function supports(tabKey: string): boolean {
    const need = PLATFORM_ONLY_TABS[tabKey]
    if (!need) return true
    const storage = storagePlatform.value
    if (!storage) return true
    return storage === need
  }

  /** 平台专属页面不可用时的落点（旧版：growth → accounts，其它 → 默认页）。 */
  function fallbackTab(tabKey: string): string {
    if (supports(tabKey)) return tabKey
    return tabKey === 'growth' ? 'accounts' : 'overview'
  }

  /** 请求头（旧版 WBPlatform.header()）。 */
  function header(): Record<string, string> {
    return { [PLATFORM_HEADER_NAME]: selector.value }
  }

  return {
    items,
    platforms,
    platform,
    realm,
    counts,
    loaded,
    busy,
    current,
    currentId,
    currentSpec,
    selector,
    storagePlatform,
    specOf,
    specFor,
    countOf,
    realmCountOf,
    load,
    reload,
    refreshCounts,
    setCurrent,
    select,
    onChange,
    onPlatformChange,
    supports,
    fallbackTab,
    header,
  }
})
