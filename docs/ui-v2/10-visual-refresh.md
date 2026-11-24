# 10 视觉精修（v2.1）：「调度台 · 精制版」

> 2026-11-24。用户反馈：v2 第一版改动太小，不够美观，可以大改。本文件**覆盖** 01-direction.md 里和下面冲突的条款（第 2 节「不给卡片加阴影」、第 5 节「卡片不用阴影」、第 6 节「禁止悬停效果」），其余条款继续有效。
> 所有前端修复线必须按本文件执行。F1（设计系统与外壳）负责落地令牌、公共组件和外壳；页面线只在页面里使用这些令牌和组件。

## 0. 目标
- 一眼看上去是**精致的专业运维产品**（参考 Linear、Vercel、Grafana Cloud 的完成度），而不是「表单 + 表格」的后台模板。
- 保留原有身份：靛蓝品牌色、传统色命名、线路带作为记忆点、状态色只表达状态。
- 信息密度不降：桌面端一屏看到的数据不少于现在。

## 1. 关键变化（相对 v2.0）

### 1.1 层次与质感
- 卡片：1px `--border` 加 `--shadow-1`（很轻），圆角 `--r-card: 12px`。大面积的英雄区和图表大卡用 `--r-lg: 14px`。
- **可点击**的卡片悬停时换成 `--shadow-2`，边框换成 `--border-strong`，过渡 160ms，**不上浮、不缩放**。
- 页面背景：浅色 `--bg` 保持霜白。内容区顶部 240px 叠一层极淡的品牌色径向光晕，用 `--bg-glow`，只出现在主内容区顶部。深色同理。
- 深色模式靠「表面层级」区分：`--bg` < `--surface` < `--surface-raised` < 浮层，每级都有可辨的亮度差，解决「卡片和背景分不清」。

### 1.2 外壳
- **侧栏改成深墨蓝**，两种主题都是。浅色 `--side-bg: #121829`，深色 `#0C0F16`。文字 `--side-fg` 和 `--side-fg-muted`。
  - 选中项：`--side-active-bg` 底，加左侧 3px 品牌色竖条，文字变白。悬停：`--side-hover-bg`。
  - 分组标题 11px、`--side-fg-muted`，不用大写，不加字间距。
  - 顶部是品牌区（logo + MiraiHub + 版本号小字），底部是用户、主题、切回 v1。
  - 1024–1279px 自动收起成 64px 图标栏，悬停显示 tooltip。<768px 变成抽屉。
  - logo 在深色侧栏上要清楚：用浅色版，或者给 logo 加一个 28px 圆角方底。
- 顶栏：吸顶，背景 `--topbar-bg`（半透明）加 `backdrop-filter: saturate(1.4) blur(10px)`，下边框 `--border-soft`。
  - 左：面包屑「分组 / 页面」。
  - 中：一个宽的搜索框样式按钮「搜索页面、账号、Key…  Ctrl K」，点开命令面板。
  - 右：平台选择器（胶囊）、状态点（**不闪烁**，异常时变成 warn/danger 色，加一个数字）、通知、主题。
- 内容区：`--content-max: 1600px` 居中。表格类页面用 `.mh-page--wide`，上限 `--content-max-wide: 1920px`。左右内边距 32px，≤1280px 时 24px，手机 16px。

### 1.3 页头
- `PageHeader`：h1 24/32、600；下面一行副标题（可选，`--fg-muted`）；右侧放操作区，**每页最多一个主按钮**（实心品牌色），其余用次级或幽灵按钮。
- 可选 `eyebrow`：h1 上方的小字分组名，12px、`--fg-muted`。
- 可选 `meta` 插槽：h1 右侧的徽章或统计，比如「35 个账号 · 3 个冷却」。

### 1.4 统计卡（KPI）
- 新组件 `KpiCard`：标签、大数字（28/34）、单位、环比（带箭头的 chip，好/坏按 `deltaGood` 着色）、迷你折线 `Sparkline`、可选的 32px 状态图标方块。
- 一行 4 个（≥1280），2 个（≥768），1 个（手机）。卡片等高靠内容，不强行拉伸；没有内容的地方不留大空白。

