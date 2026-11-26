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
 * 顶栏（03-shell §2.2 + 10-visual-refresh §1.2）：高 56px、吸顶、半透明 + 背景模糊。
 *   左：<768 汉堡 + 面包屑「分组 / 页面」；
 *   中：宽的搜索框样式按钮「搜索页面、账号、Key…  Ctrl K」，点开命令面板；
 *   右：平台选择器胶囊（platformScoped 页面）、服务状态点（不闪烁；异常变 warn/danger 并带失败次数）、
 *       复制 API 地址、主题。
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
/** 单页分组（智能体 / 公开入口）分组名与页面名相同：只显示一段。 */
const showGroupCrumb = computed(() => Boolean(groupLabel.value) && groupLabel.value !== pageLabel.value)

const statusText = computed(() => heartbeat.statusText)
const statusTone = computed(() => heartbeat.statusTone)
/** 异常时显示连续失败次数（正常时不显示数字）。 */
const failureCount = computed(() =>
  heartbeat.status === 'ok' ? 0 : Math.max(1, Number(heartbeat.consecutiveFailures) || 0),
)

const beatText = computed(() => {
  if (!heartbeat.lastBeatAt) return '—'
  const tag = locale.value === 'en-US' ? 'en-US' : 'zh-CN'
  return new Date(heartbeat.lastBeatAt).toLocaleTimeString(tag, { hour12: false })
})
const statusTip = computed(() => {
  const base = t('shell.statusTip', { endpoint: heartbeat.endpoint, time: beatText.value })
  return failureCount.value ? base + ' · ' + t('shell.statusFailures', { count: failureCount.value }) : base
})

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

      <nav class="breadcrumb" :aria-label="t('shell.breadcrumbAria')">
        <template v-if="showGroupCrumb">
          <span class="crumb-group">{{ groupLabel }}</span>
          <span class="crumb-sep" aria-hidden="true">/</span>
        </template>
        <span class="crumb-page" aria-current="page">{{ pageLabel }}</span>
      </nav>
    </div>

    <div class="topbar-center">
      <button
        type="button"
        class="cmdk"
        :aria-label="t('shell.searchOrJump') + ' ' + shortcutLabel"
        :aria-keyshortcuts="'Control+K Meta+K'"
        @click="openCommandPalette"
      >
        <Search :size="16" class="cmdk-icon" />
        <span class="cmdk-label">{{ t('shell.searchPlaceholder') }}</span>
        <kbd class="cmdk-kbd">{{ shortcutLabel }}</kbd>
      </button>
    </div>

    <div class="topbar-right">
      <PlatformPicker v-if="platformScoped" />

      <n-tooltip placement="bottom" :delay="200">
        <template #trigger>
          <span
            class="status"
            :class="'is-' + statusTone"
            role="status"
            :aria-label="t('shell.serviceStatus', { status: statusText })"
          >
            <span class="status-dot" :class="statusTone" />
            <span class="status-text">{{ statusText }}</span>
            <span v-if="failureCount" class="status-count">{{ failureCount }}</span>
          </span>
        </template>
        {{ statusTip }}
      </n-tooltip>

      <n-tooltip placement="bottom" :delay="200">
        <template #trigger>
          <button type="button" class="topbar-icon-btn topbar-copy" :aria-label="t('shell.copyApi')" @click="copyApi">
            <Copy :size="16" />
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
  gap: var(--sp-4);
  height: var(--topbar-h);
  padding: 0 var(--sp-4) 0 var(--sp-6);
  background: var(--topbar-bg);
  -webkit-backdrop-filter: saturate(1.4) blur(10px);
  backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--border-soft);
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
  flex: 1 1 0;
}

.topbar-center {
  display: flex;
  justify-content: center;
  flex: 0 1 440px;
  min-width: 0;
}

