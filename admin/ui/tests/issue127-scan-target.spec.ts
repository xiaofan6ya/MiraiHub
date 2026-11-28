/**
 * issue #127：添加账号抽屉的「扫描本机」必须按平台走。
 *
 * 回归点：默认平台走 `/api/accounts/scan-local`（通用接口），
 * 其它平台必须走各自的 `/api/<storage>/scan-local`，否则 CodeArts 抽屉里
 * 会列出并「导入」WorkBuddy 的号。
 */
import { describe, expect, it } from 'vitest'
import {
  importPlatformLocal,
  platformScanRows,
  platformScanSummary,
  scanPlatformLocal,
  scanTargetOf,
  type PlatformAddSpec,
  type PlatformScanPayload,
} from '@/api/account-add'

function spec(overrides: Partial<PlatformAddSpec> = {}): PlatformAddSpec {
  return {
    id: 'codearts',
    label: 'CodeArts',
    storage_platform: 'codearts',
    auth_mode: 'local-scan',
    supports_local_scan: true,
    actions: ['scan', 'import'],
    ...overrides,
  }
}

describe('扫描目标按平台分岔', () => {
  it('默认平台用通用接口', () => {
    expect(scanTargetOf(spec({ id: 'workbuddy', storage_platform: 'workbuddy' }))).toEqual({
      storage: 'workbuddy',
      prefix: '/api/accounts',
      generic: true,
    })
    // 历史写法 codebuddy 也算默认平台
    expect(scanTargetOf(spec({ id: 'codebuddy', storage_platform: 'codebuddy' })).generic).toBe(true)
    expect(scanTargetOf(null).generic).toBe(true)
  })

  it('非默认平台打自己的接口（CodeArts / Trae / Loomy）', () => {
    expect(scanTargetOf(spec()).prefix).toBe('/api/codearts')
    expect(scanTargetOf(spec()).generic).toBe(false)
    expect(scanTargetOf(spec({ id: 'trae-cn', storage_platform: 'trae' })).prefix).toBe('/api/trae')
    expect(scanTargetOf(spec({ id: 'loomy', storage_platform: 'loomy' })).prefix).toBe('/api/loomy')
  })

  it('接口地址拼的是平台前缀，不是通用 accounts 前缀', () => {
    const calls: string[] = []
    const fake = (url: string) => {
      calls.push(url)
      return Promise.resolve({})
    }
    globalThis.fetch = fake as unknown as typeof fetch
    void scanPlatformLocal('/api/codearts').catch(() => undefined)
    void importPlatformLocal('/api/codearts', ['u1']).catch(() => undefined)
    expect(calls[0]).toContain('/api/codearts/scan-local')
    expect(calls[1]).toContain('/api/codearts/import-local')
  })
})

describe('平台扫描结果归一', () => {
  it('兼容各平台字段名（uid/user_name/expires_at/in_pool）', () => {
    const payload: PlatformScanPayload = {
      items: [
        { uid: 'u-1', user_name: '张三', expires_at: 1797040276000, in_pool: false },
        { userid: 'u-2', nickname: '李四', expires: '2026-12-01', exists: true },
        { account_id: 'u-3', name: '王五', error: '登录态已过期' },
        { uid_hint: 'u-4' },
      ],
    }
    const rows = platformScanRows(payload)
    expect(rows.map((row) => row.uid)).toEqual(['u-1', 'u-2', 'u-3', 'u-4'])
    expect(rows[0].name).toBe('张三')
    expect(rows[0].expires).toBe('1797040276000')
    expect(rows[0].selected).toBe(true)
    // 已在池子里的默认不勾
    expect(rows[1].inPool).toBe(true)
    expect(rows[1].selected).toBe(false)
    // 有错误的默认不勾
    expect(rows[2].selected).toBe(false)
    // 没名字时回落 uid
    expect(rows[3].name).toBe('u-4')
  })

  it('空结果/未安装时给出可读原因，而不是空串', () => {
    expect(platformScanSummary({ items: [], hint: '没找到登录态' }, [])).toBe('没找到登录态')
    expect(platformScanSummary({ exists: false, dir: 'C:/x' }, [])).toContain('C:/x')
    expect(platformScanSummary({ items: [{ uid: 'a' }], config_root: 'C:/y' },
      platformScanRows({ items: [{ uid: 'a' }], config_root: 'C:/y' }))).toContain('C:/y')
    expect(platformScanSummary(null, [])).toBe('')
  })
})
