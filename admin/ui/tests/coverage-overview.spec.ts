/**
 * 覆盖率补充：OverviewPage 真实挂载（首屏 / 告警 / 缓存 / 时间范围 / 平台上下文 / 错误态）。
 *
 * 只 mount 页面 + mock @/api/overview、@/api/platforms，不改 src。
 * 断言口径来自 docs/ui-v2/contracts/overview.md（末尾「实现对照」）与后端真实响应形状。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia, type Pinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import OverviewPage from '@/pages/OverviewPage.vue'
import RouteRibbon from '@/components/charts/RouteRibbon.vue'
import { ApiError } from '@/api/http'
import { usePlatformStore } from '@/stores/platform'
import * as overviewApi from '@/api/overview'
import type { OverviewMonitor, OverviewPayload, SentinelStatusPayload } from '@/api/overview'
import * as platformsApi from '@/api/platforms'
import type { PlatformSpec } from '@/api/platforms'
import type { StatsDashboardPayload } from '@/api/stats'

vi.mock('@/api/overview', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/overview')>()
  return {
    ...actual,
    getOverview: vi.fn(),
    getOverviewTraffic: vi.fn(),
    getSentinelStatus: vi.fn(),
  }
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

const getOverview = vi.mocked(overviewApi.getOverview)
const getOverviewTraffic = vi.mocked(overviewApi.getOverviewTraffic)
const getSentinelStatus = vi.mocked(overviewApi.getSentinelStatus)

/* ------------------------------------------------------------------ *
 * 后端真实响应形状
 * ------------------------------------------------------------------ */

const TODAY = {
  requests: 801,
  ok_requests: 800,
  prompt_tokens: 32000,
  completion_tokens: 9000,
  total_tokens: 41000,
  cached_tokens: 3000,
  credits: 9.55,
}

const WINDOW = {
  requests: 1250,
  ok_requests: 1200,
  prompt_tokens: 50000,
  completion_tokens: 15000,
  total_tokens: 65000,
  cached_tokens: 5000,
  credits: 198.91,
  days: 7,
}

function monitor(overrides: Partial<OverviewMonitor> = {}): OverviewMonitor {
  return {
    active: 3,
    cooling: 0,
    breaker: 0,
    no_balance: 0,
    disabled: 0,
    total: 3,
    items: [],
    ...overrides,
  }
}

function payload(overrides: Partial<OverviewPayload> = {}): OverviewPayload {
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
    today: TODAY,
    window: WINDOW,
    month: { ...WINDOW, days: 30 },
    average_credits: 66.3,
    cache: {
      cached_tokens: 5000,
      prompt_tokens: 50000,
      miss_tokens: 45000,
      rate: 0.0909,
      samples: 1200,
      note: '',
      by_platform: [],
    },
    monitor: monitor(),
    sections: 'all',
    now: '2026-11-23T15:01:00Z',
    ...overrides,
  }
}

function traffic(overrides: Partial<StatsDashboardPayload> = {}): StatsDashboardPayload {
  return {
    window: {
      days: 1,
      granularity: 'hour',
      start: '2026-11-23T00:00:00Z',
      end: '2026-11-24T00:00:00Z',
      minutes: 1440,
    },
    filters: { platform: '', model: '' },
    platform_label: '全部平台',
    summary: {
      requests: 10000,
      successes: 9900,
      failures: 100,
      success_rate: 0.99,
      credits: 1234,
      total_tokens: 650000,
      prompt_tokens: 500000,
      completion_tokens: 150000,
      cached_tokens: 50000,
      cache_hit_rate: 0.1,
      rpm: 6.9,
      tpm: 451,
      avg_latency_ms: 450,
      avg_ttfb_ms: 120,
      throughput_tps: 95.5,
      window_minutes: 1440,
    },
    models: [],
    platforms: [
      {
        value: 'workbuddy',
        label: 'WorkBuddy',
        requests: 8000,
        success_rate: 0.998,
        tokens: 400000,
        credits: 900,
      },
      {
        value: 'trae',
        label: 'Trae',
        requests: 2000,
        success_rate: null,
        tokens: 100000,
        credits: 200,
      },
    ],
    distribution: { buckets: [], series: [] },
    trend: { buckets: [], total_requests: [], series: [] },
    ranking: [],
    errors: [],
    source: 'rollup',
    ...overrides,
  }
}

function sentinel(overrides: Partial<SentinelStatusPayload> = {}): SentinelStatusPayload {
  return { tripped: false, accounts_affected: 0, pause: {}, ...overrides }
}

