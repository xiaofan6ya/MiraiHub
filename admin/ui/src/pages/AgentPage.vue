<script setup lang="ts">
/**
 * S29 智能体 /agent（独立三栏布局：会话 240 / 对话 / 设置与产物 300）
 *
 * 契约：docs/ui-v2/contracts/agent.md
 * 设计稿：docs/ui-v2/06-pages-d.md P21
 * 接口：/api/agent/bootstrap · chat(SSE) · conversations · assets · estimate · merge
 *
 * 约定：
 *   · 流式事件的状态收敛在 api/agent.ts:applyChatEvent（纯函数、可单测）；
 *   · Markdown 一律经 marked + DOMPurify 后再 v-html；
 *   · 附件只作为文本标注随消息发送（后端 /chat 没有附件字段，不做假上传）；
 *   · 右栏 ≤1280px 收起为浮层，用页头「参数」按钮开关（Esc 关闭）；卸载时断开 matchMedia 与流。
 *
 * r3 拆分（入口路径与默认导出不变）：
 *   agent/AgentConvList.vue（左栏）· AgentStart.vue（开始创作）· AgentMessage.vue（单条消息）·
 *   AgentComposer.vue（输入框）· AgentSettingsCard.vue / AgentAssetsCard.vue（右栏）· agentPrefs.ts（本地偏好）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Settings2 } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import { ErrorBlock, IconBtn, Meter, type SegmentedOption } from '@/components'
import AgentConvList from './agent/AgentConvList.vue'
import AgentStart from './agent/AgentStart.vue'
import AgentMessage from './agent/AgentMessage.vue'
import AgentComposer from './agent/AgentComposer.vue'
import AgentSettingsCard from './agent/AgentSettingsCard.vue'
import AgentAssetsCard from './agent/AgentAssetsCard.vue'
import { readAgentPrefs, writeAgentPrefs, type AgentMode, type ThinkingPref } from './agent/agentPrefs'
import { useRevisit } from './_shared/useRevisit'
import { useConfirm, useToast } from '@/composables'
import { failureText } from '@/utils/error'
import { isAbortError } from '@/api/http'
import { copyText } from '@/platform/clipboard'
import { useI18n, type MessageKey } from '@/i18n'
import {
  applyChatEvent,
  chatStream,
  deleteConversation,
  emptyAssistantMessage,
  estimateCredits,
  filterConversations,
  getBootstrap,
  getConversation,
  groupConversations,
  listAssets,
  listConversations,
  mergeAssets,
  patchConversation,
  toChatMessages,
  userChatMessage,
  type AgentAsset,
  type AgentBootstrap,
  type AgentContextUsage,
  type AgentConversation,
  type ChatMessage,
  type ChatRequestBody,
} from '@/api/agent'

const { t } = useI18n()
/** 页头说明（语种跟随 useI18n）。 */
const helpText = computed(() => t('page.agent.help'))

const toast = useToast()
const confirm = useConfirm()

/* ------------------------------------------------------------------ *
 * 本地偏好
 * ------------------------------------------------------------------ */

const prefs = readAgentPrefs()
const mode = ref<AgentMode>(prefs.mode)
const model = ref(prefs.model)
const reasoning = ref(prefs.reasoning)
const thinking = ref<ThinkingPref>(prefs.thinking)
const imageSize = ref(prefs.imageSize)
const videoSeconds = ref(prefs.videoSeconds)
const videoResolution = ref(prefs.videoResolution)
const videoAspect = ref(prefs.videoAspect)

watch([mode, model, reasoning, thinking, imageSize, videoSeconds, videoResolution, videoAspect], () => {
  writeAgentPrefs({
    mode: mode.value,
    model: model.value,
    reasoning: reasoning.value,
    thinking: thinking.value,
    imageSize: imageSize.value,
    videoSeconds: videoSeconds.value,
    videoResolution: videoResolution.value,
    videoAspect: videoAspect.value,
  })
})

/* ------------------------------------------------------------------ *
 * 首屏 / 会话列表
 * ------------------------------------------------------------------ */

const bootstrap = ref<AgentBootstrap | null>(null)
const bootLoading = ref(false)
const bootError = ref<{ message: string; detail: string } | null>(null)

const conversations = ref<AgentConversation[]>([])
const convLoading = ref(false)
const convError = ref('')
const search = ref('')
const activeConvId = ref('')

