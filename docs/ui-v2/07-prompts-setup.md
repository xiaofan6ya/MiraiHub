# 07 执行提示词：公共前缀 + 基建（S01–S05）

用法：每一步开一个新会话（或清空上下文），把 **P00 公共前缀 + 该步提示词** 一起发给执行模型。
一步做完、验收通过、提交 git 后再做下一步。步骤之间不要合并（低成本模型一次做太多容易走偏）。

---

## P00 公共前缀（每一步都带上）

```
你在为 MiraiHub（一个 FastAPI 写的 LLM 网关）重写管理后台前端。仓库根目录就是当前目录。

必读（每次开始前先读）：
- docs/ui-v2/README.md（关键决策，不许改）
- docs/ui-v2/01-direction.md（视觉规范）
- docs/ui-v2/05-patterns.md（接口调用、页面模板、状态、格式化、文案）
- 本步骤提到的其他 docs/ui-v2/*.md

硬性规则：
1. 新前端代码只写在 admin/ui/ 下。不要修改 admin/console/、admin/static/ 下的旧前端文件；
   除非本步骤明确要求，不要改任何 Python 后端文件。
2. 后端接口一个都不改。接口形状以旧前端的实际调用和 admin/routers/*.py、admin/platform/**/routers/*.py 的处理函数为准。
   不确定字段含义时去读后端处理函数，不要猜。
3. 只用这些依赖：vue@3、vue-router@4、pinia、naive-ui、vue-echarts + echarts、lucide-vue-next、
   marked、dompurify、@fontsource/ibm-plex-sans、@fontsource/jetbrains-mono、
   开发依赖 vite、@vitejs/plugin-vue、typescript、vue-tsc、vitest、@vue/test-utils、jsdom。需要别的依赖先停下来问。
4. 不用任何 CDN；不引入 Tailwind 和 Font Awesome。
5. 颜色、字号、间距、圆角只能用 admin/ui/src/styles/tokens.css 里的 CSS 变量（或由它生成的 Naive UI 主题），
   页面和组件里不许出现裸的十六进制颜色值。
6. 页面里只用 admin/ui/src/components/ 下的公共组件和 Naive UI 组件；禁止在页面里直接 fetch，必须走 src/api/。
7. 所有界面文字用简体中文，遵守 01-direction.md §8 的文案规则。旧版有的说明文字要保留（可放进 HelpTip）。
8. 功能不许比旧版少。拿不准时按旧版行为 1:1 复刻，并在交付说明里列出来。
9. 每步结束必须运行：cd admin/ui && npm run typecheck && npm run test && npm run build，全部通过。
   报告时贴出这三条命令的最后几行输出。失败就修，修不好如实说明。
10. 结束时用 git 提交本步改动，提交信息格式：feat(ui-v2): S0x <本步标题>。
11. 交付说明（中文，简短）：做了什么、和设计稿不一致的地方及原因、发现的旧版功能但未实现的（必须为空，否则说明原因）。
```

---

## S01 项目脚手架与设计令牌

