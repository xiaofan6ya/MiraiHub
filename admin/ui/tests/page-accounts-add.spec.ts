import { describe, expect, it } from 'vitest'
import {
  AUTH_STEPS,
  COUNTRY_CODES,
  PLATFORM_AUTH_FLOWS,
  addMethodsFor,
  addResultText,
  authStateOf,
  authStepIndex,
  defaultVerify,
  importLocalText,
  importModeOf,
  importStorageOf,
  manualHint,
  maskPhone,
  methodEnabled,
  parseImportText,
  scanPreviewRows,
  scanSummary,
  storageOf,
  validatePhoneForm,
  type PlatformAddSpec,
  type ScanLocalPayload,
} from '@/api/account-add'

function spec(overrides: Partial<PlatformAddSpec> = {}): PlatformAddSpec {
  return { id: 'workbuddy', label: 'WorkBuddy', storage_platform: 'workbuddy', auth_mode: 'local-scan', supports_local_scan: true, actions: ['scan', 'import'], ...overrides }
}

describe('添加方式的能力声明', () => {
  it('WorkBuddy：手机号登录 + 手动 + 导入 + 扫描', () => {
    const methods = addMethodsFor(spec()).map((method) => method.key)
    expect(methods).toEqual(['phone', 'manual', 'import', 'scan'])
    expect(methodEnabled(spec(), 'scan')).toBe(true)
    expect(methodEnabled(spec(), 'phone')).toBe(true)
    expect(methodEnabled(spec(), 'authorize')).toBe(false)
  })

  it('Loomy 有手机号登录；Marvis 有浏览器授权（按落库平台查表）', () => {
    expect(addMethodsFor(spec({ id: 'loomy', storage_platform: 'loomy', supports_local_scan: false })).map((m) => m.key)).toEqual([
      'phone', 'manual', 'import',
    ])
    expect(addMethodsFor(spec({ id: 'marvis', storage_platform: 'marvis', auth_mode: 'qr-auth', supports_local_scan: false })).map((m) => m.key)).toEqual([
      'authorize', 'manual', 'import',
    ])
  })

  it('import-only 平台不给授权类入口；trae-cn 归一到 trae', () => {
    const methods = addMethodsFor(spec({ id: 'trae-cn', storage_platform: 'trae', auth_mode: 'import-only', supports_local_scan: true })).map((m) => m.key)
    expect(methods).toEqual(['manual', 'import'])
    expect(storageOf({ id: 'trae-cn' })).toBe('trae')
    expect(storageOf({ id: 'qoder-intl' })).toBe('qoder')
    expect(storageOf(null)).toBe('')
  })

  it('平台授权表只登记真实存在的接口', () => {
    expect(PLATFORM_AUTH_FLOWS.loomy.kind).toBe('phone')
    expect(PLATFORM_AUTH_FLOWS.loomy.send?.path).toBe('/api/loomy/auth/sms')
    expect(PLATFORM_AUTH_FLOWS.krowork.prepare?.path).toBe('/api/krowork/auth/begin')
    expect(PLATFORM_AUTH_FLOWS.marvis.poll?.path).toBe('/api/marvis/auth/poll')
  })
})

describe('导入解析', () => {
  it('JSONL / 数组 / 单个对象都能解析，结构坏的行走 invalid', () => {
    const jsonl = parseImportText('{"uid":"a1","auth":{}}\n{"uid":"a2","auth":{}}\nnot-json')
    expect(jsonl.valid).toBe(2)
    expect(jsonl.invalid).toBe(1)
    expect(jsonl.items).toHaveLength(2)
    expect(jsonl.rows[0].uid).toBe('a1')
    expect(jsonl.rows[2].reason).toContain('JSON 解析失败')

    const array = parseImportText('[{"name":"号 A","auth_json":"{\\"uid\\":\\"b1\\"}"}]')
    expect(array.valid).toBe(1)
    expect(array.rows[0].name).toBe('号 A')
    expect(array.items[0].auth_json).toContain('b1')

    expect(parseImportText('').rows).toEqual([])
  })

  it('导入 storage / 模式 / 默认验证开关', () => {
    expect(importStorageOf(spec())).toBe('workbuddy')
    expect(importStorageOf(spec({ id: 'qoder', storage_platform: 'qoder' }))).toBe('qoder')
    expect(importStorageOf(null)).toBe('workbuddy')
    expect(importModeOf(spec())).toBe('items')
    expect(importModeOf(spec({ storage_platform: 'trae' }))).toBe('text')
    expect(defaultVerify(spec())).toBe(true)
    expect(defaultVerify(spec({ storage_platform: 'trae' }))).toBe(false)
  })
})

