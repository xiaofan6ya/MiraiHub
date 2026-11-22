/** 时间区间的纯计算（DateRange 快捷项，便于单测）。 */
export const DAY_MS = 24 * 60 * 60 * 1000

export function startOfDay(ts: number): number {
  const date = new Date(ts)
  date.setHours(0, 0, 0, 0)
  return date.getTime()
}

export function endOfDay(ts: number): number {
  const date = new Date(ts)
  date.setHours(23, 59, 59, 999)
  return date.getTime()
}

/** 近 N 天（含今天）：从 N-1 天前 00:00 到 now。 */
export function lastDays(days: number, now = Date.now()): [number, number] {
  const span = Math.max(1, Math.floor(days))
  return [startOfDay(now - (span - 1) * DAY_MS), now]
}

/** 昨日：00:00:00.000 – 23:59:59.999。 */
export function yesterday(now = Date.now()): [number, number] {
  const base = now - DAY_MS
  return [startOfDay(base), endOfDay(base)]
}

/** DateRange 快捷项：今日 / 昨日 / 近 7 天 / 近 30 天。 */
export function dateRangeShortcuts(now = Date.now()): Record<string, () => [number, number]> {
  return {
    '今日': () => [startOfDay(now), now],
    '昨日': () => yesterday(now),
    '近 7 天': () => lastDays(7, now),
    '近 30 天': () => lastDays(30, now),
  }
}
