import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  buildQueryString,
  download,
  filenameFromContentDisposition,
  http,
  messageFromPayload,
  resolveUrl,
  setHeaderProvider,
  stream,
} from '@/api/http'
import { resetUnauthorizedState, setUnauthorizedHandler } from '@/api/auth'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function htmlResponse(body: string, status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'text/html' }),
    text: async () => body,
  } as unknown as Response
}

function emptyResponse(status = 204): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    text: async () => '',
  } as unknown as Response
}

interface FetchCall {
  url: string
  init: RequestInit
}

function installFetch(responses: Response[] | ((call: FetchCall) => Response)) {
  const calls: FetchCall[] = []
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(input), init: init || {} }
    calls.push(call)
    if (typeof responses === 'function') return responses(call)
    const next = responses.shift()
    if (!next) throw new Error('没有预置的响应了：' + call.url)
    return next
  })
  vi.stubGlobal('fetch', mock)
  return { calls, mock }
}

function headersOf(call: FetchCall): Headers {
  return new Headers((call.init.headers || {}) as HeadersInit)
}

beforeEach(() => {
  setUnauthorizedHandler(null)
  resetUnauthorizedState()
  setHeaderProvider(null)
  document.cookie = 'wb_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(null)
  setHeaderProvider(null)
})

describe('http 基础行为', () => {
  it('同源请求带 credentials，GET 不加 CSRF 头', async () => {
    document.cookie = 'wb_csrf=token-123; path=/'
    const { calls } = installFetch([jsonResponse({ ok: true })])
    const data = await http<{ ok: boolean }>('GET', '/api/things')
    expect(data).toEqual({ ok: true })
    expect(calls).toHaveLength(1)
    expect(calls[0].init.credentials).toBe('same-origin')
    expect(headersOf(calls[0]).get('X-CSRF-Token')).toBeNull()
    expect(headersOf(calls[0]).get('Accept')).toBe('application/json')
  })

  it('非安全方法自动加 X-CSRF-Token，并按对象 body 设 JSON 头', async () => {
    document.cookie = 'wb_csrf=token-123; path=/'
    const { calls } = installFetch([jsonResponse({ ok: true }), jsonResponse({ ok: true })])
    await http('POST', '/api/things', { body: { a: 1 } })
    expect(headersOf(calls[0]).get('X-CSRF-Token')).toBe('token-123')
    expect(headersOf(calls[0]).get('Content-Type')).toBe('application/json')
    expect(calls[0].init.body).toBe(JSON.stringify({ a: 1 }))

    await http('PUT', '/api/things', { body: { b: 2 } })
    expect(headersOf(calls[1]).get('X-CSRF-Token')).toBe('token-123')
  })

  it('调用方显式传入的 CSRF 头不被覆盖', async () => {
    document.cookie = 'wb_csrf=cookie-token; path=/'
    const { calls } = installFetch([jsonResponse({ ok: true })])
    await http('DELETE', '/api/things/1', { headers: { 'X-CSRF-Token': 'manual' } })
    expect(headersOf(calls[0]).get('X-CSRF-Token')).toBe('manual')
  })

  it('204 与空响应体返回 undefined', async () => {
    installFetch([emptyResponse(204), emptyResponse(200)])
    await expect(http('DELETE', '/api/things/1')).resolves.toBeUndefined()
    await expect(http('GET', '/api/things/2')).resolves.toBeUndefined()
  })

  it('查询串：跳过空值，数组按同名多值展开，已有 ? 时用 & 续接', async () => {
    expect(buildQueryString({ a: 1, b: '', c: null, d: false, e: ['x', 'y'] })).toBe(
      '?a=1&d=false&e=x&e=y',
    )
    expect(resolveUrl('/api/x', { a: 1 })).toBe('/api/x?a=1')
    expect(resolveUrl('/api/x?z=1', { a: 1 })).toBe('/api/x?z=1&a=1')
    expect(resolveUrl('api/x')).toBe('/api/x')
    const { calls } = installFetch([jsonResponse({ ok: true })])
    await http('GET', '/api/x', { query: { page: 2, size: 50, empty: '' } })
    expect(calls[0].url).toBe('/api/x?page=2&size=50')
  })
})

describe('错误解析（05-patterns §1 的四种情况）', () => {
  async function catchError(response: Response): Promise<ApiError> {
    installFetch([response])
    try {
      await http('GET', '/api/boom')
    } catch (error) {
      return error as ApiError
    }
    throw new Error('应当抛错却没有抛')
  }

  it('detail 是字符串', async () => {
    const error = await catchError(jsonResponse({ detail: '会话已失效' }, 400))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(400)
    expect(error.message).toBe('会话已失效')
  })

  it('detail 是 FastAPI 校验错误数组，取第一个 msg', async () => {
    const error = await catchError(
      jsonResponse({ detail: [{ loc: ['body', 'name'], msg: 'field required', type: 'value_error' }] }, 422),
    )
    expect(error.message).toBe('field required')
    expect(error.status).toBe(422)
  })

  it('message 字段', async () => {
    const error = await catchError(jsonResponse({ message: '上游返回 401' }, 400))
    expect(error.message).toBe('上游返回 401')
  })

  it('error 字段', async () => {
    const error = await catchError(jsonResponse({ error: 'provider offline' }, 503))
    expect(error.message).toBe('provider offline')
  })

  it('非 JSON 响应（反向代理的 502 页面）用默认文案', async () => {
    const error = await catchError(htmlResponse('<html><body>Bad Gateway</body></html>', 502))
    expect(error.message).toBe('请求失败（HTTP 502）')
    expect(String(error.detail)).toContain('Bad Gateway')
  })

  it('完全空响应体也用默认文案', async () => {
    const error = await catchError(emptyResponse(500))
    expect(error.message).toBe('请求失败（HTTP 500）')
  })

  it('messageFromPayload 优先级：detail 优先于 message/error', () => {
    expect(messageFromPayload(400, { detail: 'd', message: 'm', error: 'e' })).toBe('d')
    expect(messageFromPayload(400, { message: 'm', error: 'e' })).toBe('m')
    expect(messageFromPayload(400, { error: 'e' })).toBe('e')
    expect(messageFromPayload(400, { detail: [] })).toBe('请求失败（HTTP 400）')
    expect(messageFromPayload(400, null)).toBe('请求失败（HTTP 400）')
  })
})

