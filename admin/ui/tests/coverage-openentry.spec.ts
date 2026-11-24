/** 覆盖率补充：OpenEntryPage 四个区块的真实挂载交互。只新增本文件，不改 src、不改其它测试。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  NSelect,
  NSwitch,
} from 'naive-ui'
import OpenEntryPage from '@/pages/OpenEntryPage.vue'
import FormDrawer from '@/components/overlay/FormDrawer.vue'
import * as openentryApi from '@/api/openentry'
import type {
  AdminTicketsPayload,
  GateStatus,
  OpenConfigPayload,
  OpenPlatform,
  PageCopy,
} from '@/api/openentry'

/** 同 coverage-settings：真实 NMessageProvider，时长压 0（文案仍进 DOM，不残留悬挂计时器）。 */
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

vi.mock('@/api/openentry', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/openentry')>()
  return {
    ...actual,
    getGateStatus: vi.fn(),
    toggleGate: vi.fn(),
    issueTickets: vi.fn(),
    getOpenConfig: vi.fn(),
    saveOpenConfig: vi.fn(),
    getAdminTickets: vi.fn(),
  }
})

const getGateStatus = vi.mocked(openentryApi.getGateStatus)
const toggleGate = vi.mocked(openentryApi.toggleGate)
const issueTickets = vi.mocked(openentryApi.issueTickets)
const getOpenConfig = vi.mocked(openentryApi.getOpenConfig)
const saveOpenConfig = vi.mocked(openentryApi.saveOpenConfig)
const getAdminTickets = vi.mocked(openentryApi.getAdminTickets)

/* ------------------------------------------------------------------ *
 * 后端真实响应形状的造数
 *   GET  /api/join/gate/status   → admin/routers/join_gate.py:gate_status
 *   GET  /api/open/admin/config  → admin/public_auth.py:open_admin_config
 *   GET  /api/open/admin/tickets → public_auth.py:open_admin_tickets
 * ------------------------------------------------------------------ */

function gate(overrides: Partial<GateStatus> = {}): GateStatus {
  return { open: true, epoch: 7, active_tickets: 3, active_invites: 1, ticket_ttl: 86400, access_ttl: 3600, ...overrides }
}

function platform(overrides: Partial<OpenPlatform> = {}): OpenPlatform {
  return { id: 'workbuddy', label: 'WorkBuddy', modes: ['sms'], order: 10, enabled: true, ...overrides }
}

function copy(overrides: Partial<PageCopy> = {}): PageCopy {
  return { title: 'MiraiHub 注册', sub: '', intro: [], foot: '', ...overrides }
}

function openConfig(overrides: Partial<OpenConfigPayload> = {}): OpenConfigPayload {
  return {
    ok: true,
    platforms: [platform(), platform({ id: 'loomy', label: 'Loomy', modes: ['email'], order: 20, enabled: false })],
    ticket_ttl: 86400,
    ttl_range: [600, 604800],
    copy: copy(),
    ...overrides,
  }
}

function adminTickets(overrides: Partial<AdminTicketsPayload> = {}): AdminTicketsPayload {
  return {
    ok: true,
    open: true,
    epoch: 7,
    count: 2,
    tickets: [
      { code: 'Ab3dE9xQ2k', permanent: false, expires_in: 82800 },
      { code: 'Zz9Permanent', permanent: true, expires_in: 0 },
    ],
    ...overrides,
  }
}

/* ------------------------------------------------------------------ *
 * 挂载脚手架
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
      { path: '/openentry', name: 'openentry', component: OpenEntryPage, meta: { title: '公开注册入口' } },
    ],
  })
  await router.push('/openentry')
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

async function clickDialog(label: string): Promise<void> {
  await settle()
  const dialog = document.querySelector<HTMLElement>('.n-dialog')
  expect(dialog, '确认框未弹出').toBeTruthy()
  await click(dialog as HTMLElement, label)
  await settle()
}

function drawer(): HTMLElement {
  const el = document.querySelector<HTMLElement>('.n-drawer')
  expect(el, '抽屉未打开').toBeTruthy()
  return el as HTMLElement
}

function nativeInput(placeholder: string): HTMLInputElement {
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

function bodyText(): string {
  return document.body.textContent || ''
}

/** 底部保存条（SettingsLayout）：保存只有这一种方式，没有改动时不渲染。 */
function saveBarEl(wrapper: VueWrapper): HTMLElement | null {
  return wrapper.element.querySelector('.mh-savebar') as HTMLElement | null
}

