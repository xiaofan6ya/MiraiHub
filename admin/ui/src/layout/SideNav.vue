<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NPopover } from 'naive-ui'
import { ChevronsLeft, ChevronsRight, LogOut, MonitorSmartphone, Undo2 } from 'lucide-vue-next'
import { GROUPS, PAGES_BY_KEY, groupEntry, isPageKey, type GroupDef, type PageKey } from '@/router/pages'
import { useShellBadges } from '@/platform/shellBadges'
import { useAppInfo } from '@/platform/version'
import { iconComponent } from './icons'
import { groupTitle, useI18n } from '@/i18n'
import { prefetchPage } from '@/router/pageLoaders'
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * 侧栏（03-shell §2.1 + 10-visual-refresh §1.2）：深墨蓝，两种主题都是。
 *   顶部品牌区：logo（28px 圆角方底，深底上清楚）+ MiraiHub + 版本号小字（有更新时带「有更新」）；
 *   中间 9 个分组项 36px：选中 = --side-active-bg 底 + 左侧 3px --side-accent 竖条 + 白字；
 *   底部：登录设备、切回旧版、退出登录。
 * 点击分组回到该组上次访问的页面（localStorage `mh.lastTab.<group>`）。
 * 768–1279 默认收起 64px（悬停显示 tooltip），状态存 `mh.sideCollapsed`；<768 由 MobileDrawer 复用本组件。
 *
 * 提示点：账号组 warn（积分即将到期）、概览组 danger（哨兵批次封控）。
 */
const props = withDefaults(
  defineProps<{
    collapsed?: boolean
    /** desktop = 固定侧栏；drawer = <768 的滑出抽屉（复用同一份内容） */
    variant?: 'desktop' | 'drawer'
  }>(),
  { collapsed: false, variant: 'desktop' },
)

const emit = defineEmits<{
  (event: 'toggle-collapse'): void
  (event: 'open-sessions'): void
  (event: 'logout'): void
  (event: 'navigate'): void
}>()

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { expiringAccounts, sentinelTripped } = useShellBadges()
const { version, hasUpdate, load: loadAppInfo } = useAppInfo()

const isDrawer = computed(() => props.variant === 'drawer')
const isCollapsed = computed(() => props.collapsed && !isDrawer.value)

const currentKey = computed<PageKey | null>(() => (isPageKey(route.name) ? route.name : null))
const activeGroup = computed(() => (currentKey.value ? PAGES_BY_KEY[currentKey.value].groupKey : ''))

const versionText = computed(() => (version.value ? 'v' + version.value : 'v…'))

interface GroupDot {
  tone: 'warn' | 'danger'
  tip: string
}

/** 分组提示点：概览组 = 哨兵批次封控（danger）；账号组 = 积分即将到期（warn）。 */
const dots = computed<Record<string, GroupDot | undefined>>(() => {
  const map: Record<string, GroupDot | undefined> = {}
  if (sentinelTripped.value) map.overview = { tone: 'danger', tip: t('shell.sentinelBatchBlocked') }
  if (expiringAccounts.value > 0) {
    map.accounts = { tone: 'warn', tip: t('shell.accountsExpiring', { count: expiringAccounts.value }) }
  }
  return map
})

onMounted(() => {
  void loadAppInfo()
})

function groupIcon(group: GroupDef) {
  return iconComponent(group.icon)
}

function go(group: GroupDef): void {
  const key = groupEntry(group)
  if (key !== currentKey.value) void router.push('/' + key)
  emit('navigate')
}

/**
 * 预取（11-polish-motion §2）：悬停 / 键盘聚焦到分组项时预取**该组入口页**的 chunk。重复触发只请求一次。
 *
 * 第 4 轮：
 *   · 悬停加 80ms 意图延迟 —— 鼠标从上到下扫过侧栏时不会把 9 个分组的页面全拉一遍；
 *     键盘聚焦是明确意图，立即预取；
 *   · 不再在悬停时预取整组其余页面：「空闲时预取同组其余页面」只对**当前所在分组**做（AppShell 路由变化后），
 *     原来每悬停一个分组就排一串空闲预取，扫一遍侧栏 ≈ 24 个页面 chunk + DataTable / DatePicker 公共块一起下。
 */
