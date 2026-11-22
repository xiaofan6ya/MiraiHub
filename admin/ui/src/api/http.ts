/**
 * 全站唯一的请求出口（对应旧版 admin/console/secure-fetch.js + console-app.js 的 api()）。
 *
 * 三件事必须在这一层做完，散到页面里就一定会漏：
 *   1. credentials: 'same-origin' —— 会话凭证是 HttpOnly Cookie，不带就是没登录；
 *   2. 非 GET/HEAD/OPTIONS 自动补 X-CSRF-Token，值取可读 Cookie wb_csrf
 *      （双提交：值与 Cookie 相同，跨站请求带不上自定义头）；
 *   3. 每个请求带 X-Platform（旧版 _apiRaw 的行为）—— 切平台即换全局上下文，
 *      统计口径由后端按这个头收窄（见 admin/platform/scope.py）。
 *
 * 页面里禁止直接 fetch。
 */
import { onUnauthorized } from './auth'

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS'

export type QueryScalar = string | number | boolean | null | undefined
export type QueryValue = QueryScalar | QueryScalar[]
export type Query = Record<string, QueryValue>

export interface HttpOptions {
  query?: Query
  /** 对象 → JSON（自动带 Content-Type）；FormData/Blob/字符串原样传。 */
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
  /** 可选的硬超时（毫秒）。仅心跳这类后台探活使用，页面请求不要设。 */
  timeoutMs?: number
}

/** 非 2xx 统一抛它。页面只需要读 message；细节（detail）留给 ErrorBlock 展开。 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const CSRF_COOKIE_NAME = 'wb_csrf'
export const CSRF_HEADER_NAME = 'X-CSRF-Token'
export const PLATFORM_HEADER_NAME = 'X-Platform'
/** 与旧版 platform-switch.js 的 STORE_KEY 完全一致 —— 不引入第二套真相。 */
export const PLATFORM_STORAGE_KEY = 'wb_platform_current'

const SAFE_METHODS = new Set<string>(['GET', 'HEAD', 'OPTIONS'])

type HeaderProvider = () => Record<string, string> | null | undefined

let headerProvider: HeaderProvider | null = null

/**
 * 注册动态请求头（平台 store 用它注入 X-Platform）。
 *
 * 刻意做成"注册"而不是 import store：http 是 api 层最底层的模块，
 * 让它依赖 Pinia store 会形成 http → store → api → http 的循环。
 */
export function setHeaderProvider(provider: HeaderProvider | null): void {
  headerProvider = provider
}

export function readCookie(name: string): string {
  if (typeof document === 'undefined') return ''
  try {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const pattern = new RegExp('(?:^|;\\s*)' + escaped + '=([^;]*)')
    const match = document.cookie.match(pattern)
    if (!match) return ''
    return decodeURIComponent(match[1])
  } catch {
    return ''
  }
}

/** 旧版 _apiRaw 的兜底：平台模块还没加载时直接读 localStorage 的落库值。 */
function platformHeaderFallback(): Record<string, string> {
  try {
    const raw = JSON.parse(localStorage.getItem(PLATFORM_STORAGE_KEY) || 'null') as
      | { platform?: unknown; realm?: unknown }
      | null
    const platform = raw && typeof raw.platform === 'string' ? raw.platform.trim() : ''
    if (!platform) return {}
    const realm = raw && typeof raw.realm === 'string' ? raw.realm.trim() : ''
    return { [PLATFORM_HEADER_NAME]: realm ? platform + ':' + realm : platform }
  } catch {
    return {}
  }
}

