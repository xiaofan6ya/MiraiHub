/** 覆盖率补充：SettingsPage 五个区块的真实挂载交互。只新增本文件，不改 src、不改其它测试。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import {
  NConfigProvider,
  NDialogProvider,
  NInput,
  NInputNumber,
  NMessageProvider,
  NSelect,
  NSwitch,
} from 'naive-ui'
import SettingsPage from '@/pages/SettingsPage.vue'
import DataTable from '@/components/data/DataTable.vue'
import { ApiError } from '@/api/http'
import * as settingsApi from '@/api/settings'
import type {
  AppSourceStatus,
  InvitationPayload,
  ProxyListPayload,
  ProxyNode,
  RuntimeField,
} from '@/api/settings'

/**
 * useToast 用真实 NMessageProvider，但把停留时长压到 0：
 * 契约文案仍渲染进 DOM（可断言），同时不会留下 2.5s / 6s 的悬挂计时器
 * （jsdom 收尾后计时器触发会读到已销毁的 window，被 vitest 记成 unhandled error）。
 */
vi.mock('@/composables', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/composables')>()
  const { useMessage } = await import('naive-ui')
  return {
    ...actual,
    useToast: () => {
      const message = useMessage()
      const send = (type: string, text: string) => {
        const fn =
          type === 'success' ? message.success : type === 'error' ? message.error : type === 'warning' ? message.warning : message.info
        return fn(text, { duration: 0, closable: false, keepAliveOnHover: false })
      }
      return {
        show: send,
        success: (text: string) => send('success', text),
        error: (text: string) => send('error', text),
        info: (text: string) => send('info', text),
        warning: (text: string) => send('warning', text),
        clear: () => message.destroyAll(),
      }
    },
  }
})

vi.mock('@/api/settings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/settings')>()
  return {
    ...actual,
    getRuntimeConfig: vi.fn(),
    saveRuntimeConfig: vi.fn(),
    listProxies: vi.fn(),
    createProxy: vi.fn(),
    patchProxy: vi.fn(),
    deleteProxy: vi.fn(),
    unblockProxy: vi.fn(),
    getInvitation: vi.fn(),
    saveInvitation: vi.fn(),
    changePassword: vi.fn(),
    getAppSourceStatus: vi.fn(),
    detectAppSource: vi.fn(),
    locateAppSource: vi.fn(),
    planUnpack: vi.fn(),
    startUnpack: vi.fn(),
    getUnpackProgress: vi.fn(),
    removeUnpackedSource: vi.fn(),
    searchAppSource: vi.fn(),
  }
})

const getRuntimeConfig = vi.mocked(settingsApi.getRuntimeConfig)
const saveRuntimeConfig = vi.mocked(settingsApi.saveRuntimeConfig)
const listProxies = vi.mocked(settingsApi.listProxies)
const createProxy = vi.mocked(settingsApi.createProxy)
const patchProxy = vi.mocked(settingsApi.patchProxy)
const deleteProxy = vi.mocked(settingsApi.deleteProxy)
const unblockProxy = vi.mocked(settingsApi.unblockProxy)
const getInvitation = vi.mocked(settingsApi.getInvitation)
const saveInvitation = vi.mocked(settingsApi.saveInvitation)
const changePassword = vi.mocked(settingsApi.changePassword)
const getAppSourceStatus = vi.mocked(settingsApi.getAppSourceStatus)
const detectAppSource = vi.mocked(settingsApi.detectAppSource)
const locateAppSource = vi.mocked(settingsApi.locateAppSource)
const planUnpack = vi.mocked(settingsApi.planUnpack)
const startUnpack = vi.mocked(settingsApi.startUnpack)
const removeUnpackedSource = vi.mocked(settingsApi.removeUnpackedSource)
const searchAppSource = vi.mocked(settingsApi.searchAppSource)

/* ------------------------------------------------------------------ *
 * 后端真实响应形状的造数
 *   GET /api/runtime-config      → admin/runtime_settings.py:snapshot
 *   GET /api/proxies             → admin/routers/proxies.py:list_proxies
 *   GET /api/settings/invitation → admin/invitation.py
 *   GET /api/app-source/status   → admin/routers/app_source.py
 * ------------------------------------------------------------------ */

function runtimeFields(): RuntimeField[] {
  return [
    {
      attr: 'GATEWAY_REQUEST_TIMEOUT', label: '网关总时限（秒）', group: '网关与超时', kind: 'float',
      desc: '单个请求的端到端硬上限', min: 1, max: 300, step: 1, restart: false,
      value: 60, default: 60, overridden: false,
    },
    {
      attr: 'ENABLE_DOCS', label: '开放 API 文档', group: '登录与安全', kind: 'bool',
      desc: '对外暴露 /docs 与 /openapi.json', min: null, max: null, step: 1, restart: true,
      value: false, default: false, overridden: false,
    },
    {
      attr: 'UPSTREAM_CLIENT_NAME', label: '上游 client 列产品名', group: '记录与上游', kind: 'str',
      desc: '', min: null, max: null, step: 1, restart: false,
      value: 'miraihub', default: 'miraihub', overridden: true,
    },
  ]
}

