import type { Component } from 'vue'
import { GROUPS, type GroupKey, type PageKey } from './pages'

/**
 * 页面 chunk 加载器（11-polish-motion §2「首屏：路由 chunk 按需加载；悬停 / 聚焦预取」）。
 *
 * 为什么单独一个文件：router/index.ts 静态依赖 AppShell → SideNav，SideNav 又要预取页面 chunk；
 * 加载器放在 router/index.ts 里就成了循环依赖。这里**不 import 任何组件**，只有动态 import。
 *
 * 用法（侧栏、命令面板已接好，页面线一般不用直接调）：
 *   prefetchPage('logs')                // 立即预取一个页面的 chunk（重复调用只请求一次）
 *   prefetchGroupIdle('usage', 'logs')  // 空闲时预取同组其余页面
 */
export type PageLoader = () => Promise<Component>

export const PAGE_LOADERS: Partial<Record<PageKey, PageLoader>> = {
  maintenance: () => import('@/pages/MaintenancePage.vue').then((mod) => mod.default),
  sync: () => import('@/pages/SyncPage.vue').then((mod) => mod.default),
  alerts: () => import('@/pages/AlertsPage.vue').then((mod) => mod.default),
  groups: () => import('@/pages/GroupsPage.vue').then((mod) => mod.default),
  ip: () => import('@/pages/IpPage.vue').then((mod) => mod.default),
  schedules: () => import('@/pages/SchedulesPage.vue').then((mod) => mod.default),
  models: () => import('@/pages/ModelsPage.vue').then((mod) => mod.default),
  market: () => import('@/pages/MarketPage.vue').then((mod) => mod.default),
  keys: () => import('@/pages/KeysPage.vue').then((mod) => mod.default),
  logs: () => import('@/pages/LogsPage.vue').then((mod) => mod.default),
  usage: () => import('@/pages/UsagePage.vue').then((mod) => mod.default),
  stats: () => import('@/pages/StatsPage.vue').then((mod) => mod.default),
  sentinel: () => import('@/pages/SentinelPage.vue').then((mod) => mod.default),
  gateway: () => import('@/pages/GatewayPage.vue').then((mod) => mod.default),
  platforms: () => import('@/pages/PlatformsPage.vue').then((mod) => mod.default),
  overview: () => import('@/pages/OverviewPage.vue').then((mod) => mod.default),
  journal: () => import('@/pages/JournalPage.vue').then((mod) => mod.default),
  credits: () => import('@/pages/CreditsPage.vue').then((mod) => mod.default),
  growth: () => import('@/pages/GrowthPage.vue').then((mod) => mod.default),
  accounts: () => import('@/pages/AccountsPage.vue').then((mod) => mod.default),
  client: () => import('@/pages/ClientPage.vue').then((mod) => mod.default),
  settings: () => import('@/pages/SettingsPage.vue').then((mod) => mod.default),
  proxy: () => import('@/pages/ProxyPage.vue').then((mod) => mod.default),
  openentry: () => import('@/pages/OpenEntryPage.vue').then((mod) => mod.default),
  agent: () => import('@/pages/AgentPage.vue').then((mod) => mod.default),
}

const pending = new Map<PageKey, Promise<void>>()

/** 省流模式 / 2G 网络下不做预取（只影响预取，正常导航不受影响）。 */
function prefetchAllowed(): boolean {
  if (typeof navigator === 'undefined') return false
  const connection = (navigator as Navigator & {
    connection?: { saveData?: boolean; effectiveType?: string }
  }).connection
  if (connection?.saveData) return false
  if (connection?.effectiveType && /(^|-)2g$/.test(connection.effectiveType)) return false
  return true
}

/** 立即预取一个页面 chunk。同一页面只请求一次；失败后允许下次再试。 */
export function prefetchPage(key: PageKey): Promise<void> {
  const existing = pending.get(key)
  if (existing) return existing
  const loader = PAGE_LOADERS[key]
  if (!loader || !prefetchAllowed()) return Promise.resolve()
  const task = loader().then(
    () => undefined,
    () => {
      pending.delete(key)
    },
  )
  pending.set(key, task)
  return task
}

type IdleCallback = (cb: () => void, opts?: { timeout: number }) => number

/** requestIdleCallback，不支持时退回 setTimeout。 */
export function whenIdle(cb: () => void, timeoutMs = 2000): void {
  if (typeof window === 'undefined') return
  const requestIdle = (window as Window & { requestIdleCallback?: IdleCallback }).requestIdleCallback
  if (typeof requestIdle === 'function') requestIdle(cb, { timeout: timeoutMs })
  else globalThis.setTimeout(cb, 300)
}

/**
 * 第 4 轮：首屏期间不做空闲预取。
 * requestIdleCallback 带 2s timeout —— 慢网下首屏接口还没回来，2s 一到就会开始拉同组页面，和首屏抢带宽。
 * 这里先等 window load，再安静 1.5s，才进入空闲预取队列；之后的调用直接走 whenIdle。
 */
let afterLoadReady = false
const afterLoadQueue: Array<() => void> = []
let afterLoadArmed = false

function whenIdleAfterLoad(cb: () => void): void {
  if (afterLoadReady || typeof window === 'undefined') {
    whenIdle(cb)
    return
  }
  afterLoadQueue.push(cb)
  if (afterLoadArmed) return
  afterLoadArmed = true
  const flush = (): void => {
    globalThis.setTimeout(() => {
      afterLoadReady = true
      afterLoadQueue.splice(0).forEach((fn) => whenIdle(fn))
    }, 1500)
  }
  if (document.readyState === 'complete') flush()
  else window.addEventListener('load', flush, { once: true })
}

/** 空闲时逐个预取同组其余页面（每个空闲片只取一个，避免和当前页抢带宽）。 */
export function prefetchGroupIdle(groupKey: GroupKey, except?: PageKey | null): void {
  const group = GROUPS.find((item) => item.key === groupKey)
  if (!group) return
  const queue = group.pages.map((page) => page.key).filter((key) => key !== except && !pending.has(key))
  const next = (): void => {
    const key = queue.shift()
    if (!key) return
    void prefetchPage(key).finally(() => whenIdle(next))
  }
  whenIdleAfterLoad(next)
}

/** 测试用：清掉预取记录。 */
export function resetPrefetch(): void {
  pending.clear()
  afterLoadReady = true
  afterLoadQueue.splice(0)
}
