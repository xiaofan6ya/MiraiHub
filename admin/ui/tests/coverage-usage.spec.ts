/**
 * 覆盖率补充：UsagePage 真实挂载 —— 时间范围口径文案（QA 缺陷 ②）。
 *
 * 「今日」在 /usage 是**自然日**（北京时间 00:00 起），数据看板的「近 24 小时」是滚动窗口：
 * 两个页面都要把口径写清楚，数字不改。只新增本文件，不改 src。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import UsagePage from '@/pages/UsagePage.vue'
import { setLocale } from '@/i18n'
import { cnDayStart } from '@/api/usage'
import type { UsageOptionsPayload, UsagePayload } from '@/api/usage'
import * as usageApi from '@/api/usage'
import * as platformsApi from '@/api/platforms'

vi.mock('@/api/usage', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/usage')>()
  return { ...actual, getUsageStats: vi.fn(), getUsageOptions: vi.fn() }
})

vi.mock('@/api/platforms', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/platforms')>()
  return {
    ...actual,
    listPlatforms: vi.fn(),
    getCurrentPlatform: vi.fn(),
    putCurrentPlatform: vi.fn(),
  }
})

const getUsageStats = vi.mocked(usageApi.getUsageStats)
const getUsageOptions = vi.mocked(usageApi.getUsageOptions)

/** GET /api/stats/usage 的真实响应形状（契约 §4.1 的示例）。 */
function stats(overrides: Partial<UsagePayload> = {}): UsagePayload {
  return {
    range_days: null,
    start: '2026-11-22T16:00:00',
    end: null,
    granularity: 'hour',
    filters: { key_id: null, model: null, account_id: null, use_case: null, platform: '' },
    platform: '',
    platform_label: '全部平台',
    platforms: [],
    overview: {
      requests: 862,
      ok_requests: 850,
      failed_requests: 12,
      credits: 729.31,
      prompt_tokens: 1200000,
      completion_tokens: 340000,
      total_tokens: 1540000,
      cached_tokens: 90000,
      avg_latency_ms: 3950,
    },
    by_model: [{ label: 'DeepSeek-V4.1-Flash', model: 'dfmodel', platform: 'qoder', platform_label: 'Qoder 中国版', requests: 120, tokens: 9000, credits: 12.5 }],
    by_endpoint: [{ label: 'chat', requests: 800, tokens: 1000, credits: 100 }],
    by_key: [{ label: 'prod-key', requests: 700, tokens: 1000, credits: 100 }],
    by_account: [{ label: '193****2486', requests: 340, tokens: 1000, credits: 9, account_id: 152 }],
    by_result: [{ label: '成功', requests: 850 }, { label: 'rate_limit', requests: 12 }],
    account_models: [],
    usage_scope: 'gateway_only',
    trend: [
      {
        bucket: '2026-11-23 10:00',
        prompt_tokens: 1000,
        completion_tokens: 300,
        cached_tokens: 0,
        credits: 1.2,
        requests: 12,
      },
    ],
    ...overrides,
  }
}

function options(): UsageOptionsPayload {
  return {
    models: [{ value: 'dfmodel', label: 'DeepSeek-V4.1-Flash', requests: 120 }],
    use_cases: ['chat'],
    keys: [{ id: 3, name: 'prod-key' }],
    accounts: [{ id: 152, name: '193****2486' }],
    platforms: [],
  }
}