function switchByLabel(wrapper: VueWrapper, label: string): VueWrapper<any> | undefined {
  return wrapper
    .findAllComponents(NSwitch)
    .find((item) => (item.attributes('aria-label') || '') === label) as VueWrapper<any> | undefined
}

beforeEach(() => {
  vi.clearAllMocks()
  getGateStatus.mockResolvedValue(gate())
  getOpenConfig.mockResolvedValue(openConfig())
  getAdminTickets.mockResolvedValue(adminTickets())
  toggleGate.mockResolvedValue({ ok: true, open: false, epoch: 8, revoked_tickets: 2, revoked_invites: 1 })
  issueTickets.mockResolvedValue({
    ok: true,
    expires_in: 86400,
    urls: ['http://localhost:3000/j#k=NEW1', 'http://localhost:3000/j#k=NEW2'],
    warning: '',
  })
  saveOpenConfig.mockResolvedValue(openConfig())
})

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
})

/* ------------------------------------------------------------------ *
 * 1. 首屏：四区块、状态 Badge 与元信息、链接列表、平台多选、文案与预览
 * ------------------------------------------------------------------ */

describe('OpenEntryPage · 首屏四个区块', () => {
  it('入口状态 / 链接 / 平台 / 文案都按真实响应渲染', async () => {
    const { wrapper } = await mountPage()

    expect(wrapper.find('h1').text()).toContain('公开注册入口')
    for (const id of ['openentry-gate', 'openentry-links', 'openentry-platforms', 'openentry-copy']) {
      expect(wrapper.find('#' + id).exists(), '区块 ' + id).toBe(true)
    }

    const gateCard = wrapper.find('#openentry-gate')
    // state 是 computed：卡片 Badge 必须跟着 gate 走（旧版在 setup 时求值一次，永远「已关闭」）。
    // 页头不再重复显示状态徽章，也没有与开关重复的「关闭入口使全部失效」按钮（第 2 轮审查 P1-6 / P2）。
    expect(wrapper.find('.ph-actions').text()).not.toContain('已开放')
    expect(gateCard.text()).toContain('已开放')
    expect(switchByLabel(wrapper, '自助注册入口总开关')!.props('value')).toBe(true)
    expect(buttonByText(gateCard.element, '关闭入口使全部失效')).toBeNull()
    expect(gateCard.text()).toContain('第 7 轮 · 有效链接 3 条 · 有效邀请码 1 个')
    expect(gateCard.text()).toContain('链接有效 1 天')

    const links = wrapper.find('#openentry-links')
    expect(links.text()).toContain('入口已开启')
    expect(links.text()).toContain('当前有效 2 条 · 第 7 轮')
    expect(links.text()).toContain('http://localhost:3000/j#k=Ab3dE9xQ2k')
    expect(links.text()).toContain('剩余 23 小时')
    expect(links.text()).toContain('永久有效')

    const platforms = wrapper.find('#openentry-platforms')
    expect(platforms.text()).toContain('WorkBuddy')
    expect(platforms.text()).toContain('Loomy')
    expect(platforms.text()).toContain('已开放 1 / 2 · 切换立即生效')

    const copyCard = wrapper.find('#openentry-copy')
    const inputs = copyCard.findAll('input')
    expect((inputs[0].element as HTMLInputElement).value).toBe('MiraiHub 注册')
    expect(copyCard.text()).toContain('预览只示意排版，实际样式以公开注册页为准')
    // 开发者用语不再出现在界面上
    expect(wrapper.text()).not.toContain('/api/open')
    expect(wrapper.text()).not.toContain('jti')
  })

  it('刷新按钮重新并发取三份数据', async () => {
    const { wrapper } = await mountPage()
    const before = {
      gate: getGateStatus.mock.calls.length,
      config: getOpenConfig.mock.calls.length,
      tickets: getAdminTickets.mock.calls.length,
    }
    await click(wrapper.element, '刷新')

    expect(getGateStatus.mock.calls.length).toBeGreaterThan(before.gate)
    expect(getOpenConfig.mock.calls.length).toBeGreaterThan(before.config)
    expect(getAdminTickets.mock.calls.length).toBeGreaterThan(before.tickets)
  })
})

