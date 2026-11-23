# 功能契约：模型广场（Model Market）

**页面标识**: P15（TABS 第 14 个）  
**入口函数**: `WBModelMarket.load(force)`  
**路由**: `/admin#panel-market` 或 `/models/market`

---

## 一、页面整体结构

```
┌─ 顶部操作栏
│  ├─ 视图切换（卡片 | 列表）
│  ├─ 时间窗口切换（1h / 24h / 3d / 7d）
│  └─ [刷新]
│
├─ 筛选与排序栏
│  ├─ 搜索框（模型名 / ID / 调用名）
│  ├─ 平台筛选下拉（全部 / OpenAI / DeepSeek / ...）
│  ├─ 能力筛选（可用 / 全部）
│  └─ 排序选择（调用量 / 价格 / 可用账号 / 名称）
│
├─ 卡片视图（默认）
│  └─ 网格布局 minmax(260px, 1fr)，每张卡片：
│     ┌─────────────────────────────────┐
│     │ [厂商标] 模型名称   [复制]       │
│     │ 6 个平台 · 倍率 ×1.0            │
│     │ 成功率 98.2% │ TTFT 0.9s       │
│     │ 请求 1,240 · 吞吐 128 t/s      │
│     │ [████░░░░░░░░░░░] 98.2%        │  <- 进度条显示成功率
│     │ 可用账号 12 · 额度耗尽 2        │
│     │ 调用名: gpt-4o                  │
│     └─────────────────────────────────┘
│
├─ 列表视图（表格）
│  └─ DataTable 包含列：
│     模型 · 价格 · 请求 · 成功率 · 延迟 · 吞吐 · 积分 · 状态 · 调用名
│
├─ 页脚摘要
│  └─ 时间窗口 · 总数 · 可用数 · 数据源（小时汇总 / 原始日志）
│
└─ 空状态
   └─ 「没有符合条件的模型」
```

---

## 二、数据来源与接口

### 主接口

```http
GET /api/stats/model-market?hours={1|24|72|168}
```

**参数**:
- `hours`: 时间窗口（1、24、72、168 对应 1h、24h、3d、7d）
- 默认值：24

**返回字段**:

```json
{
  "window_hours": 24,                    // 请求的时间窗口（小时）
  "start": "2026-11-23T00:00:00Z",       // 窗口开始时间（ISO 8601）
  "end": "2026-11-24T00:00:00Z",         // 窗口结束时间（ISO 8601）
  "source": "rollup" | "raw",            // 数据源：小时汇总表 / 原始日志
  "total": 42,                            // 总模型数
  "available_models": 38,                 // 有可用账号的模型数
  "items": [
    {
      "key": "gpt-4o",                   // 规范化模型 key
      "name": "GPT-4o",                   // 展示名称
      "platforms": ["openai", "proxy-1"], // 此模型覆盖的平台列表（已排序）
      "platform_labels": ["OpenAI", "代理1"], // 平台展示名
      "models": ["gpt-4o", "gpt-4o-alt"], // 各平台的原始模型名（已排序）
      
      // 时间窗口内的聚合数据
      "requests": 12400,                  // 总请求数
      "success_rate": 0.982,              // 成功率（0-1，null 表示无数据）
      "tokens": 5600000,                  // 总 tokens 消耗
      "credits": 234.56,                  // 总积分消耗
      
      // 性能指标
      "avg_latency_ms": 850,              // 平均延迟（毫秒）
      "avg_ttfb_ms": 120,                 // 首字延迟（毫秒）
      "tps": 128.5,                       // 平均吞吐量（tokens/秒）
      
      // 定价信息
      "free": false,                      // 是否全平台免费
      "min_multiplier": 1.0,              // 最低倍率
      "pricing": [
        {
          "platform": "openai",
          "platform_label": "OpenAI",
          "model_id": "gpt-4o",
          "multiplier": 1.0,
          "free": false
        },
        {
          "platform": "proxy-1",
          "platform_label": "代理1",
          "model_id": "gpt-4o-alt",
          "multiplier": 1.2,
          "free": false
        }
      ],
      
      // 可用性
      "active_accounts": 15,              // 此模型覆盖平台上的活跃账号数
      "exhausted_accounts": 2,            // 本模型日额度已耗尽的账号数
      "available": true                   // 是否可用（active_accounts > exhausted_accounts）
    },
    // ... 更多模型
  ]
}
```

