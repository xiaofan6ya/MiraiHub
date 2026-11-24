/**
 * 定时任务表格的单元格渲染与列定义（从 SchedulesPage.vue 拆出，10-visual-refresh §5）。
 * 只搬家、不改逻辑；启停 / 行操作回调由页面传入。
 * 这些 h() 节点由 DataTable 渲染，拿不到页面的 scoped id，样式在 SchedulesPage.css 里用 :deep 写。
 */
import { computed, h, type ComputedRef, type Ref, type VNodeChild } from 'vue'
import { NSwitch } from 'naive-ui'
import { Badge, RowActions, type TableColumn, type TableRow } from '@/components'
import { FMT_EMPTY, fmtTime } from '@/utils/format'
import { useI18n } from '@/i18n'
import type { ScheduleRow } from '@/api/schedules'

export interface ScheduleColumnDeps {
  runningIds: Ref<number[]>
  togglingIds: Ref<number[]>
  sortOrderOf: (stateKey: string) => 'ascend' | 'descend' | false
  onToggle: (row: ScheduleRow, next: boolean) => Promise<void> | void
  onRowAction: (row: ScheduleRow, key: string) => Promise<void> | void
}

export function useScheduleColumns(deps: ScheduleColumnDeps): ComputedRef<TableColumn[]> {
  const { runningIds, togglingIds, sortOrderOf, onToggle, onRowAction } = deps
  const { t } = useI18n()


  /**
   * 任务标识（`refresh_growth_tasks`）：只允许在下划线后换行（插 <wbr>），
   * 不会再被断成「task / s」（第 2 轮审查 P0-14）。
   */
  function renderTaskId(task: string): VNodeChild {
    const parts = String(task || '').split('_')
    const nodes: VNodeChild[] = []
    parts.forEach((part, index) => {
      if (index > 0) nodes.push('_', h('wbr'))
      nodes.push(part)
    })
    return h('code', { class: 'sc-task__id mono', title: t('page.schedules.cell.taskIdTitle') }, nodes)
  }

  /**
   * 任务列（合并原来的「任务名 / 类型」两列，第 2 轮审查 P2：行高从 ~100px 降到 ~60px）：
   *   主行：中文名（+ 执行中）；副行：mono 标识 ·（名称里没有时）类型 · 停止时间。
   */
  function renderName(row: TableRow): VNodeChild {
    const item = row as ScheduleRow
    const head: VNodeChild[] = [
      h('span', { class: 'sc-name', title: item.displayName }, item.displayName || FMT_EMPTY),
    ]
    if (item.running) {
      head.push(
        h(
          Badge,
          { tone: 'brand', dot: true, class: 'sc-name__badge' },
          { default: () => t('page.schedules.cell.running') },
        ),
      )
    }
    const sub: VNodeChild[] = [renderTaskId(item.task)]
    if (item.taskLabel && !String(item.displayName || '').includes(item.taskLabel)) {
      sub.push(h('span', { class: 'sc-sep', 'aria-hidden': 'true' }, '·'), h('span', null, item.taskLabel))
    }
    if (item.stopAfter) {
      sub.push(
        h('span', { class: 'sc-sep', 'aria-hidden': 'true' }, '·'),
        h(
          'span',
          { class: 'sc-name__stop', title: t('page.schedules.cell.stopAfterTitle') },
          t('page.schedules.cell.stopAfter', { time: fmtTime(item.stopAfter) }),
        ),
      )
    }
    return h('div', { class: 'sc-name-cell' }, [
      h('div', { class: 'sc-name-cell__head' }, head),
      h('div', { class: 'sc-name-cell__sub' }, sub),
    ])
  }

  function renderScope(row: TableRow): VNodeChild {
    const item = row as ScheduleRow
    // 「该平台全部账号」是所有行都一样的常态说明：收进悬停提示，不再和平台名拼在一起（第 2 轮审查 P0-15）。
    return h('div', { class: 'sc-scope' }, [
      h(
        'span',
        {
          title:
            t('page.schedules.cell.scopeTitle', { platform: item.platform || '—' }) +
            ' · ' +
            t('page.schedules.cell.scopeHint'),
        },
        item.platformLabel || FMT_EMPTY,
      ),
    ])
  }

  function renderCycle(row: TableRow): VNodeChild {
    const item = row as ScheduleRow
    // 两行：周期（+ 时区说明，用「·」分隔）/ 下次执行（第 2 轮审查 P0-15：「每 1 小时下次：…」拼在一起）。
    const main: VNodeChild[] = [h('span', { class: 'sc-cycle__main' }, item.cycleText || FMT_EMPTY)]
    if (item.zoneHint) {
      main.push(
        h('span', { class: 'sc-sep', 'aria-hidden': 'true' }, '·'),
        h('span', { class: 'sc-cycle__zone' }, item.zoneHint),
      )
    }
    return h('div', { class: 'sc-cycle' }, [
      h('div', { class: 'sc-cycle__line' }, main),
      h(
        'span',
        { class: item.enabled && item.dueSoon ? 'sc-cycle__next is-due' : 'sc-cycle__next' },
        t('page.schedules.cell.next', { text: item.nextText }),
      ),
    ])
  }

  function renderProxy(row: TableRow): VNodeChild {
    const item = row as ScheduleRow
    const hints: string[] = []
    hints.push(
      item.concurrency > 0
        ? t('page.schedules.cell.concurrency', { count: item.concurrency })
        : t('page.schedules.cell.concurrencyAuto'),
    )
    hints.push(
      item.budgetMinutes > 0
        ? t('page.schedules.cell.budget', { count: item.budgetMinutes })
        : t('page.schedules.cell.budgetDefault'),
    )
    hints.push(
      item.staleMinutes > 0
        ? t('page.schedules.cell.stale', { count: item.staleMinutes })
        : t('page.schedules.cell.staleDefault'),
    )
    // 默认值不必每行都显示（第 2 轮审查 P1-6）：只有改过并发 / 单轮预算 / 只刷时长时才出副行，完整说明在悬停提示里。
    const custom: string[] = []
    if (item.concurrency > 0) custom.push(hints[0])
    if (item.budgetMinutes > 0) custom.push(hints[1])
    if (item.staleMinutes > 0) custom.push(hints[2])
    const children: VNodeChild[] = [
      h('span', { class: item.proxyPool ? 'sc-proxy__main' : 'sc-proxy__main is-global' }, item.proxyText),
    ]
    if (custom.length) children.push(h('span', { class: 'sc-proxy__hint' }, custom.join(' · ')))
    return h('div', { class: 'sc-proxy', title: hints.join('\n') }, children)
  }

  /**
   * 最近结果：只留「徽章 + 时间」，摘要放悬停提示，详情在「查看结果」抽屉里
   * （第 2 轮审查 P0-14：长摘要溢出、压到「立即执行」按钮上）。
   */
  function renderResult(row: TableRow): VNodeChild {
    const item = row as ScheduleRow
    const result = item.result
    const time = item.lastRunAt ? fmtTime(item.lastRunAt) : t('page.schedules.cell.neverRun')
    const tip = [t('page.schedules.cell.lastRunTitle') + ' · ' + time, result.summary].filter(Boolean).join('\n')
    return h('div', { class: 'sc-result', title: tip }, [
      h('div', { class: 'sc-result__head' }, [
        h(Badge, { tone: result.tone, dot: true }, { default: () => result.label }),
        h('span', { class: 'sc-result__time' }, time),
      ]),
    ])
  }

  function renderEnabled(row: TableRow): VNodeChild {
    const item = row as ScheduleRow
    return h(NSwitch, {
      value: item.enabled,
      size: 'small',
      'aria-label': item.enabled
        ? t('page.schedules.action.disableTask')
        : t('page.schedules.action.enableTask'),
      loading: togglingIds.value.includes(item.id),
      'onUpdate:value': (value: boolean) => {
        void onToggle(item, value)
      },
    })
  }

  function renderActions(row: TableRow): VNodeChild {
    const item = row as ScheduleRow
    return h(RowActions, {
      actions: [
        {
          key: 'run',
          label: t('page.schedules.action.run'),
          disabled: runningIds.value.includes(item.id),
        },
        { key: 'edit', label: t('common.edit') },
        { key: 'result', label: t('page.schedules.action.result') },
        { key: 'delete', label: t('common.delete'), danger: true },
      ],
      max: 2,
      onSelect: (key: string) => {
        void onRowAction(item, key)
      },
    })
  }

  const columns = computed<TableColumn[]>(() => [
    {
      key: 'name',
      title: t('page.schedules.col.name'),
      mobile: 'title',
      minWidth: 280,
      sortable: true,
      sortOrder: sortOrderOf('name'),
      render: renderName,
    },
    {
      key: 'platform',
      title: t('page.schedules.col.platform'),
      mobile: 'subtitle',
      width: 176,
      sortable: true,
      sortOrder: sortOrderOf('platform'),
      render: renderScope,
    },
    {
      key: 'cycle',
      title: t('page.schedules.col.cycle'),
      mobile: 'meta',
      minWidth: 200,
      sortable: true,
      sortOrder: sortOrderOf('next_run_at'),
      render: renderCycle,
    },
    {
      key: 'proxy',
      title: t('page.schedules.col.proxy'),
      mobile: 'meta',
      width: 200,
      render: renderProxy,
    },
    {
      key: 'last',
      title: t('page.schedules.col.last'),
      mobile: 'meta',
      width: 200,
      sortable: true,
      sortOrder: sortOrderOf('last_run_at'),
      render: renderResult,
    },
    {
      key: 'enabled',
      title: t('page.schedules.col.enabled'),
      mobile: 'meta',
      width: 72,
      hideable: false,
      render: renderEnabled,
    },
    {
      key: 'actions',
      title: t('page.schedules.col.actions'),
      mobile: 'actions',
      width: 168,
      hideable: false,
      fixed: 'right',
      render: renderActions,
    },
  ])

  return columns
}
