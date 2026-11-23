/** 覆盖率补充：MarketPage 首屏/搜索/错误态。只新增本文件，不改 src。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import MarketPage from '@/pages/MarketPage.vue'
import * as marketApi from '@/api/market'
import type { ModelMarketPayload } from '@/api/market'

vi.mock('@/api/market', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/market')>()
  return { ...actual, listModelMarket: vi.fn(), getModelAggregate: vi.fn(), getMarketTrend: vi.fn() }
})
const listMarket = vi.mocked(marketApi.listModelMarket)
const getAggregate = vi.mocked(marketApi.getModelAggregate)
const getTrend = vi.mocked(marketApi.getMarketTrend)

const payload: ModelMarketPayload = {
  window_hours: 24, start: '2026-11-22T11:00:00Z', end: '2026-11-23T11:00:00Z', source: 'rollup', total: 1, available_models: 1,
  items: [{ key: 'deepseekv41flash', name: 'DeepSeek-V4.1-Flash', platforms: ['workbuddy'], platform_labels: ['WorkBuddy'], models: ['deepseek-v4.1-flash'], requests: 12400, success_rate: 0.982, tokens: 5600000, credits: 234.56, avg_latency_ms: 850, avg_ttfb_ms: 120, tps: 128.5, pricing: [{ platform: 'workbuddy', platform_label: 'WorkBuddy', model_id: 'deepseek-v4.1-flash', multiplier: 1, free: false }], free: false, min_multiplier: 1, active_accounts: 15, exhausted_accounts: 2, available: true }],
}

const Host = defineComponent({ components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>` })
let mounted: { wrapper: VueWrapper; router: Router } | null = null
async function flush() { await flushPromises(); await nextTick(); await flushPromises() }
async function mountPage() {
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/market', name: 'market', component: MarketPage }] })
  await router.push('/market'); await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flush(); await flush()
  mounted = { wrapper, router }
  return mounted
}
afterEach(() => { mounted?.wrapper.unmount(); mounted = null; document.body.innerHTML = '' })

describe('MarketPage 覆盖率补测', () => {
  it('首屏渲染模型广场与统计', async () => {
    listMarket.mockResolvedValue(payload); getAggregate.mockResolvedValue(null as never); getTrend.mockResolvedValue({ trend: [] } as never)
    const { wrapper } = await mountPage()
    expect(wrapper.find('h1').text()).toContain('模型广场')
    expect(wrapper.text()).toContain('DeepSeek-V4.1-Flash')
  })
  it('搜索写入 query', async () => {
    listMarket.mockResolvedValue(payload); getAggregate.mockResolvedValue(null as never); getTrend.mockResolvedValue({ trend: [] } as never)
    const { wrapper, router } = await mountPage()
    const input = wrapper.findAll('input').find((x) => (x.attributes('placeholder') || '').includes('搜索模型'))
    expect(input, 'search input').toBeTruthy()
    await input!.setValue('deepseek')
    await new Promise((r) => setTimeout(r, 400)); await flush()
    expect(router.currentRoute.value.query.q).toBe('deepseek')
  })
  it('主接口失败渲染错误态并可重试', async () => {
    listMarket.mockRejectedValue(new Error('boom')); getAggregate.mockResolvedValue(null as never); getTrend.mockResolvedValue({ trend: [] } as never)
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('加载模型广场')
    const retry = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    const before = listMarket.mock.calls.length
    if (retry) { await retry.trigger('click'); await flush() }
    expect(listMarket.mock.calls.length).toBeGreaterThan(before)
  })
})