# 08 执行提示词：页面（S06–S30）

每个页面步骤 = **P00 公共前缀 + 下面的「页面通用提示词」（把 `<...>` 换成该步的值）+ 该步的「额外要求」**。
推荐顺序就是步骤编号顺序：先做结构简单、能验证组件库的页面，再做账号池这种复杂页。

---

## 页面通用提示词（模板）

```
【S<编号> 页面：<页面名> <路由>】
读：docs/ui-v2/<设计稿文件> 中的 <P编号> 一节（严格按布局实现）、docs/ui-v2/04-components.md、
旧版：admin/console/index.html 中 <旧版 section id> 一段，以及该面板用到的 JS（<旧版 JS 线索>）。

第一阶段：写功能契约（先写文档，不写代码）
在 docs/ui-v2/contracts/<页面key>.md 写清楚旧版这一页的全部功能，格式：
  ## 区块（按旧版出现顺序，写出原中文标题）
  ## 数据展示：每个统计数/表格列/图表 —— 中文标签、来源字段、格式（如 2.3M、百分比、着色规则）
  ## 交互：每个按钮/开关/筛选/行操作/批量操作 —— 中文文案、调用的接口、确认文字、成功和失败时旧版的提示
  ## 接口：METHOD 路径、参数、请求体、响应中用到的字段（字段名、类型、含义），调用时机（进入页面/轮询 N 秒/点击）；
         每个接口都要注明后端处理函数位置（文件:函数名），并核对后端实际返回的字段，旧前端没用到但对新设计有用的字段也列出来
  ## 跨页联动：平台上下文、跳转到其他页面、全局事件
  ## 边界情况：空数据、加载、错误、功能开关、按平台能力显示/隐藏、桌面端/手机端差异
  ## 旧版有但设计稿没写到的功能，以及你打算放在新版哪里
找功能时搜索 index.html 里该 section 的所有 id，再到 JS 中搜索这些 id 和 onclick 的函数名，逐个追到底，不要漏。

第二阶段：实现
1. src/api/<域>.ts：契约里的全部接口，带 TypeScript 类型。
2. src/pages/<PageName>.vue（必要时拆子组件到 src/pages/<page-key>/）：按设计稿布局，使用公共组件，替换路由表中的占位页。
3. 契约里每一项功能都要实现。实现完后在契约文件末尾加「## 实现对照」表：功能 | 新版位置 | 状态（已实现/有差异+原因）。
4. 四种状态（首次加载/刷新/空/错误）都要处理；轮询按 05-patterns §3。
5. 若页面依赖平台上下文，订阅 usePlatformStore().onChange 重新取数，卸载时取消。
6. 筛选条件与分页同步到路由 query（useTableQuery）。
7. 单元测试：至少覆盖本页的数据转换函数（接口响应 → 展示数据）各一个用例，用契约里记录的真实响应形状造测试数据。

第三阶段：自查
- 连上真实后端（npm run dev，代理到 8790）逐个点一遍功能，把旧版同一页并排打开对比数字是否一致。
- 浅色/深色、1440px/1024px/375px 三个宽度各看一遍，修掉溢出、换行、对齐问题。
- 在交付说明里列出：与旧版数字不一致的地方（应为空）、无法验证的功能（例如本地没有某平台账号）。
```

---

## 步骤表

设计稿编号所在文件：P1–P5 在 `06-pages-a.md`，P6–P9 在 `06-pages-b.md`，P10–P13 在 `06-pages-c.md`，P14–P26 在 `06-pages-d.md`。

