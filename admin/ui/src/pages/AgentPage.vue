<script setup lang="ts">
/**
 * S29 智能体 /agent（独立三栏布局：会话 240 / 对话 / 设置与产物 300）
 *
 * 契约：docs/ui-v2/contracts/agent.md（本次新建）
 * 设计稿：docs/ui-v2/06-pages-d.md P21
 * 接口：/api/agent/bootstrap · chat(SSE) · conversations · assets · estimate · merge
 *
 * 约定：
 *   · 流式事件的状态收敛在 api/agent.ts:applyChatEvent（纯函数、可单测）；
 *   · Markdown 一律经 marked + DOMPurify 后再 v-html；
 *   · 附件只作为文本标注随消息发送（后端 /chat 没有附件字段，不做假上传）；
 *   · 右栏 ≤1280px 收起为浮层，用顶栏「参数」按钮开关；卸载时断开 matchMedia 与流。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NInput, NSelect, NSkeleton } from 'naive-ui'
import {
  Paperclip,
  Pencil,
  Pin,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  Square,
  Trash2,
  X,
} from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Empty,
  ErrorBlock,
  IconBtn,
  Meter,
  Segmented,
  type SegmentedOption,
} from '@/components'
import { useConfirm, useToast } from '@/composables'
import { fmtRelative } from '@/utils/format'
import { failureText } from '@/utils/error'
import { isAbortError } from '@/api/http'
import { copyText } from '@/platform/clipboard'
import {
  applyChatEvent,
  assetUrl,
  chatStream,
  conversationTitle,
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
  renderMarkdown,
  toChatMessages,
  userChatMessage,
  type AgentAsset,
  type AgentBootstrap,
  type AgentContextUsage,
  type AgentConversation,
  type ChatMessage,
  type ChatRequestBody,
} from '@/api/agent'

const HELP_TEXT =
  '内置创作助手：对话、生图、生视频。回答会边生成边显示（SSE）；' +
  '生成图片/视频会消耗积分，右侧可预估。会话保存在服务端，刷新后仍在。'

const toast = useToast()
const confirm = useConfirm()

type AgentMode = 'chat' | 'image' | 'video'

const PREF_KEY = 'wb_agent_ui'

interface AgentPrefs {
  mode: AgentMode
  model: string
  reasoning: string
  thinking: 'default' | 'on' | 'off'
  imageSize: string
  videoSeconds: string
  videoResolution: string
  videoAspect: string
}

function readPrefs(): AgentPrefs {
  const fallback: AgentPrefs = {
    mode: 'chat',
    model: '',
    reasoning: '',
    thinking: 'default',
    imageSize: '1024x1024',
    videoSeconds: '5',
    videoResolution: '720P',
    videoAspect: '16:9',
  }
  try {
    const raw = JSON.parse(localStorage.getItem(PREF_KEY) || 'null') as Partial<AgentPrefs> | null
    if (!raw || typeof raw !== 'object') return fallback
    return {
      mode: raw.mode === 'image' || raw.mode === 'video' ? raw.mode : 'chat',
      model: typeof raw.model === 'string' ? raw.model : '',
      reasoning: typeof raw.reasoning === 'string' ? raw.reasoning : '',
      thinking: raw.thinking === 'on' || raw.thinking === 'off' ? raw.thinking : 'default',
      imageSize: typeof raw.imageSize === 'string' ? raw.imageSize : fallback.imageSize,
      videoSeconds: String(raw.videoSeconds || fallback.videoSeconds),
      videoResolution: typeof raw.videoResolution === 'string' ? raw.videoResolution : fallback.videoResolution,
      videoAspect: typeof raw.videoAspect === 'string' ? raw.videoAspect : fallback.videoAspect,
    }
  } catch {
    return fallback
  }
}

const prefs = readPrefs()
const mode = ref<AgentMode>(prefs.mode)
const model = ref(prefs.model)
const reasoning = ref(prefs.reasoning)
const thinking = ref(prefs.thinking)
const imageSize = ref(prefs.imageSize)
const videoSeconds = ref(prefs.videoSeconds)
const videoResolution = ref(prefs.videoResolution)
const videoAspect = ref(prefs.videoAspect)

watch(
  [mode, model, reasoning, thinking, imageSize, videoSeconds, videoResolution, videoAspect],
  () => {
    try {
      localStorage.setItem(
        PREF_KEY,
        JSON.stringify({
          mode: mode.value,
          model: model.value,
          reasoning: reasoning.value,
          thinking: thinking.value,
          imageSize: imageSize.value,
          videoSeconds: videoSeconds.value,
          videoResolution: videoResolution.value,
          videoAspect: videoAspect.value,
        }),
      )
    } catch {
      /* 隐私模式写不进去，不影响本次会话 */
    }
  },
)

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

