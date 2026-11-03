import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, clearToken, getToken, setToken, setUnauthorizedHandler, type ApiError } from '../api/client'
import { browserFingerprint } from '../utils/fingerprint'
import { markDesktopModeFromUrl } from '../bridges/native'

interface AuthCtx {
  token: string | null
  authed: boolean
  /** 一次性 URL token（桌面托盘"浏览器打开"）捕获完成 */
  ready: boolean
  /** 尚未创建管理员账号：需要走 /setup 引导（桌面端 / 安卓端重点路径） */
  needsSetup: boolean
  /** 重新拉取初始化状态（创建账号后调用） */
  refreshSetup: () => Promise<boolean>
  /** 直接采纳一个已验证的令牌（初始化向导创建成功后用） */
  adoptToken: (t: string) => void
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<AuthCtx>({
  token: null,
  authed: false,
  ready: false,
  needsSetup: false,
  refreshSetup: async () => false,
  adoptToken: () => {},
  login: async () => {},
  logout: async () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const [needsSetup, setNeedsSetup] = useState(false)

  const refreshSetup = useCallback(async () => {
    try {
      const s = await api<{ configured: boolean; needs_setup: boolean }>('/api/setup/state', {
        skipPlatform: true,
      })
      const need = !s.configured && !!s.needs_setup
      setNeedsSetup(need)
      return need
    } catch {
      // 读不到状态（旧版本后端 / 网络抖动）时按「无需初始化」处理：
      // 让正常的登录流程去报错，不要把可用部署误踢到引导页。
      setNeedsSetup(false)
      return false
    }
  }, [])

  useEffect(() => {
    markDesktopModeFromUrl()
    // 桌面托盘 ?token= 一次性注入：取用后从地址栏清除
    const q = new URLSearchParams(window.location.search)
    const urlToken = q.get('token')
    if (urlToken) {
      setToken(urlToken)
      q.delete('token')
      const rest = q.toString()
      window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}`)
    }
    setTokenState(getToken())
    setReady(true)
    setUnauthorizedHandler(() => {
      clearToken()
      setTokenState(null)
    })
    void refreshSetup()
    return () => setUnauthorizedHandler(null)
  }, [refreshSetup])

  const login = useCallback(async (username: string, password: string) => {
    const form = new URLSearchParams({ username, password })
    try {
      const resp = await api<{ token: string }>('/api/login', {
        method: 'POST',
        form,
        skipPlatform: true,
        headers: { 'X-Browser-Fingerprint': browserFingerprint() },
      })
      if (!resp?.token) throw new Error('登录响应缺少 token')
      setToken(resp.token)
      setTokenState(resp.token)
      setNeedsSetup(false)
    } catch (e) {
      // 后端用 503 + setup_required 表示「还没创建管理员账号」，
      // 这里先记下状态，抛给页面去跳引导。
      if ((e as ApiError).code === 'setup_required') setNeedsSetup(true)
      throw e
    }
  }, [])

  const logout = useCallback(async () => {
    try {
      await api('/api/login/logout', { method: 'POST', skipPlatform: true })
    } catch {
      /* 幂等，失败也继续本地登出 */
    }
    clearToken()
    setTokenState(null)
  }, [])

  const adoptToken = useCallback((t: string) => {
    setToken(t)
    setTokenState(t)
  }, [])

  const value = useMemo<AuthCtx>(
    () => ({ token, authed: !!token, ready, needsSetup, refreshSetup, adoptToken, login, logout }),
    [token, ready, needsSetup, refreshSetup, adoptToken, login, logout],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): AuthCtx {
  return useContext(Ctx)
}
