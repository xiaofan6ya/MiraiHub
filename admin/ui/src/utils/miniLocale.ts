/**
 * 极小的双语取词（给**登录页和 http 底层**用）。
 *
 * 为什么不用 `@/i18n`：登录页是独立入口（login.html），http.ts 也会被它打包；
 * `@/i18n` 会把中英两份完整字典（约 3000 个键）一起带进登录页的 chunk，首屏白白多下几百 KB。
 * 这里只读语言偏好（与 i18n 同一个 localStorage 键 `mh.locale`），不带字典。
 * 管理台里的组件请继续用 `t()`。
 */
export type MiniLocale = 'zh-CN' | 'en-US'

const STORAGE_KEY = 'mh.locale'

export function miniLocale(): MiniLocale {
  try {
    if (typeof localStorage !== 'undefined' && localStorage.getItem(STORAGE_KEY) === 'en-US') return 'en-US'
  } catch {
    /* 隐私模式：按默认中文。 */
  }
  if (typeof document !== 'undefined' && document.documentElement.lang === 'en-US') return 'en-US'
  return 'zh-CN'
}

/** 按当前语言取一段文案。 */
export function pickText(zh: string, en: string): string {
  return miniLocale() === 'en-US' ? en : zh
}
