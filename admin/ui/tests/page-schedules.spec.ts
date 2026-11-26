/**
 * S11 定时任务：数据转换 / 结果解析 / 筛排分页 / 表单转换 的单测。
 *
 * 数据形状照抄后端真实响应：
 *   · GET /api/schedules        → admin/routers/schedules.py:list_schedules
 *   · GET /api/schedules/tasks  → admin/routers/schedules.py:list_task_catalog
 *   · GET /api/proxies          → admin/routers/proxies.py:list_proxies
 *   · last_result               → admin/scheduler.py:_dump_result 的 JSON 文本
 * 契约见 docs/ui-v2/contracts/schedules.md。
 */
import { describe, expect, it } from 'vitest'
import {
  calendarZoneHint,
  buildCycleLabel,
  buildProxyPoolOptions,
  buildScheduleInput,
  emptyScheduleForm,
  filterScheduleRows,
  formatNextRun,
  intervalOf,
  intervalParts,
  isCheckinTask,
  normalizeTiming,
  paginateScheduleRows,
  parseScheduleResult,
  scheduleFormOf,
  scheduleStats,
  sortScheduleRows,
  toScheduleRows,
  validateScheduleForm,
  type ProxyListPayload,
  type ScheduleGlobalProxy,
  type ScheduleItem,
  type ScheduleTaskOption,
} from '@/api/schedules'

/** 判定「今天/明天」的基准：北京时间 2026-11-23 09:00。 */
const NOW = Date.parse('2026-11-23T01:00:00Z')

/** list_schedules 的真实响应形状（items 按 Schedule.id ASC）。 */
const listItems: ScheduleItem[] = [
  {
    id: 3,
    name: '',
    display_name: '每天 08:47 · 签到领取积分（WorkBuddy）',
    timing: { mode: 'daily', time: '09:00', weekdays: [0], monthday: 1 },
    cycle_label: '每天 09:00',
    task: 'daily_checkin',
    task_label: '签到领取积分（WorkBuddy）',
    platform: 'workbuddy',
    platform_label: 'WorkBuddy',
    interval_minutes: 1440,
    enabled: true,
    last_run_at: '2026-11-23T00:47:12',
    next_run_at: '2026-11-23T01:30:00',
    last_result:
      '{"task":"daily_checkin","ok":true,"claimed":3,"credit":150,"skipped_already":7,' +
      '"summary":"**领取 3 个** · 7 个今日已领过","elapsed_s":12.5,' +
      '"detail":[{"account_id":11,"account":"193****2486","ok":true,"status":"签到成功","credit":50},' +
      '{"account_id":12,"account":"138****7712","ok":true,"status":"签到成功","credit":50}]}',
    stop_after: '2026-12-01T00:00:00',
    proxy_pool: 'residential',
    concurrency: 2,
    time_budget_minutes: 8,
    stale_minutes: 60,
    due_soon: false,
  },
  {
    id: 8,
    name: 'Trae 每小时保活',
    display_name: 'Trae 每小时保活',
    timing: { mode: 'preset', time: '09:00', weekdays: [0], monthday: 1 },
    cycle_label: '每 1 小时',
    task: 'trae_keepalive',
    task_label: '会话保活',
    platform: 'trae',
    platform_label: 'Trae（中国版 + 国际版）',
    interval_minutes: 60,
    enabled: false,
    last_run_at: null,
    next_run_at: null,
    last_result: '',
    stop_after: null,
    proxy_pool: '',
    concurrency: 0,
    time_budget_minutes: 0,
    stale_minutes: 0,
    due_soon: false,
  },
  {
    id: 12,
    name: '',
    display_name: '每月 1 日 09:00 · 更新积分到期快照',
    timing: { mode: 'monthly', time: '09:00', weekdays: [0], monthday: 1 },
    cycle_label: '每月 1 日 09:00',
    task: 'refresh_credits',
    task_label: '更新积分到期快照',
    platform: 'qoder',
    platform_label: 'Qoder 中国版',
    interval_minutes: 43200,
    enabled: true,
    last_run_at: '2026-11-22T10:00:00',
    next_run_at: '2026-12-01T01:00:00',
    last_result: '{"task":"refresh_credits","ok":false,"error":"上游返回 401：登录态失效"}',
    stop_after: null,
    proxy_pool: '',
    concurrency: 4,
    time_budget_minutes: 0,
    stale_minutes: 0,
    due_soon: true,
  },
]

