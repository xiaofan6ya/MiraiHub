# 功能契约：定时任务页（Schedules）

**页面 key**：`schedules`　**路由**：`/schedules`（hash：`/admin/v2/#/schedules`）　**设计稿**：`06-pages-d.md` P17　**模板**：B（列表管理型）
**旧版位置**：`admin/console/index.html` `#panel-schedules`（2199–2220 行）+ `console-app.js` 15528–15947 行（`loadSchedules / runSchedule / scheduleResultSummary / showScheduleResult / toggleSchedule / delSchedule / scheduleProxyHTML / scheduleTimingHTML / scheduleForm / submitSchedule / loadGlobalScheduleProxy / openAddSchedule / editSchedule`）+ `date-time-picker.js`（时间选择器的时区提示语义）
**后端**：`admin/routers/schedules.py`（`list_schedules` / `list_task_catalog` / `create_schedule` / `patch_schedule` / `toggle_schedule` / `run_now` / `delete_schedule` / `get_global_proxy` / `set_global_proxy`）、`admin/schedule_timing.py`（`ScheduleTiming` / `timing_of` / `next_run_at` / `cycle_label` / `display_name`）、`admin/routers/proxies.py:list_proxies`（出口代理组数据源）
**新版文件**：`src/pages/SchedulesPage.vue`、`src/api/schedules.ts`、`tests/page-schedules.spec.ts`。

调度线程随服务启动运行：任务到点自动执行并把结果写回 `Schedule.last_result`（`admin/scheduler.py:_dump_result`，**不截断 JSON 字符串**）。本页是这条链路唯一的人工入口。

---

## 一、区块（按旧版出现顺序）

| # | 旧版区块 | 旧版中文标题/文案 | 新版位置 |
|---|---|---|---|
| 1 | 工具栏左 | 页面说明「定时统计积分、同步模型、各平台签到与保活。调度线程随服务启动运行，到点自动执行并回填结果。列表按平台分组，不随顶部平台切换隐藏。」 | `PageHeader` 标题「定时任务」+ `help`（HelpTip，文案原样保留） |
| 2 | 工具栏右 | 「全局出口与并发」「新建任务」 | `PageHeader #actions`：`刷新`（quiet）+ `全局出口与并发`（quiet，Settings2）+ `新建任务`（primary，Plus） |
| 3 | 表格 | 任务名 / 类型 / 执行周期 / 下次运行 / 上次结果 / 状态 / 操作 | `Card flush` 内 `FilterBar` + `DataTable`（列见 §2.1，按 P17 重排为「任务名 / 类型 / 平台·账号范围 / 执行时间 / 出口代理组 / 最近结果 / 启用 / 操作」） |
| 4 | 新建/编辑弹窗 | 「新建定时任务 / 编辑定时任务」+ 任务名称、任务类型、执行周期、执行间隔、执行时间、出口代理与并发、成长计划说明、停止领取时间、启用任务 | `FormDrawer size="lg"`（字段见 §3.4） |
| 5 | 全局出口与并发弹窗 | 「全局出口与并发」+ 默认代理组 / 默认并发数 / 默认单轮时间预算 / 默认 stale 阈值 | `FormDrawer`（第二个抽屉，字段见 §3.5） |
| 6 | 上次结果弹窗 | 「上次运行结果 — <任务名>」：概览键值 + 逐账号明细表 + 原始 JSON 折叠 | `DetailDrawer`：`KV` 概览 + 明细 `DataTable` + `CodeBlock`（json） |

---

## 二、数据展示

### 2.1 表格列（`DataTable`）

