/** API Keys v2.1：额度条语气与限制分类（纯函数）。 */
import { describe, expect, it } from 'vitest'
import { limitKind, quotaMeterTone } from '@/api/keys'

describe('quotaMeterTone：额度条平常中性色，接近 / 用完才着色', () => {
  it('不限额 → neutral；<90% neutral；≥90% warn；≥100% danger', () => {
    expect(quotaMeterTone({ unlimited: true, usageRatio: 1 })).toBe('neutral')
    expect(quotaMeterTone({ unlimited: false, usageRatio: 0.5 })).toBe('neutral')
    expect(quotaMeterTone({ unlimited: false, usageRatio: 0.89 })).toBe('neutral')
    expect(quotaMeterTone({ unlimited: false, usageRatio: 0.96 })).toBe('warn')
    expect(quotaMeterTone({ unlimited: false, usageRatio: 1 })).toBe('danger')
  })
})

describe('limitKind：0 = 不限，空 = 随全局，其余是具体值', () => {
  it('三种分类', () => {
    expect(limitKind(0)).toBe('unlimited')
    expect(limitKind(null)).toBe('global')
    expect(limitKind(undefined)).toBe('global')
    expect(limitKind(8)).toBe('value')
  })
})
