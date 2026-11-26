<script setup lang="ts">
/**
 * S19 平台网关 /gateway（模板 A）
 *
 * 契约：docs/ui-v2/contracts/gateway.md（末尾「实现对照」是本步交付）
 * 旧版：#panel-gateway（admin/console/index.html 1454–1572）+ console-app.js 2600–2852 / 8959–9000 / 14810–14918
 * 后端：admin/platform/gateway_api.py、gateway_router.py、bench_router.py
 *
 * 排序、原因、可用性全部由后端算（gateway_router.order）；前端只展示与保存。
 * 策略切换 / 拖拽排序 / 禁用启用 / 无头分摊都是「乐观更新 + 失败回滚 + Toast」。
 *
 * r3 拆分：策略卡 → gateway/GatewayStrategyCard.vue；调度顺序 → gateway/GatewayOrderList.vue；
 * 速度表列 → gateway/speedColumns.ts。本文件只管取数、保存与布局。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NSwitch } from 'naive-ui'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  HelpTip,
  RouteRibbon,
  type RibbonLane,
  type TableRow,
} from '@/components'
import GatewayStrategyCard from './gateway/GatewayStrategyCard.vue'
import GatewayOrderList from './gateway/GatewayOrderList.vue'
import { useGatewaySpeedColumns } from './gateway/speedColumns'
import { useRevisit } from './_shared/useRevisit'
import { useConfirm, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import {
  getGatewayConfig,
  getGatewayOrder,
  getPlatformSpeed,
  priorityOf,
  priorityPayload,
  saveGatewayConfig,
  setGatewayEnabled,
  simulateRouting,
  simulationText,
  speedPayloadText,
  strategyOf,
  toSpeedRows,
  type GatewayConfig,
  type GatewayOrderItem,
  type PlatformSpeedPayload,
  type SimulatedLane,
  type SpeedRowView,
} from '@/api/gateway'

const { t } = useI18n()
const helpText = computed(() => t('page.gateway.help'))
const activityNote = computed(() => t('page.gateway.activityNote'))
const spreadText = computed(() => t('page.gateway.spreadText'))
const speedNote = computed(() => t('page.gateway.speedNote'))

const toast = useToast()
const confirm = useConfirm()
const platform = usePlatformStore()

interface ErrorState {
  message: string
  detail: string
}

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function toErrorState(error: unknown): ErrorState {
  return {
    message: errorMessage(error),
    detail: error instanceof ApiError && error.detail ? prettyDetail(error.detail) : '',
  }
}

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const config = ref<GatewayConfig | null>(null)
const order = ref<GatewayOrderItem[]>([])
const speed = ref<PlatformSpeedPayload | null>(null)

const loading = ref({ config: false, order: false, speed: false })
const configError = ref<ErrorState | null>(null)
const orderError = ref<ErrorState | null>(null)
const speedError = ref<ErrorState | null>(null)
const saving = ref<{ strategy: boolean; priority: boolean; spread: boolean }>({
  strategy: false,
  priority: false,
  spread: false,
})
const drafts = ref<Record<string, number>>({})
const simulated = ref<SimulatedLane[] | null>(null)

const firstLoading = computed(() => config.value === null && configError.value === null)
const pageError = computed(() => (config.value === null ? configError.value : null))
const isPriorityStrategy = computed(() => config.value?.strategy === 'priority')
const refreshing = computed(() => loading.value.config || loading.value.order || loading.value.speed)

async function loadConfig(): Promise<void> {
  loading.value.config = true
  configError.value = null
  try {
    config.value = await getGatewayConfig()
    syncDrafts()
  } catch (error) {
    configError.value = toErrorState(error)
    if (config.value !== null) toast.error(failureText(t('page.gateway.action.refreshConfig'), error))
  } finally {
    loading.value.config = false
  }
}

async function loadOrder(): Promise<void> {
  loading.value.order = true
  orderError.value = null
  try {
    const payload = await getGatewayOrder()
    order.value = Array.isArray(payload.items) ? payload.items : []
    syncDrafts()
  } catch (error) {
    orderError.value = toErrorState(error)
    if (config.value !== null) toast.error(failureText(t('page.gateway.action.refreshOrder'), error))
  } finally {
    loading.value.order = false
  }
}

async function loadSpeed(): Promise<void> {
  loading.value.speed = true
  speedError.value = null
  try {
    speed.value = await getPlatformSpeed(24)
  } catch (error) {
    speedError.value = toErrorState(error)
    if (speed.value !== null) toast.error(failureText(t('page.gateway.action.refreshSpeed'), error))
  } finally {
    loading.value.speed = false
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadConfig(), loadOrder(), loadSpeed()])
}

function syncDrafts(): void {
  const next: Record<string, number> = {}
  for (const row of order.value) {
    next[row.platform] = priorityOf(config.value, row.platform)
  }
  drafts.value = next
}

/* ------------------------------------------------------------------ *
 * 策略切换（立即保存，失败回滚）
 * ------------------------------------------------------------------ */

