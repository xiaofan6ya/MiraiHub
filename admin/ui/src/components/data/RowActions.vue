<script setup lang="ts">
/**
 * 表格行操作：前 2 个用 quiet 文字按钮直接显示，其余收进「⋯」下拉；
 * 危险操作在菜单最下方、朱色、上方加分隔线（04-components §2 DataTable）。
 *
 * 浮层状态由本组件持有（v-model:show）：naive 的 trigger="click" 只在点「⋯」时切换，
 * 菜单项 select 之后它不会自己收起来 —— 真实浏览器里浮层会一直挂在页面上，
 * 盖住页面随后弹出的确认框。所以菜单点选、Esc、外点（naive 的 clickoutside）都收敛到 close()。
 */
import { computed, nextTick, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import { NPopover } from 'naive-ui'
import { MoreHorizontal } from 'lucide-vue-next'
import Btn from '../base/Btn.vue'
import IconBtn from '../base/IconBtn.vue'
import { useI18n } from '@/i18n'
import type { RowAction } from './rowActions'

const props = withDefaults(defineProps<{ actions: RowAction[]; max?: number }>(), { max: 2 })

const emit = defineEmits<{ (event: 'select', key: string): void }>()
const { t } = useI18n()

const show = ref(false)
const rootRef = ref<HTMLElement | null>(null)

const visible = computed(() => props.actions.filter((action) => !action.hidden))
const inline = computed(() => visible.value.slice(0, Math.max(0, props.max)))
const overflow = computed(() => visible.value.slice(Math.max(0, props.max)))
const normal = computed(() => overflow.value.filter((action) => !action.danger))
const dangerous = computed(() => overflow.value.filter((action) => action.danger))

/** 收起浮层；restoreFocus 时把焦点还给「⋯」，Esc 之后键盘用户不会掉到 <body> 上。 */
async function close(restoreFocus = false): Promise<void> {
  if (!show.value) return
  show.value = false
  if (!restoreFocus) return
  await nextTick()
  rootRef.value?.querySelector<HTMLElement>('.mh-icon-btn')?.focus()
}

/** 菜单项点选：先关浮层再 emit，否则页面同步弹出的确认框会被浮层压住。 */
function pickMenu(key: string): void {
  void close()
  emit('select', key)
}

/** 浮层挂到 body 上，焦点可能根本不在菜单里，所以在打开期间听整篇文档的 Esc。 */
function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  // 最内层先吃掉 Esc：浮层背后如果还有抽屉/对话框，不该被这一下顺手关掉。
  event.stopPropagation()
  void close(true)
}

watch(show, (open) => {
  if (open) window.addEventListener('keydown', onKeydown, true)
  else window.removeEventListener('keydown', onKeydown, true)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown, true)
})

/** 页面被 KeepAlive 缓存：浮层 Teleport 在 body 上不会跟着离开，直接收起（第 4 轮）。 */
onDeactivated(() => {
  void close()
})

// 可操作项缩到只剩直接按钮时，浮层已经不可达，状态跟着收敛（避免之后再冒出个开着的空菜单）。
watch(
  () => overflow.value.length,
  (count) => {
    if (!count) void close()
  },
)
</script>

<template>
  <div ref="rootRef" class="mh-row-actions">
    <Btn
      v-for="action in inline"
      :key="action.key"
      variant="quiet"
      size="sm"
      :disabled="action.disabled"
      @click="emit('select', action.key)"
    >
      {{ action.label }}
    </Btn>
    <n-popover
      v-if="overflow.length"
      v-model:show="show"
      trigger="click"
      placement="bottom-end"
      :show-arrow="false"
    >
      <template #trigger>
        <span class="mh-row-actions__trigger">
          <IconBtn :label="t('common.moreActions')" :icon="MoreHorizontal" />
        </span>
      </template>
      <div class="mh-row-actions__menu">
        <Btn
          v-for="action in normal"
          :key="action.key"
          variant="quiet"
          size="sm"
          block
          :disabled="action.disabled"
          class="mh-row-actions__item"
          @click="pickMenu(action.key)"
        >
          {{ action.label }}
        </Btn>
        <i v-if="normal.length && dangerous.length" class="mh-row-actions__divider" />
        <Btn
          v-for="action in dangerous"
          :key="action.key"
          variant="danger"
          size="sm"
          block
          :disabled="action.disabled"
          class="mh-row-actions__item"
          @click="pickMenu(action.key)"
        >
          {{ action.label }}
        </Btn>
      </div>
    </n-popover>
  </div>
</template>

<style scoped>
.mh-row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-1);
}

/* 浮层锚点必须是真实元素：直接拿 IconBtn 当 trigger 时，它的根是 NTooltip 的片段，
   VTarget 的 ref 指令挂不上去（targetRef 为 null）—— naive 靠这个 ref 定位浮层、判断外点，
   两件事会一起失效（外点检测直接在 null.contains 上抛 TypeError）。 */
.mh-row-actions__trigger {
  display: inline-flex;
}

.mh-row-actions__menu {
  display: grid;
  gap: var(--sp-1);
  min-width: 140px;
}

.mh-row-actions__item {
  justify-content: flex-start;
}

.mh-row-actions__divider {
  display: block;
  height: 1px;
  background: var(--border-soft);
  margin: var(--sp-1) 0;
}
</style>
