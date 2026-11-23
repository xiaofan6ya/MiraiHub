# 功能契约：数据看板页（Dashboard）

**页面标识**: P11（TABS 第十一个）  
**入口函数**: `loadDashboard(days, granularity, platform, model)`  
**路由**: 点击导航栏「数据看板」或直接访问

---

## 一、页面整体结构

```
┌─ 时间窗口切换 + 粒度选择（1/7/30天 × 小时/天）
├─ 平台/模型过滤栏
│
├─ 上半屏：KPI 一排 7 张卡
│  ├─ 总数 / 总积分 / 总Token / 缓存读取 / RPM / TPM / 成功率
│  └─ 着色规则：成功率三档（绿/黄/红）
│
├─ 中上屏：性能健康条
│  ├─ 成功率、平均延迟、吞吐TPS、首字TTFB（4格横行）
│  └─ 下方显示 TOP 8 模型芯片（成功率着色）
│
├─ 中屏：消耗分布（可切换视图）
│  ├─ 视图选择：堆叠柱状 / 堆叠面积 / 单线趋势
│  ├─ 按模型分层（每个模型一个颜色）
│  └─ 鼠标悬停显示 tooltip（模型名、数值、时间桶）
│
├─ 下屏：模型调用分析（三个视图切换）
│  ├─ 视图 1 - 趋势：多条线，每条代表一个模型的请求数趋势
│  ├─ 视图 2 - 分布：直方图，调用次数分布（按箱子）
│  ���─ 视图 3 - 排行：TOP 12 模型排行表（调用数、占比、成功率）
│
├─ 右下辅助卡片
│  ├─ 平台贡献（按平台分摊的请求数 + 成功率）
│  └─ 失败分类（error_kind 分布）
│
└─ 底部：数据更新时间 + 刷新按钮
```

---

## 二、数据来源与接口

### 主接口

```http
GET /api/stats/dashboard?days={1|7|30}&granularity={hour|day}&platform=&model=&top_models={1..12}
```

**参数**:
- `days`: 时间窗口（1、7、30 天）
- `granularity`: 粒度（hour 或 day），超过 7 天自动降级为 day
- `platform`: 平台过滤（空=所有平台）
- `model`: 模型过滤（空=所有模型）
- `top_models`: 返回前 N 个模型（默认 6，范围 1-12）

**返回数据结构**:

```json
{
  "window": {
    "days": 1,
    "granularity": "hour",
    "start": "2026-11-23T00:00:00Z",
    "end": "2026-11-24T00:00:00Z",
    "minutes": 1440,
    "buckets": ["2026-11-23T00:00", "2026-11-23T01:00", ...]
  },
  
  "filters": {
    "platform": "",
    "model": "",
    "platform_label": "全部平台"
  },
  
  "summary": {
    "requests": 12500,
    "successes": 12300,
    "success_rate": 0.984,           // 98.4%
    "failures": 200,
    "credits": 1234.56,
    "total_tokens": 650000,
    "prompt_tokens": 500000,
    "completion_tokens": 150000,
    "cached_tokens": 50000,
    "cache_hit_rate": 0.0909,
    "rpm": 520.8,                    // 平均每分钟请求数
    "tpm": 27083.3,                  // 平均每分钟 token 数
    "avg_latency_ms": 450,
    "avg_ttfb_ms": 120,
    "throughput_tps": 95.5           // 吞吐量（token/s）
  },
  
  "models": [
    {
      "model": "gpt-4o",
      "label": "GPT-4o",
      "requests": 3000,
      "successes": 2950,
      "success_rate": 0.9833,
      "tokens": 180000,
      "credits": 450.0,
      "avg_latency_ms": 520,
      "avg_ttfb_ms": 150
    },
    // ... 更多模型
  ],
  
  "distribution": {
    "metric": "credits",             // 或 "tokens"
    "buckets": ["2026-11-23T00:00", ...],
    "series": [
      {
        "name": "gpt-4o",
        "model": "gpt-4o",
        "values": [100.5, 102.3, 98.7, ...]
      },
      // ... 其他模型
    ]
  },
  
  "trend": {
    "buckets": ["2026-11-23T00:00", ...],
    "total_requests": [520, 530, 515, ...],
    "series": [
      {
        "name": "gpt-4o",
        "model": "gpt-4o",
        "requests": [150, 160, 145, ...],
        "credits": [100.5, 102.3, 98.7, ...]
      },
      // ... 其他模型
    ]
  },
  
  "ranking": [
    {
      "rank": 1,
      "model": "gpt-4o",
      "label": "GPT-4o",
      "requests": 3000,
      "share": 0.24,                 // 占比 24%
      "success_rate": 0.9833,
      "avg_latency_ms": 520,
      "tokens": 180000
    },
    // ... 更多模型
  ],
  
  "platforms": [
    {
      "platform": "openai",
      "label": "OpenAI",
      "requests": 5000,
      "success_rate": 0.986,
      "share": 0.4                   // 占比 40%
    },
    // ... 其他平台
  ],
  
  "errors": [
    {
      "kind": "rate_limit",
      "count": 50,
      "share": 0.25
    },
    {
      "kind": "timeout",
      "count": 100,
      "share": 0.5
    },
    // ... 其他错误类型
  ]
}
```

