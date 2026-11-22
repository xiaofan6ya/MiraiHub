import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, effectScope, nextTick } from 'vue'
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