| 列 | 中文标签 | 来源字段 | 格式 / 着色 | 排序 | 手机卡片位 |
|---|---|---|---|---|---|
| 1 | 任务名 | `items[].display_name`（空则 `name`，再空则 `#<id>`） | 正文 500 字重、单行省略 + `title`；行内追加 `Badge brand dot`「执行中」（`last_result.running`）；`stop_after` 存在时下一行 `warn`「停止于 MM-DD HH:mm」 | 升/降（`localeCompare zh-CN`） | `title` |
| 2 | 类型 | `items[].task_label` + `items[].task` | 上：中文标签；下：任务标识（等宽、`--fg-muted`、`title` 显示完整 id） | — | `meta` |
| 3 | 平台 / 账号范围 | `items[].platform_label`（`platform` 作 `title`） | 平台展示名（含「Trae（中国版 + 国际版）」这类双区标注）；下一行 `--fg-muted`「该平台全部账号」 | 升/降 | `subtitle` |
| 4 | 执行时间 | `items[].cycle_label`（缺失用 `buildCycleLabel(timing, interval_minutes)`）+ `timing` + `next_run_at` | 主行：周期文案（`每天 09:00` / `每 2 小时` / `每周一、周三 21:00` / `每月 1 日 09:00`）；日历周期追加 `--fg-muted`「北京时间 · UTC+8」；次行「下次：今天 09:30」——停用显示「已停用」、执行中显示「执行中」、到期未跑（`due_soon`）用 `--warn` | 按 `next_run_at` | `meta` |
| 5 | 出口代理组 | `items[].proxy_pool` + `items[].concurrency` / `time_budget_minutes` / `stale_minutes` + 全局默认 | 有任务级组名：「代理组 residential」；否则「跟随全局（代理组 lan）」/「跟随全局（并发 6）」/「直连」（`--fg-muted`）；次行「并发 2 · 只刷 60 分钟未更新」；`title` 列出三项完整口径 | — | `meta` |
| 6 | 最近结果 | `items[].last_result`（JSON 文本）+ `last_run_at` | `Badge`（tone/label 见 §2.2）+ 上次运行时间（`fmtTime`，无则「未运行」）+ 一行摘要（`title` 全文） | 按 `last_run_at` | `meta` |
| 7 | 启用 | `items[].enabled` | `NSwitch`（small），`aria-label`「启用任务 / 停用任务」，请求中 `loading` | — | `meta` |
| 8 | 操作 | — | `RowActions`（`max=2`）：`立即执行`、`编辑` 直接显示；`结果详情`、`删除`（danger）收进「⋯」菜单 | — | `actions` |

- 行键：`String(items[].id)`。
- 默认顺序 = 后端顺序（`Schedule.id ASC`）；排序为空时保持后端顺序，同值按 id 升序稳定。
- 「执行中」是 `last_result.running === true`（成长计划后台线程写入），此时行操作「立即执行」禁用，页面按 4s 轮询刷新到结束。

### 2.2 最近结果 Badge（`parseScheduleResult`）

| 条件（按判定顺序） | Badge 文案 | tone | 摘要 |
|---|---|---|---|
| `last_result` 空 | 尚未运行 | neutral | 空（单元格显示「未运行」） |
| JSON 解析失败（历史数据被旧版截断） | 旧格式 | warn | 「历史结果无法解析，点「结果详情」查看原文」 |
| `running === true` | 执行中 | brand | `summary`，缺省「后台执行中，完成后自动刷新」 |
| `deferred === true` | 已顺延 | warn | `summary` / `error`，缺省「出口保护期间顺延，稍后自动重试」 |
| `error` 非空 | 失败 | danger | `error` 文本（去 `**`） |
| `skipped` 非空且 `ok !== false` | 已跳过 | neutral | `skipped` 文本 |
| `ok === false` | 失败 | danger | `summary` → 字段兜底 → 「任务返回失败」 |
| `failed`/`fail`/`failed_count` > 0，或 `detail[].ok === false` 存在 | 部分失败 | warn | `summary` → 字段兜底 |
| 其它 | 成功 | ok | `summary` → 字段兜底 → 「运行完成」 |

- 摘要统一去 markdown 强调符（`**`），表格不渲染 markdown（旧版同一处理）。
- `summary` 后追 ` · <elapsed_s>s`（`elapsed_s` > 0 时）。
- `ok` 有两种形态：布尔（`{"ok": true}`）与计数（`{"ok": 12, "fail": 1}`）——计数不当成失败。

### 2.3 筛选栏（`FilterBar`）

