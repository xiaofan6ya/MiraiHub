/**
 * 覆盖率补充：StatsPage 真实挂载（时间窗口 / 粒度 / 图表 / 排行 / 补充接口错误 / 错误态）。
 *
 * 只 mount 页面 + mock @/api/stats（与平台 store 依赖的 @/api/platforms），不改 src。
 * 断言口径来自 docs/ui-v2/contracts/stats.md（末尾「实现对照」）与后端真实响应形状。
 *
 * 说明：图表组件内部走 ECharts Canvas，jsdom 没有 canvas 实现，
 * 这里用同名桩替换 Chart（只替换渲染层），option 仍是页面真实算出来的，
 * 断言落在 series / x 轴标签上。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import StatsPage from '@/pages/StatsPage.vue'
import { setLocale } from '@/i18n'
import { ApiError } from '@/api/http'
import * as statsApi from '@/api/stats'
import type { StatsDashboardPayload, StatsUsagePayload } from '@/api/stats'
import * as platformsApi from '@/api/platforms'

vi.mock('@/api/stats', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/stats')>()
  return { ...actual, getStatsDashboard: vi.fn(), getStatsUsage: vi.fn() }
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

const getStatsDashboard = vi.mocked(statsApi.getStatsDashboard)
const getStatsUsage = vi.mocked(statsApi.getStatsUsage)

/* ------------------------------------------------------------------ *
 * 后端真实响应形状
 * ------------------------------------------------------------------ */

function dashboard(overrides: Partial<StatsDashboardPayload> = {}): StatsDashboardPayload {
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
      requests: 12500,
      successes: 12300,
      failures: 200,
      success_rate: 0.984,
      credits: 1234.56,
      total_tokens: 650000,
      prompt_tokens: 500000,
      completion_tokens: 150000,
      cached_tokens: 50000,
      cache_hit_rate: 0.0909,
      rpm: 520.8,
      tpm: 27083.3,
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
    trend: {
      buckets: ['2026-11-23 14:00', '2026-11-23 15:00'],
      total_requests: [520, 530],
      series: [
        {
          name: 'gpt-4o',
          requests: [150, 160],
          tokens: [1000, 1100],
          credits: [10, 11],
        },
      ],
    },
    ranking: [
      {
        model: 'gpt-4o',
        label: 'GPT-4o',
        requests: 3000,
        successes: 2950,
        failures: 50,
        success_rate: 0.9833,
        tokens: 180000,
        credits: 450,
        avg_latency_ms: 520,
        avg_ttfb_ms: 150,
        share: 0.24,
      },
    ],
    errors: [],
    source: 'rollup',
    ...overrides,
  }
}

function usage(overrides: Partial<StatsUsagePayload> = {}): StatsUsagePayload {
  return {
    range_days: 1,
    granularity: 'hour',
    platform_label: '全部平台',
    overview: {
      requests: 12500,
      ok_requests: 12300,
      failed_requests: 200,
      credits: 1234.56,
      prompt_tokens: 500000,
      completion_tokens: 150000,
      total_tokens: 650000,
      cached_tokens: 50000,
      avg_latency_ms: 450,
    },
    platforms: [],
    by_model: [],
    by_key: [{ label: 'sk-mh3…9f2e', requests: 800, tokens: 1000, credits: 5 }],
    by_endpoint: [],
    by_account: [],
    by_result: [],
    trend: [
      {
        bucket: '2026-11-23 14:00',
        prompt_tokens: 1000,
        completion_tokens: 500,
        cached_tokens: 100,
        credits: 1,
        requests: 20,
      },
      {
        bucket: '2026-11-23 15:00',
        prompt_tokens: 2000,
        completion_tokens: 600,
        cached_tokens: 200,
        credits: 2,
        requests: 30,
      },
    ],
    ...overrides,
  }
}

/* ------------------------------------------------------------------ *
 * Chart 桩：只替换 Canvas 渲染，option 仍由页面真实计算
 * ------------------------------------------------------------------ */

const ChartStub = defineComponent({
  name: 'Chart',
  props: {
    option: { type: Object, default: () => ({}) },
    height: { type: String, default: '' },
    loading: { type: Boolean, default: false },
  },
  setup(props) {
    const series = computed(() => {
      const value = (props.option as { series?: Array<{ name?: string }> }).series
      return Array.isArray(value) ? value.map((item) => String(item?.name ?? '')) : []
    })
    const labels = computed(() => {
      const axis = (props.option as { xAxis?: { data?: unknown[] } }).xAxis
      const data = axis && Array.isArray(axis.data) ? axis.data : []
      return data.map((item) => String(item))
    })
    return () =>
      h('div', {
        class: 'chart-stub',
        'data-series': series.value.join('|'),
        'data-labels': labels.value.join('|'),
        'data-height': props.height,
      })
  },
})

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
}

