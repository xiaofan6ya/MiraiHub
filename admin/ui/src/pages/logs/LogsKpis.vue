<script setup lang="ts">
/**
 * 用量日志顶部 KPI（10-visual-refresh §1.4）：请求数 / 成功率 / 积分消耗 / Token。
 *
 * 口径：/api/logs/summary，跟筛选（时间、账号、模型、Key、结果、IP）走，与分页无关。
 * 后端 log_summary 不接 platform（logs.py:1128）—— 选了平台时在提示里写明「全部平台口径」，
 * 避免和下面按平台筛过的表格对不上（第 1 轮审查 P0-6：同屏口径要标清楚）。
 */
import { computed } from 'vue'
import { Activity, CircleCheck, Coins, Sigma } from 'lucide-vue-next'
import { KpiCard } from '@/components'
import { FMT_EMPTY, fmtCompact, fmtCredit, fmtInt, fmtPct } from '@/utils/format'
import { useI18n } from '@/i18n'
import { successTone, type LogKpis } from '@/api/logs'

const props = withDefaults(
  defineProps<{
    kpis: LogKpis | null
    loading?: boolean
    /** 当前时间范围的文字（今日（自然日） / 近 7 天…） */
    rangeLabel: string
    /** 列表按平台筛选了（汇总不区分平台） */
    platformScoped?: boolean
  }>(),
  { loading: false, platformScoped: false },
)

const { t } = useI18n()

const scopeText = computed(() =>
  props.platformScoped
    ? t('page.logs.kpi.scopeAllPlatforms', { range: props.rangeLabel })
    : props.rangeLabel,
)

const requestsHint = computed(() => {
  const k = props.kpis
  if (!k) return scopeText.value
  return t('page.logs.kpi.requestsHint', { ok: fmtInt(k.ok), failed: fmtInt(k.failed) })
})

const rateHint = computed(() => {
  const k = props.kpis
  if (!k || k.requests === 0) return t('page.logs.kpi.noSample')
  return k.failed > 0 ? t('page.logs.kpi.failedHint', { count: fmtInt(k.failed) }) : t('page.logs.kpi.allOk')
})

const creditsHint = computed(() => {
  const k = props.kpis
  if (!k) return ''
  const base = t('page.logs.kpi.accountsHint', { count: fmtInt(k.accounts) })
  return k.unattributed > 0
    ? base + ' · ' + t('page.logs.kpi.unattributedHint', { count: fmtInt(k.unattributed) })
    : base
})

const rateValue = computed(() =>
  props.kpis && props.kpis.successRate !== null ? fmtPct(props.kpis.successRate) : FMT_EMPTY,
)
</script>

<template>
  <section class="logs-kpis" :aria-label="t('page.logs.kpi.aria')">
    <KpiCard
      :label="t('page.logs.kpi.requests')"
      :value="kpis ? fmtInt(kpis.requests) : null"
      :hint="requestsHint"
      :icon="Activity"
      tone="brand"
      :loading="loading && !kpis"
    />
    <KpiCard
      :label="t('page.logs.kpi.successRate')"
      :value="rateValue"
      split-unit
      :hint="rateHint"
      :icon="CircleCheck"
      :tone="successTone(kpis?.successRate ?? null)"
      :loading="loading && !kpis"
    />
    <KpiCard
      :label="t('page.logs.kpi.credits')"
      :value="kpis ? fmtCredit(kpis.credits) : null"
      :hint="creditsHint"
      :icon="Coins"
      tone="neutral"
      :loading="loading && !kpis"
    />
    <KpiCard
      :label="t('page.logs.kpi.tokens')"
      :value="kpis ? fmtCompact(kpis.tokens) : null"
      split-unit
      :hint="scopeText"
      :icon="Sigma"
      tone="neutral"
      :loading="loading && !kpis"
    />
  </section>
</template>

<style scoped>
.logs-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
  align-items: start;
  min-width: 0;
}

@media (max-width: 1279px) {
  .logs-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .logs-kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
