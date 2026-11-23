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
import {
  TTL_OPTIONS,
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
  type AdminTicketsPayload,
  type GateStatus,
  type OpenConfigPayload,
  type OpenPlatform,
  type OpenTicket,
  type PageCopy,
} from '@/api/openentry'

const HELP_TEXT =
  '多平台聚合入口：总闸关闭时 /join 与 /api/open/* 一律 404；每次开关推进代次，旧链接与邀请码同时作废。' +
  '链接按时间有效、公共共享、不限人数；通过可信渠道发送。'

const toast = useToast()
const confirm = useConfirm()

const sections = [
  { id: 'openentry-gate', title: '入口状态' },
  { id: 'openentry-links', title: '访问链接' },
  { id: 'openentry-platforms', title: '公开页平台' },
  { id: 'openentry-copy', title: '公开页文案' },
]

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

const state = gateStateOf(gate.value)
const meta = computed(() => gateMetaText(gate.value))
const platformSummary = computed(() => platformFlagSummary(config.value?.platforms))

async function loadGate(): Promise<void> {
  gateLoading.value = true
  gateError.value = ''
  try {
    gate.value = await getGateStatus()
  } catch (error) {
    gateError.value = failureText('读取入口状态', error)
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
    configError.value = failureText('读取公开入口配置', error)
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
    ticketsError.value = failureText('读取访问链接', error)
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
    title: open ? '关闭自助注册入口？' : '开启自助注册入口？',
    message: open
      ? '关闭后 /join 立即 404；已签发的全部访问链接与邀请码同时失效，且无法恢复。'
      : '开启后即可签发访问链接。每次开关都会推进代次，旧链接一律作废。',
    okText: open ? '关闭入口' : '开启入口',
    danger: open,
  })
  if (!ok) return
  gateToggling.value = true
  try {
    await toggleGate(!open)
    toast.success(open ? '入口已关闭，旧链接全部失效' : '入口已开启，可生成链接')
    await loadGate()
    await loadTickets()
  } catch (error) {
    toast.error(failureText(open ? '关闭入口' : '开启入口', error))
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
    toast.success('链接时效已保存：新签发的链接按新时效生效')
    await loadConfig()
    await loadGate()
  } catch (error) {
    toast.error(failureText('保存链接时效', error))
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
    toast.error('入口未开启：开启后才能签发访问链接')
    return
  }
  issuing.value = true
  try {
    const count = Math.max(1, Math.min(50, Math.floor(Number(ticketCount.value) || 1)))
    ticketCount.value = String(count)
    const payload = await issueTickets(count)
    issuedUrls.value = payload?.urls || []
    toast.success('已生成 ' + issuedUrls.value.length + ' 条链接，数量之外的有效期见列表')
    await loadGate()
    await loadTickets()
  } catch (error) {
    toast.error(failureText('签发访问链接', error))
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
    toast.success(platform.label + (platform.enabled ? ' 已从公开页隐藏' : ' 已在公开页开放'))
    await loadConfig()
  } catch (error) {
    toast.error(failureText('切换平台开关', error))
  } finally {
    platformSaving.value = ''
  }
}

/* ------------------------------------------------------------------ *
 * 公开页文案
 * ------------------------------------------------------------------ */

const copyDraft = reactive<PageCopy>({ title: '', sub: '', intro: [], foot: '' })
const copyText = reactive({ title: '', sub: '', intro: '', foot: '' })
const copySaving = ref(false)

function syncCopyDraft(copy: PageCopy | null | undefined): void {
  const value = normalizeCopy(copy)
  copyDraft.title = value.title
  copyDraft.sub = value.sub
  copyDraft.intro = value.intro
  copyDraft.foot = value.foot
  copyText.title = value.title
  copyText.sub = value.sub
  copyText.intro = value.intro.join('\n')
  copyText.foot = value.foot
}