let mounted: Mounted | null = null

async function flush(): Promise<void> {
  await flushPromises()
  await nextTick()
  await flushPromises()
}

async function mountPage(options: { path?: string } = {}): Promise<Mounted> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/stats', name: 'stats', component: StatsPage },
      { path: '/platforms', name: 'platforms', component: PageStub },
      { path: '/market', name: 'market', component: PageStub },
      { path: '/overview', name: 'overview', component: PageStub },
    ],
  })
  await router.push(options.path ?? '/stats')
  await router.isReady()

  const wrapper = mount(Host, {
    global: { plugins: [pinia, router], stubs: { Chart: ChartStub } },
    attachTo: document.body,
  })
  await flush()
  await flush()

  mounted = { wrapper, router }
  return mounted
}

function bodyText(): string {
  return String(document.body.textContent || '')
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((item) => (item.text() || '').includes(text))
}

function segment(wrapper: VueWrapper, ariaLabel: string) {
  return wrapper.find('[aria-label="' + ariaLabel + '"]')
}

async function clickSegment(wrapper: VueWrapper, ariaLabel: string, label: string): Promise<void> {
  const group = segment(wrapper, ariaLabel)
  expect(group.exists()).toBe(true)
  const target = group.findAll('button').find((item) => item.text().includes(label))
  expect(target, ariaLabel + ' / ' + label).toBeTruthy()
  await target!.trigger('click')
  await flush()
}

function lastQuery(): { days?: number; granularity?: string } {
  const calls = getStatsDashboard.mock.calls
  return (calls[calls.length - 1]?.[0] ?? {}) as { days?: number; granularity?: string }
}