| 步骤 | 页面 | 设计稿 | 旧版 section | 旧版 JS 线索 | 额外要求 |
|---|---|---|---|---|---|
| S06 | 更新与备份 `/maintenance` | 06-pages-d P24 | `#panel-maintenance` | desktop.js、release-notes.js、console-app.js 搜 maintenance | 最简单的设置型页面，用来跑通模板 C（左侧锚点目录 + 区块卡片 + 未保存提示 + 离开确认）。把模板 C 抽成 `src/layout/SettingsLayout.vue` 供后续复用。 |
| S07 | 同步设置 `/sync` | P19 | `#panel-sync` | 搜 `/api/sync` | 复用 SettingsLayout。 |
| S08 | 预警管理 `/alerts` | P20 | `#panel-alerts` | 搜 `/api/alerts` | 渠道类型切换时动态字段；「发送测试」显示结果 Toast；SMTP 密码留空不修改。 |
| S09 | 分组管理 `/groups` | P16 | `#panel-groups` | 搜 `/api/groups` | 第一个列表型页面：把模板 B 的通用骨架（页头 + FilterBar + DataTable + FormDrawer + 删除确认）做顺，后续照抄。 |
| S10 | IP 管理 `/ip` | 06-pages-c P13 | `#panel-ip` | ip-management.js、usage-ip.js、搜 `/api/ip` | 三个页签用 `?view=active|blocked|devices`。 |
| S11 | 定时任务 `/schedules` | P17 | `#panel-schedules` | 搜 `/api/schedules`、date-time-picker.js、`/api/proxies` | 显示「下次执行」时间；立即执行后刷新该行最近结果。 |
| S12 | 模型配置 `/models` | P14 | `#panel-models` | model-pool.js、model-metadata.js、model-icons.js、搜 `/api/models` | 模型 ID 三态排序；手机模式 c-block 等价处理。 |
| S13 | 模型广场 `/market` | P15 | `#panel-market` | market.js、market.css | 卡片/列表与时间范围持久化。 |
| S14 | API Keys `/keys` | P12 | `#panel-keys` | key-ip.js、搜 `/api/keys`、`/api/models` | 对接信息卡 + 接入指南抽屉（5 个页签，curl 内容从旧版 `<details>` 迁移，地址/模型自动填充）；创建 Key 表单所有字段；完整 Key 显示行为与旧版一致；注册命令面板操作「创建 Key」。 |
| S15 | 用量日志 `/logs` | P11 | `#panel-logs` | 搜 `/api/logs`、logSummary、请求详情缓存相关函数 | 按账号汇总可折叠且行点击即按账号筛选；详情抽屉；导出/导出诊断走 download()；快速翻页开关。支持从其他页带 query 跳入（model/key/result/account/platform/时间范围）。 |
| S16 | 使用记录 `/usage` | P10 | `#panel-usage` | usage-ip.js、搜 `/api/stats`、usageAccountChart、usageResultChart | 饼图全部改 BarList；BarList 行点击跳 `/logs` 并带筛选。 |
| S17 | 数据看板 `/stats` | 06-pages-a P3 | `#panel-stats` | dashboard.js、dashboard.css | 按 dashboard.js 实际有的图表实现；在契约里列出每个图表的数据计算方式。 |
| S18 | 风控哨兵 `/sentinel` | P2 | `#panel-sentinel` | 搜 `/api/sentinel`、traffic.js | 实时请求 2 秒轮询；流量用 RouteRibbon；止损操作需确认。 |
| S19 | 平台网关 `/gateway` | P5 | `#panel-gateway` | 搜 `/api/gateway`、`/api/platforms` | 策略单选卡；顺序列表内联改优先级 + 优先级策略下拖拽排序（用原生 HTML5 拖拽或简单的 pointer 事件实现，不引入新依赖）；调度预演。 |
| S20 | 平台管理 `/platforms` | P4 | `#panel-platforms` | platform-actions.js、platform-tiers.js、traffic.js、platform-switch.js | 平台卡片网格；平台特有操作从 platform-actions.js 的声明移植到 `src/platforms/`；导入/导出/扫描全部（后台任务用 JobProgress）。 |
| S21 | 概览 `/overview` | P1 | `#panel-overview` 与 `#overview` | dashboard 相关、搜 `/api/overview`、data-ov-days | 首屏英雄（剩余额度）+ 用量 StatStrip + 今日流量 RouteRibbon + 账号状态 + 缓存命中；告警横幅。 |
| S22 | 积分流水 `/journal` | 06-pages-b P8 | `#panel-journal` | 搜 `/api/credits`、journalPrev | 游标分页与旧版一致。 |
| S23 | 积分到期 `/credits` | P7 | `#panel-credits` | 搜 `/api/credits`、creditRoutingSave | 确认选号策略与 gateway 是否同一后端设置，是则共用 store；重新采集用 JobProgress。 |
| S24 | 成长计划 `/growth` | P9 | `#panel-growth` | growth_center.js、growth_center.css、搜 `/api/growth`、`/api/growth-center` | 只换外观，保留全部动作。 |
| S25 | 账号池 `/accounts`（上） | P6 | `#panel-accounts` | console-app.js 账号相关（搜 `/api/accounts`、accountJobStatus、data-account-filter、data-account-batch）、platform-actions.js、profile-transfer.js、各平台 `/api/<平台>/*` | **本步只做**：契约（要覆盖所有平台的差异，按平台列表格）、表格、状态页签、搜索、行操作、详情抽屉、批量工具条、后台任务进度。 |
| S26 | 账号池 `/accounts`（下） | P6 | 同上 | 同上 + phone-session.js（区号/验证码流程） | **本步只做**：添加账号的全部方式（手机号登录、浏览器授权、手动添加、导入、扫描本机、导出），按平台能力显示；注册命令面板操作「添加账号」「导入账号」。对每个平台至少用真实或模拟数据走通一次；无法验证的平台在交付说明里列出。 |
| S27 | 客户端参数 `/client` | 06-pages-d P18 | `#panel-client` | 搜 `/api/client-profile`、`/api/client-paths` | SettingsLayout；依赖平台上下文。 |
| S28 | 设置 `/settings` + 公开注册入口 `/openentry` | P23、P22 | `#panel-settings`、`#panel-openentry` | 搜 `/api/runtime-config`、`/api/proxies`、`/api/app-source`、`/api/admin/password`、`/api/open`、`/api/join` | 两页可分两个会话做。运行配置的「重启后生效」标记；改密码前端校验；公开页文案实时预览。 |
| S29 | 智能体 `/agent` + 登录页 | P21、P26 | `#panel-agent`、admin/static/login.html | agent.js、agent.css | agent：流式输出、停止、Markdown 经 DOMPurify；login.html：字段与请求和旧版完全一致，独立入口（不依赖 Vue 也可以，但要用 tokens.css），登录成功后跳 `/admin/v2/`。 |

