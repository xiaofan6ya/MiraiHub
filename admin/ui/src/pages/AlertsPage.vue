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
import { useI18n } from '@/i18n'
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

const { t } = useI18n()

const toast = useToast()
const confirm = useConfirm()

const pageHelp = computed(() => t('page.alerts.help'))

const sections = computed(() => [
  { id: 'alerts-rules', title: t('page.alerts.section.rules') },
  { id: 'alerts-channels', title: t('page.alerts.section.channels') },
  { id: 'alerts-smtp', title: t('page.alerts.section.smtp') },
])

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
  if (form.clearCompact) return t('page.alerts.placeholder.compactCleared')
  if (hasCompact.value) return t('page.alerts.placeholder.compactSet')
  return t('page.alerts.placeholder.compactEmpty')
})
const passwordPlaceholder = computed(() =>
  hasPassword.value ? t('page.alerts.placeholder.passwordSet') : t('page.alerts.placeholder.passwordEmpty'),
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
    const message = failureText(t('page.alerts.action.load'), error)
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
    title: t('page.alerts.confirmDiscard.title'),
    message: t('page.alerts.confirmDiscard.body'),
    okText: t('page.alerts.confirmDiscard.ok'),
  })
  if (!ok) return
  await load({ silent: true })
}

async function save(): Promise<void> {
  if (saving.value) return
  const cooldown = Number(form.cooldown)
  if (!Number.isFinite(cooldown) || cooldown < ALERT_COOLDOWN_MIN || cooldown > ALERT_COOLDOWN_MAX) {
    toast.error(
      t('page.alerts.toast.cooldownInvalid', { min: ALERT_COOLDOWN_MIN, max: ALERT_COOLDOWN_MAX }),
    )
    return
  }
  saving.value = true
  try {
    await putAlertConfig(buildAlertConfigBody(form))
    toast.success(t('page.alerts.toast.saved'))
    await load({ silent: true })
  } catch (error) {
    toast.error(failureText(t('page.alerts.action.save'), error))
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
    toast.error(failureText(t('page.alerts.action.testSend'), error))
  } finally {
    testing.value = false
  }
}

/* --------------------------------- 渠道编辑 --------------------------------- */

const drawerOpen = ref(false)
const draft = reactive<{ type: string; target: string }>({ type: ALERT_CHANNEL_TYPES[0], target: '' })
const draftError = ref('')
const draftMeta = computed(() => alertChannelMeta(draft.type, channelLabelMap.value))
const draftTargetLabel = computed(() =>
  draftMeta.value.kind === 'email' ? t('page.alerts.field.mailTo') : t('page.alerts.field.webhook'),
)

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
    draftError.value = t('page.alerts.drawer.duplicate')
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
  toast.success(t('page.alerts.toast.channelAdded'))
}

