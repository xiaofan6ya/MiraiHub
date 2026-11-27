<script setup lang="ts">
import { useIslandMessage as useMessage } from '@/components/feedback/islandQueue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { CornerDownLeft, Search } from 'lucide-vue-next'
import { PAGES } from '@/router/pages'
import { usePlatformStore } from '@/stores/platform'
import type { PlatformSpec } from '@/api/platforms'
import { SHELL_SHORTCUTS, useCommands, waitForCommand } from './commandRegistry'
import { apiBaseUrl, copyText } from '@/platform/clipboard'
import { useTheme } from '@/theme/useTheme'
import { groupTitle, pageTitle, useI18n, type MessageKey } from '@/i18n'

/**
 * 命令面板（03-shell §2.4）：Ctrl/⌘+K 打开，居中 560px 浮层；
 * 结果分「页面」（24 个）与「操作」两组，↑↓ 选择、回车执行、Esc 关闭。
 *
 * 页面级操作（添加账号 / 创建 Key …）通过 `registerCommand()` 注册，
 * 见 src/layout/commandRegistry.ts。
 */
type RunResult = void | 'stay'

interface PaletteItem {
  id: string
  title: string
  sub?: string
  keywords?: string
  run: () => RunResult | Promise<RunResult>
}

interface PaletteSection {
  name: string
  items: PaletteItem[]
}

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (event: 'update:open', value: boolean): void
  (event: 'logout'): void
}>()

const router = useRouter()
const message = useMessage()
const { commands } = useCommands()
const { cycle, mode: themeMode } = useTheme()
const { t } = useI18n()
const platformStore = usePlatformStore()
const platforms = computed<PlatformSpec[]>(() => platformStore.platforms)
const currentId = computed(() => platformStore.currentId)
const currentSpec = computed(() => platformStore.currentSpec)

const view = ref<'root' | 'platforms'>('root')
const query = ref('')
const activeIndex = ref(0)
const inputRef = ref<HTMLInputElement | null>(null)

const themeLabel = computed(() =>
  themeMode.value === 'light'
    ? t('shell.themeLight')
    : themeMode.value === 'dark'
      ? t('shell.themeDark')
      : t('shell.themeSystem'),
)

const platformSubtitle = computed(
  () => currentSpec.value?.label || currentId.value || t('cmdk.notSelected'),
)

function close(): void {
  emit('update:open', false)
}

function backToRoot(): RunResult {
  view.value = 'root'
  query.value = ''
  return 'stay'
}

const platformItems = computed<PaletteItem[]>(() => {
  const items: PaletteItem[] = platforms.value.map((platform) => ({
    id: 'platform.' + platform.id,
    title: platform.label,
    sub:
      platform.realm_label ||
      (platform.realm === 'cn' ? t('cmdk.realmCn') : platform.realm === 'intl' ? t('cmdk.realmIntl') : ''),
    keywords: platform.id + ' ' + platform.storage_platform,
    run: () => {
      platformStore.setCurrent(platform.id, platform.realm || '')
    },
  }))
  return [{ id: 'platform.back', title: t('cmdk.back'), sub: t('cmdk.backSub'), run: backToRoot }, ...items]
})

const operationItems = computed<PaletteItem[]>(() => {
  if (view.value === 'platforms') return platformItems.value
  const items: PaletteItem[] = [
    {
      id: 'op.copy-api',
      title: t('cmdk.copyApi'),
      sub: apiBaseUrl(),
      run: async () => {
        const url = apiBaseUrl()
        const ok = await copyText(url)
        if (ok) message.success(t('common.copied') + ' ' + url)
        else message.error(t('common.copyFailed'))
      },
    },
    {
      id: 'op.theme',
      title: t('cmdk.switchTheme'),
      sub: t('cmdk.currentTheme', { theme: themeLabel.value }),
      keywords: t('cmdk.keywordsTheme'),
      run: () => {
        cycle()
      },
    },
    {
      id: 'op.platform',
      title: t('cmdk.switchPlatform'),
      sub: platformSubtitle.value,
      keywords: t('cmdk.keywordsPlatform'),
      run: () => {
        view.value = 'platforms'
        query.value = ''
        return 'stay'
      },
    },
  ]
  // 全局快捷操作：页面已注册同 id 时用页面那条（下面的循环），否则跳页面后再执行。
  const registered = new Set(commands.value.map((command) => command.id))
  for (const shortcut of SHELL_SHORTCUTS) {
    if (registered.has(shortcut.id)) continue
    items.push({
      id: 'cmd.' + shortcut.id,
      title: t(shortcut.titleKey as MessageKey),
      sub: t(shortcut.hintKey as MessageKey),
      keywords: t(shortcut.keywordsKey as MessageKey),
      run: async () => {
        // 先关面板再跳转：等页面挂载、注册命令期间面板不该挡在新页面上。
        close()
        await router.push(shortcut.path)
        const command = await waitForCommand(shortcut.id)
        if (command) await command.run()
        else message.warning(t('cmdk.shortcutUnavailable'))
      },
    })
  }
  for (const command of commands.value) {
    items.push({
      id: 'cmd.' + command.id,
      title: command.title,
      sub: command.hint,
      keywords: command.keywords,
      run: command.run,
    })
  }
  items.push({
    id: 'op.logout',
    title: t('cmdk.logout'),
    keywords: t('cmdk.keywordsLogout'),
    run: () => {
      emit('logout')
    },
  })
  return items
})

