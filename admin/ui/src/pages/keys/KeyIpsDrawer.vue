<script setup lang="ts">
/**
 * Key 来源 IP 抽屉（GET /api/keys/{id}/ips）：时间范围 · 四项汇总 · 明细表。
 * 换 Key 时先清空汇总与明细，旧 Key 的数据不会串到新 Key（取数还没回来时显示 0）。
 */
import { computed, h, ref, watch } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import {
  Badge,
  Btn,
  DataTable,
  DetailDrawer,
  ErrorBlock,
  IconBtn,
  KV,
  Segmented,
  type KvItem,
  type TableColumn,
  type TableRow,
} from '@/components'
import { errorMessage } from '@/utils/error'
import { fmtCredit, fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
import { emptyIpOverview, keyIps, toIpOverview, type IpOverviewView, type KeyRow } from '@/api/keys'

const props = defineProps<{ show: boolean; row: KeyRow | null }>()
const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const { t } = useI18n()
const days = ref('1')
const loading = ref(false)
const error = ref('')
const overview = ref<IpOverviewView>(emptyIpOverview())
let token = 0

async function load(): Promise<void> {
  const row = props.row
  if (!row) return
  const current = ++token
  loading.value = true
  error.value = ''
  try {
    const payload = await keyIps(row.id, { days: Number(days.value) || 1 })
    if (current !== token) return
    overview.value = toIpOverview(payload)
  } catch (err) {
    if (current !== token) return
    error.value = errorMessage(err)
    overview.value = emptyIpOverview()
  } finally {
    if (current === token) loading.value = false
  }
}

watch(
  () => [props.show, props.row?.id] as const,
  ([show, id], previous) => {
    if (!show || !id) return
    const changed = !previous || previous[1] !== id || !previous[0]
    if (!changed) return
    days.value = '1'
    overview.value = emptyIpOverview()
    error.value = ''
    void load()
  },
  { immediate: true },
)

function setDays(value: string | number): void {
  days.value = String(value)
  void load()
}

const rangeOptions = computed(() => [
  { label: t('page.keys.ips.range24'), value: '1' },
  { label: t('page.keys.ips.range7'), value: '7' },
  { label: t('page.keys.ips.range30'), value: '30' },
])

const summary = computed<KvItem[]>(() => [
  { label: t('page.keys.ipSummary.ips'), value: fmtInt(overview.value.totalIps) },
  { label: t('page.keys.ipc.requests'), value: fmtInt(overview.value.requests) },
  { label: t('page.keys.ipSummary.activeIps'), value: fmtInt(overview.value.activeIps) },
  { label: t('page.keys.ipSummary.activeRequests'), value: fmtInt(overview.value.activeRequests) },
])

/** 明细行必须走 toIpOverview 的 rows（models 已拼成字符串、时间已格式化），不能直接绑原始 KeyIpRow。 */
const rows = computed<TableRow[]>(() => overview.value.rows as unknown as TableRow[])

const columns = computed<TableColumn[]>(() => [
  { key: 'ip', title: t('page.keys.ipc.ip'), mobile: 'title', width: 150, render: (row: TableRow) => h('span', { class: 'kip-mono' }, String(row.ip ?? '')) },
  { key: 'requests', title: t('page.keys.ipc.requests'), mobile: 'meta', width: 100, numeric: true, render: (row: TableRow) => fmtInt(row.requests) },
  { key: 'failed', title: t('page.keys.ipc.failed'), mobile: 'meta', width: 80, numeric: true, render: (row: TableRow) => fmtInt(row.failed) },
  { key: 'credits', title: t('page.keys.ipc.credits'), mobile: 'meta', width: 90, numeric: true, render: (row: TableRow) => fmtCredit(row.credits) },
  { key: 'tokens', title: 'Token', mobile: 'meta', width: 100, numeric: true, render: (row: TableRow) => fmtInt(row.tokens) },
  { key: 'lastSeen', title: t('page.keys.ipc.lastSeen'), mobile: 'meta', width: 140 },
  { key: 'models', title: t('page.keys.ipc.models'), mobile: 'subtitle', minWidth: 180 },
  { key: 'active', title: t('page.keys.ipc.active'), mobile: 'meta', width: 72, numeric: true, render: (row: TableRow) => fmtInt(row.active) },
  {
    key: 'blocked',
    title: t('page.keys.ipc.status'),
    mobile: 'meta',
    width: 90,
    render: (row: TableRow) =>
      row.blocked
        ? h(Badge, { tone: 'danger', dot: true }, { default: () => t('page.keys.ipc.blocked') })
        : h('span', { class: 'kip-muted' }, t('page.keys.ipc.ok')),
  },
])
</script>

<template>
  <DetailDrawer
    :show="show"
    :title="t('page.keys.ips.title', { name: row?.name || '' })"
    :subtitle="t('page.keys.ips.subtitle')"
    :width="760"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <div class="kip-bar">
      <Segmented
        :options="rangeOptions"
        :model-value="days"
        :aria-label="t('page.keys.ips.rangeAria')"
        @update:model-value="setDays"
      />
      <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="loading" @click="load" />
    </div>
    <div class="kip-summary">
      <KV :items="summary" label-width="96px" />
    </div>
    <ErrorBlock v-if="error" :title="t('page.keys.ips.error', { error })" compact @retry="load" />
    <DataTable
      v-else
      table-id="keys-ips"
      :columns="columns"
      :data="rows"
      row-key="ip"
      :loading="loading"
      :pagination="false"
      :virtual-scroll="false"
      max-height="360px"
      :empty-description="t('page.keys.ips.empty')"
    />
    <p class="kip-hint">{{ t('page.keys.ips.note') }}</p>
    <template #footer>
      <Btn variant="default" @click="emit('update:show', false)">{{ t('common.close') }}</Btn>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.kip-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}

.kip-summary {
  padding: var(--sp-3);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-card);
  background: var(--surface-2);
}

.kip-summary :deep(.mh-kv) {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.kip-hint {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

:deep(.kip-mono) {
  font-family: var(--font-mono);
  color: var(--fg);
}

:deep(.kip-muted) {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

@media (max-width: 640px) {
  .kip-summary :deep(.mh-kv) {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
