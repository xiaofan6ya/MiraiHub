
/**
 * 页面表：docs/ui-v2/03-shell.md §1（24 个页面 / 9 个分组）。
 * 侧栏、页头页签、命令面板、面包屑全部从这里生成，不要手写第二份。
 * 这个文件**不 import 任何组件**（避免 router ↔ layout 的循环依赖）。
 *
 * 页面 key 与旧版 `admin/console/console-app.js:1264` 的 TABS 完全一致，
 * 中文名与 `_SUB_TAB_LABELS`（同文件 1388 行）完全一致。
 */

/** 9 个分组：顺序、图标、组内页面顺序都按 03-shell §1 的表。 */
export const PAGE_GROUPS = [
  {
    key: 'overview',
    title: '概览',
    icon: 'LayoutDashboard',
    pages: ['overview', 'sentinel', 'stats'],
  },
  { key: 'platforms', title: '平台', icon: 'Boxes', pages: ['platforms', 'gateway'] },
  {
    key: 'accounts',
    title: '账号',
    icon: 'Users',
    pages: ['accounts', 'credits', 'journal', 'growth'],
  },
  { key: 'usage', title: '调用', icon: 'Activity', pages: ['usage', 'logs', 'keys', 'ip'] },
  {
    key: 'config',
    title: '配置',
    icon: 'SlidersHorizontal',
    pages: ['models', 'market', 'groups', 'schedules'],
  },
  { key: 'access', title: '接入', icon: 'Plug', pages: ['client', 'sync', 'alerts'] },
  { key: 'agent', title: '智能体', icon: 'Bot', pages: ['agent'] },
  { key: 'public', title: '公开入口', icon: 'DoorOpen', pages: ['openentry'] },
  { key: 'system', title: '系统', icon: 'Settings', pages: ['settings', 'maintenance'] },
] as const

export type GroupKey = (typeof PAGE_GROUPS)[number]['key']
export type PageKey = (typeof PAGE_GROUPS)[number]['pages'][number]

/** 页面中文名（与旧版 `_SUB_TAB_LABELS` 一致，测试会逐个比对）。 */
export const PAGE_TITLES: Record<PageKey, string> = {
  overview: '概览',
  sentinel: '风控哨兵',
  stats: '数据看板',
  platforms: '平台管理',
  gateway: '平台网关',
  accounts: '账号池',
  credits: '积分到期',
  journal: '积分流水',
  growth: '成长计划',
  usage: '使用记录',
  logs: '用量日志',
  keys: 'API Keys',
  ip: 'IP 管理',
  models: '模型配置',
  market: '模型广场',
  groups: '分组管理',
  schedules: '定时任务',
  client: '客户端参数',
  sync: '同步设置',
  alerts: '预警管理',
  agent: '智能体',
  openentry: '公开注册入口',
  settings: '设置',
  maintenance: '更新与备份',
}

/**
 * 只在某个**落库平台**下才有意义的页面（旧版 `_PLATFORM_ONLY_TABS`，console-app.js:1366）。
 * 真值在 S03 的 store 里（与 supports()/fallbackTab() 同一份），这里只做转出，
 * 避免导航层再维护一份「成长计划属于哪个平台」。
 */
export { PLATFORM_ONLY_TABS as PLATFORM_ONLY_PAGES } from '@/stores/platform'

/**
 * 顶栏显示「平台上下文选择器」而不是面包屑的页面（03-shell §2.2）。
 *
 * 依据旧版「切平台后这些页面会重取数」的证据：
 * · console-app.js:3314 的 `wb-platform-change` 监听：overview / platforms / accounts /
 *   usage / logs / credits / keys / models / schedules / sync；
 * · 06-pages-b.md:3「账号组所有页面都依赖顶栏的平台上下文」：+ journal / growth；
 * · 06-pages-d.md:64「客户端参数依赖平台上下文」：+ client；
 * · contracts/{sentinel,stats,gateway,model-market}.md 的「平台上下文」小节：+ sentinel / stats / gateway / market。
 * 其余页面（ip / groups / sync / alerts / agent / openentry / settings / maintenance）与其他平台无关。
 * 待 contracts/platform-context.md 落地后如有出入，只改这一处。
 */
export const PLATFORM_SCOPED_KEYS = [
  'overview',
  'sentinel',
  'stats',
  'platforms',
  'gateway',
  'accounts',
  'credits',
  'journal',
  'growth',
  'usage',
  'logs',
  'keys',
  'models',
  'market',
  'schedules',
  'client',
] as const satisfies readonly PageKey[]