/** 左栏分组标题跟随语言（分组口径仍由 api/agent.ts:groupConversations 决定）。 */
const GROUP_LABEL_KEYS: Record<string, MessageKey> = {
  pinned: 'page.agent.group.pinned',
  today: 'page.agent.group.today',
  earlier: 'page.agent.group.earlier',
}

const groups = computed(() =>
  groupConversations(filterConversations(conversations.value, search.value)).map((group) => ({
    ...group,
    label: t(GROUP_LABEL_KEYS[group.key] ?? 'page.agent.group.earlier'),
  })),
)

/** 会话标题：空标题回「新会话」（按当前语言）。 */
function displayTitle(item: AgentConversation): string {
  return String(item.title || '').trim() || t('page.agent.newChat')
}

const modelOptions = computed(() => {
  const models = bootstrap.value?.models
  const list =
    mode.value === 'image' ? models?.image || [] : mode.value === 'video' ? models?.video || [] : models?.chat || []
  return list.map((value) => ({ label: value, value }))
})

const modeOptions = computed<Array<SegmentedOption<string>>>(() => [
  { label: t('page.agent.mode.chat'), value: 'chat' },
  { label: t('page.agent.mode.image'), value: 'image' },
  { label: t('page.agent.mode.video'), value: 'video' },
])

const keyMissing = computed(() => Boolean(bootstrap.value) && !bootstrap.value?.key)

async function loadBootstrap(): Promise<void> {
  bootLoading.value = true
  bootError.value = null
  try {
    const data = await getBootstrap()
    bootstrap.value = data
    ensureModel()
  } catch (error) {
    // 二次进入静默刷新失败时保留旧数据，不把整页换成错误块。
    if (!bootstrap.value) {
      bootError.value = { message: failureText(t('page.agent.action.loadBootstrap'), error), detail: '' }
    }
  } finally {
    bootLoading.value = false
  }
}

function ensureModel(): void {
  const list = modelOptions.value.map((item) => item.value)
  if (!list.length) return
  if (!model.value || !list.includes(model.value)) model.value = list[0]
}

watch([mode, () => bootstrap.value], ensureModel)