---

## S30 测试补齐、打包接入与切换

```
【S30 测试、打包、切换】
读：docs/ui-v2/09-acceptance.md（全文）。

任务：
1. 端到端冒烟脚本 admin/ui/scripts/smoke.mjs（只用 Node 内置 fetch，不加依赖）：给定 BASE_URL 与管理员密码，登录后
   请求 /admin/v2/ 与 index.html 引用的每个 assets 文件，确认 200；再按每个 contracts/*.md 记录的「进入页面时调用的 GET 接口」
   逐个请求，确认不是 4xx/5xx。仿照 tests/verify_stats_dashboard.cjs 的风格写报告输出。
2. 打包接入：在仓库中搜索 "admin/console" 和 "console" 在这些文件里的出现：Dockerfile、.dockerignore、docker-compose.yml、
   build.py、build.bat、desktop/miraihub.spec、desktop/app.py、android/app/build.gradle、android/python/、scripts/、.github/workflows/。
   凡是把 admin/console 打进产物的地方，同样把 admin/ui/dist 打进去（只要 dist，不要 node_modules 和 src）。
   逐条列出改动。若 .dockerignore 排除了 dist，加例外。
3. 在 BUILD.md 增加一节「新版控制台（admin/ui）」：如何安装依赖、开发、构建；说明 dist 已提交、打包时不需要 Node；
   以及修改前端后必须重新 npm run build 并提交 dist。
4. 切换（默认先不切，加开关）：在 admin/server.py 的 admin_index 中，读取环境变量 MIRAIHUB_CONSOLE（取值 v1|v2，默认 v1；
   同时在 .env.example 里加注释说明）。v2 且 UI_DIST/index.html 存在时：已登录 → 302 到 /admin/v2/；未登录 → 返回 UI_DIST/login.html
   （若不存在则回退旧 login.html）。v1 时行为与现在完全一致。在新版侧栏底部加「切回旧版」小链接（打开 /admin?ui=v1），
   旧版不动；后端在 ?ui=v1 时本次请求按 v1 处理。
   为切换逻辑补测试（tests/test_ui_v2_serving.py 中追加）。
5. 跑：cd admin/ui && npm run typecheck && npm run test && npm run build；python -m pytest -q；
   以及（服务运行时）node admin/ui/scripts/smoke.mjs。全部通过后提交。

交付说明里给出：如何开启新版（设置 MIRAIHUB_CONSOLE=v2 并重启），以及回退方法。
```
