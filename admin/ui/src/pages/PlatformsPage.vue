<script setup lang="ts">
/**
 * S20 平台管理 /platforms（模板 B 的卡片变体）
 *
 * 契约：docs/ui-v2/contracts/platforms.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-a.md P4
 * 旧版：#panel-platforms + platform-switch.js / platform-actions.js / platform-tiers.js / traffic.js
 *
 * 三条口径：
 *   1. 平台与能力全部来自 /api/platforms（actions / supports_local_scan / auth_mode），页面不写 if (id === 'trae')；
 *   2. 启用/禁用 = 调度开关（/api/gateway/disable|enable），账号仍可管理；
 *   3. 卡片上的「今日请求 / 成功率 / TTFT / TPS」来自 /api/platform/speed（成功率为 0–100），
 *      「可用账号 / 剩余额度」来自 /api/accounts 快照（拿不到就只显示账号数，不猜）。
 */
import { computed, effectScope, h, markRaw, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NDropdown, NInput, NSelect, NSkeleton, type DropdownOption } from 'naive-ui'
import {
  Download,
  MoreHorizontal,
  RefreshCw,
  ScanSearch,
  Upload,
} from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  JobProgress,
  Meter,
  PlatformLogo,
  Segmented,
  type FilterChip,
  type JobItem,
  type SegmentedOption,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useJob, useTableQuery, useToast, type JobSnapshot } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import { failureText } from '@/utils/error'
import { fmtCredit, fmtCompact, fmtDuration, fmtInt } from '@/utils/format'
import { listPlatforms, type PlatformSpec } from '@/api/platforms'
import {
  aggregateAccounts,
  countByState,
  exportFilename,
  exportPlatformsAll,
  filterPlatforms,
  getAccountsSnapshot,
  getGatewayConfig,
  getPlatformSpeed,
  parsePlatformBackup,
  runPlatformScan,
  saveJson,
  scanPlanFor,
  setPlatformEnabled,
  submitPlatformImport,
  successTone,
  toPlatformCards,
  type GatewayConfigPayload,
  type PlatformCard,
  type PlatformScanResult,
  type PlatformSpeedPayload,
  type PlatformSpeedRow,
  type PlatformStateFilter,
} from '@/api/platforms-admin'

const HELP_TEXT =
  '在这里切换平台与区域。切到某个平台后，账号池 / 积分到期 / 积分流水 / 成长计划都只统计该平台；' +
  '使用记录 / 用量日志 / 风控哨兵是全网关口径。禁用平台只影响调度，账号仍然可管理。'

const toast = useToast()
const confirm = useConfirm()
const router = useRouter()
const platformStore = usePlatformStore()

const table = useTableQuery('platforms', {
  filters: { q: '', state: '', hours: '' },
})
const filters = table.state.filters as unknown as {
  q: string
  state: string
  hours: string
}

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const platforms = ref<PlatformSpec[]>([])
const speed = ref<PlatformSpeedPayload | null>(null)
const gateway = ref<GatewayConfigPayload | null>(null)
const accounts = ref<Record<string, { total: number; active: number; credits: number }>>({})
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
let controller: AbortController | null = null

const hours = computed(() => (filters.hours === '168' ? 168 : 24))
const firstLoading = computed(() => loading.value && !platforms.value.length)

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const [list, bench, cfg, snapshot] = await Promise.all([
      listPlatforms(),
      getPlatformSpeed(hours.value, current.signal).catch(() => null),
      getGatewayConfig(current.signal).catch(() => null),
      // 账号快照只用于「可用账号 / 剩余额度」：拿不到就降级，不阻塞整页
      getAccountsSnapshot(current.signal).catch(() => null),
    ])
    platforms.value = Array.isArray(list?.items) ? list.items : []
    speed.value = bench
    gateway.value = cfg
    accounts.value = aggregateAccounts(snapshot?.items || [])
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: failureText('加载平台列表', error), detail }
    toast.error(failureText('加载平台列表', error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

function refresh(): void {
  void load()
}

/* ------------------------------------------------------------------ *
 * 卡片
 * ------------------------------------------------------------------ */

const cards = computed<PlatformCard[]>(() =>
  toPlatformCards({
    platforms: platforms.value,
    speed: speed.value,
    gateway: gateway.value,
    accounts: accounts.value,
  }),
)
const counts = computed(() => countByState(cards.value))
const stateFilter = computed<PlatformStateFilter>(() => {
  if (filters.state === 'enabled' || filters.state === 'disabled') return filters.state
  return 'all'
})
const visibleCards = computed(() =>
  filterPlatforms(cards.value, { state: stateFilter.value, q: filters.q }),
)
const maxCredits = computed(() =>
  cards.value.reduce((max, card) => Math.max(max, Number(card.credits) || 0), 0),
)

const stateOptions = computed<Array<SegmentedOption<string>>>(() => [
  { label: '全部 ' + counts.value.all, value: 'all' },
  { label: '已启用 ' + counts.value.enabled, value: 'enabled' },
  { label: '已禁用 ' + counts.value.disabled, value: 'disabled' },
])

const chips = computed<FilterChip[]>(() => {
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: '搜索', value: filters.q })
  if (stateFilter.value !== 'all') {
    list.push({ key: 'state', label: '状态', value: stateFilter.value === 'enabled' ? '已启用' : '已禁用' })
  }
  return list
})