function proxyNode(id: number, overrides: Partial<ProxyNode> = {}): ProxyNode {
  return {
    id,
    label: '机房A-0' + id,
    url: 'http://user:***@10.0.0.' + id + ':8080',
    enabled: true,
    max_concurrency: 2,
    pool: '机房',
    blocked: false,
    blocked_until: null,
    last_ok_at: null,
    last_error: '',
    state: 'ready',
    ...overrides,
  }
}

function proxyPayload(items?: ProxyNode[]): ProxyListPayload {
  const list = items ?? [
    proxyNode(1),
    proxyNode(2, {
      label: '住宅-上海', pool: '住宅', url: 'socks5://u:***@1.2.3.4:1080',
      state: 'blocked', blocked: true, max_concurrency: 1,
    }),
  ]
  return {
    items: list,
    summary: {
      total: list.length,
      ready: list.filter((node) => node.state === 'ready').length,
      blocked: list.filter((node) => node.state === 'blocked').length,
      disabled: list.filter((node) => node.state === 'disabled').length,
      slots: list.reduce((sum, node) => sum + (node.enabled ? node.max_concurrency : 0), 0),
    },
    pools: ['机房', '住宅'],
    note: '地址以 *** 掩码展示，修改需整段重填。',
  }
}

function invitation(overrides: Partial<InvitationPayload> = {}): InvitationPayload {
  return {
    invite_code: 'ABC123',
    invite_url: 'https://wb.example.com/invite/ABC123',
    enabled: true,
    ...overrides,
  }
}

function sourceStatus(overrides: Partial<AppSourceStatus> = {}): AppSourceStatus {
  return {
    ok: true,
    dev_tools: true,
    install_dir: 'D:\\WorkBuddy',
    resources_dir: 'D:\\WorkBuddy\\resources',
    asar: 'D:\\WorkBuddy\\resources\\app.asar',
    source_dir: 'C:\\Temp\\wb-source',
    can_unpack: true,
    python: '3.14.0',
    node: '',
    ...overrides,
  }
}

/* ------------------------------------------------------------------ *
 * 挂载脚手架（沿用 tests/coverage-*.spec.ts 的模式）
 * ------------------------------------------------------------------ */

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: '<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>',
})

let mounted: { wrapper: VueWrapper; router: Router } | null = null

async function flush(): Promise<void> {
  await flushPromises()
  await nextTick()
  await flushPromises()
}

/** 抽屉 / 对话框有进入动画，给它一帧真实时间再断言。 */
async function settle(): Promise<void> {
  await flush()
  await new Promise((resolve) => setTimeout(resolve, 20))
  await flush()
}

async function mountPage(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/settings', name: 'settings', component: SettingsPage, meta: { title: '设置' } },
    ],
  })
  await router.push('/settings')
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flush()
  await flush()
  mounted = { wrapper, router }
  return mounted
}

function buttonByText(root: ParentNode, label: string): HTMLButtonElement | null {
  return (
    Array.from(root.querySelectorAll('button')).find(
      (button) => (button.textContent || '').trim() === label,
    ) || null
  )
}

async function click(root: ParentNode, label: string): Promise<HTMLButtonElement> {
  const button = buttonByText(root, label)
  expect(button, '找不到按钮：' + label).toBeTruthy()
  const target = button as HTMLButtonElement
  target.click()
  await settle()
  return target
}

/** 抽屉在 body 上（teleport），只能用原生 DOM 交互。 */
function drawer(): HTMLElement {
  const el = document.querySelector<HTMLElement>('.n-drawer')
  expect(el, '抽屉未打开').toBeTruthy()
  return el as HTMLElement
}

function nativeInput(placeholder: string): HTMLInputElement {
  // 用 placeholder 属性比较（不走 CSS 属性选择器：路径里的反斜杠在 CSS 字符串里是转义符）
  const el = Array.from(document.querySelectorAll<HTMLInputElement>('input')).find(
    (item) => item.placeholder === placeholder,
  )
  expect(el, '找不到输入框：' + placeholder).toBeTruthy()
  return el as HTMLInputElement
}

function typeInto(el: HTMLInputElement, value: string): void {
  el.value = value
  el.dispatchEvent(new Event('input'))
}

async function clickDialog(label: string): Promise<void> {
  await settle()
  const dialog = document.querySelector<HTMLElement>('.n-dialog')
  expect(dialog, '确认框未弹出').toBeTruthy()
  await click(dialog as HTMLElement, label)
  await settle()
}

function bodyText(): string {
  return document.body.textContent || ''
}

