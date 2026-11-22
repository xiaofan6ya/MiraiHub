import { describe, expect, it } from 'vitest'
import { DAY_MS, dateRangeShortcuts, lastDays, startOfDay, yesterday } from '../overlay/dateRange'

describe('DateRange 快捷项', () => {
  const now = new Date('2026-11-23T10:30:00+08:00').getTime()

  it('今日从 00:00 开始，到今天此刻', () => {
    const [from, to] = dateRangeShortcuts(now)['今日']()
    expect(from).toBe(startOfDay(now))
    expect(to).toBe(now)
  })

  it('近 7 天是含今天的 7 个自然日', () => {
    const [from] = lastDays(7, now)
    expect(from).toBe(startOfDay(now - 6 * DAY_MS))
    expect(Math.round((startOfDay(now) - from) / DAY_MS)).toBe(6)
  })

  it('昨日结束于 23:59:59.999', () => {
    const [, to] = yesterday(now)
    expect(new Date(to).getHours()).toBe(23)
    expect(new Date(to).getMinutes()).toBe(59)
  })

  it('四个快捷项齐全，且都返回合法区间', () => {
    const shortcuts = dateRangeShortcuts(now)
    expect(Object.keys(shortcuts)).toEqual(['今日', '昨日', '近 7 天', '近 30 天'])
    for (const build of Object.values(shortcuts)) {
      const [from, to] = build()
      expect(to).toBeGreaterThan(from)
    }
  })
})