async function changeStrategy(value: string): Promise<void> {
  if (!config.value || saving.value.strategy) return
  if (value === config.value.strategy) return
  const previous = config.value.strategy
  config.value = { ...config.value, strategy: value }
  simulated.value = null
  saving.value.strategy = true
  try {
    const result = await saveGatewayConfig({ strategy: value })
    config.value = result.config
    syncDrafts()
    toast.success(
      t('page.gateway.toast.strategyChanged', {
        name: strategyOf(result.config, value)?.label || value,
      }),
    )
    await loadOrder()
  } catch (error) {
    if (config.value) config.value = { ...config.value, strategy: previous }
    toast.error(failureText(t('page.gateway.action.strategy'), error))
  } finally {
    saving.value.strategy = false
  }
}

/* ------------------------------------------------------------------ *
 * 优先级保存（输入 / 拖拽 / 键盘移动共用）
 * ------------------------------------------------------------------ */

async function savePriority(next: Record<string, number>, message: string): Promise<void> {
  saving.value.priority = true
  try {
    const result = await saveGatewayConfig({ priority: next })
    config.value = result.config
    syncDrafts()
    toast.success(message)
    await loadOrder()
  } catch (error) {
    toast.error(failureText(t('page.gateway.action.savePriority'), error))
    await Promise.all([loadConfig(), loadOrder()])
  } finally {
    saving.value.priority = false
  }
}

function setDraft(platformId: string, value: number): void {
  drafts.value = { ...drafts.value, [platformId]: value }
}

function commitPriority(platformId: string): void {
  const raw = drafts.value[platformId]
  if (typeof raw !== 'number' || !Number.isFinite(raw)) {
    syncDrafts()
    return
  }
  const value = Math.max(0, Math.min(9999, Math.round(raw)))
  if (priorityOf(config.value, platformId) === value) return
  const next = { ...(config.value?.priority || {}), [platformId]: value }
  void savePriority(next, t('page.gateway.toast.prioritySaved'))
}

function setOrder(rows: GatewayOrderItem[]): void {
  order.value = rows
}

async function commitOrder(): Promise<void> {
  if (!isPriorityStrategy.value) return
  await savePriority(priorityPayload(order.value), t('page.gateway.toast.orderSaved'))
}

/* ------------------------------------------------------------------ *
 * 禁用 / 启用
 * ------------------------------------------------------------------ */

async function togglePlatform(row: GatewayOrderItem): Promise<void> {
  const enabling = Boolean(row.disabled)
  if (!enabling) {
    const ok = await confirm({
      title: t('page.gateway.confirmDisable.title'),
      message: t('page.gateway.confirmDisable.body', { name: row.label }),
      okText: t('page.gateway.confirmDisable.ok'),
      danger: true,
    })
    if (!ok) return
  }
  try {
    await setGatewayEnabled(row.platform, enabling, t('page.gateway.toggleReason'))
    toast.success(enabling ? t('page.gateway.toast.enabled') : t('page.gateway.toast.disabled'))
    await Promise.all([loadConfig(), loadOrder()])
  } catch (error) {
    toast.error(
      failureText(enabling ? t('page.gateway.action.enable') : t('page.gateway.action.disable'), error),
    )
  }
}

/* ------------------------------------------------------------------ *
 * 无头请求分摊
 * ------------------------------------------------------------------ */

async function toggleSpread(value: boolean): Promise<void> {
  if (!config.value || saving.value.spread) return
  const previous = config.value.spread_unbound
  config.value = { ...config.value, spread_unbound: value }
  saving.value.spread = true
  try {
    const result = await saveGatewayConfig({ spread_unbound: value })
    config.value = result.config
    toast.success(value ? t('page.gateway.toast.spreadOn') : t('page.gateway.toast.spreadOff'))
  } catch (error) {
    if (config.value) config.value = { ...config.value, spread_unbound: previous }
    toast.error(failureText(t('page.gateway.action.saveSpread'), error))
  } finally {
    saving.value.spread = false
  }
}

/* ------------------------------------------------------------------ *
 * 调度预演
 * ------------------------------------------------------------------ */

