<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { NTooltip, useMessage } from 'naive-ui'
import { Copy, Menu, MonitorCog, Moon, Search, Sun } from 'lucide-vue-next'
import PlatformPicker from './PlatformPicker.vue'
import { isMobile, openCommandPalette } from './shellState'
import { useHeartbeatStore } from '@/stores/heartbeat'
import { useTheme } from '@/theme/useTheme'
import { apiBaseUrl, copyText } from '@/platform/clipboard'
import { groupTitle, pageTitle, useI18n } from '@/i18n'
import { groupOf, isPageKey } from '@/router/pages'

/**
 * 顶栏（03-shell §2.2）：高 56px、吸顶。
 * 左：≤1023 汉堡 + 平台上下文选择器（platformScoped 页面）或面包屑；
 * 右：命令面板入口 240px、服务状态、复制 API 地址、主题切换。
 * 桌面壳（pywebview）里整条顶栏是拖拽区：interactive 子元素单独 no-drag。
 */
const emit = defineEmits<{ (event: 'open-mobile-nav'): void }>()

const route = useRoute()
const message = useMessage()
const heartbeat = useHeartbeatStore()
const { mode, cycle } = useTheme()
const { t, locale } = useI18n()

const platformScoped = computed(() => Boolean(route.meta.platformScoped))
const pageKey = computed(() => (isPageKey(route.name) ? route.name : null))
const groupLabel = computed(() =>
  pageKey.value ? groupTitle(groupOf(pageKey.value).key) : String(route.meta.group ?? ''),
)
const pageLabel = computed(() =>
  pageKey.value ? pageTitle(pageKey.value) : String(route.meta.title ?? ''),
)

const statusText = computed(() => heartbeat.statusText)
const statusTone = computed(() => heartbeat.statusTone)

const beatText = computed(() => {
  if (!heartbeat.lastBeatAt) return '—'
  const tag = locale.value === 'en-US' ? 'en-US' : 'zh-CN'
  return new Date(heartbeat.lastBeatAt).toLocaleTimeString(tag, { hour12: false })
})
const statusTip = computed(() =>
  t('shell.statusTip', { endpoint: heartbeat.endpoint, time: beatText.value }),
)

const themeLabel = computed(() =>
  mode.value === 'light'
    ? t('shell.themeLight')
    : mode.value === 'dark'
      ? t('shell.themeDark')
      : t('shell.themeSystem'),
)
const themeIcon = computed(() =>
  mode.value === 'light' ? Sun : mode.value === 'dark' ? Moon : MonitorCog,
)

const shortcutLabel = computed(() => {
  if (typeof navigator === 'undefined') return 'Ctrl K'
  const platform = String(navigator.platform || navigator.userAgent || '')
  return /Mac|iPhone|iPad|iPod/i.test(platform) ? '⌘ K' : 'Ctrl K'
})

async function copyApi(): Promise<void> {
  const url = apiBaseUrl()
  const ok = await copyText(url)
  if (ok) message.success(t('common.copied') + ' ' + url)
  else message.error(t('common.copyFailed'))
}
</script>

