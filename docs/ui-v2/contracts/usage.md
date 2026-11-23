# 功能契约：使用记录页（Usage）

**页面**: P10 / S16 · 路由 `/usage`（页面 key `usage`，分组「调用」）
**设计稿**: `docs/ui-v2/06-pages-c.md` P10、`05-patterns.md` §2 模板 A
**旧版**:
- 结构：`admin/console/index.html` `#panel-usage`（2057–2146 行）
- 逻辑：`admin/console/console-app.js`「使用记录」段（17600–17928 行）
  `initUsageSelects` / `_usageTimeParams` / `applyUsageDates` / `loadUsageFilterOptions` /
  `_usageQuery` / `_usageEmpty` / `loadUsageStats` / `donutChart`（17449）/ `trendChart`（17541）
- 入口：`activateTab("usage")`（1536）、平台切换后重载（3340）
**后端**:
- `GET /api/stats/usage` → `admin/routers/stats.py:usage_stats`（356）/ `_usage_aggregates`（463）
- `GET /api/stats/usage/options` → `admin/routers/stats.py:usage_filter_options`（738）/ `_usage_filter_options_agg`（763）
- 时间窗：`_time_filters`（213）；缓存：`_shared_cached`（L1+L2+单飞，TTL 8–60s）
**新版**: `admin/ui/src/pages/UsagePage.vue` + `admin/ui/src/api/usage.ts`

> 旧版副标题（保留进 ⓘ 说明气泡）：
> 「统计平台全部 Key 的用量：请求数、Token、积分与平均耗时。数据来自本地 usage_logs，随请求实时累计。」

---

## 一、区块（按设计稿 P10 顺序）

| # | 区块 | 旧版对应 | 新版位置 |
|---|---|---|---|
| 1 | 页头「使用记录」+ 导出 / 刷新 | `panel-toolbar` 2058–2065 | `PageHeader` + `#actions` |
| 2 | FilterBar：平台 · Key · 模型 · 时间范围 · 端点（+ 账号，见 §七）+ 已筛选 chips + 汇总 | `usage-filter-card` 2070–2103 | `FilterBar` |
| 3 | StatStrip：请求数 / 成功 / 失败 / Token 输入 / Token 输出 / 积分 | `usageOverview` 2105 | `StatStrip`（6 格） |
| 4 | Token 使用趋势（按时间分桶的堆叠面积） | `usageTrendChart` 2135–2138 + `trendChart` | `Card` + `Chart` |
| 5 | 模型分布 / Key 使用分布 / 端点分布（BarList） | 2110–2124, 2126–2132 | 3 列 BarList 卡片 |
| 6 | 账号分布（成功请求数）/ 请求结果分布（全部请求）（BarList） | 2141–2144 + `donutChart` | 2 列 BarList 卡片 |

---

## 二、数据展示

### 2.1 统计卡（`overview`）

| 标签 | 字段 | 格式 | 着色/脚注 |
|---|---|---|---|
| 请求数 | `overview.requests` | `fmtInt` | 脚注「平均耗时 `fmtDuration(avg_latency_ms)`」；0 时脚注「—」 |
| 成功 | `overview.ok_requests` | `fmtInt` | `ok`；脚注「成功率 `fmtPct(ok/requests)`」 |
| 失败 | `overview.failed_requests` | `fmtInt` | >0 → `danger`，否则 `muted`；脚注「失败率 …」 |
| Token 输入 | `overview.prompt_tokens` | `fmtCompact` | 脚注「缓存命中 `fmtCompact(cached_tokens)`」 |
| Token 输出 | `overview.completion_tokens` | `fmtCompact` | 脚注「合计 `fmtCompact(total_tokens)`」 |
| 积分 | `overview.credits` | `fmtCredit` | 脚注「平均每次 `fmtCredit(credits/requests)`」 |

口径（后端注释）：**成功 = `error_kind in ('', 'success')`**；Token / 积分只算成功请求；`requests` 含失败。
`help` 气泡里写清这一条，避免「卡片加起来不等于请求数」的疑问。

### 2.2 分布（BarList）