const summary = computed(
  () =>
    '共 ' + counts.value.all + ' 个平台 · 已启用 ' + counts.value.enabled +
    ' · 已禁用 ' + counts.value.disabled,
)

const search = ref(filters.q)
watch(
  () => filters.q,
  (value) => {
    search.value = value
  },
)

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) {
    next[name] = value === null || value === undefined ? '' : String(value)
  }
  table.setFilters(next)
}

function setState(value: string | number): void {
  table.setFilter('state', value === 'enabled' || value === 'disabled' ? String(value) : '')
}

function removeChip(key: string): void {
  if (key === 'q') search.value = ''
  table.setFilter(key, '')
}

function resetFilters(): void {
  search.value = ''
  table.setFilters({ q: '', state: '' })
}

function setHours(value: string | number): void {
  table.setFilter('hours', String(value) === '168' ? '168' : '')
}

const hoursOptions: Array<SegmentedOption<string>> = [
  { label: '近 24 小时', value: '24' },
  { label: '近 7 天', value: '168' },
]

const tierTone = (key: string): 'ok' | 'brand' | 'warn' | 'danger' =>
  key === 'hang' ? 'ok' : key === 'elite' ? 'brand' : key === 'npc' ? 'warn' : 'danger'

/** MetricTone（含 muted）→ Badge 的语气。 */
function badgeToneOf(tone: string): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (tone === 'ok' || tone === 'warn' || tone === 'danger') return tone
  return 'neutral'
}

function quotaText(card: PlatformCard): string {
  if (card.credits === null) return '—'
  const unit = card.quotaUnitLabel || ''
  const value = card.quotaUnit === 'token' ? fmtCompact(card.credits) : fmtCredit(card.credits)
  return unit ? value + ' ' + unit : value
}

function rateText(card: PlatformCard): string {
  if (card.successRate === null) return '—'
  return card.successRate.toFixed(1) + '%'
}

function isCurrent(card: PlatformCard): boolean {
  return platformStore.platform === card.id && (platformStore.realm || '') === (card.realm || '')
}

/* ------------------------------------------------------------------ *
 * 卡片操作
 * ------------------------------------------------------------------ */

function openAccounts(card: PlatformCard): void {
  platformStore.setCurrent(card.id, card.realm || '')
  void router.push('/accounts')
}

function makeCurrent(card: PlatformCard): void {
  if (isCurrent(card)) return
  platformStore.setCurrent(card.id, card.realm || '')
  toast.success('已切换到 ' + card.label)
}

async function toggleEnabled(card: PlatformCard): Promise<void> {
  const enabling = !card.enabled
  if (!enabling) {
    const ok = await confirmDisable(card)
    if (!ok) return
  }
  try {
    await setPlatformEnabled(card.storage, enabling, enabling ? '' : '界面禁用')
    toast.success(enabling ? '已启用 ' + card.label : '已禁用 ' + card.label + '（不参与调度，账号仍可管理）')
    await load()
    void platformStore.reload()
  } catch (error) {
    toast.error(failureText(enabling ? '启用平台' : '禁用平台', error))
  }
}

function confirmDisable(card: PlatformCard): Promise<boolean> {
  return confirm({
    title: '禁用「' + card.label + '」？',
    message: '禁用后该平台不再参与调度，但账号数据保留，可随时重新启用。',
    okText: '禁用',
    danger: true,
  })
}

