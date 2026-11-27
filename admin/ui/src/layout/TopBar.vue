<script setup lang="ts">
import { computed, inject, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Activity, Copy, Menu, MonitorCog, Moon, Search, Sun } from 'lucide-vue-next'
import DynamicIsland from '@/components/feedback/DynamicIsland.vue'
import { islandKey, useIslandMessage } from '@/components/feedback/islandQueue'
import PlatformPicker from './PlatformPicker.vue'
import ThemeStudio from './ThemeStudio.vue'
import { isMobile, openCommandPalette } from './shellState'
import { useHeartbeatStore } from '@/stores/heartbeat'
import { useTheme } from '@/theme/useTheme'
import { apiBaseUrl, copyText } from '@/platform/clipboard'
import { groupTitle, pageTitle, useI18n } from '@/i18n'
import { groupOf, isPageKey } from '@/router/pages'

const emit = defineEmits<{ (event: 'open-mobile-nav'): void }>()
const route = useRoute()
const queue = inject(islandKey, null)
if (queue) queue.docked.value = true
onBeforeUnmount(() => { if (queue) queue.docked.value = false })
const message = useIslandMessage()
const heartbeat = useHeartbeatStore()
const { mode, cycle } = useTheme()
const { t, locale } = useI18n()
const platformScoped = computed(() => Boolean(route.meta.platformScoped))
const pageKey = computed(() => isPageKey(route.name) ? route.name : null)
const groupLabel = computed(() => pageKey.value ? groupTitle(groupOf(pageKey.value).key) : String(route.meta.group ?? ''))
const pageLabel = computed(() => pageKey.value ? pageTitle(pageKey.value) : String(route.meta.title ?? ''))
const failureCount = computed(() => heartbeat.status === 'ok' ? 0 : Math.max(1, Number(heartbeat.consecutiveFailures) || 0))
const beatText = computed(() => heartbeat.lastBeatAt ? new Date(heartbeat.lastBeatAt).toLocaleTimeString(locale.value === 'en-US' ? 'en-US' : 'zh-CN', { hour12: false }) : '—')
const themeLabel = computed(() => t(mode.value === 'light' ? 'shell.themeLight' : mode.value === 'dark' ? 'shell.themeDark' : 'shell.themeSystem'))
const themeIcon = computed(() => mode.value === 'light' ? Sun : mode.value === 'dark' ? Moon : MonitorCog)
const shortcutLabel = computed(() => /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '') ? '⌘ K' : 'Ctrl K')
async function copyApi() {
  const url = apiBaseUrl()
  if (await copyText(url)) message.success(t('common.copied') + ' ' + url)
  else message.error(t('common.copyFailed'))
}
// Announce transitions only. Repeated heartbeats do not flood the notification queue.
watch(() => heartbeat.status, (status, previous) => {
  if (status === 'down') message.error(heartbeat.statusText)
  else if (status === 'reconnecting') message.warning(heartbeat.statusText)
  else if (previous !== 'ok') message.success(heartbeat.statusText)
})
</script>

