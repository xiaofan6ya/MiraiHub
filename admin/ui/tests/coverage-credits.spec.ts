/**
 * 覆盖率补充：CreditsPage 真实交互（挂载页面 + mock @/api/credits）。
 *
 * 只新增本文件；不改 src。覆盖：首屏渲染、包「加载更多」、风险 Badge（手机卡片）、
 * 空态、排序、分页/快速翻页、详情抽屉、策略保存/校验、恢复自动选号（确认框）、
 * 重新采集（JobProgress 成功/失败）、主接口与局部错误态、平台切换重取。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NInputNumber, NMessageProvider, NSelect } from 'naive-ui'
import CreditsPage from '@/pages/CreditsPage.vue'
import DataTable from '@/components/data/DataTable.vue'
import StatStrip from '@/components/data/StatStrip.vue'
import StackMeter from '@/components/data/StackMeter.vue'
import BarList from '@/components/data/BarList.vue'
import DetailDrawer from '@/components/overlay/DetailDrawer.vue'
import Btn from '@/components/base/Btn.vue'
import { usePlatformStore } from '@/stores/platform'
import { ApiError } from '@/api/http'
import * as creditsApi from '@/api/credits'
import type { CreditAccountRow, CreditExpiryPayload, CreditPackageRow, CreditUsagePayload } from '@/api/credits'

vi.mock('@/api/credits', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/credits')>()
  return {
    ...actual,
    getCreditExpiry: vi.fn(),
    getCreditUsage: vi.fn(),
    saveSelectionPolicy: vi.fn(),
    restoreAutomaticSelection: vi.fn(),
    syncCredits: vi.fn(),
  }
})

const getExpiry = vi.mocked(creditsApi.getCreditExpiry)
const getUsage = vi.mocked(creditsApi.getCreditUsage)
const savePolicy = vi.mocked(creditsApi.saveSelectionPolicy)
const restoreAuto = vi.mocked(creditsApi.restoreAutomaticSelection)
const syncCredits = vi.mocked(creditsApi.syncCredits)

const originalMatchMedia = window.matchMedia

function setViewport(mobile: boolean): void {
  window.matchMedia = ((query: string) => ({
    matches: mobile && query.includes('640'),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

function pkg(overrides: Partial<CreditPackageRow> = {}): CreditPackageRow {
  return {
    package_code: 'pkg-1',
    display_name: '月度积分包',
    remaining: 500,
    total: 1000,
    expire_at: 1798675200000,
    expire_label: '2026-12-31 到期',
    expire_date: '12-31',
    expired: false,
    expiring_soon: true,
    account_id: 12,
    account_name: '193****2486',
    ...overrides,
  }
}

function account(overrides: Partial<CreditAccountRow> = {}): CreditAccountRow {
  return {
    account_id: 12,
    account_name: '193****2486',
    uid: 'uid-12',
    status: 'active',
    selection_priority: null,
    balance_remain: 4764,
    total_remaining: 4764,
    total_capacity: 5000,
    expiring_soon_remaining: 11514,
    evergreen_remaining: 0,
    expired_remaining: 0,
    soonest_expire_at: 1798675200000,
    soonest_days_left: 3,
    soonest_label: '3 天后到期',
    expiring_soon: true,
    expiring_package_count: 3,
    package_count: 4,
    active_package_count: 4,
    synced_at: 1761858456000,
    next_expiring: [pkg()],
    ...overrides,
  }
}

const ACCOUNT_2 = account({
  account_id: 13,
  account_name: '137****8653',
  uid: 'uid-13',
  total_remaining: 8192,
  expiring_soon_remaining: 0,
  soonest_expire_at: null,
  soonest_days_left: null,
  soonest_label: '全部长期有效',
  expiring_soon: false,
  next_expiring: [],
})

function expiry(overrides: Partial<CreditExpiryPayload> = {}): CreditExpiryPayload {
  return {
    generated_at: 1761858456000,
    expiring_soon_days: 7,
    suggested_days: 7,
    summary: {
      accounts: 3,
      available_remaining: 12954,
      total_remaining: 12956,
      snapshot_accounts: 2,
      expiring_soon_remaining: 11514,
      evergreen_remaining: 0,
      expired_remaining: 0,
      expiring_accounts: 1,
      expiring_packages: 124,
      soonest_expire_at: 1798675200000,
      soonest_days_left: 3,
      soonest_account_id: 12,
      soonest_account_name: '193****2486',
      at_risk_ratio: 0.8886,
      packages_total: 124,
    },
    windows: [
      { days: 1, amount: 0, packages: 0 },
      { days: 3, amount: 0, packages: 0 },
      { days: 7, amount: 0, packages: 0 },
      { days: 14, amount: 0, packages: 0 },
      { days: 30, amount: 11514, packages: 124 },
    ],
    accounts: [account(), ACCOUNT_2],
    selection_mode: 'automatic',
    selection_order: [12, 13],
    routing_policy: {
      mode: 'expiry_weighted',
      upstream_start_interval_ms: 75,
      modes: [
        { value: 'expiry_weighted', label: '到期排名加权', description: '按到期先后赋予权重。' },
        { value: 'expiring', label: '严格到期优先', description: '先用最早到期的健康账号。' },
      ],
    },
    packages: [pkg()],
    packages_total: 124,
    package_offset: 0,
    package_limit: 60,
    has_more: false,
    ...overrides,
  }
}

function usage(overrides: Partial<CreditUsagePayload> = {}): CreditUsagePayload {
  return {
    range_days: 7,
    total_credits: 1234.5,
    accounts: [
      { account_id: 13, account_name: '137****8653', requests: 43, credits: 154, tokens: 900, remaining: 8192, expiring_soon_remaining: 0, soonest_days_left: null },
      { account_id: 12, account_name: '193****2486', requests: 595, credits: 44, tokens: 1200, remaining: 4764, expiring_soon_remaining: 11514, soonest_days_left: 3 },
    ],
    models: [],
    ...overrides,
  }
}

const PLATFORM_SPEC = {
  id: 'trae-cn',
  label: 'Trae 国内版',
  enabled: true,
  supports_local_scan: true,
  auth_mode: 'local-scan',
  default_realm: 'cn',
  notes: '',
  icon: 'trae',
  storage_platform: 'trae',
  realm: 'cn',
  realm_label: '国内版',
  realms: [],
  proxied: false,
  rating: {},
  extra: {},
  managed: true,
  actions: [],
  quota_unit: 'credits' as const,
  quota_unit_label: '',
  account_count: 3,
  realm_counts: { cn: 3 },
}

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `
    <n-config-provider>
      <n-message-provider>
        <n-dialog-provider>
          <router-view />
        </n-dialog-provider>
      </n-message-provider>
    </n-config-provider>
  `,
})

interface Mounted {
  wrapper: VueWrapper
  router: Router
  platform: ReturnType<typeof usePlatformStore>
}

let mounted: Mounted | null = null

async function flush(): Promise<void> {
  await flushPromises()
  await nextTick()
  await flushPromises()
}

async function mountPage(): Promise<Mounted> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/credits', name: 'credits', component: CreditsPage }],
  })
  await router.push('/credits')
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flush()
  const platform = usePlatformStore(pinia)
  mounted = { wrapper, router, platform }
  return mounted
}

function table(wrapper: VueWrapper, tableId: string) {
  const found = wrapper
    .findAllComponents(DataTable)
    .find((item) => item.props('tableId') === tableId)
  expect(found, 'table ' + tableId).toBeTruthy()
  return found!
}

function btnComponent(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAllComponents(Btn).find((item) => item.text().includes(label))
  expect(found, 'Btn ' + label).toBeTruthy()
  return found!
}

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().includes(label))
  expect(found, 'button ' + label).toBeTruthy()
  return found!
}

async function clickButton(wrapper: VueWrapper, label: string): Promise<void> {
  await button(wrapper, label).trigger('click')
  await flush()
}

async function clickDialog(label: string): Promise<void> {
  await flush()
  const buttons = Array.from(document.querySelectorAll('.n-dialog .n-button')) as HTMLElement[]
  const target = buttons.find((item) => (item.textContent || '').includes(label))
  expect(target, 'dialog button ' + label).toBeTruthy()
  target!.click()
  await flush()
}

beforeEach(() => {
  vi.clearAllMocks()
  setViewport(false)
  getExpiry.mockResolvedValue(expiry())
  getUsage.mockResolvedValue(usage())
  savePolicy.mockImplementation(async (input) => ({
    mode: input.mode,
    upstream_start_interval_ms: input.upstream_start_interval_ms,
    modes: expiry().routing_policy.modes,
  }))
  restoreAuto.mockResolvedValue({ ok: true, mode: 'automatic', ids: [] })
  syncCredits.mockResolvedValue({
    ok: true,
    total: 3,
    refreshed: 2,
    failed: 1,
    credits_expiring_soon: 11514,
    detail: [],
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, items: [PLATFORM_SPEC], default: 'trae-cn', loader: {} }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
})

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
  window.matchMedia = originalMatchMedia
  vi.unstubAllGlobals()
})

describe('首屏渲染', () => {
  it('StatStrip / 风险分布 / 两张表 / BarList 都拿到 mock 数据', async () => {
    const { wrapper } = await mountPage()
    const strip = wrapper.findComponent(StatStrip)
    const stripItems = strip.props('items') as Array<{ label: string; value: string | number }>
    expect(stripItems.map((item) => item.label)).toEqual([
      '账号总数',
      '可用积分',
      '7 天内到期',
      '长期有效',
      '最紧迫账号',
    ])
    expect(stripItems[1].value).toBe('12,954')
    expect(wrapper.text()).toContain('账号调度策略')
    expect(wrapper.text()).toContain('到期风险分布')
    const stack = wrapper.findComponent(StackMeter)
    expect((stack.props('items') as Array<{ label: string }>)[0].label).toContain('≤30 天')
    const packageData = table(wrapper, 'credits-packages').props('data') as Array<Record<string, unknown>>
    expect(packageData).toHaveLength(1)
    expect(packageData[0].rowKey).toBe('12-pkg-1-1798675200000')
    const accountData = table(wrapper, 'credits-accounts').props('data') as Array<Record<string, unknown>>
    expect(accountData.map((row) => row.account_name)).toEqual(['193****2486', '137****8653'])
    expect(accountData[0].rank).toBe(1)
    expect(accountData[1].rank).toBe(2)
    const bars = wrapper.findComponent(BarList)
    expect((bars.props('items') as unknown[]).length).toBe(2)
    expect(wrapper.text()).toContain('2 个账号 · 自动：到期排名加权')
  })
})

describe('积分包：加载更多', () => {
  it('第二次请求带 packageOffset，追加行；has_more=false 后按钮消失', async () => {
    getExpiry
      .mockResolvedValueOnce(expiry({ packages: [pkg()], has_more: true, packages_total: 2 }))
      .mockResolvedValueOnce(expiry({ packages: [pkg({ package_code: 'pkg-2', display_name: '活动积分' })], has_more: false }))
    const { wrapper } = await mountPage()
    expect(table(wrapper, 'credits-packages').props('data')).toHaveLength(1)
    await clickButton(wrapper, '加载更多')
    expect(getExpiry).toHaveBeenCalledTimes(2)
    expect(getExpiry.mock.calls[1][0]).toMatchObject({ packageLimit: 60, packageOffset: 1 })
    const data = table(wrapper, 'credits-packages').props('data') as Array<Record<string, unknown>>
    expect(data).toHaveLength(2)
    expect(data[1].display_name).toBe('活动积分')
    expect(wrapper.findAll('button').some((item) => item.text().includes('加载更多'))).toBe(false)
  })
})

describe('风险等级 Badge（≤640px 卡片模式）', () => {
  it('快到期 / 已过期 / 最近到期天数都渲染成 Badge 文本', async () => {
    setViewport(true)
    getExpiry.mockResolvedValue(
      expiry({
        packages: [
          pkg(),
          pkg({ package_code: 'pkg-old', display_name: '过期包', expired: true, expiring_soon: false, remaining: 0 }),
        ],
        accounts: [account({ soonest_days_left: 3 }), ACCOUNT_2],
      }),
    )
    const { wrapper } = await mountPage()
    expect(wrapper.findAll('.mh-cards__item').length).toBeGreaterThan(0)
    const text = wrapper.text()
    expect(text).toContain('快到期')
    expect(text).toContain('已过期')
    expect(text).toContain('3 天后到期')
    // 包 2 张 + 账号 2 张卡片（≤640 的卡片模式确实生效）
    expect(wrapper.findAll('.mh-cards__item').length).toBeGreaterThanOrEqual(4)
  })
})

describe('空态', () => {
  it('没有账号 / 没有积分包 / 没有即将到期额度', async () => {
    getExpiry.mockResolvedValue(
      expiry({
        accounts: [],
        packages: [],
        windows: [],
        summary: {
          ...expiry().summary,
          accounts: 0,
          available_remaining: 0,
          total_remaining: 0,
          expiring_soon_remaining: 0,
          evergreen_remaining: 0,
          snapshot_accounts: 0,
          packages_total: 0,
        },
      }),
    )
    getUsage.mockResolvedValue(usage({ accounts: [], total_credits: 0 }))
    const { wrapper } = await mountPage()
    const text = wrapper.text()
    expect(text).toContain('没有任何即将到期的额度')
    expect(text).toContain('没有仍有剩余的积分包')
    expect(text).toContain('没有启用账号。先在「账号池」里添加账号。')
    expect(text).toContain('近 7 天没有产生积分消耗。')
  })
})

describe('账号排行：排序与分页', () => {
  it('排序事件写入路由 query，并按剩余积分升序重排', async () => {
    const { wrapper, router } = await mountPage()
    await table(wrapper, 'credits-accounts').vm.$emit('update:sorter', {
      columnKey: 'total_remaining',
      order: 'ascend',
    })
    await flush()
    expect(router.currentRoute.value.query).toMatchObject({ sort: 'total_remaining', order: 'asc' })
    const data = table(wrapper, 'credits-accounts').props('data') as Array<Record<string, unknown>>
    expect(data.map((row) => row.account_name)).toEqual(['193****2486', '137****8653'])
    const columns = table(wrapper, 'credits-accounts').props('columns') as unknown as Array<Record<string, unknown>>
    expect(columns.find((column) => column.key === 'total_remaining')?.sortOrder).toBe('ascend')
  })

  it('翻页 / 改每页条数：query 与排名同步，快速翻页以最后一页为准', async () => {
    const many = Array.from({ length: 45 }, (_, index) =>
      account({
        account_id: 100 + index,
        account_name: '账号 ' + String(index + 1).padStart(2, '0'),
        uid: 'uid-' + (100 + index),
        total_remaining: 9000 - index * 100,
      }),
    )
    getExpiry.mockResolvedValue(expiry({ accounts: many }))
    const { wrapper, router } = await mountPage()
    const accountsTable = () => table(wrapper, 'credits-accounts')
    expect((accountsTable().props('data') as unknown[]).length).toBe(20)
    await accountsTable().vm.$emit('update:page', 2)
    await accountsTable().vm.$emit('update:page', 3)
    await flush()
    expect(router.currentRoute.value.query.page).toBe('3')
    const page3 = accountsTable().props('data') as Array<Record<string, unknown>>
    expect(page3).toHaveLength(5)
    expect(page3[0].rank).toBe(41)
    await accountsTable().vm.$emit('update:pageSize', 50)
    await flush()
    expect(router.currentRoute.value.query.size).toBe('50')
    expect(router.currentRoute.value.query.page).toBeUndefined()
    expect((accountsTable().props('data') as unknown[]).length).toBe(45)
  })
})

describe('明细抽屉', () => {
  it('行点击打开抽屉：标题、KV、明细包表都渲染；关闭按钮生效', async () => {
    const { wrapper } = await mountPage()
    await table(wrapper, 'credits-accounts').vm.$emit('row-click', account())
    await flush()
    expect(document.body.textContent).toContain('积分明细 · 193****2486')
    expect(document.body.textContent).toContain('7 天内到期')
    const detailTable = Array.from(document.querySelectorAll('.n-drawer'))
    expect(detailTable.length).toBeGreaterThan(0)
    const close = Array.from(document.querySelectorAll('.n-drawer button')).find((el) =>
      (el.textContent || '').includes('关闭'),
    ) as HTMLElement | undefined
    expect(close).toBeTruthy()
    close!.click()
    await flush()
    expect(wrapper.findComponent(DetailDrawer).props('show')).toBe(false)
  })
})

describe('账号调度策略', () => {
  it('改模式后保存：调用接口并重新加载到期数据', async () => {
    const { wrapper } = await mountPage()
    // 初始 form.mode 未回填（页面 applyPolicyForm 在 dirty 时提前 return）→ dirty 为真：
    // 先选回与 policy 相同的模式，让「保存策略」回到禁用，再改成另一种模式启用。
    expect(btnComponent(wrapper, '保存策略').props('disabled')).toBe(false)
    await wrapper.findComponent(NSelect).vm.$emit('update:value', 'expiry_weighted')
    await flush()
    expect(btnComponent(wrapper, '保存策略').props('disabled')).toBe(true)
    await wrapper.findComponent(NSelect).vm.$emit('update:value', 'expiring')
    await flush()
    expect(btnComponent(wrapper, '保存策略').props('disabled')).toBe(false)
    await clickButton(wrapper, '保存策略')
    expect(savePolicy).toHaveBeenCalledWith({ mode: 'expiring', upstream_start_interval_ms: 75 })
    expect(getExpiry).toHaveBeenCalledTimes(2)
  })

  it('非法间隔不保存并提示（0–60000 整数）', async () => {
    const { wrapper } = await mountPage()
    await wrapper.findComponent(NInputNumber).vm.$emit('update:value', 70000)
    await wrapper.findComponent(NSelect).vm.$emit('update:value', 'expiring')
    await flush()
    await clickButton(wrapper, '保存策略')
    expect(savePolicy).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('上游启动间隔必须是 0 到 60000 的整数毫秒')
  })

  it('手动优先级：点「恢复自动选号」弹确认框，确认后调用接口', async () => {
    getExpiry.mockResolvedValue(expiry({ selection_mode: 'manual' }))
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('手动优先级')
    expect(btnComponent(wrapper, '恢复自动选号').props('disabled')).toBe(false)
    await button(wrapper, '恢复自动选号').trigger('click')
    await clickDialog('恢复自动选号')
    expect(restoreAuto).toHaveBeenCalledWith([12, 13])
    expect(getExpiry).toHaveBeenCalledTimes(2)
  })
})

describe('重新采集（JobProgress）', () => {
  it('成功：进度卡显示汇总，点「查看详情」给出结果提示', async () => {
    const { wrapper } = await mountPage()
    await clickButton(wrapper, '重新采集')
    expect(syncCredits).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('采集完成：成功 2/3，1 个失败，7 天内到期 11,514')
    expect(wrapper.text()).toContain('部分失败')
    await clickButton(wrapper, '查看详情')
    expect(document.body.textContent).toContain('采集完成：成功 2/3')
    expect(getExpiry).toHaveBeenCalledTimes(2)
  })

  it('失败：job 标记失败并提示错误信息', async () => {
    syncCredits.mockRejectedValue(new ApiError(500, '采集服务不可用'))
    const { wrapper } = await mountPage()
    await clickButton(wrapper, '重新采集')
    expect(wrapper.text()).toContain('采集服务不可用')
    expect(wrapper.text()).toContain('失败')
    expect(document.body.textContent).toContain('重新采集积分快照失败：采集服务不可用')
  })
})

describe('错误态与重试', () => {
  it('主接口失败：ErrorBlock + 重试重新请求', async () => {
    getExpiry.mockRejectedValueOnce(new ApiError(500, '到期服务炸了', { trace: 'db down' }))
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('加载积分到期失败')
    expect(getUsage).toHaveBeenCalled()
    await clickButton(wrapper, '重试')
    expect(getExpiry).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('账号调度策略')
  })

  it('消耗构成局部失败：只影响该卡片，重试可恢复', async () => {
    getUsage.mockRejectedValueOnce(new ApiError(502, 'usage 502'))
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('加载账号消耗构成失败：usage 502')
    expect(wrapper.text()).toContain('账号调度策略')
    await clickButton(wrapper, '重试')
    expect(getUsage).toHaveBeenCalledTimes(2)
    expect(wrapper.findComponent(BarList).exists()).toBe(true)
  })
})

describe('平台切换', () => {
  it('切平台触发重新取数', async () => {
    const { platform } = await mountPage()
    platform.items = [PLATFORM_SPEC]
    platform.loaded = true
    expect(getExpiry).toHaveBeenCalledTimes(1)
    platform.setCurrent('trae-cn')
    await flush()
    expect(platform.selector).toBe('trae-cn')
    expect(getExpiry).toHaveBeenCalledTimes(2)
    expect(getUsage).toHaveBeenCalledTimes(2)
  })
})

// Naive message 的 auto-hide 定时器会在 jsdom 销毁后触发；用可自动推进的假时钟并在收尾清空。
beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })
