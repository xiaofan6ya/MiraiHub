import { describe, expect, it } from 'vitest'
import {
  FMT_EMPTY,
  expiryTone,
  fmtCompact,
  fmtCredit,
  fmtDuration,
  fmtExpiry,
  fmtFullTime,
  fmtInt,
  fmtPct,
  fmtRelative,
  fmtTime,
  maskSecret,
  toDate,
} from '@/utils/format'

describe('空值统一 —', () => {
  it('所有格式化函数对 null/undefined/空串都给 —', () => {
    for (const fn of [fmtInt, fmtCompact, fmtCredit, fmtPct, fmtDuration, fmtTime, fmtRelative, fmtExpiry, maskSecret]) {
      expect(fn(null)).toBe(FMT_EMPTY)
      expect(fn(undefined)).toBe(FMT_EMPTY)
      expect(fn('')).toBe(FMT_EMPTY)
    }
    expect(FMT_EMPTY).toBe('—')
  })

  it('NaN 也当空值，不显示 0', () => {
    expect(fmtCompact(Number.NaN)).toBe(FMT_EMPTY)
    expect(fmtInt(Number.NaN)).toBe(FMT_EMPTY)
  })
})

describe('fmtInt / fmtCredit', () => {
  it('fmtInt 千分位', () => {
    expect(fmtInt(17583)).toBe('17,583')
    expect(fmtInt('17583')).toBe('17,583')
    expect(fmtInt(0)).toBe('0')
    expect(fmtInt(118030)).toBe('118,030')
  })

  it('fmtCredit 整数原样，小数最多两位', () => {
    expect(fmtCredit(9.55)).toBe('9.55')
    expect(fmtCredit(789.31)).toBe('789.31')
    expect(fmtCredit(12)).toBe('12')
    expect(fmtCredit(118030)).toBe('118,030')
    expect(fmtCredit(1.23456)).toBe('1.23')
  })
})

describe('fmtCompact（05-patterns §4 的量级缩写）', () => {
  it('1 万以下原样加千分位', () => {
    expect(fmtCompact(9999)).toBe('9,999')
    expect(fmtCompact(512)).toBe('512')
    expect(fmtCompact(0)).toBe('0')
  })

  it('1 万起用 K / M / B，保留 1 位并去掉 .0', () => {
    expect(fmtCompact(150900)).toBe('150.9K')
    expect(fmtCompact(2300000)).toBe('2.3M')
    expect(fmtCompact(12000)).toBe('12K')
    expect(fmtCompact(1500000000)).toBe('1.5B')
    expect(fmtCompact(-150900)).toBe('-150.9K')
  })
})

describe('fmtPct', () => {
  it('比率 → 百分比，默认 1 位小数', () => {
    expect(fmtPct(0.95)).toBe('95.0%')
    expect(fmtPct(1)).toBe('100.0%')
    expect(fmtPct(0)).toBe('0.0%')
    expect(fmtPct(0.9555, 2)).toBe('95.55%')
  })
})

describe('fmtDuration（毫秒进）', () => {
  it('三种量级', () => {
    expect(fmtDuration(850)).toBe('850ms')
    expect(fmtDuration(3950)).toBe('3.95s')
    expect(fmtDuration(90000)).toBe('1m 30s')
    expect(fmtDuration(60000)).toBe('1m 00s')
    expect(fmtDuration(0)).toBe('0ms')
  })

  it('负数当无效值', () => {
    expect(fmtDuration(-1)).toBe(FMT_EMPTY)
  })
})

describe('时间解析与展示（后端存 UTC，统一 +8 展示）', () => {
  it('不带时区的后端时间按 UTC 解释', () => {
    expect(fmtFullTime('2025-10-30 22:27:36')).toBe('2025-10-31 06:27:36')
    expect(fmtFullTime('2025-10-30T22:27:36')).toBe('2025-10-31 06:27:36')
    expect(fmtFullTime('2025-10-30T22:27:36Z')).toBe('2025-10-31 06:27:36')
    expect(fmtFullTime('2025-10-31T06:27:36+08:00')).toBe('2025-10-31 06:27:36')
  })

  it('秒 / 毫秒时间戳都认', () => {
    expect(fmtFullTime(1761858456)).toBe(fmtFullTime(1761858456000))
    expect(toDate(1761858456)?.getTime()).toBe(1761858456000)
  })

  it('fmtTime：今天只给时间，今年给月日，更早给年月日', () => {
    const now = Date.now()
    expect(fmtTime(new Date(now - 60000))).toMatch(/^\d{2}:\d{2}:\d{2}$/)
    expect(fmtTime(new Date(now - 40 * 86400000))).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(fmtTime(new Date(now - 400 * 86400000))).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
  })

  it('解析不出但非空时原样返回，不吞成 —', () => {
    expect(fmtTime('刚刚')).toBe('刚刚')
  })
})

describe('fmtRelative', () => {
  const now = Date.parse('2026-03-10T04:00:00Z')

  it('刚刚 / N 分钟前 / N 小时前', () => {
    expect(fmtRelative(now - 30000, now)).toBe('刚刚')
    expect(fmtRelative(now - 3 * 60000, now)).toBe('3 分钟前')
    expect(fmtRelative(now - 2 * 3600000, now)).toBe('2 小时前')
  })

  it('昨天 / 日期', () => {
    expect(fmtRelative(Date.parse('2026-03-09T04:00:00Z'), now)).toBe('昨天')
    expect(fmtRelative(now - 40 * 86400000, now)).toMatch(/^\d{2}-\d{2}$/)
    expect(fmtRelative(Date.parse('2024-01-01T00:00:00Z'), now)).toBe('2024-01-01')
  })
})

describe('fmtExpiry / expiryTone', () => {
  const now = Date.parse('2026-03-10T04:00:00Z')

  it('未到期按天，超过 7 天 neutral，7 天内 warn', () => {
    const in15 = now + 15 * 86400000
    expect(fmtExpiry(in15, now)).toBe('15 天后到期')
    expect(expiryTone(in15, now)).toBe('neutral')

    const in3 = now + 3 * 86400000
    expect(fmtExpiry(in3, now)).toBe('3 天后到期')
    expect(expiryTone(in3, now)).toBe('warn')

    expect(expiryTone(now + 7 * 86400000, now)).toBe('warn')
    expect(expiryTone(now + 8 * 86400000, now)).toBe('neutral')
  })

  it('已过期 danger，并给出天数', () => {
    const past = now - 2 * 86400000
    expect(fmtExpiry(past, now)).toBe('已过期 2 天')
    expect(expiryTone(past, now)).toBe('danger')
    expect(fmtExpiry(now - 3600000, now)).toBe('已过期')
  })
})

describe('maskSecret', () => {
  it('保留前 6 后 4', () => {
    expect(maskSecret('sk-mh3abcdef9f2e')).toBe('sk-mh3…9f2e')
  })

  it('短串中段全遮，极短只留省略号', () => {
    expect(maskSecret('abcdefgh')).toBe('a…h')
    expect(maskSecret('abc')).toBe('…')
  })
})
