import type { DataTableBaseColumn } from 'naive-ui'

/** 卡片里该列的位置。 */
export type MobileSlot = 'title' | 'subtitle' | 'meta' | 'actions' | 'hidden'
export type TableRow = Record<string, unknown>

/** DataTable 列定义：在 NDataTable 列基础上加手机标注与数值对齐。 */
export interface TableColumn<T = TableRow> extends DataTableBaseColumn<T> {
  /** 手机卡片中的位置；不填时统一落到 meta（无任何列标注时按顺序推断）。 */
  mobile?: MobileSlot
  /** 数值列：右对齐 + 等宽数字。 */
  numeric?: boolean
  /** false 时不出现在「列显示设置」里。 */
  hideable?: boolean
  /** true 时交给 NDataTable 排序（远程排序用 @update:sorter）。 */
  sortable?: boolean
}

export interface SorterPayload {
  columnKey: string
  order: 'ascend' | 'descend' | false
}
