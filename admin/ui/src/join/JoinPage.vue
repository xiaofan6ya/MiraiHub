<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, Check, CheckCheck, ChevronRight, CircleHelp, ExternalLink, KeyRound, LoaderCircle, Moon, QrCode, RefreshCw, ShieldCheck, Smartphone, Sun } from 'lucide-vue-next'
import OrbitScene from '@/components/ambient/OrbitScene.vue'
import PlatformLogo from '@/components/identity/PlatformLogo.vue'
import { useTheme } from '@/theme/useTheme'
import { useJoin } from './useJoin'

const { effectiveTheme, setMode } = useTheme()
const { platforms, copy, loading, busy, error, stage, selected, mode, qr, authUrl, userCode, needCallback, callback, country, mobile, code, password, needPassword, accounts, accountName, seconds, retrySeconds, choose, back, load, sendSms, verify, submitCallback, selectAccount } = useJoin()
const panel = ref<HTMLElement | null>(null)
const titles: Record<string, string> = { choose: '选择连接的平台', loading: '正在准备验证', qr: '扫码连接账号', auth: '完成账号授权', sms: '使用手机号验证', code: '验证你的身份', select: '选择要连接的账号', done: '账号已成功连接', expired: '重新开启连接' }
const panelTitle = computed(() => titles[stage.value] || titles.choose)
const subtitle = computed(() => stage.value === 'choose' ? '选择平台后，按提示完成验证即可。' : stage.value === 'done' ? '感谢你的参与，账号已加入共享池。' : selected.value ? `${selected.value.label} · ${mode.value === 'sms' ? '短信验证' : mode.value === 'official' ? '官方登录' : '安全授权'}` : '')
const steps = computed(() => stage.value === 'done' ? 3 : stage.value === 'choose' ? 1 : 2)
const timeLeft = computed(() => `${Math.floor(seconds.value / 60)}:${String(seconds.value % 60).padStart(2, '0')}`)
const intro = computed(() => copy.value.intro?.length ? copy.value.intro : ['选择你常用的 **平台**', '完成一次 **身份验证**', '账号自动加入 **共享池**'])
const parts = (line: string) => line.split(/(\*\*.*?\*\*)/g).map(text => ({ text: text.startsWith('**') && text.endsWith('**') ? text.slice(2, -2) : text, bold: text.startsWith('**') && text.endsWith('**') }))
watch(stage, async () => { await nextTick(); panel.value?.focus({ preventScroll: true }) })
/** 短信验证码只收数字：粘贴带空格/横线的内容也能直接用。 */
const digitsOnly = () => { code.value = code.value.replace(/\D/g, '').slice(0, 8) }
</script>