const groups = computed(() => groupConversations(filterConversations(conversations.value, search.value)))

const modelOptions = computed(() => {
  const models = bootstrap.value?.models
  const list = mode.value === 'image' ? models?.image || [] : mode.value === 'video' ? models?.video || [] : models?.chat || []
  return list.map((value) => ({ label: value, value }))
})

const reasoningOptions = [
  { label: '思考档位：默认', value: '' },
  { label: '低', value: 'low' },
  { label: '中', value: 'medium' },
  { label: '高', value: 'high' },
  { label: '极高', value: 'max' },
]

const thinkingOptions = [
  { label: '思考：默认', value: 'default' },
  { label: '思考：开启', value: 'on' },
  { label: '思考：关闭', value: 'off' },
]

const modeOptions: Array<SegmentedOption<string>> = [
  { label: '对话', value: 'chat' },
  { label: '生图', value: 'image' },
  { label: '生视频', value: 'video' },
]

const keyMissing = computed(() => Boolean(bootstrap.value) && !bootstrap.value?.key)

async function loadBootstrap(): Promise<void> {
  bootLoading.value = true
  bootError.value = null
  try {
    const data = await getBootstrap()
    bootstrap.value = data
    ensureModel()
  } catch (error) {
    bootError.value = { message: failureText('加载智能体配置', error), detail: '' }
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
    convError.value = failureText('加载会话', error)
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
const fileInput = ref<HTMLInputElement | null>(null)
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
    messagesError.value = failureText('打开会话', error)
  } finally {
    messagesLoading.value = false
  }
}

function newConversation(): void {
  if (streaming.value) stop()
  activeConvId.value = ''
  messages.value = []
  context.value = null
  messagesError.value = ''
  input.value = ''
  attachments.value = []
  nextTick(() => {
    const el = document.querySelector<HTMLTextAreaElement>('.agent-composer textarea')
    el?.focus()
  })
}

async function removeConversation(item: AgentConversation): Promise<void> {
  const ok = await confirm({
    title: '删除会话「' + conversationTitle(item) + '」？',
    message: '删除后无法恢复；该会话的历史消息不会再出现在列表里。',
    okText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteConversation(item.conversation_id)
    toast.success('已删除会话')
    if (activeConvId.value === item.conversation_id) newConversation()
    await loadConversations()
  } catch (error) {
    toast.error(failureText('删除会话', error))
  }
}

async function togglePin(item: AgentConversation): Promise<void> {
  try {
    await patchConversation(item.conversation_id, { pinned: !item.pinned })
    await loadConversations()
  } catch (error) {
    toast.error(failureText('置顶会话', error))
  }
}

const editingId = ref('')
const editingTitle = ref('')

function startRename(item: AgentConversation): void {
  editingId.value = item.conversation_id
  editingTitle.value = conversationTitle(item)
}

async function saveRename(item: AgentConversation): Promise<void> {
  const title = editingTitle.value.trim()
  editingId.value = ''
  if (!title || title === conversationTitle(item)) return
  try {
    await patchConversation(item.conversation_id, { title })
    await loadConversations()
  } catch (error) {
    toast.error(failureText('重命名会话', error))
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
    toast.warning('先输入内容再发送')
    return
  }
  if (keyMissing.value) {
    toast.error('还没有可用的 API Key，请先在「API Keys」创建')
    return
  }
  const names = attachments.value.map((item) => item.name)
  const note = names.length ? '\n\n（附件：' + names.join('、') + '）' : ''
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
          toast.error(failureText('模型回复', event.message || '未知错误'))
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
        error: isAbortError(error) ? '' : failureText('发送消息', error),
      }
    }
    if (!isAbortError(error)) toast.error(failureText('发送消息', error))
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

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  void send()
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