/** 底部保存条（SettingsLayout）：运行配置与邀请码都从这里保存 / 放弃，没有改动时不渲染。 */
function saveBarOf(wrapper: { element: Element }): HTMLElement {
  const el = wrapper.element.querySelector('.mh-savebar') as HTMLElement | null
  expect(el, '保存条未出现').toBeTruthy()
  return el as HTMLElement
}

beforeEach(() => {
  vi.clearAllMocks()
  getRuntimeConfig.mockResolvedValue({ fields: runtimeFields() })
  saveRuntimeConfig.mockResolvedValue({ changed: [], reverted: [], errors: {}, fields: runtimeFields() })
  listProxies.mockResolvedValue(proxyPayload())
  createProxy.mockResolvedValue({ ok: true, item: proxyNode(3) })
  patchProxy.mockResolvedValue({ ok: true, item: proxyNode(1, { enabled: false }) })
  deleteProxy.mockResolvedValue({ ok: true, id: 1 })
  unblockProxy.mockResolvedValue({ ok: true, id: 2 })
  getInvitation.mockResolvedValue(invitation())
  saveInvitation.mockResolvedValue(invitation())
  changePassword.mockResolvedValue({ ok: true, revoked_sessions: 2 })
  getAppSourceStatus.mockResolvedValue(sourceStatus())
  detectAppSource.mockResolvedValue(sourceStatus({ install_dir: 'E:\\WorkBuddy' }))
  locateAppSource.mockResolvedValue(sourceStatus())
  planUnpack.mockResolvedValue({ can_run: true, files: 1200 })
  startUnpack.mockResolvedValue({ ok: true })
  removeUnpackedSource.mockResolvedValue(sourceStatus({ source_dir: '' }))
  searchAppSource.mockResolvedValue({ ok: true, files: [] })
})

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
})

/* ------------------------------------------------------------------ *
 * 1. 首屏：5 个区块、运行配置分组与元信息、代理摘要、邀请、逆向产物 KV
 * ------------------------------------------------------------------ */

describe('SettingsPage · 首屏五个区块', () => {
  it('渲染区块锚点、运行配置字段与默认值/范围/覆盖标记', async () => {
    const { wrapper } = await mountPage()

    expect(wrapper.find('h1').text()).toContain('设置')
    for (const id of [
      'settings-runtime',
      'settings-proxies',
      'settings-invitation',
      'settings-password',
      'settings-appsource',
    ]) {
      expect(wrapper.find('#' + id).exists(), '区块 ' + id).toBe(true)
    }

    const runtime = wrapper.find('#settings-runtime')
    expect(runtime.text()).toContain('网关与超时')
    expect(runtime.text()).toContain('登录与安全')
    expect(runtime.text()).toContain('记录与上游')
    expect(runtime.text()).toContain('GATEWAY_REQUEST_TIMEOUT')
    expect(runtime.text()).toContain('默认 60')
    expect(runtime.text()).toContain('范围 1 ~ 300')
    expect(runtime.text()).toContain('已覆盖')
    expect(runtime.text()).toContain('重启后生效')
  })

  it('代理摘要与说明、邀请状态、逆向产物状态行都来自真实响应形状', async () => {
    const { wrapper } = await mountPage()

    const proxies = wrapper.find('#settings-proxies')
    expect(proxies.text()).toContain('共 2 个节点，可用 1，静默 1，停用 0')
    expect(proxies.text()).toContain('可并发槽位 3')
    expect(proxies.text()).toContain('地址以 *** 掩码展示')
    expect(proxies.findComponent(DataTable).props('data')).toHaveLength(2)

    const invite = wrapper.find('#settings-invitation')
    expect(invite.text()).toContain('已启用')
    expect(invite.text()).toContain('https://wb.example.com/invite/ABC123')

    const source = wrapper.find('#settings-appsource')
    expect(source.text()).toContain('D:\\WorkBuddy\\resources\\app.asar')
    expect(source.text()).toContain('3.14.0 / —')
    expect(source.text()).toContain('可拆包')
  })
})

/* ------------------------------------------------------------------ *
 * 2. 运行配置：改动 → 保存只发改动项 → 重启横幅 → 取消回滚
 * ------------------------------------------------------------------ */