| 控件 | 中文标签 | 路由 query | 取值 | 语义 |
|---|---|---|---|---|
| 搜索 | 搜索任务名 / 类型 / 平台 | `q` | 文本（300ms 防抖） | 大小写不敏感，匹配 `display_name`、`name`、`task_label`、`platform_label`、`proxy_pool` |
| 平台 | 平台：全部 | `platform` | 平台 id | 精确匹配 `platform`（选项由当前列表去重生成） |
| 类型 | 类型：全部 | `task` | 任务 id | 精确匹配 `task`（选项来自 `/api/schedules/tasks`） |
| 状态 | 状态：全部 | `state` | `''/enabled/disabled/due` | `due`：启用且 `due_soon`（后端判定已到期） |
| 结果 | 结果：全部 | `result` | `''/ok/warn/danger/none` | 按 §2.2 的 tone；`none` = `parsed === null`（含旧格式与未运行） |

- 「已筛选」chips：每个生效项一个可关闭 chip；`remove` 即清空该项。
- 右侧统计文字：无筛选「共 N 个任务 · 启用 E · 停用 D」+「· X 个最近失败」「· Y 个执行中」；有筛选「筛选出 N / 共 T 个任务」。
- 分页 / 排序 / 筛选经 `useTableQuery('schedules')` 写入路由 query（`page`、`size`、`sort`、`order`、`q`、`platform`、`task`、`state`、`result`）；每页条数 20/50/100 另存 `localStorage['mh.pageSize.schedules']`。

### 2.4 四种状态

| 状态 | 表现 |
|---|---|
| 首次加载 | 表格区 6 行 `NSkeleton`，`aria-busy="true"` |
| 刷新中 | 保留旧数据；`DataTable` 顶部 2px 进度条；`刷新` 按钮转圈 |
| 空 | 无任务：`Empty`「还没有定时任务。新建一个任务，按周期自动刷新积分 / 同步模型 / 跑各平台签到。」+ `新建任务`；有筛选无命中：`Empty` + `清除筛选` |
| 错误 | 首次失败：`ErrorBlock`（「加载定时任务失败：<原因>」+ 可展开 detail + 重试）；已有数据刷新失败：保留旧数据 + 错误 Toast，不清表 |
| 轮询暂停 | 有任务执行中时按 4s 轮询；页面不可见暂停、连续失败 3 次停止并显示横幅「自动刷新已暂停：<原因>」+ `恢复` |

---

## 三、交互

### 3.1 页头

| 动作 | 中文文案 | 行为 | 成功 / 失败 |
|---|---|---|---|
| 刷新 | `刷新` | `GET /api/schedules` + `GET /api/schedules/global-proxy`，保留筛选/分页/排序 | 失败：错误 Toast + 旧数据保留 |
| 新建 | `新建任务` | 打开空表单抽屉（并行拉任务清单与代理组） | 任务清单拉取失败时抽屉内 `hint` 显示失败原因，仍可填其它字段 |
| 全局 | `全局出口与并发` | `GET /api/proxies` 后打开全局抽屉，回填当前全局默认值 | 见 §3.5 |

### 3.2 行操作

| 动作 | 中文文案 | 行为 | 成功 / 失败 |
|---|---|---|---|
| 立即执行 | `立即执行` | `POST /api/schedules/{id}/run`；执行中该行按钮禁用 + `runningIds` 去重（同一行不并发触发） | 成功 Toast「「X」已执行：成功 · <摘要>」（tone 决定 success/warning/error）；失败「立即执行失败：<原因>」。执行完 `GET /api/schedules` 刷新该行（结果、下次时间、上次时间）；若 `result.running` 则开启 4s 轮询 |
| 编辑 | `编辑` | 打开抽屉回填（见 §3.4） | 任务已不存在：Toast「该任务已不存在，请刷新列表」 |
| 结果详情 | `结果详情` | 打开 `DetailDrawer`：Badge + 标量 KV + 逐账号明细表（分页 10）+ 原始 JSON | 无结果时显示「该任务还没有运行过，没有结果。」 |
| 删除 | `删除` | `useConfirm`：标题「删除定时任务「X」？」；正文「删除后该任务不再执行，已产生的执行结果与历史记录保留在日志里。」；按钮 `取消` / `删除`（danger） | 成功 Toast「已删除任务「X」」；失败「删除任务失败：<原因>」 |
| 启用 / 停用 | 开关 | 打开：`POST /api/schedules/{id}/toggle`；关闭前先确认：标题「停用任务「X」？」；正文「停用后不再按周期执行，下次运行时间会被清空；已有执行结果保留。随时可以再启用。」；按钮 `取消` / `停用` | 成功 Toast「已启用「X」」/「已停用「X」」；失败「启用任务失败：<原因>」/「停用任务失败：<原因>」；确认取消则开关回到原状态（开关值由行数据驱动，不发请求） |

