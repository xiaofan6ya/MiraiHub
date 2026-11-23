/**
 * 代表页（overview）的真实挂载切换：
 *   · 页面模板里的文案来自 useI18n().t；
 *   · 数据文案（英雄卡 / 用量条 / 账号结论）来自 api/overview 的 translate 参数；
 *   · 切换语言后无需重新取数，整页立即变英文，再切回中文。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import OverviewPage from '@/pages/OverviewPage.vue'
import * as overviewApi from '@/api/overview'
import { setLocale } from '@/i18n'

vi.mock('@/api/overview', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/overview')>()
  return {
    ...actual,
    getOverview: vi.fn(),
    getOverviewTraffic: vi.fn(),
    getSentinelStatus: vi.fn(),
  }
})

const getOverview = vi.mocked(overviewApi.getOverview)
const getOverviewTraffic = vi.mocked(overviewApi.getOverviewTraffic)
const getSentinelStatus = vi.mocked(overviewApi.getSentinelStatus)

let wrapper: VueWrapper | null = null

function payload(): overviewApi.OverviewPayload {
  const today = {
    requests: 10,
    ok_requests: 9,
    prompt_tokens: 100,
    completion_tokens: 50,
    total_tokens: 150,
    cached_tokens: 10,
    credits: 9.55,
  }
  return {
    platform: '',
    realm: '',
    platform_label: '全部平台',
    accounts: {
      total: 3,
      balance_remain: 12954,
      balance_total: 17583,
      disabled_balance: 0,
      used_ratio: null,
    },
    models: 41,
    keys: 5,
    today,
    window: { ...today, days: 7 },
    month: { ...today, days: 30 },
    average_credits: 66.3,
    cache: {
      cached_tokens: 10,
      prompt_tokens: 100,
      miss_tokens: 90,
      rate: 0.1,
      samples: 3,
      note: '',
      by_platform: [],
    },
    monitor: { active: 3, cooling: 0, breaker: 0, disabled: 0, no_balance: 0, total: 3, items: [] },
    sections: 'all',
    now: '2026-11-23T15:01:00Z',
  }
}

async function mountPage(): Promise<VueWrapper> {
  getOverview.mockResolvedValue(payload())
  getOverviewTraffic.mockResolvedValue(null as never)
  getSentinelStatus.mockResolvedValue({ tripped: false, accounts_affected: 0, pause: {} })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/overview',
        name: 'overview',
        component: OverviewPage,
        meta: { title: '概览', group: '概览', platformScoped: true },
      },
    ],
  })
  await router.push('/overview')
  await router.isReady()

  const host = mount(
    {
      components: { NConfigProvider, NMessageProvider, NDialogProvider },
      template: `
        <n-config-provider>
          <n-message-provider>
            <n-dialog-provider>
              <router-view />
            </n-dialog-provider>
          </n-message-provider>
        </n-config-provider>
      `,
    },
    { global: { plugins: [createPinia(), router] }, attachTo: document.body },
  )
  await flushPromises()
  return host
}

beforeEach(() => {
  setLocale('zh-CN')
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setLocale('zh-CN')
  document.body.innerHTML = ''
})

describe('概览页中英切换', () => {
  it('模板文案与数据文案一起切，切回中文不重新请求', async () => {
    wrapper = await mountPage()
    const text = (): string => wrapper!.text()
    expect(text()).toContain('剩余额度')
    expect(text()).toContain('服务运行中')
    expect(text()).toContain('账号状态')
    expect(text()).toContain('缓存命中')
    expect(text()).toContain('3 个账号') // 英雄卡 caption（数据文案）

    const calls = getOverview.mock.calls.length
    setLocale('en-US')
    await nextTick()
    await flushPromises()

    expect(text()).toContain('Remaining balance')
    expect(text()).toContain('Service running')
    expect(text()).toContain('Account status')
    expect(text()).toContain('Cache hits')
    expect(text()).toContain('All accounts healthy')
    expect(text()).not.toContain('剩余额度')
    expect(getOverview.mock.calls.length).toBe(calls)

    setLocale('zh-CN')
    await nextTick()
    expect(text()).toContain('剩余额度')
  })
})
