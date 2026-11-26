# 功能契约：成长计划页（Growth）

**页面**: P9 / S24 · 路由 `/growth`（页面 key `growth`，分组「账号」）
**设计稿**: `docs/ui-v2/06-pages-b.md` P9、`05-patterns.md` 模板 A/B 混合
**旧版**:
- `admin/console/index.html` `#panel-growth`（`#growthCenter` 容器）
- `admin/console/growth_center.js`（26KB：快照分区渲染 + 单次操作）、`growth_center.css`
- `admin/console/console-app.js`「成长计划」段（`/api/growth/*`：任务清单 / 做任务 / 领奖）
**后端**:
- 成长中心：`admin/routers/growth_center.py`（`GET /accounts`、`GET /accounts/{aid}`、`POST /accounts/{aid}/refresh`、`POST /accounts/{aid}/action`、`GET /accounts/{aid}/records`、`GET /accounts/{aid}/official-records`）+ `admin/growth_center.py`（快照分区 `SECTIONS`、白名单 `clean_section`）
- 成长任务：`admin/routers/growth.py`（`GET /accounts/{id}/tasks`、`POST /run-async`、`GET /job/{id}`、`POST /claim`、`POST /accept`、`GET /tasks`、`GET /plans`）+ `admin/growth_plans.py:classify`
- 平台能力：`router/pages.ts` 的 `PLATFORM_ONLY_TABS`（`growth → workbuddy`）由 store 的 `supports('growth')` 判定，页面不硬编码平台 id
**新版**: `admin/ui/src/pages/GrowthPage.vue` + `admin/ui/src/api/growth.ts`

> 旧版副标题（保留进 ⓘ）：「成长计划只对声明支持的平台有意义；任务进度来自上游真实账号，
> 做任务与领奖都会写入积分流水。」

---

## 一、区块

| # | 区块 | 旧版 | 新版 |
|---|---|---|---|
| 1 | 页头「成长计划 · <平台>」+ 全部领奖 / 刷新 | `panel-growth` 页头 | `PageHeader` + `#actions`（全部领奖 primary、刷新 quiet） |
| 2 | StatStrip（账号 / 任务总数 / 已完成 / 可领奖励 / 可领积分 / 能量合计） | 旧版散落统计 | `StatStrip`（6 格） |
| 3 | FilterBar（搜索账号 + 状态筛选 + 本地分页） | 无（账号少） | `FilterBar` + `useTableQuery('growth')` |
| 4 | 账号成长卡（账号 / 状态 / 连续登录 / 能量 / 等级进度 Meter / 任务清单 / 做任务·领奖·成长动作） | `growthCenter` 快照面板 + 任务弹窗 | 卡片网格 |
| 5 | 后台任务进度 | `runJob('/api/growth/job/')` | `JobProgress` + `useJob` |

---

## 二、数据展示（真实接口字段）

### 2.1 `GET /api/growth-center/accounts`（`growth_center.py:list_accounts`）

```jsonc
{ "items": [{ "id": 12, "name": "193****2486", "status": "active", "streak_days": 6, "energy": 320 }],
  "excluded_enterprise": 2 }
```

- 「账号」卡头：`name` + `status` Badge（active ok / 其它 neutral）。
- chips：`streak_days`（「连续 6 天」）、`energy`（「能量 320」）；缺失显示 `—`。
- `excluded_enterprise`：企业版账号不参与成长任务 → 页脚说明「已排除 N 个企业版账号」。

### 2.2 `GET /api/growth/accounts/{id}/tasks`（`growth.py:account_tasks`）

```jsonc
{ "account_id": 12, "name": "…", "tasks": [{
    "task_code": "chat_request_send", "title": "发送一条对话", "reward_credit": 50,
    "accept_status": "completed", "progress": { "current": 3, "target": 3 }, "locked": false,
    "level": "auto", "level_label": "可自动", "actionable": true,
    "strategy": "发一次请求即完成", "need_times": 1
  }],
  "profile": { "level": 3, "level_name": "Lv.3", "completed": 12, "total": 20, "max_level": 10 },
  "summary": { "total": 20, "done": 12, "claimable": 3, "claimable_credit": 150, "actionable": 4 } }
```

| 展示 | 字段 | 规则 |
|---|---|---|
| 等级 | `profile.level_name` / `profile.level` | 有则显示「Lv.3」，无则 `—` |
| 进度 Meter | `summary.done / summary.total`（兜底 `profile.completed/total`） | Meter 值 = done，max = total；右侧「done/total」 |
| 任务清单 | `tasks[]` | 默认展示前 4 条（可展开全部）；状态 Badge 见 §2.3 |
| 任务状态 | `accept_status` / `level` / `actionable` | `completed` → warn「可领取」；`claimed` → ok「已领取」；`not_accepted` → neutral「未参与」；其余 neutral「进行中」 |
| 任务奖励 | `reward_credit` | 积分，`fmtCredit`；0 显示 `—` |
| 做任务可用性 | `actionable` | 至少一条 `actionable && accept_status !== 'claimed'` 才启用「做任务」 |
| 领奖可用性 | `summary.claimable > 0` | 启用「领奖」 |

