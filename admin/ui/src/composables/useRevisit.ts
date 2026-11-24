/**
 * 回到 KeepAlive 缓存页时静默刷新（11-polish-motion §2「切换已访问页面时不重新出骨架」）。
 *
 * 外壳（AppShell）用 `<KeepAlive :max="6">` 缓存最近访问的页面。页面里：
 *
 *   onMounted(load)                                  // 首次加载照旧（出骨架）
 *   useRevisit(() => load({ silent: true }), {       // 回来时后台刷新：保留旧数据，只显示右上角小转圈
 *     minIntervalMs: 10_000,                         // 离开不到 10 秒就回来的，不刷新
 *     onLeave: () => poll.stop(),                    // 离开（被缓存）时停掉自己的定时器 / SSE
 *   })
 *
 * 行为（与 pages/_shared/useRevisit.ts 的临时版完全一致，签名不变）：
 *   · 首次挂载不触发（首次加载由页面自己的 onMounted 负责，避免请求两次）；
 *   · 之后每次 onActivated 调一次 cb；离开不到 minIntervalMs（默认 0）就回来的，不调；
 *   · onLeave：页面被缓存（deactivated）时调用；
 *   · 页面没有被 KeepAlive 包住时两个钩子都不会触发，完全无副作用；
 *   · 在组件 setup 之外调用时什么都不做（不会打 Vue 的「没有活动实例」警告）。
 *
 * usePolling 已经自带「离开暂停、回来立即刷新」，用了 usePolling 的轮询不需要再写 onLeave。
 *
 * 右上角小转圈：cb 返回 Promise 时自动计入 `silentRefreshing`，外壳（AppShell）在内容区右上角
 * 显示一个 14px 转圈（>300ms 才出现，快接口不闪）。页面不需要自己画。
 * 不走 useRevisit 的后台刷新（例如切平台后的重拉）也可以手动 `trackSilent(load({ silent: true }))`。
 */
import {
  computed,
  getCurrentInstance,
  onActivated,
  onDeactivated,
  ref,
  type ComponentInternalInstance,
  type ComputedRef,
} from 'vue'

const pendingSilent = ref(0)

/**
 * 第 4 轮：登记「哪些组件实例注册了 useRevisit」。
 * 同一页面同时用了 usePolling 和 useRevisit 时，回到页面那一刻两者都会刷新 → 同一接口双发。
 * usePolling 在 activated 时查这里：本实例已有 useRevisit 负责回来刷新，就只重排下一拍，不再立即请求。
 */
const revisitOwners = new WeakSet<ComponentInternalInstance>()

/** usePolling 用：当前组件是否注册了 useRevisit。 */
export function hasRevisit(instance: ComponentInternalInstance | null): boolean {
  return Boolean(instance && revisitOwners.has(instance))
}

/** 是否有页面正在后台静默刷新（外壳右上角小转圈用）。 */
export const silentRefreshing: ComputedRef<boolean> = computed(() => pendingSilent.value > 0)

/** 把一次后台刷新计入「静默刷新中」；返回原值，失败照常抛出。 */
export function trackSilent<T>(work: T | Promise<T>): Promise<T> {
  pendingSilent.value += 1
  return Promise.resolve(work).finally(() => {
    pendingSilent.value = Math.max(0, pendingSilent.value - 1)
  })
}

export interface RevisitOptions {
  /** 离开后至少过这么久才在回来时刷新（毫秒，默认 0）。 */
  minIntervalMs?: number
  /** 页面被 KeepAlive 缓存（离开）时调用。 */
  onLeave?: () => void
}

export function useRevisit(cb: () => unknown, options: RevisitOptions = {}): void {
  const instance = getCurrentInstance()
  if (!instance) return
  revisitOwners.add(instance)
  /**
   * 第 4 轮修：原来用「第一次 activated 一律跳过」判断首次挂载。但 KeepAlive 根组件挂好之后才挂上的子组件
   * （tab 面板、v-if 区块里调用的 useRevisit）**不会**在挂载时收到 activated —— 第一次收到的就是真正的「回来」，
   * 被误跳过。改成：只有先见过 deactivated（确实离开过）才算回来。
   */
  let left = false
  let leftAt = 0
  onActivated(() => {
    if (!left) return
    left = false
    if (options.minIntervalMs && Date.now() - leftAt < options.minIntervalMs) return
    const result: unknown = cb()
    if (result && typeof (result as { then?: unknown }).then === 'function') {
      // 静默刷新失败不打断页面（旧数据保留，页面自己的错误态照常工作）。
      trackSilent(result as Promise<unknown>).catch(() => undefined)
    }
  })
  onDeactivated(() => {
    left = true
    leftAt = Date.now()
    options.onLeave?.()
  })
}
