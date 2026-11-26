<script setup lang="ts">
/**
 * 模板 C：设置型页面外壳（05-patterns §2C，按 10-visual-refresh §1.6 重做）。
 *
 *   · 左 200px 锚点目录（滚动高亮）+ 右区块卡片（最大 1080px，卡内「左栏标签 + 说明 / 右栏控件」）；
 *     分区 ≤ 2 个时目录没有意义，隐藏，卡片铺满（第 2 轮审查 P1-3）。
 *   · **唯一的保存方式**是底部吸附的保存条：「有 N 项未保存　放弃　保存」，没有改动时不出现。
 *     页头不再放保存 / 取消，卡片底部也不再各自保存（第 2 轮审查 P1-1/P1-2）。
 *     页面传 `dirty`（布尔或改动项数），监听 `save` / `discard`。
 *   · 有未保存修改时，路由离开 / 关闭标签页会二次确认。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useDialog } from 'naive-ui'
import PageHeader from './PageHeader.vue'
import SaveBar from '@/components/base/SaveBar.vue'
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
    /** 未保存修改：布尔，或改动项数（保存条显示「有 N 项未保存」）。 */
    dirty?: boolean | number
    /** 保存进行中（保存条的「保存」转圈、「放弃」禁用）。 */
    saving?: boolean
    /** 有改动但不能保存（例如校验没过）。 */
    saveDisabled?: boolean
  }>(),
  { title: undefined, help: undefined, dirty: false, saving: false, saveDisabled: false },
)

const emit = defineEmits<{ (event: 'save'): void; (event: 'discard'): void }>()

const dialog = useDialog()
const { t } = useI18n()
const activeId = ref(props.sections[0]?.id ?? '')
let observer: IntersectionObserver | null = null

const isDirty = computed(() => (typeof props.dirty === 'number' ? props.dirty > 0 : props.dirty === true))

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false
}

function jump(id: string): void {
  activeId.value = id
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' })
}

/** 目录键盘操作：↑/↓（窄屏横排时 ←/→）在分区间移动焦点，Home/End 跳到首尾（11-polish §3 焦点）。 */
function onNavKey(event: KeyboardEvent, index: number): void {
  const keys = ['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft', 'Home', 'End']
  if (!keys.includes(event.key)) return
  event.preventDefault()
  const total = props.sections.length
  let next = index
  if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % total
  else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index - 1 + total) % total
  else if (event.key === 'Home') next = 0
  else next = total - 1
  const nav = (event.currentTarget as HTMLElement | null)?.parentElement
  const target = nav?.querySelectorAll<HTMLButtonElement>('.nav-item')[next]
  target?.focus()
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!isDirty.value) return
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
  if (!isDirty.value) return true
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

/** 分区 ≤ 2 个时隐藏目录（只有 1 个分区的页面一定不显示）。 */
const showNav = computed(() => props.sections.length > 2)
</script>

<template>
  <div class="settings-page">
    <PageHeader :title="title" :help="help">
      <template v-if="$slots.actions" #actions>
        <slot name="actions" />
      </template>
    </PageHeader>

    <div class="settings-body" :class="{ 'settings-body--single': !showNav }">
      <nav v-if="showNav" class="settings-nav" :aria-label="t('settingsLayout.nav')">
        <button
          v-for="(section, index) in sections"
          :key="section.id"
          type="button"
          class="nav-item"
          :class="{ active: activeId === section.id }"
          :aria-current="activeId === section.id ? 'true' : undefined"
          @click="jump(section.id)"
          @keydown="onNavKey($event, index)"
        >
          {{ section.title }}
        </button>
      </nav>

      <div class="settings-main">
        <slot />
        <SaveBar
          :dirty="dirty"
          :saving="saving"
          :disabled="saveDisabled"
          @save="emit('save')"
          @discard="emit('discard')"
        />
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
  grid-template-columns: 200px minmax(0, 1080px);
  gap: var(--sp-6);
  align-items: start;
}
.settings-body--single {
  grid-template-columns: minmax(0, 1080px);
}
.settings-nav {
  position: sticky;
  top: calc(var(--topbar-h) + var(--sp-4));
  display: grid;
  gap: 2px;
  align-content: start;
}
.nav-item {
  position: relative;
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
  transition:
    background var(--dur-1, 120ms) var(--ease-out, ease),
    color var(--dur-1, 120ms) var(--ease-out, ease);
}
.nav-item::before {
  /* 选中指示条：从中间展开，不改变布局（零抖动） */
  content: '';
  position: absolute;
  left: 0;
  top: 6px;
  bottom: 6px;
  width: 2px;
  border-radius: 2px;
  background: var(--brand);
  transform: scaleY(0);
  transition: transform var(--dur-2, 180ms) var(--ease-out, ease);
}
.nav-item:hover {
  background: var(--surface-hover);
  color: var(--fg);
}
.nav-item:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}
.nav-item.active {
  background: var(--brand-soft);
  color: var(--brand);
  font-weight: 600;
}
.nav-item.active::before {
  transform: scaleY(1);
}
.settings-main {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
/* 首次挂载时卡片依次淡入（KeepAlive 回来不会重放，二次进入无闪烁） */
.settings-main > :deep(.settings-card) {
  animation: settings-card-in var(--dur-2, 180ms) var(--ease-out, ease) both;
}
.settings-main > :deep(.settings-card:nth-child(2)) { animation-delay: 30ms; }
.settings-main > :deep(.settings-card:nth-child(3)) { animation-delay: 60ms; }
.settings-main > :deep(.settings-card:nth-child(n + 4)) { animation-delay: 90ms; }
@keyframes settings-card-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .settings-main > :deep(.settings-card) { animation: none; }
  .nav-item,
  .nav-item::before { transition: none; }
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
  .nav-item::before { display: none; }
}
</style>
