import { beforeEach, describe, expect, it } from 'vitest'
import {
  GROUPS,
  PAGES,
  PAGE_TITLES,
  PLATFORM_ONLY_PAGES,
  createAppRouter,
  groupEntry,
  isPageKey,
} from './index'

/**
 * 旧版 `admin/console/console-app.js:1264` 的 TABS —— 逐字抄写作为回归基准，
 * 新路由表的 key 必须与它**完全一致**（03-shell §1 / S04 验收）。
 */
const LEGACY_TABS = [
  'overview',
  'sentinel',
  'stats',
  'platforms',
  'accounts',
  'credits',
  'journal',
  'growth',
  'usage',
  'logs',
  'keys',
  'ip',
  'models',
  'market',
  'groups',
  'agent',
  'schedules',
  'client',
  'sync',
  'alerts',
  'gateway',
  'openentry',
  'settings',
  'maintenance',
]
/** 旧版 `_SUB_TAB_LABELS`（console-app.js:1388）。 */
const LEGACY_LABELS: Record<string, string> = {
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

const resetUrl = (url: string): void => {
  window.history.replaceState({}, '', url)
}

describe('路由表', () => {
  beforeEach(() => {
    resetUrl('/')
    localStorage.clear()
    // jsdom 没实现 scrollTo，scrollBehavior 会打印警告；测试里直接吞掉。
    window.scrollTo = () => undefined
  })

  it('有 25 个页面：24 个与旧版 TABS 一致，外加 /proxy（issue #39）', () => {
    expect(PAGES).toHaveLength(25)
    const keys = PAGES.map((page) => page.key)
    expect(keys).toContain('proxy')
    expect([...keys.filter((key) => key !== 'proxy')].sort()).toEqual([...LEGACY_TABS].sort())
    expect(new Set(LEGACY_TABS).size).toBe(24)
  })

  it('页面中文名与旧版 _SUB_TAB_LABELS 一致', () => {
    expect(Object.keys(PAGE_TITLES)).toHaveLength(25)
    for (const [key, label] of Object.entries(LEGACY_LABELS)) {
      expect(PAGE_TITLES[key as keyof typeof PAGE_TITLES]).toBe(label)
    }
    // 新增页只有这一处偏离旧版基线：/proxy = 出口代理（issue #39）。
    expect(PAGE_TITLES.proxy).toBe('出口代理')
  })

  it('9 个分组，顺序与 03-shell §1 的表一致', () => {
    expect(GROUPS.map((group) => group.title)).toEqual([
      '概览',
      '平台',
      '账号',
      '调用',
      '配置',
      '接入',
      '智能体',
      '公开入口',
      '系统',
    ])
    expect(GROUPS.map((group) => group.pages.map((page) => page.key))).toEqual([
      ['overview', 'sentinel', 'stats'],
      ['platforms', 'gateway'],
      ['accounts', 'credits', 'journal', 'growth'],
      ['usage', 'logs', 'keys', 'ip'],
      ['models', 'market', 'groups', 'schedules'],
      ['client', 'sync', 'alerts'],
      ['agent'],
      ['openentry'],
      ['settings', 'proxy', 'maintenance'],
    ])
  })

  it('路由 meta 带 group/title/icon/platformScoped，路径为 /key', () => {
    for (const page of PAGES) {
      expect(page.path).toBe('/' + page.key)
      expect(page.group).toBeTruthy()
      expect(page.icon).toBeTruthy()
      expect(typeof page.platformScoped).toBe('boolean')
    }
    const scoped = PAGES.filter((page) => page.platformScoped).map((page) => page.key)
    // 唯一真值：contracts/platform-context.md §8。
    expect(scoped).toEqual([
      'overview',
      'sentinel',
      'stats',
      'platforms',
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
      'sync',
    ])
    expect(scoped).not.toContain('gateway')
    expect(scoped).not.toContain('client')
    expect(scoped).not.toContain('settings')
    expect(scoped).not.toContain('maintenance')
  })

  it('成长计划只在 WorkBuddy 平台下存在（旧版 _PLATFORM_ONLY_TABS）', () => {
    expect(PLATFORM_ONLY_PAGES).toEqual({ growth: 'workbuddy' })
  })

  it('isPageKey 只认 24 个 key', () => {
    expect(isPageKey('keys')).toBe(true)
    expect(isPageKey('nope')).toBe(false)
    expect(isPageKey(undefined)).toBe(false)
  })

  it('分组入口回该组上次访问的页面，没有则第一个', () => {
    const accounts = GROUPS.find((group) => group.key === 'accounts')!
    expect(groupEntry(accounts)).toBe('accounts')
    localStorage.setItem('mh.lastTab.accounts', 'journal')
    expect(groupEntry(accounts)).toBe('journal')
    // 不属于该组的值不能把用户带到别的组
    localStorage.setItem('mh.lastTab.accounts', 'overview')
    expect(groupEntry(accounts)).toBe('accounts')
  })
})

describe('旧链接兼容', () => {
  beforeEach(() => {
    window.scrollTo = () => undefined
  })

  it('?tab=keys 启动时重定向到 /keys', async () => {
    resetUrl('/?tab=keys')
    const router = createAppRouter()
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/keys')
  })

  it('未知 tab 值不重定向', async () => {
    resetUrl('/?tab=nope')
    const router = createAppRouter()
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/overview')
  })

  it('未知路由回到 /overview', async () => {
    resetUrl('/')
    const router = createAppRouter()
    await router.push('/definitely-not-a-page')
    expect(router.currentRoute.value.path).toBe('/overview')
  })

  it('根路径落在 /overview', async () => {
    resetUrl('/')
    const router = createAppRouter()
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/overview')
  })
})