const copyFromForm = computed<PageCopy>(() => ({
  title: copyText.title,
  sub: copyText.sub,
  intro: copyText.intro.split('\n'),
  foot: copyText.foot,
}))
const previewLines = computed(() => copyPreviewLines(copyFromForm.value))
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
  const error = copyValidation(copyFromForm.value)
  if (error) {
    toast.error(error)
    return
  }
  copySaving.value = true
  try {
    await saveOpenConfig({ page_copy: normalizeCopy(copyFromForm.value) })
    toast.success('公开页文案已保存')
    await loadConfig()
  } catch (err) {
    toast.error(failureText('保存公开页文案', err))
  } finally {
    copySaving.value = false
  }
}

async function resetCopy(): Promise<void> {
  const ok = await confirm({
    title: '恢复默认文案？',
    message: '公开页会回到默认标题与空说明；已填的标题 / 副标题 / 说明 / 页脚全部清空。',
    okText: '恢复默认',
  })
  if (!ok) return
  copySaving.value = true
  try {
    await saveOpenConfig({ page_copy: { title: '', sub: '', intro: [], foot: '' } })
    toast.success('已恢复默认文案')
    await loadConfig()
  } catch (err) {
    toast.error(failureText('恢复默认文案', err))
  } finally {
    copySaving.value = false
  }
}

const dirty = computed(() => ttlDirty.value || copyDirty.value)
</script>

