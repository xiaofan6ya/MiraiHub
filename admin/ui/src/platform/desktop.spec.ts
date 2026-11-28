import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ensureDesktopSession,
  isDesktop,
  resetDesktopSessionForTest,
} from './desktop'

/**
 * 回归测试：桌面壳免登引导。
 *
 * 1.1.6 换代后 `pywebview.api.desktop_login` 在前端没有任何调用方 —— 工作台
 * 页面能开（本地运行时放行），但 API 全 401 → 统一跳 /admin → 整页重载 →
 * 又 401，用户看到「一直闪 + 一直提示未登录」。这组测试钉住：
 *   1. 桌面壳里用桥签发的 local_shell 令牌换 /api/login/session；
 *   2. 401 跳转丢掉的 `?desktop=1` 会在换令牌前补回地址栏；
 *   3. 非桌面环境完全 no-op。
 */
type FetchMock = ReturnType<typeof vi.fn>

function installBridge(token: string | null): void {
  ;(window as unknown as { pywebview?: unknown }).pywebview = {
    api: {
      desktop_login: () => (token === null ? undefined : { token }),
    },
  }
}

describe('桌面壳免登引导', () => {
  let fetchMock: FetchMock

  beforeEach(() => {
    resetDesktopSessionForTest()
    history.replaceState(null, '', '/admin')
    document.documentElement.classList.remove('native-window')
    fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      return new Response('{}', {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    delete (window as unknown as { pywebview?: unknown }).pywebview
  })

  it('桌面壳里用 local_shell 令牌换会话 Cookie', async () => {
    installBridge('local-shell-token')
    expect(isDesktop()).toBe(true)
    const ok = await ensureDesktopSession()
    expect(ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/login/session',
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-Admin-Token': 'local-shell-token' }),
        credentials: 'same-origin',
      }),
    )
    // 幂等：就绪后不再重复换取。
    fetchMock.mockClear()
    await expect(ensureDesktopSession()).resolves.toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('401 重定向丢掉的 ?desktop=1 会在签令牌前补回', async () => {
    history.replaceState(null, '', '/admin')
    installBridge('local-shell-token')
    await ensureDesktopSession()
    expect(location.pathname).toBe('/admin')
    expect(location.search).toContain('desktop=1')
  })

  it('桥没给令牌时返回 false 且不发请求', async () => {
    installBridge(null)
    await expect(ensureDesktopSession()).resolves.toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('浏览器环境（无桥）完全 no-op', async () => {
    delete (window as unknown as { pywebview?: unknown }).pywebview
    await expect(ensureDesktopSession()).resolves.toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