const HOVER_INTENT_MS = 80
let hoverTimer: ReturnType<typeof setTimeout> | null = null

function cancelHoverPrefetch(): void {
  if (hoverTimer) {
    clearTimeout(hoverTimer)
    hoverTimer = null
  }
}

function prefetchGroup(group: GroupDef): void {
  cancelHoverPrefetch()
  void prefetchPage(groupEntry(group))
}

function hoverPrefetch(group: GroupDef): void {
  cancelHoverPrefetch()
  hoverTimer = setTimeout(() => {
    hoverTimer = null
    void prefetchPage(groupEntry(group))
  }, HOVER_INTENT_MS)
}

onBeforeUnmount(cancelHoverPrefetch)

function goMaintenance(): void {
  void router.push('/maintenance')
  emit('navigate')
}
</script>

<template>
  <aside class="side" :class="{ collapsed: isCollapsed, drawer: isDrawer }">
    <div class="side-brand">
      <span class="brand-mark" aria-hidden="true">
        <svg class="brand-logo" viewBox="0 0 40 40" width="40" height="40" role="img" aria-label="MiraiHub">
          <defs>
            <linearGradient id="mh-shell-logo" x1="4" y1="4" x2="36" y2="36" gradientUnits="userSpaceOnUse">
              <stop offset="0" stop-color="#e6eef5" />
              <stop offset=".52" stop-color="#b5c5d4" />
              <stop offset="1" stop-color="#88a6bb" />
            </linearGradient>
          </defs>
          <rect x="1" y="1" width="38" height="38" rx="12" fill="#1b2936" />
          <path d="M10.5 28V12.5l9.5 9 9.5-9V28" fill="none" stroke="url(#mh-shell-logo)" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" />
          <circle cx="30.4" cy="10.2" r="2" fill="#88a6bb" />
        </svg>
      </span>
      <div v-if="!isCollapsed" class="brand-text">
        <div class="brand-title">MiraiHub</div>
        <TitleTip :content="hasUpdate ? t('shell.hasUpdateTip') : t('shell.updates')">
          <button type="button" class="brand-sub" @click="goMaintenance">
            <span class="ver">{{ versionText }}</span>
            <span v-if="hasUpdate" class="has-update">{{ t('shell.hasUpdate') }}</span>
          </button>
        </TitleTip>
      </div>
      <button
        v-if="isDrawer"
        type="button"
        class="collapse-btn drawer-close"
        :aria-label="t('shell.closeNav')"
        @click="emit('navigate')"
      >
        <ChevronsLeft :size="16" />
      </button>
      <TitleTip v-else :content="isCollapsed ? t('shell.expandSide') : t('shell.collapseSide')">
        <button
          type="button"
          class="collapse-btn"
          :aria-label="isCollapsed ? t('shell.expandSide') : t('shell.collapseSide')"
          @click="emit('toggle-collapse')"
        >
          <ChevronsRight v-if="isCollapsed" :size="16" />
          <ChevronsLeft v-else :size="16" />
        </button>
      </TitleTip>
    </div>

    <nav class="side-nav" :aria-label="t('shell.mainNav')">
      <n-popover
        v-for="group in GROUPS"
        :key="group.key"
        placement="right"
        :delay="200"
        :disabled="!isCollapsed && !dots[group.key]"
      >
        <template #trigger>
          <button
            type="button"
            class="side-item"
            :class="{ active: group.key === activeGroup, iconOnly: isCollapsed }"
            :aria-current="group.key === activeGroup ? 'page' : undefined"
            :aria-label="isCollapsed ? groupTitle(group.key) : undefined"
            @click="go(group)"
            @mouseenter="hoverPrefetch(group)"
            @mouseleave="cancelHoverPrefetch"
            @focus="prefetchGroup(group)"
          >
            <component :is="groupIcon(group)" :size="18" class="item-icon" />
            <span v-if="!isCollapsed" class="item-label">{{ groupTitle(group.key) }}</span>
            <span
              v-if="dots[group.key]"
              class="item-dot"
              :class="[dots[group.key]?.tone, { floating: isCollapsed }]"
              role="img"
              :aria-label="dots[group.key]?.tip"
            />
          </button>
        </template>
        <div class="mh-help__content">
          <span class="tip-row">{{ groupTitle(group.key) }}</span>
          <span v-if="dots[group.key]" class="tip-sub">{{ dots[group.key]?.tip }}</span>
        </div>
      </n-popover>
    </nav>

    <footer class="side-foot">
      <n-popover placement="right" :delay="200" :disabled="!isCollapsed">
        <template #trigger>
          <button type="button" class="foot-item" @click="emit('open-sessions')">
            <MonitorSmartphone :size="16" />
            <span v-if="!isCollapsed">{{ t('shell.sessions') }}</span>
            <span v-else class="mh-sr-only">{{ t('shell.sessions') }}</span>
          </button>
        </template>
        <div class="mh-help__content">{{ t('shell.sessions') }}</div>
      </n-popover>
      <n-popover placement="right" :delay="200">
        <template #trigger>
          <a class="foot-item foot-link" href="/admin?ui=v1">
            <Undo2 :size="16" />
            <span v-if="!isCollapsed">{{ t('shell.legacyConsole') }}</span>
            <span v-else class="mh-sr-only">{{ t('shell.legacyConsole') }}</span>
          </a>
        </template>
        <div class="mh-help__content">{{ t('shell.legacyConsoleTip') }}</div>
      </n-popover>
      <n-popover placement="right" :delay="200" :disabled="!isCollapsed">
        <template #trigger>
          <button type="button" class="foot-item" @click="emit('logout')">
            <LogOut :size="16" />
            <span v-if="!isCollapsed">{{ t('shell.logout') }}</span>
            <span v-else class="mh-sr-only">{{ t('shell.logout') }}</span>
          </button>
        </template>
        <div class="mh-help__content">{{ t('shell.logout') }}</div>
      </n-popover>
      <TitleTip v-if="isCollapsed" :content="hasUpdate ? t('shell.hasUpdateTip') : t('shell.updates')">
        <button type="button" class="foot-version" @click="goMaintenance">
          <span class="ver">{{ versionText }}</span>
          <span v-if="hasUpdate" class="update-dot" aria-hidden="true" />
        </button>
      </TitleTip>
    </footer>
  </aside>
