<script setup lang="ts">
/**
 * 平台管理 · 单张平台卡（从 PlatformsPage 拆出，r3）。
 *
 * 卡头：logo 方块 + 名称 + 「区域 · 优先级 · 档位」+ 圆点状态徽章；
 * 中部三格关键数（账号 可用/总数 · 今日请求 + 成功率 · TTFT · TPS），格间 1px 竖线分隔；
 * 一行中性色额度条；卡底「账号池」+ 当前平台标记 +「⋯ 更多」。
 * 动作全部 emit 给页面执行，卡片本身无状态。
 */
import { computed } from 'vue'
import { NDropdown, type DropdownOption } from 'naive-ui'
import { MoreHorizontal } from 'lucide-vue-next'
import { Badge, Btn, Meter, PlatformLogo } from '@/components'
import { fmtCompact, fmtCredit, fmtDuration, fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
import { successTone, type PlatformCard } from '@/api/platforms-admin'

const props = defineProps<{
  card: PlatformCard
  current: boolean
  maxCredits: number
  menuOptions: DropdownOption[]
}>()

const emit = defineEmits<{
  (event: 'accounts', card: PlatformCard): void
  (event: 'select', card: PlatformCard, key: string | number): void
}>()

const { t } = useI18n()

/**
 * 卡片状态：每张卡只给一个状态，常态（已启用）不再每张都挂「已启用」徽章（第 1 轮审查 P1-6）。
 *   已停用 → 中性；有账号但没有可用的 → 琥珀「无可用账号」；今日有请求 → 绿「运行中」；其余 → 中性「待命」。
 */
const status = computed<{ tone: 'ok' | 'warn' | 'neutral'; label: string }>(() => {
  const card = props.card
  if (!card.enabled) return { tone: 'neutral', label: t('page.platforms.badge.disabled') }
  if (card.accountCount === 0) return { tone: 'neutral', label: t('page.platforms.status.noAccounts') }
  if (card.activeAccounts === 0) return { tone: 'warn', label: t('page.platforms.status.noActive') }
  if (card.requests > 0) return { tone: 'ok', label: t('page.platforms.status.live') }
  return { tone: 'neutral', label: t('page.platforms.status.idle') }
})

const quotaText = computed(() => {
  const card = props.card
  if (card.credits === null) return '—'
  const unit = card.quotaUnitLabel || ''
  const value = card.quotaUnit === 'token' ? fmtCompact(card.credits) : fmtCredit(card.credits)
  return unit ? value + ' ' + unit : value
})

const rateText = computed(() => (props.card.successRate === null ? '—' : props.card.successRate.toFixed(1) + '%'))
const rateTone = computed(() => (props.card.successRate === null ? 'muted' : successTone(props.card.successRate)))
const showRealm = computed(() => Boolean(props.card.realmLabel) && !props.card.label.includes(props.card.realmLabel || ''))
</script>

<template>
  <article
    class="pf-card"
    :class="{ 'is-disabled': !card.enabled, 'is-current': current }"
    :aria-label="card.label"
  >
    <header class="pf-card__head">
      <span class="pf-card__logo"><PlatformLogo :id="card.id" :size="24" :label="card.label" /></span>
      <div class="pf-card__title">
        <h3 :title="card.notes || card.label">{{ card.label }}</h3>
        <p>
          <template v-if="showRealm">{{ card.realmLabel }} · </template>
          {{ t('page.platforms.card.priority', { value: card.priority === null ? '—' : card.priority }) }}
        </p>
      </div>
      <Badge :tone="status.tone" dot class="pf-card__status">{{ status.label }}</Badge>
    </header>

    <!-- 三个关键数：数字大一号、标签小字（10-visual-refresh §1.4 的卡片层次），格间竖线分隔。 -->
    <dl class="pf-card__stats">
      <div class="pf-stat">
        <dt>{{ t('page.platforms.card.accounts') }}</dt>
        <dd class="tnum">
          <template v-if="card.activeAccounts === null">{{ fmtInt(card.accountCount) }}</template>
          <template v-else>
            {{ fmtInt(card.activeAccounts) }}<span class="pf-stat__sub">/{{ fmtInt(card.accountCount) }}</span>
          </template>
        </dd>
      </div>
      <div class="pf-stat">
        <dt>{{ t('page.platforms.card.todayLabel') }}</dt>
        <dd class="tnum">
          {{ fmtInt(card.requests) }}
          <span v-if="card.successRate !== null" class="pf-stat__sub" :class="'pf-tone-' + rateTone">{{ rateText }}</span>
        </dd>
      </div>
      <div class="pf-stat">
        <dt>TTFT · TPS</dt>
        <dd class="tnum">
          {{ card.ttfbMs ? fmtDuration(card.ttfbMs) : '—' }}<span class="pf-stat__sub"> · {{ card.tps ? card.tps.toFixed(1) : '—' }}</span>
        </dd>
      </div>
    </dl>

    <div class="pf-card__quota">
      <span class="pf-card__quota-text">
        {{ t('page.platforms.card.quota', { value: quotaText }) }}
      </span>
      <!-- 额度条是分类量，不是状态：用中性色（第 1 轮审查 P1-2）。 -->
      <Meter
        :value="card.credits ?? 0"
        :max="maxCredits || 1"
        tone="neutral"
        :aria-label="t('page.platforms.card.quotaAria', { name: card.label })"
      />
    </div>

    <p v-if="!card.enabled" class="pf-card__note">
      {{ t('page.platforms.card.disabledNote') }}{{
        card.disabledReason ? t('page.platforms.card.disabledReason', { reason: card.disabledReason }) : ''
      }}
    </p>

    <footer class="pf-card__actions">
      <Btn size="sm" variant="default" @click="emit('accounts', card)">
        {{ t('page.platforms.card.accountsBtn') }}
      </Btn>
      <span v-if="current" class="pf-card__current">{{ t('page.platforms.card.current') }}</span>
      <n-dropdown
        trigger="click"
        placement="bottom-end"
        :options="menuOptions"
        @select="(key: string | number) => emit('select', card, key)"
      >
        <Btn size="sm" variant="quiet" :icon="MoreHorizontal" :aria-label="t('page.platforms.card.moreAria')">
          {{ t('page.platforms.card.more') }}
        </Btn>
      </n-dropdown>
    </footer>
  </article>
</template>

<style scoped>
.pf-card {
  position: relative;
  display: grid;
  gap: var(--sp-3);
  align-content: start;
  padding: var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  box-shadow: var(--shadow-1);
  min-width: 0;
  transition:
    box-shadow var(--dur-2, 180ms) var(--ease-out, var(--ease)),
    border-color var(--dur-2, 180ms) var(--ease-out, var(--ease));
}

.pf-card:hover,
.pf-card:focus-within {
  box-shadow: var(--shadow-2);
  border-color: var(--border-strong);
}

/* 当前平台：左侧 3px 品牌色竖条（与侧栏选中同一语言）。 */
.pf-card.is-current {
  border-color: var(--border-strong);
  box-shadow: inset 3px 0 0 var(--brand), var(--shadow-1);
}

.pf-card.is-current:hover {
  box-shadow: inset 3px 0 0 var(--brand), var(--shadow-2);
}

.pf-card.is-disabled {
  background: var(--surface-2);
  box-shadow: none;
}

.pf-card.is-disabled .pf-card__head,
.pf-card.is-disabled .pf-card__stats,
.pf-card.is-disabled .pf-card__quota {
  opacity: 0.6;
}

.pf-card__head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-width: 0;
}