const globalProxy: ScheduleGlobalProxy = {
  proxy_pool: 'lan',
  concurrency: 6,
  time_budget_minutes: 10,
  stale_minutes: 30,
}

describe('toScheduleRows：接口 items → 表格行', () => {
  const rows = toScheduleRows(listItems, { globalProxy, now: NOW })

  it('display_name / cycle_label / 平台名原样落到列上', () => {
    expect(rows.map((row) => row.key)).toEqual(['3', '8', '12'])
    expect(rows[0].displayName).toBe('每天 08:47 · 签到领取积分（WorkBuddy）')
    expect(rows[0].cycleText).toBe('每天 09:00')
    expect(rows[0].zoneHint).toBe(calendarZoneHint())
    expect(rows[0].taskLabel).toBe('签到领取积分（WorkBuddy）')
    expect(rows[2].platformLabel).toBe('Qoder 中国版')
  })

  it('下次执行：今天 / 明天 / 停用 / 往年日期', () => {
    expect(rows[0].nextText).toBe('今天 09:30')
    expect(rows[2].nextText).toBe('12-01 09:00')
    expect(rows[1].nextText).toBe('已停用')
    expect(rows[1].nextRunAt).toBeNull()
  })

  it('出口代理组：任务显式值优先，其次跟随全局', () => {
    expect(rows[0].proxyText).toBe('代理组 residential')
    expect(rows[1].proxyText).toBe('跟随全局（代理组 lan）')
    expect(toScheduleRows([{ ...listItems[1], enabled: true }], { now: NOW })[0].proxyText).toBe('直连')
  })

  it('最近结果：成功 / 尚未运行 / 失败，并保留逐账号明细', () => {
    expect(rows[0].result.tone).toBe('ok')
    expect(rows[0].result.label).toBe('成功')
    expect(rows[0].result.summary).toBe('领取 3 个 · 7 个今日已领过 · 12.5s')
    expect(rows[0].result.detail).toHaveLength(2)
    expect(rows[0].running).toBe(false)

    expect(rows[1].result.tone).toBe('neutral')
    expect(rows[1].result.label).toBe('尚未运行')
    expect(rows[1].result.summary).toBe('')
    expect(rows[1].lastRunText).toBe('—')

    expect(rows[2].result.tone).toBe('danger')
    expect(rows[2].result.summary).toBe('上游返回 401：登录态失效')
  })

  it('timing 缺失 / 非法时兜底成 preset + 09:00 + 周一', () => {
    const [row] = toScheduleRows(
      [{ ...listItems[0], timing: { mode: 'bogus', time: '25:99', weekdays: [], monthday: 0 } as never }],
      { now: NOW },
    )
    expect(row.timing.mode).toBe('preset')
    expect(row.timing.time).toBe('09:00')
    expect(row.timing.weekdays).toEqual([0])
    expect(row.timing.monthday).toBe(1)
  })

  it('null / undefined 输入返回空数组', () => {
    expect(toScheduleRows(null)).toEqual([])
    expect(toScheduleRows(undefined)).toEqual([])
  })
})

