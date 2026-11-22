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
