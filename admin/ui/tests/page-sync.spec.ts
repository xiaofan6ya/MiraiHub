/**
 * S07 同步设置：接口适配 / 数据转换纯函数单测。
 * 契约：docs/ui-v2/contracts/sync.md（响应形状全部按 admin/routers/sync.py 的真实返回构造）。
 */
import { describe, expect, it } from 'vitest'
import { maskSecret } from '@/utils/format'
import {
  SYNC_RECORD_KEY,
  clearSyncRecord,
  describeSyncConfig,
  formatPushSummary,
  generateSyncSecret,
  hasStoredSecret,
  isHttpUrl,
  isSecretMasked,
  isSyncFormDirty,
  looksMaskedSecret,
  needsAdminPassword,
  normalizeRemoteUrl,
  normalizeSyncInput,
  plainSecretOf,
  pushRecordFromError,
  pushRecordFromResult,
  readSyncRecord,
  remoteUrlProblem,
  summarizePushResult,
  writeSyncRecord,
  type SyncConfigPayload,
  type SyncRecordStorage,
} from '@/api/sync'

/** `secrets.token_hex(32)` 的形状：64 位小写十六进制。 */
const SECRET = '9f2c1d5e7a8b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c'

/** `GET /api/sync/config` 的真实响应（save_config 会把 URL 去掉末尾 /）。 */
const CONFIG: SyncConfigPayload = {
  remote_url: 'https://online.example.com',
  secret: SECRET,
  secret_masked: '********3b4c',
  is_auto_generated: false,
}

/** `POST /api/sync/push` 的真实响应：本地条数 + 线上 /api/sync/receive 的 upsert 结果。 */
const PUSH_PAYLOAD = {
  ok: true,
  remote: 'https://online.example.com',
  accounts: 12,
  models: 7,
  client_profile: true,
  profile_fields: ['cli_version', 'desktop_version', 'headers'],
  remote_result: {
    ok: true,
    accounts: { added: 3, updated: 9 },
    models: { added: 1, updated: 6 },
    client_profile: {
      applied: true,
      source: 'saved',
      fields: ['desktop_version', 'user_agent'],
      errors: [],
    },
  },
}

function fakeStorage(initial: Record<string, string> = {}): SyncRecordStorage & {
  data: Map<string, string>
} {
  const data = new Map<string, string>(Object.entries(initial))
  return {
    data,
    getItem: (key: string) => (data.has(key) ? (data.get(key) as string) : null),
    setItem: (key: string, value: string) => {
      data.set(key, value)
    },
    removeItem: (key: string) => {
      data.delete(key)
    },
  }
}

describe('远程 URL 归一（对齐后端 remote_url.rstrip("/")）', () => {
  it('去首尾空白与末尾斜杠，空值保持空串', () => {
    expect(normalizeRemoteUrl('  https://online.example.com/  ')).toBe('https://online.example.com')
    expect(normalizeRemoteUrl('http://127.0.0.1:8790///')).toBe('http://127.0.0.1:8790')
    expect(normalizeRemoteUrl(null)).toBe('')
    expect(normalizeRemoteUrl(undefined)).toBe('')
    expect(normalizeRemoteUrl('')).toBe('')
  })

  it('isHttpUrl 只认 http(s) 绝对地址（仅作字段提示，不拦截保存）', () => {
    expect(isHttpUrl('https://online.example.com')).toBe(true)
    expect(isHttpUrl('http://127.0.0.1:8790/')).toBe(true)
    expect(isHttpUrl('online.example.com')).toBe(false)
    expect(isHttpUrl('ftp://online.example.com')).toBe(false)
    expect(isHttpUrl('')).toBe(false)
  })

  it('normalizeSyncInput：URL 归一 + 密钥 trim（后端 .strip()）', () => {
    expect(normalizeSyncInput({ remoteUrl: ' https://online.example.com// ', secret: '  abc  ' })).toEqual({
      remote_url: 'https://online.example.com',
      secret: 'abc',
    })
    expect(normalizeSyncInput({ remoteUrl: '', secret: '' })).toEqual({ remote_url: '', secret: '' })
  })
})

