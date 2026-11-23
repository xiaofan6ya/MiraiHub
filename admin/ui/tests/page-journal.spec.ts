import { describe, expect, it } from 'vitest'
import {
  KIND_OPTIONS,
  accountOptions,
  canGoNext,
  csvFilename,
  deltaTone,
  filterJournalRows,
  importingHint,
  kindMeta,
  pageState,
  rangeToQuery,
  sourceLabel,
  sourceOptions,
  summarize,
  toCsv,
  toJournalRow,
  toJournalRows,
  type CreditEventItem,
  type CreditEventPayload,
} from '@/api/journal'
import { FMT_EMPTY } from '@/utils/format'

/** 真实响应形状：admin/routers/maintenance.py:credit_events 的 items[]。 */
function item(overrides: Partial<CreditEventItem> = {}): CreditEventItem {
  return {
    id: 918,
    account_id: 152,
    account_name: '工作号 A',
    kind: 'confirmed',
    source: 'schedule · 每日签到',
    delta: 120,
    balance_after: 4764,
    created_at: '2026-11-23T02:15:00Z',
    ...overrides,
  }
}

/** 真实响应形状：GET /api/maintenance/credit-events。 */
function payload(overrides: Partial<CreditEventPayload> = {}): CreditEventPayload {
  return {
    items: [
      item(),
      item({ id: 917, kind: 'observed', source: 'manual · 余额对账', delta: -30, balance_after: null, account_name: '工作号 B', account_id: 153 }),
      item({ id: 916, kind: 'observed', source: '', delta: 0, balance_after: 0 }),
    ],
    total: 317,
    page: 2,
    page_size: 50,
    importing: false,
    sources: ['schedule · 每日签到', 'manual · 余额对账', 'task · 积分采集'],
    accounts: [
      { id: 152, name: '工作号 A' },
      { id: 153, name: '工作号 B' },
    ],
    ...overrides,
  }
}

describe('流水行转换', () => {
  it('确认奖励：带符号变动、余额、类型 Badge、说明与来源中文', () => {
    const row = toJournalRow(item(), 'WorkBuddy')
    expect(row.accountText).toBe('工作号 A #152')
    expect(row.platformText).toBe('WorkBuddy')
    expect(row.kindLabel).toBe('确认奖励')
    expect(row.kindTone).toBe('ok')
    expect(row.delta).toBe(120)
    expect(row.deltaText).toBe('+120')
    expect(row.deltaTone).toBe('ok')
    expect(row.balanceText).toBe('4,764')
    expect(row.balanceRecorded).toBe(true)
    expect(row.sourceText).toBe('定时 · 每日签到')
    expect(row.note).toBe('确认奖励 · 定时 · 每日签到')
    expect(row.time).not.toBe('')
  })

  it('余额观察 + 负数 + 未记录余额 + 空来源', () => {
    const row = toJournalRow(item({ kind: 'observed', delta: -30, balance_after: null, source: '' }))
    expect(row.kindLabel).toBe('余额观察')
    expect(row.kindTone).toBe('brand')
    expect(row.deltaText).toBe('-30')
    expect(row.deltaTone).toBe('danger')
    expect(row.balanceText).toBe('未记录')
    expect(row.balanceRecorded).toBe(false)
    expect(row.sourceText).toBe(FMT_EMPTY)
    expect(row.note).toBe('余额观察')
    // 没传平台名时显示 —，不猜平台
    expect(row.platformText).toBe(FMT_EMPTY)
    expect(toJournalRows(null)).toEqual([])
  })

  it('变动着色：正数 ok、负数 danger、0 中性', () => {
    expect(deltaTone(12)).toBe('ok')
    expect(deltaTone(-12)).toBe('danger')
    expect(deltaTone(0)).toBe('neutral')
    expect(deltaTone(null)).toBe('neutral')
    expect(toJournalRow(item({ delta: 0 })).deltaText).toBe('0')
  })

  it('未知 kind 兜底成中性徽章', () => {
    expect(kindMeta('confirmed').label).toBe('确认奖励')
    const meta = kindMeta('mystery')
    expect(meta.label).toBe('mystery')
    expect(meta.tone).toBe('neutral')
    expect(toJournalRow(item({ kind: 'mystery' })).kindLabel).toBe('mystery')
  })
})