### 副接口

**获取模型聚合信息**（映射调用名）:
```http
GET /api/models/aggregate
```
返回聚合的模型列表，包含各平台的 `client_id` 映射（由模型配置页提供）。用于显示规范的「调用名」。

---

## 三、卡片字段详解

### 3.1 卡片头部

**模型名称** (`name`)
- 显示规范化后的模型展示名（如「GPT-4o」而非「gpt-4o-20240806」）
- 文字大小 18px，粗体

**平台覆盖** (`platform_labels`)
- 显示此模型支持的平台，每个平台一个小 chip
- chip 样式：灰色背景，8px padding，8px 圆角，4px 字体
- 若平台过多（>4），剩余的显示为「+N」chip

**复制按钮** (`models[0]` 或 `pricing[0].model_id`)
- 点击复制「主要调用名」（来自 aggregate 中的第一个或倍率最低的平台）
- 按钮样式：纯文本，蓝色 hover

### 3.2 卡片内容块

**定价** (`pricing`, `free`, `min_multiplier`)
- 若 `free: true` 且所有平台都免费：显示「免费」
- 否则显示「×1.0」（最低倍率）
- 若无定价信息：显示「未定价」
- 字体：18px，粗体，下方小字「每千 Token 倍率」

**使用统计**
- **请求数** (`requests`)：格式化为 `num()` 函数（1.2M / 12.3K / 1200）
- **成功率** (`success_rate`)：格式化为 `pct()`，着色规则见下
- **平均延迟** (`avg_latency_ms`)：格式化为 `latency()`（≥1000ms 显示秒）
- **吞吐** (`tps`)：「128.5 t/s」或「—」（无数据）
- **首字** (`avg_ttfb_ms`)：格式化为 `latency()`
- **积分** (`credits`)：格式化为 `credit()`，显示 2 位小数

**进度条**
- 表示成功率（0-100%），宽度跟随数字百分比
- 颜色规则：见「着色规则」
- 高度 4px，圆角 2px

### 3.3 卡片底部

**可用性状态** (`available`, `active_accounts`, `exhausted_accounts`)
- 若 `available: true`：「可用账号 N」（绿色）
- 若 `available: false`：「当前不可用」（红色）
- 若 `exhausted_accounts > 0`：另显「额度耗尽 N」（灰色）

**调用名** (`pricing[0].model_id` 或 aggregate 中的映射)
- 以 `<code>` 显示，单行截断（max-width 90%）
- 悬停显示完整名称

---

## 四、数据展示规范

### 4.1 数字格式化

**缩写格式** (`num()`)
- ≥ 1 亿（1e8）：「1.23 亿」
- ≥ 1 万（1e4）：「12.34 万」
- ≥ 1 千（1e3）：「1.2K」或「1200」
- 规则：中文式千分位分组（但万级显示「万」）

**百分比格式** (`pct()`)
- 乘以 100，保留 2 位小数
- 如 `0.982` → `98.20%`
- 若为 null：显示「—」

**时间格式** (`latency()`)
- < 1000ms：「850 ms」
- ≥ 1000ms 且 < 60000ms：「2.15 秒」
- ≥ 60000ms：「1 分 25 秒」
- 若为 0 或 null：显示「—」

**金额格式** (`credit()`)
- 千分位分组 + 2 位小数
- 如 `234.5678` → `234.57`

### 4.2 着色规则

#### 成功率着色 (`rateClass()`)
- 绿色（`wbd-ok`）：≥ 99.5%（0.995）
- 黄色（`wbd-warn`）：97% ≤ < 99.5%
- 红色（`wbd-danger`）：< 97%
- 灰色（`wbd-neutral`）：null 或无数据

#### 可用性着色
- 绿色（`wbd-ok`）：`available: true`
- 红色（`wbd-danger`）：`available: false`

