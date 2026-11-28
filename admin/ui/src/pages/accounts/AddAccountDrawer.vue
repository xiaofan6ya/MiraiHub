<script setup lang="ts">
/**
 * 账号池 · 添加账号抽屉（S26 的全部添加方式，从 AccountsPage 拆出）。
 *
 * 一个抽屉按 kind 切换内容：手动添加 / 导入（粘贴 → 预览 → 勾选）/ 扫描本机 / 手机号登录 / 浏览器授权。
 * 方式是否可用来自平台能力声明（api/account-add.ts:addMethodsFor），页面不写 if(platform==)。
 *
 * 事件：
 *   · added —— 账号已入池（页面刷新列表）；
 *   · job   —— 导入是后台任务，把 job 快照交给页面的进度条 / JobProgress 轮询。
 * 关闭或卸载时停掉倒计时与授权轮询。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NCheckbox, NInput, NSelect } from 'naive-ui'
import { Btn, ErrorBlock, FormDrawer, FormField } from '@/components'
import { useToast } from '@/composables'
import { copyText } from '@/composables/useCopy'
import { failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import type { AccountJobSnapshot } from '@/api/accounts'
import {
  addAccountManually,
  addResultText,
  authStateOf,
  authStepIndex,
  beginBrowserAuth,
  countryCodes,
  defaultVerify,
  flowOf,
  importAccountsAsync,
  importLocalAccounts,
  importLocalText,
  importModeOf,
  importPlatformLocal,
  importStorageOf,
  manualHint,
  maskPhone,
  parseImportText,
  platformScanRows,
  platformScanSummary,
  pollBrowserAuth,
  preparePhoneFlow,
  scanLocalAccounts,
  scanPlatformLocal,
  scanPreviewRows,
  scanSummary,
  scanTargetOf,
  storageOf,
  submitAuthFallback,
  sendPhoneCode,
  validatePhoneForm,
  verifyPhoneCode,
  type BrowserAuthView,
  type PhoneForm,
  type PlatformAddSpec,
  type PlatformScanPayload,
  type ScanLocalPayload,
  type ScanPreviewRow,
} from '@/api/account-add'
import type { AddDrawerKind } from './types'

const props = defineProps<{
  show: boolean
  kind: AddDrawerKind
  spec: PlatformAddSpec | null
  platformName: string
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'added'): void
  (event: 'job', snapshot: AccountJobSnapshot): void
}>()

const { t } = useI18n()
const toast = useToast()

const countryOptions = computed(() => countryCodes(t))
const authSteps = computed(() => [
  t('page.accounts.authStepOpen'),
  t('page.accounts.authStepWait'),
  t('page.accounts.authStepPool'),
])

const TITLE_KEY = {
  manual: 'page.accounts.addModeManual',
  import: 'page.accounts.importAccounts',
  scan: 'page.accounts.scan',
  phone: 'add.method.phone',
  authorize: 'add.method.authorize',
} as const

const title = computed(() =>
  t('page.accounts.addJoin', { title: t(TITLE_KEY[props.kind]), platform: props.platformName }),
)

function close(): void {
  emit('update:show', false)
}

/* ---------------- 手动添加 ---------------- */
const manualName = ref('')
const manualJson = ref('')
const manualBusy = ref(false)

async function submitManual(): Promise<void> {
  const text = manualJson.value.trim()
  if (!text) {
    toast.error(t('page.accounts.manualJsonRequired'))
    return
  }
  manualBusy.value = true
  try {
    const result = await addAccountManually({ name: manualName.value.trim(), auth_json: text })
    toast.success(addResultText(result, props.platformName, t))
    close()
    emit('added')
  } catch (error) {
    toast.error(failureText(t('page.accounts.addSubmit'), error))
  } finally {
    manualBusy.value = false
  }
}

/* ---------------- 导入 ---------------- */
const importText = ref('')
const importVerify = ref(true)
const importBusy = ref(false)
const importSelected = ref<number[]>([])
const importPreview = computed(() => parseImportText(importText.value, t))
const importRows = computed(() => importPreview.value.rows)
const importMode = computed(() => importModeOf(props.spec))

watch(importRows, (rows) => {
  importSelected.value = rows.filter((row) => row.selected).map((row) => row.index)
})

function toggleImportRow(index: number, checked: boolean): void {
  const next = new Set(importSelected.value)
  if (checked) next.add(index)
  else next.delete(index)
  importSelected.value = [...next]
}

async function submitImport(): Promise<void> {
  const rows = importRows.value.filter((row) => row.valid && importSelected.value.includes(row.index))
  if (!rows.length) {
    toast.error(t('page.accounts.importNone'))
    return
  }
  importBusy.value = true
  try {
    const snapshot = await importAccountsAsync({
      storage: importStorageOf(props.spec),
      items: importMode.value === 'items' ? rows.map((row) => ({ name: row.name, auth_json: row.authJson })) : [],
      text: importMode.value === 'text' ? importText.value : '',
      verify: importVerify.value,
    })
    close()
    emit('job', snapshot as unknown as AccountJobSnapshot)
  } catch (error) {
    toast.error(failureText(t('page.accounts.importFailed'), error))
  } finally {
    importBusy.value = false
  }
}

/* ---------------- 扫描本机 ---------------- */
const scanLoading = ref(false)
const scanPayload = ref<ScanLocalPayload | null>(null)
const scanPlatformPayload = ref<PlatformScanPayload | null>(null)
const scanRows = ref<ScanPreviewRow[]>([])
const scanSelected = ref<string[]>([])
const scanError = ref('')
const scanBusy = ref(false)

/**
 * 「扫描本机」打哪一组接口（issue #127）。
 *
 * 默认平台走 `/api/accounts/scan-local`（通用接口，只认 WorkBuddy 凭证目录）；
 * 其余平台走各自的 `/api/<storage>/scan-local`，否则 CodeArts 抽屉里会列出
 * 并「导入」WorkBuddy 的号 —— 用户看到的是「扫描到了但导不进这个平台」。
 */
const scanTarget = computed(() => scanTargetOf(props.spec))

async function loadScan(): Promise<void> {
  scanLoading.value = true
  scanError.value = ''
  try {
    if (scanTarget.value.generic) {
      const payload = await scanLocalAccounts()
      scanPayload.value = payload
      scanPlatformPayload.value = null
      scanRows.value = scanPreviewRows(payload, t)
    } else {
      const payload = await scanPlatformLocal(scanTarget.value.prefix)
      scanPayload.value = null
      scanPlatformPayload.value = payload
      scanRows.value = platformScanRows(payload, t)
    }
    scanSelected.value = scanRows.value.filter((row) => row.selected).map((row) => row.file)
  } catch (error) {
    scanError.value = failureText(t('page.accounts.scanScanFailed'), error)
    toast.error(scanError.value)
  } finally {
    scanLoading.value = false
  }
}

const scanSummaryText = computed(() =>
  scanTarget.value.generic
    ? scanSummary(scanPayload.value, scanRows.value, t)
    : platformScanSummary(scanPlatformPayload.value, scanRows.value, t),
)

function toggleScanRow(file: string, checked: boolean): void {
  const next = new Set(scanSelected.value)
  if (checked) next.add(file)
  else next.delete(file)
  scanSelected.value = [...next]
}

async function submitScan(): Promise<void> {
  if (!scanSelected.value.length) {
    toast.error(t('page.accounts.scanSelectFirst'))
    return
  }
  scanBusy.value = true
  try {
    const result = scanTarget.value.generic
      ? await importLocalAccounts(scanSelected.value)
      : await importPlatformLocal(scanTarget.value.prefix, scanSelected.value)
    toast.success(t('page.accounts.scanImported', { summary: importLocalText(result, t) }))
    close()
    emit('added')
  } catch (error) {
    toast.error(failureText(t('page.accounts.scanFailed'), error))
  } finally {
    scanBusy.value = false
  }
}

