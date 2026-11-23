/**
 * 覆盖率补充：KeysPage（S14 /keys）真实挂载交互。
 *
 * 只新增本文件：不改 src/**、package.json、vite 配置与其他测试，不新增依赖。
 * 网络层 mock 在 @/api/keys（页面唯一的取数入口），api/keys 的纯函数
 * （toKeyRow / filterKeyRows / validateKeyForm …）走真实实现，断言既证明 DOM 变化，
 * 也真的跑到页面里的分支。契约见 docs/ui-v2/contracts/api-keys.md「实现对照」。
 *
 * 覆盖点：首屏（表格/汇总/对接信息/模型 chip/选号策略）· 辅助接口降级 · 空态 ·
 *        搜索防抖 + chip 移除 · 分组/状态筛选 · 重置 · 分页与每页条数 · 排序 ·
 *        创建抽屉（额度/并发/频率校验、提交、成功弹窗）· 创建失败 Toast ·
 *        编辑抽屉（预填、名称必填、重置已用额度）·
 *        复制完整 Key（成功/剪贴板失败/读取失败）· 停用/启用 · 重置用量确认 · 删除确认 ·
 *        用量抽屉 · 来源 IP 抽屉（含时间范围切换）· 全部模型抽屉 + 搜索 · 接入指南 ·
 *        选号策略跳转 · 列表失败错误态与重试 · 刷新。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider, NSelect } from 'naive-ui'
import KeysPage from '@/pages/KeysPage.vue'
import DataTable from '@/components/data/DataTable.vue'
import RowActions from '@/components/data/RowActions.vue'
import Copyable from '@/components/base/Copyable.vue'
import * as keysApi from '@/api/keys'
import * as copyModule from '@/composables/useCopy'
import { ApiError } from '@/api/http'
import type {
  AggregateModelsPayload,
  ApiKeyItem,
  KeyCreatePayload,
  KeyIpsPayload,
  KeyUsagePayload,
  KeyViewPayload,
} from '@/api/keys'

vi.mock('@/api/keys', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/keys')>()
  return {
    ...actual,
    listKeys: vi.fn(),
    createKey: vi.fn(),
    viewKey: vi.fn(),
    keyUsage: vi.fn(),
    keyIps: vi.fn(),
    patchKey: vi.fn(),
    deleteKey: vi.fn(),
    getSelectionPolicy: vi.fn(),
    getNetwork: vi.fn(),
    listAggregateModels: vi.fn(),
    listGroupOptions: vi.fn(),
    listAccountOptions: vi.fn(),
    listPlatformOptions: vi.fn(),
  }
})

// 剪贴板在 jsdom 里没有 navigator.clipboard，execCommand 也未实现；
// 复制按钮的成功/失败分支改用替身驱动（页面只依赖 copyText 的布尔结果）。
vi.mock('@/composables/useCopy', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/composables/useCopy')>()
  return { ...actual, copyText: vi.fn() }
})

const listKeys = vi.mocked(keysApi.listKeys)
const createKey = vi.mocked(keysApi.createKey)
const viewKey = vi.mocked(keysApi.viewKey)
const keyUsage = vi.mocked(keysApi.keyUsage)
const keyIps = vi.mocked(keysApi.keyIps)
const patchKey = vi.mocked(keysApi.patchKey)
const deleteKey = vi.mocked(keysApi.deleteKey)
const getSelectionPolicy = vi.mocked(keysApi.getSelectionPolicy)
const getNetwork = vi.mocked(keysApi.getNetwork)
const listAggregateModels = vi.mocked(keysApi.listAggregateModels)
const listGroupOptions = vi.mocked(keysApi.listGroupOptions)
const listAccountOptions = vi.mocked(keysApi.listAccountOptions)
const listPlatformOptions = vi.mocked(keysApi.listPlatformOptions)
const copyText = vi.mocked(copyModule.copyText)

/* -------------------------------------------------------------------------- */
/* fixtures（真实响应形状：admin/routers/keys.py）                              */
/* -------------------------------------------------------------------------- */

function keyItem(overrides: Partial<ApiKeyItem> = {}): ApiKeyItem {
  return {
    id: 7,
    name: '给设计组的 Key',
    key_prefix: 'sk-3f9a2b7c1d0e',
    masked: 'sk-3f9a2b****c1d0',
    credit_limit: 100,
    credit_used: 96,
    unlimited: false,
    max_concurrent: 0,
    rate_per_minute: null,
    status: 'active',
    note: '设计组共享',
    group_id: 3,
    group_name: '便宜模型组',
    account_id: null,
    account_ids: [11, 12],
    platform: 'trae',
    aggregate: true,
    account_name: '',
    created_at: '2026-11-20T09:12:33',
    ip_summary: {
      days: 1,
      unique_ips: 4,
      requests: 128,
      active_ips: 1,
      active_requests: 2,
      top_ips: [
        { ip: '10.0.0.8', active_requests: 2, requests: 90 },
        { ip: '10.0.0.9', active_requests: 0, requests: 38 },
      ],
      active_scope: '当前服务进程',
    },
    ...overrides,
  }
}

