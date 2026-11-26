import { describe, expect, it } from 'vitest'
import { BADGE_TONES, badgeToneStyle } from '../base/badge'
import { statToneColor, stripColumns, stripColumnsMedium } from '../data/stat'

describe('Badge tone 映射', () => {
  it('文字用状态色、底色用 -soft 版本', () => {
    expect(badgeToneStyle('ok')).toEqual({ text: 'var(--ok)', bg: 'var(--ok-soft)' })
    expect(badgeToneStyle('warn')).toEqual({ text: 'var(--warn)', bg: 'var(--warn-soft)' })
    expect(badgeToneStyle('danger')).toEqual({ text: 'var(--danger)', bg: 'var(--danger-soft)' })
    expect(badgeToneStyle('neutral')).toEqual({
      text: 'var(--fg-2)',
      bg: 'var(--neutral-soft)',
    })
    expect(badgeToneStyle('brand')).toEqual({ text: 'var(--brand)', bg: 'var(--brand-soft)' })
  })

  it('未知 / 空 tone 回落到 neutral，不出现无底色裸文字', () => {
    expect(badgeToneStyle(undefined)).toEqual(BADGE_TONES.neutral)
    expect(badgeToneStyle('ACTIVE')).toEqual(BADGE_TONES.neutral)
  })
})

describe('统计卡与统计条', () => {
  it('数值着色只认已知语气', () => {
    expect(statToneColor('ok')).toBe('var(--ok)')
    expect(statToneColor()).toBe('var(--fg)')
    expect(statToneColor('muted')).toBe('var(--fg-muted)')
  })

  it('统计条列数限制在 2–5', () => {
    expect(stripColumns(1)).toBe(1)
    expect(stripColumns(3)).toBe(3)
    expect(stripColumns(9)).toBe(5)
  })

  it('6 个排 3×2、7–8 个排 4 列，不出现「5+1」（第 1 轮审查 P2 风控哨兵）', () => {
    expect(stripColumns(6)).toBe(3)
    expect(stripColumns(7)).toBe(4)
    expect(stripColumns(8)).toBe(4)
    expect(stripColumnsMedium(4)).toBe(2)
    expect(stripColumnsMedium(6)).toBe(3)
    expect(stripColumnsMedium(5)).toBe(3)
    expect(stripColumnsMedium(2)).toBe(2)
  })
})
