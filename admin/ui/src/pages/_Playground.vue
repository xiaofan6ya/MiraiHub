<script setup lang="ts">
/**
 * 组件展示页（_Playground）：每个公共组件一个示例，含空 / 加载 / 错误状态。
 * 用途：S05 验收时在浅/深色、桌面/手机宽度下逐组件检查。
 */
import { computed, h, ref } from 'vue'
import { NInput, NSelect } from 'naive-ui'
import { CircleHelp, Plus, RefreshCw, Settings2, Trash2 } from 'lucide-vue-next'
import { useTheme, type ThemeMode } from '@/theme/useTheme'
import {
  BADGE_TONE_NAMES,
  Badge,
  BarList,
  Btn,
  BulkBar,
  Card,
  Chart,
  Chip,
  CodeBlock,
  ConfirmDialog,
  Copyable,
  DataTable,
  DateRange,
  DetailDrawer,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  HelpTip,
  IconBtn,
  JobProgress,
  KV,
  Meter,
  ModelIcon,
  PlatformLogo,
  Ring,
  RouteRibbon,
  RowActions,
  Section,
  Segmented,
  StatCard,
  StatStrip,
  StackMeter,
  StatusDot,
  applyBarDefaults,
  applyLineDefaults,
  type BadgeTone,
  type BarListItem,
  type FilterChip,
  type JobItem,
  type KvItem,
  type RibbonLane,
  type SegmentedOption,
  type StackMeterItem,
  type StatItem,
  type TableColumn,
} from '@/components'

const { mode, effectiveTheme, setMode } = useTheme()

const modes: Array<{ label: string; value: ThemeMode }> = [
  { label: '浅色', value: 'light' },
  { label: '深色', value: 'dark' },
  { label: '跟随系统', value: 'system' },
]

/* ---------------- 令牌 ---------------- */
const tokens = [
  '--bg', '--surface', '--surface-2', '--surface-hover', '--border', '--border-soft',
  '--border-strong', '--fg', '--fg-2', '--fg-muted', '--fg-faint', '--brand',
  '--brand-hover', '--brand-soft', '--ok', '--ok-soft', '--warn', '--warn-soft',
  '--danger', '--danger-soft', '--neutral-soft', '--chart-1', '--chart-2', '--chart-3',
]

const sizes = [
  { name: 'display', size: 'var(--fs-display)', lh: 'var(--lh-display)', text: '剩余额度 1,234.56' },
  { name: 'h1', size: 'var(--fs-h1)', lh: 'var(--lh-h1)', text: '页面标题' },
  { name: 'h2', size: 'var(--fs-h2)', lh: 'var(--lh-h2)', text: '卡片标题' },
  { name: 'stat', size: 'var(--fs-stat)', lh: 'var(--lh-stat)', text: '12,954' },
  { name: 'body', size: 'var(--fs-body)', lh: 'var(--lh-body)', text: '正文内容 14px' },
  { name: 'small', size: 'var(--fs-small)', lh: 'var(--lh-small)', text: '次要说明 12px' },
]

const swatchStyle = (token: string) => ({ background: 'var(' + token + ')' })
const badgeTones = BADGE_TONE_NAMES

/* ---------------- 基础 ---------------- */
const range = ref<string | number>('today')
const rangeOptions: SegmentedOption[] = [
  { label: '今日', value: 'today' },
  { label: '7 天', value: '7d' },
  { label: '30 天', value: '30d' },
]

const kvItems: KvItem[] = [
  { label: '账号 ID', value: 'acc_8f2c1d7a', mono: true, copy: true },
  { label: '平台', value: 'WorkBuddy' },
  { label: 'API Key', value: 'sk-mh3f9e2b7c1d9f2e', mono: true, copy: true },
  { label: '最近刷新', value: null },
  { label: '备注', value: '上游 401 后自动切换备用账号。', block: true },
]

const curl = 'curl -X POST https://gateway.example.com/v1/chat/completions \\\n  -H "Authorization: Bearer sk-xxx" \\\n  -d "{\"model\":\"gpt-4o\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"'

const retryCount = ref(0)