async function loadConversations(): Promise<void> {
  convLoading.value = true
  convError.value = ''
  try {
    const payload = await listConversations(60)
    conversations.value = Array.isArray(payload?.items) ? payload.items : []
  } catch (error) {
    convError.value = failureText(t('page.agent.action.loadChats'), error)
  } finally {
    convLoading.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 当前会话
 * ------------------------------------------------------------------ */

const messages = ref<ChatMessage[]>([])
const messagesLoading = ref(false)
const messagesError = ref('')
const context = ref<AgentContextUsage | null>(null)
const streaming = ref(false)
const input = ref('')
const attachments = ref<Array<{ name: string; size: number }>>([])
const messagesEl = ref<HTMLElement | null>(null)

let abort: AbortController | null = null

async function openConversation(item: AgentConversation): Promise<void> {
  if (streaming.value) stop()
  activeConvId.value = item.conversation_id
  messagesLoading.value = true
  messagesError.value = ''
  messages.value = []
  context.value = null
  try {
    const data = await getConversation(item.conversation_id)
    messages.value = toChatMessages(data.messages)
    context.value = data.context ?? null
    const storedMode = String(data.mode || '')
    if (storedMode === 'image' || storedMode === 'video') mode.value = storedMode
    if (data.model) model.value = data.model
    if (data.reasoning !== undefined) reasoning.value = String(data.reasoning || '')
    await scrollToEnd()
  } catch (error) {
    messagesError.value = failureText(t('page.agent.action.openChat'), error)
  } finally {
    messagesLoading.value = false
  }
}

function retryOpen(): void {
  if (activeConvId.value) void openConversation({ conversation_id: activeConvId.value } as AgentConversation)
}

function focusComposer(): void {
  void nextTick(() => {
    document.querySelector<HTMLTextAreaElement>('.agent-composer textarea')?.focus()
  })
}

function newConversation(): void {
  if (streaming.value) stop()
  activeConvId.value = ''
  messages.value = []
  context.value = null
  messagesError.value = ''
  input.value = ''
  attachments.value = []
  focusComposer()
}

async function removeConversation(item: AgentConversation): Promise<void> {
  const ok = await confirm({
    title: t('page.agent.confirm.deleteTitle', { title: displayTitle(item) }),
    message: t('page.agent.confirm.deleteBody'),
    okText: t('page.agent.confirm.deleteOk'),
    danger: true,
  })
  if (!ok) return
  try {
    await deleteConversation(item.conversation_id)
    toast.success(t('page.agent.deleted'))
    if (activeConvId.value === item.conversation_id) newConversation()
    await loadConversations()
  } catch (error) {
    toast.error(failureText(t('page.agent.deleteChat'), error))
  }
}

async function togglePin(item: AgentConversation): Promise<void> {
  try {
    await patchConversation(item.conversation_id, { pinned: !item.pinned })
    await loadConversations()
  } catch (error) {
    toast.error(failureText(t('page.agent.action.pinChat'), error))
  }
}

async function renameConversation(item: AgentConversation, title: string): Promise<void> {
  try {
    await patchConversation(item.conversation_id, { title })
    await loadConversations()
  } catch (error) {
    toast.error(failureText(t('page.agent.action.renameChat'), error))
  }
}

/* ------------------------------------------------------------------ *
 * 发送 / 停止
 * ------------------------------------------------------------------ */

const sending = computed(() => streaming.value)

async function send(): Promise<void> {
  if (streaming.value) {
    stop()
    return
  }
  const text = input.value.trim()
  if (!text) {
    toast.warning(t('page.agent.toast.emptyInput'))
    return
  }
  if (keyMissing.value) {
    toast.error(t('page.agent.keyMissingToast'))
    return
  }
  const names = attachments.value.map((item) => item.name)
  const note = names.length
    ? '\n\n' + t('page.agent.attachmentsNote', { names: names.join(t('page.agent.listSeparator')) })
    : ''
  const userMessage = userChatMessage(text, names)
  messages.value.push(userMessage)
  messages.value.push(emptyAssistantMessage())
  const index = messages.value.length - 1
  input.value = ''
  attachments.value = []
  streaming.value = true
  const controller = new AbortController()
  abort = controller
  const body: ChatRequestBody = {
    message: text + note,
    conversation_id: activeConvId.value || undefined,
    model: model.value || undefined,
    mode: mode.value,
    reasoning: reasoning.value || undefined,
    thinking: thinking.value === 'on' ? true : thinking.value === 'off' ? false : null,
  }
  if (mode.value === 'image') body.image = { model: model.value, size: imageSize.value }
  if (mode.value === 'video') {
    body.video = {
      model: model.value,
      seconds: Number(videoSeconds.value) || 5,
      resolution: videoResolution.value,
      aspect: videoAspect.value,
    }
  }
  try {
    await chatStream(
      body,
      (event) => {
        if (event.type === 'meta') {
          if (event.conversation_id) activeConvId.value = event.conversation_id
          if (event.context) context.value = event.context
          return
        }
        const current = messages.value[index]
        if (!current) return
        messages.value[index] = applyChatEvent(current, event)
        if (event.type === 'done') {
          if (event.context) context.value = event.context
          void loadConversations()
          void loadAssets()
        }
        if (event.type === 'error') {
          // 上游没给原因时 applyChatEvent 会填中文兜底，这里按当前语言覆盖。
          if (!event.message) {
            const failed = messages.value[index]
            if (failed) messages.value[index] = { ...failed, error: t('page.agent.modelFailed') }
          }
          toast.error(failureText(t('page.agent.action.modelReply'), event.message || t('page.agent.unknownError')))
        }
      },
      controller.signal,
    )
  } catch (error) {
    const current = messages.value[index]
    if (current) {
      messages.value[index] = {
        ...current,
        streaming: false,
        error: isAbortError(error) ? '' : failureText(t('page.agent.action.sendMessage'), error),
      }
    }
    if (!isAbortError(error)) toast.error(failureText(t('page.agent.action.sendMessage'), error))
  } finally {
    if (abort === controller) abort = null
    streaming.value = false
    void scrollToEnd()
  }
}

function stop(): void {
  abort?.abort()
  abort = null
  streaming.value = false
}

function onFiles(event: Event): void {
  const target = event.target as HTMLInputElement
  for (const file of Array.from(target.files || [])) {
    if (attachments.value.length >= 5) break
    attachments.value.push({ name: file.name, size: file.size })
  }
  target.value = ''
}

function removeAttachment(index: number): void {
  attachments.value.splice(index, 1)
}

/**
 * 自动滚到底：只有用户本来就在底部附近（< 80px）时才跟随，
 * 往上翻看历史时流式输出不会把视图拽回底部。
 */
let stickToBottom = true

function onMessagesScroll(): void {
  const el = messagesEl.value
  if (!el) return
  stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

async function scrollToEnd(force = false): Promise<void> {
  await nextTick()
  const el = messagesEl.value
  if (!el) return
  if (force || stickToBottom) {
    el.scrollTop = el.scrollHeight
    stickToBottom = true
  }
}

watch(
  () => messages.value.map((item) => item.content.length + item.reasoning.length + item.assets.length).join('-'),
  () => {
    void scrollToEnd()
  },
)

watch(
  () => messages.value.length,
  (length, previous) => {
    // 新发一条（用户消息 + 助手占位）时一定滚到底。
    if (length > (previous ?? 0)) void scrollToEnd(true)
  },
)

async function onMessageClick(event: MouseEvent): Promise<void> {
  const target = event.target as HTMLElement | null
  const pre = target?.closest('pre')
  if (!pre || !pre.closest('.agent-md')) return
  const text = pre.textContent || ''
  if (!text.trim()) return
  const ok = await copyText(text)
  if (ok) toast.success(t('page.agent.copied'))
  else toast.error(t('page.agent.copyFailed'))
}

/* ------------------------------------------------------------------ *
 * 资产 / 积分预估
 * ------------------------------------------------------------------ */

const assets = ref<AgentAsset[]>([])
const assetsLoading = ref(false)
const selectedAssets = ref<string[]>([])
const merging = ref(false)

async function loadAssets(): Promise<void> {
  assetsLoading.value = true
  try {
    const payload = await listAssets('', 60)
    assets.value = Array.isArray(payload?.items) ? payload.items : []
  } catch {
    assets.value = []
  } finally {
    assetsLoading.value = false
  }
}

function toggleAsset(id: string): void {
  const next = new Set(selectedAssets.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedAssets.value = Array.from(next)
}

async function mergeSelected(): Promise<void> {
  if (selectedAssets.value.length < 2 || merging.value) return
  merging.value = true
  try {
    const result = await mergeAssets(selectedAssets.value)
    toast.success(String(result.summary || t('page.agent.merged')))
    selectedAssets.value = []
    await loadAssets()
    await loadConversations()
  } catch (error) {
    toast.error(failureText(t('page.agent.action.mergeVideos'), error))
  } finally {
    merging.value = false
  }
}

const estimate = ref<number | null>(null)
let estimateTimer: ReturnType<typeof setTimeout> | null = null

function scheduleEstimate(): void {
  if (estimateTimer) clearTimeout(estimateTimer)
  if (mode.value === 'chat') {
    estimate.value = null
    return
  }
  estimateTimer = setTimeout(async () => {
    try {
      const body =
        mode.value === 'image'
          ? { kind: 'image', count: 1, size: imageSize.value, model: model.value }
          : { kind: 'video', seconds: Number(videoSeconds.value) || 5, resolution: videoResolution.value, model: model.value }
      const result = await estimateCredits(body)
      const value = Number(result.credits ?? result.total_credits ?? result.total ?? 0)
      estimate.value = Number.isFinite(value) && value > 0 ? value : null
    } catch {
      estimate.value = null
    }
  }, 300)
}

watch([mode, model, imageSize, videoSeconds, videoResolution], scheduleEstimate)

/* ------------------------------------------------------------------ *
 * 右栏折叠（≤1280 收起为浮层）
 * ------------------------------------------------------------------ */

const narrow = ref(false)
const asideOpen = ref(true)
let mediaQuery: MediaQueryList | null = null

function syncNarrow(): void {
  narrow.value = Boolean(mediaQuery?.matches)
  asideOpen.value = !narrow.value
}

function onAsideKey(event: KeyboardEvent): void {
  if (event.key === 'Escape' && narrow.value && asideOpen.value) {
    asideOpen.value = false
  }
}

onMounted(() => {
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    mediaQuery = window.matchMedia('(max-width: 1279px)')
    syncNarrow()
    mediaQuery.addEventListener('change', syncNarrow)
  }
  void loadBootstrap()
  void loadConversations()
  void loadAssets()
  scheduleEstimate()
})

// 二次进入（KeepAlive 命中）：保留当前对话与旧列表，后台静默刷新会话与产物；流式中不打扰。
useRevisit(() => {
  if (streaming.value) return
  void loadConversations()
  void loadAssets()
  if (!bootstrap.value) void loadBootstrap()
})

onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', syncNarrow)
  mediaQuery = null
  stop()
  if (estimateTimer) clearTimeout(estimateTimer)
})
</script>

<template>
  <div class="agent-page">
    <PageHeader :title="t('page.agent.title')" :help="helpText">
      <!-- 页头不放「对话 / 生图 / 生视频」分段：与「开始创作」三张卡是同一组选择（第 2 轮审查 P1-6），
           模式切换在输入框工具栏，对话开始后也能切。 -->
      <template #actions>
        <span v-if="context" class="agent-context">
          <span class="tnum">{{ t('page.agent.context', { percent: context.percent }) }}</span>
          <Meter :value="context.percent" tone="brand" :aria-label="t('page.agent.contextAria', { percent: context.percent })" />
        </span>
        <IconBtn :label="t('page.agent.asideToggle')" :icon="Settings2" :active="asideOpen" @click="asideOpen = !asideOpen" />
      </template>
    </PageHeader>

    <ErrorBlock v-if="bootError" :title="bootError.message" :detail="bootError.detail" @retry="loadBootstrap" />

    <!-- 首屏骨架：三栏形状与真实布局一致，数据到来不跳动。 -->
    <div v-else-if="bootLoading && !bootstrap" class="agent-layout agent-skeleton" aria-busy="true" :aria-label="t('common.loading')">
      <div class="agent-sk-col">
        <span class="mh-skeleton agent-sk agent-sk--btn" />
        <span v-for="index in 6" :key="index" class="mh-skeleton agent-sk agent-sk--row" />
      </div>
      <div class="agent-sk-col agent-sk-col--chat">
        <span class="mh-skeleton agent-sk agent-sk--title" />
        <span class="mh-skeleton agent-sk agent-sk--cards" />
        <span class="mh-skeleton agent-sk agent-sk--composer" />
      </div>
      <div class="agent-sk-col agent-sk-col--aside">
        <span class="mh-skeleton agent-sk agent-sk--card" />
        <span class="mh-skeleton agent-sk agent-sk--card" />
      </div>
    </div>

    <div v-else class="agent-layout">
      <AgentConvList
        v-model:search="search"
        :groups="groups"
        :total="conversations.length"
        :loading="convLoading"
        :error="convError"
        :active-id="activeConvId"
        @open="openConversation"
        @new="newConversation"
        @rename="renameConversation"
        @pin="togglePin"
        @remove="removeConversation"
        @retry="loadConversations"
      />

      <section class="agent-chat" :aria-label="t('page.agent.title')">
        <p v-if="keyMissing" class="agent-banner agent-chat__banner" role="status">
          {{ t('page.agent.keyMissing') }}
        </p>

        <div ref="messagesEl" class="agent-messages" @click="onMessageClick" @scroll.passive="onMessagesScroll">
          <div v-if="messagesLoading" class="agent-msg-sk" aria-busy="true" :aria-label="t('page.agent.loadingChats')">
            <span class="mh-skeleton agent-sk agent-sk--bubble" />
            <span class="mh-skeleton agent-sk agent-sk--reply" />
            <span class="mh-skeleton agent-sk agent-sk--bubble" />
          </div>
          <ErrorBlock v-else-if="messagesError" :title="messagesError" compact @retry="retryOpen" />
          <AgentStart v-else-if="!messages.length" v-model:mode="mode" />

          <AgentMessage v-for="(message, index) in messages" :key="index" :message="message" />
        </div>

        <AgentComposer
          v-model="input"
          v-model:mode="mode"
          class="agent-chat__composer"
          :show-mode="messages.length > 0"
          :sending="sending"
          :attachments="attachments"
          :mode-options="modeOptions"
          @send="send"
          @files="onFiles"
          @remove-attachment="removeAttachment"
        />
      </section>

      <aside
        class="agent-aside"
        :class="{ 'is-overlay': narrow, 'is-open': asideOpen }"
        :aria-hidden="narrow && !asideOpen ? 'true' : undefined"
        @keydown="onAsideKey"
      >
        <AgentSettingsCard
          v-model:model="model"
          v-model:thinking="thinking"
          v-model:reasoning="reasoning"
          v-model:image-size="imageSize"
          v-model:video-seconds="videoSeconds"
          v-model:video-resolution="videoResolution"
          v-model:video-aspect="videoAspect"
          :mode="mode"
          :model-options="modelOptions"
          :estimate="estimate"
        />
        <AgentAssetsCard
          :assets="assets"
          :loading="assetsLoading"
          :selected="selectedAssets"
          :merging="merging"
          @toggle="toggleAsset"
          @refresh="loadAssets"
          @merge="mergeSelected"
        />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.agent-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.agent-layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 300px;
  gap: var(--sp-4);
  align-items: stretch;
  height: calc(100vh - 200px);
  min-height: 520px;
  min-width: 0;
}

