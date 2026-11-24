<script setup lang="ts">
/**
 * 平台网关 · 当前调度顺序（从 GatewayPage 拆出，r3）。
 *
 * - 「优先级」策略下可拖拽排序；拖拽柄是按钮，聚焦后 ↑/↓ 也能移动（键盘可完成主任务，11 §4.5）；
 * - 行内「禁用 / 启用」桌面端悬停 / 键盘聚焦 / 已禁用行才显示；
 * - 优先级输入框失焦或回车提交。
 * 本组件只管展示与交互，保存全部 emit 给页面（乐观更新 + 失败回滚在页面）。
 */
import { ref } from 'vue'
import { NInputNumber } from 'naive-ui'
import { GripVertical } from 'lucide-vue-next'
import { Badge, Btn, PlatformLogo } from '@/components'
import { fmtInt } from '@/utils/format'
import { formatBalance, priorityOf, type GatewayConfig, type GatewayOrderItem } from '@/api/gateway'
import { useI18n } from '@/i18n'

const props = defineProps<{
  order: GatewayOrderItem[]
  config: GatewayConfig | null
  drafts: Record<string, number>
  editable: boolean
  saving: boolean
}>()

const emit = defineEmits<{
  /** 拖拽 / 键盘移动过程中的新顺序（页面直接替换 order）。 */
  (event: 'update:order', rows: GatewayOrderItem[]): void
  /** 拖拽 / 键盘移动结束：页面按当前顺序保存优先级。 */
  (event: 'commit-order'): void
  (event: 'update:draft', platform: string, value: number): void
  (event: 'commit-priority', platform: string): void
  (event: 'toggle', row: GatewayOrderItem): void
}>()

const { t } = useI18n()
const dragIndex = ref<number | null>(null)
const listEl = ref<HTMLElement | null>(null)

function canDrag(row: GatewayOrderItem | undefined): boolean {
  return Boolean(row) && props.editable && Boolean(row?.available) && !row?.disabled
}

function moveTo(from: number, to: number): boolean {
  if (from === to || to < 0 || to >= props.order.length) return false
  if (!canDrag(props.order[to]) && !canDrag(props.order[from])) return false
  const rows = [...props.order]
  const [moved] = rows.splice(from, 1)
  if (!moved) return false
  rows.splice(to, 0, moved)
  emit('update:order', rows)
  return true
}

function onDragStart(index: number, event: DragEvent): void {
  if (!props.editable) return
  dragIndex.value = index
  try {
    event.dataTransfer?.setData('text/plain', String(index))
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  } catch {
    /* 老 WebView 忽略 */
  }
}

function onDragOver(index: number, event: DragEvent): void {
  if (dragIndex.value === null || dragIndex.value === index) return
  const from = dragIndex.value
  if (!canDrag(props.order[index]) && !canDrag(props.order[from])) return
  event.preventDefault()
  if (moveTo(from, index)) dragIndex.value = index
}

function onDragEnd(): void {
  if (dragIndex.value === null) return
  dragIndex.value = null
  emit('commit-order')
}

/** 拖拽柄键盘：↑/↓ 移动一位并立即保存，焦点跟着行走。 */
function onHandleKey(index: number, event: KeyboardEvent): void {
  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
  event.preventDefault()
  const to = event.key === 'ArrowUp' ? index - 1 : index + 1
  if (!moveTo(index, to)) return
  emit('commit-order')
  requestAnimationFrame(() => {
    const handles = listEl.value?.querySelectorAll<HTMLButtonElement>('.gw-order__handle')
    handles?.[to]?.focus()
  })
}
</script>

