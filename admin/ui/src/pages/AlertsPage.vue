<script setup lang="ts">
/**
 * S08 预警管理 /alerts（模板 C，设计稿 06-pages-d P20）。
 *
 * 契约：docs/ui-v2/contracts/alerts.md（功能清单就是验收标准）。
 * 旧版：#panel-alerts + console-app.js「预警管理」段（loadAlerts / alertsSave / alertsSendTest）。
 *
 * 三个刻意的设计决定（契约 §八 有完整理由）：
 *   1. 只有一个「保存配置」：PUT /api/alerts/config 是全量覆盖，按卡片部分提交会把别的区块清空；
 *   2. 渠道「启用」开关是编辑态：后端没有 per-channel 位，关闭后保存 = 从 channels 里移除；
 *   3. 密码 / 一条式授权码只写不读：输入框初值恒为空串，placeholder 写明「留空不修改」。
 */
import { computed, onMounted, reactive, ref, type Component } from 'vue'
import {
  Link as LinkIcon,
  Mail,
  MessageCircle,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  Undo2,
  Webhook,
} from 'lucide-vue-next'
import { NInput, NInputNumber, NSelect, NSwitch } from 'naive-ui'
import SettingsLayout from '@/layout/SettingsLayout.vue'
import SettingsCard from '@/layout/SettingsCard.vue'
import Badge from '@/components/base/Badge.vue'
import Btn from '@/components/base/Btn.vue'
import Empty from '@/components/base/Empty.vue'
import ErrorBlock from '@/components/base/ErrorBlock.vue'
import FormField from '@/components/overlay/FormField.vue'
import HelpTip from '@/components/base/HelpTip.vue'
import IconBtn from '@/components/base/IconBtn.vue'
import Section from '@/components/base/Section.vue'
import FormDrawer from '@/components/overlay/FormDrawer.vue'
import { useConfirm, useToast } from '@/composables'
import { failureText } from '@/utils/error'
import { fmtTime } from '@/utils/format'
import {
  ALERT_CHANNEL_TYPES,
  ALERT_COOLDOWN_DEFAULT,
  ALERT_COOLDOWN_MAX,
  ALERT_COOLDOWN_MIN,
  FALLBACK_CHANNEL_LABELS,
  SMTP_PORT_DEFAULT,
  alertChannelMeta,
  alertFormFromPayload,
  buildAlertConfigBody,
  channelKey,
  clampCooldown,
  cloneAlertForm,
  describeCooldown,
  describeSmtpHealth,
  getAlertConfig,
  isConfigDirty,
  putAlertConfig,
  sendAlertTest,
  summarizeTestResult,
  toChannelViews,
  validateChannelTarget,
  type AlertChannelView,
  type AlertConfigForm,
  type AlertSmtpHealth,
  type AlertTestResult,
} from '@/api/alerts'

const toast = useToast()
const confirm = useConfirm()

const pageHelp =
  '风控哨兵判定批次封控时，通过下列渠道通知管理员。同一平台的同一业务码在去重窗口内只通知一次，避免连坐时被刷屏。'

const sections = [
  { id: 'alerts-rules', title: '告警规则' },
  { id: 'alerts-channels', title: '通知渠道' },
  { id: 'alerts-smtp', title: 'SMTP（邮件渠道）' },
]

const loading = ref(false)
const loaded = ref(false)
const saving = ref(false)
const testing = ref(false)
const loadError = ref<{ message: string; detail?: string } | null>(null)
const labels = ref<Record<string, string>>({})
const smtpHealth = ref<AlertSmtpHealth | null>(null)
const hasPassword = ref(false)
const hasCompact = ref(false)
const lastTest = ref<AlertTestResult | null>(null)
const lastTestAt = ref(0)

function emptyForm(): AlertConfigForm {
  return {
    enabled: true,
    cooldown: ALERT_COOLDOWN_DEFAULT,
    channels: [],
    smtpHost: '',
    smtpPort: SMTP_PORT_DEFAULT,
    smtpUser: '',
    smtpPassword: '',
    smtpFrom: '',
    smtpStarttls: true,
    smtpCompact: '',
    mailTo: '',
    clearCompact: false,
  }
}

