/**
 * 覆盖率补充：GatewayPage（平台网关）真实挂载交互。只新增本文件，不改 src。
 *
 * 契约：docs/ui-v2/contracts/gateway.md（末尾「实现对照」是验收口径）。
 * 挂载模式同 tests/coverage-sentinel.spec.ts / coverage-accounts.spec.ts：
 *   RouterView + NConfigProvider/NMessageProvider/NDialogProvider + memory 路由 + Pinia；
 *   只 mock @/api 的网络函数，纯函数（simulateRouting / priorityPayload / toSpeedRows …）
 *   走真实实现，所以断言既证明 DOM 变化，也真的跑到页面里的分支。
 *
 * 覆盖（26 例）：首屏骨架→数据、策略单选（保存 / 失败回滚 / 保存中重复点击不发请求）、
 *       拖拽排序（仅优先级策略）与优先级数字保存（含未变化不发请求 / 失败重取）、
 *       禁用确认框（取消 / 确认 / 接口失败）、启用免确认、无头请求开关（开 / 关 / 失败回滚）、
 *       调度预演线路带 + 模拟按钮、平台速度表（含最快 / 样本少 Badge）、
 *       空态（无平台 / 无样本 / 非数组载荷）、四类错误态与重试（含刷新失败保留旧数据）、
 *       刷新按钮、平台上下文切换（含卸载退订）。
 *
 * 未改动的 src 缺陷见交付报告（全页错误态丢原因、首屏 order 未到先出空态、
 * 模拟结果在顺序变化后不失效、RouteRibbon select 未接）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia, type Pinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NInputNumber, NMessageProvider, NSwitch } from 'naive-ui'
import GatewayPage from '@/pages/GatewayPage.vue'
import RouteRibbon from '@/components/charts/RouteRibbon.vue'
import DataTable from '@/components/data/DataTable.vue'
import { usePlatformStore } from '@/stores/platform'
import { ApiError } from '@/api/http'
import type { PlatformSpec } from '@/api/platforms'
import * as gatewayApi from '@/api/gateway'
import type {
  GatewayConfig,
  GatewayOrderItem,
  GatewaySaveInput,
  GatewaySaveResult,
  PlatformSpeedPayload,
} from '@/api/gateway'

/* -------------------------------------------------------------------------- */
/* mocks                                                                       */
/* -------------------------------------------------------------------------- */

const platformMocks = vi.hoisted(() => ({
  listPlatforms: vi.fn(),
  getCurrentPlatform: vi.fn(),
  putCurrentPlatform: vi.fn(),
}))

vi.mock('@/api/gateway', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/gateway')>()
  return {
    ...actual,
    getGatewayConfig: vi.fn(),
    saveGatewayConfig: vi.fn(),
    getGatewayOrder: vi.fn(),
    setGatewayEnabled: vi.fn(),
    getPlatformSpeed: vi.fn(),
  }
})

vi.mock('@/api/platforms', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/platforms')>()
  return {
    ...actual,
    listPlatforms: platformMocks.listPlatforms,
    getCurrentPlatform: platformMocks.getCurrentPlatform,
    putCurrentPlatform: platformMocks.putCurrentPlatform,
  }
})

const getConfig = vi.mocked(gatewayApi.getGatewayConfig)
const saveConfig = vi.mocked(gatewayApi.saveGatewayConfig)
const getOrder = vi.mocked(gatewayApi.getGatewayOrder)
const setEnabled = vi.mocked(gatewayApi.setGatewayEnabled)
const getSpeed = vi.mocked(gatewayApi.getPlatformSpeed)

/* -------------------------------------------------------------------------- */
/* 数据（形状按 admin/platform/gateway_router.py、bench_router.py 的真实返回）  */
/* -------------------------------------------------------------------------- */

const STRATEGIES: GatewayConfig['strategies'] = [
  { value: 'priority', label: '优先级', description: '按手动设置的优先级排序，数字小的平台先用。适合「主力平台 + 兜底平台」。' },
  { value: 'balance', label: '额度优先', description: '剩余额度多的平台先用，避免某一个平台先被耗干。' },
  { value: 'expiring', label: '到期优先', description: '快到期/快结束的平台先用 —— 先把即将浪费的额度用掉。' },
  { value: 'throughput', label: '吞吐优先', description: '当前在途请求少的平台先用，降低排队延迟。' },
  { value: 'round_robin', label: '轮转', description: '各平台平均分摊，不偏向任何一个。' },
  { value: 'random', label: '随机', description: '随机选择，最简单也最均匀。' },
]

