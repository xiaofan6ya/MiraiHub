import { describe, expect, it } from 'vitest'
import { useTheme } from '@/theme/useTheme'

describe('useTheme', () => {
  it('设置 dark 后写入 html[data-theme]', () => {
    const { setMode } = useTheme()
    setMode('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('设置 light 后写入 html[data-theme]', () => {
    const { setMode } = useTheme()
    setMode('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('cycle 依次为 浅色 → 深色 → 跟随系统', () => {
    const { mode, setMode, cycle } = useTheme()
    setMode('light')
    cycle()
    expect(mode.value).toBe('dark')
    cycle()
    expect(mode.value).toBe('system')
    cycle()
    expect(mode.value).toBe('light')
  })
})
