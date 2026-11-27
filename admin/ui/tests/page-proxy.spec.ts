/**
 * /proxy 出口代理页（issue #39）真实挂载交互。
 *
 * 只新增本文件，不改 src；网络层 mock 在 @/api/settings（页面唯一取数入口），
 * 同模块的纯函数（validateProxyInput / proxyStateOf）走真实实现。
 *
 * 覆盖点：首屏渲染（当前出口 / KPI / 当前标记）· 全局出口开关与模式 / 周期 /
 *        每条请求 / 分组保存 · 立即轮换 · 行内测试 · 测试当前出口（延迟 + 出口 IP）·
 *        添加节点抽屉（校验 / 提交 / 刷新）· 空态。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NInputNumber, NMessageProvider, NSelect, NSwitch } from 'naive-ui'
import ProxyPage from '@/pages/ProxyPage.vue'
import * as settingsApi from '@/api/settings'
import type {
  ProxyEgressPayload,
  ProxyListPayload,
  ProxyNode,
  ProxyProbeResult,
} from '@/api/settings'

vi.mock('@/api/settings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/settings')>()
  return {
    ...actual,
    listProxies: vi.fn(),
    getProxyEgress: vi.fn(),
    saveProxyEgress: vi.fn(),
    rotateProxyEgress: vi.fn(),
    testProxyNode: vi.fn(),
    createProxy: vi.fn(),
    patchProxy: vi.fn(),
    deleteProxy: vi.fn(),
    unblockProxy: vi.fn(),
  }
})

const listProxies = vi.mocked(settingsApi.listProxies)
const getProxyEgress = vi.mocked(settingsApi.getProxyEgress)
const saveProxyEgress = vi.mocked(settingsApi.saveProxyEgress)
const rotateProxyEgress = vi.mocked(settingsApi.rotateProxyEgress)
const testProxyNode = vi.mocked(settingsApi.testProxyNode)
const createProxy = vi.mocked(settingsApi.createProxy)

/* -------------------------------------------------------------------------- */
/* 造数：形状按 admin/routers/proxies.py 与 admin/proxy_egress.py 的真实响应      */
/* -------------------------------------------------------------------------- */

function probe(overrides: Partial<ProxyProbeResult> = {}): ProxyProbeResult {
  return {
    ok: true,
    latency_ms: 413.2,
    status: 200,
    egress_ip: '103.151.173.102',
    via: 'http://127.0.0.1:7897',
    target: 'https://api.ipify.org?format=json',
    error: '',
    at: '2026-11-27T01:56:04Z',
    total_ms: 413.4,
    ...overrides,
  }
}

function node(id: number, overrides: Partial<ProxyNode> = {}): ProxyNode {
  return {
    id,
    label: '本地-Clash-' + id,
    url: 'http://127.0.0.1:789' + id,
    enabled: true,
    max_concurrency: 2,
    pool: 'local',
    blocked: false,
    blocked_until: null,
    last_ok_at: '2026-11-27T01:56:05',
    last_error: '',
    state: 'ready',
    current: false,
    probe: null,
    ...overrides,
  }
}

function nodeList(items: ProxyNode[]): ProxyListPayload {
  return {
    items,
    summary: {
      total: items.length,
      ready: items.filter((item) => item.state === 'ready').length,
      blocked: items.filter((item) => item.state === 'blocked').length,
      disabled: items.filter((item) => !item.enabled).length,
      slots: items.reduce((sum, item) => sum + (item.enabled ? item.max_concurrency : 0), 0),
    },
    pools: ['local', 'residential'],
    note: '',
  }
}

function egress(overrides: Partial<ProxyEgressPayload> = {}): ProxyEgressPayload {
  return {
    settings: {
      enabled: 1,
      mode: 'auto',
      endpoint_id: 0,
      pool: 'local',
      rotate_seconds: 300,
      rotate_per_request: 0,
    },
    current: {
      endpoint_id: 1,
      label: '本地-Clash-1',
      url: 'http://127.0.0.1:7891',
      pool: 'local',
      since: '2026-11-27T01:56:04Z',
      reason: 'select',
    },
    available: 2,
    slots: 4,
    next_rotate_in: 240,
    runtime: {
      requests: 5,
      rotations: 2,
      last_ok_at: '2026-11-27T01:56:05Z',
      last_error: '',
      last_error_at: '',
      last_reason: 'select',
      waf_blocked: false,
    },
    warnings: [],
    probe_target: 'https://api.ipify.org?format=json',
    ...overrides,
  }
}