---

## 三、数据展示规范

### 3.1 数字格式化

#### 缩写格式（`cnum()`）
- ≥ 1 亿（1e8）：显示为 B（亿），2 位小数，如 `1.23B`
- ≥ 1 万（1e4）：显示为 M（万），2 位小数，如 `12.34M`  
- ≥ 1 千（1e3）：显示为 K（千），1 位小数，如 `1.2K`
- < 1 千：显示原数，四舍五入

#### 千分位格式（`exact()`）
- 用于精确计数，千分位分组，如 `1,234,567`

#### 金额格式（`credit()`）
- 千分位 + 固定 2 位小数，如 `1,234.56`

#### 百分比格式（`pct()`）
- 乘以 100，固定 2 位小数，如 `98.40%`

#### 时间格式（`latency()`）
- < 1 秒：毫秒，如 `450 ms`
- 1-60 秒：浮点秒，如 `1.23 秒`
- > 60 秒：分秒，如 `1 分 23 秒`

#### 时间桶标签（`bucketLabel()`）
- 小时粒度：`HH:mm`，如 `15:30`
- 日粒度：`MM/DD`，如 `11/23`

### 3.2 着色规则

#### 成功率着色
- 绿色（`ok`）：≥ 98%
- 黄色（`warn`）：90% ≤ < 98%
- 红色（`danger`）：< 90%

#### 模型芯片着色
- 根据该模型的成功率三档着色
- 深蓝背景 + 彩色圆点指示

---

## 四、交互与行为

### 4.1 页面加载

**首次进入**:
1. 显示加载骨架屏
2. 调用 `GET /api/stats/dashboard?days=1&granularity=hour&platform=&model=`
3. 渲染 KPI 卡、图表、表格

**时间窗口切换** (1天 ↔ 7天 ↔ 30天):
1. 更新 `days` 参数
2. 粒度自动调整：days > 7 时降级为 `day`
3. 重新调用接口，动画更新图表

**粒度切换** (小时 ↔ 天):
1. 仅在 7 天内允许
2. 更新 `granularity` 参数，重新调用

**平台/模型过滤**:
1. 更新对应参数，调用接口
2. 其他筛选组件保持状态

### 4.2 操作

#### 时间窗口按钮
- 位置：页面顶部
- 显示：当前选中的天数（1/7/30）
- 点击切换窗口

#### 粒度选择（仅 ≤7 天）
- 位置：时间窗口按钮旁
- 选项：小时、天
- 禁用提示：「超过 7 天仅支持日粒度」

#### 图表视图切换（消耗分布）
- 位置：消耗分布卡片顶部
- 选项：柱状 / 面积
- 记住用户选择

#### 分析模式切换（模型调用分析）
- 位置：模型分析卡片顶部
- 选项：趋势 / 分布 / 排行
- 切换时平滑过渡

#### 刷新按钮
- 位置：页面顶部右侧
- 行为：`loadDashboard(force=true)`
- 显示最后刷新时间

---

## 五、SVG 图表规范

### 5.1 堆叠柱状/面积图

**参数**:
- `buckets`: 时间桶数组
- `series`: 数据序列（每个模型一条）
- `mode`: `bar` 或 `area`
- `unit`: `credits` 或 `tokens`

**绘制规则**:
- X 轴：时间（底部）
- Y 轴：金额或 tokens（左侧）
- 堆叠：从底往上累加
- 颜色：调色板循环（8 色）
- 动画：从下往上填充（延迟 12ms/根）

**交互**:
- 悬停显示 tooltip：`模型名 · 数值 · 时间桶`
- 移出隐藏 tooltip

### 5.2 多线趋势图

**绘制规则**:
- 每个模型一条线
- 线上打点（圆点表示数据点）
- 线条笔画宽度 1.8px
- 颜色同堆叠图

**交互**:
- 悬停点显示 tooltip
- 鼠标移出隐藏

### 5.3 排行条形图

**绘制规则**:
- 横向条形，从左往右
- 宽度按占比缩放
- 旁显示排名、模型名、占比百分比

---

## 六、跨页联动

