<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HelpTip from '@/components/base/HelpTip.vue'
import { groupOf, isPageKey, type PageKey } from '@/router/pages'

/**
 * 页头（03-shell §2.3）：标题 + 说明气泡 + 右侧操作区 + 同组页签。
 * 页头不吸顶；页签仅在组内多于 1 个页面时出现。
 */
const props = defineProps<{
  /** 不传则取路由 meta.title */
  title?: string
  /** 旧版页面副标题那段长说明，折叠进 ⓘ 气泡 */
  help?: string
}>()

const route = useRoute()
const router = useRouter()

const pageKey = computed<PageKey | null>(() => (isPageKey(route.name) ? route.name : null))
const title = computed(() => props.title ?? String(route.meta.title ?? ''))
const tabs = computed(() => (pageKey.value ? groupOf(pageKey.value).pages : []))
const showTabs = computed(() => tabs.value.length > 1)

function go(key: PageKey): void {
  if (key === pageKey.value) return
  void router.push('/' + key)
}
</script>

<template>
  <header class="page-header">
    <div class="ph-row">
      <h1 class="ph-title">
        {{ title }}
        <HelpTip v-if="help" :content="help" label="页面说明" />
      </h1>
      <div class="ph-actions">
        <slot name="actions" />
      </div>
    </div>

    <nav v-if="showTabs" class="ph-tabs" role="tablist" :aria-label="String(route.meta.group ?? '页面')">
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
        {{ tab.title }}
      </button>
    </nav>
  </header>
</template>

<style scoped>
.page-header {
  margin-bottom: var(--sp-5);
  border-bottom: 1px solid var(--border);
}

.ph-row {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-4);
  min-height: 30px;
  padding-bottom: var(--sp-3);
}

.ph-title {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  margin: 0;
  font-size: var(--fs-h1);
  line-height: var(--lh-h1);
  font-weight: 600;
  color: var(--fg);
}

.ph-actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
  flex-wrap: wrap;
  justify-content: flex-end;
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
  height: 34px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--fg-muted);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
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
  bottom: 0;
  height: 2px;
  background: var(--brand);
  border-radius: 1px;
}

@media (max-width: 640px) {
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