/* ---------------- 数据展示 ---------------- */
const stats: StatItem[] = [
  { label: '今日请求', value: '2,326', foot: '成功 2,180 次 · 较昨日 +12%', help: '所有平台的入站请求总数' },
  { label: '成功率', value: '93.7', unit: '%', tone: 'ok', foot: '失败 146 次' },
  { label: '额度剩余', value: '12,954', unit: '积分', tone: 'warn', foot: '7 天内到期 3 个账号' },
  { label: '平均延迟', value: '850', unit: 'ms', foot: 'P95 3.95s' },
  { label: '在线平台', value: '8/9', tone: 'brand', to: '/platforms' },
]

const ribbonLanes: RibbonLane[] = [
  { id: 'workbuddy', name: 'WorkBuddy', count: 1786, successRate: 98.4, preferred: true },
  { id: 'loomy', name: 'Loomy', count: 210, successRate: 92.1 },
  { id: 'trae-cn', name: 'Trae 国内版', count: 175, successRate: 74.3 },
  { id: 'qoder', name: 'Qoder 中国版', count: 127, successRate: 58.2 },
  { id: 'trae-intl', name: 'Trae 国际版', count: 8, successRate: 0 },
  { id: 'marvis', name: '腾讯 Marvis', count: 0, successRate: null, disabled: true },
]

const ribbonTotal = computed(() => ribbonLanes.reduce((acc, lane) => acc + lane.count, 0))

const emptyLanes: RibbonLane[] = ribbonLanes.map((lane) => ({
  ...lane,
  count: 0,
  successRate: null,
  disabled: true,
}))

const statusTone = (status: string): BadgeTone =>
  status === '活跃' ? 'ok' : status === '冷却' ? 'warn' : 'neutral'

const rowActions = [
  { key: 'refresh', label: '刷新' },
  { key: 'disable', label: '禁用' },
  { key: 'detail', label: '详情' },
  { key: 'delete', label: '删除', danger: true },
]

const tableColumns: TableColumn[] = [
  { key: 'account', title: '账号', mobile: 'title', width: 150 },
  {
    key: 'status',
    title: '状态',
    mobile: 'title',
    width: 90,
    render: (row) =>
      h(Badge, { tone: statusTone(String(row.status)) }, { default: () => String(row.status) }),
  },
  { key: 'platform', title: '平台', mobile: 'subtitle', width: 140 },
  { key: 'remain', title: '剩余', mobile: 'subtitle', numeric: true, width: 100, sortable: true },
  { key: 'requests', title: '请求', mobile: 'meta', numeric: true, width: 100, sortable: true },
  { key: 'points', title: '积分', mobile: 'meta', numeric: true, width: 90 },
  { key: 'hitRate', title: '命中率', mobile: 'meta', numeric: true, width: 100 },
  { key: 'createdAt', title: '创建时间', mobile: 'meta', hideable: false, width: 120 },
  {
    key: 'actions',
    title: '操作',
    mobile: 'actions',
    width: 180,
    render: () =>
      h(RowActions, {
        actions: rowActions,
        onSelect: (key: string) => recordAction('行操作：' + key),
      }),
  },
]

const tableRows = Array.from({ length: 42 }, (_, index) => ({
  id: 'acc-' + (index + 1),
  account: '193****' + String(2486 + index).padStart(4, '0'),
  status: ['活跃', '活跃', '冷却', '已禁用'][index % 4],
  platform: ['WorkBuddy', 'Loomy', 'Trae 国内版', 'Qoder 中国版'][index % 4],
  remain: 4764 - index * 37,
  requests: 340 - index * 3,
  points: 9 + (index % 5),
  hitRate: 95 - (index % 20) + '%',
  createdAt: '2026-11-' + String((index % 28) + 1).padStart(2, '0'),
}))

const tablePage = ref(1)
const tablePageSize = ref(20)
const tableLoading = ref(false)
const tableEmpty = ref(false)
const tableSelection = ref<string[]>([])

const tableData = computed(() => (tableEmpty.value ? [] : tableRows))

const barItems: BarListItem[] = [
  { label: 'WorkBuddy', value: 1786, tone: 'brand' },
  { label: 'Loomy', value: 210, tone: 'brand' },
  { label: 'Trae 国内版', value: 175, tone: 'warn' },
  { label: 'Qoder 中国版', value: 127, tone: 'danger' },
  { label: 'Marvis', value: 8, tone: 'neutral' },
]

