import { describe, expect, it } from 'vitest'
import { canConfirm } from '../overlay/confirmGate'

describe('ConfirmDialog 门禁（useConfirm 的 Promise 结果依赖它）', () => {
  it('普通确认框直接可提交', () => {
    expect(canConfirm({})).toBe(true)
  })

  it('要求输入确认文字时必须完全一致', () => {
    expect(canConfirm({ requireText: 'DELETE' })).toBe(false)
    expect(canConfirm({ requireText: 'DELETE', typed: 'delete' })).toBe(false)
    expect(canConfirm({ requireText: 'DELETE', typed: ' DELETE ' })).toBe(true)
  })

  it('要求勾选时必须真的勾上', () => {
    expect(canConfirm({ requireCheckbox: '我已了解' })).toBe(false)
    expect(canConfirm({ requireCheckbox: '我已了解', checked: false })).toBe(false)
    expect(canConfirm({ requireCheckbox: '我已了解', checked: true })).toBe(true)
  })

  it('提交中禁止重复点击', () => {
    expect(canConfirm({ loading: true })).toBe(false)
  })
})
