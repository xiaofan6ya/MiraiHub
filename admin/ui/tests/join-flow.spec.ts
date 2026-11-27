import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { useJoin, safeAuthUrl, validPhone } from '@/join/useJoin'

let wrapper: VueWrapper | undefined, api: ReturnType<typeof useJoin>
const fetchMock = vi.fn()
const qrPlatform = { id: 'workbuddy', label: 'WorkBuddy', modes: ['qr', 'sms'] as ('qr' | 'sms')[] }
const smsPlatform = { id: 'minimax', label: 'MiniMax', modes: ['sms'] as 'sms'[] }
const response = (data: unknown, status = 200, retryAfter = '') => new Response(JSON.stringify(data), { status, headers: retryAfter ? { 'Retry-After': retryAfter } : {} })
async function start() {
  wrapper = mount(defineComponent({ setup() { api = useJoin(); return () => null } }))
  await flushPromises()
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-27T08:00:00Z'))
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockImplementation(async (url: string) => response(url.endsWith('/platforms') ? { platforms: [qrPlatform, smsPlatform], copy: { title: '测试标题' } } : { status: 'cancelled' }))
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); fetchMock.mockReset() })

describe('public join flow', () => {
  it('loads platform copy without starting an authorization before selection', async () => {
    await start(); expect(api.platforms.value).toHaveLength(2); expect(api.copy.value.title).toBe('测试标题'); expect(api.stage.value).toBe('choose'); expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('keeps supported phone regions and rejects malformed numbers and unsafe auth URLs', () => {
    expect(validPhone('86', '13800138000')).toBe(true); expect(validPhone('852', '51234567')).toBe(true); expect(validPhone('853', '61234567')).toBe(true)
    expect(validPhone('86', '51234567')).toBe(false); expect(validPhone('1', '1234567890')).toBe(false)
    for (const url of ['javascript:alert(1)', 'data:text/html,x', '//example.com', 'https://user:pass@example.com']) expect(safeAuthUrl(url)).toBe('')
    expect(safeAuthUrl('https://example.com/auth')).toBe('https://example.com/auth')
  })
  it('polls QR authorization to completion and clears its session and timers', async () => {
    await start(); fetchMock.mockResolvedValueOnce(response({ session: 'qr-1', qr_base64: 'abc', expires_in: 30, poll_interval: 2500 }))
    await api.choose(qrPlatform); expect(api.stage.value).toBe('qr'); expect(api.qr.value).toBe('data:image/png;base64,abc')
    fetchMock.mockResolvedValueOnce(response({ status: 'pending' })); await vi.advanceTimersByTimeAsync(2500)
    fetchMock.mockResolvedValueOnce(response({ status: 'done', account: { name: '<script>safe text</script>' } })); await vi.advanceTimersByTimeAsync(2500)
    expect(api.stage.value).toBe('done'); expect(api.accountName.value).toBe('<script>safe text</script>'); const count = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(30000); expect(fetchMock).toHaveBeenCalledTimes(count); expect(api.seconds.value).toBe(0)
  })
  it('cancels a late begin response after the user changes platform', async () => {
    await start(); let resolve!: (value: Response) => void
    fetchMock.mockImplementationOnce(() => new Promise<Response>(r => { resolve = r }))
    const pending = api.choose(qrPlatform); await api.choose(smsPlatform)
    resolve(response({ session: 'late-session', qr_base64: 'abc' })); await pending
    expect(api.stage.value).toBe('sms'); expect(api.selected.value?.id).toBe('minimax'); expect(api.qr.value).toBe('')
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/workbuddy/cancel') && JSON.parse(init.body).session === 'late-session')).toBe(true)
  })
  it('handles SMS password and multi-account branches without creating a second session', async () => {
    await start(); await api.choose(smsPlatform); api.mobile.value = '13800138000'
    fetchMock.mockResolvedValueOnce(response({ session: 'sms-1', expires_in: 120, retry_after: 60 })); await api.sendSms()
    expect(api.stage.value).toBe('code'); expect(api.retrySeconds.value).toBe(60); const sent = fetchMock.mock.calls.length; await api.sendSms(); expect(fetchMock).toHaveBeenCalledTimes(sent)
    api.code.value = '123456'; fetchMock.mockResolvedValueOnce(response({ status: 'need_password' })); await api.verify(); expect(api.needPassword.value).toBe(true)
    api.password.value = 'short'; await api.verify(); expect(api.error.value).toContain('6')
    api.password.value = 'correct-password'; fetchMock.mockResolvedValueOnce(response({ status: 'select_account', accounts: [{ name: 'A', sec_uid: 'account-1' }] })); await api.verify(); expect(api.stage.value).toBe('select')
    fetchMock.mockResolvedValueOnce(response({ status: 'done', account: { name: 'A' } })); await api.selectAccount('account-1'); expect(api.stage.value).toBe('done'); expect(api.password.value).toBe(''); expect(api.code.value).toBe('')
  })
  it('respects Retry-After on send errors and lets the user retry afterwards', async () => {
    await start(); await api.choose(smsPlatform); api.mobile.value = '13800138000'; fetchMock.mockResolvedValueOnce(response({ detail: { message: '稍后重试' } }, 429, '3'))
    await api.sendSms(); expect(api.error.value).toBe('稍后重试'); expect(api.retrySeconds.value).toBe(3); const count = fetchMock.mock.calls.length; await api.sendSms(); expect(fetchMock).toHaveBeenCalledTimes(count)
    await vi.advanceTimersByTimeAsync(3000); expect(api.retrySeconds.value).toBe(0)
  })
  it('supports callback submission and never polls a callback-only platform', async () => {
    await start(); fetchMock.mockResolvedValueOnce(response({ session: 'auth-1', auth_url: 'https://example.com/login', need_callback: true, expires_in: 60 }))
    await api.choose({ id: 'marvis', label: 'Marvis', modes: ['auth_url'] }); expect(api.needCallback.value).toBe(true); const count = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000); expect(fetchMock).toHaveBeenCalledTimes(count)
    api.callback.value = 'https://example.com/callback?code=local'; fetchMock.mockResolvedValueOnce(response({ status: 'failed', error: '回调无效' })); await api.submitCallback(); expect(api.error.value).toBe('回调无效')
    fetchMock.mockResolvedValueOnce(response({ status: 'done' })); await api.submitCallback(); expect(api.stage.value).toBe('done'); expect(api.callback.value).toBe('')
  })
  it('pauses polling in hidden documents and expires locally', async () => {
    await start(); fetchMock.mockResolvedValueOnce(response({ session: 'qr-1', qr_base64: 'abc', expires_in: 5, poll_interval: 2500 })); await api.choose(qrPlatform)
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true); const count = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(2600); expect(fetchMock).toHaveBeenCalledTimes(count); hidden.mockRestore()
    await vi.advanceTimersByTimeAsync(2400); expect(api.stage.value).toBe('expired'); expect(api.busy.value).toBe(false)
    api.back(); expect(api.stage.value).toBe('choose'); expect(api.selected.value).toBeNull()
  })
  it('ignores a poll result received after the session expires', async () => {
    await start(); fetchMock.mockResolvedValueOnce(response({ session: 'qr-1', qr_base64: 'abc', expires_in: 4, poll_interval: 2500 })); await api.choose(qrPlatform)
    let resolve!: (value: Response) => void; fetchMock.mockImplementationOnce(() => new Promise<Response>(r => { resolve = r }))
    await vi.advanceTimersByTimeAsync(2500); await vi.advanceTimersByTimeAsync(1500); expect(api.stage.value).toBe('expired')
    resolve(response({ status: 'done', account: { name: 'late-account' } })); await flushPromises(); expect(api.stage.value).toBe('expired'); expect(api.accountName.value).toBe('')
  })
  it('cancels current session on unmount and avoids orphaning a late send response', async () => {
    await start(); await api.choose(smsPlatform); api.mobile.value = '13800138000'; let resolve!: (value: Response) => void
    fetchMock.mockImplementationOnce(() => new Promise<Response>(r => { resolve = r })); const sending = api.sendSms()
    wrapper!.unmount(); wrapper = undefined; resolve(response({ session: 'late-sms', expires_in: 300 })); await sending
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/minimax/cancel') && JSON.parse(init.body).session === 'late-sms')).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })
  it('shows an unavailable invitation error and can reload without admin login redirects', async () => {
    fetchMock.mockResolvedValueOnce(response({ detail: 'Not Found' }, 404)); await start(); expect(api.error.value).toContain('邀请链接'); expect(api.loading.value).toBe(false)
    await api.load(); expect(api.platforms.value).toHaveLength(2); expect(api.error.value).toBe('')
  })
})
