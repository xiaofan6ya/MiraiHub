import { createApp, h } from 'vue'

import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-sans/latin-600.css'

import '../styles/tokens.css'
import '../styles/base.css'
import { initTheme } from '../theme/useTheme'

initTheme()

createApp({
  render: () =>
    h('main', { style: 'padding: 48px; max-width: 420px' }, [
      h('h1', { style: 'font-size: var(--fs-h1); font-weight: 600' }, '登录 · MiraiHub'),
      h(
        'p',
        { style: 'margin-top: 12px; color: var(--fg-muted)' },
        '新版登录页建设中。当前登录仍由旧控制台处理：/admin',
      ),
    ]),
}).mount('#login-app')