describe('SettingsPage · 运行配置保存', () => {
  it('只提交改动项，保存后按 changed ∩ restart 显示重启横幅并清掉未保存标记', async () => {
    const fields = runtimeFields()
    const after = fields.map((field) =>
      field.attr === 'ENABLE_DOCS' ? { ...field, value: true, overridden: true } : field,
    )
    saveRuntimeConfig.mockResolvedValue({
      changed: ['ENABLE_DOCS'],
      reverted: [],
      errors: {},
      fields: after,
    })
    const { wrapper } = await mountPage()

    const docSwitch = wrapper
      .findAllComponents(NSwitch)
      .find((item) => (item.attributes('aria-label') || '') === '开放 API 文档')
    expect(docSwitch, '开放 API 文档 开关').toBeTruthy()
    docSwitch!.vm.$emit('update:value', true)
    await flush()

    // SettingsPage 把改动项数交给 SaveBar（第 5 轮：卡内保存按钮收进底部保存条），
    // 计数型 dirty 渲染「有 N 项未保存」，不再是布尔型的「有未保存的修改」。
    expect(saveBarOf(wrapper).textContent).toContain('有 1 项未保存')
    await click(saveBarOf(wrapper), '保存')

    expect(saveRuntimeConfig).toHaveBeenCalledTimes(1)
    expect(saveRuntimeConfig).toHaveBeenCalledWith({ ENABLE_DOCS: true })
    expect(wrapper.text()).toContain('有 1 项修改需要重启服务后生效')
    expect(bodyText()).toContain('已保存 1 项运行配置')
    expect(wrapper.element.querySelector('.mh-savebar'), '保存后保存条应消失').toBeNull()
  })

  it('数字与字符串控件改动都进入 diff（清空字符串 = 回落 .env），保存后按快照回填', async () => {
    saveRuntimeConfig.mockResolvedValueOnce({
      changed: ['GATEWAY_REQUEST_TIMEOUT'],
      reverted: ['UPSTREAM_CLIENT_NAME'],
      errors: {},
      fields: runtimeFields().map((field) => {
        if (field.attr === 'GATEWAY_REQUEST_TIMEOUT') return { ...field, value: 90, overridden: true }
        if (field.attr === 'UPSTREAM_CLIENT_NAME') return { ...field, value: 'miraihub', overridden: false }
        return field
      }),
    })
    const { wrapper } = await mountPage()

    const number = wrapper
      .findAllComponents(NInputNumber)
      .find((item) => (item.attributes('aria-label') || '') === '网关总时限（秒）')
    expect(number, '网关总时限 数字输入').toBeTruthy()
    number!.vm.$emit('update:value', 90)

    const text = wrapper
      .findAllComponents(NInput)
      .find((item) => item.props('placeholder') === 'miraihub')
    expect(text, '上游 client 列产品名 文本输入').toBeTruthy()
    text!.vm.$emit('update:value', '')

    await flush()
    await click(saveBarOf(wrapper), '保存')

    expect(saveRuntimeConfig).toHaveBeenCalledWith({
      GATEWAY_REQUEST_TIMEOUT: 90,
      UPSTREAM_CLIENT_NAME: '',
    })
    // changed / reverted 都算受理：清空字符串（回落默认）不应被当成「未保存」
    expect(number!.props('value')).toBe(90)
    expect(text!.props('value')).toBe('miraihub')
    expect(wrapper.element.querySelector('.mh-savebar'), '保存后保存条应消失').toBeNull()
  })

  it('保存返回 errors 时用错误 Toast 列出未保存项，未落库的改动留在草稿里', async () => {
    // 后端 save() 对 errors 里的字段只 continue（不落库），所以响应里 changed 与 errors 不会重叠。
    saveRuntimeConfig.mockResolvedValue({
      changed: [],
      reverted: [],
      errors: { ENABLE_DOCS: '需要重启才能生效' },
      fields: runtimeFields(),
    })
    const { wrapper } = await mountPage()
    const docSwitch = wrapper
      .findAllComponents(NSwitch)
      .find((item) => (item.attributes('aria-label') || '') === '开放 API 文档')
    docSwitch!.vm.$emit('update:value', true)
    await flush()
    await click(saveBarOf(wrapper), '保存')

    expect(bodyText()).toContain('部分配置未保存')
    expect(bodyText()).toContain('ENABLE_DOCS 需要重启才能生效')
    // errors 里的字段后端没有落库（runtime_settings.py:save 只 continue）：
    // 用返回的 fields 快照重置草稿会把用户刚改的值静默丢掉，所以值要留在草稿里、仍算未保存。
    expect(docSwitch!.props('value')).toBe(true)
    expect(saveBarOf(wrapper).textContent).toContain('有 1 项未保存')
    expect(buttonByText(saveBarOf(wrapper), '保存')?.disabled).toBe(false)
  })

  it('部分成功：成功的字段回填服务端值，未保存项留草稿，重试只再提交未保存项', async () => {
    saveRuntimeConfig.mockResolvedValueOnce({
      changed: ['GATEWAY_REQUEST_TIMEOUT'],
      reverted: [],
      errors: { ENABLE_DOCS: '需要重启才能生效' },
      fields: runtimeFields().map((field) =>
        field.attr === 'GATEWAY_REQUEST_TIMEOUT' ? { ...field, value: 90, overridden: true } : field,
      ),
    })
    const { wrapper } = await mountPage()
    const number = wrapper
      .findAllComponents(NInputNumber)
      .find((item) => (item.attributes('aria-label') || '') === '网关总时限（秒）')
    const docSwitch = wrapper
      .findAllComponents(NSwitch)
      .find((item) => (item.attributes('aria-label') || '') === '开放 API 文档')
    number!.vm.$emit('update:value', 90)
    docSwitch!.vm.$emit('update:value', true)
    await flush()
    await click(saveBarOf(wrapper), '保存')

    expect(saveRuntimeConfig).toHaveBeenNthCalledWith(1, { GATEWAY_REQUEST_TIMEOUT: 90, ENABLE_DOCS: true })
    // 落库的字段回到服务端快照（90，不再算改动）；失败的开关仍在草稿里
    expect(number!.props('value')).toBe(90)
    expect(docSwitch!.props('value')).toBe(true)
    // 只剩 ENABLE_DOCS 这一项未落库 → 保存条计数为 1。
    expect(saveBarOf(wrapper).textContent).toContain('有 1 项未保存')

    await click(saveBarOf(wrapper), '保存')
    expect(saveRuntimeConfig).toHaveBeenNthCalledWith(2, { ENABLE_DOCS: true })
  })

  it('保存失败时提示「保存运行配置失败」且改动不丢', async () => {
    saveRuntimeConfig.mockRejectedValue(new Error('503 upstream busy'))
    const { wrapper } = await mountPage()
    const docSwitch = wrapper
      .findAllComponents(NSwitch)
      .find((item) => (item.attributes('aria-label') || '') === '开放 API 文档')
    docSwitch!.vm.$emit('update:value', true)
    await flush()
    await click(saveBarOf(wrapper), '保存')

    expect(bodyText()).toContain('保存运行配置失败：503 upstream busy')
    expect(saveBarOf(wrapper).textContent).toContain('有 1 项未保存')
  })

  it('放弃：草稿回到上次读到的服务端值（不重新请求），保存条消失', async () => {
    const { wrapper } = await mountPage()
    const docSwitch = wrapper
      .findAllComponents(NSwitch)
      .find((item) => (item.attributes('aria-label') || '') === '开放 API 文档')
    docSwitch!.vm.$emit('update:value', true)
    await flush()
    expect(saveBarOf(wrapper).textContent).toContain('有 1 项未保存')

    const before = getRuntimeConfig.mock.calls.length
    await click(saveBarOf(wrapper), '放弃')

    expect(getRuntimeConfig.mock.calls.length).toBe(before)
    expect(docSwitch!.props('value')).toBe(false)
    expect(wrapper.text()).not.toContain('项未保存')
    expect(wrapper.element.querySelector('.mh-savebar')).toBeNull()
  })

  it('加载失败渲染 ErrorBlock，重试重新取数', async () => {
    getRuntimeConfig.mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountPage()

    const runtime = wrapper.find('#settings-runtime')
    expect(runtime.text()).toContain('加载运行配置失败：boom')
    const before = getRuntimeConfig.mock.calls.length
    getRuntimeConfig.mockResolvedValue({ fields: runtimeFields() })
    await click(runtime.element, '重试')

    expect(getRuntimeConfig.mock.calls.length).toBeGreaterThan(before)
    expect(wrapper.find('#settings-runtime').text()).toContain('网关与超时')
  })
})

