import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AddAccountDrawer from '@/pages/accounts/AddAccountDrawer.vue'
import { beginBrowserAuth, pollBrowserAuth } from '@/api/account-add'

vi.mock('@/api/account-add', async (original) => ({
  ...await original<typeof import('@/api/account-add')>(),
  beginBrowserAuth: vi.fn(),
  pollBrowserAuth: vi.fn(),
}))
vi.mock('@/composables', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }))

const begin = vi.mocked(beginBrowserAuth)
const poll = vi.mocked(pollBrowserAuth)
const mounted: ReturnType<typeof mount>[] = []
function drawer(id: string) {
  const wrapper = mount(AddAccountDrawer, {
    props: { show: true, kind: 'authorize', spec: { id }, platformName: id },
    global: { stubs: {
      FormDrawer: { props: ['show'], template: '<section v-if="show"><slot/><button class="start-auth" @click="$emit(\'submit\')">Start</button></section>' },
      FormField: { template: '<div><slot/></div>' },
      Btn: { template: '<button><slot/></button>' },
      NInput: true,
    } },
  })
  mounted.push(wrapper)
  return wrapper
}
beforeEach(() => {
  vi.useFakeTimers()
  begin.mockReset()
  poll.mockReset()
  vi.spyOn(window, 'open').mockReturnValue(null)
})
afterEach(() => {
  mounted.splice(0).forEach(wrapper => wrapper.unmount())
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('issue #122 authorization drawer lifecycle', () => {
  it('keeps the device code/link and session token across pending polls, then adds once', async () => {
    begin.mockResolvedValue({ auth_id: 'device-session', verification_uri: 'https://example.test/activate', user_code: 'ABCD', interval: 2 })
    poll.mockResolvedValueOnce({ status: 'pending' }).mockResolvedValueOnce({ status: 'done', account: { account_id: 7 } })
    const wrapper = drawer('minimax')
    await wrapper.find('.start-auth').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    expect(poll).toHaveBeenCalledWith(expect.objectContaining({ id: 'minimax' }), expect.objectContaining({ auth_id: 'device-session' }), expect.any(Function))
    expect(wrapper.find('.acc-auth-url').text()).toContain('https://example.test/activate')
    expect(wrapper.text()).toContain('ABCD')
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.emitted('added')).toHaveLength(1)
    expect(wrapper.emitted('update:show')).toEqual([[false]])
    await vi.advanceTimersByTimeAsync(10000)
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('renders the Codar same-origin QR image instead of opening it as a login page', async () => {
    begin.mockResolvedValue({ auth_id: 'qr +/session' })
    poll.mockResolvedValue({ status: 'pending' })
    const wrapper = drawer('codar')
    await wrapper.find('.start-auth').trigger('click')
    await flushPromises()
    expect(wrapper.find('img.acc-auth-qr').attributes('src')).toBe('/api/codar/auth/qr.png?auth_id=qr%20%2B%2Fsession')
    expect(window.open).not.toHaveBeenCalled()
  })

  it('ignores a late poll response after closing and stops further requests', async () => {
    begin.mockResolvedValue({ nonce: 'trae-session', url: 'https://example.test/login' })
    let resolvePoll!: (value: Record<string, unknown>) => void
    poll.mockReturnValue(new Promise(resolve => { resolvePoll = resolve }))
    const wrapper = drawer('trae')
    await wrapper.find('.start-auth').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    expect(poll).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ show: false })
    resolvePoll({ status: 'done', account: { account_id: 7 } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10000)
    expect(wrapper.emitted('added')).toBeUndefined()
    expect(poll).toHaveBeenCalledTimes(1)
  })
})