describe('parseScheduleResult：last_result 的 JSON 文本', () => {
  it('空串 → 尚未运行', () => {
    const view = parseScheduleResult('')
    expect(view).toMatchObject({ tone: 'neutral', label: '尚未运行', parsed: null, detail: [] })
  })

  it('执行中（成长计划后台跑）→ brand + running', () => {
    const view = parseScheduleResult('{"task":"run_growth_plan","ok":true,"running":true,"summary":"成长计划正在执行"}')
    expect(view.tone).toBe('brand')
    expect(view.label).toBe('执行中')
    expect(view.running).toBe(true)
    expect(view.summary).toBe('成长计划正在执行')
  })

  it('出口保护顺延 → warn + 已顺延', () => {
    const view = parseScheduleResult('{"task":"refresh_balances","ok":true,"deferred":true,"summary":"出口保护期间顺延任务，保护解除后重试"}')
    expect(view.tone).toBe('warn')
    expect(view.label).toBe('已顺延')
    expect(view.running).toBe(false)
  })

  it('已跳过（活动结束）→ neutral', () => {
    const view = parseScheduleResult('{"skipped":"活动已结束","ok":true}')
    expect(view.tone).toBe('neutral')
    expect(view.label).toBe('已跳过')
    expect(view.summary).toBe('活动已结束')
  })

  it('detail 里有失败项 → 部分失败（warn）', () => {
    const view = parseScheduleResult(
      '{"task":"refresh_balances","ok":12,"fail":1,"summary":"失败 1 个：登录态失效",' +
        '"detail":[{"account":"a","ok":true},{"account":"b","ok":false,"reason":"401"}]}',
    )
    expect(view.tone).toBe('warn')
    expect(view.label).toBe('部分失败')
    expect(view.summary).toBe('失败 1 个：登录态失效')
    expect(view.detail).toHaveLength(2)
  })

  it('只有 failed 计数（没有 detail）也判为部分失败', () => {
    const view = parseScheduleResult('{"ok":0,"failed":3,"summary":"3 个账号全部失败"}')
    expect(view.tone).toBe('warn')
    expect(view.label).toBe('部分失败')
  })

  it('ok=false 且无 summary → 失败，摘要从字段兜底', () => {
    const view = parseScheduleResult('{"ok":false,"error":"该平台未注册此任务：qoder_checkin"}')
    expect(view.tone).toBe('danger')
    expect(view.label).toBe('失败')
    expect(view.summary).toBe('该平台未注册此任务：qoder_checkin')
  })

  it('没有 summary 的成功结果按字段拼摘要', () => {
    const view = parseScheduleResult('{"refresh_balances":0,"refreshed":7,"failed":0,"elapsed_s":3}')
    expect(view.tone).toBe('ok')
    expect(view.summary).toBe('刷新 7 个 · 3s')
  })

  it('被截断的历史 JSON → 旧格式（warn），不冒充无结果', () => {
    const view = parseScheduleResult('{"ok":true,"detail":[{"account":"a"')
    expect(view.tone).toBe('warn')
    expect(view.label).toBe('旧格式')
    expect(view.parsed).toBeNull()
    expect(view.raw).toContain('"detail"')
  })
})

describe('formatNextRun：今天 / 明天 / 后天 / 往年', () => {
  it('按时段与日期给出人话', () => {
    expect(formatNextRun('2026-11-23T01:30:00', NOW)).toBe('今天 09:30')
    expect(formatNextRun('2026-11-24T00:00:00', NOW)).toBe('明天 08:00')
    expect(formatNextRun('2026-11-25T01:00:00', NOW)).toBe('后天 09:00')
    expect(formatNextRun('2026-11-30T01:00:00', NOW)).toBe('11-30 09:00')
    expect(formatNextRun('2027-01-02T01:00:00', NOW)).toBe('2027-01-02 09:00')
  })

  it('空值显示 —', () => {
    expect(formatNextRun(null, NOW)).toBe('—')
    expect(formatNextRun('', NOW)).toBe('—')
  })
})

describe('buildCycleLabel / normalizeTiming：周期文案兜底', () => {
  it('interval 按最大可整除单位', () => {
    const timing = normalizeTiming({ mode: 'interval', time: '09:00', weekdays: [0], monthday: 1 })
    expect(buildCycleLabel(timing, 10080)).toBe('每 1 周')
    expect(buildCycleLabel(timing, 1440)).toBe('每 1 天')
    expect(buildCycleLabel(timing, 120)).toBe('每 2 小时')
    expect(buildCycleLabel(timing, 45)).toBe('每 45 分钟')
  })

  it('日历周期按 HH:MM / 星期 / 日', () => {
    expect(buildCycleLabel(normalizeTiming({ mode: 'daily', time: '08:05', weekdays: [0], monthday: 1 }), 60)).toBe('每天 08:05')
    const weekly = normalizeTiming({ mode: 'weekly', time: '21:00', weekdays: [2, 0, 2], monthday: 1 })
    expect(weekly.weekdays).toEqual([0, 2])
    expect(buildCycleLabel(weekly, 60)).toBe('每周一、周三 21:00')
    expect(buildCycleLabel(normalizeTiming({ mode: 'monthly', time: '09:00', weekdays: [0], monthday: 15 }), 60)).toBe('每月 15 日 09:00')
  })
})