</template>

<style scoped>
.side {
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
  width: var(--side-w);
  height: 100vh;
  height: 100dvh;
  position: sticky;
  top: 0;
  background: var(--side-bg);
  color: var(--side-fg);
  border-right: 1px solid var(--side-border);
  overflow: hidden;
  transition: width var(--t-base) var(--ease);
  z-index: 30;
  color-scheme: dark;
}

.side.collapsed {
  width: var(--side-w-collapsed);
}

.side.drawer {
  width: 100%;
  height: 100%;
  position: static;
  border-right: 0;
}

/* ---------- 品牌区 ---------- */
.side-brand {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  padding: 0 var(--sp-3) 0 var(--sp-4);
  flex: 0 0 auto;
  border-bottom: 1px solid var(--side-border);
}

.side.collapsed .side-brand {
  justify-content: center;
  padding: 0;
}

/* logo 在深色上要清楚：28px 圆角方底 + 1px 亮边（第 1 轮审查 P1-9） */
.brand-mark {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--r-ctl);
  background: var(--grad-brand);
  box-shadow:
    0 0 0 1px rgba(255, 255, 255, .16),
    0 2px 6px rgba(0, 0, 0, .35);
  overflow: hidden;
}

.brand-logo {
  width: 28px;
  height: 28px;
  display: block;
  object-fit: cover;
}

.brand-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.brand-title {
  font-size: var(--fs-h2);
  font-weight: 600;
  line-height: 20px;
  color: var(--side-fg);
}

.brand-sub {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--side-fg-muted);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  text-align: left;
}

.brand-sub:hover {
  color: var(--side-fg);
}

