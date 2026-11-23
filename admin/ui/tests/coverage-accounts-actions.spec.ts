/** 覆盖率补充：AccountsPage 行操作 / 批量条 / 添加抽屉模式 / 导入预览真实挂载。只新增本文件，不改 src。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import AccountsPage from '@/pages/AccountsPage.vue'
import RowActions from '@/components/data/RowActions.vue'
import * as accountsApi from '@/api/accounts'
import * as addApi from '@/api/account-add'
import * as httpApi from '@/api/http'
import { usePlatformStore } from '@/stores/platform'
import type { AccountItem } from '@/api/accounts'
import type { PlatformSpec } from '@/api/platforms'

vi.mock('@/api/accounts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/accounts')>()
  return {
    ...actual,
    listAccounts: vi.fn(),
    getTodayUsage: vi.fn(),
    getModelLimits: vi.fn(),
    bulkAccounts: vi.fn(),
    refreshAccount: vi.fn(),
    healthCheckAccount: vi.fn(),
    deleteAccount: vi.fn(),
    patchAccount: vi.fn(),
    refreshBatch: vi.fn(),
    healthCheckBatch: vi.fn(),
    exportAccountsAsync: vi.fn(),
    runAccountTask: vi.fn(),
    growthRunAsync: vi.fn(),
    catTravelBatchAsync: vi.fn(),
    getCreditDetails: vi.fn(),
  }
})

vi.mock('@/api/account-add', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/account-add')>()
  return {
    ...actual,
    addAccountManually: vi.fn(),
    importAccountsAsync: vi.fn(),
    scanLocalAccounts: vi.fn(),
    importLocalAccounts: vi.fn(),
    preparePhoneFlow: vi.fn(),
    sendPhoneCode: vi.fn(),
    verifyPhoneCode: vi.fn(),
    beginBrowserAuth: vi.fn(),
    pollBrowserAuth: vi.fn(),
  }
})

// useJob 走 http 轮询 /api/accounts/job/；测试里给确定快照，避免真实 fetch 与 1500ms 定时器。
vi.mock('@/api/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/http')>()
  return { ...actual, http: vi.fn(), download: vi.fn() }
})

const listAccounts = vi.mocked(accountsApi.listAccounts)
const getTodayUsage = vi.mocked(accountsApi.getTodayUsage)
const bulkAccounts = vi.mocked(accountsApi.bulkAccounts)
const refreshAccount = vi.mocked(accountsApi.refreshAccount)
const healthCheckAccount = vi.mocked(accountsApi.healthCheckAccount)
const deleteAccount = vi.mocked(accountsApi.deleteAccount)
const patchAccount = vi.mocked(accountsApi.patchAccount)
const exportAccountsAsync = vi.mocked(accountsApi.exportAccountsAsync)
const addAccountManually = vi.mocked(addApi.addAccountManually)
const importAccountsAsync = vi.mocked(addApi.importAccountsAsync)
const scanLocalAccounts = vi.mocked(addApi.scanLocalAccounts)
const importLocalAccounts = vi.mocked(addApi.importLocalAccounts)
const preparePhoneFlow = vi.mocked(addApi.preparePhoneFlow)
const sendPhoneCode = vi.mocked(addApi.sendPhoneCode)
const verifyPhoneCode = vi.mocked(addApi.verifyPhoneCode)
const beginBrowserAuth = vi.mocked(addApi.beginBrowserAuth)
const http = vi.mocked(httpApi.http)

/* -------------------------------------------------------------------------- */
/* 数据形状照抄后端：GET /api/accounts、/api/logs/summary、平台声明            */
/* -------------------------------------------------------------------------- */

