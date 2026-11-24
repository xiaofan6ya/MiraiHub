import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, fetchBlob } from '@/api/http'
import { resetUnauthorizedState, setUnauthorizedHandler } from '@/api/auth'
import { downloadBackup } from '@/api/maintenance'

/**
 * review_notes 待优化清单第 6 条：api/maintenance.ts 不再自己 fetch，改走 http.ts 的 fetchBlob()。
 * 这里验证 fetchBlob 与 http() 同一套请求头 / 错误处理，以及备份下载走的是它。
 */

interface FetchCall {
  url: string
  init: RequestInit
}

function blobResponse(status = 200, disposition = ''): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(disposition ? { 'content-disposition': disposition } : {}),
    blob: async () => new Blob(['SQLite format 3']),
    text: async () => (status >= 400 ? JSON.stringify({ detail: '需要开启文件操作' }) : ''),
  } as unknown as Response
}

function installFetch(response: Response) {
  const calls: FetchCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init: init || {} })
      return response
    }),
  )
  return calls
}

beforeEach(() => {
  setUnauthorizedHandler(null)
  resetUnauthorizedState()
  document.cookie = 'wb_csrf=tok-9; path=/'
})

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(null)
  document.cookie = 'wb_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
})

describe('fetchBlob', () => {
  it('POST 带 CSRF 头与同源凭证，返回 blob + 文件名', async () => {
    const calls = installFetch(blobResponse(200, 'attachment; filename="workbuddy-backup-1.db"'))
    const result = await fetchBlob('POST', '/api/maintenance/backup', {
      headers: { Accept: 'application/octet-stream' },
    })
    expect(result.filename).toBe('workbuddy-backup-1.db')
    expect(result.blob.size).toBeGreaterThan(0)
    expect(calls[0].init.method).toBe('POST')
    expect(calls[0].init.credentials).toBe('same-origin')
    const headers = new Headers(calls[0].init.headers as HeadersInit)
    expect(headers.get('X-CSRF-Token')).toBe('tok-9')
    expect(headers.get('Accept')).toBe('application/octet-stream')
  })

  it('非 2xx 抛 ApiError（文案取后端 detail），401 走统一的登录失效处理', async () => {
    installFetch(blobResponse(403))
    await expect(fetchBlob('POST', '/api/maintenance/backup')).rejects.toBeInstanceOf(ApiError)
    await expect(fetchBlob('POST', '/api/maintenance/backup')).rejects.toThrow('需要开启文件操作')

    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    installFetch(blobResponse(401))
    await expect(fetchBlob('GET', '/api/x')).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthorized).toHaveBeenCalled()
  })
})

describe('api/maintenance 备份下载', () => {
  it('downloadBackup 走 fetchBlob（POST + CSRF），文件名优先用响应头', async () => {
    const calls = installFetch(blobResponse(200, 'attachment; filename="from-header.db"'))
    const file = await downloadBackup()
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toContain('/api/maintenance/backup')
    expect(calls[0].init.method).toBe('POST')
    expect(new Headers(calls[0].init.headers as HeadersInit).get('X-CSRF-Token')).toBe('tok-9')
    expect(file.filename).toBe('from-header.db')
  })
})