.pf-card__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
}

.pf-card__title {
  flex: 1;
  min-width: 0;
}

.pf-card__title h3 {
  margin: 0;
  font-size: var(--fs-h3);
  line-height: var(--lh-h3);
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pf-card__title p {
  margin: 2px 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pf-card__status {
  flex: none;
  align-self: flex-start;
}

.pf-card__stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0;
  padding: var(--sp-3) 0;
  border-top: 1px solid var(--border-soft);
  border-bottom: 1px solid var(--border-soft);
}

.pf-stat {
  display: grid;
  gap: 2px;
  min-width: 0;
  padding: 0 var(--sp-3);
}

.pf-stat:first-child {
  padding-left: 0;
}

.pf-stat + .pf-stat {
  border-left: 1px solid var(--border-soft);
}

.pf-stat dt {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pf-stat dd {
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--fg);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pf-stat__sub {
  margin-left: 2px;
  font-size: var(--fs-small);
  font-weight: 400;
  letter-spacing: 0;
  color: var(--fg-muted);
}

.pf-tone-ok { color: var(--ok); }
.pf-tone-warn { color: var(--warn); }
.pf-tone-danger { color: var(--danger); }
.pf-tone-muted { color: var(--fg-muted); }

.pf-card__quota {
  display: grid;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.pf-card__quota-text {
  font-variant-numeric: tabular-nums;
}

.pf-card__note {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.pf-card__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.pf-card__actions > :last-child {
  margin-left: auto;
}

.pf-card__current {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--brand);
}

.pf-card__current::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--brand);
}

@media (prefers-reduced-motion: reduce) {
  .pf-card {
    transition: none;
  }
}
</style>