/* ------------------------------------------------------------------ *
 * 2. 总开关（代次语义）
 * ------------------------------------------------------------------ */

describe('OpenEntryPage · 入口总开关', () => {
  it('关闭入口：确认框写明旧链接与邀请码失效，成功后重载状态与链接', async () => {
    const next = gate({ open: false, epoch: 8, active_tickets: 0 })
    getGateStatus.mockResolvedValueOnce(gate()).mockResolvedValue(next)
    getAdminTickets.mockResolvedValueOnce(adminTickets()).mockResolvedValue(
      adminTickets({ open: false, epoch: 8, count: 2 }),
    )
    const { wrapper } = await mountPage()

    switchByLabel(wrapper, '自助注册入口总开关')!.vm.$emit('update:value', false)
    await settle()
    expect(bodyText()).toContain('关闭自助注册入口？')
    expect(bodyText()).toContain('已签发的全部访问链接与邀请码同时失效')
    await clickDialog('关闭入口')

    expect(toggleGate).toHaveBeenCalledWith(false)
    expect(getGateStatus.mock.calls.length).toBeGreaterThan(1)
    expect(getAdminTickets.mock.calls.length).toBeGreaterThan(1)
    expect(bodyText()).toContain('入口已关闭，旧链接全部失效')
    expect(wrapper.find('#openentry-gate').text()).toContain('已关闭')
    expect(wrapper.find('#openentry-gate').text()).toContain('第 8 轮')
  })

  it('取消确认框不推进代次', async () => {
    const { wrapper } = await mountPage()
    switchByLabel(wrapper, '自助注册入口总开关')!.vm.$emit('update:value', false)
    await settle()
    await clickDialog('取消')

    expect(toggleGate).not.toHaveBeenCalled()
    expect(switchByLabel(wrapper, '自助注册入口总开关')!.props('value')).toBe(true)
  })

  it('入口关闭时用 Switch 打开：确认后 toggle(true) 并提示', async () => {
    getGateStatus.mockResolvedValue(gate({ open: false, epoch: 3, active_tickets: 0 }))
    getAdminTickets.mockResolvedValue(adminTickets({ open: false, epoch: 3, count: 0 }))
    toggleGate.mockResolvedValue({ ok: true, open: true, epoch: 4, revoked_tickets: 0, revoked_invites: 0 })
    const { wrapper } = await mountPage()
    expect(wrapper.find('#openentry-gate').text()).toContain('已关闭')

    switchByLabel(wrapper, '自助注册入口总开关')!.vm.$emit('update:value', true)
    await settle()
    expect(bodyText()).toContain('开启自助注册入口？')
    await clickDialog('开启入口')

    expect(toggleGate).toHaveBeenCalledWith(true)
    expect(bodyText()).toContain('入口已开启，可生成链接')
  })
})

/* ------------------------------------------------------------------ *
 * 3. 生成访问链接（FormDrawer 1–50）
 * ------------------------------------------------------------------ */