const speedRows = computed<SpeedRowView[]>(() => toSpeedRows(speed.value?.items))
const speedTableRows = computed<TableRow[]>(() => speedRows.value as unknown as TableRow[])
const speedHint = computed(() =>
  speed.value ? speedPayloadText(speed.value, t) : t('page.gateway.speedHintFallback'),
)
const speedColumns = useGatewaySpeedColumns(t, speed)

const speedMap = computed<Record<string, SpeedRowView>>(() => {
  const map: Record<string, SpeedRowView> = {}
  for (const row of speedRows.value) map[row.platform] = row
  return map
})

const simLanes = computed<SimulatedLane[]>(() => {
  if (simulated.value) return simulated.value
  return simulateRouting(order.value, config.value?.strategy || 'priority', 100)
})

const ribbonLanes = computed<RibbonLane[]>(() =>
  simLanes.value.map((lane, index) => ({
    id: lane.platform,
    name: lane.label,
    logo: lane.platform,
    count: lane.count,
    successRate: speedMap.value[lane.platform]?.success_rate ?? null,
    disabled: lane.disabled,
    preferred: index === 0,
  })),
)

const simSource = computed(() => ({ label: t('page.gateway.simSource'), total: 100 }))
const simText = computed(() => simulationText(simLanes.value, 100, t))

