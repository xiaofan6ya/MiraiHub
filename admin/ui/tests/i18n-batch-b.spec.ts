/**
 * i18n 批次 B：七页中英切换的真实挂载断言。
 *
 * 覆盖 SentinelPage / GroupsPage / SyncPage / PlatformsPage / GatewayPage /
 * OpenEntryPage / ClientPage：先用默认中文（逐字不变）挂载断言，再切 en-US 断言
 * 页面文案立即换成英文，最后切回中文确认还原。
 * 网络层全部在 api 模块上 mock；页面内的 t() 走真实 useI18n。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { RouterView, createMemoryHistory, createRouter } from 'vue-router'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import { setLocale } from '@/i18n'

vi.mock('@/api/sentinel', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/sentinel')>()
  return {
    ...actual,
    getSentinelStatus: vi.fn(),
    getTraffic: vi.fn(),
    pausePlatform: vi.fn(),
    resumePlatform: vi.fn(),
  }
})

vi.mock('@/api/groups', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/groups')>()
  return {
    ...actual,
    listGroups: vi.fn(),
    listModelConfigs: vi.fn(),
    createGroup: vi.fn(),
    updateGroup: vi.fn(),
    deleteGroup: vi.fn(),
  }
})

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

vi.mock('@/api/platforms-admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/platforms-admin')>()
  return {
    ...actual,
    getAccountsSnapshot: vi.fn(),
    getGatewayConfig: vi.fn(),
    getPlatformSpeed: vi.fn(),
    setPlatformEnabled: vi.fn(),
    exportPlatformsAll: vi.fn(),
    runPlatformScan: vi.fn(),
    submitPlatformImport: vi.fn(),
  }
})

vi.mock('@/api/gateway', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/gateway')>()
  return {
    ...actual,
    getGatewayConfig: vi.fn(),
    getGatewayOrder: vi.fn(),
    getPlatformSpeed: vi.fn(),
    saveGatewayConfig: vi.fn(),
    setGatewayEnabled: vi.fn(),
    simulateRouting: vi.fn(),
  }
})

vi.mock('@/api/openentry', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/openentry')>()
  return {
    ...actual,
    getGateStatus: vi.fn(),
    getOpenConfig: vi.fn(),
    getAdminTickets: vi.fn(),
    toggleGate: vi.fn(),
    saveOpenConfig: vi.fn(),
    issueTickets: vi.fn(),
  }
})

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    getClientProfile: vi.fn(),
    detectClient: vi.fn(),
    saveClientProfile: vi.fn(),
    resetClientProfile: vi.fn(),
  }
})

const sentinelApi = await import('@/api/sentinel')
const groupsApi = await import('@/api/groups')
const syncApi = await import('@/api/sync')
const platformsApi = await import('@/api/platforms')
const platformsAdminApi = await import('@/api/platforms-admin')
const gatewayApi = await import('@/api/gateway')
const openentryApi = await import('@/api/openentry')
const clientApi = await import('@/api/client')

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
  return String(document.body.textContent || '')
}

async function textAfterLocale(locale: 'zh-CN' | 'en-US'): Promise<string> {
  setLocale(locale)
  await nextTick()
  await flushPromises()
  return String(document.body.textContent || '')
}

/* -------------------------------------------------------------------------- */
/* fixtures                                                                   */
/* -------------------------------------------------------------------------- */

const SENTINEL_STATUS = {
  tripped: false,
  accounts_affected: 0,
  accounts_total: 1,
  accounts_risk: 0,
  threshold: 3,
  window_seconds: 300,
  suggested_pause_seconds: 600,
  total_events: 0,
  by_code: {},
  by_signal: {},
  signal_labels: {},
  by_severity: {},
  by_platform: [],
  accounts: [],
  recent: [],
  redis: true,
  pause: {},
  now: 1_800_000_000,
}

const SENTINEL_TRAFFIC = {
  day: '2026-11-24',
  platforms: [],
  totals: { requests: 0, ok: 0, error: 0, timeout: 0, interrupted: 0 },
  recent: [],
  storage: 'redis',
}

const GATEWAY_CONFIG = {
  strategy: 'priority',
  priority: {},
  disabled: {},
  spread_unbound: false,
  strategies: [],
  platforms: [],
}

const SPEED = { hours: 24, model: '', items: [] }

const GATE_PAYLOAD = { open: true, epoch: 7, active_tickets: 0, active_invites: 0, ticket_ttl: 86400, access_ttl: 3600 }

const OPEN_CONFIG = {
  ok: true,
  platforms: [],
  ticket_ttl: 86400,
  ttl_range: [600, 604800],
  copy: { title: '', sub: '', intro: [], foot: '' },
}

const TICKETS = { ok: true, open: true, epoch: 7, count: 0, tickets: [] }

