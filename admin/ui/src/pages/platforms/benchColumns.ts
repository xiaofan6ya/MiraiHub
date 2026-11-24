/**
 * 平台管理 · 速度对比表列定义（从 PlatformsPage 拆出，r3）。
 * 单元格由 DataTable 渲染，拿不到页面的 scoped id —— 用到的类名样式写在页面的 :deep() 里。
 */
import { computed, h, type ComputedRef } from 'vue'
import { Badge, type TableColumn, type TableRow } from '@/components'
import { fmtDuration, fmtInt } from '@/utils/format'
import { successTone, type PlatformSpeedRow } from '@/api/platforms-admin'
import type { Translate } from '@/i18n'

/** MetricTone（含 muted）→ Badge 的语气。 */
export function badgeToneOf(tone: string): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (tone === 'ok' || tone === 'warn' || tone === 'danger') return tone
  return 'neutral'
}

export function benchCell(row: TableRow, key: keyof PlatformSpeedRow): string {
  const item = row as unknown as PlatformSpeedRow
  if (key === 'success_rate') return Number(item.success_rate).toFixed(1) + '%'
  if (key === 'ttfb_avg' || key === 'latency_avg') return item[key] ? fmtDuration(item[key]) : '—'
  if (key === 'tps_avg') return item.tps_avg ? item.tps_avg.toFixed(1) + ' tok/s' : '—'
  if (key === 'jitter') return item.jitter ? fmtDuration(item.jitter) : '—'
  return String(item[key] ?? '—')
}

export function usePlatformBenchColumns(t: Translate): ComputedRef<TableColumn[]> {
  return computed<TableColumn[]>(() => [
    { key: 'rank', title: '#', width: 48, mobile: 'meta', render: (row: TableRow) => h('span', { class: 'pf-rank' }, String(row.rank ?? '—')) },
    // 首列吸附：窄屏横向滚动时平台名一直可见（11-polish-motion §2）。
    { key: 'label', title: t('page.platforms.bench.platform'), minWidth: 140, mobile: 'title', fixed: 'left' },
    {
      key: 'success_rate',
      title: t('page.platforms.bench.successRate'),
      width: 96,
      mobile: 'subtitle',
      render: (row: TableRow) => {
        const item = row as unknown as PlatformSpeedRow
        return h(Badge, { tone: badgeToneOf(successTone(item.success_rate)) }, { default: () => benchCell(row, 'success_rate') })
      },
    },
    { key: 'ttfb_avg', title: t('page.platforms.bench.ttfb'), width: 110, numeric: true, mobile: 'meta', render: (row: TableRow) => benchCell(row, 'ttfb_avg') },
    { key: 'tps_avg', title: t('page.platforms.bench.tps'), width: 110, numeric: true, mobile: 'meta', render: (row: TableRow) => benchCell(row, 'tps_avg') },
    { key: 'latency_avg', title: t('page.platforms.bench.latency'), width: 120, numeric: true, mobile: 'meta', render: (row: TableRow) => benchCell(row, 'latency_avg') },
    { key: 'jitter', title: t('page.platforms.bench.jitter'), width: 90, numeric: true, mobile: 'hidden', render: (row: TableRow) => benchCell(row, 'jitter') },
    {
      key: 'samples',
      title: t('page.platforms.bench.samples'),
      width: 110,
      numeric: true,
      mobile: 'hidden',
      render: (row: TableRow) => {
        const item = row as unknown as PlatformSpeedRow
        return h(
          'span',
          { class: item.low_sample ? 'pf-low' : '' },
          fmtInt(item.samples) + (item.low_sample ? t('page.platforms.bench.lowSample') : ''),
        )
      },
    },
  ])
}
