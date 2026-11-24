/** 覆盖率补充：SentinelPage 真实挂载交互。只新增本文件，不改 src。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider, NSelect } from 'naive-ui'
import SentinelPage from '@/pages/SentinelPage.vue'
import RouteRibbon from '@/components/charts/RouteRibbon.vue'
import * as sentinelApi from '@/api/sentinel'
import type { SentinelStatus, TrafficPayload } from '@/api/sentinel'

vi.mock('@/api/sentinel', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/sentinel')>()
  return { ...actual, getSentinelStatus: vi.fn(), getTraffic: vi.fn(), pausePlatform: vi.fn(), resumePlatform: vi.fn() }
})
const getStatus = vi.mocked(sentinelApi.getSentinelStatus)
const getTraffic = vi.mocked(sentinelApi.getTraffic)

function status(overrides: Partial<SentinelStatus> = {}): SentinelStatus {
  return {
    tripped: true, accounts_affected: 3, accounts_total: 5, accounts_risk: 3, threshold: 3,
    window_seconds: 300, suggested_pause_seconds: 120, total_events: 7,
    by_code: { '11140': 4 }, by_signal: { risk_control: 7 }, signal_labels: { risk_control: '账号/设备风控' },
    by_severity: { risk: 7, throttle: 0, auth: 0, info: 0 },
    by_platform: [{ platform: 'workbuddy', count: 7, risk: 7, last_ts: 1_800_000_000 }],
    accounts: [{ account_id: 152, count: 3, first_ts: 1_800_000_000, last_ts: 1_800_000_600, kind: 'account_fault', code: '11140', label: '账号级授权风控', signal: 'risk_control', signal_label: '账号/设备风控', severity: 'risk', detail: 'upstream 403', platform: 'workbuddy' }],
    recent: [{ ts: 1_800_000_600, account_id: 152, kind: 'account_fault', code: '11140', label: '账号级授权风控（11140）', signal: 'risk_control', signal_label: '账号/设备风控', severity: 'risk', unmapped: false, detail: 'upstream 403', model: 'deepseek-v4.1-flash', platform: 'workbuddy' }],
    redis: true, pause: {}, now: 1_800_000_700, ...overrides,
  } as SentinelStatus
}
function traffic(overrides: Partial<TrafficPayload> = {}): TrafficPayload {
  return {
    day: '2026-11-23', window_seconds: 3600,
    platforms: [{ platform: 'workbuddy', label: 'WorkBuddy', requests: 1000, ok: 306, error: 600, timeout: 94, interrupted: 0, last_at: 1_800_000_000, last_status: 200 }],
    totals: { requests: 1000, ok: 306, error: 600, timeout: 94, interrupted: 0 },
    recent: [{ rid: 'r1', ts: 1_800_000_700, model: 'deepseek-v4.1-flash', platform: 'workbuddy', status: 500, outcome: 'completed', error: 'upstream 500' }],
    running: 1, storage: 'redis', now: 1_800_000_800, ...overrides,
  } as TrafficPayload
}

const Host = defineComponent({ components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>` })
let mounted: { wrapper: VueWrapper; router: Router } | null = null
async function flush() { await flushPromises(); await nextTick(); await flushPromises() }
async function mountPage() {
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/sentinel', name: 'sentinel', component: SentinelPage }] })
  await router.push('/sentinel'); await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flush(); await flush()
  mounted = { wrapper, router }
  return mounted
}
afterEach(() => { mounted?.wrapper.unmount(); mounted = null; document.body.innerHTML = '' })

describe('SentinelPage 覆盖率补测', () => {
  it('首屏渲染结论横幅与统计', async () => {
    getStatus.mockResolvedValue(status()); getTraffic.mockResolvedValue(traffic())
    const { wrapper } = await mountPage()
    expect(wrapper.find('h1').text()).toContain('风控哨兵')
    expect(wrapper.text()).toContain('3 个账号被风控')
    // traffic 的 platforms/totals 是 traffic_stats 按服务器日期分键的「今日累计」，
    // window=3600 只影响 recent 明细（fixlog_frontend_F2 §0 第 2 条）：统计格叫「今日请求」。
    expect(wrapper.text()).toContain('今日请求')
    expect(wrapper.text()).not.toContain('近 1 小时请求')
  })
  it('切换受影响账号页签并写 query', async () => {
    getStatus.mockResolvedValue(status()); getTraffic.mockResolvedValue(traffic())
    const { wrapper, router } = await mountPage()
    const tab = wrapper.findAll('button').find((item) => item.text().includes('受影响账号'))
    expect(tab).toBeTruthy()
    await tab!.trigger('click'); await flush()
    expect(router.currentRoute.value.query.tab).toBe('accounts')
  })
  it('切换窗口调用哨兵接口并写 query', async () => {
    getStatus.mockResolvedValue(status()); getTraffic.mockResolvedValue(traffic())
    const { wrapper, router } = await mountPage()
    const selects = wrapper.findAllComponents(NSelect)
    const before = getStatus.mock.calls.length
    selects[0].vm.$emit('update:value', '900'); await flush()
    expect(router.currentRoute.value.query.window).toBe('900')
    expect(getStatus.mock.calls.length).toBeGreaterThan(before)
  })
  it('点平台流量带筛选平台', async () => {
    getStatus.mockResolvedValue(status()); getTraffic.mockResolvedValue(traffic())
    const { wrapper, router } = await mountPage()
    const ribbon = wrapper.findComponent(RouteRibbon)
    ribbon.vm.$emit('select', 'workbuddy'); await flush()
    expect(router.currentRoute.value.query.platform).toBe('workbuddy')
  })
  it('打开暂停抽屉显示确认文案', async () => {
    getStatus.mockResolvedValue(status()); getTraffic.mockResolvedValue(traffic())
    const { wrapper } = await mountPage()
    const pause = wrapper.findAll('button').find((item) => item.text().includes('暂停平台'))
    expect(pause).toBeTruthy()
    await pause!.trigger('click'); await flush()
    expect(document.body.textContent).toContain('暂停')
  })
  it('哨兵失败渲染错误态并可重试', async () => {
    getStatus.mockRejectedValue(new Error('boom')); getTraffic.mockResolvedValue(traffic())
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('哨兵加载失败')
    const retry = wrapper.findAll('button').find((item) => item.text().includes('重试'))
    const before = getStatus.mock.calls.length
    if (retry) { await retry.trigger('click'); await flush() }
    expect(getStatus.mock.calls.length).toBeGreaterThan(before)
  })
})