const ACTIVE_KEY = keyItem()
const REVOKED_KEY = keyItem({
  id: 8,
  name: '旧测试 Key',
  status: 'revoked',
  unlimited: true,
  credit_limit: 0,
  credit_used: 12,
  group_id: 0,
  group_name: '',
  account_ids: [],
  account_id: null,
  ip_summary: { days: 1, unique_ips: 0, requests: 0, active_ips: 0, active_requests: 0, top_ips: [] },
})

function aggregate(): AggregateModelsPayload {
  return {
    items: [
      {
        key: 'deepseek-v4-flash',
        name: 'DeepSeek-V4.1-Flash',
        platform_count: 2,
        platforms: [
          { platform: 'workbuddy', model_id: 'deepseek-v4.1-flash', multiplier: 1, enabled: true, platform_label: 'WorkBuddy' },
          { platform: 'trae', model_id: 'dfmodel', client_id: 'dfmodel-trae', multiplier: 0, enabled: true, platform_label: 'Trae' },
        ],
      },
      {
        key: 'glm-5.3-flash',
        name: 'GLM-5.3-Flash',
        platform_count: 1,
        platforms: [{ platform: 'qoder', model_id: 'glm-5.3-flash', multiplier: 0.5, enabled: true }],
      },
      {
        key: 'disabled-one',
        name: '停用模型',
        platform_count: 1,
        platforms: [{ platform: 'trae', model_id: 'x', enabled: false }],
      },
    ],
    total_models: 3,
    total_groups: 3,
  }
}

const POLICY = {
  mode: 'strict-expiry',
  upstream_start_interval_ms: 500,
  modes: [
    { value: 'strict-expiry', label: '严格到期优先', description: '优先挑积分快过期的账号' },
    { value: 'balanced', label: '均衡', description: '按余额比例分摊' },
  ],
}

const NETWORK = { lan_ips: ['192.168.1.7'], port: 8790 }

function usagePayload(overrides: Partial<KeyUsagePayload> = {}): KeyUsagePayload {
  return {
    id: 7,
    name: '给设计组的 Key',
    credit_limit: 100,
    credit_used: 96,
    credit_remaining: 4,
    usage_percent: 96,
    unlimited: false,
    status: 'active',
    created_at: '2026-11-20T09:12:33',
    ...overrides,
  }
}

function ipsPayload(overrides: Partial<KeyIpsPayload> = {}): KeyIpsPayload {
  return {
    id: 7,
    name: '给设计组的 Key',
    days: 1,
    sort: 'requests',
    direction: 'desc',
    offset: 0,
    limit: 100,
    total_ips: 2,
    total_requests: 30,
    active_ips: 1,
    active_requests: 2,
    active_scope: '当前服务进程',
    active: [{ ip: '10.0.0.8', active_requests: 2, blocked: false, blocked_id: null }],
    items: [
      {
        ip: '10.0.0.8', requests: 20, failed: 1, credits: 3.5, tokens: 1200,
        last_seen: '2026-11-23T03:00:00', active_requests: 2, blocked: false, blocked_id: null,
        models: [{ model: 'glm-5.3-flash', requests: 20, tokens: 1200, last_seen: null, recent: true }],
      },
      {
        ip: '10.0.0.9', requests: 10, failed: 0, credits: 1, tokens: 300,
        last_seen: null, active_requests: 0, blocked: true, blocked_id: 3,
        models: [],
      },
    ],
    ...overrides,
  }
}

function createdPayload(): KeyCreatePayload {
  return {
    id: 21,
    name: 'Key A',
    key: 'sk-created-21-full-value',
    key_prefix: 'sk-created',
    credit_limit: 20,
    unlimited: false,
    max_concurrent: 2,
    rate_per_minute: 60,
    group_id: 3,
    account_id: null,
    account_ids: [12],
    platform: 'trae',
    aggregate: true,
  }
}

