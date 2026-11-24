<script setup lang="ts">
/** 卡片内分组：小标题 + 内容，组间 1px --border-soft 分隔（01-direction §5）。 */
withDefaults(
  defineProps<{
    title?: string
    /** 组内第一块：去掉上分隔线，避免与卡片标题打架。 */
    first?: boolean
  }>(),
  { title: undefined, first: false },
)
</script>

<template>
  <section class="mh-section" :class="{ 'mh-section--first': first }">
    <header v-if="title || $slots.title || $slots.extra" class="mh-section__head">
      <h3 class="mh-section__title">
        <slot name="title">{{ title }}</slot>
      </h3>
      <slot name="extra" />
    </header>
    <div class="mh-section__body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.mh-section {
  padding: var(--sp-4) 0;
  border-top: 1px solid var(--border-soft);
}

.mh-section--first {
  padding-top: 0;
  border-top: 0;
}

.mh-section:last-child {
  padding-bottom: 0;
}

.mh-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  margin-bottom: var(--sp-3);
}

.mh-section__title {
  font-size: var(--fs-h3);
  line-height: var(--lh-h3);
  font-weight: 600;
  color: var(--fg);
}

.mh-section__body {
  min-width: 0;
}
</style>