#### 卡片整体状态
- 活跃：标准样式
- 不可用：整张卡片加 `is-off` 类，降低透明度 (0.5)、去饱和度

---

## 五、筛选与排序

### 5.1 搜索 (`query`)
- 在 `name`、`key`、`models` 中模糊搜索（大小写不敏感）
- 实时渲染（`input` 事件）
- 搜索内容存储在 `state.query`，不持久化

### 5.2 平台筛选 (`platform`)
- 下拉列表，选项从数据动态生成
- 显示「全部平台」（空值）+ 各平台名
- 仅显示在当前数据集中出现过的平台
- 选择后过滤 `item.platforms` 包含该值的模型

### 5.3 可用性筛选 (`onlyAvailable`)
- 单个复选框「仅显示可用模型」
- 若勾选，过滤 `available: true` 的模型

### 5.4 排序 (`sort`)
- **调用量**（默认）：`requests` 降序
- **价格**：`min_multiplier` 升序
- **可用账号**：`(active_accounts - exhausted_accounts)` 降序
- **名称**：`name` 升序（中文按 localeCompare）

### 5.5 时间窗口切换 (`hours`)
- 按钮组：1h / 24h / 3d / 7d（对应 1 / 24 / 72 / 168 小时）
- 点击时调用 `setHours()` 并重新加载数据
- 当前活跃按钮标记 `active` 类
- 状态存储在 `state.hours`，localStorage 可选

---

## 六、交互与行为

### 6.1 页面加载

**初次进入**:
1. 显示 7 张骨架屏卡片（`wbm-skeleton` 类）
2. 并行调用两个接口：
   - `GET /api/stats/model-market?hours=24`
   - `GET /api/models/aggregate`（可选，若失败不影响主流程）
3. 成功后渲染卡片/列表
4. 绑定过滤器事件
5. 状态标记 `state.loaded = true`

**重新加载** (`load(force)`):
- `force=false` 且已加载：直接 `render()`，不请求接口
- `force=true`：总是请求接口，刷新数据

### 6.2 视图切换

**卡片视图** (默认)
- 网格布局：`grid` + `minmax(260px, 1fr)`
- 间距 16px
- 适配断点：≤768px 时改为单列

**列表视图**
- DataTable，列顺序：模型 · 价格 · 请求 · 成功率 · 延迟 · 吞吐 · 积分 · 状态 · 调用名
- 点击列头可排序（同排序选择器）
- 行高度 40px，间距 1px 分割线

**切换时调用** `setView(view)` 并刷新 `render()`
- 切换状态存储在 `state.view`
- 可选持久化到 localStorage

### 6.3 复制按钮

- 点击 `[data-copy]` 属性的元素
- 获取 `data-copy` 值（模型调用名）
- 调用 `navigator.clipboard.writeText()`
- 成功时：Toast「已复制 gpt-4o」（绿色）
- 失败时：Toast「复制失败，请手动选择」（红色）

### 6.4 卡片点击

- 点击卡片本身（非按钮）时，可选跳转到「模型详情」或「日志筛选页」
- **当前实现**：仅支持复制按钮，卡片整体无跳转（待定）

---

## 七、跨页联动

### 7.1 平台上下文
- 所有请求自动添加 `X-Platform` 请求头（由平台上下文事件设置）
- 若切换平台，页面无需特殊处理（后端已按平台过滤）
- 可选：监听 `wb-platform-change` 事件，调用 `load(true)` 刷新

### 7.2 导航跳转
- **模型配置页**（P14）：点击某模型 → 展开详情抽屉，跳转到「模型配置」并定位到该模型
- **日志查询页**（P9）：点击某模型 → 跳转到日志页并预设「模型名」过滤条件
- **目前实现**：仅支持复制调用名，跳转功能待后续迭代

---

## 八、边界情况与容错

### 8.1 数据缺失