describe('未保存判定 isSyncFormDirty', () => {
  const form = { remoteUrl: CONFIG.remote_url, secret: CONFIG.secret ?? '' }

  it('与已保存值一致（含末尾斜杠差异）时干净', () => {
    expect(isSyncFormDirty(form, CONFIG)).toBe(false)
    expect(isSyncFormDirty({ ...form, remoteUrl: 'https://online.example.com/' }, CONFIG)).toBe(false)
    expect(isSyncFormDirty({ ...form, remoteUrl: ' https://online.example.com ' }, CONFIG)).toBe(false)
  })

  it('URL 或密钥变化时脏；清空密钥也算修改（保存后重载恢复原值）', () => {
    expect(isSyncFormDirty({ ...form, remoteUrl: 'https://other.example.com' }, CONFIG)).toBe(true)
    expect(isSyncFormDirty({ ...form, secret: SECRET + 'a' }, CONFIG)).toBe(true)
    expect(isSyncFormDirty({ ...form, secret: '' }, CONFIG)).toBe(true)
  })

  it('还没有基线（首次加载前）时不脏', () => {
    expect(isSyncFormDirty(form, null)).toBe(false)
  })
})

describe('generateSyncSecret（256 bit，对齐后端 token_hex(32)）', () => {
  it('默认 32 字节 → 64 位十六进制，且两次不同', () => {
    const first = generateSyncSecret()
    expect(first).toMatch(/^[0-9a-f]{64}$/)
    expect(generateSyncSecret()).not.toBe(first)
  })

  it('可指定字节数', () => {
    expect(generateSyncSecret(16)).toMatch(/^[0-9a-f]{32}$/)
  })
})

describe('summarizePushResult / formatPushSummary', () => {
  it('完整响应：计数取 remote_result，客户端参数取 applied + fields', () => {
    const summary = summarizePushResult(PUSH_PAYLOAD)
    expect(summary).toMatchObject({
      remote: 'https://online.example.com',
      accounts: { added: 3, updated: 9 },
      models: { added: 1, updated: 6 },
      profile: { applied: true, fields: 2, errors: [] },
    })
    expect(formatPushSummary(summary)).toEqual({
      accounts: '+3 / ~9',
      models: '+1 / ~6',
      profile: '已同步 2 项',
    })
  })

  it('线上没回传 remote_result：计数与客户端参数显示 —，但仍算成功', () => {
    const summary = summarizePushResult({ ...PUSH_PAYLOAD, remote_result: undefined })
    expect(summary.accounts).toBeNull()
    expect(summary.models).toBeNull()
    expect(summary.profile).toBeNull()
    expect(formatPushSummary(summary)).toEqual({ accounts: '—', models: '—', profile: '—' })
  })

  it('本地客户端参数为空（client_profile:false）→「为空，未同步」', () => {
    const summary = summarizePushResult({
      ...PUSH_PAYLOAD,
      client_profile: false,
      profile_fields: [],
      remote_result: {
        ok: true,
        accounts: { added: 0, updated: 0 },
        models: { added: 0, updated: 0 },
      },
    })
    expect(summary.profile).toEqual({ applied: null, fields: 0, errors: [] })
    expect(formatPushSummary(summary)).toEqual({
      accounts: '+0 / ~0',
      models: '+0 / ~0',
      profile: '为空，未同步',
    })
  })

  it('线上应用失败：拼接 errors；没有 errors 时给兜底文案', () => {
    const failed = summarizePushResult({
      ...PUSH_PAYLOAD,
      remote_result: {
        ok: true,
        accounts: { added: 1, updated: 0 },
        models: { added: 0, updated: 0 },
        client_profile: {
          applied: false,
          fields: [],
          errors: ['cpu_cores 不合法', 'timezone 不合法'],
        },
      },
    })
    expect(formatPushSummary(failed).profile).toBe('未同步：cpu_cores 不合法；timezone 不合法')

    const noErrors = summarizePushResult({
      ...PUSH_PAYLOAD,
      remote_result: { ok: true, client_profile: { applied: false } },
    })
    expect(formatPushSummary(noErrors).profile).toBe('未同步：线上未接受')
  })
})

