<script setup lang="ts">
/**
 * S30 出口代理 /proxy（issue #39）
 *
 * 对话主链路的全局出口，一页说清四件事：
 *   1. 现在走不走代理（开关 + 当前节点 + 出口地址）；
 *   2. 怎么换（自动轮换周期 / 每条请求换 / 立即轮换 / 手动锁定某个节点）；
 *   3. 每个节点通不通（真拨一次，回延迟与**出口 IP**）；
 *   4. 节点怎么管（增删、启停、解除静默、单节点并发、分组）。
 *
 * 口径（和 admin/proxy_egress.py 一致）：
 *   · 账号绑定优先于全局出口；关掉全局出口 = 直连，不动任何账号配置；
 *   · 全局开着但没有可用节点时**不会假装在走代理**：面板上直接给告警；
 *   · 锁定的节点掉线会顺延到下一个可用节点，绝不静默退回本机 IP。
 */
import { computed, h, onMounted, ref } from 'vue'
import { NInput, NInputNumber, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import {
  Activity,
  ArrowLeftRight,
  CircleCheck,
  Globe2,
  Plus,
  RefreshCw,
  ShieldAlert,
  Waypoints,
  Zap,
} from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  DataTable,
  Empty,
  ErrorBlock,
  FormDrawer,
  FormField,
  KpiCard,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useRevisit, useToast } from '@/composables'
import { failureText } from '@/utils/error'
import { fmtInt, fmtTime } from '@/utils/format'
import { useI18n } from '@/i18n'
import {
  createProxy,
  deleteProxy,
  getProxyEgress,
  listProxies,
  patchProxy,
  proxyStateOf,
  rotateProxyEgress,
  saveProxyEgress,
  testProxyNode,
  unblockProxy,
  validateProxyInput,
  type ProxyEgressPayload,
  type ProxyEgressSettings,
  type ProxyListPayload,
  type ProxyNode,
  type ProxyProbeResult,
} from '@/api/settings'

const { t } = useI18n()
const toast = useToast()
const confirm = useConfirm()
const helpText = computed(() => t('page.proxy.help'))

/* ------------------------------------------------------------------ *
 * 数据
 * ------------------------------------------------------------------ */

const nodesPayload = ref<ProxyListPayload | null>(null)
const egress = ref<ProxyEgressPayload | null>(null)
const loading = ref(false)
const refreshing = ref(false)
const loadError = ref('')
const probingId = ref(0)
const egressBusy = ref(false)
/** 「测试当前出口」的结果单独放：它不属于某一个表格行。 */
const currentProbe = ref<ProxyProbeResult | null>(null)

const nodes = computed<ProxyNode[]>(() => nodesPayload.value?.items || [])
const firstLoading = computed(() => loading.value && !nodesPayload.value)