function config(): GatewayConfig {
  return {
    strategy: 'priority',
    priority: { workbuddy: 10, loomy: 20 },
    disabled: {},
    spread_unbound: false,
    strategies: STRATEGIES,
    platforms: [
      { id: 'workbuddy', label: 'WorkBuddy', managed: true },
      { id: 'loomy', label: 'Loomy', managed: true },
    ],
  }
}

function orderItem(
  platform: string,
  label: string,
  usable: number,
  extra: Partial<GatewayOrderItem> = {},
): GatewayOrderItem {
  return {
    platform,
    label,
    ok: usable > 0,
    accounts: usable + 1,
    active: usable,
    cooling: 0,
    usable,
    balance_remain: usable * 4000,
    balance_total: usable * 5000,
    balance_ratio: 0.8,
    soonest_expire_at: null,
    disabled: false,
    available: usable > 0,
    reason: '优先级 10',
    ...extra,
  }
}

function order(): GatewayOrderItem[] {
  return [
    orderItem('workbuddy', 'WorkBuddy', 3, { reason: '优先级 10' }),
    orderItem('loomy', 'Loomy', 1, { reason: '优先级 20' }),
    orderItem('qoder', 'Qoder', 0, { available: false, reason: '无可用账号' }),
    orderItem('trae', 'Trae', 2, { disabled: true, available: false, reason: '已禁用' }),
  ]
}

function speed(): PlatformSpeedPayload {
  return {
    hours: 24,
    model: '',
    items: [
      {
        platform: 'workbuddy', label: 'WorkBuddy', requests: 1786, ok_requests: 1780, success_rate: 99.2,
        ttfb_avg: 1204, ttfb_p50: 1100, latency_avg: 3950, latency_p50: 3800, latency_p95: 9200,
        tps_avg: 42.4, tps_p50: 40.1, jitter: 120, samples: 1780, low_sample: false, score: 4.1, rank: 1,
      },
      {
        platform: 'loomy', label: 'Loomy', requests: 210, ok_requests: 190, success_rate: 90.5,
        ttfb_avg: 0, ttfb_p50: 0, latency_avg: 0, latency_p50: 0, latency_p95: 0,
        tps_avg: 0, tps_p50: 0, jitter: 0, samples: 0, low_sample: true, score: 9.9, rank: 2,
      },
      {
        platform: 'qoder', label: 'Qoder', requests: 50, ok_requests: 40, success_rate: 80,
        ttfb_avg: 2100, latency_avg: 5000, tps_avg: 12.5, samples: 40, low_sample: false, rank: 3,
      },
    ],
    fastest: 'workbuddy',
    steadiest: 'workbuddy',
    observed_at: '2026-03-10T04:00:00Z',
  }
}

function platformSpec(id: string, label: string): PlatformSpec {
  return {
    id,
    label,
    enabled: true,
    supports_local_scan: false,
    auth_mode: 'web-login',
    default_realm: '',
    notes: '',
    icon: id,
    storage_platform: id,
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
    account_count: 2,
    realm_counts: {},
  }
}

/** 服务端那份配置：saveGatewayConfig 真写进去，后续 GET 拿到写回后的值。 */
let serverConfig: GatewayConfig = config()

function applySave(input: GatewaySaveInput): GatewaySaveResult {
  serverConfig = {
    ...serverConfig,
    strategy: input.strategy ?? serverConfig.strategy,
    priority: input.priority ? { ...serverConfig.priority, ...input.priority } : serverConfig.priority,
    spread_unbound: input.spread_unbound ?? serverConfig.spread_unbound,
  }
  return { ok: true, config: serverConfig }
}

/* -------------------------------------------------------------------------- */
/* 挂载                                                                        */
/* -------------------------------------------------------------------------- */

const HOST_TEMPLATE =
  '<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>'

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: HOST_TEMPLATE,
})

interface Mounted {
  wrapper: VueWrapper
  router: Router
  pinia: Pinia
}

let mounted: Mounted | null = null

async function flush(): Promise<void> {
  await flushPromises()
  await nextTick()
  await flushPromises()
}

async function mountPage(flushes = 2): Promise<Mounted> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/gateway', name: 'gateway', component: GatewayPage }],
  })
  await router.push('/gateway')
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  for (let index = 0; index < flushes; index += 1) await flush()
  mounted = { wrapper, router, pinia }
  return mounted
}