/* ---------- 骨架 ---------- */
.agent-sk-col {
  display: grid;
  align-content: start;
  gap: var(--sp-2);
  min-width: 0;
}

.agent-sk-col--chat {
  grid-template-rows: auto auto minmax(0, 1fr);
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  padding-top: var(--sp-8, 48px);
  gap: var(--sp-4);
}

.agent-sk-col--aside {
  gap: var(--sp-3);
}

.agent-sk {
  display: block;
  border-radius: var(--r-ctl);
}

.agent-sk--btn { height: 28px; }
.agent-sk--row { height: 40px; }
.agent-sk--title { height: 32px; width: 40%; }
.agent-sk--cards { height: 112px; border-radius: var(--r-card); }
.agent-sk--composer { height: 96px; align-self: end; border-radius: var(--r-lg, 14px); }
.agent-sk--card { height: 220px; border-radius: var(--r-card); }
.agent-sk--bubble { height: 44px; width: 46%; justify-self: end; border-radius: 14px; }
.agent-sk--reply { height: 96px; width: 82%; }

.agent-msg-sk {
  display: grid;
  gap: var(--sp-4);
}

/* ---------- 中栏 ---------- */
.agent-chat {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  min-height: 0;
}

/*
 * 中栏三行固定放置：横幅 / 消息 / 输入框。原来横幅 v-if 不出现时只剩两个子元素，
 * 输入框落进了 minmax(0, 1fr) 那一行被拉高（第 2 轮审查 P2）。
 */