### 3.3 结果详情抽屉

- 标题「最近结果 — <display_name>」，Badge 用 §2.2 的 tone/label，副标题「启用中/已停用 · 上次运行 <fmtTime>」。
- 概览：`last_result` 的**标量**字段（跳过数组/对象），键名按 `RESULT_FIELD_LABELS` 翻中文（`ok`「结果」、`claimed`「新领取」、`credit`「获得积分」、`skipped_already`「今日已领」、`elapsed_s`「耗时（秒）」…），未知键原样显示；超过 240 字的截断加「…」。
- 明细表：账号 / 结果（失败=「失败」，否则 `status` 或「成功」）/ 积分（千分位）/ 说明（`reason` 或 `error`）。
- 原始 JSON：`CodeBlock language="json" max-height="280px"`，可复制。

### 3.4 表单抽屉（`FormDrawer size="lg"`，`FormField`）

| 字段 | 控件 | 必填 | 默认 / 校验 / 说明 |
|---|---|---|---|
| 任务名称 | `NInput`（placeholder「留空自动命名」，maxlength 64） | 否 | 留空由后端 `display_name` 按周期生成；> 64 字报错 |
| 任务类型 | `NSelect`（filterable，选项 `/api/schedules/tasks`） | 是 | 切换任务即更新「平台 / 账号范围」与「停止领取时间」的显隐；目录里没有当前任务时补一项「（目录中已无此任务）」，不静默清空 |
| 平台 / 账号范围 | 只读文本 | — | 由任务决定（`task → platform`），显示「平台名（落库平台 id）」。后端按它找平台 runner，用户不可改 |
| 执行周期 | `NSelect`：系统预设 / 固定间隔 / 每天 / 每周 / 每月 | 是 | 对应 `timing.mode = preset/interval/daily/weekly/monthly` |
| 执行间隔 | `NInputNumber`（1–5256000）+ `NSelect` 单位（分钟/小时/天/周） | 是（`preset`/`interval`） | 分钟总数 = 数值 × 单位；`preset` 的 hint 说明「沿用这里填的间隔；token 保活另有系统整点时间」 |
| 执行时间 | `NInput`（placeholder 09:00，maxlength 5） | 是（日历周期） | `HH:MM` 正则校验；hint「时区口径：北京时间 · UTC+8（与服务器时区无关）」——对应旧版 `date-time-picker.js` 的 `data-timezone` 提示 |
| 执行日 | `NCheckboxGroup` 周一…周日 | 是（`weekly`） | 至少一天；内部按 0–6 排序去重（周一=0，与后端一致） |
| 每月几号 | `NInputNumber`（1–31） | 是（`monthly`） | hint「当月没有该日期时，在当月最后一天执行。」 |
| 出口代理组 | `NSelect`（`buildProxyPoolOptions`） | 否 | 第一项「跟随全局（当前：代理组 X / 直连）」= 空值；其后为 `/api/proxies.pools` 各组「可用/总数」；当前值不在组列表时补「节点已不存在」，保存不丢值；无任何节点时给禁用提示项 |
| 并发数 | `NInputNumber`（0–64） | 否 | 0 = 自动（各节点 `max_concurrency` 之和）；hint 说明住宅建议 1~2、机房 5~10 |
| 单轮时间预算（分钟） | `NInputNumber`（0–720） | 否 | 0 = 默认 8；到点就停，剩余下一轮继续 |
| 只刷超过 N 分钟未更新的号 | `NInputNumber`（0–10080） | 否 | 0 = 默认 60；刚刷过的直接跳过 |
| 停止领取时间 | `NDatePicker type="datetime"`（`value-format="yyyy-MM-dd'T'HH:mm"`，clearable） | 否 | **仅签到类任务**（`isCheckinTask`）显示；hint「到达该时间后停止自动领取（本地时间）」——沿用旧版 `datetime-local` 的本地时间口径；非签到任务提交时强制 `null` |
| 启用任务 | `NSwitch` + 说明「按设置的周期自动执行」 | 否 | 默认开启 |
| 成长计划说明 | `HelpTip` | — | 旧版大段说明原样保留（05-patterns §6：长文案进 HelpTip） |