describe('OpenEntryPage · 生成访问链接', () => {
  it('抽屉生成 3 条：提交数量、列出明文链接并重载代次信息', async () => {
    const { wrapper } = await mountPage()
    await click(wrapper.find('#openentry-links').element, '生成访问链接')

    expect(bodyText()).toContain('生成访问链接')
    const count = nativeInput('1')
    typeInto(count, '3')
    await flush()

    const beforeGate = getGateStatus.mock.calls.length
    const beforeTickets = getAdminTickets.mock.calls.length
    await click(drawer(), '生成链接')

    expect(issueTickets).toHaveBeenCalledWith(3)
    expect(bodyText()).toContain('http://localhost:3000/j#k=NEW1')
    expect(bodyText()).toContain('已生成 2 条链接')
    expect(getGateStatus.mock.calls.length).toBeGreaterThan(beforeGate)
    expect(getAdminTickets.mock.calls.length).toBeGreaterThan(beforeTickets)
  })

  it('数量超过 50 时按 50 签发', async () => {
    const { wrapper } = await mountPage()
    await click(wrapper.find('#openentry-links').element, '生成访问链接')
    const count = nativeInput('1')
    typeInto(count, '999')
    await flush()
    await click(drawer(), '生成链接')

    expect(issueTickets).toHaveBeenCalledWith(50)
    expect(count.value).toBe('50')
  })

  it('入口未开启时提交被拦下并给出可执行提示', async () => {
    getGateStatus.mockResolvedValue(gate({ open: false, active_tickets: 0 }))
    getAdminTickets.mockResolvedValue(adminTickets({ open: false, count: 0 }))
    const { wrapper } = await mountPage()

    const issueButton = buttonByText(wrapper.find('#openentry-links').element, '生成访问链接')
    expect(issueButton?.disabled).toBe(true)

    const formDrawer = wrapper.findComponent(FormDrawer)
    formDrawer.vm.$emit('update:show', true)
    await settle()
    formDrawer.vm.$emit('submit')
    await settle()

    expect(issueTickets).not.toHaveBeenCalled()
    expect(bodyText()).toContain('入口未开启：开启后才能签发访问链接')
  })
})

/* ------------------------------------------------------------------ *
 * 4. 链接时效（限制参数）
 * ------------------------------------------------------------------ */

describe('OpenEntryPage · 链接时效', () => {
  it('改档位后出现未保存标记，保存后提交 ticket_ttl 并重载配置', async () => {
    const { wrapper } = await mountPage()
    const before = getOpenConfig.mock.calls.length
    expect(saveBarEl(wrapper), '没改动时不应有保存条').toBeNull()

    const ttl = wrapper.findAllComponents(NSelect)[0]
    ttl.vm.$emit('update:value', '3600')
    await flush()
    // 计数型 dirty → 保存条文案是「有 N 项未保存」（旧断言停在布尔型的「有未保存的修改」）。
    expect(saveBarEl(wrapper)?.textContent).toContain('有 1 项未保存')
    await click(saveBarEl(wrapper)!, '保存')

    expect(saveOpenConfig).toHaveBeenCalledWith({ ticket_ttl: 3600 })
    expect(getOpenConfig.mock.calls.length).toBeGreaterThan(before)
    expect(bodyText()).toContain('链接时效已保存：新签发的链接按新时效生效')
    expect(saveBarEl(wrapper), '保存后保存条应消失').toBeNull()
  })

  it('时效未变时不出现保存条（不会白发请求）', async () => {
    const { wrapper } = await mountPage()
    expect(saveBarEl(wrapper)).toBeNull()
    expect(buttonByText(wrapper.find('#openentry-gate').element, '保存时效')).toBeNull()
  })

  it('时效和文案同时改：保存条一次保存两块，先保存的那块重读配置不会冲掉另一块的草稿', async () => {
    const { wrapper } = await mountPage()
    wrapper.findAllComponents(NSelect)[0].vm.$emit('update:value', '3600')
    const card = wrapper.find('#openentry-copy')
    await card.findAll('input')[0].setValue('新的标题')
    await flush()
    expect(saveBarEl(wrapper)?.textContent).toContain('有 2 项未保存')

    await click(saveBarEl(wrapper)!, '保存')
    await settle()
    expect(saveOpenConfig).toHaveBeenCalledWith({ ticket_ttl: 3600 })
    const copyCall = saveOpenConfig.mock.calls.find((call) => 'page_copy' in (call[0] as object))
    expect(copyCall, '文案也要保存').toBeTruthy()
    expect((copyCall![0] as { page_copy: { title: string } }).page_copy.title).toBe('新的标题')
  })

  it('放弃：草稿回到上次读到的配置，不发请求', async () => {
    const { wrapper } = await mountPage()
    wrapper.findAllComponents(NSelect)[0].vm.$emit('update:value', '3600')
    await flush()
    const before = getOpenConfig.mock.calls.length
    await click(saveBarEl(wrapper)!, '放弃')
    expect(saveBarEl(wrapper)).toBeNull()
    expect(saveOpenConfig).not.toHaveBeenCalled()
    expect(getOpenConfig.mock.calls.length).toBe(before)
  })
})

