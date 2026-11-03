/**
 * 统一 API 封装：自动附带 X-Admin-Token 与 X-Platform（平台口径），
 * 401 时本地壳（桌面/安卓）先走桥换新 token 重试一次，其余场景交由 AuthContext 处理。
 * 对应旧版 index.html 的 api()/_apiRaw() 约定。
 */
import { getPlatformHeader } from './platformBus'
import { ensureLocalSession, isLocalShell } from '../bridges/native'

export const TOKEN_KEY = 'wb_admin_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

let unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(fn: (() => void) | null) {
  unauthorizedHandler = fn
}

export interface ApiError extends Error {
  status?: number
  data?: unknown
  /** 后端返回的错误码（如 setup_required） */
  code?: string
}

export interface ApiInit extends Omit<RequestInit, 'body' | 'headers'> {
  /** JSON body（自动序列化并设置 Content-Type） */
  body?: unknown
  /** form-urlencoded body */
  form?: URLSearchParams
  /** 原样发送的 body（如备份恢复的 octet-stream） */
  rawBody?: BodyInit
  headers?: HeadersInit
  /** 不注入 X-Platform（平台管理接口本身等） */
  skipPlatform?: boolean
  /** 返回原始 Response（下载/blob 场景） */
  raw?: boolean
  /** 超时毫秒数，默认 30000；0 表示不限制 */
  timeout?: number
}

async function extractError(resp: Response): Promise<ApiError> {
  let data: unknown = undefined
  let text = ''
  try {
    text = await resp.text()
    data = text ? JSON.parse(text) : undefined
  } catch {
    data = text || undefined
  }
  let msg = ''
  let code = ''
  if (data && typeof data === 'object') {
    const d = data as Record<string, any>
    if (Array.isArray(d?.detail)) {
      // FastAPI 422 校验错误：[{loc, msg, type}...]，转成可读文本（最多 5 条）
      const items = d.detail as { loc?: (string | number)[]; msg?: string }[]
      const head = items
        .slice(0, 5)
        .map((e) => `${(e.loc ?? []).slice(1).join('.') || '参数'}：${e.msg ?? ''}`)
      msg = head.join('；') + (items.length > 5 ? `（共 ${items.length} 项）` : '')
    } else if (d?.detail && typeof d.detail === 'object') {
      // 结构化 detail（例如 503 {"message": "...", "code": "setup_required"}）
      const detail = d.detail as Record<string, any>
      msg = detail.message || detail.msg || ''
      code = typeof detail.code === 'string' ? detail.code : ''
    } else {
      msg = d?.error?.message || (typeof d?.detail === 'string' ? d.detail : '') || d?.message || ''
      code = typeof d?.code === 'string' ? d.code : ''
    }
  } else if (typeof data === 'string' && data) {
    msg = data.length < 300 ? data : ''
  }
  const err = new Error(msg || `请求失败（HTTP ${resp.status}）`) as ApiError
  err.status = resp.status
  err.data = data
  if (code) err.code = code
  return err
}

export async function api<T = unknown>(path: string, init: ApiInit = {}): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const headers = new Headers(init.headers)
    const token = getToken()
    if (token) headers.set('X-Admin-Token', token)
    if (!init.skipPlatform) {
      const ph = getPlatformHeader()
      for (const [k, v] of Object.entries(ph)) {
        if (!headers.has(k)) headers.set(k, v)
      }
    }
    let body: BodyInit | undefined
    if (init.form !== undefined) {
      body = init.form
    } else if (init.rawBody !== undefined) {
      body = init.rawBody
    } else if (init.body !== undefined) {
      body = JSON.stringify(init.body)
      if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    }
    const ctrl = init.timeout === 0 ? null : new AbortController()
    const timer = ctrl
      ? window.setTimeout(() => ctrl.abort(), init.timeout ?? 30000)
      : undefined
    try {
      return await fetch(path, {
        ...init,
        headers,
        body,
        credentials: 'same-origin',
        signal: init.signal ?? ctrl?.signal,
      })
    } finally {
      if (timer) window.clearTimeout(timer)
    }
  }

  let resp = await doFetch()
  if (resp.status === 401 && isLocalShell() && !path.startsWith('/api/login')) {
    const t = await ensureLocalSession(true)
    if (t) {
      setToken(t)
      resp = await doFetch()
    }
  }

  if (init.raw) return resp as unknown as T

  if (resp.status === 401) {
    if (!path.startsWith('/api/login')) unauthorizedHandler?.()
    const err = new Error('未登录或会话已过期') as ApiError
    err.status = 401
    throw err
  }
  if (!resp.ok) throw await extractError(resp)

  const text = await resp.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    return text as unknown as T
  }
}

/** 容错 JSON 解析（对应旧版 safeJson），不抛错 */
export async function apiSafe<T = unknown>(path: string, init: ApiInit = {}): Promise<{ ok: boolean; status: number; data: T | undefined }> {
  try {
    const data = await api<T>(path, init)
    return { ok: true, status: 200, data }
  } catch (e) {
    const err = e as ApiError
    return { ok: false, status: err.status ?? 0, data: undefined }
  }
}

/**
 * 带一次性票据的 SSE 地址换取（EventSource 不能带自定义头，JWT 不进 URL）。
 * 对应旧版 /api/logs/requests/ticket（GET）→ /api/logs/usage/stream?ticket= 的两步握手。
 */
export async function fetchSseTicket(ticketPath: string, streamPath: string, params: Record<string, string | number | undefined> = {}): Promise<string> {
  const { ticket } = await api<{ ticket: string }>(ticketPath)
  const q = new URLSearchParams({ ticket })
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  }
  return `${streamPath}?${q.toString()}`
}