/* ---------------- 手机号登录 ---------------- */
const phoneCountry = ref('+86')
const phoneNumber = ref('')
const phoneCode = ref('')
const phonePassword = ref('')
const phoneName = ref('')
const phoneSession = ref<Record<string, unknown>>({})
const phoneSending = ref(false)
const phoneBusy = ref(false)
const phoneCountdown = ref(0)
const phoneErrors = ref<Record<string, string>>({})
const phoneNeedsPassword = ref(false)
let countdownTimer: ReturnType<typeof setInterval> | null = null

function stopCountdown(): void {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
}

function startCountdown(): void {
  stopCountdown()
  phoneCountdown.value = 60
  countdownTimer = setInterval(() => {
    if (phoneCountdown.value <= 1) {
      phoneCountdown.value = 0
      stopCountdown()
      return
    }
    phoneCountdown.value -= 1
  }, 1000)
}

function resetPhone(): void {
  stopCountdown()
  phoneNumber.value = ''
  phoneCode.value = ''
  phonePassword.value = ''
  phoneName.value = ''
  phoneSession.value = {}
  phoneErrors.value = {}
  phoneCountdown.value = 0
  phoneNeedsPassword.value = false
}

function phoneForm(): PhoneForm {
  return {
    country: phoneCountry.value,
    phone: phoneNumber.value,
    code: phoneCode.value,
    password: phonePassword.value,
    name: phoneName.value,
  }
}

async function onSendCode(): Promise<void> {
  const form = phoneForm()
  const errors = validatePhoneForm({ ...form, code: 'pending' }, false, t)
  delete errors.code
  phoneErrors.value = errors
  if (Object.keys(errors).length) return
  phoneSending.value = true
  try {
    if (!Object.keys(phoneSession.value).length) {
      phoneSession.value = await preparePhoneFlow(props.spec, form)
    }
    const result = await sendPhoneCode(props.spec, form, phoneSession.value, t)
    phoneSession.value = { ...phoneSession.value, ...result }
    startCountdown()
    toast.success(t('page.accounts.phoneSent', { phone: maskPhone(form.phone) }))
  } catch (error) {
    toast.error(failureText(t('page.accounts.phoneSendFailed'), error))
  } finally {
    phoneSending.value = false
  }
}

async function onVerifyPhone(): Promise<void> {
  const form = phoneForm()
  const errors = validatePhoneForm(form, phoneNeedsPassword.value, t)
  phoneErrors.value = errors
  if (Object.keys(errors).length) return
  phoneBusy.value = true
  try {
    const result = await verifyPhoneCode(props.spec, form, phoneSession.value, t)
    if (result.need_password || result.needs_password) {
      phoneNeedsPassword.value = true
      toast.info(t('page.accounts.phoneNeedPassword'))
      return
    }
    const name = typeof result.name === 'string' ? result.name : ''
    toast.success(name ? t('page.accounts.phoneAddedNamed', { name }) : t('page.accounts.phoneAdded'))
    close()
    resetPhone()
    emit('added')
  } catch (error) {
    toast.error(failureText(t('page.accounts.phoneFailed'), error))
  } finally {
    phoneBusy.value = false
  }
}

/* ---------------- 浏览器授权 ---------------- */
const authView = ref<BrowserAuthView>({ state: 'idle', url: '', accountId: 0, message: '' })
const authSession = ref<Record<string, unknown>>({})
const authBusy = ref(false)
const fallbackValue = ref('')
const fallbackUsername = ref('')
const fallbackPassword = ref('')
const authFallback = computed(() => flowOf(props.spec)?.fallback)
const fallbackReady = computed(() => authFallback.value?.kind === 'password'
  ? Boolean(fallbackUsername.value.trim() && fallbackPassword.value)
  : Boolean(fallbackValue.value.trim()))
const authTicks = ref(0)
let authTimer: ReturnType<typeof setInterval> | null = null
let authGeneration = 0
let authPolling = false

function stopAuthPoll(): void {
  authGeneration += 1
  authPolling = false
  if (authTimer) {
    clearInterval(authTimer)
    authTimer = null
  }
}

function resetAuth(): void {
  stopAuthPoll()
  authTicks.value = 0
  authView.value = { state: 'idle', url: '', accountId: 0, message: '' }
  authSession.value = {}
  fallbackValue.value = ''
  fallbackUsername.value = ''
  fallbackPassword.value = ''
}