| 卡片 | 源字段 | 数值 | 提示（卡片右侧） | 行点击 |
|---|---|---|---|---|
| 模型分布 | `by_model[]`（`label/model/platform/platform_label/requests/tokens/credits`） | 见 §2.3 | 「按积分」/「按成功请求数」 | `/logs?model=<原始 model id>` |
| Key 使用分布 | `by_key[]`（只有 `label`，值为 Key 名称或 `#id`） | 同上 | 同上 | `/logs?key=<Key 名称>`（`#id` 行不可点，见 §六.4） |
| 端点分布 | `by_endpoint[]`（`label` = `use_case`，空值后端已补 `chat`） | 同上 | 同上 | 不可点（`/api/logs` 无 use_case 参数，见 §六.5） |
| 账号分布 | `by_account[]`（`label/account_id/others`；后端 SQL 侧 Top 8 + 「其他 N 个账号」） | 请求数 | 「按成功请求数」 | `/logs?account=<账号名>`（`account_id=null` / `others` 行不可点） |
| 请求结果分布 | `by_result[]`（`label` 成功或 error_kind；全部请求含失败） | 请求数 | 「全部请求」 | 成功 → `/logs?result=success`，其余 → `/logs?result=error` |

- BarList 一次最多 8 项 + 「其他 N 项」（组件行为），后端 `by_account` 已先收敛一次。
- 数值统一 `fmtCompact`（请求）/ `fmtCredit`（积分）；占比由 BarList 计算。
- 行点击的说明写进卡片副标题：「点击行看日志」。

### 2.3 分布数值口径（旧版 `loadUsageStats` 17914–17921）

- 每张卡片独立判断：行里**有任意 `credits > 0` → 按积分画**；否则按请求数画。
- 卡片右侧同时显示当前口径（「按积分」/「按成功请求数」），避免两份图不可比。
- 后端分布行只统计成功请求（`_ok_filter`），所以口径文案写「成功请求数」而不是「请求数」。

### 2.4 Token 使用趋势（`trend[]`）

- 字段：`bucket / prompt_tokens / completion_tokens / cached_tokens / credits / requests`。
- 画法：ECharts **堆叠面积**（`stack: 'tokens'`），两条系列「输入」「输出」，x 轴为 `bucket`（`YYYY-MM-DD HH:00` 或 `YYYY-MM-DD`，显示时裁成 `MM-DD HH:00` / `MM-DD`）。
- 粒度：`granularity=hour|day`，默认 `hour`；窗口 > 3 天且用户没显式选过时自动 `day`（query 里保存 `grain`）。
- 没有趋势点 → 卡片内 `Empty`「该时间范围内没有任何请求记录」。
- 旧版只画输入/输出两条（不加 cached），保持一致；`cached_tokens` 只进统计卡脚注。

---

## 三、交互

| 控件 | 旧版行为 | 新版行为 |
|---|---|---|
| 平台筛选 | 选项 = 窗口内出现过的平台（含「全部平台（全网关）」）；**不传 X-Platform 口径** | 同；选项来自 `options.platforms`（`value`=`storage platform`，`label` 含产品名/区域）；缺省回落到平台 store 的清单 |
| Key 筛选 | 选项 = 全部 Key（`id/name`），值传 `key_id` | 同（`key_id` 数字） |
| 模型筛选 | 值传**落库 id**，显示名；选项只列窗口内出现过的模型 | 同（`options.models[].value/label`） |
| 账号筛选（见 §七） | 选项 = 窗口内账号 + 全部账号，值传 `account_id` | 同 |
| 端点筛选 | `use_case` 字符串 | 同 |
| 时间范围 | 「今日（自然日）」/ 近 N 天 / 自定义日期（含结束当天）；开始 > 结束报错 | `DateRange`（datetimerange + 今日/昨日/近 7 天/近 30 天快捷项）；默认**今日（自然日）= 北京时间 00:00 起**；清空 = 回到默认；开始晚于结束 → error Toast「开始时间不能晚于结束时间」且不改筛选 |
| 趋势粒度 | 「按小时 / 按天」下拉 | 趋势卡右上 `Segmented`（按小时 / 按天），写进 query `grain` |
| 刷新 | 重查并重画 | `刷新` 按钮：`refresh=1` 跳过后端缓存，保留旧数据只转圈 |
| 导出（设计稿新增） | 旧版没有导出 | 前端把当前筛选下的聚合导出为 CSV（`miraihub-usage-<时间戳>.csv`）：概览一行 + 模型/Key/端点/账号/结果/趋势各一段；不发新接口（后端无 usage 导出接口） |
| 清除筛选 / chips | `resetUsageFilters`：清空所有筛选并回到今日 | FilterBar 的「重置」与 chips 上的 × 都回到默认（今日 + 全部） |
| BarList 行点击 | 无（旧版只有平台分布条可下钻） | 按 §2.2 跳 `/logs`，**带上当前时间范围**（`start`/`end` ISO）；无键盘可达性 → 卡片同时提供 `查看日志` quiet 按钮（跳当前第一行） |
| 平台上下文变化 | 触发 `loadUsageStats()`（3340） | 订阅 `usePlatformStore().onChange`：选择真的变了才重载（options + stats），卸载取消。口径仍是**全网关**（后端只看显式 `?platform=`） |

