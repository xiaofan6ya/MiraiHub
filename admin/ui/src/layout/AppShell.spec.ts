import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView } from 'vue-router'
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  NNotificationProvider,
} from 'naive-ui'
import { GROUPS, PAGES } from '@/router/pages'
import { createAppRouter } from '@/router'
import { resetShellState } from './shellState'

/**
 * 外壳冒烟：侧栏分组、24 个占位页可点、页签高亮、命令面板键盘、平台选择器显示规则。
 * 用假 fetch，避免测试真的打后端（http 层 401 会跳 /admin）。
 */
const PLATFORM_ITEMS = [
  {
    id: 'workbuddy',
    label: 'WorkBuddy',
    enabled: true,
    supports_local_scan: true,
    auth_mode: 'local-scan',
    default_realm: '',
    notes: '',
    icon: 'workbuddy',
    storage_platform: 'workbuddy',
    realm: '',
    realm_label: '',
    realms: [],
    proxied: false,
    rating: {},
    extra: {},
    managed: true,
    actions: [],
    quota_unit: 'credits',
    quota_unit_label: '',
    account_count: 12,
    realm_counts: {},
  },
  {
    id: 'trae-cn',
    label: 'Trae 国内版',
    enabled: true,
    supports_local_scan: true,
    auth_mode: 'local-scan',
    default_realm: 'cn',
    notes: '',
    icon: 'trae',
    storage_platform: 'trae',
    realm: 'cn',
    realm_label: '国内版',
    realms: [],
    proxied: false,
    rating: {},
    extra: {},
    managed: true,
    actions: [],
    quota_unit: 'credits',
    quota_unit_label: '',
    account_count: 3,
    realm_counts: { cn: 3 },
  },
]

function stubFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown) => {
      const url = String(input)
      let payload: unknown = { ok: true }
      if (url.includes('/api/platforms/current')) {
        payload = { platform: 'workbuddy', realm: '', raw: 'workbuddy' }
      } else if (url.includes('/api/platforms')) {
        payload = { items: PLATFORM_ITEMS, default: 'workbuddy', loader: {} }
      } else if (url.includes('/api/accounts/badges')) {
        payload = { expiring_accounts: 2, scope: 'all' }
      } else if (url.includes('/api/app/version')) {
        payload = { version: '1.1.4', releases_url: '' }
      } else if (url.includes('/api/app/releases')) {
        payload = { available: false, releases: [] }
      } else if (url.includes('/api/')) {
        // 其他页面（S06-S30）进入时会各自取数；外壳用例只关心路由可打开，
        // 让这些请求保持 pending，避免用假数据触发页面内的字段适配。
        return new Promise<Response>(() => undefined)
      }
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
}

/** 真实应用结构：providers → router-view → '/' 的 AppShell → 子路由页面。 */
const Host = defineComponent({
  components: {
    RouterView,
    NConfigProvider,
    NMessageProvider,
    NDialogProvider,
    NNotificationProvider,
  },
  template: `
    <n-config-provider>
      <n-notification-provider>
        <n-message-provider>
          <n-dialog-provider>
            <router-view />
          </n-dialog-provider>
        </n-message-provider>
      </n-notification-provider>
    </n-config-provider>
  `,
})

let wrapper: VueWrapper | null = null

/** @param width 视口宽度：1440 = 完整布局；1024 = 侧栏默认收起。 */
async function mountShell(width = 1440) {
  window.innerWidth = width
  const router = createAppRouter()
  await router.push('/overview')
  await router.isReady()
  wrapper = mount(Host, {
    global: { plugins: [createPinia(), router] },
    attachTo: document.body,
  })
  await flushPromises()
  window.dispatchEvent(new Event('resize'))
  await flushPromises()
  return { router, wrapper }
}

beforeEach(() => {
  localStorage.clear()
  resetShellState()
  stubFetch()
  // jsdom 未实现 scrollIntoView / scrollTo；真实浏览器不受影响。
  HTMLElement.prototype.scrollIntoView = () => undefined
  window.scrollTo = () => undefined
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  resetShellState()
  vi.unstubAllGlobals()
})

