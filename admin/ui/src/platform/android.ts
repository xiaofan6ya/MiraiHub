/**
 * Android（APK）桥接。只在 `window.WBAndroid` 存在时启用，浏览器里全部是 no-op。
 *
 * 旧实现散在 `android/web/mobile_settings.js`（`window.WBMobileSettings`）和旧控制台
 * 移动端设置面板里：那套是「`build_android.py` 改写 `admin/console/index.html` 后由
 * WebView 加载」。后台前端合并后 `/admin` 由新版 Vue 承担（`MainActivity.java:145`
 * 加载的就是 `/admin?desktop=1&android=1`），旧面板不再被加载，所以这些能力必须在新版里接上。
 *
 * Java 侧 `MainActivity.MobileBridge` 的接口一个都没变
 * （`addJavascriptInterface(new MobileBridge(), "WBAndroid")`），这里只做类型化封装 +
 * 对「浏览器里调不到」的统一降级，调用点不需要写 `typeof window.WBAndroid`。
 */

/** `WBAndroid.status()` 返回的字段（对应 `MainActivity.MobileBridge.status()`）。 */
export interface AndroidStatus {
  /** 本地服务已注册到系统。 */
  registered?: boolean
  /** 正在启动。 */
  starting?: boolean
  /** 最近一次健康检查时间。 */
  last_healthy?: number | string
  /** 通知权限 + `local-api` 通道都没被关掉。 */
  notifications?: boolean
  /** 已忽略电池优化（后台不被杀）。 */
  battery_unrestricted?: boolean
  /** 系统限制后台运行（Android 9+）。 */
  background_restricted?: boolean
  /** 省电模式。 */
  power_save?: boolean
  /** 持有唤醒锁。 */
  wake_lock?: boolean
  /** 「保活」开关。 */
  keeper_enabled?: boolean
  interrupts?: number
  last_interrupt?: number | string
  /** 本地服务地址（`http://127.0.0.1:PORT`）。 */
  origin?: string
  error?: string
}

interface AndroidBridge {
  status(): string
  restartService(): void
  openSystemSettings(section: string): void
  revealLastExport(): void
  lastExportPath(): string
  saveText(request: string, filename: string, text: string, mime: string): boolean
  setTheme(theme: string): void
  sessionToken(): string
}

function bridge(): AndroidBridge | null {
  if (typeof window === 'undefined') return null
  const api = (window as unknown as { WBAndroid?: AndroidBridge }).WBAndroid
  return api ?? null
}

/** 当前是否跑在 APK 的 WebView 里。 */
export function isAndroid(): boolean {
  return bridge() !== null
}

/** 读本机服务状态；拿不到就返回空对象，调用方按「不可用」处理。 */
export function androidStatus(): AndroidStatus {
  const api = bridge()
  if (!api) return {}
  try {
    return JSON.parse(api.status() || '{}') as AndroidStatus
  } catch (error) {
    console.warn('[android] status 解析失败', error)
    return {}
  }
}

/** 重启本地服务（账号与设置保留）。 */
export function restartAndroidService(): void {
  bridge()?.restartService()
}

/** 打开系统设置页：通知权限 / 电池优化 / 自启动。 */
export function openAndroidSettings(section: 'notifications' | 'battery' | 'startup'): void {
  bridge()?.openSystemSettings(section)
}

/** 打开最近一次导出的文件所在位置。 */
export function revealLastExport(): void {
  bridge()?.revealLastExport()
}

/** 最近一次导出的可读路径；没导出过返回空串。 */
export function lastExportPath(): string {
  return bridge()?.lastExportPath() ?? ''
}

/**
 * 把文本交给系统文件保存器（APK 里 `window.saveText` / `WBAndroid.saveText`）。
 *
 * `request` 必须是 1–10 位数字（Java 侧正则校验），并发时只保留最后一次。
 * 返回 false 表示桥接拒绝（未受信任 / 已有导出在写 / 内容超 32MB）。
 */
export function saveTextOnAndroid(
  request: string,
  filename: string,
  text: string,
  mime: string,
): boolean {
  const api = bridge()
  if (!api) return false
  try {
    return api.saveText(request, filename, text, mime)
  } catch (error) {
    console.warn('[android] saveText 失败', error)
    return false
  }
}

/** 同步主题到原生窗口（状态栏配色跟着走）。 */
export function syncAndroidTheme(theme: 'light' | 'dark'): void {
  bridge()?.setTheme(theme)
}

/** 原生侧的会话令牌（WebView 首屏可能要它换取上一次登录态）。 */
export function androidSessionToken(): string {
  return bridge()?.sessionToken() ?? ''
}
