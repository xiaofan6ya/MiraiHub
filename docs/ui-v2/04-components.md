# 04 公共组件

全部放在 `admin/ui/src/components/`。能用 Naive UI 的就包一层 Naive UI，不要自己从零写。
页面里**只用这些组件**，不要在页面里直接写散装样式的按钮、卡片、徽章。

## 1. 基础

| 组件 | 基于 | 规格 |
|---|---|---|
| `Btn` | `NButton` | 尺寸 sm 28px / md 32px（默认）/ lg 36px。类型：`primary`（靛底白字）、`default`（纸底 + 1px `--border-strong`）、`quiet`（无边框，悬停 `--surface-hover`）、`danger`（朱色文字，primary 时朱底白字）。图标在左，16px，间距 6px。加载中显示转圈并禁用。**每个区域最多 1 个 primary。** |
| `IconBtn` | `NButton` circle=false | 32×32，仅图标，必须有 `aria-label` 与 tooltip。 |
| `Card` | 自写 | `--surface` 底、1px `--border`、圆角 10px、内边距 20px、无阴影。插槽：`title`（15px/600）、`extra`（标题右侧，放次要操作/时间范围切换）、`default`、`footer`（上方 1px `--border-soft`）。属性 `flush` 去掉内边距（用于内嵌表格）。属性 `loading` 在标题右侧显示 14px 转圈（不遮罩内容）。 |
| `Section` | 自写 | 卡片内分组：小标题 13px/600 + 内容，组与组之间 1px `--border-soft` 分隔线，上下各 16px。 |
| `Badge` | 自写 | 高 20px，内边距 0 6px，圆角 5px，12px/500。`tone`: ok / warn / danger / neutral / brand → 文字用状态色，底用 `-soft`。可选左侧 6px 圆点（`dot`）。 |
| `StatusDot` | 自写 | 8px 圆点 + 可选文字。`pulse` 属性仅用于「运行中」，reduced-motion 下不动。 |
| `Chip` | `NTag` | 用于模型名、标签：高 24px、圆角 5px、`--surface-2` 底、1px `--border-soft`，可关闭、可点击复制。 |
| `Copyable` | 自写 | 等宽字体显示一段文本（Key、URL、模型 ID），右侧 `Copy` 图标按钮，点击复制并 Toast「已复制」。属性 `mask` 时中间打码显示（`sk-abc…f9e2`），旁边 `Eye` 图标切换明文。 |
| `HelpTip` | `NPopover` | `CircleHelp` 14px 图标，悬停/点击显示说明文字，最大宽 320px。用来收纳旧版页面上大段灰色说明。 |
| `Empty` | `NEmpty` | 图标 32px `--fg-faint` + 一句说明 + 0–2 个操作按钮。 |
| `ErrorBlock` | 自写 | 朱色左图标 + 错误标题 + 详细信息（可展开，等宽）+「重试」按钮。 |

## 2. 数据展示

### `StatCard`
```
今日请求          ⓘ
2.3K
成功 696 次 · 较昨日 +12%
```
- 标签 12px `--fg-muted`；数值 26px/600 tabular；脚注 12px `--fg-muted`。
- 属性：`label`、`value`、`unit`（数值后 14px 单位）、`tone`（给数值着色，仅当它本身是状态时）、`hint`、`to`（可点击跳转，整卡可点，悬停边框变 `--border-strong`）。
- 统计卡**一组放在一个 `StatStrip` 里**：一个卡片外框，内部用竖分隔线分成 N 格（N=2–5），而不是 N 张独立卡片。手机上 2 列网格。

### `RouteRibbon`（全站唯一的视觉记忆点）
用于概览、平台网关、风控哨兵。用 SVG 自绘，不用 ECharts。

```
                ┌──────────────────────────── WorkBuddy   1,786 次  69.4%  ●
  调用方 ═══════╪════════════════ Loomy         210 次  27.1%  ●
  2,326 次/今日 ╪═══════ Trae 国内版   175 次   9.7%  ●
                ╪═══ Qoder 中国版   127 次  50.4%  ●
                ╰─ Trae 国际版      8 次   0.0%  ●
```
- 左侧起点：标签（如「调用方」或「客户端请求」）+ 总量。
- 每条线路：一条从左汇流点出发的三次贝塞尔曲线到右侧平台行，**线宽 = 2 + 18 × 占比**（px），颜色按健康度：成功率 ≥90% `--ok`，60–90% `--warn`，<60% `--danger`，已禁用平台 `--fg-faint` 虚线。
- 右侧平台行：logo 18px + 名称 + 次数 + 成功率（着色）+ 状态圆点。行高 36px。行可点击（触发 `@select(platformId)`）。
- 「首选平台」（调度顺序第 1 位）那条线路用 `--brand` 描边 1px 外框高亮，并在名称后显示 Badge「首选」。
- 流量脉冲：每条线路上一个 4px 圆点沿路径循环移动，周期 = `clamp(1.2s, 6s / 每分钟请求数的对数, 6s)`。没有流量时不显示。`prefers-reduced-motion` 时不显示。
- 属性：`source: {label, total}`、`lanes: [{id, name, logo, count, successRate, status, disabled, preferred}]`、`metric`（显示次数还是 token）。
- 空数据：显示所有平台的灰色细线与「暂无流量」。
- 宽度 < 640px 时退化为纵向列表（每行一个水平条形图），不画曲线。

