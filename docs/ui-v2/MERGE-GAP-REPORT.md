# 后台前端合并 · 功能缺口报告

> 生成时间：2026-11-27
> 基线：`v2-ui` 分支快照 `501074e`（合并前）对比旧控制台 `admin/console`（`v1-console` 分支 `dc33e83`）
> 结论：**两套前端 25 个页面对 25 个 tab 一一对应，没有整页缺失**；缺口集中在页内能力，共 **86 项**（缺失 36 / 降级 50），其中建议优先补的严重项 **20 项**。

---

## 1. 本次合并做了什么

| 项 | 改动 |
|---|---|
| 路由 | `/admin` 唯一入口 → `admin/ui/dist`；未登录发同一份构建的 `login.html`，已登录发 `index.html` |
| 静态资源 | `/admin/assets/*` → `UI_DIST/assets/`（内容哈希，公开可读、一年 immutable） |
| 其余文件 | `/admin/{path}` → 产物文件 + SPA 回退（未登录 404，公开入口 `login.html`/`join.html`/`favicon.svg`/`logos/*` 放行） |
| 旧兼容 | `/admin/v2/*` → 301 `/admin/*`（老书签、发版公告、截图里的前缀） |
| 移除 | `_console_mode()`、`?ui=v1` 回退、`MIRAIHUB_CONSOLE` 开关、`/admin/assets/*` 旧控制台路由、资源 `?v=` 哈希盖章机制（`stamp_asset_urls` / `asset_digest` / `_console_shell` 等 7 个函数）、`console_asset` |
| 删除文件 | `admin/static/login.html`、`join.html`、`phone-session.js`、`phone-region.css` |
| 保留 | `admin/console/`（不再被任何路由服务，留作补缺口时的实现参考）、`admin/static/vendor/` |
| 构建 | `vite.config.ts` 的 `base: '/admin/v2/'` → `'/admin/'`；dev proxy 去掉旧 `/admin/assets` |
| 测试 | `tests/test_ui_v2_serving.py` 重写（18 例）、`tests/test_join_vue_entry.py` 适配（5 例），23 passed |

前台入口：`http://<host>/admin` · 注册：`/join` · 票据引导：`/j`

---

## 2. 缺口总览

| 域 | 缺失 | 降级 | 合计 | 严重 |
|---|---|---|---|---|
| 账号 / 平台 / 积分 / 成长 | 14 | 13 | 27 | 5 |
| 调用 / 日志 / Keys / IP / 看板 | 5 | 9 | 14 | 4 |
| 配置 / 模型 / 任务 / 客户端参数 / 系统 | 11 | 11 | 22 | 7 |
| 概览 / 智能体 / 注册页 / 外观 | 6 | 17 | 23 | 4 |
| **合计** | **36** | **50** | **86** | **20** |

判定口径：**缺失** = 旧版有入口/能力、新版完全没有；**降级** = 有但少了关键能力。已核实为「新版等价实现」或「后端端点已不存在（死代码）」的不计入。

---

## 3. 建议优先补的 20 项

排序依据：后端端点仍在（能力空转）+ 影响日常可用性。

### 3.1 一句话就能通（后端全在，前端只差接线）