const stackItems: StackMeterItem[] = [
  { label: '≤30 天到期', value: 3, tone: 'danger' },
  { label: '30 天以外', value: 12, tone: 'warn' },
  { label: '长期有效', value: 26, tone: 'ok' },
]

const lineOption = computed(() =>
  applyLineDefaults({
    xAxis: { type: 'category', data: ['00:00', '04:00', '08:00', '12:00', '16:00', '20:00'] },
    yAxis: { type: 'value' },
    legend: { data: ['请求', '错误'] },
    series: [
      { name: '请求', data: [420, 300, 980, 1240, 880, 620] },
      { name: '错误', data: [3, 1, 18, 24, 11, 6] },
    ],
  }),
)

const barOption = computed(() =>
  applyBarDefaults({
    xAxis: { type: 'category', data: ribbonLanes.map((lane) => lane.name) },
    yAxis: { type: 'value' },
    series: [{ name: '调用次数', data: ribbonLanes.map((lane) => lane.count) }],
  }),
)

/* ---------------- 筛选 ---------------- */
const filterChips = ref<FilterChip[]>([
  { key: 'platform', label: '平台', value: 'WorkBuddy' },
  { key: 'result', label: '结果', value: '错误' },
])
const filterSummary = ref('共 862 次 · 成功 850 · 错误 12')
const dateRange = ref<[number, number] | null>(null)

function onDateRange(value: [number, number] | null): void {
  dateRange.value = value
}

function onFilterChange(values: Record<string, unknown>): void {
  filterSummary.value = '已提交筛选：' + JSON.stringify(values)
}

function onFilterReset(): void {
  filterChips.value = []
  filterSummary.value = '共 2,326 次 · 成功 2,180 · 错误 146'
}

function onFilterRemove(key: string): void {
  filterChips.value = filterChips.value.filter((chip) => chip.key !== key)
}

/* ---------------- 浮层 ---------------- */
const showForm = ref(false)
const showConfirm = ref(false)
const showTyped = ref(false)
const showDetail = ref(false)
const confirmResult = ref('')
const formName = ref('主账号')

const jobs = ref<JobItem[]>([
  { id: 'job-1', name: '刷新 24 个账号额度', status: 'running', percent: 62, succeeded: 15, failed: 0 },
  { id: 'job-2', name: '导出用量日志', status: 'failed', percent: 100, succeeded: 8_312, failed: 3, message: '3 行写入失败：字段超长' },
])

function recordAction(text: string): void {
  confirmResult.value = text
}

function confirmDelete(): void {
  showConfirm.value = false
  recordAction('删除了 3 个账号')
}

function confirmClear(): void {
  showTyped.value = false
  recordAction('已清空日志')
}

function toggleJobs(): void {
  jobs.value = jobs.value.length
    ? []
    : [
        { id: 'job-1', name: '刷新 24 个账号额度', status: 'running', percent: 62, succeeded: 15, failed: 0 },
        {
          id: 'job-2',
          name: '导出用量日志',
          status: 'failed',
          percent: 100,
          succeeded: 8312,
          failed: 3,
          message: '3 行写入失败：字段超长',
        },
      ]
}

function onPageChange(page: number): void {
  tablePage.value = page
}

function onPageSizeChange(size: number): void {
  tablePageSize.value = size
}

function onSelectionChange(keys: Array<string | number>): void {
  tableSelection.value = keys.map(String)
}

function hideJob(id: string): void {
  jobs.value = jobs.value.filter((job) => job.id !== id)
}

/* ---------------- 身份 ---------------- */
const platformIds = [
  'trae',
  'trae-cn',
  'workbuddy',
  'qoder',
  'loomy',
  'raccoon',
  'codearts',
  'minimax',
  'wps-lingxi',
  'doubao',
  'marvis',
  'krowork',
  'unknown-platform',
]

const modelVendors = ['gpt-4o', 'gemini-2.0', 'grok-2', 'qwen-max', 'doubao-seed', 'kling', 'step-3', 'deepseek-v3', 'claude-3.7', 'glm-4', 'kimi-k2', 'hunyuan-turbo', 'balanced', 'unknown-model']

const themeLabel = computed(() => (effectiveTheme.value === 'dark' ? '深色' : '浅色'))
</script>