async function scanOne(card: PlatformCard): Promise<void> {
  const spec = platforms.value.find((item) => item.id === card.id)
  const plan = scanPlanFor(spec)
  if (!spec || !plan) {
    toast.error('扫描失败：找不到该平台的声明信息')
    return
  }
  toast.info('开始扫描 ' + card.label + '…')
  const result = await runPlatformScan(spec, plan)
  if (result.note && !result.imported && !result.scanned) toast.error(card.label + '：' + result.note)
  else toast.success(card.label + '：扫描 ' + result.scanned + ' · 入库 ' + result.imported)
  await load()
  void platformStore.refreshCounts()
}

async function exportOne(card: PlatformCard): Promise<void> {
  await runExport('backup', card.storage)
}

async function runExport(mode: 'backup' | 'plain', platform = ''): Promise<void> {
  exporting.value = true
  try {
    const data = await exportPlatformsAll(mode, platform)
    const platformsMap = (data?.platforms || {}) as Record<string, { count?: number }>
    const count = Object.values(platformsMap).reduce((sum, item) => sum + Number(item?.count || 0), 0)
    if (!count) {
      toast.error('该平台没有账号可导出')
      return
    }
    const scope = String(data?.platform_scope || (platform || 'all-platforms'))
    saveJson(data, exportFilename(mode, scope))
    toast.success('已导出 ' + count + ' 个账号（' + Object.keys(platformsMap).length + ' 个平台）')
    exportOpen.value = false
  } catch (error) {
    toast.error(failureText('导出平台账号', error))
  } finally {
    exporting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 卡片 ⋯ 菜单
 * ------------------------------------------------------------------ */

function menuOptions(card: PlatformCard): DropdownOption[] {
  const options: DropdownOption[] = [
    { label: card.enabled ? '禁用平台' : '启用平台', key: 'toggle' },
    { label: '扫描本机', key: 'scan', disabled: !card.actions.some((item) => item.name === 'scan') },
    { label: '导出本平台', key: 'export' },
  ]
  const extra = card.actions.filter((item) => !['scan', 'export'].includes(item.name))
  if (extra.length) {
    options.push({ type: 'divider', key: 'divider-1' })
    for (const action of extra) {
      options.push({
        label: action.name === 'import' || action.name === 'import-file' ? action.label : action.label + '（账号池）',
        key: 'action:' + action.name,
      })
    }
  }
  if (card.tier) {
    options.push({ type: 'divider', key: 'divider-2' })
    options.push({ label: '平均级：' + card.tier.label, key: 'tier', disabled: true })
  }
  return options
}

function onMenuSelect(card: PlatformCard, key: string | number): void {
  const name = String(key)
  if (name === 'toggle') {
    void toggleEnabled(card)
    return
  }
  if (name === 'scan') {
    void scanOne(card)
    return
  }
  if (name === 'export') {
    void exportOne(card)
    return
  }
  if (name === 'tier') return
  if (name.startsWith('action:')) {
    const action = name.slice(7)
    if (action === 'import' || action === 'import-file') {
      openImport(card.storage)
      return
    }
    // 其余平台动作（签到 / 刷新额度 / 授权登录…）在账号池按账号执行（S25/S26）
    platformStore.setCurrent(card.id, card.realm || '')
    toast.info('已切到 ' + card.label + '：该动作在账号池执行')
    void router.push('/accounts')
  }
}

/* ------------------------------------------------------------------ *
 * 扫描全部
 * ------------------------------------------------------------------ */

const scanningAll = ref(false)
const scanResults = ref<PlatformScanResult[]>([])

async function scanAll(): Promise<void> {
  if (scanningAll.value) return
  if (!platforms.value.length) {
    toast.error('平台清单还没加载好，请稍后再试')
    return
  }
  scanningAll.value = true
  scanResults.value = []
  let imported = 0
  try {
    for (const spec of platforms.value) {
      const plan = scanPlanFor(spec)
      if (!plan) continue
      if (!spec.supports_local_scan && plan.kind !== 'accounts-import') continue
      const result = await runPlatformScan(spec, plan)
      scanResults.value.push(result)
      imported += result.imported
    }
    toast.success('扫描完成：共入库 ' + imported + ' 个账号')
    await load()
    void platformStore.refreshCounts()
  } finally {
    scanningAll.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 导入全部（FormDrawer + 后台任务 JobProgress）
 * ------------------------------------------------------------------ */

const importOpen = ref(false)
const importFiles = ref<Array<{ name: string; text: string }>>([])
const importNotes = ref<string[]>([])
const importPreview = ref<Array<{ storage: string; count: number }>>([])
const submitting = ref(false)

function openImport(preset?: string): void {
  importFiles.value = []
  importNotes.value = []
  importPreview.value = []
  importOpen.value = true
  if (preset) importNotes.value.push('已选平台 ' + preset + '：把该平台的导出文件拖进来即可')
}

async function onImportFiles(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement | null
  const files = Array.from(input?.files || [])
  if (!files.length) return
  const loaded: Array<{ name: string; text: string }> = []
  for (const file of files) {
    try {
      loaded.push({ name: file.name, text: await file.text() })
    } catch (error) {
      importNotes.value.push(file.name + '：读取失败（' + ((error as Error)?.message || String(error)) + '）')
    }
  }
  importFiles.value = loaded
  const parsed = parsePlatformBackup(loaded)
  importNotes.value = parsed.notes.slice()
  importPreview.value = Object.entries(parsed.byPlatform).map(([storage, list]) => ({
    storage,
    count: list.length,
  }))
}

/** useJob 的最小结构（只要 snapshot / percent 两个 ref）。 */
interface TrackedJobHandle {
  snapshot: { value: JobSnapshot | null }
  percent: { value: number }
}

const jobHandles = ref<Array<{ id: string; name: string; handle: TrackedJobHandle }>>([])
const jobScopes: Array<ReturnType<typeof effectScope>> = []

function trackJob(id: string, name: string): void {
  const scope = effectScope()
  const handle = scope.run(() =>
    useJob(id, {
      pollUrl: '/api/accounts/job/',
      onFinish: (snapshot: JobSnapshot) => {
        const result = (snapshot.result || {}) as { added?: number }
        if (snapshot.status === 'failed') toast.error(name + ' 导入失败：' + (snapshot.error || '未知错误'))
        else toast.success(name + ' 导入完成：新增 ' + Number(result.added || 0) + ' 个')
        void load()
        void platformStore.refreshCounts()
      },
    }),
  )
  if (!handle) return
  jobScopes.push(scope)
  // useJob 返回 ref 句柄；不 markRaw 会被 ref 深响应式解包，jobItems 再读 .value 会崩。
  jobHandles.value.push({ id, name, handle: markRaw(handle) as unknown as TrackedJobHandle })
}

const jobItems = computed<JobItem[]>(() =>
  jobHandles.value
    .filter((entry) => entry.handle.snapshot.value !== null)
    .map((entry) => {
      const snapshot = entry.handle.snapshot.value as JobSnapshot
      const result = (snapshot.result || {}) as { added?: number; failed?: number }
      return {
        id: entry.id,
        name: entry.name,
        status: snapshot.status === 'running' ? 'running' : snapshot.status === 'failed' ? 'failed' : 'done',
        percent: entry.handle.percent.value,
        succeeded: Number(result.added || 0),
        failed: Number(result.failed || 0),
        message: snapshot.current || snapshot.error || '',
      } as JobItem
    }),
)

function hideJob(id: string): void {
  jobHandles.value = jobHandles.value.filter((entry) => entry.id !== id)
}

async function submitImport(): Promise<void> {
  if (submitting.value) return
  const parsed = parsePlatformBackup(importFiles.value)
  const entries = Object.entries(parsed.byPlatform).filter(([, list]) => list.length)
  if (!entries.length) {
    toast.error('没有解析出任何平台账号')
    return
  }
  submitting.value = true
  let submitted = 0
  try {
    for (const [storage, list] of entries) {
      try {
        const jobId = await submitPlatformImport(storage, list)
        trackJob(jobId, storage + ' 导入')
        submitted += 1
      } catch (error) {
        toast.error(storage + ' 提交失败：' + ((error as Error)?.message || String(error)))
      }
    }
    if (submitted) {
      toast.success('已提交 ' + submitted + ' 个平台的后台导入，完成后自动刷新')
      importOpen.value = false
    }
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 导出全部（FormDrawer）
 * ------------------------------------------------------------------ */

const exportOpen = ref(false)
const exporting = ref(false)
const exportMode = ref<'backup' | 'plain'>('backup')
const exportScope = ref('')

const exportModeOptions: Array<SegmentedOption<string>> = [
  { label: '备份（含凭证）', value: 'backup' },
  { label: '清单（脱敏）', value: 'plain' },
]

const exportScopeOptions = computed(() => {
  const seen = new Set<string>()
  const options = [{ label: '全部平台（整机备份）', value: '' }]
  for (const card of cards.value) {
    if (!card.accountCount || seen.has(card.storage)) continue
    seen.add(card.storage)
    options.push({ label: card.label + '（' + card.accountCount + ' 个账号）', value: card.storage })
  }
  return options
})

/* ------------------------------------------------------------------ *
 * 跑分表
 * ------------------------------------------------------------------ */

const benchRows = computed<TableRow[]>(() =>
  ((speed.value?.items || []) as PlatformSpeedRow[]).map((row) => ({ ...row })),
)

function benchCell(row: TableRow, key: keyof PlatformSpeedRow): string {
  const item = row as unknown as PlatformSpeedRow
  if (key === 'success_rate') return Number(item.success_rate).toFixed(1) + '%'
  if (key === 'ttfb_avg' || key === 'latency_avg') return item[key] ? fmtDuration(item[key]) : '—'
  if (key === 'tps_avg') return item.tps_avg ? item.tps_avg.toFixed(1) + ' tok/s' : '—'
  if (key === 'jitter') return item.jitter ? fmtDuration(item.jitter) : '—'
  return String(item[key] ?? '—')
}

const benchColumns: TableColumn[] = [
  { key: 'rank', title: '#', width: 48, mobile: 'meta' },
  { key: 'label', title: '平台', minWidth: 140, mobile: 'title' },
  {
    key: 'success_rate',
    title: '成功率',
    width: 96,
    mobile: 'subtitle',
    render: (row: TableRow) => {
      const item = row as unknown as PlatformSpeedRow
      return h(Badge, { tone: badgeToneOf(successTone(item.success_rate)) }, { default: () => benchCell(row, 'success_rate') })
    },
  },
  { key: 'ttfb_avg', title: 'TTFT 均值', width: 110, mobile: 'meta', render: (row: TableRow) => benchCell(row, 'ttfb_avg') },
  { key: 'tps_avg', title: 'TPS 均值', width: 110, mobile: 'meta', render: (row: TableRow) => benchCell(row, 'tps_avg') },
  { key: 'latency_avg', title: '总耗时均值', width: 120, mobile: 'meta', render: (row: TableRow) => benchCell(row, 'latency_avg') },
  { key: 'jitter', title: '抖动', width: 90, mobile: 'hidden', render: (row: TableRow) => benchCell(row, 'jitter') },
  {
    key: 'samples',
    title: '样本',
    width: 96,
    mobile: 'hidden',
    render: (row: TableRow) => {
      const item = row as unknown as PlatformSpeedRow
      return h('span', { class: item.low_sample ? 'pf-low' : '' }, fmtInt(item.samples) + (item.low_sample ? '（样本少）' : ''))
    },
  },
]

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null
let lastSelector = platformStore.selector

onMounted(() => {
  lastSelector = platformStore.selector
  void load()
  offPlatform = platformStore.onChange((detail) => {
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    void load()
  })
})

onBeforeUnmount(() => {
  offPlatform?.()
  offPlatform = null
  controller?.abort()
  controller = null
  for (const scope of jobScopes) scope.stop()
  jobScopes.length = 0
})

watch(
  () => filters.hours,
  () => {
    void load()
  },
)
</script>

<template>
  <div class="pf-page">
    <PageHeader title="平台管理" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="default" :icon="ScanSearch" :loading="scanningAll" :disabled="loading" @click="scanAll">
          扫描全部平台
        </Btn>
        <Btn variant="default" :icon="Upload" :disabled="loading" @click="openImport()">导入</Btn>
        <Btn variant="default" :icon="Download" :disabled="loading" @click="exportOpen = true">导出</Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="refresh">刷新</Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="loadError && !platforms.length"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="refresh"
    />

    <div v-else-if="firstLoading" class="pf-skeleton" aria-busy="true" aria-label="平台列表加载中">
      <div v-for="index in 6" :key="index" class="pf-skeleton__card"><n-skeleton text :repeat="4" /></div>
    </div>

    <template v-else>
      <Card>
        <FilterBar
          :chips="chips"
          :summary="summary"
          :loading="loading"
          @change="onFilterChange"
          @reset="resetFilters"
          @remove="removeChip"
        >
          <template #default>
            <Segmented :options="stateOptions" :model-value="stateFilter" aria-label="平台状态" @update:model-value="setState" />
            <n-input
              v-model:value="search"
              class="pf-search"
              placeholder="搜索平台"
              clearable
              @update:value="(value: string) => onFilterChange({ q: value })"
            />
          </template>
        </FilterBar>

        <div v-if="visibleCards.length" class="pf-grid">
          <article
            v-for="card in visibleCards"
            :key="card.id"
            class="pf-card"
            :class="{ 'is-disabled': !card.enabled }"
            :aria-label="card.label"
          >
            <header class="pf-card__head">
              <PlatformLogo :id="card.id" :size="28" :label="card.label" />
              <div class="pf-card__title">
                <h3 :title="card.notes || card.label">{{ card.label }}</h3>
                <p>{{ card.realmLabel || '单区平台' }} · 优先级 {{ card.priority === null ? '—' : card.priority }}</p>
              </div>
              <div class="pf-card__badges">
                <Badge :tone="card.enabled ? 'ok' : 'neutral'">{{ card.enabled ? '已启用' : '已禁用' }}</Badge>
                <Badge v-if="card.tier" :tone="tierTone(card.tier.key)" :title="card.tier.reason">{{ card.tier.label }}</Badge>
              </div>
            </header>

            <div class="pf-card__metrics">
              <span class="tnum">
                账号
                {{ card.activeAccounts === null ? card.accountCount + ' 个' : card.activeAccounts + '/' + card.accountCount + ' 可用' }}
              </span>
              <span class="tnum">
                今日 {{ fmtInt(card.requests) }} 次
                <template v-if="card.successRate !== null"> · <b :class="'pf-tone-' + successTone(card.successRate)">{{ rateText(card) }}</b></template>
              </span>
              <span class="tnum">
                TTFT {{ card.ttfbMs ? fmtDuration(card.ttfbMs) : '—' }} · TPS {{ card.tps ? card.tps.toFixed(1) : '—' }}
              </span>
            </div>

            <div class="pf-card__quota">
              <span>剩余额度 {{ quotaText(card) }}</span>
              <Meter
                :value="card.credits ?? 0"
                :max="maxCredits || 1"
                tone="brand"
                :aria-label="card.label + ' 剩余额度占比'"
              />
            </div>

            <p v-if="!card.enabled" class="pf-card__note">
              不参与调度，账号仍可管理{{ card.disabledReason ? '（' + card.disabledReason + '）' : '' }}
            </p>

            <footer class="pf-card__actions">
              <Btn size="sm" variant="default" @click="openAccounts(card)">账号池</Btn>
              <Btn size="sm" variant="quiet" :disabled="isCurrent(card)" @click="makeCurrent(card)">
                {{ isCurrent(card) ? '当前平台' : '设为当前' }}
              </Btn>
              <n-dropdown
                trigger="click"
                placement="bottom-end"
                :options="menuOptions(card)"
                @select="(key: string | number) => onMenuSelect(card, key)"
              >
                <Btn size="sm" variant="quiet" :icon="MoreHorizontal" aria-label="更多平台操作">更多</Btn>
              </n-dropdown>
            </footer>
          </article>
        </div>

        <Empty v-else :description="filters.q || stateFilter !== 'all' ? '没有符合条件的平台，试试清空搜索或切回「全部」。' : '还没有登记任何平台。'">
          <Btn v-if="filters.q || stateFilter !== 'all'" variant="default" size="sm" @click="resetFilters">清除筛选</Btn>
          <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="refresh">刷新</Btn>
        </Empty>
      </Card>

      <Card title="平台速度与稳定性" :loading="loading && !!speed">
        <template #extra>
          <Segmented :options="hoursOptions" :model-value="String(hours)" aria-label="统计窗口" @update:model-value="setHours" />
        </template>
        <DataTable
          table-id="platform-bench"
          :columns="benchColumns"
          :data="benchRows"
          row-key="platform"
          :loading="loading"
          empty-description="近窗口内还没有真实请求样本。"
        >
          <template #empty>
            <Empty compact description="近窗口内还没有真实请求样本。" />
          </template>
        </DataTable>
      </Card>

      <Card v-if="scanResults.length" title="扫描全部平台 · 结果">
        <ul class="pf-scan">
          <li v-for="item in scanResults" :key="item.storage" class="pf-scan__row">
            <span class="pf-scan__name">{{ item.label }}</span>
            <span class="tnum">扫描 {{ item.scanned }}</span>
            <span class="tnum">入库 {{ item.imported }}</span>
            <span class="tnum">跳过 {{ item.skipped }}</span>
            <span class="pf-scan__note">{{ item.note }}</span>
          </li>
        </ul>
      </Card>
    </template>

    <FormDrawer
      v-model:show="importOpen"
      title="导入全部平台"
      submit-text="开始导入"
      :submit-disabled="!importPreview.length || submitting"
      :submit-loading="submitting"
      @submit="submitImport"
    >
      <p class="pf-hint">
        选择「导出全部平台」生成的 JSON（format=miraihub-all-platforms）。每个平台的账号都会走各自的后台导入任务，
        可以切到其它页面等结果。
      </p>
      <input class="pf-file" type="file" accept=".json,application/json" multiple aria-label="选择全平台备份文件" @change="onImportFiles" />
      <ul v-if="importPreview.length" class="pf-preview">
        <li v-for="item in importPreview" :key="item.storage">
          {{ item.storage }} · {{ item.count }} 个账号
        </li>
      </ul>
      <p v-for="(note, index) in importNotes" :key="index" class="pf-hint">{{ note }}</p>
    </FormDrawer>

    <FormDrawer
      v-model:show="exportOpen"
      title="导出平台账号"
      submit-text="开始导出"
      :submit-loading="exporting"
      @submit="runExport(exportMode, exportScope)"
    >
      <p class="pf-hint">「备份」含明文凭证，可用「导入全部平台」原样回灌；「清单」已脱敏，只能用于核对。</p>
      <Segmented :options="exportModeOptions" :model-value="exportMode" aria-label="导出模式" @update:model-value="(value: string | number) => (exportMode = String(value) === 'plain' ? 'plain' : 'backup')" />
      <n-select
        v-model:value="exportScope"
        :options="exportScopeOptions"
        aria-label="导出范围"
        placeholder="导出范围"
      />
    </FormDrawer>

    <JobProgress :jobs="jobItems" title="平台导入任务" @hide="hideJob" />
  </div>
</template>

<style scoped>
.pf-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.pf-search {
  width: 240px;
  max-width: 100%;
}

.pf-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--sp-4);
  margin-top: var(--sp-4);
}

.pf-card {
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  min-width: 0;
}

.pf-card.is-disabled {
  opacity: 0.6;
}

.pf-card__head {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  min-width: 0;
}

.pf-card__title {
  flex: 1;
  min-width: 0;
}

.pf-card__title h3 {
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pf-card__title p {
  margin-top: 2px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.pf-card__badges {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--sp-1);
  flex: none;
}

.pf-card__metrics {
  display: grid;
  gap: 2px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.pf-card__quota {
  display: grid;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.pf-card__note {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--warn);
}

.pf-card__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.pf-tone-ok { color: var(--ok); }
.pf-tone-warn { color: var(--warn); }
.pf-tone-danger { color: var(--danger); }
.pf-tone-muted { color: var(--fg-muted); }

.pf-low { color: var(--warn); }

.pf-scan {
  display: grid;
  gap: var(--sp-2);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.pf-scan__row {
  display: flex;
  gap: var(--sp-3);
  flex-wrap: wrap;
  color: var(--fg-2);
}

.pf-scan__name {
  font-weight: 500;
  color: var(--fg);
  min-width: 96px;
}

.pf-scan__note {
  color: var(--fg-muted);
}

.pf-hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.pf-file {
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.pf-preview {
  display: grid;
  gap: var(--sp-1);
  margin: 0;
  padding-left: var(--sp-5);
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.pf-skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--sp-4);
}

.pf-skeleton__card {
  padding: var(--sp-5);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
}

@media (max-width: 640px) {
  .pf-grid,
  .pf-skeleton {
    grid-template-columns: minmax(0, 1fr);
  }

  .pf-search {
    width: 100%;
  }
}
</style>
