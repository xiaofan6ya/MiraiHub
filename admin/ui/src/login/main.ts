import { createApp } from 'vue'

import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-sans/latin-600.css'

import '../styles/tokens.css'
import '../styles/base.css'
import '../styles/refinement.css'
import '../styles/appearance.css'
import { initAppearance } from '../theme/useAppearance'
import { initPalette } from '../theme/usePalette'
import { initTheme } from '../theme/useTheme'
import LoginPage from './LoginPage.vue'
// APK 首次进入是未登录状态，服务端发的是这一个页面 ——
// window.WBMobile 必须在登录页就注册好，Java 才不会卡在启动画面。
import '../platform/mobile'

/** 独立登录入口（admin/ui/login.html → dist/login.html），不依赖路由与外壳。 */
initTheme()
initPalette()
initAppearance()

createApp(LoginPage).mount('#login-app')
