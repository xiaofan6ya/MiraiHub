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
 * 视觉（10-visual-refresh / review_notes 待优化清单第 7 条）：
 *   左侧深墨蓝品牌区（logo 放在 --grad-brand 圆角方底上，与靛蓝协调；三条能力说明；版本号），
 *   右侧表单卡；大屏不再空一大片，<900px 品牌区收成表单上方的一条；按钮 / 输入框 34px。
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
    <section class="login__brand" aria-hidden="true">
      <div class="login__brand-inner">
        <div class="login__brand-head">
          <span class="login__mark">
            <img v-if="!logoFailed" class="login__logo" :src="iconUrl" alt="" @error="logoFailed = true" />
            <span v-else class="login__logo-fallback">M</span>
          </span>
          <span class="login__brand-name">MiraiHub</span>
        </div>
        <p class="login__brand-title">多平台账号调度台</p>
        <ul class="login__features">
          <li><span class="login__bullet" />多账号轮换与熔断，额度用到最后一分</li>
          <li><span class="login__bullet" />统一 API Key 网关，一个地址接所有平台</li>
          <li><span class="login__bullet" />模型配置、用量与风控哨兵集中查看</li>
        </ul>
        <p class="login__brand-foot">
          <span v-if="version">v{{ version }}</span>
          <span v-else>MiraiHub</span>
        </p>
      </div>
    </section>

    <section class="login__panel">
      <form class="login__card" @submit.prevent="submit">
        <header class="login__head">
          <span class="login__mark login__mark--sm" aria-hidden="true">
            <img v-if="!logoFailed" class="login__logo" :src="iconUrl" alt="" @error="logoFailed = true" />
            <span v-else class="login__logo-fallback">M</span>
          </span>
          <h1 class="login__title">登录 MiraiHub</h1>
          <p class="login__sub">管理后台 · 使用管理员账号登录</p>
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
    </section>
  </main>
</template>

<style scoped>
.login {
  min-height: 100vh;
  min-height: 100dvh;
  display: grid;
  grid-template-columns: minmax(360px, 5fr) 7fr;
  background: var(--bg);
  color: var(--fg);
}

/* ---------- 品牌区：深墨蓝 + 品牌光晕 ---------- */
.login__brand {
  position: relative;
  display: flex;
  align-items: center;
  padding: var(--sp-12) var(--sp-10);
  background:
    radial-gradient(900px 480px at 0% 0%, rgba(125, 143, 255, .22), transparent 60%),
    radial-gradient(700px 420px at 100% 100%, rgba(91, 108, 240, .16), transparent 60%),
    var(--side-bg);
  color: var(--side-fg);
  overflow: hidden;
}

.login__brand-inner {
  display: flex;
  flex-direction: column;
  gap: var(--sp-6);
  max-width: 420px;
  margin-left: auto;
  width: 100%;
}

.login__brand-head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}

.login__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--r-card);
  background: var(--grad-brand);
  box-shadow:
    0 0 0 1px rgba(255, 255, 255, .16),
    0 6px 18px rgba(49, 72, 200, .35);
  overflow: hidden;
  flex: none;
}

.login__mark--sm {
  width: 36px;
  height: 36px;
}

.login__logo {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.login__logo-fallback {
  font-size: var(--fs-h2);
  font-weight: 600;
  color: #fff;
}

.login__brand-name {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
}

.login__brand-title {
  font-size: var(--fs-display);
  line-height: var(--lh-display);
  font-weight: 600;
  color: #fff;
  text-wrap: balance;
}

.login__features {
  display: grid;
  gap: var(--sp-3);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--side-fg-muted);
}

.login__features li {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}

.login__bullet {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--side-accent);
  box-shadow: 0 0 0 3px rgba(125, 143, 255, .18);
}

.login__brand-foot {
  font-size: var(--fs-small);
  color: var(--side-fg-muted);
  font-variant-numeric: tabular-nums;
}

/* ---------- 表单区 ---------- */
.login__panel {
  display: grid;
  place-items: center;
  padding: var(--sp-10) var(--sp-6);
  background-image: var(--bg-glow);
  background-repeat: no-repeat;
}

.login__card {
  width: 100%;
  max-width: 380px;
  display: grid;
  gap: var(--sp-4);
  padding: var(--sp-8);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-2);
}

.login__head {
  display: grid;
  gap: var(--sp-1);
  margin-bottom: var(--sp-1);
}

.login__head .login__mark {
  display: none;
}

.login__title {
  margin: 0;
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
  gap: 6px;
}

.login__label {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  color: var(--fg-2);
}

.login__input {
  height: var(--ctl-h);
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

.login__input:hover {
  border-color: var(--fg-faint);
}

.login__input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--ring);
}

.login__error {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--danger-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--danger);
  word-break: break-all;
}

.login__submit {
  height: var(--ctl-h);
  margin-top: var(--sp-1);
  border: 1px solid var(--brand);
  border-radius: var(--r-ctl);
  background: var(--brand);
  color: var(--brand-fg);
  font: inherit;
  font-weight: 500;
  cursor: pointer;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .14), var(--shadow-1);
  transition: background var(--t-fast) var(--ease), opacity var(--t-fast) var(--ease);
}

.login__submit:hover:not(:disabled) {
  background: var(--brand-hover);
  border-color: var(--brand-hover);
}

.login__submit:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

.login__submit:disabled {
  opacity: 0.6;
  cursor: wait;
}

.login__foot {
  margin: 0;
  text-align: center;
  font-size: var(--fs-small);
  color: var(--fg-faint);
  font-variant-numeric: tabular-nums;
}

/* 有品牌区时版本号写在品牌区，表单卡里不重复 */
@media (min-width: 900px) {
  .login__foot {
    display: none;
  }
}

/* <900：品牌区收起，表单卡单独居中，卡头显示 logo */
@media (max-width: 899px) {
  .login {
    grid-template-columns: 1fr;
  }
  .login__brand {
    display: none;
  }
  .login__head .login__mark {
    display: inline-flex;
    margin-bottom: var(--sp-2);
  }
}

@media (max-width: 480px) {
  .login__panel {
    padding: var(--sp-6) var(--sp-4);
    align-items: start;
  }
  .login__card {
    padding: var(--sp-6) var(--sp-5);
  }
}
</style>
