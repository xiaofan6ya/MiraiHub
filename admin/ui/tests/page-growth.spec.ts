import { describe, expect, it } from 'vitest'
import {
  GROWTH_ACTIONS,
  claimableCodes,
  filterGrowthCards,
  growthStats,
  levelTone,
  pageGrowthCards,
  progressOf,
  sortTasks,
  summarizeClaim,
  taskStatusOf,
  toGrowthCards,
  type GrowthAccountTasksPayload,
  type GrowthCard,
  type GrowthCenterAccount,
  type GrowthTask,
} from '@/api/growth'

/**
 * 契约 docs/ui-v2/contracts/growth.md 的数据转换。
 * 测试数据按后端真实响应形状造：
 *   GET /api/growth-center/accounts        → growth_center.py:list_accounts
 *   GET /api/growth/accounts/{id}/tasks    → growth.py:account_tasks（tasks = growth_plans.classify）
 *   POST /api/growth/claim                 → growth.py:claim_accounts（results[].claimed[] / total_credit）
 */

const ACCOUNTS: GrowthCenterAccount[] = [
  { id: 12, name: '193****2486', status: 'active', streak_days: 6, energy: 320 },
  { id: 13, name: '188****0911', status: 'active', streak_days: null, energy: null },
  { id: 14, name: '177****3322', status: 'disabled', streak_days: 2, energy: 10 },
]

function tasksPayload(overrides: Partial<GrowthAccountTasksPayload> = {}): GrowthAccountTasksPayload {
  return {
    account_id: 12,
    name: '193****2486',
    tasks: [
      {
        task_code: 'chat_request_send',
        title: '发送一条对话',
        reward_credit: 50,
        accept_status: 'completed',
        progress: { current: 3, target: 3 },
        level: 'auto',
        level_label: '可自动',
        actionable: true,
        strategy: '发一次请求即完成',
      },
      {
        task_code: 'first_buddy',
        title: '孵化第一只宠物',
        reward_credit: 100,
        accept_status: 'not_accepted',
        level: 'manual',
        level_label: '需人工',
        actionable: false,
      },
      {
        task_code: 'checkin_daily',
        title: '每日打卡',
        reward_credit: 20,
        accept_status: 'claimed',
        level: 'auto',
        level_label: '可自动',
        actionable: true,
      },
      {
        task_code: 'paid_trial',
        title: '付费试用',
        reward_credit: 0,
        accept_status: 'not_accepted',
        level: 'skip',
        level_label: '跳过',
        actionable: false,
      },
    ],
    profile: { level: 3, level_name: 'Lv.3', completed: 1, total: 4, max_level: 10 },
    summary: { total: 4, done: 1, claimable: 1, claimable_credit: 50, actionable: 1 },
    ...overrides,
  }
}

describe('任务状态 / 分级口径', () => {
  it('accept_status → 文案与语气（上游原值）', () => {
    expect(taskStatusOf({ task_code: 'a', accept_status: 'completed' })).toEqual({ label: '可领取', tone: 'warn' })
    expect(taskStatusOf({ task_code: 'a', accept_status: 'claimed' })).toEqual({ label: '已领取', tone: 'ok' })
    expect(taskStatusOf({ task_code: 'a', accept_status: 'not_accepted' })).toEqual({ label: '未参与', tone: 'neutral' })
    expect(taskStatusOf({ task_code: 'a', accept_status: 'accepted' })).toEqual({ label: '进行中', tone: 'brand' })
    expect(taskStatusOf({ task_code: 'a', accept_status: 'weird' }).label).toBe('进行中')
    expect(taskStatusOf({ task_code: 'a', level: 'skip' })).toEqual({ label: '已跳过', tone: 'neutral' })
  })

  it('策略分级 → 徽章语气（auto/multi brand、manual warn、其它 neutral）', () => {
    expect(levelTone('auto')).toBe('brand')
    expect(levelTone('multi')).toBe('brand')
    expect(levelTone('manual')).toBe('warn')
    expect(levelTone('skip')).toBe('neutral')
    expect(levelTone(undefined)).toBe('neutral')
  })
})

describe('progressOf：等级与任务进度', () => {
  it('优先 summary.done/total，level_name 叠加', () => {
    const progress = progressOf({ done: 12, total: 20 }, { level: 3, level_name: 'Lv.3' })
    expect(progress).toMatchObject({ done: 12, total: 20, percent: 60, label: '12/20', levelLabel: 'Lv.3' })
  })

  it('summary 缺失时退回 profile，再缺失显示 —', () => {
    expect(progressOf(null, { completed: 1, total: 4 }).label).toBe('1/4')
    expect(progressOf(null, { level: 2 })).toMatchObject({ percent: 0, label: '—', levelLabel: 'Lv.2' })
    expect(progressOf(null, null)).toMatchObject({ done: 0, total: 0, percent: 0, label: '—', levelLabel: '' })
  })
})

describe('claimableCodes / sortTasks', () => {
  it('只取 completed 的任务码', () => {
    expect(claimableCodes(tasksPayload().tasks)).toEqual(['chat_request_send'])
    expect(claimableCodes(null)).toEqual([])
  })

  it('排序：可领取 → 可做 → 其它 → 已领取', () => {
    const sorted = sortTasks(tasksPayload().tasks).map((task) => task.task_code)
    // completed → 可做 → 其它（保持后端顺序）→ 已领取
    expect(sorted).toEqual(['chat_request_send', 'first_buddy', 'paid_trial', 'checkin_daily'])
  })
})

