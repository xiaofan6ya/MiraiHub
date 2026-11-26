/**
 * i18n 批次 D：成长计划 / 智能体两页中英切换 + failureText 分隔符。
 *
 * GrowthPage / AgentPage 是最后两页带硬编码文案的页面：先用默认中文（逐字不变）挂载断言，
 * 再切 en-US 断言整页换成英文并确认页面文案里不再残留中文，最后切回中文确认还原。
 *
 * failureText 的分隔符跟随语言：中文保持「失败：」，英文用 " failed: "，默认中文逐字不变。
 * 网络层全部 mock 在 api 模块上；页面里的 t() 走真实 useI18n。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import { setLocale, t } from '@/i18n'
import { zhCN } from '@/i18n/messages/zh-CN'
import { enUS } from '@/i18n/messages/en-US'
import { failureText } from '@/utils/error'

vi.mock('@/api/growth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/growth')>()
  return {
    ...actual,
    getGrowthAccounts: vi.fn(),
    getAccountTasks: vi.fn(),
    claimGrowth: vi.fn(),
    runGrowthTasks: vi.fn(),
    postGrowthAction: vi.fn(),
    refreshGrowthCenter: vi.fn(),
  }
})

vi.mock('@/api/agent', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/agent')>()
  return {
    ...actual,
    getBootstrap: vi.fn(),
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    listAssets: vi.fn(),
    estimateCredits: vi.fn(),
    mergeAssets: vi.fn(),
    chatStream: vi.fn(),
  }
})

const growthApi = await import('@/api/growth')
const agentApi = await import('@/api/agent')

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template:
    '<n-config-provider><n-message-provider :duration="0"><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>',
})

let wrapper: VueWrapper | null = null

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
  await nextTick()
}

async function mountRoute(component: unknown, path: string): Promise<string> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path, name: path.replace('/', ''), component: component as never },
      { path: '/accounts', name: 'accounts', component: { template: '<div>accounts</div>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  wrapper = mount(Host, { global: { plugins: [createPinia(), router] }, attachTo: document.body })
  await settle()
  await settle()
  return wrapperText()
}

/** 只取组件树内的文案（Toast 是取数那一刻的快照，留在 body 里会干扰断言）。 */
function wrapperText(): string {
  return wrapper ? wrapper.text() : ''
}

async function textAfterLocale(locale: 'zh-CN' | 'en-US'): Promise<string> {
  setLocale(locale)
  await nextTick()
  await flushPromises()
  return wrapperText()
}

/* -------------------------------------------------------------------------- */
/* fixtures                                                                   */
/* -------------------------------------------------------------------------- */

const ACCOUNTS = {
  items: [{ id: 12, name: '193****2486', status: 'active', streak_days: 6, energy: 320 }],
  excluded_enterprise: 2,
}

const TASKS = {
  account_id: 12,
  name: '193****2486',
  tasks: [
    {
      task_code: 'chat_request_send',
      title: '发送一条对话',
      reward_credit: 50,
      accept_status: 'completed',
      progress: { current: 3, target: 3 },
      level: 'auto',
      level_label: '可自动',
      actionable: true,
    },
  ],
  profile: { level: 3, level_name: 'Lv.3', completed: 1, total: 1, max_level: 10 },
  summary: { total: 1, done: 1, claimable: 1, claimable_credit: 50, actionable: 1 },
}

const BOOTSTRAP = {
  key: { name: 'main', masked: 'sk-***' },
  models: { chat: ['gpt-4o'], image: [], video: [] },
  video: [],
  estimates: {},
  presets: {},
}

const CONVERSATIONS = {
  items: [
    {
      conversation_id: 'c-1',
      title: '生成城市夜景视频',
      model: 'gpt-4o',
      mode: 'chat',
      reasoning: '',
      thinking: null,
      pinned: false,
      pinned_at: '',
      updated_at: '2026-11-23T15:00:00Z',
      created_at: '2026-11-23T15:00:00Z',
    },
  ],
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.mocked(growthApi.getGrowthAccounts).mockResolvedValue(ACCOUNTS as never)
  vi.mocked(growthApi.getAccountTasks).mockResolvedValue(TASKS as never)
  vi.mocked(agentApi.getBootstrap).mockResolvedValue(BOOTSTRAP as never)
  vi.mocked(agentApi.listConversations).mockResolvedValue(CONVERSATIONS as never)
  vi.mocked(agentApi.listAssets).mockResolvedValue({ items: [] } as never)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setLocale('zh-CN')
  document.body.innerHTML = ''
})

/* -------------------------------------------------------------------------- */
/* 两页                                                                        */
/* -------------------------------------------------------------------------- */