function account(id: number, overrides: Partial<AccountItem> = {}): AccountItem {
  return {
    id,
    name: id === 1 ? '主号' : '备用号',
    uid: '1930000' + id,
    platform: 'workbuddy',
    domain: '',
    status: id === 2 ? 'disabled' : 'active',
    max_in_flight: null,
    bound_key_id: null,
    bound_key_name: '',
    balance_total: 5000,
    balance_remain: 4000,
    last_sync_at: null,
    last_used_at: null,
    created_at: '2026-11-01T00:00:00',
    model_limits: [],
    gateway_state: 'ready',
    gateway_until: null,
    gateway_reason: '',
    credits_expiring_soon: 0,
    credits_evergreen: 5000,
    credits_expired: 0,
    credits_soonest_expire_at: null,
    token_supported: false,
    token_expires_at: null,
    token_days_left: null,
    token_state: '',
    renewal_expires_at: null,
    renewal_days_left: null,
    renewal_state: '',
    renewable: false,
    ...overrides,
  } as AccountItem
}

function platform(overrides: Partial<PlatformSpec> = {}): PlatformSpec {
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
    actions: ['scan', 'import'],
    quota_unit: 'credits',
    quota_unit_label: '',
    account_count: 2,
    realm_counts: {},
    ...overrides,
  }
}

const jobSnapshot = (overrides: Record<string, unknown> = {}) =>
  ({ job_id: 'job-1', status: 'running', total: 2, done: 0, percent: 0, ...overrides }) as never

/* -------------------------------------------------------------------------- */
/* 挂载（照 coverage-sentinel / coverage-accounts 的模式，多一层手机卡片）      */
/* -------------------------------------------------------------------------- */

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template:
    '<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>',
})

let mounted: { wrapper: VueWrapper; router: Router } | null = null

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
  await nextTick()
}

async function waitFor(check: () => boolean, label: string, timeout = 1500): Promise<void> {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (check()) return
    await new Promise((resolve) => setTimeout(resolve, 15))
    await flushPromises()
    await nextTick()
  }
  throw new Error('等待超时：' + label)
}

async function mountPage(options: { specs?: PlatformSpec[]; current?: string } = {}) {
  const specs = options.specs || [platform()]
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = usePlatformStore()
  store.items = specs
  store.platform = options.current || specs[0].id
  store.realm = ''
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/accounts', name: 'accounts', component: AccountsPage }],
  })
  await router.push('/accounts')
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await settle()
  await settle()
  mounted = { wrapper, router }
  return mounted
}

/* jsdom 里 NDataTable 的虚拟行不会渲染；把 matchMedia 指到手机断点，
   页面改用 DataTable 的卡片列表，行操作/勾选才有真实 DOM 可点。 */
function media(query: string): MediaQueryList {
  return {
    matches: query.includes('max-width: 640px'),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  } as unknown as MediaQueryList
}

const originalMatchMedia = window.matchMedia
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView

beforeEach(() => {
  for (const fn of [
    listAccounts, getTodayUsage, bulkAccounts, refreshAccount, healthCheckAccount, deleteAccount,
    patchAccount, exportAccountsAsync, addAccountManually, importAccountsAsync, scanLocalAccounts,
    importLocalAccounts, preparePhoneFlow, sendPhoneCode, verifyPhoneCode, http,
  ]) {
    fn.mockReset()
  }
  listAccounts.mockResolvedValue({ items: [account(1), account(2)] } as never)
  getTodayUsage.mockResolvedValue({ items: [] } as never)
  http.mockResolvedValue(jobSnapshot())
  refreshAccount.mockResolvedValue({ id: 1, balance_total: 5000, balance_remain: 4321 } as never)
  healthCheckAccount.mockResolvedValue(jobSnapshot({ job_id: 'job-check' }))
  deleteAccount.mockResolvedValue({ id: 1, ok: true } as never)
  patchAccount.mockResolvedValue({ id: 1, ok: true } as never)
  bulkAccounts.mockResolvedValue({ ok: true, action: 'enable', requested: 1, affected: 1, skipped: [], errors: [] } as never)
  exportAccountsAsync.mockResolvedValue(jobSnapshot({ job_id: 'job-export' }))
  addAccountManually.mockResolvedValue({ id: 9, name: '工作号' } as never)
  importAccountsAsync.mockResolvedValue({ job_id: 'job-import', status: 'running' } as never)
  importLocalAccounts.mockResolvedValue({ added: 1, updated: 0, skipped: 0, errors: [] } as never)
  preparePhoneFlow.mockResolvedValue({} as never)
  sendPhoneCode.mockResolvedValue({} as never)
  verifyPhoneCode.mockResolvedValue({ name: '工作号' } as never)
  window.matchMedia = ((query: string) => media(query)) as typeof window.matchMedia
  vi.stubGlobal('ResizeObserver', class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  })
  HTMLElement.prototype.scrollIntoView = () => undefined
})

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
  window.matchMedia = originalMatchMedia
  HTMLElement.prototype.scrollIntoView = originalScrollIntoView
  vi.unstubAllGlobals()
  localStorage.clear()
})

