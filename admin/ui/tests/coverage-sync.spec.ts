/**
 * 覆盖率补充：SyncPage（S07 /sync）真实挂载交互。
 *
 * 只新增本文件：不改 src/**、package.json、vite 配置与其他测试，不新增依赖。
 * 网络层 mock 在 @/api/sync（页面唯一取数入口）与 @/api/platforms（平台 store 的取数入口）；
 * @/api/sync 的纯函数（normalizeRemoteUrl / summarizePushResult / describeSyncConfig …）走真实实现，
 * 断言既证明 DOM 变化，也真的跑到页面分支。契约见 docs/ui-v2/contracts/sync.md。
 *
 * 覆盖点：首屏骨架 → 表单/只读状态行 · 未保存徽章 · 保存（请求体归一 / 成功重载 / 失败 Toast）·
 *        取消 · 同步到线上（确认框文案、取消确认、进行中、成功计数、失败记录、缺 URL 前置校验）·
 *        刷新失败保留旧值 + 重试 · 首次加载错误 + 重试 · 重新生成密钥（确认 / 取消）·
 *        显示 / 隐藏 / 复制密钥 · URL 失焦归一与非 http 提示 · 平台切换重取（有/无未保存修改）·
 *        GET 回传 last_push_* 时优先于本机记录。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import SyncPage from '@/pages/SyncPage.vue'
import { usePlatformStore } from '@/stores/platform'
import * as syncApi from '@/api/sync'
import * as platformApi from '@/api/platforms'
import type { SyncConfigPayload, SyncPushResult } from '@/api/sync'
import type { PlatformSpec } from '@/api/platforms'

vi.mock('@/api/sync', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/sync')>()
  return { ...actual, getSyncConfig: vi.fn(), saveSyncConfig: vi.fn(), pushToOnline: vi.fn() }
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

// 剪贴板在 jsdom 里不可用（navigator.clipboard 与 execCommand 都没有）：
// 页面的成功 / 失败分支用替身驱动（它只消费 copy() 的布尔结果）。
const copyState = vi.hoisted(() => ({ ok: true }))
vi.mock('@/composables/useCopy', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/composables/useCopy')>()
  const { ref } = await import('vue')
  return { ...actual, useCopy: () => ({ copied: ref(false), copy: async () => copyState.ok }) }
})

const getSyncConfig = vi.mocked(syncApi.getSyncConfig)
const saveSyncConfig = vi.mocked(syncApi.saveSyncConfig)
const pushToOnline = vi.mocked(syncApi.pushToOnline)
const listPlatforms = vi.mocked(platformApi.listPlatforms)
const putCurrentPlatform = vi.mocked(platformApi.putCurrentPlatform)

/* -------------------------------------------------------------------------- */
/* 测试数据（形状按 admin/routers/sync.py 的真实响应）                          */
/* -------------------------------------------------------------------------- */

const SECRET = '9f2c1d5e7a8b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c'

function config(overrides: Partial<SyncConfigPayload> = {}): SyncConfigPayload {
  return {
    remote_url: 'https://online.example.com',
    secret: SECRET,
    secret_masked: '********3b4c',
    is_auto_generated: false,
    ...overrides,
  }
}

function pushPayload(overrides: Partial<SyncPushResult> = {}): SyncPushResult {
  return {
    ok: true,
    remote: 'https://online.example.com',
    accounts: 12,
    models: 7,
    client_profile: true,
    profile_fields: ['cli_version', 'desktop_version', 'headers'],
    remote_result: {
      ok: true,
      accounts: { added: 3, updated: 9 },
      models: { added: 1, updated: 6 },
      client_profile: { applied: true, source: 'saved', fields: ['desktop_version', 'user_agent'], errors: [] },
    },
    ...overrides,
  }
}

function spec(id: string, overrides: Partial<PlatformSpec> = {}): PlatformSpec {
  return {
    id,
    label: id,
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
    managed: true,
    actions: [],
    quota_unit: 'credits',
    quota_unit_label: '',
    account_count: 1,
    realm_counts: {},
    ...overrides,
  }
}