describe('filterScheduleRows：搜索 / 平台 / 类型 / 状态 / 结果', () => {
  const rows = toScheduleRows(listItems, { globalProxy, now: NOW })

  it('搜索匹配展示名、任务名、类型、平台、代理组，大小写不敏感', () => {
    expect(filterScheduleRows(rows, { q: '保活' }).map((row) => row.id)).toEqual([8])
    expect(filterScheduleRows(rows, { q: 'RESIDENTIAL' }).map((row) => row.id)).toEqual([3])
    expect(filterScheduleRows(rows, { q: 'qoder' }).map((row) => row.id)).toEqual([12])
    expect(filterScheduleRows(rows, { q: '不存在' })).toEqual([])
  })

  it('平台 / 类型筛选按 id 精确匹配', () => {
    expect(filterScheduleRows(rows, { platform: 'trae' }).map((row) => row.id)).toEqual([8])
    expect(filterScheduleRows(rows, { task: 'refresh_credits' }).map((row) => row.id)).toEqual([12])
  })

  it('状态筛选：已启用 / 已停用 / 已到期', () => {
    expect(filterScheduleRows(rows, { state: 'enabled' }).map((row) => row.id)).toEqual([3, 12])
    expect(filterScheduleRows(rows, { state: 'disabled' }).map((row) => row.id)).toEqual([8])
    expect(filterScheduleRows(rows, { state: 'due' }).map((row) => row.id)).toEqual([12])
  })

  it('结果筛选：成功 / 失败 / 尚未运行', () => {
    expect(filterScheduleRows(rows, { result: 'ok' }).map((row) => row.id)).toEqual([3])
    expect(filterScheduleRows(rows, { result: 'danger' }).map((row) => row.id)).toEqual([12])
    expect(filterScheduleRows(rows, { result: 'none' }).map((row) => row.id)).toEqual([8])
  })

  it('不修改入参', () => {
    filterScheduleRows(rows, { q: 'Trae' })
    expect(rows).toHaveLength(3)
  })
})

describe('sortScheduleRows：三态排序', () => {
  const rows = toScheduleRows(listItems, { globalProxy, now: NOW })

  it('按任务名 / 平台名排序（zh-CN 排序：每天 < 每月 < Trae < Qoder < WorkBuddy）', () => {
    expect(sortScheduleRows(rows, 'name', 'asc').map((row) => row.id)).toEqual([3, 12, 8])
    expect(sortScheduleRows(rows, 'platform', 'asc').map((row) => row.id)).toEqual([12, 8, 3])
  })

  it('按下次执行时间排序：空值（停用）恒排最后，升降序一致', () => {
    expect(sortScheduleRows(rows, 'next_run_at', 'asc').map((row) => row.id)).toEqual([3, 12, 8])
    expect(sortScheduleRows(rows, 'next_run_at', 'desc').map((row) => row.id)).toEqual([12, 3, 8])
  })

  it('按上次运行时间排序（未运行的一行恒在最后）', () => {
    expect(sortScheduleRows(rows, 'last_run_at', 'desc').map((row) => row.id)).toEqual([3, 12, 8])
    expect(sortScheduleRows(rows, 'last_run_at', 'asc').map((row) => row.id)).toEqual([12, 3, 8])
  })

  it('空排序 / 未知字段 / 非法方向保持后端顺序', () => {
    expect(sortScheduleRows(rows, '', '').map((row) => row.id)).toEqual([3, 8, 12])
    expect(sortScheduleRows(rows, 'proxy', 'asc').map((row) => row.id)).toEqual([3, 8, 12])
    expect(sortScheduleRows(rows, 'name', '').map((row) => row.id)).toEqual([3, 8, 12])
  })
})

