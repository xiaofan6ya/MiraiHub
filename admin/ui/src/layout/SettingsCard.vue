<script setup lang="ts">
/**
 * 设置型页面的区块卡片（10-visual-refresh §1.6）：
 *   · 卡头：标题（+ 未保存小徽章）+ 一行说明（最长 80ch，text-wrap: pretty，避免孤字换行）+ 右侧附加操作；
 *   · 卡体：卡内的每个 FormField 自动排成「左栏 280px 标签 + 说明 / 右栏控件」，组与组之间用
 *     --border-soft 细线分隔；卡体变窄（< 720px）时退回「标签在上、控件在下」。
 *   · **不再有卡片底部的保存 / 取消**：保存统一走 SettingsLayout 的底部保存条。
 *
 * 兼容：`saveLabel / saveDisabled / hideSave / hideReset / loading` 和 `save` / `reset` 事件仍然声明，
 * 旧调用方传了也不会报错，但不再渲染按钮。
 */
import Badge from '@/components/base/Badge.vue'
import { vReveal } from '@/composables/usePageMotion'
import { useI18n } from '@/i18n'

withDefaults(
  defineProps<{
    title: string
    description?: string
    dirty?: boolean
    loading?: boolean
    /** @deprecated 保存统一走底部保存条，这些参数只为兼容旧调用方。 */
    saveLabel?: string
    saveDisabled?: boolean
    hideSave?: boolean
    hideReset?: boolean
    /** 卡体不做「左标签 / 右控件」排版（表格、列表类内容用）。 */
    plain?: boolean
  }>(),
  {
    description: undefined,
    dirty: false,
    loading: false,
    saveLabel: undefined,
    saveDisabled: false,
    hideSave: true,
    hideReset: true,
    plain: false,
  },
)

defineEmits<{ (event: 'save'): void; (event: 'reset'): void }>()
const { t } = useI18n()
</script>

<template>
  <section v-reveal class="settings-card" :class="{ 'settings-card--dirty': dirty }">
    <header class="card-head">
      <div class="head-text">
        <h2 class="card-title">
          <span>{{ title }}</span>
          <Badge v-if="dirty" tone="warn" dot>{{ t('settingsCard.unsaved') }}</Badge>
          <slot name="title-extra" />
        </h2>
        <p v-if="description" class="card-desc">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="card-extra"><slot name="actions" /></div>
    </header>

    <div class="card-body" :class="{ 'card-body--plain': plain }"><slot /></div>
  </section>
</template>

<style scoped>
.settings-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-1);
  padding: var(--sp-5) var(--sp-6);
  min-width: 0;
  scroll-margin-top: calc(var(--topbar-h) + var(--sp-4));
  transition: border-color var(--dur-1, 120ms) var(--ease-out, ease);
}
/* 卡内有焦点时边框轻提亮，键盘用户能看出当前在哪一块 */
.settings-card:focus-within {
  border-color: var(--border-strong);
}
@media (max-width: 767px) {
  .settings-card { padding: var(--sp-4); }
  .card-head { flex-wrap: wrap; }
}
.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-3);
  padding-bottom: var(--sp-4);
  margin-bottom: var(--sp-4);
  border-bottom: 1px solid var(--border-soft);
}
.head-text { min-width: 0; }
.card-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--fg);
}
.card-desc {
  margin: var(--sp-1) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 80ch;
  text-wrap: pretty;
}
.card-extra { flex: none; display: flex; gap: var(--sp-2); align-items: center; }

.card-body {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
  container-type: inline-size;
}

/* —— 「左栏标签 + 说明 / 右栏控件」：作用在卡体直接子级的 FormField 上 —— */
@container (min-width: 720px) {
  .card-body:not(.card-body--plain) > :deep(.mh-field) {
    grid-template-columns: 280px minmax(0, 1fr);
    grid-template-areas:
      'label control'
      'hint control';
    column-gap: var(--sp-6);
    row-gap: 2px;
    align-items: start;
    padding-top: var(--sp-4);
    border-top: 1px solid var(--border-soft);
  }
  .card-body:not(.card-body--plain) > :deep(.mh-field:first-child) {
    padding-top: 0;
    border-top: 0;
  }
  .card-body:not(.card-body--plain) > :deep(.mh-field) > .mh-field__label {
    grid-area: label;
    padding-top: 6px;
  }
  .card-body:not(.card-body--plain) > :deep(.mh-field) > .mh-field__control {
    grid-area: control;
    max-width: 560px;
  }
  .card-body:not(.card-body--plain) > :deep(.mh-field) > .mh-field__hint,
  .card-body:not(.card-body--plain) > :deep(.mh-field) > .mh-field__error {
    grid-area: hint;
    max-width: 44ch;
    text-wrap: pretty;
  }
}
</style>