async function onSubmitAuthFallback(): Promise<void> {
  if (authBusy.value || !fallbackReady.value) return
  const generation = authGeneration
  authBusy.value = true
  try {
    const payload = await submitAuthFallback(props.spec, {
      value: fallbackValue.value, username: fallbackUsername.value, password: fallbackPassword.value,
    }, authSession.value)
    if (generation !== authGeneration || !props.show) return
    const view = authStateOf(payload)
    if (view.state === 'authorized') {
      stopAuthPoll()
      toast.success(t('page.accounts.authSuccess'))
      close()
      resetAuth()
      emit('added')
    } else {
      authSession.value = { ...authSession.value, ...payload }
      authView.value = authStateOf(authSession.value)
      if (view.state === 'failed') stopAuthPoll()
    }
  } catch (error) {
    if (generation === authGeneration && props.show) toast.error(failureText(t('page.accounts.authBeginFailed'), error))
  } finally {
    authBusy.value = false
  }
}

async function onBeginAuth(): Promise<void> {
  stopAuthPoll()
  const generation = authGeneration
  authBusy.value = true
  try {
    const payload = await beginBrowserAuth(props.spec, t)
    if (generation !== authGeneration || !props.show) return
    if (storageOf(props.spec) === 'codar' && payload.auth_id) {
      payload.qr_image = '/api/codar/auth/qr.png?auth_id=' + encodeURIComponent(String(payload.auth_id))
    }
    authSession.value = { ...payload }
    const view = authStateOf(payload)
    authView.value = view
    if (view.url && !view.qrImage) {
      try {
        window.open(view.url, '_blank', 'noopener,noreferrer')
      } catch {
        /* 弹窗被拦就只显示链接 */
      }
    }
    startAuthPoll()
  } catch (error) {
    toast.error(failureText(t('page.accounts.authBeginFailed'), error))
  } finally {
    authBusy.value = false
  }
}

async function pollAuthOnce(): Promise<void> {
  if (authPolling || !props.show || props.kind !== 'authorize') return
  const generation = authGeneration
  authPolling = true
  try {
    const payload = await pollBrowserAuth(props.spec, authSession.value, t)
    if (generation !== authGeneration || !props.show) return
    authTicks.value = 0
    authSession.value = { ...authSession.value, ...payload }
    const view = authStateOf(authSession.value)
    authView.value = view
    if (view.state === 'authorized') {
      stopAuthPoll()
      toast.success(t('page.accounts.authSuccess'))
      close()
      resetAuth()
      emit('added')
    } else if (view.state === 'failed') {
      stopAuthPoll()
      toast.error(t('page.accounts.authFailed', { reason: view.message || t('page.accounts.authUnknownReason') }))
    }
  } catch (error) {
    if (generation !== authGeneration || !props.show) return
    authTicks.value += 1
    if (authTicks.value >= 3) {
      stopAuthPoll()
      toast.error(failureText(t('page.accounts.authPollFailed'), error))
    }
  } finally {
    if (generation === authGeneration) authPolling = false
  }
}

function startAuthPoll(): void {
  stopAuthPoll()
  authTicks.value = 0
  const deadline = Date.now() + Math.max(30, Number(authSession.value.expires_in) || 300) * 1000
  const interval = Math.max(2, Number(authSession.value.interval) || 2) * 1000
  authTimer = setInterval(() => {
    if (Date.now() >= deadline) {
      stopAuthPoll()
      authView.value = { ...authView.value, state: 'failed' }
      return
    }
    void pollAuthOnce()
  }, interval)
}

async function copyAuthUrl(): Promise<void> {
  if (!authView.value.url) return
  const ok = await copyText(authView.value.url)
  if (ok) toast.success(t('page.accounts.authCopied'))
  else toast.error(t('common.copyFailed'))
}

const authStep = computed(() => authStepIndex(authView.value))

const authStateText = computed(() => {
  const state = authView.value.state
  if (state === 'authorized') return t('page.accounts.authAuthorized')
  if (state === 'failed') return t('page.accounts.authFailedState')
  if (state === 'idle') return t('page.accounts.authIdle')
  return t('page.accounts.authWaiting')
})

