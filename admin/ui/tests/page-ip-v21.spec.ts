/**
 * IP 管理 v2.1：自动封禁识别 / 一键清理 / 定时封禁能力探测（api/ip.ts 末尾新增的纯函数）。
 *
 * 事实来源：admin/abuse_guard.py:_ban 写入 note = "auto: {reason}"；
 * admin/routers/ip_management.py:_blocked_item 目前不返回 expires_at。
 */
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/http'
import {
  AUTO_BLOCK_PREFIX,
  autoBlockReason,
  blockedExpirySupported,
  blockedSourceStats,
  bulkDeleteBlocked,
  cleanBlockedRecords,
  filterBlockedBySource,
  isAutoBlock,
  toBlockedRows,
  type BlockedIpItem,
} from '@/api/ip'

const items: BlockedIpItem[] = [
  { id: 1, ip: '198.51.100.9', enabled: true, note: 'auto: 高频 401', created_at: '2026-11-22T10:00:00', updated_at: null },
  { id: 2, ip: '198.51.100.10', enabled: false, note: 'AUTO:burst', created_at: '2026-11-22T11:00:00', updated_at: null },
  { id: 3, ip: '203.0.113.7', enabled: true, note: '异常刷请求', created_at: '2026-11-23T09:00:00', updated_at: null },
  { id: 4, ip: '203.0.113.8', enabled: true, note: '', created_at: null, updated_at: null },
  { id: 5, ip: '203.0.113.9', enabled: true, note: '手动 auto: 不算', created_at: null, updated_at: null },
]
const rows = toBlockedRows(items)

describe('autoBlockReason / isAutoBlock：备注以 auto: 开头才算自动封禁', () => {
  it('去掉前缀与空白，大小写不敏感', () => {
    expect(AUTO_BLOCK_PREFIX).toBe('auto:')
    expect(autoBlockReason('auto: 高频 401')).toBe('高频 401')
    expect(autoBlockReason('AUTO:burst')).toBe('burst')
    expect(autoBlockReason('auto:')).toBe('')
  })

  it('手动封禁（含中间出现 auto: 的）返回 null', () => {
    expect(autoBlockReason('异常刷请求')).toBeNull()
    expect(autoBlockReason('手动 auto: 不算')).toBeNull()
    expect(autoBlockReason('')).toBeNull()
    expect(autoBlockReason(null)).toBeNull()
    expect(autoBlockReason(undefined)).toBeNull()
  })

  it('isAutoBlock 按行判断', () => {
    expect(rows.filter(isAutoBlock).map((row) => row.id)).toEqual([1, 2])
  })
})

describe('blockedSourceStats / filterBlockedBySource', () => {
  it('统计总数、启用中、自动、自动且启用、手动', () => {
    expect(blockedSourceStats(rows)).toEqual({ total: 5, enabled: 4, auto: 2, autoEnabled: 1, manual: 3 })
    expect(blockedSourceStats(null)).toEqual({ total: 0, enabled: 0, auto: 0, autoEnabled: 0, manual: 0 })
  })

  it('来源筛选：auto / manual / 其他值不过滤', () => {
    expect(filterBlockedBySource(rows, 'auto').map((row) => row.id)).toEqual([1, 2])
    expect(filterBlockedBySource(rows, 'manual').map((row) => row.id)).toEqual([3, 4, 5])
    expect(filterBlockedBySource(rows, '')).toHaveLength(5)
    expect(filterBlockedBySource(rows, 'whatever')).toHaveLength(5)
  })
})

describe('blockedExpirySupported：响应里出现 expires_at 字段才开放「时长 / 到期」', () => {
  it('现状（字段缺失）→ false；空列表 → false', () => {
    expect(blockedExpirySupported(items)).toBe(false)
    expect(blockedExpirySupported([])).toBe(false)
    expect(blockedExpirySupported(null)).toBe(false)
  })

  it('任意一条带 expires_at（哪怕是 null）→ true', () => {
    expect(blockedExpirySupported([{ ...items[0], expires_at: null }])).toBe(true)
  })
})

describe('bulkDeleteBlocked：逐条删除，单条失败不影响其他条', () => {
  it('全部成功，进度回调到 total', async () => {
    const removed: number[] = []
    const progress: Array<[number, number]> = []
    const result = await bulkDeleteBlocked([1, 2, 3], {
      concurrency: 2,
      remove: async (id) => {
        removed.push(id)
      },
      onProgress: (done, total) => progress.push([done, total]),
    })
    expect(result.deleted.sort()).toEqual([1, 2, 3])
    expect(result.failed).toEqual([])
    expect(removed.sort()).toEqual([1, 2, 3])
    expect(progress[progress.length - 1]).toEqual([3, 3])
  })

  it('部分失败：失败项带上错误，成功项照常删除', async () => {
    const boom = new Error('404 封禁记录不存在')
    const result = await bulkDeleteBlocked([1, 2, 3], {
      remove: async (id) => {
        if (id === 2) throw boom
      },
    })
    expect(result.deleted.sort()).toEqual([1, 3])
    expect(result.failed).toEqual([{ id: 2, error: boom }])
  })

  it('空列表直接返回', async () => {
    const result = await bulkDeleteBlocked([], { remove: async () => undefined })
    expect(result).toEqual({ deleted: [], failed: [] })
  })
})

describe('r3：后端新字段 + 批量删除接口', () => {
  it('expiry_supported 标记（列表为空时也能打开时长）', () => {
    expect(blockedExpirySupported([], true)).toBe(true)
    expect(blockedExpirySupported([{ ...items[0], expiry_supported: true }])).toBe(true)
  })

  it('toBlockedRows 带出 expired / active / source（后端字段优先，缺失时推算）', () => {
    const [timed, legacy] = toBlockedRows([
      { ...items[2], expires_at: '2020-01-01T00:00:00Z', expired: true, active: false, source: 'manual' },
      items[0],
    ])
    expect(timed.expired).toBe(true)
    expect(timed.active).toBe(false)
    expect(timed.source).toBe('manual')
    expect(legacy.expired).toBe(false)
    expect(legacy.active).toBe(true)
    expect(legacy.source).toBe('auto')
  })

  it('cleanBlockedRecords：一次 POST 完成，不逐条删', async () => {
    const calls: number[][] = []
    const removed: number[] = []
    const result = await cleanBlockedRecords([1, 2, 3], {
      batch: async (input) => {
        calls.push(input.ids || [])
        return { deleted: 3, deleted_ids: [1, 2, 3] }
      },
      remove: async (id) => {
        removed.push(id)
      },
    })
    expect(calls).toEqual([[1, 2, 3]])
    expect(removed).toEqual([])
    expect(result.via).toBe('batch')
    expect(result.deleted).toEqual([1, 2, 3])
  })

  it('cleanBlockedRecords：老后端 404 时回落逐条 DELETE', async () => {
    const removed: number[] = []
    const result = await cleanBlockedRecords([1, 2], {
      batch: async () => {
        throw new ApiError(404, 'Not Found')
      },
      remove: async (id) => {
        removed.push(id)
      },
    })
    expect(result.via).toBe('each')
    expect(removed.sort()).toEqual([1, 2])
  })

  it('cleanBlockedRecords：其他错误原样抛出，不回落', async () => {
    await expect(
      cleanBlockedRecords([1], {
        batch: async () => {
          throw new ApiError(500, 'boom')
        },
        remove: async () => undefined,
      }),
    ).rejects.toThrow('boom')
  })
})
