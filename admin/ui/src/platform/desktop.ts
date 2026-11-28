/**
 * pywebview（桌面版）桥接。只在 `window.pywebview` 存在时启用，浏览器里全部是 no-op。
 * 旧版能力来自 `admin/console/desktop.js`（`window.WBDesktop`）。
 */

interface PywebviewApi {
  [method: string]: (...args: unknown[]) => unknown
}

interface PywebviewBridge {
  pywebview?: { api?: PywebviewApi }
}

let nativeReady = false

function bridge(): PywebviewApi | null {
  if (typeof window === 'undefined') return null
  const api = (window as unknown as PywebviewBridge).pywebview?.api
  return api ?? null
}

/** 当前是否跑在 pywebview 桌面壳里。 */
export function isDesktop(): boolean {
  return bridge() !== null
}

/** 桌面壳是否已经握手完成（`pywebviewready` 之后）。 */
export function isDesktopReady(): boolean {
  return nativeReady
}

/** 调用桌面壳暴露的方法；浏览器里返回 null，不抛错。 */
export async function callDesktop<T = unknown>(
  method: string,
  ...args: unknown[]
): Promise<T | null> {
  const api = bridge()
  const fn = api?.[method]
  if (!fn) return null
  try {
    return (await fn.apply(api, args)) as T
  } catch (error) {
    console.warn('[desktop] 调用失败：' + method, error)
    return null
  }
}

/**
 * 初始化桌面壳：等一次 `pywebviewready`，成功后在 `<html>` 上打 native-window 标记
 * （旧版 desktop.js 用同一个类名控制窗口内边距与拖拽区）。
 */
export async function initDesktop(timeoutMs = 2000): Promise<boolean> {
  if (typeof window === 'undefined') return false
  if (nativeReady) return true
  if (!isDesktop()) {
    // 脚本可能比桌面壳晚就绪，等一小会儿再判定一次。
    await new Promise<void>((resolve) => {
      let done = false
      const finish = (): void => {
        if (done) return
        done = true
        window.removeEventListener('pywebviewready', finish)
        resolve()
      }
      window.addEventListener('pywebviewready', finish, { once: true })
      window.setTimeout(finish, timeoutMs)
    })
  }
  if (!isDesktop()) return false
  nativeReady = true
  document.documentElement.classList.add('native-window')
  return true
}

/** 最小化 / 最大化 / 关闭（桌面壳的窗口按钮用）。 */
export async function windowAction(action: 'minimize' | 'maximize' | 'close' | string): Promise<void> {
  await callDesktop('window_action', action)
}

/**
 * 打开「版本与更新」。桌面壳里交给原生更新对话框；
 * 浏览器里由调用方跳转 /maintenance（返回 false 表示没有原生对话框）。
 */
export async function openNativeUpdates(): Promise<boolean> {
  if (!isDesktop()) return false
  await callDesktop('check_updates')
  return true
}

/** 桌面壳检查更新（启动时静默跑一次，与旧版一致）。 */
export async function checkUpdates(): Promise<Record<string, unknown> | null> {
  return callDesktop<Record<string, unknown>>('check_updates')
}

/**
 * 桌面壳免登：用本机桥签发的 `local_shell` 令牌换一次会话 Cookie。
 *
 * ## 为什么必须存在
 *
 * `/admin` 页面本身在本地运行时直接放行（`admin/server.py:_console_allowed`），
 * 但 **API 全部要会话**。安卓壳有 `WBMobile.initialize(token)` 兜住，桌面壳的
 * 对应物（`pywebview.api.desktop_login`）在 1.1.6 换代时前端没人接 —— 于是
 * 第一拍心跳就 401 → 统一跳 `/admin` → 整页重载 → 又 401：用户看到的就是
 * 「一直闪 + 一直提示未登录」。
 *
 * 外壳必须在**心跳与会话检查之前** await 本函数。
 *
 * ## 401 跳转会丢 `?desktop=1`
 *
 * `location.replace('/admin')` 不带查询串，而壳的 `desktop_login` 校验本地
 * 控制台 URL。这里像旧版 desktop.js 一样先把 `desktop=1` 补回地址栏
 * （`history.replaceState`，不触发导航），再签令牌 —— 否则一次 401 之后
 * 就永远换不到令牌，死循环。
 */
let desktopSessionReady = false

export async function ensureDesktopSession(): Promise<boolean> {
  if (desktopSessionReady) return true
  // 快速判否：连 `window.pywebview` 都没有就绝不是桌面壳（浏览器 / 测试环境），
  // 直接返回，不给外壳的初始化加任何等待。
  if (!bridge()) return false

  // ⚠️ 桥是**半初始化**的：`window.pywebview.api` 可能已经存在，但底层
  // `_returnValuesCallbacks` 还没挂上，此时调用会抛
  // `Cannot read properties of undefined (reading '<id>')`。
  // 首次启动要解压 + 起服务 + 加载页面，2 秒的握手窗口根本不够 ——
  // 实测就是这么错过令牌、然后 401 循环的（用户看到「一直闪 + 一直提示未登录」）。
  // 这里改成「调用 + 退避重试」，以「真的拿到 token 并换到 Cookie」为成功判据，
  // 而不是「对象存在」。
  for (let attempt = 0; attempt < 6; attempt++) {
    await initDesktop(attempt === 0 ? 3000 : 1200)
    if (!isDesktop()) return false

    // 补回桌面标记（幂等）：401 重定向会把它丢掉，而壳侧 desktop_login
    // 会校验「当前是不是本地控制台 URL」。
    try {
      const url = new URL(location.href)
      if (url.pathname.startsWith('/admin') && url.searchParams.get('desktop') !== '1') {
        url.searchParams.set('desktop', '1')
        history.replaceState(null, '', url.pathname + '?' + url.searchParams.toString() + url.hash)
      }
    } catch { /* 解析不了就不补，交给壳侧判据 */ }

    let token: string | undefined
    try {
      const result = await callDesktop<{ token?: string } | null>('desktop_login')
      token = result?.token
    } catch (error) {
      console.warn('[desktop] 桥接签发本地令牌失败（第 ' + (attempt + 1) + ' 次）', error)
    }
    if (token) {
      try {
        const response = await fetch('/api/login/session', {
          headers: { 'X-Admin-Token': token },
          credentials: 'same-origin',
          cache: 'no-store',
        })
        if (response.ok) {
          desktopSessionReady = true
          return true
        }
        console.warn('[desktop] 本地会话换取失败：HTTP ' + response.status)
      } catch (error) {
        console.warn('[desktop] 本地会话初始化失败', error)
      }
    }
    // 桥还没热：退避后再试（400/700/1000/1300/1600ms）。
    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, 400 + attempt * 300)
    })
  }
  return false
}

/** 仅测试用：复位「桌面会话已就绪」标记。 */
export function resetDesktopSessionForTest(): void {
  desktopSessionReady = false
}