### 1.5 表格（DataTable 的视觉层）
- 表头吸顶，`--surface-2` 底，12px/500/`--fg-muted`。
- 行高 `--row-h: 44px`，紧凑模式 `--row-h-compact: 36px`。悬停 `--surface-hover`，选中 `--brand-soft`。
- 第一列（名称）用 500 字重加 `--fg`；数字列右对齐、tabular-nums；状态列用「圆点 + 文字」。
- 行操作：桌面端悬停时才显示（键盘聚焦时也显示），触屏一直显示。
- 工具栏合成一行：左边是搜索和筛选 chip，右边是「共 N 条」、批量操作、列设置、刷新。表格上方的空白不超过 56px。
- 手机端（<768px）表格变成卡片列表，由 DataTable 统一处理。

### 1.6 徽章、按钮、表单
- Badge：高 22px，圆角 `--r-chip: 6px`，soft 底 + 状态色文字；`dot` 变体前面带 6px 圆点。
- 主按钮：品牌实色，内部加 `inset 0 1px 0 rgba(255,255,255,.14)` 高光，悬停 `--brand-hover`。高 34px（md），28px（sm）。
- 输入框：34px，`--surface` 底，聚焦时 3px `--ring` 外环。
- 设置类页面：左栏 280px 写「标签 + 说明」，右栏放控件，每组之间用 `--border-soft` 分隔。**统一的保存方式**是底部吸附的 `SaveBar`：「有 N 项未保存　放弃　保存」，没有改动时不出现。不再用卡片底部各自的保存按钮，也不再用页头的保存按钮。

### 1.7 空状态、加载、错误
- 空状态：48px 圆形 soft 底加 lucide 图标，一行标题，一行说明，一个动作按钮。
- 加载：只用一种骨架屏（`--surface-2` 底，1.2s 微光动画），刷新时保留旧数据，卡片右上角显示小转圈。
- 错误：`ErrorBlock`，danger-soft 底，写清楚原因和下一步，带「重试」。

### 1.8 图表
- ECharts 主题：网格线 `--border-soft` 虚线；折线 2px、平滑；面积用品牌色到透明的渐变（顶部 18%）；tooltip 用 `--surface-raised` 加 `--shadow-pop`；**横轴一律用本地时区**（Asia/Shanghai 或浏览器时区）。
- 系列颜色和图例必须一致。数量级相差很大的系列（比如输入和输出 token）用双轴或分开画。
- 线路带：放在概览首屏的英雄卡里（`--r-lg`），高度 220–280px。只有一个平台有流量时，要显示成「主线 + 其余平台的细灰线（0 流量）」，不要变成一根实心粗条。

### 1.9 动效
- 允许：可点击卡片的悬停阴影、抽屉和弹窗的进出、骨架屏微光、线路带脉冲。
- 不允许：自发闪烁的状态点、数字滚动、入场动画、点击波纹。`prefers-reduced-motion` 时全部关闭。

