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
 */
import { computed, h, onBeforeUnmount, onMounted, ref, type VNodeChild } from 'vue'
import { NInputNumber, NSkeleton, NSwitch } from 'naive-ui'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  HelpTip,
  PlatformLogo,
  RouteRibbon,
  type RibbonLane,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import { fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
import {
  formatBalance,
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
  strategyDescription,
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
const refreshing = computed(() => loading.value.config || loading.value.order)

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
 * 优先级保存（输入 / 拖拽共用）
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

/* ------------------------------------------------------------------ *
 * 原生拖拽排序（仅「优先级」策略）
 * ------------------------------------------------------------------ */

const dragIndex = ref<number | null>(null)

function canDrag(row: GatewayOrderItem): boolean {
  return isPriorityStrategy.value && Boolean(row.available) && !row.disabled
}

function onDragStart(index: number, event: DragEvent): void {
  if (!isPriorityStrategy.value) return
  dragIndex.value = index
  try {
    event.dataTransfer?.setData('text/plain', String(index))
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  } catch {
    /* 老 WebView 忽略 */
  }
}

function onDragOver(index: number, event: DragEvent): void {
  if (dragIndex.value === null || dragIndex.value === index) return
  const from = dragIndex.value
  if (!canDrag(order.value[index]) && !canDrag(order.value[from])) return
  event.preventDefault()
  const rows = [...order.value]
  const [moved] = rows.splice(from, 1)
  if (!moved) return
  rows.splice(index, 0, moved)
  order.value = rows
  dragIndex.value = index
}

async function onDragEnd(): Promise<void> {
  if (dragIndex.value === null) return
  dragIndex.value = null
  const next = priorityPayload(order.value)
  await savePriority(next, t('page.gateway.toast.orderSaved'))
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
    toast.success(
      enabling ? t('page.gateway.toast.enabled') : t('page.gateway.toast.disabled'),
    )
    await Promise.all([loadConfig(), loadOrder()])
  } catch (error) {
    toast.error(
      failureText(
        enabling ? t('page.gateway.action.enable') : t('page.gateway.action.disable'),
        error,
      ),
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
 * 平台速度表
 * ------------------------------------------------------------------ */

const speedRows = computed<SpeedRowView[]>(() => toSpeedRows(speed.value?.items))
const speedTableRows = computed<TableRow[]>(() => speedRows.value as unknown as TableRow[])
const speedHint = computed(() =>
  speed.value ? speedPayloadText(speed.value, t) : t('page.gateway.speedHintFallback'),
)

function speedPlatform(row: TableRow): VNodeChild {
  const item = row as unknown as SpeedRowView
  const fastest = speed.value?.fastest === item.platform
  return h('span', { class: 'gw-speed-name' }, [
    h(PlatformLogo, { id: item.platform, size: 18 }),
    h('span', null, item.label || item.platform),
    fastest ? h(Badge, { tone: 'ok' }, { default: () => t('page.gateway.speed.fastest') }) : null,
    item.low_sample
      ? h(Badge, { tone: 'neutral' }, { default: () => t('page.gateway.speed.lowSample') })
      : null,
  ])
}

function speedRate(row: TableRow): VNodeChild {
  const item = row as unknown as SpeedRowView
  return h(Badge, { tone: item.tone }, { default: () => item.successText })
}

const speedColumns = computed<TableColumn[]>(() => [
  {
    key: 'platform',
    title: t('page.gateway.speed.platform'),
    mobile: 'title',
    minWidth: 200,
    render: speedPlatform,
  },
  {
    key: 'success',
    title: t('page.gateway.speed.successRate'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    render: speedRate,
  },
  {
    key: 'ttfb',
    title: t('page.gateway.speed.ttfb'),
    mobile: 'meta',
    width: 108,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'tnum' }, (row as unknown as SpeedRowView).ttfbText),
  },
  {
    key: 'tps',
    title: t('page.gateway.speed.tps'),
    mobile: 'meta',
    width: 116,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'tnum' }, (row as unknown as SpeedRowView).tpsText),
  },
  {
    key: 'latency',
    title: t('page.gateway.speed.latency'),
    mobile: 'meta',
    width: 116,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'tnum' }, (row as unknown as SpeedRowView).latencyText),
  },
  {
    key: 'samples',
    title: t('page.gateway.speed.samples'),
    mobile: 'meta',
    width: 84,
    numeric: true,
    render: (row: TableRow) =>
      h('span', { class: 'gw-sub tnum' }, fmtInt((row as unknown as SpeedRowView).requests)),
  },
])

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

    <div v-else-if="firstLoading" class="gw-skeleton" aria-busy="true">
      <NSkeleton v-for="index in 6" :key="index" height="44px" />
    </div>

    <template v-else>
      <!-- 调度策略 -->
      <Card class="gw-strategy-card" :loading="loading.config || saving.strategy">
        <template #title>{{ t('page.gateway.strategy.title') }}</template>
        <template #extra>
          <span class="gw-sub">{{ t('page.gateway.strategy.hint') }}</span>
        </template>

        <div
          v-if="config"
          class="gw-strategy-grid"
          role="radiogroup"
          :aria-label="t('page.gateway.strategy.title')"
        >
          <button
            v-for="item in config.strategies"
            :key="item.value"
            type="button"
            role="radio"
            class="gw-strategy"
            :class="{ active: config.strategy === item.value }"
            :aria-checked="config.strategy === item.value"
            @click="changeStrategy(item.value)"
          >
            <span class="gw-strategy__name">{{ item.label }}</span>
            <span class="gw-strategy__desc">{{ item.description }}</span>
          </button>
        </div>
        <p v-if="config" class="gw-strategy__detail">
          {{ strategyDescription(config) }}
        </p>
        <p class="gw-note">{{ activityNote }}</p>
      </Card>

      <div class="gw-split">
        <!-- 当前调度顺序 -->
        <Card class="gw-order-card" :loading="loading.order || saving.priority">
          <template #title>{{ t('page.gateway.order.title') }}</template>
          <template #extra>
            <span class="gw-sub">
              {{
                isPriorityStrategy
                  ? t('page.gateway.order.hintEditable')
                  : t('page.gateway.order.hintReadonly')
              }}
            </span>
            <HelpTip
              :content="t('page.gateway.order.helpContent')"
              :label="t('page.gateway.order.helpLabel')"
            />
          </template>

          <ErrorBlock
            v-if="orderError"
            compact
            :title="t('page.gateway.order.loadFailed', { reason: orderError.message })"
            :detail="orderError.detail"
            @retry="loadOrder"
          />
          <Empty
            v-else-if="!order.length"
            :description="t('page.gateway.order.empty')"
          />
          <div v-else class="gw-order">
            <div
              v-for="(row, index) in order"
              :key="row.platform"
              class="gw-order-row"
              :class="{ 'is-disabled': row.disabled || !row.available, 'is-draggable': canDrag(row) }"
              :draggable="canDrag(row)"
              @dragstart="onDragStart(index, $event)"
              @dragover="onDragOver(index, $event)"
              @dragend="onDragEnd"
            >
              <span class="gw-order__rank">{{ index + 1 }}</span>
              <span v-if="isPriorityStrategy" class="gw-order__handle" aria-hidden="true">⠿</span>
              <span v-else class="gw-order__handle gw-order__handle--off" aria-hidden="true">·</span>
              <PlatformLogo :id="row.platform" :size="18" :label="row.label" />
              <span class="gw-order__name">{{ row.label || row.platform }}</span>

              <span class="gw-order__priority">
                <template v-if="isPriorityStrategy">
                  <NInputNumber
                    class="gw-priority"
                    size="small"
                    :min="0"
                    :max="9999"
                    :show-button="false"
                    :value="drafts[row.platform]"
                    :disabled="saving.priority"
                    :aria-label="
                      t('page.gateway.order.priorityAria', { name: row.label || row.platform })
                    "
                    @update:value="(value: number | null) => (drafts[row.platform] = typeof value === 'number' ? value : 0)"
                    @blur="commitPriority(row.platform)"
                    @keyup.enter="commitPriority(row.platform)"
                  />
                  <span class="gw-sub">{{ t('page.gateway.order.priority') }}</span>
                </template>
                <span v-else class="gw-sub">
                  {{ t('page.gateway.order.priorityValue', { value: priorityOf(config, row.platform) }) }}
                </span>
              </span>

              <span class="gw-order__meta">
                {{
                  t('page.gateway.order.meta', {
                    usable: row.usable,
                    balance: formatBalance(row.balance_remain),
                  })
                }}
              </span>
              <span class="gw-order__reason">
                <Badge v-if="row.disabled" tone="danger" dot>
                  {{ t('page.gateway.order.disabled') }}
                </Badge>
                <Badge v-else-if="!row.available" tone="neutral">
                  {{ t('page.gateway.order.noAccounts') }}
                </Badge>
                <span class="gw-sub">{{ row.reason || '—' }}</span>
              </span>
              <Btn
                variant="quiet"
                size="sm"
                :disabled="saving.priority"
                @click="togglePlatform(row)"
              >
                {{ row.disabled ? t('page.gateway.action.enable') : t('page.gateway.action.disable') }}
              </Btn>
            </div>
          </div>
        </Card>

        <!-- 调度预演 -->
        <Card class="gw-sim-card" :loading="loading.order || loading.speed">
          <template #title>{{ t('page.gateway.sim.title') }}</template>
          <template #extra>
            <span class="gw-sub">{{ t('page.gateway.sim.hint') }}</span>
          </template>

          <RouteRibbon
            v-if="ribbonLanes.length"
            :source="simSource"
            :lanes="ribbonLanes"
            :unit="t('page.gateway.sim.unit')"
          />
          <Empty v-else :description="t('page.gateway.sim.empty')" />

          <p class="gw-sim__text">{{ simText }}</p>
          <div class="gw-sim__actions">
            <Btn variant="primary" :disabled="!order.length" @click="runSimulation">
              {{ t('page.gateway.sim.run') }}
            </Btn>
            <span class="gw-sub">{{ t('page.gateway.sim.note') }}</span>
          </div>
        </Card>
      </div>

      <!-- 无头请求 -->
      <Card class="gw-spread-card" :loading="saving.spread">
        <template #title>{{ t('page.gateway.spread.title') }}</template>
        <template #extra>
          <span class="gw-sub">{{ t('page.gateway.spread.hint') }}</span>
        </template>
        <div class="gw-spread">
          <NSwitch
            :value="config?.spread_unbound === true"
            :loading="saving.spread"
            :disabled="!config || saving.spread"
            :aria-label="t('page.gateway.spread.aria')"
            @update:value="toggleSpread"
          />
          <div class="gw-spread__text">
            <strong>{{ t('page.gateway.spread.switch') }}</strong>
            <p class="gw-sub">{{ spreadText }}</p>
          </div>
        </div>
      </Card>

      <!-- 平台速度 -->
      <Card class="gw-speed-card" :loading="loading.speed">
        <template #title>{{ t('page.gateway.speed.title') }}</template>
        <template #extra><span class="gw-sub">{{ speedHint }}</span></template>

        <ErrorBlock
          v-if="speedError"
          compact
          :title="t('page.gateway.speed.loadFailed', { reason: speedError.message })"
          :detail="speedError.detail"
          @retry="loadSpeed"
        />
        <div v-else-if="loading.speed && !speed" class="gw-skeleton" aria-busy="true">
          <NSkeleton v-for="index in 4" :key="index" height="40px" />
        </div>
        <Empty v-else-if="!speedRows.length" :description="t('page.gateway.speed.empty')" />
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
  margin-top: var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 78ch;
}

