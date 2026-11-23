/**
 * 添加账号（S26）：能力声明 + 各添加方式的接口适配。
 *
 * 旧版实现散在：console-app.js（openAddAccount / submitAddAccount / import* / scan*）、
 * phone-session.js（短信登录）、platform-actions.js（平台动作声明）。
 * 后端：admin/routers/accounts.py（POST /api/accounts、/batch、/import-async、/import-local、
 * /scan-local）与各平台 /api/<平台>/auth/*（krowork / loomy / marvis / raccoon / wps-lingxi）。
 *
 * 设计原则（P6）：**按平台能力声明显示，不在页面里散落 if (platform === 'xxx')**。
 * 平台特有的授权流程集中在本文件的 PLATFORM_AUTH_FLOWS 表里，页面只问 addMethodsFor()。
 */
import { http } from './http'
import { fmtTime } from '@/utils/format'
import { t as defaultTranslate, type Translate } from '@/i18n'

/* ------------------------------------------------------------------ *
 * 类型
 * ------------------------------------------------------------------ */

/** 页面添加方式（对应「添加账号 ▾」的下拉项）。 */
export type AddMethodKey = 'phone' | 'authorize' | 'manual' | 'import' | 'scan'

export interface AddMethod {
  key: AddMethodKey
  label: string
  /** 不可用原因（有值时按钮禁用并显示原因，而不是消失——用户能知道为什么）。 */
  disabledReason?: string
}

/** /api/platforms 里本页用到的字段（PlatformSpec 的子集）。 */
export interface PlatformAddSpec {
  id?: string
  label?: string
  storage_platform?: string
  auth_mode?: string
  supports_local_scan?: boolean
  actions?: string[]
  notes?: string
}

/** 短信登录表单。 */
export interface PhoneForm {
  country: string
  phone: string
  code: string
  /** 新号注册需要补密码（loomy need_password）。 */
  password?: string
  name?: string
}

/** 浏览器授权的步骤状态。 */
export type AuthState = 'idle' | 'opened' | 'pending' | 'scanned' | 'authorized' | 'failed'

export interface BrowserAuthView {
  state: AuthState
  /** 是否已拿到可打开的授权地址。 */
  url: string
  accountId: number
  message: string
}

/** 导入解析结果的一行（预览表格）。 */
export interface ImportPreviewRow {
  index: number
  name: string
  uid: string
  /** 该行最终提交的凭证 JSON 原文。 */
  authJson: string
  /** 结构是否像凭证（能 JSON.parse 且非空）。 */
  valid: boolean
  reason: string
  selected: boolean
}

export interface ImportParseResult {
  rows: ImportPreviewRow[]
  /** 可以直接提交的 items（只含结构有效的行）。 */
  items: Array<{ name: string; auth_json: string }>
  /** 非 WB 平台要用原文交给后端（各家备份形态不同）。 */
  text: string
  valid: number
  invalid: number
}

/** GET /api/accounts/scan-local 的一项。 */
export interface ScanLocalItem {
  file?: string
  name?: string
  uid?: string
  nickname?: string
  expires?: string
  expires_at?: string | number
  in_pool?: boolean
  pooled?: boolean
  exists?: boolean
  error?: string
}

export interface ScanLocalPayload {
  dir: string
  exists: boolean
  active_uid: string | null
  items: ScanLocalItem[]
  pool_total?: number
  pool_only?: number
  pool_only_sample?: string[]
}

export interface ScanPreviewRow extends Record<string, unknown> {
  file: string
  name: string
  uid: string
  expires: string
  inPool: boolean
  active: boolean
  error: string
  selected: boolean
}

/* ------------------------------------------------------------------ *
 * 平台授权流程声明（平台特有逻辑集中在这里）
 * ------------------------------------------------------------------ */