beforeEach(() => {
  getStatsDashboard.mockReset().mockResolvedValue(dashboard())
  getStatsUsage.mockReset().mockResolvedValue(usage())
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

describe('StatsPage 覆盖率补测', () => {
  it('首屏渲染 KPI、窗口说明、趋势脚注与四张排行卡', async () => {
    const { wrapper } = await mountPage()

    expect(wrapper.find('h1').text()).toContain('数据看板')

    // StatStrip 五格
    expect(wrapper.text()).toContain('请求数')
    expect(wrapper.text()).toContain('12,500')
    expect(wrapper.text()).toContain('成功 12,300 · 失败 200')
    expect(wrapper.text()).toContain('98.4%')
    expect(wrapper.text()).toContain('650K')
    expect(wrapper.text()).toContain('入 500K · 出 150K')
    expect(wrapper.text()).toContain('1,234.56')
    expect(wrapper.text()).toContain('缓存命中 9.1%')
    expect(wrapper.text()).toContain('450ms')
    expect(wrapper.text()).toContain('首字 120ms · 95.5 t/s')

    // 窗口 / 更新时间
    expect(wrapper.find('.stats-meta').text()).toContain('窗口')
    expect(wrapper.find('.stats-meta').text()).toContain('更新于')

    // days=1 是滚动 24 小时，不是自然日：按钮与窗口说明都要按真实口径写
    const daysGroup = segment(wrapper, '统计区间')
    expect(daysGroup.text()).toContain('近 24 小时')
    expect(daysGroup.text()).not.toContain('今日')
    expect(wrapper.find('.stats-meta').text()).toContain('滚动窗口：最近 24 小时，不是自然日')

    // 卡片标题与副标题
    const titles = wrapper.findAll('.mh-card__title').map((item) => item.text())
    expect(titles).toEqual(['请求趋势', 'Token 趋势', '平台排行', '模型排行', 'Key 排行'])
    expect(wrapper.text()).toContain('成功 12,300 · 失败 200')
    expect(wrapper.text()).toContain('共 2 个平台 · 按请求数')
    expect(wrapper.text()).toContain('共 1 个模型 · 按请求数')
    expect(wrapper.text()).toContain('Top 1 · 按请求数')

    // 排行条（BarList）：平台 / 模型 / Key
    const labels = wrapper.findAll('.mh-barlist__label').map((item) => item.text())
    expect(labels).toEqual(['WorkBuddy', 'Trae', 'GPT-4o', 'sk-mh3…9f2e'])
    expect(wrapper.text()).toContain('8,000 次')
    expect(wrapper.text()).toContain('3,000 次')
    expect(wrapper.text()).toContain('800 次')
    expect(wrapper.text()).toContain('80.0%')
  })

  it('两张图拿到真实 option：请求趋势含全部请求 + 各模型，Token 为三根堆叠柱', async () => {
    const { wrapper } = await mountPage()

    const charts = wrapper.findAll('.chart-stub')
    expect(charts).toHaveLength(2)

    expect(charts[0].attributes('data-series')).toBe('全部请求|gpt-4o')
    expect(charts[0].attributes('data-labels')).toBe('14:00|15:00')
    expect(charts[0].attributes('data-height')).toBe('260px')

    expect(charts[1].attributes('data-series')).toBe('输入|输出|缓存命中')
    expect(charts[1].attributes('data-labels')).toBe('14:00|15:00')
    expect(charts[1].attributes('data-height')).toBe('240px')

    // 缓存脚注来自 usage.trend：cached / prompt = 300 / 3000
    expect(wrapper.text()).toContain('缓存命中 10.0%')
  })

  it('切换时间范围写 query 并重新取数，超过 7 天自动降级按天', async () => {
    const { wrapper, router } = await mountPage()
    const before = getStatsDashboard.mock.calls.length

    await clickSegment(wrapper, '统计区间', '7 天')
    expect(router.currentRoute.value.query.days).toBe('7')
    expect(lastQuery()).toMatchObject({ days: 7, granularity: 'hour' })

    await clickSegment(wrapper, '统计区间', '30 天')
    expect(router.currentRoute.value.query.days).toBe('30')
    expect(router.currentRoute.value.query.grain).toBe('day')
    expect(lastQuery()).toMatchObject({ days: 30, granularity: 'day' })
    expect(getStatsDashboard.mock.calls.length).toBe(before + 2)
  })

  it('超过 7 天时点按小时只提示，不改状态也不重新取数', async () => {
    const { wrapper, router } = await mountPage()
    await clickSegment(wrapper, '统计区间', '30 天')
    const before = getStatsDashboard.mock.calls.length

    await clickSegment(wrapper, '聚合粒度', '按小时')
    await flush()

    expect(bodyText()).toContain('超过 7 天只支持按天聚合')
    expect(router.currentRoute.value.query.grain).toBe('day')
    expect(getStatsDashboard.mock.calls.length).toBe(before)
  })

  it('7 天内切换聚合粒度按新粒度取数', async () => {
    const { wrapper, router } = await mountPage()

    await clickSegment(wrapper, '聚合粒度', '按天')

    expect(router.currentRoute.value.query.grain).toBe('day')
    expect(lastQuery()).toMatchObject({ days: 1, granularity: 'day' })
    expect(getStatsUsage.mock.calls[getStatsUsage.mock.calls.length - 1]?.[0]).toMatchObject({
      days: 1,
      granularity: 'day',
    })
  })

  it('刷新按钮重新取数', async () => {
    const { wrapper } = await mountPage()
    const before = getStatsDashboard.mock.calls.length

    const refresh = buttonByText(wrapper, '刷新')
    expect(refresh).toBeTruthy()
    await refresh!.trigger('click')
    await flush()

    expect(getStatsDashboard.mock.calls.length).toBe(before + 1)
    expect(getStatsUsage.mock.calls.length).toBeGreaterThan(1)
  })

  it('排行超过 8 项时合并为「其他」', async () => {
    const platforms = Array.from({ length: 10 }, (_, index) => ({
      value: 'p' + index,
      label: '平台' + (index + 1),
      requests: 1000 - index * 10,
      success_rate: 0.99,
      tokens: 0,
      credits: 0,
    }))
    getStatsDashboard.mockResolvedValue(dashboard({ platforms }))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('共 10 个平台 · 按请求数')
    expect(wrapper.findAll('.mh-barlist__label').map((item) => item.text())).toContain(
      '其他 2 项',
    )
  })

  it('补充接口失败只在 Token / Key 两张卡里报错，重试可恢复', async () => {
    getStatsUsage.mockRejectedValue(new ApiError(500, 'usage boom'))
    const { wrapper } = await mountPage()

    // 主接口正常：KPI 与平台/模型排行照常
    expect(wrapper.text()).toContain('12,500')
    expect(wrapper.text()).toContain('2 个平台 · 按请求数')

    const errors = wrapper.findAll('.mh-error')
    expect(errors).toHaveLength(2)
    expect(errors[0].text()).toContain('加载 Token / Key 明细失败：usage boom')
    expect(errors[1].text()).toContain('加载 Token / Key 明细失败：usage boom')

    const before = getStatsUsage.mock.calls.length
    getStatsUsage.mockResolvedValue(usage())
    const retry = wrapper
      .findAll('.mh-error button')
      .find((item) => (item.text() || '').includes('重试'))
    expect(retry).toBeTruthy()
    await retry!.trigger('click')
    await flush()
    await flush()

    expect(getStatsUsage.mock.calls.length).toBeGreaterThan(before)
    expect(wrapper.findAll('.mh-error')).toHaveLength(0)
    expect(wrapper.text()).toContain('sk-mh3…9f2e')
  })

  it('主接口失败替换内容区，重试后恢复', async () => {
    getStatsDashboard.mockRejectedValue(new ApiError(502, 'dashboard down', { trace: 'upstream' }))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('加载数据看板失败：dashboard down')
    expect(wrapper.find('.mh-strip').exists()).toBe(false)

    getStatsDashboard.mockResolvedValue(dashboard())
    const retry = wrapper
      .findAll('.mh-error button')
      .find((item) => (item.text() || '').includes('重试'))
    expect(retry).toBeTruthy()
    await retry!.trigger('click')
    await flush()
    await flush()

    expect(wrapper.findAll('.mh-error')).toHaveLength(0)
    expect(wrapper.find('.mh-strip').exists()).toBe(true)
  })

  it('刷新失败只提示，保留上次成功的数据', async () => {
    const { wrapper } = await mountPage()
    getStatsDashboard.mockRejectedValue(new ApiError(500, 'refresh boom'))

    const refresh = buttonByText(wrapper, '刷新')
    await refresh!.trigger('click')
    await flush()
    await flush()

    expect(wrapper.find('.mh-strip').exists()).toBe(true)
    expect(wrapper.text()).toContain('12,500')
    expect(wrapper.findAll('.mh-error')).toHaveLength(0)
    expect(bodyText()).toContain('刷新数据看板失败：refresh boom')
  })

  it('窗口内没有数据时五处都走空状态', async () => {
    getStatsDashboard.mockResolvedValue(
      dashboard({
        summary: { ...dashboard().summary, requests: 0, successes: 0, failures: 0, success_rate: null },
        platforms: [],
        ranking: [],
        models: [],
        trend: { buckets: [], total_requests: [], series: [] },
      }),
    )
    getStatsUsage.mockResolvedValue(usage({ trend: [], by_key: [] }))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('窗口内没有调用记录。换个时间窗口，或先发起一次调用。')
    expect(wrapper.text()).toContain('窗口内没有 Token 记录。')
    expect(wrapper.text()).toContain('窗口内没有平台流量。')
    expect(wrapper.text()).toContain('窗口内没有模型调用。')
    expect(wrapper.text()).toContain('窗口内没有 Key 调用记录。')
    expect(wrapper.findAll('.chart-stub')).toHaveLength(0)

    // 无样本的成功率是 —（不冒充 0%）
    expect(wrapper.findAll('.mh-strip .mh-stat__num')[1].text()).toBe('—')
  })

  it('从分享链接还原 days / grain，并标出选中项', async () => {
    const { wrapper } = await mountPage({ path: '/stats?days=30&grain=day' })

    expect(getStatsDashboard.mock.calls[0]?.[0]).toMatchObject({ days: 30, granularity: 'day' })
    const thirty = segment(wrapper, '统计区间')
      .findAll('button')
      .find((item) => item.text().includes('30 天'))
    expect(thirty?.attributes('aria-checked')).toBe('true')
    const day = segment(wrapper, '聚合粒度')
      .findAll('button')
      .find((item) => item.text().includes('按天'))
    expect(day?.attributes('aria-checked')).toBe('true')
  })

  it('后端降级到原表时显示数据源提示', async () => {
    getStatsDashboard.mockResolvedValue(dashboard({ source: 'raw' }))
    const { wrapper } = await mountPage()

    expect(wrapper.find('.stats-meta').text()).toContain('数据源：原表（汇总表降级）')
  })
})

describe('StatsPage 中英切换', () => {
  it('切到英文：模板与数据文案一起出英文，切回中文逐字还原', async () => {
    setLocale('zh-CN')
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('成功 12,300 · 失败 200')

    setLocale('en-US')
    await flush()

    expect(wrapper.find('h1').text()).toContain('Dashboard')
    expect(wrapper.text()).toContain('Request trend')
    expect(wrapper.text()).toContain('platforms: 2 · by requests')
    expect(wrapper.text()).toContain('OK 12,300 · failed 200')
    expect(wrapper.text()).toContain('Cache hit 10.0%')
    expect(wrapper.text()).not.toContain('数据看板')

    setLocale('zh-CN')
    await flush()
    expect(wrapper.text()).toContain('数据看板')
    expect(wrapper.text()).toContain('成功 12,300 · 失败 200')
  })
})