const form = reactive<AlertConfigForm>(emptyForm())
/** 上次保存/加载的快照，用来算未保存状态与「取消修改」。 */
const saved = ref<AlertConfigForm>(emptyForm())

const dirty = computed(() => isConfigDirty(form, saved.value))
const channelLabelMap = computed<Record<string, string>>(() => ({
  ...FALLBACK_CHANNEL_LABELS,
  ...(labels.value || {}),
}))
const typeOptions = computed(() =>
  ALERT_CHANNEL_TYPES.map((type) => ({ label: channelLabelMap.value[type] || type, value: type })),
)
const cooldownValue = computed<number | null>({
  get: () => form.cooldown,
  set: (value) => {
    form.cooldown = typeof value === 'number' && Number.isFinite(value) ? value : ALERT_COOLDOWN_DEFAULT
  },
})

const healthView = computed(() => describeSmtpHealth(smtpHealth.value))
const healthIcon = computed<Component>(() =>
  healthView.value.tone === 'ok' ? ShieldCheck : TriangleAlert,
)
const compactPlaceholder = computed(() => {
  if (form.clearCompact) return '保存后清空已存授权码'
  if (hasCompact.value) return '已设置（留空不修改，可粘贴覆盖）'
  return 'qqSMTP:授权码  或  qqSMTP:你的邮箱@qq.com:授权码'
})
const passwordPlaceholder = computed(() =>
  hasPassword.value ? '已设置（留空不修改）' : '未设置（留空不修改）',
)
const canClearCompact = computed(() => hasCompact.value && !form.clearCompact)

const testRows = computed(() =>
  (lastTest.value?.results ?? []).map((item, index) => ({
    key: item.type + '::' + item.target + '::' + index,
    ok: item.ok,
    label: channelLabelMap.value[item.type] || item.type,
    target: item.target,
    error: item.error,
  })),
)
const testTime = computed(() => (lastTestAt.value ? fmtTime(lastTestAt.value) : ''))

const channelIcons: Record<string, Component> = {
  email: Mail,
  feishu: Webhook,
  qqbot: MessageCircle,
}

function channelIcon(type: string): Component {
  return channelIcons[type] || LinkIcon
}

/* ------------------------------- 加载 / 保存 ------------------------------- */

function errorDetail(error: unknown): string | undefined {
  const detail = (error as { detail?: unknown } | null | undefined)?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') return JSON.stringify(detail, null, 2)
  return undefined
}

async function load(options: { silent?: boolean } = {}): Promise<void> {
  if (loading.value) return
  loading.value = true
  try {
    const payload = await getAlertConfig()
    labels.value = { ...(payload.labels || {}) }
    smtpHealth.value = payload.smtp ?? null
    hasPassword.value = Boolean(payload.config?.has_password)
    hasCompact.value = Boolean(payload.config?.has_smtp)
    const next = alertFormFromPayload(payload)
    Object.assign(form, next)
    saved.value = cloneAlertForm(next)
    loadError.value = null
    loaded.value = true
  } catch (error) {
    const message = failureText('加载预警配置', error)
    loadError.value = { message, detail: errorDetail(error) }
    if (options.silent) toast.error(message)
  } finally {
    loading.value = false
  }
}

function reload(): void {
  void load({ silent: true })
}

async function discard(): Promise<void> {
  if (!dirty.value) return
  const ok = await confirm({
    title: '放弃未保存的修改？',
    message: '这一页的修改还没有保存，放弃后回到上次保存的内容。',
    okText: '放弃修改',
  })
  if (!ok) return
  await load({ silent: true })
}

async function save(): Promise<void> {
  if (saving.value) return
  const cooldown = Number(form.cooldown)
  if (!Number.isFinite(cooldown) || cooldown < ALERT_COOLDOWN_MIN || cooldown > ALERT_COOLDOWN_MAX) {
    toast.error('保存失败：去重窗口需在 ' + ALERT_COOLDOWN_MIN + '–' + ALERT_COOLDOWN_MAX + ' 秒之间')
    return
  }
  saving.value = true
  try {
    await putAlertConfig(buildAlertConfigBody(form))
    toast.success('预警配置已保存')
    await load({ silent: true })
  } catch (error) {
    toast.error(failureText('保存预警配置', error))
  } finally {
    saving.value = false
  }
}