/* -------------------------------------------------------------------------- */
/* mount / DOM 工具                                                            */
/* -------------------------------------------------------------------------- */

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider :duration="0"><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>`,
})

interface Mounted {
  wrapper: VueWrapper
  router: Router
  pinia: ReturnType<typeof createPinia>
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
      { path: '/sync', name: 'sync', component: SyncPage },
      { path: '/alerts', name: 'alerts', component: { template: '<div>alerts</div>' } },
      { path: '/client', name: 'client', component: { template: '<div>client</div>' } },
    ],
  })
  await router.push('/sync')
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await settle()
  await settle()
  mounted = { wrapper, router, pinia }
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

async function clickButton(wrapper: VueWrapper, text: string, index = 0): Promise<void> {
  findButton(pageRoot(wrapper), text, index).click()
  await settle()
}

function inputByPlaceholder(wrapper: VueWrapper, placeholder: string, index = 0): HTMLInputElement {
  const list = (Array.from(pageRoot(wrapper).querySelectorAll('input')) as HTMLInputElement[]).filter(
    (el) => el.getAttribute('placeholder') === placeholder,
  )
  const target = list[index]
  if (!target) {
    throw new Error(
      'input not found: ' + placeholder + ' | have=' +
        (Array.from(pageRoot(wrapper).querySelectorAll('input')) as HTMLInputElement[])
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

function kvText(wrapper: VueWrapper): string {
  const rows = Array.from(pageRoot(wrapper).querySelectorAll('.mh-kv__row')) as HTMLElement[]
  return rows.map((row) => (row.textContent || '').trim()).join(' | ')
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
  // naive 的对话框关闭后仍留在 DOM（只隐藏）；不等过渡结束就点下一个触发器会被 click-outside 关掉。
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

/* -------------------------------------------------------------------------- */
/* setup / teardown                                                            */
/* -------------------------------------------------------------------------- */

beforeEach(() => {
  vi.clearAllMocks()
  copyState.ok = true
  // jsdom 没实现：SettingsLayout 的锚点跳转与 naive 内部滚动都会调用它们。
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { value: () => undefined, configurable: true })
  HTMLElement.prototype.scrollIntoView = () => undefined
  getSyncConfig.mockResolvedValue(config())
  saveSyncConfig.mockResolvedValue({ ok: true, secret_set: true })
  pushToOnline.mockResolvedValue(pushPayload())
  listPlatforms.mockResolvedValue({
    items: [spec('workbuddy'), spec('trae')],
    default: 'workbuddy',
    loader: { loaded: ['workbuddy', 'trae'], failed: {} },
  })
  putCurrentPlatform.mockResolvedValue({ ok: true, platform: 'trae', realm: '' })
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

describe('SyncPage 覆盖率补测 · 首屏与状态行', () => {
  it('首屏渲染表单与只读状态行，未改动时保存禁用', async () => {
    const { wrapper } = await mountPage()

    expect(wrapper.find('h1').text()).toContain('同步设置')
    // 表单回填：URL 去掉末尾斜杠、密钥为完整值（登录管理员可见）
    expect(inputByPlaceholder(wrapper, 'https://online.example.com').value).toBe('https://online.example.com')
    const secret = inputByPlaceholder(wrapper, '与线上实例一致的密钥')
    expect(secret.value).toBe(SECRET)
    expect(secret.getAttribute('type')).toBe('password')

    // 只读状态行：远程 URL + 后端打码串
    const kv = kvText(wrapper)
    expect(kv).toContain('远程 URL')
    expect(kv).toContain('https://online.example.com')
    expect(kv).toContain('********3b4c')
    // 没有本机记录、后端也没回 last_push_*：显示「尚未同步」
    expect(wrapper.text()).toContain('尚未同步')

    // 干净表单：保存禁用、页头没有未保存徽章
    expect(findButton(pageRoot(wrapper), '保存配置').disabled).toBe(true)
    expect(wrapper.text()).not.toContain('有未保存的修改')
  })

  it('首次加载期间显示骨架，动作按钮全部禁用；加载完成后消失', async () => {
    const pending = deferred<SyncConfigPayload>()
    getSyncConfig.mockReturnValue(pending.promise)
    const { wrapper } = await mountPage()

    expect(pageRoot(wrapper).querySelector('[aria-busy="true"]')).toBeTruthy()
    expect(findButton(pageRoot(wrapper), '保存配置').disabled).toBe(true)
    expect(findButton(pageRoot(wrapper), '同步到线上').disabled).toBe(true)

    pending.resolve(config())
    await settle()
    await settle()

    expect(pageRoot(wrapper).querySelector('[aria-busy="true"]')).toBeNull()
    expect(kvText(wrapper)).toContain('********3b4c')
    expect(findButton(pageRoot(wrapper), '同步到线上').disabled).toBe(false)
  })

  it('自动生成的密钥显示「本次访问自动生成」徽章', async () => {
    getSyncConfig.mockResolvedValue(config({ secret_masked: '', is_auto_generated: true }))
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('本次访问自动生成')
    // secret_masked 为空时回落 maskSecret(secret)
    expect(kvText(wrapper)).toContain('3b4c')
  })

  it('后端回传 last_push_* 时优先于本机记录（前向兼容）', async () => {
    getSyncConfig.mockResolvedValue(
      config({
        last_push_at: 1_800_000_000,
        last_push_result: { ok: false, accounts: { added: 0, updated: 4 } },
        last_push_error: '线上返回 500',
      }),
    )
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).toContain('线上返回 500')
    expect(wrapper.text()).toContain('~4')
    expect(wrapper.text()).not.toContain('尚未同步')
  })
})

describe('SyncPage 覆盖率补测 · 保存与取消', () => {
  it('改 URL/密钥后出现未保存徽章，保存发送归一后的请求体并重载表单', async () => {
    const { wrapper } = await mountPage()
    await typeInto(inputByPlaceholder(wrapper, 'https://online.example.com'), '  https://new.example.com/  ')
    await typeInto(inputByPlaceholder(wrapper, '与线上实例一致的密钥'), '  new-secret  ')

    expect(wrapper.text()).toContain('有未保存的修改')
    expect(wrapper.text()).toContain('未保存')
    expect(findButton(pageRoot(wrapper), '保存配置').disabled).toBe(false)

    getSyncConfig.mockResolvedValue(config({ remote_url: 'https://new.example.com', secret: 'new-secret' }))
    await clickButton(wrapper, '保存配置')
    await settle()

    expect(saveSyncConfig).toHaveBeenCalledTimes(1)
    expect(saveSyncConfig.mock.calls[0][0]).toEqual({
      remote_url: 'https://new.example.com',
      secret: 'new-secret',
    })
    expect(bodyText()).toContain('已保存同步配置')
    // 保存后以库里实际值为准重新 GET 并覆盖表单
    expect(getSyncConfig.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(inputByPlaceholder(wrapper, 'https://online.example.com').value).toBe('https://new.example.com')
    expect(wrapper.text()).not.toContain('有未保存的修改')
  })

  it('保存中按钮进入 loading/禁用（进度），失败弹「保存配置失败」且保留修改', async () => {
    const { wrapper } = await mountPage()
    await typeInto(inputByPlaceholder(wrapper, 'https://online.example.com'), 'https://new.example.com')
    const pending = deferred<{ ok: boolean; secret_set: boolean }>()
    saveSyncConfig.mockReturnValue(pending.promise)
    findButton(pageRoot(wrapper), '保存配置').click()
    await settle()

    const saveBtn = findButton(pageRoot(wrapper), '保存配置')
    expect(saveBtn.disabled).toBe(true)
    // 保存中不允许并发推送
    expect(findButton(pageRoot(wrapper), '同步到线上').disabled).toBe(true)

    pending.reject(new Error('磁盘只读'))
    await settle()
    await settle()

    expect(bodyText()).toContain('保存配置失败：磁盘只读')
    expect(wrapper.text()).toContain('有未保存的修改')
    expect(saveBtn.disabled).toBe(false)
    expect(saveSyncConfig).toHaveBeenCalledTimes(1)
  })

  it('取消：恢复为上次保存的配置并提示', async () => {
    const { wrapper } = await mountPage()
    const url = inputByPlaceholder(wrapper, 'https://online.example.com')
    await typeInto(url, 'https://other.example.com')
    await clickButton(wrapper, '取消')
    expect(inputByPlaceholder(wrapper, 'https://online.example.com').value).toBe('https://online.example.com')
    expect(bodyText()).toContain('已恢复为上次保存的配置')
    expect(wrapper.text()).not.toContain('有未保存的修改')
  })

  it('URL 失焦去掉末尾斜杠；非 http(s) 只提示不拦截', async () => {
    const { wrapper } = await mountPage()
    const input = inputByPlaceholder(wrapper, 'https://online.example.com')
    await typeInto(input, '  ftp://mirror.example.com/  ')
    expect(wrapper.text()).toContain('不是 http(s) 地址')
    input.dispatchEvent(new Event('blur'))
    await settle()
    const next = inputByPlaceholder(wrapper, 'https://online.example.com')
    expect(next.value).toBe('ftp://mirror.example.com')
    // 保存按钮仍可点（后端不校验格式）
    expect(findButton(pageRoot(wrapper), '保存配置').disabled).toBe(false)
  })
})

describe('SyncPage 覆盖率补测 · 同步到线上', () => {
  it('确认框文案是问句 + 后果；取消确认不发请求', async () => {
    const { wrapper } = await mountPage()
    findButton(pageRoot(wrapper), '同步到线上').click()
    await settle()
    expect(dialogTitle()).toBe('同步到线上？')
    expect(lastDialog().textContent).toContain('会用本地账号、模型配置与客户端参数覆盖线上实例的同名数据')
    await answerDialog('取消')
    expect(pushToOnline).not.toHaveBeenCalled()
  })

  it('确认后推送并展示线上 upsert 计数，本机记录写入 localStorage', async () => {
    const { wrapper } = await mountPage()
    findButton(pageRoot(wrapper), '同步到线上').click()
    await settle()
    await answerDialog('同步到线上')

    expect(pushToOnline).toHaveBeenCalledTimes(1)
    expect(pushToOnline.mock.calls[0][0]).toEqual({
      remote_url: 'https://online.example.com',
      secret: SECRET,
    })
    expect(bodyText()).toContain('已同步到线上')

    const detail = wrapper.find('.last-push').text()
    expect(detail).toContain('成功')
    expect(detail).toContain('推送至 https://online.example.com')
    expect(detail).toContain('账号 +3 / ~9')
    expect(detail).toContain('模型 +1 / ~6')
    expect(detail).toContain('已同步 2 项')

    const stored = JSON.parse(String(localStorage.getItem(syncApi.SYNC_RECORD_KEY)))
    expect(stored).toMatchObject({ ok: true, remote: 'https://online.example.com' })
    expect(stored.accounts).toEqual({ added: 3, updated: 9 })
    expect(typeof stored.at).toBe('number')
  })

  it('进行中：按钮禁用且不可重复触发（进度）', async () => {
    const { wrapper } = await mountPage()
    const pending = deferred<SyncPushResult>()
    pushToOnline.mockReturnValue(pending.promise)
    findButton(pageRoot(wrapper), '同步到线上').click()
    await settle()
    const positive = Array.from(lastDialog().querySelectorAll('.n-dialog__action button')) as HTMLElement[]
    const ok = positive.find((el) => (el.textContent || '').trim() === '同步到线上')
    expect(ok).toBeTruthy()

    ok!.click()
    await settle()

    const pushBtn = findButton(pageRoot(wrapper), '同步到线上')
    expect(pushBtn.disabled).toBe(true)
    expect(pushToOnline).toHaveBeenCalledTimes(1)

    pending.resolve(pushPayload())
    await settle()
    await settle()
    expect(bodyText()).toContain('已同步到线上')
    expect(findButton(pageRoot(wrapper), '同步到线上').disabled).toBe(false)
  })

  it('失败：Toast 说原因，状态行显示失败与后端原文，并写入失败记录', async () => {
    const { wrapper } = await mountPage()
    pushToOnline.mockRejectedValue(new Error('HMAC 签名校验失败'))
    findButton(pageRoot(wrapper), '同步到线上').click()
    await settle()
    await answerDialog('同步到线上')

    expect(bodyText()).toContain('同步到线上失败：HMAC 签名校验失败')
    const detail = wrapper.find('.last-push').text()
    expect(detail).toContain('失败')
    expect(detail).toContain('HMAC 签名校验失败')

    const stored = JSON.parse(String(localStorage.getItem(syncApi.SYNC_RECORD_KEY)))
    expect(stored).toMatchObject({ ok: false, error: 'HMAC 签名校验失败' })
  })

  it('远程 URL 为空时前置拦截：只提示，不发请求', async () => {
    getSyncConfig.mockResolvedValue(config({ remote_url: '', secret: SECRET }))
    const { wrapper } = await mountPage()
    findButton(pageRoot(wrapper), '同步到线上').click()
    await settle()
    expect(bodyText()).toContain('请先填写远程 URL')
    expect(pushToOnline).not.toHaveBeenCalled()
    expect(document.querySelector('.n-dialog')).toBeNull()
  })
})

describe('SyncPage 覆盖率补测 · 刷新、重试与密钥工具', () => {
  it('首次加载失败显示错误块，重试成功后渲染表单', async () => {
    getSyncConfig.mockRejectedValueOnce(new Error('网络中断')).mockResolvedValue(config())
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('同步配置加载失败')
    // 原因收在「展开详细信息」里（05-patterns：错误块只露一句，细节可展开）
    await clickButton(wrapper, '展开详细信息')
    expect(wrapper.text()).toContain('网络中断')
    await clickButton(wrapper, '重试')
    await settle()

    expect(getSyncConfig.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(wrapper.text()).not.toContain('同步配置加载失败')
    expect(kvText(wrapper)).toContain('********3b4c')
  })

  // 契约 §六「刷新失败（已有数据）」：保留旧数据 + 卡片内 compact ErrorBlock **与** Toast 两条都给。
  // refresh() 走 load() 的默认（非后台静默）路径，所以失败既留错误块也弹 Toast。
  it('刷新失败：保留上次数据，给 compact 错误块与 Toast，可再次重试', async () => {
    const { wrapper } = await mountPage()
    getSyncConfig.mockRejectedValueOnce(new Error('网关超时'))
    await clickButton(wrapper, '刷新')

    expect(wrapper.text()).toContain('刷新同步配置失败，当前显示上次加载的值')
    expect(bodyText()).toContain('刷新同步配置失败：网关超时')
    await clickButton(wrapper, '展开详细信息')
    expect(wrapper.text()).toContain('网关超时')
    expect(kvText(wrapper)).toContain('https://online.example.com')
    await clickButton(wrapper, '收起详细信息')

    getSyncConfig.mockResolvedValue(config({ remote_url: 'https://backup.example.com' }))
    findButton(pageRoot(wrapper), '重试').click()
    await settle()
    expect(wrapper.text()).not.toContain('刷新同步配置失败')
    expect(kvText(wrapper)).toContain('https://backup.example.com')
  })

  it('重新生成：确认框是问句，确认后填入 64 位十六进制并设为明文；取消则不改', async () => {
    const { wrapper } = await mountPage()
    await clickButton(wrapper, '重新生成')
    expect(dialogTitle()).toBe('重新生成同步密钥？')
    await answerDialog('取消')
    expect(inputByPlaceholder(wrapper, '与线上实例一致的密钥').value).toBe(SECRET)

    await clickButton(wrapper, '重新生成')
    await answerDialog('重新生成')
    const secret = inputByPlaceholder(wrapper, '与线上实例一致的密钥')
    expect(secret.value).toMatch(/^[0-9a-f]{64}$/)
    expect(secret.getAttribute('type')).toBe('text')
    expect(bodyText()).toContain('已生成新密钥，记得点「保存配置」')
    expect(wrapper.text()).toContain('有未保存的修改')
  })

  it('显示 / 隐藏密钥与复制（成功、剪贴板被拒、无密钥可复制）', async () => {
    const { wrapper } = await mountPage()
    const eye = pageRoot(wrapper).querySelector('button[aria-label="显示密钥"]') as HTMLButtonElement
    expect(eye).toBeTruthy()
    eye.click()
    await settle()
    expect(inputByPlaceholder(wrapper, '与线上实例一致的密钥').getAttribute('type')).toBe('text')
    const hide = pageRoot(wrapper).querySelector('button[aria-label="隐藏密钥"]') as HTMLButtonElement
    expect(hide).toBeTruthy()
    hide.click()
    await settle()
    expect(inputByPlaceholder(wrapper, '与线上实例一致的密钥').getAttribute('type')).toBe('password')

    const copy = pageRoot(wrapper).querySelector('button[aria-label="复制密钥"]') as HTMLButtonElement
    copy.click()
    await settle()
    expect(bodyText()).toContain('已复制')

    copyState.ok = false
    copy.click()
    await settle()
    expect(bodyText()).toContain('复制失败：浏览器拒绝了剪贴板访问，请手动选中复制')
  })

  it('库里也没有密钥时复制给 warning，而不是静默失败', async () => {
    getSyncConfig.mockResolvedValue(config({ secret: '', secret_masked: '' }))
    const { wrapper } = await mountPage()
    const copy = pageRoot(wrapper).querySelector('button[aria-label="复制密钥"]') as HTMLButtonElement
    copy.click()
    await settle()
    expect(bodyText()).toContain('还没有可复制的密钥')
    expect(copyState.ok).toBe(true)
  })
})

describe('SyncPage 覆盖率补测 · 平台切换', () => {
  it('切换平台重取配置；表单有未保存修改时不覆盖输入', async () => {
    const { wrapper, pinia } = await mountPage()
    setActivePinia(pinia)
    const platform = usePlatformStore()
    platform.items = [spec('workbuddy'), spec('trae')]

    await typeInto(inputByPlaceholder(wrapper, 'https://online.example.com'), 'https://typed.example.com')
    const before = getSyncConfig.mock.calls.length

    getSyncConfig.mockResolvedValue(config({ remote_url: 'https://server.example.com' }))
    platform.setCurrent('trae')
    await settle()
    await settle()

    expect(getSyncConfig.mock.calls.length).toBe(before + 1)
    // 有未保存修改：保留用户输入，只更新状态行
    expect(inputByPlaceholder(wrapper, 'https://online.example.com').value).toBe('https://typed.example.com')
    expect(wrapper.text()).toContain('有未保存的修改')
  })

  it('切平台重取失败只留卡片内错误块，不弹 Toast（后台重取走 quiet）', async () => {
    const { wrapper, pinia } = await mountPage()
    setActivePinia(pinia)
    const platform = usePlatformStore()
    platform.items = [spec('workbuddy'), spec('trae')]

    const before = getSyncConfig.mock.calls.length
    getSyncConfig.mockRejectedValueOnce(new Error('平台切换时超时'))
    platform.setCurrent('trae')
    await settle()
    await settle()

    expect(getSyncConfig.mock.calls.length).toBe(before + 1)
    expect(wrapper.text()).toContain('刷新同步配置失败，当前显示上次加载的值')
    // 后台静默重取不给 Toast：错误原因只收在卡片错误块的「展开详细信息」里
    expect(bodyText()).not.toContain('失败：平台切换时超时')
  })

  it('表单干净时切换平台用最新配置覆盖表单', async () => {
    const { wrapper, pinia } = await mountPage()
    setActivePinia(pinia)
    const platform = usePlatformStore()
    platform.items = [spec('workbuddy'), spec('trae')]

    getSyncConfig.mockResolvedValue(config({ remote_url: 'https://trae.example.com', secret: 'trae-secret' }))
    platform.setCurrent('trae')
    await settle()
    await settle()

    expect(inputByPlaceholder(wrapper, 'https://online.example.com').value).toBe('https://trae.example.com')
    expect(inputByPlaceholder(wrapper, '与线上实例一致的密钥').value).toBe('trae-secret')
    expect(wrapper.text()).not.toContain('有未保存的修改')
  })
})

// Naive message 的 auto-hide 定时器会在 jsdom 销毁后触发；用可自动推进的假时钟并在收尾清空。
beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })
