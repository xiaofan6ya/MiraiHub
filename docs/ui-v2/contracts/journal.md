# 功能契约：积分流水（Journal）

- **页面 key / 路由**：`journal` → `/journal`（hash 路由：`/admin/v2/#/journal`）
- **设计稿**：`docs/ui-v2/06-pages-b.md` P8（模板 B：PageHeader + FilterBar + DataTable + 游标分页）
- **旧版位置**：`admin/console/index.html#panel-journal`（约 2561–2576 行）、`admin/console/review-improvements.js`（`WBReview.loadJournal` / `resetJournal` / `journalRow` / `sourceLabel` / `journalPage`）
- **后端**：`admin/routers/maintenance.py:credit_events`（模型 `admin/models.py:CreditEvent`）、平台作用域 `admin/platform/scope.py`
- **新版实现**：`admin/ui/src/pages/JournalPage.vue` + `admin/ui/src/api/journal.ts`

---

## 一、区块

| # | 区块 | 内容 |
|---|---|---|
| 1 | 页头 | 标题「积分流水」+ ⓘ（旧版副标题与「确认奖励与余额观察分别记录」说明）+「刷新」「导出」 |
| 2 | FilterBar | 平台 · 账号 · 类型 · 来源 · 时间范围 · 搜索；右侧「重置」；下方「已筛选」chips + 结果统计 |
| 3 | DataTable | 时间 · 账号 · 平台 · 类型 Badge · 变动（带符号着色）· 余额 · 说明 |
| 4 | 分页 | 上一页 / 第 n 页（+ 页数）/ 下一页；「快速翻页（不统计总数）」开关 |

---

## 二、数据展示

| 展示项 | 来源字段 | 格式 / 规则 |
|---|---|---|
| 时间 | `created_at`（ISO，后端补 Z） | `fmtTime`（今天 `HH:mm:ss`、今年 `MM-DD HH:mm`、更早带年份），悬停显示完整 ISO |
| 账号 | `account_name` + `account_id` | `名称 #id`；名称空时用「账号 #id」 |
| 平台 | —（**后端没有平台字段**） | 显示当前平台上下文的展示名（如 WorkBuddy）；跨平台无法逐行归属，这一点在 ⓘ 与页脚写明 |
| 类型 | `kind` | Badge：`confirmed` →「确认奖励」ok；`observed` →「余额观察」brand；未知 kind → neutral + 原值 |
| 变动 | `delta`（整数） | 正数前加 `+`，等宽 tabular；正数 `--ok`、负数 `--danger`、0 `--fg-muted` |
| 余额 | `balance_after` | `fmtInt`；`null` → 「未记录」（`--fg-faint`，悬停「历史记录未保存余额」） |
| 说明 | `kind` + `source` | `确认奖励 / 余额观察` + ` · ` + 来源中文（`schedule/manual/task` → 定时/手动/任务）；悬停显示原始 source |
| 结果统计 | 当前页行 + 服务端 `total` | 「共 N 条 · 本页净变化 ±X · 确认奖励 A 笔 · 余额观察 B 笔」 |
| 行数 | `page_size` | 默认 50（可选 20 / 50 / 100） |

---

## 三、交互

| 交互 | 文案 | 行为 |
|---|---|---|
| 平台 | 平台下拉（各平台） | **切全局平台上下文**（`usePlatformStore().setCurrent`）→ 触发 `onChange` → 回到第 1 页重新取数。原因：`CreditEvent` 没有平台列，后端按 `X-Platform` 收窄 |
| 账号 | 「全部账号 / 名称 · #id」 | `account_id` 查询参数 |
| 类型 | 「全部类型 / 确认奖励 / 余额观察」 | `kind` 查询参数（后端只接受这两个值，非法值 400） |
| 来源 | 「全部来源 / 定时 · 每日签到 …」 | `source` 精确匹配（来自响应 `sources[]`） |
| 时间范围 | NDatePicker 区间 + 今日/昨日/近 7 天/近 30 天 | 转 ISO 传给 `start`/`end`；开始晚于结束时后端 400，页面 Toast 后端 detail |
| 搜索 | 「搜索账号 / 来源 / 说明（当前页）」 | 300ms 防抖；**只在当前页做客户端过滤**（后端无 search 参数），页脚注明 |
| 筛选 chips | 已筛选：账号/类型/来源/时间/搜索 | 点击移除单个条件；「重置」清空全部 |
| 刷新 | 「刷新」 | 重新请求当前页，保留旧数据 + 表格 loading |
| 上一页 / 下一页 | 「上一页」「下一页」 | 偏移分页（`page`）；到边界禁用 |
| 快速翻页开关 | 「快速翻页（不统计总数）」 | 本地偏好（`mh.journal.fast`）；开启后隐藏总数与页数上限、下一页按「本页是否满」判断。**后端仍会 COUNT**（见「实现对照」） |
| 导出 | 「导出」 | 前端把当前页导出 CSV（UTF-8 BOM），文件名 `miraihub-journal-YYYYMMDD-HHmmss.csv`；空页提示「导出失败：当前页没有可导出的记录」；成功 Toast「已导出当前页 N 条流水」 |
| 排序 | 表头时间 / 变动 / 余额 | 只排当前页（后端不支持排序参数），同步 `?sort=&order=`，页脚注明 |

