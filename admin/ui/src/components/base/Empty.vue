<script setup lang="ts">
/**
 * 空状态（10-visual-refresh §1.7）：48px 圆形 soft 底 + lucide 图标，一行标题，一行说明，一个动作。
 * 旧用法（description + 默认插槽放按钮）不变；title 可选。
 *
 * 插画位（第 5 轮，可选）：`illustration="empty|search|error|offline"` 用内置插画替换 48px 图标圆，
 * 或用 `#illustration` 插槽放自己的插画。两者都不给时行为与之前完全一致。
 */
import { computed } from 'vue'
import { useI18n } from '@/i18n'
import { Inbox } from 'lucide-vue-next'
import type { Component } from 'vue'
import EmptyIllustration from './EmptyIllustration.vue'
import type { EmptyIllustrationKind } from './empty'

const props = withDefaults(
  defineProps<{
    /** 说明：说清楚下一步（01-direction §8）。 */
    description?: string
    /** 可选的一行标题（例如「还没有 API Key」）。 */
    title?: string
    icon?: Component
    /** 紧凑模式：表格内嵌。 */
    compact?: boolean
    /** 内置插画：无数据 empty / 无结果 search / 出错 error / 离线 offline。不传 = 旧的图标圆。 */
    illustration?: EmptyIllustrationKind
  }>(),
  { description: undefined, title: undefined, icon: undefined, compact: false, illustration: undefined },
)

const { t } = useI18n()
const iconComponent = computed(() => props.icon ?? Inbox)
const desc = computed(() => props.description ?? t('common.empty'))
</script>

<template>
  <div class="mh-empty" :class="{ 'mh-empty--compact': compact, 'mh-empty--art': illustration || $slots.illustration }">
    <span v-if="$slots.illustration || illustration" class="mh-empty__art" aria-hidden="true">
      <slot name="illustration">
        <EmptyIllustration v-if="illustration" :kind="illustration" :compact="compact" />
      </slot>
    </span>
    <span v-else class="mh-empty__icon" aria-hidden="true">
      <component :is="iconComponent" :size="compact ? 20 : 22" />
    </span>
    <p v-if="title || $slots.title" class="mh-empty__title">
      <slot name="title">{{ title }}</slot>
    </p>
    <span class="mh-empty__desc"><slot name="description">{{ desc }}</slot></span>
    <div v-if="$slots.default" class="mh-empty__actions"><slot /></div>
  </div>
</template>

<style scoped>
.mh-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--sp-1);
  padding: var(--sp-10) var(--sp-4);
  text-align: center;
  min-width: 0;
}

.mh-empty--compact {
  padding: var(--sp-6) var(--sp-4);
}

.mh-empty__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  margin-bottom: var(--sp-2);
  border-radius: 50%;
  background: var(--brand-soft);
  color: var(--brand);
}

.mh-empty--compact .mh-empty__icon {
  width: 40px;
  height: 40px;
  margin-bottom: var(--sp-1);
  background: var(--neutral-soft);
  color: var(--fg-muted);
}

.mh-empty__art {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-bottom: var(--sp-3);
}

.mh-empty--compact .mh-empty__art {
  margin-bottom: var(--sp-2);
}

.mh-empty__title {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  font-weight: 600;
  color: var(--fg);
}

.mh-empty__desc {
  display: block;
  max-width: 48ch;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  text-wrap: pretty;
}

.mh-empty__actions {
  display: flex;
  gap: var(--sp-2);
  justify-content: center;
  flex-wrap: wrap;
  margin-top: var(--sp-3);
}
</style>
