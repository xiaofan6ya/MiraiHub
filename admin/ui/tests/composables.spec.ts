import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, effectScope, nextTick, watch } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { copyText, fallbackCopy, useCopy } from '@/composables/useCopy'
import { usePolling } from '@/composables/usePolling'
import { useTableQuery } from '@/composables/useTableQuery'

async function flushMicro() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('立即执行一次，之后按间隔执行；stop 后停止', async () => {
    const task = vi.fn(async () => undefined)
    const scope = effectScope()
    const polling = scope.run(() => usePolling(task, 1000))!
    polling.start()
    await flushMicro()
    expect(task).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    await flushMicro()
    expect(task).toHaveBeenCalledTimes(2)

    polling.stop()
    await vi.advanceTimersByTimeAsync(3000)
    await flushMicro()
    expect(task).toHaveBeenCalledTimes(2)
    scope.stop()
  })

  it('连续失败 3 次自动暂停，给出可恢复的提示', async () => {
    const task = vi.fn(async () => {
      throw new Error('网关无响应')
    })
    const scope = effectScope()
    const polling = scope.run(() => usePolling(task, 1000))!
    polling.start()
    await flushMicro()
    expect(polling.failures.value).toBe(1)
    expect(polling.paused.value).toBe(false)

    await vi.advanceTimersByTimeAsync(1000)
    await flushMicro()
    await vi.advanceTimersByTimeAsync(1000)
    await flushMicro()

    expect(polling.failures.value).toBe(3)
    expect(polling.paused.value).toBe(true)
    expect(polling.pausedMessage.value).toBe('自动刷新已暂停：网关无响应')

    const before = task.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    await flushMicro()
    expect(task.mock.calls.length).toBe(before)

    // 恢复后重新开始计数
    polling.resume()
    await flushMicro()
    expect(polling.paused.value).toBe(false)
    expect(polling.failures.value).toBe(1)
    scope.stop()
  })
})

describe('useCopy', () => {
  const originalClipboard = navigator.clipboard
  const originalExec = document.execCommand

  afterEach(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: originalClipboard })
    Object.defineProperty(document, 'execCommand', { configurable: true, value: originalExec })
  })

  it('优先用 navigator.clipboard', async () => {
    const writeText = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    await expect(copyText('sk-test')).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('sk-test')
  })

  it('clipboard 不可用时回退 textarea + execCommand', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    const exec = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { configurable: true, value: exec })
    await expect(copyText('sk-fallback')).resolves.toBe(true)
    expect(exec).toHaveBeenCalledWith('copy')
    // 临时节点必须清理干净
    expect(document.querySelectorAll('textarea')).toHaveLength(0)
  })

  it('execCommand 返回 false 时如实报告失败', () => {
    Object.defineProperty(document, 'execCommand', { configurable: true, value: () => false })
    expect(fallbackCopy('x')).toBe(false)
  })

  it('useCopy 维护 copied 状态', async () => {
    const writeText = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    const { copied, copy } = useCopy()
    await copy('abc')
    expect(copied.value).toBe(true)
  })
})

describe('useTableQuery（分页/排序/筛选 ↔ 路由 query）', () => {
  async function mountWithRoute(initial: string) {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/logs', component: defineComponent({ render: () => null }) }],
    })
    await router.push(initial)
    await router.isReady()

    let api: ReturnType<typeof useTableQuery> | null = null
    const app = createApp(
      defineComponent({
        setup() {
          api = useTableQuery('logs', { filters: { platform: '', result: '' } })
          return () => null
        },
      }),
    )
    app.use(router)
    app.mount(document.createElement('div'))
    return { router, app, api: api as unknown as ReturnType<typeof useTableQuery> }
  }

  it('初始化时从 query 读取筛选项', async () => {
    const { api, app } = await mountWithRoute('/logs?platform=trae&result=error&page=2&size=50')
    expect(api.state.filters).toEqual({ platform: 'trae', result: 'error' })
    expect(api.state.page).toBe(2)
    expect(api.state.pageSize).toBe(50)
    app.unmount()
  })

  it('翻页 / 筛选 / 重置会写回 query', async () => {
    const { api, router, app } = await mountWithRoute('/logs?platform=trae')
    api.setPage(3)
    await nextTick()
    await flushPromises()
    expect(router.currentRoute.value.query.page).toBe('3')

    api.setFilter('result', 'error')
    await nextTick()
    await flushPromises()
    expect(router.currentRoute.value.query.result).toBe('error')
    // 改筛选回到第 1 页，query 里不留 page
    expect(router.currentRoute.value.query.page).toBeUndefined()

    api.setSort('created_at', 'desc')
    await nextTick()
    await flushPromises()
    expect(router.currentRoute.value.query.sort).toBe('created_at')
    expect(router.currentRoute.value.query.order).toBe('desc')

    api.reset()
    await nextTick()
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({})
    app.unmount()
  })

  it('外部导航（链接 / 浏览器前进后退）时状态跟着变', async () => {
    const { api, router, app } = await mountWithRoute('/logs?platform=qoder')
    expect(api.state.filters.platform).toBe('qoder')

    // 表格自己的写入用 replace（不污染历史），这里模拟"外部跳到另一个筛选链接"
    await router.push('/logs?platform=trae&page=4')
    await nextTick()
    await flushPromises()

    expect(api.state.filters.platform).toBe('trae')
    expect(api.state.page).toBe(4)
    app.unmount()
  })
})

