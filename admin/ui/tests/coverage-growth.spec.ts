import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NDialogProvider, NDropdown, NMessageProvider, NSelect } from 'naive-ui'
import { defineComponent, h } from 'vue'
import GrowthPage from '@/pages/GrowthPage.vue'
import { usePlatformStore } from '@/stores/platform'
import type { GrowthAccountTasksPayload, GrowthCenterAccount, GrowthTask } from '@/api/growth'

// 真实 useJob 的 snapshot/percent 是 ref；GrowthPage 把它们放进深层 reactive 的
// jobHandles 后会自动解包，再读 .value 会抛 TypeError（src 既有 bug，本任务不改 src）。
// 这里注入「普通对象 + value」的假 job 句柄，只覆盖 JobProgress 的渲染与隐藏路径。
vi.mock('@/composables', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/composables')>()
  return {
    ...actual,
    useJob: (id: string) => ({
      snapshot: { value: { job_id: id, status: 'running', percent: 42, current: '执行中', result: { tasks_done: 1, failed: [] } } },
      percent: { value: 42 },
      status: { value: 'running' },
      isRunning: { value: true },
      finished: { value: false },
      failed: { value: false },
      polling: { value: true },
      error: { value: null },
      start: () => undefined,
      stop: () => undefined,
    }),
  }
})

/**
 * GrowthPage 交互覆盖率测试（S24）。
 *
 * 只 mock 浏览器 fetch（页面通过 @/api/http 走它），不真实调用上游。
 * 覆盖：首屏渲染 / 搜索 / 状态筛选 / 清空筛选 / 分页 / 任务展开 / 单任务领奖 /
 * 账号领奖 / 全部领奖确认与取消 / 做任务 + JobProgress / 成长动作菜单 /
 * 刷新成长状态 / 任务清单失败重试 / 账号列表失败重试 / 平台不支持 / 空结果。
 */

interface StubResponse {
  status?: number
  body: unknown
}

type Handler = (url: string, method: string, body: unknown) => StubResponse | undefined

interface FetchCall {
  url: string
  method: string
  body: Record<string, unknown> | undefined
}

const calls: FetchCall[] = []

function jsonResponse(body: unknown, status = 200) {
  const text = JSON.stringify(body)
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (key: string) => (key.toLowerCase() === 'content-type' ? 'application/json' : null) },
    text: async () => text,
  }
}

function installFetch(handler: Handler): void {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : String((input as { url?: string })?.url || input)
      const method = String(init?.method || 'GET').toUpperCase()
      let parsed: Record<string, unknown> | undefined
      if (typeof init?.body === 'string') {
        try {
          parsed = JSON.parse(init.body) as Record<string, unknown>
        } catch {
          parsed = undefined
        }
      }
      calls.push({ url, method, body: parsed })
      const result = handler(url, method, parsed)
      if (!result) return jsonResponse({ detail: 'unmocked ' + method + ' ' + url }, 404)
      return jsonResponse(result.body, result.status ?? 200)
    }),
  )
}

function callsTo(fragment: string, method = 'GET'): FetchCall[] {
  return calls.filter((call) => call.url.includes(fragment) && call.method === method)
}

/* ------------------------------------------------------------------ *
 * 数据形状（与 api/growth.ts 的类型一致）
 * ------------------------------------------------------------------ */

const ACCOUNTS: GrowthCenterAccount[] = [
  { id: 12, name: '193****2486', status: 'active', streak_days: 6, energy: 320 },
  { id: 13, name: '188****0911', status: 'disabled', streak_days: null, energy: null },
  { id: 14, name: '177****3322', status: 'active', streak_days: 2, energy: 10 },
]

function tasksFor(id: number, tasks: GrowthTask[], summary: Partial<GrowthAccountTasksPayload['summary']> = {}): GrowthAccountTasksPayload {
  const base = { total: tasks.length, done: 0, claimable: 0, claimable_credit: 0, actionable: 0 }
  return {
    account_id: id,
    name: '账号' + id,
    tasks,
    profile: { level: 3, level_name: 'Lv.3', completed: 1, total: 4, max_level: 10 },
    summary: { ...base, ...summary },
  }
}