describe('paginateScheduleRows / scheduleStats', () => {
  const many = toScheduleRows(
    Array.from({ length: 25 }, (_, index) => ({
      ...listItems[0],
      id: index + 1,
      name: '任务 ' + (index + 1),
      display_name: '任务 ' + (index + 1),
    })),
    { now: NOW },
  )

  it('第 2 页取 5 条，越界回退到最后一页', () => {
    const page = paginateScheduleRows(many, 2, 20)
    expect(page.total).toBe(25)
    expect(page.pageCount).toBe(2)
    expect(page.items).toHaveLength(5)
    expect(paginateScheduleRows(many, 99, 20).page).toBe(2)
    expect(paginateScheduleRows([], 3, 20)).toEqual({ items: [], total: 0, pageCount: 0, page: 1 })
  })

  it('统计总数 / 启停 / 到期 / 问题 / 执行中', () => {
    expect(scheduleStats(toScheduleRows(listItems, { globalProxy, now: NOW }))).toEqual({
      total: 3,
      enabled: 2,
      disabled: 1,
      due: 1,
      problem: 1,
      running: 0,
    })
    expect(scheduleStats([])).toEqual({ total: 0, enabled: 0, disabled: 0, due: 0, problem: 0, running: 0 })
  })
})

describe('执行间隔换算', () => {
  it('分钟 → 数值 + 单位，再换回分钟', () => {
    expect(intervalParts(10080)).toEqual({ value: 1, unit: 10080 })
    expect(intervalParts(1440)).toEqual({ value: 1, unit: 1440 })
    expect(intervalParts(120)).toEqual({ value: 2, unit: 60 })
    expect(intervalParts(45)).toEqual({ value: 45, unit: 1 })
    // 0 / 非法值回落到 60 分钟默认值，再按最大单位显示成「1 小时」
    expect(intervalParts(0)).toEqual({ value: 1, unit: 60 })
    expect(intervalOf(2, 60)).toBe(120)
    expect(intervalOf(0, 1)).toBe(1)
    expect(intervalOf(999999999, 10080)).toBe(5256000)
  })
})

describe('表单：回填 / 校验 / 提交体', () => {
  const catalog: ScheduleTaskOption[] = [
    { value: 'daily_checkin', label: '签到领取积分（WorkBuddy）', platform: 'workbuddy', platform_label: 'WorkBuddy', platforms: ['workbuddy'] },
    { value: 'qoder_checkin', label: '签到领取权益（Qoder）', platform: 'qoder', platform_label: 'Qoder 中国版', platforms: ['qoder'] },
    { value: 'refresh_credits', label: '更新积分到期快照', platform: 'workbuddy', platform_label: 'WorkBuddy', platforms: ['workbuddy'] },
  ]

  it('scheduleFormOf 回填周期、间隔、代理与停止时间', () => {
    const form = scheduleFormOf(listItems[0])
    expect(form.id).toBe(3)
    expect(form.mode).toBe('daily')
    expect(form.time).toBe('09:00')
    expect(form.intervalValue).toBe(1)
    expect(form.intervalUnit).toBe(1440)
    expect(form.proxyPool).toBe('residential')
    expect(form.concurrency).toBe(2)
    expect(form.stopAfter).toBe('2026-12-01T00:00')
    expect(form.enabled).toBe(true)
  })

  it('新建默认值与旧版一致（启用 / 60 分钟 / refresh_balances）', () => {
    expect(emptyScheduleForm()).toMatchObject({
      id: null,
      task: 'refresh_balances',
      mode: 'preset',
      intervalValue: 60,
      intervalUnit: 1,
      time: '09:00',
      enabled: true,
      proxyPool: '',
    })
  })

  it('校验：周任务必须选一天、时间必须 HH:MM、数值范围', () => {
    const form = emptyScheduleForm('daily_checkin')
    expect(validateScheduleForm(form)).toEqual({})

    const weekly = { ...form, mode: 'weekly' as const, weekdays: [] }
    expect(validateScheduleForm(weekly).weekdays).toBe('请至少选择一天')

    expect(validateScheduleForm({ ...form, mode: 'daily', time: '25:00' }).time).toContain('HH:MM')
    expect(validateScheduleForm({ ...form, mode: 'monthly', monthday: 40 }).monthday).toBeTruthy()
    expect(validateScheduleForm({ ...form, concurrency: 99 }).concurrency).toBeTruthy()
    expect(validateScheduleForm({ ...form, budgetMinutes: 800 }).budget).toBeTruthy()
    expect(validateScheduleForm({ ...form, staleMinutes: -1 }).stale).toBeTruthy()
  })

  it('buildScheduleInput：platform 由任务决定，签到任务保留 stop_after', () => {
    const form = {
      ...emptyScheduleForm('daily_checkin'),
      name: ' 每日签到 ',
      mode: 'daily' as const,
      time: '08:47',
      intervalValue: 1,
      intervalUnit: 1440,
      proxyPool: ' residential ',
      concurrency: 2,
      budgetMinutes: 8,
      staleMinutes: 60,
      stopAfter: '2026-12-01T00:00',
    }
    expect(buildScheduleInput(form, catalog)).toEqual({
      name: '每日签到',
      task: 'daily_checkin',
      platform: 'workbuddy',
      interval_minutes: 1440,
      timing: { mode: 'daily', time: '08:47', weekdays: [0], monthday: 1 },
      enabled: 1,
      stop_after: '2026-12-01T00:00',
      proxy_pool: 'residential',
      concurrency: 2,
      time_budget_minutes: 8,
      stale_minutes: 60,
    })
  })

  it('buildScheduleInput：非签到任务丢掉 stop_after；周任务去重排序；月任务夹紧', () => {
    const base = emptyScheduleForm('refresh_credits')
    const weekly = buildScheduleInput(
      { ...base, mode: 'weekly', weekdays: [4, 1, 4], time: '21:00', enabled: false },
      catalog,
    )
    expect(weekly.timing).toEqual({ mode: 'weekly', time: '21:00', weekdays: [1, 4], monthday: 1 })
    expect(weekly.stop_after).toBeNull()
    expect(weekly.enabled).toBe(0)

    const monthly = buildScheduleInput({ ...base, mode: 'monthly', monthday: 99, stopAfter: '2026-12-01T00:00' }, catalog)
    expect(monthly.timing).toEqual({ mode: 'monthly', time: '09:00', weekdays: [0], monthday: 31 })
    expect(monthly.stop_after).toBeNull()

    const unknownTask = buildScheduleInput({ ...base, task: 'gone_task' }, catalog)
    expect(unknownTask.platform).toBe('')
  })

  it('isCheckinTask 覆盖各平台签到命名', () => {
    expect(isCheckinTask('daily_checkin')).toBe(true)
    expect(isCheckinTask('trae_checkin')).toBe(true)
    expect(isCheckinTask('wps_lingxi_checkin')).toBe(true)
    expect(isCheckinTask('trae_keepalive')).toBe(false)
  })
})