<template>
  <header class="topbar pywebview-drag-region">
    <div class="topbar-left">
      <button
        v-if="isMobile"
        type="button"
        class="topbar-icon-btn"
        :aria-label="t('shell.openMenu')"
        @click="emit('open-mobile-nav')"
      >
        <Menu :size="18" />
      </button>

      <PlatformPicker v-if="platformScoped" />
      <nav v-else class="breadcrumb" :aria-label="t('shell.breadcrumbAria')">
        <span class="crumb-group">{{ groupLabel }}</span>
        <span class="crumb-sep" aria-hidden="true">/</span>
        <span class="crumb-page">{{ pageLabel }}</span>
      </nav>
    </div>

    <div class="topbar-right">
      <button
        type="button"
        class="cmdk"
        :aria-label="t('shell.searchOrJump') + ' ' + shortcutLabel"
        :aria-keyshortcuts="'Control+K Meta+K'"
        @click="openCommandPalette"
      >
        <Search :size="16" class="cmdk-icon" />
        <span class="cmdk-label">{{ t('shell.searchOrJump') }}</span>
        <kbd class="cmdk-kbd">{{ shortcutLabel }}</kbd>
      </button>

      <n-tooltip placement="bottom" :delay="200">
        <template #trigger>
          <span class="status" role="status" :aria-label="t('shell.serviceStatus', { status: statusText })">
            <span class="status-dot" :class="statusTone" />
            <span class="status-text">{{ statusText }}</span>
          </span>
        </template>
        {{ statusTip }}
      </n-tooltip>

      <n-tooltip placement="bottom" :delay="200">
        <template #trigger>
          <button type="button" class="topbar-btn" :aria-label="t('shell.copyApi')" @click="copyApi">
            <Copy :size="16" />
            <span class="btn-text">{{ t('shell.apiAddress') }}</span>
          </button>
        </template>
        {{ t('shell.copyApiTip', { url: apiBaseUrl() }) }}
      </n-tooltip>

      <n-tooltip placement="bottom" :delay="200">
        <template #trigger>
          <button
            type="button"
            class="topbar-icon-btn"
            :aria-label="t('shell.themeTip', { theme: themeLabel })"
            @click="cycle"
          >
            <component :is="themeIcon" :size="16" />
          </button>
        </template>
        {{ t('shell.themeTip', { theme: themeLabel }) }}
      </n-tooltip>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  height: var(--topbar-h);
  padding: 0 var(--sp-3) 0 var(--sp-4);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  -webkit-app-region: drag;
}

/* 桌面壳里顶栏可拖拽，按钮与选择器必须退回可点击状态。 */
.topbar :deep(button),
.topbar :deep(.picker),
.topbar :deep(input) {
  -webkit-app-region: no-drag;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
  flex: 1 1 auto;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex: 0 0 auto;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
  font-size: var(--fs-body);
  color: var(--fg-muted);
}

.crumb-group {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.crumb-sep {
  color: var(--fg-faint);
}

.crumb-page {
  color: var(--fg);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cmdk {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  width: 240px;
  height: 32px;
  padding: 0 var(--sp-2) 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  color: var(--fg-muted);
  cursor: pointer;
  transition:
    border-color var(--t-fast) var(--ease),
    background var(--t-fast) var(--ease);
}

.cmdk:hover {
  border-color: var(--border-strong);
  background: var(--surface-hover);
}

.cmdk-icon {
  flex: 0 0 auto;
}

.cmdk-label {
  flex: 1 1 auto;
  text-align: left;
  font-size: var(--fs-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cmdk-kbd {
  flex: 0 0 auto;
  padding: 1px 5px;
  border: 1px solid var(--border);
  border-radius: var(--r-chip);
  background: var(--surface);
  color: var(--fg-2);
  font-family: var(--font-sans);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ok);
}

.status-dot.ok {
  background: var(--ok);
  animation: mh-pulse 2s var(--ease) infinite;
}

.status-dot.warn {
  background: var(--warn);
}

.status-dot.danger {
  background: var(--danger);
}

@keyframes mh-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}

@media (prefers-reduced-motion: reduce) {
  .status-dot.ok {
    animation: none;
  }
}

.topbar-btn,
.topbar-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg-2);
  font-size: var(--fs-body);
  cursor: pointer;
  transition:
    background var(--t-fast) var(--ease),
    border-color var(--t-fast) var(--ease);
}

.topbar-btn:hover,
.topbar-icon-btn:hover {
  background: var(--surface-hover);
  border-color: var(--border);
  color: var(--fg);
}

.topbar-icon-btn {
  width: 32px;
  padding: 0;
}

@media (max-width: 1023px) {
  .cmdk {
    width: 32px;
    padding: 0;
    justify-content: center;
  }
  .cmdk-label,
  .cmdk-kbd {
    display: none;
  }
}

@media (max-width: 640px) {
  .topbar {
    padding: 0 var(--sp-2);
    gap: var(--sp-1);
  }
  .topbar-right {
    gap: 2px;
  }
  .status-text,
  .btn-text {
    display: none;
  }
  .status {
    padding: 0 4px;
  }
}
</style>