| # | 功能 | 后端 | 新版缺什么 |
|---|---|---|---|
| 1 | 账号「重新登录」 | `POST /api/accounts/{id}/reauth`（`routers/accounts.py:1465`） | 无入口。凭证过期只能删号重加，Key 独占绑定、手动优先级、历史全丢 |
| 2 | Qoder 设备授权登录 | `/api/qoder/device/start|poll|claim`（`platform/qoder/routers/oauth.py:40/67/90`） | `PLATFORM_AUTH_FLOWS`（`api/account-add.ts:163`）只登记 10 个平台，无 qoder —— **Qoder 账号加不进来** |
| 3 | 智能体提示词增强 | `/api/agent/enhance`（`routers/agent.py:1055`） | `AgentComposer` 工具栏无「增强」按钮 |
| 4 | 智能体提示词预设 | bootstrap 下发 `presets`（`routers/agent.py:162,174`） | 类型声明了却没渲染，生图/生视频模板全丢 |
| 5 | 智能体长视频分段 | `/api/agent/plan`（`routers/agent.py:1168`） | 超过单段上限的视频无法规划分镜 |
| 6 | 模型拥堵程度 | `/api/models/busy`（`routers/models.py:1025`） | 无列、无徽标、无 50s 轮询 —— 选型失去「上游现在堵不堵」 |
| 7 | 模型使用统计面板 | `/api/stats/model-pool`（`routers/stats.py:41`） | 无调用 —— 「今天哪个模型还能用」的依据没了 |
| 8 | 解除模型冷却 | `POST /api/accounts/{id}/model-limits/clear`（`routers/accounts.py:1366`） | `ModelLimitsDrawer` 全文只读，误判时无法立即恢复 |
| 9 | 客户端手动路径卡片 | `GET/PUT/DELETE /api/client-paths/{platform}`、`POST /api/client-paths/refresh`（`routers/client_paths.py:58-82`） | 全 `ui/src` 无 `client-paths` 命中 —— 网络盘/绿色版找不到客户端时没有人工兜底 |
| 10 | 客户端全平台一键探测 | `POST /api/client-profile/detect-all`（`routers/client_profile.py:187`） | 无调用，只能逐平台点 |
| 11 | 客户端档案导出/导入 | `GET /api/client-profile/export`、`POST /import`（`client_profile.py:337/348`） | 无调用，线上探测不到客户端时搬家只能手抄 21 个字段 |
| 12 | 手机验证码会话恢复 | `GET /api/login/status/{sid}` | 关抽屉即丢会话，重开要重发短信 |
| 13 | Key 设备标识封禁 | `keys.py:450-458` 已返回 `devices` | `KeyIpsPayload` 类型无 `devices` —— Key 被共享换 IP 复用后没有封禁入口 |
| 14 | Key 来源 IP 封禁/解封 | `POST /api/ip/blocked`、`PATCH /api/ip/blocked/{id}` | `KeyIpsDrawer` 无 action 列，发现异常 IP 只能跳 IP 页重录 |

### 3.2 需要改结构、但缺口最大

| # | 功能 | 现状 |
|---|---|---|
| 15 | 每平台专属账号池表格 | 旧版 Trae/Qoder/Loomy/CodeArts/MiniMax 各有专属列与 4 张统计卡；新版统一 9 列通用表，`AccountItem` 里连 `addon_remain`/`plan`/`daily_balance`/`session_days_left` 字段都没有 |
| 16 | 账号池服务端分页 | 旧版 `page/page_size/search/status/sort/min_credits/max_credits` 全转 query；新版一次 `listAccounts({})` 拉全量，前端筛排 |
| 17 | 请求动态·按平台计数 | 后端 `sentinel_router.py:83-87` 仍下发 `traffic["platforms"]`；`LogsLive.vue` 只有 15 根分钟柱，看不到哪个平台在报错/超时 |
| 18 | 数据看板三块可视化 | 旧版有消耗分布（柱/面积可切）、调用分析四视图、性能健康条（含每模型成功率）；新版只剩趋势折线 + Token 柱 + 三张纯请求数 BarList |
| 19 | 注册页区号选择器 + 验证码约束 | 旧版自绘 listbox（方向键/`aria-*`/视口夹取）；新版退回原生 `<select>`，验证码只校验非空、无数字过滤。`need_password` 分支无提示文案 |
| 20 | 运行配置逐字段恢复默认 | 数字框清空得 `null` 会被 `diffRuntimeFields` 跳过，提交不了「清除覆盖」 |

---

## 4. 完整缺口清单 · 账号 / 平台 / 积分 / 成长（27）

### 缺失（14）

| # | 功能 | 旧版证据 |
|---|---|---|
| 1 | 账号「重新登录」reauth | `console-app.js:11977` openReauthAccount、`:11992` submitReauthAccount |
| 2 | 「注入本机客户端」inject | `console-app.js:9766` injectAcc（含「需重启客户端」二次确认） |
| 3 | 单账号导出登录态（`.info`/backup） | `console-app.js:11963` exportAcc |
| 4 | 单账号请求用量（日期区间 + 分页 + 上游明细） | `console-app.js:10530` viewRequestUsage、`:10543` doLoadRequestUsage |
| 5 | Qoder 设备授权（start→poll→claim） | `console-app.js:7923/7962/7982` |
| 6 | 平台级用量明细 + 逐号上游用量 | `console-app.js:9084` platformUsageDetail、`:9128` platformUsageOne、`:6302` traeUsageDetail |
| 7 | Trae「用量 Cookie」 | `console-app.js:6391` traeWebCookie |
| 8 | Loomy 邀请码面板 / 绑定 / 兑换 | `console-app.js:4081` loomyInvitePanel、`:4028` accInviteBind、`:4055` accRedeemCode |
| 9 | 失效复检 ban-recheck | `console-app.js:8385` platformBanRecheck |
| 10 | Loomy 新手任务 onboarding（dry-run→领取） | `console-app.js:8413` platformOnboarding |
| 11 | 平台级签到（整平台一次） | `console-app.js:8495` platformCheckin |
| 12 | 各平台连通性检查 `/accounts/{id}/test` | `console-app.js:5721/5535/5882` |
| 13 | 模型使用统计面板 | `model-pool.js:4/21`、`index.html:1827-1848` |
| 14 | 平台「平均级」徽标（有意删除） | `platform-tiers.js:98`、`platform-switch.js:117` |