describe('toGrowthCards：账号 + 任务清单 → 卡片', () => {
  it('合并 summary、claimable、canRun 与禁用状态', () => {
    const cards = toGrowthCards(ACCOUNTS, {
      12: tasksPayload(),
      13: tasksPayload({
        account_id: 13,
        summary: { total: 0, done: 0, claimable: 0, claimable_credit: 0, actionable: 0 },
        tasks: [],
      }),
    })
    expect(cards).toHaveLength(3)
    expect(cards[0]).toMatchObject({
      claimable: 1,
      claimableCredit: 50,
      canRun: true,
      disabled: false,
      loading: false,
      error: '',
    })
    expect(cards[0].progress.label).toBe('1/4')
    expect(cards[0].account.streak_days).toBe(6)

    // 第二个账号任务清单也到了但为空 → 不能做任务
    expect(cards[1].canRun).toBe(false)

    // 第三个账号还是 loading（没有 payload）且账号已禁用
    expect(cards[2].disabled).toBe(true)
    expect(cards[2].loading).toBe(false)
  })

  it('任务清单未回来的卡片保留 loading 骨架；失败的卡片带 error', () => {
    const cards = toGrowthCards(ACCOUNTS.slice(0, 2), {}, {
      loading: { 12: true },
      errors: { 13: '加载任务清单失败：HTTP 502' },
    })
    expect(cards[0]).toMatchObject({ loading: true, error: '' })
    expect(cards[1]).toMatchObject({ loading: false })
    expect(cards[1].error).toContain('502')
  })
})

describe('filterGrowthCards / pageGrowthCards', () => {
  const cards: GrowthCard[] = toGrowthCards(ACCOUNTS, {
    12: tasksPayload(),
    13: tasksPayload({
      account_id: 13,
      tasks: [],
      summary: { total: 0, done: 0, claimable: 0, claimable_credit: 0, actionable: 0 },
    }),
  })

  it('状态筛选：可领 / 可做 / 已禁用', () => {
    expect(filterGrowthCards(cards, { state: 'claimable' }).map((card) => card.account.id)).toEqual([12])
    expect(filterGrowthCards(cards, { state: 'runnable' }).map((card) => card.account.id)).toEqual([12])
    expect(filterGrowthCards(cards, { state: 'disabled' }).map((card) => card.account.id)).toEqual([14])
    expect(filterGrowthCards(cards, {}).map((card) => card.account.id)).toEqual([12, 13, 14])
  })

  it('搜索账号名（大小写与空白容忍；号码星号不是通配）', () => {
    expect(filterGrowthCards(cards, { q: ' 2486 ' }).map((card) => card.account.id)).toEqual([12])
    expect(filterGrowthCards(cards, { q: 'nobody' })).toEqual([])
  })

  it('本地分页：页号越界被夹到最后一页', () => {
    const page = pageGrowthCards(cards, 9, 2)
    expect(page.page).toBe(2)
    expect(page.pageCount).toBe(2)
    expect(page.items.map((card) => card.account.id)).toEqual([14])
    expect(pageGrowthCards(cards, 1, 20).items).toHaveLength(3)
  })
})

describe('growthStats：统计条', () => {
  it('汇总任务数 / 可领 / 积分 / 能量，并显示排除的企业版账号', () => {
    const cards = toGrowthCards(ACCOUNTS, { 12: tasksPayload() })
    const stats = growthStats(cards, 2)
    expect(stats.map((item) => item.label)).toEqual(['账号', '任务总数', '已完成', '可领奖励', '可领积分', '能量合计'])
    expect(stats[0]).toMatchObject({ value: '3', foot: '已排除 2 个企业版账号' })
    expect(stats[1].value).toBe('4')
    expect(stats[2]).toMatchObject({ value: '1', tone: 'ok' })
    expect(stats[3]).toMatchObject({ value: '1', tone: 'warn' })
    expect(stats[4]).toMatchObject({ value: '50', tone: 'warn' })
    expect(stats[5].value).toBe('330')
  })

  it('没有可领时不着色', () => {
    const stats = growthStats([], 0)
    expect(stats[3].tone).toBe('default')
    expect(stats[4].tone).toBe('default')
    expect(stats[0].foot).toBe('成长任务账号')
  })
})

describe('summarizeClaim：POST /api/growth/claim → Toast', () => {
  it('按 claimed[] 统计（already 不算本次）+ total_credit', () => {
    const summary = summarizeClaim({
      results: [
        {
          account_id: 12,
          name: '193****2486',
          ok: true,
          total_credit: 70,
          claimed: [
            { task_code: 'chat_request_send', ok: true, credit: 50 },
            { task_code: 'checkin_daily', ok: true, already: true, credit: 20 },
          ],
        },
      ],
    })
    expect(summary).toMatchObject({ accounts: 1, tasks: 1, credit: 70, failed: 0 })
    expect(summary.text).toBe('已领取 1 个任务，共 70 积分')
  })

  it('全部失败时给失败文案', () => {
    const summary = summarizeClaim({
      results: [{ account_id: 13, ok: false, msg: '账号不存在' }],
    })
    expect(summary.failed).toBe(1)
    expect(summary.text).toContain('领取失败')
  })

  it('空响应不崩', () => {
    expect(summarizeClaim(null)).toMatchObject({ accounts: 0, tasks: 0, credit: 0, failed: 0 })
  })
})

describe('成长中心动作表（growth_center.js 的全部动作）', () => {
  it('五个动作 + 三档连登兑换', () => {
    expect(GROWTH_ACTIONS.map((item) => item.action)).toEqual([
      'checkin', 'open', 'gift', 'draw', 'redeem', 'redeem', 'redeem',
    ])
    expect(GROWTH_ACTIONS.filter((item) => item.action === 'redeem').map((item) => item.tier)).toEqual([
      '7', '14', '28',
    ])
  })
})