const GROWTH_ACTION_KEYS: Record<string, string> = {
  '每日打卡': 'checkin',
  '开启盲盒': 'open',
  '领取礼物': 'gift',
  '限时抽奖': 'draw',
  '连登兑换 · 7 天': 'redeem-7',
  '刷新成长状态': 'refresh-center',
  '打开任务清单': 'open-tasks',
}

const FIVE_TASKS: GrowthTask[] = [
  { task_code: 'chat_request_send', title: '发送一条对话', reward_credit: 50, accept_status: 'completed', progress: { current: 3, target: 3 }, level: 'auto', level_label: '可自动', actionable: true, strategy: '发一次请求即完成' },
  { task_code: 'first_buddy', title: '孵化第一只宠物', reward_credit: 100, accept_status: 'not_accepted', level: 'manual', level_label: '需人工', actionable: false },
  { task_code: 'checkin_daily', title: '每日打卡', reward_credit: 20, accept_status: 'claimed', level: 'auto', level_label: '可自动', actionable: true },
  { task_code: 'paid_trial', title: '付费试用', reward_credit: 0, accept_status: 'not_accepted', level: 'skip', level_label: '跳过', actionable: false },
  { task_code: 'streak_7', title: '连续登录 7 天', reward_credit: 30, accept_status: 'accepted', level: 'auto', level_label: '可自动', actionable: true, progress: { current: 2, target: 7 } },
]

function defaultHandler(overrides: Partial<Record<string, Handler>> = {}): Handler {
  return (url, method, body) => {
    if (overrides.accounts && url.includes('/api/growth-center/accounts') && method === 'GET') {
      const hit = overrides.accounts(url, method, body)
      if (hit) return hit
    }
    if (method === 'GET' && url.split('?')[0].endsWith('/api/growth-center/accounts')) {
      return { body: { items: ACCOUNTS, excluded_enterprise: 2 } }
    }
    const taskMatch = /\/api\/growth\/accounts\/(\d+)\/tasks/.exec(url)
    if (method === 'GET' && taskMatch) {
      const id = Number(taskMatch[1])
      const override = overrides.tasks
      const hit = override ? override(url, method, body) : undefined
      if (hit) return hit
      const payload =
        id === 12
          ? tasksFor(12, FIVE_TASKS, { total: 5, done: 2, claimable: 1, claimable_credit: 50, actionable: 1 })
          : id === 13
            ? tasksFor(13, [], { total: 0, done: 0, claimable: 0, claimable_credit: 0, actionable: 0 })
            : tasksFor(14, [FIVE_TASKS[4]], { total: 2, done: 1, claimable: 0, claimable_credit: 0, actionable: 1 })
      return { body: payload }
    }
    if (method === 'POST' && url.includes('/api/growth/claim')) {
      const hit = overrides.claim ? overrides.claim(url, method, body) : undefined
      if (hit) return hit
      const ids = Array.isArray((body as { account_ids?: unknown })?.account_ids) ? ((body as { account_ids: number[] }).account_ids) : [12]
      return {
        body: {
          results: ids.map((id) => ({
            account_id: id,
            name: '账号' + id,
            ok: true,
            total_credit: 50,
            claimed: [{ task_code: 'chat_request_send', ok: true, credit: 50 }],
          })),
        },
      }
    }
    if (method === 'POST' && url.includes('/api/growth/run-async')) {
      const hit = overrides.run ? overrides.run(url, method, body) : undefined
      if (hit) return hit
      return { body: { job_id: 'job-1', status: 'running', reused: false } }
    }
    if (method === 'GET' && url.includes('/api/growth/job/')) {
      const hit = overrides.job ? overrides.job(url, method, body) : undefined
      if (hit) return hit
      return { body: { job_id: 'job-1', status: 'running', percent: 42, current: '执行中', result: { tasks_done: 1, failed: [] } } }
    }
    if (method === 'POST' && /\/api\/growth-center\/accounts\/\d+\/action/.test(url)) {
      const hit = overrides.action ? overrides.action(url, method, body) : undefined
      if (hit) return hit
      return { body: { ok: true, reason: '打卡成功', credit: 10 } }
    }
    if (method === 'POST' && /\/api\/growth-center\/accounts\/\d+\/refresh/.test(url)) {
      const hit = overrides.refresh ? overrides.refresh(url, method, body) : undefined
      if (hit) return hit
      return { body: { ok: true, running: false } }
    }
    return undefined
  }
}