describe('推送记录（后端无最近同步字段时的本机补齐）', () => {
  const summary = summarizePushResult(PUSH_PAYLOAD)

  it('成功记录保留计数与目标', () => {
    const record = pushRecordFromResult(summary, 1761858456000)
    expect(record).toMatchObject({
      at: 1761858456000,
      ok: true,
      remote: 'https://online.example.com',
      accounts: { added: 3, updated: 9 },
      models: { added: 1, updated: 6 },
      profile: { applied: true, fields: 2, errors: [] },
      error: '',
    })
  })

  it('失败记录保留目标地址与后端 detail，计数为 null', () => {
    const record = pushRecordFromError(
      ' https://online.example.com/ ',
      '线上返回错误 401: 签名校验失败',
      1761858457000,
    )
    expect(record).toMatchObject({
      at: 1761858457000,
      ok: false,
      remote: 'https://online.example.com',
      accounts: null,
      models: null,
      profile: null,
      error: '线上返回错误 401: 签名校验失败',
    })
  })

  it('写入 / 读取 / 清除 roundtrip', () => {
    const storage = fakeStorage()
    const record = pushRecordFromResult(summary, 1761858456000)
    writeSyncRecord(record, storage)
    expect(storage.data.get(SYNC_RECORD_KEY)).toBeTruthy()
    expect(readSyncRecord(storage)).toEqual(record)
    clearSyncRecord(storage)
    expect(readSyncRecord(storage)).toBeNull()
  })

  it('损坏 / 形状不对 / 存储不可用时一律当没有记录', () => {
    expect(readSyncRecord(fakeStorage({ [SYNC_RECORD_KEY]: '{not json' }))).toBeNull()
    expect(readSyncRecord(fakeStorage({ [SYNC_RECORD_KEY]: JSON.stringify({ at: 0, ok: true }) }))).toBeNull()
    expect(readSyncRecord(fakeStorage({ [SYNC_RECORD_KEY]: JSON.stringify({ at: 1, ok: 'yes' }) }))).toBeNull()
    expect(readSyncRecord(null)).toBeNull()

    const broken: SyncRecordStorage = {
      getItem() {
        throw new Error('denied')
      },
      setItem() {
        throw new Error('denied')
      },
      removeItem() {
        throw new Error('denied')
      },
    }
    expect(readSyncRecord(broken)).toBeNull()
    expect(() => writeSyncRecord(pushRecordFromResult(summary), broken)).not.toThrow()
    expect(() => clearSyncRecord(broken)).not.toThrow()
  })
})