.agent-chat__banner {
  grid-row: 1;
  margin: 0 0 var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.agent-messages {
  grid-row: 2;
  overflow: auto;
  display: grid;
  align-content: start;
  gap: var(--sp-6);
  padding: var(--sp-2) var(--sp-2) var(--sp-4);
  min-height: 0;
  scroll-behavior: smooth;
  overscroll-behavior: contain;
  /* 顶部淡出：长对话滚动时内容在卡片上沿柔和消失。 */
  mask-image: linear-gradient(to bottom, transparent 0, #000 12px);
}

.agent-chat__composer {
  grid-row: 3;
  margin-top: var(--sp-2);
}

/* ---------- 右栏 ---------- */
.agent-aside {
  display: grid;
  gap: var(--sp-3);
  align-content: start;
  min-width: 0;
  overflow: auto;
}

.agent-context {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
  min-width: 120px;
}

.agent-context :deep(.mh-meter) {
  width: 64px;
}

@media (max-width: 1279px) {
  .agent-layout {
    grid-template-columns: 220px minmax(0, 1fr);
  }

  .agent-sk-col--aside {
    display: none;
  }

  .agent-aside {
    position: fixed;
    top: calc(var(--topbar-h) + var(--sp-4));
    right: var(--sp-4);
    bottom: var(--sp-4);
    width: 300px;
    max-width: calc(100vw - 32px);
    padding: var(--sp-3);
    background: var(--surface-raised, var(--surface));
    border: 1px solid var(--border);
    border-radius: var(--r-card);
    box-shadow: var(--shadow-pop);
    z-index: 900;
    transform: translateX(calc(100% + 24px));
    visibility: hidden;
    transition:
      transform var(--dur-3, 260ms) var(--ease-out, ease),
      visibility 0s linear var(--dur-3, 260ms);
  }

  .agent-aside.is-open {
    transform: none;
    visibility: visible;
    transition:
      transform var(--dur-3, 260ms) var(--ease-out, ease),
      visibility 0s;
  }
}

@media (max-width: 640px) {
  .agent-layout {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
    min-height: 0;
  }

  .agent-sk-col:not(.agent-sk-col--chat) {
    display: none;
  }

  .agent-messages {
    max-height: 60vh;
  }

  .agent-aside {
    width: calc(100vw - 32px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .agent-messages {
    scroll-behavior: auto;
  }
  .agent-aside,
  .agent-aside.is-open {
    transition: none;
  }
}
</style>
