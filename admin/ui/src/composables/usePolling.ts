/**
 * 轮询（05-patterns §3：页面不可见时暂停；连续失败 3 次后停止并提示可恢复）。
 *
 * 用法：
 *   const { start, stop, paused, pausedMessage, resume } = usePolling(fetchList, 15000)
 *   start()
 *
 * 组件卸载（effect scope 销毁）时自动停止，不需要页页面自己记 timer。
 */
import { computed, onScopeDispose, ref, type ComputedRef, type Ref } from 'vue'
import { errorMessage } from '@/utils/error'

export interface PollingOptions {
  /** 立即执行一次（默认 true）。 */
  immediate?: boolean
  /** 页面不可见时暂停（默认 true）。 */
  pauseWhenHidden?: boolean
  /** 连续失败几次后停止（默认 3）。 */
  maxFailures?: number
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

  const running = ref(false)
  const paused = ref(false)
  const reason = ref('')
  const failures = ref(0)
  const lastRunAt = ref<number | null>(null)

  let timer: ReturnType<typeof setTimeout> | null = null
  let inFlight = false
  let visibilityBound = false

  const pausedMessage = computed(() => (paused.value ? '自动刷新已暂停：' + reason.value : ''))

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
    if (!running.value || paused.value) return
    timer = setTimeout(() => {
      void trigger()
    }, Math.max(200, intervalMs))
  }

  async function trigger(): Promise<void> {
    if (!running.value || paused.value) return
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
      await task()
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

  onScopeDispose(stop)

  return { running, paused, reason, failures, lastRunAt, pausedMessage, start, stop, resume, trigger }
}
