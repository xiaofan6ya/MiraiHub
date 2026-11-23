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
import { NInput, NInputNumber, NSkeleton, NSwitch } from 'naive-ui'
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

const HELP_TEXT =
  '常用 .env 项可以在这里动态改（exe / apk 部署没有配置文件，改这里即时生效；标「重启后生效」的项保存后需要重启服务）。' +
  '出口代理节点用于批量任务的出口 IP —— 一个 IP 上跑几十个号会被上游拉黑。'

const toast = useToast()
const confirm = useConfirm()
const router = useRouter()

const sections = [
  { id: 'settings-runtime', title: '运行配置' },
  { id: 'settings-proxies', title: '出口代理节点' },
  { id: 'settings-invitation', title: 'WorkBuddy 邀请绑定' },
  { id: 'settings-password', title: '修改登录密码' },
  { id: 'settings-appsource', title: '逆向产物' },
]

/* ------------------------------------------------------------------ *
 * 运行配置
 * ------------------------------------------------------------------ */

const runtimeLoading = ref(false)
const runtimeSaving = ref(false)
const runtimeError = ref('')
const runtimeFields = ref<RuntimeField[]>([])
const runtimeDraft = reactive<Record<string, string | number | boolean | null>>({})
const restartPending = ref<RuntimeField[]>([])

