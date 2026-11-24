<script setup lang="ts">
/**
 * 智能体 · 空对话时的「开始创作」三张模式卡（从 AgentPage 拆出，r3）= 模式选择。
 * 键盘：radiogroup，←/→/↑/↓ 切换，选中即生效。
 */
import { ref, type Component } from 'vue'
import { Image, MessageSquare, Video } from 'lucide-vue-next'
import { useI18n } from '@/i18n'
import type { AgentMode } from './agentPrefs'

const props = defineProps<{ mode: AgentMode }>()
const emit = defineEmits<{ (event: 'update:mode', value: AgentMode): void }>()

const { t } = useI18n()
const groupEl = ref<HTMLElement | null>(null)

const cards: Array<{ value: AgentMode; icon: Component; title: 'page.agent.mode.chat' | 'page.agent.mode.image' | 'page.agent.mode.video'; desc: 'page.agent.start.chatDesc' | 'page.agent.start.imageDesc' | 'page.agent.start.videoDesc' }> = [
  { value: 'chat', icon: MessageSquare, title: 'page.agent.mode.chat', desc: 'page.agent.start.chatDesc' },
  { value: 'image', icon: Image, title: 'page.agent.mode.image', desc: 'page.agent.start.imageDesc' },
  { value: 'video', icon: Video, title: 'page.agent.mode.video', desc: 'page.agent.start.videoDesc' },
]

function onKey(event: KeyboardEvent, index: number): void {
  let next = -1
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % cards.length
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + cards.length) % cards.length
  if (next < 0) return
  event.preventDefault()
  emit('update:mode', cards[next].value)
  groupEl.value?.querySelectorAll<HTMLButtonElement>('.agent-start__card')[next]?.focus()
}
</script>

<template>
  <div class="agent-start">
    <p class="agent-start__title">{{ t('page.agent.startTitle') }}</p>
    <div ref="groupEl" class="agent-start__cards" role="radiogroup" :aria-label="t('page.agent.modeAria')">
      <button
        v-for="(card, index) in cards"
        :key="card.value"
        type="button"
        role="radio"
        class="agent-start__card"
        :class="{ 'is-active': props.mode === card.value }"
        :aria-checked="props.mode === card.value"
        :tabindex="props.mode === card.value ? 0 : -1"
        @click="emit('update:mode', card.value)"
        @keydown="onKey($event, index)"
      >
        <span class="agent-start__icon"><component :is="card.icon" :size="18" aria-hidden="true" /></span>
        <strong>{{ t(card.title) }}</strong>
        <span class="agent-start__desc">{{ t(card.desc) }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.agent-start {
  display: grid;
  gap: var(--sp-4);
  padding: var(--sp-8, 48px) 0 var(--sp-6);
}

.agent-start__title {
  margin: 0;
  font-size: var(--fs-h1);
  line-height: var(--lh-h1);
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--fg);
}

.agent-start__cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-3);
}

.agent-start__card {
  display: grid;
  justify-items: start;
  gap: var(--sp-1);
  text-align: left;
  padding: var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  box-shadow: var(--shadow-1);
  cursor: pointer;
  color: var(--fg);
  transition:
    border-color var(--dur-2, 180ms) var(--ease-out, ease),
    box-shadow var(--dur-2, 180ms) var(--ease-out, ease),
    transform var(--dur-1, 120ms) var(--ease-out, ease);
}

.agent-start__card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-2);
}

.agent-start__card:active {
  transform: scale(0.98);
}

.agent-start__card:focus-visible {
  outline: none;
  box-shadow: var(--shadow-1), 0 0 0 3px var(--ring);
}

.agent-start__card.is-active {
  border-color: var(--brand);
  background: var(--brand-soft);
}

.agent-start__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  margin-bottom: var(--sp-1);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  color: var(--fg-2);
}

.agent-start__card.is-active .agent-start__icon {
  background: var(--brand);
  color: #fff;
}

.agent-start__card strong {
  font-size: var(--fs-body);
  font-weight: 600;
}

.agent-start__card.is-active strong {
  color: var(--brand);
}

.agent-start__desc {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

@media (max-width: 640px) {
  .agent-start {
    padding-top: var(--sp-4);
  }
  .agent-start__cards {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (prefers-reduced-motion: reduce) {
  .agent-start__card {
    transition: none;
  }
  .agent-start__card:active {
    transform: none;
  }
}
</style>
