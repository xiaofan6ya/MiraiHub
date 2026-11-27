<script setup lang="ts">
/**
 * 风控哨兵 · 状态总览（v2.1）。
 *
 * 第 1 轮审查 P2：原来「未检测到批次级封控」这种常态也用整条绿色横幅，太吵；6 个统计排成 5+1。
 * 现在：
 *   · 常态（ok）：一张普通卡，左侧 32px 绿色盾牌方块 + 一行结论 + 一行灰字事实；
 *   · 有风控账号 / 有暂停（warn）：同一张卡，方块变琥珀，暂停的平台各带一个「恢复」；
 *   · 批次封控（danger）：danger-soft 底的横幅，右侧实心危险按钮「暂停 X 止损」；
 *   · 下方 4 个 KpiCard：今日请求 / 成功率 / 失败·超时 / 风控信号；
 *     「涉及账号」「暂停中的平台」并进上面那行事实，不再单独占格。
 */
import { computed } from 'vue'
import { Play, ShieldAlert, ShieldCheck } from 'lucide-vue-next'
import { Btn, KpiCard } from '@/components'
import type { SentinelBanner, SentinelKpi, SentinelStatusLine } from '@/api/sentinel'
import { useI18n } from '@/i18n'

const props = defineProps<{
  banner: SentinelBanner
  line: SentinelStatusLine
  kpis: SentinelKpi[]
  /** 暂停中的键（'all' = 全部平台）及其展示名。 */
  paused: Array<{ key: string; label: string }>
  loading?: boolean
}>()

const emit = defineEmits<{
  (event: 'pause', platform: string, seconds: number): void
  (event: 'resume', key: string): void
}>()

const { t } = useI18n()

const icon = computed(() => (props.line.tone === 'ok' ? ShieldCheck : ShieldAlert))
</script>

<template>
  <section class="sn-status" :class="'is-' + line.tone" role="status" aria-live="polite">
    <span class="sn-status__icon" aria-hidden="true">
      <component :is="icon" :size="18" />
    </span>
    <div class="sn-status__body">
      <strong class="sn-status__title">{{ banner.title }}</strong>
      <p class="sn-status__facts">
        <span>{{ banner.detail }}</span>
        <span v-for="fact in line.facts" :key="fact">{{ fact }}</span>
      </p>
    </div>
    <div class="sn-status__actions">
      <Btn
        v-if="banner.tripped"
        variant="danger"
        solid
        size="sm"
        @click="emit('pause', banner.platform, banner.suggestedSeconds)"
      >
        {{
          banner.platformLabel
            ? t('page.sentinel.pauseTrippedLabel', { platform: banner.platformLabel })
            : t('page.sentinel.pauseTrippedThis')
        }}
      </Btn>
      <Btn
        v-for="item in paused"
        :key="item.key"
        variant="default"
        size="sm"
        :icon="Play"
        @click="emit('resume', item.key)"
      >
        {{ t('page.sentinel.resumeBtn', { name: item.label }) }}
      </Btn>
    </div>
  </section>

  <section class="sn-kpis" :aria-label="t('page.sentinel.kpiAria')">
    <KpiCard
      v-for="item in kpis"
      :key="item.key"
      :label="item.label"
      :value="item.value"
      split-unit
      :hint="item.hint"
      :tone="item.tone"
      :loading="loading"
    />
  </section>
</template>

<style scoped>
.sn-status {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
  padding: var(--sp-3) var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  box-shadow: var(--shadow-1);
  min-width: 0;
}

.sn-status.is-danger {
  background: var(--danger-soft);
  border-color: var(--danger);
  padding: var(--sp-4) var(--sp-5);
}

.sn-status__icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--r-ctl);
  background: var(--ok-soft);
  color: var(--ok);
}

.sn-status.is-warn .sn-status__icon {
  background: var(--warn-soft);
  color: var(--warn);
}

.sn-status.is-danger .sn-status__icon {
  background: var(--surface);
  color: var(--danger);
}

.sn-status__body {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}

.sn-status__title {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  font-weight: 600;
  color: var(--fg);
}

.sn-status.is-danger .sn-status__title {
  color: var(--danger);
}

.sn-status__facts {
  display: flex;
  flex-wrap: wrap;
  gap: 0 var(--sp-2);
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

/* 事实之间用「·」分隔（文案拼接一律带分隔符）。 */
.sn-status__facts span + span::before {
  content: '·';
  margin-right: var(--sp-2);
  color: var(--fg-faint);
}

.sn-status__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.sn-kpis {
  display: grid;
  grid-auto-rows: 1fr;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
  align-items: stretch;
  min-width: 0;
}

@media (max-width: 1279px) {
  .sn-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .sn-kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