describe('401 处理', () => {
  it('401 调 onUnauthorized 并抛 ApiError', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    installFetch([jsonResponse({ detail: '未登录或登录已过期' }, 401)])
    const error = (await http('GET', '/api/private').catch((e) => e)) as ApiError
    expect(handler).toHaveBeenCalledTimes(1)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(401)
    expect(error.message).toBe('未登录或登录已过期')
  })

  it('非 401 不触发跳转', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    installFetch([jsonResponse({ detail: '参数错误' }, 400)])
    await http('GET', '/api/private').catch(() => undefined)
    expect(handler).not.toHaveBeenCalled()
  })
})

describe('平台作用域请求头', () => {
  it('注册的 provider 注入 X-Platform', async () => {
    setHeaderProvider(() => ({ 'X-Platform': 'trae:cn' }))
    const { calls } = installFetch([jsonResponse({ ok: true })])
    await http('GET', '/api/stats/overview')
    expect(headersOf(calls[0]).get('X-Platform')).toBe('trae:cn')
  })

  it('没有 provider 时回落读 localStorage（旧版 _apiRaw 的兜底）', async () => {
    localStorage.setItem('wb_platform_current', JSON.stringify({ platform: 'qoder', realm: '' }))
    const { calls } = installFetch([jsonResponse({ ok: true })])
    await http('GET', '/api/stats/overview')
    expect(headersOf(calls[0]).get('X-Platform')).toBe('qoder')
  })
})

describe('download', () => {
  it('解析 Content-Disposition 的两种文件名', () => {
    expect(filenameFromContentDisposition("attachment; filename*=UTF-8''%E8%B4%A6%E5%8F%B7.jsonl")).toBe(
      '账号.jsonl',
    )
    expect(filenameFromContentDisposition('attachment; filename="keys.jsonl"')).toBe('keys.jsonl')
    expect(filenameFromContentDisposition('attachment; filename=plain.csv')).toBe('plain.csv')
    expect(filenameFromContentDisposition(null)).toBe('')
  })

  it('下载失败抛 ApiError，成功返回文件名与大小', async () => {
    installFetch([jsonResponse({ detail: '导出失败' }, 500)])
    const failed = (await download('/api/export').catch((e) => e)) as ApiError
    expect(failed).toBeInstanceOf(ApiError)
    expect(failed.message).toBe('导出失败')

    const blob = new Blob(['a,b'], { type: 'text/csv' })
    installFetch([
      {
        ok: true,
        status: 200,
        headers: new Headers({ 'content-disposition': 'attachment; filename="accounts.csv"' }),
        blob: async () => blob,
      } as unknown as Response,
    ])
    const result = await download('/api/export')
    expect(result.filename).toBe('accounts.csv')
    expect(result.size).toBe(blob.size)
  })
})

describe('stream', () => {
  function sseResponse(chunks: string[], contentType = 'text/event-stream'): Response {
    let index = 0
    return {
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': contentType }),
      body: {
        getReader: () => ({
          read: async () =>
            index < chunks.length
              ? { done: false, value: new TextEncoder().encode(chunks[index++]) }
              : { done: true, value: undefined },
          releaseLock: () => undefined,
        }),
      },
    } as unknown as Response
  }

  it('SSE 按空行分帧，跨分片的帧能拼起来，坏帧与 [DONE] 跳过', async () => {
    const events: Array<{ payload: unknown; event: string }> = []
    installFetch([
      sseResponse([
        'event: delta\ndata: {"text":"你',
        '好"}\n\n',
        'data: [DONE]\n\n',
        'data: not-json\n\n',
        'event: done\ndata: {"ok":true}\n\n',
      ]),
    ])
    await stream('/api/agent/chat', { prompt: 'hi' }, (payload, meta) => {
      events.push({ payload, event: meta.event })
    })
    expect(events).toEqual([
      { payload: { text: '你好' }, event: 'delta' },
      { payload: { ok: true }, event: 'done' },
    ])
  })

  it('NDJSON 一行一条', async () => {
    const events: unknown[] = []
    installFetch([sseResponse(['{"a":1}\n{"b":', '2}\n'], 'application/x-ndjson')])
    await stream('/api/export/stream', null, (payload) => {
      events.push(payload)
    })
    expect(events).toEqual([{ a: 1 }, { b: 2 }])
  })

  it('有 body 用 POST + JSON，无 body 用 GET', async () => {
    const { calls } = installFetch([sseResponse(['data: {}\n\n']), sseResponse(['data: {}\n\n'])])
    await stream('/api/agent/chat', { prompt: 'hi' }, () => undefined)
    expect(calls[0].init.method).toBe('POST')
    expect(headersOf(calls[0]).get('Content-Type')).toBe('application/json')
    expect(calls[0].init.body).toBe(JSON.stringify({ prompt: 'hi' }))
    await stream('/api/logs/usage/stream', null, () => undefined)
    expect(calls[1].init.method).toBe('GET')
  })
})