describe('应用外壳', () => {
  it('侧栏渲染 9 个分组（≥1280 完整布局）', async () => {
    const { wrapper } = await mountShell(1440)
    const items = wrapper.findAll('.side-item')
    expect(items).toHaveLength(9)
    expect(items.map((item) => item.text())).toEqual(GROUPS.map((group) => group.title))
    expect(wrapper.find('.side.collapsed').exists()).toBe(false)
  })

  it('1024–1279 侧栏默认收起成 64px，只显示图标', async () => {
    const { wrapper } = await mountShell(1024)
    expect(wrapper.find('.side.collapsed').exists()).toBe(true)
    expect(wrapper.findAll('.side-item')).toHaveLength(9)
    expect(wrapper.findAll('.item-label')).toHaveLength(0)
  })

  it('24 个页面都能点开，标题/侧栏高亮/文档标题同步', async () => {
    const { router, wrapper } = await mountShell(1440)
    // 已接入真实页面的 key；其余仍应是 _Placeholder（逐页交付，路由表始终完整）。
    const implemented = new Set([
      'maintenance', 'sync', 'alerts', 'groups',
      'ip', 'schedules', 'models', 'market',
      'keys', 'logs', 'usage', 'stats',
      'sentinel', 'gateway', 'platforms', 'overview',
      'journal', 'credits', 'growth', 'accounts',
    ])
    for (const page of PAGES) {
      await router.push('/' + page.key)
      // 已实现页面是 defineAsyncComponent：等动态 import 解析完再断言。
      await flushPromises()
      await flushPromises()
      const title = wrapper.find('h1.ph-title')
      expect(title.exists(), page.key).toBe(true)
      // 真实页面可能带平台名（如「成长计划 · WorkBuddy」），标题必须包含页面名。
      expect(title.text().trim()).toContain(page.title)
      expect(wrapper.find('.placeholder').exists(), page.key).toBe(!implemented.has(page.key))
      const active = wrapper.find('.side-item.active')
      expect(active.exists(), page.key).toBe(true)
      expect(active.text()).toBe(page.group)
      expect(document.title).toBe(page.title + ' · MiraiHub')
    }
  })

  it('同组页签只在该组多于 1 页时出现，点击可切换', async () => {
    const { router, wrapper } = await mountShell(1440)
    await router.push('/accounts')
    await flushPromises()
    const tabs = wrapper.findAll('.ph-tab')
    expect(tabs.map((tab) => tab.text())).toEqual(['账号池', '积分到期', '积分流水', '成长计划'])
    await tabs[2].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/journal')

    // 单页组（智能体）不出现页签
    await router.push('/agent')
    await flushPromises()
    expect(wrapper.findAll('.ph-tab')).toHaveLength(0)
  })

  it('点分组回到该组上次访问的页面', async () => {
    const { router, wrapper } = await mountShell(1440)
    localStorage.setItem('mh.lastTab.usage', 'logs')
    await router.push('/overview')
    await flushPromises()
    const usageGroup = wrapper
      .findAll('.side-item')
      .find((item) => item.text().includes('调用'))
    expect(usageGroup).toBeTruthy()
    await usageGroup!.trigger('click')
    // 目标页是懒加载组件：等 import 解析与路由提交完成。
    await flushPromises()
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/logs')
  })

  it('顶栏：platformScoped 页显示平台选择器，其它页显示面包屑', async () => {
    const { router, wrapper } = await mountShell(1440)
    await router.push('/accounts')
    await flushPromises()
    expect(wrapper.find('.picker').exists()).toBe(true)
    expect(wrapper.find('.breadcrumb').exists()).toBe(false)

    await router.push('/settings')
    await flushPromises()
    expect(wrapper.find('.picker').exists()).toBe(false)
    expect(wrapper.find('.breadcrumb').text()).toContain('设置')
  })

  it('命令面板：Ctrl+K 打开，↑↓ 选择，Esc 关闭', async () => {
    const { wrapper } = await mountShell(1440)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))
    await flushPromises()
    await nextTick()
    expect(document.querySelector('.cmdk-panel')).toBeTruthy()
    expect(document.querySelectorAll('.cmdk-item').length).toBeGreaterThanOrEqual(24)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown' }))
    await flushPromises()
    expect(document.querySelectorAll('.cmdk-item.active')).toHaveLength(1)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(document.querySelector('.cmdk-panel')).toBeNull()
    expect(wrapper.find('.topbar').exists()).toBe(true)
  })

  it('命令面板：切换平台子列表写回平台 store（wb_platform_current）', async () => {
    const { router, wrapper } = await mountShell(1440)
    await router.push('/accounts')
    await flushPromises()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))
    await flushPromises()
    await nextTick()
    const input = document.querySelector('.cmdk-input') as HTMLInputElement | null
    expect(input).toBeTruthy()
    input!.value = '切换平台'
    input!.dispatchEvent(new Event('input'))
    await flushPromises()
    await nextTick()

    const platformCommand = Array.from(document.querySelectorAll('.cmdk-item')).find((el) =>
      el.textContent?.includes('切换平台'),
    )
    expect(platformCommand).toBeTruthy()
    ;(platformCommand as HTMLElement).click()
    await flushPromises()
    await nextTick()

    const trae = Array.from(document.querySelectorAll('.cmdk-item')).find((el) =>
      el.textContent?.includes('Trae 国内版'),
    )
    expect(trae).toBeTruthy()
    ;(trae as HTMLElement).click()
    await flushPromises()

    const saved = JSON.parse(localStorage.getItem('wb_platform_current') || '{}')
    expect(saved.platform).toBe('trae-cn')
    expect(wrapper.find('.topbar').exists()).toBe(true)
  })
})