- 底部操作栏：`取消` + `创建任务` / `保存`；提交中 loading 且禁用。
- 校验失败：`FormField error` 朱色文案，不发请求（`validateScheduleForm`）。
- 提交成功：Toast「已创建任务「X」」/「已保存任务「X」」，关抽屉，重载列表（新建后回第 1 页）。
- 提交失败：Toast「创建任务失败：<原因>」/「保存任务失败：<原因>」，抽屉保持打开、已填内容不丢。
- 请求体见 §4.3；`platform` 由任务目录决定（`buildScheduleInput`）。

### 3.5 全局出口与并发抽屉

| 字段 | 控件 | 说明 |
|---|---|---|
| 默认代理组 | `NSelect`（同一份代理组选项） | 留空 = 直连；优先级：任务显式配置 > 全局默认 > 环境变量 > 内置默认 |
| 默认并发数 | `NInputNumber`（0–64） | 0 = 自动 |
| 默认单轮时间预算（分钟） | `NInputNumber`（0–720） | 0 = 内置 8 |
| 默认只刷超过 N 分钟未更新的号 | `NInputNumber`（0–10080） | 0 = 内置 60 |

- 保存：`PUT /api/schedules/global-proxy`（四个字段全量提交）；成功 Toast「已保存全局出口与并发，下一轮任务执行即生效」→ 关抽屉 → 重载列表（单条任务行的「跟随全局」文案随之更新）；失败「保存全局出口与并发失败：<原因>」。
- 该配置存 `SystemSetting['schedule:global_proxy']`，任务执行时读取，天然热生效、无需重启。

---

## 四、接口

### 4.1 `GET /api/schedules` — 任务列表

- 后端：`admin/routers/schedules.py:list_schedules`。
- 参数：无（不按 `X-Platform` 收窄，任务本身带 `platform`）。
- 调用时机：进入页面、点「刷新」、切平台（`usePlatformStore().onChange`）、增删改/启停/立即执行之后、执行中轮询（4s）。
- 响应：

```json
{
  "items": [
    {
      "id": 3,
      "name": "",
      "display_name": "每天 08:47 · 签到领取积分（WorkBuddy）",
      "timing": { "mode": "daily", "time": "09:00", "weekdays": [0], "monthday": 1 },
      "cycle_label": "每天 09:00",
      "task": "daily_checkin",
      "task_label": "签到领取积分（WorkBuddy）",
      "platform": "workbuddy",
      "platform_label": "WorkBuddy",
      "interval_minutes": 1440,
      "enabled": true,
      "last_run_at": "2026-11-23T00:47:12",
      "next_run_at": "2026-11-23T01:30:00",
      "last_result": "{\"task\":\"daily_checkin\",\"ok\":true,\"claimed\":3,\"summary\":\"领取 3 个\",\"elapsed_s\":12.5}",
      "stop_after": "2026-12-01T00:00:00",
      "proxy_pool": "residential",
      "concurrency": 2,
      "time_budget_minutes": 8,
      "stale_minutes": 60,
      "due_soon": false
    }
  ]
}
```

| 字段 | 类型 | 含义 | 新版用途 |
|---|---|---|---|
| `id` | int | 主键 | 行键、各操作路径参数 |
| `name` | string | 用户填的名称（可空） | 编辑回填 |
| `display_name` | string | `admin/schedule_timing.py:display_name` 算的展示名 | 列 1 |
| `timing` | object | `ScheduleTiming`（mode/time/weekdays/monthday） | 列 4、抽屉回填 |
| `cycle_label` | string | 周期人话（「每 1 天」「每天 09:00」「每天 22:00（系统时区）」） | 列 4 主行 |
| `task` / `task_label` | string | 任务 id / 中文名 | 列 2、筛选 |
| `platform` / `platform_label` | string | 落库平台 id / 展示名（双区平台标注「中国版 + 国际版」） | 列 3、筛选 |
| `interval_minutes` | int | 固定间隔（preset/interval 用） | 抽屉「执行间隔」 |
| `enabled` | bool | 启用状态 | 列 7、列 4 的「已停用」 |
| `last_run_at` | string \| null | 上次运行（naive UTC） | 列 6 时间、排序 |
| `next_run_at` | string \| null | 下次运行（`next_run_at()` 已含抖动；naive UTC） | 列 4「下次：…」、排序 |
| `last_result` | string | `_dump_result` 的 JSON 文本（空串=未运行） | 列 6、结果抽屉 |
| `stop_after` | string \| null | 签到任务的停止领取时间 | 列 1 提示、抽屉 |
| `proxy_pool` | string | 任务级代理组（空=跟随全局） | 列 5、抽屉 |
| `concurrency` | int | 0=自动 | 列 5 title、抽屉 |
| `time_budget_minutes` | int | 0=默认 8 | 同上 |
| `stale_minutes` | int | 0=默认 60 | 同上 |
| `due_soon` | bool | `enabled && (next_run_at 为空或已过)`（后端 `datetime.utcnow()` 判定） | 列 4 着色、`state=due` 筛选 |

