import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { http, setHeaderProvider } from '@/api/http'
import {
  PLATFORM_CHANGE_EVENT,
  PLATFORM_ONLY_TABS,
  readSavedSelection,
  usePlatformStore,
} from '@/stores/platform'

const PLATFORM_ITEMS = [
  {
    id: 'workbuddy',
    label: 'WorkBuddy',
    enabled: true,
    storage_platform: 'workbuddy',
    realm: '',
    realm_label: '',
    realms: [],
    account_count: 3,
    realm_counts: {},
  },
  {
    id: 'trae',
    label: 'Trae',
    enabled: true,
    storage_platform: 'trae',
    realm: '',
    realm_label: '',
    realms: [
      { key: 'cn', label: '国内版', default: true },
      { key: 'intl', label: '国际版' },
    ],
    account_count: 5,
    realm_counts: { cn: 4, intl: 1 },
  },
  {
    id: 'trae-cn',
    label: 'Trae 国内版',
    enabled: true,
    storage_platform: 'trae',
    realm: 'cn',
    realm_label: '国内版',
    realms: [],
    account_count: 4,
    realm_counts: {},
  },
]

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

interface Call {
  url: string
  method: string
  body: string
}

function installFetch(options: {
  current?: { platform: string; realm: string; raw?: string }
  failList?: boolean
} = {}) {
  const calls: Call[] = []
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = String((init && init.method) || 'GET').toUpperCase()
    const body = typeof init?.body === 'string' ? init.body : ''
    calls.push({ url, method, body })
    if (url.startsWith('/api/platforms/current')) {
      if (method === 'PUT') {
        const payload = JSON.parse(body || '{}') as { platform?: string; realm?: string }
        return jsonResponse({
          ok: true,
          platform: payload.platform || '',
          realm: payload.realm || '',
          raw: payload.platform || '',
        })
      }
      return jsonResponse(
        options.current || { platform: 'workbuddy', realm: '', raw: 'workbuddy' },
      )
    }
    if (url.startsWith('/api/platforms')) {
      if (options.failList) throw new TypeError('Failed to fetch')
      return jsonResponse({ items: PLATFORM_ITEMS, default: 'workbuddy', loader: { loaded: [], failed: {} } })
    }
    throw new Error('未预置的请求：' + method + ' ' + url)
  })
  vi.stubGlobal('fetch', mock)
  return calls
}

beforeEach(() => {
  setActivePinia(createPinia())
  setHeaderProvider(null)
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  setHeaderProvider(null)
})

describe('platform store：加载与持久化', () => {
  it('优先用 localStorage 的选择，并派发一次 wb-platform-change', async () => {
    localStorage.setItem('wb_platform_current', JSON.stringify({ platform: 'trae', realm: 'cn' }))
    installFetch()
    const store = usePlatformStore()
    const seen: Array<{ selector: string }> = []
    store.onChange((detail) => seen.push({ selector: detail.selector }))

    await store.load()

    expect(store.loaded).toBe(true)
    expect(store.platforms).toHaveLength(3)
    expect(store.currentId).toBe('trae')
    expect(store.current.realm).toBe('cn')
    expect(store.selector).toBe('trae:cn')
    expect(store.storagePlatform).toBe('trae')
    expect(seen).toEqual([{ selector: 'trae:cn' }])
    expect(store.countOf('trae')).toBe(5)
    expect(store.realmCountOf('trae', 'cn')).toBe(4)
  })

  it('localStorage 没有值时读后端 /api/platforms/current', async () => {
    const calls = installFetch({ current: { platform: 'trae', realm: 'intl', raw: 'trae:intl' } })
    const store = usePlatformStore()
    await store.load()
    expect(calls.some((call) => call.url.startsWith('/api/platforms/current') && call.method === 'GET')).toBe(true)
    expect(store.selector).toBe('trae:intl')
  })

  it('选择的是不存在的平台时回落列表第一个', async () => {
    localStorage.setItem('wb_platform_current', JSON.stringify({ platform: 'ghost', realm: '' }))
    installFetch()
    const store = usePlatformStore()
    await store.load()
    expect(store.currentId).toBe('workbuddy')
    expect(readSavedSelection()).toEqual({ platform: 'workbuddy', realm: '' })
  })

  it('列表加载失败时回落到 WorkBuddy，不抛错', async () => {
    installFetch({ failList: true })
    const store = usePlatformStore()
    await store.load()
    expect(store.platforms).toHaveLength(1)
    expect(store.currentId).toBe('workbuddy')
  })
})