export interface AuthFlowSpec {
  kind: 'phone' | 'browser'
  /** 手机号登录：先准备会话（krowork 需要先 begin 拿 auth_id）。 */
  prepare?: { path: string; body?: (form: PhoneForm) => Record<string, unknown> }
  /** 发送验证码。 */
  send?: { path: string; body: (form: PhoneForm, session: Record<string, unknown>) => Record<string, unknown> }
  /** 提交验证码（成功后账号入池）。 */
  verify?: { path: string; body: (form: PhoneForm, session: Record<string, unknown>) => Record<string, unknown> }
  /** 浏览器授权：发起。 */
  begin?: { path: string; body?: (platformId: string) => Record<string, unknown> }
  /** 浏览器授权：轮询会话。 */
  poll?: { path: string }
}

/**
 * 各平台真实的授权接口（来自 admin/platform/<平台>/routers/accounts.py）。
 *
 * 没收录的平台不会出现对应的添加方式 —— 宁可不显示，也不给一个点了 404 的按钮。
 */
export const PLATFORM_AUTH_FLOWS: Record<string, AuthFlowSpec> = {
  // Loomy：手机号 + 验证码（老号直接登录、新号需要补密码）
  loomy: {
    kind: 'phone',
    send: { path: '/api/loomy/auth/sms', body: (form) => ({ phone: form.phone }) },
    verify: {
      path: '/api/loomy/auth/verify',
      body: (form, session) => ({
        phone: form.phone,
        code: form.code,
        msgid: session.msgid || session.id || '',
        ...(form.password ? { password: form.password } : {}),
        ...(form.name ? { name: form.name } : {}),
      }),
    },
  },
  // krowork：先 begin 拿 auth_id，再发码 / 提交（登录态只有 30 分钟）
  krowork: {
    kind: 'phone',
    prepare: { path: '/api/krowork/auth/begin', body: () => ({ realm: 'cn' }) },
    send: {
      path: '/api/krowork/auth/send-code',
      body: (form, session) => ({
        auth_id: session.auth_id || session.id || '',
        phone: form.phone,
        country: form.country || '+86',
      }),
    },
    verify: {
      path: '/api/krowork/auth/submit',
      body: (form, session) => ({
        auth_id: session.auth_id || session.id || '',
        phone: form.phone,
        code: form.code,
        country: form.country || '+86',
      }),
    },
  },
  // 二维码 / 设备授权（打开授权地址 → 轮询 → 入池）
  marvis: {
    kind: 'browser',
    begin: { path: '/api/marvis/auth/begin' },
    poll: { path: '/api/marvis/auth/poll' },
  },
  raccoon: {
    kind: 'browser',
    begin: { path: '/api/raccoon/auth/begin' },
    poll: { path: '/api/raccoon/auth/poll' },
  },
  'wps-lingxi': {
    kind: 'browser',
    begin: { path: '/api/wps-lingxi/auth/start' },
    poll: { path: '/api/wps-lingxi/auth/poll' },
  },
}

/** 平台 id 归一：trae-cn → trae（授权流程按落库平台登记）。 */
export function storageOf(spec: PlatformAddSpec | null | undefined): string {
  const raw = String(spec?.storage_platform || spec?.id || '').toLowerCase()
  if (!raw) return ''
  return raw.replace(/-cn$/, '').replace(/-intl$/, '')
}

export function flowOf(spec: PlatformAddSpec | null | undefined): AuthFlowSpec | null {
  const storage = storageOf(spec)
  if (!storage) return null
  return PLATFORM_AUTH_FLOWS[storage] || null
}

/**
 * 「添加账号 ▾」的方式清单。
 *
 * 规则（与 P6 一致）：
 *   · 手机号登录 / 浏览器授权：只有该平台真的实现了对应接口才出现；
 *   · 手动添加 / 导入：所有平台都有（后端 POST /api/accounts、/import-async 通用）；
 *   · 扫描本机：只在 supports_local_scan 为真时出现（服务端读本机登录目录）；
 *   · import-only 平台（auth_mode）不显示授权类入口，只留导入/手动。
 */
