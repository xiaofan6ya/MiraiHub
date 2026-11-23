<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDialog } from 'naive-ui'
import SideNav from './SideNav.vue'
import TopBar from './TopBar.vue'
import MobileDrawer from './MobileDrawer.vue'
import CommandPalette from './CommandPalette.vue'
import SessionsDrawer from './SessionsDrawer.vue'
import {
  closeMobileNav,
  commandPaletteOpen,
  initShellViewport,
  isMobile,
  mobileNavOpen,
  openSessionsDrawer,
  sessionsOpen,
  sideCollapsed,
  toggleMobileNav,
  toggleSideCollapsed,
} from './shellState'
import { PAGES_BY_KEY, isPageKey, rememberLastTab } from '@/router/pages'
import { usePlatformStore } from '@/stores/platform'
import { useHeartbeatStore } from '@/stores/heartbeat'
import { useAppInfo } from '@/platform/version'
import { refreshShellBadges } from '@/platform/shellBadges'
import { consumeNextHash } from '@/api/auth'
import { logout } from '@/api/session'
import { initDesktop } from '@/platform/desktop'
import { pageTitle, useI18n } from '@/i18n'

/**
 * 应用外壳（03-shell §2）：侧栏 + 顶栏 + 内容区，以及三个全局浮层
 * （命令面板、移动端导航抽屉、登录设备抽屉）。
 *
 * 外壳只做三件事（03-shell §4）：会话检查、心跳、平台列表；其余数据归页面。
 */
const route = useRoute()
const router = useRouter()
const dialog = useDialog()
const { t, locale } = useI18n()
const platformStore = usePlatformStore()
const heartbeat = useHeartbeatStore()
const { load: loadAppInfo } = useAppInfo()

const mobileOpen = computed({
  get: () => mobileNavOpen.value,
  set: (value: boolean) => {
    mobileNavOpen.value = value
  },
})
const paletteOpen = computed({
  get: () => commandPaletteOpen.value,
  set: (value: boolean) => {
    commandPaletteOpen.value = value
  },
})
const sessionsOpenModel = computed({
  get: () => sessionsOpen.value,
  set: (value: boolean) => {
    sessionsOpen.value = value
  },
})

let offPlatformChange: (() => void) | null = null

/**
 * 版本号 / 是否有更新是顶栏的"锦上添花"，不是首屏内容。
 *
 * 为什么必须推迟：/api/app/releases 在生产返回 100KB+ 的整包发布清单（后端未压缩），
 * 首屏就和 /api/overview 抢同一条移动网络 —— 实测它在 0.6s 发起，把概览接口的
 * 响应挤到 2s 之后，LCP 被拖慢约 0.8s。这里等 load 之后**再安静 2 秒**才发，
 * 用户一有交互就立刻发，保证徽标不会一直缺。
 */
function scheduleAppInfo(): void {
  const run = (): void => {
    void loadAppInfo()
  }
  const idle = (): void => {
    const requestIdle = (
      window as Window & {
        requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
      }
    ).requestIdleCallback
    if (typeof requestIdle === 'function') requestIdle(run, { timeout: 2000 })
    else globalThis.setTimeout(run, 800)
  }
  /** 首屏安静期：load 之后再等 2s，避开 LCP 窗口。 */
  const quiet = (): void => {
    globalThis.setTimeout(idle, 2000)
  }
  if (document.readyState === 'complete') quiet()
  else window.addEventListener('load', quiet, { once: true })
  // 首屏后用户一旦操作，说明已经可用：立刻补齐版本/更新信息。
  window.addEventListener('pointerdown', run, { once: true, passive: true })
  window.addEventListener('keydown', run, { once: true })
}

/** 平台专属页面（成长计划）当前平台不支持时，跳回账号池（旧版 activateTab 的行为）。 */
function guardPlatformOnlyPage(): void {
  const name = route.name
  if (!isPageKey(name)) return
  if (platformStore.supports(name)) return
  const fallback = platformStore.fallbackTab(name)
  if (fallback && '/' + fallback !== route.path) void router.replace('/' + fallback)
}

onMounted(async () => {
  initShellViewport()
  // 心跳第一拍同时是「会话检查」：401 由 http 层统一回 /admin（带 hash 暂存）。
  heartbeat.start()
  scheduleAppInfo()
  void initDesktop()
  await platformStore.load()
  void refreshShellBadges()
  offPlatformChange = platformStore.onChange(() => {
    void refreshShellBadges()
    guardPlatformOnlyPage()
  })
  // 401 跳回登录页时暂存的 hash：登录成功后回到原页面。
  const nextHash = consumeNextHash()
  if (nextHash) {
    const target = nextHash.startsWith('#') ? nextHash.slice(1) : nextHash
    if (target && target !== route.fullPath) void router.replace(target)
  }
  guardPlatformOnlyPage()
})

onBeforeUnmount(() => {
  heartbeat.stop()
  offPlatformChange?.()
  offPlatformChange = null
})

watch(
  [() => route.fullPath, () => locale.value],
  () => {
    closeMobileNav()
    const name = route.name
    if (isPageKey(name)) {
      rememberLastTab(PAGES_BY_KEY[name].groupKey, name)
      document.title = t('app.documentTitle', { title: pageTitle(name) })
      return
    }
    const title = route.meta.title
    document.title = t('app.documentTitle', { title: title ? String(title) : t('shell.brandSub') })
  },
  { immediate: true },
)

/** 退出登录：确认框标题是问句，动作与按钮同词（01-direction §8）。 */
function confirmLogout(): void {
  dialog.warning({
    title: t('shell.logoutTitle'),
    content: t('shell.logoutBody'),
    positiveText: t('shell.logoutConfirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => {
      void logout()
    },
  })
}
</script>

<template>
  <div class="shell">
    <SideNav
      v-if="!isMobile"
      :collapsed="sideCollapsed"
      @toggle-collapse="toggleSideCollapsed"
      @open-sessions="openSessionsDrawer"
      @logout="confirmLogout"
    />

    <div class="shell-main">
      <TopBar @open-mobile-nav="toggleMobileNav" />
      <main class="shell-content">
        <router-view />
      </main>
    </div>

    <MobileDrawer
      v-model:open="mobileOpen"
      @open-sessions="openSessionsDrawer"
      @logout="confirmLogout"
    />
    <CommandPalette v-model:open="paletteOpen" @logout="confirmLogout" />
    <SessionsDrawer v-model:open="sessionsOpenModel" />
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  align-items: stretch;
  min-height: 100vh;
  width: 100%;
  background: var(--bg);
}

.shell-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.shell-content {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  max-width: var(--content-max);
  margin-right: auto;
  padding: var(--sp-6) var(--sp-8) var(--sp-12);
}

@media (max-width: 1279px) {
  .shell-content {
    padding: var(--sp-6) var(--sp-6) var(--sp-10);
  }
}

@media (max-width: 640px) {
  .shell-content {
    padding: var(--sp-4) var(--sp-4) var(--sp-8);
  }
}
</style>