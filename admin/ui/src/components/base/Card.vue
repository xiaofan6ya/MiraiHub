<script setup lang="ts">
/**
 * 卡片（10-visual-refresh §1.1）：表面底 + 1px --border + --shadow-1（很轻）+ 12px 圆角。
 * 英雄区 / 图表大卡用 hero（14px 圆角）；可点击的卡片用 hoverable（悬停换 --shadow-2 与
 * --border-strong，160ms，不上浮、不缩放）。内嵌表格时用 flush 去掉内边距。
 */
import { NSpin } from 'naive-ui'

withDefaults(
  defineProps<{
    title?: string
    /** 标题下一行说明（12px --fg-muted），可选。 */
    subtitle?: string
    /** 去掉内边距（内嵌表格 / 线路带）。 */
    flush?: boolean
    /** 标题右侧 14px 转圈，不遮罩内容（刷新时保留旧数据）。 */
    loading?: boolean
    /** 英雄区 / 图表大卡：14px 圆角。 */
    hero?: boolean
    /** 可点击卡片的悬停效果（点击行为由使用方绑定 @click 或外层 RouterLink）。 */
    hoverable?: boolean
  }>(),
  { title: undefined, subtitle: undefined, flush: false, loading: false, hero: false, hoverable: false },
)
</script>

<template>
  <section
    class="mh-card"
    :class="{ 'mh-card--flush': flush, 'mh-card--hero': hero, 'mh-card--hoverable': hoverable }"
  >
    <header v-if="title || $slots.title || $slots.extra || loading" class="mh-card__head">
      <div class="mh-card__heading">
        <h2 class="mh-card__title">
          <slot name="title">{{ title }}</slot>
        </h2>
        <p v-if="subtitle" class="mh-card__sub">{{ subtitle }}</p>
      </div>
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
  box-shadow: var(--shadow-1);
  min-width: 0;
}

.mh-card--hero {
  border-radius: var(--r-lg);
}

.mh-card--hoverable {
  cursor: pointer;
  transition:
    box-shadow var(--t-base) var(--ease),
    border-color var(--t-base) var(--ease),
    transform var(--dur-1) var(--ease-out);
}

/* 按压反馈（11-polish-motion §1）：scale(.98)，减少动效时不缩放 */
.mh-card--hoverable:active {
  transform: scale(.98);
}

@media (prefers-reduced-motion: reduce) {
  .mh-card--hoverable:active {
    transform: none;
  }
}

.mh-card--hoverable:hover,
.mh-card--hoverable:focus-within {
  box-shadow: var(--shadow-2);
  border-color: var(--border-strong);
}

.mh-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  min-height: 52px;
  padding: var(--sp-3) var(--sp-5) 0;
}

.mh-card__heading {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
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

.mh-card__sub {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
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
  padding: var(--sp-4) var(--sp-5) var(--sp-5);
  min-width: 0;
}

/* 没有标题时上下内边距对称 */
.mh-card__body:first-child {
  padding-top: var(--sp-5);
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

@media (max-width: 767px) {
  .mh-card__head {
    padding-left: var(--sp-4);
    padding-right: var(--sp-4);
  }
  .mh-card__body {
    padding-left: var(--sp-4);
    padding-right: var(--sp-4);
  }
  .mh-card--flush .mh-card__body {
    padding: 0;
  }
}
</style>