### 4.2 `GET /api/schedules/tasks` — 可选任务清单

- 后端：`admin/routers/schedules.py:list_task_catalog`（内建 `TASK_CHOICES` + 各平台 `Gateway.task_runners()`，按 `(platform, value)` 排序）。
- 调用时机：进入页面、打开抽屉（缓存复用；失败可重试）。
- 响应：`{ "items": [{ "value": "qoder_checkin", "label": "签到领取权益（Qoder）", "platform": "qoder", "platform_label": "Qoder 中国版", "platforms": ["qoder"] }] }`。
- 用途：任务类型下拉；`platform` 决定提交体的 `platform`（见 §4.5）。

### 4.3 `POST /api/schedules` / `PATCH /api/schedules/{id}` — 新建 / 编辑

- 后端：`create_schedule` / `patch_schedule`（`ScheduleIn` / `SchedulePatch`）。
- 请求体：

```json
{
  "name": "每日签到",
  "task": "daily_checkin",
  "platform": "workbuddy",
  "interval_minutes": 1440,
  "timing": { "mode": "daily", "time": "08:47", "weekdays": [0], "monthday": 1 },
  "enabled": 1,
  "stop_after": "2026-12-01T00:00",
  "proxy_pool": "residential",
  "concurrency": 2,
  "time_budget_minutes": 8,
  "stale_minutes": 60
}
```

- 新版一律按**整表提交**（PATCH 也带上全部字段）；`name` 为空时后端按 `TASK_LABELS` 兜底命名。
- `timing`：`time` 必须 `HH:MM`；`weekdays` 1–7 个、0–6（周一=0）；`monthday` 1–31。非日历周期提交 `time: "09:00"`、`weekdays:[0]`、`monthday:1`（与旧版归一逻辑一致）。
- `stop_after`：`datetime-local` 口径的本地时间字符串（`YYYY-MM-DDTHH:mm`，后端 `datetime.fromisoformat` 解析），**仅签到类任务**提交，其它一律 `null`。
- 响应：`{"id": 3, "ok": true}`。
- 错误：400「未知任务类型」（`PATCH` 只认 `TASK_CHOICES`；新增平台任务请重建任务）/ 404「任务不存在」。

### 4.4 `POST /api/schedules/{id}/toggle`、`POST /api/schedules/{id}/run`、`DELETE /api/schedules/{id}`

| 接口 | 后端 | 响应 | 说明 |
|---|---|---|---|
| toggle | `toggle_schedule` | `{"id":3,"enabled":true}` | 取反；重新启用时重算 `next_run_at`；404「任务不存在」 |
| run | `run_now` | `{"id":3,"result":{...}}` | 与调度器同源写入 `last_result`；出口保护期（`POOL.waf.active()`）返回 `deferred`；`run_growth_plan` 走后台线程、返回 `running:true`；失败也会落库 `{"ok": false, "error": ...}` |
| delete | `delete_schedule` | `{"id":3,"ok":true}` | 404「任务不存在」 |

### 4.5 `GET /api/schedules/global-proxy` / `PUT /api/schedules/global-proxy`

- 后端：`get_global_proxy` / `set_global_proxy`，存 `SystemSetting['schedule:global_proxy']`。
- 响应 / 请求体：`{"proxy_pool": "lan", "concurrency": 6, "time_budget_minutes": 10, "stale_minutes": 30}`；`PUT` 返回 `{"ok": true, ...四字段}`。
- 解析失败（坏数据）按未配置处理（后端已兜底），前端不额外处理。

