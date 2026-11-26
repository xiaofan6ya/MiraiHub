/**
 * 轮询（05-patterns §3：页面不可见时暂停；连续失败 3 次后停止并提示可恢复）。
 *
 * 用法：
 *   const { start, stop, paused, pausedMessage, resume } = usePolling(fetchList, 15000)
 *   start()
 *
 * 组件卸载（effect scope 销毁）时自动停止，不需要页页面自己记 timer。
 *
 * 第 3 轮（11-polish-motion §2「轮询」）：
 *   · 页面不可见时暂停，回到可见**立即刷新一次**再按间隔继续；
 *   · 页面被外壳 KeepAlive 缓存（离开）时同样暂停，回到页面立即刷新一次（首次挂载不重复触发）；
 *   · `key`：同一接口多处轮询时合并请求 —— 同 key 的任务在途或刚结束 300ms 内只跑一次（dedupeRequest）。
 */
import {
  computed,
  getCurrentInstance,
  onActivated,
  onDeactivated,
  onScopeDispose,
  ref,
  type ComputedRef,
  type Ref,
} from 'vue'
import { errorMessage } from '@/utils/error'
import { t } from '@/i18n'
import { DEDUPE_WINDOW_MS, dedupeRequest } from './dedupe'
import { hasRevisit } from './useRevisit'

export interface PollingOptions {
  /** 立即执行一次（默认 true）。 */
  immediate?: boolean
  /** 页面不可见时暂停（默认 true）。 */
  pauseWhenHidden?: boolean
  /** 连续失败几次后停止（默认 3）。 */
  maxFailures?: number
  /** 页面被 KeepAlive 缓存时暂停、回来立即刷新（默认 true；只在组件 setup 里调用时生效）。 */
  pauseWhenInactive?: boolean
  /** 请求合并键：同 key 的任务在途或结束 dedupeMs 内复用同一次结果。 */
  key?: string
  /** 合并窗口（默认 300ms）。 */
  dedupeMs?: number
  /**
   * 第 4 轮：同一组件也注册了 useRevisit 时，回到页面那一刻不立即轮询（交给 useRevisit 刷新），
   * 只重新排下一拍。默认 true；轮询任务和 useRevisit 刷的不是同一份数据时传 false。
   */
  yieldToRevisit?: boolean
}

export interface PollingHandle {
  running: Ref<boolean>
  paused: Ref<boolean>
  reason: Ref<string>
  failures: Ref<number>
  lastRunAt: Ref<number | null>
  pausedMessage: ComputedRef<string>
  start: () => void
  stop: () => void
  resume: () => void
  trigger: () => Promise<void>
}

export function usePolling(
  task: () => unknown | Promise<unknown>,
  intervalMs: number,
  options: PollingOptions = {},
): PollingHandle {
  const maxFailures = Math.max(1, options.maxFailures ?? 3)
  const pauseWhenHidden = options.pauseWhenHidden !== false
  const pauseWhenInactive = options.pauseWhenInactive !== false
  const run = (): Promise<unknown> => {
    const key = options.key
    if (!key) return Promise.resolve(task())
    return dedupeRequest(key, () => Promise.resolve(task()), options.dedupeMs ?? DEDUPE_WINDOW_MS)
  }

  const running = ref(false)
  const paused = ref(false)
  const reason = ref('')
  const failures = ref(0)
  const lastRunAt = ref<number | null>(null)

  let timer: ReturnType<typeof setTimeout> | null = null
  let inFlight = false
  let visibilityBound = false
  /** KeepAlive 缓存中（页面已离开）。 */
  let inactive = false

  const pausedMessage = computed(() => (paused.value ? t('polling.paused', { reason: reason.value }) : ''))

  function clearTimer(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function hidden(): boolean {
    return typeof document !== 'undefined' && document.hidden
  }

  function schedule(): void {
    clearTimer()
    if (!running.value || paused.value || inactive) return
    timer = setTimeout(() => {
      void trigger()
    }, Math.max(200, intervalMs))
  }

  async function trigger(): Promise<void> {
    if (!running.value || paused.value || inactive) return
    if (pauseWhenHidden && hidden()) {
      schedule()
      return
    }
    if (inFlight) {
      schedule()
      return
    }
    inFlight = true
    try {
      await run()
      failures.value = 0
      lastRunAt.value = Date.now()
    } catch (error) {
      failures.value += 1
      if (failures.value >= maxFailures) {
        paused.value = true
        reason.value = errorMessage(error)
        clearTimer()
        return
      }
    } finally {
      inFlight = false
    }
    schedule()
  }

  function onVisibilityChange(): void {
    if (!running.value) return
    if (hidden()) {
      clearTimer()
      return
    }
    void trigger()
  }

  function bindVisibility(): void {
    if (visibilityBound || typeof document === 'undefined') return
    document.addEventListener('visibilitychange', onVisibilityChange)
    visibilityBound = true
  }

  function unbindVisibility(): void {
    if (!visibilityBound || typeof document === 'undefined') return
    document.removeEventListener('visibilitychange', onVisibilityChange)
    visibilityBound = false
  }

  function start(): void {
    if (running.value) return
    running.value = true
    bindVisibility()
    if (hidden() && pauseWhenHidden) return
    if (options.immediate === false) schedule()
    else void trigger()
  }

  function stop(): void {
    running.value = false
    clearTimer()
    unbindVisibility()
  }

  /** 恢复被自动暂停的轮询（对应页面上的"恢复"按钮）。 */
  function resume(): void {
    failures.value = 0
    reason.value = ''
    paused.value = false
    running.value = false
    start()
  }

  const instance = getCurrentInstance()
  if (pauseWhenInactive && instance) {
    onActivated(() => {
      /*
       * 第 4 轮修（P1）：原来「第一次 activated 一律跳过」。KeepAlive 根挂好之后才挂上的子组件
       * 第一次收到的 activated 就是真正的「回来」，却被跳过且 inactive 没复位 → 轮询永久停住。
       * 现在只看有没有离开过：没离开过（首次挂载那一次）什么都不做。
       */
      if (!inactive) return
      inactive = false
      if (!running.value || paused.value) return
      // 同一页面也注册了 useRevisit：回来的那次刷新交给它，这里只排下一拍，避免同一接口双发。
      if (options.yieldToRevisit !== false && hasRevisit(instance)) schedule()
      else void trigger()
    })
    onDeactivated(() => {
      inactive = true
      clearTimer()
    })
  }

  onScopeDispose(stop)

  return { running, paused, reason, failures, lastRunAt, pausedMessage, start, stop, resume, trigger }
}