<template>
  <div class="join-page">
    <header class="join-top">
      <a class="join-brand" href="/join" aria-label="MiraiHub 连接账号首页">
        <svg viewBox="0 0 36 36" aria-hidden="true"><rect width="36" height="36" rx="11" fill="var(--brand)"/><path d="M10 25V11l8 10 8-10v14" fill="none" stroke="var(--brand-fg)" stroke-width="2.4" stroke-linejoin="round"/></svg>
        <span>MiraiHub<span class="join-brand__divider">/</span><span class="join-brand__sub">连接账号</span></span>
      </a>
      <button class="join-icon" type="button" :aria-label="effectiveTheme === 'dark' ? '切换浅色主题' : '切换深色主题'" @click="setMode(effectiveTheme === 'dark' ? 'light' : 'dark')"><Sun v-if="effectiveTheme === 'dark'" :size="18"/><Moon v-else :size="18"/></button>
    </header>

    <main class="join-main">
      <section class="join-story" aria-labelledby="join-title">
        <span class="join-eyebrow"><span/> CONNECT & SHARE</span>
        <h1 id="join-title">{{ copy.title || '连接账号，共享更多可能' }}</h1>
        <p class="join-lead">{{ copy.sub || '让每一个账号的潜力，都被看见。\n连接你常用的平台，一起拓展 AI 的更多可能。' }}</p>
        <div class="join-universe">
          <svg class="join-universe__grid" viewBox="0 0 440 300" aria-hidden="true"><ellipse cx="220" cy="160" rx="194" ry="83"/><ellipse cx="220" cy="160" rx="154" ry="116" transform="rotate(-18 220 160)"/><path d="M20 160h400M220 24v272" stroke-dasharray="2 7"/><circle cx="46" cy="124" r="3"/><circle cx="392" cy="194" r="3"/></svg>
          <OrbitScene class="ov-hero__planet join-planet"/>
          <span class="join-orbit-note"><span/> 一个连接，更多可能</span>
          <span class="join-orbit-coordinate">MH / OPEN CONNECT</span>
        </div>
        <ol class="join-intro"><li v-for="(line, index) in intro" :key="index"><span class="join-intro__index">{{ String(index + 1).padStart(2, '0') }}</span><span><template v-for="(part, i) in parts(line)" :key="i"><strong v-if="part.bold">{{ part.text }}</strong><template v-else>{{ part.text }}</template></template></span></li></ol>
      </section>

      <section class="join-card" aria-labelledby="join-panel-title" :aria-busy="busy || loading">
        <div class="join-progress" aria-label="连接进度"><span v-for="(label, i) in ['选择平台', '验证身份', '完成连接']" :key="label" :class="{ 'is-current': steps === i + 1, 'is-complete': steps > i + 1 }" :aria-current="steps === i + 1 ? 'step' : undefined"><span class="join-progress__dot"><Check v-if="steps > i + 1" :size="12"/><template v-else>{{ i + 1 }}</template></span>{{ label }}</span></div>
        <div ref="panel" class="join-card__heading" tabindex="-1"><h2 id="join-panel-title">{{ panelTitle }}</h2><p>{{ subtitle }}</p></div>
        <div v-if="error" class="join-error" role="alert"><CircleHelp :size="16"/><span>{{ error }}</span></div>
        <Transition name="join-step" mode="out-in">
          <div :key="stage" class="join-stage">
            <template v-if="stage === 'choose'">
              <div v-if="loading" class="join-wait" role="status"><LoaderCircle class="join-spin" :size="26"/><span>正在加载可用平台…</span></div>
              <div v-else-if="!platforms.length" class="join-empty"><ShieldCheck :size="34"/><h3>入口暂未开放</h3><p>请联系邀请人确认开放状态，或稍后重试。</p><button type="button" class="join-button join-button--soft" @click="load"><RefreshCw :size="16"/>重新加载</button></div>
              <div v-else class="join-platforms"><button v-for="platform in platforms" :key="platform.id" class="join-platform" type="button" @click="choose(platform)"><span class="join-platform__logo"><PlatformLogo :id="platform.id" :size="27" :label="platform.label"/></span><span class="join-platform__text"><strong>{{ platform.label }}</strong><span>{{ platform.modes.includes('qr') ? '扫码授权' : platform.modes.includes('auth_url') || platform.modes.includes('official') ? '官方授权登录' : '短信验证' }}<template v-if="platform.modes.includes('sms') && platform.modes.length > 1"> · 支持手机号</template></span></span><ArrowRight :size="17"/></button></div>
            </template>
            <div v-else-if="stage === 'loading'" class="join-wait" role="status"><LoaderCircle class="join-spin" :size="28"/><span>正在创建安全验证会话…</span></div>
            <template v-else-if="stage === 'qr' || stage === 'auth'">
              <div v-if="qr" class="join-qr"><div class="join-qr__frame"><img :src="qr" alt="平台登录授权二维码" width="192" height="192"/></div><p>使用 {{ selected?.label }} App 扫码并确认</p></div>
              <div v-else class="join-auth"><span class="join-auth__icon"><ExternalLink :size="30"/></span><p v-if="mode === 'official'">点下面的按钮打开官方授权页，<br/>用同一台手机完成登录，结果会自动出现。</p><p v-else>前往 {{ selected?.label }} 授权页面完成登录，<br/>然后回到这里继续。</p><a v-if="authUrl" class="join-button" :href="authUrl" target="_blank" rel="noopener noreferrer">{{ mode === 'official' ? '打开官方登录页' : '打开授权页面' }}<ExternalLink :size="16"/></a></div>
              <div v-if="userCode" class="join-user-code"><span>授权码</span><strong>{{ userCode }}</strong></div>
              <form v-if="needCallback" class="join-form" @submit.prevent="submitCallback"><label for="join-callback">授权完成后的回调地址</label><textarea id="join-callback" v-model="callback" rows="3" placeholder="粘贴授权后浏览器地址栏的完整地址" autocomplete="off" spellcheck="false" required :disabled="busy"/><p class="join-field-hint">授权完成后，将地址栏的回调链接复制到这里。</p><button class="join-button" :disabled="busy || !callback.trim()"><LoaderCircle v-if="busy" class="join-spin" :size="16"/>确认连接<ArrowRight :size="16"/></button></form>
              <div class="join-session" role="status"><span class="join-pulse"/>{{ needCallback ? '等待提交授权结果' : '等待授权确认' }}<span class="join-session__time">{{ timeLeft }} 后过期</span></div>
              <button v-if="selected?.modes.includes('sms')" class="join-text-button" type="button" @click="choose(selected!, 'sms')"><Smartphone :size="15"/>改用手机号验证</button>
            </template>
            <form v-else-if="stage === 'sms' || stage === 'code'" class="join-form" @submit.prevent="stage === 'sms' ? sendSms() : verify()">
              <template v-if="stage === 'sms'"><label for="join-mobile">手机号</label><div class="join-phone"><select v-model="country" aria-label="手机区号" :disabled="busy"><option value="86">+86 中国大陆</option><option value="852">+852 中国香港</option><option value="853">+853 中国澳门</option></select><input id="join-mobile" v-model="mobile" type="tel" inputmode="numeric" autocomplete="tel-national" :maxlength="country === '86' ? 11 : 8" :placeholder="country === '86' ? '11 位手机号' : '8 位手机号'" required :disabled="busy"/></div><p class="join-field-hint">验证码将由所选平台发送至你的手机。</p><button class="join-button" :disabled="busy || retrySeconds > 0"><LoaderCircle v-if="busy" class="join-spin" :size="16"/>{{ retrySeconds ? `${retrySeconds} 秒后重试` : '获取验证码' }}<ArrowRight v-if="!busy" :size="16"/></button></template>
              <template v-else><div class="join-phone-summary"><Smartphone :size="19"/><span>验证码已发送至<br/><strong>+{{ country }} {{ mobile }}</strong></span><button class="join-text-button" type="button" :disabled="busy" @click="choose(selected!, 'sms')">修改</button></div><label for="join-code">短信验证码</label><input id="join-code" v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" placeholder="输入短信验证码" required :disabled="busy" @input="digitsOnly"/><template v-if="needPassword"><label for="join-password">账号密码</label><input id="join-password" v-model="password" type="password" autocomplete="current-password" minlength="6" placeholder="平台要求的账号密码" required :disabled="busy"/><p class="join-field-hint">该平台还需要账号密码以完成身份验证。</p></template><div class="join-code-actions"><span>{{ timeLeft }} 后过期</span><button class="join-text-button" type="button" :disabled="busy || retrySeconds > 0" @click="sendSms">{{ retrySeconds ? `${retrySeconds} 秒后重发` : '重新发送验证码' }}</button></div><button class="join-button" :disabled="busy || !code.trim()"><LoaderCircle v-if="busy" class="join-spin" :size="16"/>验证并连接<ArrowRight v-if="!busy" :size="16"/></button></template>
            </form>
            <button v-if="(stage === 'sms' || stage === 'code') && (selected?.modes.includes('official') || selected?.modes.includes('auth_url'))" class="join-text-button" type="button" @click="choose(selected!, selected!.modes.includes('official') ? 'official' : 'auth_url')"><ExternalLink :size="15"/>改用官方授权登录</button>
            <div v-else-if="stage === 'select'" class="join-accounts"><p>当前手机号关联了多个账号，请选择本次连接的账号。</p><button v-for="account in accounts" :key="account.sec_uid" class="join-platform" type="button" :disabled="busy" @click="selectAccount(account.sec_uid)"><span class="join-platform__logo"><KeyRound :size="22"/></span><span class="join-platform__text"><strong>{{ account.name || '未命名账号' }}</strong><span>{{ account.type || '平台账号' }}</span></span><ChevronRight :size="17"/></button><p v-if="!accounts.length">没有可选择的账号，请返回重新验证。</p></div>
            <div v-else-if="stage === 'done'" class="join-success"><span class="join-success__mark"><CheckCheck :size="34"/></span><h3>{{ accountName }}</h3><p>连接已完成，可以关闭当前页面，<br/>或继续连接其他平台的账号。</p><button class="join-button" type="button" @click="back">继续连接其他账号<ArrowRight :size="16"/></button></div>
            <div v-else class="join-empty"><QrCode :size="36"/><p>验证已结束，重新开始即可获取新的验证会话。</p><button class="join-button" type="button" @click="selected && choose(selected, mode)"><RefreshCw :size="16"/>重新开始</button></div>
          </div>
        </Transition>
        <button v-if="stage !== 'choose' && stage !== 'done'" class="join-back join-text-button" type="button" @click="back"><ArrowLeft :size="15"/>返回重选平台</button>
        <div class="join-card__foot"><ShieldCheck :size="16"/><span>仅向所选平台提交登录验证信息，请确认账号后授权。</span></div>
      </section>
    </main>
    <footer class="join-footer"><span>MiraiHub<span class="join-footer__dot">·</span>连接，让可能发生</span><span>{{ copy.foot || '请使用你有权管理的账号完成连接' }}</span></footer>
  </div>
</template>

<style scoped src="./join.css"></style>

