<script setup lang="ts">
/**
 * S28 公开注册入口 /openentry（模板 C：4 个区块）
 *
 * 契约：docs/ui-v2/contracts/openentry.md
 * 设计稿：docs/ui-v2/06-pages-d.md P22
 * 旧版：#panel-openentry（2580–2695）+ console-app.js（loadJoinGate 2933 / loadOpenEntryConfig 2965 /
 *       loadOpenEntryTickets 3048 / openEntrySaveCopy 3100 / joinGateToggle 3164 / joinGateMakeLink 3193）
 *
 * 三条口径：
 *   1. 开关是「代次」语义：每次 toggle 旧链接与邀请码全部作废（确认框必须写明）；
 *   2. 单条链接无法撤销（后端只有整代失效），页面不伪造；
 *   3. 文案上限与后端 _sanitize_copy 完全一致，前端先拦并指行号。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { NInput, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import { Link2, Power, RefreshCw } from 'lucide-vue-next'
import SettingsLayout from '@/layout/SettingsLayout.vue'
import SettingsCard from '@/layout/SettingsCard.vue'
import Badge from '@/components/base/Badge.vue'
import Btn from '@/components/base/Btn.vue'
import Copyable from '@/components/base/Copyable.vue'
import Empty from '@/components/base/Empty.vue'
import ErrorBlock from '@/components/base/ErrorBlock.vue'
import FormDrawer from '@/components/overlay/FormDrawer.vue'
import FormField from '@/components/overlay/FormField.vue'
import PlatformLogo from '@/components/identity/PlatformLogo.vue'
import { useConfirm, useToast } from '@/composables'
import { failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import {
  buildTicketUrl,
  copyPreviewLines,
  copyValidation,
  gateMetaText,
  gateStateOf,
  getAdminTickets,
  getGateStatus,
  getOpenConfig,
  issueTickets,
  normalizeCopy,
  platformFlagSummary,
  saveOpenConfig,
  ticketExpiryText,
  toggleGate,
  ttlLabel,
  ttlOptions,
  type AdminTicketsPayload,
  type GateStatus,
  type OpenConfigPayload,
  type OpenPlatform,
  type OpenTicket,
  type PageCopy,
} from '@/api/openentry'

const { t } = useI18n()
const helpText = computed(() => t('page.openentry.help'))
const ttlSelectOptions = computed(() => ttlOptions(t))

const toast = useToast()
const confirm = useConfirm()

const sections = computed(() => [
  { id: 'openentry-gate', title: t('page.openentry.section.gate') },
  { id: 'openentry-links', title: t('page.openentry.section.links') },
  { id: 'openentry-platforms', title: t('page.openentry.section.platforms') },
  { id: 'openentry-copy', title: t('page.openentry.section.copy') },
])

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const gateLoading = ref(false)
const gateError = ref('')
const gate = ref<GateStatus | null>(null)
const configLoading = ref(false)
const configError = ref('')
const config = ref<OpenConfigPayload | null>(null)
const ticketsLoading = ref(false)
const ticketsError = ref('')
const tickets = ref<AdminTicketsPayload | null>(null)

/** 页头与入口卡片的 Badge 都读它：必须随 gate 变化求值（setup 时算一次会永远停在「已关闭」）。 */
const state = computed(() => gateStateOf(gate.value, t))
const meta = computed(() => gateMetaText(gate.value, t))
const platformSummary = computed(() => platformFlagSummary(config.value?.platforms, t))

async function loadGate(): Promise<void> {
  gateLoading.value = true
  gateError.value = ''
  try {
    gate.value = await getGateStatus()
  } catch (error) {
    gateError.value = failureText(t('page.openentry.action.loadGate'), error)
  } finally {
    gateLoading.value = false
  }
}

async function loadConfig(): Promise<void> {
  configLoading.value = true
  configError.value = ''
  try {
    config.value = await getOpenConfig()
    syncCopyDraft(config.value?.copy)
    ttlDraft.value = String(config.value?.ticket_ttl ?? 86400)
  } catch (error) {
    configError.value = failureText(t('page.openentry.action.loadConfig'), error)
  } finally {
    configLoading.value = false
  }
}