### `DataTable`
基于 `NDataTable`，统一封装：
- 表头 `--surface-2` 底，12px/500 `--fg-muted`，**数值列右对齐**、文本列左对齐、操作列右对齐且固定在右侧。
- 行高 44px（`size="medium"`），可切换紧凑 36px（表格右上角 `Rows3` 图标按钮，偏好按表格 id 存 localStorage）。
- 行悬停 `--surface-hover`；选中行 `--brand-soft`。
- 支持：排序、多选（首列复选框）、远程分页（每页 20/50/100）、列显示设置（右上角 `Columns3` 图标）、空状态插槽、加载中（保留旧数据 + 顶部 2px 进度条）。
- 多选后，表格上方出现 `BulkBar`：「已选 3 项」+ 批量操作按钮 + 「取消选择」，`--brand-soft` 底，高 44px。
- **手机模式（≤640px）**：自动渲染成卡片列表。列定义里用 `mobile: 'title' | 'subtitle' | 'meta' | 'hidden' | 'actions'` 标注每列在卡片里的位置：
  ```
  ┌─────────────────────────────────┐
  │ 193****2486            [活跃]   │ ← title + 状态徽章
  │ WorkBuddy · 剩余 4,764           │ ← subtitle
  │ 请求 340   积分 9   命中率 95%   │ ← meta（最多 4 项，两列）
  │                    [刷新] [⋯]   │ ← actions
  └─────────────────────────────────┘
  ```
  容器内部滚动，最大高 `calc(100vh - 220px)`（沿用旧版的做法）。
- 行操作：最多直接显示 2 个文字按钮（quiet 型），其余收进 `⋯` 下拉菜单；危险操作在菜单最下方、朱色、上方加分隔线。

### `FilterBar`
- 一行横向排列的筛选控件，控件宽度固定（下拉 160px、日期区间 260px、搜索 240px），放不下自动换行。
- 右侧固定：「重置」quiet 按钮。筛选变化 300ms 防抖后自动查询（不需要「查询」按钮；旧版有「查询」按钮的页面保留回车触发）。
- 下方一行「已筛选」chips：每个生效的筛选条件一个可关闭 chip，以及结果统计文字（如「共 862 次 · 成功 850 · 错误 12」）。
- 筛选条件同步到路由 query（`/logs?platform=trae&result=error`），刷新和分享链接不丢。

### `Segmented`
分段控件（如「今日 / 7 天 / 30 天」「卡片 / 列表」）：高 28px，`--surface-2` 底，选中项 `--surface` 底 + 1px `--border` + `--fg` 文字。

### `Meter`
水平进度条：高 6px，圆角 3px，底 `--border-soft`，填充按 tone。可选右侧数值文字。用于额度、积分占比、健康度。
`StackMeter`：多段堆叠（如到期风险分布：≤30 天 / 30 天以外 / 长期有效），下方图例。

### `Ring`
环形图（缓存命中率）：直径 104px，线宽 10px，中间数值 22px/600 + 单位 12px。

### 图表 `Chart`
封装 vue-echarts，统一主题：
- 读取 CSS 变量生成 ECharts 主题（主题切换时重建）。字体 `--font-sans` 11px，坐标轴线 `--border`，网格线 `--border-soft` 虚线，tooltip 用 `--surface` 底 + `--shadow-pop` + 圆角 7px。
- 系列颜色按 `--chart-1..8` 顺序。
- 折线：线宽 2，无数据点标记（悬停才显示），面积填充 8% 透明度。
- 柱状：圆角顶 3px，最大宽 24px。
- 饼图一律改成**横向条形排行**（`BarList` 组件：名称 + Meter + 数值 + 占比），比饼图易读。
- 图表高度：趋势图 240px，排行最多显示 8 项 +「其他」。

## 3. 表单与浮层

| 组件 | 规格 |
|---|---|
| `FormDrawer` | 右侧抽屉，宽 480px（复杂表单 640px），标题 + 关闭按钮；底部固定操作栏：「取消」+ 主按钮（动词，如「创建 Key」「保存」）。**新建/编辑类表单一律用抽屉**，不要用居中大弹窗。表单项标签在上、控件在下，标签 13px/500，帮助文字 12px `--fg-muted` 在控件下方。 |
| `ConfirmDialog` | 居中小弹窗 420px。标题是一个问句（「删除 3 个账号？」），正文说清后果（「删除后无法恢复，相关用量记录保留。」），按钮「取消」+「删除」（危险操作朱色）。高风险操作（批量删除、清空、禁用整个平台）要求输入确认文字或勾选「我已了解」。通过 `useConfirm()` 以 Promise 方式调用。 |
| `DetailDrawer` | 右侧抽屉 640px，只读详情（日志详情、账号详情），顶部标题 + Badge，内容用 `Section` 分组，键值对用 `KV` 组件（左标签 120px `--fg-muted`，右值）。 |
| `JobProgress` | 后台任务进度：右下角浮动卡片 320px，显示任务名、进度条、成功/失败计数、「查看详情」「隐藏」。多个任务堆叠。轮询由 `useJob(jobId)` 负责。 |
| `Toast` | 用 Naive UI `useMessage`，右上角，成功 2.5s、错误 6s 且可手动关闭。错误 Toast 文字取后端 `detail`/`message` 字段。 |
| `CodeBlock` | 等宽、`--surface-2` 底、圆角 7px、右上角「复制」，可选语言标签。用于 curl 示例、配置片段。 |
| `DateRange` | `NDatePicker type="datetimerange"`，附快捷项：今日、昨日、近 7 天、近 30 天。 |

## 4. 组合式函数（`src/composables/`）

- `useApi()`：见 05-patterns §1。
- `usePolling(fn, intervalMs, { immediate, pauseWhenHidden: true })`：页面卸载自动停止。
- `useConfirm()`、`useToast()`、`useJob()`、`useCopy()`。
- `useTableQuery(key)`：把分页/排序/筛选同步到路由 query。
- `usePlatformStore()`（Pinia）：平台列表、当前平台、`onPlatformChange(cb)`。
