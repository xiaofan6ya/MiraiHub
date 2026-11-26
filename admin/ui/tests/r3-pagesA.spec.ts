/**
 * 第 3 轮 r3_pagesA 续跑：账号池导出二次确认判定、用量 → 日志结果筛选（英文 label 兼容）。
 */
import { describe, expect, it } from 'vitest'
import { isAccountExportConfirmRequired } from '@/api/accounts'
import { logsQueryFor } from '@/api/usage'

describe('isAccountExportConfirmRequired', () => {
  it('428 + export_confirm_required → true', () => {
    expect(isAccountExportConfirmRequired({ status: 428, detail: { code: 'export_confirm_required' } })).toBe(true)
    expect(
      isAccountExportConfirmRequired({ status: 428, detail: { detail: { code: 'export_confirm_required' } } }),
    ).toBe(true)
    expect(isAccountExportConfirmRequired({ status: 428 })).toBe(true)
  })

  it('其他状态码或其他 code → false', () => {
    expect(isAccountExportConfirmRequired({ status: 400, detail: { code: 'export_confirm_required' } })).toBe(false)
    expect(isAccountExportConfirmRequired({ status: 428, detail: { code: 'other' } })).toBe(false)
    expect(isAccountExportConfirmRequired(null)).toBe(false)
    expect(isAccountExportConfirmRequired('428')).toBe(false)
  })
})

describe('logsQueryFor result', () => {
  const filters = { start: '', end: '' }
  it('中文 / 英文「成功」都映射为 success', () => {
    expect(logsQueryFor('result', { label: '成功' } as never, filters)?.result).toBe('success')
    expect(logsQueryFor('result', { label: 'Success' } as never, filters)?.result).toBe('success')
    expect(logsQueryFor('result', { label: '失败' } as never, filters)?.result).toBe('error')
  })
})
