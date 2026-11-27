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
import JoinPage from './JoinPage.vue'

// Independent public Vue entry, sharing console theme preferences.
initTheme()
initPalette()
initAppearance()

createApp(JoinPage).mount('#join-app')