async function removeChannel(channel: AlertChannelView): Promise<void> {
  if (channel.auto) return
  const ok = await confirm({
    title: t('page.alerts.confirmRemove.title', { label: channel.label }),
    message: t('page.alerts.confirmRemove.body'),
    okText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  form.channels = form.channels.filter((item) => item.key !== channel.key)
}

/* -------------------------------- SMTP 清除 -------------------------------- */

async function clearCompactCode(): Promise<void> {
  if (!canClearCompact.value) return
  const ok = await confirm({
    title: t('page.alerts.confirmClear.title'),
    message: t('page.alerts.confirmClear.body'),
    okText: t('page.alerts.confirmClear.ok'),
    danger: true,
  })
  if (!ok) return
  form.clearCompact = true
  form.smtpCompact = ''
  toast.info(t('page.alerts.toast.clearMarked'))
}

function undoClear(): void {
  form.clearCompact = false
}

onMounted(() => {
  void load()
})
</script>

<template>
  <SettingsLayout
    :title="t('page.alerts.title')"
    :help="pageHelp"
    :sections="sections"
    :dirty="dirty"
  >
    <template #actions>
      <Btn
        variant="default"
        :icon="Send"
        :loading="testing"
        :disabled="saving || loading"
        @click="testSend"
      >
        {{ t('page.alerts.action.test') }}
      </Btn>
      <IconBtn
        :label="t('page.alerts.action.reload')"
        :icon="RefreshCw"
        :loading="loading && loaded"
        :disabled="saving"
        @click="reload"
      />
      <Btn variant="quiet" :disabled="!dirty || saving || loading" @click="discard">
        {{ t('page.alerts.action.discard') }}
      </Btn>
      <Btn variant="primary" :loading="saving" :disabled="loading" @click="save">
        {{ t('page.alerts.action.saveConfig') }}
      </Btn>
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
      :title="t('page.alerts.section.rules')"
      :description="t('page.alerts.rules.description')"
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
              <span class="row-label">{{ t('page.alerts.rules.enabled') }}</span>
              <p class="row-hint">{{ t('page.alerts.rules.enabledHint') }}</p>
            </div>
            <n-switch
              v-model:value="form.enabled"
              :disabled="saving"
              :aria-label="t('page.alerts.rules.enabled')"
            />
          </div>
        </Section>

        <Section :title="t('page.alerts.cooldown.title')">
          <FormField
            :label="t('page.alerts.cooldown.label')"
            :hint="
              t('page.alerts.cooldown.hint', {
                value: describeCooldown(form.cooldown),
                min: ALERT_COOLDOWN_MIN,
                max: ALERT_COOLDOWN_MAX,
              })
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
              <template #suffix>{{ t('page.alerts.unit.second') }}</template>
            </n-input-number>
          </FormField>
        </Section>
      </template>
    </SettingsCard>

    <!-- 2. 通知渠道 -->
    <SettingsCard
      id="alerts-channels"
      :title="t('page.alerts.section.channels')"
      :description="t('page.alerts.channels.description')"
      hide-save
      hide-reset
    >
      <template #actions>
        <Btn
          size="sm"
          variant="default"
          :icon="Plus"
          :disabled="saving || !loaded"
          @click="openAddDrawer"
        >
          {{ t('page.alerts.channels.add') }}
        </Btn>
      </template>

      <div v-if="!loaded && !loadError" class="skel" aria-hidden="true">
        <div class="skel-row" />
        <div class="skel-row" />
        <div class="skel-row skel-row--short" />
      </div>
      <template v-else-if="loaded">
        <Empty v-if="!form.channels.length" :description="t('page.alerts.channels.empty')">
          <Btn variant="default" :icon="Plus" :disabled="saving" @click="openAddDrawer">
            {{ t('page.alerts.channels.add') }}
          </Btn>
        </Empty>

        <ul v-else class="channel-list">
          <li v-for="channel in form.channels" :key="channel.key" class="channel-row">
            <span class="channel-icon" aria-hidden="true">
              <component :is="channelIcon(channel.type)" :size="16" />
            </span>
            <div class="channel-main">
              <div class="channel-head">
                <span class="channel-label">{{ channel.label }}</span>
                <Badge v-if="channel.auto" tone="neutral">{{ t('page.alerts.channels.auto') }}</Badge>
                <Badge v-else-if="!channel.enabled" tone="warn">
                  {{ t('page.alerts.channels.disabled') }}
                </Badge>
              </div>
              <span class="channel-target mono" :title="channel.target">{{ channel.target }}</span>
              <p v-if="channel.auto" class="channel-hint">
                {{ t('page.alerts.channels.autoHint') }}
              </p>
              <p v-else-if="!channel.enabled" class="channel-hint">
                {{ t('page.alerts.channels.disabledHint') }}
              </p>
            </div>
            <div class="channel-actions">
              <n-switch
                v-model:value="channel.enabled"
                size="small"
                :disabled="saving || channel.auto"
                :aria-label="t('page.alerts.channels.switchAria')"
              />
              <Btn
                size="sm"
                variant="quiet"
                :icon="Send"
                :disabled="testing || saving || loading"
                :title="t('page.alerts.channels.testTitle')"
                @click="testSend"
              >
                {{ t('page.alerts.action.test') }}
              </Btn>
              <Btn
                v-if="!channel.auto"
                size="sm"
                variant="danger"
                :icon="Trash2"
                :disabled="saving"
                @click="removeChannel(channel)"
              >
                {{ t('common.delete') }}
              </Btn>
            </div>
          </li>
        </ul>

        <Section v-if="testRows.length" :title="t('page.alerts.test.title')">
          <ul class="test-list">
            <li v-for="row in testRows" :key="row.key" class="test-row">
              <Badge :tone="row.ok ? 'ok' : 'danger'">
                {{ row.ok ? t('page.alerts.test.ok') : t('page.alerts.test.fail') }}
              </Badge>
              <span class="test-label">{{ row.label }}</span>
              <span class="test-target mono">{{ row.target }}</span>
              <span v-if="!row.ok" class="test-error">
                {{ row.error || t('page.alerts.test.unknownReason') }}
              </span>
            </li>
          </ul>
          <p class="card-note">{{ t('page.alerts.test.time', { time: testTime }) }}</p>
        </Section>
      </template>
    </SettingsCard>

    <!-- 3. SMTP（邮件渠道） -->
    <SettingsCard
      id="alerts-smtp"
      :title="t('page.alerts.section.smtp')"
      :description="t('page.alerts.smtp.description')"
      hide-save
      hide-reset
    >
      <template #actions>
        <Btn
          size="sm"
          variant="danger"
          :disabled="!canClearCompact || saving"
          @click="clearCompactCode"
        >
          {{ t('page.alerts.smtp.clear') }}
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

        <Section :title="t('page.alerts.smtp.qqTitle')">
          <FormField
            :label="t('page.alerts.smtp.qqLabel')"
            :hint="t('page.alerts.smtp.qqHint')"
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
                :label="t('page.alerts.smtp.helpLabel')"
                :content="t('page.alerts.smtp.helpContent')"
              />
            </div>
          </FormField>

          <div v-if="form.clearCompact" class="inline-flag">
            <Badge tone="warn">{{ t('page.alerts.smtp.pendingClear') }}</Badge>
            <span class="inline-flag__text">{{ t('page.alerts.smtp.pendingClearNote') }}</span>
            <Btn size="sm" variant="quiet" :icon="Undo2" @click="undoClear">
              {{ t('page.alerts.smtp.undoClear') }}
            </Btn>
          </div>

          <FormField
            :label="t('page.alerts.smtp.mailToLabel')"
            :hint="t('page.alerts.smtp.mailToHint')"
          >
            <n-input v-model:value="form.mailTo" placeholder="me@qq.com" :disabled="saving" />
          </FormField>
        </Section>

        <Section :title="t('page.alerts.smtp.otherTitle')">
          <div class="smtp-grid">
            <FormField :label="t('page.alerts.smtp.host')">
              <n-input v-model:value="form.smtpHost" placeholder="smtp.example.com" :disabled="saving" />
            </FormField>
            <FormField :label="t('page.alerts.smtp.port')">
              <n-input-number v-model:value="form.smtpPort" :min="1" :max="65535" :disabled="saving" />
            </FormField>
            <FormField :label="t('page.alerts.smtp.user')">
              <n-input
                v-model:value="form.smtpUser"
                placeholder="bot@example.com"
                autocomplete="off"
                :disabled="saving"
              />
            </FormField>
            <FormField :label="t('page.alerts.smtp.password')" :hint="t('page.alerts.smtp.passwordHint')">
              <n-input
                v-model:value="form.smtpPassword"
                type="password"
                show-password-on="click"
                autocomplete="off"
                :placeholder="passwordPlaceholder"
                :disabled="saving"
              />
            </FormField>
            <FormField :label="t('page.alerts.smtp.from')">
              <n-input v-model:value="form.smtpFrom" placeholder="bot@example.com" :disabled="saving" />
            </FormField>
            <FormField :label="t('page.alerts.smtp.tls')" :hint="t('page.alerts.smtp.tlsHint')">
              <div class="switch-inline">
                <n-switch
                  v-model:value="form.smtpStarttls"
                  :disabled="saving"
                  :aria-label="t('page.alerts.smtp.starttls')"
                />
                <span class="switch-inline__text">{{ t('page.alerts.smtp.starttls') }}</span>
              </div>
            </FormField>
          </div>
          <p class="card-note">{{ t('page.alerts.smtp.priorityNote') }}</p>
        </Section>
      </template>
    </SettingsCard>

    <FormDrawer
      v-model:show="drawerOpen"
      :title="t('page.alerts.drawer.title')"
      :submit-text="t('page.alerts.channels.add')"
      :submit-disabled="!draft.target.trim() || saving"
      @submit="submitAdd"
    >
      <FormField :label="t('page.alerts.drawer.type')" required>
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
      <p class="card-note">{{ t('page.alerts.drawer.note') }}</p>
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