async function scrollToEnd(): Promise<void> {
  await nextTick()
  const el = messagesEl.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => messages.value.map((item) => item.content.length + item.reasoning.length + item.assets.length).join('-'),
  () => {
    void scrollToEnd()
  },
)

async function onMessageClick(event: MouseEvent): Promise<void> {
  const target = event.target as HTMLElement | null
  const pre = target?.closest('pre')
  if (!pre) return
  const text = pre.textContent || ''
  if (!text.trim()) return
  const ok = await copyText(text)
  if (ok) toast.success('已复制代码')
  else toast.error('复制失败：浏览器拒绝了剪贴板访问')
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
    toast.success(String(result.summary || '已合并所选视频'))
    selectedAssets.value = []
    await loadAssets()
    await loadConversations()
  } catch (error) {
    toast.error(failureText('合并视频', error))
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

const imageSizeOptions = ['1024x1024', '1024x1536', '1280x720', '1536x1024'].map((value) => ({ label: value, value }))
const resolutionOptions = ['720P', '1080P'].map((value) => ({ label: value, value }))
const aspectOptions = ['16:9', '9:16', '1:1'].map((value) => ({ label: value, value }))
const secondsOptions = [5, 6, 8, 10].map((value) => ({ label: value + ' 秒', value: String(value) }))

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

onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', syncNarrow)
  mediaQuery = null
  stop()
  if (estimateTimer) clearTimeout(estimateTimer)
})
</script>