function runSimulation(): void {
  simulated.value = simulateRouting(order.value, config.value?.strategy || 'priority', 100)
  toast.success(t('page.gateway.toast.simulated'))
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let stopPlatformWatch: (() => void) | null = null

onMounted(() => {
  void loadAll()
  // 平台上下文订阅：网关配置是全局的，切平台重取不会改变数据，只保持页面与外壳一致
  // （platform-context.md §6.4 记录 gateway 与平台无关）。
  stopPlatformWatch = platform.onChange(() => {
    void loadAll()
  })
})

// 二次进入（KeepAlive 命中）：保留旧数据，后台静默刷新，不出骨架（11-polish-motion §2）。
useRevisit(() => {
  if (config.value !== null) void loadAll()
})

onBeforeUnmount(() => {
  stopPlatformWatch?.()
  stopPlatformWatch = null
})
</script>

<template>
  <div class="gateway-page">
    <PageHeader :title="t('page.gateway.title')" :help="helpText">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="refreshing" @click="loadAll">
          {{ t('common.refresh') }}
        </Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="pageError"
      :title="t('page.gateway.loadFailed')"
      :detail="pageError.detail"
      @retry="loadAll"
    />

    <!-- 首屏骨架：形状与真实布局一致（策略条 / 左顺序 + 右预演 / 两张卡），数据到来不跳动。 -->
    <div v-else-if="firstLoading" class="gw-skeleton" aria-busy="true" :aria-label="t('common.loading')">
      <div class="gw-sk-card">
        <span class="mh-skeleton gw-sk gw-sk--title" />
        <span class="mh-skeleton gw-sk gw-sk--bar" />
      </div>
      <div class="gw-split">
        <div class="gw-sk-card">
          <span class="mh-skeleton gw-sk gw-sk--title" />
          <span v-for="index in 5" :key="index" class="mh-skeleton gw-sk gw-sk--row" />
        </div>
        <div class="gw-sk-card">
          <span class="mh-skeleton gw-sk gw-sk--title" />
          <span class="mh-skeleton gw-sk gw-sk--ribbon" />
        </div>
      </div>
    </div>

    <template v-else>
      <GatewayStrategyCard
        :config="config"
        :loading="loading.config || saving.strategy"
        :note="activityNote"
        @change="changeStrategy"
      />

      <div class="gw-split">
        <Card class="gw-order-card" :loading="loading.order || saving.priority">
          <template #title>{{ t('page.gateway.order.title') }}</template>
          <template #extra>
            <span class="gw-sub">
              {{ isPriorityStrategy ? t('page.gateway.order.hintEditable') : t('page.gateway.order.hintReadonly') }}
            </span>
            <HelpTip :content="t('page.gateway.order.helpContent')" :label="t('page.gateway.order.helpLabel')" />
          </template>

          <ErrorBlock
            v-if="orderError"
            compact
            :title="t('page.gateway.order.loadFailed', { reason: orderError.message })"
            :detail="orderError.detail"
            @retry="loadOrder"
          />
          <Empty v-else-if="!order.length" :description="t('page.gateway.order.empty')" />
          <GatewayOrderList
            v-else
            :order="order"
            :config="config"
            :drafts="drafts"
            :editable="isPriorityStrategy"
            :saving="saving.priority"
            @update:order="setOrder"
            @commit-order="commitOrder"
            @update:draft="setDraft"
            @commit-priority="commitPriority"
            @toggle="togglePlatform"
          />
        </Card>

        <Card class="gw-sim-card" :loading="loading.order || loading.speed">
          <template #title>{{ t('page.gateway.sim.title') }}</template>
          <template #extra>
            <span class="gw-sub">{{ t('page.gateway.sim.hint') }}</span>
          </template>

          <div class="gw-sim__stage">
            <RouteRibbon
              v-if="ribbonLanes.length"
              :source="simSource"
              :lanes="ribbonLanes"
              :unit="t('page.gateway.sim.unit')"
            />
            <Empty v-else compact :description="t('page.gateway.sim.empty')" />
          </div>

          <p class="gw-sim__text">{{ simText }}</p>
          <div class="gw-sim__actions">
            <Btn variant="primary" :disabled="!order.length" @click="runSimulation">
              {{ t('page.gateway.sim.run') }}
            </Btn>
            <span class="gw-sub">{{ t('page.gateway.sim.note') }}</span>
          </div>
        </Card>
      </div>

      <Card class="gw-spread-card" :loading="saving.spread">
        <template #title>{{ t('page.gateway.spread.title') }}</template>
        <template #extra>
          <span class="gw-sub">{{ t('page.gateway.spread.hint') }}</span>
        </template>
        <div class="gw-spread">
          <div class="gw-spread__text">
            <strong>{{ t('page.gateway.spread.switch') }}</strong>
            <p class="gw-sub">{{ spreadText }}</p>
          </div>
          <NSwitch
            :value="config?.spread_unbound === true"
            :loading="saving.spread"
            :disabled="!config || saving.spread"
            :aria-label="t('page.gateway.spread.aria')"
            @update:value="toggleSpread"
          />
        </div>
      </Card>

      <Card class="gw-speed-card" :loading="loading.speed && !!speed">
        <template #title>{{ t('page.gateway.speed.title') }}</template>
        <template #extra><span class="gw-sub">{{ speedHint }}</span></template>

        <ErrorBlock
          v-if="speedError"
          compact
          :title="t('page.gateway.speed.loadFailed', { reason: speedError.message })"
          :detail="speedError.detail"
          @retry="loadSpeed"
        />
        <div v-else-if="loading.speed && !speed" class="gw-sk-rows" aria-busy="true">
          <span v-for="index in 4" :key="index" class="mh-skeleton gw-sk gw-sk--row" />
        </div>
        <Empty v-else-if="!speedRows.length" compact :description="t('page.gateway.speed.empty')" />
        <template v-else>
          <DataTable
            table-id="gateway-speed"
            :columns="speedColumns"
            :data="speedTableRows"
            row-key="platform"
            :pagination="false"
            :virtual-scroll="false"
            :empty-description="t('page.gateway.speed.empty')"
          />
          <p class="gw-note">{{ speedNote }}</p>
        </template>
      </Card>
    </template>
  </div>
</template>

<style scoped>
.gateway-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.gw-sub {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.gw-note {
  margin: var(--sp-3) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 78ch;
}

.gw-split {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: var(--sp-4);
  align-items: start;
}

/* 骨架（只用 .mh-skeleton 一种，10 §1.7） */
.gw-skeleton {
  display: grid;
  gap: var(--sp-4);
}

.gw-sk-card {
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  box-shadow: var(--shadow-1);
}

.gw-sk-rows {
  display: grid;
  gap: var(--sp-2);
}

.gw-sk {
  display: block;
  border-radius: var(--r-ctl);
}

.gw-sk--title { height: 20px; width: 140px; }
.gw-sk--bar { height: 44px; }
.gw-sk--row { height: 36px; }
.gw-sk--ribbon { height: 240px; border-radius: var(--r-card); }

/* 预演舞台：固定最小高度占位，数据到来不跳（11 §2 CLS≈0） */
.gw-sim__stage {
  min-height: 220px;
  display: grid;
  align-items: center;
}

.gw-sim__text {
  margin: var(--sp-3) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.gw-sim__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  margin-top: var(--sp-3);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}

.gw-spread {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--sp-4);
  align-items: center;
}

.gw-spread__text strong {
  display: block;
  font-size: var(--fs-body);
  font-weight: 500;
  color: var(--fg);
  margin-bottom: 2px;
}

.gw-spread__text p {
  margin: 0;
  max-width: 80ch;
  line-height: var(--lh-small);
  text-wrap: pretty;
}

/* 速度表单元格（DataTable 渲染，需 :deep） */
:deep(.gw-speed-name) {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

:deep(.gw-speed-name__text) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.gw-sub) {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

@media (max-width: 1280px) {
  .gw-split {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