/* ------------------------------------------------------------------ *
 * 5. 公开页平台多选
 * ------------------------------------------------------------------ */

describe('OpenEntryPage · 公开页平台', () => {
  it('切换平台即保存并刷新清单', async () => {
    const { wrapper } = await mountPage()
    const before = getOpenConfig.mock.calls.length

    switchByLabel(wrapper, 'WorkBuddy 是否在公开页可见')!.vm.$emit('update:value', false)
    await settle()
    expect(saveOpenConfig).toHaveBeenCalledWith({ platforms: { workbuddy: false } })
    expect(bodyText()).toContain('WorkBuddy 已从公开页隐藏')

    switchByLabel(wrapper, 'Loomy 是否在公开页可见')!.vm.$emit('update:value', true)
    await settle()
    expect(saveOpenConfig).toHaveBeenCalledWith({ platforms: { loomy: true } })
    expect(bodyText()).toContain('Loomy 已在公开页开放')
    expect(getOpenConfig.mock.calls.length).toBeGreaterThan(before)
  })

  it('没有平台适配器时显示空状态', async () => {
    getOpenConfig.mockResolvedValue(openConfig({ platforms: [] }))
    const { wrapper } = await mountPage()

    const platforms = wrapper.find('#openentry-platforms')
    expect(platforms.text()).toContain('该部署没有可公开注册的平台适配器')
    expect(platforms.text()).toContain('已开放 0 / 0')
  })
})

/* ------------------------------------------------------------------ *
 * 6. 公开页文案 + 实时预览
 * ------------------------------------------------------------------ */

describe('OpenEntryPage · 公开页文案', () => {
  it('超长标题先被前端拦下（错误文案按后端上限），不调接口', async () => {
    const { wrapper } = await mountPage()
    const card = wrapper.find('#openentry-copy')
    const inputs = card.findAll('input')
    await inputs[0].setValue('标'.repeat(41))
    await flush()
    expect(saveBarEl(wrapper)?.textContent).toContain('有 1 项未保存')

    await click(saveBarEl(wrapper)!, '保存')
    expect(saveOpenConfig).not.toHaveBeenCalled()
    expect(bodyText()).toContain('标题不能超过 40 字')
  })

  it('预览标题跟着标题输入实时变化（标题/副标题/说明同一份草稿）', async () => {
    const { wrapper } = await mountPage()
    const card = wrapper.find('#openentry-copy')
    // 预览标题是卡片里唯一的 h4（SettingsCard 标题是 h2）
    expect(card.find('h4').text()).toBe('MiraiHub 注册')

    const inputs = card.findAll('input')
    await inputs[0].setValue('内测入口')
    await flush()
    expect(card.find('h4').text()).toBe('内测入口')

    // 改副标题不动标题，但副标题自己要出现在预览里（与实时草稿同源）
    await inputs[1].setValue('仅限内部同事')
    await flush()
    expect(card.find('h4').text()).toBe('内测入口')
    expect(card.text()).toContain('仅限内部同事')

    // 清空标题回落到默认标题，不留上一次的旧值
    await inputs[0].setValue('   ')
    await flush()
    expect(card.find('h4').text()).toBe('MiraiHub 注册')
    expect(wrapper.find('#openentry-copy').text()).not.toContain('内测入口')
  })

  it('改说明即时反映到预览，保存时提交归一后的 page_copy', async () => {
    const { wrapper } = await mountPage()
    const card = wrapper.find('#openentry-copy')
    const inputs = card.findAll('input')
    const intro = card.find('textarea')

    await inputs[0].setValue('  内测入口  ')
    await intro.setValue('第一行：填邀请码即可注册\n\n第二行：仅限内部同事')
    await flush()

    // 预览取表单值（说明/副标题/页脚随输入实时变化）
    expect(card.text()).toContain('第一行：填邀请码即可注册')
    expect(card.text()).toContain('第二行：仅限内部同事')

    await click(saveBarEl(wrapper)!, '保存')
    expect(saveOpenConfig).toHaveBeenCalledWith({
      page_copy: {
        title: '内测入口',
        sub: '',
        intro: ['第一行：填邀请码即可注册', '第二行：仅限内部同事'],
        foot: '',
      },
    })
    expect(bodyText()).toContain('公开页文案已保存')
    expect(saveBarEl(wrapper), '保存后保存条应消失').toBeNull()
  })

  it('恢复默认要确认，确认后提交空文案', async () => {
    const { wrapper } = await mountPage()
    const card = wrapper.find('#openentry-copy')

    await click(card.element, '恢复默认')
    expect(bodyText()).toContain('恢复默认文案？')
    await clickDialog('恢复默认')

    expect(saveOpenConfig).toHaveBeenCalledWith({
      page_copy: { title: '', sub: '', intro: [], foot: '' },
    })
    expect(bodyText()).toContain('已恢复默认文案')
  })
})

