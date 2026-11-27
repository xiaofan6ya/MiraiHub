import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createIslandQueue } from '@/components/feedback/islandQueue'
import DynamicIsland from '@/components/feedback/DynamicIsland.vue'
import { trafficGeometry } from '@/components/charts/trafficTrend'
import { overviewComposition } from '@/pages/Overview/overviewComposition'
import type { OverviewWindow } from '@/api/overview'

afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

describe('Overview lower-card data', () => {
  const usage: OverviewWindow = { requests: 100, ok_requests: 97, prompt_tokens: 80, completion_tokens: 20, total_tokens: 100, cached_tokens: 60, credits: 0 }
  it('uses successful and failed counts from the same usage window', () => {
    const data = overviewComposition(usage)
    expect(data.success).toBe(97)
    expect(data.failures).toBe(3)
    expect(data.successShare).toBe(.97)
  })
  it('does not add cached input a second time to the input/output composition', () => {
    const data = overviewComposition(usage)
    expect(data.promptShare).toBe(.8)
    expect(data.outputShare).toBe(.2)
    expect(data.promptShare + data.outputShare).toBe(1)
  })
  it('keeps empty and invalid results honest without NaN or negative widths', () => {
    expect(overviewComposition(null).hasTokens).toBe(false)
    const data = overviewComposition({ ...usage, requests: 0, ok_requests: 100, prompt_tokens: NaN, completion_tokens: -1 })
    expect(data.successShare).toBe(0)
    expect(data.failures).toBe(0)
    expect(data.promptShare).toBe(0)
    expect(data.outputShare).toBe(0)
  })
})

describe('Dynamic Island operations', () => {
  it('serializes messages and preserves the second operation until its own display duration', () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    queue.api.success('Deleted', { duration: 1000 })
    queue.api.error('Next operation failed', { duration: 2000 })
    expect(queue.current.value?.text).toBe('Deleted')
    vi.advanceTimersByTime(1000)
    expect(queue.current.value?.tone).toBe('error')
    vi.advanceTimersByTime(1999)
    expect(queue.current.value).not.toBeNull()
    vi.advanceTimersByTime(1)
    expect(queue.current.value).toBeNull()
    queue.dispose()
  })
  it('pauses independently for hover, keyboard focus and a hidden browser tab', () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    queue.api.info('A readable notification', { duration: 1000 })
    vi.advanceTimersByTime(400)
    queue.pause('hover'); queue.pause('hidden')
    vi.advanceTimersByTime(10000)
    queue.resume('hover')
    vi.advanceTimersByTime(10000)
    expect(queue.current.value?.text).toBe('A readable notification')
    queue.resume('hidden')
    vi.advanceTimersByTime(599)
    expect(queue.current.value).not.toBeNull()
    vi.advanceTimersByTime(1)
    expect(queue.current.value).toBeNull()
    queue.dispose()
  })
  it('coalesces repeated messages, bounds bursts and supports persistent loading and manual dismissal', () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    const loading = queue.api.loading('Importing')
    queue.api.loading('Importing')
    expect(queue.items.value).toHaveLength(1)
    expect(queue.current.value?.count).toBe(2)
    vi.advanceTimersByTime(60000)
    expect(queue.current.value?.tone).toBe('loading')
    for (let index = 0; index < 24; index++) queue.api.error('Error ' + index)
    expect(queue.items.value).toHaveLength(16)
    loading.destroy()
    expect(queue.current.value?.tone).toBe('error')
    queue.api.destroyAll()
    expect(queue.current.value).toBeNull()
    queue.dispose()
  })
  it('renders the real operation in an accessible live region and closes from the keyboard', async () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    queue.api.error('Delete failed: record is in use')
    const wrapper = mount(DynamicIsland, { props: { queue }, attachTo: document.body })
    await nextTick()
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('Delete failed')
    document.querySelector('[data-testid="dynamic-island"]')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(queue.current.value).toBeNull()
    wrapper.unmount(); queue.dispose()
  })
})

describe('Honest traffic geometry', () => {
  it('uses the real samples with a zero baseline, including all-zero and single-sample results', () => {
    const geometry = trafficGeometry([0, 100, 50])
    expect(geometry.points.map(point => point.value)).toEqual([0, 100, 50])
    expect(geometry.points[0]?.y).toBe(216)
    expect(geometry.points[1]?.y).toBe(36)
    expect(trafficGeometry([0, 0]).points.every(point => point.y === 216)).toBe(true)
    expect(trafficGeometry([10]).points[0]?.x).toBe(460)
    expect(trafficGeometry([]).line).toBe('')
  })
  it('rejects invalid and negative chart values without manufacturing requests', () => {
    expect(trafficGeometry([NaN, Infinity, -20, 5]).points.map(point => point.value)).toEqual([0, 0, 0, 5])
  })
})