/* -------------------------------------------------------------------------- */
/* DOM 查询小工具                                                              */
/* -------------------------------------------------------------------------- */

function cardList(wrapper: VueWrapper) {
  return wrapper.findAll('.mh-cards__item')
}

function bodyButton(text: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll('button')).find(
    (el) => !el.closest('.n-drawer') && !el.closest('.n-dialog') && (el.textContent || '').trim() === text,
  ) as HTMLElement | undefined
}

async function clickBodyButton(text: string): Promise<void> {
  const button = bodyButton(text)
  if (!button) throw new Error('找不到按钮：' + text)
  button.click()
  await settle()
}

async function clickIn(root: Element, text: string): Promise<void> {
  const button = Array.from(root.querySelectorAll('button')).find(
    (el) => (el.textContent || '').trim() === text,
  ) as HTMLElement | undefined
  if (!button) throw new Error('找不到按钮：' + text + ' @ ' + (root.textContent || '').slice(0, 80))
  button.click()
  await settle()
}

async function checkCard(wrapper: VueWrapper, index: number, checked = true): Promise<void> {
  // naive-ui 的 NCheckbox 是 div[role=checkbox]，不是原生 input。
  const box = cardList(wrapper)[index].find('.mh-cards__check')
  const el = box.element as HTMLElement
  if ((el.getAttribute('aria-checked') === 'true') !== checked) el.click()
  await settle()
}

function bulkBar(wrapper: VueWrapper) {
  return wrapper.find('.mh-bulk')
}

async function clickBulk(wrapper: VueWrapper, text: string): Promise<void> {
  await clickIn(bulkBar(wrapper).element, text)
}

function dialogEl(): HTMLElement | null {
  const dialogs = Array.from(document.querySelectorAll('.n-dialog')) as HTMLElement[]
  return dialogs.length ? dialogs[dialogs.length - 1] : null
}

async function clickDialogButton(text: string): Promise<void> {
  const dialog = dialogEl()
  if (!dialog) throw new Error('确认框没出现：' + document.body.textContent?.slice(-160))
  const buttons = Array.from(dialog.querySelectorAll('.n-dialog__action button'))
  const button = buttons.find((el) => (el.textContent || '').trim() === text)
  if (!button) throw new Error('确认框没有按钮：' + text + ' | ' + buttons.map((b) => b.textContent).join(','))
  ;(button as HTMLElement).click()
  await settle()
}

async function fill(element: Element | null, value: string): Promise<void> {
  if (!element) throw new Error('找不到输入框')
  await new DOMWrapper(element as HTMLElement).setValue(value)
  await settle()
}

function drawerEl(): HTMLElement | null {
  return document.querySelector('.n-drawer') as HTMLElement | null
}

function drawerText(): string {
  return drawerEl()?.textContent || ''
}

async function openDrawerByHeader(text: string): Promise<void> {
  await clickBodyButton(text)
  await waitFor(() => Boolean(drawerEl()?.querySelector('.n-drawer-header__main')), '抽屉打开：' + text)
}