const pageItems = computed<PaletteItem[]>(() =>
  PAGES.map((page) => ({
    id: 'page.' + page.key,
    title: pageTitle(page.key),
    sub: groupTitle(page.groupKey),
    keywords: page.key,
    run: () => {
      void router.push('/' + page.key)
    },
  })),
)

function matched(items: PaletteItem[], keyword: string): PaletteItem[] {
  const needle = keyword.trim().toLowerCase()
  if (!needle) return items
  return items.filter((item) =>
    [item.title, item.sub, item.keywords].filter(Boolean).join(' ').toLowerCase().includes(needle),
  )
}

const sections = computed<PaletteSection[]>(() => {
  if (view.value === 'platforms') {
    const items = matched(operationItems.value, query.value)
    return items.length ? [{ name: t('cmdk.platformSection'), items }] : []
  }
  const pages = matched(pageItems.value, query.value)
  const ops = matched(operationItems.value, query.value)
  const result: PaletteSection[] = []
  if (pages.length) result.push({ name: t('cmdk.sectionPages'), items: pages })
  if (ops.length) result.push({ name: t('cmdk.sectionOps'), items: ops })
  return result
})

const flatItems = computed<PaletteItem[]>(() => sections.value.flatMap((section) => section.items))
const activeItem = computed<PaletteItem | null>(() => flatItems.value[activeIndex.value] ?? null)

watch([query, view], () => {
  activeIndex.value = 0
})

watch(
  () => props.open,
  (open) => {
    if (open) {
      view.value = 'root'
      query.value = ''
      activeIndex.value = 0
      void nextTick(() => {
        inputRef.value?.focus()
        scrollActiveIntoView()
      })
    } else {
      inputRef.value?.blur()
    }
  },
)

watch(activeItem, () => {
  void nextTick(scrollActiveIntoView)
})

function scrollActiveIntoView(): void {
  const el = document.querySelector('.cmdk-panel .cmdk-item.active')
  // jsdom（单元测试）没有 scrollIntoView，真实浏览器一定有。
  if (el instanceof HTMLElement && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'nearest' })
  }
}

function focusItem(item: PaletteItem): void {
  const index = flatItems.value.indexOf(item)
  if (index >= 0) activeIndex.value = index
}

function move(step: number): void {
  const total = flatItems.value.length
  if (!total) return
  activeIndex.value = (activeIndex.value + step + total) % total
}

async function runItem(item: PaletteItem | null): Promise<void> {
  if (!item) return
  try {
    const result = await item.run()
    if (result !== 'stay') close()
  } catch (error) {
    console.warn('[cmdk] 命令执行失败', error)
    message.error(
      t('cmdk.operationFailed', { message: error instanceof Error ? error.message : String(error) }),
    )
  }
}

function isActive(id: string): boolean {
  return activeItem.value?.id === id
}

function onGlobalKeydown(event: KeyboardEvent): void {
  const key = event.key.toLowerCase()
  const withModifier = event.ctrlKey || event.metaKey
  if (withModifier && key === 'k') {
    event.preventDefault()
    emit('update:open', !props.open)
    return
  }
  if (!props.open) return
  if (event.isComposing) return
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    move(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    move(-1)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    void runItem(activeItem.value)
  }
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
})
</script>

<template>
  <teleport to="body">
    <transition name="cmdk-fade">
      <div v-if="open" class="cmdk-overlay" @click.self="close">
        <div class="cmdk-panel" role="dialog" aria-modal="true" :aria-label="t('cmdk.title')">
          <div class="cmdk-head">
            <Search :size="16" class="cmdk-search-icon" />
            <input
              ref="inputRef"
              v-model="query"
              class="cmdk-input"
              type="text"
              autocomplete="off"
              spellcheck="false"
              :placeholder="view === 'platforms' ? t('cmdk.searchPlatforms') : t('cmdk.searchPagesOrOps')"
              :aria-label="t('cmdk.searchPagesOrOps')"
            />
            <kbd class="cmdk-kbd">Esc</kbd>
          </div>

          <div class="cmdk-body" role="listbox" :aria-label="t('cmdk.results')">
            <template v-for="section in sections" :key="section.name">
              <div class="cmdk-section">{{ section.name }}</div>
              <button
                v-for="item in section.items"
                :key="item.id"
                type="button"
                role="option"
                class="cmdk-item"
                :class="{ active: isActive(item.id) }"
                :aria-selected="isActive(item.id) ? 'true' : 'false'"
                @mousemove="focusItem(item)"
                @click="runItem(item)"
              >
                <span class="cmdk-item-title">{{ item.title }}</span>
                <span v-if="item.sub" class="cmdk-item-sub">{{ item.sub }}</span>
                <CornerDownLeft v-if="isActive(item.id)" :size="14" class="cmdk-item-enter" />
              </button>
            </template>
            <p v-if="!flatItems.length" class="cmdk-empty">{{ t('cmdk.noMatches') }}</p>
          </div>

          <footer class="cmdk-foot">
            <span>{{ t('cmdk.selectHint') }}</span>
            <span>{{ t('cmdk.runHint') }}</span>
            <span>{{ t('cmdk.closeHint') }}</span>
          </footer>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.cmdk-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 12vh var(--sp-4) var(--sp-4);
  background: var(--scrim);
}

.cmdk-panel {
  width: 560px;
  max-width: 100%;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
}

.cmdk-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: 0 var(--sp-3);
  height: 52px;
  border-bottom: 1px solid var(--border-soft);
  flex: 0 0 auto;
}

.cmdk-search-icon {
  color: var(--fg-muted);
  flex: 0 0 auto;
}

.cmdk-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--fg);
  font-size: var(--fs-body);
}

.cmdk-input::placeholder {
  color: var(--fg-faint);
}

.cmdk-kbd {
  flex: 0 0 auto;
  padding: 1px 5px;
  border: 1px solid var(--border);
  border-radius: var(--r-chip);
  color: var(--fg-2);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
}

.cmdk-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: var(--sp-1) 0 var(--sp-2);
}

.cmdk-section {
  padding: var(--sp-2) var(--sp-3) var(--sp-1);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
}

.cmdk-item {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: calc(100% - var(--sp-4));
  margin: 0 var(--sp-2);
  min-height: 40px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--fg);
  text-align: left;
  cursor: pointer;
}

.cmdk-item.active {
  background: var(--brand-soft);
}

.cmdk-item-title {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cmdk-item.active .cmdk-item-title {
  color: var(--brand);
  font-weight: 600;
}

.cmdk-item-sub {
  margin-left: auto;
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
}

.cmdk-item-enter {
  flex: 0 0 auto;
  color: var(--brand);
}

.cmdk-empty {
  margin: 0;
  padding: var(--sp-6) var(--sp-3);
  text-align: center;
  color: var(--fg-muted);
  font-size: var(--fs-small);
}

.cmdk-foot {
  display: flex;
  gap: var(--sp-4);
  padding: var(--sp-2) var(--sp-3);
  border-top: 1px solid var(--border-soft);
  color: var(--fg-faint);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  flex: 0 0 auto;
}

.cmdk-fade-enter-active,
.cmdk-fade-leave-active {
  transition: opacity var(--t-base) var(--ease);
}

.cmdk-fade-enter-from,
.cmdk-fade-leave-to {
  opacity: 0;
}

@media (max-width: 640px) {
  .cmdk-overlay {
    padding-top: var(--sp-6);
  }
  .cmdk-panel {
    max-height: calc(100vh - var(--sp-12));
  }
}
</style>