| 字段 | 缺失时处理 |
|------|---------|
| `success_rate` | null 时显示「—」，着色为 `wbd-neutral` |
| `avg_latency_ms` / `avg_ttfb_ms` / `tps` | 0 或 null 时显示「—」 |
| `credits` | NaN 或无穷时显示「—」 |
| `active_accounts` | 0 表示无可用账号 |
| `exhausted_accounts` | 0 表示无耗尽账号，不显示该段信息 |
| `platforms` / `platform_labels` | 空数组时不显示 chip，显示「暂无平台」 |
| `pricing` | 空数组时显示「未定价」，`available` 为 false |

### 8.2 加载态与错误

**加载中**:
- 显示 7 张骨架屏卡片
- 若切换时间窗口，只刷新数据，保持视图稳定

**请求失败**:
- 显示错误提示（红色背景）：「模型广场加载失败：<错误信息>」
- Toast 通知：「模型广场加载失败」（红色）
- 提供隐式重试（点击页面重新加载，或等待自动重试）

**响应超时** (> 10 秒):
- 视为失败处理

**副接口失败** (`/api/models/aggregate`):
- 若获取 aggregate 失败，使用 `market` 返回的 `models` 字段作为调用名
- 不影响主流程，调用名显示为第一个模型 ID

### 8.3 空状态

| 场景 | 显示 |
|------|------|
| 筛选结果为空 | 「没有符合条件的模型」（灰色） |
| 总模型数为 0 | 「模型广场为空，请先添加模型配置」 |
| 所有模型都不可用 | 「所有模型当前都不可用」 |
| 时间窗口内无数据 | 卡片显示「0」，不着色 |

### 8.4 响应体异常

- 若响应不是有效 JSON，先读取为纯文本，尝试解析
- 若仍失败，提取纯文本前 160 字符作为错误信息显示
- HTTP 非 2xx 时显示「HTTP <状态码>」

---

## 九、性能与缓存

### 9.1 后端缓存

- 接口返回数据在后端缓存 30 秒（TTL）
- 新请求落在同一缓存 key 内时返回缓存数据
- 若缓存过期或数据源变化，重新计算
- **数据源降级**：若汇总表不可用，自动降级为原始日志扫描（可能较慢）

### 9.2 前端缓存

- `state.loaded = true` 后，直接调用 `render()` 而不重新请求
- 切换视图/筛选时仅本地过滤，不请求接口
- 只有切换时间窗口时才重新请求数据

---

## 十、无障碍与国际化

### 10.1 无障碍

- 卡片用 `<article>` 语义标签，标题用 `<h3>`
- 数字数据用 `<span aria-label="完整数字">缩写</span>` 提供完整文字描述
- 着色规则不单独依靠颜色：
  - 成功率用进度条表示
  - 可用性用文字标签表示
  - 状态用 Badge 颜色 + 文字双重表示
- 按钮/链接用 `<button>`，aria-label 补充说明

### 10.2 国际化

所有文案需提取到 i18n 配置：
- 「模型广场」「卡片」「列表」「刷新」等 UI 文本
- 「可用账号」「额度耗尽」「调用名」等数据标签
- 「模型广场加载失败」等错误信息
- 「每千 Token 倍率」等说明文本

---

## 十一、旧版有但本契约未涉及的功能

| 功能 | 旧版位置 | 新版建议 | 状态 |
|------|---------|--------|------|
| 模型对比（多选） | 卡片点击复选框 | 待定（可作为后期功能） | ⏳ |
| 模型详情页 | 卡片点击跳转 | 待定 | ⏳ |
| 价格预警 | 右侧面板 | 移到「预警管理」（P20） | ✓ |
| 模型标签筛选 | 顶部筛选器 | 暂不实现 | ⏳ |

---

## 十二、实现要点

1. **并发请求**：使用 `Promise.all()` 同时加载 `model-market` 和 `aggregate`，提高加载速度
2. **数据映射**：aggregate 中的 client_id 按 canonical key 索引，用于快速查找平台-调用名映射
3. **降级路径**：后端数据源从汇总表降级为原始日志，前端无需感知（`source` 字段仅作日志用）
4. **渲染性能**：
   - 筛选/排序在内存完成，不重新请求接口
   - 大列表（>100 行）时考虑虚拟滚动
