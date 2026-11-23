/**
 * i18n 批次 C：剩余八页中英切换的真实挂载断言。
 *
 * 覆盖 CreditsPage / AlertsPage / IpPage / LogsPage / ModelsPage / MaintenancePage /
 * SchedulesPage / KeysPage：先用默认中文（逐字不变）挂载断言，再切 en-US 断言页面文案
 * 立即换成英文，最后切回中文确认还原。
 *
 * 网络层全部在 api 模块上 mock（统一拒绝 → 页面落在错误态，页头与工具条仍然渲染，
 * 正好验证「静态文案不依赖取数」）；页面内的 t() 走真实 useI18n。
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

const offline = () => Promise.reject(new Error('offline'))

vi.mock('@/api/credits', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/credits')>()
  return {
    ...actual,
    getCreditExpiry: vi.fn(offline),
    getCreditUsage: vi.fn(offline),
    syncCredits: vi.fn(offline),
    saveSelectionPolicy: vi.fn(offline),
    restoreAutomaticSelection: vi.fn(offline),
  }
})

vi.mock('@/api/alerts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/alerts')>()
  return {
    ...actual,
    getAlertConfig: vi.fn(offline),
    putAlertConfig: vi.fn(offline),
    sendAlertTest: vi.fn(offline),
  }
})

vi.mock('@/api/ip', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ip')>()
  return {
    ...actual,
    getIpActivity: vi.fn(offline),
    listBlockedIps: vi.fn(offline),
    listIpDevices: vi.fn(offline),
    blockIp: vi.fn(offline),
    patchBlockedIp: vi.fn(offline),
    deleteBlockedIp: vi.fn(offline),
    updateIpDevice: vi.fn(offline),
  }
})

vi.mock('@/api/logs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/logs')>()
  return {
    ...actual,
    listUsageLogs: vi.fn(offline),
    getUsageSummary: vi.fn(offline),
    listLogAccounts: vi.fn(offline),
    getLiveRequests: vi.fn(offline),
    getCaptureSettings: vi.fn(offline),
    getUsageLog: vi.fn(offline),
    requestStreamTicket: vi.fn(offline),
    setCaptureEnabled: vi.fn(offline),
    clearCaptureCache: vi.fn(offline),
    downloadLogsExport: vi.fn(offline),
    downloadLogCapture: vi.fn(offline),
  }
})

vi.mock('@/api/models', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/models')>()
  return {
    ...actual,
    listModelConfigs: vi.fn(offline),
    listModelHealth: vi.fn(offline),
    syncModels: vi.fn(offline),
    updateModelConfig: vi.fn(offline),
    deleteModelConfig: vi.fn(offline),
    batchToggleModels: vi.fn(offline),
    speedTestModel: vi.fn(offline),
  }
})

vi.mock('@/api/maintenance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/maintenance')>()
  return {
    ...actual,
    getUpdateInfo: vi.fn(offline),
    getReleasesInfo: vi.fn(offline),
    downloadBackup: vi.fn(offline),
    setBackupDir: vi.fn(offline),
    restoreDatabase: vi.fn(offline),
    restartService: vi.fn(offline),
  }
})

vi.mock('@/api/schedules', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/schedules')>()
  return {
    ...actual,
    listSchedules: vi.fn(offline),
    getGlobalScheduleProxy: vi.fn(offline),
    listScheduleTasks: vi.fn(offline),
    listProxyEndpoints: vi.fn(offline),
  }
})

vi.mock('@/api/keys', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/keys')>()
  return {
    ...actual,
    listKeys: vi.fn(offline),
    getSelectionPolicy: vi.fn(offline),
    getNetwork: vi.fn(offline),
    listAggregateModels: vi.fn(offline),
  }
})

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
    routes: [{ path, name: path.replace('/', ''), component: component as never }],
  })
  await router.push(path)
  await router.isReady()
  wrapper = mount(Host, { global: { plugins: [createPinia(), router] }, attachTo: document.body })
  await settle()
  await settle()
  return wrapperText()
}

/** 只取组件树内的文案：失败 Toast 是取数当时的中文快照，留在 body 里会干扰断言。 */
function wrapperText(): string {
  return wrapper ? wrapper.text() : ''
}

async function textAfterLocale(locale: 'zh-CN' | 'en-US'): Promise<string> {
  setLocale(locale)
  await nextTick()
  await flushPromises()
  return wrapperText()
}

beforeEach(() => {
  setLocale('zh-CN')
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setLocale('zh-CN')
  document.body.innerHTML = ''
})

/* -------------------------------------------------------------------------- */
/* 八页                                                                        */
/* -------------------------------------------------------------------------- */

