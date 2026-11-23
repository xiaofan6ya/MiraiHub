/**
 * 覆盖率补充：JournalPage 首屏统计（QA 缺陷 ③）。
 *
 * 数据没回来时不能写「共 0 条」—— 0 条（真的没有流水）和「还没取到」是两件事。
 * 只新增本文件，不改 src。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter, type Router } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import JournalPage from '@/pages/JournalPage.vue'
import * as journalApi from '@/api/journal'
import type { CreditEventPayload } from '@/api/journal'
import * as platformsApi from '@/api/platforms'

vi.mock('@/api/journal', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/journal')>()
  return { ...actual, listCreditEvents: vi.fn() }
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

const listCreditEvents = vi.mocked(journalApi.listCreditEvents)

/** GET /api/maintenance/credit-events 的真实响应形状。 */
function payload(overrides: Partial<CreditEventPayload> = {}): CreditEventPayload {
  return {
    items: [
      {
        id: 918,
        account_id: 152,
        account_name: '工作号 A',
        kind: 'confirmed',
        source: 'schedule · 每日签到',
        delta: 120,
        balance_after: 4764,
        created_at: '2026-11-23T02:15:00Z',
      },
    ],
    total: 317,
    page: 1,
    page_size: 50,
    importing: false,
    sources: ['schedule · 每日签到'],
    accounts: [{ id: 152, name: '工作号 A' }],
    ...overrides,
  }
}

const DateRangeStub = defineComponent({ name: 'DateRange', template: '<div class="daterange-stub" />' })

const Host = defineComponent({
  components: { RouterView, NConfigProvider, NMessageProvider, NDialogProvider },
  template: `<n-config-provider><n-message-provider><n-dialog-provider><router-view /></n-dialog-provider></n-message-provider></n-config-provider>`,
})

let mounted: { wrapper: VueWrapper; router: Router } | null = null

async function flush(): Promise<void> {
  await flushPromises()
  await nextTick()
  await flushPromises()
}

async function mountPage(): Promise<{ wrapper: VueWrapper; router: Router }> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/journal', name: 'journal', component: JournalPage },
      { path: '/logs', name: 'logs', component: defineComponent({ template: '<div class="logs-stub" />' }) },
      { path: '/accounts', name: 'accounts', component: defineComponent({ template: '<div class="accounts-stub" />' }) },
      { path: '/overview', name: 'overview', component: defineComponent({ template: '<div class="overview-stub" />' }) },
    ],
  })
  await router.push('/journal')
  await router.isReady()
  const wrapper = mount(Host, {
    global: { plugins: [pinia, router], stubs: { DateRange: DateRangeStub } },
    attachTo: document.body,
  })
  await flush()
  await flush()
  mounted = { wrapper, router }
  return mounted
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

afterEach(() => {
  mounted?.wrapper.unmount()
  mounted = null
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('JournalPage 首屏统计加载态', () => {
  it('首屏请求未返回时显示加载态，不显示「共 0 条」', async () => {
    vi.mocked(platformsApi.listPlatforms).mockResolvedValue({ items: [] } as never)
    vi.mocked(platformsApi.getCurrentPlatform).mockResolvedValue({ platform: 'workbuddy', realm: '' } as never)
    vi.mocked(platformsApi.putCurrentPlatform).mockResolvedValue(undefined as never)
    const pending = deferred<CreditEventPayload>()
    listCreditEvents.mockReturnValue(pending.promise)

    const { wrapper } = await mountPage()

    expect(listCreditEvents).toHaveBeenCalled()
    // 还没拿到 total：不能把「未知」显示成 0
    expect(wrapper.text()).not.toContain('共 0 条')
    expect(wrapper.text()).toContain('加载中…')

    pending.resolve(payload())
    await flush()

    expect(wrapper.text()).toContain('共 317 条')
    expect(wrapper.text()).not.toContain('加载中…')
  })

  it('真正空页才显示「共 0 条」', async () => {
    vi.mocked(platformsApi.listPlatforms).mockResolvedValue({ items: [] } as never)
    vi.mocked(platformsApi.getCurrentPlatform).mockResolvedValue({ platform: 'workbuddy', realm: '' } as never)
    vi.mocked(platformsApi.putCurrentPlatform).mockResolvedValue(undefined as never)
    listCreditEvents.mockResolvedValue(payload({ items: [], total: 0 }))

    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('共 0 条')
    expect(wrapper.text()).toContain('还没有流水记录。账号刷新或签到后会自动记账。')
  })
})