```
【S01 项目脚手架与设计令牌】
读：docs/ui-v2/tokens.css、docs/ui-v2/04-components.md §「图表 Chart」。

任务：
1. 在 admin/ui/ 建 Vite + Vue 3 + TypeScript 项目（手写文件，不要用交互式脚手架）：
   package.json（scripts: dev, build, preview, typecheck= vue-tsc --noEmit, test= vitest run）、
   vite.config.ts、tsconfig.json、index.html、src/main.ts、src/App.vue、src/env.d.ts。
   - vite.config.ts：base: '/admin/v2/'；build.outDir: 'dist'；build.assetsDir: 'assets'；
     多入口 input: { index: 'index.html', login: 'login.html' }（login.html 先放一个占位页面）；
     dev server 代理：'/api'、'/admin/assets'、'/static' → http://127.0.0.1:8790（端口从仓库 .env.example 核对，默认 8790）。
   - 锁定依赖版本（package.json 写确切版本，生成 package-lock.json）。
2. 把 docs/ui-v2/tokens.css 原样复制到 admin/ui/src/styles/tokens.css；新建 src/styles/base.css
   （body 去边距、滚动条样式细 8px 颜色 --border-strong、::selection 用 --brand-soft、链接色 --brand）。
   在 main.ts 引入 @fontsource/ibm-plex-sans/400.css、500.css、600.css 和 @fontsource/jetbrains-mono/400.css、500.css
   （只要 latin 子集：若包默认含多子集，改为引入 latin-*.css 文件）。
3. src/theme/：
   - useTheme.ts：主题 'light' | 'dark' | 'system'，存 localStorage 'mh.theme'；首次无值时读取旧版键 localStorage 'wb_console_theme'（值 dark/light）
     作为初始值；把实际主题写到 <html data-theme>；监听系统深浅色变化。
   - naiveTheme.ts：从 CSS 变量生成 Naive UI 的 GlobalThemeOverrides（primaryColor=--brand、primaryColorHover=--brand-hover、
     successColor=--ok、warningColor=--warn、errorColor=--danger、borderRadius=7px、fontFamily=--font-sans、
     fontFamilyMono=--font-mono、fontSize=14px、bodyColor=--bg、cardColor=--surface、modalColor/popoverColor=--surface、
     borderColor=--border、textColor1/2/3=--fg/--fg-2/--fg-muted、tableHeaderColor=--surface-2、tableColorHover=--surface-hover），
     用 getComputedStyle 读取，主题切换后重新计算。深色时同时传 darkTheme。
   - echartsTheme.ts：从 CSS 变量生成 ECharts 主题对象（规则见 04-components「图表 Chart」），导出 registerChartTheme()。
4. App.vue：NConfigProvider（zhCN、dateZhCN、theme、themeOverrides）+ NMessageProvider + NDialogProvider + NLoadingBarProvider
   包裹 <router-view/>。
5. 建一个临时页面 src/pages/_Playground.vue（路由 '/_playground'），展示：所有令牌色块（浅/深两套）、字号阶梯、
   Naive UI 的按钮/输入框/表格/开关在两种主题下的样子，以及一个主题切换按钮。这页用来人工检查令牌是否生效。
6. 仓库根 .gitignore 已配置好：admin/ui/node_modules 忽略，admin/ui/dist、docs/ui-v2/**、tests/test_ui_v2_serving.py 入库。
   用 git add -n admin/ui docs/ui-v2 确认 dist 会被加入、node_modules 不会；不要改 .gitignore。
7. vitest 配置 jsdom 环境；写一个测试：useTheme 设置 'dark' 后 document.documentElement.dataset.theme === 'dark'。

验收：npm run dev 打开 http://localhost:5173/admin/v2/#/_playground 能看到浅/深色正确切换，字体为 IBM Plex Sans；
typecheck/test/build 通过；dist/ 里有 index.html、login.html、assets/。
```

---

## S02 后端挂载 /admin/v2（唯一的后端改动）

```
【S02 后端挂载 /admin/v2】
读：admin/server.py 中这些位置：约 175–245 行（中间件、安全头、CSP）、964 行附近 CONSOLE_DIR、
1007–1025 行 admin_index、1230–1252 行 console_asset、_console_allowed 的定义。

任务（只改 admin/server.py，新增代码放在 console_asset 函数之后，风格与周围一致，中文注释）：
1. 新增常量 UI_DIST = Path(__file__).resolve().parent / "ui" / "dist"。
2. 新增路由：
   - GET /admin/v2 与 /admin/v2/：未通过 _console_allowed(request) → RedirectResponse("/admin", 303)；
     通过 → 返回 UI_DIST/"index.html"，头 Cache-Control: no-store, no-cache, must-revalidate；文件不存在 → 404 并在日志里说明「新版控制台未构建」。
   - GET /admin/v2/{file_path:path}：未登录 → 404（与 console_asset 一致，不暴露存在性）；
     做与 console_asset 相同的路径穿越校验；文件存在 → FileResponse；
     路径以 assets/ 开头（Vite 带哈希的文件名）→ Cache-Control: public, max-age=31536000, immutable，其它 → no-cache；
     文件不存在且不是 assets/ 下 → 返回 index.html（SPA 回退）。
   注意路由注册顺序：两条都要在任何会吞掉 /admin/v2 的通配路由之前。
3. 检查 175–245 行的中间件：凡是对 "/admin" 页面与 "/admin/assets" 区别对待的逻辑（如第 228 行附近），
   让 /admin/v2/assets/ 与 /admin/assets 同等对待，/admin/v2 页面与 /admin 页面同等对待。逐条说明你改了什么、为什么。
4. 检查 CSP：确认 Vite 产物（外链 module script、CSS、woff2 字体、png 图片、ECharts canvas、Naive UI 注入的 <style>）
   在现有 CSP 下都能工作（script-src 'self'、style-src 'self' 'unsafe-inline' 已有；若缺 font-src / img-src 'self' data: 等，
   只在新路由的响应上补齐，不放宽旧路由）。把最终生效的 CSP 字符串写进交付说明。
5. 在 tests/ 新增 tests/test_ui_v2_serving.py（参照现有测试的写法，用 FastAPI TestClient）：
   未登录访问 /admin/v2 → 303 到 /admin；未登录访问 /admin/v2/assets/x.js → 404；
   路径穿越 /admin/v2/../server.py → 404；登录后（参照现有测试如何构造登录态）/admin/v2 → 200 且 HTML。
   测试里用临时目录伪造 UI_DIST（monkeypatch），不依赖真实构建。
6. 运行 python -m pytest tests/test_ui_v2_serving.py -q 并通过；再运行全部测试 python -m pytest -q，确认没有打破已有测试。

本步不需要跑 npm 命令（若 admin/ui 已存在，也跑一次 npm run build 确认能被新路由服务）。
验收：启动服务（python main.py 或项目惯用方式），登录旧后台后访问 http://127.0.0.1:8790/admin/v2/#/_playground 能看到 S01 的页面。
```

---

## S03 接口层与全局状态

```
【S03 接口层与全局状态】
读：docs/ui-v2/05-patterns.md §1、admin/console/secure-fetch.js、admin/console/platform-switch.js（全文）、
admin/console/console-app.js 中处理 401/登录态的代码（搜索 "401"、"#login"、"logout"、"showLogin"）、
顶栏服务状态 topbarMode 的更新逻辑（搜索 "topbarMode"）。

任务：
1. src/api/http.ts：按 05-patterns §1 实现 http / download / stream、ApiError。401 时调用 auth store 的 onUnauthorized
   （跳到 /admin?next=<当前 hash 编码>；若旧后端不支持 next 参数，就只跳 /admin）。
   单元测试：CSRF 头只加在非安全方法上；错误信息解析（字符串 detail、校验错误数组、message、无 JSON 四种情况）。
2. src/api/session.ts：当前登录态检查、登出（与旧版 logout() 调用相同接口与方法）、登录设备列表/踢出/拉黑（旧版 openSessionsPanel 用的接口）。
3. src/api/platforms.ts + src/stores/platform.ts（Pinia）：
   - 完整复刻 platform-switch.js 的「当前平台」语义：平台列表从哪个接口来、字段有哪些、当前平台如何选定和持久化
     （localStorage 键名、是否同时写后端）、切换时旧版派发了什么事件。
   - store 暴露：platforms、current、currentId、setCurrent(id)、onChange(cb)（返回取消函数）、reload()。
   - 在 docs/ui-v2/contracts/platform-context.md 写下：平台对象的完整字段表（字段名、类型、含义、来自哪个后端函数）、
     持久化方式、旧版哪些面板在切换平台后重渲染（这个列表决定顶栏平台选择器在哪些页面显示）。
4. src/stores/heartbeat.ts：复刻旧版顶栏「服务已连接」的判定方式，每 15 秒一次，document.hidden 时暂停；
   暴露 status: 'ok' | 'down' | 'reconnecting'、lastBeatAt、以及侧栏提示点需要的数据（若旧版没有现成接口提供
   「冷却账号数 / 批次封控」，就先不做提示点，在交付说明里写明）。
5. src/composables/：usePolling、useCopy（navigator.clipboard，失败时回退 textarea + execCommand）、useConfirm（基于 NDialog，
   返回 Promise<boolean>，支持 danger 和 requireText 选项）、useToast（包 useMessage，success 2.5s / error 6s 可关闭）、
   useTableQuery（分页/排序/筛选 ↔ 路由 query）。
6. src/utils/format.ts：实现 05-patterns §4 全部函数，并写单元测试覆盖表中示例。
   fmtCompact 的进位规则要与旧版一致：在 console-app.js 搜索生成 "K"/"M" 的格式化函数，对照实现，测试里用旧版实际输出做期望值。

验收：typecheck/test/build 通过；contracts/platform-context.md 内容完整（每个字段都标了来源）。
```

---

## S04 外壳：布局、导航、顶栏、路由、命令面板

```
【S04 外壳】
读：docs/ui-v2/03-shell.md（全文，严格照做）、docs/ui-v2/04-components.md、admin/console/index.html 第 1178–1243 行、
console-app.js 中 activateTab 与生成 topbarSubnav 的代码（核对 24 个页面的中文名和顺序）、admin/console/desktop.js（WBDesktop 暴露的能力）。

任务：
1. src/router/index.ts：hash 模式；24 个路由按 03-shell §1 的表，每个路由组件先用占位页 src/pages/_Placeholder.vue
   （显示页面名 +「建设中」+ 旧版位置）。meta: { group, title, icon, platformScoped }，platformScoped 依据 contracts/platform-context.md。
   启动时处理旧链接 ?tab=xxx → /xxx。未知路由 → /overview。
   导出 groups 定义（9 组、图标、顺序），侧栏、页签、命令面板都从这里生成。
2. src/layout/AppShell.vue、SideNav.vue、TopBar.vue、PageHeader.vue、PlatformPicker.vue、CommandPalette.vue、MobileDrawer.vue：
   尺寸、颜色、交互全部按 03-shell §2–§3。要点：
   - 侧栏分组点击回到该组上次访问页（localStorage 'mh.lastTab.<group>'）；1024–1279px 默认收起；收起状态可切换并持久化。
   - 平台选择器只在 meta.platformScoped 的页面显示，其余页面显示面包屑。
   - 命令面板 Ctrl/⌘+K，页面与操作两组，键盘可操作。操作组先放：复制 API 地址、切换主题、切换平台（子列表）、退出登录；
     「添加账号」「创建 Key」在对应页面完成后再注册（提供 registerCommand API）。
   - 退出登录、登录设备抽屉（P25，见 06-pages-d）在本步完成。
   - 版本号与「有更新」：数据来源与旧版 appVersion / WBDesktop.openUpdates 相同；点击跳 /maintenance。
   - 桌面端：src/platform/desktop.ts 封装 window.pywebview 的调用（只在存在时启用），顶栏加 pywebview-drag-region class。
3. PageHeader：标题、HelpTip、右侧 actions 插槽、同组页签（同组 >1 页时）。
4. 响应式四档全部实现；≤1023px 汉堡菜单抽屉。
5. 测试：路由表有 24 个页面且 key 与旧版 TABS（console-app.js:1264）完全一致；?tab=keys 会被重定向到 /keys。

验收：在 /admin/v2/ 下能点遍 24 个占位页；浅/深色、四个断点下截图自查（用浏览器开发者工具切宽度），把发现的问题修掉。
```

---

## S05 公共组件库

```
【S05 公共组件】
读：docs/ui-v2/04-components.md（全文，严格照做）、admin/console/platform-icons.js、admin/console/model-icons.js、admin/console/model-vendor.css。

任务：
1. 实现 04-components 列出的全部组件：Btn、IconBtn、Card、Section、Badge、StatusDot、Chip、Copyable、HelpTip、Empty、ErrorBlock、
   StatCard、StatStrip、DataTable（含手机卡片模式、BulkBar、列设置、密度切换）、FilterBar、Segmented、Meter、StackMeter、Ring、
   Chart、BarList、FormDrawer、ConfirmDialog（通过 useConfirm）、DetailDrawer、KV、JobProgress（+ useJob）、CodeBlock、DateRange、
   RouteRibbon、PlatformLogo、ModelIcon。
2. PlatformLogo / ModelIcon：移植旧版映射逻辑；把 admin/console/logos/ 下文件复制到 admin/ui/public/logos/（不要删旧文件）。
3. RouteRibbon 按 04-components 的规格用 SVG 自绘：线宽公式、颜色规则、首选平台高亮、流量脉冲（requestAnimationFrame 或 SVG animateMotion，
   prefers-reduced-motion 时关闭）、<640px 退化为条形列表、空数据状态。
4. 把 _Playground 页扩展成组件展示页：每个组件至少一个示例，含空/加载/错误状态；RouteRibbon 用 6 个假平台数据。
5. 单元测试：fmt 已在 S03；本步测 DataTable 手机模式按 mobile 标注渲染、useConfirm 返回值、RouteRibbon 线宽计算函数（导出为纯函数单测）、
   Badge tone 映射。

验收：_Playground 页在浅/深色、桌面/手机宽度下逐个检查，截图自查并修复对齐、溢出、对比度问题。
```
