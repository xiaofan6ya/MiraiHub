<script setup lang="ts">
/**
 * 平台网关 · 调度策略卡（从 GatewayPage 拆出，r3）。
 * 卡片里只放策略名（说明进 title），当前策略的说明只在下面写一次。
 * 键盘：radiogroup 内 ←/→/↑/↓ 切换焦点，Enter/空格选中（11-polish-motion §4.5）。
 */
import { ref } from 'vue'
import { Card } from '@/components'
import { strategyDescription, type GatewayConfig } from '@/api/gateway'
import { useI18n } from '@/i18n'

const props = defineProps<{
  config: GatewayConfig | null
  loading: boolean
  note: string
}>()

const emit = defineEmits<{
  (event: 'change', value: string): void
}>()

const { t } = useI18n()
const groupEl = ref<HTMLElement | null>(null)

function onKey(event: KeyboardEvent, index: number): void {
  const list = props.config?.strategies || []
  if (!list.length) return
  let next = -1
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % list.length
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + list.length) % list.length
  if (next < 0) return
  event.preventDefault()
  const buttons = groupEl.value?.querySelectorAll<HTMLButtonElement>('.gw-strategy')
  buttons?.[next]?.focus()
}
</script>

<template>
  <Card class="gw-strategy-card" :loading="loading">
    <template #title>{{ t('page.gateway.strategy.title') }}</template>
    <template #extra>
      <span class="gw-sub">{{ t('page.gateway.strategy.hint') }}</span>
    </template>

    <div
      v-if="config"
      ref="groupEl"
      class="gw-strategy-grid"
      role="radiogroup"
      :aria-label="t('page.gateway.strategy.title')"
    >
      <button
        v-for="(item, index) in config.strategies"
        :key="item.value"
        type="button"
        role="radio"
        class="gw-strategy"
        :class="{ active: config.strategy === item.value }"
        :aria-checked="config.strategy === item.value"
        :tabindex="config.strategy === item.value ? 0 : -1"
        @click="emit('change', item.value)"
        @keydown="onKey($event, index)"
      >
        <span class="gw-strategy__dot" aria-hidden="true" />
        <span class="gw-strategy__name" :title="item.description">{{ item.label }}</span>
      </button>
    </div>
    <p v-if="config" class="gw-strategy__detail">{{ strategyDescription(config) }}</p>
    <p class="gw-note">{{ note }}</p>
  </Card>
</template>

<style scoped>
.gw-sub {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.gw-note {
  margin: var(--sp-3) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 78ch;
}

.gw-strategy-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--sp-2);
  padding: 3px;
  border-radius: calc(var(--r-ctl) + 3px);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
}

/* 分段式选择：底槽 + 选中项浮起（Linear / Vercel 的 segmented 质感）。 */
.gw-strategy {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-2);
  min-height: 36px;
  padding: var(--sp-1) var(--sp-3);
  border: 1px solid transparent;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg-2);
  text-align: center;
  cursor: pointer;
  transition:
    background var(--dur-1, 120ms) var(--ease-out, ease),
    border-color var(--dur-1, 120ms) var(--ease-out, ease),
    box-shadow var(--dur-1, 120ms) var(--ease-out, ease),
    transform var(--dur-1, 120ms) var(--ease-out, ease);
}

.gw-strategy:hover {
  background: var(--surface-hover);
  color: var(--fg);
}

.gw-strategy:active {
  transform: scale(0.98);
}

.gw-strategy:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

.gw-strategy.active {
  border-color: var(--border);
  background: var(--surface-raised, var(--surface));
  box-shadow: var(--shadow-1);
  color: var(--fg);
}

.gw-strategy.active:focus-visible {
  box-shadow: var(--shadow-1), 0 0 0 3px var(--ring);
}

.gw-strategy__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--border-strong);
  transition: background var(--dur-1, 120ms) var(--ease-out, ease);
}

.gw-strategy.active .gw-strategy__dot {
  background: var(--brand);
}

.gw-strategy__name {
  font-size: var(--fs-body);
  font-weight: 500;
  white-space: nowrap;
}

.gw-strategy.active .gw-strategy__name {
  font-weight: 600;
}

.gw-strategy__detail {
  margin: var(--sp-3) 0 0;
  max-width: 80ch;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  text-wrap: pretty;
}

@media (max-width: 1280px) {
  .gw-strategy-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .gw-strategy-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (prefers-reduced-motion: reduce) {
  .gw-strategy,
  .gw-strategy__dot {
    transition: none;
  }
  .gw-strategy:active {
    transform: none;
  }
}
</style>