/* ------------------------------------------------------------------ *
 * 3. 出口代理节点：抽屉校验 / 创建 / 筛选 / 空态 / 行操作
 * ------------------------------------------------------------------ */

describe('SettingsPage · 出口代理节点', () => {
  it('打开添加抽屉：地址不合法先拦、合法后提交并刷新列表', async () => {
    const { wrapper } = await mountPage()
    await click(wrapper.find('#settings-proxies').element, '添加节点')

    expect(bodyText()).toContain('添加出口节点')
    const url = nativeInput('http://user:pass@host:port')
    typeInto(url, 'ftp://host:21')
    await flush()
    await click(drawer(), '添加节点')

    expect(createProxy).not.toHaveBeenCalled()
    expect(bodyText()).toContain('必须以 http://')

    typeInto(url, 'http://user:pass@10.0.0.9:8080')
    await flush()
    const before = listProxies.mock.calls.length
    await click(drawer(), '添加节点')

    expect(createProxy).toHaveBeenCalledWith({
      label: '',
      url: 'http://user:pass@10.0.0.9:8080',
      max_concurrency: 1,
      pool: '',
    })
    expect(listProxies.mock.calls.length).toBeGreaterThan(before)
    expect(bodyText()).toContain('已添加出口节点')
  })

  it('关键字搜索与分组筛选都作用到表格数据', async () => {
    const { wrapper } = await mountPage()
    expect(wrapper.findComponent(DataTable).props('data')).toHaveLength(2)

    const search = wrapper
      .findAll('input')
      .find((item) => (item.attributes('placeholder') || '') === '搜索名称 / 地址')
    expect(search, '搜索代理输入框').toBeTruthy()
    await search!.setValue('上海')
    await flush()
    expect(wrapper.findComponent(DataTable).props('data')).toHaveLength(1)

    await search!.setValue('')
    await flush()
    const poolSelect = wrapper.findAllComponents(NSelect)[0]
    poolSelect.vm.$emit('update:value', '住宅')
    await flush()
    expect(wrapper.findComponent(DataTable).props('data')).toHaveLength(1)
  })

  it('无节点时显示空状态说明并给出添加入口', async () => {
    listProxies.mockResolvedValue(proxyPayload([]))
    const { wrapper } = await mountPage()

    const proxies = wrapper.find('#settings-proxies')
    expect(proxies.text()).toContain('还没有配置出口节点，任务会从本机 IP 直连')
    expect(proxies.text()).toContain('共 0 个节点')
  })

  it('手机卡片模式下停用 / 解除静默 / 删除（含确认框）都真实落到接口', async () => {
    const originalMatchMedia = window.matchMedia
    window.matchMedia = ((query: string) =>
      ({
        matches: query === '(max-width: 640px)',
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList) as typeof window.matchMedia
    try {
      const { wrapper } = await mountPage()
      // ≤640px 走 DataTable 的卡片模式，行操作才落到真实 DOM（桌面表格在 jsdom 里是虚拟滚动，不渲染行）
      expect(wrapper.find('#settings-proxies').exists()).toBe(true)
      const cards = (): HTMLElement[] =>
        Array.from(document.querySelectorAll<HTMLElement>('.mh-cards__item'))
      expect(cards()).toHaveLength(2)
      const card = (label: string): HTMLElement => {
        const found = cards().find((item) => (item.textContent || '').includes(label))
        expect(found, '卡片 ' + label).toBeTruthy()
        return found as HTMLElement
      }

      await click(card('机房A-01'), '停用')
      expect(patchProxy).toHaveBeenCalledWith(1, { enabled: 0 })
      expect(bodyText()).toContain('已停用节点')

      await click(card('住宅-上海'), '解除静默')
      expect(unblockProxy).toHaveBeenCalledWith(2)
      expect(bodyText()).toContain('已解除静默')

      await click(card('住宅-上海'), '删除')
      await clickDialog('删除')
      expect(deleteProxy).toHaveBeenCalledWith(2)
      expect(bodyText()).toContain('已删除节点')
    } finally {
      window.matchMedia = originalMatchMedia
    }
  })
})

/* ------------------------------------------------------------------ *
 * 4. 邀请绑定
 * ------------------------------------------------------------------ */

describe('SettingsPage · WorkBuddy 邀请绑定', () => {
  it('只有去掉首尾空白后与已存邀请码不同才可保存', async () => {
    const { wrapper } = await mountPage()
    const invite = wrapper.find('#settings-invitation')
    const input = invite
      .findAll('input')
      .find((item) => (item.attributes('placeholder') || '').includes('邀请码'))
    expect(input, '邀请码输入框').toBeTruthy()

    // 邀请码也走底部保存条：卡片里不再有单独的「保存」
    const hasSaveBar = (): boolean => Boolean(wrapper.element.querySelector('.mh-savebar'))
    expect(buttonByText(invite.element, '保存')).toBeNull()
    expect(hasSaveBar()).toBe(false)

    await input!.setValue(' ABC123 ')
    await flush()
    expect(hasSaveBar()).toBe(false)

    await input!.setValue('https://wb.example.com/invite/NEW99')
    await flush()
    expect(hasSaveBar()).toBe(true)
    await click(saveBarOf(wrapper), '保存')

    expect(saveInvitation).toHaveBeenCalledWith('https://wb.example.com/invite/NEW99')
    expect(bodyText()).toContain('邀请绑定已保存')
  })

  it('停用要确认，确认后提交空值并显示未启用', async () => {
    saveInvitation.mockResolvedValue(invitation({ invite_code: '', invite_url: '', enabled: false }))
    const { wrapper } = await mountPage()
    const invite = wrapper.find('#settings-invitation')

    await click(invite.element, '停用')
    expect(bodyText()).toContain('停用邀请绑定？')
    await clickDialog('停用')

    expect(saveInvitation).toHaveBeenCalledWith('')
    expect(wrapper.find('#settings-invitation').text()).toContain('未启用')
    expect(bodyText()).toContain('邀请绑定已停用')
  })

  it('读取失败渲染 ErrorBlock 并可重试', async () => {
    getInvitation.mockRejectedValue(new Error('500 db down'))
    const { wrapper } = await mountPage()
    const invite = wrapper.find('#settings-invitation')
    expect(invite.text()).toContain('读取邀请设置失败：500 db down')

    const before = getInvitation.mock.calls.length
    getInvitation.mockResolvedValue(invitation())
    await click(invite.element, '重试')
    expect(getInvitation.mock.calls.length).toBeGreaterThan(before)
  })
})

/* ------------------------------------------------------------------ *
 * 5. 修改登录密码
 * ------------------------------------------------------------------ */

describe('SettingsPage · 修改登录密码', () => {
  it('前端先拦「少于 6 位」「两次不一致」，通过后才调接口并清空表单', async () => {
    const { wrapper } = await mountPage()
    const card = wrapper.find('#settings-password')
    const passwords = (): DOMWrapper<HTMLInputElement>[] =>
      card.findAll('input[type="password"]') as unknown as DOMWrapper<HTMLInputElement>[]
    expect(passwords()).toHaveLength(3)

    await passwords()[0].setValue('old-pass')
    await passwords()[1].setValue('12345')
    await passwords()[2].setValue('12345')
    await flush()
    await click(card.element, '保存新密码')
    expect(changePassword).not.toHaveBeenCalled()
    expect(bodyText()).toContain('新密码长度不能少于 6 位')

    await passwords()[1].setValue('new-pass')
    await passwords()[2].setValue('new-pas')
    await flush()
    await click(card.element, '保存新密码')
    expect(changePassword).not.toHaveBeenCalled()
    expect(bodyText()).toContain('两次输入的新密码不一致')

    await passwords()[2].setValue('new-pass')
    await flush()
    await click(card.element, '保存新密码')
    expect(changePassword).toHaveBeenCalledWith('old-pass', 'new-pass')
    expect(bodyText()).toContain('密码已修改，其他设备需要重新登录（已吊销 2 个会话）')
    expect(passwords()[0].element.value).toBe('')
  })

  it('旧密码错误时把后端原因显示出来', async () => {
    changePassword.mockRejectedValue(new Error('旧密码不正确'))
    const { wrapper } = await mountPage()
    const card = wrapper.find('#settings-password')
    const passwords = (): DOMWrapper<HTMLInputElement>[] =>
      card.findAll('input[type="password"]') as unknown as DOMWrapper<HTMLInputElement>[]
    await passwords()[0].setValue('wrong-pass')
    await passwords()[1].setValue('new-pass')
    await passwords()[2].setValue('new-pass')
    await flush()
    await click(card.element, '保存新密码')

    expect(bodyText()).toContain('修改密码失败：旧密码不正确')
  })
})

/* ------------------------------------------------------------------ *
 * 6. 逆向产物
 * ------------------------------------------------------------------ */

describe('SettingsPage · 逆向产物', () => {
  it('后端整路由 404（DEV_TOOLS=0，生产环境常态）时整块和目录项都隐藏，不报错、不影响其它区块', async () => {
    // 真实路径：ADMIN_DEV_TOOLS=0 → http 层抛 ApiError(404)
    getAppSourceStatus.mockRejectedValue(new ApiError(404, 'Not Found'))
    const { wrapper } = await mountPage()

    expect(wrapper.find('#settings-appsource').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('加载逆向产物状态失败')
    expect(bodyText()).not.toContain('加载逆向产物状态失败')
    // 目录里也不留「逆向产物」
    const navText = wrapper.find('.settings-nav').text()
    expect(navText).toContain('运行配置')
    expect(navText).not.toContain('逆向产物')
    expect(wrapper.find('#settings-invitation').text()).toContain('已启用')
  })

  it('非 ApiError 的 404 文案（代理层改写）同样按「未开放」隐藏', async () => {
    getAppSourceStatus.mockRejectedValue(new Error('404 Not Found'))
    const { wrapper } = await mountPage()

    expect(wrapper.find('#settings-appsource').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('加载逆向产物状态失败')
  })

  it('状态接口 500 不降级：显示错误 + 重试，重试成功后回到正常渲染', async () => {
    getAppSourceStatus.mockRejectedValue(new ApiError(500, 'runtime settings 读取失败'))
    const { wrapper } = await mountPage()

    const source = wrapper.find('#settings-appsource')
    expect(source.text()).toContain('加载逆向产物状态失败：runtime settings 读取失败')
    // 关键：500 不能被吞成「本部署未开放」
    expect(source.text()).not.toContain('本部署未开放逆向产物')
    expect(buttonByText(source.element, '重试')).toBeTruthy()

    const before = getAppSourceStatus.mock.calls.length
    getAppSourceStatus.mockResolvedValue(sourceStatus())
    await click(source.element, '重试')

    expect(getAppSourceStatus.mock.calls.length).toBeGreaterThan(before)
    const after = wrapper.find('#settings-appsource')
    expect(after.text()).not.toContain('加载逆向产物状态失败')
    expect(after.text()).toContain('D:\\WorkBuddy\\resources\\app.asar')
  })

  it('断网（fetch TypeError）同样走错误态 + 重试，不降级、不炸其它区块', async () => {
    getAppSourceStatus.mockRejectedValue(new TypeError('Failed to fetch'))
    const { wrapper } = await mountPage()

    const source = wrapper.find('#settings-appsource')
    expect(source.text()).toContain('加载逆向产物状态失败：Failed to fetch')
    expect(source.text()).not.toContain('本部署未开放逆向产物')
    expect(buttonByText(source.element, '重试')).toBeTruthy()
    expect(wrapper.find('#settings-runtime').text()).toContain('网关与超时')
    expect(wrapper.find('#settings-proxies').text()).toContain('共 2 个节点')
  })

  it('状态接口回报 dev_tools=false 时同样隐藏（防御性口径）', async () => {
    getAppSourceStatus.mockResolvedValue(sourceStatus({ dev_tools: false }))
    const { wrapper } = await mountPage()

    expect(wrapper.find('#settings-appsource').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('D:\\WorkBuddy\\resources\\app.asar')
    expect(wrapper.text()).not.toContain('加载逆向产物状态失败')
  })

  it('路径校验保存 / 输出目录校验 / 检索命中与产物未产出', async () => {
    const { wrapper } = await mountPage()
    const source = wrapper.find('#settings-appsource')

    const install = nativeInput('例如 D:\\WorkBuddy')
    typeInto(install, 'E:\\WorkBuddy')
    await flush()
    await click(source.element, '校验并保存')
    expect(locateAppSource).toHaveBeenCalledWith({
      install_dir: 'E:\\WorkBuddy',
      source_dir: 'C:\\Temp\\wb-source',
    })
    expect(bodyText()).toContain('路径已校验并保存')

    const output = nativeInput('留空 = 用户缓存目录')
    typeInto(output, '')
    await flush()
    locateAppSource.mockClear()
    await click(source.element, '设为输出目录')
    expect(locateAppSource).not.toHaveBeenCalled()
    expect(bodyText()).toContain('请填写逆向产物输出目录')

    typeInto(output, 'D:\\out')
    await flush()
    await click(source.element, '设为输出目录')
    expect(locateAppSource).toHaveBeenCalledWith({ source_dir: 'D:\\out' })
    expect(bodyText()).toContain('已设为输出目录')

    await click(source.element, '检索')
    expect(searchAppSource).not.toHaveBeenCalled()
    expect(bodyText()).toContain('请填写检索关键字')

    searchAppSource.mockResolvedValue({
      ok: true,
      files: [{ file: 'app/main.js', line: 42, text: 'create_canvas(ctx)' }],
    })
    const query = nativeInput('例如 create_canvas / turingSdk')
    typeInto(query, 'create_canvas')
    await flush()
    await click(source.element, '检索')

    expect(searchAppSource).toHaveBeenCalledWith('create_canvas', 50)
    expect(source.text()).toContain('app/main.js:42')
    expect(source.text()).toContain('create_canvas(ctx)')

    searchAppSource.mockResolvedValue({ ok: false, files: [], reason: '逆向产物尚未产出' })
    await click(source.element, '检索')
    expect(wrapper.find('#settings-appsource').text()).toContain('逆向产物尚未产出')
  })

  it('重新扫描 / 预检 / 一键拆包 / 删除产物（含确认）都真实调用接口', async () => {
    const { wrapper } = await mountPage()
    const source = wrapper.find('#settings-appsource')

    await click(source.element, '重新扫描')
    expect(detectAppSource).toHaveBeenCalledTimes(1)
    expect(bodyText()).toContain('已重新扫描安装目录')
    expect(nativeInput('例如 D:\\WorkBuddy').value).toBe('E:\\WorkBuddy')

    await click(source.element, '预检')
    expect(planUnpack).toHaveBeenCalledTimes(1)
    expect(bodyText()).toContain('预检完成')

    await click(source.element, '一键拆包')
    expect(startUnpack).toHaveBeenCalledTimes(1)
    expect(bodyText()).toContain('拆包任务已启动')
    expect(source.text()).toContain('准备中')

    await click(source.element, '删除产物')
    expect(bodyText()).toContain('删除逆向产物？')
    await clickDialog('删除')
    expect(removeUnpackedSource).toHaveBeenCalledTimes(1)
    expect(bodyText()).toContain('已删除逆向产物')
  })

  it('逆向产物接口失败走错误 Toast，不炸页面', async () => {
    locateAppSource.mockRejectedValue(new Error('目录不存在'))
    const { wrapper } = await mountPage()
    const source = wrapper.find('#settings-appsource')
    const install = nativeInput('例如 D:\\WorkBuddy')
    typeInto(install, 'Z:/missing')
    await flush()
    await click(source.element, '校验并保存')

    expect(bodyText()).toContain('校验安装目录失败：目录不存在')
    expect(wrapper.find('#settings-appsource').text()).toContain('D:\\WorkBuddy\\resources\\app.asar')
  })
})
