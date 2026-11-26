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
  IconBtn,
  Section,
  Segmented,
  type KvItem,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useRevisit, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError } from '@/api/http'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
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
  headerSourceTone,
  sourceLabel,
  sourceTone,
  sourceRows,
  type ClientDetectPayload,
  type ClientProfilePayload,
} from '@/api/client'

const { t } = useI18n()
const helpText = computed(() => t('page.client.help'))
const scanHelp = computed(() => t('page.client.scanHelp'))
const uaHelp = computed(() => t('page.client.uaHelp'))
const headersHelp = computed(() => t('page.client.headersHelp'))
const sections = computed(() => [
  { id: 'client-scan', title: t('page.client.section.scan') },
  { id: 'client-runtime', title: t('page.client.section.runtime') },
  { id: 'client-ua', title: t('page.client.section.ua') },
  { id: 'client-version', title: t('page.client.section.version') },
  { id: 'client-fingerprint', title: t('page.client.section.fingerprint') },
  { id: 'client-headers', title: t('page.client.section.headers') },
  { id: 'client-sources', title: t('page.client.section.sources') },
])

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
/** 保存条显示「有 N 项未保存」：版本、指纹两块各算一项。 */
const dirty = computed(() => Number(versionDirty.value) + Number(fingerprintDirty.value))
const savingCards = computed(() => saving.version || saving.fingerprint)

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
    if (profile.value !== null) toast.error(failureText(t('page.client.action.refresh'), err))
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
    toast.warning(t('page.client.toast.noChanges'))
    return
  }
  saving[kind] = true
  try {
    const payload = await saveClientProfile({ profile: result.profile, merge: true })
    profile.value = payload
    fillForms()
    toast.success(t('page.client.toast.saved'))
    if (payload.errors?.length) toast.warning(payload.errors.join(t('page.client.listSeparator')))
  } catch (err) {
    toast.error(
      failureText(
        kind === 'version' ? t('page.client.action.saveVersion') : t('page.client.action.saveFingerprint'),
        err,
      ),
    )
  } finally {
    saving[kind] = false
  }
}

function resetCard(kind: 'version' | 'fingerprint'): void {
  const baseline = kind === 'version' ? versionBaseline : fingerprintBaseline
  const form = kind === 'version' ? versionForm : fingerprintForm
  fillForm(form, baseline)
}

/** 保存条「保存」：有改动的卡片依次保存（各自的校验与提示不变）。 */
async function saveAll(): Promise<void> {
  if (savingCards.value) return
  if (versionDirty.value) await saveCard('version')
  if (fingerprintDirty.value) await saveCard('fingerprint')
}

/** 保存条「放弃」：两块都回到上次读到的值。 */
function discardAll(): void {
  resetCard('version')
  resetCard('fingerprint')
  toast.info(t('page.client.toast.restored'))
}

/** 取值策略：卡片内的分段控件（原来是页头一个伪装成按钮的状态，第 2 轮审查 P2）。 */
const sourceOptions = computed(() => [
  { label: t('page.client.sourceMode.detect'), value: 'auto' },
  { label: t('page.client.sourceMode.saved'), value: 'saved' },
])
const sourceValue = computed(() => (profile.value?.source === 'saved' ? 'saved' : 'auto'))

function onSourceChange(value: string | number): void {
  if (String(value) === sourceValue.value) return
  void toggleSource()
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
      next === 'saved' ? t('page.client.toast.sourceSaved') : t('page.client.toast.sourceDetect'),
    )
  } catch (err) {
    toast.error(failureText(t('page.client.action.toggleSource'), err))
  } finally {
    saving.source = false
  }
}

async function resetAll(): Promise<void> {
  const ok = await confirm({
    title: t('page.client.confirmReset.title'),
    message: t('page.client.confirmReset.body'),
    okText: t('page.client.confirmReset.ok'),
    danger: true,
  })
  if (!ok) return
  saving.reset = true
  try {
    profile.value = await resetClientProfile()
    detected.value = null
    fillForms()
    toast.success(t('page.client.toast.reset'))
  } catch (err) {
    toast.error(failureText(t('page.client.action.reset'), err))
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
    if (payload.has_client) toast.success(payload.message || t('page.client.toast.detected'))
    else toast.warning(payload.message || t('page.client.toast.notDetected'))
    await loadProfile()
  } catch (err) {
    toast.error(failureText(t('page.client.action.detect'), err))
  } finally {
    saving.detect = false
  }
}

/* ------------------------------------------------------------------ *
 * 只读视图
 * ------------------------------------------------------------------ */