export function addMethodsFor(
  spec: PlatformAddSpec | null | undefined,
  translate?: Translate,
): AddMethod[] {
  const tr = translate ?? defaultTranslate
  const methods: AddMethod[] = []
  const flow = flowOf(spec)
  const authMode = String(spec?.auth_mode || '').toLowerCase()
  const importOnly = authMode === 'import-only'
  if (flow?.kind === 'phone' && flow.send && flow.verify && !importOnly) {
    methods.push({ key: 'phone', label: tr('add.method.phone') })
  }
  if (flow?.kind === 'browser' && flow.begin && flow.poll && !importOnly) {
    methods.push({ key: 'authorize', label: tr('add.method.authorize') })
  }
  methods.push({ key: 'manual', label: tr('add.method.manual') })
  methods.push({ key: 'import', label: tr('add.method.import') })
  if (spec?.supports_local_scan && !importOnly) {
    methods.push({ key: 'scan', label: tr('add.method.scan') })
  }
  return methods
}

/** 该方式是否可用（页面用它决定禁用与提示，而不是静默隐藏）。 */
export function methodEnabled(spec: PlatformAddSpec | null | undefined, key: AddMethodKey): boolean {
  return addMethodsFor(spec).some((method) => method.key === key)
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

export interface AddAccountResult {
  id: number
  name: string
  ok?: boolean
  duplicated?: boolean
  verified?: boolean
  balance_remain?: number
  message?: string
}

/** 手动添加：凭证由服务端做结构校验 + 真实上游验证。 */
export function addAccountManually(input: { name?: string; auth_json: string }): Promise<AddAccountResult> {
  return http<AddAccountResult>('POST', '/api/accounts', {
    body: { name: input.name || '', auth_json: input.auth_json },
  })
}

export interface ImportAsyncInput {
  storage: string
  items?: Array<{ name?: string; auth_json: string }>
  text?: string
  verify?: boolean
}

/** 导入：后台任务（返回 job 快照，进度由页面的 JobProgress 轮询 /api/accounts/job/{id}）。 */
export function importAccountsAsync(input: ImportAsyncInput): Promise<Record<string, unknown>> {
  return http('POST', '/api/accounts/import-async', {
    body: {
      storage: input.storage,
      items: input.items || [],
      text: input.text || '',
      verify: input.verify !== false,
    },
  })
}

export function scanLocalAccounts(): Promise<ScanLocalPayload> {
  return http<ScanLocalPayload>('GET', '/api/accounts/scan-local')
}

export interface ImportLocalResult {
  ok?: boolean
  added?: number
  updated?: number
  skipped?: number
  errors?: string[]
  total?: number
}

/** 把本机登录目录里的 .info 读入号池（服务端读文件，token 不下发前端）。 */
export function importLocalAccounts(files: string[], all = false): Promise<ImportLocalResult> {
  return http<ImportLocalResult>('POST', '/api/accounts/import-local', {
    body: { files, all },
  })
}

/** 手机号登录：准备会话（可选，krowork 用）。 */
export function preparePhoneFlow(spec: PlatformAddSpec | null | undefined, form: PhoneForm): Promise<Record<string, unknown>> {
  const flow = flowOf(spec)
  if (!flow?.prepare) return Promise.resolve({})
  return http('POST', flow.prepare.path, { body: flow.prepare.body ? flow.prepare.body(form) : {} })
}

export function sendPhoneCode(
  spec: PlatformAddSpec | null | undefined,
  form: PhoneForm,
  session: Record<string, unknown>,
  translate?: Translate,
): Promise<Record<string, unknown>> {
  const tr = translate ?? defaultTranslate
  const flow = flowOf(spec)
  if (!flow?.send) return Promise.reject(new Error(tr('add.phone.noSms')))
  return http('POST', flow.send.path, { body: flow.send.body(form, session) })
}

export function verifyPhoneCode(
  spec: PlatformAddSpec | null | undefined,
  form: PhoneForm,
  session: Record<string, unknown>,
  translate?: Translate,
): Promise<Record<string, unknown>> {
  const tr = translate ?? defaultTranslate
  const flow = flowOf(spec)
  if (!flow?.verify) return Promise.reject(new Error(tr('add.phone.noSms')))
  return http('POST', flow.verify.path, { body: flow.verify.body(form, session) })
}

export function beginBrowserAuth(
  spec: PlatformAddSpec | null | undefined,
  translate?: Translate,
): Promise<Record<string, unknown>> {
  const tr = translate ?? defaultTranslate
  const flow = flowOf(spec)
  if (!flow?.begin) return Promise.reject(new Error(tr('add.phone.noBrowser')))
  const platformId = String(spec?.id || storageOf(spec))
  return http('POST', flow.begin.path, { body: flow.begin.body ? flow.begin.body(platformId) : {} })
}

export function pollBrowserAuth(
  spec: PlatformAddSpec | null | undefined,
  translate?: Translate,
): Promise<Record<string, unknown>> {
  const tr = translate ?? defaultTranslate
  const flow = flowOf(spec)
  if (!flow?.poll) return Promise.reject(new Error(tr('add.phone.noBrowser')))
  return http('GET', flow.poll.path)
}

/* ------------------------------------------------------------------ *
 * 纯函数：解析 / 归一 / 校验（单测覆盖）
 * ------------------------------------------------------------------ */

function pickString(source: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  }
  return ''
}