<template>
  <SettingsLayout title="公开注册入口" :help="HELP_TEXT" :sections="sections" :dirty="dirty">
    <template #actions>
      <Badge :tone="state.tone">{{ state.label }}</Badge>
      <Btn variant="quiet" :icon="RefreshCw" :loading="gateLoading || configLoading || ticketsLoading" @click="reload">
        刷新
      </Btn>
    </template>

    <!-- 1. 入口状态 + 限制参数 -->
    <SettingsCard
      id="openentry-gate"
      title="入口状态"
      description="关闭时 /join 与 /api/open/* 一律 404；每次开关推进代次，旧链接与邀请码同时失效。"
      :dirty="ttlDirty"
      :loading="ttlSaving"
      save-label="保存时效"
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
              自助注册入口
              <Badge :tone="state.tone">{{ state.label }}</Badge>
            </div>
            <p class="oe-note">{{ meta }}</p>
            <p class="oe-note">
              链接有效 {{ ttlLabel(gate?.ticket_ttl) }}，可重复打开（关闸即全部失效）。
            </p>
          </div>
          <div class="oe-gate__switch">
            <n-switch
              :value="Boolean(gate?.open)"
              size="large"
              :loading="gateToggling"
              :disabled="gateLoading"
              aria-label="自助注册入口总开关"
              @update:value="switchGate"
            />
            <Btn variant="danger" size="sm" :icon="Power" :disabled="!gate?.open" @click="switchGate">
              关闭入口使全部失效
            </Btn>
          </div>
        </div>

        <FormField label="链接时效（限制参数）" :hint="'新签发的链接按新时效生效；当前 ' + ttlLabel(gate?.ticket_ttl) + '。'">
          <n-select v-model:value="ttlDraft" :options="TTL_OPTIONS" aria-label="链接时效" style="width: 200px" />
        </FormField>
      </template>
    </SettingsCard>

    <!-- 2. 访问链接 -->
    <SettingsCard
      id="openentry-links"
      title="访问链接"
      description="按时间有效、公共共享、不限人数；后端只保存 jti，明文链接只在生成时返回一次。"
      hide-save
      hide-reset
    >
      <template #actions>
        <Btn variant="default" size="sm" :icon="Link2" :disabled="!canIssue" @click="openDrawer">
          生成访问链接
        </Btn>
      </template>

      <div v-if="ticketsLoading && !tickets" class="oe-skeleton"><n-skeleton text :repeat="3" /></div>
      <ErrorBlock v-else-if="ticketsError" :title="ticketsError" retry @retry="loadTickets" />
      <template v-else>
        <p class="oe-note">
          {{ tickets?.open ? '入口已开启' : '入口未开启（开启后才能签发）' }} · 当前有效 {{ tickets?.count || 0 }} 条 · 代次 {{ tickets?.epoch || 0 }}
        </p>
        <ul v-if="ticketRows.length" class="oe-tickets">
          <li v-for="ticket in ticketRows" :key="ticket.code" class="oe-ticket">
            <Copyable :value="ticketUrl(ticket)" class="oe-ticket__url" />
            <Badge :tone="ticket.permanent ? 'brand' : 'neutral'">{{ ticketExpiryText(ticket) }}</Badge>
          </li>
        </ul>
        <Empty v-else compact description="还没有链接，点「生成访问链接」签发。">
          <Btn variant="default" size="sm" :disabled="!canIssue" @click="openDrawer">生成访问链接</Btn>
        </Empty>
        <p class="oe-note">撤销粒度是「代次」：关闭入口会让全部链接同时失效（后端没有单条撤销接口）。</p>
      </template>
    </SettingsCard>

    <!-- 3. 公开页平台 -->
    <SettingsCard
      id="openentry-platforms"
      title="公开页可见的平台"
      description="只有这里打开的平台会出现在公开注册页；清单来自各平台的公开登录适配器。"
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
              :aria-label="platform.label + ' 是否在公开页可见'"
              @update:value="togglePlatform(platform)"
            />
          </li>
        </ul>
        <Empty v-else compact description="该部署没有可公开注册的平台适配器。" />
      </template>
    </SettingsCard>

    <!-- 4. 公开页文案 + 预览 -->
    <SettingsCard
      id="openentry-copy"
      title="公开页文案"
      description="纯文本，最多 6 行说明；保存后立即对公开页生效。"
      :dirty="copyDirty"
      :loading="copySaving"
      save-label="保存文案"
      :save-disabled="!copyDirty"
      @save="saveCopy"
      @reset="loadConfig"
    >
      <template #actions>
        <Btn variant="quiet" size="sm" :disabled="copySaving" @click="resetCopy">恢复默认</Btn>
      </template>

      <div v-if="configLoading && !config" class="oe-skeleton"><n-skeleton text :repeat="4" /></div>
      <ErrorBlock v-else-if="configError" :title="configError" retry @retry="loadConfig" />
      <div v-else class="oe-copy">
        <div class="oe-copy__form">
          <FormField label="标题" hint="不超过 40 字。">
            <n-input v-model:value="copyText.title" placeholder="MiraiHub 注册" />
          </FormField>
          <FormField label="副标题" hint="不超过 120 字，可留空。">
            <n-input v-model:value="copyText.sub" placeholder="可留空" />
          </FormField>
          <FormField label="说明" hint="一行一条，最多 6 行、每行不超过 200 字。">
            <n-input v-model:value="copyText.intro" type="textarea" :autosize="{ minRows: 3, maxRows: 6 }" placeholder="欢迎使用 MiraiHub；填写邀请码即可注册" />
          </FormField>
          <FormField label="页脚" hint="不超过 160 字，可留空。">
            <n-input v-model:value="copyText.foot" placeholder="可留空" />
          </FormField>
        </div>
        <aside class="oe-copy__preview" aria-label="公开页文案预览">
          <h4>{{ copyDraft.title || 'MiraiHub 注册' }}</h4>
          <p v-for="(line, index) in previewLines.slice(1)" :key="index" class="oe-copy__line">{{ line }}</p>
          <p class="oe-copy__foot">预览仅示意排版，公开页样式以 join.html 为准。</p>
        </aside>
      </div>
    </SettingsCard>

    <FormDrawer
      v-model:show="drawerOpen"
      title="生成访问链接"
      submit-text="生成链接"
      :submit-loading="issuing"
      :submit-disabled="!canIssue"
      @submit="submitIssue"
    >
      <FormField label="生成数量" hint="1–50 条；同一条链接在有效期内可重复打开、不限人数。">
        <n-input v-model:value="ticketCount" placeholder="1" />
      </FormField>
      <ul v-if="issuedUrls.length" class="oe-issued">
        <li v-for="url in issuedUrls" :key="url">
          <Copyable :value="url" />
        </li>
      </ul>
      <p class="oe-note">明文链接只在生成时返回一次；关闸后全部失效。</p>
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