function buildHeaders(method: string, input?: Record<string, string>): Headers {
  const headers = new Headers(input)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')

  if (!headers.has(PLATFORM_HEADER_NAME)) {
    const dynamic = headerProvider ? headerProvider() : null
    const scope = dynamic && Object.keys(dynamic).length ? dynamic : platformHeaderFallback()
    for (const [key, value] of Object.entries(scope || {})) {
      if (value && !headers.has(key)) headers.set(key, value)
    }
  }

  if (!SAFE_METHODS.has(method) && !headers.has(CSRF_HEADER_NAME)) {
    const token = readCookie(CSRF_COOKIE_NAME)
    if (token) headers.set(CSRF_HEADER_NAME, token)
  }
  return headers
}

function encodeQueryValue(value: QueryScalar): string {
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

/** 拼查询串：跳过 null/undefined/空串；数组按同名多值展开。 */
export function buildQueryString(query?: Query): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue
    const list = Array.isArray(value) ? value : [value]
    for (const item of list) {
      if (item === null || item === undefined || item === '') continue
      params.append(key, encodeQueryValue(item))
    }
  }
  const text = params.toString()
  return text ? '?' + text : ''
}

/** 相对路径补前导斜杠；已是绝对 URL 时原样使用。 */
export function resolveUrl(path: string, query?: Query): string {
  const suffix = buildQueryString(query)
  if (/^https?:\/\//i.test(path)) {
    if (!suffix) return path
    return path + (path.includes('?') ? '&' + suffix.slice(1) : suffix)
  }
  const normalized = path.startsWith('/') ? path : '/' + path
  if (!suffix) return normalized
  return normalized.includes('?') ? normalized + '&' + suffix.slice(1) : normalized + suffix
}

function isRawBody(body: unknown): boolean {
  return (
    typeof body === 'string' ||
    (typeof FormData !== 'undefined' && body instanceof FormData) ||
    (typeof Blob !== 'undefined' && body instanceof Blob) ||
    (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) ||
    (typeof ArrayBuffer !== 'undefined' && (body instanceof ArrayBuffer || ArrayBuffer.isView(body)))
  )
}

interface AbortHandle {
  signal?: AbortSignal
  dispose(): void
}

function createAbortHandle(signal?: AbortSignal, timeoutMs?: number): AbortHandle {
  if (!signal && !timeoutMs) return { signal, dispose: () => undefined }
  const controller = new AbortController()
  const forward = () => controller.abort(signal ? signal.reason : undefined)
  if (signal) {
    if (signal.aborted) forward()
    else signal.addEventListener('abort', forward)
  }
  const timer = timeoutMs && timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null
  return {
    signal: controller.signal,
    dispose() {
      if (timer) clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', forward)
    },
  }
}

type ErrorPayload = Record<string, unknown> | string | null

/** 从 FastAPI 的错误体里取一句人话。规则见 docs/ui-v2/05-patterns.md §1。 */
export function messageFromPayload(status: number, payload: ErrorPayload): string {
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    const detail = record.detail
    if (typeof detail === 'string' && detail.trim()) return detail.trim()
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0] as Record<string, unknown> | string | null
      if (typeof first === 'string' && first.trim()) return first.trim()
      if (first && typeof first === 'object') {
        const msg = (first as Record<string, unknown>).msg
        if (typeof msg === 'string' && msg.trim()) return msg.trim()
      }
    }
    if (detail && typeof detail === 'object') {
      const nested = detail as Record<string, unknown>
      if (typeof nested.message === 'string' && nested.message.trim()) return nested.message.trim()
      if (typeof nested.reason === 'string' && nested.reason.trim()) return nested.reason.trim()
    }
    for (const key of ['message', 'error']) {
      const value = record[key]
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
  }
  return '请求失败（HTTP ' + status + '）'
}

async function toApiError(response: Response): Promise<ApiError> {
  let text = ''
  try {
    text = await response.text()
  } catch {
    text = ''
  }
  let payload: ErrorPayload = null
  if (text.trim()) {
    try {
      payload = JSON.parse(text) as ErrorPayload
    } catch {
      // 非 JSON（反向代理的 502 页面、明文异常）：保留原文给 ErrorBlock 展开
      payload = text
    }
  }
  const error = new ApiError(response.status, messageFromPayload(response.status, payload), payload ?? text)
  if (response.status === 401) onUnauthorized()
  return error
}

