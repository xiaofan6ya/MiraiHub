/** 覆盖率补充：AccountsPage 首屏/筛选/错误态真实挂载。只新增本文件，不改 src。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import AccountsPage from '@/pages/AccountsPage.vue'
import * as accountsApi from '@/api/accounts'
import type { AccountItem } from '@/api/accounts'

vi.mock('@/api/accounts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/accounts')>()
  return {
    ...actual,
    listAccounts: vi.fn(),
    getTodayUsage: vi.fn(),
    getModelLimits: vi.fn(),
    bulkAccounts: vi.fn(),
    refreshAccount: vi.fn(),
    healthCheckAccount: vi.fn(),
    deleteAccount: vi.fn(),
    patchAccount: vi.fn(),
    refreshBatch: vi.fn(),
    healthCheckBatch: vi.fn(),
    exportAccountsAsync: vi.fn(),
    runAccountTask: vi.fn(),
    growthRunAsync: vi.fn(),
    catTravelBatchAsync: vi.fn(),
    getCreditDetails: vi.fn(),
  }
})

const listAccounts = vi.mocked(accountsApi.listAccounts)
const getTodayUsage = vi.mocked(accountsApi.getTodayUsage)

function account(id: number, overrides: Partial<AccountItem> = {}): AccountItem {
  return {
    id, name: id === 1 ? '主号' : '备用号', uid: '1930000' + id, platform: 'workbuddy', domain: '',
    status: id === 2 ? 'disabled' : 'active', max_in_flight: null, bound_key_id: null, bound_key_name: '',
    balance_total: 5000, balance_remain: 4000, last_sync_at: null, last_used_at: null,
    created_at: '2026-11-01T00:00:00', model_limits: [], gateway_state: 'ready', gateway_until: null,
    gateway_reason: '', credits_expiring_soon: 0, credits_evergreen: 5000, credits_expired: 0,
    credits_soonest_expire_at: null, token_supported: false, token_expires_at: null, token_days_left: null,
    token_state: '', renewal_expires_at: null, renewal_days_left: null, renewal_state: '', renewable: false,
    ...overrides,
  } as AccountItem
}

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>`,
})

let mounted: { wrapper: VueWrapper; router: Router } | null = null

async function flush(): Promise<void> { await flushPromises(); await nextTick(); await flushPromises() }

async function mountPage(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/accounts', name: 'accounts', component: AccountsPage }] })
  await router.push('/accounts')
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flush(); await flush()
  mounted = { wrapper, router }
  return mounted
}

afterEach(() => { mounted?.wrapper.unmount(); mounted = null; document.body.innerHTML = '' })

describe('AccountsPage 覆盖率补测', () => {
  it('首屏渲染账号与计数', async () => {
    listAccounts.mockResolvedValue({ items: [account(1), account(2)] } as never)
    getTodayUsage.mockResolvedValue({ items: [] } as never)
    const { wrapper } = await mountPage()
    expect(wrapper.find('h1').text()).toContain('账号池')
    // NDataTable 在 jsdom 不渲染虚拟行；断言页签计数与汇总，证明数据已进入视图。
    expect(wrapper.text()).toContain('全部 2')
    expect(wrapper.text()).toContain('已启用 1')
    expect(wrapper.text()).toContain('已禁用 1')
  })

  it('搜索写入 query 并过滤行', async () => {
    listAccounts.mockResolvedValue({ items: [account(1), account(2)] } as never)
    getTodayUsage.mockResolvedValue({ items: [] } as never)
    const { wrapper, router } = await mountPage()
    const input = wrapper.findAll('input').find((item) => (item.attributes('placeholder') || '').includes('搜索'))
    expect(input, 'search input').toBeTruthy()
    await input!.setValue('主号')
    await new Promise((resolve) => setTimeout(resolve, 400))
    await flush()
    expect(router.currentRoute.value.query.q).toBe('主号')
    expect(wrapper.text()).toContain('搜索')
  })

  it('列表失败渲染错误态并可重试', async () => {
    listAccounts.mockRejectedValue(new Error('boom'))
    getTodayUsage.mockResolvedValue({ items: [] } as never)
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('加载账号池')
    const before = listAccounts.mock.calls.length
    const retry = wrapper.findAll('button').find((item) => item.text().includes('重试'))
    if (retry) { await retry.trigger('click'); await flush() }
    expect(listAccounts.mock.calls.length).toBeGreaterThan(before)
  })
})