<template>
  <div class="agent-page">
    <PageHeader title="智能体" :help="HELP_TEXT">
      <template #actions>
        <Segmented :options="modeOptions" :model-value="mode" aria-label="创作模式" @update:model-value="(value: string | number) => (mode = value === 'image' || value === 'video' ? value : 'chat')" />
        <span v-if="context" class="agent-context">
          上下文 {{ context.percent }}%
          <Meter :value="context.percent" tone="brand" :aria-label="'上下文占用 ' + context.percent + '%'" />
        </span>
        <IconBtn label="参数与产物" :icon="Settings2" :active="asideOpen" @click="asideOpen = !asideOpen" />
      </template>
    </PageHeader>

    <ErrorBlock v-if="bootError" :title="bootError.message" :detail="bootError.detail" @retry="loadBootstrap" />

    <div v-else-if="bootLoading && !bootstrap" class="agent-skeleton" aria-busy="true">
      <n-skeleton height="480px" />
    </div>

    <div v-else class="agent-layout">
      <!-- 左栏：会话 -->
      <aside class="agent-conv">
        <div class="agent-conv__head">
          <Btn variant="primary" size="sm" :icon="Plus" block @click="newConversation">新会话</Btn>
        </div>
        <n-input v-model:value="search" size="small" placeholder="搜索会话" clearable />
        <div class="agent-conv__list">
          <p v-if="convLoading && !conversations.length" class="agent-muted">正在加载会话…</p>
          <ErrorBlock v-else-if="convError" :title="convError" compact @retry="loadConversations" />
          <p v-else-if="!groups.length" class="agent-muted">{{ conversations.length ? '没有匹配的会话' : '还没有会话，开始创作吧' }}</p>
          <div v-for="group in groups" v-else :key="group.key" class="agent-conv__group">
            <p class="agent-conv__label">{{ group.label }}</p>
            <div
              v-for="item in group.items"
              :key="item.conversation_id"
              class="agent-conv__item"
              :class="{ 'is-active': item.conversation_id === activeConvId }"
              role="button"
              tabindex="0"
              @click="openConversation(item)"
              @keydown.enter="openConversation(item)"
            >
              <n-input
                v-if="editingId === item.conversation_id"
                v-model:value="editingTitle"
                size="tiny"
                @click.stop
                @keydown.enter.stop="saveRename(item)"
                @blur="saveRename(item)"
              />
              <template v-else>
                <span class="agent-conv__title" :title="conversationTitle(item)">{{ conversationTitle(item) }}</span>
                <span class="agent-conv__time">{{ fmtRelative(item.updated_at || item.created_at) }}</span>
              </template>
              <span class="agent-conv__ops" @click.stop>
                <IconBtn label="重命名" :icon="Pencil" @click="startRename(item)" />
                <IconBtn :label="item.pinned ? '取消置顶' : '置顶'" :icon="Pin" :active="item.pinned" @click="togglePin(item)" />
                <IconBtn label="删除会话" :icon="Trash2" @click="removeConversation(item)" />
              </span>
            </div>
          </div>
        </div>
      </aside>

      <!-- 中栏：对话 -->
      <section class="agent-chat">
        <p v-if="keyMissing" class="agent-banner is-warn">
          还没有可用的 API Key。请先在「API Keys」创建，智能体会复用它的配额与分组。
        </p>

        <div ref="messagesEl" class="agent-messages" @click="onMessageClick">
          <p v-if="messagesLoading" class="agent-muted">正在加载会话…</p>
          <ErrorBlock v-else-if="messagesError" :title="messagesError" compact @retry="activeConvId && openConversation({ conversation_id: activeConvId } as AgentConversation)" />
          <div v-else-if="!messages.length" class="agent-start">
            <p class="agent-start__title">开始创作</p>
            <div class="agent-start__cards">
              <button type="button" class="agent-start__card" @click="mode = 'chat'">
                <strong>对话</strong>
                <span>问问题、查用法、让它调用工具</span>
              </button>
              <button type="button" class="agent-start__card" @click="mode = 'image'">
                <strong>生图</strong>
                <span>封面、插图、海报、产品图</span>
              </button>
              <button type="button" class="agent-start__card" @click="mode = 'video'">
                <strong>生视频</strong>
                <span>分镜、长视频分段生成与合并</span>
              </button>
            </div>
          </div>

          <article
            v-for="(message, index) in messages"
            :key="index"
            class="agent-msg"
            :class="'is-' + message.role"
          >
            <div class="agent-bubble">
              <template v-if="message.role === 'user'">
                <p class="agent-text">{{ message.content }}</p>
                <p v-if="message.attachments.length" class="agent-muted">附件：{{ message.attachments.join('、') }}</p>
              </template>
              <template v-else>
                <details v-if="message.reasoning" class="agent-think">
                  <summary>思考过程</summary>
                  <pre>{{ message.reasoning }}</pre>
                </details>
                <div v-if="message.tools.length" class="agent-tools">
                  <span v-for="tool in message.tools" :key="tool.tool + tool.status" class="agent-tool" :class="'is-' + tool.status">
                    {{ tool.label }}
                    <template v-if="tool.status === 'running'">…</template>
                    <template v-else-if="tool.summary"> · {{ tool.summary }}</template>
                  </span>
                </div>
                <p v-for="(notice, noticeIndex) in message.notices" :key="noticeIndex" class="agent-notice">{{ notice }}</p>
                <div v-if="message.content" class="agent-md" v-html="renderMarkdown(message.content)" />
                <div v-if="message.assets.length" class="agent-assets">
                  <figure v-for="asset in message.assets" :key="asset.asset_id" class="agent-asset">
                    <img v-if="asset.kind === 'image'" :src="assetUrl(asset.asset_id)" :alt="asset.prompt || '生成图片'" loading="lazy" />
                    <video v-else :src="assetUrl(asset.asset_id)" controls preload="metadata" />
                    <figcaption>{{ asset.kind === 'image' ? '图片' : '视频' }} · {{ asset.model }}<template v-if="asset.credits"> · {{ asset.credits }} 积分</template></figcaption>
                  </figure>
                </div>
                <p v-if="message.error" class="agent-error">{{ message.error }}</p>
                <p v-if="message.streaming" class="agent-typing">生成中…</p>
                <p v-if="message.credits" class="agent-muted">本轮消耗 {{ message.credits }} 积分</p>
              </template>
            </div>
          </article>
        </div>

        <div class="agent-composer">
          <div v-if="attachments.length" class="agent-attachments">
            <span v-for="(file, index) in attachments" :key="file.name + index" class="agent-attachment">
              {{ file.name }}
              <button type="button" aria-label="移除附件" @click="removeAttachment(index)"><X :size="12" /></button>
            </span>
          </div>
          <n-input
            v-model:value="input"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 8 }"
            :placeholder="mode === 'image' ? '描述你想生成的画面…' : mode === 'video' ? '描述分镜、镜头与光线…' : '输入消息，Enter 发送，Shift+Enter 换行'"
            @keydown="onKeydown"
          />
          <div class="agent-composer__bar">
            <IconBtn label="添加附件" :icon="Paperclip" @click="fileInput?.click()" />
            <input ref="fileInput" class="agent-file" type="file" multiple @change="onFiles" />
            <span class="agent-muted">Enter 发送 · Shift+Enter 换行</span>
            <Btn :variant="sending ? 'danger' : 'primary'" :icon="sending ? Square : Send" @click="send">
              {{ sending ? '停止' : '发送' }}
            </Btn>
          </div>
        </div>
      </section>

      <!-- 右栏：设置与产物 -->
      <aside class="agent-aside" :class="{ 'is-overlay': narrow, 'is-open': asideOpen }">
        <Card title="模型与参数">
          <div class="agent-field">
            <span class="agent-label">模型</span>
            <n-select :value="model" :options="modelOptions" size="small" placeholder="选择模型" @update:value="(value: string | number | null) => (model = String(value || ''))" />
          </div>
          <div class="agent-field">
            <span class="agent-label">思考</span>
            <n-select :value="thinking" :options="thinkingOptions" size="small" @update:value="(value: string | number | null) => (thinking = value === 'on' || value === 'off' ? value : 'default')" />
          </div>
          <div class="agent-field">
            <span class="agent-label">思考档位</span>
            <n-select :value="reasoning" :options="reasoningOptions" size="small" @update:value="(value: string | number | null) => (reasoning = String(value || ''))" />
          </div>
          <div v-if="mode === 'image'" class="agent-field">
            <span class="agent-label">尺寸</span>
            <n-select :value="imageSize" :options="imageSizeOptions" size="small" @update:value="(value: string | number | null) => (imageSize = String(value || ''))" />
          </div>
          <template v-if="mode === 'video'">
            <div class="agent-field">
              <span class="agent-label">时长</span>
              <n-select :value="videoSeconds" :options="secondsOptions" size="small" @update:value="(value: string | number | null) => (videoSeconds = String(value || '5'))" />
            </div>
            <div class="agent-field">
              <span class="agent-label">分辨率</span>
              <n-select :value="videoResolution" :options="resolutionOptions" size="small" @update:value="(value: string | number | null) => (videoResolution = String(value || ''))" />
            </div>
            <div class="agent-field">
              <span class="agent-label">画面比例</span>
              <n-select :value="videoAspect" :options="aspectOptions" size="small" @update:value="(value: string | number | null) => (videoAspect = String(value || ''))" />
            </div>
          </template>
          <p class="agent-muted">
            <template v-if="mode === 'chat'">对话按 Token 计费，模型会自动选用当前分组内可用的账号。</template>
            <template v-else>预计消耗 {{ estimate === null ? '—' : estimate + ' 积分' }}（仅提示，以实际为准）。</template>
          </p>
        </Card>

        <Card title="产物" :loading="assetsLoading">
          <template #extra>
            <Badge v-if="selectedAssets.length" tone="brand">{{ selectedAssets.length }} 已选</Badge>
          </template>
          <div v-if="assets.length" class="agent-gallery">
            <button
              v-for="asset in assets"
              :key="asset.asset_id"
              type="button"
              class="agent-gallery__item"
              :class="{ 'is-selected': selectedAssets.includes(asset.asset_id) }"
              :title="asset.prompt || asset.asset_id"
              @click="toggleAsset(asset.asset_id)"
            >
              <img v-if="asset.kind === 'image'" :src="assetUrl(asset.asset_id)" alt="" loading="lazy" />
              <video v-else :src="assetUrl(asset.asset_id)" preload="metadata" mute />
              <span class="agent-gallery__kind">{{ asset.kind === 'image' ? '图' : '视频' }}</span>
            </button>
          </div>
          <Empty v-else description="还没有生成任何图片或视频。" compact />
          <template #footer>
            <Btn variant="quiet" size="sm" :icon="RefreshCw" :loading="assetsLoading" @click="loadAssets">刷新</Btn>
            <Btn
              variant="default"
              size="sm"
              :disabled="selectedAssets.length < 2"
              :loading="merging"
              @click="mergeSelected"
            >
              合并所选视频
            </Btn>
          </template>
        </Card>
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