/* --------------------------------- 发送测试 --------------------------------- */

async function testSend(): Promise<void> {
  if (testing.value) return
  testing.value = true
  try {
    const result = await sendAlertTest()
    lastTest.value = result
    lastTestAt.value = Date.now()
    if (result.smtp) smtpHealth.value = result.smtp
    const summary = summarizeTestResult(result, channelLabelMap.value)
    if (summary.tone === 'success') toast.success(summary.text)
    else if (summary.tone === 'warning') toast.warning(summary.text)
    else toast.error(summary.text)
  } catch (error) {
    toast.error(failureText('发送测试通知', error))
  } finally {
    testing.value = false
  }
}

/* --------------------------------- 渠道编辑 --------------------------------- */

const drawerOpen = ref(false)
const draft = reactive<{ type: string; target: string }>({ type: ALERT_CHANNEL_TYPES[0], target: '' })
const draftError = ref('')
const draftMeta = computed(() => alertChannelMeta(draft.type, channelLabelMap.value))
const draftTargetLabel = computed(() => (draftMeta.value.kind === 'email' ? '收件邮箱' : 'Webhook 地址'))

function openAddDrawer(): void {
  draft.type = ALERT_CHANNEL_TYPES[0]
  draft.target = ''
  draftError.value = ''
  drawerOpen.value = true
}

function onDraftTypeChange(): void {
  draft.target = ''
  draftError.value = ''
}

function submitAdd(): void {
  draftError.value = validateChannelTarget(draft.type, draft.target)
  if (draftError.value) return
  const target = draft.target.trim()
  const key = channelKey(draft.type, target)
  if (form.channels.some((channel) => channel.key === key)) {
    draftError.value = '该渠道已存在'
    return
  }
  form.channels = [
    ...form.channels,
    {
      key,
      type: draft.type,
      label: draftMeta.value.label,
      target,
      enabled: true,
      auto: false,
    },
  ]
  drawerOpen.value = false
  toast.success('已添加渠道，保存配置后生效')
}

async function removeChannel(channel: AlertChannelView): Promise<void> {
  if (channel.auto) return
  const ok = await confirm({
    title: '删除「' + channel.label + '」渠道？',
    message: '删除后风控告警不再发送到该渠道；点「保存配置」后才写入后端。',
    okText: '删除',
    danger: true,
  })
  if (!ok) return
  form.channels = form.channels.filter((item) => item.key !== channel.key)
}

/* -------------------------------- SMTP 清除 -------------------------------- */

async function clearCompactCode(): Promise<void> {
  if (!canClearCompact.value) return
  const ok = await confirm({
    title: '清除已保存的 QQ 授权码？',
    message: '保存配置后生效。清除后邮件渠道需要改用下面的服务器 / 账号 / 密码发信。',
    okText: '清除',
    danger: true,
  })
  if (!ok) return
  form.clearCompact = true
  form.smtpCompact = ''
  toast.info('已标记清除，点「保存配置」后生效')
}

function undoClear(): void {
  form.clearCompact = false
}

onMounted(() => {
  void load()
})
</script>

