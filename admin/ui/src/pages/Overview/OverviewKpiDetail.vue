<script setup lang="ts">
import { computed } from 'vue'
import type { OverviewWindow } from '@/api/overview'
import { useI18n } from '@/i18n'
import { fmtInt } from '@/utils/format'
import { overviewComposition } from './overviewComposition'

const props = defineProps<{ kind: 'success' | 'tokens'; window?: OverviewWindow | null }>()
const data = computed(() => overviewComposition(props.window))
const { t, localeTag } = useI18n()
const pct = (value: number) => value.toLocaleString(localeTag.value, { style: 'percent', maximumFractionDigits: 1 })
</script>

<template>
  <div class="kpi-detail" :data-composition="kind">
    <template v-if="kind === 'success'">
      <div class="kpi-detail__track" aria-hidden="true"><span class="kpi-detail__fill is-success" :style="{ width: data.successShare * 100 + '%' }" /></div>
      <div class="kpi-detail__legend"><span><i class="is-success" />{{ t('overview.composition.success') }} <b class="tnum">{{ window ? fmtInt(data.success) : '—' }}</b></span><span><i class="is-failure" />{{ t('overview.composition.failure') }} <b class="tnum">{{ window ? fmtInt(data.failures) : '—' }}</b></span></div>
    </template>
    <template v-else>
      <div class="kpi-detail__track" aria-hidden="true"><span class="kpi-detail__fill is-input" :style="{ width: data.promptShare * 100 + '%' }" /><span class="kpi-detail__fill is-output" :style="{ width: data.outputShare * 100 + '%' }" /></div>
      <div class="kpi-detail__legend"><span><i class="is-input" />{{ t('overview.composition.input') }} <b class="tnum">{{ data.hasTokens ? pct(data.promptShare) : '—' }}</b></span><span><i class="is-output" />{{ t('overview.composition.output') }} <b class="tnum">{{ data.hasTokens ? pct(data.outputShare) : '—' }}</b></span></div>
    </template>
  </div>
</template>

<style scoped>
.kpi-detail{display:grid;gap:9px;min-width:0}
.kpi-detail__track{display:flex;height:4px;background:var(--border-soft);border-radius:4px;overflow:hidden;gap:2px}
.kpi-detail__fill{height:100%;border-radius:3px;flex-shrink:1}
.kpi-detail__legend{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;column-gap:10px;row-gap:3px;font-size:10px;line-height:16px;color:var(--fg-muted)}
.kpi-detail__legend>span{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.kpi-detail__legend b{font-weight:500;color:var(--fg-2)}
.kpi-detail__legend i{width:4px;height:4px;border-radius:50%;display:inline-block;margin-right:2px}
.is-success{background:var(--ok)}.is-failure{background:var(--danger)}.is-input{background:var(--brand)}.is-output{background:var(--fg-faint)}
</style>