describe('platform store：切换', () => {
  it('setCurrent 写 localStorage、写后端、派发事件', async () => {
    const calls = installFetch()
    const store = usePlatformStore()
    await store.load()

    const events: Array<{ selector: string }> = []
    const onDocument = (event: Event) => {
      events.push({ selector: (event as CustomEvent<{ selector: string }>).detail.selector })
    }
    document.addEventListener(PLATFORM_CHANGE_EVENT, onDocument)

    const changed = store.setCurrent('trae', 'intl')
    await nextTick()
    await Promise.resolve()

    expect(changed).toBe(true)
    expect(store.selector).toBe('trae:intl')
    expect(JSON.parse(localStorage.getItem('wb_platform_current') || '{}')).toEqual({
      platform: 'trae',
      realm: 'intl',
    })
    expect(events).toEqual([{ selector: 'trae:intl' }])
    const put = calls.find((call) => call.method === 'PUT' && call.url.startsWith('/api/platforms/current'))
    expect(put).toBeTruthy()
    expect(JSON.parse(put!.body)).toEqual({ platform: 'trae', realm: 'intl' })

    document.removeEventListener(PLATFORM_CHANGE_EVENT, onDocument)
  })

  it('无效区域归零为整个平台', async () => {
    installFetch()
    const store = usePlatformStore()
    await store.load()
    store.setCurrent('trae', 'mars')
    expect(store.selector).toBe('trae')
    expect(store.current.realm).toBe('')
  })

  it('同一平台重复选择返回 false', async () => {
    installFetch()
    const store = usePlatformStore()
    await store.load()
    expect(store.setCurrent('workbuddy')).toBe(false)
  })

  it('onChange 返回的取消函数生效', async () => {
    installFetch()
    const store = usePlatformStore()
    await store.load()
    const spy = vi.fn()
    const off = store.onChange(spy)
    store.setCurrent('trae')
    expect(spy).toHaveBeenCalledTimes(1)
    off()
    store.setCurrent('workbuddy')
    expect(spy).toHaveBeenCalledTimes(1)
  })
})

describe('platform store：平台专属页面规则（旧版 _PLATFORM_ONLY_TABS）', () => {
  it('成长计划只在 WorkBuddy 下可用', async () => {
    installFetch()
    const store = usePlatformStore()
    await store.load()
    expect(PLATFORM_ONLY_TABS.growth).toBe('workbuddy')
    expect(store.supports('growth')).toBe(true)
    expect(store.supports('accounts')).toBe(true)

    store.setCurrent('trae')
    expect(store.supports('growth')).toBe(false)
    expect(store.fallbackTab('growth')).toBe('accounts')
    expect(store.fallbackTab('accounts')).toBe('accounts')
  })
})

describe('platform store：请求头注入', () => {
  it('后续 http 请求自动带 X-Platform', async () => {
    localStorage.setItem('wb_platform_current', JSON.stringify({ platform: 'trae', realm: 'cn' }))
    const calls = installFetch()
    const store = usePlatformStore()
    await store.load()

    const inner = vi.mocked(fetch)
    inner.mockImplementationOnce(async (_input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: '/api/stats/overview',
        method: String((init && init.method) || 'GET').toUpperCase(),
        body: '',
      })
      return jsonResponse({ ok: true })
    })
    await http('GET', '/api/stats/overview')
    const lastCall = inner.mock.calls[inner.mock.calls.length - 1]
    const headers = new Headers((lastCall[1] && lastCall[1].headers) as HeadersInit)
    expect(headers.get('X-Platform')).toBe('trae:cn')
  })
})
