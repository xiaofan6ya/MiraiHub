import { createApp } from 'vue'
import { createPinia } from 'pinia'

import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-sans/latin-600.css'
import '@fontsource/jetbrains-mono/latin-400.css'
import '@fontsource/jetbrains-mono/latin-500.css'

import './styles/tokens.css'
import './styles/base.css'

import App from './App.vue'
import router from './router'
import { initTheme } from './theme/useTheme'
import { initLocale } from './i18n'

initTheme()

// 默认语言（zh-CN）字典在入口里，initLocale 立即 resolve；存的是 en-US 时先等英文字典
// （独立 chunk）到达再挂载，首帧即英文、不闪中文 / 键名。加载失败也会 resolve（按中文显示）。
void initLocale().then(() => {
  createApp(App).use(createPinia()).use(router).mount('#app')
})
