<script setup lang="ts">
/**
 * S27 客户端参数 /client（模板 C）
 *
 * 契约：docs/ui-v2/contracts/client.md（本步新建，末尾含「实现对照」）
 * 旧版：#panel-client（admin/console/index.html 2339–2423）+ console-app.js 13571–14318 / 14972–15040
 * 后端：admin/routers/client_profile.py、admin/client_profile.py、admin/routers/client_paths.py
 *
 * 7 个区块与设计稿 P18 一一对应：扫描位置 / 运行时诊断 / 生效中的 UA / 版本与归属 /
 * 桌面事件指纹 / 风控与审计请求头 / 取值来源。
 * 可编辑项（版本与归属、桌面事件指纹）按卡片保存（PUT merge 语义）；
 * 其余为只读 KV / 表格 / CodeBlock。
 */
import { computed, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { NInput, NInputNumber, NSkeleton } from 'naive-ui'
import { RefreshCw, Search } from 'lucide-vue-next'
import SettingsLayout from '@/layout/SettingsLayout.vue'
import SettingsCard from '@/layout/SettingsCard.vue'
import {
  Badge,
  Btn,
  CodeBlock,
  DataTable,
  Empty,
  ErrorBlock,
  FormField,
  HelpTip,
  Section,
  type KvItem,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import {
  FINGERPRINT_FIELDS,
  VERSION_FIELDS,
  buildProfilePayload,
  detectClient,
  dirtyFields,
  getClientProfile,
  headerRows,
  profileForm,
  resetClientProfile,
  runtimeView,
  saveClientProfile,
  scanView,
  sourceLabel,
  sourceRows,
  sourceTone,
  type ClientDetectPayload,
  type ClientProfilePayload,
} from '@/api/client'

const HELP_TEXT =
  'UA、版本号与风控头按平台分别保存，改完保存即生效，不需要重启服务。' +
  '档案由「内置兜底 ← 现场探测 / 已保存 ← 环境变量」合成；UA 始终由桌面端版本与 CLI 版本现拼。' +
  '线上实例探测不到客户端时，请把取值策略切换为「钉住已保存值」，否则会退回内置兜底版本号。'

const SCAN_HELP = '这些目录里没有凭据文件不代表未登录，仅表示未在本机落盘。目录名固定，系统会自动在所有盘符与父文件夹中定位。'

const UA_HELP =
  'UA 由「桌面端版本」与「CLI 版本」自动拼成三段式，两者一致才能避免「UA 报旧版本、X-IDE-Version 报新版本」的错位。' +
  '要整体自定义请用环境变量 WORKBUDDY_USER_AGENT。'

const HEADERS_HELP =
  '客户端源码中观察到的兼容请求头。实际发送范围由端点决定；多个默认头会与保存值合并，不会互相覆盖。' +
  '「运行时生成」的头由账号凭据 / 调用方上下文动态产生，不在本页保存。'

const SECTIONS = [
  { id: 'client-scan', title: '扫描位置' },
  { id: 'client-runtime', title: '运行时诊断' },
  { id: 'client-ua', title: '生效中的 User-Agent' },
  { id: 'client-version', title: '版本与归属' },
  { id: 'client-fingerprint', title: '桌面事件指纹' },
  { id: 'client-headers', title: '风控 / 审计请求头' },
  { id: 'client-sources', title: '取值来源' },
]

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

const profile = ref<ClientProfilePayload | null>(null)
const detected = ref<ClientDetectPayload | null>(null)
const loading = ref(false)
const error = ref<ErrorState | null>(null)
const saving = reactive({ version: false, fingerprint: false, source: false, detect: false, reset: false })

const versionForm = reactive<Record<string, string>>({})
const fingerprintForm = reactive<Record<string, string>>({})
let versionBaseline: Record<string, string> = {}
let fingerprintBaseline: Record<string, string> = {}

function fillForm(target: Record<string, string>, source: Record<string, string>): void {
  for (const key of Object.keys(source)) target[key] = source[key]
}

function fillForms(): void {
  if (!profile.value) return
  if (!versionDirty.value) {
    versionBaseline = profileForm(profile.value.effective, VERSION_FIELDS)
    fillForm(versionForm, versionBaseline)
  }
  if (!fingerprintDirty.value) {
    fingerprintBaseline = profileForm(profile.value.effective, FINGERPRINT_FIELDS)
    fillForm(fingerprintForm, fingerprintBaseline)
  }
}

const versionDirty = computed(
  () => dirtyFields(versionForm, versionBaseline, VERSION_FIELDS).length > 0,
)
const fingerprintDirty = computed(
  () => dirtyFields(fingerprintForm, fingerprintBaseline, FINGERPRINT_FIELDS).length > 0,
)
const dirty = computed(() => versionDirty.value || fingerprintDirty.value)

const firstLoading = computed(() => profile.value === null && loading.value)
const pageError = computed(() => (profile.value === null ? error.value : null))

async function loadProfile(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    profile.value = await getClientProfile()
    fillForms()
  } catch (err) {
    error.value = toErrorState(err)
    if (profile.value !== null) toast.error(failureText('刷新客户端参数', err))
  } finally {
    loading.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 保存 / 重置 / 取值策略
 * ------------------------------------------------------------------ */

async function saveCard(kind: 'version' | 'fingerprint'): Promise<void> {
  const fields = kind === 'version' ? VERSION_FIELDS : FINGERPRINT_FIELDS
  const form = kind === 'version' ? versionForm : fingerprintForm
  const result = buildProfilePayload(form, fields)
  if (result.errors.length) {
    toast.error(result.errors.join('；'))
    return
  }
  if (!Object.keys(result.profile).length) {
    toast.warning('没有要保存的改动（留空的字段不会提交）')
    return
  }
  saving[kind] = true
  try {
    const payload = await saveClientProfile({ profile: result.profile, merge: true })
    profile.value = payload
    fillForms()
    toast.success('已保存，新值立即生效')
    if (payload.errors?.length) toast.warning(payload.errors.join('；'))
  } catch (err) {
    toast.error(failureText(kind === 'version' ? '保存版本与归属' : '保存桌面事件指纹', err))
  } finally {
    saving[kind] = false
  }
}

function resetCard(kind: 'version' | 'fingerprint'): void {
  const baseline = kind === 'version' ? versionBaseline : fingerprintBaseline
  const form = kind === 'version' ? versionForm : fingerprintForm
  fillForm(form, baseline)
  toast.info('已恢复为当前生效值')
}

async function toggleSource(): Promise<void> {
  if (!profile.value || saving.source) return
  const next = profile.value.source === 'saved' ? 'auto' : 'saved'
  saving.source = true
  try {
    const payload = await saveClientProfile({ profile: {}, source: next })
    profile.value = payload
    fillForms()
    toast.success(
      next === 'saved' ? '已切换为「钉住已保存值」' : '已切换为「优先现场探测」',
    )
  } catch (err) {
    toast.error(failureText('切换取值策略', err))
  } finally {
    saving.source = false
  }
}

async function resetAll(): Promise<void> {
  const ok = await confirm({
    title: '重置客户端参数？',
    message: '清空后台保存的值，回到「优先现场探测 + 内置兜底」。已保存的版本号与指纹会丢失。',
    okText: '重置',
    danger: true,
  })
  if (!ok) return
  saving.reset = true
  try {
    profile.value = await resetClientProfile()
    detected.value = null
    fillForms()
    toast.success('已重置')
  } catch (err) {
    toast.error(failureText('重置客户端参数', err))
  } finally {
    saving.reset = false
  }
}

async function runDetect(): Promise<void> {
  if (saving.detect) return
  saving.detect = true
  try {
    const payload = await detectClient()
    detected.value = payload
    if (payload.has_client) toast.success(payload.message || '已探测到本机客户端')
    else toast.warning(payload.message || '未探测到本机客户端')
    await loadProfile()
  } catch (err) {
    toast.error(failureText('探测本机客户端', err))
  } finally {
    saving.detect = false
  }
}

/* ------------------------------------------------------------------ *
 * 只读视图
 * ------------------------------------------------------------------ */

const scan = computed(() => scanView(profile.value, detected.value))
const runtime = computed(() => runtimeView(detected.value?.runtime_diagnostics || profile.value?.runtime_diagnostics))
const uaText = computed(() => detected.value?.ua || profile.value?.ua || '（未知）')
const headerTableRows = computed<TableRow[]>(() => headerRows(profile.value) as unknown as TableRow[])
const sources = computed(() => sourceRows(profile.value?.sources))
const sourcesOpen = ref(false)
const visibleSources = computed(() => (sources.value.length > 10 && !sourcesOpen.value ? sources.value.slice(0, 10) : sources.value))

const sourceMode = computed(() => (profile.value?.source === 'saved' ? '钉住已保存值' : '优先现场探测'))
const platformLabel = computed(() => platform.currentSpec?.label || platform.storagePlatform || 'WorkBuddy')

const scanKv = computed<KvItem[]>(() => {
  if (!profile.value) return []
  return [
    { label: '状态', value: scan.value.stateText },
    { label: '本机客户端', value: profile.value.has_client ? '已探测到（' + Object.keys(profile.value.detected || {}).length + ' 项）' : '未探测到' },
    { label: '取值策略', value: sourceMode.value },
    { label: '已保存字段', value: String(Object.keys(profile.value.saved || {}).length) },
    { label: '扫描目录', value: String(scan.value.dirs.length) + ' 个' },
  ]
})

const headerColumns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    title: '请求头',
    mobile: 'title',
    minWidth: 220,
    render: (row: TableRow) => (row as unknown as { name: string }).name,
  },
  {
    key: 'value',
    title: '值',
    mobile: 'subtitle',
    minWidth: 260,
    render: (row: TableRow) => (row as unknown as { value: string }).value,
  },
  {
    key: 'source',
    title: '来源',
    mobile: 'meta',
    minWidth: 200,
    render: (row: TableRow) => {
      const item = row as unknown as { source: string }
      const tone = item.source.startsWith('档案值') ? 'brand' : item.source.startsWith('SDK') ? 'ok' : 'neutral'
      return h(Badge, { tone }, { default: () => item.source })
    },
  },
])

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let stopPlatformWatch: (() => void) | null = null