### 2.3 统计条

| 标签 | 计算 | 说明 |
|---|---|---|
| 账号 | `accounts.length` | 当前平台可管理账号数 |
| 任务总数 | Σ `summary.total` | 只算已加载任务清单的账号 |
| 已完成 | Σ `summary.done` | 含已领取 |
| 可领奖励 | Σ `summary.claimable` | 条数 |
| 可领积分 | Σ `summary.claimable_credit` | `fmtCredit` |
| 能量合计 | Σ `energy` | 来自 `/api/growth-center/accounts` |

### 2.4 速度/等级着色

- `level`（growth_plans.classify）：`auto`/`multi` → brand；`manual` → warn；`skip` → neutral。文案用后端 `level_label`，前端不翻译。
- 可领取 Badge 用 warn，已领取 ok，其余 neutral（01-direction §3 状态色映射）。

---

## 三、交互

| 控件 | 旧版行为 | 新版行为（接口 / 文案） |
|---|---|---|
| 刷新 | 重载账号与快照 | 重载 `/growth-center/accounts` + 全部账号任务清单（`refresh=true` 绕缓存），保留旧数据只转圈 |
| 全部领奖 | 遍历账号逐个 claim | 确认「领取全部可领奖励？」→ `POST /api/growth/claim {account_ids: 所有有可领的 id}`（task_codes 省略 = 后端自动领 completed）→ Toast「已领取 N 个任务，共 X 积分」→ 重载 |
| 账号卡「做任务」 | `POST /api/growth/run-async` + JobProgress | 同：`{account_ids:[id]}` → job_id → `useJob('/api/growth/job/')` + JobProgress；完成后重载该账号任务 |
| 账号卡「领奖」 | `POST /api/growth/claim` | `{account_ids:[id]}`（task_codes 省略）→ Toast 结果 → 重载 |
| 任务行「领奖」 | 单任务 claim | `{account_ids:[id], task_codes:[code]}`（仅 `completed` 行显示） |
| 账号卡 ⋯「每日打卡 / 开启盲盒 / 领取礼物 / 限时抽奖 / 连登兑换(7/14/28 天)」 | `POST /api/growth-center/accounts/{aid}/action` | 同；`redeem` 带 `tier`（7/14/28）；成功 Toast「已完成」+ 积分，失败 Toast 后端 `detail` |
| 刷新账号状态（成长中心快照） | `POST /api/growth-center/accounts/{aid}/refresh` | ⋯「刷新成长状态」→ 后台线程（后端 2 并发限制，429 原样提示）→ 轮询 `GET /accounts/{aid}` 直到 `refresh.running=false` |
| 搜索 / 状态筛选 / 分页 | 无 | FilterBar：`q`（账号名）+ `state`（全部/可领奖励/有可做任务/已禁用）+ `page/size`，全部同步路由 query |

**Toast 文案**：`已领取 N 个任务，共 X 积分` / `做任务已提交后台执行` / `操作失败：<后端 detail>`。

---

## 四、接口

| 方法 | 路径 | 参数 / 请求体 | 响应（本页用到的字段） | 后端 |
|---|---|---|---|---|
| GET | `/api/growth-center/accounts` | — | `items[{id,name,status,streak_days,energy}]`、`excluded_enterprise` | growth_center.py:list_accounts |
| GET | `/api/growth-center/accounts/{aid}` | — | `account`、`snapshot.sections`、`refresh.running`、`latest_run`、`schedule`、`today` | growth_center.py:overview |
| POST | `/api/growth-center/accounts/{aid}/refresh` | — | `{ok, running}` | growth_center.py:refresh |
| POST | `/api/growth-center/accounts/{aid}/action` | `{action: redeem\|open\|gift\|draw\|checkin, tier?}` | `{ok, credit, reason, steps[]}` | growth_center.py:action |
| GET | `/api/growth/accounts/{id}/tasks` | `refresh=true` 可选 | `tasks[]`、`profile`、`summary` | growth.py:account_tasks |
| POST | `/api/growth/run-async` | `{account_ids, task_codes?}` | job 快照（`job_id`…） | growth.py:growth_run_async |
| GET | `/api/growth/job/{job_id}` | — | job 快照（`status/percent/result`） | growth.py:growth_job |
| POST | `/api/growth/claim` | `{account_ids, task_codes?}` | `{results[{account_id, ok, credit, tasks[]}]}` | growth.py:growth_claim |
| POST | `/api/growth/accept` | `{account_ids, task_codes}` | `{results[]}` | growth.py:growth_accept |
| GET | `/api/growth/plans` | — | `levels`、`plans[]`（策略表，排障用） | growth.py:growth_plans_view |

