<script setup lang="ts">
/**
 * S29 登录页（独立入口 admin/ui/login.html → dist/login.html）
 *
 * 设计稿：docs/ui-v2/06-pages-d.md P26
 * 后端：admin/server.py:login（POST /api/login，form-urlencoded，HttpOnly Cookie）
 *
 * 与旧版（admin/static/login.html）的字段与请求完全一致：
 *   字段 username + password；请求体 URLSearchParams；带 X-Browser-Fingerprint；
 *   失败提示取后端 detail（401 = 用户名或密码错误，429 = 登录过于频繁 + 剩余秒数）。
 *
 * 两个刻意的差异：
 *   1. 成功后跳 /admin/v2/（新版控制台），而不是旧版的 /admin；
 *   2. 用 @/api/http 发请求，但**先注册一个空的 401 处理器** —— http 层默认
 *      401 会 location.replace('/admin')，登录页里那等于无限刷新。
 */
import { computed, onMounted, ref } from 'vue'
import { http, ApiError } from '@/api/http'
import { setUnauthorizedHandler } from '@/api/auth'

const username = ref('')
const password = ref('')
const submitting = ref(false)
const errorText = ref('')
const version = ref('')
/** 运行时地址：写成字面量会被 Vite 当成待打包的静态资源。 */
const iconUrl = '/api/app/icon'
const logoFailed = ref(false)
const usernameEl = ref<HTMLInputElement | null>(null)
const passwordEl = ref<HTMLInputElement | null>(null)

const errorVisible = computed(() => Boolean(errorText.value))

function fingerprint(): string {
  try {
    const seed = [
      navigator.userAgent,
      navigator.language,
      screen.width + 'x' + screen.height,
      Intl.DateTimeFormat().resolvedOptions().timeZone || '',
    ].join('|')
    let a = 0x811c9dc5
    let b = 0x9e3779b9
    for (let i = 0; i < seed.length; i += 1) {
      const code = seed.charCodeAt(i)
      a = Math.imul(a ^ code, 0x01000193) >>> 0
      b = Math.imul(b ^ ((code + i) & 0xffff), 0x85ebca6b) >>> 0
    }
    return a.toString(16).padStart(8, '0') + b.toString(16).padStart(8, '0')
  } catch {
    return ''
  }
}

async function submit(): Promise<void> {
  if (submitting.value) return
  const name = username.value.trim()
  const pass = password.value
  errorText.value = ''
  if (!name || !pass) {
    errorText.value = '请输入用户名和密码'
    return
  }
  submitting.value = true
  try {
    const body = new URLSearchParams({ username: name, password: pass })
    const headers: Record<string, string> = { 'Content-Type': 'application/x-www-form-urlencoded' }
    const fp = fingerprint()
    if (fp) headers['X-Browser-Fingerprint'] = fp
    await http('POST', '/api/login', { body, headers })
    // 会话在 HttpOnly Cookie 里；直接进新版控制台。
    location.replace('/admin/v2/')
  } catch (error) {
    if (error instanceof ApiError) {
      errorText.value = error.status === 401 ? '用户名或密码错误' : error.message
    } else {
      errorText.value = '网络异常，请稍后重试'
    }
    submitting.value = false
    password.value = ''
    passwordEl.value?.focus()
  }
}

function onUsernameEnter(): void {
  passwordEl.value?.focus()
}

onMounted(() => {
  // 登录页的 401 是「密码错」而不是「会话失效」：屏蔽 http 层的跳转。
  setUnauthorizedHandler(() => undefined)
  usernameEl.value?.focus()
  void http<{ version?: string }>('GET', '/api/app/version')
    .then((payload) => {
      version.value = String(payload?.version || '')
    })
    .catch(() => {
      version.value = ''
    })
})
</script>

<template>
  <main class="login">
    <form class="login__card" @submit.prevent="submit">
      <header class="login__head">
        <img
          v-if="!logoFailed"
          class="login__logo"
          :src="iconUrl"
          alt=""
          @error="logoFailed = true"
        />
        <div v-else class="login__logo login__logo--fallback" aria-hidden="true">M</div>
        <h1 class="login__title">MiraiHub 管理后台</h1>
        <p class="login__sub">多账号轮换 · API Key 网关 · 模型配置</p>
      </header>

      <label class="login__field">
        <span class="login__label">用户名</span>
        <input
          ref="usernameEl"
          v-model="username"
          class="login__input"
          autocomplete="username"
          autocapitalize="none"
          spellcheck="false"
          placeholder="用户名"
          @keydown.enter.prevent="onUsernameEnter"
        />
      </label>

      <label class="login__field">
        <span class="login__label">密码</span>
        <input
          ref="passwordEl"
          v-model="password"
          class="login__input"
          type="password"
          autocomplete="current-password"
          placeholder="密码"
          @keydown.enter.prevent="submit"
        />
      </label>

      <p v-if="errorVisible" class="login__error" role="alert">{{ errorText }}</p>

      <button class="login__submit" type="submit" :disabled="submitting">
        {{ submitting ? '登录中…' : '登录' }}
      </button>

      <p class="login__foot">
        <span v-if="version">v{{ version }}</span>
        <span v-else>MiraiHub</span>
      </p>
    </form>
  </main>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: var(--sp-6);
  background: var(--bg);
  color: var(--fg);
}

.login__card {
  width: 100%;
  max-width: 360px;
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-8) var(--sp-6) var(--sp-5);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
}

.login__head {
  display: grid;
  justify-items: center;
  gap: var(--sp-1);
  margin-bottom: var(--sp-2);
  text-align: center;
}

.login__logo {
  width: 48px;
  height: 48px;
  object-fit: contain;
  display: block;
}

.login__logo--fallback {
  display: grid;
  place-items: center;
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 600;
  color: var(--brand);
  background: var(--brand-soft);
  border-radius: var(--r-card);
}

.login__title {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-h1);
  line-height: var(--lh-h1);
  font-weight: 600;
  color: var(--fg);
}

.login__sub {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.login__field {
  display: grid;
  gap: var(--sp-1);
}

.login__label {
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
}

.login__input {
  height: 40px;
  width: 100%;
  padding: 0 var(--sp-3);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-ctl);
  background: var(--surface);
  color: var(--fg);
  font: inherit;
  outline: none;
  transition: border-color var(--t-fast) var(--ease), box-shadow var(--t-fast) var(--ease);
}

.login__input::placeholder {
  color: var(--fg-faint);
}

.login__input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 2px var(--brand-soft);
}

.login__error {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--danger);
  word-break: break-all;
}

.login__submit {
  height: 40px;
  border: 1px solid var(--brand);
  border-radius: var(--r-ctl);
  background: var(--brand);
  color: var(--brand-fg);
  font: inherit;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--t-fast) var(--ease), opacity var(--t-fast) var(--ease);
}

.login__submit:hover:not(:disabled) {
  background: var(--brand-hover);
}

.login__submit:disabled {
  opacity: 0.6;
  cursor: wait;
}

.login__foot {
  margin: var(--sp-2) 0 0;
  text-align: center;
  font-size: var(--fs-small);
  color: var(--fg-faint);
  font-variant-numeric: tabular-nums;
}
</style>