### 6.1 平台上下文
- 所有数据自动按 `X-Platform` 请求头过滤
- 切平台时自动刷新看板数据

### 6.2 导航跳转
- 点击某模型行 → 跳转到「模型广场」（P10）并高亮该模型
- 点击某平台贡献行 → 跳转到「平台管理」（P4）
- 点击错误类型 → 跳转到「错误分析」（P15）并过滤该错误类型

---

## 七、边界情况与容错

### 7.1 数据缺失或异常

| 情况 | 处理 |
|------|------|
| 窗口内无请求 | 卡片显示 `0`，图表显示「暂无数据」 |
| 模型/平台为空 | 显示「暂无数据」或默认显示所有 |
| 计算异常（分母为 0） | 显示 `—` 或 `0.00` |
| 缺少 TOP 模型信息 | 只显示有数据的模型 |

### 7.2 加载态与错误

**加载中**:
- 显示骨架屏（KPI 灰色占位、图表灰色背景）
- 禁用时间窗口切换

**请求失败**:
- 显示 Toast：「数据加载失败，请重试」
- 保留上次成功的数据
- 提供「重试」按钮

**响应超时**（> 10 秒）:
- 显示降级视图（上次缓存）
- 后台继续重试

### 7.3 空状态

| 场景 | 显示 |
|------|------|
| 账户被禁用 | 顶部横幅「账户已禁用」，内容置灰 |
| 无平台可用 | 「没有可用平台」 |
| 新账户（无使用记录） | 「暂无数据，请先调用 API」 |
| 过滤条件无匹配 | 「没有符合条件的数据」 |

---

## 八、实现要点

1. **缓存策略**：看板数据缓存 1-2 分钟，时间窗口切换时绕过缓存
2. **性能优化**：
   - SVG 图表一次性生成（而非逐帧 append）
   - 视图切换时整块替换 SVG
   - 避免重排，使用 CSS transform 动画
3. **无障碍**：
   - 卡片用 `<article>` 标签，标题用 `<h3>`
   - 图表用 `role="img"` 和 `aria-label`
   - 数字用 `<span aria-label="完整数字">` 提供完整文字
4. **国际化**：所有文案（「总数」「成功率」等）需提取到 i18n 配置
5. **主题适应**：所有颜色变量用 CSS Custom Properties
6. **调色板**：8 色循环，支持亮/暗主题

---

## 九、旧版有但本契约未涉及的功能

| 功能 | 旧版位置 | 新版建议位置 | 状态 |
|------|---------|------------|------|
| 日均对比 | 卡片对比 | 暂不实现（后期优化） | ⏳ |
| 模型搜索 | 排行表 | 保留（排行表可搜索） | ✓ |
| 自定义时间范围 | 时间选择器 | 暂不实现（固定 1/7/30） | ⏳ |

---

## 实现对照

