<script setup lang="ts">
/** 只读详情抽屉（日志 / 账号）：640px，标题 + 徽章，内容用 Section 分组。 */
import { computed } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'
import Badge from '../base/Badge.vue'
import { MOBILE_QUERY, useMediaQuery } from '../_internal/media'
import type { BadgeTone } from '../base/badge'
import { useKeepAliveSuspended } from '@/composables/useKeepAliveSuspended'

const props = withDefaults(
  defineProps<{
    show: boolean
    title: string
    badge?: { tone: BadgeTone; label: string } | null
    subtitle?: string
    width?: number
  }>(),
  { badge: null, subtitle: undefined, width: 640 },
)

const isPhone = useMediaQuery(MOBILE_QUERY)
/** ≤640px 铺满屏幕（640px 的抽屉在 375 宽的手机上比屏幕还宽）。 */
const drawerWidth = computed<number | string>(() => (isPhone.value ? '100%' : props.width))

const emit = defineEmits<{ (event: 'update:show', show: boolean): void }>()

/** 页面被 KeepAlive 缓存时收起（Teleport 到 body 的抽屉不会跟着页面离开），回来原样恢复。 */
const suspended = useKeepAliveSuspended()
/*
 * 挂起期间改用 display-directive="show"：抽屉只是隐藏，内容（子组件本地状态、输入框、抽屉内滚动位置）
 * 不卸载，回来原样恢复；默认 'if' 会在离场动画后销毁内容。只对「页面里本来开着」的抽屉生效，
 * 关着的抽屉不会因为离开页面被挂载出来（第 5 轮）。
 */
</script>

<template>
  <n-drawer
    :show="show && !suspended"
    :display-directive="suspended && show ? 'show' : 'if'"
    :width="drawerWidth"
    placement="right"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <n-drawer-content closable :native-scrollbar="false">
      <template #header>
        <div class="mh-detail__head">
          <span class="mh-detail__title">{{ title }}</span>
          <Badge v-if="badge" :tone="badge.tone" dot>{{ badge.label }}</Badge>
          <span v-if="subtitle" class="mh-detail__subtitle">{{ subtitle }}</span>
        </div>
      </template>
      <div class="mh-detail__body">
        <slot />
      </div>
      <template v-if="$slots.footer" #footer>
        <div class="mh-detail__footer"><slot name="footer" /></div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.mh-detail__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
  flex-wrap: wrap;
}

.mh-detail__title {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.mh-detail__subtitle {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.mh-detail__body {
  display: grid;
  gap: var(--sp-3);
  min-width: 0;
}

.mh-detail__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-2);
  width: 100%;
}
</style>
