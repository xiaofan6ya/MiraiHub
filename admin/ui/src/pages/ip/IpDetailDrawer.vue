<script setup lang="ts">
/**
 * IP 管理 · 活跃 IP 详情抽屉（点表格行打开）。
 * 上：流量（所选时间范围内）；下：客户端识别（UA / 设备 ID / 指纹 / MAC，客户端上报未验证）。
 * 底部：复制 IP · 查看日志 · 封禁 / 解除封禁（动作由页面执行，这里只 emit）。
 */
import { computed } from 'vue'
import { Ban, ScrollText, ShieldBan } from 'lucide-vue-next'
import { Btn, Copyable, DetailDrawer, KV, type KvItem } from '@/components'
import { FMT_EMPTY, fmtCompact, fmtCredit, fmtFullTime, fmtInt, fmtPct, fmtRelative } from '@/utils/format'
import { useI18n } from '@/i18n'
import type { IpActivityRow } from '@/api/ip'

const props = withDefaults(
  defineProps<{
    show: boolean
    row: IpActivityRow | null
    /** 当前时间范围文案（「近 24 小时」等），写在「流量」标题后。 */
    rangeLabel?: string
  }>(),
  { rangeLabel: '' },
)

const emit = defineEmits<{
  (event: 'update:show', show: boolean): void
  (event: 'action', row: IpActivityRow, key: string): void
}>()

const { t } = useI18n()

const badge = computed(() => {
  if (!props.row) return null
  return props.row.blocked
    ? { tone: 'danger' as const, label: t('page.ip.status.blocked') }
    : { tone: 'ok' as const, label: t('page.ip.status.allowed') }
})

function list(values: string[]): string {
  return values.length ? values.join(', ') : t('page.ip.detail.notReported')
}

const trafficItems = computed<KvItem[]>(() => {
  const row = props.row
  if (!row) return []
  return [
    { label: t('page.ip.col.requests'), value: fmtInt(row.requests) },
    {
      label: t('page.ip.col.failed'),
      value: row.requests > 0 ? fmtInt(row.failed) + ' · ' + fmtPct(row.failureRate) : fmtInt(row.failed),
    },
    { label: t('page.ip.col.blockedRequests'), value: fmtInt(row.blockedRequests) },
    { label: t('page.ip.col.credits'), value: row.credits ? fmtCredit(row.credits) : FMT_EMPTY },
    { label: t('page.ip.detail.avgCredits'), value: row.avgCredits === null ? FMT_EMPTY : row.avgCredits.toFixed(4) },
    { label: 'Token', value: row.tokens ? fmtCompact(row.tokens) : FMT_EMPTY },
    { label: t('page.ip.detail.avgTokens'), value: row.avgTokens === null ? FMT_EMPTY : fmtCompact(row.avgTokens) },
    { label: t('page.ip.detail.knownUsage'), value: fmtInt(row.knownUsage) },
    {
      label: t('page.ip.col.lastSeen'),
      value: row.lastSeen ? fmtRelative(row.lastSeen) + ' · ' + fmtFullTime(row.lastSeen) : FMT_EMPTY,
    },
  ]
})

const clientItems = computed<KvItem[]>(() => {
  const row = props.row
  if (!row) return []
  const client = row.client
  return [
    { label: t('page.ip.detail.clientName'), value: client.client || FMT_EMPTY },
    { label: 'UA', value: client.userAgent || t('page.ip.detail.uaMissing'), mono: Boolean(client.userAgent), block: true },
    { label: t('page.ip.detail.deviceIds'), value: list(client.deviceIds), mono: client.deviceIds.length > 0 },
    { label: t('page.ip.detail.fingerprints'), value: list(client.fingerprints), mono: client.fingerprints.length > 0 },
    { label: 'MAC', value: list(client.macs), mono: client.macs.length > 0 },
  ]
})

function act(key: string): void {
  if (props.row) emit('action', props.row, key)
}
</script>

<template>
  <DetailDrawer
    :show="show"
    :title="row?.ip || FMT_EMPTY"
    :badge="badge"
    :subtitle="row?.isCurrent ? t('page.ip.detail.currentHint') : undefined"
    :width="520"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <template v-if="row">
      <section class="idd-section">
        <h3 class="idd-title">
          {{ t('page.ip.detail.traffic') }}<span v-if="rangeLabel" class="idd-range"> · {{ rangeLabel }}</span>
        </h3>
        <KV :items="trafficItems" label-width="132px" />
      </section>
      <section class="idd-section">
        <h3 class="idd-title">{{ t('page.ip.detail.client') }}</h3>
        <KV :items="clientItems" label-width="132px" />
        <p class="idd-note">{{ t('page.ip.detail.clientNote') }}</p>
      </section>
    </template>
    <template #footer>
      <div v-if="row" class="idd-foot">
        <Copyable :value="row.ip" muted />
        <span class="idd-spacer" />
        <Btn variant="default" :icon="ScrollText" @click="act('logs')">{{ t('page.ip.action.viewLogs') }}</Btn>
        <Btn v-if="row.blocked" variant="default" :icon="ShieldBan" @click="act('toggle')">
          {{ t('page.ip.action.unblock') }}
        </Btn>
        <Btn v-else variant="danger" :icon="Ban" @click="act('toggle')">{{ t('page.ip.action.block') }}</Btn>
      </div>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.idd-section {
  display: grid;
  gap: var(--sp-2);
  padding-bottom: var(--sp-4);
  margin-bottom: var(--sp-4);
  border-bottom: 1px solid var(--border-soft);
}

.idd-section:last-child {
  border-bottom: 0;
  margin-bottom: 0;
}

.idd-title {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 600;
  color: var(--fg);
}

.idd-range {
  font-weight: 400;
  color: var(--fg-muted);
}

.idd-note {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.idd-foot {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  width: 100%;
}

.idd-spacer {
  flex: 1;
}
</style>