onMounted(() => {
  void loadProfile()
  // 客户端档案是 WorkBuddy 的全局设置；按任务书保持平台订阅（切平台重取一次，
  // 不改变档案内容）。platform-context.md 也把 client 归为「与平台无关」。
  stopPlatformWatch = platform.onChange(() => {
    void loadProfile()
  })
})

onBeforeUnmount(() => {
  stopPlatformWatch?.()
  stopPlatformWatch = null
})
</script>

<template>
  <SettingsLayout
    title="客户端参数"
    :help="HELP_TEXT"
    :sections="SECTIONS"
    :dirty="dirty"
  >
    <template #actions>
      <Btn variant="default" :icon="Search" :loading="saving.detect" @click="runDetect">探测本机客户端</Btn>
      <Btn variant="quiet" :loading="saving.source" :disabled="!profile" @click="toggleSource">
        取值策略：{{ sourceMode }}
      </Btn>
      <Btn variant="quiet" :loading="saving.reset" :disabled="!profile" @click="resetAll">重置</Btn>
      <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="loadProfile">刷新</Btn>
    </template>

    <ErrorBlock
      v-if="pageError"
      title="加载客户端参数失败"
      :detail="pageError.detail"
      @retry="loadProfile"
    />
    <SettingsCard v-else-if="firstLoading" id="client-loading" title="读取客户端参数…" hide-save hide-reset>
      <div class="cl-skeleton" aria-busy="true">
        <NSkeleton v-for="index in 6" :key="index" height="32px" />
      </div>
    </SettingsCard>

    <template v-else>
      <!-- 1. 扫描位置 -->
      <SettingsCard id="client-scan" title="扫描位置" :loading="saving.detect" hide-save hide-reset>
        <template #actions>
          <Btn variant="quiet" size="sm" :loading="saving.detect" @click="runDetect">重新探测</Btn>
          <HelpTip :content="SCAN_HELP" label="扫描位置说明" />
        </template>

        <p class="cl-sub">当前平台：{{ platformLabel }} · 本机路径与凭据位置（只读，客户端档案是 WorkBuddy 的全局设置）。</p>
        <Section title="状态" first>
          <div class="cl-rows">
            <div v-for="item in scanKv" :key="item.label" class="cl-row">
              <span class="cl-row__label">{{ item.label }}</span>
              <span class="cl-row__value">{{ item.value || '—' }}</span>
            </div>
          </div>
        </Section>

        <Section title="扫描目录">
          <ul v-if="scan.dirs.length" class="cl-list">
            <li v-for="dir in scan.dirs" :key="dir"><code class="mono">{{ dir }}</code></li>
          </ul>
          <p v-else class="cl-sub">—</p>
        </Section>

        <Section v-if="scan.items.length" title="凭据位置（不含令牌）">
          <ul class="cl-list">
            <li v-for="(item, index) in scan.items" :key="index">
              <span class="cl-tag">{{ item.plain ? '明文' : '加密' }}</span>
              <code class="mono">{{ item.where }}</code>
              <span v-if="item.who" class="cl-sub">{{ item.who }}</span>
            </li>
          </ul>
        </Section>

        <p v-if="scan.baseUrl" class="cl-sub">上游地址：<code class="mono">{{ scan.baseUrl }}</code></p>
        <p v-if="scan.hint" class="cl-sub">{{ scan.hint }}</p>
        <p v-for="err in scan.errors" :key="err" class="cl-error">{{ err }}</p>
      </SettingsCard>

      <!-- 2. 运行时诊断 -->
      <SettingsCard id="client-runtime" title="运行时诊断" hide-save hide-reset>
        <template #actions>
          <Badge v-if="runtime.warning" tone="warn" dot>有异常项</Badge>
          <HelpTip content="能力状态与请求头名称（不含令牌）。原生 SDK 不可用时不要手工填写或伪造设备令牌。" label="运行时诊断说明" />
        </template>

        <Section title="能力状态" first>
          <div class="cl-rows">
            <div v-for="row in runtime.rows" :key="row.label" class="cl-row">
              <span class="cl-row__label">{{ row.label }}</span>
              <span class="cl-row__value">
                <Badge :tone="row.tone">{{ row.value }}</Badge>
                <span v-if="row.detail" class="cl-sub">{{ row.detail }}</span>
              </span>
            </div>
          </div>
        </Section>

        <Section title="请求头字段组">
          <div v-if="runtime.groups.length" class="cl-rows">
            <div v-for="group in runtime.groups" :key="group.name" class="cl-row">
              <span class="cl-row__label mono">{{ group.name }}</span>
              <span class="cl-row__value mono cl-wrap">{{ group.headers.join(', ') || '—' }}</span>
            </div>
          </div>
          <Empty v-else description="没有运行时诊断信息（后端未提供 header_groups）。" />
        </Section>
        <p class="cl-note">{{ runtime.note }}</p>
      </SettingsCard>

      <!-- 3. 生效中的 UA -->
      <SettingsCard id="client-ua" title="生效中的 User-Agent" hide-save hide-reset>
        <template #actions><HelpTip :content="UA_HELP" label="User-Agent 说明" /></template>
        <CodeBlock :code="uaText" language="http" />
        <p class="cl-note">UA 始终由版本号现拼；想整体自定义请用环境变量 WORKBUDDY_USER_AGENT。</p>
      </SettingsCard>

      <!-- 4. 版本与归属 -->
      <SettingsCard
        id="client-version"
        title="版本与归属"
        description="改完保存即生效，不需要重启服务。留空提交的字段不会覆盖已保存值。"
        :dirty="versionDirty"
        :loading="saving.version"
        save-label="保存版本与归属"
        :save-disabled="!versionDirty || saving.version"
        @save="saveCard('version')"
        @reset="resetCard('version')"
      >
        <div class="cl-grid">
          <FormField
            v-for="field in VERSION_FIELDS"
            :key="field.key"
            :label="field.label"
            :hint="field.hint"
          >
            <NInputNumber
              v-if="field.type === 'int'"
              :value="versionForm[field.key] === '' ? null : Number(versionForm[field.key])"
              :min="0"
              :show-button="false"
              :disabled="saving.version"
              :aria-label="field.label"
              @update:value="(value: number | null) => (versionForm[field.key] = value === null ? '' : String(value))"
            />
            <NInput
              v-else
              :value="versionForm[field.key]"
              :disabled="saving.version"
              :aria-label="field.label"
              @update:value="(value: string) => (versionForm[field.key] = value)"
            />
          </FormField>
        </div>
      </SettingsCard>

      <!-- 5. 桌面事件指纹 -->
      <SettingsCard
        id="client-fingerprint"
        title="桌面事件指纹"
        description="遥测事件里的设备与产品形态；OS / 硬件 / 时区按本机系统实测（装了客户端才准确）。"
        :dirty="fingerprintDirty"
        :loading="saving.fingerprint"
        save-label="保存桌面事件指纹"
        :save-disabled="!fingerprintDirty || saving.fingerprint"
        @save="saveCard('fingerprint')"
        @reset="resetCard('fingerprint')"
      >
        <div class="cl-grid">
          <FormField
            v-for="field in FINGERPRINT_FIELDS"
            :key="field.key"
            :label="field.label"
            :hint="field.hint"
          >
            <NInputNumber
              v-if="field.type === 'int'"
              :value="fingerprintForm[field.key] === '' ? null : Number(fingerprintForm[field.key])"
              :min="0"
              :show-button="false"
              :disabled="saving.fingerprint"
              :aria-label="field.label"
              @update:value="(value: number | null) => (fingerprintForm[field.key] = value === null ? '' : String(value))"
            />
            <NInput
              v-else
              :value="fingerprintForm[field.key]"
              :disabled="saving.fingerprint"
              :aria-label="field.label"
              @update:value="(value: string) => (fingerprintForm[field.key] = value)"
            />
          </FormField>
        </div>
      </SettingsCard>

      <!-- 6. 风控 / 审计请求头 -->
      <SettingsCard id="client-headers" title="风控 / 审计请求头" hide-save hide-reset>
        <template #actions><HelpTip :content="HEADERS_HELP" label="请求头说明" /></template>
        <DataTable
          table-id="client-headers"
          :columns="headerColumns"
          :data="headerTableRows"
          row-key="name"
          :pagination="false"
          :virtual-scroll="false"
          empty-description="没有可展示的请求头（后端未提供 header_groups）。"
        >
          <template #empty>
            <Empty description="没有可展示的请求头（后端未提供 header_groups）。" />
          </template>
        </DataTable>
      </SettingsCard>

      <!-- 7. 取值来源 -->
      <SettingsCard id="client-sources" title="取值来源" hide-save hide-reset>
        <template #actions>
          <span class="cl-sub">每个参数来自现场探测还是存档（排障用）</span>
        </template>
        <div v-if="sources.length" class="cl-rows">
          <div v-for="row in visibleSources" :key="row.key" class="cl-row">
            <span class="cl-row__label mono">{{ row.key }}</span>
            <span class="cl-row__value">
              <Badge :tone="row.tone" :title="row.source">{{ sourceLabel(row.source) }}</Badge>
            </span>
          </div>
        </div>
        <Empty v-else description="没有来源信息。" />
        <div v-if="sources.length > 10" class="cl-more">
          <Btn variant="quiet" size="sm" @click="sourcesOpen = !sourcesOpen">
            {{ sourcesOpen ? '收起' : '展开全部（' + sources.length + ' 项）' }}
          </Btn>
        </div>
      </SettingsCard>
    </template>
  </SettingsLayout>
</template>

<style scoped>
.cl-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.cl-sub {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.cl-note {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 78ch;
}

.cl-error {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--danger);
}

.cl-rows {
  display: grid;
  gap: var(--sp-1);
}

.cl-row {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  align-items: baseline;
  gap: var(--sp-2);
  min-height: 24px;
}

.cl-row__label {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
}

.cl-row__value {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-width: 0;
  font-size: var(--fs-body);
  color: var(--fg);
}

.cl-wrap {
  word-break: break-word;
  white-space: normal;
}

.cl-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-3) var(--sp-4);
}

.cl-list {
  display: grid;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  color: var(--fg-2);
  word-break: break-all;
}

.cl-list code {
  font-size: 12.5px;
}

.cl-tag {
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
  margin-right: var(--sp-1);
}

.cl-more {
  display: flex;
  justify-content: center;
  padding-top: var(--sp-2);
}

@media (max-width: 760px) {
  .cl-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .cl-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }
}
</style>