# 05 页面模式与通用规则

## 1. 接口调用（`src/api/`）

- `src/api/http.ts` 提供唯一的请求函数 `http<T>(method, path, { query, body, signal })`：
  - `credentials: 'same-origin'`。
  - 非 GET/HEAD/OPTIONS 请求自动加请求头 `X-CSRF-Token`，值取 Cookie `wb_csrf`（与旧版 `admin/console/secure-fetch.js` 完全一致）。
  - 响应 401 → 调用外壳的 `onUnauthorized()`（跳转 `/admin`）。
  - 非 2xx → 抛 `ApiError { status, message, detail }`，`message` 依次取响应 JSON 的 `detail`（字符串，或 FastAPI 校验错误数组的第一个 `msg`）、`message`、`error`，都没有时用「请求失败（HTTP 502）」。
  - 下载类接口用 `download(path, query, filename)`：fetch → blob → 触发下载，文件名优先取 `Content-Disposition`。
  - 流式接口（SSE / NDJSON）用 `stream(path, body, onEvent, signal)`，格式以旧代码为准。
- **每个业务域一个文件**：`src/api/accounts.ts`、`keys.ts`、`logs.ts`…，导出带类型的函数。类型定义写在同文件，字段名与后端保持一致（snake_case 原样保留，不做转换）。
- 页面里**禁止**直接 `fetch`。

## 2. 页面模板

所有页面都是下面三种模板之一：

### A. 仪表盘型（overview、sentinel、stats、gateway、credits、market）
```
PageHeader（标题 + 时间范围 Segmented + 刷新）
StatStrip（3–5 个关键数）
主视觉区（RouteRibbon 或主图表）            | 侧栏卡片（列表/排行）  ← 栅格 8:4，≤1280 时上下堆叠
次级卡片网格（2 列）
```

### B. 列表管理型（accounts、keys、ip、models、groups、schedules、journal、logs、usage）
```
PageHeader（标题 + 次要操作 + 主操作「添加 / 创建」）
[可选] StatStrip 或 状态页签（全部 12 / 已启用 10 / 已禁用 2）
FilterBar
BulkBar（选中时）
DataTable（Card flush 包裹）+ 分页
FormDrawer / DetailDrawer（按需）
```

### C. 设置型（client、sync、alerts、settings、maintenance、openentry）
```
PageHeader
左侧 200px 锚点目录（页内各区块，滚动高亮） | 右侧区块卡片，最大宽 880px
每个卡片 = 标题 + 一句说明 + 表单项；底部「保存」只保存本卡片
```
设置型页面：未保存的修改在卡片标题旁显示 `Badge warn`「未保存」，离开页面前弹确认「有未保存的修改，确定离开？」。

## 3. 状态

每个取数区域必须处理四种状态：
| 状态 | 表现 |
|---|---|
| 首次加载 | 卡片内显示骨架（灰色块，统一一种样式，不闪烁或只做 1.2s 缓慢明暗） |
| 刷新中 | 保留旧数据，卡片标题右侧 14px 转圈 |
| 空 | `Empty`：说明 + 下一步按钮 |
| 错误 | `ErrorBlock`：错误信息 + 重试。局部失败不影响其他卡片 |

轮询页面（哨兵、日志「请求动态」、任务进度等）：页面不可见时暂停；连续失败 3 次后停止并显示「自动刷新已暂停：<原因>」+「恢复」按钮。

## 4. 格式化（`src/utils/format.ts`，全站统一）

| 函数 | 规则 | 示例 |
|---|---|---|
| `fmtCompact(n)` | <1万 原样加千分位；≥1万 用 K/M/B，保留 1 位小数（与旧版一致，旧版就是 2.3M / 150.9K） | `12,954` `2.3M` `150.9K` |
| `fmtInt(n)` | 千分位 | `17,583` |
| `fmtCredit(n)` | 积分：整数显示整数，小数最多 2 位 | `9.55` `789.31` |
| `fmtPct(x, d=1)` | 输入 0–1 或 0–100 要按后端字段确认；输出 `95.0%` | |
| `fmtDuration(ms)` | <1s 显示 `850ms`；否则 `3.95s`；≥60s `1m 30s` | |
| `fmtTime(ts)` | 今天：`22:27:36`；今年：`10-30 22:27`；更早：`2025-10-30 22:27` | |
| `fmtRelative(ts)` | `刚刚` / `3 分钟前` / `2 小时前` / `昨天` / 日期；悬停 tooltip 显示完整时间 | |
| `fmtExpiry(ts)` | `15 天后到期`（>7 天 neutral，≤7 天 warn，已过期 danger「已过期 2 天」） | |
| `maskSecret(s)` | 保留前 6 后 4 | `sk-mh3…9f2e` |

空值统一显示 `—`（`--fg-faint`），不显示 `0` 冒充无数据，也不显示 `null`/`undefined`。

## 5. 平台相关

- 平台 logo：`<PlatformLogo :id="platformId" :size="20" />`，映射逻辑从旧版 `admin/console/platform-icons.js` 移植，图片放 `admin/ui/public/logos/`。
- 平台名称、区域、能力（支持哪些账号操作）**以后端返回为准**，不要在前端硬编码平台列表。旧版中若存在按平台 id 硬编码的分支（例如某平台特有按钮），移植时集中放在 `src/platforms/<id>.ts` 里，用一个 `platformFeatures(id)` 函数统一查询，页面里不要散落 `if (id === 'trae')`。

## 6. 文案规则（摘要，完整见 01-direction §8）

- 按钮：动词开头，2–4 字。「添加账号」「刷新额度」「导出」「保存」。
- 确认框：问句标题 + 后果说明 + 动作按钮与标题动词一致。
- Toast：过去式，「已保存」「已禁用 3 个账号」「已复制」。
- 错误：「<动作>失败：<原因>」。
- 旧版中的长段说明（如用量日志页「开启后记录新请求的入站正文…」）**内容必须保留**，但移到 `HelpTip` 或卡片底部 12px 说明里，不要占据页面主视线。

## 7. 可访问性底线

- 所有图标按钮有 `aria-label` 与 tooltip。
- 键盘可完成所有操作；焦点环可见（tokens.css 已定义）。
- 状态不只靠颜色：徽章有文字，线路带的成功率有数字。
- 表格行点击打开详情时，同时提供可聚焦的「详情」按钮。