.agent-skeleton {
  min-width: 0;
}

.agent-layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 300px;
  gap: var(--sp-4);
  align-items: stretch;
  min-height: calc(100vh - 220px);
  min-width: 0;
}

/* ---------- 左栏 ---------- */
.agent-conv {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: var(--sp-3);
  padding: var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  min-height: 0;
}

.agent-conv__list {
  overflow: auto;
  min-height: 0;
  display: grid;
  align-content: start;
  gap: var(--sp-2);
}

.agent-conv__group {
  display: grid;
  gap: 2px;
}

.agent-conv__label {
  margin: var(--sp-2) 0 2px;
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
}

.agent-conv__item {
  position: relative;
  display: grid;
  gap: 2px;
  padding: var(--sp-2);
  border-radius: var(--r-ctl);
  cursor: pointer;
  min-width: 0;
}

.agent-conv__item:hover,
.agent-conv__item.is-active {
  background: var(--surface-hover);
}

.agent-conv__title {
  font-size: var(--fs-small);
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-conv__time {
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
}

.agent-conv__ops {
  position: absolute;
  right: 2px;
  top: 2px;
  display: none;
  align-items: center;
  background: var(--surface);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-chip);
}

.agent-conv__item:hover .agent-conv__ops {
  display: flex;
}

