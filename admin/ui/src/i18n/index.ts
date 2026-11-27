import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { zhCN, type MessageKey } from './messages/zh-CN'

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
 *   · 首屏瘦身（第 5 轮）：只有默认语言 zh-CN 静态打进入口（它也是 MessageKey 的来源与缺词回退）；
 *     en-US 按需 `import()` 成独立 chunk。切换时**先加载完再切**（加载期间界面保持原语言，
 *     不会闪键名或半中半英）；已加载的语言切换仍是同步的。
 *     存的语言是 en-US 时，main.ts 等 `initLocale()` 加载完再挂载，首帧即英文。
 *     测试 / SSR 需要同步可用时用 `registerMessages('en-US', enUS)` 先注入（tests/setup.ts 已注入）。
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

export type Dictionary = Record<MessageKey, string>

/** 已加载的字典。zh-CN 静态在入口里；其它语言由 LOADERS 动态加载后写入。 */
const DICTIONARIES: Partial<Record<Locale, Dictionary>> = {
  'zh-CN': zhCN,
}

/** 非默认语言的加载器（独立 chunk，不进首屏）。 */
const LOADERS: Record<Locale, () => Promise<Dictionary>> = {
  'zh-CN': () => Promise.resolve(zhCN),
  'en-US': () => import('./messages/en-US').then((mod) => mod.enUS),
}

const inflight = new Map<Locale, Promise<Dictionary>>()

/** 语言字典是否已就绪（可同步切换）。 */
export function isLocaleLoaded(target: Locale): boolean {
  return DICTIONARIES[target] !== undefined
}

/** 同步注入字典（测试 / SSR / 预取完成后使用）。之后 setLocale(target) 是同步的。 */
export function registerMessages(target: Locale, messages: Dictionary): void {
  DICTIONARIES[target] = messages
}

/** 预加载某个语言（不切换）。失败时 reject，下一次调用会重试。 */
export function loadLocale(target: Locale): Promise<Dictionary> {
  const ready = DICTIONARIES[target]
  if (ready) return Promise.resolve(ready)
  const running = inflight.get(target)
  if (running) return running
  const task = LOADERS[target]()
    .then((messages) => {
      DICTIONARIES[target] = messages
      return messages
    })
    .finally(() => {
      inflight.delete(target)
    })
  inflight.set(target, task)
  return task
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

/** 用户想要的语言（localStorage）；字典没到之前界面先用默认语言，避免闪键名。 */
const storedLocale = readStoredLocale()
const locale = ref<Locale>(isLocaleLoaded(storedLocale) ? storedLocale : DEFAULT_LOCALE)
/** 正在加载、加载完就会切过去的语言（null = 没有）。语言切换控件可据此显示小转圈。 */
const pending = ref<Locale | null>(null)

/** 正在加载中的目标语言（只读）。 */
export const pendingLocale: ComputedRef<Locale | null> = computed(() => pending.value)

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

function commit(next: Locale, persist: boolean): void {
  locale.value = next
  applyDocumentLang(next)
  if (!persist) return
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* 存不了不影响当前会话。 */
  }
}

/**
 * 切到目标语言。字典已就绪 → 同步切换（返回已 resolve 的 promise）；
 * 未就绪 → 先加载，加载完且期间没有再切到别的语言才生效。加载失败保持原语言并 reject。
 */
function switchTo(next: Locale, persist: boolean): Promise<void> {
  if (isLocaleLoaded(next)) {
    pending.value = null
    commit(next, persist)
    return Promise.resolve()
  }
  pending.value = next
  return loadLocale(next).then(
    () => {
      // 加载期间用户又切了别的语言：以最后一次为准。
      if (pending.value !== next) return
      pending.value = null
      commit(next, persist)
    },
    (error: unknown) => {
      if (pending.value === next) pending.value = null
      throw error
    },
  )
}

/**
 * 切换语言：写入 localStorage、同步 <html lang>。
 * 已加载的语言立即生效；未加载的先下载字典再切（期间界面保持原语言）。
 * 返回值可以不管（旧调用 `setLocale(x)` 照常工作）；加载失败时 promise reject 并在控制台告警。
 */
export function setLocale(next: Locale): Promise<void> {
  if (!isLocale(next)) return Promise.resolve()
  const task = switchTo(next, true)
  task.catch((error: unknown) => {
    if (typeof console !== 'undefined') console.warn('[i18n] 语言包加载失败', next, error)
  })
  return task
}

let storageBound = false

/**
 * 在挂载应用之前调用，让首帧的 <html lang> 与文案一致。
 * 存的语言还没加载（en-US）时返回的 promise 在字典到达后 resolve；main.ts 等它再 mount。
 * 加载失败也 resolve（按默认语言显示，不阻塞启动）。
 */
export function initLocale(): Promise<void> {
  applyDocumentLang(locale.value)
  if (typeof window !== 'undefined' && !storageBound) {
    storageBound = true
    // 其它标签页改了语言：这边跟着变，不写回（避免回声）。
    window.addEventListener('storage', (event) => {
      if (event.key !== STORAGE_KEY) return
      const next = resolveInitialLocale(event.newValue)
      if (next === locale.value && pending.value === null) return
      switchTo(next, false).catch(() => undefined)
    })
  }
  const wanted = readStoredLocale()
  if (wanted === locale.value) return Promise.resolve()
  return switchTo(wanted, false).catch(() => undefined)
}

export interface UseI18n {
  t: Translate
  locale: ComputedRef<Locale>
  localeTag: ComputedRef<string>
  /** 正在加载的目标语言（null = 无）；切换控件可据此显示加载态。 */
  pendingLocale: ComputedRef<Locale | null>
  setLocale: (next: Locale) => Promise<void>
  locales: ReadonlyArray<{ value: Locale; label: string }>
}

export function useI18n(): UseI18n {
  return { t, locale: currentLocale, localeTag, pendingLocale, setLocale, locales: LOCALES }
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
  proxy: 'nav.page.proxy',
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

/** 供测试/诊断用：当前语言字典的 key 数量（未加载的语言为 0）。 */
export function messageCount(target: Locale = locale.value): number {
  return Object.keys(DICTIONARIES[target] || {}).length
}

/** 只读 ref，给需要在模板外判断语言的极少数场景（如 storage 同步测试）。 */
export const localeRef: Ref<Locale> = locale