describe('来源文案与下拉', () => {
  it('前缀中文化：schedule/manual/task → 定时/手动/任务，其余原样', () => {
    expect(sourceLabel('schedule · 每小时刷新')).toBe('定时 · 每小时刷新')
    expect(sourceLabel('manual · 对账')).toBe('手动 · 对账')
    expect(sourceLabel('task · 积分采集')).toBe('任务 · 积分采集')
    expect(sourceLabel('trae · 签到')).toBe('trae · 签到')
    expect(sourceLabel('签到')).toBe('签到')
    expect(sourceLabel('')).toBe(FMT_EMPTY)
  })

  it('账号与来源下拉：全部 + 去重后的候选项', () => {
    expect(accountOptions(payload().accounts)).toEqual([
      { label: '全部账号', value: 0 },
      { label: '工作号 A · #152', value: 152 },
      { label: '工作号 B · #153', value: 153 },
    ])
    expect(accountOptions(null)).toEqual([{ label: '全部账号', value: 0 }])
    expect(sourceOptions(payload().sources)).toEqual([
      { label: '全部来源', value: '' },
      { label: '定时 · 每日签到', value: 'schedule · 每日签到' },
      { label: '手动 · 余额对账', value: 'manual · 余额对账' },
      { label: '任务 · 积分采集', value: 'task · 积分采集' },
    ])
  })
})

describe('搜索 / 统计 / 分页', () => {
  const rows = toJournalRows(payload().items, 'WorkBuddy')

  it('搜索只在当前页过滤，匹配账号/来源/说明/变动', () => {
    expect(filterJournalRows(rows, { q: '工作号 B' })).toHaveLength(1)
    expect(filterJournalRows(rows, { q: '每日签到' })).toHaveLength(1)
    expect(filterJournalRows(rows, { q: '+120' })).toHaveLength(1)
    expect(filterJournalRows(rows, { q: '未记录' })).toHaveLength(1)
    expect(filterJournalRows(rows, { q: '' })).toHaveLength(3)
    expect(filterJournalRows(rows, { q: '不存在' })).toHaveLength(0)
  })

  it('统计：全量条数 + 本页净变化 + 两类笔数', () => {
    const summary = summarize(rows, payload().total)
    expect(summary.total).toBe(317)
    expect(summary.loaded).toBe(3)
    expect(summary.netDelta).toBe(90)
    expect(summary.netText).toBe('+90')
    expect(summary.confirmed).toBe(1)
    expect(summary.observed).toBe(2)
    expect(summary.text).toBe('共 317 条 · 本页净变化 +90 · 确认奖励 1 笔 · 余额观察 2 笔')
  })

  it('偏移分页：页数、上一页/下一页边界', () => {
    expect(pageState(payload())).toEqual({ page: 2, pageSize: 50, total: 317, pageCount: 7, hasPrev: true, hasNext: true })
    const last = pageState({ page: 7, page_size: 50, total: 317, items: [] })
    expect(last.hasNext).toBe(false)
    const empty = pageState(null)
    expect(empty).toEqual({ page: 1, pageSize: 50, total: 0, pageCount: 1, hasPrev: false, hasNext: false })
  })

  it('快速翻页：没有总数时按「本页是否满」判断下一页', () => {
    const state = pageState(payload())
    expect(canGoNext(false, state, 3)).toBe(true)
    expect(canGoNext(true, state, 50)).toBe(true)
    expect(canGoNext(true, state, 3)).toBe(false)
    expect(canGoNext(true, state, 0)).toBe(false)
  })

  it('时间区间 → ISO；非法区间给空串', () => {
    const start = Date.UTC(2026, 10, 23, 0, 0, 0)
    const end = Date.UTC(2026, 10, 23, 12, 30, 0)
    expect(rangeToQuery([start, end])).toEqual({
      start: '2026-11-23T00:00:00.000Z',
      end: '2026-11-23T12:30:00.000Z',
    })
    expect(rangeToQuery(null)).toEqual({ start: '', end: '' })
  })

  it('导入中提示与类型下拉', () => {
    expect(importingHint({ importing: true })).toContain('历史记录导入中')
    expect(importingHint({ importing: false })).toBe('')
    expect(importingHint(null)).toBe('')
    expect(KIND_OPTIONS.map((option) => option.value)).toEqual(['', 'confirmed', 'observed'])
  })
})

describe('导出 CSV', () => {
  it('表头 + 中文列，逗号/引号/换行转义', () => {
    const rows = toJournalRows(
      [item({ account_name: '工作号, "A"' }), item({ id: 1, source: 'multi\nline' })],
      'WorkBuddy',
    )
    const csv = toCsv(rows)
    const lines = csv.split('\n')
    expect(lines[0]).toBe('时间,账号,平台,类型,变动,余额,说明,来源')
    expect(csv).toContain('"工作号, ""A"" #152"')
    expect(csv).toContain('WorkBuddy,确认奖励,+120')
    expect(csv).toContain('multi line')
  })

  it('文件名带日期与扩展名', () => {
    const name = csvFilename(new Date(2026, 10, 23, 9, 5, 7))
    expect(name).toBe('miraihub-journal-20261123-090507.csv')
  })
})