### 4.6 `GET /api/proxies` — 出口代理组（抽屉下拉）

- 后端：`admin/routers/proxies.py:list_proxies`。
- 本页只消费：`pools`（现有组名，升序）、`items[].pool` / `items[].state`（算「可用/总数」）。
- 调用时机：首次打开新建/编辑/全局抽屉（缓存复用；全局抽屉每次强制刷新）。
- 失败处理：静默降级为「只剩跟随全局」——任务级 `proxy_pool` 原值仍保留在表单里，不会被清空。

---

## 五、跨页联动

| 联动 | 说明 |
|---|---|
| 平台上下文 | `schedules` 在 `router/pages.ts:PLATFORM_SCOPED_KEYS` 内，顶栏显示平台选择器；页面订阅 `usePlatformStore().onChange`，切换后重拉一次（旧版 `console-app.js:3314-3350` 的 `loadSchedules()`）。**列表本身不按平台过滤**（旧版 15538 行的显式决定：各平台签到/保活要一眼可见），卸载时取消订阅 |
| 设置 › 出口代理节点（`/settings`，S28） | 代理组名在此维护；本页「出口代理组」下拉直接读 `/api/proxies.pools`。设置页说明里的「定时任务」链接指回本页 |
| 账号池 / 积分到期（`/accounts`、`/credits`） | 任务执行会改余额与积分；旧版执行后重拉账号池。新版不跨页强制刷新，只在本页刷新结果（其它页进入时自然取新数据） |
| 命令面板 | 本页不在 `08-prompts-pages` 的命令注册清单里，不注册全局命令 |
| 全局事件 | 无 localStorage 业务态（只有表格密度/列显示/每页条数偏好） |

---

## 六、边界情况

1. **空数据**：`items: []` → `Empty` + `新建任务`。
2. **旧格式结果**：后端历史版本曾把 JSON 截断（`json.dumps(...)[:500]`），`JSON.parse` 失败 → Badge「旧格式」+ 抽屉显示原文，**不冒充「尚未运行」**。
3. **`ok` 的两种形态**：布尔 vs 计数；计数 > 0 仍是成功（旧版同一判定）。
4. **轮询**：只有存在 `last_result.running` 时才轮询；页面不可见自动暂停；连续失败 3 次停止并给「恢复」按钮（05-patterns §3）。
5. **平台专属任务**：不带 `platform` 时后端会去默认平台找 runner，得到「该平台未注册此任务」；新版由任务目录决定 `platform`，避免用户手工填错。
6. **任务类型下线**：目录里已没有当前任务时，抽屉保留原值并标注「目录中已无此任务」；保存时后端 400「未知任务类型」如实提示。
7. **代理组被删**：任务里仍引用已不存在的组 → 下拉补「节点已不存在」项，保存不静默清空；执行时后端按空组回退直连/全局。
8. **`next_run_at` 已过**：`due_soon` 为真 → 列 4 的「下次」用 `--warn` 着色（后端会在下一个调度拍执行）。
9. **时区**：库内时间戳是 naive UTC，展示统一 +8（`utils/format`）；日历周期的 `timing.time` 本身就是北京时间口径，所以只做文字提示、不做换算。
10. **桌面 / 手机**：≤640px 由 `DataTable` 切卡片模式（`mobile` 标注见 §2.1）；抽屉在手机上占满宽度；筛选控件整行。
11. **深浅色 / 动效**：只用 tokens.css 令牌，无裸写颜色；不新增自发动画（轮询失败横幅无动效）。

---

## 七、旧版有但设计稿没写到的功能，以及新版放置