/* ------------------------------------------------------------------ *
 * 7. 空态与错误态
 * ------------------------------------------------------------------ */

describe('OpenEntryPage · 空态与错误态', () => {
  it('没有链接时给出签发指引', async () => {
    getAdminTickets.mockResolvedValue(adminTickets({ count: 0, tickets: [] }))
    const { wrapper } = await mountPage()

    const links = wrapper.find('#openentry-links')
    expect(links.text()).toContain('当前有效 0 条')
    expect(links.text()).toContain('还没有链接，点右上角「生成访问链接」签发')
    // 生成按钮只在卡头放一个
    expect(Array.from(links.element.querySelectorAll('button')).filter((b) => (b.textContent || '').trim() === '生成访问链接').length).toBe(1)
  })

  it('入口关闭且没有链接：空状态提示先开启入口', async () => {
    getGateStatus.mockResolvedValue(gate({ open: false }))
    getAdminTickets.mockResolvedValue(adminTickets({ open: false, count: 0, tickets: [] }))
    const { wrapper } = await mountPage()
    expect(wrapper.find('#openentry-links').text()).toContain('入口关闭中：先在上方打开「自助注册入口」')
  })

  it('三份数据都失败时各自渲染 ErrorBlock，重试只重取自己那一块', async () => {
    getGateStatus.mockRejectedValue(new Error('boom'))
    getOpenConfig.mockRejectedValue(new Error('boom'))
    getAdminTickets.mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountPage()

    expect(wrapper.find('#openentry-gate').text()).toContain('读取入口状态失败：boom')
    expect(wrapper.find('#openentry-links').text()).toContain('读取访问链接失败：boom')
    expect(wrapper.find('#openentry-platforms').text()).toContain('读取公开入口配置失败：boom')
    expect(wrapper.find('#openentry-copy').text()).toContain('读取公开入口配置失败：boom')

    const beforeGate = getGateStatus.mock.calls.length
    const beforeConfig = getOpenConfig.mock.calls.length
    const beforeTickets = getAdminTickets.mock.calls.length

    getGateStatus.mockResolvedValue(gate())
    await click(wrapper.find('#openentry-gate').element, '重试')
    expect(getGateStatus.mock.calls.length).toBeGreaterThan(beforeGate)
    expect(getOpenConfig.mock.calls.length).toBe(beforeConfig)

    getAdminTickets.mockResolvedValue(adminTickets())
    await click(wrapper.find('#openentry-links').element, '重试')
    expect(getAdminTickets.mock.calls.length).toBeGreaterThan(beforeTickets)

    getOpenConfig.mockResolvedValue(openConfig())
    await click(wrapper.find('#openentry-platforms').element, '重试')
    expect(getOpenConfig.mock.calls.length).toBeGreaterThan(beforeConfig)
    expect(wrapper.find('#openentry-platforms').text()).toContain('WorkBuddy')
  })

  it('开关失败时提示原因且状态不变', async () => {
    toggleGate.mockRejectedValue(new Error('409 入口状态被其它管理员改动'))
    const { wrapper } = await mountPage()

    switchByLabel(wrapper, '自助注册入口总开关')!.vm.$emit('update:value', false)
    await settle()
    await clickDialog('关闭入口')

    expect(bodyText()).toContain('关闭入口失败：409 入口状态被其它管理员改动')
    expect(switchByLabel(wrapper, '自助注册入口总开关')!.props('value')).toBe(true)
  })
})
