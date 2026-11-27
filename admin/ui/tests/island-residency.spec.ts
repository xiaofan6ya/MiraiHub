import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createIslandQueue } from '@/components/feedback/islandQueue'
import DynamicIsland from '@/components/feedback/DynamicIsland.vue'

afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

describe('Persistent island regression', () => {
  it('keeps the original deadline when resuming a reason that was never paused', () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    queue.api.info('deadline', { duration: 1000 })
    vi.advanceTimersByTime(400)
    queue.resume('expanded')
    vi.advanceTimersByTime(600)
    console.log('OBSERVED deadline_at_1000ms=' + (queue.current.value?.text ?? 'expired'))
    const observed = queue.current.value
    queue.dispose()
    expect(observed).toBeNull()
  })

  it('expires an expanded notification without requiring a close click', async () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    queue.api.info('A long notification with enough detail to open the expanded panel and reproduce the timer freeze.', { duration: 1000 })
    const wrapper = mount(DynamicIsland, { props: { queue }, attachTo: document.body })
    await nextTick()
    ;(document.querySelector('.island-expand') as HTMLButtonElement)?.click()
    await nextTick()
    vi.advanceTimersByTime(1001)
    await nextTick()
    console.log('OBSERVED expanded_at_1001ms=' + (queue.current.value ? 'stuck' : 'expired'))
    const observed = queue.current.value
    wrapper.unmount(); queue.dispose()
    expect(observed).toBeNull()
  })

  it('starts the next notification after dismissal even if the previous one was hovered', () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    const first = queue.api.info('first', { duration: 1000 })
    queue.api.success('second', { duration: 1000 })
    queue.pause('hover')
    first.destroy()
    vi.advanceTimersByTime(1001)
    console.log('OBSERVED next_at_1001ms=' + (queue.current.value?.text ?? 'expired'))
    const observed = queue.current.value
    queue.dispose()
    expect(observed).toBeNull()
  })

  it('stays visible while idle and toggles the same panel on repeated clicks', async () => {
    const queue = createIslandQueue()
    const wrapper = mount(DynamicIsland, { props: { queue, persistent: true } as any, attachTo: document.body })
    await nextTick()
    const island = document.querySelector('[data-testid="dynamic-island"]')
    console.log('OBSERVED persistent_idle=' + Boolean(island))
    try {
      expect(island).not.toBeNull()
      const toggle = document.querySelector('.island-toggle') as HTMLButtonElement
      toggle.click(); await nextTick()
      expect(toggle.getAttribute('aria-expanded')).toBe('true')
      toggle.click(); await nextTick()
      expect(toggle.getAttribute('aria-expanded')).toBe('false')
      toggle.click(); await nextTick()
      document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
      await nextTick()
      expect(toggle.getAttribute('aria-expanded')).toBe('false')
    } finally { wrapper.unmount(); queue.dispose() }
  })

  it('cleans up focus when expiration collapses a persistent panel and then accepts another message', async () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    queue.api.info('A long notification that can be expanded to focus a control inside its details panel.', { duration: 1000 })
    const wrapper = mount(DynamicIsland, { props: { queue, persistent: true } as any, slots: { panel: '<button class="panel-focus">Focus</button>' }, attachTo: document.body })
    await nextTick()
    ;(document.querySelector('.island-expand') as HTMLButtonElement)?.click()
    await nextTick()
    ;(document.querySelector('.panel-focus') as HTMLButtonElement)?.focus()
    vi.advanceTimersByTime(1001); await nextTick()
    const firstExpired = !queue.current.value
    queue.api.success('Next operation', { duration: 1000 })
    await nextTick(); vi.advanceTimersByTime(1001); await nextTick()
    console.log('OBSERVED focus_expiry=' + firstExpired + ',next_expiry=' + !queue.current.value)
    const final = queue.current.value
    wrapper.unmount(); queue.dispose()
    expect(firstExpired).toBe(true)
    expect(final).toBeNull()
  })

  it('retains hidden-tab pause and explicit persistent loading', () => {
    vi.useFakeTimers()
    const queue = createIslandQueue()
    queue.api.info('hidden', { duration: 1000 })
    vi.advanceTimersByTime(400); queue.pause('hidden')
    vi.advanceTimersByTime(5000)
    expect(queue.current.value?.text).toBe('hidden')
    queue.resume('hidden'); vi.advanceTimersByTime(600)
    expect(queue.current.value).toBeNull()
    queue.api.loading('Working', { duration: 0 })
    vi.advanceTimersByTime(60000)
    console.log('OBSERVED persistent_loading=' + queue.current.value?.text)
    expect(queue.current.value?.text).toBe('Working')
    queue.dispose()
  })
})