/* ---------------- 打开 / 切换 / 提交 ---------------- */

function prepare(kind: AddDrawerKind): void {
  if (kind === 'scan') void loadScan()
  if (kind === 'phone') resetPhone()
  if (kind === 'authorize') resetAuth()
  if (kind === 'import') {
    importText.value = ''
    importSelected.value = []
    importVerify.value = defaultVerify(props.spec)
  }
  if (kind === 'manual') {
    manualName.value = ''
    manualJson.value = ''
  }
}

watch(
  () => [props.show, props.kind] as const,
  ([show, kind], previous) => {
    const [wasShown, previousKind] = previous || [false, undefined]
    if (!show) {
      // 关闭：停掉倒计时与授权轮询（离开流程就不要再打上游）。
      stopCountdown()
      stopAuthPoll()
      fallbackValue.value = ''
      fallbackUsername.value = ''
      fallbackPassword.value = ''
      return
    }
    if (!wasShown || previousKind !== kind) prepare(kind)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  stopAuthPoll()
  stopCountdown()
})

const submitText = computed(() => {
  if (props.kind === 'import') return t('page.accounts.addSubmitImport', { count: importSelected.value.length })
  if (props.kind === 'scan') return t('page.accounts.addSubmitScan', { count: scanSelected.value.length })
  if (props.kind === 'phone') return t('page.accounts.addSubmitPhone')
  if (props.kind === 'authorize') {
    return authView.value.state === 'idle' ? t('page.accounts.addSubmitAuthorizeStart') : t('page.accounts.addSubmitAuthorize')
  }
  return t('page.accounts.addSubmit')
})

const submitLoading = computed(
  () => manualBusy.value || importBusy.value || scanBusy.value || phoneBusy.value || authBusy.value,
)

function submit(): void {
  if (props.kind === 'manual') return void submitManual()
  if (props.kind === 'import') return void submitImport()
  if (props.kind === 'scan') return void submitScan()
  if (props.kind === 'phone') return void onVerifyPhone()
  if (props.kind === 'authorize') return void onBeginAuth()
}
</script>