<template>
  <main class="pg">
    <header class="pg__head">
      <div>
        <h1 class="pg__title">组件与令牌展示</h1>
        <p class="pg__hint">
          当前模式：{{ mode }}（实际 {{ themeLabel }}）。把视口拖到 ≤640px 检查卡片模式与退化样式。
        </p>
      </div>
      <div class="pg__actions">
        <Btn
          v-for="item in modes"
          :key="item.value"
          :variant="mode === item.value ? 'primary' : 'default'"
          size="sm"
          @click="setMode(item.value)"
        >
          {{ item.label }}
        </Btn>
      </div>
    </header>

    <!-- 令牌 -->
    <section class="pg__group">
      <h2 class="pg__group-title">色彩令牌</h2>
      <div class="pg__grid">
        <div class="pg__card">
          <div class="pg__swatches">
            <div v-for="token in tokens" :key="token" class="pg__swatch">
              <span class="pg__chip" :style="swatchStyle(token)" />
              <code class="mono">{{ token }}</code>
            </div>
          </div>
        </div>
        <div class="pg__card">
          <div v-for="item in sizes" :key="item.name" class="pg__type-row">
            <code class="mono">{{ item.name }}</code>
            <span :style="{ fontSize: item.size, lineHeight: item.lh }">{{ item.text }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 基础 -->
    <section class="pg__group">
      <h2 class="pg__group-title">基础组件</h2>
      <div class="pg__grid">
        <Card title="Btn" class="pg__wide-2">
          <template #extra><Badge tone="brand">每区域最多 1 个 primary</Badge></template>
          <div class="pg__row">
            <Btn variant="primary" :icon="Plus">添加账号</Btn>
            <Btn variant="default" :icon="RefreshCw">刷新额度</Btn>
            <Btn variant="quiet">取消</Btn>
            <Btn variant="danger" :icon="Trash2">删除</Btn>
            <Btn variant="danger" solid>确认删除</Btn>
          </div>
          <div class="pg__row">
            <Btn size="sm">小 28</Btn>
            <Btn size="md">中 32</Btn>
            <Btn size="lg">大 36</Btn>
            <Btn variant="primary" loading>保存中</Btn>
            <Btn disabled>已禁用</Btn>
          </div>
        </Card>

        <Card title="IconBtn / StatusDot">
          <div class="pg__row">
            <IconBtn label="刷新" :icon="RefreshCw" />
            <IconBtn label="设置" :icon="Settings2" />
            <IconBtn label="删除" :icon="Trash2" tone="danger" />
            <IconBtn label="帮助" :icon="CircleHelp" active />
          </div>
          <div class="pg__row">
            <StatusDot tone="ok" label="运行中" pulse />
            <StatusDot tone="warn" label="冷却" />
            <StatusDot tone="danger" label="熔断" />
            <StatusDot tone="neutral" label="已禁用" />
          </div>
        </Card>

        <Card title="Badge / Chip">
          <div class="pg__row">
            <Badge v-for="tone in badgeTones" :key="tone" :tone="tone">{{ tone }}</Badge>
          </div>
          <div class="pg__row">
            <Badge tone="ok" dot>健康</Badge>
            <Badge tone="warn" dot>部分失败</Badge>
            <Badge tone="danger" dot>已过期</Badge>
          </div>
          <div class="pg__row">
            <Chip>gpt-4o</Chip>
            <Chip mono :copy="true" value="claude-3-7-sonnet">claude-3-7-sonnet</Chip>
            <Chip :closable="true">可关闭</Chip>
          </div>
        </Card>

        <Card title="Copyable / CodeBlock">
          <div class="pg__stack">
            <Copyable value="sk-mh3f9e2b7c1d9f2e" mask />
            <Copyable value="https://gateway.example.com/v1" />
            <CodeBlock :code="curl" language="bash" max-height="140px" />
          </div>
        </Card>

        <Card title="Segmented / HelpTip">
          <div class="pg__row">
            <Segmented v-model="range" :options="rangeOptions" aria-label="时间范围" />
            <span class="pg__text">
              额度按平台分别计算
              <HelpTip content="每个平台单独统计额度与到期时间，切换平台后数字会变化。" />
            </span>
          </div>
          <p class="pg__text">长说明收进 ? 气泡，页面主视线保持干净。</p>
        </Card>

        <Card title="KV" class="pg__wide-2">
          <KV :items="kvItems" />
        </Card>

        <Card title="Card（含 loading / footer / flush）">
          <template #extra><Btn variant="quiet" size="sm">次要操作</Btn></template>
          <p class="pg__text">卡片无阴影，只有 1px 边框；标题右侧 14px 转圈不遮罩内容。</p>
          <template #footer>
            <Btn variant="primary" size="sm">保存</Btn>
            <Btn variant="default" size="sm">取消</Btn>
          </template>
        </Card>

        <Card title="Section（卡片内分组）">
          <Section title="第一组" first>
            <p class="pg__text">组与组之间 1px --border-soft 分隔。</p>
          </Section>
          <Section title="第二组">
            <p class="pg__text">不要卡片套卡片。</p>
          </Section>
        </Card>

        <Card title="Empty / ErrorBlock" class="pg__wide">
          <div class="pg__split">
            <Empty description="还没有账号。添加账号或导入账号。">
              <Btn variant="primary" size="sm" :icon="Plus">添加账号</Btn>
              <Btn variant="default" size="sm">导入账号</Btn>
            </Empty>
            <ErrorBlock
              title="额度刷新失败：上游返回 401，令牌可能已过期。请重新登录该账号。"
              detail="POST /api/accounts/acc_8f2c1d7a/refresh → 401 Unauthorized&#10;{ &quot;detail&quot;: &quot;token expired at 2026-11-22T17:03:11Z&quot; }"
              @retry="retryCount += 1"
            />
          </div>
          <p class="pg__text">已重试 {{ retryCount }} 次。</p>
        </Card>
      </div>
    </section>

    <!-- 数据展示 -->
    <section class="pg__group">
      <h2 class="pg__group-title">数据展示</h2>
      <div class="pg__grid">
        <div class="pg__full">
          <StatStrip :items="stats" />
        </div>

        <Card title="StatCard（单张）">
          <StatCard :item="{ label: '今日 Token', value: '150.9K', foot: '输入 120.4K · 输出 30.5K' }" />
        </Card>

        <Card title="Meter">
          <div class="pg__stack">
            <Meter :value="72" tone="brand" show-value />
            <Meter :value="46" tone="warn" show-value />
            <Meter :value="12" tone="danger" show-value />
            <Meter :value="88" tone="ok" :show-value="true" value-text="88 / 100" />
          </div>
        </Card>

        <Card title="StackMeter">
          <StackMeter :items="stackItems" unit=" 个" />
        </Card>

        <Card title="Ring">
          <div class="pg__center">
            <Ring :value="87.4" text="87.4%" unit="%" label="缓存命中率" tone="ok" />
          </div>
        </Card>

        <Card title="BarList（替代饼图）">
          <BarList :items="barItems" :limit="4" />
        </Card>

        <Card title="BulkBar" class="pg__wide">
          <BulkBar :count="3" @cancel="tableSelection = []">
            <Btn variant="default" size="sm">批量刷新</Btn>
            <Btn variant="danger" size="sm" :icon="Trash2">批量删除</Btn>
          </BulkBar>
        </Card>

        <Card title="FilterBar" class="pg__full">
          <FilterBar
            :chips="filterChips"
            :summary="filterSummary"
            @change="onFilterChange"
            @reset="onFilterReset"
            @remove="onFilterRemove"
          >
            <template #default="{ change }">
              <n-select
                :value="'workbuddy'"
                :options="[
                  { label: '全部平台', value: 'all' },
                  { label: 'WorkBuddy', value: 'workbuddy' },
                  { label: 'Loomy', value: 'loomy' },
                ]"
                style="width: 160px"
                @update:value="(value: string) => change('platform', value)"
              />
              <DateRange :model-value="dateRange" @update:model-value="onDateRange" />
              <n-input placeholder="搜索账号 / Key" style="width: 240px" />
            </template>
          </FilterBar>
          <p v-if="dateRange" class="pg__text pg__range">
            当前区间：{{ new Date(dateRange[0]).toLocaleString('zh-CN') }} –
            {{ new Date(dateRange[1]).toLocaleString('zh-CN') }}
          </p>
        </Card>

        <section class="pg__full">
          <Card title="DataTable" flush>
            <template #extra>
              <div class="pg__row">
                <Btn variant="quiet" size="sm" @click="tableLoading = !tableLoading">
                  {{ tableLoading ? '停止加载' : '模拟加载' }}
                </Btn>
                <Btn variant="quiet" size="sm" @click="tableEmpty = !tableEmpty">
                  {{ tableEmpty ? '填充数据' : '空状态' }}
                </Btn>
              </div>
            </template>
            <DataTable
              table-id="playground-accounts"
              :columns="tableColumns"
              :data="tableData"
              row-key="id"
              :selectable="true"
              :loading="tableLoading"
              :page="tablePage"
              :page-size="tablePageSize"
              :item-count="tableData.length"
              :clickable-rows="false"
              @update:page="onPageChange"
              @update:page-size="onPageSizeChange"
              @update:checked-keys="onSelectionChange"
            >
              <template #bulk>
                <Btn variant="default" size="sm" :icon="RefreshCw">批量刷新</Btn>
                <Btn variant="danger" size="sm" :icon="Trash2">批量删除</Btn>
              </template>
            </DataTable>
          </Card>
        </section>
      </div>
    </section>

    <!-- 图表 -->
    <section class="pg__group">
      <h2 class="pg__group-title">图表</h2>
      <div class="pg__grid">
        <Card title="Chart · 折线（applyLineDefaults）" class="pg__full">
          <Chart :option="lineOption" height="240px" />
        </Card>
        <Card title="Chart · 柱状（applyBarDefaults）" class="pg__full">
          <Chart :option="barOption" height="240px" loading />
        </Card>
        <Card title="RouteRibbon（6 个假平台）" class="pg__full">
          <template #extra><Badge tone="neutral">全站唯一视觉记忆点</Badge></template>
          <RouteRibbon :source="{ label: '调用方', total: ribbonTotal }" :lanes="ribbonLanes" />
        </Card>
        <Card title="RouteRibbon · 空数据" class="pg__full">
          <RouteRibbon :source="{ label: '调用方', total: 0 }" :lanes="emptyLanes" />
        </Card>
      </div>
    </section>

    <!-- 浮层 -->
    <section class="pg__group">
      <h2 class="pg__group-title">表单与浮层</h2>
      <div class="pg__grid">
        <Card title="FormDrawer / FormField">
          <Btn variant="primary" @click="showForm = true">打开表单抽屉</Btn>
          <p class="pg__text">新建 / 编辑一律用右侧抽屉，底部固定「取消 + 动词按钮」。</p>
        </Card>

        <Card title="ConfirmDialog">
          <div class="pg__row">
            <Btn variant="danger" @click="showConfirm = true">删除 3 个账号</Btn>
            <Btn variant="danger" solid @click="showTyped = true">清空全部日志</Btn>
          </div>
          <p class="pg__text">高风险操作要求输入确认文字或勾选「我已了解」。</p>
        </Card>

        <Card title="DetailDrawer">
          <Btn variant="default" @click="showDetail = true">查看日志详情</Btn>
        </Card>

        <Card title="JobProgress">
          <template #extra>
            <Btn variant="quiet" size="sm" @click="toggleJobs">
              {{ jobs.length ? '隐藏任务卡' : '显示任务卡' }}
            </Btn>
          </template>
          <p class="pg__text">右下角浮动卡片，多个任务堆叠；轮询由 useJob(jobId) 负责。</p>
        </Card>
      </div>
    </section>

    <!-- 身份 -->
    <section class="pg__group">
      <h2 class="pg__group-title">平台与模型图标</h2>
      <div class="pg__grid">
        <Card title="PlatformLogo" class="pg__full">
          <div class="pg__logos">
            <div v-for="id in platformIds" :key="id" class="pg__logo-item">
              <PlatformLogo :id="id" :size="20" />
              <span class="pg__logo-id">{{ id }}</span>
            </div>
          </div>
        </Card>
        <Card title="ModelIcon" class="pg__full">
          <div class="pg__logos">
            <div v-for="vendor in modelVendors" :key="vendor" class="pg__logo-item">
              <ModelIcon :model="vendor" :size="18" />
              <span class="pg__logo-id">{{ vendor }}</span>
            </div>
          </div>
        </Card>
      </div>
    </section>

    <FormDrawer
      v-model:show="showForm"
      title="编辑账号"
      size="md"
      submit-text="保存"
      @submit="showForm = false"
    >
      <FormField label="账号名称" required hint="仅用于在账号池里识别，不会发给上游。">
        <n-input v-model:value="formName" placeholder="例如：主账号" />
      </FormField>
      <FormField label="平台">
        <n-select :value="'workbuddy'" :options="[{ label: 'WorkBuddy', value: 'workbuddy' }]" />
      </FormField>
      <FormField label="备注">
        <n-input type="textarea" :rows="3" placeholder="可选" />
      </FormField>
    </FormDrawer>

    <ConfirmDialog
      v-model:show="showConfirm"
      title="删除 3 个账号？"
      content="删除后无法恢复，相关用量记录保留。"
      danger
      confirm-text="删除"
      @confirm="confirmDelete"
    />

    <ConfirmDialog
      v-model:show="showTyped"
      title="清空全部用量日志？"
      content="此操作不可恢复，且会影响统计报表。"
      danger
      confirm-text="清空"
      require-text="清空日志"
      require-checkbox="我已了解后果"
      @confirm="confirmClear"
    />

    <DetailDrawer
      v-model:show="showDetail"
      title="请求详情"
      :badge="{ tone: 'danger', label: '上游超时' }"
      subtitle="2026-11-23 01:12:08"
    >
      <Section title="基本信息" first>
        <KV
          :items="[
            { label: '请求 ID', value: 'req_7f3a91c2', mono: true, copy: true },
            { label: '平台', value: 'WorkBuddy' },
            { label: '模型', value: 'gpt-4o', mono: true },
            { label: '耗时', value: '30,000 ms' },
          ]"
        />
      </Section>
      <Section title="响应">
        <CodeBlock
          :code="'{ &quot;error&quot;: { &quot;message&quot;: &quot;upstream timeout&quot; } }'"
          language="json"
          max-height="120px"
        />
      </Section>
    </DetailDrawer>

    <JobProgress
      :jobs="jobs"
      @view="() => undefined"
      @hide="hideJob"
    />

    <p v-if="confirmResult" class="pg__result">最近操作：{{ confirmResult }}</p>
  </main>