/* ---------- 中栏 ---------- */
.agent-chat {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: var(--sp-3);
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  min-height: 0;
}

.agent-messages {
  overflow: auto;
  display: grid;
  align-content: start;
  gap: var(--sp-4);
  padding: var(--sp-2);
  min-height: 0;
}

.agent-msg.is-user {
  justify-items: end;
  display: grid;
}

.agent-bubble {
  max-width: 100%;
  min-width: 0;
}

.agent-msg.is-user .agent-bubble {
  max-width: 80%;
  background: var(--brand-soft);
  border-radius: var(--r-card);
  padding: var(--sp-3);
}

.agent-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--fg);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
}

.agent-muted {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.agent-think {
  margin-bottom: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.agent-think summary {
  cursor: pointer;
}

.agent-think pre {
  margin: var(--sp-1) 0 0;
  padding: var(--sp-2);
  background: var(--surface-2);
  border-radius: var(--r-ctl);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-tiny);
}

.agent-tools {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1);
  margin-bottom: var(--sp-2);
}

.agent-tool {
  font-size: var(--fs-tiny);
  padding: 1px var(--sp-2);
  border-radius: var(--r-chip);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  color: var(--fg-muted);
}

.agent-tool.is-failed {
  color: var(--danger);
  border-color: var(--danger-soft);
}

.agent-notice {
  margin: 0 0 var(--sp-1);
  font-size: var(--fs-small);
  color: var(--warn);
}

.agent-md {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--fg);
  word-break: break-word;
}

.agent-md :deep(p) {
  margin: 0 0 var(--sp-2);
}

