/**
 * 新行高亮（11-polish-motion §1「新行插入（实时日志）用背景 --brand-soft 闪一下 900ms 淡出」）。
 *
 *   const flash = useFlashKeys()
 *   watch(rows, (list) => flash.track(list, (row) => row.id))   // 第一次只记住，之后新出现的 key 闪一下
 *   <DataTable :row-class="(row) => flash.rowClass(row.id)" … />
 *   // 不用 DataTable 的列表：<li :class="flash.rowClass(item.id)">
 *
 * 样式类 `.mh-row-flash` 在 styles/base.css（减少动效时不闪）。
 */
import { onScopeDispose, shallowRef } from 'vue'

export const ROW_FLASH_CLASS = 'mh-row-flash'
export const ROW_FLASH_MS = 900

export interface FlashKeys {
  /** 让这些 key 闪一下。 */
  flash: (keys: Iterable<string | number>) => void
  /** 对比上一次的 key：首次调用只记录，之后新出现的 key 闪一下。 */
  track: <T>(rows: readonly T[], keyOf: (row: T) => string | number) => void
  isFlashing: (key: string | number) => boolean
  /** 正在闪的 key 返回 'mh-row-flash'，否则空串。 */
  rowClass: (key: string | number) => string
  reset: () => void
}

export function useFlashKeys(durationMs = ROW_FLASH_MS): FlashKeys {
  const active = shallowRef<ReadonlySet<string>>(new Set())
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  let seen: Set<string> | null = null

  function drop(key: string): void {
    timers.delete(key)
    if (!active.value.has(key)) return
    const next = new Set(active.value)
    next.delete(key)
    active.value = next
  }

  function flash(keys: Iterable<string | number>): void {
    const next = new Set(active.value)
    let changed = false
    for (const raw of keys) {
      const key = String(raw)
      const old = timers.get(key)
      if (old) clearTimeout(old)
      timers.set(key, setTimeout(() => drop(key), durationMs))
      if (!next.has(key)) {
        next.add(key)
        changed = true
      }
    }
    if (changed) active.value = next
  }

  function track<T>(rows: readonly T[], keyOf: (row: T) => string | number): void {
    const current = new Set(rows.map((row) => String(keyOf(row))))
    if (seen === null) {
      seen = current
      return
    }
    const fresh: string[] = []
    current.forEach((key) => {
      if (!seen?.has(key)) fresh.push(key)
    })
    seen = current
    if (fresh.length) flash(fresh)
  }

  const isFlashing = (key: string | number): boolean => active.value.has(String(key))
  const rowClass = (key: string | number): string => (isFlashing(key) ? ROW_FLASH_CLASS : '')

  function reset(): void {
    timers.forEach((timer) => clearTimeout(timer))
    timers.clear()
    active.value = new Set()
    seen = null
  }

  onScopeDispose(reset)

  return { flash, track, isFlashing, rowClass, reset }
}
