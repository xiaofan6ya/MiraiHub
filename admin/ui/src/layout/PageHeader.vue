<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HelpTip from '@/components/base/HelpTip.vue'
import { groupOf, isPageKey, type PageKey } from '@/router/pages'
import { groupTitle, pageTitle, useI18n } from '@/i18n'

/**
 * 页头（03-shell §2.3 + 10-visual-refresh §1.3）：
 *   [eyebrow 小字]
 *   h1 24/32 · ⓘ · [meta 插槽：徽章 / 统计]              [actions：每页最多一个主按钮]
 *   [subtitle 一行]
 *   同组页签（组内多于 1 页时）
 * 页头不吸顶。eyebrow / subtitle / meta 都可选，旧用法（title / help / actions）不变。
 */
const props = defineProps<{
  /** 不传则取路由 meta.title */
  title?: string
  /** 旧版页面副标题那段长说明，折叠进 ⓘ 气泡 */
  help?: string
  /** h1 上方的小字分组名（12px、--fg-muted），可选（10-visual-refresh §1.3）。 */
  eyebrow?: string
  /** h1 下方一行副标题（--fg-muted），可选。长说明仍放 help。 */
  subtitle?: string
}>()

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const pageKey = computed<PageKey | null>(() => (isPageKey(route.name) ? route.name : null))
const title = computed(() =>
  props.title ?? (pageKey.value ? pageTitle(pageKey.value) : String(route.meta.title ?? '')),
)
const tabs = computed(() => (pageKey.value ? groupOf(pageKey.value).pages : []))
const showTabs = computed(() => tabs.value.length > 1)
const groupLabel = computed(() =>
  pageKey.value ? groupTitle(groupOf(pageKey.value).key) : String(route.meta.group ?? ''),
)

function go(key: PageKey): void {
  if (key === pageKey.value) return
  void router.push('/' + key)
}
</script>

<template>
  <header class="page-header" :class="{ 'page-header--tabs': showTabs }">
    <div class="ph-row">
      <div class="ph-heading">
        <p v-if="eyebrow" class="ph-eyebrow">{{ eyebrow }}</p>
        <div class="ph-title-row">
          <h1 class="ph-title">
            {{ title }}
            <HelpTip v-if="help" :content="help" :label="t('shell.pageHelp')" />
          </h1>
          <div v-if="$slots.meta" class="ph-meta">
            <slot name="meta" />
          </div>
        </div>
        <p v-if="subtitle" class="ph-sub">{{ subtitle }}</p>
      </div>
      <div class="ph-actions">
        <slot name="actions" />
      </div>
    </div>

    <nav
      v-if="showTabs"
      class="ph-tabs"
      role="tablist"
      :aria-label="t('shell.pageTabsAria', { group: groupLabel })"
    >
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        role="tab"
        class="ph-tab"
        :class="{ active: tab.key === pageKey }"
        :aria-selected="tab.key === pageKey ? 'true' : 'false'"
        @click="go(tab.key)"
      >
        {{ pageTitle(tab.key) }}
      </button>
    </nav>
  </header>
</template>

<style scoped>
.page-header {
  margin-bottom: var(--sp-4);
}

.page-header--tabs {
  border-bottom: 1px solid var(--border);
}

.ph-row {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-4);
  min-height: 32px;
  padding-bottom: var(--sp-3);
}

.page-header:not(.page-header--tabs) .ph-row {
  padding-bottom: 0;
}

.ph-heading {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ph-eyebrow {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  color: var(--fg-muted);
}

.ph-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2) var(--sp-3);
  min-width: 0;
}

.ph-title {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  margin: 0;
  min-width: 0;
  font-size: var(--fs-h1);
  line-height: var(--lh-h1);
  font-weight: 600;
  color: var(--fg);
}

.ph-meta {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  min-width: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
}

.ph-sub {
  max-width: 80ch;
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--fg-muted);
  text-wrap: pretty;
}

/* 操作区不截断：放不下就换行（第 1 轮审查 P0-11「暂停平台…」被截断） */
.ph-actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
  padding-top: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
  flex: 0 1 auto;
  min-width: 0;
}

.ph-actions :deep(.n-button__content) {
  white-space: nowrap;
}

.ph-tabs {
  display: flex;
  align-items: center;
  gap: var(--sp-5);
  padding-bottom: 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.ph-tabs::-webkit-scrollbar {
  display: none;
}

.ph-tab {
  position: relative;
  flex: 0 0 auto;
  height: 36px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--fg-muted);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  font-weight: 500;
  cursor: pointer;
  transition: color var(--t-base) var(--ease);
}

.ph-tab:hover {
  color: var(--fg);
}

.ph-tab.active {
  color: var(--fg);
  font-weight: 600;
}

.ph-tab.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  background: var(--brand);
  border-radius: 2px 2px 0 0;
}

.ph-tab:focus-visible {
  outline-offset: -2px;
}

@media (max-width: 767px) {
  .ph-row {
    flex-wrap: wrap;
    gap: var(--sp-2);
  }
  .ph-actions {
    width: 100%;
    justify-content: flex-start;
    margin-left: 0;
  }
}
</style>