.gw-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.gw-strategy-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: var(--sp-2);
}

.gw-strategy {
  display: grid;
  gap: 4px;
  align-content: start;
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-ctl);
  background: var(--surface);
  text-align: left;
  cursor: pointer;
  transition: border-color var(--t-base) var(--ease), background var(--t-base) var(--ease);
}

.gw-strategy:hover {
  border-color: var(--border-strong);
  background: var(--surface-hover);
}

.gw-strategy.active {
  border-color: var(--brand);
  background: var(--brand-soft);
}

.gw-strategy__name {
  font-size: var(--fs-body);
  font-weight: 600;
  color: var(--fg);
}

.gw-strategy.active .gw-strategy__name {
  color: var(--brand);
}

.gw-strategy__desc {
  font-size: var(--fs-tiny);
  line-height: 1.5;
  color: var(--fg-muted);
}

.gw-strategy__detail {
  margin-top: var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.gw-split {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: var(--sp-4);
  align-items: start;
}

.gw-order {
  display: grid;
  gap: var(--sp-1);
}

.gw-order-row {
  display: grid;
  grid-template-columns: 24px 16px 18px minmax(0, 1fr) 108px 132px minmax(0, 1.1fr) auto;
  align-items: center;
  gap: var(--sp-2);
  min-height: 44px;
  padding: 0 var(--sp-2);
  border-radius: var(--r-ctl);
  border: 1px solid transparent;
}

.gw-order-row:hover {
  background: var(--surface-hover);
}

.gw-order-row.is-disabled {
  color: var(--fg-muted);
  opacity: 0.72;
}

.gw-order-row.is-draggable {
  cursor: grab;
}

.gw-order-row.is-draggable:active {
  cursor: grabbing;
}

.gw-order__rank {
  font-size: var(--fs-small);
  color: var(--fg-faint);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.gw-order__handle {
  color: var(--fg-faint);
  letter-spacing: -2px;
  user-select: none;
}

.gw-order__handle--off {
  color: transparent;
}

.gw-order__name {
  font-size: var(--fs-body);
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gw-order__priority {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
}

.gw-priority {
  width: 72px;
}

.gw-order__meta {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.gw-order__reason {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
  overflow: hidden;
}

.gw-order__reason .gw-sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gw-sim__text {
  margin-top: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.gw-sim__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  margin-top: var(--sp-2);
}

.gw-spread {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--sp-3);
  align-items: start;
}

.gw-spread__text strong {
  display: block;
  font-size: var(--fs-body);
  color: var(--fg);
  margin-bottom: var(--sp-1);
}

.gw-speed-name {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

@media (max-width: 1280px) {
  .gw-strategy-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .gw-split {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 760px) {
  .gw-strategy-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .gw-order-row {
    grid-template-columns: 24px 16px 18px minmax(0, 1fr) auto;
    padding: var(--sp-2);
  }

  .gw-order__priority,
  .gw-order__meta,
  .gw-order__reason {
    grid-column: 2 / -1;
  }
}
</style>