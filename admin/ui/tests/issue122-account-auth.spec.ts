import { afterEach, describe, expect, it, vi } from 'vitest'
import { addMethodsFor, authStateOf, beginBrowserAuth, flowOf, pollBrowserAuth, submitAuthFallback } from '@/api/account-add'

afterEach(() => vi.unstubAllGlobals())

describe('issue #122 account auth flows', () => {
  it('registers WorkBuddy phone login and all browser authorization platforms', () => {
    expect(addMethodsFor({ id: 'workbuddy', storage_platform: 'workbuddy' }).map((item) => item.key)).toContain('phone')
    for (const id of ['trae', 'minimax', 'astudio', 'codar']) {
      expect(addMethodsFor({ id, storage_platform: id }).map((item) => item.key)).toContain('authorize')
    }
  })

  it('passes the begin session identifier into the platform poll request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ pending: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await pollBrowserAuth({ id: 'trae', storage_platform: 'trae' }, { nonce: 'nonce-123' })
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/trae/device/poll?nonce=nonce-123'),
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('keeps each flow mapped to the backend endpoint and query key', () => {
    expect(flowOf({ id: 'minimax', storage_platform: 'minimax' })?.poll?.path).toBe('/api/minimax/device/poll')
    expect(flowOf({ id: 'minimax', storage_platform: 'minimax' })?.poll?.query?.({ auth_id: 'a-1' })).toEqual({ auth_id: 'a-1' })
    expect(flowOf({ id: 'astudio', storage_platform: 'astudio' })?.poll?.query?.({ auth_id: 'a-2' })).toEqual({ auth_id: 'a-2' })
    expect(flowOf({ id: 'codar', storage_platform: 'codar' })?.poll?.query?.({ auth_id: 'a-3' })).toEqual({ auth_id: 'a-3' })
  })

  it.each(['marvis', 'raccoon', 'wps-lingxi', 'minimax', 'astudio', 'codar'])('sends the auth_id returned by %s', async (id) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await pollBrowserAuth({ id }, { auth_id: 'session +/123' })
    expect(fetchMock.mock.calls[0]?.[0]).toContain('auth_id=session+%2B%2F123')
  })

  it('uses the selected international region when starting device authorization', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}', { status: 200 })))
    vi.stubGlobal('fetch', fetchMock)
    for (const id of ['trae-intl', 'minimax-intl']) {
      await beginBrowserAuth({ id })
      expect(fetchMock.mock.lastCall?.[0]).toContain('realm=intl')
    }
  })

  it('normalizes device links, codes, hints and terminal states', () => {
    expect(authStateOf({ verification_uri_complete: 'https://example.test/verify?code=ABC', user_code: 'ABC', hint: 'Confirm device' }))
      .toMatchObject({ state: 'opened', url: 'https://example.test/verify?code=ABC', userCode: 'ABC', message: 'Confirm device' })
    for (const status of ['expired', 'denied', 'canceled']) expect(authStateOf({ status }).state).toBe('failed')
    expect(authStateOf({ status: 'done', account: { account_id: 7 } }).state).toBe('authorized')
  })

  it.each([
    ['trae', '/api/trae/device/callback', { nonce: 'n-1', callback_url: 'callback' }],
    ['astudio', '/api/astudio/auth/cookie', { cookie: 'callback' }],
    ['codar', '/api/codar/auth/password', { username: 'account', password: 'secret' }],
  ] as const)('submits %s fallback using the backend contract', async (id, path, body) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await submitAuthFallback({ id }, { value: ' callback ', username: ' account ', password: 'secret' }, { nonce: 'n-1' })
    expect(fetchMock.mock.calls[0]?.[0]).toBe(path)
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual(body)
  })
})