</template>

<style scoped>
.pg {
  padding: var(--sp-8);
  max-width: var(--content-max);
  display: grid;
  gap: var(--sp-6);
  background: var(--bg);
}

.pg__head {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3);
  align-items: flex-start;
  justify-content: space-between;
}

.pg__title {
  font-size: var(--fs-h1);
  line-height: var(--lh-h1);
  font-weight: 600;
  color: var(--fg);
}

.pg__hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  margin-top: var(--sp-1);
}

.pg__actions {
  display: flex;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.pg__group {
  display: grid;
  gap: var(--sp-3);
}

.pg__group-title {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.pg__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--sp-4);
  align-items: start;
}

.pg__full {
  grid-column: 1 / -1;
}

.pg__wide {
  grid-column: 1 / -1;
}

.pg__card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  padding: var(--sp-5);
  display: grid;
  gap: var(--sp-3);
}

.pg__swatches {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: var(--sp-2);
}

.pg__swatch {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
  min-width: 0;
}

.pg__chip {
  width: 28px;
  height: 28px;
  border-radius: var(--r-chip);
  border: 1px solid var(--border);
  flex: none;
}

.pg__type-row {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: var(--sp-3);
  align-items: baseline;
  padding: var(--sp-2) 0;
  border-bottom: 1px solid var(--border-soft);
}

.pg__type-row:last-child {
  border-bottom: 0;
}

.pg__type-row code {
  color: var(--fg-muted);
}

.pg__row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  align-items: center;
}

.pg__stack {
  display: grid;
  gap: var(--sp-3);
  min-width: 0;
}

.pg__text {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.pg__split {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--sp-4);
  align-items: start;
}

.pg__center {
  display: flex;
  justify-content: center;
  padding: var(--sp-2) 0;
}

.pg__logos {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3) var(--sp-5);
}

.pg__logo-item {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 140px;
}

.pg__logo-id {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.pg__result {
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.pg__range {
  margin-top: var(--sp-2);
}

@media (max-width: 640px) {
  .pg {
    padding: var(--sp-4);
  }

  .pg__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