beforeEach(() => {
  vi.resetAllMocks()
  serverConfig = config()
  getConfig.mockImplementation(async () => serverConfig)
  saveConfig.mockImplementation(async (input: GatewaySaveInput) => applySave(input))
  getOrder.mockResolvedValue({ items: order() })
  getSpeed.mockResolvedValue(speed())
  setEnabled.mockResolvedValue({ ok: true, platform: 'workbuddy', enabled: false })
  platformMocks.listPlatforms.mockResolvedValue({
    items: [platformSpec('workbuddy', 'WorkBuddy'), platformSpec('loomy', 'Loomy')],
    default: 'workbuddy',
    loader: { loaded: ['workbuddy', 'loomy'], failed: {} },
  })
  platformMocks.getCurrentPlatform.mockResolvedValue({ platform: 'workbuddy', realm: '', raw: 'workbuddy' })
  platformMocks.putCurrentPlatform.mockResolvedValue({ ok: true })
})

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
})

/* -------------------------------------------------------------------------- */
/* 查询小工具                                                                  */
/* -------------------------------------------------------------------------- */

function buttons(wrapper: VueWrapper, label: string): DOMWrapper<Element>[] {
  return wrapper.findAll('button').filter((item) => item.text().includes(label))
}

function button(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const found = buttons(wrapper, label)[0]
  expect(found, 'button ' + label).toBeTruthy()
  return found
}

function strategyCard(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const found = wrapper.findAll('.gw-strategy').find((item) => item.text().includes(label))
  expect(found, 'strategy ' + label).toBeTruthy()
  return found!
}

function orderRows(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('.gw-order-row')
}

function dialogElement(): HTMLElement {
  const found = document.querySelector('.n-dialog') as HTMLElement | null
  expect(found, 'dialog').toBeTruthy()
  return found!
}

async function clickDialog(label: string): Promise<void> {
  const dialog = dialogElement()
  const target = Array.from(dialog.querySelectorAll('.n-button')).find((item) =>
    (item.textContent || '').includes(label),
  ) as HTMLElement | undefined
  expect(target, 'dialog button ' + label).toBeTruthy()
  target!.click()
  await flush()
}

function speedTable(wrapper: VueWrapper): VueWrapper {
  const found = wrapper
    .findAllComponents(DataTable)
    .find((item) => (item.props() as unknown as { tableId?: string }).tableId === 'gateway-speed')
  expect(found, 'table gateway-speed').toBeTruthy()
  return found!
}

/** DataTable 收到的行（NDataTable 的 props 在 VTU 里推不出具体键，统一在这里收口）。 */
function speedTableRows(wrapper: VueWrapper): unknown[] {
  const props = speedTable(wrapper).props() as unknown as { data: unknown[] }
  return props.data
}

/** Toast 文案（只取 NMessage 的内容，避免撞上页面里同名的状态文字）。 */
function toastText(): string {
  return Array.from(document.querySelectorAll('.n-message__content'))
    .map((item) => item.textContent || '')
    .join(' | ')
}

/* -------------------------------------------------------------------------- */
/* 首屏                                                                        */
/* -------------------------------------------------------------------------- */

