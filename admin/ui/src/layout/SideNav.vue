<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NTooltip } from 'naive-ui'
import { ChevronsLeft, ChevronsRight, LogOut, MonitorSmartphone, Undo2 } from 'lucide-vue-next'
import { GROUPS, PAGES_BY_KEY, groupEntry, isPageKey, type GroupDef, type PageKey } from '@/router/pages'
import { useShellBadges } from '@/platform/shellBadges'
import { useAppInfo } from '@/platform/version'
import { iconComponent } from './icons'
import { groupTitle, useI18n } from '@/i18n'

/**
 * 侧栏（03-shell §2.1）：宽 224px，9 个分组项 36px；
 * 点击分组回到该组上次访问的页面（localStorage `mh.lastTab.<group>`）。
 * 1024–1279 默认收起 64px，状态存 `mh.sideCollapsed`。
 */
const props = withDefaults(
  defineProps<{
    collapsed?: boolean
    /** desktop = 固定侧栏；drawer = ≤1023 的滑出抽屉（复用同一份内容） */
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
const { expiringAccounts } = useShellBadges()
const { version, hasUpdate, load: loadAppInfo } = useAppInfo()

const isDrawer = computed(() => props.variant === 'drawer')
const isCollapsed = computed(() => props.collapsed && !isDrawer.value)

const currentKey = computed<PageKey | null>(() => (isPageKey(route.name) ? route.name : null))
const activeGroup = computed(() => (currentKey.value ? PAGES_BY_KEY[currentKey.value].groupKey : ''))

const versionText = computed(() => (version.value ? 'v' + version.value : 'v…'))

/** 账号组的提示点：有积分即将到期的账号时亮起（旧版 refreshShellBadges 的用途）。 */
const accountsDot = computed(() => expiringAccounts.value > 0)
const accountsDotTip = computed(() =>
  t('shell.accountsExpiring', { count: expiringAccounts.value }),
)

/** 品牌图标由后端提供：写成变量而不是字面量 src，避免被 Vite 当静态资源解析。 */
const appIconUrl = '/api/app/icon'

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

function goMaintenance(): void {
  void router.push('/maintenance')
  emit('navigate')
}
</script>

<template>
  <aside class="side" :class="{ collapsed: isCollapsed, drawer: isDrawer }">
    <div class="side-brand">
      <img class="brand-logo" :src="appIconUrl" alt="MiraiHub" width="28" height="28" />
      <div v-if="!isCollapsed" class="brand-text">
        <div class="brand-title">MiraiHub</div>
        <div class="brand-sub">{{ t('shell.brandSub') }}</div>
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
      <button
        v-else
        type="button"
        class="collapse-btn"
        :aria-label="isCollapsed ? t('shell.expandSide') : t('shell.collapseSide')"
        :title="isCollapsed ? t('shell.expandSide') : t('shell.collapseSide')"
        @click="emit('toggle-collapse')"
      >
        <ChevronsRight v-if="isCollapsed" :size="16" />
        <ChevronsLeft v-else :size="16" />
      </button>
    </div>

    <nav class="side-nav" :aria-label="t('shell.mainNav')">
      <n-tooltip
        v-for="group in GROUPS"
        :key="group.key"
        placement="right"
        :delay="200"
        :disabled="!isCollapsed"
      >
        <template #trigger>
          <button
            type="button"
            class="side-item"
            :class="{ active: group.key === activeGroup, iconOnly: isCollapsed }"
            :aria-current="group.key === activeGroup ? 'page' : undefined"
            @click="go(group)"
          >
            <component :is="groupIcon(group)" :size="18" class="item-icon" />
            <span v-if="!isCollapsed" class="item-label">{{ groupTitle(group.key) }}</span>
            <span
              v-if="group.key === 'accounts' && accountsDot"
              class="item-dot warn"
              :class="{ floating: isCollapsed }"
              :title="accountsDotTip"
              :aria-label="accountsDotTip"
            />
          </button>
        </template>
        <span class="tip-row">{{ groupTitle(group.key) }}</span>
        <span v-if="group.key === 'accounts' && accountsDot" class="tip-sub">
          {{ accountsDotTip }}
        </span>
      </n-tooltip>
    </nav>

    <footer class="side-foot">
      <button type="button" class="foot-item" @click="emit('open-sessions')">
        <MonitorSmartphone :size="16" />
        <span v-if="!isCollapsed">{{ t('shell.sessions') }}</span>
        <span v-else class="sr-only">{{ t('shell.sessions') }}</span>
      </button>
      <a class="foot-item foot-link" href="/admin?ui=v1" :title="t('shell.legacyConsoleTip')">
        <Undo2 :size="16" />
        <span v-if="!isCollapsed">{{ t('shell.legacyConsole') }}</span>
        <span v-else class="sr-only">{{ t('shell.legacyConsole') }}</span>
      </a>
      <button
        type="button"
        class="foot-version"
        :title="hasUpdate ? t('shell.hasUpdateTip') : t('shell.updates')"
        @click="goMaintenance"
      >
        <span class="ver">{{ versionText }}</span>
        <span v-if="hasUpdate" class="has-update">{{ t('shell.hasUpdate') }}</span>
      </button>
      <button type="button" class="foot-item" @click="emit('logout')">
        <LogOut :size="16" />
        <span v-if="!isCollapsed">{{ t('shell.logout') }}</span>
        <span v-else class="sr-only">{{ t('shell.logout') }}</span>
      </button>
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
  position: sticky;
  top: 0;
  background: var(--surface);
  border-right: 1px solid var(--border);
  overflow: hidden;
  transition: width var(--t-base) var(--ease);
  z-index: 30;
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

.side-brand {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  height: 64px;
  padding: 0 var(--sp-3);
  flex: 0 0 auto;
}

.side.collapsed .side-brand {
  justify-content: center;
  padding: 0;
}

.brand-logo {
  width: 28px;
  height: 28px;
  border-radius: var(--r-ctl);
  flex: 0 0 auto;
}

.brand-text {
  min-width: 0;
}

.brand-title {
  font-size: var(--fs-h2);
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -0.01em;
  color: var(--fg);
}

.brand-sub {
  font-size: var(--fs-small);
  line-height: 1.3;
  color: var(--fg-muted);
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
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg-faint);
  cursor: pointer;
}

.collapse-btn:hover {
  background: var(--surface-hover);
  color: var(--fg);
}

.side.collapsed .collapse-btn {
  position: absolute;
  right: 4px;
  top: 4px;
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

.side-nav {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: var(--sp-2) var(--sp-2) var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: 2px;
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
  color: var(--fg-2);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  text-align: left;
  cursor: pointer;
  transition:
    background var(--t-fast) var(--ease),
    color var(--t-fast) var(--ease);
}

.side-item.iconOnly {
  justify-content: center;
  padding: 0;
}

.side-item:hover {
  background: var(--surface-hover);
  color: var(--fg);
}

.side-item.active {
  background: var(--brand-soft);
  color: var(--brand);
  font-weight: 600;
}

.side-item:focus-visible {
  outline: 2px solid var(--brand);
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
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-left: auto;
}

.item-dot.warn {
  background: var(--warn);
}

.side.collapsed .item-dot.floating {
  position: absolute;
  top: 7px;
  right: 9px;
  margin: 0;
  width: 5px;
  height: 5px;
}

.side-foot {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--sp-2);
  border-top: 1px solid var(--border-soft);
}

.foot-item,
.foot-link,
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
  color: var(--fg-2);
  font-size: var(--fs-body);
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.foot-item:hover,
.foot-link:hover,
.foot-version:hover {
  background: var(--surface-hover);
  color: var(--fg);
}

.side.collapsed .foot-item,
.side.collapsed .foot-link,
.side.collapsed .foot-version {
  justify-content: center;
  padding: 0;
}

.foot-version {
  justify-content: flex-start;
  color: var(--fg-muted);
  font-size: var(--fs-small);
}

.foot-version .ver {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.foot-version .has-update {
  margin-left: auto;
  color: var(--brand);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
}

.side.collapsed .has-update {
  display: none;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.tip-row {
  display: block;
  font-size: var(--fs-small);
  color: var(--fg);
}

.tip-sub {
  display: block;
  margin-top: 2px;
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
}

@media (max-width: 1023px) {
  .side {
    display: none;
  }
  .side.drawer {
    display: flex;
  }
}
</style>