<template>
  <FormDrawer
    :show="show"
    :title="title"
    size="lg"
    :submit-text="submitText"
    :submit-loading="submitLoading"
    @update:show="(value: boolean) => emit('update:show', value)"
    @submit="submit"
  >
    <template v-if="kind === 'manual'">
      <FormField :label="t('page.accounts.manualName')" :hint="t('page.accounts.manualNameHint')">
        <n-input
          v-model:value="manualName"
          :placeholder="t('page.accounts.manualNamePlaceholder')"
          maxlength="120"
          clearable
        />
      </FormField>
      <FormField :label="t('page.accounts.manualJson')" required :hint="manualHint(spec, t)">
        <n-input
          v-model:value="manualJson"
          type="textarea"
          :rows="10"
          class="acc-json"
          :placeholder="t('page.accounts.manualJsonPlaceholder')"
        />
      </FormField>
    </template>

    <template v-else-if="kind === 'import'">
      <FormField
        :label="t('page.accounts.importPaste')"
        required
        :hint="importMode === 'items' ? t('page.accounts.importHintItems') : t('page.accounts.importHintText')"
      >
        <n-input
          v-model:value="importText"
          type="textarea"
          :rows="8"
          class="acc-json"
          placeholder='{"uid":"...","auth":{...}}'
        />
      </FormField>
      <p class="acc-hint">
        {{ t('page.accounts.importParsed', { valid: importPreview.valid }) }}<template v-if="importPreview.invalid">{{
          t('page.accounts.importParsedInvalid', { invalid: importPreview.invalid })
        }}</template>
      </p>
      <ul v-if="importRows.length" class="acc-scan">
        <li v-for="row in importRows" :key="row.index" class="acc-scan__row" :class="{ 'is-invalid': !row.valid }">
          <n-checkbox
            :checked="importSelected.includes(row.index)"
            :disabled="!row.valid"
            @update:checked="(checked: boolean) => toggleImportRow(row.index, checked)"
          />
          <span class="acc-scan__name">
            {{ row.name || row.uid || t('page.accounts.importRowName', { index: row.index }) }}
          </span>
          <span class="acc-muted">
            <template v-if="row.valid && row.uid">uid <span class="mono">{{ row.uid }}</span></template>
            <template v-else>{{ row.valid ? t('page.accounts.importStructureOk') : row.reason }}</template>
          </span>
        </li>
      </ul>
      <n-checkbox v-model:checked="importVerify" class="acc-verify">
        {{ t('page.accounts.importVerify') }}
      </n-checkbox>
    </template>

    <template v-else-if="kind === 'scan'">
      <div class="acc-scan__bar">
        <p class="acc-hint acc-hint--flat">
          {{ scanLoading ? t('page.accounts.scanLoading') : scanSummaryText }}
        </p>
        <Btn variant="quiet" size="sm" :loading="scanLoading" @click="loadScan()">
          {{ t('page.accounts.scanRescan') }}
        </Btn>
      </div>
      <ErrorBlock v-if="scanError" :title="scanError" :retry="false" compact />
      <ul v-if="scanRows.length" class="acc-scan">
        <li v-for="row in scanRows" :key="row.file" class="acc-scan__row">
          <n-checkbox
            :checked="scanSelected.includes(row.file)"
            :disabled="Boolean(row.error) || row.inPool"
            @update:checked="(checked: boolean) => toggleScanRow(row.file, checked)"
          />
          <span class="acc-scan__name">{{ row.name || row.uid || row.file }}</span>
          <span class="acc-muted">
            {{
              row.error
                ? row.error
                : row.inPool
                  ? t('page.accounts.scanInPool')
                  : row.active
                    ? t('page.accounts.scanActive')
                    : ''
            }}<template v-if="row.expires">{{ t('page.accounts.scanExpires', { time: row.expires }) }}</template>
          </span>
        </li>
      </ul>
      <p class="acc-hint">{{ t('page.accounts.scanNotice') }}</p>
    </template>

    <template v-else-if="kind === 'phone'">
      <FormField :label="t('page.accounts.phoneNumber')" :error="phoneErrors.phone">
        <div class="acc-phone">
          <n-select v-model:value="phoneCountry" :options="countryOptions" class="acc-phone__country" />
          <n-input
            v-model:value="phoneNumber"
            :placeholder="t('page.accounts.phonePlaceholderNumber')"
            maxlength="20"
          />
        </div>
      </FormField>
      <FormField :label="t('page.accounts.phoneCode')" :error="phoneErrors.code">
        <div class="acc-phone">
          <n-input
            v-model:value="phoneCode"
            :placeholder="t('page.accounts.phonePlaceholderCode')"
            maxlength="8"
          />
          <Btn
            variant="default"
            :loading="phoneSending"
            :disabled="phoneCountdown > 0 || phoneSending"
            @click="onSendCode"
          >
            {{
              phoneCountdown > 0
                ? t('page.accounts.phoneResend', { seconds: phoneCountdown })
                : t('page.accounts.phoneGetCode')
            }}
          </Btn>
        </div>
      </FormField>
      <FormField
        v-if="phoneNeedsPassword"
        :label="t('page.accounts.phonePasswordLabel')"
        required
        :error="phoneErrors.password"
        :hint="t('page.accounts.phonePasswordHint')"
      >
        <n-input v-model:value="phonePassword" type="password" show-password-on="click" />
      </FormField>
      <p class="acc-hint">
        {{
          t('page.accounts.phoneNotice', {
            phone: maskPhone(phoneNumber) || t('page.accounts.phonePlaceholderNumber'),
          })
        }}
      </p>
    </template>

    <template v-else>
      <ol class="acc-steps">
        <li
          v-for="(step, index) in authSteps"
          :key="step"
          :class="{ 'is-done': index < authStep, 'is-active': index === authStep }"
        >
          <span class="acc-steps__index">{{ index + 1 }}</span>
          <span>{{ step }}</span>
        </li>
      </ol>
      <p class="acc-hint">
        {{ t('page.accounts.authStatus', { state: authStateText }) }}<template v-if="authView.message">
          · {{ authView.message }}</template>
      </p>
      <img v-if="authView.qrImage" class="acc-auth-qr" :src="authView.qrImage" :alt="t('page.accounts.authQr')" width="200" height="200" />
      <p v-if="authView.userCode" class="acc-hint">{{ t('page.accounts.authUserCode') }} <code class="mono">{{ authView.userCode }}</code></p>
      <div v-if="authView.url" class="acc-auth-url">
        <code class="mono">{{ authView.url }}</code>
        <Btn variant="quiet" size="sm" @click="copyAuthUrl()">{{ t('page.accounts.authCopy') }}</Btn>
        <Btn variant="quiet" size="sm" @click="pollAuthOnce()">{{ t('page.accounts.authCheckNow') }}</Btn>
      </div>
      <p class="acc-hint">{{ t('page.accounts.authNotice') }}</p>
      <section v-if="authFallback && (authFallback.kind !== 'callback' || authSession.nonce)" class="acc-auth-fallback">
        <p class="acc-hint">{{ t(`page.accounts.authFallback.${authFallback.kind}`) }}</p>
        <template v-if="authFallback.kind === 'password'">
          <FormField :label="t('page.accounts.authUsername')" required><n-input v-model:value="fallbackUsername" autocomplete="username" /></FormField>
          <FormField :label="t('page.accounts.authPassword')" required><n-input v-model:value="fallbackPassword" type="password" show-password-on="click" autocomplete="current-password" /></FormField>
        </template>
        <FormField v-else :label="t(`page.accounts.authFallbackLabel.${authFallback.kind}`)" required>
          <n-input v-model:value="fallbackValue" :type="authFallback.kind === 'cookie' ? 'password' : 'textarea'" show-password-on="click" autocomplete="off" />
        </FormField>
        <Btn :loading="authBusy" :disabled="!fallbackReady" @click="onSubmitAuthFallback">{{ t('page.accounts.authFallbackSubmit') }}</Btn>
      </section>
    </template>
  </FormDrawer>
