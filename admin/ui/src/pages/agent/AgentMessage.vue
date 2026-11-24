<script setup lang="ts">
/**
 * 智能体 · 单条消息（从 AgentPage 拆出，r3）。
 *
 * 视觉（对照 Linear / Vercel AI 的对话区）：
 *   · 用户：右侧品牌浅底气泡，右下角收小圆角；
 *   · 助手：左侧 28px 品牌渐变头像 + 无底正文（Markdown），代码块深一档底色、右上角「复制」提示；
 *   · 思考过程：可折叠的灰色引用块；
 *   · 工具调用：一行一张小卡（状态图标 · 工具名 · 摘要 · 耗时），运行中图标旋转（减少动效时静止）；
 *   · 生成中：一行小转圈 + 文案，不做闪烁。
 * 代码块点击复制由页面在消息容器上统一处理（v-html 内容无法挂组件事件）。
 */
import { computed } from 'vue'
import { Bot, CircleAlert, CircleCheck, LoaderCircle, Paperclip, Wrench } from 'lucide-vue-next'
import { assetUrl, renderMarkdown, type ChatMessage, type ChatToolTrace } from '@/api/agent'
import { fmtDuration } from '@/utils/format'
import { useI18n } from '@/i18n'

const props = defineProps<{
  message: ChatMessage
}>()

const { t } = useI18n()

const html = computed(() => (props.message.role === 'assistant' ? renderMarkdown(props.message.content) : ''))
/** 代码块右上角提示文案（CSS content 读自定义属性，语种跟随 i18n）。 */
const copyLabelStyle = computed(() => ({ '--agent-copy-label': JSON.stringify(t('page.agent.copyCode')) }))

function toolIcon(tool: ChatToolTrace) {
  if (tool.status === 'running') return LoaderCircle
  if (tool.status === 'failed') return CircleAlert
  return CircleCheck
}
</script>

<template>
  <article class="agent-msg" :class="'is-' + message.role">
    <template v-if="message.role === 'user'">
      <div class="agent-bubble">
        <p class="agent-text">{{ message.content }}</p>
        <p v-if="message.attachments.length" class="agent-bubble__files">
          <Paperclip :size="14" aria-hidden="true" />
          {{ t('page.agent.attachments', { names: message.attachments.join(t('page.agent.listSeparator')) }) }}
        </p>
      </div>
    </template>

    <template v-else>
      <span class="agent-avatar" aria-hidden="true"><Bot :size="16" /></span>
      <div class="agent-reply">
        <details v-if="message.reasoning" class="agent-think">
          <summary>{{ t('page.agent.reasoning') }}</summary>
          <pre>{{ message.reasoning }}</pre>
        </details>

        <ul v-if="message.tools.length" class="agent-tools" :aria-label="t('page.agent.toolsAria')">
          <li
            v-for="(tool, toolIndex) in message.tools"
            :key="tool.tool + toolIndex"
            class="agent-tool"
            :class="'is-' + tool.status"
          >
            <span class="agent-tool__icon">
              <component :is="toolIcon(tool)" :size="14" aria-hidden="true" />
            </span>
            <Wrench :size="14" class="agent-tool__kind" aria-hidden="true" />
            <span class="agent-tool__name">{{ tool.label }}</span>
            <span v-if="tool.summary && tool.status !== 'running'" class="agent-tool__summary" :title="tool.summary">
              {{ tool.summary }}
            </span>
            <span v-if="tool.ms" class="agent-tool__ms">{{ fmtDuration(tool.ms) }}</span>
          </li>
        </ul>

        <p v-for="(notice, noticeIndex) in message.notices" :key="noticeIndex" class="agent-notice">{{ notice }}</p>

        <div v-if="html" class="agent-md" :style="copyLabelStyle" v-html="html" />

        <div v-if="message.assets.length" class="agent-assets">
          <figure v-for="asset in message.assets" :key="asset.asset_id" class="agent-asset">
            <img
              v-if="asset.kind === 'image'"
              :src="assetUrl(asset.asset_id)"
              :alt="asset.prompt || t('page.agent.imageAlt')"
              loading="lazy"
              width="240"
              height="240"
            />
            <video v-else :src="assetUrl(asset.asset_id)" controls preload="metadata" width="240" height="135" />
            <figcaption>
              {{ t('page.agent.assetCaption', {
                kind: asset.kind === 'image' ? t('page.agent.kind.image') : t('page.agent.kind.video'),
                model: asset.model,
              }) }}<template v-if="asset.credits">{{ t('page.agent.assetCredits', { credits: asset.credits }) }}</template>
            </figcaption>
          </figure>
        </div>

        <p v-if="message.error" class="agent-error" role="alert">
          <CircleAlert :size="14" aria-hidden="true" />
          {{ message.error }}
        </p>
        <p v-if="message.streaming" class="agent-typing" aria-live="polite">
          <LoaderCircle :size="14" class="agent-spin" aria-hidden="true" />
          {{ t('page.agent.generating') }}
        </p>
        <p v-if="message.credits" class="agent-meta">
          {{ t('page.agent.creditsUsed', { credits: message.credits }) }}
        </p>
      </div>
    </template>
  </article>
</template>

<style scoped>
.agent-msg {
  display: grid;
  min-width: 0;
}

.agent-msg.is-user {
  justify-items: end;
}

.agent-msg.is-assistant {
  grid-template-columns: 28px minmax(0, 1fr);
  gap: var(--sp-3);
  align-items: start;
}

/* ---------- 用户气泡 ---------- */
.agent-bubble {
  max-width: min(80%, 620px);
  min-width: 0;
  padding: 10px 14px;
  background: var(--brand-soft);
  border: 1px solid color-mix(in srgb, var(--brand) 14%, transparent);
  border-radius: 14px 14px 4px 14px;
}

