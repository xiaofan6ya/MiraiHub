import { describe, expect, it } from 'vitest'
import {
  FMT_EMPTY,
  bucketLocalParts,
  expiryTone,
  fmtCompact,
  fmtCredit,
  fmtDuration,
  fmtBeijingFullTime,
  fmtBeijingTime,
  fmtExpiry,
  fmtFullTime,
  fmtTimeTitle,
  fmtTzOffset,
  timeText,
  toIsoString,
  fmtInt,
  fmtLatency,
  fmtLocalFullTime,
  fmtLocalHM,
  fmtLocalTime,
  fmtMultiplier,
  fmtPct,
  fmtRelative,
  isBlank,
  joinParts,
  orDash,
  relativeTime,
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

  it('入参就是毫秒：不猜量级、不换算（后端 ×1000 的汇总错误由后端修，前端不能 ÷1000 掩盖）', () => {
    // 1 小时以上照样按 ms 计算：14,345,921ms = 239m 06s（就是审查里「放大 1000 倍」的那条原始值）
    expect(fmtDuration(14345921)).toBe('239m 06s')
    expect(fmtDuration(25800)).toBe('25.8s')
    expect(fmtDuration(999)).toBe('999ms')
    expect(fmtDuration(1000)).toBe('1s')
    expect(fmtDuration('1500')).toBe('1.5s')
  })
})