const CLIENT_PROFILE = {
  ok: true,
  source: 'saved',
  effective: {},
  saved: {},
  detected: {},
  sources: {},
  runtime_diagnostics: { header_groups: {} },
  ua: '',
  has_client: false,
  fields: { version: [], fingerprint: [] },
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.mocked(sentinelApi.getSentinelStatus).mockResolvedValue(SENTINEL_STATUS as never)
  vi.mocked(sentinelApi.getTraffic).mockResolvedValue(SENTINEL_TRAFFIC as never)
  vi.mocked(groupsApi.listGroups).mockResolvedValue({ items: [] } as never)
  vi.mocked(groupsApi.listModelConfigs).mockResolvedValue({ items: [] } as never)
  vi.mocked(syncApi.getSyncConfig).mockResolvedValue({
    remote_url: '',
    secret: '',
    secret_masked: '',
    is_auto_generated: false,
  } as never)
  vi.mocked(platformsApi.listPlatforms).mockResolvedValue({
    items: [],
    default: '',
    loader: { loaded: [], failed: {} },
  } as never)
  vi.mocked(platformsApi.getCurrentPlatform).mockResolvedValue(null as never)
  vi.mocked(platformsAdminApi.getGatewayConfig).mockResolvedValue(GATEWAY_CONFIG as never)
  vi.mocked(platformsAdminApi.getAccountsSnapshot).mockResolvedValue({ items: [] } as never)
  vi.mocked(platformsAdminApi.getPlatformSpeed).mockResolvedValue(SPEED as never)
  vi.mocked(gatewayApi.getGatewayConfig).mockResolvedValue(GATEWAY_CONFIG as never)
  vi.mocked(gatewayApi.getGatewayOrder).mockResolvedValue([] as never)
  vi.mocked(gatewayApi.getPlatformSpeed).mockResolvedValue(SPEED as never)
  vi.mocked(openentryApi.getGateStatus).mockResolvedValue(GATE_PAYLOAD as never)
  vi.mocked(openentryApi.getOpenConfig).mockResolvedValue(OPEN_CONFIG as never)
  vi.mocked(openentryApi.getAdminTickets).mockResolvedValue(TICKETS as never)
  vi.mocked(clientApi.getClientProfile).mockResolvedValue(CLIENT_PROFILE as never)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setLocale('zh-CN')
  document.body.innerHTML = ''
})

/* -------------------------------------------------------------------------- */
/* 七页                                                                        */
/* -------------------------------------------------------------------------- */

describe('i18n 批次 B：七页中英切换', () => {
  it('SentinelPage：默认中文，切英文后标题与横幅变英文', async () => {
    const { default: SentinelPage } = await import('@/pages/SentinelPage.vue')
    const zh = await mountRoute(SentinelPage, '/sentinel')
    expect(zh).toContain('风控哨兵')
    expect(zh).toContain('未检测到批次级封控')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Risk Sentinel')
    expect(en).toContain('No batch-level block detected')
    expect(en).not.toContain('风控哨兵')

    expect(await textAfterLocale('zh-CN')).toContain('风控哨兵')
  })

  it('GroupsPage：默认中文，切英文后标题与空态变英文', async () => {
    const { default: GroupsPage } = await import('@/pages/GroupsPage.vue')
    const zh = await mountRoute(GroupsPage, '/groups')
    expect(zh).toContain('分组管理')
    expect(zh).toContain('新建分组')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Groups')
    expect(en).toContain('New group')
    expect(en).not.toContain('分组管理')
  })

  it('SyncPage：默认中文，切英文后卡片文案变英文', async () => {
    const { default: SyncPage } = await import('@/pages/SyncPage.vue')
    const zh = await mountRoute(SyncPage, '/sync')
    expect(zh).toContain('同步设置')
    expect(zh).toContain('线上同步')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Sync settings')
    expect(en).toContain('Online sync')
    expect(en).not.toContain('同步设置')
  })

  it('PlatformsPage：默认中文，切英文后页头动作变英文', async () => {
    const { default: PlatformsPage } = await import('@/pages/PlatformsPage.vue')
    const zh = await mountRoute(PlatformsPage, '/platforms')
    expect(zh).toContain('平台管理')
    expect(zh).toContain('扫描全部平台')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Platforms')
    expect(en).toContain('Scan all platforms')
    expect(en).not.toContain('平台管理')
  })

  it('GatewayPage：默认中文，切英文后区块标题变英文', async () => {
    const { default: GatewayPage } = await import('@/pages/GatewayPage.vue')
    const zh = await mountRoute(GatewayPage, '/gateway')
    expect(zh).toContain('平台网关')
    expect(zh).toContain('调度策略')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Platform gateway')
    expect(en).toContain('Routing strategy')
    expect(en).not.toContain('平台网关')
  })

  it('OpenEntryPage：默认中文，切英文后区块标题与开关文案变英文', async () => {
    const { default: OpenEntryPage } = await import('@/pages/OpenEntryPage.vue')
    const zh = await mountRoute(OpenEntryPage, '/openentry')
    expect(zh).toContain('公开注册入口')
    expect(zh).toContain('入口状态')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Public sign-up')
    expect(en).toContain('Entry status')
    expect(en).not.toContain('公开注册入口')
  })

  it('ClientPage：默认中文，切英文后区块标题变英文', async () => {
    const { default: ClientPage } = await import('@/pages/ClientPage.vue')
    const zh = await mountRoute(ClientPage, '/client')
    expect(zh).toContain('客户端参数')
    expect(zh).toContain('扫描位置')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Client profile')
    expect(en).toContain('Scan locations')
    expect(en).not.toContain('客户端参数')
  })
})
