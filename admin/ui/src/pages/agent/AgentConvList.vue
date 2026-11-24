<script setup lang="ts">
/**
 * 智能体 · 左栏会话列表（从 AgentPage 拆出，r3）。
 * 重命名的编辑态在本组件内；打开 / 置顶 / 删除 / 新会话全部 emit 给页面。
 * 键盘：会话项 Enter / 空格打开，F2 重命名，Delete 删除；操作按钮在悬停或键盘聚焦时出现。
 */
import { nextTick, ref } from 'vue'
import { NInput } from 'naive-ui'
import { Pencil, Pin, Plus, Search, Trash2 } from 'lucide-vue-next'
import { Btn, ErrorBlock, IconBtn } from '@/components'
import { fmtRelative } from '@/utils/format'
import { useI18n } from '@/i18n'
import type { AgentConversation } from '@/api/agent'

defineProps<{
  groups: Array<{ key: string; label: string; items: AgentConversation[] }>
  total: number
  loading: boolean
  error: string
  activeId: string
  search: string
}>()

const emit = defineEmits<{
  (event: 'update:search', value: string): void
  (event: 'open', item: AgentConversation): void
  (event: 'new'): void
  (event: 'rename', item: AgentConversation, title: string): void
  (event: 'pin', item: AgentConversation): void
  (event: 'remove', item: AgentConversation): void
  (event: 'retry'): void
}>()

const { t } = useI18n()

function displayTitle(item: AgentConversation): string {
  return String(item.title || '').trim() || t('page.agent.newChat')
}

const editingId = ref('')
const editingTitle = ref('')

function startRename(item: AgentConversation): void {
  editingId.value = item.conversation_id
  editingTitle.value = displayTitle(item)
  void nextTick(() => {
    document.querySelector<HTMLInputElement>('.agent-conv__item.is-editing input')?.focus()
  })
}

function saveRename(item: AgentConversation): void {
  if (editingId.value !== item.conversation_id) return
  const title = editingTitle.value.trim()
  editingId.value = ''
  if (!title || title === displayTitle(item)) return
  emit('rename', item, title)
}

function cancelRename(): void {
  editingId.value = ''
}

function onItemKey(event: KeyboardEvent, item: AgentConversation): void {
  if (editingId.value === item.conversation_id) return
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    emit('open', item)
  } else if (event.key === 'F2') {
    event.preventDefault()
    startRename(item)
  } else if (event.key === 'Delete') {
    event.preventDefault()
    emit('remove', item)
  }
}
</script>