所有筛选与分页写进路由 query：`?account=&kind=&source=&q=&start=&end=&page=&size=&sort=&order=`（`useTableQuery('journal')`），刷新与分享不丢。

---

## 四、接口

| 方法 | 路径 | 参数 | 响应字段 | 调用时机 | 后端处理函数 |
|---|---|---|---|---|---|
| GET | `/api/maintenance/credit-events` | `account_id?: int`、`source?: str`、`kind?: confirmed\|observed`、`start?/end?: ISO datetime`、`page>=1`、`page_size 1..200`（默认 50） | `items[]`：`id` `account_id` `account_name` `kind` `source` `delta` `balance_after` `created_at`；以及 `total` `page` `page_size` `importing` `sources[]` `accounts[{id,name}]` | 进入页面、筛选/分页变化、平台切换、刷新 | `admin/routers/maintenance.py:credit_events`（`admin.credit_journal.backfill` + `platform.scope`） |
| GET | `/api/platforms` | — | 平台清单（id / label / storage_platform） | 页面初始化（`usePlatformStore().load()`） | `admin/platform/router.py` |

**错误语义**：`400`（kind 非法 / 开始晚于结束）、`401`（`http` 层跳登录）、其余按 `<动作>失败：<detail>` Toast；首次加载失败显示 `ErrorBlock` + 重试。

---

## 五、跨页联动

- **平台上下文**：本页在 `PLATFORM_SCOPED_KEYS` 里，订阅 `usePlatformStore().onChange`（`onBeforeUnmount` 取消）。页面内的「平台」下拉就是切这个上下文，与顶栏选择器等价。
- **积分到期 / 概览**：无直接跳转；流水里的账号名与「账号池」同源（`CreditEvent.account_name` 是写入时快照）。
- **导出**：不经过后端（没有流水导出接口），不与他人文件耦合。
- **来源链接**：无（旧版也没有）。

---

## 六、边界情况

| 场景 | 表现 |
|---|---|
| 首次加载 | 6 行骨架（`n-skeleton`），不闪屏 |
| 刷新中 | 保留旧数据 + 表格 loading + 「刷新」按钮 loading |
| 空（无记录） | `Empty`「还没有流水记录。账号刷新或签到后会自动记账。」 |
| 空（筛选后） | `Empty`「没有符合条件的流水…」+「清除筛选」 |
| 错误（首次） | `ErrorBlock`「加载积分流水失败：<原因>」+ 重试（可展开 detail） |
| 错误（刷新） | 保留旧数据，只 Toast「刷新积分流水失败：<原因>」 |
| `importing=true` | 表格上方 warn 文案「历史记录导入中，请刷新继续。」 |
| `balance_after=null` | 「未记录」（`--fg-faint`），不显示 0 冒充 |
| 时间区间非法 | 交给后端 400，Toast 显示后端 detail（开始不能晚于结束） |
| 快速翻页 | 不显示总数/页数；下一页按「本页满」判断，可能点到末页后再点一次为空页 |
| 手机 375px | DataTable 卡片模式：时间=title、账号=subtitle、其余=meta；筛选控件与分页按钮换行、下拉全宽 |

---

## 七、旧版有但设计稿没写到的功能

