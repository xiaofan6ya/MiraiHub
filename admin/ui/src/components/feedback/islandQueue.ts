import { computed, inject, ref, type InjectionKey } from 'vue'
import { useMessage } from 'naive-ui'

export type IslandTone = 'success' | 'error' | 'warning' | 'info' | 'loading'
export interface IslandOptions { duration?: number; closable?: boolean; keepAliveOnHover?: boolean }
export interface IslandItem {
  id: number; tone: IslandTone; text: string; count: number; duration: number; closable: boolean
  keepAliveOnHover: boolean
}
const durations: Record<IslandTone, number> = { success: 2800, info: 3200, warning: 5000, error: 7000, loading: 0 }

/** One visible operation at a time. A queue, rather than overlapping live regions. */
export function createIslandQueue() {
  const items = ref<IslandItem[]>([])
  const history = ref<IslandItem[]>([])
  const docked = ref(false)
  const current = computed(() => items.value[0] ?? null)
  const lifetime = ref({ remaining: 0, total: 0, running: false, version: 0 })
  let serial = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let started = 0
  let remaining = 0
  const pauses = new Set<string>()
  function publish() {
    lifetime.value = { remaining, total: current.value?.duration ?? 0, running: timer !== undefined, version: lifetime.value.version + 1 }
  }
  function clearTimer() { if (timer !== undefined) clearTimeout(timer); timer = undefined }
  function arm() {
    clearTimer()
    if (!current.value || pauses.size || remaining <= 0) { publish(); return }
    started = Date.now()
    const id = current.value.id
    timer = setTimeout(() => dismiss(id), remaining)
    publish()
  }
  function reset() {
    for (const reason of [...pauses]) if (reason !== 'hidden') pauses.delete(reason)
    remaining = current.value?.duration ?? 0
    arm()
  }
  function dismiss(id: number) {
    const index = items.value.findIndex(item => item.id === id)
    if (index < 0) return
    items.value.splice(index, 1)
    if (index === 0) reset()
  }
  function pause(reason: string) {
    if (!pauses.size && timer !== undefined) remaining = Math.max(1, remaining - (Date.now() - started))
    pauses.add(reason)
    clearTimer()
    publish()
  }
  function resume(reason: string) {
    // An unrelated resume must never restart an already running deadline.
    if (!pauses.delete(reason)) return
    if (!pauses.size) arm()
  }
  function push(tone: IslandTone, content: string, options: IslandOptions = {}) {
    const text = String(content)
    const existing = items.value.find(item => item.tone === tone && item.text === text)
    if (existing) {
      existing.count++
      const recent = history.value.find(item => item.id === existing.id)
      if (recent) recent.count = existing.count
      if (existing.id === current.value?.id) reset()
      return { destroy: () => dismiss(existing.id) }
    }
    const id = ++serial
    // Bound bursts, retaining errors over disposable success messages.
    if (items.value.length >= 16) {
      const removable = items.value.findIndex((item, index) => index > 0 && item.tone !== 'error')
      items.value.splice(removable > 0 ? removable : 1, 1)
    }
    const item = { id, tone, text, count: 1, duration: Math.max(0, options.duration ?? durations[tone]), closable: options.closable ?? true, keepAliveOnHover: options.keepAliveOnHover ?? false }
    items.value.push(item)
    history.value.unshift({ ...item })
    history.value.splice(20)
    if (items.value.length === 1) reset()
    return { destroy: () => dismiss(id) }
  }
  function destroyAll() { clearTimer(); items.value = []; remaining = 0; pauses.clear(); publish() }
  const api = {
    success: (text: string, options?: IslandOptions) => push('success', text, options),
    error: (text: string, options?: IslandOptions) => push('error', text, options),
    warning: (text: string, options?: IslandOptions) => push('warning', text, options),
    info: (text: string, options?: IslandOptions) => push('info', text, options),
    loading: (text: string, options?: IslandOptions) => push('loading', text, options),
    destroyAll,
  }
  return { items, current, history, docked, lifetime, api, dismiss, pause, resume, dispose: destroyAll }
}
export type IslandQueue = ReturnType<typeof createIslandQueue>
export const islandKey: InjectionKey<IslandQueue> = Symbol('miraihub-island')

/** Existing isolated component mounts can continue to use Naive's provider. */
export function useIslandMessage() {
  return inject(islandKey, null)?.api ?? useMessage()
}