async function closeDrawer(): Promise<void> {
  const close = document.querySelector('.n-drawer-header__close') as HTMLElement | null
  if (close) close.click()
  await settle()
}

function dropdownOptions(): HTMLElement[] {
  return Array.from(document.querySelectorAll('.n-dropdown-option')) as HTMLElement[]
}

async function pickAddMode(label: string): Promise<void> {
  await clickBodyButton('添加账号')
  await waitFor(() => dropdownOptions().length > 0, '添加账号下拉')
  const option = dropdownOptions().find((el) => (el.textContent || '').trim() === label)
  if (!option) throw new Error('下拉没有：' + label + ' | ' + dropdownOptions().map((el) => el.textContent).join(','))
  const body = option.querySelector('.n-dropdown-option-body') as HTMLElement | null
  ;(body || option).click()
  await settle()
}

function toastText(): string {
  return Array.from(document.querySelectorAll('.n-message')).map((el) => el.textContent || '').join(' | ')
}

/** 行操作的前 2 项在卡片里是可点按钮；其余收在「⋯」浮层里。浮层在 jsdom 里
 *  依赖 teleport + transition，直接对页面渲染出的 RowActions 发 select 更稳定，
 *  走的仍是页面的 onRowAction 分支。 */
async function rowAction(wrapper: VueWrapper, index: number, key: string): Promise<void> {
  const actions = wrapper.findAllComponents(RowActions)
  if (!actions[index]) throw new Error('第 ' + index + ' 行没有 RowActions')
  actions[index].vm.$emit('select', key)
  await settle()
}

/* -------------------------------------------------------------------------- */
/* 行操作                                                                      */
/* -------------------------------------------------------------------------- */

describe('AccountsPage · 行操作', () => {
  it('行内「刷新额度」调用单号刷新并重新取数', async () => {
    const { wrapper } = await mountPage()
    // 默认排序 id desc：卡片 0=备用号(2)，卡片 1=主号(1)
    await clickIn(cardList(wrapper)[1].element, '刷新额度')
    expect(refreshAccount).toHaveBeenCalledWith(1)
    expect(listAccounts.mock.calls.length).toBeGreaterThan(1)
    await waitFor(() => toastText().includes('已刷新「主号」'), '刷新 toast')
    expect(toastText()).toContain('4,321')
  })

  it('行内「检测」把单号健康检查交给后台任务并出现任务条', async () => {
    const { wrapper } = await mountPage()
    await clickIn(cardList(wrapper)[0].element, '检测')
    expect(healthCheckAccount).toHaveBeenCalledWith(2)
    await waitFor(() => Boolean(wrapper.find('.acc-jobbar').exists()), '任务内联条')
    expect(wrapper.find('.acc-jobbar').text()).toContain('账号任务')
    expect(wrapper.find('.acc-jobbar').text()).toContain('0 / 2')
    await waitFor(() => toastText().includes('任务已启动'), '任务 toast')
  })

  it('「禁用」经确认框后 patchAccount(status=disabled)', async () => {
    const { wrapper } = await mountPage()
    await rowAction(wrapper, 1, 'disable')
    await waitFor(() => Boolean(dialogEl()), '禁用确认框')
    expect(dialogEl()!.textContent).toContain('禁用「主号」？')
    expect(dialogEl()!.textContent).toContain('禁用后该账号不再接请求')
    await clickDialogButton('禁用')
    expect(patchAccount).toHaveBeenCalledWith(1, { status: 'disabled' })
    await waitFor(() => toastText().includes('已禁用「主号」'), '禁用 toast')
  })

  it('已禁用行显示「启用」，确认后 patchAccount(status=active)', async () => {
    const { wrapper } = await mountPage()
    const actions = wrapper.findAllComponents(RowActions)[0]
    expect(actions.props('actions').map((item: { key: string }) => item.key)).toEqual([
      'refresh', 'check', 'edit', 'enable', 'detail', 'delete',
    ])
    await rowAction(wrapper, 0, 'enable')
    await waitFor(() => Boolean(dialogEl()), '启用确认框')
    expect(dialogEl()!.textContent).toContain('启用「备用号」？')
    await clickDialogButton('启用')
    expect(patchAccount).toHaveBeenCalledWith(2, { status: 'active' })
  })

  it('「删除」确认后 deleteAccount 并重新取数', async () => {
    const { wrapper } = await mountPage()
    await rowAction(wrapper, 1, 'delete')
    await waitFor(() => Boolean(dialogEl()), '删除确认框')
    expect(dialogEl()!.textContent).toContain('删除账号「主号」？')
    expect(dialogEl()!.textContent).toContain('删除后无法恢复')
    const before = listAccounts.mock.calls.length
    await clickDialogButton('删除')
    expect(deleteAccount).toHaveBeenCalledWith(1)
    expect(listAccounts.mock.calls.length).toBeGreaterThan(before)
    await waitFor(() => toastText().includes('已删除「主号」'), '删除 toast')
  })
})

