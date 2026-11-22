/** 键值行（DetailDrawer / 设置页摘要共用）。 */
export interface KvItem {
  label: string
  value?: string | number | null
  /** 等宽（Key / URL / 模型 ID） */
  mono?: boolean
  /** 右侧显示复制按钮 */
  copy?: boolean
  /** 自定义插槽名（内容复杂时用） */
  slot?: string
  /** 独占一行（长文本） */
  block?: boolean
}
