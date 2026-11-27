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

/** 独立登录入口（admin/ui/login.html → dist/login.html），不依赖路由与外壳。 */
initTheme()
initPalette()
initAppearance()

createApp(LoginPage).mount('#login-app')
