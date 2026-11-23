/**
 * 覆盖率补充：AlertsPage（S08 /alerts）真实挂载交互。
 *
 * 只新增本文件：不改 src/**、package.json、vite 配置与其他测试，不新增依赖。
 * 网络层 mock 在 @/api/alerts（页面唯一取数入口）；@/api/alerts 的纯函数
 * （alertFormFromPayload / buildAlertConfigBody / validateChannelTarget …）走真实实现。
 * 契约见 docs/ui-v2/contracts/alerts.md（§二 展示 / §三 交互 / §八 三个设计决定）。
 *
 * 覆盖点：首屏骨架与四块卡片 · 规则总开关 · 去重窗口（合法写入 / 越界拦截）·
 *        渠道启用开关（保存即移除）· 事件确认（删除渠道 / 清除授权码 / 放弃修改，含取消分支）·
 *        添加渠道抽屉（渠道类型选择 → 动态字段、邮箱与 QQ 机器人校验、去重）·
 *        发送测试（全部成功 / 部分失败 / 接口失败 + 最近一次测试结果）·
 *        错误态（首次加载失败 + 重试、保存失败、发送失败）· 空态与邮件链路未就绪。
 *
 * 契约 §「无筛选/分页」写明本页没有列表筛选：「筛选」维度用渠道类型选择（不同规则 → 不同字段）
 * 与渠道列表的启用态筛选观察来覆盖。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NInputNumber, NMessageProvider, NSelect } from 'naive-ui'
import AlertsPage from '@/pages/AlertsPage.vue'
import * as alertsApi from '@/api/alerts'
import { ApiError } from '@/api/http'
import type { AlertConfigPayload, AlertTestResult } from '@/api/alerts'

vi.mock('@/api/alerts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/alerts')>()
  return { ...actual, getAlertConfig: vi.fn(), putAlertConfig: vi.fn(), sendAlertTest: vi.fn() }
})

const getAlertConfig = vi.mocked(alertsApi.getAlertConfig)
const putAlertConfig = vi.mocked(alertsApi.putAlertConfig)
const sendAlertTest = vi.mocked(alertsApi.sendAlertTest)

/* -------------------------------------------------------------------------- */
/* 测试数据（形状按 alerts_router.get_config / send_test 的真实响应）            */
/* -------------------------------------------------------------------------- */

const FEISHU = 'https://open.feishu.cn/open-apis/bot/v2/hook/abc123'

function payload(overrides: Partial<AlertConfigPayload> = {}): AlertConfigPayload {
  const base: AlertConfigPayload = {
    config: {
      enabled: true,
      cooldown: 600,
      channels: [{ type: 'feishu', target: FEISHU }],
      smtp_host: 'smtp.qq.com',
      smtp_port: 465,
      smtp_user: 'me@qq.com',
      smtp_password: alertsApi.SMTP_MASK,
      has_password: true,
      smtp_from: 'me@qq.com',
      smtp_starttls: true,
      smtp: alertsApi.SMTP_MASK,
      has_smtp: true,
      mail_to: 'me@qq.com',
    },
    channels: [
      { type: 'feishu', label: '飞书群机器人', target: FEISHU, enabled: true },
      { type: 'email', label: '邮件（SMTP）', target: 'me@qq.com', enabled: true },
    ],
    labels: { email: '邮件（SMTP）', feishu: '飞书群机器人', qqbot: 'QQ 机器人' },
    enabled: true,
    smtp: {
      host: 'smtp.qq.com',
      port: 465,
      user: 'me@qq.com',
      from: 'me@qq.com',
      mail_to: 'me@qq.com',
      ssl: true,
      compact: true,
      has_password: true,
      ready: true,
      missing: [],
    },
  }
  return { ...base, ...overrides }
}

function testResult(overrides: Partial<AlertTestResult> = {}): AlertTestResult {
  return {
    ok: true,
    title: '[风控提醒] 测试 出现封控信号',
    text: '这是一条来自 MiraiHub 的测试通知。',
    level: 'info',
    results: [
      { type: 'feishu', target: FEISHU, ok: true, error: '' },
      { type: 'email', target: 'me@qq.com', ok: true, error: '' },
    ],
    ...overrides,
  }
}

/* -------------------------------------------------------------------------- */
/* mount / DOM 工具                                                            */
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

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
  await nextTick()
}

