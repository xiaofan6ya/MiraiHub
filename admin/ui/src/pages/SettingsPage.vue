<script setup lang="ts">
/**
 * S28 设置 /settings（模板 C：5 个区块，每卡独立保存）
 *
 * 契约：docs/ui-v2/contracts/settings.md（末尾「## 实现对照（S28）」）
 * 设计稿：docs/ui-v2/06-pages-d.md P23
 * 旧版：#panel-settings（2776–2926）+ console-app.js（loadRuntimeConfig 559 / saveRuntimeConfig 662 /
 *       loadProxyNodes 699 / loadInvitation 781 / changePassword 15257 / app_source 15055+）
 *
 * 三条口径：
 *   1. 运行配置的分组/类型/上下界/重启标记全部由后端 CATALOG 下发（不硬编码 attr 语义），只提交改动项；
 *   2. 代理地址 API 永不返回明文（列表里 ***），修改必须整段重填；
 *   3. 逆向产物在 ADMIN_DEV_TOOLS=0 时后端整路由 404 → 卡片降级说明，不报错。
 */
import { computed, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NInputNumber, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import { FileSearch, FolderOpen, PackageOpen, RefreshCw, ScanSearch, Trash2 } from 'lucide-vue-next'
import SettingsLayout from '@/layout/SettingsLayout.vue'
import SettingsCard from '@/layout/SettingsCard.vue'
import Badge from '@/components/base/Badge.vue'
import Btn from '@/components/base/Btn.vue'
import ErrorBlock from '@/components/base/ErrorBlock.vue'
import FormField from '@/components/overlay/FormField.vue'
import KV from '@/components/base/KV.vue'
import Meter from '@/components/data/Meter.vue'
import Section from '@/components/base/Section.vue'
import LanguageCard from '@/layout/LanguageCard.vue'
import DataTable from '@/components/data/DataTable.vue'
import Empty from '@/components/base/Empty.vue'
import FormDrawer from '@/components/overlay/FormDrawer.vue'
import type { TableColumn, TableRow } from '@/components'
import { useConfirm, usePolling, useToast } from '@/composables'
import { failureText } from '@/utils/error'
import { fmtTime } from '@/utils/format'
import {
  changePassword,
  createProxy,
  deleteProxy,
  detectAppSource,
  diffRuntimeFields,
  filterProxies,
  getAppSourceStatus,
  getInvitation,
  getRuntimeConfig,
  getUnpackProgress,
  groupRuntimeFields,
  listProxies,
  locateAppSource,
  patchProxy,
  planUnpack,
  proxyStateOf,
  proxySummaryText,
  removeUnpackedSource,
  restartPendingOf,
  saveInvitation,
  saveRuntimeConfig,
  searchAppSource,
  sourceStatusLines,
  startUnpack,
  unblockProxy,
  unpackPercent,
  validatePasswordChange,
  validateProxyInput,
  type AppSourceStatus,
  type InvitationPayload,
  type ProxyListPayload,
  type ProxyNode,
  type RuntimeField,
  type SourceHit,
  type UnpackProgress,
} from '@/api/settings'
import { useI18n } from '@/i18n'

const toast = useToast()
const { t, locale } = useI18n()
const helpText = computed(() => t('page.settings.help'))
const confirm = useConfirm()
const router = useRouter()

const sections = computed(() => [
  { id: 'settings-language', title: t('page.settings.sections.language') },
  { id: 'settings-runtime', title: t('page.settings.sections.runtime') },
  { id: 'settings-proxies', title: t('page.settings.sections.proxies') },
  { id: 'settings-invitation', title: t('page.settings.sections.invitation') },
  { id: 'settings-password', title: t('page.settings.sections.password') },
  { id: 'settings-appsource', title: t('page.settings.sections.appsource') },
])



/* ------------------------------------------------------------------ *
 * 运行配置
 * ------------------------------------------------------------------ */

const runtimeLoading = ref(false)
const runtimeSaving = ref(false)
const runtimeError = ref('')
const runtimeFields = ref<RuntimeField[]>([])
const runtimeDraft = reactive<Record<string, string | number | boolean | null>>({})
const restartPending = ref<RuntimeField[]>([])

const runtimeGroups = computed(() => groupRuntimeFields(runtimeFields.value, t))

function setRuntimeDraft(attr: string, value: string | number | boolean | null): void {
  runtimeDraft[attr] = value
}

function runtimeText(attr: string): string {
  const value = runtimeDraft[attr]
  return value === null || value === undefined ? '' : String(value)
}

function runtimeNumber(attr: string): number | null {
  const value = runtimeDraft[attr]
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}
const runtimeUpdates = computed(() => diffRuntimeFields(runtimeFields.value, runtimeDraft))
const runtimeDirty = computed(() => Object.keys(runtimeUpdates.value).length > 0)

function syncRuntimeDraft(fields: RuntimeField[]): void {
  for (const key of Object.keys(runtimeDraft)) delete runtimeDraft[key]
  for (const field of fields) {
    runtimeDraft[field.attr] =
      field.kind === 'bool' ? Boolean(field.value) : (field.value as string | number | null)
  }
}

async function loadRuntime(): Promise<void> {
  runtimeLoading.value = true
  runtimeError.value = ''
  try {
    const payload = await getRuntimeConfig()
    runtimeFields.value = Array.isArray(payload?.fields) ? payload.fields : []
    syncRuntimeDraft(runtimeFields.value)
  } catch (error) {
    runtimeError.value = failureText(t('page.settings.runtime.loadFailed'), error)
  } finally {
    runtimeLoading.value = false
  }
}

async function saveRuntime(): Promise<void> {
  if (!runtimeDirty.value || runtimeSaving.value) return
  runtimeSaving.value = true
  try {
    const result = await saveRuntimeConfig(runtimeUpdates.value)
    runtimeFields.value = Array.isArray(result?.fields) ? result.fields : runtimeFields.value
    syncRuntimeDraft(runtimeFields.value)
    restartPending.value = restartPendingOf(runtimeFields.value, result?.changed)
    const errors = Object.entries(result?.errors || {})
    if (errors.length) {
      toast.error(
        t('page.settings.runtime.partialFailed', {
          errors: errors.map(([attr, msg]) => attr + ' ' + msg).join('; '),
        }),
      )
    } else {
      toast.success(t('page.settings.runtime.saved', { count: result?.changed?.length || 0 }))
    }
  } catch (error) {
    toast.error(failureText(t('page.settings.runtime.saveFailed'), error))
  } finally {
    runtimeSaving.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 出口代理节点
 * ------------------------------------------------------------------ */

const proxyLoading = ref(false)
const proxyError = ref('')
const proxyData = ref<ProxyListPayload | null>(null)
const proxyPool = ref('')
const proxyQuery = ref('')
const proxyDrawer = ref(false)
const proxySubmitting = ref(false)
const proxyDraft = ref({ label: '', url: '', max_concurrency: 1, pool: '' })

const proxyNodes = computed(() =>
  filterProxies(proxyData.value?.items || [], { pool: proxyPool.value, q: proxyQuery.value }),
)
const proxySummary = computed(() => proxySummaryText(proxyData.value?.summary, t))

const proxyColumns = computed<TableColumn[]>(() => [
  {
    key: 'label',
    title: t('page.settings.proxies.colName'),
    minWidth: 120,
    mobile: 'title',
    render: (row: TableRow) => {
      const node = row as unknown as ProxyNode
      return node.label || '#' + node.id
    },
  },
  {
    key: 'url',
    title: t('page.settings.proxies.colUrl'),
    minWidth: 220,
    mobile: 'subtitle',
    render: (row: TableRow) => h('span', { class: 'mono' }, (row as unknown as ProxyNode).url),
  },
  {
    key: 'max_concurrency',
    title: t('page.settings.proxies.colConcurrency'),
    width: 80,
    mobile: 'meta',
    render: (row: TableRow) => String((row as unknown as ProxyNode).max_concurrency),
  },
  {
    key: 'pool',
    title: t('page.settings.proxies.colPool'),
    width: 100,
    mobile: 'hidden',
    render: (row: TableRow) => (row as unknown as ProxyNode).pool || t('page.settings.proxies.defaultPool'),
  },
  {
    key: 'state',
    title: t('page.settings.proxies.colState'),
    width: 100,
    mobile: 'meta',
    render: (row: TableRow) => {
      const state = proxyStateOf(row as unknown as ProxyNode, t)
      return h(Badge, { tone: state.tone }, { default: () => state.label })
    },
  },
  {
    key: 'ops',
    title: t('page.settings.proxies.colOps'),
    width: 190,
    mobile: 'actions',
    render: (row: TableRow) => {
      const node = row as unknown as ProxyNode
      const buttons = [
        h(Btn, { size: 'sm', variant: 'quiet', onClick: () => toggleProxy(node) }, {
          default: () => (node.enabled ? t('page.settings.proxies.disable') : t('page.settings.proxies.enable')),
        }),
      ]
      if (node.blocked) {
        buttons.push(
          h(Btn, { size: 'sm', variant: 'quiet', onClick: () => unblockProxyNode(node) }, {
            default: () => t('page.settings.proxies.unblock'),
          }),
        )
      }
      buttons.push(
        h(Btn, { size: 'sm', variant: 'danger', onClick: () => removeProxy(node) }, {
          default: () => t('common.delete'),
        }),
      )
      return h('div', { class: 'st-ops' }, buttons)
    },
  },
])

async function loadProxies(): Promise<void> {
  proxyLoading.value = true
  proxyError.value = ''
  try {
    proxyData.value = await listProxies()
  } catch (error) {
    proxyError.value = failureText(t('page.settings.proxies.loadFailed'), error)
  } finally {
    proxyLoading.value = false
  }
}

function openProxyDrawer(): void {
  proxyDraft.value.label = ''
  proxyDraft.value.url = ''
  proxyDraft.value.max_concurrency = 1
  proxyDraft.value.pool = proxyPool.value
  proxyDrawer.value = true
}

async function submitProxy(): Promise<void> {
  const error = validateProxyInput(proxyDraft.value, t)
  if (error) {
    toast.error(error)
    return
  }
  proxySubmitting.value = true
  try {
    await createProxy({ ...proxyDraft.value })
    proxyDrawer.value = false
    toast.success(t('page.settings.proxies.added'))
    await loadProxies()
  } catch (err) {
    toast.error(failureText(t('page.settings.proxies.addFailed'), err))
  } finally {
    proxySubmitting.value = false
  }
}

async function toggleProxy(node: ProxyNode): Promise<void> {
  try {
    await patchProxy(node.id, { enabled: node.enabled ? 0 : 1 })
    toast.success(node.enabled ? t('page.settings.proxies.disabled') : t('page.settings.proxies.enabled'))
    await loadProxies()
  } catch (error) {
    toast.error(failureText(t('page.settings.proxies.toggleFailed'), error))
  }
}

async function unblockProxyNode(node: ProxyNode): Promise<void> {
  try {
    await unblockProxy(node.id)
    toast.success(t('page.settings.proxies.unblocked'))
    await loadProxies()
  } catch (error) {
    toast.error(failureText(t('page.settings.proxies.unblockFailed'), error))
  }
}

async function removeProxy(node: ProxyNode): Promise<void> {
  const ok = await confirm({
    title: t('page.settings.proxies.deleteTitle'),
    message: t('page.settings.proxies.deleteBody'),
    okText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  try {
    await deleteProxy(node.id)
    toast.success(t('page.settings.proxies.deleted'))
    await loadProxies()
  } catch (error) {
    toast.error(failureText(t('page.settings.proxies.deleteFailed'), error))
  }
}

/* ------------------------------------------------------------------ *
 * 邀请绑定
 * ------------------------------------------------------------------ */

const invitationLoading = ref(false)
const invitationError = ref('')
const invitation = ref<InvitationPayload | null>(null)
const invitationInput = ref('')

const invitationDirty = computed(
  () => invitationInput.value.trim() !== String(invitation.value?.invite_code || ''),
)

async function loadInvitation(): Promise<void> {
  invitationLoading.value = true
  invitationError.value = ''
  try {
    invitation.value = await getInvitation()
    invitationInput.value = invitation.value?.invite_code || ''
  } catch (error) {
    invitationError.value = failureText(t('page.settings.invitation.loadFailed'), error)
  } finally {
    invitationLoading.value = false
  }
}

async function saveInvitationValue(value: string): Promise<void> {
  try {
    invitation.value = await saveInvitation(value)
    invitationInput.value = invitation.value?.invite_code || ''
    toast.success(
      invitation.value?.enabled ? t('page.settings.invitation.saved') : t('page.settings.invitation.savedOff'),
    )
  } catch (error) {
    toast.error(failureText(t('page.settings.invitation.saveFailed'), error))
  }
}

async function disableInvitation(): Promise<void> {
  const ok = await confirm({
    title: t('page.settings.invitation.disableTitle'),
    message: t('page.settings.invitation.disableBody'),
    okText: t('page.settings.invitation.disable'),
  })
  if (!ok) return
  await saveInvitationValue('')
}

/* ------------------------------------------------------------------ *
 * 修改登录密码
 * ------------------------------------------------------------------ */

const passwordSaving = ref(false)
const passwordDraft = reactive({ old: '', next: '', confirm: '' })
const passwordDirty = computed(
  () => Boolean(passwordDraft.old || passwordDraft.next || passwordDraft.confirm),
)

async function submitPassword(): Promise<void> {
  const error = validatePasswordChange(passwordDraft.old, passwordDraft.next, passwordDraft.confirm, t)
  if (error) {
    toast.error(error)
    return
  }
  passwordSaving.value = true
  try {
    const result = await changePassword(passwordDraft.old, passwordDraft.next)
    passwordDraft.old = ''
    passwordDraft.next = ''
    passwordDraft.confirm = ''
    const revoked = Number(result?.revoked_sessions || 0)
    toast.success(
      revoked
        ? t('page.settings.password.savedRevoked', { count: revoked })
        : t('page.settings.password.saved'),
    )
  } catch (error) {
    toast.error(failureText(t('page.settings.password.saveFailed'), error))
  } finally {
    passwordSaving.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 逆向产物
 * ------------------------------------------------------------------ */

const sourceLoading = ref(false)
const sourceError = ref('')
const sourceStatus = ref<AppSourceStatus | null>(null)
const sourceMissing = ref(false)
const installDraft = ref('')
const outputDraft = ref('')
const sourceBusy = ref('')
const unpackProgress = ref<UnpackProgress | null>(null)
const unpacking = ref(false)
const searchQuery = ref('')
const searching = ref(false)
const searchHits = ref<SourceHit[]>([])
const searchNote = ref('')

const sourceLines = computed(() => sourceStatusLines(sourceStatus.value, t))
const sourceDirty = computed(
  () =>
    installDraft.value.trim() !== String(sourceStatus.value?.install_dir || '') ||
    outputDraft.value.trim() !== String(sourceStatus.value?.source_dir || ''),
)

async function loadSource(): Promise<void> {
  sourceLoading.value = true
  sourceError.value = ''
  sourceMissing.value = false
  try {
    sourceStatus.value = await getAppSourceStatus()
    installDraft.value = sourceStatus.value?.install_dir || ''
    outputDraft.value = sourceStatus.value?.source_dir || ''
  } catch (error) {
    // DEV_TOOLS=0 / 生产环境：后端整路由 404 → 卡片降级说明
    sourceMissing.value = true
    sourceError.value = failureText(t('page.settings.appsource.loadFailed'), error)
  } finally {
    sourceLoading.value = false
  }
}

async function runSourceAction(action: 'detect' | 'plan' | 'unpack' | 'remove'): Promise<void> {
  if (sourceBusy.value) return
  sourceBusy.value = action
  try {
    if (action === 'detect') {
      sourceStatus.value = await detectAppSource()
      installDraft.value = sourceStatus.value?.install_dir || ''
      toast.success(
        sourceStatus.value?.install_dir
          ? t('page.settings.appsource.rescanned')
          : t('page.settings.appsource.scanMissing'),
      )
    } else if (action === 'plan') {
      const plan = await planUnpack()
      toast.info(t('page.settings.appsource.planDone', { detail: JSON.stringify(plan).slice(0, 160) }))
    } else if (action === 'unpack') {
      await startUnpack()
      unpacking.value = true
      toast.info(t('page.settings.appsource.unpackStarted'))
    } else {
      const ok = await confirm({
        title: t('page.settings.appsource.removeTitle'),
        message: t('page.settings.appsource.removeBody'),
        okText: t('common.delete'),
        danger: true,
      })
      if (!ok) return
      sourceStatus.value = await removeUnpackedSource()
      toast.success(t('page.settings.appsource.removed'))
    }
  } catch (error) {
    toast.error(failureText(t('page.settings.appsource.actionFailed'), error))
  } finally {
    sourceBusy.value = ''
  }
}

async function saveInstallDir(): Promise<void> {
  if (!installDraft.value.trim() && !outputDraft.value.trim()) {
    toast.error(t('page.settings.appsource.installRequired'))
    return
  }
  sourceBusy.value = 'locate'
  try {
    sourceStatus.value = await locateAppSource({
      install_dir: installDraft.value,
      source_dir: outputDraft.value,
    })
    installDraft.value = sourceStatus.value?.install_dir || installDraft.value
    outputDraft.value = sourceStatus.value?.source_dir || outputDraft.value
    toast.success(t('page.settings.appsource.pathSaved'))
  } catch (error) {
    toast.error(failureText(t('page.settings.appsource.validateFailed'), error))
  } finally {
    sourceBusy.value = ''
  }
}

async function saveOutputDir(): Promise<void> {
  if (!outputDraft.value.trim()) {
    toast.error(t('page.settings.appsource.outputRequired'))
    return
  }
  sourceBusy.value = 'locate-source'
  try {
    sourceStatus.value = await locateAppSource({ source_dir: outputDraft.value })
    outputDraft.value = sourceStatus.value?.source_dir || outputDraft.value
    toast.success(t('page.settings.appsource.outputSet'))
  } catch (error) {
    toast.error(failureText(t('page.settings.appsource.outputFailed'), error))
  } finally {
    sourceBusy.value = ''
  }
}

async function runSearch(): Promise<void> {
  const q = searchQuery.value.trim()
  if (!q) {
    toast.error(t('page.settings.appsource.searchKeyword'))
    return
  }
  searching.value = true
  searchNote.value = ''
  try {
    const payload = await searchAppSource(q, 50)
    searchHits.value = payload?.files || []
    if (!payload?.ok) {
      searchNote.value = String(payload?.reason || t('page.settings.appsource.notReady'))
    } else if (!searchHits.value.length) {
      searchNote.value = t('page.settings.appsource.noMatches')
    }
  } catch (error) {
    searchHits.value = []
    searchNote.value = failureText(t('page.settings.appsource.search'), error)
  } finally {
    searching.value = false
  }
}

/** 拆包进度轮询：只有正在拆时才跑，结束自动停。 */
const unpackPoll = usePolling(
  async () => {
    if (!unpacking.value) return
    try {
      const progress = await getUnpackProgress()
      unpackProgress.value = progress
      if (progress?.running === false) {
        unpacking.value = false
        toast.success(t('page.settings.appsource.unpackDone'))
        await loadSource()
      }
    } catch (error) {
      unpacking.value = false
      toast.error(failureText(t('page.settings.appsource.progressFailed'), error))
    }
  },
  1500,
  { immediate: false },
)

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

const dirty = computed(
  () => runtimeDirty.value || invitationDirty.value || passwordDirty.value || sourceDirty.value,
)

onMounted(() => {
  void loadRuntime()
  void loadProxies()
  void loadInvitation()
  void loadSource()
  unpackPoll.start()
})

onBeforeUnmount(() => {
  unpackPoll.stop()
})
</script>

<template>
  <SettingsLayout
    :title="t('page.settings.title')"
    :help="helpText"
    :sections="sections"
    :dirty="dirty"
  >
    <!-- 0. 界面语言（本地偏好，不写入后端配置） -->
    <LanguageCard />

    <!-- 1. 运行配置 -->
    <SettingsCard
      id="settings-runtime"
      :title="t('page.settings.runtime.title')"
      :description="t('page.settings.runtime.description')"
      :dirty="runtimeDirty"
      :loading="runtimeSaving"
      :save-label="t('page.settings.runtime.saveLabel')"
      :save-disabled="!runtimeDirty"
      @save="saveRuntime"
      @reset="loadRuntime"
    >
      <div v-if="runtimeLoading && !runtimeFields.length" class="st-skeleton"><n-skeleton text :repeat="6" /></div>
      <ErrorBlock v-else-if="runtimeError" :title="runtimeError" retry @retry="loadRuntime" />
      <template v-else>
        <div v-if="restartPending.length" class="st-banner">
          <Badge tone="warn">{{ t('page.settings.runtime.restartBadge') }}</Badge>
          <span>
            {{
              t('page.settings.runtime.restartBanner', {
                count: restartPending.length,
                labels: restartPending.map((item) => item.label).join(locale === 'en-US' ? ', ' : '、'),
              })
            }}
          </span>
        </div>
        <Section
          v-for="(group, index) in runtimeGroups"
          :key="group.group"
          :title="group.group"
          :first="index === 0"
        >
          <div class="st-rows">
            <div v-for="field in group.items" :key="field.attr" class="st-row">
              <div class="st-row__text">
                <div class="st-row__label">
                  <span>{{ field.label }}</span>
                  <code class="st-attr">{{ field.attr }}</code>
                  <Badge v-if="field.restart" tone="warn">{{ t('page.settings.runtime.restartBadge') }}</Badge>
                  <Badge v-if="field.overridden" tone="brand">{{ t('page.settings.runtime.overridden') }}</Badge>
                </div>
                <p v-if="field.desc" class="st-row__desc">{{ field.desc }}</p>
                <p class="st-row__default">
                  {{
                    t('page.settings.runtime.default', {
                      value:
                        field.default === null || field.default === ''
                          ? t('page.settings.runtime.emptyValue')
                          : String(field.default),
                    })
                  }}
                  <template v-if="field.min !== null || field.max !== null">
                    · {{ t('page.settings.runtime.range', { min: field.min ?? '−∞', max: field.max ?? '∞' }) }}
                  </template>
                </p>
              </div>
              <div class="st-row__control">
                <n-switch
                  v-if="field.kind === 'bool'"
                  :value="Boolean(runtimeDraft[field.attr])"
                  :aria-label="field.label"
                  @update:value="(value: boolean) => setRuntimeDraft(field.attr, value)"
                />
                <n-input-number
                  v-else-if="field.kind === 'int' || field.kind === 'float'"
                  :value="runtimeNumber(field.attr)"
                  :min="field.min ?? undefined"
                  :max="field.max ?? undefined"
                  :step="field.step || 1"
                  :aria-label="field.label"
                  style="width: 160px"
                  @update:value="(value: number | null) => setRuntimeDraft(field.attr, value)"
                />
                <n-input
                  v-else
                  :value="runtimeText(field.attr)"
                  :aria-label="field.label"
                  :placeholder="String(field.default ?? '')"
                  @update:value="(value: string) => setRuntimeDraft(field.attr, value)"
                />
              </div>
            </div>
          </div>
        </Section>
      </template>
    </SettingsCard>

    <!-- 2. 出口代理节点 -->
    <SettingsCard
      id="settings-proxies"
      :title="t('page.settings.proxies.title')"
      :description="t('page.settings.proxies.description')"
      hide-save
      hide-reset
    >
      <template #actions>
        <Btn size="sm" variant="default" @click="router.push('/schedules')">
          {{ t('page.settings.proxies.goSchedules') }}
        </Btn>
        <Btn size="sm" variant="default" :disabled="proxyLoading" @click="openProxyDrawer">
          {{ t('page.settings.proxies.add') }}
        </Btn>
      </template>

      <div v-if="proxyLoading && !proxyData" class="st-skeleton"><n-skeleton text :repeat="4" /></div>
      <ErrorBlock v-else-if="proxyError" :title="proxyError" retry @retry="loadProxies" />
      <template v-else>
        <p class="st-note">{{ proxySummary }}</p>
        <p class="st-note">{{ proxyData?.note || '' }}</p>
        <div class="st-filters">
          <n-select
            :value="proxyPool"
            :options="[
              { label: t('page.settings.proxies.allPools'), value: '' },
              ...(proxyData?.pools || []).map((pool) => ({
                label: pool || t('page.settings.proxies.defaultPool'),
                value: pool,
              })),
            ]"
            :aria-label="t('page.settings.proxies.filterAria')"
            style="width: 180px"
            @update:value="(value: string | null) => (proxyPool = value || '')"
          />
          <n-input
            v-model:value="proxyQuery"
            :placeholder="t('page.settings.proxies.search')"
            clearable
            style="width: 220px"
          />
        </div>
        <DataTable
          table-id="settings-proxies"
          :columns="proxyColumns"
          :data="proxyNodes as unknown as TableRow[]"
          row-key="id"
          :loading="proxyLoading"
        />
      </template>
      <Empty
        v-if="!proxyLoading && !proxyError && !proxyNodes.length"
        compact
        :description="t('page.settings.proxies.empty')"
      >
        <Btn variant="default" size="sm" @click="openProxyDrawer">
          {{ t('page.settings.proxies.add') }}
        </Btn>
      </Empty>
    </SettingsCard>

    <!-- 3. 邀请绑定 -->
    <SettingsCard
      id="settings-invitation"
      :title="t('page.settings.invitation.title')"
      :description="t('page.settings.invitation.description')"
      hide-save
      hide-reset
    >
      <template #actions>
        <Badge :tone="invitation?.enabled ? 'ok' : 'neutral'">
          {{ invitation?.enabled ? t('page.settings.invitation.enabled') : t('page.settings.invitation.disabled') }}
        </Badge>
      </template>
      <div v-if="invitationLoading && !invitation" class="st-skeleton"><n-skeleton text :repeat="2" /></div>
      <ErrorBlock v-else-if="invitationError" :title="invitationError" retry @retry="loadInvitation" />
      <template v-else>
        <FormField
          :label="t('page.settings.invitation.field')"
          :hint="t('page.settings.invitation.hint')"
        >
          <div class="st-inline">
            <n-input
              v-model:value="invitationInput"
              :placeholder="t('page.settings.invitation.placeholder')"
              autocomplete="off"
            />
            <Btn
              variant="primary"
              size="sm"
              :disabled="!invitationDirty"
              @click="saveInvitationValue(invitationInput.trim())"
            >
              {{ t('page.settings.invitation.save') }}
            </Btn>
            <Btn
              variant="default"
              size="sm"
              :disabled="!invitation?.enabled"
              @click="disableInvitation"
            >
              {{ t('page.settings.invitation.disable') }}
            </Btn>
          </div>
        </FormField>
        <p v-if="invitation?.invite_url" class="st-note mono">{{ invitation.invite_url }}</p>
      </template>
    </SettingsCard>

    <!-- 4. 修改登录密码 -->
    <SettingsCard
      id="settings-password"
      :title="t('page.settings.password.title')"
      :description="t('page.settings.password.description')"
      hide-save
      hide-reset
    >
      <div class="st-grid">
        <FormField :label="t('page.settings.password.old')">
          <n-input v-model:value="passwordDraft.old" type="password" autocomplete="current-password" />
        </FormField>
        <FormField :label="t('page.settings.password.next')" :hint="t('page.settings.password.hint')">
          <n-input v-model:value="passwordDraft.next" type="password" autocomplete="new-password" />
        </FormField>
        <FormField :label="t('page.settings.password.confirm')">
          <n-input v-model:value="passwordDraft.confirm" type="password" autocomplete="new-password" />
        </FormField>
      </div>
      <div class="st-inline">
        <Btn variant="primary" size="sm" :loading="passwordSaving" :disabled="!passwordDirty" @click="submitPassword">
          {{ t('page.settings.password.save') }}
        </Btn>
      </div>
    </SettingsCard>

    <!-- 5. 逆向产物 -->
    <SettingsCard
      id="settings-appsource"
      :title="t('page.settings.appsource.title')"
      :description="t('page.settings.appsource.description')"
      hide-save
      hide-reset
    >
      <template #actions>
        <Badge v-if="sourceStatus?.dev_tools === false || sourceMissing" tone="neutral">
          {{ t('page.settings.appsource.unavailable') }}
        </Badge>
      </template>

      <div v-if="sourceLoading && !sourceStatus" class="st-skeleton"><n-skeleton text :repeat="4" /></div>
      <div v-else-if="sourceMissing" class="st-note">
        {{ t('page.settings.appsource.unavailableBody') }}
      </div>
      <template v-else>
        <KV :items="sourceLines.map((line) => ({ label: line.label, value: line.value, mono: line.mono }))" />

        <Section :title="t('page.settings.appsource.paths')">
          <FormField
            :label="t('page.settings.appsource.install')"
            :hint="t('page.settings.appsource.installHint')"
          >
            <div class="st-inline">
              <n-input v-model:value="installDraft" :placeholder="t('page.settings.appsource.installPlaceholder')" />
              <Btn
                variant="primary"
                size="sm"
                :loading="sourceBusy === 'locate'"
                @click="saveInstallDir"
              >
                {{ t('page.settings.appsource.validate') }}
              </Btn>
            </div>
          </FormField>
          <FormField
            :label="t('page.settings.appsource.output')"
            :hint="t('page.settings.appsource.outputHint')"
          >
            <div class="st-inline">
              <n-input v-model:value="outputDraft" :placeholder="t('page.settings.appsource.outputPlaceholder')" />
              <Btn
                variant="default"
                size="sm"
                :loading="sourceBusy === 'locate-source'"
                @click="saveOutputDir"
              >
                {{ t('page.settings.appsource.setOutput') }}
              </Btn>
            </div>
          </FormField>
          <div class="st-inline">
            <Btn
              variant="default"
              size="sm"
              :icon="ScanSearch"
              :loading="sourceBusy === 'detect'"
              @click="runSourceAction('detect')"
            >
              {{ t('page.settings.appsource.rescan') }}
            </Btn>
            <Btn
              variant="default"
              size="sm"
              :icon="FileSearch"
              :loading="sourceBusy === 'plan'"
              @click="runSourceAction('plan')"
            >
              {{ t('page.settings.appsource.precheck') }}
            </Btn>
            <Btn variant="primary" size="sm" :icon="PackageOpen" :loading="sourceBusy === 'unpack' || unpacking" :disabled="sourceStatus?.can_unpack === false" @click="runSourceAction('unpack')">
              {{ t('page.settings.appsource.unpack') }}
            </Btn>
            <Btn
              variant="danger"
              size="sm"
              :icon="Trash2"
              :loading="sourceBusy === 'remove'"
              @click="runSourceAction('remove')"
            >
              {{ t('page.settings.appsource.remove') }}
            </Btn>
          </div>

          <div v-if="unpacking || unpackProgress" class="st-progress">
            <div class="st-progress__head">
              <span>{{ unpackProgress?.phase || t('page.settings.appsource.preparing') }}</span>
              <span class="tnum">{{ unpackPercent(unpackProgress) }}%</span>
            </div>
            <Meter
              :value="unpackPercent(unpackProgress)"
              tone="brand"
              :aria-label="t('page.settings.appsource.progressAria')"
            />
            <p class="st-note mono">{{ unpackProgress?.current || '' }}</p>
          </div>
        </Section>

        <Section :title="t('page.settings.appsource.searchSection')">
          <div class="st-inline">
            <n-input
              v-model:value="searchQuery"
              :placeholder="t('page.settings.appsource.searchPlaceholder')"
              @keyup.enter="runSearch"
            />
            <Btn variant="default" size="sm" :icon="RefreshCw" :loading="searching" @click="runSearch">
              {{ t('page.settings.appsource.search') }}
            </Btn>
          </div>
          <p v-if="searchNote" class="st-note">{{ searchNote }}</p>
          <ul v-if="searchHits.length" class="st-hits">
            <li v-for="(hit, index) in searchHits" :key="index" class="st-hit">
              <span class="st-hit__file mono">{{ hit.file }}:{{ hit.line }}</span>
              <code class="st-hit__text">{{ hit.text }}</code>
            </li>
          </ul>
        </Section>
      </template>
    </SettingsCard>

    <FormDrawer
      v-model:show="proxyDrawer"
      :title="t('page.settings.proxies.drawerTitle')"
      :submit-text="t('page.settings.proxies.drawerSubmit')"
      :submit-loading="proxySubmitting"
      :submit-disabled="!proxyDraft.url.trim()"
      @submit="submitProxy"
    >
      <FormField :label="t('page.settings.proxies.nameField')">
        <n-input
          :value="proxyDraft.label"
          :placeholder="t('page.settings.proxies.namePlaceholder')"
          @update:value="(value: string) => (proxyDraft.label = value)"
        />
      </FormField>
      <FormField
        :label="t('page.settings.proxies.urlField')"
        required
        :hint="t('page.settings.proxies.urlHint')"
      >
        <n-input
          :value="proxyDraft.url"
          placeholder="http://user:pass@host:port"
          autocomplete="off"
          @update:value="(value: string) => (proxyDraft.url = value)"
        />
      </FormField>
      <FormField
        :label="t('page.settings.proxies.concurrencyField')"
        :hint="t('page.settings.proxies.concurrencyHint')"
      >
        <n-input-number
          :value="proxyDraft.max_concurrency"
          :min="1"
          :max="32"
          style="width: 140px"
          @update:value="(value: number | null) => (proxyDraft.max_concurrency = value ?? 1)"
        />
      </FormField>
      <FormField
        :label="t('page.settings.proxies.poolField')"
        :hint="t('page.settings.proxies.poolHint')"
      >
        <n-input
          :value="proxyDraft.pool"
          :placeholder="t('page.settings.proxies.poolPlaceholder')"
          @update:value="(value: string) => (proxyDraft.pool = value)"
        />
      </FormField>
    </FormDrawer>
  </SettingsLayout>
</template>

<style scoped>
.st-skeleton {
  padding: var(--sp-2) 0;
}

.st-banner {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  padding: var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.st-rows {
  display: grid;
  gap: var(--sp-3);
}

.st-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
  min-width: 0;
}

.st-row__text {
  min-width: 0;
}

.st-row__label {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
}

.st-attr {
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
  font-family: var(--font-mono);
}

.st-row__desc {
  margin-top: 2px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  max-width: 56ch;
}

.st-row__default {
  margin-top: 2px;
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
}

.st-row__control {
  flex: none;
  max-width: 50%;
}

.st-note {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
}

.st-inline {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.st-inline > :first-child {
  flex: 1 1 220px;
  min-width: 0;
}

.st-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-3) var(--sp-4);
}

.st-ops {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  flex-wrap: wrap;
}

.st-filters {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.st-progress {
  display: grid;
  gap: var(--sp-1);
  margin-top: var(--sp-2);
}

.st-progress__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.st-hits {
  display: grid;
  gap: var(--sp-2);
  margin: var(--sp-2) 0 0;
  padding: 0;
  list-style: none;
  max-height: 280px;
  overflow: auto;
}

.st-hit {
  display: grid;
  gap: 2px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.st-hit__file {
  color: var(--brand);
}

.st-hit__text {
  color: var(--fg-2);
  word-break: break-all;
}

@media (max-width: 720px) {
  .st-row {
    flex-wrap: wrap;
  }

  .st-row__control {
    max-width: 100%;
    width: 100%;
  }

  .st-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
