import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import {
  HEARTBEAT_INTERVAL_MS,
  HEARTBEAT_PATH,
  statusFromFailures,
  useHeartbeatStore,
} from '@/stores/heartbeat'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function stubHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

async function flush() {
  // beat() 里有两个 await（fetch + readBody），推进微任务即可
  for (let i = 0; i < 5; i += 1) await Promise.resolve()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  stubHidden(false)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  stubHidden(false)
})

describe('状态映射', () => {
  it('0 次成功、1–2 次重连中、3 次以上中断', () => {
    expect(statusFromFailures(0)).toBe('ok')
    expect(statusFromFailures(1)).toBe('reconnecting')
    expect(statusFromFailures(2)).toBe('reconnecting')
    expect(statusFromFailures(3)).toBe('down')
    expect(statusFromFailures(9)).toBe('down')
  })
})

describe('heartbeat store', () => {
  it('start() 立刻打一拍，命中旧版启动探针', async () => {
    const mock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ ok: true }),
    )
    vi.stubGlobal('fetch', mock)
    const hb = useHeartbeatStore()
    hb.start()
    await flush()
    expect(mock).toHaveBeenCalledTimes(1)
    expect(String(mock.mock.calls[0][0])).toBe(HEARTBEAT_PATH)
    expect(hb.status).toBe('ok')
    expect(hb.statusText).toBe('运行中')
    expect(hb.lastBeatAt).not.toBeNull()
    expect(hb.lastOkAt).not.toBeNull()
    hb.stop()
  })

  it('每 15 秒一拍；连续失败 3 次后为 down，恢复后回 ok', async () => {
    let fail = true
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        if (fail) throw new TypeError('Failed to fetch')
        return jsonResponse({ ok: true })
      }),
    )
    const hb = useHeartbeatStore()
    hb.start()
    await flush()
    expect(hb.status).toBe('reconnecting')
    expect(hb.consecutiveFailures).toBe(1)

    await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS)
    await flush()
    expect(hb.status).toBe('reconnecting')
    expect(hb.consecutiveFailures).toBe(2)

    await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS)
    await flush()
    expect(hb.status).toBe('down')
    expect(hb.statusText).toBe('连接中断')
    expect(hb.statusTone).toBe('danger')

    fail = false
    await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS)
    await flush()
    expect(hb.status).toBe('ok')
    expect(hb.consecutiveFailures).toBe(0)
    hb.stop()
  })

  it('document.hidden 时暂停，回到前台立刻补一拍', async () => {
    const mock = vi.fn(async () => jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', mock)
    stubHidden(true)
    const hb = useHeartbeatStore()
    hb.start()
    await flush()
    expect(mock).not.toHaveBeenCalled()
    expect(hb.lastBeatAt).toBeNull()

    stubHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(mock).toHaveBeenCalledTimes(1)
    expect(hb.status).toBe('ok')

    // 暂停期间即使时间推进也不发请求
    stubHidden(true)
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    const before = mock.mock.calls.length
    await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS * 2)
    await flush()
    expect(mock.mock.calls.length).toBe(before)
    hb.stop()
  })

  it('stop() 后不再发送', async () => {
    const mock = vi.fn(async () => jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', mock)
    const hb = useHeartbeatStore()
    hb.start()
    await flush()
    hb.stop()
    await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS * 3)
    await flush()
    expect(mock).toHaveBeenCalledTimes(1)
  })
})
