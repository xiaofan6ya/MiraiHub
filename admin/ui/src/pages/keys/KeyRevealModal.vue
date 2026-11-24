<script setup lang="ts">
/**
 * 查看完整 Key（GET /api/keys/{id}/view）。
 *
 * fixlog_security §S5：这个接口就是 Key 的 reveal 接口，后端每次调用都会写审计
 * （时间 / 动作 / 目标 / IP / 会话前缀 / UA）。所以先说明「会记审计」，用户确认后才真正读取；
 * 读到的明文只放在这个弹窗里，关闭即清空，不进列表、不进路由。
 */
import { computed, onDeactivated, ref, watch } from 'vue'
import { NModal } from 'naive-ui'
import { Eye, ShieldAlert } from 'lucide-vue-next'
import { Btn, Copyable, ErrorBlock, KV, type KvItem } from '@/components'
import { errorMessage } from '@/utils/error'
import { FMT_EMPTY, fmtFullTime } from '@/utils/format'
import { useI18n } from '@/i18n'
import { useKeepAliveSuspended } from '@/composables'
import { viewKey, type KeyRow, type KeyViewPayload } from '@/api/keys'

const props = defineProps<{
  show: boolean
  row: KeyRow | null
}>()

const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const { t } = useI18n()
const loading = ref(false)
const error = ref('')
const data = ref<KeyViewPayload | null>(null)

watch(
  () => [props.show, props.row?.id] as const,
  () => {
    // 每次打开 / 换 Key 都回到「先确认」状态，旧明文立即丢弃
    data.value = null
    error.value = ''
    loading.value = false
  },
)

async function reveal(): Promise<void> {
  const row = props.row
  if (!row || loading.value) return
  loading.value = true
  error.value = ''
  try {
    data.value = await viewKey(row.id)
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

const items = computed<KvItem[]>(() => [
  { label: t('page.keys.view.name'), value: props.row?.name || FMT_EMPTY },
  { label: t('page.keys.view.prefix'), value: props.row?.masked || FMT_EMPTY, mono: true },
  {
    label: t('page.keys.view.createdAt'),
    value: data.value?.created_at
      ? fmtFullTime(data.value.created_at)
      : props.row?.createdAt
        ? fmtFullTime(props.row.createdAt)
        : FMT_EMPTY,
  },
])

function close(): void {
  data.value = null
  emit('update:show', false)
}

/**
 * 第 5 轮：KeepAlive 离开页面时立即收起弹窗（Teleport 不随页面移走），并**直接关闭、丢弃明文**——
 * 完整 Key 不应在缓存页里留着，回来需要重新确认读取（会再记一次审计，符合预期）。
 */
const suspended = useKeepAliveSuspended()
onDeactivated(() => {
  if (props.show) close()
})
</script>

<template>
  <NModal
    :show="show && !suspended"
    preset="card"
    class="kr-modal"
    :title="t('page.keys.reveal.title')"
    :style="{ width: '520px', maxWidth: 'calc(100vw - 32px)' }"
    :mask-closable="true"
    @update:show="(value: boolean) => (value ? emit('update:show', true) : close())"
  >
    <div class="kr-body">
      <KV :items="items" label-width="72px" />
      <div class="kr-audit" role="note">
        <ShieldAlert :size="16" class="kr-audit__icon" />
        <p>{{ t('page.keys.reveal.audit') }}</p>
      </div>
      <ErrorBlock
        v-if="error"
        compact
        :title="t('page.keys.reveal.error', { error })"
        @retry="reveal"
      />
      <div v-if="data" class="kr-value">
        <Copyable :value="data.key" />
      </div>
    </div>
    <template #footer>
      <div class="kr-footer">
        <Btn variant="quiet" @click="close">{{ t('common.close') }}</Btn>
        <Btn v-if="!data" variant="primary" :icon="Eye" :loading="loading" :disabled="!row" @click="reveal">
          {{ t('page.keys.reveal.confirm') }}
        </Btn>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.kr-body {
  display: grid;
  gap: var(--sp-3);
}

.kr-audit {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  padding: var(--sp-3);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  background: var(--warn-soft);
}

.kr-audit p {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  text-wrap: pretty;
}

.kr-audit__icon {
  flex: none;
  margin-top: 1px;
  color: var(--warn);
}

.kr-value {
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
}

.kr-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
}
</style>
