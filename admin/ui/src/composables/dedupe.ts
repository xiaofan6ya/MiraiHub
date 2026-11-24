/**
 * 同 key 请求合并（11-polish-motion §2「同一接口多处使用时合并请求（同 key 去重 300ms）」）。
 *
 *   const data = await dedupeRequest('overview:' + platform, () => getOverview(platform))
 *
 * · 同一个 key 的请求还没回来：直接复用那一个 promise；
 * · 回来后 windowMs（默认 300ms）内再要：复用结果，不发第二次；
 * · 失败不缓存：下一次调用会重新请求。
 * key 要带上会影响结果的所有参数（平台、区间、筛选…）。
 */
interface Entry {
  promise: Promise<unknown>
  settledAt: number | null
}

const entries = new Map<string, Entry>()

export const DEDUPE_WINDOW_MS = 300

export function dedupeRequest<T>(key: string, fn: () => Promise<T> | T, windowMs = DEDUPE_WINDOW_MS): Promise<T> {
  const hit = entries.get(key)
  if (hit && (hit.settledAt === null || Date.now() - hit.settledAt < windowMs)) {
    return hit.promise as Promise<T>
  }
  const entry: Entry = { promise: Promise.resolve(), settledAt: null }
  const promise = Promise.resolve()
    .then(fn)
    .then(
      (value) => {
        entry.settledAt = Date.now()
        globalThis.setTimeout(() => {
          if (entries.get(key) === entry) entries.delete(key)
        }, Math.max(0, windowMs))
        return value
      },
      (error: unknown) => {
        if (entries.get(key) === entry) entries.delete(key)
        throw error
      },
    )
  entry.promise = promise
  entries.set(key, entry)
  return promise
}

/** 清掉某个前缀下的所有 key（http.ts 在写操作成功后清 `http:`，避免 300ms 窗口内读到写之前的结果）。 */
export function clearDedupePrefix(prefix: string): void {
  for (const key of Array.from(entries.keys())) {
    if (key.startsWith(prefix)) entries.delete(key)
  }
}

/** 测试用 / 写操作之后想强制重新拉取时用：清掉某个 key（不传则全部）。 */
export function clearDedupe(key?: string): void {
  if (key === undefined) entries.clear()
  else entries.delete(key)
}
