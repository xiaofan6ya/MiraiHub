import { createApp } from 'vue'
import { createPinia } from 'pinia'

import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-sans/latin-600.css'
import '@fontsource/jetbrains-mono/latin-400.css'
import '@fontsource/jetbrains-mono/latin-500.css'

import './styles/tokens.css'
import './styles/base.css'
import './styles/refinement.css'
import './styles/appearance.css'
import { initAppearance } from './theme/useAppearance'
import { initPalette } from './theme/usePalette'

import App from './App.vue'
import router from './router'
import { initTheme } from './theme/useTheme'
import { initLocale } from './i18n'
// APK 的 WebView 靠 window.WBMobile 判定工作台就绪并换本地会话 Cookie；
// 必须在挂载前注册，否则 Java 侧的 checkWorkbench 永远拿到 'waiting'。
import './platform/mobile'

initTheme()
initPalette()
initAppearance()

// 默认语言（zh-CN）字典在入口里，initLocale 立即 resolve；存的是 en-US 时先等英文字典
// （独立 chunk）到达再挂载，首帧即英文、不闪中文 / 键名。加载失败也会 resolve（按中文显示）。
void initLocale().then(() => {
  createApp(App).use(createPinia()).use(router).mount('#app')
})
