/** 组件内部通用文本工具。与 05-patterns §4 的 format.ts 同规则，但不依赖其落地。 */

/** 空值统一显示 —：（null / undefined / 空字符串）。 */
export function isBlank(value: unknown): boolean {
  return value === null || value === undefined || value === ''
}

/** 掩码：保留前 6 后 4（Key、令牌）。太短时原样返回。 */
export function maskValue(value: string): string {
  const text = String(value ?? '')
  if (text.length <= 12) return text
  return text.slice(0, 6) + '…' + text.slice(-4)
}

/** 数字加千分位，非数字原样。 */
export function formatInt(value: number | string | null | undefined): string {
  if (isBlank(value)) return '—'
  const n = typeof value === 'number' ? value : Number(String(value).replace(/,/g, ''))
  if (!Number.isFinite(n)) return String(value)
  return n.toLocaleString('zh-CN')
}

/** 0–1 或 0–100 的比值归一成 0–1。 */
export function toRatio(value: number | null | undefined, max = 100): number {
  if (value === null || value === undefined || !Number.isFinite(value)) return 0
  const ratio = max === 1 ? value : value / max
  return Math.min(1, Math.max(0, ratio))
}