const PLATFORM_SCOPED = new Set<string>(PLATFORM_SCOPED_KEYS)

export interface PageDef {
  /** 页面 key，也是路由 name */
  key: PageKey
  /** 所属分组 key（`mh.lastTab.<groupKey>` 的存储键） */
  groupKey: GroupKey
  /** 所属分组中文名 */
  group: string
  title: string
  icon: string
  path: string
  platformScoped: boolean
}

export interface GroupDef {
  key: GroupKey
  title: string
  icon: string
  pages: PageDef[]
}

const GROUP_TITLE = Object.fromEntries(PAGE_GROUPS.map((g) => [g.key, g.title])) as Record<
  GroupKey,
  string
>

/** 24 个页面，按分组顺序平铺（命令面板、页签过滤都用它）。 */
export const PAGES: PageDef[] = PAGE_GROUPS.flatMap((group) =>
  group.pages.map((key) => ({
    key,
    groupKey: group.key,
    group: group.title,
    title: PAGE_TITLES[key],
    icon: group.icon,
    path: '/' + key,
    platformScoped: PLATFORM_SCOPED.has(key),
  })),
)

export const GROUPS: GroupDef[] = PAGE_GROUPS.map((group) => ({
  key: group.key,
  title: group.title,
  icon: group.icon,
  pages: PAGES.filter((page) => page.groupKey === group.key),
}))

export const PAGES_BY_KEY: Record<PageKey, PageDef> = Object.fromEntries(
  PAGES.map((page) => [page.key, page]),
) as Record<PageKey, PageDef>

export const PAGE_KEYS = PAGES.map((page) => page.key)

export function isPageKey(value: unknown): value is PageKey {
  return typeof value === 'string' && PAGE_KEYS.includes(value as PageKey)
}

export function groupOf(key: PageKey): GroupDef {
  return GROUPS.find((group) => group.key === PAGES_BY_KEY[key].groupKey) ?? GROUPS[0]
}

/** 同组页签：只有组内多于 1 个页面时才需要渲染（03-shell §2.3）。 */
export function siblingPages(key: PageKey): PageDef[] {
  return groupOf(key).pages
}

const LAST_TAB_PREFIX = 'mh.lastTab.'

/** 侧栏点分组时回到该组上次访问的页面（03-shell §2.1）。 */
export function readLastTab(groupKey: GroupKey): PageKey | null {
  try {
    const raw = localStorage.getItem(LAST_TAB_PREFIX + groupKey)
    return isPageKey(raw) && PAGES_BY_KEY[raw].groupKey === groupKey ? raw : null
  } catch {
    return null
  }
}

export function rememberLastTab(groupKey: GroupKey, key: PageKey): void {
  try {
    localStorage.setItem(LAST_TAB_PREFIX + groupKey, key)
  } catch {
    /* 隐私模式下写不进去，不影响本次会话。 */
  }
}

/** 分组入口目标：上次访问的页面，没有则第一个页面。 */
export function groupEntry(group: GroupDef): PageKey {
  return readLastTab(group.key) ?? group.pages[0].key
}

/** 旧链接兼容：`?tab=xxx`（新版挂在 /admin/v2/ 下，hash 为空时生效）。 */
export function readLegacyTab(): PageKey | null {
  if (typeof window === 'undefined') return null
  const search = new URLSearchParams(window.location.search)
  let tab = search.get('tab')
  if (!tab) {
    // 也接受 `#/?tab=keys` 这种把参数写进 hash 的旧链接。
    const hash = window.location.hash
    const idx = hash.indexOf('?')
    if (idx >= 0) tab = new URLSearchParams(hash.slice(idx + 1)).get('tab')
  }
  return isPageKey(tab) ? tab : null
}

export function atRootHash(): boolean {
  if (typeof window === 'undefined') return true
  const hash = window.location.hash
  return hash === '' || hash === '#' || hash === '#/' || hash === '#/?'
}

/** 把 `?tab=` 从地址栏抹掉，避免登录后返回 / 刷新时再次被重定向往回跳。 */
export function clearLegacyQuery(): void {
  if (typeof window === 'undefined') return
  try {
    const url = window.location.pathname + window.location.hash
    window.history.replaceState(null, '', url)
  } catch {
    /* file:// 等环境忽略。 */
  }
}