import { describe, expect, it } from 'vitest'
import { buildThemeOverrides } from '@/theme/naiveTheme'
import { CHART_THEME_NAME, buildChartTheme } from '@/theme/echartsTheme'

describe('主题配置', () => {
  it('Naive 主题覆盖包含品牌色与圆角，不返回空对象', () => {
    const overrides = buildThemeOverrides()
    // jsdom 没有真实 CSS 变量，值可能是空串；这里校验结构与关键键存在。
    expect(overrides.common).toHaveProperty('primaryColor')
    expect(overrides.common).toHaveProperty('borderRadius')
    expect(overrides.Button).toBeTruthy()
  })

  it('ECharts 主题名与配色完整', () => {
    const theme = buildChartTheme()
    expect(CHART_THEME_NAME).toBe('miraihub')
    expect(Array.isArray(theme.color)).toBe(true)
    expect((theme.color as string[]).length).toBeGreaterThan(2)
    expect(theme.backgroundColor).toBeTruthy()
  })
})