describe('describeSyncConfig（状态行）', () => {
  const now = Date.parse('2026-03-10T04:00:00Z')
  const at = now - 2 * 60000
  const summary = summarizePushResult(PUSH_PAYLOAD)

  it('已配置 + 本机记录：URL / 打码密钥 / 最近同步', () => {
    const view = describeSyncConfig(CONFIG, pushRecordFromResult(summary, at), now)
    expect(view.empty).toBe(false)
    expect(view.url).toEqual({ configured: true, value: 'https://online.example.com' })
    expect(view.secret).toEqual({ configured: true, masked: '********3b4c', autoGenerated: false })
    expect(view.lastPush).toMatchObject({
      ok: true,
      resultText: '成功',
      timeText: '2 分钟前',
      remoteText: '推送至 https://online.example.com',
      accountsText: '+3 / ~9',
      modelsText: '+1 / ~6',
      profileText: '已同步 2 项',
      error: '',
      source: 'local',
    })
    expect(view.lastPush?.timeFull).toMatch(/^2026-03-10 \d{2}:\d{2}:\d{2}$/)
  })

  it('secret_masked 缺失时回落 maskSecret；is_auto_generated 标记本次生成', () => {
    const view = describeSyncConfig(
      { ...CONFIG, secret_masked: '', is_auto_generated: true },
      null,
      now,
    )
    expect(view.secret.masked).toBe(maskSecret(SECRET))
    expect(view.secret.autoGenerated).toBe(true)
    expect(view.lastPush).toBeNull()
  })

  it('后端将来提供 last_push_* 时优先采用（秒级时间戳也认）', () => {
    const view = describeSyncConfig(
      {
        ...CONFIG,
        last_push_at: 1761858456,
        last_push_result: {
          ok: true,
          accounts: { added: 5, updated: 2 },
          models: { added: 0, updated: 3 },
          client_profile: { applied: true, fields: ['user_agent'], errors: [] },
        },
      },
      pushRecordFromResult(summary, at),
      now,
    )
    expect(view.lastPush?.source).toBe('backend')
    expect(view.lastPush?.accountsText).toBe('+5 / ~2')
    expect(view.lastPush?.modelsText).toBe('+0 / ~3')
    expect(view.lastPush?.profileText).toBe('已同步 1 项')
  })

  it('空配置：empty=true，未配置 URL / 密钥，无最近同步', () => {
    const view = describeSyncConfig(
      { remote_url: '', secret: '', secret_masked: '', is_auto_generated: false },
      null,
      now,
    )
    expect(view.empty).toBe(true)
    expect(view.url).toEqual({ configured: false, value: '' })
    expect(view.secret).toEqual({ configured: false, masked: '', autoGenerated: false })
    expect(view.lastPush).toBeNull()

    expect(describeSyncConfig(null, null, now).empty).toBe(true)
  })

  it('失败记录：结果 Badge 用 danger，错误原文透出，无目标地址', () => {
    const view = describeSyncConfig(CONFIG, pushRecordFromError('', '签名校验失败', at), now)
    expect(view.lastPush).toMatchObject({
      ok: false,
      resultText: '失败',
      timeText: '2 分钟前',
      remoteText: '',
      accountsText: '—',
      profileText: '—',
      error: '签名校验失败',
      source: 'local',
    })
  })
})

/* ------------------------------------------------------------------ *
 * 掩码形状（fix_plan S5 / 第 3 轮审查 B7）：新后端不再下发明文，只给掩码 + has_secret；
 * 旧后端仍下发明文。两种形状都要能用。
 * ------------------------------------------------------------------ */
describe('密钥掩码：新旧后端形状兼容', () => {
  const MASKED: SyncConfigPayload = {
    remote_url: 'https://online.example.com',
    secret_masked: '********3b4c',
    has_secret: true,
    is_auto_generated: false,
  }

  it('旧形状：明文照常可读，不算掩码模式', () => {
    expect(plainSecretOf(CONFIG)).toBe(SECRET)
    expect(isSecretMasked(CONFIG)).toBe(false)
  })

  it('新形状：省略 secret / 给掩码串 / 与 secret_masked 相同，都读不出明文', () => {
    expect(plainSecretOf(MASKED)).toBe('')
    expect(plainSecretOf({ ...MASKED, secret: '********3b4c' })).toBe('')
    expect(plainSecretOf({ ...MASKED, secret: '9f2c…3b4c' })).toBe('')
    expect(isSecretMasked(MASKED)).toBe(true)
    expect(isSecretMasked({ ...MASKED, secret: '' })).toBe(true)
    expect(looksMaskedSecret('********3b4c')).toBe(true)
    expect(looksMaskedSecret(SECRET)).toBe(false)
  })

  it('掩码模式下空输入框不算未保存修改（基线是空串），填了新值才算', () => {
    const form = { remoteUrl: MASKED.remote_url, secret: '' }
    expect(isSyncFormDirty(form, MASKED)).toBe(false)
    expect(isSyncFormDirty({ ...form, secret: 'new-secret' }, MASKED)).toBe(true)
  })

  it('状态行：只有掩码也显示「已设置」', () => {
    const view = describeSyncConfig(MASKED, null, Date.parse('2026-11-24T02:00:00Z'))
    expect(view.secret).toEqual({ configured: true, masked: '********3b4c', autoGenerated: false })
    expect(view.empty).toBe(false)
    const onlyFlag = describeSyncConfig({ ...MASKED, secret_masked: '', remote_url: '' })
    expect(onlyFlag.secret.configured).toBe(true)
    expect(onlyFlag.empty).toBe(false)
  })

  it('没有密钥的新形状：has_secret=false 时是未设置', () => {
    const view = describeSyncConfig({ remote_url: '', secret_masked: '', has_secret: false, is_auto_generated: false })
    expect(view.secret.configured).toBe(false)
    expect(isSecretMasked({ remote_url: '', secret_masked: '', has_secret: false, is_auto_generated: false })).toBe(false)
  })
})