5. **主题适配**：所有颜色变量使用 CSS Custom Properties（`--ok`、`--warn`、`--danger` 等），支持亮/暗主题
6. **防止重复请求**：`state.seq` 递增，响应时检查序列号，丢弃过期响应

---

## 附录：API 响应示例

```json
{
  "window_hours": 24,
  "start": "2026-11-23T00:00:00Z",
  "end": "2026-11-24T00:00:00Z",
  "source": "rollup",
  "total": 42,
  "available_models": 38,
  "items": [
    {
      "key": "gpt-4o",
      "name": "GPT-4o",
      "platforms": ["openai", "openai-backup"],
      "platform_labels": ["OpenAI", "OpenAI（备用）"],
      "models": ["gpt-4o", "gpt-4o-backup"],
      "requests": 12400,
      "success_rate": 0.982,
      "tokens": 5600000,
      "credits": 234.56,
      "avg_latency_ms": 850,
      "avg_ttfb_ms": 120,
      "tps": 128.5,
      "free": false,
      "min_multiplier": 1.0,
      "pricing": [
        {
          "platform": "openai",
          "platform_label": "OpenAI",
          "model_id": "gpt-4o",
          "multiplier": 1.0,
          "free": false
        }
      ],
      "active_accounts": 15,
      "exhausted_accounts": 2,
      "available": true
    }
  ]
}
```

---

## 实现对照

> S13 实测记录。接口与数据转换在 `admin/ui/src/api/market.ts`，页面在 `admin/ui/src/pages/MarketPage.vue`，
> 单测在 `admin/ui/tests/page-market.spec.ts`（28 条）。路由由 captain 统一接线（`router/index.ts` 的 PAGE_COMPONENTS）。

### 接口

| 用途 | 调用 | 后端位置 |
|---|---|---|
| 卡片全部数字（窗口用量 / 定价 / 可用账号） | `GET /api/stats/model-market?hours=24\|72\|168` | `admin/routers/stats.py:model_market`（913 行） |
| 主要调用名（`client_id`） | `GET /api/models/aggregate`，失败不影响主流程 | `admin/routers/models.py:list_aggregate` + `admin/model_alias.py:aggregate_payload` |
| sparkline 分时序列 | `GET /api/stats/dashboard?days=1\|3\|7&granularity=hour&top_models=12`，只取 `trend` | `admin/routers/stats.py:dashboard_stats` → `_dashboard_agg` |

