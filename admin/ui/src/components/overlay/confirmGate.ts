/**
 * 确认框的「高风险门禁」：输入确认文字或勾选「我已了解」后才允许提交。
 * 抽成纯函数，便于单测（组件里也用它算按钮 disabled）。
 */

export interface ConfirmGateInput {
  /** 需要用户原样输入的确认文字。 */
  requireText?: string
  /** 用户当前输入。 */
  typed?: string
  /** 需要勾选的说明文案（存在即要求勾选）。 */
  requireCheckbox?: string
  /** 用户是否已勾选。 */
  checked?: boolean
  /** 提交中：按钮置灰防重复点击。 */
  loading?: boolean
}

export function canConfirm(input: ConfirmGateInput): boolean {
  if (input.loading) return false
  const needText = (input.requireText ?? '').trim()
  if (needText) {
    const typed = (input.typed ?? '').trim()
    if (typed !== needText) return false
  }
  if (input.requireCheckbox && input.checked !== true) return false
  return true
}