describe('i18n 批次 C：八页中英切换', () => {
  it('CreditsPage：默认中文，切英文后页头与错误卡片变英文', async () => {
    const { default: CreditsPage } = await import('@/pages/CreditsPage.vue')
    const zh = await mountRoute(CreditsPage, '/credits')
    expect(zh).toContain('积分到期')
    expect(zh).toContain('加载积分到期失败')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Credit expiry')
    expect(en).toContain('Failed to load credit expiry')
    expect(en).not.toContain('积分到期')

    expect(await textAfterLocale('zh-CN')).toContain('积分到期')
  })

  it('AlertsPage：默认中文，切英文后页头与规则卡片变英文', async () => {
    const { default: AlertsPage } = await import('@/pages/AlertsPage.vue')
    const zh = await mountRoute(AlertsPage, '/alerts')
    expect(zh).toContain('预警管理')
    expect(zh).toContain('告警规则')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Alerts')
    expect(en).toContain('Alert rules')
    expect(en).not.toContain('预警管理')
  })

  it('IpPage：默认中文，切英文后页头与统计条变英文', async () => {
    const { default: IpPage } = await import('@/pages/IpPage.vue')
    const zh = await mountRoute(IpPage, '/ip')
    expect(zh).toContain('IP 管理')
    expect(zh).toContain('活跃 IP')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('IP management')
    expect(en).toContain('Active IPs')
    expect(en).not.toContain('IP 管理')
  })

  it('LogsPage：默认中文，切英文后页头与详情记录卡片变英文', async () => {
    const { default: LogsPage } = await import('@/pages/LogsPage.vue')
    const zh = await mountRoute(LogsPage, '/logs')
    expect(zh).toContain('用量日志')
    expect(zh).toContain('请求详情记录')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Usage log')
    expect(en).toContain('Request-detail recording')
    expect(en).not.toContain('用量日志')
  })

  it('ModelsPage：默认中文，切英文后页头与工具条变英文', async () => {
    const { default: ModelsPage } = await import('@/pages/ModelsPage.vue')
    const zh = await mountRoute(ModelsPage, '/models')
    expect(zh).toContain('模型配置')
    expect(zh).toContain('同步目录')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Models')
    expect(en).toContain('Sync catalog')
    expect(en).not.toContain('模型配置')
  })

  it('MaintenancePage：默认中文，切英文后页头与备份卡片变英文', async () => {
    const { default: MaintenancePage } = await import('@/pages/MaintenancePage.vue')
    const zh = await mountRoute(MaintenancePage, '/maintenance')
    expect(zh).toContain('更新与备份')
    expect(zh).toContain('数据库备份')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Updates and backups')
    expect(en).toContain('Database backup')
    expect(en).not.toContain('更新与备份')
  })

  it('SchedulesPage：默认中文，切英文后页头与工具条变英文', async () => {
    const { default: SchedulesPage } = await import('@/pages/SchedulesPage.vue')
    const zh = await mountRoute(SchedulesPage, '/schedules')
    expect(zh).toContain('定时任务')
    expect(zh).toContain('新建任务')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Schedules')
    expect(en).toContain('New task')
    expect(en).not.toContain('定时任务')
  })

  it('KeysPage：默认中文，切英文后工具条变英文（页头 API Keys 两语同名）', async () => {
    const { default: KeysPage } = await import('@/pages/KeysPage.vue')
    const zh = await mountRoute(KeysPage, '/keys')
    expect(zh).toContain('创建 Key')
    expect(zh).toContain('接入指南')

    const en = await textAfterLocale('en-US')
    expect(en).toContain('Create key')
    expect(en).toContain('Integration guide')
    expect(en).not.toContain('接入指南')
  })
})

/* -------------------------------------------------------------------------- */
/* 词条本身：中文真值逐字不变 + 英文不残留中文                                  */
/* -------------------------------------------------------------------------- */

const NAMESPACES = ['credits', 'alerts', 'ip', 'logs', 'models', 'maintenance', 'schedules', 'keys']

describe('i18n 批次 C：词条真值', () => {
  it('八页中文词条保留原文，英文词条全部是英文', () => {
    const zhKeys = Object.keys(zhCN)
    const enKeys = Object.keys(enUS)
    expect(enKeys.length).toBe(zhKeys.length)

    for (const ns of NAMESPACES) {
      const prefix = 'page.' + ns + '.'
      const zhPage = zhKeys.filter((key) => key.startsWith(prefix))
      expect(zhPage.length, ns + ' 应有多条词条').toBeGreaterThan(20)
      for (const key of zhPage) {
        expect(zhCN[key as keyof typeof zhCN], key + ' 英文缺失').toBeTruthy()
        const en = enUS[key as keyof typeof enUS]
        expect(en, key + ' 英文残留中文').toBeTruthy()
        expect(/[\u4e00-\u9fff]/.test(en), key + ' 英文不应含中文：' + en).toBe(false)
      }
    }
  })

  it('默认中文逐字不变（抽查页头与关键按钮）', () => {
    const anchors: Array<[string, string]> = [
      ['page.credits.title', '积分到期'],
      ['page.alerts.title', '预警管理'],
      ['page.ip.title', 'IP 管理'],
      ['page.logs.title', '用量日志'],
      ['page.models.title', '模型配置'],
      ['page.maintenance.title', '更新与备份'],
      ['page.schedules.title', '定时任务'],
      ['page.keys.action.create', '创建 Key'],
    ]
    for (const [key, text] of anchors) {
      expect(zhCN[key as keyof typeof zhCN], key).toBe(text)
      setLocale('zh-CN')
      expect(t(key as never), key).toBe(text)
    }
  })
})