describe('扫描本机预览', () => {
  const payload: ScanLocalPayload = {
    dir: 'C:/Users/me/AppData/Roaming/workbuddy/data',
    exists: true,
    active_uid: 'u2',
    items: [
      { file: 'a.info', uid: 'u1', nickname: '工作号 A', expires: '2026-12-01 10:00:00', in_pool: false },
      { file: 'b.info', uid: 'u2', nickname: '工作号 B' },
      { file: 'c.info', uid: 'u3', error: '凭据已损坏' },
    ],
    pool_total: 5,
    pool_only: 2,
  }

  it('行映射：已在池子里 / 当前登录 / 出错的行不默认勾选', () => {
    const rows = scanPreviewRows(payload)
    expect(rows).toHaveLength(3)
    expect(rows[0].selected).toBe(true)
    expect(rows[0].name).toBe('工作号 A')
    expect(rows[1].active).toBe(true)
    expect(rows[2].selected).toBe(false)
    expect(rows[2].error).toBe('凭据已损坏')
    expect(scanPreviewRows(null)).toEqual([])
  })

  it('汇总文案带目录与池子差异；目录不存在时说明原因', () => {
    const rows = scanPreviewRows(payload)
    expect(scanSummary(payload, rows)).toContain('发现 3 个客户端账号')
    expect(scanSummary(payload, rows)).toContain('池子里有 2 个本机没有的账号')
    expect(scanSummary({ ...payload, exists: false, items: [] }, [])).toContain('没有找到客户端登录目录')
    expect(scanSummary(null, [])).toBe('')
  })
})

describe('授权状态归一', () => {
  it('authorized / scanned / failed / pending 映射到步骤条', () => {
    const authorized = authStateOf({ status: 'authorized', account_id: 12 })
    expect(authorized.state).toBe('authorized')
    expect(authorized.accountId).toBe(12)
    expect(authStepIndex(authorized)).toBe(2)

    const scanned = authStateOf({ status: 'scanned' })
    expect(scanned.state).toBe('scanned')
    expect(authStepIndex(scanned)).toBe(1)

    const failed = authStateOf({ ok: false, detail: '用户取消' })
    expect(failed.state).toBe('failed')
    expect(failed.message).toBe('用户取消')

    const opened = authStateOf({ auth_url: 'https://example.com/auth' })
    expect(opened.state).toBe('opened')
    expect(opened.url).toBe('https://example.com/auth')
    expect(authStepIndex(opened)).toBe(1)

    const pending = authStateOf({})
    expect(pending.state).toBe('pending')
    expect(authStepIndex(authStateOf(null))).toBe(1)
    expect(AUTH_STEPS).toHaveLength(3)
  })
})

describe('手机号表单与文案', () => {
  it('手机号/验证码校验、新号密码必填', () => {
    expect(validatePhoneForm({ country: '+86', phone: '', code: '' })).toEqual({
      phone: '请填写手机号',
      code: '请填写验证码',
    })
    expect(validatePhoneForm({ country: '+86', phone: '123', code: '1' }).phone).toBe('手机号长度不正确')
    expect(validatePhoneForm({ country: '+86', phone: '13800138000', code: '1234' })).toEqual({})
    expect(validatePhoneForm({ country: '+86', phone: '13800138000', code: '1234' }, true).password).toBe('新账号需要设置密码')
    expect(COUNTRY_CODES[0].value).toBe('+86')
  })

  it('打码与结果文案', () => {
    expect(maskPhone('13800138000')).toBe('138****8000')
    expect(maskPhone('12345')).toBe('12****')
    expect(maskPhone('')).toBe('')
    expect(addResultText({ id: 7, name: '工作号 A', duplicated: false }, 'WorkBuddy')).toBe('已添加 工作号 A（WorkBuddy）')
    expect(addResultText({ id: 7, name: '工作号 A', duplicated: true }, 'WorkBuddy')).toContain('已用新凭证覆盖更新')
    expect(importLocalText({ added: 2, updated: 1, skipped: 3, errors: ['x'] })).toBe('新增 2 · 更新 1 · 跳过 3 · 失败 1')
    expect(manualHint(spec())).toContain('.info')
    expect(manualHint(spec({ storage_platform: 'trae' }))).toContain('字段以平台为准')
  })
})