### 降级（13）

| # | 功能 | 旧版 | 新版 |
|---|---|---|---|
| 1 | 账号列表分页 | 服务端分页（`console-app.js:4860/4863/5193`） | 全量拉取 + 前端筛排（`AccountsPage.vue:116/149`） |
| 2 | 每平台专属账号池表格 | `console-app.js:5403/5488/5597/5790/5930` | 统一 9 列（`AccountsTable.vue:215-296`） |
| 3 | 账号池顶部统计卡（4 张） | `console-app.js:5262-5280` | 只剩页头 meta 一行文本 |
| 4 | 「入库时间」列 | `console-app.js:5161` accCreatedCell | `AccountRow` 无 `createdAt` |
| 5 | 批量导出选项 | 格式×范围×分片×关键字×条数×剪贴板（`console-app.js:11729-11823`） | 两个直通按钮 |
| 6 | 模型额度与冷却（8 列 + 解除冷却 + 风险观测 + 模型健康表） | `console-app.js:4513-4548`、`:4553` | 3 列只读（`ModelLimitsDrawer.vue:82-88`） |
| 7 | 积分页手动调整选号优先级 | `console-app.js:11246` saveCreditOrder、`:11262` moveCreditAccount | 只有「恢复自动选号」 |
| 8 | 积分页「显示已禁用账号」开关 | `console-app.js:10827` | `includeDisabled` 支持但页面从不传 |
| 9 | 账号积分明细弹窗（4 卡/已用完折叠/来源标注） | `console-app.js:10479-10518` | 只有包列表（`AccountDetailDrawer.vue:126-181`） |
| 10 | 按账号猫猫旅行 | `console-app.js:9827/9818` | 只有整池 batch |
| 11 | 按账号平台任务按钮 | `console-app.js:4764-4781`（checkin/onboarding/activity） | 只按 `features.checkin` 出一个 |
| 12 | 平台卡动作 13 种 | `platform-actions.js:53-83`、`console-app.js:6154-6227` | 只执行 scan/import/export，其余 toast + 跳转 |
| 13 | 平台级「刷新额度」 | `console-app.js:9167` platformRefreshAll | 无入口 |

---

## 5. 完整缺口清单 · 调用 / 日志 / Keys / IP / 看板（14）

### 缺失（5）

| # | 功能 | 旧版证据 | 新版现状 |
|---|---|---|---|
| 1 | 请求动态按平台计数（请求/失败/中断拆分） | `console-app.js:16476-16500`、`index.html:2481` | `LogsLive.vue` 无平台聚合 |
| 2 | Key 关联设备标识（封禁/解封） | `key-ip.js:37/54/92` | `api/keys.ts:144-159` 无 `devices` 字段 |
| 3 | Key 当前在途表（IP/并发/模型/最早开始/状态） | `key-ip.js:34/56` | 只剩 `activeRequests` 一个数字（`KeyIpsDrawer.vue:80-85`） |
| 4 | Key 来源 IP 封禁/解封 | `key-ip.js:55/74-90` | 无 action 列 |
| 5 | 流量拓扑动画 | `traffic.js:76-333`，用于哨兵 `console-app.js:14834` 与网关 `:15128` | 全库无 `offset-path`/`topology`（0 命中） |

### 降级（9）

| # | 功能 | 旧版 | 新版 |
|---|---|---|---|
| 1 | 日志行客户端 IP 单元格（点击重查 + 复制） | `usage-ip.js:4-21` | 纯文本 + tooltip（`LogsTable.vue:204-215`） |
| 2 | IP 页 →「查看日志」钻取 | `usage-ip.js:9-14` | 参数名不一致：IP 页推 `client_ip`（`IpPage.vue:292`），日志页认 `ip`（`LogsPage.vue:68-81`）—— **一个词就能修** |
| 3 | 请求动态最近请求 7 列（含 stage 文案） | `console-app.js:16507-16554` | 4 列（`LogsLive.vue:122-135`） |
| 4 | 请求动态走 SSE | `console-app.js:16609` EventSource | 固定 5s 轮询（`LogsPage.vue:341-354`） |
| 5 | Key IP 抽屉时间窗/排序/分页/自刷 | `ip-management.js:8`、`key-ip.js:35/48/63/67` | 24h/7d/30d、无排序、无分页、无自刷 |
| 6 | 日志筛选模型/Key 输入联想 | `filter-combobox.js:4-60` | 纯文本 `NInput` |
| 7 | 用量日志页 RPM/TPM | `console-app.js:17466-17474` | 4 张 KPI 无 rpm/tpm |
| 8 | 对接卡内网地址探测反馈 | `console-app.js:12135-12144` | 静默 catch，用户不知道为什么少了几项 |
| 9 | 数据看板三块可视化 | `dashboard.js:8-12/28-29/360-405/443-450` | `StatsPage.vue:346-430` 只剩两张图 + 三张 BarList |

