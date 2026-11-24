import { defineConfig, type Plugin } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

const API_TARGET = 'http://127.0.0.1:8790'

/**
 * 第三方库分块表：顺序即优先级（先匹配先返回）。
 *
 * 为什么按用途拆，而不是"全都丢进 vendor"：
 *   · 三个包的变更频率完全不同 —— Vue 运行时几乎不动、naive-ui 跟着升级、echarts 只被图表用；
 *     混在一个 chunk 里，改任意一类都会让另外两类的文件名（内容哈希）一起变，客户端缓存全废。
 *   · echarts 只出现在图表页，单独成块后才能和首屏分开（见 preloadFirstRoute 里的说明）。
 */
const VENDOR_CHUNKS: ReadonlyArray<{ name: string; test: RegExp }> = [
  {
    // echarts 全家（含它独占的 tslib）单独成块，只被 chartRuntime 的动态 import 引用，
    // 首屏 / 公共 chunk 里不出现（fix_plan 第 6 批第 4 条）。
    // tslib 只有 echarts / zrender 依赖（node_modules 里逐个 package.json 核对过）：
    // 不显式归进来时 rollup 可能把它放进入口共享 chunk，入口就会静态 import 这一块。
    // 以后若有首屏依赖也用到 tslib，要把 tslib 挪到 vendor-vue，否则 echarts 块会被拖进首屏。
    name: 'vendor-echarts',
    test: /[\\/]node_modules[\\/](?:echarts|zrender|vue-echarts|tslib)[\\/]/,
  },
  {
    // naive-ui 这一块只装它自己的**运行时依赖**：CSS-in-JS（css-render / seemly）、
    // 焦点与键盘（vueuc / evtd / vdirs）、尺寸与虚拟化工具（treemate / vooks / vwave）。
    // 这些被所有 naive 组件共享、几乎不随业务代码变，是缓存收益最高的一块。
    //
    // naive-ui 自己的组件（data-table / date-picker / select / options …）**故意不并进来**：
    // 一旦所有 naive 模块合成一个 chunk，这个 chunk 就成了入口的静态依赖，
    // 只有个别页面用的组件会被拖进首屏 —— 实测首屏传输 358KB → 584KB、mobile LCP 4.2s → 5.2s。
    // 组件继续交给 rollup 按页面异步切分（产物里就是 DataTable / DatePicker / Select 这些按需 chunk）。
    name: 'vendor-naive',
    test:
      /[\\/]node_modules[\\/](?:vueuc|seemly|css-render|@css-render|evtd|treemate|vdirs|vooks|vwave)[\\/]/,
  },
  {
    name: 'vendor-vue',
    test: /[\\/]node_modules[\\/](?:vue|@vue|vue-router|pinia|@vueuse)[\\/]/,
  },
]

function manualChunks(id: string): string | undefined {
  for (const group of VENDOR_CHUNKS) {
    if (group.test.test(id)) return group.name
  }
  return undefined
}

/** 构建产物里被 rollup 切出来的一个 chunk（只取这里用得到的字段）。 */
interface BuiltChunk {
  type: 'chunk'
  fileName: string
  name: string
  isEntry?: boolean
  isDynamicEntry?: boolean
  imports: string[]
  code: string
}

/** 首屏 modulepreload 黑名单：echarts 一族只在图表真正要画时才下载。 */
const EXCLUDED_PRELOAD = /vendor-echarts|chartRuntime/

/**
 * 把首屏路由的 chunk 写进 index.html 的 `<link rel="modulepreload">`。
 *
 * 默认页（/overview）的 chunk 是路由动态 import 的，浏览器要等 index.js 下载、执行、
 * 路由解析完才知道要下载它；那之前的每一跳都是串行的 RTT。提前写进 HTML 之后，
 * 它和 index.js 并行下载，只是把"本来就要下的文件"提前到更早的时间点，不增加总字节。
 *
 * 只对入口 index.html 生效（login.html 是另一条入口，不预载管理台的路由）。
 */
function preloadFirstRoute(routeChunkName: string, minBytes: number): Plugin {
  let base = '/'
  return {
    name: 'mh-preload-first-route',
    apply: 'build',
    configResolved(config) {
      base = config.base
    },
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!/(^|\/)index\.html$/.test(ctx.path)) return html
        const bundle = ctx.bundle
        if (!bundle) return html
        const chunks = (Object.values(bundle) as unknown as BuiltChunk[]).filter(
          (file) => file.type === 'chunk',
        )
        const byName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]))
        const route = chunks.find((chunk) => chunk.isDynamicEntry && chunk.name === routeChunkName)
        if (!route) return html

        const already = new Set<string>()
        for (const match of html.matchAll(/<link[^>]+rel="modulepreload"[^>]*href="([^"]+)"/g)) {
          already.add(match[1])
        }

        const picked: string[] = []
        const seen = new Set<string>()
        const walk = (chunk: BuiltChunk): void => {
          for (const dep of chunk.imports) {
            if (seen.has(dep)) continue
            seen.add(dep)
            const file = byName.get(dep)
            if (!file) continue
            // 先递归依赖，保证依赖排在引用它的 chunk 前面。
            walk(file)
            // 入口 chunk 自己已经是 <script type="module">，再写一条只是重复请求。
            if (file.isEntry) continue
            // 图表库永远不进首屏 modulepreload（第 4 轮防回归：即便将来某个首屏 chunk 静态引用了它）。
            if (EXCLUDED_PRELOAD.test(file.name || dep)) continue
            const href = base + dep
            if (already.has(href) || file.code.length < minBytes) continue
            picked.push(href)
          }
        }
        walk(route)

        const routeHref = base + route.fileName
        if (!already.has(routeHref)) picked.push(routeHref)
        if (picked.length === 0) return html

        const tags = picked
          .map((href) => `<link rel="modulepreload" crossorigin href="${href}">`)
          .join('\n    ')
        return html.replace('</head>', `    ${tags}\n  </head>`)
      },
    },
  }
}