const scan = computed(() => scanView(profile.value, detected.value))
const runtime = computed(() => runtimeView(detected.value?.runtime_diagnostics || profile.value?.runtime_diagnostics))
const uaText = computed(() => detected.value?.ua || profile.value?.ua || t('page.client.uaUnknown'))
const headerTableRows = computed<TableRow[]>(() => headerRows(profile.value) as unknown as TableRow[])
const sources = computed(() => sourceRows(profile.value?.sources))
const sourcesOpen = ref(false)
const visibleSources = computed(() => (sources.value.length > 10 && !sourcesOpen.value ? sources.value.slice(0, 10) : sources.value))

const platformLabel = computed(() => platform.currentSpec?.label || platform.storagePlatform || 'WorkBuddy')

const scanKv = computed<KvItem[]>(() => {
  if (!profile.value) return []
  return [
    { label: t('page.client.kv.state'), value: scan.value.stateText },
    {
      label: t('page.client.kv.detected'),
      value: profile.value.has_client
        ? t('page.client.kv.detectedYes', { count: Object.keys(profile.value.detected || {}).length })
        : t('page.client.kv.detectedNo'),
    },
    {
      label: t('page.client.kv.savedFields'),
      value: String(Object.keys(profile.value.saved || {}).length),
    },
    {
      label: t('page.client.kv.scanDirs'),
      value: t('page.client.kv.scanDirsValue', { count: scan.value.dirs.length }),
    },
  ]
})

const headerColumns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    title: t('page.client.col.header'),
    mobile: 'title',
    minWidth: 220,
    render: (row: TableRow) => (row as unknown as { name: string }).name,
  },
  {
    key: 'value',
    title: t('page.client.col.value'),
    mobile: 'subtitle',
    minWidth: 260,
    render: (row: TableRow) => (row as unknown as { value: string }).value,
  },
  {
    key: 'source',
    title: t('page.client.col.source'),
    mobile: 'meta',
    minWidth: 200,
    render: (row: TableRow) => {
      const item = row as unknown as { source: string }
      const tone = headerSourceTone(item.source)
      return h(Badge, { tone }, { default: () => item.source })
    },
  },
])

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let stopPlatformWatch: (() => void) | null = null