describe('图表时间轴：后端 UTC 桶 → 本地时区（与运行测试的机器时区无关）', () => {
  it('小时桶按 UTC 解析再取本地时分', () => {
    const expected = new Date(Date.UTC(2026, 10, 23, 15, 0))
    const parts = bucketLocalParts('2026-11-23 15:00')
    expect(parts).toMatchObject({
      y: expected.getFullYear(),
      mo: expected.getMonth() + 1,
      d: expected.getDate(),
      h: expected.getHours(),
      mi: 0,
    })
    expect(bucketLocalParts('2026-11-23T15:00:00Z')).toEqual(parts)
    expect(bucketLocalParts('2026-11-23T23:00:00+08:00')).toEqual(parts)
  })

  it('天桶 / 空串 / 文案不是小时桶，返回 null', () => {
    expect(bucketLocalParts('2026-11-23')).toBeNull()
    expect(bucketLocalParts('')).toBeNull()
    expect(bucketLocalParts('刚刚')).toBeNull()
    expect(bucketLocalParts(null)).toBeNull()
  })

  it('fmtLocalHM 用本地时区', () => {
    const date = new Date(Date.UTC(2026, 2, 10, 3, 58))
    expect(fmtLocalHM(date)).toBe(
      String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0'),
    )
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

describe('v2.1 补充：无效值 / 拼接 / 倍率 / 指标耗时 / 本地时区', () => {
  it('isBlank / orDash：null、空串、NaN、— 都是无效值；0 不是', () => {
    for (const value of [null, undefined, '', '  ', Number.NaN, Number.POSITIVE_INFINITY, FMT_EMPTY]) {
      expect(isBlank(value)).toBe(true)
      expect(orDash(value)).toBe(FMT_EMPTY)
    }
    expect(isBlank(0)).toBe(false)
    expect(orDash(0)).toBe('0')
    expect(orDash(1234, (n) => n.toLocaleString('zh-CN'))).toBe('1,234')
  })

  it('joinParts：一律用「 · 」，跳过空段（第 1 轮 P0-7 / 第 2 轮 P0-15）', () => {
    expect(joinParts(['上下文 128K', '输出 16K'])).toBe('上下文 128K · 输出 16K')
    expect(joinParts(['每 1 小时', null, '', FMT_EMPTY, '下次 23:30'])).toBe('每 1 小时 · 下次 23:30')
    expect(joinParts(['不限', '频率随全局'], ' ')).toBe('不限 频率随全局')
    expect(joinParts([null, undefined])).toBe(FMT_EMPTY)
  })

  it('fmtMultiplier：负数「按量计费」，无效值「—」', () => {
    expect(fmtMultiplier(1.5)).toBe('×1.50')
    expect(fmtMultiplier(-1)).toBe('按量计费')
    expect(fmtMultiplier(null)).toBe(FMT_EMPTY)
    expect(fmtMultiplier('abc')).toBe(FMT_EMPTY)
  })

  it('fmtLatency：超过 1 小时显示「数据异常」，其余同 fmtDuration', () => {
    expect(fmtLatency(25800)).toBe('25.8s')
    expect(fmtLatency(-1)).toBe(FMT_EMPTY)
    expect(fmtLatency(14345921)).toBe('数据异常')
  })

  it('fmtRelative：将来的时间说「N 分钟后 / N 小时后」', () => {
    const now = Date.parse('2026-03-10T04:00:00Z')
    expect(fmtRelative(now + 5 * 60000, now)).toBe('5 分钟后')
    expect(fmtRelative(now + 3 * 3600000, now)).toBe('3 小时后')
    expect(fmtRelative(now + 20000, now)).toBe('刚刚')
    expect(fmtRelative(now + 3 * 86400000, now)).toMatch(/^\d{2}-\d{2}$/)
  })

  it('fmtLocalTime / fmtLocalFullTime / relativeTime 用浏览器本地时区', () => {
    const date = new Date(2026, 2, 10, 9, 5, 7)
    expect(fmtLocalFullTime(date)).toBe('2026-03-10 09:05:07')
    expect(fmtLocalTime(date, new Date(2026, 2, 10, 23, 0, 0))).toBe('09:05:07')
    expect(fmtLocalTime(date, new Date(2026, 5, 1))).toBe('03-10 09:05')
    expect(fmtLocalTime(date, new Date(2027, 0, 1))).toBe('2026-03-10 09:05')
    expect(fmtLocalTime(null)).toBe(FMT_EMPTY)
    const rel = relativeTime(date.getTime() - 3 * 60000, date.getTime())
    expect(rel.text).toBe('3 分钟前')
    expect(rel.title).toBe('2026-03-10 09:02:07 ' + fmtTzOffset(date))
    expect(relativeTime(null)).toEqual({ text: FMT_EMPTY, title: '' })
  })
})

describe('第 5 轮：本地时区 + 悬停带偏移 + 显式北京时间', () => {
  it('fmtTime / fmtFullTime 按本地时区（与 fmtLocal* 一致）', () => {
    const date = new Date(2026, 2, 10, 9, 5, 7)
    expect(fmtFullTime(date)).toBe('2026-03-10 09:05:07')
    expect(fmtFullTime(date)).toBe(fmtLocalFullTime(date))
    expect(fmtTime(date, new Date(2026, 2, 10, 23, 0, 0))).toBe('09:05:07')
    expect(fmtTime(date, new Date(2026, 5, 1))).toBe('03-10 09:05')
    expect(fmtTime(date, new Date(2027, 0, 1))).toBe('2026-03-10 09:05')
  })

  it('被当回调传给 map 时下标不会被当成「现在」', () => {
    const date = new Date()
    // map 会把下标当第二个参数传进来；断言它不会被当成「现在」。类型上收窄成单参回调。
    expect([date].map((value) => fmtTime(value))[0]).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })

  it('fmtTzOffset / fmtTimeTitle：完整时间 + 偏移；空值不出 tooltip', () => {
    const date = new Date(2026, 10, 24, 23, 5, 0)
    const offset = fmtTzOffset(date)
    expect(offset).toMatch(/^UTC[+\-±]\d{2}:\d{2}$/)
    const minutes = -date.getTimezoneOffset()
    const sign = minutes > 0 ? '+' : minutes < 0 ? '-' : '±'
    const abs = Math.abs(minutes)
    expect(offset).toBe(
      'UTC' + sign + String(Math.floor(abs / 60)).padStart(2, '0') + ':' + String(abs % 60).padStart(2, '0'),
    )
    expect(fmtTimeTitle(date)).toBe('2026-11-24 23:05:00 ' + offset)
    expect(fmtTimeTitle(null)).toBe('')
    expect(fmtTimeTitle('垃圾')).toBe('')
  })

  it('测试环境固定 Asia/Shanghai（vite.config test.env.TZ）', () => {
    expect(fmtTzOffset(new Date(Date.UTC(2026, 0, 1)))).toBe('UTC+08:00')
  })

  it('timeText：text / title / iso 一次拿齐，relative 走 fmtRelative', () => {
    const now = Date.parse('2026-03-10T04:00:00Z')
    const v = timeText('2026-03-10 03:57:00', { relative: true, now })
    expect(v.text).toBe('3 分钟前')
    expect(v.iso).toBe('2026-03-10T03:57:00.000Z')
    expect(v.title).toBe(fmtTimeTitle('2026-03-10T03:57:00Z'))
    expect(timeText(null)).toEqual({ text: FMT_EMPTY, title: '', iso: '' })
    expect(timeText('刚刚').text).toBe('刚刚')
    expect(toIsoString('2026-03-10 03:57:00')).toBe('2026-03-10T03:57:00.000Z')
    expect(toIsoString('')).toBe('')
  })

  it('fmtBeijingTime / fmtBeijingFullTime 与浏览器时区无关', () => {
    const utc = '2025-10-30T22:27:36Z'
    expect(fmtBeijingFullTime(utc)).toBe('2025-10-31 06:27:36')
    expect(fmtBeijingTime(utc, new Date('2025-10-31T01:00:00Z'))).toBe('06:27:36')
    expect(fmtBeijingTime(utc, new Date('2025-12-01T00:00:00Z'))).toBe('10-31 06:27')
    expect(fmtBeijingTime(utc, new Date('2027-01-01T00:00:00Z'))).toBe('2025-10-31 06:27')
    expect(fmtBeijingTime(null)).toBe(FMT_EMPTY)
    expect(fmtBeijingFullTime(null)).toBe(FMT_EMPTY)
  })
})