.agent-md :deep(pre) {
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  padding: var(--sp-3);
  overflow: auto;
  cursor: copy;
  font-family: var(--font-mono);
  font-size: var(--fs-small);
}

.agent-md :deep(code) {
  font-family: var(--font-mono);
  font-size: var(--fs-small);
}

.agent-md :deep(ul),
.agent-md :deep(ol) {
  padding-left: var(--sp-5);
  margin: 0 0 var(--sp-2);
}

.agent-assets {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
}

.agent-asset {
  margin: 0;
  width: 200px;
  display: grid;
  gap: 2px;
}

.agent-asset img,
.agent-asset video {
  width: 100%;
  border-radius: var(--r-ctl);
  border: 1px solid var(--border);
  background: var(--surface-2);
}

.agent-asset figcaption {
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
}

.agent-error {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-small);
  color: var(--danger);
}

.agent-typing {
  margin: var(--sp-1) 0 0;
  font-size: var(--fs-small);
  color: var(--brand);
}

.agent-start {
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-6) 0;
}

.agent-start__title {
  margin: 0;
  font-size: var(--fs-h2);
  color: var(--fg);
  font-weight: 600;
}

.agent-start__cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-3);
}

.agent-start__card {
  display: grid;
  gap: var(--sp-1);
  text-align: left;
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  cursor: pointer;
  color: var(--fg);
}

.agent-start__card:hover {
  border-color: var(--brand);
}

.agent-start__card strong {
  font-size: var(--fs-body);
}

.agent-start__card span {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.agent-banner {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  font-size: var(--fs-small);
}

.agent-banner.is-warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.agent-composer {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
}

.agent-composer__bar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

.agent-composer__bar .agent-muted {
  flex: 1;
}

.agent-file {
  display: none;
}

.agent-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1);
}

.agent-attachment {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  height: 22px;
  padding: 0 var(--sp-2);
  border-radius: var(--r-chip);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  font-size: var(--fs-tiny);
  color: var(--fg-2);
}

.agent-attachment button {
  border: 0;
  background: transparent;
  color: var(--fg-muted);
  cursor: pointer;
  padding: 0;
  display: inline-flex;
}

/* ---------- 右栏 ---------- */
.agent-aside {
  display: grid;
  gap: var(--sp-3);
  align-content: start;
  min-width: 0;
}

.agent-field {
  display: grid;
  gap: 2px;
  margin-bottom: var(--sp-2);
}

.agent-label {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.agent-gallery {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-1);
}

.agent-gallery__item {
  position: relative;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--r-chip);
  overflow: hidden;
  background: var(--surface-2);
  cursor: pointer;
  aspect-ratio: 1;
}

.agent-gallery__item.is-selected {
  border-color: var(--brand);
  box-shadow: 0 0 0 1px var(--brand);
}

.agent-gallery__item img,
.agent-gallery__item video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.agent-gallery__kind {
  position: absolute;
  left: 2px;
  bottom: 2px;
  font-size: 10px;
  padding: 0 3px;
  border-radius: 2px;
  background: var(--surface);
  color: var(--fg-muted);
}

.agent-context {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
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

  .agent-aside {
    position: fixed;
    top: calc(var(--topbar-h) + var(--sp-4));
    right: var(--sp-4);
    bottom: var(--sp-4);
    width: 300px;
    max-width: calc(100vw - 32px);
    padding: var(--sp-3);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--r-card);
    box-shadow: var(--shadow-pop);
    overflow: auto;
    z-index: 900;
    transform: translateX(calc(100% + 24px));
    transition: transform var(--t-slow) var(--ease);
  }

  .agent-aside.is-open {
    transform: none;
  }
}

@media (max-width: 640px) {
  .agent-layout {
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
  }

  .agent-conv {
    max-height: 220px;
  }

  .agent-start__cards {
    grid-template-columns: minmax(0, 1fr);
  }

  .agent-msg.is-user .agent-bubble {
    max-width: 92%;
  }

  .agent-aside {
    width: calc(100vw - 32px);
  }
}
</style>