/* -------------------------------------------------------------------------- */
/* mount 工具                                                                  */
/* -------------------------------------------------------------------------- */

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>`,
})

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
  await nextTick()
}

function bodyText(): string {
  return document.body.textContent || ''
}

function buttonByText(root: ParentNode, label: string): HTMLButtonElement | null {
  return (
    Array.from(root.querySelectorAll('button')).find(
      (button) => (button.textContent || '').trim() === label,
    ) || null
  )
}

async function click(root: ParentNode, label: string): Promise<HTMLButtonElement> {
  const button = buttonByText(root, label)
  expect(button, '找不到按钮：' + label).toBeTruthy()
  const target = button as HTMLButtonElement
  target.click()
  await settle()
  return target
}

let wrapper: VueWrapper | null = null

async function mountPage(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/proxy', name: 'proxy', component: ProxyPage, meta: { title: '出口代理' } },
    ],
  })
  await router.push('/proxy')
  await router.isReady()
  const mounted = mount(Host, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await settle()
  await settle()
  wrapper = mounted
  return { wrapper: mounted, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  listProxies.mockResolvedValue(
    nodeList([
      node(1, { current: true, probe: probe() }),
      node(2, { label: '住宅-上海', pool: 'residential', state: 'blocked', blocked: true, enabled: false }),
    ]),
  )
  getProxyEgress.mockResolvedValue(egress())
  saveProxyEgress.mockResolvedValue(egress())
  rotateProxyEgress.mockResolvedValue({ ok: true, current: egress().current, status: egress() })
  testProxyNode.mockResolvedValue({ ok: true, error: '', endpoint_id: 1, probe: probe() })
  createProxy.mockResolvedValue({ ok: true, item: node(3) })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('出口代理页 · 首屏', () => {
  it('展示当前出口、轮换倒计时、KPI 与当前节点标记', async () => {
    const { wrapper: page } = await mountPage()
    const text = page.text()
    expect(text).toContain('出口代理')
    expect(text).toContain('当前出口：本地-Clash-1 · http://127.0.0.1:7891')
    expect(text).toContain('240s 后轮换')
    expect(text).toContain('共 2 个节点')
    expect(text).toContain('当前')
    expect(text).toContain('413.2 ms')
    expect(listProxies).toHaveBeenCalled()
    expect(getProxyEgress).toHaveBeenCalled()
  })

  it('全局关闭时提示直连，且不把任何节点标成当前', async () => {
    getProxyEgress.mockResolvedValue(
      egress({ settings: { ...egress().settings, enabled: 0 }, current: null }),
    )
    listProxies.mockResolvedValue(nodeList([node(1)]))
    const { wrapper: page } = await mountPage()
    expect(page.text()).toContain('全局出口关闭')
    expect(page.find('.proxy-current').exists()).toBe(false)
  })

  it('节点为空时给出添加引导', async () => {
    listProxies.mockResolvedValue(nodeList([]))
    const { wrapper: page } = await mountPage()
    expect(page.text()).toContain('还没有出口节点')
  })
})

describe('出口代理页 · 全局出口控制', () => {
  it('开关 / 模式 / 分组 / 周期 / 每条请求都落到 PUT /api/proxies/egress', async () => {
    const { wrapper: page } = await mountPage()
    const card = page.find('.proxy-egress')

    await card.findAllComponents(NSwitch)[0].vm.$emit('update:value', false)
    await settle()
    expect(saveProxyEgress).toHaveBeenCalledWith({ enabled: 0 })

    saveProxyEgress.mockClear()
    await card.findAllComponents(NSelect)[0].vm.$emit('update:value', 'fixed')
    await settle()
    expect(saveProxyEgress).toHaveBeenCalledWith({ mode: 'fixed' })

    saveProxyEgress.mockClear()
    await card.findAllComponents(NSelect)[1].vm.$emit('update:value', 'residential')
    await settle()
    expect(saveProxyEgress).toHaveBeenCalledWith({ pool: 'residential' })

    saveProxyEgress.mockClear()
    await card.findComponent(NInputNumber).vm.$emit('update:value', 600)
    await settle()
    expect(saveProxyEgress).toHaveBeenCalledWith({ rotate_seconds: 600 })

    saveProxyEgress.mockClear()
    await card.findAllComponents(NSwitch)[1].vm.$emit('update:value', true)
    await settle()
    expect(saveProxyEgress).toHaveBeenCalledWith({ rotate_per_request: 1 })
  })

  it('立即轮换切到下一个节点并刷新', async () => {
    const { wrapper: page } = await mountPage()
    await click(page.find('.proxy-egress').element, '立即轮换')
    expect(rotateProxyEgress).toHaveBeenCalled()
    expect(bodyText()).toContain('已切换到 本地-Clash-1')
  })

  it('测试当前出口：真拨当前节点，并把延迟与出口 IP 留在面板上', async () => {
    const { wrapper: page } = await mountPage()
    await click(page.find('.proxy-egress').element, '测试当前出口')
    expect(testProxyNode).toHaveBeenCalledWith(1)
    expect(page.find('.proxy-egress__probe').text()).toContain('连通正常：413.2 ms')
    expect(page.find('.proxy-egress__probe').text()).toContain('出口 IP 103.151.173.102')
  })

  it('告警（开了但没有可用节点）如实显示', async () => {
    getProxyEgress.mockResolvedValue(
      egress({
        current: null,
        warnings: ['全局出口已开启，但没有任何可用节点，对话会退回直连'],
      }),
    )
    const { wrapper: page } = await mountPage()
    expect(page.text()).toContain('还没有选出当前节点')
    expect(page.text()).toContain('对话会退回直连')
  })
})

describe('出口代理页 · 节点操作', () => {
  it('行内测试只测该节点；设为当前会切到 fixed 并锁定 node_id', async () => {
    const { wrapper: page } = await mountPage()
    const list = page.find('.proxy-list')

    await click(list.element, '测试')
    expect(testProxyNode).toHaveBeenCalledWith(1)

    saveProxyEgress.mockClear()
    // 节点 2 不是当前节点，才有「设为当前」。
    const useThis = Array.from(list.element.querySelectorAll('button')).filter(
      (button) => (button.textContent || '').trim() === '设为当前',
    )
    expect(useThis.length).toBeGreaterThan(0)
    ;(useThis[0] as HTMLButtonElement).click()
    await settle()
    expect(saveProxyEgress).toHaveBeenCalledWith({ mode: 'fixed', endpoint_id: 2 })
  })

  it('添加节点：地址不合法先拦，合法后提交并刷新', async () => {
    const { wrapper: page } = await mountPage()
    await click(page.element as HTMLElement, '添加节点')

    const drawer = document.querySelector<HTMLElement>('.n-drawer')
    expect(drawer, '抽屉未打开').toBeTruthy()
    const urlInput = Array.from(drawer!.querySelectorAll<HTMLInputElement>('input')).find(
      (input) => (input.placeholder || '').includes('socks5://host:port'),
    )
    expect(urlInput).toBeTruthy()
    urlInput!.value = 'ftp://host:21'
    urlInput!.dispatchEvent(new Event('input'))
    await settle()
    await click(drawer!, '添加节点')
    expect(createProxy).not.toHaveBeenCalled()
    expect(bodyText()).toContain('必须以 http://')

    urlInput!.value = 'http://127.0.0.1:7897'
    urlInput!.dispatchEvent(new Event('input'))
    await settle()
    await click(drawer!, '添加节点')
    expect(createProxy).toHaveBeenCalledWith({
      label: '',
      url: 'http://127.0.0.1:7897',
      max_concurrency: 1,
      pool: 'local',
    })
    expect(bodyText()).toContain('已添加出口节点')
  })
})