/**
 * 导入文本 → 预览行 + 待提交 items。
 *
 * 支持旧版三种粘贴形态：
 *   1. 每行一个 JSON（JSONL）；
 *   2. 一个 JSON 数组；
 *   3. 单个 JSON 对象（含 auth_json 字段，或本身就是凭证）。
 * 结构解析失败的行走 invalid，导入时跳过（不偷偷丢数据）。
 */
export function parseImportText(text: string, translate?: Translate): ImportParseResult {
  const tr = translate ?? defaultTranslate
  const raw = String(text || '').trim()
  if (!raw) return { rows: [], items: [], text: '', valid: 0, invalid: 0 }

  const records: unknown[] = []
  const errors: string[] = []
  try {
    const parsed = JSON.parse(raw) as unknown
    if (Array.isArray(parsed)) records.push(...parsed)
    else records.push(parsed)
  } catch {
    for (const line of raw.split(/\r?\n/)) {
      const item = line.trim().replace(/,$/, '')
      if (!item) continue
      try {
        records.push(JSON.parse(item))
      } catch {
        errors.push(item.slice(0, 60))
      }
    }
  }

  const rows: ImportPreviewRow[] = []
  const items: Array<{ name: string; auth_json: string }> = []
  let index = 0
  for (const record of records) {
    index += 1
    if (!record || typeof record !== 'object') {
      rows.push({
        index,
        name: '',
        uid: '',
        authJson: '',
        valid: false,
        reason: tr('add.import.notObject'),
        selected: false,
      })
      continue
    }
    const object = record as Record<string, unknown>
    const explicit = typeof object.auth_json === 'string' ? object.auth_json : ''
    const authJson = explicit || JSON.stringify(object)
    const credential = explicit ? safeParse(explicit) : object
    const name = pickString(object, ['name', 'nickname', 'display_name'])
    const uid = pickString(credential || {}, ['uid', 'account_id', 'id']) || pickString(object, ['uid'])
    const valid = Boolean(authJson) && Boolean(credential && typeof credential === 'object')
    rows.push({
      index,
      name,
      uid,
      authJson,
      valid,
      reason: valid ? '' : tr('add.import.missingCredential'),
      selected: valid,
    })
    if (valid) items.push({ name, auth_json: authJson })
  }
  for (const error of errors) {
    index += 1
    rows.push({
      index,
      name: '',
      uid: '',
      authJson: '',
      valid: false,
      reason: tr('add.import.parseFailed', { error }),
      selected: false,
    })
  }
  const valid = rows.filter((row) => row.valid).length
  return { rows, items, text: raw, valid, invalid: rows.length - valid }
}