/* ------------------------------------------------------------------ *
 * 挂载助手
 * ------------------------------------------------------------------ */

interface MountedPage {
  wrapper: VueWrapper
  router: Router
  pinia: Pinia
  unmount(): void
}

async function mountGrowth(options: { pinia?: Pinia; platform?: string; withoutPlatforms?: boolean } = {}): Promise<MountedPage> {
  const pinia = options.pinia || createPinia()
  if (options.platform) {
    const store = usePlatformStore(pinia)
    store.items = options.withoutPlatforms ? [] : store.items
    store.platform = options.platform
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/accounts', name: 'accounts', component: { template: '<div>accounts</div>' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const Host = defineComponent({
    setup() {
      return () =>
        h(NMessageProvider, { max: 12 }, {
          default: () => h(NDialogProvider, null, { default: () => h(GrowthPage) }),
        })
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await flushPromises()
  await flushPromises()
  return {
    wrapper,
    router,
    pinia,
    unmount() {
      wrapper.unmount()
    },
  }
}

function bodyText(): string {
  return String(document.body.textContent || '')
}

type ButtonHost = {
  findAll(selector: string): Array<{ text(): string; trigger(event: string): Promise<void> }>
}

function buttonByText(host: ButtonHost, text: string, selector = 'button') {
  return host.findAll(selector).find((node) => (node.text() || '').includes(text))
}

async function clickDialog(text: string): Promise<void> {
  await flushPromises()
  const buttons = Array.from(document.querySelectorAll('.n-dialog button, .n-dialog__action button')) as HTMLElement[]
  const target = [...buttons].reverse().find((node) => String(node.textContent || '').includes(text))
  if (!target) throw new Error('dialog button not found: ' + text + ' / ' + bodyText().slice(0, 200))
  target.click()
  await flushPromises()
}

async function selectDropdown(wrapper: VueWrapper, cardIndex: number, label: string): Promise<void> {
  const triggers = wrapper
    .findAll('button')
    .filter((node) => node.attributes('aria-label') === '更多成长操作')
  const trigger = triggers[cardIndex]
  if (!trigger) throw new Error('dropdown trigger missing at ' + cardIndex)
  await trigger.trigger('click')
  await flushPromises()
  // 菜单本身是 teleport + 过渡，jsdom 里不一定完成渲染；直接断言 NDropdown 的
  // options 已包含目标项，再发 select —— 覆盖「菜单项 → 动作」的接线。
  const dropdowns = wrapper.findAllComponents(NDropdown)
  expect(dropdowns.length).toBeGreaterThan(cardIndex)
  const options = (dropdowns[cardIndex].props('options') || []) as Array<{ label?: string; key?: string }>
  expect(options.some((item) => String(item.label || '').includes(label))).toBe(true)
  dropdowns[cardIndex].vm.$emit('select', GROWTH_ACTION_KEYS[label] || label)
  await flushPromises()
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('GrowthPage 首屏与筛选交互', () => {
  it('渲染统计条、账号卡、任务状态与排除企业版脚注', async () => {
    installFetch(defaultHandler())
    const page = await mountGrowth()
    const text = page.wrapper.text()

    expect(text).toContain('成长计划')
    expect(text).toContain('193****2486')
    expect(text).toContain('188****0911')
    expect(text).toContain('177****3322')
    // 统计条 6 格
    expect(text).toContain('任务总数')
    expect(text).toContain('可领奖励')
    expect(text).toContain('能量合计')
    // 卡片任务状态：可领取 / 已领取 / 未参与 / 可自动
    expect(text).toContain('可领取')
    expect(text).toContain('未参与')
    expect(text).toContain('可自动')
    // 禁用账号与「可做」判断
    expect(text).toContain('已禁用')
    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(3)
    expect(text).toContain('已排除 2 个企业版账号')

    page.unmount()
  })

  it('搜索账号名后只剩匹配卡片，清除筛选恢复全部', async () => {
    installFetch(defaultHandler())
    const page = await mountGrowth()

    const input = page.wrapper.find('input[placeholder="搜索账号"]')
    expect(input.exists()).toBe(true)
    await input.setValue('2486')
    await flushPromises()

    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(1)
    expect(page.wrapper.text()).toContain('193****2486')
    expect(page.wrapper.text()).not.toContain('177****3322')
    expect(page.router.currentRoute.value.query.q).toBe('2486')

    const clear = buttonByText(page.wrapper, '重置')
    expect(clear).toBeTruthy()
    await clear!.trigger('click')
    await flushPromises()
    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(3)
    expect(page.router.currentRoute.value.query.q).toBeUndefined()

    page.unmount()
  })

  it('状态筛选：只显示可领奖励的账号，空结果可清除筛选恢复', async () => {
    installFetch(defaultHandler())
    const page = await mountGrowth()

    const select = page.wrapper.findComponent(NSelect)
    expect(select.exists()).toBe(true)
    select.vm.$emit('update:value', 'claimable')
    await flushPromises()

    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(1)
    expect(page.wrapper.text()).toContain('193****2486')
    expect(page.router.currentRoute.value.query.state).toBe('claimable')

    select.vm.$emit('update:value', 'disabled')
    await flushPromises()
    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(1)
    expect(page.wrapper.text()).toContain('188****0911')

    // 搜一个不存在的名字 → 空结果 + 清除筛选按钮
    await page.wrapper.find('input[placeholder="搜索账号"]').setValue('nobody')
    await flushPromises()
    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(0)
    expect(page.wrapper.text()).toContain('没有符合条件的账号。')
    const clearButton = buttonByText(page.wrapper, '清除筛选')
    expect(clearButton).toBeTruthy()
    await clearButton!.trigger('click')
    await flushPromises()
    expect(page.wrapper.findAll('article.gw-card').length).toBeGreaterThan(0)

    page.unmount()
  })

  it('账号多于每页条数时显示分页，点下一页同步 query 并切换卡片', async () => {
    const many: GrowthCenterAccount[] = Array.from({ length: 21 }, (_, index) => ({
      id: 100 + index,
      name: '账号' + String(index + 1).padStart(2, '0'),
      status: 'active',
      streak_days: 1,
      energy: 5,
    }))
    installFetch((url, method, body) => {
      if (method === 'GET' && /\/api\/growth-center\/accounts$/.test(url)) {
        return { body: { items: many, excluded_enterprise: 0 } }
      }
      const taskMatch = /\/api\/growth\/accounts\/(\d+)\/tasks/.exec(url)
      if (method === 'GET' && taskMatch) {
        return { body: tasksFor(Number(taskMatch[1]), [], { total: 0 }) }
      }
      return defaultHandler()(url, method, body)
    })
    const page = await mountGrowth()

    const cardNames = () => page.wrapper.findAll('article.gw-card h3').map((node) => node.text())
    expect(page.wrapper.text()).toContain('第 1 / 2 页')
    expect(cardNames()).toHaveLength(20)
    expect(cardNames()).toContain('账号01')
    expect(cardNames()).not.toContain('账号21')

    const next = buttonByText(page.wrapper, '下一页')
    expect(next).toBeTruthy()
    await next!.trigger('click')
    await flushPromises()
    expect(page.router.currentRoute.value.query.page).toBe('2')
    expect(cardNames()).toEqual(['账号21'])
    expect(page.wrapper.text()).toContain('第 2 / 2 页')

    await buttonByText(page.wrapper, '上一页')!.trigger('click')
    await flushPromises()
    expect(page.wrapper.text()).toContain('账号01')

    page.unmount()
  })

  it('任务超过 4 条时可展开全部/收起', async () => {
    installFetch(defaultHandler())
    const page = await mountGrowth()

    const firstCard = page.wrapper.findAll('article.gw-card')[0]
    expect(firstCard.findAll('li.gw-task')).toHaveLength(4)
    const expand = buttonByText(firstCard, '展开全部')
    expect(expand).toBeTruthy()
    await expand!.trigger('click')
    expect(firstCard.findAll('li.gw-task')).toHaveLength(5)
    expect(firstCard.text()).toContain('已领取')
    expect(firstCard.text()).toContain('收起')

    await buttonByText(firstCard, '收起')!.trigger('click')
    expect(firstCard.findAll('li.gw-task')).toHaveLength(4)

    page.unmount()
  })
})

describe('GrowthPage 领奖与动作', () => {
  it('单任务领奖提交 task_codes 并刷新该账号任务', async () => {
    installFetch(defaultHandler())
    const page = await mountGrowth()

    const firstCard = page.wrapper.findAll('article.gw-card')[0]
    const claimTask = buttonByText(firstCard, '领奖')
    expect(claimTask).toBeTruthy()
    await claimTask!.trigger('click')
    await flushPromises()

    const posts = callsTo('/api/growth/claim', 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toEqual({ account_ids: [12], task_codes: ['chat_request_send'] })
    // 成功后刷新该账号任务清单（refresh=1）
    expect(callsTo('/api/growth/accounts/12/tasks?refresh=1', 'GET').length).toBeGreaterThan(0)
    expect(bodyText()).toContain('已领取 1 个任务，共 50 积分')

    page.unmount()
  })

  it('账号领奖按钮提交 account_ids，失败时展示错误 Toast', async () => {
    let fail = true
    installFetch(
      defaultHandler({
        claim: () => (fail ? { status: 500, body: { detail: '上游拒绝了领奖' } } : undefined),
      }),
    )
    const page = await mountGrowth()

    const firstCard = page.wrapper.findAll('article.gw-card')[0]
    const claimAccount = buttonByText(firstCard, '领奖 1')
    expect(claimAccount).toBeTruthy()
    await claimAccount!.trigger('click')
    await flushPromises()

    expect(callsTo('/api/growth/claim', 'POST')[0].body).toEqual({ account_ids: [12] })
    expect(bodyText()).toContain('领取奖励失败：上游拒绝了领奖')

    fail = false
    await claimAccount!.trigger('click')
    await flushPromises()
    expect(bodyText()).toContain('已领取 1 个任务，共 50 积分')

    page.unmount()
  })

  it('全部领奖需要确认：取消不发请求，确认后按可领账号提交', async () => {
    installFetch(defaultHandler())
    const page = await mountGrowth()

    const claimAll = buttonByText(page.wrapper, '全部领奖')
    expect(claimAll).toBeTruthy()
    await claimAll!.trigger('click')
    await flushPromises()
    expect(bodyText()).toContain('领取全部可领奖励？')
    await clickDialog('取消')
    expect(callsTo('/api/growth/claim', 'POST')).toHaveLength(0)

    await claimAll!.trigger('click')
    await flushPromises()
    await clickDialog('全部领取')
    const posts = callsTo('/api/growth/claim', 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toEqual({ account_ids: [12] })
    expect(bodyText()).toContain('已领取')

    page.unmount()
  })

  it('做任务提交后台任务并显示 JobProgress，可隐藏卡片', async () => {
    installFetch(defaultHandler())
    const page = await mountGrowth()

    const firstCard = page.wrapper.findAll('article.gw-card')[0]
    const run = buttonByText(firstCard, '做任务')
    expect(run).toBeTruthy()
    await run!.trigger('click')
    await flushPromises()

    expect(callsTo('/api/growth/run-async', 'POST')[0].body).toEqual({ account_ids: [12] })
    expect(page.wrapper.find('.mh-jobs').exists()).toBe(true)
    expect(page.wrapper.text()).toContain('进行中')
    expect(page.wrapper.text()).toContain('42%')
    expect(bodyText()).toContain('做任务已提交后台执行')

    const hide = buttonByText(page.wrapper, '隐藏')
    expect(hide).toBeTruthy()
    await hide!.trigger('click')
    await flushPromises()
    expect(page.wrapper.find('.mh-jobs').exists()).toBe(false)

    page.unmount()
  })

  it('更多菜单：每日打卡提交 action 并 Toast；刷新成长状态提交 refresh', async () => {
    installFetch(defaultHandler())
    const page = await mountGrowth()

    await selectDropdown(page.wrapper, 0, '每日打卡')
    const actions = callsTo('/api/growth-center/accounts/12/action', 'POST')
    expect(actions).toHaveLength(1)
    expect(actions[0].body).toEqual({ action: 'checkin' })
    expect(bodyText()).toContain('每日打卡：打卡成功（+10 积分）')
    expect(callsTo('/api/growth/accounts/12/tasks?refresh=1', 'GET').length).toBeGreaterThan(0)

    // 盲盒 / 连登兑换（tier）走同一个 action 入口：各自提交正确的 action/tier
    await selectDropdown(page.wrapper, 0, '开启盲盒')
    await selectDropdown(page.wrapper, 0, '连登兑换 · 7 天')
    const allActions = callsTo('/api/growth-center/accounts/12/action', 'POST')
    expect(allActions.map((call) => call.body)).toEqual([
      { action: 'checkin' },
      { action: 'open' },
      { action: 'redeem', tier: '7' },
    ])

    await selectDropdown(page.wrapper, 0, '刷新成长状态')
    expect(callsTo('/api/growth-center/accounts/12/refresh', 'POST')).toHaveLength(1)
    expect(bodyText()).toContain('已发起刷新')

    page.unmount()
  })
})

describe('GrowthPage 失败态与平台能力', () => {
  it('任务清单失败时该卡显示错误与重试，重试成功后渲染任务', async () => {
    const seen = new Set<number>()
    installFetch(
      defaultHandler({
        tasks: (url) => {
          const match = /\/api\/growth\/accounts\/(\d+)\/tasks/.exec(url)
          const id = match ? Number(match[1]) : 0
          if (id === 12 && !seen.has(id)) {
            seen.add(id)
            return { status: 502, body: { detail: '上游超时' } }
          }
          return undefined
        },
      }),
    )
    const page = await mountGrowth()

    const firstCard = page.wrapper.findAll('article.gw-card')[0]
    expect(firstCard.text()).toContain('加载任务清单失败：上游超时')
    const retry = buttonByText(firstCard, '重试')
    expect(retry).toBeTruthy()
    await retry!.trigger('click')
    await flushPromises()

    expect(page.wrapper.findAll('article.gw-card')[0].text()).not.toContain('加载任务清单失败')
    expect(page.wrapper.findAll('article.gw-card')[0].text()).toContain('发送一条对话')

    page.unmount()
  })

  it('账号列表失败显示 ErrorBlock，重试成功后渲染卡片', async () => {
    let fail = true
    installFetch(
      defaultHandler({
        accounts: () => (fail ? { status: 500, body: { detail: '数据库连接失败' } } : undefined),
      }),
    )
    const page = await mountGrowth()

    expect(page.wrapper.text()).toContain('加载成长账号失败：数据库连接失败')
    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(0)

    fail = false
    const retry = buttonByText(page.wrapper, '重试')
    expect(retry).toBeTruthy()
    await retry!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(3)
    expect(page.wrapper.text()).toContain('193****2486')

    page.unmount()
  })

  it('当前平台不支持成长计划时显示 Empty，可跳账号池', async () => {
    installFetch(defaultHandler())
    const pinia = createPinia()
    const store = usePlatformStore(pinia)
    store.platform = 'trae'
    const page = await mountGrowth({ pinia })

    expect(page.wrapper.text()).toContain('当前平台没有成长计划')
    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(0)
    expect(callsTo('/api/growth-center/accounts', 'GET')).toHaveLength(0)

    const go = buttonByText(page.wrapper, '去账号池')
    expect(go).toBeTruthy()
    await go!.trigger('click')
    await flushPromises()
    expect(page.router.currentRoute.value.path).toBe('/accounts')

    page.unmount()
  })

  it('没有账号时显示空状态与刷新入口', async () => {
    installFetch((url, method, body) => {
      if (method === 'GET' && /\/api\/growth-center\/accounts$/.test(url)) {
        return { body: { items: [], excluded_enterprise: 0 } }
      }
      return defaultHandler()(url, method, body)
    })
    const page = await mountGrowth()

    expect(page.wrapper.text()).toContain('当前平台还没有可管理的成长账号。')
    expect(page.wrapper.findAll('article.gw-card')).toHaveLength(0)
    const refresh = buttonByText(page.wrapper, '刷新')
    expect(refresh).toBeTruthy()
    await refresh!.trigger('click')
    await flushPromises()
    expect(callsTo('/api/growth-center/accounts', 'GET').length).toBeGreaterThan(1)

    page.unmount()
  })
})