</template>

<style scoped>
.acc-auth-fallback {
  display: grid;
  gap: var(--sp-3);
  margin-top: var(--sp-4);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}
.acc-auth-qr {
  display: block;
  width: 200px;
  height: 200px;
  max-width: 100%;
  object-fit: contain;
  margin: var(--sp-3) auto;
}
.acc-hint {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.acc-hint--flat {
  margin: 0;
}

.acc-muted {
  color: var(--fg-muted);
  font-size: var(--fs-small);
}

.acc-json :deep(textarea) {
  font-family: var(--font-mono);
  font-size: var(--fs-small);
}

.acc-verify {
  margin-top: var(--sp-3);
}

.acc-scan__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
}

.acc-scan {
  list-style: none;
  margin: var(--sp-2) 0 0;
  padding: 0;
  display: grid;
  max-height: 300px;
  overflow: auto;
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
}

.acc-scan__row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-height: var(--row-h-compact, 36px);
  padding: 0 var(--sp-3);
  font-size: var(--fs-small);
  border-bottom: 1px solid var(--border-soft);
}

.acc-scan__row:last-child {
  border-bottom: 0;
}

.acc-scan__row.is-invalid .acc-muted {
  color: var(--danger);
}

.acc-scan__name {
  color: var(--fg);
  min-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-phone {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
}

.acc-phone__country {
  width: 168px;
  flex: none;
}

.acc-steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--sp-2);
}

.acc-steps li {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.acc-steps__index {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 1px solid var(--border-strong);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
  flex: none;
}

.acc-steps li.is-active {
  color: var(--fg);
}

.acc-steps li.is-active .acc-steps__index {
  border-color: var(--brand);
  color: var(--brand);
}

.acc-steps li.is-done {
  color: var(--ok);
}

.acc-steps li.is-done .acc-steps__index {
  border-color: var(--ok);
  color: var(--ok);
}

.acc-auth-url {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  margin-top: var(--sp-2);
  word-break: break-all;
  font-size: var(--fs-small);
}
</style>