**Toast 文案**
- 加载失败：`加载使用记录失败：<后端 detail>`（ErrorBlock 同步展示，可展开 detail）
- 导出成功：`已导出使用记录 CSV`；无数据：`导出失败：当前筛选下没有数据`
- 时间范围非法：`开始时间不能晚于结束时间`

---

## 四、接口

> 全部走 `@/api/http` 的 `http<T>()`（自动带 `X-CSRF-Token` 与 `X-Platform`）；页面不直接 fetch。

### 4.1 GET `/api/stats/usage`

- **调用时机**：进入页面、任一筛选/时间范围/粒度变化、刷新按钮、平台上下文变化、错误重试。
- **参数**（`admin/routers/stats.py:usage_stats`）：
  - `days`（默认 1，滚动窗口；有 `start` 时后端忽略）
  - `start`/`end`（ISO 8601；`_time_filters` 转 UTC naive 后比较；`start>end` → HTTP 400）
  - `granularity`：`hour`（默认）| `day`
  - `key_id`（int）、`model`（**精确匹配落库 id**）、`account_id`（int）、`use_case`（str）
  - `platform`、`realm`：只认显式 query（`all`/`*` 视为全部）；**不看 X-Platform 头**
  - `refresh=1`：跳过缓存读，照常写缓存
- **响应关键字段**：

```jsonc
{
  "range_days": null, "start": "2026-11-22T16:00:00", "end": null,
  "granularity": "hour",
  "filters": { "key_id": null, "model": null, "account_id": null, "use_case": null, "platform": "" },
  "platform": "", "platform_label": "全部平台",
  "platforms": [{ "value": "trae", "label": "Trae 国内版", "requests": 175, "tokens": 1200, "credits": 9.5 }],
  "overview": {
    "requests": 862, "ok_requests": 850, "failed_requests": 12, "credits": 729.31,
    "prompt_tokens": 1200000, "completion_tokens": 340000, "total_tokens": 1540000,
    "cached_tokens": 90000, "avg_latency_ms": 3950
  },
  "by_model": [{ "label": "DeepSeek-V4.1-Flash", "model": "dfmodel", "platform": "qoder", "platform_label": "Qoder 中国版", "requests": 120, "tokens": 9000, "credits": 12.5 }],
  "by_endpoint": [{ "label": "chat", "requests": 800, "tokens": 1000, "credits": 100.0 }],
  "by_key": [{ "label": "prod-key", "requests": 700, "tokens": 1000, "credits": 100.0 }],
  "by_account": [{ "label": "193****2486", "requests": 340, "tokens": 1000, "credits": 9.0, "account_id": 152 },
                 { "label": "其他 12 个账号", "requests": 60, "tokens": 10, "credits": 1.0, "account_id": null, "others": true }],
  "by_result": [{ "label": "成功", "requests": 850 }, { "label": "rate_limit", "requests": 12 }],
  "account_models": [],
  "usage_scope": "gateway_only",
  "trend": [{ "bucket": "2026-11-23 10:00", "prompt_tokens": 1000, "completion_tokens": 300, "cached_tokens": 0, "credits": 1.2, "requests": 12 }]
}
```

- **本页消费**：`overview` 全部字段、`by_model/by_key/by_endpoint/by_account/by_result`、`trend`、`filters`（回填筛选）、`platform_label`（摘要）。
- **不消费**：`platforms[]`（平台筛选改用 options，见 §4.2；仅作兜底）、`account_models[]`（旧版「所选账号 · 模型用量与限额」表，见 §七）、`usage_scope`。
- **缓存**：后端按条件 TTL 8/30/60s（`_stats_ttl_for_days`），刷新按钮用 `refresh=1` 绕过。

### 4.2 GET `/api/stats/usage/options`

- **调用时机**：进入页面、时间范围变化（窗口变了选项要变）、刷新、平台上下文变化。
- **参数**：`days` / `start` / `end`（与 4.1 同一套时间窗；后端 30s 缓存）。
- **响应**：