调用时机：进入页面 / 平台变化 / 刷新 / 任何写操作成功后重载对应账号。

---

## 五、跨页联动

- **平台上下文**：`usePlatformStore().onChange` 订阅，卸载取消；`supports('growth')` 为假时整页 `Empty`「当前平台没有成长计划」+「去账号池」（切平台由顶栏完成）。
- 账号名/ID 与账号池一致；做任务/领奖的积分会进「积分流水」（后端 `credit_journal`），本页不重复展示。
- 无其它页面跳转；无命令面板动作。

---

## 六、边界情况

1. **首次加载**：卡片骨架（每账号一张灰块）+ 统计条骨架；「全部领奖」禁用。
2. **刷新中**：保留旧数据，刷新按钮转圈；任务清单按账号粒度单独 loading。
3. **空**：无成长账号 → `Empty`「当前平台还没有可管理的成长账号」；平台不支持 → `Empty`「当前平台没有成长计划」；筛选无结果 → 「没有符合条件的账号」。
4. **错误**：账号列表失败 → `ErrorBlock` + 「重试」（`loadError`，无数据时占整页）；单账号任务清单失败 → 该卡内错误提示 + 「重试」（`reloadAccountTasks`），孤立的个别失败不影响其它卡；**连续 8 个失败则熔断整条队列，只在顶部留一条提示**：
   - **熔断阈值**：批量预取按 `TASK_FAILURE_LIMIT = 8` 计「连续失败」，任一账号成功即清零；`abort` / `AbortError` 直接返回、不计入。第 8 个连续失败落定时置 `tripped`，两个 worker 都不再领新 id；并发 2 下最多发出 **9 个请求**（8 个已失败 + 1 个先于熔断发出的在途请求），194 个账号整站 502 不会跑到底。
   - **顶部单条提示**：熔断后清空逐卡 `tasksError`（失败卡片回落「这个账号还没有拉到任务清单。」空态，不逐卡刷 502），改在统计条上方渲染唯一一条 `.gw-bulk-error`（`role="alert"`、`TriangleAlert` 图标、`Btn`「重试」）：正文「任务清单连续 8 个账号加载失败，已暂停批量预取」（N > 0 时补「（剩余 N 个账号未请求）」），副行是最后一条失败的 `failureText('加载任务清单', …)`；N = 本轮队列长度 − 已发出请求数（`ids.length - attempted`）。
   - **重试只补缺口**：`retryBulkTasks()` 清掉全局提示后跑 `loadAllTasks(false)`，按 `force || !tasksByAccount[id]` 过滤，只请求首轮失败与从未请求的账号（已拿到 payload 的不重跑）；若这轮再连续失败，会重新熔断并再次出提示。
   - **卸载 / 切平台 abort 旧队列**：队列挂在 `tasksAbort` 上，`abortTaskQueue()` 依次 `abort()` 在途请求、置空 `tasksAbort`、`tasksToken += 1`（旧 token 失效）、清 `tasksLoading`；调用点为 `onBeforeUnmount`、`platformStore.onChange`（`selector` 变化时先 abort 再判 `supports('growth')`）、`watch(supported)` 转 false、以及每轮 `loadAllTasks` 开头（新一轮取代旧一轮）。worker 在写入 `tasksByAccount` / `tasksError` 前用 `stale()`（`abort.signal.aborted || token !== tasksToken`）拦掉迟到响应与错误，因此 abort 只静默退出、不产生全局提示；切平台时同时清空 `bulkTasksError`，平台不支持则清空账号列表并显示 `Empty`。
5. **任务清单拉取慢**：`/api/growth/accounts/{id}/tasks` 单次 1–3.3s（后端 3s 缓存）→ 逐账号并发 2 拉取，先到先渲染，未到的卡显示骨架；失败收敛与队列取消见第 4 条。
6. **企业版账号**：后端已排除，页脚显示排除数量。
7. **禁用账号**：`status !== 'active'` 时「做任务/领奖/成长动作」禁用，提示「请先启用账号或重新登录」（后端同口径 400）。
8. **并发限制**：成长中心刷新后端只允许 2 个并发，第 3 个返回 429 → Toast 原文。
9. **长任务**：做任务是后台 job（`run-async`），完成前不重复提交；重复点击返回同一 job（`reused`）。
10. **手机 375px**：卡片单列；FilterBar 控件换行；任务清单行换行显示状态与奖励。

---

## 七、新版取舍

