/**
 * 注意：这里**不要**静态 import 'echarts/core'。
 *
 * 这个模块被 Chart.vue 静态依赖（buildChartTheme 是纯函数），一旦在顶层 import
 * echarts 的任何一个入口，echarts（≈190KB gzip）就会跟着进每个页面的模块图，
 * Chart.vue 里的动态 import 就白做了。需要 registerTheme 的地方自己动态取。
 */
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

export async function registerChartTheme(): Promise<void> {
  const { registerTheme } = await import('echarts/core')
  registerTheme(CHART_THEME_NAME, buildChartTheme())
}
