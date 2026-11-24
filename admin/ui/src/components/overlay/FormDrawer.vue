<script setup lang="ts">
/** 新建 / 编辑表单统一用右侧抽屉：480px（复杂表单 640px），底部固定操作栏。 */
import { computed } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'
import Btn from '../base/Btn.vue'
import { MOBILE_QUERY, useMediaQuery } from '../_internal/media'
import { useI18n } from '@/i18n'
import { useKeepAliveSuspended } from '@/composables/useKeepAliveSuspended'

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
    submitText: undefined,
    cancelText: undefined,
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

const { t } = useI18n()
const isPhone = useMediaQuery(MOBILE_QUERY)
/** 页面被 KeepAlive 缓存时收起（Teleport 出去的抽屉不跟页面走），回来原样恢复，表单内容不丢。 */
const suspended = useKeepAliveSuspended()
/*
 * 挂起期间改用 display-directive="show"：抽屉只是隐藏，内容（子组件本地状态、输入框、抽屉内滚动位置）
 * 不卸载，回来原样恢复；默认 'if' 会在离场动画后销毁内容。只对「页面里本来开着」的抽屉生效，
 * 关着的抽屉不会因为离开页面被挂载出来（第 5 轮）。
 */
/** ≤640px（375 宽的手机）抽屉铺满：固定 480 / 640px 会比屏幕还宽，关闭按钮跑到屏幕外。 */
const width = computed<number | string>(() => (isPhone.value ? '100%' : props.size === 'lg' ? 640 : 480))
const submitLabel = computed(() => props.submitText ?? t('common.save'))
const cancelLabel = computed(() => props.cancelText ?? t('common.cancel'))

function close(): void {
  emit('update:show', false)
  emit('cancel')
}
</script>

<template>
  <n-drawer
    :show="show && !suspended"
    :display-directive="suspended && show ? 'show' : 'if'"
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
          <Btn variant="default" @click="close">{{ cancelLabel }}</Btn>
          <Btn
            variant="primary"
            :loading="submitLoading"
            :disabled="submitDisabled"
            @click="emit('submit')"
          >
            {{ submitLabel }}
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