```jsonc
{
  "models": [{ "value": "dfmodel", "label": "DeepSeek-V4.1-Flash", "requests": 120 }],
  "use_cases": ["chat", "responses"],
  "keys": [{ "id": 3, "name": "prod-key" }],
  "accounts": [{ "id": 152, "name": "193****2486" }],
  "platforms": [{ "value": "qoder", "label": "Qoder 中国版", "requests": 120 }]
}
```

- 选项只含**窗口内真实出现过**的值（避免「选了必然 0 条」）。
- 切换时间窗后，若当前选中的值已不在选项里 → 回落「全部」（旧版 `keep()` 的逻辑），否则会出现「标签写全部、实际仍在筛选」的假象。

---

## 五、跨页联动

- **/logs 深链**（S15 契约 §7「支持从其他页带 query 跳入」）：`/logs?model=&key=&account=&result=&start=&end=`
  - `model` 用**落库 id**（`/api/logs` 精确匹配 `UsageLog.model`）
  - `key`/`account` 用**名称模糊**（`logs.py:720-729` 的 `_resolve_ids_by_name`）
  - `result` 只支持 `success|error`（`logs.py:752-756`）
  - 时间范围按当前窗口换算成 ISO `start`/`end`：窗口是默认「今日」时只带 `start`（与旧版一致）
- **平台上下文**：`/usage` 是**全网关口径**（后端 docstring 356-388：只有使用记录/用量日志/风控哨兵不跟随 X-Platform）。页面仍订阅 `onChange` 以便平台清单/选项变化后刷新，但不把当前平台塞进 `platform` 筛选。
- **侧栏页签**：同组「调用」的 使用记录 / 用量日志 / API Keys / IP 管理 由 `PageHeader` 自动渲染，本页不自己写页签。
- 无全局事件、无命令面板动作。

---

## 六、边界情况

1. **首次加载**：4 个区域各自的骨架（统一 `NSkeleton` 样式，不闪烁）；页头操作可用。
2. **刷新中**：保留旧数据，刷新按钮 loading、FilterBar 右侧 loading；不闪屏。
3. **空**：`overview.requests = 0` 或 `trend` 为空 → 每张卡片 `Empty`：
   - 有筛选：`当前筛选条件（<条件>）下没有记录，试试放宽时间范围或清除筛选` + 「清除筛选」
   - 无筛选：`该时间范围内没有任何请求记录` + 「刷新」
4. **Key 名称缺失**：后端 `by_key` 对查不到名字的 Key 返回 `#<id>`；这种行不可点（`/api/logs` 按名称模糊查，`#12` 匹配不到），补一句 title「这个 Key 已删除或改名，无法按名称跳日志」。
5. **端点行不可点**：`/api/logs` 没有 `use_case` 参数（`logs.py:839-856`），强行跳过去只会得到无筛选的日志页；卡片说明写「端点维度暂不支持跳日志」。
6. **其他 / 未分配账号行**：`account_id=null` 或 `others=true` 不可点。
7. **请求结果行**：除「成功」外的 error_kind（如 `rate_limit`）统一跳 `result=error`（`/api/logs` 只有成功/错误两档），卡片说明写清。
8. **时间窗**：默认「今日（自然日，北京时间 00:00 起）」；只给 `start` 不给 `end` 时后端按 `<= 无上界` 处理（= 到当前）。清空 DateRange 回到默认。
9. **旧版日期输入**：旧版是两个 `<input type=date>` + 「查询」；新版用 `DateRange`（选择即查询，无「查询」按钮），语义保持「包含结束当天」（`DateRange` 的 end 是精确时刻，快捷项「今日」到 now）。
10. **32 位/大数**：Token 用 `fmtCompact`（K/M/B），积分 `fmtCredit`；精确值进 `title`/脚注。
11. **账号/Key 上千**：BarList 只画 8 项 + 「其他」，后端 `by_account` 已 Top 8 收敛，不会出现几百行。
12. **后端 400（start > end）**：前端先拦（Toast + 不改筛选）；若仍被后端拒绝，ErrorBlock 展示 detail。
13. **手机 375px**：StatStrip 变 2 列（组件内置）；分布卡片 1 列；FilterBar 控件换行；趋势图随宽度自适应（`Chart` autoresize）。
14. **深色**：只用 `tokens.css` 变量；ECharts 主题由 `Chart` 组件按 `--chart-*` 重建。

---

## 七、旧版有但设计稿没写到的功能，以及新版落位