> 本节由 S17 实现时追加（原 1–387 行未改动）。上文是旧版看板的完整功能描述；
> 新版按设计稿 `06-pages-a.md` P3 与「以 dashboard.js 实际图表为准」的要求实现，
> 与上文不一致处逐行标注了原因。

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 页面骨架（页头 + StatStrip + 主图 + 次级卡片栅格 2 列） | `src/pages/StatsPage.vue` | 已实现 |
| 时间范围 今日 / 7 天 / 30 天 | `Segmented` + `normalizeDays`（`src/api/stats.ts`） | 已实现 |
| 粒度 按小时 / 按天；超过 7 天自动降级为按天 | `normalizeGranularity` + `setDays/setGranularity`（与 dashboard.js:580-596 同规则） | 已实现 |
| 刷新按钮 + 最近更新时间 | 页头 `刷新`（loading 转圈）+ `更新于 HH:mm:ss` | 已实现 |
| StatStrip：请求数 / 成功率 / Token 合计 / 积分消耗 / 平均耗时 | `toSummaryItems`（`summary`） | 已实现（设计稿 P3 的 5 格；旧版 7 张 KPI + 性能健康条按 P3 合并） |
| 请求趋势折线（设计稿写「成功/失败」） | `buildRequestTrend` / `requestTrendOption`（`trend`） | 已实现（有差异：后端 `trend.series` 只有**按模型的请求数**、无按桶成功/失败拆分，dashboard.js 同口径；成功/失败改为 StatStrip 脚注 + 卡片副标题「成功 X · 失败 Y」） |
| Token 趋势堆叠柱（输入 / 输出 / 缓存命中） | `buildTokenTrend` / `tokenTrendOption`（`/api/stats/usage` 的 `trend[]`） | 已实现（输入 = `prompt_tokens - cached_tokens`，缓存命中单独一段；堆叠合计 = Token 总量，避免重复计数） |
| 平台排行 BarList | `platformBarItems`（`dashboard.platforms`，按 requests DESC） | 已实现（条形 tone 按成功率三档着色） |
| 模型排行 BarList | `modelBarItems`（优先 `dashboard.ranking`，含 top N 之外的模型） | 已实现 |
| Key 排行 BarList | `keyBarItems`（`/api/stats/usage` 的 `by_key`） | 已实现（dashboard 接口没有 Key 维度，契约上文未列此接口，本次补充调用） |
| 首次加载骨架 | 4 段 `NSkeleton`（StatStrip / 主图 / 两行卡片） | 已实现 |
| 刷新保留旧数据 | `Card loading` 右上角 14px 转圈 + 页头按钮 loading；失败只 Toast 不清表 | 已实现 |
| 空状态 | 各图表/排行内 `Empty`（窗口内没有调用记录 / 没有 Token / 没有平台流量…） | 已实现 |
| 错误状态 + 重试 | `ErrorBlock`（首屏失败替换内容区；Token/Key 的补充接口失败只在各自卡片内报错，不影响 KPI 与平台/模型排行） | 已实现 |
| 成功率着色（dashboard.js:88-93 三档） | `rateTone`：≥99.5% ok / ≥97% warn / 其余 danger / 无样本 muted | 已实现（上文 §3.2 写 98%/90%，以 dashboard.js 实际口径为准） |
| 数字格式（缩写 / 千分位 / 积分 / 百分比 / 耗时） | `src/utils/format.ts` 的 `fmtCompact / fmtInt / fmtCredit / fmtPct / fmtDuration` | 已实现（统一走全站格式，替代旧版 `cnum/credit/latency`） |
| 时间桶标签（小时 HH:mm、天 MM/DD） | `bucketLabel` | 已实现 |
| 筛选/窗口状态同步路由 query（`days` / `grain`） | `useTableQuery('stats')` | 已实现（刷新/前进后退/分享链接都还原；本页没有表格，分页/排序不适用） |
| 平台上下文订阅 + 卸载取消 | `usePlatformStore().onChange`（selector 变化才重取） | 已实现（说明：`/api/stats/dashboard` 按 `?platform=` 显式筛选、不读 `X-Platform`，切平台时数据口径不变，仅按旧版「切平台即刷新」的行为重新取数） |
| 手机 375px 单列卡片 | `@media (max-width:1023px)` 栅格降为单列；StatStrip 由组件自带 2 列 | 已实现 |
| 图表视图切换（消耗分布柱/面积） | — | 未做（P3 固定为请求趋势 + Token 堆叠两图，不再提供视图切换） |
| 模型调用分析三视图（趋势/分布/排行） | 请求趋势 + 模型排行两张卡 | 已实现（有差异：P3 拆成独立卡片，不再用页签切换） |
| 平台贡献点击筛选 / 模型行跳转模型广场 / 错误分类跳转 | — | 未做（P3 未列出；错误分类与平台点击筛选属旧版交互，后续如需再加） |
| 失败分类卡片（error_kind） | — | 未做（P3 未列出；数据仍在 `dashboard.errors`，接口已保留） |
| 旧版「消耗分布（按模型堆叠）」 | — | 未做（P3 用 Token 输入/输出/缓存命中替代；`dashboard.distribution` 未消费） |
| 单元测试（分桶标签 / 格式化 / 图 option / 排行 / 空值） | `tests/page-stats.spec.ts`（19 个用例，用后端真实响应形状） | 已实现 |

### 口径核对（以 dashboard.js 与后端实际字段为准）

- `summary.requests` 含失败；`successes = error_kind in ('', 'success')`；`credits` / `total_tokens` 只算成功请求。
- `success_rate` 无请求时为 `null`（显示 `—`，不是 0%）；`avg_latency_ms` / `avg_ttfb_ms` 只取成功样本。
- `rpm` / `tpm` 的分母是 `window.minutes`（滚动窗口的实际分钟数），不是固定 1440。
- `trend.series[].requests` 是**按模型、按桶**的请求数；`trend.total_requests` 是全部模型合计（含 top N 之外的模型），新版折线第一条即「全部请求」。
- `distribution.series[].metric` 是**每条系列各自**的 `credits` / `tokens`（不是接口级字段），旧版按每条系列自己判断单位。
- `platforms[]` 的键是 `value`（不是 `platform`）；`ranking[].share` = `requests / summary.requests`；`errors[]` 只有 `kind` / `count`，占比由前端用 `summary.failures` 算。
- `/api/stats/usage` 的 `trend[].requests` 只统计成功请求（`_ok_filter`）；`cached_tokens` 是 `prompt_tokens` 的子集，所以 Token 堆叠图的「输入」必须先减去缓存段。