/* -------------------------------------------------------------------------- */
/* mount helpers                                                              */
/* -------------------------------------------------------------------------- */

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>`,
})

interface Mounted {
  wrapper: VueWrapper
  router: Router
}

let mounted: Mounted | null = null

/** ≤640px 断点由 DataTable 的 useMediaQuery 读取；手机模式才渲染行内单元格与行操作。 */
function stubMatchMedia(mobile: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: mobile && query === '(max-width: 640px)',
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }))
}

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
  await nextTick()
}

async function mountPage(options: { mobile?: boolean } = {}): Promise<Mounted> {
  stubMatchMedia(options.mobile !== false)
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/keys', name: 'keys', component: KeysPage },
      { path: '/credits', name: 'credits', component: { template: '<div>credits</div>' } },
      { path: '/usage', name: 'usage', component: { template: '<div>usage</div>' } },
      { path: '/logs', name: 'logs', component: { template: '<div>logs</div>' } },
      { path: '/ip', name: 'ip', component: { template: '<div>ip</div>' } },
    ],
  })
  await router.push('/keys')
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await settle()
  await settle()
  mounted = { wrapper, router }
  return mounted
}

function bodyText(): string {
  return String(document.body.textContent || '')
}

/** naive 的浮层 / 抽屉 / 对话框都带过渡，按真实时间轮询等待目标节点出现或消失。 */
async function waitFor(predicate: () => boolean, label: string, rounds = 30): Promise<void> {
  for (let index = 0; index < rounds; index += 1) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 20))
    await nextTick()
    await flushPromises()
  }
  throw new Error('waitFor timeout: ' + label + ' | body=' + bodyText().slice(0, 200))
}

/** 当前打开的只读抽屉正文（DetailDrawer 的 .mh-detail__body）。 */
function drawerText(): string {
  const bodies = Array.from(document.querySelectorAll('.mh-detail__body')) as HTMLElement[]
  if (!bodies.length) throw new Error('no detail drawer open | body=' + bodyText().slice(0, 200))
  return bodies[bodies.length - 1].textContent || ''
}

function buttonsIn(root: ParentNode): HTMLButtonElement[] {
  return Array.from(root.querySelectorAll('button')) as HTMLButtonElement[]
}

function findButton(root: ParentNode, text: string, index = 0): HTMLButtonElement {
  const list = buttonsIn(root).filter((el) => (el.textContent || '').trim() === text)
  const target = list[index]
  if (!target) {
    throw new Error('button not found: ' + text + ' | have=' + buttonsIn(root).map((el) => (el.textContent || '').trim()).join('|'))
  }
  return target
}

async function clickButton(root: ParentNode, text: string, index = 0): Promise<void> {
  findButton(root, text, index).click()
  await settle()
}

function headerActions(): HTMLElement {
  const el = document.querySelector('.ph-actions') as HTMLElement | null
  if (!el) throw new Error('page header actions not found')
  return el
}

function cards(): HTMLElement[] {
  return Array.from(document.querySelectorAll('.mh-cards__item')) as HTMLElement[]
}

async function openRowMenu(cardIndex = 0): Promise<void> {
  const card = cards()[cardIndex]
  if (!card) throw new Error('card not found: ' + cardIndex + ' | cards=' + cards().length)
  const trigger = card.querySelector('button[aria-label="更多操作"]') as HTMLElement | null
  if (!trigger) throw new Error('row menu trigger not found')
  trigger.click()
  await waitFor(() => document.querySelectorAll('.mh-row-actions__menu').length > 0, 'row actions menu')
}

function lastRowMenu(): HTMLElement {
  const menus = Array.from(document.querySelectorAll('.mh-row-actions__menu')) as HTMLElement[]
  if (!menus.length) throw new Error('row menu not rendered | body=' + bodyText().slice(0, 200))
  return menus[menus.length - 1]
}

async function clickRowMenu(text: string): Promise<void> {
  const menu = lastRowMenu()
  const target = buttonsIn(menu).find((el) => (el.textContent || '').trim() === text)
  if (!target) throw new Error('menu item not found: ' + text + ' | have=' + buttonsIn(menu).map((el) => el.textContent).join('|'))
  target.click()
  await settle()
}

/**
 * 行操作：直接向 RowActions 发 select。
 *
 * 为什么不全走 DOM：⋯ 菜单是 NPopover，jsdom 没有真实「点击外部」事件；
 * 一旦中间开过 NDialog（确认框），再点触发器会把还处于打开态的浮层关掉，
 * 第二次就取不到菜单节点。菜单本身的可点性由「停用/启用」用例以真实 DOM 覆盖，
 * 这里只覆盖页面对 select 的处理链。
 */
async function rowAction(wrapper: VueWrapper, cardIndex: number, key: string): Promise<void> {
  const actions = wrapper.findAllComponents(RowActions)
  const target = actions[cardIndex]
  if (!target) throw new Error('RowActions not found: ' + cardIndex + ' | count=' + actions.length)
  target.vm.$emit('select', key)
  await settle()
}

/** useConfirm → NDialog；正按钮文案随场景变化（删除 / 重置用量）。 */
async function answerDialog(okText: string): Promise<void> {
  await settle()
  // 已关闭的对话框仍留在 DOM 里（只是隐藏），必须取最后一个（最新打开的）那个。
  const dialogs = Array.from(document.querySelectorAll('.n-dialog'))
  const dialog = dialogs[dialogs.length - 1]
  if (!dialog) throw new Error('dialog not found | body=' + bodyText().slice(0, 200))
  const buttons = Array.from(dialog.querySelectorAll('.n-dialog__action button')) as HTMLElement[]
  const target = buttons.find((el) => (el.textContent || '').trim() === okText) || buttons[buttons.length - 1]
  target.click()
  // naive 的对话框关闭后仍留在 DOM 里（只是隐藏），过渡期间会挂 click-outside；
  // 不等它结束就点下一个触发器，浮层会被立刻关掉。
  await new Promise((resolve) => setTimeout(resolve, 300))
  await settle()
}

function inputByPlaceholder(placeholder: string, index = 0): HTMLInputElement {
  const list = Array.from(document.querySelectorAll('input')).filter((el) => el.getAttribute('placeholder') === placeholder)
  const target = list[index]
  if (!target) {
    throw new Error(
      'input not found: ' + placeholder + ' | have=' +
        Array.from(document.querySelectorAll('input')).map((el) => el.getAttribute('placeholder')).join('|'),
    )
  }
  return target as HTMLInputElement
}

async function typeInto(el: HTMLInputElement, value: string): Promise<void> {
  el.value = value
  el.dispatchEvent(new Event('input'))
  await settle()
}

async function toggleCheckbox(text: string): Promise<void> {
  const boxes = Array.from(document.querySelectorAll('.n-checkbox')) as HTMLElement[]
  const box = boxes.find((el) => (el.textContent || '').includes(text))
  if (!box) throw new Error('checkbox not found: ' + text + ' | have=' + boxes.map((el) => el.textContent).join('|'))
  box.click()
  await settle()
}

interface SelectOptionLike {
  label?: string
  value?: string | number
}

/**
 * 下拉选择：按「选项文案」定位到 NSelect 实例再发 update:value。
 *
 * 为什么不用 DOM 点选项：naive 的下拉选项走虚拟列表，jsdom 里容器高度为 0，
 * 菜单能弹出但选项节点不渲染（点不到）。按选项文案定位实例，既能避开这个环境限制，
 * 也仍然走页面真实的 v-model / @update:value 处理链。
 */
async function chooseInSelect(
  wrapper: VueWrapper,
  optionLabel: string,
  value: string | number,
): Promise<void> {
  const target = wrapper.findAllComponents(NSelect).find((select) =>
    ((select.props('options') || []) as SelectOptionLike[]).some((option) => option.label === optionLabel),
  )
  if (!target) throw new Error('select with option not found: ' + optionLabel)
  target.vm.$emit('update:value', value)
  await settle()
}

function drawerFooter(): HTMLElement {
  const el = document.querySelector('.mh-form__actions') as HTMLElement | null
  if (!el) throw new Error('form drawer footer not found | body=' + bodyText().slice(0, 200))
  return el
}

async function waitDebounce(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 360))
  await settle()
}

/* -------------------------------------------------------------------------- */
/* setup / teardown                                                           */
/* -------------------------------------------------------------------------- */

beforeEach(() => {
  vi.clearAllMocks()
  // jsdom 没实现 scrollTo：naive 的下拉虚拟列表会调用它，否则抛未捕获的 rejection
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { value: () => undefined, configurable: true })
  HTMLElement.prototype.scrollIntoView = () => undefined
  listKeys.mockResolvedValue({ items: [ACTIVE_KEY, REVOKED_KEY] })
  createKey.mockResolvedValue(createdPayload())
  viewKey.mockResolvedValue({
    id: 7,
    name: ACTIVE_KEY.name,
    key: 'sk-7-full-visible',
    key_prefix: 'sk-7',
    created_at: '2026-11-20T09:12:33',
  } as KeyViewPayload)
  keyUsage.mockResolvedValue(usagePayload())
  keyIps.mockResolvedValue(ipsPayload())
  patchKey.mockResolvedValue({ id: 7, ok: true })
  deleteKey.mockResolvedValue({ id: 7, ok: true })
  getSelectionPolicy.mockResolvedValue(POLICY)
  getNetwork.mockResolvedValue(NETWORK)
  listAggregateModels.mockResolvedValue(aggregate())
  listGroupOptions.mockResolvedValue({ items: [{ id: 3, name: '便宜模型组', effective_count: 5 }] })
  listAccountOptions.mockResolvedValue({
    items: [{ id: 12, name: '设计号', uid: 'u12', platform: 'trae', status: 'active', balance_remain: 88 }],
    total: 1,
  })
  listPlatformOptions.mockResolvedValue({ items: [{ id: 'trae', storage_platform: 'trae', label: 'Trae' }] })
  copyText.mockResolvedValue(true)
})

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  localStorage.clear()
})

/* -------------------------------------------------------------------------- */
/* tests                                                                      */
/* -------------------------------------------------------------------------- */

describe('KeysPage 首屏与对接信息', () => {
  it('渲染页头、汇总、对接地址、模型 chip 与选号策略', async () => {
    const { wrapper } = await mountPage({ mobile: false })

    expect(wrapper.find('h1').text()).toContain('API Keys')
    expect(wrapper.text()).toContain('共 2 个 Key · 正常 1 · 已停用 1 · 不限额 1')
    // PageHeader 的选号策略按钮取 /api/credits/selection-policy 的 modes 文案
    expect(findButton(document, '严格到期优先')).toBeTruthy()

    const copies = wrapper.findAllComponents(Copyable)
    expect(copies.length).toBeGreaterThanOrEqual(3)
    expect(copies.map((item) => item.props('value'))).toContain('http://localhost:3000/v1')

    // 可用模型 chip：同一模型跨平台合一只算一组，停用平台被过滤
    expect(wrapper.text()).toContain('2 组')
    expect(wrapper.text()).toContain('DeepSeek-V4.1-Flash')
    expect(findButton(document, '全部 2 ▸')).toBeTruthy()
    expect(wrapper.text()).not.toContain('停用模型')
  })

  it('卡片视图渲染行内单元格：Key 打码、额度、并发、来源 IP、分组、账号', async () => {
    await mountPage()

    const list = cards()
    expect(list).toHaveLength(2)
    const first = list[0].textContent || ''
    expect(first).toContain('给设计组的 Key')
    expect(first).toContain('独占 2 个账号')
    expect(first).toContain('sk-3f9a2b****c1d0')
    expect(first).toContain('96')
    expect(first).toContain('100')
    expect(first).toContain('不限')
    expect(first).toContain('频率随全局')
    expect(first).toContain('4 个 / 24h')
    expect(first).toContain('10.0.0.8')
    expect(first).toContain('便宜模型组')
    // 有限额 → Meter；不缺额 → 提示「不限额」
    expect(list[0].querySelector('.mh-meter')).toBeTruthy()
    expect(first).toContain('编辑')
    expect(first).toContain('复制 Key')

    const second = list[1].textContent || ''
    expect(second).toContain('旧测试 Key')
    expect(second).toContain('∞')
    expect(second).toContain('不限额')
    expect(second).toContain('全部模型')
    expect(second).toContain('公共账号池')
    expect(list[1].querySelector('.mh-meter')).toBeNull()
  })

  it('辅助接口失败只降级对应区域', async () => {
    getSelectionPolicy.mockRejectedValue(new Error('policy 500'))
    getNetwork.mockRejectedValue(new Error('network 500'))
    listAggregateModels.mockRejectedValue(new Error('aggregate 500'))
    const { wrapper } = await mountPage({ mobile: false })

    // 策略名回落、连接地址仍用当前访问地址、模型区给出「未配置」提示
    expect(findButton(document, '账号调度策略')).toBeTruthy()
    expect(wrapper.findAllComponents(Copyable).map((item) => item.props('value'))).toContain('http://localhost:3000/v1')
    expect(wrapper.text()).toContain('未配置模型白名单（放行全部模型）')
    // 主列表不受影响
    expect(wrapper.text()).toContain('共 2 个 Key')
  })

  it('没有 Key 时显示空态，点空态按钮打开创建抽屉', async () => {
    listKeys.mockResolvedValue({ items: [] })
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('还没有 Key。创建 Key 后即可对外调用网关。')
    expect(cards()).toHaveLength(0)
    await clickButton(document, '创建 Key', 1)
    expect(bodyText()).toContain('创建 API Key')
  })
})

describe('KeysPage 搜索 / 筛选 / 分页 / 排序', () => {
  it('搜索防抖后写入 query 并过滤卡片，chip 可移除', async () => {
    const { wrapper, router } = await mountPage()

    await typeInto(inputByPlaceholder('搜索名称 / 备注 / 账号'), '旧测试')
    await waitDebounce()

    expect(router.currentRoute.value.query.q).toBe('旧测试')
    expect(cards()).toHaveLength(1)
    expect(wrapper.text()).toContain('筛选出 1 / 共 2 个 Key · 正常 0')
    const chip = document.querySelector('.mh-filter__chip') as HTMLElement
    expect(chip.textContent).toContain('搜索')
    expect(chip.textContent).toContain('旧测试')

    chip.click()
    await settle()
    expect(router.currentRoute.value.query.q).toBeUndefined()
    expect(cards()).toHaveLength(2)
  })

  it('分组与状态筛选写入 query，筛选无结果可一键清除', async () => {
    const { wrapper, router } = await mountPage()

    await chooseInSelect(wrapper, '分组：全部', 'none')
    expect(router.currentRoute.value.query.group).toBe('none')
    expect(cards()).toHaveLength(1)

    await chooseInSelect(wrapper, '状态：全部', 'active')
    expect(router.currentRoute.value.query.status).toBe('active')
    expect(cards()).toHaveLength(0)
    expect(wrapper.text()).toContain('没有符合条件的 Key。清空筛选后可看到全部 Key。')

    await clickButton(document, '清除筛选')
    expect(router.currentRoute.value.query.group).toBeUndefined()
    expect(router.currentRoute.value.query.status).toBeUndefined()
    expect(cards()).toHaveLength(2)
  })

  it('分页与每页条数写进 query，条数记到 localStorage', async () => {
    listKeys.mockResolvedValue({
      items: Array.from({ length: 25 }, (_, index) =>
        keyItem({ id: 100 + index, name: '批量 Key ' + String(index + 1).padStart(2, '0'), group_id: 0, group_name: '' }),
      ),
    })
    const { wrapper, router } = await mountPage()

    expect(cards()).toHaveLength(20)
    const pager = document.querySelectorAll('.n-pagination-item')
    const page2 = Array.from(pager).find((el) => (el.textContent || '').trim() === '2') as HTMLElement | undefined
    expect(page2, 'pagination item 2').toBeTruthy()
    page2!.click()
    await settle()

    expect(router.currentRoute.value.query.page).toBe('2')
    expect(cards()).toHaveLength(5)

    wrapper.findComponent(DataTable).vm.$emit('update:pageSize', 50)
    await settle()
    expect(router.currentRoute.value.query.size).toBe('50')
    expect(router.currentRoute.value.query.page).toBeUndefined()
    expect(localStorage.getItem('mh.pageSize.keys')).toBe('50')
    expect(cards()).toHaveLength(25)
  })

  it('排序写进 query，取消排序清空', async () => {
    const { wrapper, router } = await mountPage()
    const table = wrapper.findComponent(DataTable)

    table.vm.$emit('update:sorter', { columnKey: 'used', order: 'descend' })
    await settle()
    expect(router.currentRoute.value.query.sort).toBe('used')
    expect(router.currentRoute.value.query.order).toBe('desc')
    expect(cards()[0].textContent).toContain('给设计组的 Key')

    table.vm.$emit('update:sorter', null)
    await settle()
    expect(router.currentRoute.value.query.sort).toBeUndefined()
    expect(router.currentRoute.value.query.order).toBeUndefined()
  })

  it('刷新按钮与对接信息刷新各自重新取数', async () => {
    await mountPage()
    expect(listKeys).toHaveBeenCalledTimes(1)

    await clickButton(headerActions(), '刷新')
    expect(listKeys).toHaveBeenCalledTimes(2)

    const shareRefresh = Array.from(document.querySelectorAll('.keys-share button')).find(
      (el) => (el.textContent || '').trim() === '刷新',
    ) as HTMLElement
    expect(shareRefresh).toBeTruthy()
    shareRefresh.click()
    await settle()
    expect(listAggregateModels).toHaveBeenCalledTimes(2)
  })
})

describe('KeysPage 创建 / 编辑抽屉', () => {
  it('创建抽屉：额度/并发/频率校验拦截，修正后提交并弹出完整 Key', async () => {
    const { wrapper } = await mountPage()
    await clickButton(headerActions(), '创建 Key')

    expect(bodyText()).toContain('创建 API Key')
    // 打开即预取表单候选（分组 / 平台 / 账号），账号只取首页 50 条
    expect(listGroupOptions).toHaveBeenCalledTimes(1)
    expect(listPlatformOptions).toHaveBeenCalledTimes(1)
    expect(listAccountOptions).toHaveBeenCalledWith({ search: '', pageSize: 50 })

    // 默认勾选「不限额」→ 金额输入禁用；取消后可填
    expect(inputByPlaceholder('留空不限额；0 表示零额度').disabled).toBe(true)
    await toggleCheckbox('不限额（∞）')
    expect(inputByPlaceholder('留空不限额；0 表示零额度').disabled).toBe(false)

    await typeInto(inputByPlaceholder('留空不限额；0 表示零额度'), 'abc')
    await clickButton(drawerFooter(), '创建')
    expect(bodyText()).toContain('额度必须是数字')
    expect(createKey).not.toHaveBeenCalled()

    await typeInto(inputByPlaceholder('留空不限额；0 表示零额度'), '20')
    await typeInto(inputByPlaceholder('空 = 全局，0 = 不限', 0), '-1')
    await clickButton(drawerFooter(), '创建')
    expect(bodyText()).toContain('并发上限不能小于 0')

    await typeInto(inputByPlaceholder('空 = 全局，0 = 不限', 0), '2')
    await typeInto(inputByPlaceholder('空 = 全局，0 = 不限', 1), '2.5')
    await clickButton(drawerFooter(), '创建')
    expect(bodyText()).toContain('每分钟请求数必须是整数')
    expect(createKey).not.toHaveBeenCalled()

    // 补齐合法值 + 分组 / 平台 / 独占账号 / 备注
    await typeInto(inputByPlaceholder('空 = 全局，0 = 不限', 1), '60')
    await typeInto(inputByPlaceholder('例如：给设计组的 Key'), 'Key A')
    const note = document.querySelector('textarea') as HTMLTextAreaElement
    note.value = '给设计组用'
    note.dispatchEvent(new Event('input'))
    await settle()
    await chooseInSelect(wrapper, '不限制（全部可用模型）', 3)
    await chooseInSelect(wrapper, '不限制（全部平台）', 'trae')
    const accountSelect = wrapper.findAllComponents(NSelect).find((item) => item.props('multiple'))
    expect(accountSelect, 'account multi select').toBeTruthy()
    accountSelect!.vm.$emit('search', '设计')
    accountSelect!.vm.$emit('update:value', [12])
    await settle()

    await clickButton(drawerFooter(), '创建')
    expect(createKey).toHaveBeenCalledTimes(1)
    expect(createKey.mock.calls[0][0]).toMatchObject({
      name: 'Key A',
      note: '给设计组用',
      unlimited: false,
      credit_limit: 20,
      max_concurrent: 2,
      rate_per_minute: 60,
      group_id: 3,
      platform: 'trae',
      account_ids: [12],
      aggregate: true,
    })
    // 成功弹窗只出现一次完整 Key，点「完成」后列表刷新
    expect(bodyText()).toContain('Key 已创建')
    expect(bodyText()).toContain('sk-created-21-full-value')
    expect(listKeys).toHaveBeenCalledTimes(2)
    await clickButton(document, '完成')
    // naive 的 NModal 关闭后留在 DOM 里（内联 display: none），按可见性判断
    await waitFor(
      () => ((document.querySelector('.keys-modal') as HTMLElement | null)?.style.display || '') === 'none',
      'created modal hidden',
    )
  })

  it('创建失败弹出错误 Toast', async () => {
    createKey.mockRejectedValue(new ApiError(400, '名称重复'))
    await mountPage()
    await clickButton(headerActions(), '创建 Key')
    await typeInto(inputByPlaceholder('例如：给设计组的 Key'), '重复的 Key')
    await clickButton(drawerFooter(), '创建')
    expect(bodyText()).toContain('创建 Key失败：名称重复')
    expect(listKeys).toHaveBeenCalledTimes(1)
  })

  it('编辑抽屉预填、名称必填、勾选重置已用额度后 PATCH', async () => {
    await mountPage()
    await clickButton(cards()[0], '编辑')

    expect(bodyText()).toContain('编辑 API Key')
    const nameInput = inputByPlaceholder('例如：给设计组的 Key')
    expect(nameInput.value).toBe('给设计组的 Key')
    expect(bodyText()).toContain('勾选后把已用积分归零（当前 96')

    await typeInto(nameInput, '   ')
    await clickButton(drawerFooter(), '保存')
    expect(bodyText()).toContain('名称不能为空')
    expect(patchKey).not.toHaveBeenCalled()

    await typeInto(inputByPlaceholder('例如：给设计组的 Key'), '改个名')
    await toggleCheckbox('勾选后把已用积分归零')
    await clickButton(drawerFooter(), '保存')

    expect(patchKey).toHaveBeenCalledTimes(1)
    expect(patchKey.mock.calls[0][0]).toBe(7)
    expect(patchKey.mock.calls[0][1]).toMatchObject({ name: '改个名', reset_used: true, group_id: 3 })
    expect(bodyText()).toContain('已保存')
    expect(listKeys).toHaveBeenCalledTimes(2)
  })
})

describe('KeysPage 行操作（复制 / 启停 / 重置 / 删除）', () => {
  it('复制完整 Key：成功、剪贴板被拒、读取失败三种提示', async () => {
    await mountPage()

    await clickButton(cards()[0], '复制 Key')
    expect(viewKey).toHaveBeenCalledWith(7)
    expect(copyText).toHaveBeenCalledWith('sk-7-full-visible')
    expect(bodyText()).toContain('已复制完整 Key')

    copyText.mockResolvedValue(false)
    await clickButton(cards()[0], '复制 Key')
    expect(bodyText()).toContain('复制失败：浏览器拒绝了剪贴板访问，请手动选中复制')

    viewKey.mockRejectedValue(new ApiError(410, '该 Key 创建于查看功能上线前'))
    await clickButton(cards()[0], '复制 Key')
    expect(bodyText()).toContain('读取 Key失败：该 Key 创建于查看功能上线前')
  })

  it('停用与启用直接生效并刷新列表', async () => {
    await mountPage()

    await openRowMenu(0)
    await clickRowMenu('停用')
    expect(patchKey).toHaveBeenCalledWith(7, { status: 'revoked' })
    expect(bodyText()).toContain('已停用')
    expect(listKeys).toHaveBeenCalledTimes(2)

    await openRowMenu(1)
    expect(lastRowMenu().textContent).toContain('启用')
    await clickRowMenu('启用')
    expect(patchKey).toHaveBeenCalledWith(8, { status: 'active' })
    expect(bodyText()).toContain('已启用')
  })

  it('停用失败弹出错误 Toast', async () => {
    patchKey.mockRejectedValue(new ApiError(500, '上游拒绝'))
    await mountPage()
    await openRowMenu(0)
    await clickRowMenu('停用')
    expect(bodyText()).toContain('停用 Key失败：上游拒绝')
    expect(listKeys).toHaveBeenCalledTimes(1)
  })

  it('重置用量需确认：取消不发请求，确认后 PATCH reset_used', async () => {
    const { wrapper } = await mountPage()

    await rowAction(wrapper, 0, 'reset')
    await waitFor(() => bodyText().includes('重置已用额度？'), 'reset confirm')
    expect(bodyText()).toContain('已用积分归零，历史日志保留')
    await answerDialog('取消')
    expect(patchKey).not.toHaveBeenCalled()

    await rowAction(wrapper, 0, 'reset')
    await waitFor(() => bodyText().includes('重置已用额度？'), 'reset confirm again')
    await answerDialog('重置用量')
    expect(patchKey).toHaveBeenCalledWith(7, { reset_used: true })
    expect(bodyText()).toContain('已重置已用额度')
    expect(listKeys).toHaveBeenCalledTimes(2)
  })

  it('删除需确认：取消不发请求，确认后 DELETE 并刷新', async () => {
    const { wrapper } = await mountPage()

    await rowAction(wrapper, 0, 'delete')
    await waitFor(() => bodyText().includes('删除该 Key？'), 'delete confirm')
    expect(bodyText()).toContain('删除后该 Key 立即无法调用网关，用量记录保留')
    await answerDialog('取消')
    expect(deleteKey).not.toHaveBeenCalled()

    await rowAction(wrapper, 0, 'delete')
    await waitFor(() => bodyText().includes('删除该 Key？'), 'delete confirm again')
    await answerDialog('删除')
    expect(deleteKey).toHaveBeenCalledWith(7)
    expect(bodyText()).toContain('已删除')
    expect(listKeys).toHaveBeenCalledTimes(2)
  })

  it('删除失败弹出错误 Toast', async () => {
    deleteKey.mockRejectedValue(new ApiError(404, 'Key 不存在'))
    await mountPage()
    await openRowMenu(0)
    await clickRowMenu('删除')
    await answerDialog('删除')
    expect(bodyText()).toContain('删除 Key失败：Key 不存在')
  })
})

describe('KeysPage 抽屉（用量 / 来源 IP / 全部模型 / 接入指南）', () => {
  it('查看用量渲染四格与进度条；无限额不渲染进度条', async () => {
    await mountPage()
    await openRowMenu(0)
    await clickRowMenu('查看用量')

    expect(keyUsage).toHaveBeenCalledWith(7)
    expect(bodyText()).toContain('Key 用量')
    expect(bodyText()).toContain('限额')
    expect(bodyText()).toContain('96.0%')
    expect(bodyText()).toContain('创建于')

    keyUsage.mockResolvedValue(usagePayload({ unlimited: true, usage_percent: null, credit_remaining: null }))
    await openRowMenu(1)
    await clickRowMenu('查看用量')
    expect(bodyText()).toContain('∞（无限制）')
    expect(bodyText()).toContain('N/A')
  })

  it('用量读取失败只在抽屉里显示错误', async () => {
    keyUsage.mockRejectedValue(new ApiError(502, '配额服务 502'))
    await mountPage()
    await openRowMenu(0)
    await clickRowMenu('查看用量')
    expect(bodyText()).toContain('读取用量失败：配额服务 502')
  })

  it('来源 IP 抽屉渲染汇总与明细，切换时间范围重新取数', async () => {
    await mountPage()
    const count = cards()[0].querySelector('.keys-ips__count') as HTMLElement
    expect(count.textContent).toContain('4 个 / 24h')
    count.click()
    await settle()

    expect(keyIps).toHaveBeenCalledWith(7, { days: 1 })
    expect(bodyText()).toContain('来源 IP · 给设计组的 Key')
    const drawer = drawerText()
    expect(drawer).toContain('10.0.0.8')
    expect(drawer).toContain('10.0.0.9')
    expect(drawer).toContain('调用次数')

    const sevenDays = Array.from(document.querySelectorAll('.mh-seg__item')).find(
      (el) => (el.textContent || '').includes('7 天'),
    ) as HTMLElement
    sevenDays.click()
    await settle()
    expect(keyIps).toHaveBeenLastCalledWith(7, { days: 7 })
  })

  it('来源 IP 读取失败显示错误块', async () => {
    keyIps.mockRejectedValue(new ApiError(500, '统计库超时'))
    await mountPage()
    ;(cards()[0].querySelector('.keys-ips__count') as HTMLElement).click()
    await settle()
    expect(bodyText()).toContain('读取来源 IP 失败：统计库超时')
  })

  it('全部模型抽屉可搜索调用名，无匹配给出提示', async () => {
    await mountPage({ mobile: false })
    await clickButton(document, '全部 2 ▸')

    expect(bodyText()).toContain('可用模型 · 2 组')
    expect(bodyText()).toContain('点击模型名复制调用名（带平台后缀）')
    expect(drawerText()).toContain('dfmodel-trae')

    const search = inputByPlaceholder('搜索模型名 / 调用名 / 上游 id')
    await typeInto(search, 'glm')
    expect(drawerText()).toContain('GLM-5.3-Flash')
    expect(drawerText()).not.toContain('DeepSeek-V4.1-Flash')

    await typeInto(search, 'zzz')
    expect(drawerText()).toContain('没有匹配的模型。')
  })

  it('接入指南按页签展示 curl 示例，地址取当前连接地址', async () => {
    await mountPage({ mobile: false })
    await clickButton(headerActions(), '接入指南')

    expect(bodyText()).toContain('接入指南')
    expect(bodyText()).toContain('OpenAI 协议')
    expect(bodyText()).toContain('curl http://localhost:3000/v1/chat/completions')
    expect(bodyText()).toContain('"model":"deepseek-v4.1-flash-workbuddy"')

    const claudeTab = Array.from(document.querySelectorAll('.n-tabs-tab')).find(
      (el) => (el.textContent || '').includes('Claude Code'),
    ) as HTMLElement
    claudeTab.click()
    await settle()
    expect(bodyText()).toContain('anthropic-version')
    expect(bodyText()).not.toContain('/v1/v1/messages')
  })

  it('点选号策略跳到积分到期页', async () => {
    const { router } = await mountPage({ mobile: false })
    await clickButton(headerActions(), '严格到期优先')
    expect(router.currentRoute.value.path).toBe('/credits')
  })
})

describe('KeysPage 错误态', () => {
  it('列表失败渲染 ErrorBlock，展开详情并重试后恢复', async () => {
    listKeys.mockRejectedValueOnce(new ApiError(500, '后端炸了', { hint: 'keys.py 抛错' }))
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('加载 Key 失败：后端炸了')
    await clickButton(document, '展开详细信息')
    expect(document.querySelector('.mh-error__detail')?.textContent).toContain('keys.py 抛错')
    expect(bodyText()).toContain('加载 Key失败：后端炸了')

    await clickButton(document, '重试')
    expect(listKeys).toHaveBeenCalledTimes(2)
    expect(cards()).toHaveLength(2)
    expect(wrapper.find('.mh-error').exists()).toBe(false)
  })
})
