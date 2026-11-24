import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/http'
import { resetUnauthorizedState, setUnauthorizedHandler } from '@/api/auth'
import { backupWithConfirm, downloadBackup, isBackupConfirmRequired } from '@/api/maintenance'

/**
 * 第 5 轮 r5_fe_pages：维护页整库备份接确认闸门。
 *   · 先确认、确认后带 X-Export-Confirm: include-secrets；
 *   · 收到 428 export_confirm_required 时再确认一次后重发（最多一次）；
 *   · 老后端（不看确认头、直接 200）同样可用。
 */

interface FetchCall {
  url: string
  init: RequestInit
}

function okResponse(): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-disposition': 'attachment; filename="workbuddy-backup-r5.db"' }),
    blob: async () => new Blob(['SQLite format 3']),
    text: async () => '',
  } as unknown as Response
}

function confirmRequired(): Response {
  const body = JSON.stringify({ detail: { code: 'export_confirm_required', message: 'confirm required' } })
  return {
    ok: false,
    status: 428,
    headers: new Headers({ 'content-type': 'application/json' }),
    blob: async () => new Blob([body]),
    text: async () => body,
    json: async () => JSON.parse(body),
  } as unknown as Response
}

function installFetch(...responses: Response[]): FetchCall[] {
  const calls: FetchCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init: init || {} })
      return responses[Math.min(calls.length - 1, responses.length - 1)]
    }),
  )
  return calls
}

function confirmHeader(call: FetchCall): string | null {
  return new Headers(call.init.headers as HeadersInit).get('X-Export-Confirm')
}

beforeEach(() => {
  setUnauthorizedHandler(null)
  resetUnauthorizedState()
  document.cookie = 'wb_csrf=tok-r5; path=/'
})

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(null)
  document.cookie = 'wb_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
})

describe('downloadBackup 确认头', () => {
  it('默认不带确认头（兼容旧调用）', async () => {
    const calls = installFetch(okResponse())
    await downloadBackup()
    expect(confirmHeader(calls[0])).toBeNull()
  })

  it('confirmed=true 带 X-Export-Confirm: include-secrets，CSRF 头不丢', async () => {
    const calls = installFetch(okResponse())
    await downloadBackup(undefined, true)
    expect(confirmHeader(calls[0])).toBe('include-secrets')
    expect(new Headers(calls[0].init.headers as HeadersInit).get('X-CSRF-Token')).toBe('tok-r5')
    expect(calls[0].init.method).toBe('POST')
  })
})

describe('backupWithConfirm', () => {
  it('取消确认：不发请求，返回 null', async () => {
    const calls = installFetch(okResponse())
    const ask = vi.fn(async () => false)
    expect(await backupWithConfirm(ask)).toBeNull()
    expect(ask).toHaveBeenCalledTimes(1)
    expect(calls).toHaveLength(0)
  })

  it('确认后带头请求；老后端 / 新后端放行都只发一次', async () => {
    const calls = installFetch(okResponse())
    const ask = vi.fn(async () => true)
    const file = await backupWithConfirm(ask)
    expect(file?.filename).toBe('workbuddy-backup-r5.db')
    expect(ask).toHaveBeenCalledTimes(1)
    expect(ask).toHaveBeenCalledWith(false)
    expect(calls).toHaveLength(1)
    expect(confirmHeader(calls[0])).toBe('include-secrets')
  })

  it('收到 428 export_confirm_required：再确认一次后重发', async () => {
    const calls = installFetch(confirmRequired(), okResponse())
    const ask = vi.fn(async () => true)
    const file = await backupWithConfirm(ask)
    expect(file).not.toBeNull()
    expect(ask).toHaveBeenNthCalledWith(2, true)
    expect(calls).toHaveLength(2)
    expect(confirmHeader(calls[1])).toBe('include-secrets')
  })

  it('428 后取消：返回 null，不重发', async () => {
    const calls = installFetch(confirmRequired(), okResponse())
    const answers = [true, false]
    const ask = vi.fn(async () => answers.shift() ?? false)
    expect(await backupWithConfirm(ask)).toBeNull()
    expect(calls).toHaveLength(1)
  })

  it('连续两次 428：只重发一次，错误抛给调用方', async () => {
    const calls = installFetch(confirmRequired(), confirmRequired())
    const ask = vi.fn(async () => true)
    await expect(backupWithConfirm(ask)).rejects.toBeInstanceOf(ApiError)
    expect(calls).toHaveLength(2)
    expect(ask).toHaveBeenCalledTimes(2)
  })

  it('其它错误不重试', async () => {
    const forbidden = { ...okResponse(), ok: false, status: 403, text: async () => JSON.stringify({ detail: 'nope' }) } as unknown as Response
    const calls = installFetch(forbidden)
    const ask = vi.fn(async () => true)
    await expect(backupWithConfirm(ask)).rejects.toBeInstanceOf(ApiError)
    expect(calls).toHaveLength(1)
    expect(ask).toHaveBeenCalledTimes(1)
  })

  it('isBackupConfirmRequired 只认 428', () => {
    expect(isBackupConfirmRequired({ status: 428, detail: { detail: { code: 'export_confirm_required' } } })).toBe(true)
    expect(isBackupConfirmRequired({ status: 403, detail: {} })).toBe(false)
    expect(isBackupConfirmRequired(null)).toBe(false)
  })
})