describe('首屏', () => {
  it('先出骨架，配置到达后渲染策略卡 / 调度顺序 / 预演 / 速度表', async () => {
    const { wrapper } = await mountPage(0)
    expect(wrapper.find('h1').text()).toContain('平台网关')
    expect(wrapper.find('.gw-skeleton').exists()).toBe(true)
    expect(wrapper.find('.gw-skeleton').attributes('aria-busy')).toBe('true')
    expect(wrapper.findAll('.gw-strategy')).toHaveLength(0)

    await flush()
    await flush()

    expect(wrapper.find('.gw-skeleton').exists()).toBe(false)
    expect(getConfig).toHaveBeenCalledTimes(1)
    expect(getOrder).toHaveBeenCalledTimes(1)
    expect(getSpeed).toHaveBeenCalledWith(24)

    const cards = wrapper.findAll('.gw-strategy')
    expect(cards.map((card) => card.find('.gw-strategy__name').text())).toEqual([
      '优先级', '额度优先', '到期优先', '吞吐优先', '轮转', '随机',
    ])
    expect(cards[0].attributes('aria-checked')).toBe('true')
    expect(cards[1].attributes('aria-checked')).toBe('false')
    expect(cards.every((card) => card.attributes('role') === 'radio')).toBe(true)
    // 只显示选中策略的说明 + 活跃度回温注释
    expect(wrapper.find('.gw-strategy__detail').text()).toContain('按手动设置的优先级排序')
    expect(wrapper.text()).toContain('活跃度回温')

    const rows = orderRows(wrapper)
    expect(rows).toHaveLength(4)
    expect(rows.map((row) => row.find('.gw-order__name').text())).toEqual([
      'WorkBuddy', 'Loomy', 'Qoder', 'Trae',
    ])
    expect(rows.map((row) => row.find('.gw-order__rank').text())).toEqual(['1', '2', '3', '4'])
    expect(rows[0].find('.gw-order__meta').text()).toContain('3 可用')
    // 额度用量级缩写，完整数字在悬停提示里（第 1 轮审查 P2）
    expect(rows[0].find('.gw-order__meta').text()).toContain('12K')
    expect(rows[0].find('.gw-order__meta').attributes('title')).toBeUndefined()
    await rows[0].find('.gw-order__meta').trigger('mouseenter')
    await new Promise(resolve => setTimeout(resolve, 180))
    expect(document.querySelector('.mh-help__content')?.textContent).toBe('12,000')
    expect(rows[0].classes()).toContain('is-draggable')
    expect(rows[1].find('.gw-order__reason').text()).toContain('优先级 20')
    expect(rows[2].find('.gw-order__reason').text()).toContain('无可用账号')
    expect(rows[2].classes()).toContain('is-disabled')
    expect(rows[3].find('.gw-order__reason').text()).toContain('已禁用')
    expect(rows[3].classes()).toContain('is-disabled')

    // 拓扑：预演线路带按可用账号数加权（3:1 → 75/25），首选是第一个平台
    expect(wrapper.find('.gw-sim__text').text()).toBe('100 个请求中：WorkBuddy 75 · Loomy 25')
    const ribbon = wrapper.findComponent(RouteRibbon)
    expect(ribbon.exists()).toBe(true)
    const lanes = ribbon.props('lanes') as Array<{
      id: string
      count: number
      preferred: boolean
      disabled: boolean
      successRate: number | null
    }>
    expect(lanes.map((lane) => lane.id)).toEqual(['workbuddy', 'loomy'])
    expect(lanes.map((lane) => lane.count)).toEqual([75, 25])
    expect(lanes.map((lane) => lane.preferred)).toEqual([true, false])
    expect(lanes.map((lane) => lane.successRate)).toEqual([99.2, 90.5])
    expect(ribbon.props('source')).toMatchObject({ total: 100 })
    expect(ribbon.props('unit')).toBe('次')

    // 平台速度表：后端给的行原样展示（不前端排序），最快 / 样本少 有 Badge
    expect(speedTableRows(wrapper)).toHaveLength(3)
    expect(wrapper.find('.gw-speed-card').text()).toContain('近 24 小时 · 3 个平台有样本')
    expect(wrapper.findAll('.mh-table__body tbody tr')).toHaveLength(3)
    expect(wrapper.findAll('.gw-speed-name').map((item) => item.text())).toEqual([
      'WorkBuddy最快', 'Loomy样本少', 'Qoder',
    ])
    expect(wrapper.find('.gw-speed-card').text()).toContain('99.2%')
    expect(wrapper.find('.gw-speed-card').text()).toContain('1204ms')
    expect(wrapper.find('.gw-speed-card').text()).toContain('1,786')
  })
})

/* -------------------------------------------------------------------------- */
/* 策略                                                                        */
/* -------------------------------------------------------------------------- */