.agent-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--fg);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
}

.agent-bubble__files {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: var(--sp-1) 0 0;
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
}

/* ---------- 助手 ---------- */
.agent-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--r-ctl);
  background: var(--grad-brand, var(--brand));
  color: #fff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.18), var(--shadow-1);
}

.agent-reply {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
  padding-top: 3px;
}

.agent-think {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  border-left: 2px solid var(--border-strong);
  padding-left: var(--sp-3);
}

.agent-think summary {
  cursor: pointer;
  width: fit-content;
  border-radius: var(--r-chip);
}

.agent-think summary:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

.agent-think pre {
  margin: var(--sp-1) 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow: auto;
  font-family: inherit;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

/* 工具调用卡 */
.agent-tools {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.agent-tool {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
  height: 32px;
  padding: 0 var(--sp-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.agent-tool__icon {
  display: inline-flex;
  color: var(--ok);
}

.agent-tool.is-running .agent-tool__icon {
  color: var(--brand);
  animation: agent-spin 900ms linear infinite;
}

.agent-tool.is-failed {
  border-color: color-mix(in srgb, var(--danger) 30%, transparent);
  background: var(--danger-soft);
}

.agent-tool.is-failed .agent-tool__icon {
  color: var(--danger);
}

.agent-tool__kind {
  flex: none;
  color: var(--fg-faint);
}

.agent-tool__name {
  flex: none;
  font-family: var(--font-mono);
  font-size: var(--fs-tiny);
  color: var(--fg);
}

.agent-tool__summary {
  flex: 1;
  min-width: 0;
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-tool__ms {
  margin-left: auto;
  flex: none;
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
  font-variant-numeric: tabular-nums;
}

.agent-notice {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--warn-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--warn);
}

/* Markdown 正文 */
.agent-md {
  font-size: var(--fs-body);
  line-height: 1.7;
  color: var(--fg);
  word-break: break-word;
}

.agent-md :deep(> :first-child) { margin-top: 0; }
.agent-md :deep(> :last-child) { margin-bottom: 0; }

.agent-md :deep(p) {
  margin: 0 0 var(--sp-3);
}

.agent-md :deep(h1),
.agent-md :deep(h2),
.agent-md :deep(h3),
.agent-md :deep(h4) {
  margin: var(--sp-4) 0 var(--sp-2);
  font-size: var(--fs-h3);
  line-height: var(--lh-h3);
  font-weight: 600;
  letter-spacing: -0.01em;
}

.agent-md :deep(a) {
  color: var(--brand);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.agent-md :deep(blockquote) {
  margin: 0 0 var(--sp-3);
  padding-left: var(--sp-3);
  border-left: 2px solid var(--border-strong);
  color: var(--fg-muted);
}

.agent-md :deep(ul),
.agent-md :deep(ol) {
  padding-left: var(--sp-5);
  margin: 0 0 var(--sp-3);
}

.agent-md :deep(li + li) {
  margin-top: 2px;
}

.agent-md :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.9em;
  padding: 1px 5px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
}

.agent-md :deep(pre) {
  position: relative;
  margin: 0 0 var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  overflow: auto;
  cursor: copy;
  font-family: var(--font-mono);
  font-size: var(--fs-small);
  line-height: 1.6;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
}

.agent-md :deep(pre code) {
  padding: 0;
  border: 0;
  background: transparent;
  font-size: inherit;
}

.agent-md :deep(pre)::after {
  content: var(--agent-copy-label, '');
  position: absolute;
  top: 6px;
  right: 8px;
  padding: 0 6px;
  height: 20px;
  line-height: 20px;
  border-radius: var(--r-chip);
  background: var(--surface);
  border: 1px solid var(--border-soft);
  font-family: var(--font-sans, inherit);
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
  opacity: 0;
  transition: opacity var(--dur-1, 120ms) var(--ease-out, ease);
  pointer-events: none;
}

.agent-md :deep(pre:hover)::after {
  opacity: 1;
}

.agent-md :deep(table) {
  width: 100%;
  margin: 0 0 var(--sp-3);
  border-collapse: collapse;
  font-size: var(--fs-small);
}

.agent-md :deep(th),
.agent-md :deep(td) {
  padding: 6px var(--sp-2);
  border-bottom: 1px solid var(--border-soft);
  text-align: left;
}

.agent-md :deep(th) {
  color: var(--fg-muted);
  font-weight: 500;
  background: var(--surface-2);
}

.agent-md :deep(hr) {
  border: 0;
  border-top: 1px solid var(--border-soft);
  margin: var(--sp-4) 0;
}

/* 产物 */
.agent-assets {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3);
}

.agent-asset {
  margin: 0;
  width: 240px;
  max-width: 100%;
  display: grid;
  gap: 4px;
}

.agent-asset img,
.agent-asset video {
  width: 100%;
  height: auto;
  border-radius: var(--r-card);
  border: 1px solid var(--border);
  background: var(--surface-2);
  box-shadow: var(--shadow-1);
}

.agent-asset figcaption {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
}

.agent-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--danger-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--danger);
}

.agent-typing {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.agent-spin {
  color: var(--brand);
  animation: agent-spin 900ms linear infinite;
}

.agent-meta {
  margin: 0;
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-faint);
  font-variant-numeric: tabular-nums;
}

@keyframes agent-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .agent-bubble {
    max-width: 92%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .agent-spin,
  .agent-tool.is-running .agent-tool__icon {
    animation: none;
  }
  .agent-md :deep(pre)::after {
    transition: none;
  }
}
</style>