async function waitFor(predicate: () => boolean, label: string, rounds = 40): Promise<void> {
  for (let index = 0; index < rounds; index += 1) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 20))
    await nextTick()
    await flushPromises()
  }
  throw new Error('waitFor timeout: ' + label + ' | body=' + bodyText().slice(0, 240))
}

async function mountPage(): Promise<Mounted> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/alerts', name: 'alerts', component: AlertsPage },
      { path: '/sync', name: 'sync', component: { template: '<div>sync</div>' } },
      { path: '/client', name: 'client', component: { template: '<div>client</div>' } },
    ],
  })
  await router.push('/alerts')
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

function pageRoot(wrapper: VueWrapper): HTMLElement {
  return wrapper.element as HTMLElement
}

function buttonsIn(root: ParentNode): HTMLButtonElement[] {
  return Array.from(root.querySelectorAll('button')) as HTMLButtonElement[]
}

function findButton(root: ParentNode, text: string, index = 0): HTMLButtonElement {
  const list = buttonsIn(root).filter((el) => (el.textContent || '').trim() === text)
  const target = list[index]
  if (!target) {
    throw new Error(
      'button not found: ' + text + ' | have=' +
        buttonsIn(root).map((el) => (el.textContent || '').trim()).filter(Boolean).join('|'),
    )
  }
  return target
}

async function clickButton(target: VueWrapper | HTMLElement, text: string, index = 0): Promise<void> {
  const root = target instanceof HTMLElement ? target : pageRoot(target)
  findButton(root, text, index).click()
  await settle()
}

function headerActions(wrapper: VueWrapper): HTMLElement {
  const el = pageRoot(wrapper).querySelector('.ph-actions') as HTMLElement | null
  if (!el) throw new Error('page header actions not found')
  return el
}

function card(id: string): HTMLElement {
  const el = document.getElementById(id)
  if (!el) throw new Error('card not found: ' + id + ' | body=' + bodyText().slice(0, 240))
  return el
}

function channelRows(wrapper: VueWrapper): HTMLElement[] {
  return Array.from(pageRoot(wrapper).querySelectorAll('.channel-row')) as HTMLElement[]
}

function channelRow(wrapper: VueWrapper, label: string): HTMLElement {
  const row = channelRows(wrapper).find((el) => (el.textContent || '').includes(label))
  if (!row) {
    throw new Error(
      'channel row not found: ' + label + ' | have=' +
        channelRows(wrapper).map((el) => (el.textContent || '').slice(0, 40)).join('|'),
    )
  }
  return row
}

function inputByPlaceholder(placeholder: string, index = 0): HTMLInputElement {
  const list = (Array.from(document.querySelectorAll('input')) as HTMLInputElement[]).filter(
    (el) => el.getAttribute('placeholder') === placeholder,
  )
  const target = list[index]
  if (!target) {
    throw new Error(
      'input not found: ' + placeholder + ' | have=' +
        (Array.from(document.querySelectorAll('input')) as HTMLInputElement[])
          .map((el) => el.getAttribute('placeholder'))
          .join('|'),
    )
  }
  return target
}

async function typeInto(el: HTMLInputElement, value: string): Promise<void> {
  el.value = value
  el.dispatchEvent(new Event('input'))
  await settle()
}

function lastDialog(): HTMLElement {
  const list = Array.from(document.querySelectorAll('.n-dialog')) as HTMLElement[]
  if (!list.length) throw new Error('dialog not found | body=' + bodyText().slice(0, 240))
  return list[list.length - 1]
}

function dialogTitle(): string {
  return (lastDialog().querySelector('.n-dialog__title')?.textContent || '').trim()
}

async function answerDialog(okText: string): Promise<void> {
  await waitFor(() => document.querySelectorAll('.n-dialog').length > 0, 'confirm dialog')
  await settle()
  const buttons = Array.from(lastDialog().querySelectorAll('.n-dialog__action button')) as HTMLElement[]
  const target = buttons.find((el) => (el.textContent || '').trim() === okText) || buttons[buttons.length - 1]
  if (!target) throw new Error('dialog action not found: ' + okText)
  target.click()
  // naive 的对话框关闭后仍留在 DOM（只隐藏）；等过渡结束再继续，避免 click-outside 干扰。
  await new Promise((resolve) => setTimeout(resolve, 300))
  await settle()
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (error: unknown) => void } {
  let resolveFn: (value: T) => void = () => undefined
  let rejectFn: (error: unknown) => void = () => undefined
  const promise = new Promise<T>((resolve, reject) => {
    resolveFn = resolve
    rejectFn = reject
  })
  return { promise, resolve: resolveFn, reject: rejectFn }
}