describe('策略切换', () => {
  it('点非当前策略：立即保存、写回配置、重取顺序、Toast', async () => {
    const { wrapper } = await mountPage()
    expect(getOrder).toHaveBeenCalledTimes(1)

    await strategyCard(wrapper, '轮转').trigger('click')
    await flush()

    expect(saveConfig).toHaveBeenCalledWith({ strategy: 'round_robin' })
    expect(getOrder).toHaveBeenCalledTimes(2)
    expect(toastText()).toContain('策略已切换：轮转')

    const cards = wrapper.findAll('.gw-strategy')
    expect(cards[4].attributes('aria-checked')).toBe('true')
    expect(cards[0].attributes('aria-checked')).toBe('false')
    expect(wrapper.find('.gw-strategy__detail').text()).toContain('各平台平均分摊')

    // 非优先级策略：只读顺序（无输入框、拖拽关闭、说明改成只读文案）
    expect(wrapper.findAllComponents(NInputNumber)).toHaveLength(0)
    const rows = orderRows(wrapper)
    expect(rows[0].classes()).not.toContain('is-draggable')
    expect(rows[0].find('.gw-order__handle').text()).toBe('·')
    expect(rows[0].text()).toContain('优先级 10')
    expect(wrapper.text()).toContain('按当前策略实际排出的顺序')

    // 轮转策略下预演平均分摊
    expect(wrapper.find('.gw-sim__text').text()).toBe('100 个请求中：WorkBuddy 50 · Loomy 50')
  })

  it('点当前策略不重复发请求', async () => {
    const { wrapper } = await mountPage()
    await strategyCard(wrapper, '优先级').trigger('click')
    await flush()
    expect(saveConfig).not.toHaveBeenCalled()
    expect(getOrder).toHaveBeenCalledTimes(1)
  })

  it('保存中再点别的策略不会重复发请求', async () => {
    const { wrapper } = await mountPage()
    let release: (value: GatewaySaveResult) => void = () => undefined
    saveConfig.mockImplementation(
      () => new Promise<GatewaySaveResult>((resolve) => { release = resolve }),
    )

    await strategyCard(wrapper, '轮转').trigger('click')
    await flush()
    await strategyCard(wrapper, '随机').trigger('click')
    await flush()

    expect(saveConfig).toHaveBeenCalledTimes(1)
    expect(saveConfig).toHaveBeenCalledWith({ strategy: 'round_robin' })

    release({ ok: true, config: { ...serverConfig, strategy: 'round_robin' } })
    await flush()

    expect(toastText()).toContain('策略已切换：轮转')
    expect(strategyCard(wrapper, '轮转').attributes('aria-checked')).toBe('true')
  })

  it('保存失败回滚到原策略并 Toast，不再重取顺序', async () => {
    const { wrapper } = await mountPage()
    saveConfig.mockRejectedValue(new ApiError(500, '策略服务不可用'))

    await strategyCard(wrapper, '随机').trigger('click')
    await flush()

    expect(toastText()).toContain('切换策略失败：策略服务不可用')
    expect(strategyCard(wrapper, '优先级').attributes('aria-checked')).toBe('true')
    expect(strategyCard(wrapper, '随机').attributes('aria-checked')).toBe('false')
    expect(getOrder).toHaveBeenCalledTimes(1)
  })
})

/* -------------------------------------------------------------------------- */
/* 排序                                                                        */
/* -------------------------------------------------------------------------- */

describe('调度顺序', () => {
  it('优先级策略下拖拽：松手整表重排成 10/20/30/40 并保存', async () => {
    const { wrapper } = await mountPage()
    const rows = orderRows(wrapper)

    await rows[0].trigger('dragstart')
    await rows[1].trigger('dragover')
    expect(orderRows(wrapper).map((row) => row.find('.gw-order__name').text())).toEqual([
      'Loomy', 'WorkBuddy', 'Qoder', 'Trae',
    ])

    await orderRows(wrapper)[1].trigger('dragend')
    await flush()

    expect(saveConfig).toHaveBeenCalledWith({
      priority: { loomy: 10, workbuddy: 20, qoder: 30, trae: 40 },
    })
    expect(toastText()).toContain('已保存调度顺序')
    expect(getOrder).toHaveBeenCalledTimes(2)
  })

  it('非优先级策略下拖拽不生效', async () => {
    serverConfig = { ...serverConfig, strategy: 'balance' }
    const { wrapper } = await mountPage()
    const rows = orderRows(wrapper)
    await rows[0].trigger('dragstart')
    await rows[1].trigger('dragover')
    await orderRows(wrapper)[0].trigger('dragend')
    await flush()
    expect(saveConfig).not.toHaveBeenCalled()
    expect(orderRows(wrapper)[0].find('.gw-order__name').text()).toBe('WorkBuddy')
  })

  it('改优先级数字：失焦保存该平台的新值', async () => {
    const { wrapper } = await mountPage()
    const field = wrapper.findAllComponents(NInputNumber)[0].find('input')

    await field.setValue('5')
    await field.trigger('blur')
    await flush()

    expect(saveConfig).toHaveBeenCalledWith({ priority: { workbuddy: 5, loomy: 20 } })
    expect(toastText()).toContain('已保存优先级')
    expect(getOrder).toHaveBeenCalledTimes(2)
  })

  it('数字没变则不发请求', async () => {
    const { wrapper } = await mountPage()
    const field = wrapper.findAllComponents(NInputNumber)[0].find('input')
    await field.setValue('10')
    await field.trigger('blur')
    await flush()
    expect(saveConfig).not.toHaveBeenCalled()
  })

  it('优先级保存失败：Toast 并重取配置与顺序恢复', async () => {
    const { wrapper } = await mountPage()
    saveConfig.mockRejectedValue(new ApiError(500, '保存炸了'))
    const field = wrapper.findAllComponents(NInputNumber)[0].find('input')
    await field.setValue('5')
    await field.trigger('blur')
    await flush()

    expect(toastText()).toContain('保存优先级失败：保存炸了')
    expect(getConfig).toHaveBeenCalledTimes(2)
    expect(getOrder).toHaveBeenCalledTimes(2)
    expect(wrapper.findAllComponents(NInputNumber)[0].props('value')).toBe(10)
  })
})

