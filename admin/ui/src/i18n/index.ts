import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { zhCN, type MessageKey } from './messages/zh-CN'
import { enUS } from './messages/en-US'

export type { MessageKey } from './messages/zh-CN'
import type { GroupKey, PageKey } from '@/router/pages'

/**
 * 轻量 i18n（不引入 vue-i18n / i18next）。
 *
 * 设计：
 *   · 字典是扁平 key（`page.accounts.title`），`MessageKey` 由 zh-CN 推导；
 *   · `t()` 在**调用时**读取 `locale` ref，所以写在 computed / render 里的文案
 *     会随语言切换自动重算；模块级常量不会（定义列表时用 computed 包一层）；
 *   · 语言存 localStorage `mh.locale`，默认 zh-CN；跨标签页用 storage 事件同步；
 *   · `document.documentElement.lang` 跟随，日期/数字格式用 localeTag。
 *
 * 不翻译后端下发的 label / desc —— 那是运行配置的内容，不是界面文案。
 */

export type Locale = 'zh-CN' | 'en-US'
export type Translate = (key: MessageKey, params?: Record<string, string | number>) => string

export const STORAGE_KEY = 'mh.locale'
export const DEFAULT_LOCALE: Locale = 'zh-CN'

export const LOCALES: ReadonlyArray<{ value: Locale; label: string }> = [
  { value: 'zh-CN', label: '中文' },
  { value: 'en-US', label: 'English' },
]

const DICTIONARIES: Record<Locale, Record<MessageKey, string>> = {
  'zh-CN': zhCN,
  'en-US': enUS,
}

export function isLocale(value: unknown): value is Locale {
  return value === 'zh-CN' || value === 'en-US'
}

/** 解析持久化值：坏值/空值都回落到默认语言（默认中文，不跟浏览器语言猜）。 */
export function resolveInitialLocale(saved?: string | null): Locale {
  return isLocale(saved) ? saved : DEFAULT_LOCALE
}

function readStoredLocale(): Locale {
  try {
    return resolveInitialLocale(localStorage.getItem(STORAGE_KEY))
  } catch {
    /* 隐私模式：localStorage 不可用，按默认语言。 */
    return DEFAULT_LOCALE
  }
}

const locale = ref<Locale>(readStoredLocale())

/** 当前语言（只读）。 */
export const currentLocale: ComputedRef<Locale> = computed(() => locale.value)

/** BCP-47 标签，给 toLocaleString / toLocaleTimeString 用。 */
export const localeTag: ComputedRef<string> = computed(() => locale.value)

function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  )
}

/** 取文案；未知 key 返回 key 本身（不会渲染成 undefined）。 */
export const t: Translate = (key, params) => {
  const table: Record<string, string> = DICTIONARIES[locale.value] || zhCN
  const template = table[key] ?? zhCN[key]
  if (template === undefined) return String(key)
  return interpolate(template, params)
}

function applyDocumentLang(value: Locale): void {
  if (typeof document === 'undefined') return
  document.documentElement.lang = value
}

/** 切换语言：立即生效、写入 localStorage、同步 <html lang>。 */
export function setLocale(next: Locale): void {
  if (!isLocale(next)) return
  locale.value = next
  applyDocumentLang(next)
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* 存不了不影响当前会话。 */
  }
}

/** 在挂载应用之前调用，让首帧的 <html lang> 与文案一致。 */
export function initLocale(): void {
  applyDocumentLang(locale.value)
  if (typeof window === 'undefined') return
  // 其它标签页改了语言：这边跟着变，不写回（避免回声）。
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY) return
    const next = resolveInitialLocale(event.newValue)
    if (next === locale.value) return
    locale.value = next
    applyDocumentLang(next)
  })
}

export interface UseI18n {
  t: Translate
  locale: ComputedRef<Locale>
  localeTag: ComputedRef<string>
  setLocale: (next: Locale) => void
  locales: ReadonlyArray<{ value: Locale; label: string }>
}

export function useI18n(): UseI18n {
  return { t, locale: currentLocale, localeTag, setLocale, locales: LOCALES }
}

/* ------------------------------------------------------------------ *
 * 导航文案：唯一真值仍是 router/pages.ts 的中文页面表（测试会逐项比对），
 * 这里只负责把 page key / group key 映射到当前语言。
 * ------------------------------------------------------------------ */

const PAGE_TITLE_KEY: Record<PageKey, MessageKey> = {
  overview: 'nav.page.overview',
  sentinel: 'nav.page.sentinel',
  stats: 'nav.page.stats',
  platforms: 'nav.page.platforms',
  gateway: 'nav.page.gateway',
  accounts: 'nav.page.accounts',
  credits: 'nav.page.credits',
  journal: 'nav.page.journal',
  growth: 'nav.page.growth',
  usage: 'nav.page.usage',
  logs: 'nav.page.logs',
  keys: 'nav.page.keys',
  ip: 'nav.page.ip',
  models: 'nav.page.models',
  market: 'nav.page.market',
  groups: 'nav.page.groups',
  schedules: 'nav.page.schedules',
  client: 'nav.page.client',
  sync: 'nav.page.sync',
  alerts: 'nav.page.alerts',
  agent: 'nav.page.agent',
  openentry: 'nav.page.openentry',
  settings: 'nav.page.settings',
  maintenance: 'nav.page.maintenance',
}

const GROUP_TITLE_KEY: Record<GroupKey, MessageKey> = {
  overview: 'nav.group.overview',
  platforms: 'nav.group.platforms',
  accounts: 'nav.group.accounts',
  usage: 'nav.group.usage',
  config: 'nav.group.config',
  access: 'nav.group.access',
  agent: 'nav.group.agent',
  public: 'nav.group.public',
  system: 'nav.group.system',
}

export function pageTitle(key: PageKey): string {
  return t(PAGE_TITLE_KEY[key])
}

export function groupTitle(key: GroupKey): string {
  return t(GROUP_TITLE_KEY[key])
}

/** 供测试/诊断用：当前语言字典的 key 数量。 */
export function messageCount(target: Locale = locale.value): number {
  return Object.keys(DICTIONARIES[target] || {}).length
}

/** 只读 ref，给需要在模板外判断语言的极少数场景（如 storage 同步测试）。 */
export const localeRef: Ref<Locale> = locale
