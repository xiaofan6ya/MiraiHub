/**
 * 登录态 / 登出 / 登录设备（踢下线、拉黑）。
 *
 * 接口与方法逐条对齐旧版（admin/console/console-app.js）：
 *   · 启动登录态检查：GET /api/accounts/probe —— 旧版 enterApp 用它判断"是否已登录"，
 *     只验凭据不碰账号表（O(1)），401/403 时旧版显示登录卡片；
 *   · logout()：POST /api/login/logout，成功后 location.replace('/admin')（旧版 1184 行）；
 *   · 登录设备：GET /api/sessions、POST /api/sessions/{sid}/revoke、
 *     POST /api/sessions/revoke-others、POST /api/sessions/{sid}/ban、
 *     DELETE /api/sessions/ban/{ban_id}（旧版 openSessionsPanel 一整段）。
 *
 * 后端实现见 admin/routers/sessions.py 与 admin/server.py:713/873/902。
 */
import { http } from './http'
import { ApiError } from './http'
import { redirectToLogin } from './auth'

/** 会话行（admin/session_store.py: list_sessions 的返回结构，字段原样保留）。 */
export interface SessionRow {
  sid: string
  /** 就是当前这台设备（后端按 Cookie 里的 sid 比对）。 */
  current: boolean
  /** active | revoked | expired */
  state: string
  label: string
  ip: string
  /** 设备指纹前 12 位，仅用于排障。 */
  fp: string
  created_at: string | null
  last_seen_at: string | null
  expires_at: string | null
  revoked_at: string | null
  revoked_reason: string
}

/** 已拉黑的设备（admin/session_store.py: list_bans）。 */
export interface DeviceBan {
  id: number
  label: string
  ip: string
  fp: string
  reason: string
  created_at: string | null
  revoked_at: string | null
}

/** GET /api/sessions 的完整响应。 */
export interface SessionsPayload {
  ok: boolean
  /** 是否允许多设备同时在线（SESSION_SINGLE 的取反）。 */
  multi_device: boolean
  /** 0 表示不限。 */
  max_devices: number
  /** 在线设备数。 */
  active: number
  items: SessionRow[]
  bans: DeviceBan[]
}

/**
 * 登录态检查。
 *
 * 返回 false 只在"凭证无效"（401/403）时出现；网络异常照旧抛出，
 * 由调用方决定是重试还是提示"网络异常"（旧版对网络异常也不清会话）。
 */
export async function checkSession(): Promise<boolean> {
  try {
    const payload = await http<{ ok?: boolean }>('GET', '/api/accounts/probe')
    return payload ? payload.ok !== false : true
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return false
    throw error
  }
}

/**
 * 用当前 Cookie 续期一次会话（GET /api/login/session）。
 *
 * 桌面版/安卓版桥接令牌换 Cookie 也走这个端点，但令牌只能由外壳递进来，
 * 这里只负责"凭已有 Cookie 续期"这一件事。
 */
export async function renewSession(): Promise<void> {
  await http('GET', '/api/login/session')
}

export interface LogoutOptions {
  /** 默认 true：调用成功与否都回登录页（旧版行为）。 */
  redirect?: boolean
}

/**
 * 真实退出：服务端把会话行置为 revoked 并删 Cookie。
 *
 * 退出必须幂等：即使请求失败也要放行到登录页 —— 否则用户被困在一个
 * 已经没用的会话里（旧版用 try/catch 吞掉异常后照样 replace）。
 */
export async function logout(options: LogoutOptions = {}): Promise<void> {
  try {
    await http('POST', '/api/login/logout')
  } catch (error) {
    console.warn('[session] 退出请求失败，仍然回到登录页', error)
  }
  if (options.redirect !== false) redirectToLogin({ keepNext: false })
}

/** 登录设备列表。includeRevoked 对应后端 include_revoked=1。 */
export function listSessions(includeRevoked = false): Promise<SessionsPayload> {
  return http<SessionsPayload>('GET', '/api/sessions', {
    query: includeRevoked ? { include_revoked: 1 } : undefined,
  })
}

/** 踢掉一台设备：只废掉它当前这次登录，它下次还能登。 */
export function revokeSession(sid: string): Promise<{ ok: boolean; sid: string }> {
  return http('POST', '/api/sessions/' + encodeURIComponent(sid) + '/revoke')
}

/** 踢掉除本机外的全部设备。 */
export function revokeOtherSessions(): Promise<{ ok: boolean; revoked: number }> {
  return http('POST', '/api/sessions/revoke-others')
}

/** 拉黑某台设备：它之后的所有登录都会被拒（登录接口签发会话前就查）。 */
export function banSession(
  sid: string,
  reason = '手动拉黑',
): Promise<{ ok: boolean; ban_id: number; label: string }> {
  return http('POST', '/api/sessions/' + encodeURIComponent(sid) + '/ban', { body: { reason } })
}

/** 按设备指纹拉黑（设备还没登录过、只知道指纹时用）。 */
export function banFingerprint(fp: string, reason = '手动拉黑指纹'): Promise<{ ok: boolean; ban_id: number }> {
  return http('POST', '/api/sessions/ban-fp', { body: { fp, reason } })
}

/** 解除拉黑。 */
export function unbanDevice(banId: number): Promise<{ ok: boolean }> {
  return http('DELETE', '/api/sessions/ban/' + banId)
}

/** 登录设备面板标题里的"在线 / 上限"文案，避免各页面自己拼。 */
export function sessionsSummary(payload: SessionsPayload | null): string {
  if (!payload) return ''
  const online = payload.active || 0
  const cap = payload.max_devices ? '（上限 ' + payload.max_devices + '）' : ''
  return '当前 ' + online + ' 台在线' + cap
}