<template>
  <header class="topbar pywebview-drag-region" :aria-label="t('island.workspace')">
    <button v-if="isMobile" type="button" class="topbar-mobile-menu" :aria-label="t('shell.openMenu')" @click="emit('open-mobile-nav')"><Menu :size="18" /></button>
    <DynamicIsland v-if="queue" :queue="queue" persistent :title="pageLabel" :status="heartbeat.statusText" :status-tone="heartbeat.statusTone">
      <template #panel="{ close }">
        <nav class="island-breadcrumb" :aria-label="t('shell.breadcrumbAria')"><span v-if="groupLabel && groupLabel !== pageLabel">{{ groupLabel }} / </span><strong aria-current="page">{{ pageLabel }}</strong></nav>
        <div class="island-service" role="status" :aria-label="t('shell.serviceStatus', { status: heartbeat.statusText })">
          <span class="service-icon" :class="'is-' + heartbeat.statusTone"><Activity :size="18" /></span>
          <div><strong>{{ heartbeat.statusText }}</strong><span>{{ t('island.lastBeat') }} {{ beatText }}<template v-if="failureCount"> · {{ t('shell.statusFailures', { count: failureCount }) }}</template></span></div>
          <span class="service-badge" :class="'is-' + heartbeat.statusTone">{{ heartbeat.statusTone === 'ok' ? 'LIVE' : 'RETRY' }}</span>
        </div>
        <button type="button" class="island-search" :aria-label="t('shell.searchOrJump') + ' ' + shortcutLabel" aria-keyshortcuts="Control+K Meta+K" @click="close(); openCommandPalette()"><Search :size="16" /><span>{{ t('shell.searchPlaceholder') }}</span><kbd>{{ shortcutLabel }}</kbd></button>
        <div class="island-toolbar">
          <PlatformPicker v-if="platformScoped" />
          <button type="button" class="island-tool" :aria-label="t('shell.copyApi')" @click="copyApi"><Copy :size="15" /><span>API</span></button>
          <button type="button" class="island-tool" :aria-label="t('shell.themeTip', { theme: themeLabel })" @click="cycle"><component :is="themeIcon" :size="15" /><span>{{ themeLabel }}</span></button>
          <ThemeStudio />
        </div>
      </template>
    </DynamicIsland>
  </header>
</template>

<style scoped>
.topbar{position:sticky;top:env(safe-area-inset-top,0px);z-index:40;flex:none;height:var(--topbar-h,68px);pointer-events:none;-webkit-app-region:drag}
.topbar :deep(button),.topbar :deep(.picker),.topbar :deep(input){-webkit-app-region:no-drag;pointer-events:auto}
.topbar-mobile-menu{position:absolute;left:12px;top:13px;z-index:55;display:grid;place-items:center;width:32px;height:32px;border:1px solid var(--border);border-radius:50%;background:var(--surface);color:var(--fg-2);cursor:pointer}
.island-breadcrumb{padding:12px 0 13px;border-top:1px solid var(--border-soft);font-size:11px;color:var(--fg-muted)}.island-breadcrumb strong{font-weight:500;color:var(--fg)}
.island-service{display:flex;align-items:center;gap:11px;padding-bottom:16px}.service-icon{display:grid;place-items:center;width:36px;height:36px;border-radius:12px;background:var(--ok-soft);color:var(--ok)}.service-icon.is-warn{color:var(--warn);background:var(--warn-soft)}.service-icon.is-danger{color:var(--danger);background:var(--danger-soft)}.island-service>div{display:grid;gap:3px;flex:1;min-width:0}.island-service strong{font-size:13px;font-weight:500}.island-service>div>span{font-size:10px;color:var(--fg-muted)}.service-badge{font:9px var(--font-mono);letter-spacing:1px;color:var(--ok)}.service-badge.is-warn{color:var(--warn)}.service-badge.is-danger{color:var(--danger)}
.island-search{display:flex;align-items:center;gap:9px;min-height:38px;width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:12px;background:var(--surface-2);color:var(--fg-muted);font:inherit;font-size:11px;text-align:left;cursor:pointer}.island-search span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.island-search kbd{font:9px var(--font-mono);white-space:nowrap;color:var(--fg-muted)}
.island-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding-top:12px}.island-tool{display:flex;align-items:center;justify-content:center;gap:6px;min-height:34px;padding:6px 10px;border:1px solid var(--border);border-radius:11px;background:var(--surface);color:var(--fg-2);font:inherit;font-size:11px;cursor:pointer}.island-tool:hover,.island-search:hover{background:var(--surface-hover);border-color:var(--border-strong)}.island-tool:focus-visible,.island-search:focus-visible,.topbar-mobile-menu:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
@media(max-width:767px){.topbar :deep(.island-anchor){padding-left:52px;padding-right:12px}.topbar :deep(.dynamic-island.is-expanded){max-width:100%}}
</style>
