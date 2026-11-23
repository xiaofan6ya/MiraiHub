<script setup lang="ts">
/**
 * 模板 C：设置型页面外壳（05-patterns §2C / 08-prompts S06）。
 * 左 200px 锚点目录（滚动高亮）+ 右区块卡片（最大 880px）。
 * 页面级「有未保存修改」时显示 warn Badge，并在路由离开/关闭标签页时确认。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useDialog } from 'naive-ui'
import PageHeader from './PageHeader.vue'
import Badge from '@/components/base/Badge.vue'
import { useI18n } from '@/i18n'

export interface SettingsSection {
  id: string
  title: string
}

const props = withDefaults(
  defineProps<{
    title?: string
    help?: string
    sections: SettingsSection[]
    /** 任一卡片有未保存修改（由页面汇总） */
    dirty?: boolean
  }>(),
  { title: undefined, help: undefined, dirty: false },
)

const dialog = useDialog()
const { t } = useI18n()
const activeId = ref(props.sections[0]?.id ?? '')
let observer: IntersectionObserver | null = null

function jump(id: string): void {
  activeId.value = id
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!props.dirty) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
  if (typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visible[0]?.target.id) activeId.value = visible[0].target.id
    },
    { rootMargin: '-88px 0px -60% 0px', threshold: 0 },
  )
  for (const section of props.sections) {
    const el = document.getElementById(section.id)
    if (el) observer.observe(el)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
  observer?.disconnect()
  observer = null
})

onBeforeRouteLeave(() => {
  if (!props.dirty) return true
  return new Promise<boolean>((resolve) => {
    dialog.warning({
      title: t('settingsLayout.leaveTitle'),
      content: t('settingsLayout.leaveBody'),
      positiveText: t('settingsLayout.leave'),
      negativeText: t('settingsLayout.keepEditing'),
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
      onMaskClick: () => resolve(false),
    })
  })
})

const dirtyLabel = computed(() => (props.dirty ? t('settingsLayout.unsaved') : ''))
</script>

<template>
  <div class="settings-page">
    <PageHeader :title="title" :help="help">
      <template #actions>
        <Badge v-if="dirty" tone="warn" dot>{{ dirtyLabel }}</Badge>
        <slot name="actions" />
      </template>
    </PageHeader>

    <div class="settings-body">
      <nav class="settings-nav" :aria-label="t('settingsLayout.nav')">
        <button
          v-for="section in sections"
          :key="section.id"
          type="button"
          class="nav-item"
          :class="{ active: activeId === section.id }"
          @click="jump(section.id)"
        >
          {{ section.title }}
        </button>
      </nav>

      <div class="settings-main">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-page {
  width: 100%;
  min-width: 0;
}
.settings-body {
  display: grid;
  grid-template-columns: 200px minmax(0, 880px);
  gap: var(--sp-6);
  align-items: start;
}
.settings-nav {
  position: sticky;
  top: calc(var(--topbar-h) + var(--sp-4));
  display: grid;
  gap: 2px;
  align-content: start;
}
.nav-item {
  display: block;
  width: 100%;
  padding: 6px var(--sp-3);
  border: 0;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg-muted);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  text-align: left;
  cursor: pointer;
  transition: background var(--t-base) var(--ease), color var(--t-base) var(--ease);
}
.nav-item:hover {
  background: var(--surface-hover);
  color: var(--fg);
}
.nav-item.active {
  background: var(--brand-soft);
  color: var(--brand);
  font-weight: 600;
}
.settings-main {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
@media (max-width: 1023px) {
  .settings-body {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sp-3);
  }
  .settings-nav {
    position: static;
    display: flex;
    gap: var(--sp-1);
    overflow-x: auto;
    padding-bottom: var(--sp-1);
    scrollbar-width: none;
  }
  .settings-nav::-webkit-scrollbar { display: none; }
  .nav-item { width: auto; white-space: nowrap; }
}
</style>
