/** 行操作定义：最多 2 个直接显示，其余收进「⋯」菜单；危险操作排在最后。 */
export interface RowAction {
  key: string
  label: string
  /** 危险操作：菜单里朱色，且与普通项之间有分隔线。 */
  danger?: boolean
  disabled?: boolean
  hidden?: boolean
}
