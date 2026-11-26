/**
 * 后台批量任务进度（04-components §3 JobProgress 的数据源）。
 *
 * 与旧版 console-app.js:9711 的 runJob 一一对应：
 *   · 提交后轮询，间隔 1500ms（旧版写死 1500）；
 *   · 默认轮询地址 /api/growth/job/{job_id}，其它任务用 options.pollUrl 覆盖；
 *   · status !== 'running' 视为结束；'failed' 才算失败（后端快照见 admin/jobrunner.py:134）。
 *
 * 页面拿到 jobId 后：
 *   const job = useJob(jobId)
 *   job.start()
 */
import { computed, onScopeDispose, ref, watch, type MaybeRef } from 'vue'
import { http } from '@/api/http'

export type JobStatus = 'running' | 'finished' | 'failed' | string

/** admin/jobrunner.py: Job.snapshot() 的字段，原样保留。 */
export interface JobSnapshot {
  job_id: string
  key?: string
  title?: string
  status: JobStatus
  phase?: string
  total?: number
  done?: number
  elapsed_s?: number
  /** 0–100 的整数（后端已算好，不需要前端再乘 100）。 */
  percent?: number
  current?: string
  task_done?: number
  task_current?: string
  items?: Array<Record<string, unknown>>
  items_total?: number
  items_truncated?: boolean
  result?: unknown
  error?: string | null
}

export interface UseJobOptions {
  /** 轮询地址前缀，默认 /api/growth/job/。 */
  pollUrl?: string
  /** 轮询间隔（毫秒），默认 1500（与旧版一致）。 */
  intervalMs?: number
  /** 拿到 jobId 就立刻开始轮询（默认 true）。 */
  autoStart?: boolean
  onFinish?: (snapshot: JobSnapshot) => void
  onError?: (error: unknown) => void
}

export const DEFAULT_JOB_POLL_URL = '/api/growth/job/'
export const DEFAULT_JOB_POLL_INTERVAL = 1500

export function useJob(jobId?: MaybeRef<string | null | undefined>, options: UseJobOptions = {}) {
  const pollUrl = options.pollUrl || DEFAULT_JOB_POLL_URL
  const intervalMs = Math.max(300, options.intervalMs ?? DEFAULT_JOB_POLL_INTERVAL)

  const snapshot = ref<JobSnapshot | null>(null)
  const error = ref<unknown>(null)
  const polling = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let inFlight = false
  let activeId: string | null = null

  const status = computed<JobStatus>(() => snapshot.value?.status || '')
  const isRunning = computed(() => snapshot.value?.status === 'running')
  const finished = computed(() => Boolean(snapshot.value) && snapshot.value?.status !== 'running')
  const failed = computed(() => snapshot.value?.status === 'failed')
  /** 0–100：优先后端算好的 percent，没有就按 done/total 推。 */
  const percent = computed(() => {
    const data = snapshot.value
    if (!data) return 0
    if (typeof data.percent === 'number' && Number.isFinite(data.percent)) return data.percent
    const total = Number(data.total || 0)
    const done = Number(data.done || 0)
    return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  })

  function clearTimer(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function schedule(): void {
    clearTimer()
    if (!polling.value) return
    timer = setTimeout(() => {
      void tick()
    }, intervalMs)
  }

  async function tick(): Promise<void> {
    if (!polling.value || !activeId || inFlight) return
    inFlight = true
    try {
      const data = await http<JobSnapshot>('GET', pollUrl + encodeURIComponent(activeId))
      if (data) {
        snapshot.value = data
        error.value = null
        if (data.status && data.status !== 'running') {
          polling.value = false
          clearTimer()
          options.onFinish?.(data)
          return
        }
      }
    } catch (err) {
      // 与旧版一致：进度连接中断只提示，不停轮询（任务的提交是幂等的，重复轮询无害）
      error.value = err
      options.onError?.(err)
    } finally {
      inFlight = false
    }
    schedule()
  }

  function start(id?: string | null): void {
    const next = id ?? (jobId !== undefined ? String(resolveJobId()) : '')
    if (!next) return
    if (activeId === next && polling.value) return
    activeId = next
    polling.value = true
    snapshot.value = null
    error.value = null
    void tick()
  }

  function resolveJobId(): string | null {
    if (jobId === undefined) return null
    const value = typeof jobId === 'function' ? (jobId as () => string)() : (jobId as { value?: string }).value ?? jobId
    return value ? String(value) : null
  }

  function stop(): void {
    polling.value = false
    clearTimer()
  }

  if (jobId !== undefined) {
    watch(
      () => resolveJobId(),
      (value) => {
        if (value && options.autoStart !== false) start(value)
        else stop()
      },
      { immediate: true },
    )
  }

  onScopeDispose(stop)

  return {
    snapshot,
    status,
    percent,
    isRunning,
    finished,
    failed,
    polling,
    error,
    start,
    stop,
  }
}