| 功能 | 旧版位置 | 新版落位 | 状态 |
|---|---|---|---|
| 账号筛选（按账号看用量） | `usageAccountSel` 2084–2085、17761 | FilterBar 增加「账号」下拉（值是 `account_id`） | 保留 |
| 趋势粒度「按小时 / 按天」 | `usageGrainSel` 2062、17654 | 趋势卡右上 `Segmented`，query `grain` | 保留 |
| 平台分布条（可点下钻） | `usagePlatformStrip` 2108、17879–17902 | 由 FilterBar 的「平台」下拉承载（点条 = 选下拉），不再单独画一条 | 有差异（少一处视觉入口，筛选行为等价） |
| 「所选账号 · 模型用量与限额」表（`account_models`） | 17864–17869 | 未实现：S16 任务书与 P10 设计稿均未列，且依赖 `_active_model_limits`；本页不做 | 有差异（任务范围外，已在 §4.1 标注不消费字段） |
| 平均耗时 | 17877 | 进「请求数」卡脚注 | 保留 |

---

## 八、新版取舍

1. **导出是前端 CSV**：后端没有 usage 导出接口（`/api/logs/export` 导的是明细日志，不是聚合），所以把当前聚合渲染成 CSV；明细导出仍走 `/logs` 页。
2. **BarList 行点击用容器事件委托**：`BarList` 组件（S05 定稿）没有 click 事件，页面在包装层按 `li` → `.mh-barlist__label` 取标签再映射回源行；键盘可达性由卡片上的「查看日志」按钮兜底。
3. **默认窗口在 query 为空时按「今日」现场计算**：不把默认值写进 URL（分享链接仍是干净的 `/usage`），用户一旦选时间范围才写入 `start`/`end`。
4. **平台上下文只触发刷新**：不把当前平台当默认筛选（后端明确 usage 是全网关口末）。

---

## 实现对照

| 契约功能 | 新版位置 | 状态 |
|---|---|---|
| 页头标题 + 旧版副标题（ⓘ）+ 导出 / 刷新 | `UsagePage.vue` → `PageHeader` `#actions` | 已实现 |
| FilterBar：平台 / Key / 模型 / 时间范围 / 端点 / 账号 | `UsagePage.vue` 过滤器区 + `api/usage.ts: toUsageParams` | 已实现 |
| 已筛选 chips + 汇总（共 N 次 · 成功 · 失败 · 积分） | `api/usage.ts: usageChips/usageSummary` | 已实现 |
| StatStrip 6 格（请求/成功/失败/Token 入/Token 出/积分） | `api/usage.ts: toStatItems` | 已实现（`:columns="6"`，移动端组件内置 2 列） |
| Token 使用趋势（堆叠面积，按小时/按天） | `api/usage.ts: toTrendOption` + `Chart` | 已实现 |
| 模型分布 BarList（按积分/请求自动口径） | `api/usage.ts: toBarItems/metricOf` | 已实现 |
| Key 使用分布 BarList | 同上 | 已实现 |
| 端点分布 BarList | 同上 | 已实现 |
| 账号分布 BarList（成功请求数 + 其他账号行） | 同上 | 已实现 |
| 请求结果分布 BarList（全部请求） | 同上 | 已实现 |
| BarList 行点击 → /logs（model / key / result / account + 时间范围） | `api/usage.ts: logsLinkFor` + 卡片包装层 | 已实现 |
| 导出 CSV + 刷新（refresh=1） | `api/usage.ts: usageCsv/saveText` + 页头按钮 | 已实现 |
| 平台上下文订阅（onChange + 卸载取消） | `UsagePage.vue` `onMounted/onBeforeUnmount` | 已实现 |
| 四态（首次加载骨架 / 刷新保留 / 空 / 错误） | `UsagePage.vue` 各卡片 | 已实现 |
| 筛选/粒度同步路由 query（useTableQuery） | `useTableQuery('usage', …)` | 已实现 |
| 手机 375px（StatStrip 2 列 / 分布 1 列） | `UsagePage.vue` 媒体查询 | 已实现 |
| 窗口内选项回落「全部」（旧版 keep） | `api/usage.ts: reconcileFilters` | 已实现 |
| 账号筛选 | FilterBar 账号下拉 | 已实现 |
| 平台分布条（可点下钻） | 由平台下拉承载 | 有差异（§七） |
| 所选账号模型限额表 `account_models` | 未实现 | 有差异（任务范围外，§七） |
| 单元测试（响应形状 → 展示数据） | `admin/ui/tests/page-usage.spec.ts` | 已实现 |