function looksLikeJson(text: string): boolean {
  const head = text.trimStart()[0]
  return head === '{' || head === '['
}

async function readBody<T>(response: Response): Promise<T> {
  if (response.status === 204 || response.status === 205) return undefined as T
  const text = await response.text()
  if (!text) return undefined as T
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('json') || looksLikeJson(text)) {
    try {
      return JSON.parse(text) as T
    } catch {
      throw new ApiError(response.status, '响应不是合法的 JSON', text)
    }
  }
  return text as unknown as T
}

export async function http<T = unknown>(
  method: HttpMethod | string,
  path: string,
  options: HttpOptions = {},
): Promise<T> {
  const upper = String(method).toUpperCase()
  const headers = buildHeaders(upper, options.headers)
  let body: BodyInit | undefined
  const raw = options.body
  if (raw !== undefined && raw !== null) {
    if (isRawBody(raw)) {
      body = raw as BodyInit
    } else {
      if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
      body = JSON.stringify(raw)
    }
  }
  const abort = createAbortHandle(options.signal, options.timeoutMs)
  try {
    const response = await fetch(resolveUrl(path, options.query), {
      method: upper,
      credentials: 'same-origin',
      headers,
      body,
      signal: abort.signal,
    })
    if (!response.ok) throw await toApiError(response)
    return await readBody<T>(response)
  } finally {
    abort.dispose()
  }
}

/** 常见动词的薄封装，页面里读起来更短。 */
export const api = {
  get: <T = unknown>(path: string, options: Omit<HttpOptions, 'body'> = {}) => http<T>('GET', path, options),
  post: <T = unknown>(path: string, body?: unknown, options: HttpOptions = {}) =>
    http<T>('POST', path, { ...options, body }),
  put: <T = unknown>(path: string, body?: unknown, options: HttpOptions = {}) =>
    http<T>('PUT', path, { ...options, body }),
  patch: <T = unknown>(path: string, body?: unknown, options: HttpOptions = {}) =>
    http<T>('PATCH', path, { ...options, body }),
  del: <T = unknown>(path: string, options: HttpOptions = {}) => http<T>('DELETE', path, options),
}

/** Content-Disposition 的文件名解析：优先 RFC 5987 的 filename*，其次 filename。 */
export function filenameFromContentDisposition(header: string | null | undefined): string {
  if (!header) return ''
  const star = /filename\*\s*=\s*([^;]+)/i.exec(header)
  if (star) {
    const value = star[1].trim().replace(/^"|"$/g, '')
    const extended = /^([\w-]+)''([\s\S]*)$/.exec(value)
    if (extended) {
      try {
        return decodeURIComponent(extended[2])
      } catch {
        return extended[2]
      }
    }
    return value
  }
  const plain = /filename\s*=\s*("([^"]*)"|[^;]+)/i.exec(header)
  if (plain) {
    const value = (plain[2] !== undefined ? plain[2] : plain[1]) || ''
    return value.trim().replace(/^"|"$/g, '')
  }
  return ''
}

export interface DownloadResult {
  filename: string
  size: number
}

/**
 * 下载类接口：fetch → blob → 触发浏览器下载。
 * 文件名优先用调用方给的，其次 Content-Disposition，最后退回路径末段。
 */
export async function download(path: string, query?: Query, filename?: string): Promise<DownloadResult> {
  const headers = buildHeaders('GET')
  const response = await fetch(resolveUrl(path, query), {
    method: 'GET',
    credentials: 'same-origin',
    headers,
  })
  if (!response.ok) throw await toApiError(response)
  const blob = await response.blob()
  const segments = path.split('?')[0].split('/').filter(Boolean)
  const fallback = segments.length ? segments[segments.length - 1] : 'download'
  const name = filename || filenameFromContentDisposition(response.headers.get('content-disposition')) || fallback
  if (typeof document !== 'undefined' && typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
    const href = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = href
    link.download = name
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(href), 0)
  }
  return { filename: name, size: blob.size }
}