/* ------------------------------------------------------------------ *
 * 2026-11-24 后端安全改动（fixlog_security §6 前端 1～3）
 * ------------------------------------------------------------------ */
describe('同步：新后端契约（secret_set / admin_password / skipped_not_newer）', () => {
  const SAVED: SyncConfigPayload = {
    remote_url: 'https://online.example.com',
    secret: '********3b4c',
    secret_masked: '********3b4c',
    secret_set: true,
    is_auto_generated: false,
  }

  it('secret_set 与 has_secret 都算「库里有密钥」；掩码串不算明文', () => {
    expect(hasStoredSecret(SAVED)).toBe(true)
    expect(hasStoredSecret({ ...SAVED, secret_set: undefined, has_secret: true })).toBe(true)
    expect(hasStoredSecret({ ...SAVED, secret_set: false })).toBe(false)
    expect(plainSecretOf(SAVED)).toBe('')
    expect(isSecretMasked(SAVED)).toBe(true)
  })

  it('needsAdminPassword：只有修改已保存的地址才需要后台密码', () => {
    expect(needsAdminPassword({ remoteUrl: 'https://online.example.com/', secret: '' }, SAVED)).toBe(false)
    expect(needsAdminPassword({ remoteUrl: 'https://other.example.com', secret: '' }, SAVED)).toBe(true)
    // 清空输入框：后端不写库，不需要
    expect(needsAdminPassword({ remoteUrl: '', secret: '' }, SAVED)).toBe(false)
    // 首次填写
    expect(needsAdminPassword({ remoteUrl: 'https://first.example.com', secret: '' }, { ...SAVED, remote_url: '' })).toBe(false)
    expect(needsAdminPassword({ remoteUrl: 'https://x.example.com', secret: '' }, null)).toBe(false)
  })

  it('remoteUrlProblem：非 http(s) / https_only 下的 http', () => {
    expect(remoteUrlProblem('', true)).toBe('')
    expect(remoteUrlProblem('ftp://a.example.com', false)).toBe('invalid')
    expect(remoteUrlProblem('http://a.example.com', true)).toBe('httpsOnly')
    expect(remoteUrlProblem('http://a.example.com', false)).toBe('')
    expect(remoteUrlProblem('https://a.example.com/', true)).toBe('')
  })

  it('skipped_not_newer：汇总进 accounts.skipped，并在文案里显示', () => {
    const summary = summarizePushResult({
      ok: true,
      remote: 'https://online.example.com',
      accounts: 8,
      models: 2,
      client_profile: false,
      profile_fields: [],
      remote_result: { ok: true, accounts: { added: 1, updated: 2, skipped_not_newer: 5 }, models: { added: 0, updated: 2 } },
    })
    expect(summary.accounts).toEqual({ added: 1, updated: 2, skipped: 5 })
    expect(formatPushSummary(summary).accounts).toBe('+1 / ~2 · 5 个线上更新、未覆盖')
    // 0 不显示
    const zero = summarizePushResult({
      ok: true,
      remote: '',
      accounts: 1,
      models: 0,
      client_profile: false,
      profile_fields: [],
      remote_result: { accounts: { added: 1, updated: 0, skipped_not_newer: 0 } },
    })
    expect(zero.accounts).toEqual({ added: 1, updated: 0 })
    expect(formatPushSummary(zero).accounts).toBe('+1 / ~0')
  })
})