async function loadTickets(): Promise<void> {
  ticketsLoading.value = true
  ticketsError.value = ''
  try {
    tickets.value = await getAdminTickets()
  } catch (error) {
    ticketsError.value = failureText(t('page.openentry.action.loadTickets'), error)
  } finally {
    ticketsLoading.value = false
  }
}

function reload(): void {
  void loadGate()
  void loadConfig()
  void loadTickets()
}

const origin = ref('')
onMounted(() => {
  origin.value = typeof window !== 'undefined' ? window.location.origin : ''
  reload()
})

/* ------------------------------------------------------------------ *
 * 总开关
 * ------------------------------------------------------------------ */

const gateToggling = ref(false)

async function switchGate(): Promise<void> {
  if (gateToggling.value) return
  const open = Boolean(gate.value?.open)
  const ok = await confirm({
    title: open ? t('page.openentry.confirmGate.closeTitle') : t('page.openentry.confirmGate.openTitle'),
    message: open ? t('page.openentry.confirmGate.closeBody') : t('page.openentry.confirmGate.openBody'),
    okText: open ? t('page.openentry.action.closeGate') : t('page.openentry.action.openGate'),
    danger: open,
  })
  if (!ok) return
  gateToggling.value = true
  try {
    await toggleGate(!open)
    toast.success(open ? t('page.openentry.toast.gateClosed') : t('page.openentry.toast.gateOpened'))
    await loadGate()
    await loadTickets()
  } catch (error) {
    toast.error(
      failureText(
        open ? t('page.openentry.action.closeGate') : t('page.openentry.action.openGate'),
        error,
      ),
    )
  } finally {
    gateToggling.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 链接时效（限制参数）
 * ------------------------------------------------------------------ */

const ttlDraft = ref('86400')
const ttlSaving = ref(false)
const ttlDirty = computed(() => String(config.value?.ticket_ttl ?? 86400) !== ttlDraft.value)

async function saveTtl(): Promise<void> {
  if (!ttlDirty.value || ttlSaving.value) return
  ttlSaving.value = true
  try {
    await saveOpenConfig({ ticket_ttl: Number(ttlDraft.value) })
    toast.success(t('page.openentry.toast.ttlSaved'))
    await loadConfig()
    await loadGate()
  } catch (error) {
    toast.error(failureText(t('page.openentry.action.saveTtl'), error))
  } finally {
    ttlSaving.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 生成链接
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const ticketCount = ref('1')
const issuing = ref(false)
const issuedUrls = ref<string[]>([])

const canIssue = computed(() => Boolean(gate.value?.open))

function openDrawer(): void {
  ticketCount.value = '1'
  issuedUrls.value = []
  drawerOpen.value = true
}

async function submitIssue(): Promise<void> {
  if (!canIssue.value) {
    toast.error(t('page.openentry.toast.gateClosedWarn'))
    return
  }
  issuing.value = true
  try {
    const count = Math.max(1, Math.min(50, Math.floor(Number(ticketCount.value) || 1)))
    ticketCount.value = String(count)
    const payload = await issueTickets(count)
    issuedUrls.value = payload?.urls || []
    toast.success(t('page.openentry.toast.issued', { count: issuedUrls.value.length }))
    await loadGate()
    await loadTickets()
  } catch (error) {
    toast.error(failureText(t('page.openentry.action.issue'), error))
  } finally {
    issuing.value = false
  }
}

const ticketRows = computed(() => tickets.value?.tickets || [])

function ticketUrl(ticket: OpenTicket): string {
  return buildTicketUrl(ticket.code, origin.value)
}

/* ------------------------------------------------------------------ *
 * 平台开关
 * ------------------------------------------------------------------ */

const platformSaving = ref('')

async function togglePlatform(platform: OpenPlatform): Promise<void> {
  if (platformSaving.value) return
  platformSaving.value = platform.id
  try {
    await saveOpenConfig({ platforms: { [platform.id]: !platform.enabled } })
    toast.success(
      platform.enabled
        ? t('page.openentry.toast.platformHidden', { name: platform.label })
        : t('page.openentry.toast.platformShown', { name: platform.label }),
    )
    await loadConfig()
  } catch (error) {
    toast.error(failureText(t('page.openentry.action.togglePlatform'), error))
  } finally {
    platformSaving.value = ''
  }
}

/* ------------------------------------------------------------------ *
 * 公开页文案
 * ------------------------------------------------------------------ */

/**
 * 文案草稿只有一份：输入框与右侧预览共用同一个对象（说明用换行串）。
 * 之前输入框绑一份状态、预览标题绑另一份（只在取数时同步），
 * 结果改了标题预览不动，和副标题/说明的实时表现不一致。
 */
const copyDraft = reactive({ title: '', sub: '', intro: '', foot: '' })
const copySaving = ref(false)

function syncCopyDraft(copy: PageCopy | null | undefined): void {
  const value = normalizeCopy(copy)
  copyDraft.title = value.title
  copyDraft.sub = value.sub
  copyDraft.intro = value.intro.join('\n')
  copyDraft.foot = value.foot
}

const copyFromForm = computed<PageCopy>(() => ({
  title: copyDraft.title,
  sub: copyDraft.sub,
  intro: copyDraft.intro.split('\n'),
  foot: copyDraft.foot,
}))
const previewLines = computed(() => copyPreviewLines(copyFromForm.value, t))
const copyDirty = computed(() => {
  const saved = normalizeCopy(config.value?.copy)
  const next = normalizeCopy(copyFromForm.value)
  return (
    saved.title !== next.title ||
    saved.sub !== next.sub ||
    saved.foot !== next.foot ||
    saved.intro.join('\n') !== next.intro.join('\n')
  )
})

async function saveCopy(): Promise<void> {
  const error = copyValidation(copyFromForm.value, t)
  if (error) {
    toast.error(error)
    return
  }
  copySaving.value = true
  try {
    await saveOpenConfig({ page_copy: normalizeCopy(copyFromForm.value) })
    toast.success(t('page.openentry.toast.copySaved'))
    await loadConfig()
  } catch (err) {
    toast.error(failureText(t('page.openentry.action.saveCopy'), err))
  } finally {
    copySaving.value = false
  }
}

async function resetCopy(): Promise<void> {
  const ok = await confirm({
    title: t('page.openentry.confirmReset.title'),
    message: t('page.openentry.confirmReset.body'),
    okText: t('page.openentry.confirmReset.ok'),
  })
  if (!ok) return
  copySaving.value = true
  try {
    await saveOpenConfig({ page_copy: { title: '', sub: '', intro: [], foot: '' } })
    toast.success(t('page.openentry.toast.copyReset'))
    await loadConfig()
  } catch (err) {
    toast.error(failureText(t('page.openentry.action.resetCopy'), err))
  } finally {
    copySaving.value = false
  }
}

const dirty = computed(() => ttlDirty.value || copyDirty.value)
</script>

<template>
  <SettingsLayout :title="t('page.openentry.title')" :help="helpText" :sections="sections" :dirty="dirty">
    <template #actions>
      <Badge :tone="state.tone">{{ state.label }}</Badge>
      <Btn variant="quiet" :icon="RefreshCw" :loading="gateLoading || configLoading || ticketsLoading" @click="reload">
        {{ t('common.refresh') }}
      </Btn>
    </template>

    <!-- 1. 入口状态 + 限制参数 -->
    <SettingsCard
      id="openentry-gate"
      :title="t('page.openentry.section.gate')"
      :description="t('page.openentry.gateDescription')"
      :dirty="ttlDirty"
      :loading="ttlSaving"
      :save-label="t('page.openentry.saveTtl')"
      :save-disabled="!ttlDirty"
      @save="saveTtl"
      @reset="loadConfig"
    >
      <div v-if="gateLoading && !gate" class="oe-skeleton"><n-skeleton text :repeat="3" /></div>
      <ErrorBlock v-else-if="gateError" :title="gateError" retry @retry="loadGate" />
      <template v-else>
        <div class="oe-gate">
          <div class="oe-gate__text">
            <div class="oe-gate__label">
              {{ t('page.openentry.gateSwitch') }}
              <Badge :tone="state.tone">{{ state.label }}</Badge>
            </div>
            <p class="oe-note">{{ meta }}</p>
            <p class="oe-note">
              {{ t('page.openentry.gateNote', { ttl: ttlLabel(gate?.ticket_ttl, t) }) }}
            </p>
          </div>
          <div class="oe-gate__switch">
            <n-switch
              :value="Boolean(gate?.open)"
              size="large"
              :loading="gateToggling"
              :disabled="gateLoading"
              :aria-label="t('page.openentry.gateAria')"
              @update:value="switchGate"
            />
            <Btn variant="danger" size="sm" :icon="Power" :disabled="!gate?.open" @click="switchGate">
              {{ t('page.openentry.closeGate') }}
            </Btn>
          </div>
        </div>

        <FormField
          :label="t('page.openentry.field.ttl')"
          :hint="t('page.openentry.field.ttlHint', { ttl: ttlLabel(gate?.ticket_ttl, t) })"
        >
          <n-select
            v-model:value="ttlDraft"
            :options="ttlSelectOptions"
            :aria-label="t('page.openentry.field.ttlAria')"
            style="width: 200px"
          />
        </FormField>
      </template>
    </SettingsCard>

    <!-- 2. 访问链接 -->
    <SettingsCard
      id="openentry-links"
      :title="t('page.openentry.section.links')"
      :description="t('page.openentry.linksDescription')"
      hide-save
      hide-reset
    >
      <template #actions>
        <Btn variant="default" size="sm" :icon="Link2" :disabled="!canIssue" @click="openDrawer">
          {{ t('page.openentry.generate') }}
        </Btn>
      </template>

      <div v-if="ticketsLoading && !tickets" class="oe-skeleton"><n-skeleton text :repeat="3" /></div>
      <ErrorBlock v-else-if="ticketsError" :title="ticketsError" retry @retry="loadTickets" />
      <template v-else>
        <p class="oe-note">
          {{
            t('page.openentry.ticketsSummary', {
              state: tickets?.open
                ? t('page.openentry.ticketsOpen')
                : t('page.openentry.ticketsClosed'),
              count: tickets?.count || 0,
              epoch: tickets?.epoch || 0,
            })
          }}
        </p>
        <ul v-if="ticketRows.length" class="oe-tickets">
          <li v-for="ticket in ticketRows" :key="ticket.code" class="oe-ticket">
            <Copyable :value="ticketUrl(ticket)" class="oe-ticket__url" />
            <Badge :tone="ticket.permanent ? 'brand' : 'neutral'">{{ ticketExpiryText(ticket, t) }}</Badge>
          </li>
        </ul>
        <Empty v-else compact :description="t('page.openentry.linksEmpty')">
          <Btn variant="default" size="sm" :disabled="!canIssue" @click="openDrawer">
            {{ t('page.openentry.generate') }}
          </Btn>
        </Empty>
        <p class="oe-note">{{ t('page.openentry.epochNote') }}</p>
      </template>
    </SettingsCard>

    <!-- 3. 公开页平台 -->
    <SettingsCard
      id="openentry-platforms"
      :title="t('page.openentry.section.platforms')"
      :description="t('page.openentry.platformsDescription')"
      hide-save
      hide-reset
    >
      <template #actions>
        <span class="oe-note">{{ platformSummary }}</span>
      </template>

      <div v-if="configLoading && !config" class="oe-skeleton"><n-skeleton text :repeat="3" /></div>
      <ErrorBlock v-else-if="configError" :title="configError" retry @retry="loadConfig" />
      <template v-else>
        <ul v-if="config?.platforms?.length" class="oe-platforms">
          <li v-for="platform in config.platforms" :key="platform.id" class="oe-platform">
            <PlatformLogo :id="platform.id" :size="20" :label="platform.label" />
            <div class="oe-platform__text">
              <span class="oe-platform__name">{{ platform.label }}</span>
              <span class="oe-note">{{ (platform.modes || []).join(' / ') || '—' }}</span>
            </div>
            <n-switch
              :value="platform.enabled"
              :loading="platformSaving === platform.id"
              :aria-label="t('page.openentry.platformAria', { name: platform.label })"
              @update:value="togglePlatform(platform)"
            />
          </li>
        </ul>
        <Empty v-else compact :description="t('page.openentry.platformsEmpty')" />
      </template>
    </SettingsCard>

    <!-- 4. 公开页文案 + 预览 -->
    <SettingsCard
      id="openentry-copy"
      :title="t('page.openentry.section.copy')"
      :description="t('page.openentry.copyDescription')"
      :dirty="copyDirty"
      :loading="copySaving"
      :save-label="t('page.openentry.saveCopy')"
      :save-disabled="!copyDirty"
      @save="saveCopy"
      @reset="loadConfig"
    >
      <template #actions>
        <Btn variant="quiet" size="sm" :disabled="copySaving" @click="resetCopy">
          {{ t('page.openentry.resetCopy') }}
        </Btn>
      </template>

      <div v-if="configLoading && !config" class="oe-skeleton"><n-skeleton text :repeat="4" /></div>
      <ErrorBlock v-else-if="configError" :title="configError" retry @retry="loadConfig" />
      <div v-else class="oe-copy">
        <div class="oe-copy__form">
          <FormField :label="t('page.openentry.field.title')" :hint="t('page.openentry.field.titleHint')">
            <n-input v-model:value="copyDraft.title" :placeholder="t('page.openentry.field.titlePlaceholder')" />
          </FormField>
          <FormField :label="t('page.openentry.field.sub')" :hint="t('page.openentry.field.subHint')">
            <n-input v-model:value="copyDraft.sub" :placeholder="t('page.openentry.field.optional')" />
          </FormField>
          <FormField :label="t('page.openentry.field.intro')" :hint="t('page.openentry.field.introHint')">
            <n-input
              v-model:value="copyDraft.intro"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 6 }"
              :placeholder="t('page.openentry.field.introPlaceholder')"
            />
          </FormField>
          <FormField :label="t('page.openentry.field.foot')" :hint="t('page.openentry.field.footHint')">
            <n-input v-model:value="copyDraft.foot" :placeholder="t('page.openentry.field.optional')" />
          </FormField>
        </div>
        <aside class="oe-copy__preview" :aria-label="t('page.openentry.previewAria')">
          <h4>{{ copyDraft.title.trim() || t('page.openentry.field.titlePlaceholder') }}</h4>
          <p v-for="(line, index) in previewLines.slice(1)" :key="index" class="oe-copy__line">{{ line }}</p>
          <p class="oe-copy__foot">{{ t('page.openentry.previewNote') }}</p>
        </aside>
      </div>
    </SettingsCard>

    <FormDrawer
      v-model:show="drawerOpen"
      :title="t('page.openentry.generate')"
      :submit-text="t('page.openentry.generateSubmit')"
      :submit-loading="issuing"
      :submit-disabled="!canIssue"
      @submit="submitIssue"
    >
      <FormField :label="t('page.openentry.field.count')" :hint="t('page.openentry.field.countHint')">
        <n-input v-model:value="ticketCount" placeholder="1" />
      </FormField>
      <ul v-if="issuedUrls.length" class="oe-issued">
        <li v-for="url in issuedUrls" :key="url">
          <Copyable :value="url" />
        </li>
      </ul>
      <p class="oe-note">{{ t('page.openentry.plainNote') }}</p>
    </FormDrawer>
  </SettingsLayout>
</template>

<style scoped>
.oe-skeleton {
  padding: var(--sp-2) 0;
}

.oe-gate {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
  flex-wrap: wrap;
}

.oe-gate__label {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-h2);
  font-weight: 600;
  color: var(--fg);
}

.oe-gate__switch {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

.oe-note {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
}

.oe-tickets,
.oe-platforms,
.oe-issued {
  display: grid;
  gap: var(--sp-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.oe-ticket {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-width: 0;
}

.oe-ticket__url {
  flex: 1 1 260px;
  min-width: 0;
}

.oe-platform {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) 0;
  border-bottom: 1px solid var(--border-soft);
}

.oe-platform:last-child {
  border-bottom: 0;
}

.oe-platform__text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}

.oe-platform__name {
  font-size: var(--fs-body);
  color: var(--fg);
}

.oe-copy {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: var(--sp-4);
  align-items: start;
}

.oe-copy__form {
  display: grid;
  gap: var(--sp-3);
  min-width: 0;
}

.oe-copy__preview {
  padding: var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface-2);
  min-width: 0;
}

.oe-copy__preview h4 {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-h2);
  color: var(--fg);
}

.oe-copy__line {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  margin: 0 0 var(--sp-1);
}

.oe-copy__foot {
  margin-top: var(--sp-2);
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
}

@media (max-width: 720px) {
  .oe-copy {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
