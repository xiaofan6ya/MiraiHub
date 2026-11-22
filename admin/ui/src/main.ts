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

initTheme()

createApp(App).use(createPinia()).use(router).mount('#app')