// 二次进入（KeepAlive 命中）：保留旧数据、不出骨架，后台静默刷新（11-polish-motion §2）。
// 设置型页面：有未保存修改时不刷新，免得覆盖草稿。
useRevisit(
  () => {
    if (!dirty.value) void loadProfile()
  },
  { minIntervalMs: 10_000 },
)

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
    :title="t('page.client.title')"
    :help="helpText"
    :sections="sections"
    :dirty="dirty"
    :saving="savingCards"
    @save="saveAll"
    @discard="discardAll"
  >
    <template #actions>
      <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="loading" @click="loadProfile" />
      <Btn variant="quiet" :loading="saving.reset" :disabled="!profile" @click="resetAll">
        {{ t('page.client.action.reset') }}
      </Btn>
      <Btn variant="primary" :icon="Search" :loading="saving.detect" @click="runDetect">
        {{ t('page.client.action.detect') }}
      </Btn>
    </template>

    <ErrorBlock
      v-if="pageError"
      :title="t('page.client.loadFailed')"
      :detail="pageError.detail"
      @retry="loadProfile"
    />
    <SettingsCard
      v-else-if="firstLoading"
      id="client-loading"
      :title="t('page.client.loadingCard')"
    >
      <div class="cl-skeleton" aria-busy="true">
        <NSkeleton v-for="index in 6" :key="index" height="32px" />
      </div>
    </SettingsCard>

    <template v-else>
      <!-- 1. 扫描位置 -->
      <SettingsCard
        id="client-scan"
        :title="t('page.client.section.scan')"
        :description="t('page.client.scanIntro', { platform: platformLabel })"
      >
        <template #actions>
          <HelpTip :content="scanHelp" :label="t('page.client.scanHelpLabel')" />
        </template>

        <FormField :label="t('page.client.kv.sourceMode')" :hint="t('page.client.field.sourceHint')">
          <Segmented
            :options="sourceOptions"
            :model-value="sourceValue"
            :aria-label="t('page.client.kv.sourceMode')"
            @update:model-value="onSourceChange"
          />
        </FormField>

        <Section :title="t('page.client.section.state')">
          <div class="cl-rows">
            <div v-for="item in scanKv" :key="item.label" class="cl-row">
              <span class="cl-row__label">{{ item.label }}</span>
              <span class="cl-row__value">{{ item.value || '—' }}</span>
            </div>
          </div>
        </Section>

        <!-- 没有目录时不再单独列一节「扫描目录 —」：上面的状态行已经写了「0 个」。 -->
        <Section v-if="scan.dirs.length" :title="t('page.client.section.dirs')">
          <ul class="cl-list">
            <li v-for="dir in scan.dirs" :key="dir"><code class="mono">{{ dir }}</code></li>
          </ul>
        </Section>

        <Section v-if="scan.items.length" :title="t('page.client.section.credentialPaths')">
          <ul class="cl-list">
            <li v-for="(item, index) in scan.items" :key="index">
              <span class="cl-tag">
                {{ item.plain ? t('page.client.credential.plain') : t('page.client.credential.encrypted') }}
              </span>
              <code class="mono">{{ item.where }}</code>
              <span v-if="item.who" class="cl-sub">{{ item.who }}</span>
            </li>
          </ul>
        </Section>

        <p v-if="scan.baseUrl" class="cl-sub">
          {{ t('page.client.scanBaseUrl') }}<code class="mono">{{ scan.baseUrl }}</code>
        </p>
        <p v-if="scan.hint" class="cl-sub">{{ scan.hint }}</p>
        <p v-for="err in scan.errors" :key="err" class="cl-error">{{ err }}</p>
      </SettingsCard>

      <!-- 2. 运行时诊断 -->
      <SettingsCard
        id="client-runtime"
        :title="t('page.client.section.runtime')"
        plain
      >
        <template #actions>
          <Badge v-if="runtime.warning" tone="warn" dot>{{ t('page.client.runtime.warning') }}</Badge>
          <HelpTip :content="t('page.client.runtime.help')" :label="t('page.client.runtime.helpLabel')" />
        </template>

        <Section :title="t('page.client.section.capabilities')" first>
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

        <Section :title="t('page.client.section.headerGroups')">
          <div v-if="runtime.groups.length" class="cl-rows">
            <div v-for="group in runtime.groups" :key="group.name" class="cl-row">
              <span class="cl-row__label mono">{{ group.name }}</span>
              <span class="cl-row__value mono cl-wrap">{{ group.headers.join(', ') || '—' }}</span>
            </div>
          </div>
          <Empty v-else :description="t('page.client.runtime.empty')" />
        </Section>
        <p class="cl-note">{{ runtime.note }}</p>
      </SettingsCard>

      <!-- 3. 生效中的 UA -->
      <SettingsCard id="client-ua" :title="t('page.client.section.ua')" plain>
        <template #actions>
          <HelpTip :content="uaHelp" :label="t('page.client.uaHelpLabel')" />
        </template>
        <CodeBlock :code="uaText" language="http" />
        <p class="cl-note">{{ t('page.client.uaNote') }}</p>
      </SettingsCard>

      <!-- 4. 版本与归属 -->
      <SettingsCard
        id="client-version"
        :title="t('page.client.section.version')"
        :description="t('page.client.version.description')"
        :dirty="versionDirty"
      >
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
      </SettingsCard>

      <!-- 5. 桌面事件指纹 -->
      <SettingsCard
        id="client-fingerprint"
        :title="t('page.client.section.fingerprint')"
        :description="t('page.client.fingerprint.description')"
        :dirty="fingerprintDirty"
      >
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
      </SettingsCard>

      <!-- 6. 风控 / 审计请求头 -->
      <SettingsCard id="client-headers" :title="t('page.client.section.headers')" plain>
        <template #actions>
          <HelpTip :content="headersHelp" :label="t('page.client.headersHelpLabel')" />
        </template>
        <DataTable
          table-id="client-headers"
          :columns="headerColumns"
          :data="headerTableRows"
          row-key="name"
          :pagination="false"
          :virtual-scroll="false"
          :empty-description="t('page.client.headers.empty')"
        >
          <template #empty>
            <Empty :description="t('page.client.headers.empty')" />
          </template>
        </DataTable>
      </SettingsCard>

      <!-- 7. 取值来源 -->
      <SettingsCard
        id="client-sources"
        :title="t('page.client.section.sources')"
        :description="t('page.client.sources.intro')"
        plain
      >
        <div v-if="sources.length" class="cl-rows">
          <div v-for="row in visibleSources" :key="row.key" class="cl-row">
            <span class="cl-row__label mono">{{ row.key }}</span>
            <span class="cl-row__value">
              <Badge :tone="row.tone" :title="row.source">{{ sourceLabel(row.source) }}</Badge>
            </span>
          </div>
        </div>
        <Empty v-else :description="t('page.client.sources.empty')" />
        <div v-if="sources.length > 10" class="cl-more">
          <Btn variant="quiet" size="sm" @click="sourcesOpen = !sourcesOpen">
            {{
              sourcesOpen
                ? t('common.collapseDetails')
                : t('page.client.sources.expand', { count: sources.length })
            }}
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
  font-size: var(--fs-mono);
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