<template>
  <div ref="listEl" class="gw-order">
    <div
      v-for="(row, index) in order"
      :key="row.platform"
      class="gw-order-row"
      :class="{
        'is-disabled': row.disabled || !row.available,
        'is-draggable': canDrag(row),
        'is-dragging': dragIndex === index,
      }"
      :draggable="canDrag(row)"
      @dragstart="onDragStart(index, $event)"
      @dragover="onDragOver(index, $event)"
      @dragend="onDragEnd"
    >
      <span class="gw-order__rank">{{ index + 1 }}</span>
      <button
        v-if="editable"
        type="button"
        class="gw-order__handle"
        :disabled="!canDrag(row) || saving"
        :aria-label="t('page.gateway.order.moveAria', { name: row.label || row.platform })"
        @keydown="onHandleKey(index, $event)"
      >
        <GripVertical :size="14" aria-hidden="true" />
      </button>
      <span v-else class="gw-order__handle gw-order__handle--off" aria-hidden="true">·</span>
      <span class="gw-order__logo"><PlatformLogo :id="row.platform" :size="18" :label="row.label" /></span>
      <span class="gw-order__name">{{ row.label || row.platform }}</span>

      <span class="gw-order__priority">
        <template v-if="editable">
          <NInputNumber
            class="gw-priority"
            size="small"
            :min="0"
            :max="9999"
            :show-button="false"
            :value="drafts[row.platform]"
            :disabled="saving"
            :aria-label="t('page.gateway.order.priorityAria', { name: row.label || row.platform })"
            @update:value="(value: number | null) => emit('update:draft', row.platform, typeof value === 'number' ? value : 0)"
            @blur="emit('commit-priority', row.platform)"
            @keyup.enter="emit('commit-priority', row.platform)"
          />
          <span class="gw-sub">{{ t('page.gateway.order.priority') }}</span>
        </template>
        <span v-else class="gw-sub">
          {{ t('page.gateway.order.priorityValue', { value: priorityOf(config, row.platform) }) }}
        </span>
      </span>

      <span
        class="gw-order__meta"
        :title="row.balance_remain === null || row.balance_remain === undefined ? undefined : fmtInt(row.balance_remain)"
      >
        {{ t('page.gateway.order.meta', { usable: row.usable, balance: formatBalance(row.balance_remain) }) }}
      </span>
      <span class="gw-order__reason">
        <Badge v-if="row.disabled" tone="neutral" dot :title="row.reason || undefined">
          {{ t('page.gateway.order.disabled') }}
        </Badge>
        <Badge v-else-if="!row.available" tone="warn" dot :title="row.reason || undefined">
          {{ t('page.gateway.order.noAccounts') }}
        </Badge>
        <span v-else-if="row.reason" class="gw-sub" :title="row.reason">{{ row.reason }}</span>
      </span>
      <Btn class="gw-order__toggle" variant="quiet" size="sm" :disabled="saving" @click="emit('toggle', row)">
        {{ row.disabled ? t('page.gateway.action.enable') : t('page.gateway.action.disable') }}
      </Btn>
    </div>
  </div>
</template>

<style scoped>
.gw-sub {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.gw-order {
  display: grid;
  gap: 0;
  margin: 0 calc(var(--sp-2) * -1);
}

.gw-order-row {
  display: grid;
  grid-template-columns: 24px 20px 28px minmax(0, 1fr) 108px 132px minmax(0, 1.1fr) auto;
  align-items: center;
  gap: var(--sp-2);
  min-height: var(--row-h, 44px);
  padding: 0 var(--sp-2);
  border-radius: var(--r-ctl);
  transition: background var(--dur-1, 120ms) var(--ease-out, ease);
}

.gw-order-row + .gw-order-row {
  box-shadow: inset 0 1px 0 var(--border-soft);
}

.gw-order-row:hover {
  background: var(--surface-hover);
  box-shadow: none;
}

.gw-order-row:hover + .gw-order-row {
  box-shadow: none;
}

.gw-order-row.is-disabled {
  color: var(--fg-muted);
}

.gw-order-row.is-disabled .gw-order__logo,
.gw-order-row.is-disabled .gw-order__name,
.gw-order-row.is-disabled .gw-order__meta {
  opacity: 0.6;
}

.gw-order-row.is-draggable {
  cursor: grab;
}

.gw-order-row.is-draggable:active {
  cursor: grabbing;
}

.gw-order-row.is-dragging {
  background: var(--brand-soft);
}

.gw-order__rank {
  font-size: var(--fs-small);
  color: var(--fg-faint);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.gw-order__handle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: var(--r-chip);
  background: transparent;
  color: var(--fg-faint);
  cursor: grab;
}

.gw-order__handle:hover:not(:disabled) {
  color: var(--fg-2);
  background: var(--surface-2);
}

.gw-order__handle:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
  color: var(--brand);
}

.gw-order__handle:disabled {
  cursor: default;
  opacity: 0.4;
}

.gw-order__handle--off {
  color: transparent;
  cursor: default;
}

.gw-order__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
}

.gw-order__name {
  font-size: var(--fs-body);
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gw-order__priority {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
}

.gw-priority {
  width: 72px;
}

.gw-priority :deep(input) {
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.gw-order__meta {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.gw-order__reason {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
  overflow: hidden;
}

.gw-order__reason .gw-sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 760px) {
  .gw-order-row {
    grid-template-columns: 24px 20px 28px minmax(0, 1fr) auto;
    padding: var(--sp-2);
  }

  .gw-order__priority,
  .gw-order__meta,
  .gw-order__reason {
    grid-column: 2 / -1;
  }
}

@media (hover: hover) {
  .gw-order__toggle {
    opacity: 0;
    transition: opacity var(--dur-1, 120ms) var(--ease-out, ease);
  }
  .gw-order-row:hover .gw-order__toggle,
  .gw-order-row:focus-within .gw-order__toggle,
  .gw-order-row.is-disabled .gw-order__toggle {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .gw-order-row,
  .gw-order__toggle {
    transition: none;
  }
}
</style>
