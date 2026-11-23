import type { BadgeTone } from '../base/badge'

/** 堆叠进度的一段。 */
export interface StackMeterItem {
  label: string
  value: number
  tone?: BadgeTone
}