describe('i18n 批次 D：成长计划 / 智能体中英切换', () => {
  it('GrowthPage：默认中文，切英文后页头、统计条与卡片变英文', async () => {
    const { default: GrowthPage } = await import('@/pages/GrowthPage.vue')
    const zh = await mountRoute(GrowthPage, '/growth')
    expect(zh).toContain('成长计划')
    expect(zh).toContain('全部领奖')
    expect(zh).toContain('任务完成')
    expect(zh).toContain('做任务')
    expect(zh).toContain('已排除 2 个企业版账号')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Growth plan')
    expect(en).toContain('Claim all')
    expect(en).toContain('Tasks')
    expect(en).toContain('Run tasks')
    expect(en).toContain('2 enterprise accounts excluded')
    expect(en).not.toContain('成长计划')
    expect(en).not.toContain('全部领奖')
    expect(en).not.toContain('任务清单')

    expect(await textAfterLocale('zh-CN')).toContain('全部领奖')
  })

  it('AgentPage：默认中文，切英文后页头、会话栏与参数卡变英文', async () => {
    const { default: AgentPage } = await import('@/pages/AgentPage.vue')
    const zh = await mountRoute(AgentPage, '/agent')
    expect(zh).toContain('智能体')
    expect(zh).toContain('新会话')
    expect(zh).toContain('开始创作')
    expect(zh).toContain('模型与参数')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Agent')
    expect(en).toContain('New chat')
    expect(en).toContain('Start creating')
    expect(en).toContain('Model and parameters')
    expect(en).not.toContain('新会话')
    expect(en).not.toContain('模型与参数')

    expect(await textAfterLocale('zh-CN')).toContain('新会话')
  })
})

/* -------------------------------------------------------------------------- */
/* failureText：分隔符跟随语言                                                  */
/* -------------------------------------------------------------------------- */

describe('failureText 分隔符', () => {
  it('中文用「失败：」，英文用 " failed: "，默认中文逐字不变', () => {
    expect(failureText('加载成长账号', new Error('上游超时'))).toBe('加载成长账号失败：上游超时')
    expect(failureText('加载成长账号', '上游超时')).toBe('加载成长账号失败：上游超时')
    expect(t('common.failure', { action: '加载成长账号', reason: '上游超时' })).toBe(
      '加载成长账号失败：上游超时',
    )

    setLocale('en-US')
    expect(failureText('Load growth accounts', new Error('upstream timeout'))).toBe(
      'Load growth accounts failed: upstream timeout',
    )
    expect(t('common.failure', { action: 'Load growth accounts', reason: 'upstream timeout' })).toBe(
      'Load growth accounts failed: upstream timeout',
    )

    setLocale('zh-CN')
    expect(failureText('领取奖励', new Error('上游拒绝了领奖'))).toBe('领取奖励失败：上游拒绝了领奖')
  })
})

/* -------------------------------------------------------------------------- */
/* 词条本身：中文真值逐字不变 + 英文不残留中文                                  */
/* -------------------------------------------------------------------------- */

const NAMESPACES = ['growth', 'agent']

describe('i18n 批次 D：词条真值', () => {
  it('两页中文词条齐全，英文词条全部是英文', () => {
    const zhKeys = Object.keys(zhCN)
    const enKeys = Object.keys(enUS)
    expect(enKeys.length).toBe(zhKeys.length)

    for (const ns of NAMESPACES) {
      const prefix = 'page.' + ns + '.'
      const zhPage = zhKeys.filter((key) => key.startsWith(prefix))
      expect(zhPage.length, ns + ' 应有多条词条').toBeGreaterThan(20)
      for (const key of zhPage) {
        expect(zhCN[key as keyof typeof zhCN], key + ' 中文缺失').toBeTruthy()
        const en = enUS[key as keyof typeof enUS]
        expect(en, key + ' 英文缺失').toBeTruthy()
        expect(/[\u4e00-\u9fff]/.test(en), key + ' 英文不应含中文：' + en).toBe(false)
      }
    }
  })

  it('默认中文逐字不变（抽查页头、按钮与失败模板）', () => {
    const anchors: Array<[string, string]> = [
      ['page.growth.title', '成长计划 · {platform}'],
      ['page.growth.claimAll', '全部领奖'],
      ['page.growth.tasks.title', '任务清单（{done}/{total}）'],
      ['page.growth.pager.text', '第 {page} / {pageCount} 页 · 共 {total} 个账号'],
      ['page.agent.title', '智能体'],
      ['page.agent.newChat', '新会话'],
      ['page.agent.confirm.deleteTitle', '删除会话「{title}」？'],
      ['common.failure', '{action}失败：{reason}'],
    ]
    for (const [key, text] of anchors) {
      expect(zhCN[key as keyof typeof zhCN], key).toBe(text)
      setLocale('zh-CN')
      expect(t(key as never), key).toBe(text)
    }
  })
})
