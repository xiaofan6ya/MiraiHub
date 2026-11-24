import { describe, expect, it } from 'vitest'
import { pickerDotTone, pickerSubtitle, sortPickerPlatforms } from './platformPicker'
import type { PlatformSpec } from '@/api/platforms'

function spec(id: string, extra: Partial<PlatformSpec> = {}): PlatformSpec {
  return { id, label: id, enabled: true, realms: [], ...extra } as PlatformSpec
}

describe('平台选择器圆点（第 3 轮审查 A4）', () => {
  it('0 个账号不是绿点', () => {
    expect(pickerDotTone(spec('trae'), 0)).toBe('muted')
    expect(pickerDotTone(spec('trae'), null)).toBe('muted')
  })

  it('没有 active_count 时按总数：有账号 = 绿', () => {
    expect(pickerDotTone(spec('workbuddy'), 35)).toBe('ok')
  })

  it('有 active_count 时区分「全部禁用」（琥珀）与「有可用」（绿）', () => {
    expect(pickerDotTone(spec('workbuddy', { active_count: 0 }), 35)).toBe('warn')
    expect(pickerDotTone(spec('workbuddy', { active_count: 3 }), 35)).toBe('ok')
    expect(pickerDotTone(spec('qoder', { active_count: 0 }), 0)).toBe('muted')
  })

  it('平台停用一律灰', () => {
    expect(pickerDotTone(spec('loomy', { enabled: false }), 10)).toBe('muted')
  })
})

describe('排序与副标题', () => {
  it('有账号的在前，同组保持后端顺序', () => {
    const counts: Record<string, number | null> = { a: 0, b: 3, c: null, d: 1 }
    const sorted = sortPickerPlatforms([spec('a'), spec('b'), spec('c'), spec('d')], (id) => counts[id] ?? null)
    expect(sorted.map((item) => item.id)).toEqual(['b', 'd', 'a', 'c'])
  })

  it('名称已含区域时不重复副标题', () => {
    expect(pickerSubtitle('Trae 国内版', '国内版')).toBe('')
    expect(pickerSubtitle('Trae', '国内版 / 国际版')).toBe('国内版 / 国际版')
    expect(pickerSubtitle('WorkBuddy', '')).toBe('')
  })
})
