/**
 * 平台网关 · 平台速度表列定义（从 GatewayPage 拆出，r3）。
 * 单元格由 DataTable 渲染，拿不到页面 scoped id —— 用到的类名样式写在页面的 :deep() 里
 * （拆分前 `.gw-speed-name` 是普通 scoped 选择器，从未生效，本轮顺带修好）。
 */
import { computed, h, type ComputedRef, type Ref, type VNodeChild } from 'vue'
import { Badge, PlatformLogo, type TableColumn, type TableRow } from '@/components'
import { fmtInt } from '@/utils/format'
import type { PlatformSpeedPayload, SpeedRowView } from '@/api/gateway'
import type { Translate } from '@/i18n'

export function useGatewaySpeedColumns(
  t: Translate,
  speed: Ref<PlatformSpeedPayload | null>,
): ComputedRef<TableColumn[]> {
  function speedPlatform(row: TableRow): VNodeChild {
    const item = row as unknown as SpeedRowView
    const fastest = speed.value?.fastest === item.platform
    return h('span', { class: 'gw-speed-name' }, [
      h(PlatformLogo, { id: item.platform, size: 18 }),
      h('span', { class: 'gw-speed-name__text' }, item.label || item.platform),
      fastest ? h(Badge, { tone: 'ok' }, { default: () => t('page.gateway.speed.fastest') }) : null,
      item.low_sample ? h(Badge, { tone: 'neutral' }, { default: () => t('page.gateway.speed.lowSample') }) : null,
    ])
  }

  function speedRate(row: TableRow): VNodeChild {
    const item = row as unknown as SpeedRowView
    return h(Badge, { tone: item.tone }, { default: () => item.successText })
  }

  const num = (text: string): VNodeChild => h('span', { class: 'tnum' }, text)

  return computed<TableColumn[]>(() => [
    // 首列吸附：窄屏横向滚动时平台名一直可见（11-polish-motion §2）。
    { key: 'platform', title: t('page.gateway.speed.platform'), mobile: 'title', minWidth: 200, fixed: 'left', render: speedPlatform },
    { key: 'success', title: t('page.gateway.speed.successRate'), mobile: 'meta', width: 96, numeric: true, render: speedRate },
    { key: 'ttfb', title: t('page.gateway.speed.ttfb'), mobile: 'meta', width: 108, numeric: true, render: (row: TableRow) => num((row as unknown as SpeedRowView).ttfbText) },
    { key: 'tps', title: t('page.gateway.speed.tps'), mobile: 'meta', width: 116, numeric: true, render: (row: TableRow) => num((row as unknown as SpeedRowView).tpsText) },
    { key: 'latency', title: t('page.gateway.speed.latency'), mobile: 'meta', width: 116, numeric: true, render: (row: TableRow) => num((row as unknown as SpeedRowView).latencyText) },
    {
      key: 'samples',
      title: t('page.gateway.speed.samples'),
      mobile: 'meta',
      width: 84,
      numeric: true,
      render: (row: TableRow) => h('span', { class: 'gw-sub tnum' }, fmtInt((row as unknown as SpeedRowView).requests)),
    },
  ])
}
