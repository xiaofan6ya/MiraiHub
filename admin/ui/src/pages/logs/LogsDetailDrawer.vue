<script setup lang="ts">
/**
 * 调用详情抽屉（10-visual-refresh：失败行在抽屉里展示诊断信息和尝试记录）。
 *
 * 结构：顶部 4 个关键数（首字 / 耗时 / Token / 积分）→ 诊断（失败时置顶：错误说明、
 * 网关与上游状态码徽章、逐次尝试记录、阶段耗时，原始 JSON 折叠）→ 请求信息 → 请求正文（内存缓存）。
 * 诊断字段来自 admin/request_metrics.py:usage_evidence，经 api/logs.ts:diagnosticView 结构化；
 * 开发者用语（字段名、JSON）只在折叠的「原始诊断」里出现。
 */
import { computed, reactive, ref, watch } from 'vue'
import { ChevronRight, Copy, Download, Network, UserRound } from 'lucide-vue-next'
import { Badge, Btn, CodeBlock, DetailDrawer, ErrorBlock, KV, Section, TitleTip } from '@/components'
import { useCopy, useToast } from '@/composables'
import { ApiError, isAbortError } from '@/api/http'
import { FMT_EMPTY, fmtCredit, fmtDuration, fmtFullTime, fmtInt, fmtRelative } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import {
  buildCurl,
  diagnosticView,
  downloadLogCapture,
  getCaptureBody,
  getUsageLog,
  httpStatusTone,
  toLogRow,
  type CaptureBodyPage,
  type LogRowView,
  type UsageLogDetail,
} from '@/api/logs'
import { errorKindText, networkErrorText } from './text'

const props = defineProps<{
  show: boolean
  row: LogRowView | null
  platformLabel: (value: string) => string
}>()

const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const { t } = useI18n()
const toast = useToast()
const { copy } = useCopy()

const detail = ref<UsageLogDetail | null>(null)
const loading = ref(false)
const error = ref<{ message: string; detail: string } | null>(null)
const ingressText = ref('')
const ingressPage = ref<CaptureBodyPage | null>(null)
const upstreamBodies = reactive<Record<number, { text: string; page: CaptureBodyPage | null; loading: boolean }>>({})
const rawOpen = ref(false)
let controller: AbortController | null = null

function toError(err: unknown): { message: string; detail: string } {
  let text = ''
  if (err instanceof ApiError && err.detail) {
    try {
      text = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail, null, 2)
    } catch {
      text = String(err.detail)
    }
  }
  return { message: errorMessage(err), detail: text }
}