export interface StreamEventMeta {
  /** SSE 的 event 名（没有 event: 行时为空串）。 */
  event: string
  /** 原始帧内容，排障用。 */
  raw: string
}

export type StreamHandler<T = unknown> = (payload: T, meta: StreamEventMeta) => void

/**
 * 流式接口（SSE / NDJSON）。
 *
 * 两种格式在旧版里都有：
 *   · agent.js 的对话流：SSE，按空行分帧，每帧若干 data: 行拼起来再 JSON.parse；
 *   · 导出/推送类接口：NDJSON，一行一条 JSON。
 * 按响应的 content-type 选择分帧方式；分不清时按 SSE 处理（旧版主流）。
 * 单帧解析失败直接跳过 —— 与旧版一致，坏帧不能打断整条流。
 */
export async function stream<T = unknown>(
  path: string,
  body: unknown,
  onEvent: StreamHandler<T>,
  signal?: AbortSignal,
): Promise<void> {
  const hasBody = body !== undefined && body !== null
  const headers = buildHeaders(hasBody ? 'POST' : 'GET', {
    Accept: 'text/event-stream, application/x-ndjson, application/json',
  })
  if (hasBody && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(resolveUrl(path), {
    method: hasBody ? 'POST' : 'GET',
    credentials: 'same-origin',
    headers,
    body: hasBody ? JSON.stringify(body) : undefined,
    signal,
  })
  if (!response.ok) throw await toApiError(response)
  const contentType = response.headers.get('content-type') || ''
  const mode = contentType.includes('ndjson') ? 'ndjson' : 'sse'

  const reader = response.body ? response.body.getReader() : null
  if (!reader) return
  const decoder = new TextDecoder()
  let buffer = ''

  const emit = (rawFrame: string, data: string, event: string) => {
    if (!data || data === '[DONE]') return
    try {
      onEvent(JSON.parse(data) as T, { event, raw: rawFrame })
    } catch {
      /* 坏帧跳过 */
    }
  }

  const drainSse = () => {
    let index = buffer.indexOf('\n\n')
    while (index >= 0) {
      const frame = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      let event = ''
      const dataLines: string[] = []
      for (const line of frame.split('\n')) {
        const trimmed = line.replace(/\r$/, '')
        if (!trimmed || trimmed.startsWith(':')) continue
        if (trimmed.startsWith('event:')) event = trimmed.slice(6).trim()
        else if (trimmed.startsWith('data:')) dataLines.push(trimmed.slice(5).trim())
      }
      emit(frame, dataLines.join('\n'), event)
      index = buffer.indexOf('\n\n')
    }
  }

  const drainNdjson = () => {
    let index = buffer.indexOf('\n')
    while (index >= 0) {
      const line = buffer.slice(0, index).replace(/\r$/, '').trim()
      buffer = buffer.slice(index + 1)
      if (line) emit(line, line, '')
      index = buffer.indexOf('\n')
    }
  }

  try {
    for (;;) {
      const chunk = await reader.read()
      if (chunk.done) break
      buffer += decoder.decode(chunk.value, { stream: true })
      if (mode === 'ndjson') drainNdjson()
      else drainSse()
    }
    buffer += decoder.decode()
    if (buffer.trim()) {
      if (mode === 'ndjson') emit(buffer, buffer.trim(), '')
      else {
        const dataLines = buffer
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trim())
        emit(buffer, dataLines.join('\n'), '')
      }
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      /* 已释放 */
    }
  }
}

/** 判断是不是 AbortError（页面切换/重复提交主动取消）。 */
export function isAbortError(error: unknown): boolean {
  return Boolean(error && typeof error === 'object' && (error as { name?: string }).name === 'AbortError')
}
