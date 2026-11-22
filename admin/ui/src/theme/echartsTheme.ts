import { registerTheme } from 'echarts/core'

export const CHART_THEME_NAME = 'miraihub'

const cssVar = (name: string): string =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()

/** 图表主题与 tokens.css 同源；主题切换后重新注册并重建图表实例。 */
export function buildChartTheme(): Record<string, unknown> {
  const chartColors = Array.from({ length: 8 }, (_, index) => cssVar('--chart-' + (index + 1)))
  const fg = cssVar('--fg')
  const fgMuted = cssVar('--fg-muted')
  const border = cssVar('--border')
  const borderSoft = cssVar('--border-soft')
  const surface = cssVar('--surface')
  const fontSans = cssVar('--font-sans')

  return {
    color: chartColors,
    backgroundColor: 'transparent',
    textStyle: { fontFamily: fontSans, fontSize: 11, color: fgMuted },
    title: { textStyle: { color: fg, fontSize: 15, fontWeight: 600 } },
    legend: { textStyle: { color: fgMuted, fontSize: 11 } },
    tooltip: {
      backgroundColor: surface,
      borderColor: border,
      borderWidth: 1,
      textStyle: { color: fg, fontSize: 12, fontFamily: fontSans },
      extraCssText: 'box-shadow: var(--shadow-pop); border-radius: 7px;',
    },
    categoryAxis: {
      axisLine: { lineStyle: { color: border } },
      axisTick: { show: false },
      axisLabel: { color: fgMuted, fontSize: 11 },
      splitLine: { show: false },
    },
    valueAxis: {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: fgMuted, fontSize: 11 },
      splitLine: { lineStyle: { color: borderSoft, type: 'dashed' } },
    },
  }
}

export function registerChartTheme(): void {
  registerTheme(CHART_THEME_NAME, buildChartTheme())
}
