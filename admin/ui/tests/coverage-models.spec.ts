/** 覆盖率补充：ModelsPage 真实挂载交互。只新增本文件，不改 src。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider, NSelect } from 'naive-ui'
import ModelsPage from '@/pages/ModelsPage.vue'
import * as modelsApi from '@/api/models'
import type { ModelConfigItem } from '@/api/models'

vi.mock('@/api/models', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/models')>()
  return { ...actual, listModelConfigs: vi.fn(), listModelHealth: vi.fn(), syncModels: vi.fn(), batchToggleModels: vi.fn(), updateModelConfig: vi.fn(), deleteModelConfig: vi.fn(), speedTestModel: vi.fn() }
})
const listConfigs = vi.mocked(modelsApi.listModelConfigs)
const listHealth = vi.mocked(modelsApi.listModelHealth)
const syncModels = vi.mocked(modelsApi.syncModels)

function item(): ModelConfigItem {
  return {
    id: 1, level: 'system', platform: 'workbuddy', realm: '', platform_label: 'WorkBuddy',
    model_id: 'deepseek-v4.1-flash', client_id: 'deepseek-v4.1-flash-workbuddy', upstream_id: 'deepseek-v4.1-flash',
    caps: { context_window: 128000, max_output_tokens: 16000, supports_images: false, supports_reasoning: true, supports_tools: true },
    enabled: true, note: 'DeepSeek V4.1 Flash', sort: null, description: '', metadata: {}, model_type: 'text',
    tags: ['reasoning'], billing_mode: 'token_multiplier', credit_multiplier: 1, credits_raw: 'x1.00',
    is_free: false, plan_free: null, video_capability: null, official_visible: true, official_order: 0,
    created_at: '2026-11-01T12:00:00',
  } as ModelConfigItem
}

const Host = defineComponent({ components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>` })
let mounted: { wrapper: VueWrapper; router: Router } | null = null
async function flush() { await flushPromises(); await nextTick(); await flushPromises() }
async function mountPage() {
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/models', name: 'models', component: ModelsPage }] })
  await router.push('/models'); await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flush(); await flush()
  mounted = { wrapper, router }
  return mounted
}
afterEach(() => { mounted?.wrapper.unmount(); mounted = null; document.body.innerHTML = '' })

describe('ModelsPage 覆盖率补测', () => {
  it('首屏渲染模型汇总，官方目录提示存在', async () => {
    listConfigs.mockResolvedValue({ items: [item()], platforms: [], official_total: 2 } as never)
    listHealth.mockResolvedValue({ items: [] } as never)
    const { wrapper } = await mountPage()
    expect(wrapper.find('h1').text()).toContain('模型配置')
    expect(wrapper.text()).toContain('共 1 个模型')
    expect(wrapper.text()).toContain('官方目录共 2 个模型')
  })
  it('搜索写入 query', async () => {
    listConfigs.mockResolvedValue({ items: [item()], platforms: [], official_total: 0 } as never)
    listHealth.mockResolvedValue({ items: [] } as never)
    const { wrapper, router } = await mountPage()
    const input = wrapper.findAll('input').find((x) => (x.attributes('placeholder') || '').includes('搜索模型'))
    expect(input).toBeTruthy()
    await input!.setValue('deepseek')
    await new Promise((r) => setTimeout(r, 400)); await flush()
    expect(router.currentRoute.value.query.q).toBe('deepseek')
  })
  it('厂商筛选写入 query', async () => {
    listConfigs.mockResolvedValue({ items: [item()], platforms: [], official_total: 0 } as never)
    listHealth.mockResolvedValue({ items: [] } as never)
    const { wrapper, router } = await mountPage()
    const selects = wrapper.findAllComponents(NSelect)
    selects[0].vm.$emit('update:value', 'deepseek'); await flush()
    expect(router.currentRoute.value.query.vendor).toBe('deepseek')
  })
  it('同步目录调用接口并重新加载', async () => {
    listConfigs.mockResolvedValue({ items: [item()], platforms: [], official_total: 1 } as never)
    listHealth.mockResolvedValue({ items: [] } as never)
    syncModels.mockResolvedValue({ platforms: [] } as never)
    const { wrapper } = await mountPage()
    const before = listConfigs.mock.calls.length
    const sync = wrapper.findAll('button').find((b) => b.text().includes('同步目录'))
    expect(sync).toBeTruthy()
    await sync!.trigger('click'); await flush()
    expect(syncModels).toHaveBeenCalled()
    expect(listConfigs.mock.calls.length).toBeGreaterThan(before)
  })
  it('加载失败渲染错误态并可重试', async () => {
    listConfigs.mockRejectedValue(new Error('boom'))
    listHealth.mockResolvedValue({ items: [] } as never)
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('加载模型失败')
    const retry = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    const before = listConfigs.mock.calls.length
    if (retry) { await retry.trigger('click'); await flush() }
    expect(listConfigs.mock.calls.length).toBeGreaterThan(before)
  })
})