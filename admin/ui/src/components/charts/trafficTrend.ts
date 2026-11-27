/** Coordinates use a zero baseline. Cubic control points never overshoot either sample. */
export function trafficGeometry(values: readonly number[]) {
  const clean = values.map(value => Number.isFinite(value) ? Math.max(0, value) : 0)
  if (!clean.length) return { points: [], line: '', area: '', max: 0 }
  const max = Math.max(1, ...clean)
  const points = clean.map((value, index) => ({ x: clean.length === 1 ? 460 : index / (clean.length - 1) * 920, y: 216 - value / max * 180, value }))
  let line = `M${points[0]!.x},${points[0]!.y}`
  for (let index = 1; index < points.length; index++) {
    const prev = points[index - 1]!, point = points[index]!, mid = (prev.x + point.x) / 2
    line += ` C${mid},${prev.y} ${mid},${point.y} ${point.x},${point.y}`
  }
  return { points, line, area: `${line} L${points[points.length - 1]!.x},216 L${points[0]!.x},216 Z`, max }
}