### 功能对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 卡片 / 列表视图切换，存 localStorage | 页头 `Segmented` + `readMarketView`/`writeMarketView`（键 `mh.market.view`） | 已实现 |
| 时间窗口 24h / 3d / 7d，存 localStorage 并同步路由 | 页头 `Segmented` + `normalizeHours`/`readMarketHours`/`writeMarketHours`（键 `mh.market.hours`）+ `useTableQuery` 的 `hours` 筛选 | 已实现 |
| 搜索（展示名 / key / 调用名 / 各平台原始模型名，大小写不敏感） | `filterMarketCards` + FilterBar 搜索框 | 已实现 |
| 厂商（平台）过滤，选项只取数据集中出现过的平台 | `platformOptions` + `filterMarketCards` | 已实现 |
| 能力过滤「只看可用」 | `filterMarketCards({ available: 'available' })` | 已实现 |
| 排序（调用量 / 价格 / 速度 / 成功率 / 名称） | `sortMarketCards` + FilterBar 排序下拉 + DataTable 列头排序（两处同一份状态） | 已实现 |
| 卡片：厂商图标 | `ModelIcon`（`vendor` 传归一 key，沿旧版 `model-icons.js` 映射） | 已实现 |
| 卡片：模型名 + 平台 chips（>3 折叠为 `+N`） | `market-card__head` | 已实现 |
| 卡片：平台数 / 倍率（免费、未定价） | `priceText` + `platformCount` | 已实现 |
| 卡片：成功率（≥99.5% ok / ≥97% warn / <97% danger / 无数据 neutral） | `rateTone` + `fmtPct` | 已实现 |
| 卡片：TTFT（毫秒进、自动单位） | `fmtDuration` | 已实现 |
| 卡片：列表补齐延迟 / 吞吐 / 积分 | `columns`（延迟、吞吐、积分列）+ `fmtCredit` | 已实现 |
| 卡片：近窗口请求 sparkline | 内联 SVG `Sparkline` + `sparklineBars`（12 桶合并后按峰值归一） | 有差异：`model-market` 不返回分桶，序列取自 dashboard 的 `trend`（后端只回 Top N 模型）；没进 Top N 的模型不画（页脚注明，不伪造数据） |
| 复制模型名 | `copyName` → `copyText`，成功 Toast「已复制 …」，失败「复制失败：…」 | 已实现 |
| 查看日志（跳 `/logs` 带 `model`） | `logLinkFor` + `router.push({ path: '/logs', query: { model } })` | 已实现 |
| 列表视图同字段 | `columns`：模型（图标+平台标签）· 倍率 · 请求 · 成功率 · TTFT · 延迟 · 吞吐 · 近窗口趋势 · 积分 · 状态 · 调用名 · 操作 | 已实现 |
| 页脚摘要（窗口 / 总数 / 可用数 / 数据源） | `windowSummary`（`source` rollup → 小时汇总，raw → 原始日志） | 已实现 |
| 四态：首次加载 / 刷新 / 空 / 错误 | 骨架 7 张卡（契约 §6.1）；刷新保留旧数据 + `Card loading` 转圈；`Empty`；`ErrorBlock` + 重试 | 已实现 |
| 空状态三档文案（筛选为空 / 广场为空 / 全部不可用） | `emptyDescription` | 已实现 |
| 不可用卡片弱化 | `.market-card.is-off`（透明度降到 0.55） | 已实现 |
| 平台上下文：切平台重新取数、卸载取消 | `onMounted` 订阅 `usePlatformStore().onChange`（selector 变了才请求），`onBeforeUnmount` 取消 | 已实现 |
| 筛选 / 排序 / 分页同步路由 query | `useTableQuery('market')`：`q` / `platform` / `available` / `hours` / `sort` / `order` / `page` / `size` | 已实现（契约 §5 未涉及分页，按任务书要求补：卡片视图用「上一页 / 下一页」，列表用 DataTable 分页） |
| 手机 375px | 卡片单列、筛选控件满宽；列表走 DataTable 的 mobile 卡片模式（title/subtitle/meta/actions） | 已实现 |
| 数字与时间格式（num / pct / latency / credit） | `utils/format` 的 `fmtCompact` / `fmtPct` / `fmtDuration` / `fmtCredit` | 有差异：全站统一口径（`fmtCompact` 用 K/M/B，本契约 §4.1 示例用「万 / 亿」），按 05-patterns §4 服从全站 |
| 1h 时间窗口 | 未提供 | 有差异：任务书窗口范围为 24h / 3d / 7d（设计稿 P15 同样只有这三档），旧版 1h 不迁移 |
| 响应超时 > 10 秒视为失败 | 走 http 层与后端 30 秒缓存 | 有差异：`http()` 对页面请求不设硬超时（05-patterns §1 约定只有心跳类请求用 `timeoutMs`），超时表现为网络错误 |
| 卡片整卡点击跳转 | — | 有差异：契约 §6.4 / §7.2 记录旧版就没有整卡跳转；新版改为卡片内「查看日志」按钮（P15 设计稿同款） |

### 单测覆盖（`admin/ui/tests/page-market.spec.ts`）

- `toMarketCards`：字段映射、调用名优先级（aggregate `client_id` → 最低倍率 `model_id` → `models[0]`）、可用性重算、免费/未定价、sparkline 命中与缺失、脏数据兜底；
- `sparklineBars`：分桶守恒、峰值归一、全 0 / 空 / NaN 返回空数组（不画假数据）；
- `filterMarketCards` / `sortMarketCards` / `paginateMarketCards` / `marketStats` / `platformOptions` / `rateTone` / `logLinkFor` / `hoursToDays` / `normalizeModelName` / `indexCallNames` / `indexTrendSeries`。

测试数据全部照抄后端真实响应形状（`model_market` / `aggregate_payload` / `_dashboard_agg` 的 `trend`）。