## 2. 新增令牌（F1 写进 tokens.css；页面线可以直接用）
| 令牌 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--surface-raised` | `#FFFFFF` | `#222733` | 浮起的卡片、tooltip |
| `--shadow-1` | `0 1px 2px rgba(16,24,40,.05), 0 1px 1px rgba(16,24,40,.03)` | `0 1px 2px rgba(0,0,0,.4)` | 卡片 |
| `--shadow-2` | `0 6px 16px rgba(16,24,40,.08), 0 2px 4px rgba(16,24,40,.04)` | `0 6px 18px rgba(0,0,0,.5)` | 可点击卡片悬停 |
| `--bg-glow` | `radial-gradient(1200px 240px at 30% -40px, rgba(49,72,200,.07), transparent)` | `radial-gradient(1200px 240px at 30% -40px, rgba(125,143,255,.10), transparent)` | 内容区顶部 |
| `--side-bg` / `--side-fg` / `--side-fg-muted` | `#121829` / `#E7EAF3` / `#8C94AB` | `#0C0F16` / `#E6E9EF` / `#7E8699` | 侧栏 |
| `--side-hover-bg` / `--side-active-bg` / `--side-border` | `rgba(255,255,255,.06)` / `rgba(125,143,255,.18)` / `rgba(255,255,255,.06)` | 同左 | 侧栏 |
| `--topbar-bg` | `rgba(244,246,250,.82)` | `rgba(18,21,27,.78)` | 顶栏 |
| `--ring` | `rgba(49,72,200,.22)` | `rgba(125,143,255,.30)` | 聚焦外环 |
| `--grad-brand` | `linear-gradient(135deg,#3148C8,#5B6CF0)` | `linear-gradient(135deg,#6E80FF,#9AA6FF)` | 只用于 logo 底和英雄区小面积点缀 |
| `--r-card` / `--r-lg` / `--r-ctl` / `--r-chip` | 12 / 14 / 8 / 6 px | 同左 | 圆角（`--r-card`、`--r-ctl`、`--r-chip` 改值） |
| `--fs-h1` / `--lh-h1` | 24 / 32 | 同左 | 改值 |
| `--fs-stat` / `--lh-stat` | 28 / 34 | 同左 | 改值 |
| `--row-h` / `--row-h-compact` | 44 / 36 px | 同左 | 表格行高 |
| `--content-max` / `--content-max-wide` | 1600 / 1920 px | 同左 | 改值、新增 |

## 3. 新增公共组件（F1 必须实现，props 签名固定，页面线可以直接用）
```ts
// src/components/data/Sparkline.vue —— 纯 SVG，不依赖 echarts
props: { data: number[]; tone?: 'brand'|'ok'|'warn'|'danger'|'neutral'; height?: number /*默认 28*/; area?: boolean /*默认 true*/ }

// src/components/data/KpiCard.vue
props: { label: string; value: string | number | null; unit?: string; delta?: number | null /*百分比，正负*/;
         deltaGood?: 'up' | 'down' /*默认 up*/; hint?: string; tone?: 'brand'|'ok'|'warn'|'danger'|'neutral';
         icon?: Component; spark?: number[]; loading?: boolean; to?: string /*router 路径，有则整卡可点*/ }
slots: { extra?: 底部附加内容 }

// src/components/base/SaveBar.vue —— 底部吸附保存条
props: { dirty: number | boolean; saving?: boolean; disabled?: boolean }
emits: { save: [], discard: [] }
```
三个组件都要从 `src/components/index.ts` 导出。`PageHeader` 新增可选的 `eyebrow` prop 和 `meta` 插槽，必须向后兼容。

## 4. 公共样式类（F1 写进 base.css）
`.mh-page`（标准内容宽度）、`.mh-page--wide`、`.mh-grid`（12 列，gap 16）、`.mh-col-{3,4,6,8,12}`（<1024 时变成 12）、`.mh-kpis`（KPI 自适应网格）、`.mh-stack`（纵向 gap 16）、`.mh-toolbar`（表格上方的一行工具栏）、`.mh-card`（等价于 Card 组件的外观，给不用组件的地方用）、`.mh-muted`、`.mh-num`（tabular-nums）、`.mh-skeleton`。

## 5. 页面线的通用要求
- 巨型页面拆分：超过 900 行的页面拆成 `src/pages/<PageName>/` 下的子组件（筛选、表格、抽屉、图表各一个文件），页面入口文件保留原路径和导出名，路由不变。
- 只用令牌，不写裸色值，不写裸 px 字号（间距可以用 `--sp-*`）。
- 每页只有一个主按钮。
- 修复各轮审查里落在本页的所有问题（见 ui_review_round1/2/3 和 fix_plan 第 6 批）。
- 文案拼接一律带分隔（「·」或空格），无效值显示「—」，负倍率显示「按量计费」。
- 时间一律本地时区，相对时间 + 悬停显示绝对时间。