| 旧版功能 | 新版处理 |
|---|---|
| 「全局出口与并发」按钮 + 弹窗（`openGlobalScheduleProxy`） | 保留为页头 quiet 按钮 + 独立 `FormDrawer`（§3.5） |
| 上次结果弹窗：概览键值 + 逐账号明细表 + 原始 JSON（`showScheduleResult`） | `DetailDrawer`：`KV` + 明细 `DataTable` + `CodeBlock`（§3.3），不再「一坨 JSON」 |
| 结果摘要的 markdown 星号清理 | `parseScheduleResult` 统一去 `**` |
| 「启用/禁用」文字徽章 | 换成 `NSwitch`（P17 要求的「启用开关」），停用前有确认 |
| 表格按平台分组排序（`localeCompare(platform)` 后按 id） | 默认用后端顺序（`Schedule.id ASC`），平台排序改由列头三态排序承担；平台筛选可替代 |
| 执行中的行按钮防重复点击（`_scheduleRequests`） | `runningIds` 集合去重 |
| 「停止于 <时间>」行内提示 | 列 1 第二行 `warn` 文案 |
| 「成长计划执行说明」`<details>` | `HelpTip`（长文案进气泡，05-patterns §6） |
| 旧版 `uiSelect`/`date-time-picker` 的自绘控件 | 统一换成 Naive UI `NSelect` / `NInputNumber` / `NCheckboxGroup` / `NDatePicker`；时区提示文字保留 |
| 无批量操作 | 本页不加批量（重启任务/删任务的批量语义与「立即执行」的耗时差异大，留待需要时再按 BulkBar 规范补） |

---

## 实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 任务列表（任务名 / 类型 / 平台·账号范围 / 执行时间 / 出口代理组 / 最近结果 / 启用 / 操作） | `SchedulesPage.vue` 的 `columns` + `api/schedules.ts:toScheduleRows` | 已实现 |
| 「下次：今天 09:00」展示（今天/明天/后天/日期，停用显示「已停用」） | `formatNextRun` + 列 4 | 已实现 |
| cron/间隔或时刻文案 + 北京时间提示 | `cycle_label` 优先，缺失用 `buildCycleLabel`；`zoneHint` | 已实现 |
| 最近结果 Badge（成功/部分失败/失败/执行中/已顺延/已跳过/旧格式/尚未运行） | `parseScheduleResult` + 列 6 | 已实现 |
| 结果详情抽屉（概览 KV + 明细表 + 原始 JSON） | `DetailDrawer` + `resultKv` / `resultDetailColumns` | 已实现 |
| 启用开关（停用前确认） | 列 7 `NSwitch` + `onToggle` | 已实现 |
| 立即执行 + 执行后刷新该行（running 时 4s 轮询，按钮去重禁用） | `runOne` + `usePolling` | 已实现 |
| 新建 / 编辑（任务名 / 类型 / 周期 / 间隔 / 时间 / 周几 / 几号 / 停止时间 / 代理组 / 并发 / 预算 / stale / 启用） | `FormDrawer` + `buildScheduleInput` | 已实现 |
| 时间字段的时区提示（北京时间 · UTC+8；停止时间「本地时间」） | 抽屉 `hint` | 已实现 |
| 出口代理组下拉来自 `/api/proxies` 的分组 + 跟随全局 | `buildProxyPoolOptions` + `ensureProxies` | 已实现 |
| 全局出口与并发（`GET/PUT /api/schedules/global-proxy`） | 第二个 `FormDrawer` + `openGlobalProxy` / `saveGlobalProxy` | 已实现 |
| 删除确认（danger） | `removeOne` + `useConfirm` | 已实现 |
| 搜索 / 平台 / 类型 / 状态 / 结果筛选 → 路由 query | `FilterBar` + `filterScheduleRows` + `useTableQuery('schedules')` | 已实现 |
| 排序（任务名 / 平台 / 下次执行 / 上次运行，三态） | 列 `sortable` + `sortScheduleRows` | 已实现 |
| 分页（20/50/100，后端无分页 → 客户端） | `paginateScheduleRows` + `useTableQuery` | 已实现（差异：后端一次返回全部，见 §4.1） |
| 四态（首次骨架 / 刷新保留 / 空 / 错误 + 重试） | §2.4 + `NSkeleton` / `ErrorBlock` / `Empty` | 已实现 |
| 轮询（执行中 4s、不可见暂停、失败 3 次停止 + 恢复） | `usePolling` + 横幅 | 已实现 |
| 平台上下文订阅（切换后重拉，卸载取消） | `platformStore.onChange` / `onBeforeUnmount` | 已实现 |
| 手机卡片模式（`mobile` 标注） | `columns` 的 `mobile` 字段 | 已实现 |
| 单元测试（行转换 / 结果解析 / 时间文案 / 筛排分页 / 统计 / 间隔换算 / 表单校验与提交体 / 代理组选项） | `tests/page-schedules.spec.ts` | 已实现 |