/** 抽屉底部操作栏（FormDrawer 的 .mh-form__actions）。 */
function drawerFooter(): HTMLElement {
  const el = document.querySelector('.mh-form__actions') as HTMLElement | null
  if (!el) throw new Error('drawer footer not found | body=' + bodyText().slice(0, 240))
  return el
}

/* -------------------------------------------------------------------------- */
/* setup / teardown                                                            */
/* -------------------------------------------------------------------------- */

beforeEach(() => {
  vi.clearAllMocks()
  // jsdom 没实现：SettingsLayout 的锚点跳转与 naive 内部滚动都会调用它们。
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { value: () => undefined, configurable: true })
  HTMLElement.prototype.scrollIntoView = () => undefined
  getAlertConfig.mockResolvedValue(payload())
  putAlertConfig.mockResolvedValue({
    ok: true,
    channels: [{ type: 'feishu', label: '飞书群机器人', target: FEISHU, enabled: true }],
  })
  sendAlertTest.mockResolvedValue(testResult())
  localStorage.clear()
})

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
  localStorage.clear()
})

/* -------------------------------------------------------------------------- */
/* 用例                                                                        */
/* -------------------------------------------------------------------------- */

describe('AlertsPage 覆盖率补测 · 首屏与规则开关', () => {
  it('首屏：骨架 → 四块卡片、渠道列表与邮件链路体检', async () => {
    const pending = deferred<AlertConfigPayload>()
    getAlertConfig.mockReturnValue(pending.promise)
    const { wrapper } = await mountPage()

    // 加载中：三张卡片都渲染骨架，保存按钮仍可点（页面允许先改后加载？—— 实为禁用）
    expect(pageRoot(wrapper).querySelectorAll('.skel').length).toBe(3)
    expect(findButton(headerActions(wrapper), '保存配置').disabled).toBe(true)

    pending.resolve(payload())
    await settle()
    await settle()

    expect(pageRoot(wrapper).querySelectorAll('.skel').length).toBe(0)
    expect(wrapper.find('h1').text()).toContain('预警管理')
    // 渠道：飞书 + 由 mail_to 派生出的邮件渠道（标记「默认收件邮箱」）
    expect(channelRows(wrapper).length).toBe(2)
    expect(channelRow(wrapper, '飞书群机器人').textContent).toContain(FEISHU)
    expect(channelRow(wrapper, '邮件（SMTP）').textContent).toContain('默认收件邮箱')
    // 邮件链路就绪
    expect(wrapper.text()).toContain('邮件链路就绪：me@qq.com → me@qq.com（smtp.qq.com:465 SSL）')
    // 规则总开关是打开状态
    const master = pageRoot(wrapper).querySelector('.switch-row .n-switch') as HTMLElement
    expect(master.getAttribute('aria-checked')).toBe('true')
    // 本页「保存配置」不受未保存状态控制（全量 PUT，恒可点）
    expect(findButton(headerActions(wrapper), '保存配置').disabled).toBe(false)
    expect(wrapper.text()).not.toContain('有未保存的修改')
  })

  it('规则总开关：关掉后出现未保存徽章，保存发送全量请求体', async () => {
    const next = payload()
    next.config.enabled = false
    next.enabled = false
    getAlertConfig.mockResolvedValueOnce(payload()).mockResolvedValue(next)
    const { wrapper } = await mountPage()

    const master = pageRoot(wrapper).querySelector('.switch-row .n-switch') as HTMLElement
    master.click()
    await settle()
    expect(master.getAttribute('aria-checked')).toBe('false')
    expect(wrapper.text()).toContain('有未保存的修改')
    expect(wrapper.text()).toContain('未保存')

    await clickButton(headerActions(wrapper), '保存配置')
    await settle()

    expect(putAlertConfig).toHaveBeenCalledTimes(1)
    const body = putAlertConfig.mock.calls[0][0]
    // PUT 是全量覆盖：一个字段都不能省
    expect(body).toEqual({
      enabled: false,
      cooldown: 600,
      channels: [{ type: 'feishu', target: FEISHU }],
      smtp_host: 'smtp.qq.com',
      smtp_port: 465,
      smtp_user: 'me@qq.com',
      smtp_password: '',
      smtp_from: 'me@qq.com',
      smtp_starttls: true,
      smtp: '',
      mail_to: 'me@qq.com',
    })
    expect(bodyText()).toContain('预警配置已保存')
    // 保存后重新加载：开关停在关闭，未保存标记消失
    expect(getAlertConfig.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(master.getAttribute('aria-checked')).toBe('false')
    expect(wrapper.text()).not.toContain('有未保存的修改')
  })

  it('去重窗口：合法值写进请求体，越界值保存前被拦下', async () => {
    const next = payload()
    next.config.cooldown = 1200
    getAlertConfig.mockResolvedValueOnce(payload()).mockResolvedValue(next)
    const { wrapper } = await mountPage()

    const number = wrapper.findAllComponents(NInputNumber)[0]
    number.vm.$emit('update:value', 1200)
    await settle()
    expect(wrapper.text()).toContain('当前 20 分钟')

    await clickButton(headerActions(wrapper), '保存配置')
    await settle()
    expect(putAlertConfig.mock.calls[0][0].cooldown).toBe(1200)

    // 越界（页面 NInputNumber 会夹到 30，先把它改成非法值再保存）
    putAlertConfig.mockClear()
    number.vm.$emit('update:value', 10)
    await settle()
    await clickButton(headerActions(wrapper), '保存配置')
    await settle()
    expect(putAlertConfig).not.toHaveBeenCalled()
    expect(bodyText()).toContain('保存失败：去重窗口需在 30–86400 秒之间')
  })

  it('渠道启用开关：关掉后标「已停用」，保存时从 channels 剔除', async () => {
    const { wrapper } = await mountPage()
    const row = channelRow(wrapper, '飞书群机器人')
    const toggle = row.querySelector('.channel-actions .n-switch') as HTMLElement
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    toggle.click()
    await settle()

    expect(row.textContent).toContain('已停用')
    expect(row.textContent).toContain('保存后该渠道会被移除')
    expect(wrapper.text()).toContain('有未保存的修改')

    await clickButton(headerActions(wrapper), '保存配置')
    await settle()
    // 停用的飞书渠道不写回；派生邮件渠道本来就不写回
    expect(putAlertConfig.mock.calls[0][0].channels).toEqual([])
  })
})

describe('AlertsPage 覆盖率补测 · 事件确认', () => {
  it('删除渠道：确认框问句 + 后果，取消保留、确认后离开列表且未保存', async () => {
    const { wrapper } = await mountPage()
    const deleteBtn = findButton(channelRow(wrapper, '飞书群机器人'), '删除')

    deleteBtn.click()
    await settle()
    expect(dialogTitle()).toBe('删除「飞书群机器人」渠道？')
    expect(lastDialog().textContent).toContain('删除后风控告警不再发送到该渠道；点「保存配置」后才写入后端。')
    await answerDialog('取消')
    expect(channelRows(wrapper).length).toBe(2)
    expect(putAlertConfig).not.toHaveBeenCalled()

    findButton(channelRow(wrapper, '飞书群机器人'), '删除').click()
    await settle()
    await answerDialog('删除')
    expect(channelRows(wrapper).some((el) => (el.textContent || '').includes('飞书群机器人'))).toBe(false)
    // 只改本地：仍需保存
    expect(putAlertConfig).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('有未保存的修改')

    await clickButton(headerActions(wrapper), '保存配置')
    await settle()
    expect(putAlertConfig.mock.calls[0][0].channels).toEqual([])
  })

  it('清除授权码：确认后标「待清除」，保存发 __clear__；撤销可回退', async () => {
    const { wrapper } = await mountPage()
    const clearBtn = findButton(card('alerts-smtp'), '清除授权码')
    expect(clearBtn.disabled).toBe(false)

    clearBtn.click()
    await settle()
    expect(dialogTitle()).toBe('清除已保存的 QQ 授权码？')
    await answerDialog('取消')
    expect(wrapper.text()).not.toContain('待清除')

    findButton(card('alerts-smtp'), '清除授权码').click()
    await settle()
    await answerDialog('清除')
    expect(wrapper.text()).toContain('待清除')
    expect(findButton(card('alerts-smtp'), '撤销')).toBeTruthy()

    await clickButton(headerActions(wrapper), '保存配置')
    await settle()
    expect(putAlertConfig.mock.calls[0][0].smtp).toBe(alertsApi.SMTP_CLEAR)
  })

  it('清除授权码后点「撤销」回到未修改状态', async () => {
    const { wrapper } = await mountPage()
    findButton(card('alerts-smtp'), '清除授权码').click()
    await settle()
    await answerDialog('清除')
    expect(wrapper.text()).toContain('待清除')

    await clickButton(card('alerts-smtp'), '撤销')
    expect(wrapper.text()).not.toContain('待清除')
    expect(wrapper.text()).not.toContain('有未保存的修改')
    expect(putAlertConfig).not.toHaveBeenCalled()
  })

  it('取消修改：确认框「放弃未保存的修改？」；取消保留、确认后重载', async () => {
    const { wrapper } = await mountPage()
    const master = pageRoot(wrapper).querySelector('.switch-row .n-switch') as HTMLElement
    master.click()
    await settle()
    expect(wrapper.text()).toContain('有未保存的修改')

    findButton(headerActions(wrapper), '取消修改').click()
    await settle()
    expect(dialogTitle()).toBe('放弃未保存的修改？')
    await answerDialog('取消')
    expect(master.getAttribute('aria-checked')).toBe('false')

    findButton(headerActions(wrapper), '取消修改').click()
    await settle()
    await answerDialog('放弃修改')
    expect(getAlertConfig.mock.calls.length).toBeGreaterThanOrEqual(2)
    const reloaded = pageRoot(wrapper).querySelector('.switch-row .n-switch') as HTMLElement
    expect(reloaded.getAttribute('aria-checked')).toBe('true')
    expect(wrapper.text()).not.toContain('有未保存的修改')
  })
})

describe('AlertsPage 覆盖率补测 · 添加渠道（类型选择 / 筛选规则）', () => {
  it('渠道类型切换 → 动态字段与占位；邮箱校验、成功添加、重复检测', async () => {
    const { wrapper } = await mountPage()
    await clickButton(card('alerts-channels'), '添加渠道')
    await waitFor(() => Boolean(document.querySelector('.mh-form__actions')), 'add drawer')
    await settle()

    // 默认类型是飞书（ALERT_CHANNEL_TYPES 第一项）
    const feishuInput = inputByPlaceholder('https://open.feishu.cn/open-apis/bot/v2/hook/…')
    expect(feishuInput).toBeTruthy()
    await typeInto(feishuInput, 'https://open.feishu.cn/open-apis/bot/v2/hook/typed')

    // 切到邮件：字段名与占位随类型变化，且切换会清空已填目标（契约 §三）
    const select = wrapper.findAllComponents(NSelect)[0]
    select.vm.$emit('update:value', 'email')
    await settle()
    expect(document.querySelector('.n-drawer-container')?.textContent).toContain('收件邮箱')
    const emailInput = inputByPlaceholder('收件邮箱，例如 me@qq.com（多个用英文逗号分隔）')

    // 非法邮箱：抽屉内联错误，不关闭
    await typeInto(emailInput, 'me@qq')
    findButton(drawerFooter(), '添加渠道').click()
    await settle()
    expect(document.querySelector('.mh-field__error')?.textContent).toContain('收件邮箱格式不对')

    // 合法邮箱：加入本地列表并提示仍需保存
    await typeInto(inputByPlaceholder('收件邮箱，例如 me@qq.com（多个用英文逗号分隔）'), 'ops@example.com')
    findButton(drawerFooter(), '添加渠道').click()
    await settle()
    expect(bodyText()).toContain('已添加渠道，保存配置后生效')
    // 新增的 email 渠道与「默认收件邮箱」同名，按 target 找这一行
    expect(channelRows(wrapper).length).toBe(3)
    expect(channelRows(wrapper).some((el) => (el.textContent || '').includes('ops@example.com'))).toBe(true)
    expect(putAlertConfig).not.toHaveBeenCalled()

    // 重复添加同一 type+target → 提示已存在
    await clickButton(card('alerts-channels'), '添加渠道')
    await waitFor(() => Boolean(document.querySelector('.mh-form__actions')), 'add drawer again')
    await settle()
    wrapper.findAllComponents(NSelect)[0].vm.$emit('update:value', 'email')
    await settle()
    await typeInto(inputByPlaceholder('收件邮箱，例如 me@qq.com（多个用英文逗号分隔）'), 'ops@example.com')
    findButton(drawerFooter(), '添加渠道').click()
    await settle()
    expect(document.querySelector('.mh-field__error')?.textContent).toContain('该渠道已存在')
  })

  it('QQ 机器人：地址必须 http(s) 且带 ?group=群号', async () => {
    const { wrapper } = await mountPage()
    await clickButton(card('alerts-channels'), '添加渠道')
    await waitFor(() => Boolean(document.querySelector('.mh-form__actions')), 'add drawer')
    await settle()

    wrapper.findAllComponents(NSelect)[0].vm.$emit('update:value', 'qqbot')
    await settle()
    expect(document.querySelector('.n-drawer-container')?.textContent).toContain('末尾必须带 ?group=群号')

    await typeInto(inputByPlaceholder('http://127.0.0.1:5700/send_group_msg?group=群号'), 'ftp://127.0.0.1:5700/send')
    findButton(drawerFooter(), '添加渠道').click()
    await settle()
    expect(document.querySelector('.mh-field__error')?.textContent).toContain('Webhook 地址需以 http(s):// 开头')

    await typeInto(inputByPlaceholder('http://127.0.0.1:5700/send_group_msg?group=群号'), 'http://127.0.0.1:5700/send_group_msg')
    findButton(drawerFooter(), '添加渠道').click()
    await settle()
    expect(document.querySelector('.mh-field__error')?.textContent).toContain('QQ 机器人地址需带 ?group=群号')

    await typeInto(
      inputByPlaceholder('http://127.0.0.1:5700/send_group_msg?group=群号'),
      'http://127.0.0.1:5700/send_group_msg?group=123456',
    )
    findButton(drawerFooter(), '添加渠道').click()
    await settle()
    expect(channelRow(wrapper, 'QQ 机器人').textContent).toContain('group=123456')
  })
})

describe('AlertsPage 覆盖率补测 · 发送测试', () => {
  it('全部成功：Toast 与「最近一次测试结果」列表，并用响应刷新链路体检', async () => {
    const { wrapper } = await mountPage()
    await clickButton(headerActions(wrapper), '发送测试')
    await settle()

    expect(sendAlertTest).toHaveBeenCalledTimes(1)
    expect(bodyText()).toContain('测试通知已发送：2 个渠道全部成功')
    const section = wrapper.text()
    expect(section).toContain('最近一次测试结果')
    const rows = Array.from(pageRoot(wrapper).querySelectorAll('.test-row')) as HTMLElement[]
    expect(rows.length).toBe(2)
    expect(rows[0].textContent).toContain('成功')
    expect(rows[0].textContent).toContain('飞书群机器人')
    expect(pageRoot(wrapper).textContent).toContain('测试时间：')
  })

  it('部分失败：warning Toast、失败行展示后端 error，体检提示缺少的项', async () => {
    const { wrapper } = await mountPage()
    sendAlertTest.mockResolvedValue(
      testResult({
        ok: false,
        results: [
          { type: 'feishu', target: FEISHU, ok: true, error: '' },
          { type: 'email', target: 'me@qq.com', ok: false, error: 'SMTPAuthenticationError: 535 authentication failed' },
        ],
        smtp: {
          host: '',
          port: 465,
          user: '',
          from: '',
          mail_to: '',
          ssl: true,
          compact: false,
          has_password: false,
          ready: false,
          missing: ['邮件服务器（或 ADMIN_ALERT_SMTP=qqSMTP:授权码）'],
        },
      }),
    )
    await clickButton(headerActions(wrapper), '发送测试')
    await settle()

    expect(bodyText()).toContain('测试通知部分失败：1/2 个渠道')
    expect(bodyText()).toContain('邮件（SMTP）')
    const failed = (Array.from(pageRoot(wrapper).querySelectorAll('.test-row')) as HTMLElement[]).find((el) =>
      (el.textContent || '').includes('失败'),
    )
    expect(failed?.textContent).toContain('SMTPAuthenticationError: 535 authentication failed')
    // 测试响应里的 smtp 体检覆盖页面状态
    expect(pageRoot(wrapper).querySelector('.health--warn')).toBeTruthy()
    expect(pageRoot(wrapper).textContent).toContain('邮件链路还差：邮件服务器')
  })

  it('渠道行「发送测试」同样调接口；页头「重新加载」重取配置', async () => {
    const { wrapper } = await mountPage()
    findButton(channelRow(wrapper, '飞书群机器人'), '发送测试').click()
    await settle()
    expect(sendAlertTest).toHaveBeenCalledTimes(1)

    const before = getAlertConfig.mock.calls.length
    const reload = pageRoot(wrapper).querySelector('button[aria-label="重新加载配置"]') as HTMLButtonElement
    expect(reload).toBeTruthy()
    reload.click()
    await settle()
    expect(getAlertConfig.mock.calls.length).toBe(before + 1)
  })

  it('接口失败：Toast 说原因，不产生测试结果区块', async () => {
    const { wrapper } = await mountPage()
    sendAlertTest.mockRejectedValue(new Error('网络中断'))
    await clickButton(headerActions(wrapper), '发送测试')
    await settle()

    expect(bodyText()).toContain('发送测试通知失败：网络中断')
    expect(wrapper.text()).not.toContain('最近一次测试结果')
  })
})

describe('AlertsPage 覆盖率补测 · 错误态与空态', () => {
  it('首次加载失败：错误块（可展开后端 detail）+ 重试后渲染', async () => {
    getAlertConfig
      .mockRejectedValueOnce(new ApiError(500, '配置读取失败', 'alerts:config 读库失败'))
      .mockResolvedValue(payload())
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('加载预警配置失败：配置读取失败')
    await clickButton(wrapper, '展开详细信息')
    expect(wrapper.text()).toContain('alerts:config 读库失败')
    // 失败态不渲染卡片正文
    expect(wrapper.text()).not.toContain('启用预警通知')

    await clickButton(wrapper, '重试')
    await settle()
    expect(getAlertConfig.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(wrapper.text()).not.toContain('加载预警配置失败')
    expect(wrapper.text()).toContain('启用预警通知')
    expect(channelRows(wrapper).length).toBe(2)
  })

  it('保存失败：Toast 说原因，修改保留（未保存标记还在）', async () => {
    const { wrapper } = await mountPage()
    putAlertConfig.mockRejectedValue(new Error('数据库只读'))
    const master = pageRoot(wrapper).querySelector('.switch-row .n-switch') as HTMLElement
    master.click()
    await settle()
    await clickButton(headerActions(wrapper), '保存配置')
    await settle()

    expect(bodyText()).toContain('保存预警配置失败：数据库只读')
    expect(wrapper.text()).toContain('有未保存的修改')
    expect(master.getAttribute('aria-checked')).toBe('false')
    expect(findButton(headerActions(wrapper), '保存配置').disabled).toBe(false)
  })

  it('空态：没有渠道时给说明与添加入口，链路未就绪列出缺什么', async () => {
    const empty: AlertConfigPayload = {
      config: { enabled: true, cooldown: 600, channels: [] },
      channels: [],
      labels: {},
      enabled: true,
      smtp: {
        host: '',
        port: 465,
        user: '',
        from: '',
        mail_to: '',
        ssl: true,
        compact: false,
        has_password: false,
        ready: false,
        missing: [
          '邮件服务器（或 ADMIN_ALERT_SMTP=qqSMTP:授权码）',
          '收件邮箱（默认收件人）',
        ],
      },
    }
    getAlertConfig.mockResolvedValue(empty)
    const { wrapper } = await mountPage()

    expect(channelRows(wrapper).length).toBe(0)
    expect(wrapper.text()).toContain('还没有配置通知渠道。风控告警只会记录在面板里。')
    // 卡片头 + 空态各有一个添加入口
    expect(buttonsIn(card('alerts-channels')).filter((el) => (el.textContent || '').trim() === '添加渠道').length).toBe(2)
    expect(wrapper.text()).toContain('邮件链路还差：邮件服务器（或 ADMIN_ALERT_SMTP=qqSMTP:授权码）、收件邮箱（默认收件人）')
  })
})

// Naive message 的 auto-hide 定时器会在 jsdom 销毁后触发；用可自动推进的假时钟并在收尾清空。
beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })
