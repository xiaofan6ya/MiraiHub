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
    name: 'vendor-echarts',
    test: /[\\/]node_modules[\\/](?:echarts|zrender|vue-echarts)[\\/]/,
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
  plugins: [vue(), preloadFirstRoute('OverviewPage', 4096), inlineEntryCss()],
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
    include: ['tests/**/*.spec.ts', 'src/**/*.spec.ts'],
    css: false,
  },
})