/* -------------------------------------------------------------------------- */
/* 禁用 / 启用                                                                 */
/* -------------------------------------------------------------------------- */

describe('禁用与启用', () => {
  it('禁用先出确认框：取消不调用接口', async () => {
    const { wrapper } = await mountPage()
    const btn = orderRows(wrapper)[0].findAll('button').find((item) => item.text().includes('禁用'))
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flush()

    const dialog = dialogElement()
    expect(dialog.textContent).toContain('禁用平台网关？')
    expect(dialog.textContent).toContain('不再参与调度')
    expect(dialog.textContent).toContain('账号数据保留')

    await clickDialog('取消')
    expect(setEnabled).not.toHaveBeenCalled()
    // NDialog 关闭后元素留在 body 里（display:none），断言它确实收起来了
    const closed = document.querySelector('.n-dialog') as HTMLElement | null
    expect(closed === null || closed.style.display === 'none').toBe(true)
  })

  it('禁用确认后调用 /disable 并刷新整页', async () => {
    const { wrapper } = await mountPage()
    const btn = orderRows(wrapper)[0].findAll('button').find((item) => item.text().includes('禁用'))
    await btn!.trigger('click')
    await flush()
    await clickDialog('禁用')

    expect(setEnabled).toHaveBeenCalledWith('workbuddy', false, '控制台手动操作')
    expect(toastText()).toContain('已禁用')
    expect(getConfig).toHaveBeenCalledTimes(2)
    expect(getOrder).toHaveBeenCalledTimes(2)
  })

  it('禁用接口失败：Toast 报错且不刷新', async () => {
    const { wrapper } = await mountPage()
    setEnabled.mockRejectedValue(new ApiError(500, '网关开关炸了'))
    const btn = orderRows(wrapper)[0].findAll('button').find((item) => item.text().includes('禁用'))
    await btn!.trigger('click')
    await flush()
    await clickDialog('禁用')

    expect(toastText()).toContain('禁用平台失败：网关开关炸了')
    expect(getConfig).toHaveBeenCalledTimes(1)
    expect(getOrder).toHaveBeenCalledTimes(1)
  })

  it('已禁用的平台点「启用」不弹确认，直接调用 /enable', async () => {
    const { wrapper } = await mountPage()
    const disabledRow = orderRows(wrapper)[3]
    const btn = disabledRow.findAll('button').find((item) => item.text().includes('启用'))
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flush()

    expect(document.querySelector('.n-dialog')).toBeNull()
    expect(setEnabled).toHaveBeenCalledWith('trae', true, '控制台手动操作')
    expect(toastText()).toContain('已启用')
    expect(getConfig).toHaveBeenCalledTimes(2)
  })
})

/* -------------------------------------------------------------------------- */
/* 无头请求                                                                    */
/* -------------------------------------------------------------------------- */