function spec(id: string, label: string, extra: Partial<PlatformSpec> = {}): PlatformSpec {
  return {
    id,
    label,
    enabled: true,
    supports_local_scan: false,
    auth_mode: '',
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
    managed: false,
    actions: [],
    quota_unit: 'credits',
    quota_unit_label: '',
    account_count: null,
    realm_counts: {},
    ...extra,
  } as PlatformSpec
}

/* ------------------------------------------------------------------ *
 * 挂载助手
 * ------------------------------------------------------------------ */

const PageStub = defineComponent({ template: '<div class="page-stub" />' })

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>`,
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

async function mountPage(
  options: { path?: string; platforms?: PlatformSpec[]; current?: string } = {},
): Promise<Mounted> {
  const pinia = createPinia()
  const store = usePlatformStore(pinia)
  store.items = options.platforms ?? [spec('workbuddy', 'WorkBuddy'), spec('trae', 'Trae')]
  store.platform = options.current ?? 'workbuddy'

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/overview', name: 'overview', component: OverviewPage },
      { path: '/accounts', name: 'accounts', component: PageStub },
      { path: '/credits', name: 'credits', component: PageStub },
      { path: '/sentinel', name: 'sentinel', component: PageStub },
      { path: '/platforms', name: 'platforms', component: PageStub },
    ],
  })
  await router.push(options.path ?? '/overview')
  await router.isReady()

  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flush()
  await flush()

  mounted = { wrapper, router, pinia }
  return mounted
}

function bodyText(): string {
  return String(document.body.textContent || '')
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((item) => (item.text() || '').includes(text))
}

beforeEach(() => {
  getOverview.mockReset().mockResolvedValue(payload())
  getOverviewTraffic.mockReset().mockResolvedValue(traffic())
  getSentinelStatus.mockReset().mockResolvedValue(sentinel())
  vi.mocked(platformsApi.listPlatforms).mockReset().mockResolvedValue({ items: [] } as never)
  vi.mocked(platformsApi.getCurrentPlatform)
    .mockReset()
    .mockResolvedValue({ platform: 'workbuddy', realm: '' } as never)
  vi.mocked(platformsApi.putCurrentPlatform).mockReset().mockResolvedValue(undefined as never)
})

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
})

describe('OverviewPage 覆盖率补测', () => {
  it('首屏渲染状态行、英雄卡、用量条、流量带、账号状态与缓存卡', async () => {
    const { wrapper } = await mountPage()

    expect(wrapper.find('h1').text()).toContain('概览')
    // 状态行：运行状态 / Base URL / 平台与模型数 / 当前平台 / 更新时间
    expect(wrapper.text()).toContain('服务运行中')
    expect(wrapper.find('.ov-status__url').text()).toContain('/v1')
    expect(wrapper.text()).toContain('2 个平台 · 41 个模型')
    expect(wrapper.text()).toContain('当前：全部平台')
    expect(wrapper.text()).toContain('更新于')

    // 英雄卡：剩余额度 + 总额度说明 + 今日 / N 天 / 户均
    expect(wrapper.find('.ov-balance__value').text()).toBe('12,954')
    expect(wrapper.text()).toContain('总额度 17,583 · 3 个账号')
    expect(wrapper.text()).toContain('今日 9.55')
    expect(wrapper.text()).toContain('户均 66.3')

    // 用量条五格（请求 / 成功率 / 输入 / 输出 / 合计）
    expect(wrapper.text()).toContain('成功 1,200 · 失败 50')
    expect(wrapper.text()).toContain('96.0%')
    expect(wrapper.text()).toContain('缓存命中 5,000')

    // 流量带：线路名 + 副标题
    expect(wrapper.findComponent(RouteRibbon).exists()).toBe(true)
    expect(wrapper.findAll('.mh-ribbon__lane-name').map((item) => item.text())).toEqual([
      'WorkBuddy',
      'Trae',
    ])
    expect(wrapper.text()).toContain('今日 · 点击线路切换平台')

    // 账号状态：5 行固定顺序 + 结论
    expect(wrapper.findAll('.ov-monitor__label').map((item) => item.text())).toEqual([
      '活跃',
      '冷却',
      '熔断',
      '额度耗尽',
      '已禁用',
    ])
    expect(wrapper.findAll('.ov-monitor__count').map((item) => item.text())).toEqual([
      '3',
      '0',
      '0',
      '0',
      '0',
    ])
    expect(wrapper.text()).toContain('全部账号正常')

    // 缓存卡：Ring + 3 个 KV + 默认说明
    expect(wrapper.find('.mh-ring__value').text()).toBe('9%')
    expect(wrapper.findAll('.mh-kv__label').map((item) => item.text())).toEqual([
      '输入命中',
      '输入总量',
      '样本请求',
    ])
    expect(wrapper.text()).toContain('只统计含缓存字段的上游响应')

    // 一切正常时没有告警横幅
    expect(wrapper.findAll('.ov-alert')).toHaveLength(0)
  })

  it('告警横幅按封控 / 暂停 / 冷却 / 额度不足顺序渲染，并给出去处', async () => {
    getOverview.mockResolvedValue(
      payload({
        accounts: {
          total: 3,
          balance_remain: 12954,
          balance_total: 17583,
          disabled_balance: 0,
          used_ratio: 0.85,
        },
        monitor: monitor({ active: 1, cooling: 2, total: 3 }),
      }),
    )
    getSentinelStatus.mockResolvedValue(
      sentinel({ tripped: true, accounts_affected: 5, pause: { trae: { until: 1_800_000_000 } } }),
    )
    const { wrapper } = await mountPage()

    const alerts = wrapper.findAll('.ov-alert')
    expect(alerts).toHaveLength(4)
    expect(alerts[0].classes()).toContain('is-danger')
    expect(alerts[1].classes()).toContain('is-warn')
    expect(wrapper.text()).toContain('5 个账号被上游封控，已达批次阈值')
    expect(wrapper.text()).toContain('有 1 个平台处于暂停状态')
    expect(wrapper.text()).toContain('2 个账号冷却 / 熔断中')
    expect(wrapper.text()).toContain('剩余额度不足 15%')
    expect(wrapper.text()).toContain('剩余 12,954 / 总额度 17,583')

    expect(wrapper.findAll('.ov-alert__link').map((item) => item.attributes('href'))).toEqual([
      '/sentinel',
      '/sentinel',
      '/accounts?status=cooldown',
      '/credits',
    ])
  })

  it('哨兵接口失败时不冒充异常，也不阻塞首屏', async () => {
    getSentinelStatus.mockRejectedValue(new ApiError(500, 'sentinel down'))
    const { wrapper } = await mountPage()

    expect(wrapper.findAll('.ov-alert')).toHaveLength(0)
    expect(wrapper.find('.ov-balance__value').text()).toBe('12,954')
  })

  it('缓存命中：有样本给百分比，无样本显示 — 并沿用后端说明', async () => {
    const withRate = await mountPage()
    expect(withRate.wrapper.find('.mh-ring__value').text()).toBe('9%')
    withRate.wrapper.unmount()
    mounted = null

    getOverview.mockResolvedValue(
      payload({
        cache: {
          cached_tokens: 0,
          prompt_tokens: 0,
          miss_tokens: 0,
          rate: null,
          samples: 0,
          note: '该平台不支持缓存统计',
          by_platform: [],
        },
      }),
    )
    const empty = await mountPage()
    expect(empty.wrapper.find('.mh-ring__value').text()).toBe('—')
    expect(empty.wrapper.text()).toContain('该平台不支持缓存统计')
    expect(empty.wrapper.findAll('.mh-kv__value').map((item) => item.text())).toEqual([
      '0',
      '0',
      '0',
    ])
  })

  it('已禁用账号余额单独说明', async () => {
    getOverview.mockResolvedValue(
      payload({
        accounts: {
          total: 3,
          balance_remain: 12954,
          balance_total: 17583,
          disabled_balance: 125,
          used_ratio: null,
        },
      }),
    )
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('另有 125 在已禁用账号上，不参与统计')
  })

  it('没有账号时账号卡走空状态，可跳到账号池', async () => {
    getOverview.mockResolvedValue(payload({ monitor: monitor({ active: 0, total: 0, items: [] }) }))
    const { wrapper, router } = await mountPage()

    expect(wrapper.find('.ov-monitor').exists()).toBe(false)
    expect(wrapper.text()).toContain('还没有账号。先到账号池添加或导入账号。')

    const go = buttonByText(wrapper, '去账号池')
    expect(go).toBeTruthy()
    await go!.trigger('click')
    await flush()
    expect(router.currentRoute.value.path).toBe('/accounts')
  })

  it('账号冷却时结论给出带 status 过滤的链接', async () => {
    getOverview.mockResolvedValue(payload({ monitor: monitor({ active: 1, cooling: 2, total: 3 }) }))
    const { wrapper } = await mountPage()

    const conclusion = wrapper.find('.ov-conclusion')
    expect(conclusion.text()).toContain('2 个账号冷却中，查看')
    expect(conclusion.find('a').attributes('href')).toBe('/accounts?status=cooldown')
  })

  it('流量带没有数据时显示空提示，不发线路', async () => {
    getOverviewTraffic.mockResolvedValue(traffic({ platforms: [] }))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('当前窗口没有平台流量。发起一次调用后这里会出现分流线路。')
    expect(wrapper.text()).toContain('暂无流量')
    expect(wrapper.findAll('.mh-ribbon__lane')).toHaveLength(0)
  })

  it('点击流量带线路切平台并跳到平台管理', async () => {
    const { wrapper, router, pinia } = await mountPage()
    const store = usePlatformStore(pinia)

    const lanes = wrapper.findAll('.mh-ribbon__lane-btn')
    expect(lanes).toHaveLength(2)
    await lanes[1].trigger('click')
    await flush()

    expect(store.currentId).toBe('trae')
    expect(router.currentRoute.value.path).toBe('/platforms')
  })

  it('切换时间范围写 query 并重新取数', async () => {
    const { wrapper, router } = await mountPage()
    const before = getOverview.mock.calls.length

    const seven = buttonByText(wrapper, '7 天')
    expect(seven).toBeTruthy()
    await seven!.trigger('click')
    await flush()

    expect(router.currentRoute.value.query.days).toBe('7')
    expect(getOverview.mock.calls.length).toBeGreaterThan(before)
    expect(getOverview.mock.calls[getOverview.mock.calls.length - 1]?.[0]).toBe(7)
    expect(getOverviewTraffic.mock.calls[getOverviewTraffic.mock.calls.length - 1]?.[0]).toBe(7)
    expect(wrapper.findAll('.mh-card__title').map((item) => item.text())).toContain('近 7 天流量')
  })

  it('从分享链接打开时按 query 的时间范围取数', async () => {
    const { wrapper } = await mountPage({ path: '/overview?days=30' })

    expect(getOverview.mock.calls[0]?.[0]).toBe(30)
    expect(wrapper.text()).toContain('近 30 天流量')
    const group = wrapper.find('[aria-label="统计区间"]')
    const thirty = group.findAll('button').find((item) => item.text().includes('30 天'))
    expect(thirty?.attributes('aria-checked')).toBe('true')
  })

  it('刷新失败只提示，保留上次成功的数据', async () => {
    const { wrapper } = await mountPage()
    getOverview.mockRejectedValue(new ApiError(500, 'refresh boom', { trace: 'upstream 500' }))

    const refresh = buttonByText(wrapper, '刷新')
    expect(refresh).toBeTruthy()
    await refresh!.trigger('click')
    await flush()
    await flush()

    expect(wrapper.find('.ov-balance__value').text()).toBe('12,954')
    expect(wrapper.findAll('.mh-error')).toHaveLength(0)
    expect(bodyText()).toContain('刷新概览失败：refresh boom')
  })

  it('首屏失败渲染错误态，详情可展开且重试后恢复', async () => {
    getOverview.mockRejectedValue(new ApiError(502, 'boom', { trace: 'stack-line-42' }))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('加载概览失败：boom')
    expect(wrapper.find('.ov-balance').exists()).toBe(false)

    const toggle = wrapper.find('.mh-error__toggle')
    expect(toggle.exists()).toBe(true)
    await toggle.trigger('click')
    await flush()
    expect(wrapper.find('.mh-error__detail').text()).toContain('stack-line-42')

    getOverview.mockResolvedValue(payload())
    const retry = buttonByText(wrapper, '重试')
    expect(retry).toBeTruthy()
    await retry!.trigger('click')
    await flush()
    await flush()

    expect(wrapper.findAll('.mh-error')).toHaveLength(0)
    expect(wrapper.find('.ov-balance__value').text()).toBe('12,954')
  })

  it('复制 Base URL 写入剪贴板并提示', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    const { wrapper } = await mountPage()

    const copy = wrapper.find('button[aria-label="复制 Base URL"]')
    expect(copy.exists()).toBe(true)
    await copy.trigger('click')
    await flush()

    expect(writeText).toHaveBeenCalledWith(location.origin + '/v1')
    expect(bodyText()).toContain('已复制')
  })

  it('平台上下文变化时重新取数，卸载后取消订阅', async () => {
    const { wrapper, pinia } = await mountPage()
    const store = usePlatformStore(pinia)
    const before = getOverview.mock.calls.length

    expect(store.setCurrent('trae')).toBe(true)
    await flush()
    expect(getOverview.mock.calls.length).toBe(before + 1)
    expect(store.currentId).toBe('trae')

    wrapper.unmount()
    mounted = null
    const after = getOverview.mock.calls.length
    store.setCurrent('workbuddy')
    await flush()
    expect(getOverview.mock.calls.length).toBe(after)
  })
})