<template>
  <SettingsLayout title="预警管理" :help="pageHelp" :sections="sections" :dirty="dirty">
    <template #actions>
      <Btn variant="default" :icon="Send" :loading="testing" :disabled="saving || loading" @click="testSend">
        发送测试
      </Btn>
      <IconBtn label="重新加载配置" :icon="RefreshCw" :loading="loading && loaded" :disabled="saving" @click="reload" />
      <Btn variant="quiet" :disabled="!dirty || saving || loading" @click="discard">取消修改</Btn>
      <Btn variant="primary" :loading="saving" :disabled="loading" @click="save">保存配置</Btn>
    </template>

    <ErrorBlock
      v-if="loadError && !loaded"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="reload"
    />

    <!-- 1. 告警规则 -->
    <SettingsCard
      id="alerts-rules"
      title="告警规则"
      description="关闭后不再向任何渠道发送通知；风控哨兵仍会记录信号。"
      hide-save
      hide-reset
    >
      <div v-if="!loaded && !loadError" class="skel" aria-hidden="true">
        <div class="skel-row" />
        <div class="skel-row skel-row--short" />
      </div>
      <template v-else-if="loaded">
        <Section first>
          <div class="switch-row">
            <div class="switch-text">
              <span class="row-label">启用预警通知</span>
              <p class="row-hint">关闭后所有渠道都不再发送；面板里的信号不受影响。</p>
            </div>
            <n-switch v-model:value="form.enabled" :disabled="saving" aria-label="启用预警通知" />
          </div>
        </Section>

        <Section title="去重窗口">
          <FormField
            label="同一平台同一业务码多久内只通知一次"
            :hint="
              '当前 ' + describeCooldown(form.cooldown) + '；可填 ' + ALERT_COOLDOWN_MIN + '–' + ALERT_COOLDOWN_MAX + ' 秒。'
            "
          >
            <n-input-number
              v-model:value="cooldownValue"
              :min="ALERT_COOLDOWN_MIN"
              :max="ALERT_COOLDOWN_MAX"
              :step="30"
              :disabled="saving"
              style="width: 180px"
            >
              <template #suffix>秒</template>
            </n-input-number>
          </FormField>
        </Section>
      </template>
    </SettingsCard>

    <!-- 2. 通知渠道 -->
    <SettingsCard
      id="alerts-channels"
      title="通知渠道"
      description="支持多个渠道同时发送；「发送测试」会向全部渠道发一条通知，用的是已保存的配置。"
      hide-save
      hide-reset
    >
      <template #actions>
        <Btn size="sm" variant="default" :icon="Plus" :disabled="saving || !loaded" @click="openAddDrawer">
          添加渠道
        </Btn>
      </template>

      <div v-if="!loaded && !loadError" class="skel" aria-hidden="true">
        <div class="skel-row" />
        <div class="skel-row" />
        <div class="skel-row skel-row--short" />
      </div>
      <template v-else-if="loaded">
        <Empty v-if="!form.channels.length" description="还没有配置通知渠道。风控告警只会记录在面板里。">
          <Btn variant="default" :icon="Plus" :disabled="saving" @click="openAddDrawer">添加渠道</Btn>
        </Empty>

        <ul v-else class="channel-list">
          <li v-for="channel in form.channels" :key="channel.key" class="channel-row">
            <span class="channel-icon" aria-hidden="true">
              <component :is="channelIcon(channel.type)" :size="16" />
            </span>
            <div class="channel-main">
              <div class="channel-head">
                <span class="channel-label">{{ channel.label }}</span>
                <Badge v-if="channel.auto" tone="neutral">默认收件邮箱</Badge>
                <Badge v-else-if="!channel.enabled" tone="warn">已停用</Badge>
              </div>
              <span class="channel-target mono" :title="channel.target">{{ channel.target }}</span>
              <p v-if="channel.auto" class="channel-hint">
                这条由 SMTP 区的「收件邮箱」自动带上，清空收件邮箱后才会消失。
              </p>
              <p v-else-if="!channel.enabled" class="channel-hint">
                保存后该渠道会被移除：后端只保存启用中的渠道。
              </p>
            </div>
            <div class="channel-actions">
              <n-switch
                v-model:value="channel.enabled"
                size="small"
                :disabled="saving || channel.auto"
                aria-label="渠道启用开关"
              />
              <Btn
                size="sm"
                variant="quiet"
                :icon="Send"
                :disabled="testing || saving || loading"
                title="向全部渠道发送一条测试通知"
                @click="testSend"
              >
                发送测试
              </Btn>
              <Btn
                v-if="!channel.auto"
                size="sm"
                variant="danger"
                :icon="Trash2"
                :disabled="saving"
                @click="removeChannel(channel)"
              >
                删除
              </Btn>
            </div>
          </li>
        </ul>

        <Section v-if="testRows.length" title="最近一次测试结果">
          <ul class="test-list">
            <li v-for="row in testRows" :key="row.key" class="test-row">
              <Badge :tone="row.ok ? 'ok' : 'danger'">{{ row.ok ? '成功' : '失败' }}</Badge>
              <span class="test-label">{{ row.label }}</span>
              <span class="test-target mono">{{ row.target }}</span>
              <span v-if="!row.ok" class="test-error">{{ row.error || '未知原因' }}</span>
            </li>
          </ul>
          <p class="card-note">测试时间：{{ testTime }}</p>
        </Section>
      </template>
    </SettingsCard>

    <!-- 3. SMTP（邮件渠道） -->
    <SettingsCard
      id="alerts-smtp"
      title="SMTP（邮件渠道）"
      description="密码与授权码只写不读：输入框留空表示不修改，后端不会回显已保存的值。"
      hide-save
      hide-reset
    >
      <template #actions>
        <Btn size="sm" variant="danger" :disabled="!canClearCompact || saving" @click="clearCompactCode">
          清除授权码
        </Btn>
      </template>

      <div v-if="!loaded && !loadError" class="skel" aria-hidden="true">
        <div class="skel-row" />
        <div class="skel-row" />
        <div class="skel-row skel-row--short" />
      </div>
      <template v-else-if="loaded">
        <div class="health" :class="'health--' + healthView.tone">
          <component :is="healthIcon" :size="16" />
          <span>{{ healthView.text }}</span>
        </div>

        <Section title="QQ 邮箱一条式（推荐）">
          <FormField
            label="QQ 邮箱授权码"
            hint="粘贴 qqSMTP:授权码 或 qqSMTP:你的邮箱@qq.com:授权码；服务器与端口由前缀自动推导，不用填下面的服务器。"
          >
            <div class="compact-row">
              <n-input
                v-model:value="form.smtpCompact"
                type="password"
                show-password-on="click"
                autocomplete="off"
                :placeholder="compactPlaceholder"
                :disabled="saving"
              />
              <HelpTip
                label="QQ 授权码说明"
                content="QQ 邮箱的授权码不是登录密码：在 QQ 邮箱「设置 → 账户」开启 IMAP/SMTP 后生成。填 qqSMTP:授权码 即可，服务器与端口自动推导为 smtp.qq.com:465（SSL）。授权码属于哪个邮箱无法反查，所以「收件邮箱」必须填，否则后端会报缺少发件邮箱。"
              />
            </div>
          </FormField>

          <div v-if="form.clearCompact" class="inline-flag">
            <Badge tone="warn">待清除</Badge>
            <span class="inline-flag__text">保存后已存的 QQ 授权码会被清空。</span>
            <Btn size="sm" variant="quiet" :icon="Undo2" @click="undoClear">撤销</Btn>
          </div>

          <FormField
            label="收件邮箱（默认收件人）"
            hint="QQ 授权码属于哪个邮箱无法反查，这里填自寄邮箱（例如 me@qq.com）；填上后即使没有邮件渠道也会发到这里。"
          >
            <n-input v-model:value="form.mailTo" placeholder="me@qq.com" :disabled="saving" />
          </FormField>
        </Section>

        <Section title="其它邮箱 / 自建 SMTP">
          <div class="smtp-grid">
            <FormField label="服务器">
              <n-input v-model:value="form.smtpHost" placeholder="smtp.example.com" :disabled="saving" />
            </FormField>
            <FormField label="端口">
              <n-input-number v-model:value="form.smtpPort" :min="1" :max="65535" :disabled="saving" />
            </FormField>
            <FormField label="账号">
              <n-input
                v-model:value="form.smtpUser"
                placeholder="bot@example.com"
                autocomplete="off"
                :disabled="saving"
              />
            </FormField>
            <FormField label="密码" hint="留空表示不修改；后端不会回显已保存的密码。">
              <n-input
                v-model:value="form.smtpPassword"
                type="password"
                show-password-on="click"
                autocomplete="off"
                :placeholder="passwordPlaceholder"
                :disabled="saving"
              />
            </FormField>
            <FormField label="发件人">
              <n-input v-model:value="form.smtpFrom" placeholder="bot@example.com" :disabled="saving" />
            </FormField>
            <FormField label="加密" hint="465 端口自动走 SSL；其它端口按此开关走 STARTTLS（587 通常需要）。">
              <div class="switch-inline">
                <n-switch v-model:value="form.smtpStarttls" :disabled="saving" aria-label="使用 STARTTLS" />
                <span class="switch-inline__text">使用 STARTTLS</span>
              </div>
            </FormField>
          </div>
          <p class="card-note">
            同时填了服务器与密码时，后端以这里的设置为准；否则用上面的一条式授权码。
          </p>
        </Section>
      </template>
    </SettingsCard>

    <FormDrawer
      v-model:show="drawerOpen"
      title="添加通知渠道"
      submit-text="添加渠道"
      :submit-disabled="!draft.target.trim() || saving"
      @submit="submitAdd"
    >
      <FormField label="渠道类型" required>
        <n-select
          v-model:value="draft.type"
          :options="typeOptions"
          :disabled="saving"
          @update:value="onDraftTypeChange"
        />
      </FormField>
      <FormField :label="draftTargetLabel" required :hint="draftMeta.hint" :error="draftError">
        <n-input
          v-model:value="draft.target"
          :placeholder="draftMeta.placeholder"
          :disabled="saving"
          clearable
        />
      </FormField>
      <p class="card-note">渠道先加进本地列表，点页头「保存配置」后写入后端。</p>
    </FormDrawer>
  </SettingsLayout>
