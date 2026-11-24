/**
 * F3 · v2.1：账号池去重按 platform/realm 分组、页头 meta 口径、积分到期「选号模式下拉为空」回归。
 */
import { describe, expect, it } from 'vitest'
import { accountsOverview, groupDuplicates, type AccountRow, type DuplicatesPayload } from '@/api/accounts'
import { nextPolicyForm, type SelectionPolicy } from '@/api/credits'

const ref = (id: number) => ({ id, name: '号' + id, created_at: '2026-11-0' + (id % 9) + 'T00:00:00' })

describe('groupDuplicates：按 platform/realm 分段（data-scoping D1）', () => {
  it('同 UID 不同区分到不同段；段内按可清理数倒序；段按平台、区排序', () => {
    const payload: DuplicatesPayload = {
      total: 9,
      unique: 5,
      redundant: 4,
      groups: [
        { uid: 'u1', platform: 'trae', realm: 'intl', keep: ref(1), remove: [ref(2)] },
        { uid: 'u1', platform: 'trae', realm: 'cn', keep: ref(3), remove: [ref(4)] },
        { uid: 'u2', platform: 'Trae', realm: 'CN', keep: ref(5), remove: [ref(6), ref(7)] },
        { uid: 'u3', platform: '', keep: ref(8), remove: [] },
      ],
    }
    const sections = groupDuplicates(payload)
    expect(sections.map((s) => s.key)).toEqual([':', 'trae:cn', 'trae:intl'])
    const cn = sections[1]
    expect(cn.redundant).toBe(3)
    expect(cn.groups.map((g) => g.uid)).toEqual(['u2', 'u1'])
    expect(sections[2].redundant).toBe(1)
    expect(sections[0].redundant).toBe(0)
  })

  it('空 payload 返回空数组', () => {
    expect(groupDuplicates(null)).toEqual([])
    expect(groupDuplicates({ total: 0, unique: 0, redundant: 0, groups: [] })).toEqual([])
  })
})

describe('accountsOverview：页头「N 个账号 · N 个冷却」与工具栏「可用」', () => {
  const row = (statusKey: string, balanceRemain: number) => ({ statusKey, balanceRemain }) as unknown as AccountRow
  it('已禁用不算可用也不进剩余；冷却 / 熔断 / 降级都算冷却', () => {
    const view = accountsOverview([
      row('active', 100),
      row('active', 0),
      row('cooling', 50),
      row('breaker', 10),
      row('degraded', 5),
      row('disabled', 999),
    ])
    expect(view).toEqual({ total: 6, cooling: 3, disabled: 1, available: 1, remain: 165 })
  })
})

describe('nextPolicyForm：首次加载下拉跟随后端值（第 1 轮 P0-10）', () => {
  const policy = (mode: string, interval = 75): SelectionPolicy => ({
    mode,
    upstream_start_interval_ms: interval,
    modes: [],
  })

  it('还没有旧 policy（首次加载，表单是空串）→ 跟随新值', () => {
    expect(nextPolicyForm(null, { mode: '', interval: 75 }, policy('expiry_weighted', 120))).toEqual({
      mode: 'expiry_weighted',
      interval: 120,
    })
  })

  it('用户没改过 → 跟随新值；改过 → 返回 null 保留编辑', () => {
    const old = policy('round_robin')
    expect(nextPolicyForm(old, { mode: 'round_robin', interval: 75 }, policy('expiry_weighted'))).toEqual({
      mode: 'expiry_weighted',
      interval: 75,
    })
    expect(nextPolicyForm(old, { mode: 'least_used', interval: 75 }, policy('expiry_weighted'))).toBeNull()
  })

  it('新 policy 为空 → null', () => {
    expect(nextPolicyForm(policy('a'), { mode: 'a', interval: 75 }, null)).toBeNull()
  })
})
