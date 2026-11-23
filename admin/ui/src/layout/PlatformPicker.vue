<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { NInput, NPopover } from 'naive-ui'
import { Check, ChevronDown, Search } from 'lucide-vue-next'
import { usePlatformStore } from '@/stores/platform'
import type { PlatformSpec } from '@/api/platforms'
import PlatformLogo from '@/components/identity/PlatformLogo.vue'
import { useI18n } from '@/i18n'

/**
 * 顶栏「平台上下文」选择器（03-shell §2.2）。
 *
 * 数据全部来自 S03 的 usePlatformStore（GET /api/platforms、
 * GET/PUT /api/platforms/current，localStorage `wb_platform_current`）：
 * 切平台 = 换全局上下文，切换后 store 会派发 `wb-platform-change`，
 * 依赖平台的页面各自重新取数 —— 外壳不重复发任何平台请求。
 */
const store = usePlatformStore()
const { t } = useI18n()

const open = ref(false)
const query = ref('')
const inputRef = ref<InstanceType<typeof NInput> | null>(null)

const platforms = computed<PlatformSpec[]>(() => store.platforms)
const currentId = computed(() => store.currentId)
const currentSpec = computed(() => store.currentSpec)
const currentLabel = computed(() => currentSpec.value?.label || currentId.value || t('picker.platform'))
const currentCount = computed(() => {
  const total = store.countOf(currentId.value)
  return typeof total === 'number' ? t('picker.accountCount', { count: total }) : ''
})

const filtered = computed<PlatformSpec[]>(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return platforms.value
  return platforms.value.filter((item) =>
    [item.label, item.id, item.realm_label, item.storage_platform, ...item.realms.map((r) => r.label)]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(keyword),
  )
})

function realmText(item: PlatformSpec): string {
  if (item.realm_label) return item.realm_label
  return item.realms.length ? item.realms.map((realm) => realm.label).join(' / ') : ''
}

/**
 * 账号数：后端只给「该平台总数」和「各区域数」两个口径。
 * 当前平台且选了中国/国际区时显示「本区 / 总数」，其余显示总数。
 */
function countText(item: PlatformSpec): string {
  const total = store.countOf(item.id)
  if (typeof total !== 'number') return '—'
  const realm = store.current.realm
  if (!realm || item.id !== currentId.value) return t('picker.accountCount', { count: total })
  const perRealm = store.realmCountOf(item.id, realm)
  if (typeof perRealm !== 'number') return t('picker.accountCount', { count: total })
  return perRealm + ' / ' + total
}

function choose(item: PlatformSpec): void {
  store.setCurrent(item.id, item.realm || '')
  open.value = false
  query.value = ''
}

watch(open, (value) => {
  if (!value) {
    query.value = ''
    return
  }
  void nextTick(() => inputRef.value?.focus())
})
</script>

<template>
  <n-popover
    v-model:show="open"
    trigger="click"
    placement="bottom-start"
    :width="320"
    :show-arrow="false"
    :content-style="{ padding: '0', borderRadius: 'var(--r-card)' }"
  >
    <template #trigger>
      <button
        type="button"
        class="picker"
        :aria-expanded="open ? 'true' : 'false'"
        aria-haspopup="listbox"
        :aria-label="t('picker.switchAria', { name: currentLabel })"
      >
        <PlatformLogo :id="currentSpec?.icon || currentId" :size="20" />
        <span class="picker-label">{{ currentLabel }}</span>
        <span v-if="currentCount" class="picker-count">{{ currentCount }}</span>
        <ChevronDown :size="14" class="picker-caret" />
      </button>
    </template>

    <div class="picker-panel">
      <div class="picker-search">
        <n-input
          ref="inputRef"
          v-model:value="query"
          size="small"
          :placeholder="t('picker.search')"
          clearable
          :aria-label="t('picker.search')"
        >
          <template #prefix>
            <Search :size="14" />
          </template>
        </n-input>
      </div>

      <div class="picker-list" role="listbox" :aria-label="t('picker.list')">
        <button
          v-for="item in filtered"
          :key="item.id"
          type="button"
          role="option"
          class="picker-row"
          :class="{ active: item.id === currentId }"
          :aria-selected="item.id === currentId ? 'true' : 'false'"
          @click="choose(item)"
        >
          <PlatformLogo :id="item.icon || item.id" :size="20" />
          <span class="row-body">
            <span class="row-name">{{ item.label }}</span>
            <span v-if="realmText(item)" class="row-sub">{{ realmText(item) }}</span>
          </span>
          <span class="row-count">{{ countText(item) }}</span>
          <span class="row-dot" :class="item.enabled === false ? 'off' : 'on'" />
          <Check v-if="item.id === currentId" :size="14" class="row-check" />
        </button>
        <p v-if="!filtered.length" class="picker-empty">
          {{ platforms.length ? t('picker.noMatch') : t('picker.loading') }}
        </p>
      </div>
    </div>
  </n-popover>
</template>

<style scoped>
.picker {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  height: 32px;
  max-width: 320px;
  padding: 0 var(--sp-2);
  border: 1px solid transparent;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg);
  cursor: pointer;
  transition:
    background var(--t-fast) var(--ease),
    border-color var(--t-fast) var(--ease);
}

.picker:hover {
  background: var(--surface-hover);
  border-color: var(--border);
}

.picker-label {
  font-size: var(--fs-body);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.picker-count {
  flex: 0 0 auto;
  padding: 0 6px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  border-radius: var(--r-chip);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  color: var(--fg-muted);
  font-size: var(--fs-tiny);
  font-variant-numeric: tabular-nums;
}

.picker-caret {
  flex: 0 0 auto;
  color: var(--fg-muted);
}

.picker-panel {
  display: flex;
  flex-direction: column;
  max-height: 420px;
}

.picker-search {
  padding: var(--sp-2);
  border-bottom: 1px solid var(--border-soft);
}

.picker-list {
  overflow-y: auto;
  padding: var(--sp-1);
}

.picker-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
  min-height: 44px;
  padding: var(--sp-1) var(--sp-2);
  border: 0;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg);
  text-align: left;
  cursor: pointer;
}

.picker-row:hover {
  background: var(--surface-hover);
}

.picker-row.active {
  background: var(--brand-soft);
}

.row-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1 1 auto;
}

.row-name {
  font-size: var(--fs-body);
  line-height: 20px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-sub {
  font-size: var(--fs-tiny);
  line-height: 16px;
  color: var(--fg-muted);
}

.row-count {
  flex: 0 0 auto;
  color: var(--fg-muted);
  font-size: var(--fs-small);
  font-variant-numeric: tabular-nums;
}

.row-dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.row-dot.on {
  background: var(--ok);
}

.row-dot.off {
  background: var(--fg-faint);
}

.row-check {
  flex: 0 0 auto;
  color: var(--brand);
}

.picker-empty {
  margin: 0;
  padding: var(--sp-4) var(--sp-3);
  font-size: var(--fs-small);
  color: var(--fg-muted);
  text-align: center;
}

@media (max-width: 640px) {
  .picker-count {
    display: none;
  }
  .picker {
    max-width: 140px;
  }
}
</style>