async function loadAll(silent = false): Promise<void> {
  if (silent) refreshing.value = true
  else loading.value = true
  loadError.value = ''
  try {
    const [list, status] = await Promise.all([listProxies(), getProxyEgress()])
    nodesPayload.value = list
    egress.value = status
  } catch (error) {
    loadError.value = failureText(t('page.proxy.loadFailed'), error)
    if (nodesPayload.value) toast.error(loadError.value)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

useRevisit(() => loadAll(true), { minIntervalMs: 10_000 })
onMounted(() => {
  void loadAll()
})

/* ------------------------------------------------------------------ *
 * 全局出口
 * ------------------------------------------------------------------ */

const egressEnabled = computed(() => Number(egress.value?.settings?.enabled || 0) === 1)
const egressMode = computed(() => String(egress.value?.settings?.mode || 'auto'))
const egressSeconds = computed(() => Number(egress.value?.settings?.rotate_seconds || 300))
const egressPerRequest = computed(() => Number(egress.value?.settings?.rotate_per_request || 0) === 1)
const egressPool = computed(() => String(egress.value?.settings?.pool || ''))
const current = computed(() => egress.value?.current || null)
const warnings = computed(() => egress.value?.warnings || [])
const nextRotate = computed(() => egress.value?.next_rotate_in ?? null)

const poolOptions = computed(() => [
  { label: t('page.proxy.poolAll'), value: '' },
  ...(nodesPayload.value?.pools || []).map((pool) => ({
    label: pool || t('page.proxy.poolDefault'),
    value: pool,
  })),
])

const currentText = computed(() => {
  if (!egressEnabled.value) return t('page.proxy.offHint')
  const node = current.value
  if (!node) return t('page.proxy.noCurrent')
  return t('page.proxy.currentText', {
    label: node.label || '#' + node.endpoint_id,
    url: node.url || '-',
  })
})

const metaText = computed(() => {
  const runtime = egress.value?.runtime
  if (!runtime) return ''
  const parts = [
    t('page.proxy.metaRotations', { count: runtime.rotations }),
    t('page.proxy.metaSessions', { count: runtime.requests }),
  ]
  if (egressEnabled.value) {
    if (egressPerRequest.value) parts.push(t('page.proxy.metaPerRequest'))
    else if (nextRotate.value !== null) parts.push(t('page.proxy.metaNext', { seconds: nextRotate.value }))
  }
  if (runtime.last_ok_at) parts.push(t('page.proxy.metaLastOk', { time: fmtTime(runtime.last_ok_at) }))
  if (runtime.waf_blocked) parts.push(t('page.proxy.metaWaf'))
  return parts.join(' · ')
})

const probeText = computed(() => {
  const probe = currentProbe.value
  if (!probe) return ''
  if (probe.ok) {
    return t('page.proxy.probeOk', {
      latency: probe.latency_ms ?? '-',
      ip: probe.egress_ip || '-',
      target: probe.target || '',
    })
  }
  return t('page.proxy.probeFail', { reason: probe.error || '-' })
})

async function patchEgress(patch: Partial<ProxyEgressSettings>): Promise<void> {
  if (egressBusy.value) return
  egressBusy.value = true
  try {
    egress.value = await saveProxyEgress(patch)
    await refreshList()
  } catch (error) {
    toast.error(failureText(t('page.proxy.saveFailed'), error))
  } finally {
    egressBusy.value = false
  }
}

async function refreshList(): Promise<void> {
  try {
    nodesPayload.value = await listProxies()
  } catch {
    /* 列表刷新失败保留旧数据，下一次 revisit 会重试 */
  }
}

async function toggleEgress(value: boolean): Promise<void> {
  currentProbe.value = null
  await patchEgress({ enabled: value ? 1 : 0 })
  if (value) toast.success(t('page.proxy.enabledToast'))
  else toast.success(t('page.proxy.disabledToast'))
}

async function setMode(mode: string): Promise<void> {
  await patchEgress({ mode })
}

async function setPool(pool: string): Promise<void> {
  await patchEgress({ pool: pool || '' })
}

async function setSeconds(value: number | null): Promise<void> {
  await patchEgress({ rotate_seconds: Math.max(5, Math.min(86400, Number(value || 300))) })
}

async function setPerRequest(value: boolean): Promise<void> {
  await patchEgress({ rotate_per_request: value ? 1 : 0 })
}

async function rotateNow(): Promise<void> {
  if (egressBusy.value) return
  egressBusy.value = true
  try {
    const result = await rotateProxyEgress()
    egress.value = result.status
    toast.success(t('page.proxy.rotated', { label: result.current?.label || '-' }))
    await refreshList()
  } catch (error) {
    toast.error(failureText(t('page.proxy.rotateFailed'), error))
  } finally {
    egressBusy.value = false
  }
}

async function testCurrent(): Promise<void> {
  const id = current.value?.endpoint_id || 0
  if (!id) {
    toast.warning(t('page.proxy.noCurrent'))
    return
  }
  await probeNode(id, { keepCurrent: true })
}

async function pinNode(node: ProxyNode): Promise<void> {
  await patchEgress({ mode: 'fixed', endpoint_id: node.id })
  toast.success(t('page.proxy.pinned', { label: node.label || '#' + node.id }))
}

/* ------------------------------------------------------------------ *
 * 节点测试
 * ------------------------------------------------------------------ */

async function probeNode(id: number, opts: { keepCurrent?: boolean } = {}): Promise<void> {
  if (probingId.value) return
  probingId.value = id
  try {
    const result = await testProxyNode(id)
    // 行内测试不覆盖顶部「当前出口」的体检结论；顶部按钮的测试才写回。
    if (opts.keepCurrent) currentProbe.value = result.probe || null
    if (result.ok) {
      toast.success(t('page.proxy.probeOkToast', { latency: result.probe?.latency_ms ?? '-' }))
    } else {
      toast.error(t('page.proxy.probeFailToast', { reason: result.error || '-' }))
    }
    await loadAll(true)
  } catch (error) {
    toast.error(failureText(t('page.proxy.probeFailed'), error))
  } finally {
    probingId.value = 0
  }
}

/* ------------------------------------------------------------------ *
 * 节点增删改
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const submitting = ref(false)
const draft = ref({ label: '', url: '', max_concurrency: 1, pool: '' })
const draftProbe = ref<ProxyProbeResult | null>(null)

function openDrawer(): void {
  draft.value = { label: '', url: '', max_concurrency: 1, pool: egressPool.value }
  draftProbe.value = null
  drawerOpen.value = true
}

async function submitNode(): Promise<void> {
  const error = validateProxyInput(draft.value, t)
  if (error) {
    toast.error(error)
    return
  }
  submitting.value = true
  try {
    await createProxy({ ...draft.value })
    drawerOpen.value = false
    toast.success(t('page.proxy.added'))
    await loadAll(true)
  } catch (err) {
    toast.error(failureText(t('page.proxy.addFailed'), err))
  } finally {
    submitting.value = false
  }
}

async function toggleNode(node: ProxyNode): Promise<void> {
  try {
    await patchProxy(node.id, { enabled: node.enabled ? 0 : 1 })
    toast.success(node.enabled ? t('page.proxy.disabledNode') : t('page.proxy.enabledNode'))
    await loadAll(true)
  } catch (error) {
    toast.error(failureText(t('page.proxy.toggleFailed'), error))
  }
}

async function unblockNode(node: ProxyNode): Promise<void> {
  try {
    await unblockProxy(node.id)
    toast.success(t('page.proxy.unblocked'))
    await loadAll(true)
  } catch (error) {
    toast.error(failureText(t('page.proxy.unblockFailed'), error))
  }
}

async function removeNode(node: ProxyNode): Promise<void> {
  const ok = await confirm({
    title: t('page.proxy.deleteTitle'),
    message: t('page.proxy.deleteBody', { label: node.label || '#' + node.id }),
    okText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  try {
    await deleteProxy(node.id)
    toast.success(t('page.proxy.deleted'))
    await loadAll(true)
  } catch (error) {
    toast.error(failureText(t('page.proxy.deleteFailed'), error))
  }
}

/* ------------------------------------------------------------------ *
 * 表格
 * ------------------------------------------------------------------ */

function probeCell(node: ProxyNode): { text: string; tone: 'ok' | 'warn' | 'neutral' } {
  const probe = node.probe
  if (!probe) return { text: '—', tone: 'neutral' }
  if (probe.ok) return { text: t('page.proxy.latency', { latency: probe.latency_ms ?? '-' }), tone: 'ok' }
  return { text: t('page.proxy.probeBad'), tone: 'warn' }
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'label',
    title: t('page.proxy.colName'),
    minWidth: 160,
    mobile: 'title',
    render: (row: TableRow) => {
      const node = row as unknown as ProxyNode
      const name = node.label || '#' + node.id
      if (!node.current) return name
      return h('span', { class: 'proxy-current' }, [
        name,
        h(Badge, { tone: 'ok' }, { default: () => t('page.proxy.currentTag') }),
      ])
    },
  },
  {
    key: 'url',
    title: t('page.proxy.colUrl'),
    minWidth: 220,
    mobile: 'subtitle',
    render: (row: TableRow) => h('span', { class: 'mono' }, (row as unknown as ProxyNode).url),
  },
  {
    key: 'pool',
    title: t('page.proxy.colPool'),
    width: 110,
    mobile: 'meta',
    render: (row: TableRow) => {
      const pool = (row as unknown as ProxyNode).pool
      return pool || t('page.proxy.poolDefault')
    },
  },
  {
    key: 'max_concurrency',
    title: t('page.proxy.colConcurrency'),
    width: 90,
    numeric: true,
    mobile: 'meta',
    render: (row: TableRow) => String((row as unknown as ProxyNode).max_concurrency),
  },
  {
    key: 'state',
    title: t('page.proxy.colState'),
    width: 100,
    mobile: 'meta',
    render: (row: TableRow) => {
      const state = proxyStateOf(row as unknown as ProxyNode, t)
      return h(Badge, { tone: state.tone }, { default: () => state.label })
    },
  },
  {
    key: 'probe',
    title: t('page.proxy.colProbe'),
    width: 140,
    mobile: 'meta',
    render: (row: TableRow) => {
      const cell = probeCell(row as unknown as ProxyNode)
      return h(Badge, { tone: cell.tone }, { default: () => cell.text })
    },
  },
  {
    key: 'last_ok_at',
    title: t('page.proxy.colLastOk'),
    width: 150,
    mobile: 'hidden',
    render: (row: TableRow) => {
      const value = (row as unknown as ProxyNode).last_ok_at
      return value ? fmtTime(value) : '—'
    },
  },
  {
    key: 'ops',
    title: t('page.proxy.colOps'),
    width: 320,
    mobile: 'actions',
    render: (row: TableRow) => {
      const node = row as unknown as ProxyNode
      const buttons = [
        h(Btn, { size: 'sm', variant: 'quiet', onClick: () => probeNode(node.id) }, {
          default: () => t('page.proxy.test'),
        }),
      ]
      if (!node.current) {
        buttons.push(
          h(Btn, { size: 'sm', variant: 'quiet', onClick: () => pinNode(node) }, {
            default: () => t('page.proxy.useThis'),
          }),
        )
      }
      buttons.push(
        h(Btn, { size: 'sm', variant: 'quiet', onClick: () => toggleNode(node) }, {
          default: () => (node.enabled ? t('page.proxy.disable') : t('page.proxy.enable')),
        }),
      )
      if (node.blocked) {
        buttons.push(
          h(Btn, { size: 'sm', variant: 'quiet', onClick: () => unblockNode(node) }, {
            default: () => t('page.proxy.unblock'),
          }),
        )
      }
      buttons.push(
        h(Btn, { size: 'sm', variant: 'danger', onClick: () => removeNode(node) }, {
          default: () => t('common.delete'),
        }),
      )
      return h('div', { class: 'proxy-ops' }, buttons)
    },
  },
])

