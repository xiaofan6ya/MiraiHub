import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { fmtRelative } from '@/utils/format'
import { errorMessage } from '@/utils/error'

afterEach(() => setLocale('zh-CN'))

describe('工具文案跟随语言', () => {
  it('fmtRelative 英文不泄漏中文', () => {
    setLocale('en-US')
    const now = Date.now()
    expect(fmtRelative(now - 1000, now)).toBe('just now')
    expect(fmtRelative(now - 5 * 60000, now)).toBe('5 min ago')
    expect(fmtRelative(now - 2 * 3600000, now)).toBe('2 h ago')
  })
  it('errorMessage 兜底英文', () => {
    setLocale('en-US')
    expect(errorMessage({})).toBe('Unknown error')
  })
})