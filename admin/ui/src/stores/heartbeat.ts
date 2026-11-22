/**
 * 顶栏服务状态（旧版 index.html:1238 的 topbarMode）。
 *
 * 旧版事实：那块文字是**静态**的 —— 初始写死"服务已连接"，桌面版启动成功后
 * 改成"本地运行"，从来没有周期检测（grep topbarMode 只有 desktop.js 一处写入）。
 * 所以这里没有"旧版现成的接口"可用，只能沿用旧版启动探针
 * GET /api/accounts/probe（O(1)、只验凭据、不碰账号表，见 admin/routers/accounts.py:558）
 * 当作心跳：它同时回答"服务在不在"和"登录态有没有失效"。
 *
 * 状态映射（03-shell §2.2）：
 *   0 次连续失败 → ok（运行中）；1–2 次 → reconnecting（重连中…）；≥3 次 → down（连接中断）。
 *
 * 页面不可见（document.hidden）时暂停，可见时立刻补一拍并恢复定时器。
 * 侧栏提示点（冷却账号数 / 批次封控）旧版**没有**现成数据源，
 * 本 store 不编造，等对应页面接上自己的接口后再由外壳聚合。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { http } from '@/api/http'

export type ServiceStatus = 'ok' | 'down' | 'reconnecting'

/** 15 秒一拍（03-shell §4）。 */
export const HEARTBEAT_INTERVAL_MS = 15000
/** 单拍超时：超过这个时间没回就当这一拍失败，不要让状态永远悬着。 */
export const HEARTBEAT_TIMEOUT_MS = 8000
/** 连续失败几下算"连接中断"。 */
export const DOWN_AFTER_FAILURES = 3
/** 心跳端点（旧版启动探针）。 */
export const HEARTBEAT_PATH = '/api/accounts/probe'

export function statusFromFailures(failures: number): ServiceStatus {
  if (failures <= 0) return 'ok'
  return failures >= DOWN_AFTER_FAILURES ? 'down' : 'reconnecting'
}

export const useHeartbeatStore = defineStore('heartbeat', () => {
  /** 初始值与旧版首屏文案一致（服务已连接）；start() 后第一拍会立刻校正。 */
  const status = ref<ServiceStatus>('ok')
  const lastBeatAt = ref<number | null>(null)
  const lastOkAt = ref<number | null>(null)
  const consecutiveFailures = ref(0)
  const running = ref(false)

  const baseUrl = computed<string>(() =>
    typeof location === 'undefined' ? '' : location.origin,
  )
  const endpoint = computed<string>(() => baseUrl.value + HEARTBEAT_PATH)
  const isOnline = computed<boolean>(() => status.value === 'ok')

  let timer: ReturnType<typeof setInterval> | null = null
  let inflight = false
  let visibilityBound = false

  function startTimer(): void {
    if (timer) return
    timer = setInterval(() => {
      void beat()
    }, HEARTBEAT_INTERVAL_MS)
  }

  function stopTimer(): void {
    if (!timer) return
    clearInterval(timer)
    timer = null
  }

  function onVisibilityChange(): void {
    if (typeof document === 'undefined') return
    if (document.hidden) {
      stopTimer()
      return
    }
    if (!running.value) return
    // 回到前台先补一拍，再恢复节奏：用户切回来时状态不该还是旧值。
    void beat()
    startTimer()
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

  /** 打一拍。返回这一拍是否成功；失败不会抛错（后台探活不该污染页面）。 */
  async function beat(): Promise<boolean> {
    if (inflight) return status.value === 'ok'
    inflight = true
    try {
      await http('GET', HEARTBEAT_PATH, { timeoutMs: HEARTBEAT_TIMEOUT_MS })
      consecutiveFailures.value = 0
      lastOkAt.value = Date.now()
      status.value = 'ok'
      return true
    } catch {
      consecutiveFailures.value += 1
      status.value = statusFromFailures(consecutiveFailures.value)
      return false
    } finally {
      inflight = false
      lastBeatAt.value = Date.now()
    }
  }

  /** 启动心跳（外壳挂载后调用；登录态检查通过再启动）。重复调用无副作用。 */
  function start(): void {
    if (running.value) return
    running.value = true
    bindVisibility()
    if (typeof document !== 'undefined' && document.hidden) return
    void beat()
    startTimer()
  }

  /** 停止心跳（外壳卸载时调用）。 */
  function stop(): void {
    running.value = false
    stopTimer()
    unbindVisibility()
  }

  /** 状态文字（顶栏/侧栏共用，避免各写一份）。 */
  const statusText = computed<string>(() => {
    if (status.value === 'ok') return '运行中'
    if (status.value === 'reconnecting') return '重连中…'
    return '连接中断'
  })

  const statusTone = computed<'ok' | 'warn' | 'danger'>(() => {
    if (status.value === 'ok') return 'ok'
    if (status.value === 'reconnecting') return 'warn'
    return 'danger'
  })

  return {
    status,
    statusText,
    statusTone,
    lastBeatAt,
    lastOkAt,
    consecutiveFailures,
    running,
    isOnline,
    baseUrl,
    endpoint,
    beat,
    start,
    stop,
  }
})