const summaryText = computed(() => {
  const summary = nodesPayload.value?.summary
  if (!summary) return ''
  return t('page.proxy.summary', {
    total: summary.total,
    ready: summary.ready,
    blocked: summary.blocked,
    slots: summary.slots,
  })
})
</script>

<template>
  <div class="proxy-page mh-page mh-page--wide">
    <PageHeader
      :title="t('page.proxy.title')"
      :help="helpText"
      :subtitle="t('workspace.proxy.description')"
    >
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="refreshing" @click="loadAll(true)">
          {{ t('common.refresh') }}
        </Btn>
        <Btn variant="primary" :icon="Plus" @click="openDrawer">{{ t('page.proxy.add') }}</Btn>
      </template>
    </PageHeader>

    <!-- 1. 全局出口控制条 -->
    <section class="proxy-egress mh-card" :class="{ 'proxy-egress--on': egressEnabled }">
      <div class="proxy-egress__head">
        <div class="proxy-egress__title">
          <Waypoints :size="16" aria-hidden="true" />
          <strong>{{ t('page.proxy.egressTitle') }}</strong>
          <Badge :tone="egressEnabled ? 'ok' : 'neutral'" dot>
            {{ egressEnabled ? t('page.proxy.on') : t('page.proxy.off') }}
          </Badge>
        </div>
        <div class="proxy-egress__controls">
          <n-switch
            :value="egressEnabled"
            :loading="egressBusy"
            :aria-label="t('page.proxy.toggleAria')"
            @update:value="toggleEgress"
          />
          <n-select
            :value="egressMode"
            :options="[
              { label: t('page.proxy.modeAuto'), value: 'auto' },
              { label: t('page.proxy.modeFixed'), value: 'fixed' },
            ]"
            :aria-label="t('page.proxy.modeAria')"
            style="width: 140px"
            @update:value="(value: string) => setMode(value)"
          />
          <n-select
            :value="egressPool"
            :options="poolOptions"
            :aria-label="t('page.proxy.poolAria')"
            style="width: 140px"
            @update:value="(value: string | null) => setPool(value || '')"
          />
          <n-input-number
            :value="egressSeconds"
            :min="5"
            :max="86400"
            :step="30"
            :aria-label="t('page.proxy.secondsAria')"
            style="width: 130px"
            @update:value="(value: number | null) => setSeconds(value)"
          />
          <span class="proxy-egress__per">
            <n-switch
              :value="egressPerRequest"
              size="small"
              :aria-label="t('page.proxy.perRequestAria')"
              @update:value="setPerRequest"
            />
            {{ t('page.proxy.perRequest') }}
          </span>
          <Btn size="sm" variant="default" :icon="ArrowLeftRight" :disabled="egressBusy" @click="rotateNow">
            {{ t('page.proxy.rotate') }}
          </Btn>
          <Btn size="sm" variant="default" :icon="Zap" :disabled="probingId > 0" @click="testCurrent">
            {{ t('page.proxy.testCurrent') }}
          </Btn>
        </div>
      </div>

      <p class="proxy-egress__current mono">{{ currentText }}</p>
      <p v-if="metaText" class="proxy-egress__meta">{{ metaText }}</p>
      <p v-if="probeText" class="proxy-egress__probe mono">{{ probeText }}</p>
      <p v-for="warning in warnings" :key="warning" class="proxy-egress__warn">
        <ShieldAlert :size="14" aria-hidden="true" />
        {{ warning }}
      </p>
    </section>

    <!-- 2. KPI -->
    <section class="proxy-kpis" :aria-label="t('page.proxy.kpiAria')">
      <KpiCard
        :label="t('page.proxy.kpiTotal')"
        :value="nodesPayload ? fmtInt(nodesPayload.summary.total) : null"
        :hint="t('page.proxy.kpiTotalHint')"
        :icon="Globe2"
        tone="brand"
        :loading="firstLoading"
      />
      <KpiCard
        :label="t('page.proxy.kpiReady')"
        :value="nodesPayload ? fmtInt(nodesPayload.summary.ready) : null"
        :hint="t('page.proxy.kpiReadyHint')"
        :icon="CircleCheck"
        tone="ok"
        :loading="firstLoading"
      />
      <KpiCard
        :label="t('page.proxy.kpiBlocked')"
        :value="nodesPayload ? fmtInt(nodesPayload.summary.blocked) : null"
        :hint="t('page.proxy.kpiBlockedHint')"
        :icon="ShieldAlert"
        :tone="nodesPayload && nodesPayload.summary.blocked > 0 ? 'warn' : 'neutral'"
        :loading="firstLoading"
      />
      <KpiCard
        :label="t('page.proxy.kpiSlots')"
        :value="nodesPayload ? fmtInt(nodesPayload.summary.slots) : null"
        :hint="t('page.proxy.kpiSlotsHint')"
        :icon="Activity"
        tone="neutral"
        :loading="firstLoading"
      />
    </section>

    <!-- 3. 节点表 -->
    <section class="proxy-list mh-card">
      <ErrorBlock
        v-if="loadError && !nodesPayload"
        :title="loadError"
        @retry="loadAll()"
      />
      <div v-else-if="firstLoading" class="proxy-skeleton" aria-busy="true">
        <n-skeleton text :repeat="5" />
      </div>
      <template v-else>
        <DataTable
          table-id="proxy-nodes"
          :columns="columns"
          :data="nodes as unknown as TableRow[]"
          row-key="id"
          :loading="loading && !nodesPayload"
          :refreshing="loading || refreshing"
          :summary="summaryText"
          :pagination="false"
          refreshable
          @refresh="loadAll(true)"
        />
        <Empty
          v-if="!nodes.length"
          compact
          :description="t('page.proxy.empty')"
        />
      </template>
    </section>

    <!-- 4. 添加节点 -->
    <FormDrawer
      v-model:show="drawerOpen"
      :title="t('page.proxy.drawerTitle')"
      :submit-text="t('page.proxy.drawerSubmit')"
      :submit-loading="submitting"
      :submit-disabled="!draft.url.trim()"
      @submit="submitNode"
    >
      <FormField :label="t('page.proxy.fieldName')" :hint="t('page.proxy.fieldNameHint')">
        <n-input
          :value="draft.label"
          :placeholder="t('page.proxy.fieldNamePlaceholder')"
          @update:value="(value: string) => (draft.label = value)"
        />
      </FormField>
      <FormField :label="t('page.proxy.fieldUrl')" :hint="t('page.proxy.fieldUrlHint')">
        <n-input
          :value="draft.url"
          placeholder="http://user:pass@host:port  /  socks5://host:port"
          autocomplete="off"
          @update:value="(value: string) => (draft.url = value)"
        />
      </FormField>
      <FormField :label="t('page.proxy.fieldConcurrency')" :hint="t('page.proxy.fieldConcurrencyHint')">
        <n-input-number
          :value="draft.max_concurrency"
          :min="1"
          :max="32"
          :step="1"
          @update:value="(value: number | null) => (draft.max_concurrency = value ?? 1)"
        />
      </FormField>
      <FormField :label="t('page.proxy.fieldPool')" :hint="t('page.proxy.fieldPoolHint')">
        <n-input
          :value="draft.pool"
          :placeholder="t('page.proxy.poolDefault')"
          @update:value="(value: string) => (draft.pool = value)"
        />
      </FormField>
      <p class="proxy-hint">{{ t('page.proxy.drawerHint') }}</p>
    </FormDrawer>
  </div>
</template>

<style scoped>
.proxy-page {
  display: grid;
  gap: var(--sp-4);
}

.proxy-egress {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-4);
  border-left: 3px solid var(--border-strong);
}

.proxy-egress--on {
  border-left-color: var(--brand);
}

.proxy-egress__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

.proxy-egress__title {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-body);
}

.proxy-egress__controls {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.proxy-egress__per {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.proxy-egress__current {
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.proxy-egress__meta {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.proxy-egress__probe {
  font-size: var(--fs-small);
  color: var(--ok);
}

.proxy-egress__warn {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  margin: 0;
  font-size: var(--fs-small);
  color: var(--warn);
}

.proxy-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-3);
}

.proxy-list {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3) var(--sp-3);
}

.proxy-skeleton {
  padding: var(--sp-3);
}

.proxy-current {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}

.proxy-ops {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  flex-wrap: wrap;
}

.proxy-hint {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

@media (max-width: 1100px) {
  .proxy-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .proxy-kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