describe('useTableQuery × KeepAlive（第 5 轮 P1：只在本页路由读写 query）', () => {
  async function mountOnLogs(initial: string) {
    const Blank = defineComponent({ render: () => null })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/logs', name: 'logs', component: Blank },
        { path: '/stats', name: 'stats', component: Blank },
      ],
    })
    await router.push(initial)
    await router.isReady()

    let api: ReturnType<typeof useTableQuery> | null = null
    const changes = vi.fn()
    const app = createApp(
      defineComponent({
        setup() {
          api = useTableQuery('logs', { filters: { platform: '', q: '' } })
          // 模拟页面：筛选一变就重拉
          watch(() => api!.query.value, changes, { deep: true })
          return () => null
        },
      }),
    )
    app.use(router)
    app.mount(document.createElement('div'))
    return { router, app, changes, api: api as unknown as ReturnType<typeof useTableQuery> }
  }

  async function settle() {
    await nextTick()
    await flushPromises()
  }

  it('缓存期间不读入其它页面的同名参数，也不触发页面重拉', async () => {
    const { api, router, app, changes } = await mountOnLogs('/logs?platform=qoder')
    await router.push('/stats?platform=trae&page=5&q=abc')
    await settle()
    expect(api.state.filters).toEqual({ platform: 'qoder', q: '' })
    expect(api.state.page).toBe(1)
    expect(changes).not.toHaveBeenCalled()
    app.unmount()
  })

  it('缓存期间 setXxx 不写地址栏；侧栏回来（无本表格的键）把缓存状态写回地址栏', async () => {
    const { api, router, app, changes } = await mountOnLogs('/logs?platform=qoder&page=3')
    await router.push('/stats?days=7')
    await settle()
    api.setFilter('q', 'kw')
    await settle()
    expect(router.currentRoute.value.fullPath).toBe('/stats?days=7')

    changes.mockClear()
    await router.push('/logs')
    await settle()
    expect(router.currentRoute.value.query).toEqual({ platform: 'qoder', q: 'kw' })
    expect(api.state.filters).toEqual({ platform: 'qoder', q: 'kw' })
    // 状态没变 → 不重拉（回访刷新交给 useRevisit）
    expect(changes).not.toHaveBeenCalled()
    app.unmount()
  })

  it('深链回来（带本表格的键）按地址栏读取', async () => {
    const { api, router, app } = await mountOnLogs('/logs?platform=qoder')
    await router.push('/stats')
    await settle()
    await router.push('/logs?platform=trae&page=2')
    await settle()
    expect(api.state.filters.platform).toBe('trae')
    expect(api.state.page).toBe(2)
    app.unmount()
  })

  it('restoreOnReturn: false 时回来按地址栏重置', async () => {
    const Blank = defineComponent({ render: () => null })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/logs', name: 'logs', component: Blank },
        { path: '/stats', name: 'stats', component: Blank },
      ],
    })
    await router.push('/logs?platform=qoder')
    await router.isReady()
    let api: ReturnType<typeof useTableQuery> | null = null
    const app = createApp(
      defineComponent({
        setup() {
          api = useTableQuery('logs', { filters: { platform: '' }, restoreOnReturn: false })
          return () => null
        },
      }),
    )
    app.use(router)
    app.mount(document.createElement('div'))
    await router.push('/stats')
    await settle()
    await router.push('/logs')
    await settle()
    expect(api!.state.filters.platform).toBe('')
    expect(router.currentRoute.value.query).toEqual({})
    app.unmount()
  })

  it('同一页两个实例同一拍写入，互不覆盖', async () => {
    const Blank = defineComponent({ render: () => null })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/ip', name: 'ip', component: Blank }],
    })
    await router.push('/ip')
    await router.isReady()
    let a: ReturnType<typeof useTableQuery> | null = null
    let b: ReturnType<typeof useTableQuery> | null = null
    const app = createApp(
      defineComponent({
        setup() {
          a = useTableQuery('ip.a', { filters: { ip: '' } })
          b = useTableQuery('ip.b', { filters: { device: '' } })
          return () => null
        },
      }),
    )
    app.use(router)
    app.mount(document.createElement('div'))
    a!.setFilter('ip', '1.2.3.4')
    b!.setFilter('device', 'd1')
    await settle()
    expect(router.currentRoute.value.query).toEqual({ ip: '1.2.3.4', device: 'd1' })
    app.unmount()
  })
})
