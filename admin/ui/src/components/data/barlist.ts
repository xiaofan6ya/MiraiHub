import type { BadgeTone } from '../base/badge'

/** 条形排行的一项。 */
export interface BarListItem {
  label: string
  value: number
  tone?: BadgeTone
}