1. **成长中心快照分区不做全量可视化**：旧版 `growth_center.js` 渲染 heatmap / streak / buddies / travel / lottery / redeem / quota 等分区（`admin/growth_center.py:SECTIONS`）。本页保留其**动作**（打卡/盲盒/礼物/抽奖/兑换）与账号级字段（streak_days / energy / 等级进度），分区可视化留待后续（需要 `GET /accounts/{aid}` 的 snapshot.sections，逐账号请求）。
2. **等级进度用任务完成度**：`summary.done/total` 是每账号必有的字段；`profile` 结构由上游决定，只在有 `level_name`/`completed`/`total` 时叠加显示。
3. **全部领奖一次批量**：后端 `/api/growth/claim` 支持 `account_ids` 数组，不再前端循环逐个请求。
4. **分页是本地分页**：`/growth-center/accounts` 一次返回全部成长账号（数量级很小），分页只为长列表体验与 query 同步。

---

## 实现对照

| 契约功能 | 新版位置 | 状态 |
|---|---|---|
| 页头「成长计划 · 平台名」+ 全部领奖 / 刷新 | `GrowthPage.vue` `PageHeader` | 已实现 |
| StatStrip 6 格 | `api/growth.ts: growthStats` + `StatStrip` | 已实现 |
| 账号卡：账号 / 状态 / 连续登录 / 能量 | `GrowthPage.vue` 卡片头 | 已实现 |
| 等级 / 进度 Meter | `api/growth.ts: progressOf`（done/total，profile 叠加） | 已实现 |
| 任务清单（状态 / 奖励 / 策略） | `GrowthPage.vue` `taskRows` + `taskStatusOf` | 已实现 |
| 做任务（后台任务 + JobProgress） | `runGrowthTasks` → `useJob('/api/growth/job/')` + `JobProgress` | 已实现 |
| 账号领奖 / 单任务领奖 / 全部领奖 | `claimGrowth` + `claimableCodes` | 已实现 |
| 成长中心动作（打卡/盲盒/礼物/抽奖/连登兑换 7·14·28） | `postGrowthAction` + ⋯ 菜单 | 已实现 |
| 刷新成长状态（成长中心 refresh） | `refreshGrowthCenter` + 轮询 `GET /accounts/{aid}` | 已实现 |
| 平台能力判定（不硬编码平台） | `platformStore.supports('growth')` → `Empty` | 已实现 |
| 批量预取并发 2 + 连续 8 失败熔断 | `GrowthPage.vue: loadAllTasks`（`TASK_CONCURRENCY = 2`、`TASK_FAILURE_LIMIT = 8`、`tripped`） | 已实现：最多 9 个请求即停，不再把整平台账号跑完 |
| 熔断后顶部单条提示 + 「重试」只补缺口 | `bulkTasksError` + `.gw-bulk-error`（`role="alert"`）+ `retryBulkTasks()` | 已实现：逐卡 `tasksError` 清空，只留一条全局提示与一个重试入口 |
| 卸载 / 切平台 / 新一轮预取 abort 旧队列 | `abortTaskQueue()`（`tasksAbort` + `tasksToken`）；`onBeforeUnmount` / `platformStore.onChange` / `watch(supported)` / `loadAllTasks` 开头 | 已实现：迟到响应用 `stale()` 丢弃，`AbortError` 不计失败 |
| 熔断与取消的回归测试 | `admin/ui/tests/coverage-growth.spec.ts`「批量预取：熔断与取消」（4 例） | 已实现：8–9 次请求、顶部单条、重试补 30 个、卸载/切平台不再发第 3 个请求 |
| 四态 + 筛选/分页同步 query + 375px | `GrowthPage.vue` + `useTableQuery('growth')` | 已实现 |
| 成长中心快照分区可视化（heatmap/宠物/旅行/抽奖记录…） | 未实现（§七.1） | 有差异（任务范围外，动作已保留） |
| 官方领取记录 / 积分流水明细 | 未实现（属账号页/积分流水页） | 有差异 |

### 已知不一致（代码待跟进，本次仅改文档）

- 页头 ⓘ 文案（`GrowthPage.vue` 的 `HELP_TEXT`）仍写「失败只影响那一张卡」；连续 8 个失败会熔断整条队列、收成顶部一条提示。建议 S26 把 ⓘ 补成「连续失败会暂停批量预取」，本次未改代码。

### 无法验证

- 真实后端整站 502（194 个账号）只在单测 stub 下复现：8–9 个请求即熔断、重试补发数量都来自 `coverage-growth.spec.ts` 的模拟响应，未做真网关联调（与 `accounts.md` 同：本环境无 8790 服务）。
- 顶部提示的视觉呈现（`--danger-soft` 底色、图标对齐、窄屏换行、`role="alert"` 的实际读屏行为）只做过 DOM 断言，没有浏览器/读屏实测。
- 卸载/切平台后的在途请求取消在 jsdom 里用 signal 监听代替真实 fetch 取消，未在真实浏览器 Network 面板核对。