</template>

<style scoped>
/* 骨架：只用一种样式，不闪烁（05-patterns §3）。 */
.skel {
  display: grid;
  gap: var(--sp-3);
}

.skel-row {
  height: 36px;
  border-radius: var(--r-ctl);
  background: var(--surface-2);
}

.skel-row--short {
  width: 55%;
}

.switch-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
}

.switch-text {
  min-width: 0;
}

.row-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
}

.row-hint {
  margin-top: var(--sp-1);
  max-width: 56ch;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.card-note {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
}

/* 渠道列表：卡片内一行一条，用 1px --border-soft 分隔，不嵌套卡片。 */
.channel-list {
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--border-soft);
}

.channel-row {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
  padding: var(--sp-3) 0;
  border-bottom: 1px solid var(--border-soft);
}

.channel-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 26px;
  height: 26px;
  margin-top: 1px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
  color: var(--fg-2);
}

.channel-main {
  display: grid;
  gap: 2px;
  flex: 1 1 auto;
  min-width: 0;
}

.channel-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.channel-label {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  font-weight: 500;
  color: var(--fg);
}

.channel-target {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.channel-hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--warn);
}

.channel-actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex: none;
}

/* 最近一次测试结果 */
.test-list {
  display: grid;
  gap: var(--sp-2);
  margin: 0 0 var(--sp-2);
  padding: 0;
  list-style: none;
}

.test-row {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  flex-wrap: wrap;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.test-label {
  color: var(--fg);
  font-weight: 500;
}

.test-target {
  color: var(--fg-2);
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.test-error {
  color: var(--danger);
}

/* 邮件链路体检 */
.health {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  padding: var(--sp-3);
  border-radius: var(--r-ctl);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.health--ok {
  background: var(--ok-soft);
  color: var(--ok);
}

.health--warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.compact-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

.compact-row > :first-child {
  flex: 1 1 auto;
  min-width: 0;
}

.inline-flag {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.inline-flag__text {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.smtp-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-4);
}

.switch-inline {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-height: 34px;
}

.switch-inline__text {
  font-size: var(--fs-body);
  color: var(--fg-2);
}

@media (max-width: 720px) {
  .smtp-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .channel-row {
    flex-wrap: wrap;
  }
  .channel-actions {
    width: 100%;
    padding-left: 38px;
    flex-wrap: wrap;
  }
}
</style>
