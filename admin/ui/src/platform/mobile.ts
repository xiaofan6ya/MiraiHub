/**
 * Android 外壳（APK）的本地会话桥 —— `window.WBMobile`。
 *
 * ## 为什么这个文件必须存在
 *
 * `MainActivity.checkWorkbench()` 的判据是：
 *
 * ```java
 * if (!window.WBMobile || typeof window.WBMobile.initialize !== 'function') return 'waiting';
 * WBMobile.initialize(token);
 * var app = document.getElementById('app');
 * return app && !app.classList.contains('hidden') ? 'ready' : 'waiting';
 * ```
 *
 * 也就是说**页面必须挂上 `window.WBMobile.initialize`**，否则 APK 永远停在
 * 「正在加载工作台」的启动页。旧实现是 `android/web/mobile.js`，随旧控制台
 * （`admin/console`）一起在 1.1.6 删除 —— 这里补回同样的入口。
 *
 * ## 令牌链路（安全模型不变）
 *
 * ```
 * android_runtime.session_token()   签一个短时 JWT（local_shell: true）
 *        ↓ Java evaluateJavascript
 * WBMobile.initialize(token)
 *        ↓ fetch('/api/login/session', {X-Admin-Token})
 * 服务端下发 HttpOnly 会话 Cookie
 *        ↓ 令牌立即丢弃
 * ```
 *
 * 令牌不落 localStorage、不挂 window、不进日志 —— 与旧控制台的 `ensureLocalSession()`
 * 保持同一条链路。换完 Cookie 后整页重载一次：路由守卫带着会话启动，直接进后台
 * 而不是停在登录页。用 `sessionStorage` 打标，换 Cookie 失败时不会无限重载。
 */

const RECHECK_KEY = 'mh.mobileEntered'
const TOUCH_EVENT = 'wb-shell-resume'