.topbar-right {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-2);
  flex: 1 1 0;
  min-width: 0;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
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
  /* 切页时由 View Transitions 平滑替换（router/viewTransition.ts，样式在 base.css） */
  view-transition-name: mh-page-title;
  color: var(--fg);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 搜索框样式的命令面板入口 */
.cmdk {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
  height: 34px;
  padding: 0 6px 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--r-ctl);
  background: var(--surface);
  /* 第 4 轮：原 --fg-faint 在白底上对比度 ≈2.6，作为「入口按钮」的文案太淡；改 --fg-muted（≈4.8） */
  color: var(--fg-muted);
  box-shadow: var(--shadow-1);
  cursor: pointer;
  transition:
    border-color var(--t-fast) var(--ease),
    box-shadow var(--t-fast) var(--ease),
    color var(--t-fast) var(--ease),
    transform var(--dur-1) var(--ease-out);
}

.cmdk:hover {
  border-color: var(--border-strong);
  color: var(--fg-2);
}

.cmdk:active,
.topbar-icon-btn:active {
  transform: scale(.98);
}

@media (prefers-reduced-motion: reduce) {
  .cmdk:active,
  .topbar-icon-btn:active {
    transform: none;
  }
}

.cmdk:focus-visible {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--ring);
}

.cmdk-icon {
  flex: 0 0 auto;
  color: var(--fg-muted);
}

.cmdk-label {
  flex: 1 1 auto;
  min-width: 0;
  text-align: left;
  font-size: var(--fs-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cmdk-kbd {
  flex: 0 0 auto;
  padding: 1px 6px;
  border: 1px solid var(--border);
  border-radius: var(--r-chip);
  background: var(--surface-2);
  color: var(--fg-muted);
  font-family: var(--font-sans);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
}

/* 服务状态：不闪烁（10-visual-refresh §1.9）；异常时整块变色并带失败次数 */
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: var(--fs-small);
  font-weight: 500;
  color: var(--fg-muted);
  white-space: nowrap;
}

.status.is-warn {
  color: var(--warn);
  background: var(--warn-soft);
}

.status.is-danger {
  color: var(--danger);
  background: var(--danger-soft);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 0 3px var(--ok-soft);
}

.status-dot.warn {
  background: var(--warn);
  box-shadow: none;
}

.status-dot.danger {
  background: var(--danger);
  box-shadow: none;
}

/* 失败次数：状态色做底、表面色文字 */
.status-count {
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  color: var(--surface);
  font-size: var(--fs-tiny);
  line-height: 18px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.status.is-warn .status-count {
  background: var(--warn);
}

.status.is-danger .status-count {
  background: var(--danger);
}

.topbar-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg-2);
  cursor: pointer;
  transition:
    background var(--t-fast) var(--ease),
    border-color var(--t-fast) var(--ease),
    transform var(--dur-1) var(--ease-out);
}

.topbar-icon-btn:hover {
  background: var(--surface-hover);
  border-color: var(--border);
  color: var(--fg);
}

@media (max-width: 1279px) {
  .topbar {
    padding-left: var(--sp-4);
    gap: var(--sp-3);
  }
  .status-text {
    display: none;
  }
  .status {
    padding: 0 8px;
  }
}

/* <1024：搜索收成图标按钮，面包屑只留页面名 */
@media (max-width: 1023px) {
  .topbar-center {
    flex: 0 0 auto;
  }
  .cmdk {
    width: 32px;
    height: 32px;
    padding: 0;
    justify-content: center;
    box-shadow: none;
  }
  .cmdk-label,
  .cmdk-kbd {
    display: none;
  }
  .crumb-group,
  .crumb-sep {
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
    flex: 0 1 auto;
  }
  .topbar-left {
    flex: 1 1 auto;
  }
  .status {
    padding: 0 6px;
  }
  /* 375 宽放不下：复制 API 地址在命令面板里也有 */
  .topbar-copy {
    display: none;
  }
}
</style>