const ChartStub = defineComponent({
  name: 'Chart',
  props: { option: { type: Object, default: () => ({}) }, height: { type: String, default: '' } },
  template: '<div class="chart-stub" />',
})
const DateRangeStub = defineComponent({ name: 'DateRange', template: '<div class="daterange-stub" />' })

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>`,
})

let mounted: { wrapper: VueWrapper; router: Router } | null = null

async function flush(): Promise<void> {
  await flushPromises()
  await nextTick()
  await flushPromises()
}

async function mountPage(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/usage', name: 'usage', component: UsagePage },
      { path: '/logs', name: 'logs', component: defineComponent({ template: '<div class="logs-stub" />' }) },
      { path: '/keys', name: 'keys', component: defineComponent({ template: '<div class="keys-stub" />' }) },
      { path: '/ip', name: 'ip', component: defineComponent({ template: '<div class="ip-stub" />' }) },
    ],
  })
  await router.push('/usage')
  await router.isReady()
  const wrapper = mount(Host, {
    global: { plugins: [pinia, router], stubs: { Chart: ChartStub, DateRange: DateRangeStub } },
    attachTo: document.body,
  })
  await flush()
  await flush()
  mounted = { wrapper, router }
  return mounted
}

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('UsagePage 时间范围口径', () => {
  it('默认窗口仍是「今日（自然日）」：北京时间 00:00 起，请求参数不变', async () => {
    vi.mocked(platformsApi.listPlatforms).mockResolvedValue({ items: [] } as never)
    vi.mocked(platformsApi.getCurrentPlatform).mockResolvedValue({ platform: 'workbuddy', realm: '' } as never)
    vi.mocked(platformsApi.putCurrentPlatform).mockResolvedValue(undefined as never)
    getUsageStats.mockResolvedValue(stats())
    getUsageOptions.mockResolvedValue(options())

    const { wrapper } = await mountPage()

    expect(wrapper.find('h1').text()).toContain('使用记录')
    // 6 张 KPI 卡（v2.1 由 StatStrip 换成 KpiCard）
    const labels = wrapper.findAll('.mh-kpi__label').map((item) => item.text())
    expect(labels).toEqual(['请求数', '成功', '失败', 'Token 输入', 'Token 输出', '积分'])
    expect(wrapper.text()).toContain('共 862 次 · 成功 850 · 失败 12 · 积分 729.31')

    // chips 里的时间口径是自然日
    expect(wrapper.find('.mh-filter__chips').text()).toContain('今日（自然日）')

    // 页脚把两种口径写清楚
    const foot = wrapper.find('.usage-footnote').text()
    expect(foot).toContain('今日（自然日）」= 北京时间 00:00 起')
    expect(foot).toContain('数据看板的「近 24 小时」是滚动窗口')

    // 口径文案不改数据：默认窗口仍是北京今天 00:00 到现在，不带 end
    const params = getUsageStats.mock.calls[0][0]
    expect(params.granularity).toBe('hour')
    expect(params.end).toBeUndefined()
    expect(Math.abs(Date.parse(String(params.start)) - cnDayStart(Date.now()))).toBeLessThan(60_000)
  })

  it('无数据时给出空态，不伪造 0 条统计', async () => {
    vi.mocked(platformsApi.listPlatforms).mockResolvedValue({ items: [] } as never)
    vi.mocked(platformsApi.getCurrentPlatform).mockResolvedValue({ platform: 'workbuddy', realm: '' } as never)
    vi.mocked(platformsApi.putCurrentPlatform).mockResolvedValue(undefined as never)
    getUsageStats.mockResolvedValue(
      stats({
        overview: { ...stats().overview, requests: 0, ok_requests: 0, failed_requests: 0, credits: 0 },
        trend: [],
        by_model: [],
        by_key: [],
        by_endpoint: [],
        by_account: [],
        by_result: [],
      }),
    )
    getUsageOptions.mockResolvedValue(options())

    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('该时间范围内没有任何请求记录。')
    expect(wrapper.findAll('.chart-stub')).toHaveLength(0)
  })
})

describe('UsagePage 中英切换', () => {
  it('切到英文：6 格统计 / 汇总 / chips / 页脚口径出英文', async () => {
    vi.mocked(platformsApi.listPlatforms).mockResolvedValue({ items: [] } as never)
    vi.mocked(platformsApi.getCurrentPlatform).mockResolvedValue({ platform: 'workbuddy', realm: '' } as never)
    vi.mocked(platformsApi.putCurrentPlatform).mockResolvedValue(undefined as never)
    getUsageStats.mockResolvedValue(stats())
    getUsageOptions.mockResolvedValue(options())

    setLocale('zh-CN')
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('共 862 次 · 成功 850 · 失败 12 · 积分 729.31')

    setLocale('en-US')
    await flush()

    expect(wrapper.find('h1').text()).toContain('Usage')
    const labels = wrapper.findAll('.mh-kpi__label').map((item) => item.text())
    expect(labels).toEqual(['Requests', 'Success', 'Failed', 'Input tokens', 'Output tokens', 'Credits'])
    expect(wrapper.text()).toContain('862 requests · 850 OK · 12 failed · 729.31 credits')
    expect(wrapper.find('.mh-filter__chips').text()).toContain('Today (calendar day)')
    expect(wrapper.find('.usage-footnote').text()).toContain('calendar day) from 00:00 Beijing time')
    expect(wrapper.text()).not.toContain('使用记录')

    setLocale('zh-CN')
    await flush()
    expect(wrapper.text()).toContain('使用记录')
    expect(wrapper.text()).toContain('共 862 次 · 成功 850 · 失败 12 · 积分 729.31')
  })
})