describe('buildProxyPoolOptions：出口代理组下拉', () => {
  const payload: ProxyListPayload = {
    items: [
      { id: 1, label: '住宅 1', url: 'http://1.2.3.4:8080', enabled: true, max_concurrency: 2, pool: 'residential', blocked: false, blocked_until: null, last_ok_at: null, last_error: '', state: 'ready' },
      { id: 2, label: '住宅 2', url: 'http://1.2.3.5:8080', enabled: true, max_concurrency: 2, pool: 'residential', blocked: true, blocked_until: null, last_ok_at: null, last_error: 'timed out', state: 'blocked' },
      { id: 3, label: '机房 1', url: 'socks5://5.6.7.8:1080', enabled: true, max_concurrency: 8, pool: 'lan', blocked: false, blocked_until: null, last_ok_at: null, last_error: '', state: 'ready' },
    ],
    summary: { total: 3, ready: 2, blocked: 1, disabled: 0, slots: 10 },
    pools: ['lan', 'residential'],
    note: '',
  }

  it('第一项是「跟随全局」，其后按组带可用/总数', () => {
    const options = buildProxyPoolOptions(payload, globalProxy, 'residential')
    expect(options[0]).toEqual({ label: '跟随全局（当前：代理组 lan）', value: '' })
    expect(options[1]).toEqual({ label: '代理组 lan · 1/1 可用', value: 'lan' })
    expect(options[2]).toEqual({ label: '代理组 residential · 1/2 可用', value: 'residential' })
  })

  it('当前组已不存在时补一项，避免保存时被静默清空', () => {
    const options = buildProxyPoolOptions(payload, null, 'old-pool')
    expect(options.map((option) => option.value)).toEqual(['', 'lan', 'residential', 'old-pool'])
    expect(options[3].label).toBe('代理组 old-pool · 节点已不存在')
    expect(options[0].label).toBe('跟随全局（当前：直连）')
  })

  it('没有任何代理组时给出显式提示项', () => {
    const options = buildProxyPoolOptions(null, null, '')
    expect(options[0].value).toBe('')
    expect(options[1]).toMatchObject({ value: '', disabled: true })
  })
})