/* -------------------------------------------------------------------------- */
/* 批量条                                                                      */
/* -------------------------------------------------------------------------- */

describe('AccountsPage · 批量选择与工具条', () => {
  it('勾选行出现批量条，批量启用后清空选择并重新取数', async () => {
    const { wrapper } = await mountPage()
    await checkCard(wrapper, 0)
    await checkCard(wrapper, 1)
    expect(bulkBar(wrapper).text()).toContain('已选 2 项')
    expect(bulkBar(wrapper).text()).toContain('删除')
    const before = listAccounts.mock.calls.length
    bulkAccounts.mockResolvedValue({ ok: true, action: 'enable', requested: 2, affected: 2, skipped: [], errors: [] } as never)
    await clickBulk(wrapper, '启用')
    expect(bulkAccounts).toHaveBeenCalledWith('enable', [1, 2])
    await waitFor(() => !bulkBar(wrapper).exists(), '批量条收起')
    expect(listAccounts.mock.calls.length).toBeGreaterThan(before)
    expect(toastText()).toContain('已启用 2 个账号')
  })

  it('批量删除必须输入确认文字，未匹配时不发请求', async () => {
    const { wrapper } = await mountPage()
    await checkCard(wrapper, 0)
    await checkCard(wrapper, 1)
    await clickBulk(wrapper, '删除')
    await waitFor(() => Boolean(dialogEl()), '批量删除确认框')
    expect(dialogEl()!.textContent).toContain('删除 2 个账号？')
    expect(dialogEl()!.textContent).toContain('请输入「删除」以确认')

    await clickDialogButton('删除')
    expect(dialogEl()!.textContent).toContain('输入的内容不匹配，请重新输入')
    expect(bulkAccounts).not.toHaveBeenCalled()

    await fill(document.querySelector('.n-dialog input'), '删除')
    bulkAccounts.mockResolvedValue({ ok: true, action: 'delete', requested: 2, affected: 2, skipped: [], errors: [] } as never)
    await clickDialogButton('删除')
    await waitFor(() => bulkAccounts.mock.calls.length > 0, '批量删除请求')
    expect(bulkAccounts).toHaveBeenCalledWith('delete', [1, 2])
    expect(toastText()).toContain('已删除 2 个账号')
  })

  it('取消批量删除不发请求并保留选择', async () => {
    const { wrapper } = await mountPage()
    await checkCard(wrapper, 0)
    await clickBulk(wrapper, '删除')
    await waitFor(() => Boolean(dialogEl()), '批量删除确认框')
    await clickDialogButton('取消')
    expect(bulkAccounts).not.toHaveBeenCalled()
    expect(bulkBar(wrapper).text()).toContain('已选 1 项')
  })

  it('批量检测与导出选中调用对应接口', async () => {
    const { wrapper } = await mountPage()
    await checkCard(wrapper, 0)
    await clickBulk(wrapper, '检测')
    expect(bulkAccounts).toHaveBeenCalledWith('check', [2])

    await checkCard(wrapper, 0)
    await clickBulk(wrapper, '导出选中')
    expect(exportAccountsAsync).toHaveBeenCalledWith({ ids: [2] })
    await waitFor(() => Boolean(wrapper.find('.acc-jobbar').exists()), '导出任务条')
  })
})

