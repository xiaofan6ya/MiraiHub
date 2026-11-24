<script setup lang="ts">
/**
 * 底部吸附保存条（10-visual-refresh §1.6）：设置类页面统一的保存方式。
 * 「有 N 项未保存　放弃　保存」；没有改动时不出现。
 * 放在页面内容末尾即可：position: sticky 贴着视口底部，页面滚到底时回到文档流里。
 * 有改动时 Ctrl/⌘+S 等于点「保存」。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, watch } from 'vue'
import Btn from './Btn.vue'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 未保存项数；true = 有改动但不计数。0 / false 时整条隐藏。 */
    dirty: number | boolean
    saving?: boolean
    /** 保存按钮禁用（例如表单校验没过）。 */
    disabled?: boolean
  }>(),
  { saving: false, disabled: false },
)

const emit = defineEmits<{
  (event: 'save'): void
  (event: 'discard'): void
}>()

const { t } = useI18n()

const visible = computed(() =>
  typeof props.dirty === 'number' ? Number.isFinite(props.dirty) && props.dirty > 0 : props.dirty === true,
)
const message = computed(() =>
  typeof props.dirty === 'number' ? t('savebar.count', { count: props.dirty }) : t('savebar.dirty'),
)

function save(): void {
  if (!visible.value || props.saving || props.disabled) return
  emit('save')
}

function discard(): void {
  if (props.saving) return
  emit('discard')
}

function onKeydown(event: KeyboardEvent): void {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return
  if (String(event.key).toLowerCase() !== 's') return
  event.preventDefault()
  save()
}

function bind(on: boolean): void {
  if (typeof window === 'undefined') return
  window.removeEventListener('keydown', onKeydown)
  if (on) window.addEventListener('keydown', onKeydown)
}

/**
 * 第 4 轮修（P1）：页面被 KeepAlive 缓存后组件不卸载，原来 window 上的 Ctrl/⌘+S 监听一直挂着 ——
 * 在别的页面按 Ctrl+S 会触发已离开的设置页保存。离开时解绑，回来按当前状态重绑。
 */
let inactive = false
watch(visible, (value) => bind(value && !inactive), { immediate: true })
onDeactivated(() => {
  inactive = true
  bind(false)
})
onActivated(() => {
  inactive = false
  bind(visible.value)
})
onBeforeUnmount(() => bind(false))
</script>

<template>
  <transition name="mh-savebar">
    <div
      v-if="visible"
      class="mh-savebar"
      data-testid="mh-savebar"
      role="region"
      :aria-label="t('savebar.region')"
    >
      <span class="mh-savebar__dot" aria-hidden="true" />
      <span class="mh-savebar__text" role="status">{{ message }}</span>
      <div class="mh-savebar__actions">
        <Btn variant="quiet" :disabled="saving" @click="discard">{{ t('savebar.discard') }}</Btn>
        <Btn variant="primary" :loading="saving" :disabled="disabled" @click="save">
          {{ saving ? t('savebar.saving') : t('savebar.save') }}
        </Btn>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.mh-savebar {
  position: sticky;
  bottom: var(--sp-4);
  z-index: 15;
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  width: min(100%, 720px);
  margin: var(--sp-4) auto 0;
  padding: var(--sp-2) var(--sp-2) var(--sp-2) var(--sp-4);
  background: var(--surface-raised);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
}

.mh-savebar__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--warn);
}

.mh-savebar__text {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-savebar__actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

.mh-savebar-enter-active,
.mh-savebar-leave-active {
  transition:
    opacity var(--dur-2) var(--ease-out),
    transform var(--dur-2) var(--ease-out);
}

.mh-savebar-leave-active {
  transition-duration: var(--dur-1);
}

.mh-savebar-enter-from,
.mh-savebar-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

@media (max-width: 767px) {
  .mh-savebar {
    bottom: var(--sp-2);
    padding-left: var(--sp-3);
  }
}
</style>