const runtimeGroups = computed(() => groupRuntimeFields(runtimeFields.value))

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
    runtimeError.value = failureText('加载运行配置', error)
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
      toast.error('部分配置未保存：' + errors.map(([attr, msg]) => attr + ' ' + msg).join('；'))
    } else {
      toast.success('已保存 ' + (result?.changed?.length || 0) + ' 项运行配置')
    }
  } catch (error) {
    toast.error(failureText('保存运行配置', error))
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
const proxySummary = computed(() => proxySummaryText(proxyData.value?.summary))

const proxyColumns: TableColumn[] = [
  {
    key: 'label',
    title: '名称',
    minWidth: 120,
    mobile: 'title',
    render: (row: TableRow) => {
      const node = row as unknown as ProxyNode
      return node.label || '#' + node.id
    },
  },
  {
    key: 'url',
    title: '地址',
    minWidth: 220,
    mobile: 'subtitle',
    render: (row: TableRow) => h('span', { class: 'mono' }, (row as unknown as ProxyNode).url),
  },
  {
    key: 'max_concurrency',
    title: '并发',
    width: 80,
    mobile: 'meta',
    render: (row: TableRow) => String((row as unknown as ProxyNode).max_concurrency),
  },
  {
    key: 'pool',
    title: '分组',
    width: 100,
    mobile: 'hidden',
    render: (row: TableRow) => (row as unknown as ProxyNode).pool || '默认组',
  },
  {
    key: 'state',
    title: '状态',
    width: 100,
    mobile: 'meta',
    render: (row: TableRow) => {
      const state = proxyStateOf(row as unknown as ProxyNode)
      return h(Badge, { tone: state.tone }, { default: () => state.label })
    },
  },
  {
    key: 'ops',
    title: '操作',
    width: 190,
    mobile: 'actions',
    render: (row: TableRow) => {
      const node = row as unknown as ProxyNode
      const buttons = [
        h(Btn, { size: 'sm', variant: 'quiet', onClick: () => toggleProxy(node) }, { default: () => (node.enabled ? '停用' : '启用') }),
      ]
      if (node.blocked) {
        buttons.push(h(Btn, { size: 'sm', variant: 'quiet', onClick: () => unblockProxyNode(node) }, { default: () => '解除静默' }))
      }
      buttons.push(h(Btn, { size: 'sm', variant: 'danger', onClick: () => removeProxy(node) }, { default: () => '删除' }))
      return h('div', { class: 'st-ops' }, buttons)
    },
  },
]

async function loadProxies(): Promise<void> {
  proxyLoading.value = true
  proxyError.value = ''
  try {
    proxyData.value = await listProxies()
  } catch (error) {
    proxyError.value = failureText('加载代理节点', error)
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
  const error = validateProxyInput(proxyDraft.value)
  if (error) {
    toast.error(error)
    return
  }
  proxySubmitting.value = true
  try {
    await createProxy({ ...proxyDraft.value })
    proxyDrawer.value = false
    toast.success('已添加出口节点')
    await loadProxies()
  } catch (err) {
    toast.error(failureText('添加代理节点', err))
  } finally {
    proxySubmitting.value = false
  }
}

async function toggleProxy(node: ProxyNode): Promise<void> {
  try {
    await patchProxy(node.id, { enabled: node.enabled ? 0 : 1 })
    toast.success(node.enabled ? '已停用节点' : '已启用节点')
    await loadProxies()
  } catch (error) {
    toast.error(failureText('切换节点状态', error))
  }
}

async function unblockProxyNode(node: ProxyNode): Promise<void> {
  try {
    await unblockProxy(node.id)
    toast.success('已解除静默')
    await loadProxies()
  } catch (error) {
    toast.error(failureText('解除静默', error))
  }
}

async function removeProxy(node: ProxyNode): Promise<void> {
  const ok = await confirm({
    title: '删除出口节点？',
    message: '正在使用它的任务会退回直连，可能因同 IP 高频被上游拉黑。',
    okText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteProxy(node.id)
    toast.success('已删除节点')
    await loadProxies()
  } catch (error) {
    toast.error(failureText('删除代理节点', error))
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
    invitationError.value = failureText('读取邀请设置', error)
  } finally {
    invitationLoading.value = false
  }
}

async function saveInvitationValue(value: string): Promise<void> {
  try {
    invitation.value = await saveInvitation(value)
    invitationInput.value = invitation.value?.invite_code || ''
    toast.success(invitation.value?.enabled ? '邀请绑定已保存' : '邀请绑定已停用')
  } catch (error) {
    toast.error(failureText('保存邀请绑定', error))
  }
}

async function disableInvitation(): Promise<void> {
  const ok = await confirm({
    title: '停用邀请绑定？',
    message: '停用后新注册的账号不再自动绑定这个邀请码，已绑定的账号不受影响。',
    okText: '停用',
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
  const error = validatePasswordChange(passwordDraft.old, passwordDraft.next, passwordDraft.confirm)
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
    toast.success('密码已修改，其他设备需要重新登录' + (revoked ? '（已吊销 ' + revoked + ' 个会话）' : ''))
  } catch (error) {
    toast.error(failureText('修改密码', error))
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

const sourceLines = computed(() => sourceStatusLines(sourceStatus.value))
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
    sourceError.value = failureText('加载逆向产物状态', error)
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
      toast.success(sourceStatus.value?.install_dir ? '已重新扫描安装目录' : '未扫描到安装目录')
    } else if (action === 'plan') {
      const plan = await planUnpack()
      toast.info('预检完成：' + JSON.stringify(plan).slice(0, 160))
    } else if (action === 'unpack') {
      await startUnpack()
      unpacking.value = true
      toast.info('拆包任务已启动')
    } else {
      const ok = await confirm({
        title: '删除逆向产物？',
        message: '只删除「逆向产物目录」下的文件，不影响客户端本体。',
        okText: '删除',
        danger: true,
      })
      if (!ok) return
      sourceStatus.value = await removeUnpackedSource()
      toast.success('已删除逆向产物')
    }
  } catch (error) {
    toast.error(failureText('逆向产物操作', error))
  } finally {
    sourceBusy.value = ''
  }
}

async function saveInstallDir(): Promise<void> {
  if (!installDraft.value.trim() && !outputDraft.value.trim()) {
    toast.error('请填写客户端安装目录')
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
    toast.success('路径已校验并保存')
  } catch (error) {
    toast.error(failureText('校验安装目录', error))
  } finally {
    sourceBusy.value = ''
  }
}

async function saveOutputDir(): Promise<void> {
  if (!outputDraft.value.trim()) {
    toast.error('请填写逆向产物输出目录')
    return
  }
  sourceBusy.value = 'locate-source'
  try {
    sourceStatus.value = await locateAppSource({ source_dir: outputDraft.value })
    outputDraft.value = sourceStatus.value?.source_dir || outputDraft.value
    toast.success('已设为输出目录')
  } catch (error) {
    toast.error(failureText('设置输出目录', error))
  } finally {
    sourceBusy.value = ''
  }
}

async function runSearch(): Promise<void> {
  const q = searchQuery.value.trim()
  if (!q) {
    toast.error('请填写检索关键字')
    return
  }
  searching.value = true
  searchNote.value = ''
  try {
    const payload = await searchAppSource(q, 50)
    searchHits.value = payload?.files || []
    if (!payload?.ok) searchNote.value = String(payload?.reason || '逆向产物尚未产出')
    else if (!searchHits.value.length) searchNote.value = '没有匹配的文件'
  } catch (error) {
    searchHits.value = []
    searchNote.value = failureText('检索', error)
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
        toast.success('拆包完成')
        await loadSource()
      }
    } catch (error) {
      unpacking.value = false
      toast.error(failureText('读取拆包进度', error))
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
  <SettingsLayout title="设置" :help="HELP_TEXT" :sections="sections" :dirty="dirty">
    <!-- 1. 运行配置 -->
    <SettingsCard
      id="settings-runtime"
      title="运行配置"
      description="常用 .env 项动态改（exe / apk 部署没有配置文件，改这里即时生效）。只提交改动项；清空 = 回落 .env 默认。"
      :dirty="runtimeDirty"
      :loading="runtimeSaving"
      save-label="保存配置"
      :save-disabled="!runtimeDirty"
      @save="saveRuntime"
      @reset="loadRuntime"
    >
      <div v-if="runtimeLoading && !runtimeFields.length" class="st-skeleton"><n-skeleton text :repeat="6" /></div>
      <ErrorBlock v-else-if="runtimeError" :title="runtimeError" retry @retry="loadRuntime" />
      <template v-else>
        <div v-if="restartPending.length" class="st-banner">
          <Badge tone="warn">重启后生效</Badge>
          <span>有 {{ restartPending.length }} 项修改需要重启服务后生效：{{ restartPending.map((item) => item.label).join('、') }}</span>
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
                  <Badge v-if="field.restart" tone="warn">重启后生效</Badge>
                  <Badge v-if="field.overridden" tone="brand">已覆盖</Badge>
                </div>
                <p v-if="field.desc" class="st-row__desc">{{ field.desc }}</p>
                <p class="st-row__default">
                  默认 {{ field.default === null || field.default === '' ? '（空）' : field.default }}
                  <template v-if="field.min !== null || field.max !== null"> · 范围 {{ field.min ?? '−∞' }} ~ {{ field.max ?? '∞' }}</template>
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
      title="出口代理节点"
      description="一个出口 IP 上跑几十个号会被上游拉黑。配好出口后，在「定时任务」里选组名。"
      hide-save
      hide-reset
    >
      <template #actions>
        <Btn size="sm" variant="default" @click="router.push('/schedules')">去定时任务</Btn>
        <Btn size="sm" variant="default" :disabled="proxyLoading" @click="openProxyDrawer">添加节点</Btn>
      </template>

      <div v-if="proxyLoading && !proxyData" class="st-skeleton"><n-skeleton text :repeat="4" /></div>
      <ErrorBlock v-else-if="proxyError" :title="proxyError" retry @retry="loadProxies" />
      <template v-else>
        <p class="st-note">{{ proxySummary }}</p>
        <p class="st-note">{{ proxyData?.note || '' }}</p>
        <div class="st-filters">
          <n-select
            :value="proxyPool"
            :options="[{ label: '全部分组', value: '' }, ...(proxyData?.pools || []).map((pool) => ({ label: pool || '默认组', value: pool }))]"
            aria-label="按分组筛选"
            style="width: 180px"
            @update:value="(value: string | null) => (proxyPool = value || '')"
          />
          <n-input v-model:value="proxyQuery" placeholder="搜索名称 / 地址" clearable style="width: 220px" />
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
        description="还没有配置出口节点，任务会从本机 IP 直连。批量任务建议至少配一个。"
      >
        <Btn variant="default" size="sm" @click="openProxyDrawer">添加节点</Btn>
      </Empty>
    </SettingsCard>

    <!-- 3. 邀请绑定 -->
    <SettingsCard
      id="settings-invitation"
      title="WorkBuddy 邀请绑定"
      description="填邀请码或 WorkBuddy 官方邀请链接；保存后新注册账号自动绑定。停用不会解绑已绑定的账号。"
      hide-save
      hide-reset
    >
      <template #actions>
        <Badge :tone="invitation?.enabled ? 'ok' : 'neutral'">{{ invitation?.enabled ? '已启用' : '未启用' }}</Badge>
      </template>
      <div v-if="invitationLoading && !invitation" class="st-skeleton"><n-skeleton text :repeat="2" /></div>
      <ErrorBlock v-else-if="invitationError" :title="invitationError" retry @retry="loadInvitation" />
      <template v-else>
        <FormField label="邀请码 / 邀请链接" hint="支持纯邀请码或官方邀请链接；保存后返回解析出的邀请码与链接。">
          <div class="st-inline">
            <n-input v-model:value="invitationInput" placeholder="邀请码或 WorkBuddy 官方邀请链接" autocomplete="off" />
            <Btn variant="primary" size="sm" :disabled="!invitationDirty" @click="saveInvitationValue(invitationInput.trim())">保存</Btn>
            <Btn variant="default" size="sm" :disabled="!invitation?.enabled" @click="disableInvitation">停用</Btn>
          </div>
        </FormField>
        <p v-if="invitation?.invite_url" class="st-note mono">{{ invitation.invite_url }}</p>
      </template>
    </SettingsCard>

    <!-- 4. 修改登录密码 -->
    <SettingsCard
      id="settings-password"
      title="修改登录密码"
      description="修改后其它设备需要重新登录（后端会吊销其它会话，只保留当前这一条）。"
      hide-save
      hide-reset
    >
      <div class="st-grid">
        <FormField label="当前密码">
          <n-input v-model:value="passwordDraft.old" type="password" autocomplete="current-password" />
        </FormField>
        <FormField label="新密码" hint="至少 6 位（以后端规则为准）。">
          <n-input v-model:value="passwordDraft.next" type="password" autocomplete="new-password" />
        </FormField>
        <FormField label="确认新密码">
          <n-input v-model:value="passwordDraft.confirm" type="password" autocomplete="new-password" />
        </FormField>
      </div>
      <div class="st-inline">
        <Btn variant="primary" size="sm" :loading="passwordSaving" :disabled="!passwordDirty" @click="submitPassword">
          保存新密码
        </Btn>
      </div>
    </SettingsCard>

    <!-- 5. 逆向产物 -->
    <SettingsCard
      id="settings-appsource"
      title="逆向产物（客户端源码）"
      description="从本机安装的客户端 app.asar 里解出源码，便于检索/排障。纯标准库解包，不需要 Node/npm；生产环境可用 ADMIN_DEV_TOOLS=0 关闭。"
      hide-save
      hide-reset
    >
      <template #actions>
        <Badge v-if="sourceStatus?.dev_tools === false || sourceMissing" tone="neutral">本部署未开放</Badge>
      </template>

      <div v-if="sourceLoading && !sourceStatus" class="st-skeleton"><n-skeleton text :repeat="4" /></div>
      <div v-else-if="sourceMissing" class="st-note">
        本部署未开放逆向产物（ADMIN_DEV_TOOLS=0 或非本机运行时）。功能关闭时该区块不可用，不影响其它设置。
      </div>
      <template v-else>
        <KV :items="sourceLines.map((line) => ({ label: line.label, value: line.value, mono: line.mono }))" />

        <Section title="路径">
          <FormField label="客户端安装目录" hint="可填 D:\WorkBuddy、...\resources 或 ...\resources\app.asar，会自动补齐到安装根目录。">
            <div class="st-inline">
              <n-input v-model:value="installDraft" placeholder="例如 D:\WorkBuddy" />
              <Btn variant="primary" size="sm" :loading="sourceBusy === 'locate'" @click="saveInstallDir">校验并保存</Btn>
            </div>
          </FormField>
          <FormField label="逆向产物输出目录" hint="留空 = 用户缓存目录（不写进 git 仓库）。">
            <div class="st-inline">
              <n-input v-model:value="outputDraft" placeholder="留空 = 用户缓存目录" />
              <Btn variant="default" size="sm" :loading="sourceBusy === 'locate-source'" @click="saveOutputDir">设为输出目录</Btn>
            </div>
          </FormField>
          <div class="st-inline">
            <Btn variant="default" size="sm" :icon="ScanSearch" :loading="sourceBusy === 'detect'" @click="runSourceAction('detect')">重新扫描</Btn>
            <Btn variant="default" size="sm" :icon="FileSearch" :loading="sourceBusy === 'plan'" @click="runSourceAction('plan')">预检</Btn>
            <Btn variant="primary" size="sm" :icon="PackageOpen" :loading="sourceBusy === 'unpack' || unpacking" :disabled="sourceStatus?.can_unpack === false" @click="runSourceAction('unpack')">
              一键拆包
            </Btn>
            <Btn variant="danger" size="sm" :icon="Trash2" :loading="sourceBusy === 'remove'" @click="runSourceAction('remove')">删除产物</Btn>
          </div>

          <div v-if="unpacking || unpackProgress" class="st-progress">
            <div class="st-progress__head">
              <span>{{ unpackProgress?.phase || '准备中' }}</span>
              <span class="tnum">{{ unpackPercent(unpackProgress) }}%</span>
            </div>
            <Meter :value="unpackPercent(unpackProgress)" tone="brand" :aria-label="'拆包进度'" />
            <p class="st-note mono">{{ unpackProgress?.current || '' }}</p>
          </div>
        </Section>

        <Section title="在产物里检索关键字">
          <div class="st-inline">
            <n-input v-model:value="searchQuery" placeholder="例如 create_canvas / turingSdk" @keyup.enter="runSearch" />
            <Btn variant="default" size="sm" :icon="RefreshCw" :loading="searching" @click="runSearch">检索</Btn>
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
      title="添加出口节点"
      submit-text="添加节点"
      :submit-loading="proxySubmitting"
      :submit-disabled="!proxyDraft.url.trim()"
      @submit="submitProxy"
    >
      <FormField label="名称（可选）">
        <n-input
          :value="proxyDraft.label"
          placeholder="机房A-01 / 住宅-上海"
          @update:value="(value: string) => (proxyDraft.label = value)"
        />
      </FormField>
      <FormField label="代理地址" required hint="http://user:pass@host:port、socks5://host:port，或 env:变量名 从环境变量读取。">
        <n-input
          :value="proxyDraft.url"
          placeholder="http://user:pass@host:port"
          autocomplete="off"
          @update:value="(value: string) => (proxyDraft.url = value)"
        />
      </FormField>
      <FormField label="单节点并发" hint="住宅代理建议 1~2，机房可以 5~10。">
        <n-input-number
          :value="proxyDraft.max_concurrency"
          :min="1"
          :max="32"
          style="width: 140px"
          @update:value="(value: number | null) => (proxyDraft.max_concurrency = value ?? 1)"
        />
      </FormField>
      <FormField label="分组（可选）" hint="留空 = 默认组；定时任务里按组名选出口。">
        <n-input
          :value="proxyDraft.pool"
          placeholder="留空 = 默认组"
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