/* -------------------------------------------------------------------------- */
/* 添加抽屉：模式切换 / 导入预览 / 扫描预览                                     */
/* -------------------------------------------------------------------------- */

describe('AccountsPage · 添加抽屉', () => {
  it('导入 / 扫描本机 / 手动添加三种模式在同一抽屉里切换', async () => {
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('扫描本机')

    await openDrawerByHeader('导入')
    expect(document.querySelector('.n-drawer-header__main')!.textContent).toContain('导入账号 · WorkBuddy')
    expect(drawerText()).toContain('粘贴凭证')
    await closeDrawer()

    scanLocalAccounts.mockResolvedValue({
      dir: 'C:/Users/me/AppData/Roaming/workbuddy/data',
      exists: true,
      items: [{ file: 'a.info', uid: 'u1', nickname: '工作号 A' }],
      pool_total: 1,
    } as never)
    await openDrawerByHeader('扫描本机')
    expect(document.querySelector('.n-drawer-header__main')!.textContent).toContain('扫描本机 · WorkBuddy')
    await waitFor(() => drawerText().includes('发现 1 个客户端账号'), '扫描预览')
    expect(scanLocalAccounts).toHaveBeenCalled()
    await closeDrawer()

    await pickAddMode('手动添加')
    expect(document.querySelector('.n-drawer-header__main')!.textContent).toContain('手动添加账号 · WorkBuddy')
    expect(drawerText()).toContain('凭证 JSON')
    await fill(document.querySelector('.n-drawer textarea'), '{"uid":"u9","auth":{}}')
    await clickIn(drawerEl()!, '添加账号')
    expect(addAccountManually).toHaveBeenCalledWith({ name: '', auth_json: '{"uid":"u9","auth":{}}' })
    await waitFor(() => toastText().includes('已添加'), '添加 toast')
  })

  it('导入预览区分有效/无法解析，只提交勾选的有效行', async () => {
    await mountPage()
    await openDrawerByHeader('导入')
    await fill(
      document.querySelector('.n-drawer textarea'),
      ['{"uid":"u1","auth":{}}', '{"uid":"u2","auth":{}}', 'not-json'].join('\n'),
    )
    await waitFor(() => drawerText().includes('有效 2 条'), '导入解析')
    expect(drawerText()).toContain('无法解析 1 条')
    const rows = Array.from(document.querySelectorAll('.acc-scan__row')).map((el) => el.textContent || '')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toContain('u1')
    expect(rows[2]).toContain('JSON 解析失败')

    const first = document.querySelector('.acc-scan__row .n-checkbox') as HTMLElement
    first.click()
    await settle()
    expect(drawerText()).toContain('导入 1 个')
    await clickIn(drawerEl()!, '导入 1 个')

    expect(importAccountsAsync).toHaveBeenCalledTimes(1)
    const payload = importAccountsAsync.mock.calls[0][0]
    expect(payload.storage).toBe('workbuddy')
    expect(payload.text).toBe('')
    expect(payload.verify).toBe(true)
    expect(payload.items).toHaveLength(1)
    expect(payload.items![0].auth_json).toContain('u2')
    await waitFor(() => Boolean(document.querySelector('.acc-jobbar')), '导入任务条')
  })

  it('扫描预览默认只勾选可导入项，提交调用 importLocalAccounts', async () => {
    await mountPage()
    scanLocalAccounts.mockResolvedValue({
      dir: 'C:/Users/me/AppData/Roaming/workbuddy/data',
      exists: true,
      active_uid: 'u2',
      items: [
        { file: 'a.info', uid: 'u1', nickname: '工作号 A' },
        { file: 'b.info', uid: 'u2', nickname: '工作号 B', in_pool: true },
        { file: 'c.info', uid: 'u3', error: '凭据已损坏' },
      ],
      pool_total: 3,
      pool_only: 1,
    } as never)
    await openDrawerByHeader('扫描本机')
    await waitFor(() => drawerText().includes('发现 3 个客户端账号'), '扫描预览 3 行')
    expect(drawerText()).toContain('已在池子里')
    expect(drawerText()).toContain('凭据已损坏')
    expect(drawerText()).toContain('导入选中 1 个')
    await clickIn(drawerEl()!, '导入选中 1 个')
    expect(importLocalAccounts).toHaveBeenCalledWith(['a.info'])
    await waitFor(() => toastText().includes('已导入'), '导入本机 toast')
    expect(toastText()).toContain('新增 1')
  })
})