function safeParse(text: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(text) as unknown
    return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** 扫描结果 → 预览行（默认全选可导入项，已在池子里的不勾选）。 */
export function scanPreviewRows(
  payload: ScanLocalPayload | null,
  translate?: Translate,
): ScanPreviewRow[] {
  const tr = translate ?? defaultTranslate
  const activeUid = String(payload?.active_uid || '')
  return (payload?.items || []).map((item, index) => {
    const uid = String(item.uid || '')
    const rawExpires = item.expires ?? item.expires_at ?? ''
    return {
      file: String(item.file || 'item-' + (index + 1)),
      name: String(item.nickname || item.name || uid || tr('page.accounts.scanUnnamed')),
      uid,
      expires: rawExpires ? String(rawExpires) : '',
      inPool: Boolean(item.in_pool ?? item.pooled ?? item.exists),
      active: Boolean(uid && uid === activeUid),
      error: String(item.error || ''),
      selected: !item.error && !(item.in_pool ?? item.pooled ?? item.exists),
    }
  })
}

/** 扫描汇总文案（旧版「发现 N 个，其中 M 个已在池子里」）。 */
export function scanSummary(
  payload: ScanLocalPayload | null,
  rows: ScanPreviewRow[],
  translate?: Translate,
): string {
  if (!payload) return ''
  const tr = translate ?? defaultTranslate
  if (!payload.exists) {
    return tr('add.scan.noDir', { dir: payload.dir || tr('add.scan.unknownPath') })
  }
  return (
    tr('add.scan.found', { count: rows.length }) +
    (payload.pool_only ? tr('add.scan.poolOnly', { count: payload.pool_only }) : '') +
    tr('add.scan.dir', { dir: payload.dir })
  )
}

/**
 * 授权接口返回 → 步骤条状态。
 *
 * 各家字段不统一（status / pending / state / ok / authorized），这里做一次归一；
 * 认不出来就当 pending（继续轮询），不会误报成功。
 */
export function authStateOf(payload: Record<string, unknown> | null): BrowserAuthView {
  const data = payload || {}
  const rawStatus = String(data.status || data.state || '').toLowerCase()
  const url = pickString(data, ['auth_url', 'url', 'qr_url', 'qr_svg_url', 'login_url'])
  const message = pickString(data, ['message', 'detail', 'error', 'reason'])
  const accountId = Number(data.account_id || (data.account as Record<string, unknown> | undefined)?.id || 0) || 0
  let state: AuthState = 'pending'
  if (rawStatus === 'failed' || rawStatus === 'error' || data.ok === false) state = 'failed'
  else if (rawStatus === 'authorized' || rawStatus === 'done' || rawStatus === 'success' || rawStatus === 'completed' || accountId) {
    state = 'authorized'
  } else if (rawStatus === 'scanned' || data.scanned === true) state = 'scanned'
  else if (url) state = 'opened'
  else if (rawStatus === 'pending' || rawStatus === 'waiting' || rawStatus === '') state = 'pending'
  return { state, url, accountId, message }
}

/** 步骤条文案（授权流程）。 */
export const AUTH_STEPS = ['打开授权地址', '等待确认 / 回调', '账号入池'] as const

export function authStepIndex(view: BrowserAuthView): number {
  if (view.state === 'idle') return 0
  if (view.state === 'opened' || view.state === 'pending') return 1
  if (view.state === 'scanned') return 1
  if (view.state === 'authorized') return 2
  return 1
}

/** 手机号校验（宽松：只挡空值与明显不合法；真实校验在上游）。 */
export function validatePhoneForm(
  form: PhoneForm,
  needsPassword = false,
  translate?: Translate,
): Record<string, string> {
  const tr = translate ?? defaultTranslate
  const errors: Record<string, string> = {}
  const digits = String(form.phone || '').replace(/\D/g, '')
  if (!digits) errors.phone = tr('add.phone.missing')
  else if (digits.length < 6 || digits.length > 15) errors.phone = tr('add.phone.length')
  if (!String(form.code || '').trim()) errors.code = tr('add.phone.code')
  if (needsPassword && !String(form.password || '').trim()) errors.password = tr('add.phone.password')
  return errors
}

/** 手机号打码（列表/提示里不显示完整号码）。 */
export function maskPhone(phone: string): string {
  const digits = String(phone || '').replace(/\D/g, '')
  if (!digits) return ''
  if (digits.length <= 7) return digits.slice(0, 2) + '****'
  return digits.slice(0, 3) + '****' + digits.slice(-4)
}

/** 手动添加的凭证提示（按平台给出可复制的说明，不含平台分支逻辑的副作用）。 */
export function manualHint(
  spec: PlatformAddSpec | null | undefined,
  translate?: Translate,
): string {
  const tr = translate ?? defaultTranslate
  const storage = storageOf(spec)
  if (storage === 'workbuddy' || storage === 'codebuddy') return tr('add.manual.hintWorkbuddy')
  return tr('add.manual.hintOther')
}

/** 导入时后端要的 storage 键。 */
export function importStorageOf(spec: PlatformAddSpec | null | undefined): string {
  return storageOf(spec) || 'workbuddy'
}

/** 导入方式说明（WB 走 items，其余平台走原文 text）。 */
export function importModeOf(spec: PlatformAddSpec | null | undefined): 'items' | 'text' {
  const storage = importStorageOf(spec)
  return storage === 'workbuddy' || storage === 'codebuddy' ? 'items' : 'text'
}

/** 导入时是否跳过上游验证（只允许「刚导出的备份」场景）。 */
export function defaultVerify(spec: PlatformAddSpec | null | undefined): boolean {
  return importModeOf(spec) === 'items'
}

/** 国家区号候选（旧版 phone-session 的常用区号）。 */
export const COUNTRY_CODES: Array<{ label: string; value: string }> = [
  { label: '+86 中国大陆', value: '+86' },
  { label: '+852 中国香港', value: '+852' },
  { label: '+853 中国澳门', value: '+853' },
  { label: '+886 中国台湾', value: '+886' },
  { label: '+1 美国/加拿大', value: '+1' },
  { label: '+81 日本', value: '+81' },
  { label: '+65 新加坡', value: '+65' },
]

const COUNTRY_KEY: Record<string, Parameters<Translate>[0]> = {
  '+86': 'add.country.cn',
  '+852': 'add.country.hk',
  '+853': 'add.country.mo',
  '+886': 'add.country.tw',
  '+1': 'add.country.na',
  '+81': 'add.country.jp',
  '+65': 'add.country.sg',
}

/** 区号候选（文案跟随语言；value 不变）。 */
export function countryCodes(translate?: Translate): Array<{ label: string; value: string }> {
  const tr = translate ?? defaultTranslate
  return COUNTRY_CODES.map((item) => ({
    label: COUNTRY_KEY[item.value] ? tr(COUNTRY_KEY[item.value]) : item.label,
    value: item.value,
  }))
}

/** 添加成功后的账号文案（旧版 submitAddAccount 的三种结果）。 */
export function addResultText(
  result: AddAccountResult,
  platformLabel: string,
  translate?: Translate,
): string {
  const tr = translate ?? defaultTranslate
  if (result.duplicated) return tr('add.result.duplicated', { id: result.id })
  return tr('add.result.added', {
    name: result.name || tr('add.result.account'),
    platform: platformLabel,
  })
}

/** 导入结果文案（added / updated / skipped / errors）。 */
export function importLocalText(result: ImportLocalResult, translate?: Translate): string {
  const tr = translate ?? defaultTranslate
  const parts = [
    tr('add.import.added', { count: result.added || 0 }),
    tr('add.import.updated', { count: result.updated || 0 }),
    tr('add.import.skipped', { count: result.skipped || 0 }),
  ]
  if (result.errors && result.errors.length) {
    parts.push(tr('add.import.failed', { count: result.errors.length }))
  }
  return parts.join(' · ')
}

/** 扫描项到期时间展示。 */
export function scanExpiresText(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'number') return fmtTime(value)
  return String(value)
}