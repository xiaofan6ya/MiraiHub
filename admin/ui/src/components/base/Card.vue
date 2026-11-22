<script setup lang="ts">
/**
 * 卡片：白纸 + 1px 边框 + 10px 圆角，**不用阴影**（只有浮层用阴影）。
 * 内嵌表格时用 flush 去掉内边距。
 */
import { NSpin } from 'naive-ui'

withDefaults(
  defineProps<{
    title?: string
    /** 去掉内边距（内嵌表格 / 线路带）。 */
    flush?: boolean
    /** 标题右侧 14px 转圈，不遮罩内容。 */
    loading?: boolean
  }>(),
  { title: undefined, flush: false, loading: false },
)
</script>

<template>
  <section class="mh-card" :class="{ 'mh-card--flush': flush }">
    <header v-if="title || $slots.title || $slots.extra || loading" class="mh-card__head">
      <h2 class="mh-card__title">
        <slot name="title">{{ title }}</slot>
      </h2>
      <div class="mh-card__tools">
        <n-spin v-if="loading" :size="14" />
        <slot name="extra" />
      </div>
    </header>
    <div class="mh-card__body">
      <slot />
    </div>
    <footer v-if="$slots.footer" class="mh-card__foot">
      <slot name="footer" />
    </footer>
  </section>
</template>

<style scoped>
.mh-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  min-width: 0;
}

.mh-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-5) 0;
}

.mh-card__title {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-card__tools {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex: none;
}

.mh-card__body {
  padding: var(--sp-5);
  min-width: 0;
}

.mh-card--flush .mh-card__body {
  padding: 0;
}

.mh-card--flush .mh-card__head {
  padding-bottom: var(--sp-3);
}

.mh-card__foot {
  border-top: 1px solid var(--border-soft);
  padding: var(--sp-3) var(--sp-5);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
</style>
