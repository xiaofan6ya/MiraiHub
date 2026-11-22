import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetUnauthorizedState, setUnauthorizedHandler } from '@/api/auth'
import {
  banSession,
  checkSession,
  listSessions,
  logout,
  renewSession,
  revokeOtherSessions,
  revokeSession,
  sessionsSummary,
  unbanDevice,
} from '@/api/session'

interface Call {
  url: string
  method: string
  body: string
  headers: Headers
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function installFetch(handler: (call: Call) => Response) {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        url: String(input),
        method: String((init && init.method) || 'GET').toUpperCase(),
        body: typeof init?.body === 'string' ? init.body : '',
        headers: new Headers((init && init.headers) as HeadersInit),
      }
      calls.push(call)
      return handler(call)
    }),
  )
  return calls
}

beforeEach(() => {
  setUnauthorizedHandler(() => undefined)
  resetUnauthorizedState()
})

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(null)
})

describe('登录态', () => {
  it('checkSession 打旧版启动探针 /api/accounts/probe', async () => {
    const calls = installFetch(() => jsonResponse({ ok: true }))
    await expect(checkSession()).resolves.toBe(true)
    expect(calls[0]).toMatchObject({ url: '/api/accounts/probe', method: 'GET' })
  })

  it('401/403 视为未登录，其它错误照旧抛出', async () => {
    installFetch(() => jsonResponse({ detail: '未登录' }, 401))
    await expect(checkSession()).resolves.toBe(false)

    installFetch(() => jsonResponse({ detail: '未登录' }, 403))
    await expect(checkSession()).resolves.toBe(false)

    installFetch(() => jsonResponse({ detail: '炸了' }, 500))
    await expect(checkSession()).rejects.toThrow('炸了')
  })

  it('renewSession 用 GET /api/login/session 续期', async () => {
    const calls = installFetch(() => jsonResponse({ ok: true }))
    await renewSession()
    expect(calls[0]).toMatchObject({ url: '/api/login/session', method: 'GET' })
  })

  it('logout 用 POST /api/login/logout；redirect:false 时不跳转', async () => {
    const calls = installFetch(() => jsonResponse({ ok: true, revoked: true }))
    await logout({ redirect: false })
    expect(calls[0]).toMatchObject({ url: '/api/login/logout', method: 'POST' })
  })

  it('logout 请求失败也不抛错（退出必须幂等）', async () => {
    installFetch(() => jsonResponse({ detail: '会话已失效' }, 500))
    await expect(logout({ redirect: false })).resolves.toBeUndefined()
  })
})

describe('登录设备', () => {
  it('listSessions 用 GET /api/sessions，includeRevoked 映射 include_revoked=1', async () => {
    const payload = {
      ok: true,
      multi_device: true,
      max_devices: 5,
      active: 1,
      items: [],
      bans: [],
    }
    const calls = installFetch(() => jsonResponse(payload))
    const data = await listSessions()
    expect(data).toEqual(payload)
    expect(calls[0]).toMatchObject({ url: '/api/sessions', method: 'GET' })

    await listSessions(true)
    expect(calls[1].url).toBe('/api/sessions?include_revoked=1')
  })

  it('踢下线 / 踢其它 / 拉黑 / 解黑 的路径与方法', async () => {
    const calls = installFetch((call) => {
      if (call.url.includes('/ban/')) return jsonResponse({ ok: true })
      return jsonResponse({ ok: true, revoked: 2, ban_id: 7, label: 'MacBook' })
    })

    await revokeSession('s-1')
    await revokeOtherSessions()
    await banSession('s-2')
    await unbanDevice(7)

    expect(calls[0]).toMatchObject({ url: '/api/sessions/s-1/revoke', method: 'POST' })
    expect(calls[1]).toMatchObject({ url: '/api/sessions/revoke-others', method: 'POST' })
    expect(calls[2]).toMatchObject({ url: '/api/sessions/s-2/ban', method: 'POST' })
    expect(JSON.parse(calls[2].body)).toEqual({ reason: '手动拉黑' })
    expect(calls[2].headers.get('Content-Type')).toBe('application/json')
    expect(calls[3]).toMatchObject({ url: '/api/sessions/ban/7', method: 'DELETE' })
  })

  it('sid 做 URL 编码', async () => {
    const calls = installFetch(() => jsonResponse({ ok: true }))
    await revokeSession('a/b c')
    expect(calls[0].url).toBe('/api/sessions/a%2Fb%20c/revoke')
  })

  it('sessionsSummary 文案', () => {
    expect(
      sessionsSummary({
        ok: true,
        multi_device: true,
        max_devices: 5,
        active: 2,
        items: [],
        bans: [],
      }),
    ).toBe('当前 2 台在线（上限 5）')
    expect(
      sessionsSummary({
        ok: true,
        multi_device: true,
        max_devices: 0,
        active: 1,
        items: [],
        bans: [],
      }),
    ).toBe('当前 1 台在线')
    expect(sessionsSummary(null)).toBe('')
  })
})
