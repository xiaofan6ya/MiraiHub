<script setup lang="ts">
/** 新建 / 编辑表单统一用右侧抽屉：480px（复杂表单 640px），底部固定操作栏。 */
import { computed } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'
import Btn from '../base/Btn.vue'

const props = withDefaults(
  defineProps<{
    show: boolean
    title: string
    /** md=480px / lg=640px */
    size?: 'md' | 'lg'
    submitText?: string
    cancelText?: string
    submitLoading?: boolean
    submitDisabled?: boolean
    maskClosable?: boolean
    /** 隐藏底部操作栏（只读抽屉用） */
    hideFooter?: boolean
  }>(),
  {
    size: 'md',
    submitText: '保存',
    cancelText: '取消',
    submitLoading: false,
    submitDisabled: false,
    maskClosable: false,
    hideFooter: false,
  },
)

const emit = defineEmits<{
  (event: 'update:show', show: boolean): void
  (event: 'submit'): void
  (event: 'cancel'): void
}>()

const width = computed(() => (props.size === 'lg' ? 640 : 480))

function close(): void {
  emit('update:show', false)
  emit('cancel')
}
</script>

<template>
  <n-drawer
    :show="show"
    :width="width"
    placement="right"
    :mask-closable="maskClosable"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <n-drawer-content :title="title" closable :native-scrollbar="false">
      <div class="mh-form">
        <slot />
      </div>
      <template v-if="!hideFooter" #footer>
        <div class="mh-form__actions">
          <Btn variant="default" @click="close">{{ cancelText }}</Btn>
          <Btn
            variant="primary"
            :loading="submitLoading"
            :disabled="submitDisabled"
            @click="emit('submit')"
          >
            {{ submitText }}
          </Btn>
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.mh-form {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.mh-form__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-2);
  width: 100%;
}
</style>