<template>
  <aside class="agent-conv">
    <!-- 每页只有一个主按钮（发送）：新会话用次级按钮。 -->
    <Btn variant="default" size="sm" :icon="Plus" block @click="emit('new')">
      {{ t('page.agent.newChat') }}
    </Btn>
    <n-input
      :value="search"
      size="small"
      :placeholder="t('page.agent.searchPlaceholder')"
      clearable
      @update:value="(value: string) => emit('update:search', value || '')"
    >
      <template #prefix><Search :size="14" aria-hidden="true" /></template>
    </n-input>

    <div class="agent-conv__list">
      <div v-if="loading && !total" class="agent-conv__sk" aria-busy="true" :aria-label="t('page.agent.loadingChats')">
        <span v-for="index in 6" :key="index" class="mh-skeleton agent-conv__sk-row" />
      </div>
      <ErrorBlock v-else-if="error" :title="error" compact @retry="emit('retry')" />
      <p v-else-if="!groups.length" class="agent-conv__empty">
        {{ total ? t('page.agent.noMatch') : t('page.agent.noChats') }}
      </p>
      <div v-for="group in groups" v-else :key="group.key" class="agent-conv__group">
        <p class="agent-conv__label">{{ group.label }}</p>
        <div
          v-for="item in group.items"
          :key="item.conversation_id"
          class="agent-conv__item"
          :class="{ 'is-active': item.conversation_id === activeId, 'is-editing': editingId === item.conversation_id }"
          role="button"
          tabindex="0"
          :aria-current="item.conversation_id === activeId ? 'true' : undefined"
          @click="editingId !== item.conversation_id && emit('open', item)"
          @keydown="onItemKey($event, item)"
        >
          <n-input
            v-if="editingId === item.conversation_id"
            v-model:value="editingTitle"
            size="tiny"
            @click.stop
            @keydown.enter.stop="saveRename(item)"
            @keydown.esc.stop="cancelRename"
            @blur="saveRename(item)"
          />
          <template v-else>
            <span class="agent-conv__title" :title="displayTitle(item)">
              <Pin v-if="item.pinned" :size="14" class="agent-conv__pin" aria-hidden="true" />
              {{ displayTitle(item) }}
            </span>
            <span class="agent-conv__time">{{ fmtRelative(item.updated_at || item.created_at) }}</span>
          </template>
          <span v-if="editingId !== item.conversation_id" class="agent-conv__ops" @click.stop>
            <IconBtn :label="t('page.agent.rename')" :icon="Pencil" @click="startRename(item)" />
            <IconBtn
              :label="item.pinned ? t('page.agent.unpin') : t('page.agent.pin')"
              :icon="Pin"
              :active="item.pinned"
              @click="emit('pin', item)"
            />
            <IconBtn :label="t('page.agent.deleteChat')" :icon="Trash2" @click="emit('remove', item)" />
          </span>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.agent-conv {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: var(--sp-2);
  padding: var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-1);
  min-height: 0;
}

.agent-conv__list {
  overflow: auto;
  min-height: 0;
  display: grid;
  align-content: start;
  gap: var(--sp-2);
  margin: 0 calc(var(--sp-1) * -1);
  padding: 0 var(--sp-1);
}

.agent-conv__sk {
  display: grid;
  gap: var(--sp-2);
  padding-top: var(--sp-2);
}

.agent-conv__sk-row {
  display: block;
  height: 40px;
  border-radius: var(--r-ctl);
}

.agent-conv__empty {
  margin: var(--sp-3) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  text-align: center;
}

.agent-conv__group {
  display: grid;
  gap: 1px;
}

.agent-conv__label {
  margin: var(--sp-2) var(--sp-2) 2px;
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  font-weight: 500;
  color: var(--fg-faint);
}

.agent-conv__item {
  position: relative;
  display: grid;
  gap: 1px;
  padding: 6px var(--sp-2);
  border-radius: var(--r-ctl);
  cursor: pointer;
  min-width: 0;
  transition: background var(--dur-1, 120ms) var(--ease-out, ease);
}

.agent-conv__item:hover {
  background: var(--surface-hover);
}

.agent-conv__item.is-active {
  background: var(--brand-soft);
}

.agent-conv__item.is-active .agent-conv__title {
  color: var(--brand);
  font-weight: 500;
}

.agent-conv__item:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

.agent-conv__title {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-conv__pin {
  flex: none;
  color: var(--fg-faint);
}

.agent-conv__time {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-faint);
  font-variant-numeric: tabular-nums;
}

.agent-conv__ops {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  padding: 1px;
  background: var(--surface-raised, var(--surface));
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  box-shadow: var(--shadow-1);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--dur-1, 120ms) var(--ease-out, ease);
}

.agent-conv__item:hover .agent-conv__ops,
.agent-conv__item:focus-within .agent-conv__ops {
  opacity: 1;
  pointer-events: auto;
}

@media (hover: none) {
  .agent-conv__ops {
    position: static;
    transform: none;
    opacity: 1;
    pointer-events: auto;
    justify-self: end;
    box-shadow: none;
  }
}

@media (max-width: 640px) {
  .agent-conv {
    max-height: 240px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .agent-conv__item,
  .agent-conv__ops {
    transition: none;
  }
}
</style>
