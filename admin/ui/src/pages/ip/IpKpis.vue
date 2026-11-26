<script setup lang="ts">
/**
 * IP 管理 · 顶部 4 张 KPI（10-visual-refresh §1.4）：活跃 IP / 入口请求 / 封禁 IP / 设备标识。
 * 三个页签共用；封禁数优先取 /api/ip/activity 的 blocked_count（与后端拦截口径一致），
 * 活跃接口还没回来时退回封禁名单里启用中的条数。
 */
import { computed } from 'vue'
import { Activity, Ban, Globe, MonitorSmartphone } from 'lucide-vue-next'
import { KpiCard } from '@/components'
import { fmtCompact, fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
import type { BlockedSourceStats, IpActivityPayload } from '@/api/ip'

const props = withDefaults(
  defineProps<{
    activity: IpActivityPayload | null
    blockedStats: BlockedSourceStats | null
    deviceCount: number | null
    loading?: boolean
  }>(),
  { loading: false },
)

const { t } = useI18n()

const blockedCount = computed<number | null>(() => {
  if (props.activity) return Number(props.activity.blocked_count) || 0
  return props.blockedStats ? props.blockedStats.enabled : null
})

const blockedHint = computed(() =>
  props.blockedStats && props.blockedStats.autoEnabled > 0
    ? t('page.ip.kpi.blockedHint', { auto: fmtInt(props.blockedStats.autoEnabled) })
    : t('page.ip.stat.blockedFoot'),
)
</script>

<template>
  <section class="ip-kpis" :aria-label="t('page.ip.kpi.aria')">
    <KpiCard
      :label="t('page.ip.stat.activeIps')"
      :value="activity ? fmtInt(activity.total_ips) : null"
      :hint="t('page.ip.stat.activeIpsFoot')"
      :icon="Globe"
      tone="brand"
      :loading="loading && !activity"
    />
    <KpiCard
      :label="t('page.ip.stat.requests')"
      :value="activity ? fmtCompact(activity.total_requests) : null"
      split-unit
      :hint="t('page.ip.stat.requestsFoot')"
      :icon="Activity"
      tone="neutral"
      :loading="loading && !activity"
    />
    <KpiCard
      :label="t('page.ip.stat.blocked')"
      :value="blockedCount === null ? null : fmtInt(blockedCount)"
      :hint="blockedHint"
      :icon="Ban"
      :tone="blockedCount ? 'danger' : 'neutral'"
      :loading="loading && blockedCount === null"
    />
    <KpiCard
      :label="t('page.ip.stat.devices')"
      :value="deviceCount === null ? null : fmtInt(deviceCount)"
      :hint="t('page.ip.stat.devicesFoot')"
      :icon="MonitorSmartphone"
      tone="neutral"
      :loading="loading && deviceCount === null"
    />
  </section>
</template>

<style scoped>
.ip-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
  align-items: start;
  min-width: 0;
}

@media (max-width: 1279px) {
  .ip-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .ip-kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
