<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import SideNav from './SideNav.vue'
import { useI18n } from '@/i18n'

/**
 * <768px 的导航抽屉（10-visual-refresh §1.2）：宽 264px，左侧滑出 200ms，带遮罩；内容复用深墨蓝 SideNav。
 * 不用 Naive 的 NDrawer，是为了让宽度/遮罩/动画与设计稿一一对应（遮罩用 --scrim）。
 */
const props = defineProps<{ open: boolean }>()
const { t } = useI18n()
const emit = defineEmits<{
  (event: 'update:open', value: boolean): void
  (event: 'open-sessions'): void
  (event: 'logout'): void
}>()

function close(): void {
  emit('update:open', false)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') close()
}

function lockScroll(locked: boolean): void {
  if (typeof document === 'undefined') return
  document.body.style.overflow = locked ? 'hidden' : ''
}

watch(
  () => props.open,
  (open) => {
    lockScroll(open)
    if (open) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
)

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  lockScroll(false)
})

function onSessions(): void {
  emit('open-sessions')
  close()
}

function onLogout(): void {
  emit('logout')
  close()
}
</script>

<template>
  <teleport to="body">
    <transition name="mh-drawer">
      <div v-if="open" class="drawer-root">
        <div class="drawer-scrim" @click="close" />
        <div class="drawer-panel" role="dialog" aria-modal="true" :aria-label="t('shell.navMenu')">
          <SideNav
            variant="drawer"
            @navigate="close"
            @open-sessions="onSessions"
            @logout="onLogout"
          />
        </div>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.drawer-root {
  position: fixed;
  inset: 0;
  z-index: 120;
}

.drawer-scrim {
  position: absolute;
  inset: 0;
  background: var(--scrim);
  animation: mh-fade var(--t-base) var(--ease);
}

.drawer-panel {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 264px;
  max-width: 86vw;
  background: var(--side-bg);
  border-right: 1px solid var(--side-border);
  box-shadow: var(--shadow-pop);
  animation: mh-slide var(--t-slow) var(--ease);
}

.mh-drawer-enter-active,
.mh-drawer-leave-active {
  transition: opacity var(--t-base) var(--ease);
}

.mh-drawer-leave-from,
.mh-drawer-enter-to {
  opacity: 1;
}

.mh-drawer-enter-from,
.mh-drawer-leave-to {
  opacity: 0;
}

@keyframes mh-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes mh-slide {
  from {
    transform: translateX(-12px);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}
</style>