/** 用桥接令牌换 HttpOnly 会话 Cookie；失败抛错，由调用方决定是否放行页面。 */
async function exchangeSession(token: string): Promise<void> {
  if (!token) throw new Error('本地连接凭据未准备好')
  const response = await fetch('/api/login/session', {
    headers: { 'X-Admin-Token': token },
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (!response.ok) throw new Error('本地连接凭据校验失败（HTTP ' + response.status + '）')
}

/* ------------------------------------------------------------------------- *
 * 文件保存：APK 里走系统文件选择器
 * ------------------------------------------------------------------------- */

let saveCounter = 0
const pending = new Map<string, { resolve: (v: SaveResult) => void; reject: (e: Error) => void; timer: number }>()

export interface SaveResult {
  ok: true
  path: string
  message: string
}

/** Java 侧保存完成后回调进来（`WBMobile.saveResult(id, ok, message)`）。 */
function saveResult(id: string, ok: boolean, message: string): void {
  const slot = pending.get(String(id))
  if (!slot) return
  pending.delete(String(id))
  window.clearTimeout(slot.timer)
  if (ok) {
    const path = (window as unknown as { WBAndroid?: { lastExportPath?: () => string } })
      .WBAndroid?.lastExportPath?.() || ''
    slot.resolve({ ok: true, path, message: message || '' })
  } else {
    slot.reject(new Error(message || '文件未保存'))
  }
}

function saveText(filename: string, text: string, mime: string): Promise<SaveResult> {
  return new Promise<SaveResult>((resolve, reject) => {
    const bridge = (window as unknown as { WBAndroid?: { saveText?: (...a: unknown[]) => boolean } }).WBAndroid
    if (!bridge?.saveText) {
      reject(new Error('当前安装包不支持保存文件，请更新 APK'))
      return
    }
    const id = String(++saveCounter)
    const timer = window.setTimeout(() => {
      pending.delete(id)
      reject(new Error('文件保存超时，请重试'))
    }, 300_000)
    pending.set(id, { resolve, reject, timer })
    if (!bridge.saveText(id, filename, text, mime)) {
      saveResult(id, false, '无法开始保存，请等待其他导出完成')
    }
  })
}

/* ------------------------------------------------------------------------- *
 * 入口
 * ------------------------------------------------------------------------- */

interface WBMobileApi {
  initialize: (token: string) => void
  saveText: typeof saveText
  saveResult: typeof saveResult
  revealLastExport: () => void
  lastExportPath: () => string
}

let initializing = false

function initialize(token: string): void {
  if (initializing) return
  initializing = true
  void (async () => {
    try {
      await exchangeSession(token)
    } catch (error) {
      console.warn('[mobile] 本地会话建立失败', error)
      initializing = false
      return
    }
    let entered = ''
    try {
      entered = sessionStorage.getItem(RECHECK_KEY) || ''
    } catch {
      /* 隐私模式：当作没进过，靠下面这次重载兜住 */
    }
    if (!entered) {
      try {
        sessionStorage.setItem(RECHECK_KEY, '1')
      } catch {
        /* 同上 */
      }
      // 必须用 reload，不能 `location.replace('/admin/')`：
      // `MainActivity.route()` 的判据是 `isAdmin()`，而它要求路径**精确等于**
      // `/admin`。多一个尾斜杠就成了站外链接，被 `external()` 交给系统浏览器
      // ——表现就是「APK 一打开就跳到浏览器，地址栏是 127.0.0.1:8790/admin/」。
      // reload 不改路径，服务端拿 Cookie 重新出 index.html，落在站内。
      location.reload()
      return
    }
    initializing = false
    window.dispatchEvent(new Event(TOUCH_EVENT))
  })()
}

const api: WBMobileApi = {
  initialize,
  saveText,
  saveResult,
  revealLastExport: () => {
    (window as unknown as { WBAndroid?: { revealLastExport?: () => void } }).WBAndroid?.revealLastExport?.()
  },
  lastExportPath: () => {
    return (window as unknown as { WBAndroid?: { lastExportPath?: () => string } })
      .WBAndroid?.lastExportPath?.() || ''
  },
}

/** 幂等注册：main.ts 与 login/main.ts 都会 import 本模块。 */
export function installMobileBridge(): void {
  if (typeof window === 'undefined') return
  const scope = window as unknown as { WBMobile?: WBMobileApi }
  // 只在真的跑在 APK 里时才挂（浏览器里 `WBAndroid` 不存在，挂了也没有意义，
  // 而维护页用 `window.WBMobile` 判断「这是 APK」，别在网页里说谎）。
  const isAndroidShell = Boolean((window as unknown as { WBAndroid?: unknown }).WBAndroid)
  if (!isAndroidShell) return
  scope.WBMobile = api
}

/**
 * 主动自举：不依赖 Java 侧 `checkWorkbench()` 来调 `initialize`。
 *
 * 为什么需要：Java 的轮询有一串前置条件（`isAdmin(url)`、`LocalApiService.token`
 * 已就绪、页面已 finished），任何一条不满足就静默跳过，页面就会一直停在
 * 「正在加载」的骨架上 —— 实测在 Android 9 上就是这样。
 * 这里自己拿一次桥接令牌去换 Cookie，把主动权握在页面手里。
 */
function bootstrapSession(attempt = 0): void {
  const bridge = (window as unknown as { WBAndroid?: { sessionToken?: () => string } }).WBAndroid
  if (!bridge?.sessionToken) return
  let token = ''
  try {
    token = bridge.sessionToken() || ''
  } catch {
    return
  }
  if (!token) {
    // 本地服务可能还在启动，令牌尚未签发；退避重试几次就够。
    if (attempt < 12) window.setTimeout(() => bootstrapSession(attempt + 1), 1000 + attempt * 250)
    return
  }
  initialize(token)
}

installMobileBridge()

if (typeof window !== 'undefined' && (window as unknown as { WBAndroid?: unknown }).WBAndroid) {
  // 让首帧先渲染出来再换会话：换完会整页重载，太早触发会闪一下空白。
  window.setTimeout(() => bootstrapSession(), 600)
}
