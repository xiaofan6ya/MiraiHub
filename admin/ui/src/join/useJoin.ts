import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 公开入口的验证方式。
 *
 * `auth_url` 与 `official` 的区别在**服务端**，不在渲染层：
 *   · `auth_url` —— 平台给出一个可授权的 https 地址（Marvis 这类）；
 *   · `official`  —— WorkBuddy 的「官方登录接力」：服务端开一个官方登录会话，
 *     把 `plugin_auth_url` 交给页面，用户用同一台手机在官方页完成登录后，
 *     服务端轮询上游拿凭证入库（`admin/platform/workbuddy/public_auth.py:41`）。
 * 两者返回的都是 `auth_url` 字段，前端可以走同一条渲染分支。
 */
type Mode = 'qr' | 'auth_url' | 'official' | 'sms'
export interface JoinPlatform { id: string; label: string; modes: Mode[] }
interface Account { name?: string; type?: string; sec_uid: string }
interface Reply {
  platforms?: JoinPlatform[]; copy?: { title?: string; sub?: string; intro?: string[]; foot?: string }
  session?: string; expires_in?: number; retry_after?: number; poll_interval?: number
  qr_svg_data_uri?: string; qr_base64?: string; auth_url?: string; user_code?: string; need_callback?: boolean
  status?: string; account?: { name?: string }; accounts?: Account[]; message?: string; error?: string
}
class JoinError extends Error {
  constructor(message: string, public retryAfter = 0) { super(message) }
}
export function validPhone(country: string, phone: string) {
  const patterns: Record<string, RegExp> = { '86': /^1[3-9][0-9]{9}$/, '852': /^[4-9][0-9]{7}$/, '853': /^6[0-9]{7}$/ }
  return !!patterns[country]?.test(phone)
}
export function safeAuthUrl(raw: string) {
  try { const url = new URL(raw); return /^https?:$/.test(url.protocol) && !url.username && !url.password ? url.href : '' } catch { return '' }
}
export function useJoin() {
  const platforms = ref<JoinPlatform[]>([]), copy = ref<NonNullable<Reply['copy']>>({})
  const loading = ref(true), busy = ref(false), error = ref(''), stage = ref<'choose' | 'loading' | 'qr' | 'auth' | 'sms' | 'code' | 'select' | 'done' | 'expired'>('choose')
  const selected = ref<JoinPlatform | null>(null), mode = ref<Mode>('qr'), session = ref('')
  const qr = ref(''), authUrl = ref(''), userCode = ref(''), needCallback = ref(false), callback = ref('')
  const country = ref('86'), mobile = ref(''), code = ref(''), password = ref(''), needPassword = ref(false)
  const accounts = ref<Account[]>([]), accountName = ref(''), now = ref(Date.now()), deadline = ref(0), cooldown = ref(0)
  const seconds = computed(() => Math.max(0, Math.ceil((deadline.value - now.value) / 1000)))
  const retrySeconds = computed(() => Math.max(0, Math.ceil((cooldown.value - now.value) / 1000)))
  let epoch = 0, disposed = false, pollTimer: ReturnType<typeof setTimeout> | undefined, clock: ReturnType<typeof setInterval> | undefined
  let pollMs = 3000, failures = 0
  const endpoint = (id: string, action: string) => '/api/open/' + encodeURIComponent(id) + '/' + action
  async function request(path: string, body?: unknown): Promise<Reply> {
    const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', cache: 'no-store', credentials: 'same-origin', headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      const detail = data.detail
      throw new JoinError(typeof detail === 'string' && detail !== 'Not Found' ? detail : detail?.message || (response.status === 404 ? '入口暂不可用，请重新打开邀请链接' : response.status === 429 ? '操作较频繁，请稍后重试' : '请求未完成，请稍后重试'), Number(response.headers.get('Retry-After')) || 0)
    }
    return data
  }
  function cancel(id: string, sid: string) {
    if (id && sid) void fetch(endpoint(id, 'cancel'), { method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session: sid }) }).catch(() => {})
  }
  function stopPoll() { clearTimeout(pollTimer); pollTimer = undefined }
  function reset() {
    epoch++; stopPoll(); cancel(selected.value?.id || '', session.value); session.value = ''
    busy.value = false; error.value = ''; qr.value = ''; authUrl.value = ''; userCode.value = ''; callback.value = ''; needCallback.value = false
    code.value = ''; password.value = ''; needPassword.value = false; accounts.value = []; deadline.value = 0; cooldown.value = 0; failures = 0
  }
  const current = (token: number) => !disposed && token === epoch
  function expire() { stopPoll(); cancel(selected.value?.id || '', session.value); session.value = ''; deadline.value = 0; busy.value = false; stage.value = 'expired'; error.value = '本次验证已过期，请重新开始' }
  function settle(data: Reply): boolean {
    if (data.status === 'done') {
      stopPoll(); session.value = ''; deadline.value = 0; password.value = ''; code.value = ''; callback.value = ''; error.value = ''
      accountName.value = data.account?.name || selected.value?.label || '账号'; stage.value = 'done'; return true
    }
    if (data.status === 'expired') { expire(); return true }
    if (data.status === 'failed') { error.value = data.message || data.error || '验证未完成，请重试'; return true }
    if (data.status === 'select_account') { stopPoll(); accounts.value = data.accounts || []; stage.value = 'select'; return true }
    if (data.status === 'need_password') { needPassword.value = true; stage.value = 'code'; return true }
    return false
  }
  function schedule(token: number) {
    stopPoll()
    if (!current(token) || !session.value || needCallback.value) return
    pollTimer = setTimeout(async () => {
      pollTimer = undefined
      if (!current(token) || !session.value) return
      if (document.hidden) { schedule(token); return }
      if (Date.now() >= deadline.value) { expire(); return }
      try {
        const data = await request(endpoint(selected.value!.id, 'poll') + '?session=' + encodeURIComponent(session.value))
        if (!current(token) || stage.value === 'expired') return
        failures = 0; error.value = ''
        if (!settle(data)) schedule(token)
      } catch (e) {
        if (!current(token) || stage.value === 'expired') return
        failures++; error.value = failures >= 3 ? '网络连接不稳定，正在自动重试…' : ''; schedule(token)
      }
    }, Math.min(8000, pollMs + failures * 1000))
  }
  function acceptSession(data: Reply, token: number, id: string) {
    if (!current(token)) { cancel(id, data.session || ''); return false }
    if (!data.session) throw new Error('未能创建验证会话，请重试')
    session.value = data.session; now.value = Date.now(); deadline.value = now.value + Math.max(1, Number(data.expires_in) || 300) * 1000
    return true
  }
  async function choose(platform: JoinPlatform, nextMode?: Mode) {
    reset(); selected.value = platform
    mode.value = nextMode || (platform.modes.includes('qr') ? 'qr'
      : platform.modes.includes('auth_url') ? 'auth_url'
      // WorkBuddy 官方登录接力：手机号之外的另一条主路径，别漏。
      : platform.modes.includes('official') ? 'official'
      : 'sms')
    if (!platform.modes.includes(mode.value)) { stage.value = 'choose'; error.value = '该平台暂未开放此验证方式'; return }
    if (mode.value === 'sms') { stage.value = 'sms'; return }
    const token = epoch, id = platform.id; busy.value = true; stage.value = 'loading'
    try {
      const data = await request(endpoint(id, 'begin'), { mode: mode.value })
      if (!acceptSession(data, token, id)) return
      qr.value = data.qr_svg_data_uri && /^data:image\/svg\+xml[;,]/.test(data.qr_svg_data_uri) ? data.qr_svg_data_uri : data.qr_base64 ? 'data:image/png;base64,' + data.qr_base64 : ''
      authUrl.value = safeAuthUrl(data.auth_url || ''); userCode.value = data.user_code || ''; needCallback.value = !!data.need_callback
      if (!qr.value && !authUrl.value && !userCode.value) throw new Error('未获取到有效的授权信息，请重新开始')
      pollMs = Math.min(8000, Math.max(2500, Number(data.poll_interval) || 3000))
      stage.value = qr.value ? 'qr' : 'auth'; schedule(token)
    } catch (e) { if (current(token)) { error.value = e instanceof Error ? e.message : '创建会话失败'; stage.value = 'expired' } }
    finally { if (current(token)) busy.value = false }
  }
  async function sendSms() {
    if (busy.value || retrySeconds.value || !selected.value) return
    if (!validPhone(country.value, mobile.value)) { error.value = '请输入所选地区的有效手机号'; return }
    const token = epoch, id = selected.value.id; busy.value = true; error.value = ''
    try {
      const data = await request(endpoint(id, 'sms/send'), { mobile: mobile.value, country: country.value })
      if (!current(token)) { cancel(id, data.session || ''); return }
      cancel(id, session.value)
      if (!acceptSession(data, token, id)) return
      code.value = ''; password.value = ''; needPassword.value = false; stage.value = 'code'
      cooldown.value = Date.now() + Math.max(1, Number(data.retry_after) || 60) * 1000
    } catch (e) {
      if (current(token)) { error.value = e instanceof Error ? e.message : '发送失败'; if (e instanceof JoinError && e.retryAfter) cooldown.value = Date.now() + e.retryAfter * 1000 }
    } finally { if (current(token)) busy.value = false }
  }
  async function submit(action: 'sms/verify' | 'select' | 'submit', extra: Record<string, string>) {
    if (busy.value || !selected.value || !session.value) return
    if (Date.now() >= deadline.value) { expire(); return }
    const token = epoch; busy.value = true; error.value = ''
    try { const data = await request(endpoint(selected.value.id, action), { session: session.value, ...extra }); if (current(token) && stage.value !== 'expired') { if (!settle(data)) error.value = '验证尚未完成，请确认后重试' } }
    catch (e) { if (current(token)) error.value = e instanceof Error ? e.message : '验证失败' }
    finally { if (current(token)) busy.value = false }
  }
  function verify() {
    // 短信验证码是 4–8 位纯数字（旧版 join.html 的 maxlength=8 + 剔除非数字 + ≥4 位）。
    // 只判「非空」会把 1 位数字或粘贴进来的乱码直接送到上游，白耗一次配对。
    const digit = code.value.trim()
    if (!digit || digit.length < 4) { error.value = '请输入收到的验证码'; return }
    if (!/^\d+$/.test(digit)) { error.value = '验证码只包含数字，请重新输入'; return }
    if (needPassword.value && password.value.length < 6) { error.value = '请输入至少 6 位的账号密码'; return }
    return submit('sms/verify', { code: digit, password: password.value })
  }
  function submitCallback() { if (callback.value.trim()) return submit('submit', { callback: callback.value.trim() }) }
  function back() { reset(); selected.value = null; stage.value = 'choose' }
  async function load() {
    const token = ++epoch; loading.value = true; error.value = ''
    try { const data = await request('/api/open/platforms'); if (current(token)) { platforms.value = (data.platforms || []).filter(p => p.modes?.some(m => ['qr', 'auth_url', 'official', 'sms'].includes(m))); copy.value = data.copy || {} } }
    catch (e) { if (current(token)) error.value = e instanceof Error ? e.message : '加载入口失败' }
    finally { if (current(token)) loading.value = false }
  }
  onMounted(() => { void load(); clock = setInterval(() => { now.value = Date.now(); if (session.value && deadline.value && now.value >= deadline.value) expire() }, 1000) })
  onBeforeUnmount(() => { disposed = true; reset(); clearInterval(clock) })
  return { platforms, copy, loading, busy, error, stage, selected, mode, qr, authUrl, userCode, needCallback, callback, country, mobile, code, password, needPassword, accounts, accountName, seconds, retrySeconds, choose, back, load, sendSms, verify, submitCallback, selectAccount: (id: string) => submit('select', { sec_uid: id }) }
}
