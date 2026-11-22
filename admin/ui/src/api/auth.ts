/**
 * 登录态与 401 的统一出口。
 *
 * 为什么单独一层：http.ts 要能触发"回登录页"，而外壳（S04）又想用自己的
 * 路由跳转接管 —— 直接 import 会形成 http → router/store → http 的循环依赖。
 * 这里只提供一个可注册的回调，谁挂载外壳谁调用 setUnauthorizedHandler()。
 *
 * 后端事实（admin/server.py）：
 *   · 未登录访问 /admin 时服务端只下发 admin/static/login.html，**不读 next 参数**；
 *   · login.html 登录成功后执行 location.replace("/admin")，查询串会丢。
 * 所以"登录后回到原页面"只能靠浏览器侧暂存：这里把当前 hash 写进
 * sessionStorage('mh.nextHash')，跳转仍然只去 /admin（旧后端不支持 next）。
 * 新版外壳启动时调用 consumeNextHash() 即可恢复。
 */

/** 旧后端只认这个路径：没有会话就下发登录壳。 */
export const LOGIN_PATH = '/admin'

/** 未登录时的 hash 暂存键（外壳启动时消费一次）。 */
export const NEXT_HASH_STORAGE_KEY = 'mh.nextHash'

export type UnauthorizedHandler = () => void

let handler: UnauthorizedHandler | null = null
let redirecting = false

/** 外壳注册自己的跳转逻辑（通常是 router.replace + 提示）。传 null 恢复默认行为。 */
export function setUnauthorizedHandler(fn: UnauthorizedHandler | null): void {
  handler = fn
  redirecting = false
}

/** 仅测试用：把"正在跳转"标记复位。 */
export function resetUnauthorizedState(): void {
  redirecting = false
}

function safeSessionStorage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    // 隐私模式下访问 sessionStorage 会抛异常
    return null
  }
}

/** 记下当前 hash（登录后由外壳消费）。传入自定义值时不读 location。 */
export function rememberNextHash(hash?: string): void {
  try {
    const value = hash ?? (typeof location === 'undefined' ? '' : location.hash || '')
    const storage = safeSessionStorage()
    if (!storage) return
    if (value) storage.setItem(NEXT_HASH_STORAGE_KEY, value)
    else storage.removeItem(NEXT_HASH_STORAGE_KEY)
  } catch {
    /* 存不了不影响跳转 */
  }
}

/** 取出并清空暂存的 hash（没有就返回空串）。 */
export function consumeNextHash(): string {
  try {
    const storage = safeSessionStorage()
    if (!storage) return ''
    const value = storage.getItem(NEXT_HASH_STORAGE_KEY) || ''
    storage.removeItem(NEXT_HASH_STORAGE_KEY)
    return value
  } catch {
    return ''
  }
}

export interface RedirectOptions {
  /** 是否把当前 hash 记下来供登录后恢复，默认 true。 */
  keepNext?: boolean
}

/**
 * 回登录页：一律 location.replace('/admin')（老后端不认识 next 参数）。
 *
 * 用 replace 而不是 assign：401 之后按浏览器"后退"不该再回到一个必然 401 的
 * 页面，那样只会闪一下又被踢回来。
 */
export function redirectToLogin(options: RedirectOptions = {}): void {
  if (options.keepNext !== false) rememberNextHash()
  if (redirecting) return
  redirecting = true
  if (typeof location !== 'undefined' && typeof location.replace === 'function') {
    location.replace(LOGIN_PATH)
  }
}

/**
 * 401 的统一处理：先交给注册过的处理器，没注册才走默认跳转。
 *
 * 处理器应当幂等：一个页面并发 3 个请求同时 401 时会调用 3 次。
 */
export function onUnauthorized(): void {
  if (handler) {
    try {
      handler()
    } catch (error) {
      console.error('[auth] unauthorized handler failed', error)
      redirectToLogin()
    }
    return
  }
  redirectToLogin()
}
