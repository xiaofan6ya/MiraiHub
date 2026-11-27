<script setup lang="ts">
/** Independent login entry; shares the console's cosmic silver/grey theme.
 * Authentication retains username/password, fingerprint header and HttpOnly session flow.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowRight, LockKeyhole, Moon, ShieldCheck, Sun, UserRound } from 'lucide-vue-next'
import { useTheme } from '@/theme/useTheme'
import { replayVisuals } from '@/composables/usePageMotion'
import { http, ApiError } from '@/api/http'
import { setUnauthorizedHandler } from '@/api/auth'

const root = ref<HTMLElement | null>(null)
const { effectiveTheme, setMode } = useTheme()
const hidden = ref(document.hidden)
const stars = Array.from({ length: 32 }, (_, i) => ({ x: 28 + (i * 137 + 41) % 524, y: 20 + (i * i * 29 + 13) % 330, r: i % 7 === 0 ? 1.6 : .8 }))
function syncVisibility() { hidden.value = document.hidden }
function replay() { if (root.value) replayVisuals(root.value) }
function toggleTheme() { setMode(effectiveTheme.value === 'dark' ? 'light' : 'dark') }
watch(effectiveTheme, async () => { await nextTick(); replay() })
onBeforeUnmount(() => { document.removeEventListener('visibilitychange', syncVisibility); window.removeEventListener('pageshow', replay) })

const username = ref('')
const password = ref('')
const submitting = ref(false)
const errorText = ref('')
const version = ref('')
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
    // 会话在 HttpOnly Cookie 里；直接进后台唯一入口（`/admin/v2/` 只剩 301 兼容）。
    location.replace('/admin/')
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
  document.addEventListener('visibilitychange', syncVisibility)
  window.addEventListener('pageshow', replay)
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
  <main ref="root" class="login" :class="{ 'is-paused': hidden }">
    <header class="login__topbar">
      <a class="login__brand-head" href="/admin/login.html" aria-label="MiraiHub 登录首页">
        <svg class="login__logo" viewBox="0 0 40 40" fill="none" aria-hidden="true">
          <rect x="1" y="1" width="38" height="38" rx="12" fill="var(--surface-2)" stroke="var(--border-strong)" />
          <path d="M10.5 28V12.5l9.5 9 9.5-9V28" stroke="var(--fg)" stroke-width="2.7" stroke-linecap="round" stroke-linejoin="round" />
          <circle cx="30.4" cy="10.2" r="1.8" fill="var(--cosmic-light)" />
        </svg>
        <span class="login__brand-name">MiraiHub<span>多平台账号调度台</span></span>
      </a>
      <button class="login__theme" type="button" :aria-label="effectiveTheme === 'dark' ? '切换到星空银浅色主题' : '切换到星空灰深色主题'" @click="toggleTheme">
        <Sun v-if="effectiveTheme === 'dark'" :size="16" /><Moon v-else :size="16" />
        <span>{{ effectiveTheme === 'dark' ? '星空灰' : '星空银' }}</span>
      </button>
    </header>

    <div class="login__workspace">
      <section class="login__brand" aria-labelledby="login-brand-title">
        <p class="login__eyebrow"><span /> UNIFIED AI GATEWAY</p>
        <h2 id="login-brand-title" class="login__brand-title">让每一次连接，<br /><em>都有无限可能。</em></h2>
        <p class="login__description">一个工作台，连接你的模型、账号与灵感。<br />让复杂的调度，在这里井然有序。</p>

        <svg class="login__constellation" viewBox="0 0 580 370" fill="none" aria-hidden="true" focusable="false">
          <defs>
            <radialGradient id="login-sphere" cx=".3" cy=".22" r=".8"><stop stop-color="var(--planet-highlight)" /><stop offset=".42" stop-color="var(--planet-mid)" /><stop offset=".8" stop-color="var(--planet-shadow)" /><stop offset="1" stop-color="var(--planet-dark)" /></radialGradient>
            <linearGradient id="login-ring" x1="55" y1="270" x2="510" y2="90" gradientUnits="userSpaceOnUse"><stop stop-color="var(--cosmic-light)" stop-opacity=".08" /><stop offset=".45" stop-color="var(--cosmic-light)" stop-opacity=".85" /><stop offset="1" stop-color="var(--cosmic-light)" stop-opacity=".15" /></linearGradient>
            <clipPath id="login-sphere-clip"><circle cx="290" cy="174" r="72" /></clipPath>
          </defs>
          <g class="login__stars" fill="var(--cosmic-light)"><circle v-for="(star, i) in stars" :key="i" :cx="star.x" :cy="star.y" :r="star.r" :style="{ '--star-delay': (i % 6) * -.8 + 's' }" /></g>
          <g class="login__orbital-system">
            <ellipse cx="290" cy="184" rx="221" ry="77" transform="rotate(-23 290 184)" stroke="var(--cosmic-light)" stroke-opacity=".18" stroke-dasharray="2 7" />
            <ellipse cx="290" cy="184" rx="184" ry="56" transform="rotate(-23 290 184)" stroke="url(#login-ring)" stroke-width="1" />
            <ellipse cx="290" cy="184" rx="156" ry="40" transform="rotate(-23 290 184)" stroke="url(#login-ring)" stroke-width="9" opacity=".3" />
            <circle cx="290" cy="174" r="72" fill="url(#login-sphere)" />
            <g clip-path="url(#login-sphere-clip)" stroke="var(--cosmic-light)" stroke-opacity=".19" stroke-width=".8" transform="rotate(-18 290 174)">
              <path d="M208 137Q290 106 374 139M210 148Q290 120 378 153M210 162Q290 140 378 170M212 177Q294 159 378 187M215 194Q294 183 377 204M221 213Q294 204 370 222" />
              <path d="M217 144Q287 167 378 156M212 178Q298 199 378 184M223 212Q296 230 370 218" stroke-width="2" />
            </g>
            <path d="M134 184a156 40 0 0 0 312 0" transform="rotate(-23 290 184)" stroke="url(#login-ring)" stroke-width="9" />
            <path class="login__orbit-trace" pathLength="1" d="M87 270C-6 190 417 11 493 98S149 350 87 270Z" stroke="var(--cosmic-light)" stroke-width="1.5" stroke-linecap="round" />
            <g class="login__satellite"><circle cx="290" cy="68" r="4" fill="var(--cosmic-light)" /><circle cx="290" cy="68" r="10" stroke="var(--cosmic-light)" stroke-opacity=".2" /></g>
            <g class="login__satellite login__satellite--far"><circle cx="290" cy="310" r="2.4" fill="var(--cosmic-light)" /></g>
          </g>
          <g stroke="var(--cosmic-light)" stroke-width=".8" opacity=".5"><path d="M107 101h10m-5-5v10M474 255h8m-4-4v8M419 46h6m-3-3v6" /><path d="M80 303h45l28-28M447 87l24-24h38" stroke-opacity=".5" /></g>
          <g fill="var(--fg-muted)" font-size="8" letter-spacing="1.6" font-family="var(--font-sans)"><text x="57" y="321">ONE ENDPOINT</text><text x="447" y="54">EVERY MODEL</text></g>
        </svg>

        <div class="login__features">
          <div><span class="login__feature-mark">01</span><p>统一接入<span>一个 API，连接多个平台</span></p></div>
          <div><span class="login__feature-mark">02</span><p>智能调度<span>账号轮换，额度从容分配</span></p></div>
          <div><span class="login__feature-mark">03</span><p>全局可见<span>用量、模型与风控一目了然</span></p></div>
        </div>
      </section>

      <section class="login__panel" aria-labelledby="login-title">
        <form class="login__card" @submit.prevent="submit">
          <div class="login__card-top"><span class="login__access"><ShieldCheck :size="14" /> 管理员工作区</span><span class="login__card-index" aria-hidden="true">M / 01</span></div>
          <header class="login__head">
            <p class="login__welcome">欢迎回来</p>
            <h1 id="login-title" class="login__title">登录 MiraiHub</h1>
            <p class="login__sub">使用管理员账号，继续你的工作。</p>
          </header>

          <label class="login__field">
            <span class="login__label">用户名</span>
            <span class="login__input-wrap"><UserRound :size="17" aria-hidden="true" /><input ref="usernameEl" v-model="username" name="username" class="login__input" autocomplete="username" autocapitalize="none" spellcheck="false" placeholder="请输入用户名" :aria-invalid="errorVisible || undefined" :aria-describedby="errorVisible ? 'login-error' : undefined" @keydown.enter.prevent="onUsernameEnter" /></span>
          </label>

          <label class="login__field">
            <span class="login__label">密码</span>
            <span class="login__input-wrap"><LockKeyhole :size="17" aria-hidden="true" /><input ref="passwordEl" v-model="password" name="password" class="login__input" type="password" autocomplete="current-password" placeholder="请输入密码" :aria-invalid="errorVisible || undefined" :aria-describedby="errorVisible ? 'login-error' : undefined" @keydown.enter.prevent="submit" /></span>
          </label>

          <p v-if="errorVisible" id="login-error" class="login__error" role="alert">{{ errorText }}</p>
          <button class="login__submit" type="submit" :disabled="submitting" :aria-busy="submitting"><span>{{ submitting ? '正在登录…' : '登录控制台' }}</span><span v-if="submitting" class="login__spinner" aria-hidden="true" /><ArrowRight v-else :size="18" aria-hidden="true" /></button>
          <div class="login__divider" />
          <p class="login__foot"><ShieldCheck :size="14" aria-hidden="true" />统一入口 · 专注每一次连接</p>
          <svg class="login__card-orbit" viewBox="0 0 150 150" fill="none" aria-hidden="true"><circle cx="130" cy="130" r="94" /><circle cx="130" cy="130" r="72" stroke-dasharray="2 6" /><circle cx="130" cy="130" r="49" /></svg>
        </form>
        <p class="login__panel-foot">MIRAIHUB<span />YOUR MODELS. YOUR UNIVERSE.</p>
      </section>
    </div>

    <footer class="login__bottom"><span>MiraiHub <span v-if="version" class="login__version">v{{ version }}</span></span><span>连接有序，灵感无界。</span></footer>
  </main>
</template>

<style scoped>
.login{min-height:100vh;min-height:100dvh;display:flex;flex-direction:column;position:relative;isolation:isolate;overflow:clip;background:var(--bg);color:var(--fg);padding:0 clamp(24px,5vw,88px);background-image:radial-gradient(ellipse at 18% 45%,color-mix(in srgb,var(--cosmic-light) 5%,transparent),transparent 55%)}
.login__topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;width:100%;max-width:1440px;margin:0 auto;padding:30px 0;z-index:1}
.login__brand-head{display:inline-flex;align-items:center;gap:12px;color:var(--fg);text-decoration:none}.login__logo{width:40px;height:40px;flex:none}.login__brand-name{display:grid;gap:2px;font-size:18px;font-weight:600;letter-spacing:-.6px}.login__brand-name>span{font-size:10px;font-weight:400;color:var(--fg-muted);letter-spacing:1px}
.login__theme{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--border);border-radius:24px;background:var(--surface);padding:9px 13px;color:var(--fg-2);font:inherit;font-size:11px;cursor:pointer;transition:border-color 200ms,background 200ms}.login__theme:hover{border-color:var(--border-strong);background:var(--surface-2)}.login__theme:focus-visible{outline:2px solid var(--brand);outline-offset:4px}
.login__workspace{flex:1;display:grid;grid-template-columns:minmax(0,1.2fr) minmax(320px,.85fr);align-items:center;gap:clamp(48px,7vw,128px);width:100%;max-width:1176px;margin:0 auto;padding:24px 0 38px}
.login__brand{position:relative;min-width:0}.login__eyebrow{display:flex;align-items:center;gap:10px;margin:0 0 24px;color:var(--fg-muted);font-size:10px;letter-spacing:2.6px}.login__eyebrow>span{width:5px;height:5px;background:var(--cosmic-light);border-radius:50%;box-shadow:0 0 0 4px color-mix(in srgb,var(--cosmic-light) 8%,transparent)}
.login__brand-title{font-size:clamp(32px,3.3vw,48px);line-height:1.4;font-weight:500;letter-spacing:-1.8px;margin:0 0 18px}.login__brand-title em{font-style:normal;color:var(--fg-muted)}.login__description{font-size:12px;line-height:2;color:var(--fg-muted);margin:0}
.login__constellation{display:block;width:100%;max-width:580px;height:auto;margin:-8px auto -6px;overflow:visible;pointer-events:none}.login__orbital-system{transform-origin:290px 184px;animation:login-float 9s ease-in-out infinite}.login__orbit-trace{stroke-dasharray:.13 .87;animation:login-trace 9s linear infinite;opacity:.65}.login__satellite{transform-origin:290px 174px;animation:login-orbit 32s linear infinite}.login__satellite--far{animation-duration:48s;animation-direction:reverse}.login__stars circle{animation:login-twinkle 4.8s ease-in-out var(--star-delay,0s) infinite alternate}
.login__features{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;padding-top:22px;border-top:1px solid var(--border)}.login__features>div{display:flex;align-items:flex-start;gap:9px;min-width:0}.login__feature-mark{font-size:9px;color:var(--fg-faint);font-variant-numeric:tabular-nums;padding-top:2px}.login__features p{display:grid;gap:7px;margin:0;font-size:11px;color:var(--fg-2)}.login__features p>span{font-size:9px;line-height:1.8;color:var(--fg-muted)}
.login__panel{width:100%;max-width:426px;justify-self:end}.login__card{position:relative;isolation:isolate;overflow:hidden;display:grid;gap:24px;width:100%;padding:30px 34px 28px;background:var(--surface);border:1px solid var(--border);border-radius:24px;box-shadow:var(--shadow-2),inset 0 1px 0 color-mix(in srgb,var(--fg) 4%,transparent)}.login__card::before{content:'';position:absolute;top:0;left:24%;right:24%;height:1px;background:linear-gradient(90deg,transparent,var(--cosmic-light),transparent);opacity:.6}
.login__card-top{display:flex;justify-content:space-between;align-items:center;gap:12px}.login__access{display:inline-flex;align-items:center;gap:7px;color:var(--fg-muted);font-size:10px}.login__card-index{font-size:9px;letter-spacing:1.7px;color:var(--fg-faint)}.login__head{padding:16px 0 8px}.login__welcome{font-size:11px;color:var(--fg-muted);margin:0 0 10px}.login__title{font-size:28px;font-weight:500;line-height:1.35;letter-spacing:-1px;margin:0 0 12px;color:var(--fg)}.login__sub{font-size:11px;line-height:1.8;color:var(--fg-muted);margin:0}
.login__field{display:grid;gap:9px;min-width:0}.login__label{font-size:11px;font-weight:500;line-height:1.6;color:var(--fg-2)}.login__input-wrap{display:flex;align-items:center;gap:11px;height:48px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2);color:var(--fg-faint);padding:0 14px;transition:background 200ms,border-color 200ms,box-shadow 200ms}.login__input-wrap>svg{flex:none}.login__input-wrap:hover{border-color:var(--border-strong)}.login__input-wrap:focus-within{border-color:var(--brand);box-shadow:0 0 0 3px var(--ring);background:var(--surface);color:var(--brand)}.login__input{width:100%;min-width:0;height:100%;border:0;outline:0;background:transparent;color:var(--fg);font:inherit;font-size:12px;padding:0}.login__input::placeholder{color:var(--fg-faint)}
.login__error{font-size:11px;line-height:1.8;background:var(--danger-soft);color:var(--danger);border-radius:9px;padding:10px 12px;margin:0;overflow-wrap:anywhere}.login__submit{display:flex;justify-content:center;align-items:center;gap:16px;min-height:48px;margin-top:3px;width:100%;padding:12px 18px;border:1px solid var(--brand);border-radius:10px;background:var(--brand);color:var(--brand-fg);font:inherit;font-size:12px;font-weight:500;cursor:pointer;transition:background 200ms,border-color 200ms,transform 200ms,box-shadow 200ms;box-shadow:inset 0 1px 0 #ffffff21}.login__submit svg{transition:transform 240ms}.login__submit:hover:not(:disabled){background:var(--brand-hover);border-color:var(--brand-hover);box-shadow:0 5px 20px color-mix(in srgb,var(--brand) 12%,transparent)}.login__submit:hover svg{transform:translateX(4px)}.login__submit:active:not(:disabled){transform:translateY(1px)}.login__submit:focus-visible{outline:2px solid var(--brand);outline-offset:4px}.login__submit:disabled{opacity:.65;cursor:wait}.login__spinner{width:15px;height:15px;border:1.5px solid currentColor;border-right-color:transparent;border-radius:50%;animation:login-orbit 800ms linear infinite}
.login__divider{height:1px;background:var(--border-soft);margin-top:2px}.login__foot{display:flex;align-items:center;justify-content:center;gap:7px;font-size:10px;line-height:1.8;color:var(--fg-muted);margin:0}.login__card-orbit{position:absolute;right:-75px;bottom:-80px;width:200px;height:200px;stroke:var(--cosmic-light);stroke-width:.7;opacity:.12;pointer-events:none;z-index:-1}.login__panel-foot{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;margin:22px 0 0;font-size:8px;letter-spacing:1.6px;line-height:1.8;color:var(--fg-faint)}.login__panel-foot>span{width:3px;height:3px;background:var(--fg-faint);border-radius:50%}
.login__bottom{display:flex;align-items:center;justify-content:space-between;gap:20px;width:100%;max-width:1440px;margin:0 auto;padding:22px 0;border-top:1px solid var(--border-soft);color:var(--fg-faint);font-size:10px;line-height:1.7}.login__version{margin-left:8px;font-variant-numeric:tabular-nums}
.login__brand>.login__eyebrow,.login__brand-title,.login__description,.login__features,.login__card,.login__panel-foot{animation:login-arrive 800ms cubic-bezier(.2,.8,.2,1) both}.login__brand-title{animation-delay:70ms}.login__description{animation-delay:140ms}.login__features{animation-delay:280ms}.login__card{animation-delay:160ms}.login__panel-foot{animation-delay:260ms}
.is-paused .login__orbital-system,.is-paused .login__orbit-trace,.is-paused .login__satellite,.is-paused .login__stars circle{animation-play-state:paused}
@keyframes login-arrive{from{opacity:0;transform:translate3d(0,16px,0)}to{opacity:1;transform:translate3d(0,0,0)}}@keyframes login-orbit{to{transform:rotate(360deg)}}@keyframes login-trace{to{stroke-dashoffset:-1}}@keyframes login-twinkle{from{opacity:.2}to{opacity:.8}}@keyframes login-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}
@media(min-width:1450px){.login__workspace{padding-top:35px;padding-bottom:65px}.login__constellation{margin:4px auto 0}}
@media(max-width:1000px){.login__workspace{gap:40px;grid-template-columns:minmax(0,1fr) minmax(310px,.95fr)}.login__brand-title{font-size:32px}.login__features{gap:10px}.login__feature-mark{display:none}.login__features p>span{font-size:9px}.login__card{padding:28px}.login__constellation{margin:10px auto}}
@media(max-width:760px){.login{padding:0 22px}.login__topbar{padding:23px 0}.login__brand-name{font-size:17px}.login__brand-name>span{font-size:9px}.login__theme{padding:8px 10px}.login__workspace{grid-template-columns:1fr;gap:30px;padding:18px 0 34px;max-width:426px}.login__brand{display:none}.login__panel{justify-self:center}.login__card{padding:28px;gap:22px;border-radius:20px}.login__head{padding-top:10px}.login__title{font-size:27px}.login__bottom{font-size:9px;padding:18px 0}.login__panel-foot{font-size:7px}.login__input{font-size:16px}}
@media(max-width:360px){.login{padding:0 16px}.login__card{padding:24px}.login__theme>span{display:none}.login__bottom{gap:8px;font-size:8px}}
@media(prefers-reduced-motion:reduce){.login *, .login *::before{animation:none!important;transition:none!important}}
</style>