describe('无头请求分摊', () => {
  it('打开开关写入 spread_unbound 并提示', async () => {
    const { wrapper } = await mountPage()
    wrapper.findComponent(NSwitch).vm.$emit('update:value', true)
    await flush()

    expect(saveConfig).toHaveBeenCalledWith({ spread_unbound: true })
    expect(toastText()).toContain('已开启：无头请求会按策略分摊到各平台')
    expect(wrapper.findComponent(NSwitch).props('value')).toBe(true)
    expect(wrapper.find('.gw-spread__text').text()).toContain('参与负载分摊')
  })

  it('关闭开关写回 false 并提示走默认平台', async () => {
    serverConfig = { ...serverConfig, spread_unbound: true }
    const { wrapper } = await mountPage()
    expect(wrapper.findComponent(NSwitch).props('value')).toBe(true)

    wrapper.findComponent(NSwitch).vm.$emit('update:value', false)
    await flush()

    expect(saveConfig).toHaveBeenCalledWith({ spread_unbound: false })
    expect(toastText()).toContain('已关闭：无头请求走默认平台')
    expect(wrapper.findComponent(NSwitch).props('value')).toBe(false)
  })

  it('保存失败回滚开关并 Toast', async () => {
    const { wrapper } = await mountPage()
    saveConfig.mockRejectedValue(new ApiError(500, '开关炸了'))
    wrapper.findComponent(NSwitch).vm.$emit('update:value', true)
    await flush()

    expect(toastText()).toContain('保存无头请求设置失败：开关炸了')
    expect(wrapper.findComponent(NSwitch).props('value')).toBe(false)
  })
})

/* -------------------------------------------------------------------------- */
/* 拓扑 / 预演                                                                 */
/* -------------------------------------------------------------------------- */

describe('调度预演', () => {
  it('点「模拟 100 个请求」重算分流并 Toast', async () => {
    const { wrapper } = await mountPage()
    const sim = button(wrapper, '模拟 100 个请求')
    await sim.trigger('click')
    await flush()

    expect(toastText()).toContain('已模拟 100 个请求')
    expect(wrapper.find('.gw-sim__text').text()).toBe('100 个请求中：WorkBuddy 75 · Loomy 25')
    const lanes = wrapper.findComponent(RouteRibbon).props('lanes') as Array<{ id: string; count: number }>
    expect(lanes.map((lane) => lane.count)).toEqual([75, 25])
  })
})

/* -------------------------------------------------------------------------- */
/* 空态                                                                        */
/* -------------------------------------------------------------------------- */

describe('空态', () => {
  it('没有可用平台 / 没有速度样本时的空态文案', async () => {
    getOrder.mockResolvedValue({ items: [] })
    getSpeed.mockResolvedValue({ hours: 24, model: '', items: [] })
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('当前没有可用平台（全部被禁用或没有可用账号）。')
    expect(wrapper.text()).toContain('没有可调度的平台，无法预演。')
    expect(wrapper.find('.gw-sim__text').text()).toBe('没有可调度的平台')
    expect(button(wrapper, '模拟 100 个请求').attributes('disabled')).toBeDefined()
    expect(wrapper.findComponent(RouteRibbon).exists()).toBe(false)

    expect(wrapper.text()).toContain('近 24 小时还没有样本。')
    expect(wrapper.find('.gw-speed-card').text()).toContain('近 24 小时 · 0 个平台有样本')
  })

  it('载荷缺字段（items 非数组 / 速度为 undefined）也按空态渲染', async () => {
    getOrder.mockResolvedValue({} as never)
    getSpeed.mockResolvedValue(undefined as never)
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('当前没有可用平台（全部被禁用或没有可用账号）。')
    expect(wrapper.text()).toContain('近 24 小时还没有样本。')
    expect(wrapper.find('.gw-speed-card').text()).toContain('近 24 小时真实请求聚合')
  })
})

/* -------------------------------------------------------------------------- */
/* 错误态与重试                                                                */
/* -------------------------------------------------------------------------- */