/* -------------------------------------------------------------------------- */
/* 手机号 / 浏览器授权两种平台能力模式                                          */
/* -------------------------------------------------------------------------- */

describe('AccountsPage · 平台授权方式', () => {
  it('Loomy 手机号登录：前端校验 + 发送验证码 + 提交入池', async () => {
    await mountPage({ specs: [platform({ id: 'loomy', label: 'Loomy', storage_platform: 'loomy', supports_local_scan: false })] })
    await pickAddMode('手机号登录')
    expect(document.querySelector('.n-drawer-header__main')!.textContent).toContain('手机号登录 · Loomy')

    await clickIn(drawerEl()!, '获取验证码')
    expect(drawerText()).toContain('请填写手机号')
    expect(sendPhoneCode).not.toHaveBeenCalled()

    await fill(document.querySelector('.n-drawer input[placeholder="手机号"]'), '13800138000')
    await clickIn(drawerEl()!, '获取验证码')
    expect(preparePhoneFlow).toHaveBeenCalled()
    expect(sendPhoneCode).toHaveBeenCalledTimes(1)
    await waitFor(() => drawerText().includes('秒后重发'), '验证码倒计时')

    await fill(document.querySelector('.n-drawer input[placeholder="验证码"]'), '1234')
    await clickIn(drawerEl()!, '登录并添加')
    expect(verifyPhoneCode).toHaveBeenCalledTimes(1)
    const form = verifyPhoneCode.mock.calls[0][1]
    expect(form).toMatchObject({ country: '+86', phone: '13800138000', code: '1234' })
    await waitFor(() => toastText().includes('已登录并添加账号'), '登录 toast')
  })

  it('Marvis 浏览器授权：抽屉展示三步步骤条，底部按钮可发起授权', async () => {
    await mountPage({ specs: [platform({ id: 'marvis', label: 'Marvis', storage_platform: 'marvis', supports_local_scan: false, auth_mode: 'qr-auth' })] })
    await pickAddMode('浏览器授权登录')
    expect(document.querySelector('.n-drawer-header__main')!.textContent).toContain('浏览器授权登录 · Marvis')
    const steps = Array.from(document.querySelectorAll('.acc-steps li')).map((el) => el.textContent || '')
    expect(steps).toHaveLength(3)
    expect(steps[0]).toContain('打开授权地址')
    expect(steps[2]).toContain('账号入池')
    expect(drawerText()).toContain('当前状态：尚未发起')
    beginBrowserAuth.mockResolvedValue({ state: 'pending', url: 'https://example.com/auth', account_id: 0 } as never)
    const footer = drawerEl()!.querySelector('.n-drawer-footer')
    expect(footer).not.toBeNull()
    const start = Array.from(footer!.querySelectorAll('button')).find((el) => (el.textContent || '').includes('发起授权'))
    expect(start, '发起授权按钮').toBeTruthy()
    start!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(beginBrowserAuth).toHaveBeenCalledTimes(1)
  })
})
