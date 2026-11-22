/** 图表默认样式（与 04-components §2 图表规约一致，纯函数便于复用与测试）。 */

type AnyOption = Record<string, unknown>
type AnySeries = Record<string, unknown>

function mapSeries(option: AnyOption, mapper: (series: AnySeries) => AnySeries): AnyOption {
  const series = option.series
  if (!Array.isArray(series)) return option
  return { ...option, series: series.map((item) => mapper({ ...(item as AnySeries) })) }
}

/** 统一网格 / tooltip / 坐标轴 / 图例。 */
export function applyChartDefaults(option: AnyOption): AnyOption {
  return {
    grid: { left: 8, right: 8, top: 28, bottom: 4, containLabel: true, ...(option.grid as AnyOption) },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      ...(option.tooltip as AnyOption),
    },
    ...option,
  }
}

/** 折线：线宽 2、默认不画数据点（悬停才显示）、面积 8% 透明度。 */
export function applyLineDefaults(option: AnyOption): AnyOption {
  const base = applyChartDefaults(option)
  return mapSeries(base, (series) => ({
    type: 'line',
    showSymbol: false,
    symbolSize: 6,
    smooth: false,
    emphasis: { focus: 'series' },
    ...series,
    lineStyle: { width: 2, ...(series.lineStyle as AnySeries) },
    areaStyle: series.areaStyle === null ? null : { opacity: 0.08, ...(series.areaStyle as AnySeries) },
  }))
}

/** 柱状：圆角顶 3px、最大宽 24px。 */
export function applyBarDefaults(option: AnyOption): AnyOption {
  const base = applyChartDefaults(option)
  return mapSeries(base, (series) => ({
    type: 'bar',
    barMaxWidth: 24,
    ...series,
    itemStyle: { borderRadius: [3, 3, 0, 0], ...(series.itemStyle as AnySeries) },
  }))
}
