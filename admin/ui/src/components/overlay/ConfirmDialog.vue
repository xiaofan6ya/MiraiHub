<script setup lang="ts">
/**
 * 危险 / 高风险确认：居中 420px，标题是问句，正文说清后果。
 * 高风险操作要求输入确认文字或勾选「我已了解」。
 * 页面用 useConfirm()（@/composables）以 Promise 方式调用；本组件是它的视图。
 */
import { computed, ref, watch } from 'vue'
import { NCheckbox, NInput, NModal } from 'naive-ui'
import Btn from '../base/Btn.vue'
import { canConfirm } from './confirmGate'

const props = withDefaults(
  defineProps<{
    show: boolean
    /** 问句标题，如「删除 3 个账号？」 */
    title: string
    /** 后果说明 */
    content?: string
    /** 危险操作：确认按钮朱色 */
    danger?: boolean
    confirmText?: string
    cancelText?: string
    /** 要求输入这段文字才能提交 */
    requireText?: string
    /** 要求勾选（文案固定为「我已了解」时传 true 也行） */
    requireCheckbox?: string | boolean
    loading?: boolean
  }>(),
  {
    content: undefined,
    danger: false,
    confirmText: '确定',
    cancelText: '取消',
    requireText: undefined,
    requireCheckbox: undefined,
    loading: false,
  },
)

const emit = defineEmits<{
  (event: 'update:show', show: boolean): void
  (event: 'confirm'): void
  (event: 'cancel'): void
}>()

const typed = ref('')
const checked = ref(false)

watch(
  () => props.show,
  (show) => {
    if (show) {
      typed.value = ''
      checked.value = false
    }
  },
)

const needCheckbox = computed(() => Boolean(props.requireCheckbox))
const checkboxLabel = computed(() =>
  typeof props.requireCheckbox === 'string' ? props.requireCheckbox : '我已了解后果',
)

const allowed = computed(() =>
  canConfirm({
    requireText: props.requireText,
    typed: typed.value,
    requireCheckbox: needCheckbox.value ? checkboxLabel.value : undefined,
    checked: checked.value,
    loading: props.loading,
  }),
)

function cancel(): void {
  emit('update:show', false)
  emit('cancel')
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    class="mh-confirm"
    :style="{ width: '420px', maxWidth: 'calc(100vw - 32px)' }"
    :bordered="false"
    :mask-closable="false"
    :closable="false"
    :title="undefined"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <div class="mh-confirm__body">
      <h2 class="mh-confirm__title">{{ title }}</h2>
      <p v-if="content" class="mh-confirm__content">{{ content }}</p>
      <div v-if="requireText" class="mh-confirm__gate">
        <p class="mh-confirm__gate-hint">
          请输入 <code class="mono">{{ requireText }}</code> 以确认
        </p>
        <n-input v-model:value="typed" :placeholder="requireText" size="small" />
      </div>
      <n-checkbox v-if="needCheckbox" v-model:checked="checked" class="mh-confirm__gate-check">
        {{ checkboxLabel }}
      </n-checkbox>
    </div>
    <template #footer>
      <div class="mh-confirm__actions">
        <Btn variant="default" @click="cancel">{{ cancelText }}</Btn>
        <Btn
          :variant="danger ? 'danger' : 'primary'"
          :solid="danger"
          :disabled="!allowed"
          :loading="loading"
          @click="emit('confirm')"
        >
          {{ confirmText }}
        </Btn>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.mh-confirm__body {
  display: grid;
  gap: var(--sp-3);
}

.mh-confirm__title {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.mh-confirm__content {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--fg-2);
}

.mh-confirm__gate {
  display: grid;
  gap: var(--sp-2);
}

.mh-confirm__gate-hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.mh-confirm__gate-hint code {
  color: var(--danger);
}

.mh-confirm__gate-check {
  font-size: var(--fs-small);
}

.mh-confirm__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-2);
}
</style>
