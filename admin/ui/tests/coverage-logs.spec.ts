/** 覆盖率补充：LogsPage 首屏/搜索/错误态。只新增本文件，不改 src。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import LogsPage from '@/pages/LogsPage.vue'
import * as logsApi from '@/api/logs'

vi.mock('@/api/logs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/logs')>()
  return { ...actual, listUsageLogs: vi.fn(), getUsageSummary: vi.fn(), listLogAccounts: vi.fn(), getLiveRequests: vi.fn(), getCaptureSettings: vi.fn(), requestStreamTicket: vi.fn(), downloadLogsExport: vi.fn(), getUsageLog: vi.fn(), getCaptureBody: vi.fn() }
})
const listLogs = vi.mocked(logsApi.listUsageLogs)
const getSummary = vi.mocked(logsApi.getUsageSummary)
const listAccounts = vi.mocked(logsApi.listLogAccounts)
const getLive = vi.mocked(logsApi.getLiveRequests)
const getCapture = vi.mocked(logsApi.getCaptureSettings)
const getTicket = vi.mocked(logsApi.requestStreamTicket)

class FakeEventSource { onmessage: ((e: MessageEvent) => void) | null = null; onerror: (() => void) | null = null; addEventListener() {} close() {} }

const Host = defineComponent({ components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>` })
let mounted: { wrapper: VueWrapper; router: Router } | null = null
async function flush() { await flushPromises(); await nextTick(); await flushPromises() }
async function mountPage() {
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/logs', name: 'logs', component: LogsPage }] })
  await router.push('/logs'); await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flush(); await flush()
  mounted = { wrapper, router }
  return mounted
}
beforeEach(() => {
  vi.stubGlobal('EventSource', FakeEventSource)
  listLogs.mockResolvedValue({ items: [], has_more: false, total: 0 } as never)
  getSummary.mockResolvedValue({ items: [] } as never)
  listAccounts.mockResolvedValue({ items: [] } as never)
  getLive.mockResolvedValue({ items: [], running: 0 } as never)
  getCapture.mockResolvedValue({ enabled: false } as never)
  getTicket.mockResolvedValue({ ticket: 't' } as never)
})
afterEach(() => { mounted?.wrapper.unmount(); mounted = null; document.body.innerHTML = ''; vi.unstubAllGlobals() })

describe('LogsPage 覆盖率补测', () => {
  it('首屏渲染用量日志与空表', async () => {
    const { wrapper } = await mountPage()
    expect(wrapper.find('h1').text()).toContain('用量日志')
    expect(wrapper.text()).toContain('按账号汇总')
  })
  it('搜索写入 query', async () => {
    const { wrapper, router } = await mountPage()
    const input = wrapper.findAll('input').find((x) => (x.attributes('placeholder') || '') === '全部模型')
    expect(input, 'model input').toBeTruthy()
    await input!.setValue('deepseek')
    await new Promise((r) => setTimeout(r, 400)); await flush()
    expect(router.currentRoute.value.query.model).toBe('deepseek')
  })
  it('列表失败渲染错误态并可重试', async () => {
    listLogs.mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('加载用量日志失败')
    const retry = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    const before = listLogs.mock.calls.length
    if (retry) { await retry.trigger('click'); await flush() }
    expect(listLogs.mock.calls.length).toBeGreaterThan(before)
  })
})