describe('错误态与重试', () => {
  it('配置失败：整页 ErrorBlock（含可展开详情），重试后恢复', async () => {
    getConfig.mockRejectedValueOnce(new ApiError(500, '网关服务不可用', { trace: 'db down' }))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('加载平台网关配置失败')
    // 现行为：全页错误态只渲染固定标题 + detail，ApiError 的 message 不上屏
    //（契约 6.2 要求给出原因）——已记为未修的 src 缺陷，这里按实际行为断言。
    expect(wrapper.text()).not.toContain('网关服务不可用')
    expect(wrapper.findAll('.gw-strategy')).toHaveLength(0)

    await button(wrapper, '展开详细信息').trigger('click')
    await flush()
    expect(wrapper.text()).toContain('"trace": "db down"')

    await button(wrapper, '重试').trigger('click')
    await flush()

    expect(getConfig).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.gw-strategy')).toHaveLength(6)
    expect(wrapper.text()).not.toContain('加载平台网关配置失败')
  })

  it('顺序失败：局部 ErrorBlock + Toast，重试只重取顺序', async () => {
    getOrder.mockRejectedValue(new ApiError(502, 'order 502'))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('加载调度顺序失败：order 502')
    expect(toastText()).toContain('刷新调度顺序失败：order 502')
    // 其余区域照常渲染
    expect(wrapper.findAll('.gw-strategy')).toHaveLength(6)
    expect(wrapper.find('.gw-speed-card').text()).toContain('近 24 小时 · 3 个平台有样本')

    await button(wrapper, '重试').trigger('click')
    await flush()
    expect(getOrder).toHaveBeenCalledTimes(2)
  })

  it('速度失败：局部 ErrorBlock，重试后出表', async () => {
    getSpeed.mockRejectedValueOnce(new ApiError(503, 'bench 503'))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('加载平台速度失败：bench 503')
    expect(wrapper.find('.gw-speed-card').text()).toContain('近 24 小时真实请求聚合')
    expect(wrapper.findAllComponents(DataTable)).toHaveLength(0)

    await button(wrapper, '重试').trigger('click')
    await flush()

    expect(getSpeed).toHaveBeenCalledTimes(2)
    expect(wrapper.findAllComponents(DataTable)).toHaveLength(1)
    expect(wrapper.find('.gw-speed-card').text()).toContain('近 24 小时 · 3 个平台有样本')
  })
})

/* -------------------------------------------------------------------------- */
/* 刷新 / 平台上下文                                                           */
/* -------------------------------------------------------------------------- */

describe('刷新与平台上下文', () => {
  it('页头「刷新」重取配置、顺序、速度三份数据', async () => {
    const { wrapper } = await mountPage()
    await button(wrapper, '刷新').trigger('click')
    await flush()

    expect(getConfig).toHaveBeenCalledTimes(2)
    expect(getOrder).toHaveBeenCalledTimes(2)
    expect(getSpeed).toHaveBeenCalledTimes(2)
  })

  it('刷新失败：三份数据各自 Toast，策略区保留旧数据', async () => {
    const { wrapper } = await mountPage()
    const boom = () => Promise.reject(new ApiError(503, '上游 503'))
    getConfig.mockImplementation(boom)
    getOrder.mockImplementation(boom)
    getSpeed.mockImplementation(boom)

    await button(wrapper, '刷新').trigger('click')
    await flush()

    const toasts = toastText()
    expect(toasts).toContain('刷新网关配置失败：上游 503')
    expect(toasts).toContain('刷新调度顺序失败：上游 503')
    expect(toasts).toContain('刷新平台速度失败：上游 503')
    // 配置还在 → 不退回整页错误态；顺序 / 速度卡各自换成局部错误块（带重试）
    expect(wrapper.text()).not.toContain('加载平台网关配置失败')
    expect(wrapper.findAll('.gw-strategy')).toHaveLength(6)
    expect(wrapper.text()).toContain('加载调度顺序失败：上游 503')
    expect(wrapper.text()).toContain('加载平台速度失败：上游 503')
    expect(wrapper.findAllComponents(DataTable)).toHaveLength(0)
    expect(wrapper.findAll('.gw-skeleton')).toHaveLength(0)
  })

  it('切换平台触发整页重取；卸载后退订不再请求', async () => {
    const { wrapper, pinia } = await mountPage()
    const platform = usePlatformStore(pinia)
    platform.items = [platformSpec('workbuddy', 'WorkBuddy'), platformSpec('loomy', 'Loomy')]
    platform.loaded = true

    expect(getConfig).toHaveBeenCalledTimes(1)
    platform.setCurrent('loomy')
    await flush()

    expect(platform.selector).toBe('loomy')
    expect(getConfig).toHaveBeenCalledTimes(2)
    expect(getOrder).toHaveBeenCalledTimes(2)
    expect(getSpeed).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.gw-strategy')).toHaveLength(6)

    wrapper.unmount()
    mounted = null
    platform.setCurrent('workbuddy')
    await flush()

    expect(platform.selector).toBe('workbuddy')
    expect(getConfig).toHaveBeenCalledTimes(2)
    expect(getOrder).toHaveBeenCalledTimes(2)
  })
})

// Naive message 的 auto-hide 定时器会在 jsdom 销毁后触发；用可自动推进的假时钟并在收尾清空。
beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })
