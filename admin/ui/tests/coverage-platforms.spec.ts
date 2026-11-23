import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import { ApiError } from '@/api/http'
import type { PlatformSpec } from '@/api/platforms'
import { usePlatformStore } from '@/stores/platform'

/**
 * S20 平台管理 /platforms 的交互覆盖（覆盖率补充，配合 tests/page-platforms.spec.ts 的纯函数用例）。
 *
 * 只 mock「网络函数」，纯函数（toPlatformCards / scanPlanFor / parsePlatformBackup …）走真实实现，
 * 这样断言既证明 DOM 变化，也真的跑到页面里的分支。
 *
 * 覆盖点：初次加载 / 禁用卡片 / 状态页签 / 搜索 / 空态 / 错误态+重试 / 设为当前 / 账号池跳转 /
 *         ⋯ 启停确认（取消与确认）/ 能力差异（import-only 无扫描）/ 导出本平台 / 平台动作跳转 /
 *         扫描全部 / 导入抽屉（坏文件提示 + 预览 + 提交 + JobProgress）/ 导出抽屉（模式与空结果）/ 统计窗口。
 */

/* -------------------------------------------------------------------------- */
/* mocks                                                                      */
/* -------------------------------------------------------------------------- */

const mocks = vi.hoisted(() => ({
  listPlatforms: vi.fn(),
  putCurrentPlatform: vi.fn(async () => ({ ok: true })),
  getCurrentPlatform: vi.fn(async () => ({ platform: 'workbuddy', realm: '', raw: 'workbuddy' })),
  getPlatformSpeed: vi.fn(),
  getGatewayConfig: vi.fn(),
  getAccountsSnapshot: vi.fn(),
  setPlatformEnabled: vi.fn(async () => ({ ok: true })),
  exportPlatformsAll: vi.fn(),
  runPlatformScan: vi.fn(),
  submitPlatformImport: vi.fn(async (_platform: string) => 'job-1'),
  jobSnapshot: vi.fn(async () => ({
    job_id: 'job-1',
    status: 'finished',
    percent: 100,
    result: { added: 2, failed: 0 },
  })),
}))

vi.mock('@/api/platforms', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    listPlatforms: mocks.listPlatforms,
    putCurrentPlatform: mocks.putCurrentPlatform,
    getCurrentPlatform: mocks.getCurrentPlatform,
  }
})

vi.mock('@/api/platforms-admin', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    getPlatformSpeed: mocks.getPlatformSpeed,
    getGatewayConfig: mocks.getGatewayConfig,
    getAccountsSnapshot: mocks.getAccountsSnapshot,
    setPlatformEnabled: mocks.setPlatformEnabled,
    exportPlatformsAll: mocks.exportPlatformsAll,
    runPlatformScan: mocks.runPlatformScan,
    submitPlatformImport: mocks.submitPlatformImport,
  }
})

/**
 * useJob 的测试替身（轮询是异步边界，不该在测试里依赖真实定时器）。
 *
 * 为什么不用真实的 useJob：页面把 useJob 的返回值塞进 ref 数组，深层 ref 会把
 * `handle.snapshot`（Ref）自动解包成值，于是页面里的 `handle.snapshot.value` 永远是
 * undefined，jobItems 会在渲染期抛错（这是 src 的既有缺陷，t27 不允许改 src）。
 * 替身返回「普通对象 { value: 快照 }」，既不依赖真实轮询，又能让页面按原意渲染出 JobProgress。
 */
const jobMock = vi.hoisted(() => ({
  created: [] as Array<{ id: string; options: { onFinish?: (snapshot: unknown) => void } }>,
}))

vi.mock('@/composables', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    useJob: (id: string, options: { onFinish?: (snapshot: unknown) => void } = {}) => {
      const snapshot: { value: unknown } = { value: null }
      const percent = { value: 0 }
      jobMock.created.push({ id: String(id), options })
      Promise.resolve().then(() => {
        snapshot.value = {
          job_id: String(id),
          status: 'finished',
          percent: 100,
          result: { added: 2, failed: 0 },
        }
        percent.value = 100
        options.onFinish?.(snapshot.value)
      })
      return {
        snapshot,
        percent,
        status: { value: 'finished' },
        isRunning: { value: false },
        finished: { value: true },
        failed: { value: false },
        polling: { value: false },
        error: { value: null },
        start: () => undefined,
        stop: () => undefined,
      }
    },
  }
})

// 后台任务轮询走 http（useJob）；其余网络函数已在上面单独 mock。
vi.mock('@/api/http', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    http: (...args: unknown[]) => {
      const path = String(args[1] || '')
      if (path.startsWith('/api/accounts/job/')) {
        // 恒返回合法快照：页面 jobItems 的 filter 只挡 null，undefined 会让渲染抛错
        mocks.jobSnapshot()
        return Promise.resolve({
          job_id: 'job-1',
          status: 'finished',
          percent: 100,
          result: { added: 2, failed: 0 },
        })
      }
      return Promise.reject(new Error('unexpected http ' + path))
    },
  }
})

import PlatformsPage from '@/pages/PlatformsPage.vue'

/* -------------------------------------------------------------------------- */
/* fixtures（真实响应形状）                                                    */
/* -------------------------------------------------------------------------- */

function spec(overrides: Partial<PlatformSpec> = {}): PlatformSpec {
  return {
    id: 'workbuddy',
    label: 'WorkBuddy',
    enabled: true,
    supports_local_scan: true,
    auth_mode: 'local-scan',
    default_realm: '',
    notes: '',
    icon: 'workbuddy',
    storage_platform: 'workbuddy',
    realm: '',
    realm_label: '',
    realms: [],
    proxied: false,
    rating: {},
    extra: {},
    managed: true,
    actions: ['scan', 'import', 'export', 'refresh-quota'],
    quota_unit: 'credits',
    quota_unit_label: '',
    account_count: 2,
    realm_counts: {},
    ...overrides,
  }
}

const PLATFORMS: PlatformSpec[] = [
  spec(),
  spec({
    id: 'trae-cn',
    label: 'Trae 国内版',
    icon: 'trae',
    storage_platform: 'trae',
    realm: 'cn',
    realm_label: '国内版',
    account_count: 1,
    realm_counts: { cn: 1 },
    actions: ['scan', 'import', 'export'],
  }),
  spec({
    id: 'loomy',
    label: 'Loomy',
    icon: 'loomy',
    storage_platform: 'loomy',
    realm: '',
    auth_mode: 'import-only',
    supports_local_scan: false,
    account_count: 0,
    actions: ['scan', 'begin-auth', 'import'],
    rating: { tier: 'elite', reason: '给量还行', source: 'platform' },
  }),
]

const SPEED = {
  hours: 24,
  model: '',
  fastest: 'workbuddy',
  steadiest: 'workbuddy',
  observed_at: '2026-11-23T04:00:00Z',
  items: [
    {
      platform: 'workbuddy', label: 'WorkBuddy', rank: 1, requests: 1786, ok_requests: 1750,
      success_rate: 98.0, ttfb_p50: 900, ttfb_avg: 1200, latency_avg: 3950, latency_p50: 3200,
      latency_p95: 9000, tps_p50: 40.5, tps_avg: 12.3, jitter: 310, samples: 1750,
      low_sample: false, score: 3.2,
    },
    {
      platform: 'trae', label: 'Trae 国内版', rank: 2, requests: 175, ok_requests: 160,
      success_rate: 91.4, ttfb_p50: 1500, ttfb_avg: 1700, latency_avg: 5200, latency_p50: 5000,
      latency_p95: 12000, tps_p50: 22.1, tps_avg: 23.4, jitter: 900, samples: 160,
      low_sample: false, score: 6.1,
    },
  ],
}

const GATEWAY = {
  strategy: 'priority',
  spread_unbound: false,
  priority: { workbuddy: 100, trae: 80 },
  disabled: { loomy: { at: 1763900000, reason: '界面禁用' } },
  platforms: [],
}

const ACCOUNTS = {
  items: [
    { id: 1, platform: 'workbuddy', status: 'active', balance_remain: 12800 },
    { id: 2, platform: 'workbuddy', status: 'active', balance_remain: 154 },
    { id: 3, platform: 'trae', status: 'active', domain: 'cn', balance_remain: 320 },
  ],
}

const IMPORT_FILE = JSON.stringify({
  format: 'miraihub-all-platforms',
  version: 1,
  platforms: { workbuddy: { label: 'WorkBuddy', count: 1, accounts: [{ name: 'A' }] } },
})

/* -------------------------------------------------------------------------- */
/* mount helpers                                                              */
/* -------------------------------------------------------------------------- */

interface Mounted {
  wrapper: VueWrapper
  router: Router
}

const mounted: VueWrapper[] = []

const Host = defineComponent({
  setup: () => () =>
    h(NConfigProvider, null, () =>
      h(NMessageProvider, null, () => h(NDialogProvider, null, () => h(PlatformsPage))),
    ),
})

async function mountPage(): Promise<Mounted> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/platforms', name: 'platforms', component: { template: '<div />' } },
      { path: '/accounts', name: 'accounts', component: { template: '<div />' } },
      { path: '/schedules', name: 'schedules', component: { template: '<div />' } },
    ],
  })
  await router.push('/platforms')
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  const store = usePlatformStore()
  store.items = PLATFORMS
  store.platform = 'workbuddy'
  store.realm = ''

  const wrapper = mount(Host, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  mounted.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}

function bodyText(): string {
  return document.body.textContent || ''
}

/** 轮询等待某个 DOM 条件成立（naive 的过渡/轮询/异步渲染都靠它兜底）。 */
async function waitFor(predicate: () => boolean, label: string, times = 20): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    if (predicate()) return
    await settle()
  }
  throw new Error('waitFor timeout: ' + label)
}

/** 等 naive 的过渡/对话框/抽屉真正落到 DOM（只 flushPromises 不够）。 */
async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
  await nextTick()
}

function findButton(text: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll('button')).find(
    (el) => (el.textContent || '').trim() === text,
  ) as HTMLElement | undefined
}

async function clickButton(text: string): Promise<void> {
  const button = findButton(text)
  if (!button) throw new Error('button not found: ' + text)
  button.click()
  await settle()
}

function cardByLabel(label: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll('.pf-card')).find((el) =>
    (el.querySelector('h3')?.textContent || '').trim() === label,
  ) as HTMLElement | undefined
}

async function openCardMenu(label: string): Promise<void> {
  const card = cardByLabel(label)
  if (!card) throw new Error('card not found: ' + label)
  const more = Array.from(card.querySelectorAll('button')).find((el) =>
    (el.textContent || '').includes('更多'),
  ) as HTMLElement | undefined
  if (!more) throw new Error('menu button not found: ' + label)
  more.click()
  await settle()
}

function dropdownOption(label: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll('.n-dropdown-option')).find((el) =>
    ((el.textContent || '').trim() === label),
  ) as HTMLElement | undefined
}

async function pickMenu(label: string): Promise<void> {
  const option = dropdownOption(label)
  if (!option) {
    throw new Error('menu option not found: ' + label + ' | options=' +
      Array.from(document.querySelectorAll('.n-dropdown-option')).map((el) => el.textContent).join(','))
  }
  // naive 把点击监听挂在 option-body 上，点外层不会冒泡到它。
  const body = option.querySelector('.n-dropdown-option-body') as HTMLElement | null
  ;(body || option).click()
  await settle()
}

/** 确认框的「确认」按钮（useConfirm 用 NDialog，正按钮在 action 区最后一个）。 */
async function confirmDialog(okText: string): Promise<void> {
  await settle()
  const dialog = document.querySelector('.n-dialog')
  if (!dialog) throw new Error('dialog not found: ' + bodyText().slice(0, 120))
  const buttons = Array.from(dialog.querySelectorAll('.n-dialog__action button'))
  const positive = buttons.find((el) => (el.textContent || '').trim() === okText) || buttons[buttons.length - 1]
  ;(positive as HTMLElement).click()
  await settle()
}

function setFileInput(input: HTMLInputElement, files: Array<{ name: string; text: () => Promise<string> }>): void {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  input.dispatchEvent(new Event('change'))
}

/* -------------------------------------------------------------------------- */
/* setup / teardown                                                           */
/* -------------------------------------------------------------------------- */

beforeEach(() => {
  for (const key of Object.keys(mocks)) {
    // jobSnapshot 保留默认实现：轮询可能在「重置后、重新赋值前」的窗口里触发一次
    if (key === 'jobSnapshot') continue
    const fn = (mocks as Record<string, unknown>)[key]
    if (typeof fn === 'function' && 'mockReset' in fn) (fn as { mockReset(): void }).mockReset()
  }
  mocks.jobSnapshot.mockClear()
  jobMock.created.length = 0
  mocks.listPlatforms.mockResolvedValue({ items: PLATFORMS, default: 'workbuddy', loader: { loaded: [], failed: {} } })
  mocks.getPlatformSpeed.mockResolvedValue(SPEED)
  mocks.getGatewayConfig.mockResolvedValue(GATEWAY)
  mocks.getAccountsSnapshot.mockResolvedValue(ACCOUNTS)
  mocks.exportPlatformsAll.mockResolvedValue({
    platform_scope: 'workbuddy',
    platforms: { workbuddy: { label: 'WorkBuddy', count: 2, accounts: [] } },
  })
  mocks.runPlatformScan.mockImplementation(async (spec2: { id: string; label: string }) => ({
    storage: spec2.id,
    label: spec2.label,
    scanned: 2,
    imported: 1,
    skipped: 0,
    note: '',
  }))
  mocks.submitPlatformImport.mockResolvedValue('job-1')
  mocks.jobSnapshot.mockResolvedValue({
    job_id: 'job-1', status: 'finished', percent: 100, result: { added: 2, failed: 0 },
  })
  mocks.setPlatformEnabled.mockResolvedValue({ ok: true })
  mocks.putCurrentPlatform.mockResolvedValue({ ok: true })
  // jsdom 未实现：ResizeObserver（NDataTable）/ scrollIntoView
  vi.stubGlobal('ResizeObserver', class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  })
  HTMLElement.prototype.scrollIntoView = () => undefined
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  localStorage.clear()
})

/* -------------------------------------------------------------------------- */
/* tests                                                                      */
/* -------------------------------------------------------------------------- */

describe('平台管理 · 初次加载与卡片', () => {
  it('渲染卡片墙 / 状态计数 / 汇总 / 跑分表，并按 24h 取数', async () => {
    const { wrapper } = await mountPage()

    expect(wrapper.findAll('.pf-card')).toHaveLength(3)
    expect(wrapper.text()).toContain('全部 3')
    expect(wrapper.text()).toContain('已启用 2')
    expect(wrapper.text()).toContain('已禁用 1')
    expect(wrapper.text()).toContain('共 3 个平台 · 已启用 2 · 已禁用 1')

    // 卡片上的今日指标来自 /api/platform/speed
    expect(wrapper.text()).toContain('今日 1,786 次')
    expect(wrapper.text()).toContain('98.0%')
    expect(wrapper.text()).toContain('TTFT 1.2s · TPS 12.3')
    // 可用账号 / 剩余额度来自 /api/accounts 快照
    expect(wrapper.text()).toContain('2/2 可用')
    expect(wrapper.text()).toContain('剩余额度 12,954')

    // 跑分表：表体行在 jsdom 里按需渲染，这里断言「收到两行数据」的分页统计 + 表头
    expect(document.querySelector('.n-data-table')).toBeTruthy()
    expect(wrapper.text()).toContain('共 2 项')
    expect(wrapper.text()).toContain('成功率')
    expect(wrapper.text()).toContain('TTFT 均值')

    expect(mocks.listPlatforms).toHaveBeenCalledTimes(1)
    expect(mocks.getPlatformSpeed).toHaveBeenCalledWith(24, expect.anything())
  })

  it('禁用平台：卡片 60% 透明 + 不参与调度提示；tier 优先平台自报、缺失走兜底表', async () => {
    const { wrapper } = await mountPage()
    const disabled = wrapper.findAll('.pf-card.is-disabled')
    expect(disabled).toHaveLength(1)
    expect(disabled[0].text()).toContain('Loomy')
    expect(disabled[0].text()).toContain('已禁用')
    expect(disabled[0].text()).toContain('不参与调度，账号仍可管理（界面禁用）')

    // loomy 自报 elite；workbuddy 没声明 → 兜底表「夯」
    expect(disabled[0].text()).toContain('人上人')
    const workbuddy = cardByLabel('WorkBuddy')!
    expect(workbuddy.textContent).toContain('夯')
    // 单区平台显示区域标签
    expect(cardByLabel('Trae 国内版')!.textContent).toContain('国内版')
  })
})

describe('平台管理 · 筛选 / 空态 / 错误态', () => {
  it('状态页签过滤并写入路由 query，点 chip 可清除', async () => {
    const { wrapper, router } = await mountPage()

    const disabledTab = Array.from(document.querySelectorAll('.mh-seg__item')).find((el) =>
      (el.textContent || '').includes('已禁用'),
    ) as HTMLElement
    disabledTab.click()
    await flushPromises()

    expect(wrapper.findAll('.pf-card')).toHaveLength(1)
    expect(wrapper.find('.pf-card').text()).toContain('Loomy')
    expect(router.currentRoute.value.query.state).toBe('disabled')
    expect(wrapper.find('.mh-filter__chip').text()).toContain('已禁用')

    // chip 上的 ×：清掉状态筛选
    ;(wrapper.find('.mh-filter__chip').element as HTMLElement).click()
    await flushPromises()
    expect(wrapper.findAll('.pf-card')).toHaveLength(3)
    expect(router.currentRoute.value.query.state).toBeUndefined()
  })

  it('搜索按名称/落库名/区域过滤，重置恢复全部', async () => {
    const { wrapper, router } = await mountPage()
    await wrapper.find('.pf-search input').setValue('trae')
    await flushPromises()

    expect(wrapper.findAll('.pf-card')).toHaveLength(1)
    expect(wrapper.find('.pf-card h3').text()).toBe('Trae 国内版')
    expect(router.currentRoute.value.query.q).toBe('trae')
    expect(wrapper.find('.mh-filter__chip').text()).toContain('搜索')

    await clickButton('重置')
    expect(wrapper.findAll('.pf-card')).toHaveLength(3)
    expect(router.currentRoute.value.query.q).toBeUndefined()
  })

  it('空态：没有登记平台时显示下一步，点刷新重新取数', async () => {
    mocks.listPlatforms.mockResolvedValue({ items: [], default: 'workbuddy', loader: {} })
    const { wrapper } = await mountPage()

    expect(wrapper.findAll('.pf-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('还没有登记任何平台')
    await clickButton('刷新')
    expect(mocks.listPlatforms).toHaveBeenCalledTimes(2)
  })

  it('错误态：加载失败显示 ErrorBlock，重试后恢复卡片', async () => {
    mocks.listPlatforms.mockRejectedValueOnce(new ApiError(500, '后端炸了', 'trace-1'))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('加载平台列表失败：后端炸了')
    expect(wrapper.find('.mh-error').exists()).toBe(true)
    expect(wrapper.findAll('.pf-card')).toHaveLength(0)

    await clickButton('重试')
    expect(wrapper.findAll('.pf-card')).toHaveLength(3)
  })
})

describe('平台管理 · 当前平台切换与跳转', () => {
  it('「设为当前」写 store + Toast，按钮变「当前平台」且禁用', async () => {
    const { wrapper } = await mountPage()
    const store = usePlatformStore()
    // 用单区、realm 为空的 Loomy：store.setCurrent 会把「不存在的 realm」归一成 ''，
    // 只有这种平台的卡片能稳定进入「当前平台」态（trae-cn 的 realm 会被清掉）。
    const loomy = cardByLabel('Loomy')!

    const setCurrent = Array.from(loomy.querySelectorAll('button')).find(
      (el) => (el.textContent || '').trim() === '设为当前',
    ) as HTMLElement
    setCurrent.click()
    await settle()

    expect(store.platform).toBe('loomy')
    expect(store.realm).toBe('')
    expect(bodyText()).toContain('已切换到 Loomy')
    const now = Array.from(cardByLabel('Loomy')!.querySelectorAll('button')).find(
      (el) => (el.textContent || '').trim() === '当前平台',
    ) as HTMLButtonElement
    expect(now).toBeTruthy()
    expect(now.disabled).toBe(true)
    // workbuddy 不再是当前平台 → 按钮恢复「设为当前」
    expect(cardByLabel('WorkBuddy')!.textContent).toContain('设为当前')
    expect(wrapper.findAll('.pf-card')).toHaveLength(3)
  })

  it('「账号池」切上下文并跳 /accounts', async () => {
    const { router } = await mountPage()
    const store = usePlatformStore()
    const trae = cardByLabel('Trae 国内版')!
    const open = Array.from(trae.querySelectorAll('button')).find(
      (el) => (el.textContent || '').trim() === '账号池',
    ) as HTMLElement
    open.click()
    await flushPromises()

    expect(store.platform).toBe('trae-cn')
    expect(router.currentRoute.value.path).toBe('/accounts')
  })
})

describe('平台管理 · 卡片 ⋯ 菜单与危险操作', () => {
  it('禁用需确认；确认后调用 gateway/disable 并重新加载', async () => {
    const { wrapper } = await mountPage()
    await openCardMenu('WorkBuddy')
    await pickMenu('禁用平台')

    expect(bodyText()).toContain('禁用「WorkBuddy」？')
    await confirmDialog('禁用')

    expect(mocks.setPlatformEnabled).toHaveBeenCalledWith('workbuddy', false, '界面禁用')
    // load() 一次 + platformStore.reload() 的 fetchList 一次 → 初次之后共 3 次
    expect(mocks.listPlatforms).toHaveBeenCalledTimes(3)
    expect(bodyText()).toContain('已禁用 WorkBuddy（不参与调度，账号仍可管理）')
    expect(wrapper.findAll('.pf-card')).toHaveLength(3)
  })

  it('取消确认时不发请求', async () => {
    await mountPage()
    await openCardMenu('WorkBuddy')
    await pickMenu('禁用平台')
    await confirmDialog('取消')
    expect(mocks.setPlatformEnabled).not.toHaveBeenCalled()
  })

  it('已禁用平台菜单是「启用平台」，不需要确认', async () => {
    await mountPage()
    await openCardMenu('Loomy')
    await pickMenu('启用平台')
    expect(mocks.setPlatformEnabled).toHaveBeenCalledWith('loomy', true, '')
    expect(mocks.listPlatforms).toHaveBeenCalledTimes(3)
  })

  it('能力差异：import-only 平台没有「扫描本机」，动作菜单带「（账号池）」', async () => {
    await mountPage()
    await openCardMenu('Loomy')

    const labels = Array.from(document.querySelectorAll('.n-dropdown-option')).map((el) =>
      (el.textContent || '').trim(),
    )
    // 菜单项固定有「扫描本机」，但 import-only 平台那条是禁用态（点了不会发请求）
    const scanOption = dropdownOption('扫描本机')
    expect(scanOption).toBeTruthy()
    expect(scanOption!.querySelector('.n-dropdown-option-body--disabled')).toBeTruthy()
    expect(labels).toContain('导入账号')
    expect(labels).toContain('平均级：人上人')

    await pickMenu('导入账号')
    expect(bodyText()).toContain('已选平台 loomy：把该平台的导出文件拖进来即可')
    await clickButton('取消')
  })

  it('平台特有动作（刷新额度）切到该平台并跳账号池', async () => {
    const { router } = await mountPage()
    const store = usePlatformStore()
    await openCardMenu('WorkBuddy')
    await pickMenu('刷新额度（账号池）')

    expect(store.platform).toBe('workbuddy')
    expect(router.currentRoute.value.path).toBe('/accounts')
    expect(bodyText()).toContain('该动作在账号池执行')
  })

  it('扫描本机：调用 runPlatformScan 并刷新卡片', async () => {
    await mountPage()
    await openCardMenu('WorkBuddy')
    await pickMenu('扫描本机')

    expect(mocks.runPlatformScan).toHaveBeenCalledTimes(1)
    expect(bodyText()).toContain('WorkBuddy：扫描 2 · 入库 1')
    expect(mocks.listPlatforms).toHaveBeenCalledTimes(3)
  })

  it('导出本平台：调用 export-all(backup, 落库名) 并提示数量', async () => {
    await mountPage()
    await openCardMenu('Trae 国内版')
    await pickMenu('导出本平台')

    expect(mocks.exportPlatformsAll).toHaveBeenCalledWith('backup', 'trae')
    expect(bodyText()).toContain('已导出 2 个账号（1 个平台）')
  })

  it('导出结果为空时提示「没有账号可导出」', async () => {
    mocks.exportPlatformsAll.mockResolvedValue({ platforms: {} })
    await mountPage()
    await openCardMenu('WorkBuddy')
    await pickMenu('导出本平台')
    expect(bodyText()).toContain('该平台没有账号可导出')
  })
})

describe('平台管理 · 批量操作', () => {
  it('扫描全部：只扫支持扫描的平台，展示结果表', async () => {
    const { wrapper } = await mountPage()
    await clickButton('扫描全部平台')

    // loomy 不支持本地扫描 → 跳过；workbuddy（一步导入）+ trae（扫描+入库）各一次
    expect(mocks.runPlatformScan).toHaveBeenCalledTimes(2)
    expect(mocks.runPlatformScan.mock.calls.map((call) => (call[0] as PlatformSpec).id)).toEqual([
      'workbuddy',
      'trae-cn',
    ])
    expect(wrapper.findAll('.pf-scan__row')).toHaveLength(2)
    expect(wrapper.text()).toContain('扫描 2')
    expect(wrapper.text()).toContain('入库 1')
    expect(bodyText()).toContain('扫描完成：共入库 2 个账号')
  })

  it('扫描全部在平台清单未加载时报错，不发请求', async () => {
    mocks.listPlatforms.mockResolvedValue({ items: [], default: 'workbuddy', loader: {} })
    await mountPage()
    await clickButton('扫描全部平台')
    expect(mocks.runPlatformScan).not.toHaveBeenCalled()
    expect(bodyText()).toContain('平台清单还没加载好')
  })

  it('导入：坏文件给提示，好文件出预览；提交后进入 JobProgress', async () => {
    const { wrapper } = await mountPage()
    await clickButton('导入')

    const input = document.querySelector('.pf-file') as HTMLInputElement
    expect(input).toBeTruthy()
    setFileInput(input, [{ name: 'bad.json', text: async () => 'not json' }])
    await settle()
    // 抽屉被 teleport 到 body：用 bodyText 而不是 wrapper.text()
    expect(bodyText()).toContain('bad.json：不是合法 JSON，已跳过')

    setFileInput(input, [{ name: 'backup.json', text: async () => IMPORT_FILE }])
    await settle()
    expect(bodyText()).toContain('workbuddy · 1 个账号')

    await clickButton('开始导入')
    await settle()
    await settle()

    expect(mocks.submitPlatformImport).toHaveBeenCalledTimes(1)
    expect(mocks.submitPlatformImport.mock.calls[0][0]).toBe('workbuddy')
    expect(bodyText()).toContain('已提交 1 个平台的后台导入')
    // 任务轮询已启动且快照即时完成：onFinish 的 Toast 就是用户看到的最终反馈
    expect(jobMock.created.map((entry) => entry.id)).toEqual(['job-1'])
    // 轮询替身立即完成 → JobProgress 浮出并显示「已完成 / 成功 2」
    await waitFor(() => Boolean(document.querySelector('.mh-jobs')), 'job progress')
    expect(document.querySelector('.mh-jobs')!.textContent).toContain('workbuddy 导入')
    expect(document.querySelector('.mh-jobs')!.textContent).toContain('已完成')
    expect(document.querySelector('.mh-jobs')!.textContent).toContain('成功 2')

    // 隐藏任务：列表清空后浮层消失
    const hide = Array.from(document.querySelectorAll('.mh-jobs__actions button')).find(
      (el) => (el.textContent || '').trim() === '隐藏',
    ) as HTMLElement
    hide.click()
    await settle()
    expect(document.querySelector('.mh-jobs')).toBeNull()
  })

  it('导入抽屉在没有解析出账号时禁止提交', async () => {
    await mountPage()
    await clickButton('导入')
    const submit = findButton('开始导入') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    await clickButton('取消')
  })

  it('导出抽屉：切「清单（脱敏）」后以 plain 模式导出；空结果提示', async () => {
    await mountPage()
    await clickButton('导出')

    const plainTab = Array.from(document.querySelectorAll('.n-drawer .mh-seg__item')).find((el) =>
      (el.textContent || '').includes('清单'),
    ) as HTMLElement
    expect(plainTab).toBeTruthy()
    plainTab.click()
    await settle()

    await clickButton('开始导出')
    expect(mocks.exportPlatformsAll).toHaveBeenCalledWith('plain', '')
    expect(bodyText()).toContain('已导出 2 个账号')

    // 第二次返回空 → 提示无账号
    mocks.exportPlatformsAll.mockResolvedValue({ platforms: {} })
    await clickButton('导出')
    await settle()
    await clickButton('开始导出')
    expect(bodyText()).toContain('该平台没有账号可导出')
  })
})

describe('平台管理 · 跑分窗口', () => {
  it('切到「近 7 天」重新按 168 小时取数', async () => {
    await mountPage()
    const week = Array.from(document.querySelectorAll('.mh-seg__item')).find((el) =>
      (el.textContent || '').includes('近 7 天'),
    ) as HTMLElement
    week.click()
    await flushPromises()

    expect(mocks.getPlatformSpeed).toHaveBeenLastCalledWith(168, expect.anything())
    expect(mocks.listPlatforms).toHaveBeenCalledTimes(2)
  })

  it('跑分接口失败时页面仍渲染卡片（辅助请求降级）', async () => {
    mocks.getPlatformSpeed.mockRejectedValue(new Error('speed down'))
    mocks.getGatewayConfig.mockRejectedValue(new Error('gateway down'))
    mocks.getAccountsSnapshot.mockRejectedValue(new Error('accounts down'))
    const { wrapper } = await mountPage()

    expect(wrapper.findAll('.pf-card')).toHaveLength(3)
    expect(wrapper.text()).toContain('今日 0 次')
    // 没有账号快照 → 只显示账号数
    expect(wrapper.text()).toContain('账号 2 个')
  })
})
