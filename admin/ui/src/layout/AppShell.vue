<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NSpin, useDialog } from 'naive-ui'
import SideNav from './SideNav.vue'
import TopBar from './TopBar.vue'
import MobileDrawer from './MobileDrawer.vue'
import CommandPalette from './CommandPalette.vue'
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
import { refreshShellBadges, startShellBadgePolling, stopShellBadgePolling } from '@/platform/shellBadges'
import { consumeNextHash } from '@/api/auth'
import { logout } from '@/api/session'
import { initDesktop } from '@/platform/desktop'
import { pageTitle, useI18n } from '@/i18n'
import { prefetchGroupIdle } from '@/router/pageLoaders'
import { silentRefreshing } from '@/composables/useRevisit'
import { useDelayedFlag } from '@/composables/useDelayedFlag'
import { effectiveTheme } from '@/theme/useTheme'
import { palette } from '@/theme/usePalette'
import { enterPage, replayVisuals } from '@/composables/usePageMotion'
import { useSurfaceMotion } from '@/composables/useSurfaceMotion'
import CosmicBackdrop from '@/components/ambient/CosmicBackdrop.vue'
import LayoutSkeleton from '@/components/feedback/LayoutSkeleton.vue'
import { useLayoutSkeleton } from '@/composables/useLayoutSkeleton'

/**
 * 应用外壳（03-shell §2）：侧栏 + 顶栏 + 内容区，以及三个全局浮层
 * （命令面板、移动端导航抽屉、登录设备抽屉）。
 *
 * 外壳只做三件事（03-shell §4）：会话检查、心跳、平台列表；其余数据归页面。
 */
const route = useRoute()
const surfaces = ref<HTMLElement | null>(null)
useSurfaceMotion(surfaces)
const router = useRouter()
const dialog = useDialog()
const { t, locale } = useI18n()
const layout = useLayoutSkeleton(surfaces, () => route.path, () => locale.value)
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
/**
 * 登录设备抽屉按需加载（第 5 轮首屏瘦身）：它是唯一把 NDrawer / NDrawerContent 和会话列表逻辑
 * 带进入口 chunk 的外壳组件，而绝大多数会话从不打开它。第一次打开时才下载并挂载，之后常驻
 * （保证关闭动画、再次打开不再等待）。
 */
const SessionsDrawer = defineAsyncComponent(() => import('./SessionsDrawer.vue'))
const sessionsMounted = ref(sessionsOpen.value)
watch(sessionsOpen, (open) => {
  if (open) sessionsMounted.value = true
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
  // 侧栏提示点（积分到期 warn / 哨兵批次封控 danger）60 秒刷新一次，页面不可见时跳过。
  startShellBadgePolling()
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
  stopShellBadgePolling()
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

/**
 * 路由切换（11-polish-motion §1）：新页面 opacity 0→1 + translateY(4px→0)，--dur-2；
 * 离开**不做动画**、立即移除（JS 钩子直接 done），不会两页叠加。
 * 用 Web Animations 而不是 CSS 类：KeepAlive 缓存的页面每次回来都能重新播放，且不依赖页面根节点的样式。
 * 减少动效、不支持 element.animate（jsdom）、页面根不是元素（Fragment）时直接放行。
 */
function onRouteEnter(el: Element, done: () => void): void {
  enterPage(el, done)
}

function onRouteLeave(_el: Element, done: () => void): void {
  done()
}

// Theme/palette and same-page query switches keep component state and replay their artwork.
watch([effectiveTheme, palette, () => route.fullPath], async (next, previous) => {
  // A same-page query/tab switch is an intentional replay; a route change already
  // owns its entrance animation and must not be restarted by this watcher.
  if (next[2] !== previous[2] && String(next[2]).split('?')[0] !== String(previous[2]).split('?')[0]) return
  await nextTick()
  if (surfaces.value) replayVisuals(surfaces.value)
})

/** 回到缓存页时的后台静默刷新：右上角 14px 小转圈，>300ms 才出现。 */
const showSilentSpinner = useDelayedFlag(() => silentRefreshing.value, 300)

/** 空闲时预取当前分组其余页面的 chunk（11-polish-motion §2）。 */
watch(
  () => route.name,
  (name) => {
    if (isPageKey(name)) prefetchGroupIdle(PAGES_BY_KEY[name].groupKey, name)
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
      <CosmicBackdrop />
      <TopBar @open-mobile-nav="toggleMobileNav" />
      <main ref="surfaces" class="shell-content" :data-layout-mode="layout.mode.value" :aria-busy="layout.loading.value || undefined" :style="{ minHeight: layout.reservedHeight.value }">
        <!-- KeepAlive :max=6：二次进入不出骨架；页面用 useRevisit 在回来时静默刷新 -->
        <router-view v-slot="{ Component, route: viewRoute }">
          <transition :css="false" @enter="onRouteEnter" @leave="onRouteLeave">
            <keep-alive :max="6">
              <component :is="Component" v-if="Component" :key="String(viewRoute.name ?? viewRoute.path)" />
            </keep-alive>
          </transition>
        </router-view>
        <LayoutSkeleton v-if="layout.show.value" :snapshot="layout.snapshot.value" />
      </main>
      <div
        v-if="showSilentSpinner"
        class="shell-refreshing"
        role="status"
        :aria-label="t('common.loading')"
        data-testid="shell-silent-refresh"
      >
        <n-spin :size="14" />
      </div>
    </div>

    <MobileDrawer
      v-model:open="mobileOpen"
      @open-sessions="openSessionsDrawer"
      @logout="confirmLogout"
    />
    <CommandPalette v-model:open="paletteOpen" @logout="confirmLogout" />
    <SessionsDrawer v-if="sessionsMounted" v-model:open="sessionsOpenModel" />
  </div>
</template>

<style scoped>
.shell-content :deep([data-layout-placeholder]){visibility:hidden}
.shell-content[data-layout-mode="cached"]>:not(.layout-skeleton-layer){visibility:hidden}
.shell {
  display: flex;
  align-items: stretch;
  min-height: 100vh;
  width: 100%;
  background: var(--bg);
}

/* 主列：内容区顶部 240px 叠一层极淡的品牌光晕（只在这里出现，10-visual-refresh §1.1） */
.shell-main {
  position: relative;
  isolation: isolate;
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background-image: var(--bg-glow);
  background-repeat: no-repeat;
  background-size: 100% 280px;
}

/* 内容区：1600 居中；表格页（页面根节点 .mh-page--wide）放宽到 1920 */
.shell-content {
  position: relative;
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  max-width: calc(var(--content-max) + var(--content-pad) * 2);
  margin: 0 auto;
  padding: var(--sp-6) var(--content-pad) var(--sp-12);
}

/* 回到缓存页时的静默刷新：内容区右上角小转圈，不遮挡、不占位 */
.shell-refreshing {
  position: fixed;
  top: calc(var(--topbar-h) + var(--sp-2));
  right: var(--sp-4);
  z-index: 20;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--surface-raised);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-1);
  pointer-events: none;
}

/* 表格页放宽到 1920 的规则在 styles/base.css（:has 写在 scoped 样式里会被加上作用域属性，匹配不到页面根节点） */

@media (max-width: 1280px) {
  .shell-content {
    --content-pad: 24px;
    padding-bottom: var(--sp-10);
  }
}

@media (max-width: 767px) {
  .shell-content {
    --content-pad: 16px;
    padding-top: var(--sp-4);
    padding-bottom: var(--sp-8);
  }
}
</style>