---

## 6. 完整缺口清单 · 配置 / 模型 / 任务 / 客户端参数 / 系统（22）

### 缺失（11）

| # | 功能 | 旧版证据 | 后端状态 |
|---|---|---|---|
| 1 | 客户端手动路径卡片 | `console-app.js:14383/14419/14436` | `client_paths.py:58-82` 在 |
| 2 | 客户端全平台一键探测 | `console-app.js:15312` | `client_profile.py:187` 在 |
| 3 | 客户端档案导出/导入 | `profile-transfer.js:15/24` | `client_profile.py:337/348` 在 |
| 4 | 客户端平台切换 chip + 只读探测字段 | `console-app.js:14278/14299/14455` | 在（`machine_id`/`accounts_found`/`can_import`/`live_user_data_dir`/`app_status`/`desktop_installed` 全 src 无匹配） |
| 5 | 模型拥堵程度 | `console-app.js:12981/13080/13092/13317/13371` | `models.py:1025` 在 |
| 6 | 模型显示开关（倍率/繁忙/官方同显） | `console-app.js:13031/13041` | `models.py:69/75` 在，`api/models.ts:239-245` 已封装但**零调用** |
| 7 | 模型广场「只看有故障时段」 | `market.js:401-415/556-563` | 前端筛选项 |
| 8 | 模型广场按时段可用率/按可用账号排序 | `market.js:416-434` | 前端排序 |
| 9 | 定时任务结果逐账号余额 + 今日额度已领 | `console-app.js:15861-15873` | 数据在响应里 |
| 10 | 解除模型冷却 | `console-app.js:4542/4553` | `accounts.py:1366` 在 |
| 11 | 手机验证码会话恢复 | `console-app.js:11354-11366` | `/api/login/status/{sid}` 在 |

### 降级（11）

| # | 功能 | 旧版 | 新版 |
|---|---|---|---|
| 1 | 模型配置排序维度（12 → 3） | `console-app.js:13332/13362-13391` | `filters.ts:116-141` |
| 2 | 模型配置单模型项（推理档位/排序/恢复上游默认） | `console-app.js:13140-13207/13238` | `ModelsPage.vue:426-436` 缺三件 |
| 3 | 模型配置聚合/平铺双视图 | `console-app.js:13426/13490/13251` | 固定「一行一模型」 |
| 4 | 模型配置平台过滤计数（可见/总数） | `console-app.js:13703-13732` | 单选下拉无计数 |
| 5 | 模型配置同步范围（当前平台 vs 全部） | `console-app.js:13831-13836` | 只同步全部 |
| 6 | 模型广场卡片指标（请求数/积分/可用账号/额度耗尽） | `market.js:349-376` | 换成成功率/TTFT/耗时/吞吐 |
| 7 | 模型广场汇总与图例 | `market.js:438-457` | 只剩总数/可用/请求 |
| 8 | 定时任务结果字段口径（30 键/千分位/时间格式/截断/失败清单） | `console-app.js:15780-15904` | 26 键，无格式化 |
| 9 | 客户端运行时诊断字段组来源标注 | `console-app.js:14639-14649` | 只有组名 + 头名 |
| 10 | 运行配置逐字段恢复默认 | `console-app.js:611-616` | `SettingsPage.vue:961-970` 提交不了清除 |
| 11 | 模型额度与冷却台账信息量（8 列 → 3 列） | `console-app.js:4513-4548` | `ModelLimitsDrawer.vue:82-88` |

---

## 7. 完整缺口清单 · 概览 / 智能体 / 注册页 / 外观（23）

### 缺失（6）