/**
 * 字体预加载（11-polish-motion §2「字体：font-display: swap，Plex 拉丁子集 preload」）。
 *
 * 字体文件来自 @fontsource（main.ts 引入，CSS 里已是 `font-display: swap`），构建后文件名带哈希，
 * 不能在 index.html 里手写 href —— 这里在产物里按名字前缀找到 woff2，把 index.html 里的
 * `<!-- mh:font-preload -->` 占位替换成 `<link rel="preload" as="font" type="font/woff2" crossorigin>`。
 * 只预加载正文最常用的两档（Plex 400 / 500 拉丁子集，各 ≈23KB）；600 与 JetBrains Mono 交给 swap。
 * dev 下没有 bundle，占位原样留着（是注释，无副作用）。
 */
function preloadFonts(prefixes: readonly string[]): Plugin {
  let base = '/'
  return {
    name: 'mh-preload-fonts',
    apply: 'build',
    configResolved(config) {
      base = config.base
    },
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!/(^|\/)index\.html$/.test(ctx.path)) return html
        const bundle = ctx.bundle
        if (!bundle) return html
        const files = Object.values(bundle) as Array<{ type: string; fileName: string }>
        const tags: string[] = []
        for (const prefix of prefixes) {
          const hit = files.find((file) => {
            if (file.type !== 'asset' || !file.fileName.endsWith('.woff2')) return false
            const name = file.fileName.split('/').pop() || ''
            return name.startsWith(prefix + '-') || name === prefix + '.woff2'
          })
          if (hit) {
            tags.push(`<link rel="preload" as="font" type="font/woff2" crossorigin href="${base + hit.fileName}">`)
          }
        }
        const block = tags.join('\n    ')
        if (html.includes(FONT_PRELOAD_MARK)) return html.replace(FONT_PRELOAD_MARK, block)
        return block ? html.replace('</head>', `    ${block}\n  </head>`) : html
      },
    },
  }
}

const FONT_PRELOAD_MARK = '<!-- mh:font-preload -->'

/**
 * 把入口 CSS 内联进 index.html，消掉 render-blocking 的样式请求。
 *
 * Vite 默认把入口 CSS 产出成 <link rel="stylesheet">：浏览器必须等它下载 + 解析完
 * 才能首次绘制，两个文件各占一跳 RTT（Lighthouse mobile 实测 wastedMs 330 + 180）。
 * 入口 CSS 只有几 KB（tokens + base + 字体 face + 外壳组件样式），内联进 HTML 后
 * 跟着文档一起到达，不再单独阻塞；页面级异步 CSS 仍按 chunk 懒加载。
 *
 * 只对 index.html 生效：login.html 是另一条入口，样式集不同。
 */
function inlineEntryCss(): Plugin {
  const LINK = /<link[^>]+rel="stylesheet"[^>]*>/g
  const HREF = /href="([^"]+)"/
  return {
    name: 'mh-inline-entry-css',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!/(^|\/)index\.html$/.test(ctx.path)) return html
        const bundle = ctx.bundle
        if (!bundle) return html
        const byFileName = new Map<string, { source: unknown }>()
        for (const file of Object.values(bundle) as Array<{ type: string; fileName: string; source?: unknown }>) {
          if (file.type !== 'asset') continue
          byFileName.set(file.fileName, file as { source: unknown })
          byFileName.set(file.fileName.split('/').pop() || file.fileName, file as { source: unknown })
        }
        return html.replace(LINK, (tag) => {
          const href = HREF.exec(tag)?.[1]
          if (!href) return tag
          // href 形如 /admin/v2/assets/index-xxxx.css —— 只取文件名去匹配产物。
          const name = href.split('/').pop() || ''
          const asset = byFileName.get(name)
          if (!asset) return tag
          const css =
            typeof asset.source === 'string'
              ? asset.source
              : new TextDecoder('utf-8').decode(asset.source as Uint8Array)
          return '<style data-mh-entry-css>' + css + '</style>'
        })
      },
    },
  }
}

export default defineConfig({
  base: '/admin/v2/',
  plugins: [
    vue(),
    preloadFirstRoute('OverviewPage', 4096),
    inlineEntryCss(),
    preloadFonts(['ibm-plex-sans-latin-400-normal', 'ibm-plex-sans-latin-500-normal']),
  ],
  resolve: {
    alias: {
      '@': '/src',
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: 'index.html',
        login: 'login.html',
      },
      output: {
        manualChunks,
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: false },
      '/admin/assets': { target: API_TARGET, changeOrigin: false },
      '/static': { target: API_TARGET, changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    // UI mount + i18n 切换在满载并行时会超过默认 5s；20s 只放宽上限，不放宽断言。
    testTimeout: 20000,
    setupFiles: ['./tests/setup.ts'],
    // 第 5 轮起界面时间按浏览器本地时区显示；测试固定在 UTC+8，断言不随 CI 机器时区漂移。
    env: { TZ: 'Asia/Shanghai' },
    include: ['tests/**/*.spec.ts', 'src/**/*.spec.ts'],
    css: false,
  },
})
