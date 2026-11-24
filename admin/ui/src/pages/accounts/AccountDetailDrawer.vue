<script setup lang="ts">
/**
 * 账号池 · 详情抽屉（契约 §「点击行进入详情抽屉」：基础信息 + 积分明细 + 模型冷却 + 操作按钮组）。
 *
 * 打开时拉 GET /api/accounts/{id}/credit-details（可「重新采集」）；关闭 / 换账号 / 卸载时 abort。
 * 底部按钮只抛事件，确认与请求由页面统一处理（与行操作走同一条路径）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { Btn, DetailDrawer, Empty, ErrorBlock, KV, Meter, type KvItem } from '@/components'
import { isAbortError } from '@/api/http'
import { failureText } from '@/utils/error'
import { FMT_EMPTY, fmtCredit, fmtDuration, fmtFullTime, fmtInt, fmtRelative } from '@/utils/format'
import { useI18n } from '@/i18n'
import { getCreditDetails, type AccountRow, type CreditDetailsPayload } from '@/api/accounts'
import type { DetailActionKey } from './types'

const props = defineProps<{
  show: boolean
  row: AccountRow | null
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'action', key: DetailActionKey): void
}>()

const { t } = useI18n()

const loading = ref(false)
const error = ref('')
const details = ref<CreditDetailsPayload | null>(null)
let controller: AbortController | null = null

async function loadDetails(refresh: boolean): Promise<void> {
  const row = props.row
  if (!row) return
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  error.value = ''
  try {
    const payload = await getCreditDetails(row.id, refresh, current.signal)
    if (controller !== current) return
    details.value = payload
  } catch (err) {
    if (isAbortError(err) || controller !== current) return
    error.value = failureText(t('page.accounts.detailLoadFailed'), err)
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

watch(
  () => [props.show, props.row?.id] as const,
  ([show, id], previous) => {
    if (!show) {
      controller?.abort()
      controller = null
      loading.value = false
      return
    }
    if (!id) return
    const [wasShown, previousId] = previous || [false, undefined]
    if (!wasShown || previousId !== id) {
      details.value = null
      void loadDetails(false)
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
})

const kvItems = computed<KvItem[]>(() => {
  const row = props.row
  if (!row) return []
  return [
    { label: t('page.accounts.kvId'), value: String(row.id), mono: true },
    { label: t('page.accounts.kvName'), value: row.name },
    { label: 'UID', value: row.uid || FMT_EMPTY, mono: true, copy: Boolean(row.uid) },
    { label: t('page.accounts.kvPlatform'), value: row.platform || FMT_EMPTY },
    { label: t('page.accounts.kvDomain'), value: row.domain || FMT_EMPTY },
    { label: t('page.accounts.kvStatus'), value: [row.statusLabel, row.statusTitle].filter(Boolean).join(' · ') },
    { label: t('page.accounts.kvBalance'), value: fmtInt(row.balanceRemain) + ' / ' + fmtInt(row.balanceTotal) },
    { label: t('page.accounts.kvExpire'), value: row.expireText },
    {
      label: t('page.accounts.kvLastSync'),
      value: row.raw.last_sync_at
        ? fmtRelative(row.raw.last_sync_at) + ' · ' + fmtFullTime(row.raw.last_sync_at)
        : FMT_EMPTY,
    },
    {
      label: t('page.accounts.kvLastUsed'),
      value: row.raw.last_used_at
        ? row.lastUsedText + ' · ' + fmtFullTime(row.raw.last_used_at)
        : FMT_EMPTY,
    },
    { label: t('page.accounts.kvBoundKey'), value: row.boundKeyName || FMT_EMPTY },
    {
      label: t('page.accounts.kvMaxInFlight'),
      value:
        row.raw.max_in_flight === null || row.raw.max_in_flight === undefined
          ? t('page.accounts.kvFollowGlobal')
          : String(row.raw.max_in_flight),
    },
  ]
})

interface PackageView {
  key: string
  name: string
  remain: number
  total: number
  pct: number
  expire: string
}

const packages = computed<PackageView[]>(() => {
  const list = Array.isArray(details.value?.packages) ? details.value!.packages : []
  return list.map((item, index) => {
    const remain = Number(item.remain) || 0
    const total = Math.max(Number(item.total) || 0, remain)
    return {
      key: String(index) + '-' + String(item.name || ''),
      name: item.name || t('page.accounts.detailPackage'),
      remain,
      total,
      pct: total > 0 ? Math.min(100, (remain / total) * 100) : 0,
      expire: item.deduction_end || t('page.accounts.detailLongTerm'),
    }
  })
})

const cooldowns = computed(() => props.row?.raw.model_limits || [])

const badge = computed(() =>
  props.row ? { tone: props.row.statusTone, label: props.row.statusLabel } : null,
)

const isActive = computed(() => props.row?.status === 'active')
</script>

<template>
  <DetailDrawer
    :show="show"
    :title="t('page.accounts.detailTitle', { name: row?.name || '' })"
    :badge="badge"
    :subtitle="row?.uidMasked || ''"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <template v-if="row">
      <KV :items="kvItems" label-width="96px" />

      <section class="acc-detail__section">
        <div class="acc-detail__head">
          <h3 class="acc-detail__title">{{ t('page.accounts.detailCredits') }}</h3>
          <Btn variant="quiet" size="sm" :icon="RefreshCw" :loading="loading" @click="loadDetails(true)">
            {{ t('page.accounts.detailRecollect') }}
          </Btn>
        </div>
        <div v-if="loading && !details" class="acc-detail__skeleton">
          <div v-for="index in 3" :key="index" class="acc-detail__skeleton-row mh-skeleton" />
        </div>
        <ErrorBlock v-else-if="error" :title="error" compact @retry="loadDetails(false)" />
        <Empty v-else-if="!packages.length" compact :description="t('page.accounts.detailEmpty')" />
        <ul v-else class="acc-detail__list">
          <li v-for="pkg in packages" :key="pkg.key" class="acc-detail__pkg">
            <span class="acc-detail__name" :title="pkg.name">{{ pkg.name }}</span>
            <Meter :value="pkg.pct" :height="4" tone="neutral" />
            <span class="tnum acc-detail__num">{{ fmtCredit(pkg.remain) }} / {{ fmtCredit(pkg.total) }}</span>
            <span class="acc-detail__muted">{{ pkg.expire }}</span>
          </li>
        </ul>
      </section>

      <section v-if="cooldowns.length" class="acc-detail__section">
        <div class="acc-detail__head">
          <h3 class="acc-detail__title">{{ t('page.accounts.detailCooldowns') }}</h3>
        </div>
        <ul class="acc-detail__list">
          <li v-for="limit in cooldowns" :key="limit.model + limit.kind" class="acc-detail__cool">
            <span class="acc-detail__name mono">{{ limit.model }}</span>
            <span class="acc-detail__muted">{{ limit.kind }}</span>
            <span class="tnum">{{ fmtDuration((Number(limit.remaining_seconds) || 0) * 1000) }}</span>
          </li>
        </ul>
      </section>
    </template>

    <template #footer>
      <div v-if="row" class="acc-detail__footer">
        <Btn variant="danger" size="sm" @click="emit('action', 'delete')">{{ t('common.delete') }}</Btn>
        <span class="acc-detail__spacer" />
        <Btn variant="quiet" size="sm" @click="emit('action', isActive ? 'disable' : 'enable')">
          {{ isActive ? t('page.accounts.actDisable') : t('page.accounts.actEnable') }}
        </Btn>
        <Btn v-if="row.features.checkin" variant="quiet" size="sm" @click="emit('action', 'checkin')">
          {{ t('page.accounts.actCheckin') }}
        </Btn>
        <Btn variant="quiet" size="sm" @click="emit('action', 'check')">{{ t('page.accounts.actCheck') }}</Btn>
        <Btn variant="quiet" size="sm" @click="emit('action', 'refresh')">{{ t('page.accounts.actRefresh') }}</Btn>
        <Btn variant="default" size="sm" @click="emit('action', 'edit')">{{ t('page.accounts.actEdit') }}</Btn>
      </div>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.acc-detail__section {
  margin-top: var(--sp-5);
  padding-top: var(--sp-4);
  border-top: 1px solid var(--border-soft);
}

.acc-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
}

.acc-detail__title {
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.acc-detail__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  max-height: 320px;
  overflow: auto;
}

.acc-detail__pkg,
.acc-detail__cool {
  display: grid;
  gap: var(--sp-3);
  align-items: center;
  min-height: var(--row-h-compact, 36px);
  border-bottom: 1px solid var(--border-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.acc-detail__pkg {
  grid-template-columns: minmax(0, 1.4fr) 80px minmax(0, 1fr) minmax(0, 1fr);
}

.acc-detail__cool {
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr) auto;
}

.acc-detail__pkg:last-child,
.acc-detail__cool:last-child {
  border-bottom: 0;
}

.acc-detail__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
}

.acc-detail__num {
  text-align: right;
}

.acc-detail__muted {
  color: var(--fg-muted);
}

.acc-detail__skeleton {
  display: grid;
  gap: var(--sp-2);
}

.acc-detail__skeleton-row {
  height: 12px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
}

.acc-detail__footer {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  width: 100%;
}

.acc-detail__spacer {
  flex: 1;
}
</style>