async function load(): Promise<void> {
  const item = props.row
  if (!item) return
  controller?.abort()
  const current = new AbortController()
  controller = current
  detail.value = null
  error.value = null
  ingressText.value = ''
  ingressPage.value = null
  rawOpen.value = false
  for (const key of Object.keys(upstreamBodies)) delete upstreamBodies[Number(key)]
  loading.value = true
  try {
    const payload = await getUsageLog(item.id, current.signal)
    if (current.signal.aborted) return
    detail.value = payload
  } catch (err) {
    if (isAbortError(err)) return
    error.value = toError(err)
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

watch(
  () => [props.show, props.row?.id] as const,
  ([show, id], previous) => {
    if (!show) {
      controller?.abort()
      return
    }
    if (id && (!previous || previous[1] !== id || !previous[0])) void load()
  },
  { immediate: true },
)

/**
 * 第 5 轮：抓包正文分页读取
 *   · 读取期间换了一条日志（detail.id 变了）→ 丢弃旧响应，不把上一条的正文拼到新详情里；
 *   · 「加载更多」连点 → 同一时刻只发一次，避免同一段正文被拼接两遍。
 */
let ingressBusy = false

async function loadIngress(offset: number): Promise<void> {
  const id = detail.value?.id
  if (!id || ingressBusy) return
  ingressBusy = true
  try {
    const page = await getCaptureBody(id, 'ingress', offset)
    if (detail.value?.id !== id) return
    ingressPage.value = page
    ingressText.value = offset === 0 ? page.text : ingressText.value + page.text
  } catch (err) {
    if (detail.value?.id !== id) return
    toast.error(failureText(t('page.logs.action.readIngress'), err))
  } finally {
    ingressBusy = false
  }
}

async function loadUpstream(index: number, offset = 0): Promise<void> {
  const id = detail.value?.id
  if (!id) return
  const current = upstreamBodies[index] || { text: '', page: null, loading: false }
  if (current.loading) return
  upstreamBodies[index] = { ...current, loading: true }
  try {
    const page = await getCaptureBody(id, 'upstream:' + index, offset)
    if (detail.value?.id !== id) return
    upstreamBodies[index] = { text: offset === 0 ? page.text : current.text + page.text, page, loading: false }
  } catch (err) {
    if (detail.value?.id !== id) return
    upstreamBodies[index] = { ...current, loading: false }
    toast.error(failureText(t('page.logs.action.readUpstream'), err))
  }
}

const view = computed<LogRowView | null>(() => (detail.value ? toLogRow(detail.value) : props.row))
const diag = computed(() => diagnosticView(detail.value?.diagnostic))
const failed = computed(() => Boolean(view.value?.isError))
const rawJson = computed(() => (detail.value?.diagnostic ? JSON.stringify(detail.value.diagnostic, null, 2) : ''))
const capture = computed(() => detail.value?.request_capture ?? null)

const title = computed(() => view.value?.model || t('page.logs.detail.title'))
const badge = computed(() => {
  const item = view.value
  if (!item) return null
  return {
    tone: item.resultTone,
    label: item.isError ? errorKindText(t, item.error_kind) || t('page.logs.detail.failed') : t('page.logs.detail.success'),
  }
})
const subtitle = computed(() => {
  const item = view.value
  if (!item) return ''
  return fmtFullTime(item.created_at) + ' · ' + fmtRelative(item.created_at)
})

/** 顶部 4 个关键数。 */
const facts = computed(() => {
  const item = view.value
  if (!item) return []
  return [
    { label: t('page.logs.col.ttft'), value: item.ttfbText, sub: '' },
    { label: t('page.logs.col.elapsed'), value: item.latencyText, sub: item.tpsText !== FMT_EMPTY ? 'TPS ' + item.tpsText : '' },
    {
      label: 'Token',
      value: item.tokensText === FMT_EMPTY ? FMT_EMPTY : fmtInt(item.total_tokens),
      sub:
        item.tokensText === FMT_EMPTY
          ? ''
          : t('page.logs.col.tokenIn', { value: fmtInt(item.prompt_tokens) }) +
            ' · ' +
            t('page.logs.col.tokenOut', { value: fmtInt(item.completion_tokens) }),
    },
    {
      label: t('page.logs.detail.credits'),
      value: item.credits > 0 ? fmtCredit(item.credits) : FMT_EMPTY,
      sub: item.cacheRate === null ? '' : t('page.logs.detail.cacheHitValue', { value: item.cacheRateText }),
    },
  ]
})

/** 诊断概要（全部用人话，字段名不出现）。 */
const diagKv = computed(() => {
  const d = diag.value
  const item = view.value
  const list: Array<{ label: string; value?: string | number | null; slot?: string; mono?: boolean }> = []
  if (item?.isError) {
    list.push({ label: t('page.logs.diag.kind'), slot: 'kind' })
  }
  if (!d) return list
  if (d.gatewayStatus !== null || d.upstreamStatus !== null) list.push({ label: t('page.logs.diag.status'), slot: 'status' })
  if (d.upstreamError && d.upstreamError !== 'success') {
    list.push({ label: t('page.logs.diag.upstreamError'), value: errorKindText(t, d.upstreamError) })
  }
  if (d.networkError) list.push({ label: t('page.logs.diag.networkError'), value: networkErrorText(t, d.networkError) })
  if (d.gatewayError) list.push({ label: t('page.logs.diag.gatewayError'), value: d.gatewayError })
  if (d.attemptCount !== null) list.push({ label: t('page.logs.diag.attempts'), value: fmtInt(d.attemptCount) })
  if (d.lastAccountId) list.push({ label: t('page.logs.diag.lastAccount'), value: '#' + d.lastAccountId, mono: true })
  if (d.recoveries > 0) list.push({ label: t('page.logs.diag.recoveries'), value: fmtInt(d.recoveries) })
  if (d.firstDeltaMs !== null) list.push({ label: t('page.logs.diag.firstDelta'), value: fmtDuration(d.firstDeltaMs) })
  if (d.upstreamRequestIds.length) {
    list.push({ label: t('page.logs.diag.traceIds'), value: d.upstreamRequestIds.join(' · '), mono: true })
  }
  return list
})

const stageMax = computed(() => Math.max(1, ...(diag.value?.stages || []).map((stage) => stage.ms)))
const showDiagnostics = computed(() => failed.value || Boolean(diag.value && (diag.value.attempts.length || diag.value.stages.length)))

const infoKv = computed(() => {
  const item = view.value
  if (!item) return []
  return [
    { label: t('page.logs.detail.requestId'), value: item.id, mono: true, copy: true },
    { label: t('page.logs.detail.platform'), value: item.platform ? props.platformLabel(item.platform) : FMT_EMPTY },
    { label: t('page.logs.detail.account'), value: item.account_name || FMT_EMPTY },
    { label: 'Key', value: item.key_name || FMT_EMPTY },
    {
      label: t('page.logs.detail.model'),
      value: item.model_raw && item.model_raw !== item.model ? item.model + ' · ' + item.model_raw : item.model || FMT_EMPTY,
      mono: true,
    },
    { label: t('page.logs.detail.useCase'), value: item.useCaseLabel },
    { label: t('page.logs.detail.reasoning'), value: item.reasoning_effort || t('page.logs.detail.reasoningDefault') },
    { label: t('page.logs.detail.clientIp'), value: item.client_ip || FMT_EMPTY, mono: true },
    { label: t('page.logs.detail.seq'), value: item.seq ?? FMT_EMPTY },
  ]
})

const ingressCost = computed(() => {
  const c = capture.value
  if (!c) return ''
  return (
    t('page.logs.detail.ingressCost', {
      cached: fmtInt(Number(c.ingress.body_cached_bytes || 0)),
      full: fmtInt(Number(c.ingress.body_bytes || 0)),
    }) + (c.ingress.body_truncated ? t('page.logs.detail.ingressTruncated') : '')
  )
})

const ingressLabel = computed(() =>
  ingressPage.value
    ? t('page.logs.detail.ingressRead', {
        read: fmtInt(ingressPage.value.next_offset),
        total: fmtInt(ingressPage.value.cached_bytes),
      })
    : t('page.logs.detail.ingressCollapsed'),
)

const curlText = computed(() => (detail.value ? buildCurl(detail.value, ingressText.value) : ''))

async function copyCurl(): Promise<void> {
  if (!curlText.value) {
    toast.warning(t('page.logs.toast.noCurl'))
    return
  }
  const ok = await copy(curlText.value)
  if (ok) toast.success(t('page.logs.toast.curlCopied'))
  else toast.error(t('common.copyFailed'))
}

async function downloadCapture(): Promise<void> {
  const id = detail.value?.id
  if (!id) return
  try {
    const filename = await downloadLogCapture(id)
    toast.success(t('page.logs.toast.downloaded', { name: filename }))
  } catch (err) {
    toast.error(failureText(t('page.logs.action.downloadJsonl'), err))
  }
}
</script>

<template>
  <DetailDrawer
    :show="show"
    :title="title"
    :badge="badge"
    :subtitle="subtitle"
    :width="680"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <div class="ld-facts">
      <div v-for="fact in facts" :key="fact.label" class="ld-fact">
        <span class="ld-fact__label">{{ fact.label }}</span>
        <span class="ld-fact__value tnum">{{ fact.value }}</span>
        <span v-if="fact.sub" class="ld-fact__sub tnum">{{ fact.sub }}</span>
      </div>
    </div>

    <div v-if="loading" class="ld-skeleton" aria-busy="true">
      <span v-for="index in 5" :key="index" class="mh-skeleton ld-skeleton__row" />
    </div>
    <ErrorBlock
      v-else-if="error"
      :title="t('page.logs.detail.error', { error: error.message })"
      :detail="error.detail"
      @retry="load"
    />
    <template v-else-if="detail">
      <!-- 诊断：失败时排在最前 -->
      <Section v-if="showDiagnostics" :title="t('page.logs.detail.sectionErrors')" first>
        <KV v-if="diagKv.length" :items="diagKv" label-width="112px">
          <template #kind>
            <span class="ld-kind">
              <Badge tone="danger" dot>{{ errorKindText(t, detail.error_kind) || t('page.logs.detail.failed') }}</Badge>
              <code class="ld-raw">{{ detail.error_kind }}</code>
            </span>
          </template>
          <template #status>
            <span class="ld-codes">
              <span v-if="diag && diag.gatewayStatus !== null" class="ld-code">
                <span class="ld-code__label">{{ t('page.logs.diag.gateway') }}</span>
                <Badge :tone="httpStatusTone(diag.gatewayStatus)">{{ diag.gatewayStatus }}</Badge>
              </span>
              <ChevronRight
                v-if="diag && diag.gatewayStatus !== null && diag.upstreamStatus !== null"
                :size="14"
                class="ld-code__arrow"
              />
              <span v-if="diag && diag.upstreamStatus !== null" class="ld-code">
                <span class="ld-code__label">{{ t('page.logs.diag.upstream') }}</span>
                <Badge :tone="httpStatusTone(diag.upstreamStatus)">{{ diag.upstreamStatus }}</Badge>
              </span>
            </span>
          </template>
        </KV>
        <p v-if="!diag" class="ld-muted">{{ t('page.logs.detail.noDiagnostic') }}</p>

        <template v-if="diag && diag.attempts.length">
          <p class="ld-subhead">{{ t('page.logs.diag.attemptsTitle', { count: diag.attempts.length }) }}</p>
          <ol class="ld-attempts">
            <li v-for="attempt in diag.attempts" :key="attempt.index" class="ld-attempt">
              <span class="ld-attempt__index tnum">{{ attempt.index }}</span>
              <div class="ld-attempt__body">
                <div class="ld-attempt__head">
                  <component :is="attempt.source === 'network' ? Network : UserRound" :size="14" class="ld-attempt__icon" />
                  <span class="ld-attempt__kind">
                    {{
                      attempt.source === 'network'
                        ? networkErrorText(t, attempt.kind)
                        : errorKindText(t, attempt.kind) || attempt.kind
                    }}
                  </span>
                  <Badge v-if="attempt.httpStatus !== null" :tone="httpStatusTone(attempt.httpStatus)">
                    HTTP {{ attempt.httpStatus }}
                  </Badge>
                  <span v-if="attempt.accountId" class="ld-attempt__meta mono">#{{ attempt.accountId }}</span>
                  <span v-if="attempt.code" class="ld-attempt__meta mono">{{ attempt.code }}</span>
                </div>
                <p v-if="attempt.message" class="ld-attempt__msg">{{ attempt.message }}</p>
                <p v-if="attempt.phase || attempt.until || attempt.model" class="ld-attempt__foot">
                  <template v-if="attempt.model">{{ attempt.model }}</template>
                  <template v-if="attempt.phase">
                    {{ attempt.model ? ' · ' : '' }}{{ t('page.logs.diag.phase', { phase: attempt.phase }) }}
                  </template>
                  <template v-if="attempt.until">
                    {{ attempt.model || attempt.phase ? ' · ' : '' }}{{ t('page.logs.diag.until', { time: fmtFullTime(attempt.until) }) }}
                  </template>
                </p>
              </div>
            </li>
          </ol>
        </template>

        <template v-if="diag && diag.stages.length">
          <p class="ld-subhead">{{ t('page.logs.diag.stagesTitle') }}</p>
          <div class="ld-stages">
            <div v-for="(stage, index) in diag.stages" :key="stage.name + index" class="ld-stage">
              <TitleTip :content="stage.name"><span class="ld-stage__name mono">{{ stage.name }}</span></TitleTip>
              <span class="ld-stage__track"><i :style="{ width: Math.max(2, (stage.ms / stageMax) * 100) + '%' }" /></span>
              <span class="ld-stage__ms tnum">{{ fmtDuration(stage.ms) }}</span>
            </div>
          </div>
        </template>

        <p v-if="diag && diag.truncated" class="ld-muted">{{ t('page.logs.diag.truncated') }}</p>

        <div v-if="rawJson" class="ld-raw-block">
          <button type="button" class="ld-raw-toggle" :aria-expanded="rawOpen" @click="rawOpen = !rawOpen">
            <ChevronRight :size="14" :class="{ 'is-open': rawOpen }" />
            {{ t('page.logs.diag.raw') }}
          </button>
          <CodeBlock v-if="rawOpen" :code="rawJson" language="json" max-height="280px" />
        </div>
      </Section>

      <Section :title="t('page.logs.detail.sectionSummary')" :first="!showDiagnostics">
        <KV :items="infoKv" label-width="96px" />
      </Section>

      <Section :title="t('page.logs.detail.sectionBody')">
        <template v-if="capture">
          <p class="ld-muted">
            {{ t('page.logs.detail.capturedAt', { time: fmtFullTime(capture.captured_at), cost: ingressCost }) }}
          </p>
          <p class="ld-subhead">{{ t('page.logs.detail.ingressHeaders') }}</p>
          <CodeBlock :code="JSON.stringify(capture.ingress.headers, null, 2)" language="json" max-height="160px" />
          <p class="ld-subhead">{{ t('page.logs.detail.ingressLine', { value: ingressLabel }) }}</p>
          <CodeBlock v-if="ingressText" :code="ingressText" language="http" max-height="320px" />
          <div class="ld-actions">
            <Btn v-if="!ingressText" variant="quiet" size="sm" @click="loadIngress(0)">
              {{ t('page.logs.detail.expandIngress') }}
            </Btn>
            <Btn v-if="ingressPage?.has_more" variant="quiet" size="sm" @click="loadIngress(ingressPage?.next_offset || 0)">
              {{ t('page.logs.detail.continueReading') }}
            </Btn>
            <Btn variant="quiet" size="sm" :icon="Copy" @click="copyCurl">{{ t('page.logs.detail.copyCurl') }}</Btn>
          </div>

          <p class="ld-subhead">{{ t('page.logs.detail.upstreamTitle', { count: capture.upstream.length }) }}</p>
          <div v-for="(attempt, index) in capture.upstream" :key="attempt.attempt" class="ld-upstream">
            <div class="ld-upstream__head">
              <span class="ld-attempt__index tnum">{{ attempt.attempt }}</span>
              <TitleTip :content="attempt.url"><span class="mono ld-upstream__url">{{ attempt.method }} {{ attempt.url }}</span></TitleTip>
              <Badge v-if="attempt.http_status" :tone="httpStatusTone(attempt.http_status)">{{ attempt.http_status }}</Badge>
              <Badge v-else tone="neutral">{{ t('page.logs.detail.noStatus') }}</Badge>
            </div>
            <CodeBlock v-if="upstreamBodies[index]?.text" :code="upstreamBodies[index].text" language="http" max-height="240px" />
            <div class="ld-actions">
              <Btn
                v-if="!upstreamBodies[index]?.text"
                variant="quiet"
                size="sm"
                :loading="upstreamBodies[index]?.loading"
                @click="loadUpstream(index, 0)"
              >
                {{ t('page.logs.detail.expandUpstream') }}
              </Btn>
              <Btn
                v-if="upstreamBodies[index]?.page?.has_more"
                variant="quiet"
                size="sm"
                :loading="upstreamBodies[index]?.loading"
                @click="loadUpstream(index, upstreamBodies[index]?.page?.next_offset || 0)"
              >
                {{ t('page.logs.detail.continueReading') }}
              </Btn>
            </div>
          </div>
          <p v-if="capture.upstream_omitted" class="ld-muted">
            {{ t('page.logs.detail.upstreamOmitted', { count: capture.upstream_omitted }) }}
          </p>
        </template>
        <p v-else class="ld-muted">{{ t('page.logs.detail.noCapture') }}</p>
      </Section>
    </template>

    <template #footer>
      <Btn variant="quiet" :icon="Download" :disabled="!capture" @click="downloadCapture">
        {{ t('page.logs.action.downloadJsonl') }}
      </Btn>
      <Btn variant="default" @click="emit('update:show', false)">{{ t('common.close') }}</Btn>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.ld-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--border-soft);
  border-radius: var(--r-card);
  background: var(--surface-2);
  overflow: hidden;
}