| # | 功能 | 旧版证据 | 新版现状 |
|---|---|---|---|
| 1 | 智能体提示词预设 | `agent.js:310-340` | `api/agent.ts:36-44` 有类型，页面 0 引用 |
| 2 | 智能体提示词增强 | `agent.js:984-1009` | 无封装、无按钮 |
| 3 | 智能体长视频分段 | `agent.js:429-445/1012-1047` | 只有单段参数 |
| 4 | 概览缓存分平台命中率表 | `console-app.js:2179-2195` | `by_platform` 只声明不渲染 |
| 5 | 浏览器端自检上报 | `boot-diag.js:1-22` | 全库无 `client-diag` |
| 6 | 启动看门狗 | `boot-guard.js:1-40` | 无（白屏无提示） |

### 降级（17）

| # | 功能 | 旧版 | 新版 |
|---|---|---|---|
| 1 | 智能体视频参数按模型能力渲染 | `agent.js:384-427` | 硬编码三组（`AgentSettingsCard.vue:51-56`） |
| 2 | 智能体单条消息 Token 用量 | `agent.js:665-691` | 只显示积分 |
| 3 | 智能体媒体产物下载入口 | `agent.js:693-711` | 无 `<a download>` |
| 4 | 注册页手机区号选择器 | `join.html:664` + `phone-session.js:30-89` | 原生 `<select>` |
| 5 | 注册页验证码约束（8 位/数字过滤/≥4 位） | `join.html:307/719-720/780-782` | 只校验非空、maxlength 10 |
| 6 | 注册页 `need_password` 提示文案 | `join.html:312-318/732-737` | 只切出输入框，无说明 |
| 7 | 注册页默认平台自动开局 + 短信冷却 ≥60s | `join.html:466/687` | 忽略 `default`、冷却下限 1s |
| 8 | 公开入口文案实时字数计数与硬上限 | `console-app.js:2872-2931` | 无计数、无 maxlength |
| 9 | 概览缓存命中/未命中构成条 | `console-app.js:2154-2176` | 只剩 3 条 KV |
| 10 | 概览异常账号明细列表 | `console-app.js:2306-2326` | 只有桶条 + 一句结论 |
| 11 | 概览平台矩阵（国内/国际分布 + 原地切换） | `console-app.js:2328-2367` | 换成流量健康行，点击跳页 |
| 12 | 数据看板平台筛选 | `dashboard.js:560/598-601` | `platform` 参数没传 |
| 13 | 数据看板 RPM/TPM/缓存读取独立 KPI | `dashboard.js:341-357` | 5 格，三项降级为提示文本 |
| 14 | 数据看板性能健康条（模型级成功率） | `dashboard.js:361-372` | 收进脚注，数值不显示 |
| 15 | 数据看板图表形态与维度切换 | `dashboard.js:381-412/448-453` | 固定两张图 |
| 16 | 哨兵信号明细「类型」与「细节」列 | `console-app.js:2496-2569` | 无类型列，细节退化为 tooltip |
| 17 | 主题切换波纹过渡 | `theme.js:1-160` | 直接写 `dataset.theme` |

---

## 8. 死代码清理清单（旧版仍在调、后端已无）

| 旧版调用 | 位置 | 后端 |
|---|---|---|
| `/api/doubao/login/*` 整套（qr/start、qr/poll、sms/*、cancel） | `console-app.js:18568-18698` | 平台已不存在 |
| 平台动作 `inject` / `keepalive` | `platform-actions.js:80/82` | 12 个平台 `actions` 声明都没有 |
| `/api/accounts/count` | `console-app.js:11735` | 存在（`accounts.py:595`），新版本地计数已覆盖 |
| `/api/accounts/batch` | 旧 WB 导入路径 | 存在，新版走 `/import-async` 已覆盖 |

---

## 9. 建议补的顺序

1. **先修 3 处参数/接线错位**（成本最低，立即见效）：
   - IP 页钻取参数 `client_ip` → `ip`（第 5 节降级 2）
   - 模型显示开关 `getModelDisplay/saveModelDisplay` 接线（第 6 节缺失 6）
   - 积分页 `includeDisabled` 传参（第 4 节降级 8）
2. **再做「后端已有、前端零调用」18 项**（第 3.1 节）：按端点聚合成 4 个 PR —— 账号稽核（reauth/inject/export/request-usage）、客户端探测（client-paths/detect-all/profile-transfer）、模型运维（busy/model-pool/clear）、智能体（presets/enhance/plan）。
3. **最后做结构性缺口**：每平台专属列、服务端分页、数据看板三块可视化、请求动态按平台聚合。这四项需要改数据结构与页面骨架，单独排期。

旧控制台源码 `admin/console/` 保留在原位未删，就是给第 2、3 步当实现参考用的（路由已不再服务它）。