.brand-sub .has-update {
  padding: 0 5px;
  border-radius: var(--r-chip);
  background: var(--side-active-bg);
  color: var(--side-fg);
}

.collapse-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-left: auto;
  padding: 0;
  border: 0;
  border-radius: var(--r-chip);
  background: transparent;
  color: var(--side-fg-muted);
  cursor: pointer;
}

.collapse-btn:hover {
  background: var(--side-hover-bg);
  color: var(--side-fg);
}

.side.collapsed .collapse-btn {
  position: absolute;
  right: 2px;
  top: 2px;
  width: 20px;
  height: 20px;
  margin: 0;
  opacity: 0;
}

.side.collapsed:hover .collapse-btn,
.side.collapsed .collapse-btn:focus-visible {
  opacity: 1;
}

.side.drawer .collapse-btn {
  position: static;
  margin-left: auto;
  opacity: 1;
}

/* ---------- 导航 ---------- */
.side-nav {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: var(--sp-3) var(--sp-2);
  display: flex;
  flex-direction: column;
  gap: 2px;
  scrollbar-color: rgba(255, 255, 255, .14) transparent;
}

.side-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 36px;
  width: 100%;
  padding: 0 var(--sp-3);
  border: 0;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--side-fg-muted);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition:
    background var(--t-fast) var(--ease),
    color var(--t-fast) var(--ease),
    transform var(--dur-1) var(--ease-out);
}

.side-item.iconOnly {
  justify-content: center;
  padding: 0;
}

.side-item:hover {
  background: var(--side-hover-bg);
  color: var(--side-fg);
}

.side-item.active {
  background: var(--side-active-bg);
  color: #fff;
  font-weight: 600;
}

/* 第 4 轮：选中项图标用竖条同色，扫一眼就能定位；按压 scale(.98)（减少动效时不缩放） */
.side-item.active .item-icon {
  color: var(--side-accent);
}

.side-item:active,
.foot-item:active {
  transform: scale(.98);
}

@media (prefers-reduced-motion: reduce) {
  .side-item:active,
  .foot-item:active {
    transform: none;
  }
}

/* 选中项左侧 3px 竖条 */
.side-item.active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--side-accent);
}

.side-item:focus-visible {
  outline: 2px solid var(--side-accent);
  outline-offset: -2px;
}

.item-icon {
  flex: 0 0 auto;
}

.item-label {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-dot {
  flex: 0 0 auto;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-left: auto;
  box-shadow: 0 0 0 2px var(--side-bg);
}

.item-dot.warn {
  background: var(--warn);
}

.item-dot.danger {
  background: var(--danger);
}

.side.collapsed .item-dot.floating {
  position: absolute;
  top: 6px;
  right: 12px;
  margin: 0;
}

/* ---------- 底部 ---------- */
.side-foot {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--sp-2);
  border-top: 1px solid var(--side-border);
}

.foot-item,
.foot-version {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 32px;
  width: 100%;
  padding: 0 var(--sp-3);
  border: 0;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--side-fg-muted);
  font-size: var(--fs-body);
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.foot-item:hover,
.foot-version:hover {
  background: var(--side-hover-bg);
  color: var(--side-fg);
  text-decoration: none;
}

.foot-item:focus-visible,
.foot-version:focus-visible {
  outline: 2px solid var(--side-accent);
  outline-offset: -2px;
}

.side.collapsed .foot-item,
.side.collapsed .foot-version {
  justify-content: center;
  padding: 0;
}

.foot-version {
  position: relative;
  font-size: var(--fs-tiny);
  font-variant-numeric: tabular-nums;
}

.foot-version .ver {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.update-dot {
  position: absolute;
  top: 6px;
  right: 10px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--side-accent);
}

.tip-row {
  display: block;
  font-size: var(--fs-small);
}

.tip-sub {
  display: block;
  margin-top: 2px;
  font-size: var(--fs-tiny);
  opacity: .8;
}

@media (max-width: 767px) {
  .side {
    display: none;
  }
  .side.drawer {
    display: flex;
  }
}
</style>