.ld-fact {
  display: grid;
  gap: 2px;
  padding: var(--sp-3);
  min-width: 0;
  border-left: 1px solid var(--border-soft);
}

.ld-fact:first-child {
  border-left: 0;
}

.ld-fact__label,
.ld-fact__sub {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ld-fact__value {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.ld-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.ld-skeleton__row {
  height: 20px;
}

.ld-muted {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ld-subhead {
  margin: var(--sp-3) 0 var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  color: var(--fg-2);
}

.ld-kind,
.ld-codes,
.ld-code {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.ld-raw {
  font-family: var(--font-mono);
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
}

.ld-code__label {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.ld-code__arrow {
  color: var(--fg-faint);
}

.ld-attempts {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ld-attempt {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: var(--sp-2);
  padding: var(--sp-2) 0;
  border-top: 1px solid var(--border-soft);
}

.ld-attempt:first-child {
  border-top: 0;
}

.ld-attempt__index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  font-size: var(--fs-tiny);
  color: var(--fg-2);
}

.ld-attempt__body {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.ld-attempt__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-height: 22px;
}

.ld-attempt__icon {
  color: var(--fg-muted);
}

.ld-attempt__kind {
  font-weight: 500;
  color: var(--fg);
}

.ld-attempt__meta {
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
}

.ld-attempt__msg {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.ld-attempt__foot {
  margin: 0;
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
}

.ld-stages {
  display: grid;
  gap: var(--sp-1);
}

.ld-stage {
  display: grid;
  grid-template-columns: minmax(0, 200px) minmax(0, 1fr) 64px;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
}

.ld-stage__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.ld-stage__track {
  height: 6px;
  border-radius: 3px;
  background: var(--surface-2);
  overflow: hidden;
}

.ld-stage__track i {
  display: block;
  height: 100%;
  background: var(--fg-faint);
  border-radius: 3px;
}

.ld-stage__ms {
  text-align: right;
  color: var(--fg-2);
}

.ld-raw-block {
  display: grid;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}

.ld-raw-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  justify-self: start;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--fg-muted);
  font-size: var(--fs-small);
  cursor: pointer;
}

.ld-raw-toggle svg {
  transition: transform var(--t-fast) var(--ease);
}

.ld-raw-toggle svg.is-open {
  transform: rotate(90deg);
}

.ld-actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  margin-top: var(--sp-1);
}

.ld-upstream {
  display: grid;
  gap: var(--sp-1);
  padding-top: var(--sp-2);
  margin-top: var(--sp-2);
  border-top: 1px solid var(--border-soft);
}

.ld-upstream__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.ld-upstream__url {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-small);
  color: var(--fg-2);
}

@media (max-width: 640px) {
  .ld-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ld-fact:nth-child(3) {
    border-left: 0;
  }

  .ld-fact:nth-child(n + 3) {
    border-top: 1px solid var(--border-soft);
  }

  .ld-stage {
    grid-template-columns: minmax(0, 1fr) 64px;
  }

  .ld-stage__track {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ld-raw-toggle svg {
    transition: none;
  }
}
</style>