| 旧版功能 | 新版位置 | 说明 |
|---|---|---|
| 来源筛选（`journalSource`） | FilterBar「来源」 | 保留（设计稿只写了「类型」，但来源是旧版主筛选之一） |
| 记录类型 confirmed / observed | FilterBar「类型」+ 表格 Badge | 保留 |
| 「确认奖励与余额观察分别记录，不能相加统计」提示 | ⓘ + 页脚说明 | 保留原文语义 |
| 开始/结束时间校验 | 后端 400 + Toast | 旧版前端先比较再提示，新版交给后端并展示 detail |
| 分页页数上限 | 页脚「/ N 页」 | 保留（关闭快速翻页时显示） |
| 「历史记录导入中」提示 | 表格上方 warn | 保留 |

---

## 八、实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| FilterBar 平台 / 账号 / 类型 / 时间范围 / 搜索 | `JournalPage.vue` + FilterBar | 已实现（另保留旧版「来源」筛选）；平台 = 切全局上下文（后端无平台字段，见 §2） |
| 已筛选 chips + 结果统计 | FilterBar `chips` / `summary` | 已实现：「共 N 条 · 本页净变化 ±X · 确认奖励 A 笔 · 余额观察 B 笔」 |
| DataTable 时间/账号/平台/类型 Badge/变动/余额/说明 | `columns` + `api/journal.ts:toJournalRow` | 已实现（平台列取当前平台作用域展示名；后端没有逐行平台字段） |
| 变动：正数 ok、负数 danger、带符号等宽 | `cellDelta` / `deltaTone` | 已实现（0 用中性色，不去抢状态色） |
| 类型 Badge | `cellKind` / `kindMeta` | 已实现：确认奖励 ok / 余额观察 brand / 未知 neutral |
| 余额未记录 | `cellBalance` | 已实现：「未记录」+ 悬停说明 |
| 游标分页上一页/下一页 | 页脚 `goPage` + `pageState` | 已实现；**有差异**：后端是 `page/page_size` 偏移分页（旧版也是如此），不是真游标；`journalPrev/journalNext` 的语义（第 n 页前后翻）保持一致 |
| 快速翻页（不统计总数）开关 | 页脚 `n-switch` | 已实现（有差异）：开关只影响前端是否显示总数与是否按「本页满」判断下一页；`credit-events` 每次都执行 `COUNT(*)`，前端无法让后端跳过统计。契约写明，不伪造能力 |
| 导出 | 页头「导出」→ `toCsv` + `saveCsv` | 已实现（有差异）：后端没有流水导出接口，导出**当前页** CSV（UTF-8 BOM，Excel 可读中文），页脚与 Toast 说明范围 |
| 排序同步 query | 表头时间/变动/余额 + `?sort=&order=` | 已实现（有差异）：只排当前页（后端按时间倒序、无排序参数），页脚注明 |
| 四态 | 页面 | 已实现：骨架 / 刷新保留 / 空（无记录 + 筛选后两种）/ 错误（首次 ErrorBlock、刷新 Toast） |
| 筛选与分页同步路由 | `useTableQuery('journal')` | 已实现：`?account=&kind=&source=&q=&start=&end=&page=&size=&sort=&order=` |
| 平台上下文订阅 | `usePlatformStore().onChange` + `onBeforeUnmount` 取消 | 已实现：切换后回第 1 页重新取数 |
| 手机 375px | DataTable `mobile` 标注 + 媒体查询 | 已实现：时间=title、账号=subtitle、其余 meta；控件全宽、分页换行 |
| 历史导入中提示 | `importingHint` | 已实现（与旧版 `journalHint` 同语义） |
| 「来源」前缀中文化 | `sourceLabel` | 已实现：schedule/manual/task → 定时/手动/任务 |

### 无法验证

- 真实后端联调（本环境无 8790 服务）：查询参数、平台作用域、分页边界、`importing` 都只用单测与类型检查验证。
- 真实数据量下的表现：几十万条 `credit_events` 的 `COUNT(*)` 延迟（决定「快速翻页」是否真的必要）、历史账号名与现存账号的差异。
- 导出 CSV 在 Excel / WPS 下的中文显示（BOM 已加，但未实机打开）。
- `platform.scope` 的跨平台行为：本页只能按当前平台上下文取数，无法验证「一次看全部平台」的流水（后端没有该能力）。
