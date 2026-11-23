# 功能契约：概览页（Overview）

**页面标识**: P1（TABS 第一个）  
**入口函数**: `loadOverview(force, opts={scope:"all"|"usage"})`  
**路由**: 应用启动默认打开，或点击导航栏「概览」

---

## 一、页面整体结构

```
┌─ 平台上下文切换器（侧栏顶部）
├─ 时间粒度切换（1天 / 7天 / 30天）
│
├─ 上半屏：概览卡片矩阵
│  ├─ 余额卡片组（4行）
│  │  ├─ 账户余额 / 总余额 / 冻结余额 / 平均每日消耗
│  │  └─ 着色规则：超高风险/异常消耗时标红
│  │
│  ├─ 使用统计卡片组（1行，4列）
│  │  ├─ 请求数 / 提示词Tokens / 完成Tokens / 缓存命中
│  │  └─ 数字格式、缩写、简单着色
│  │
│  └─ 账号监控卡片（监控覆盖度，分状态聚合）
│
├─ 中间屏：缓存统计卡片（可折叠）
│  ├─ 缓存命中率、缓存节省额度
│  ├─ 按平台折分详情
│  └─ 展开时显示数据表格或图表
│
├─ 下半屏：告警横幅（可选）
│  ├─ 风控告警（来自 /api/sentinel/status）
│  └─ 其他重要事件（待定）
│
└─ 底部：数据采集时间戳 + 刷新按钮
```

---

## 二、数据来源与接口

### 主接口

```http
GET /api/overview?days={1|7|30}&sections={all|usage}
```

**参数**:
- `days`: 时间窗口（1、7、30 天，对应「今日」「最近7天」「最近30天」）
- `sections`: 返回哪些数据块
  - `all` - 完整（开页面时用）
  - `usage` - 只刷新用量数据（后续轮询可用，可选）

**返回字段**（`sections=all`）:

```json
{
  "balance_remain": 123.45,           // 账户当前余额
  "balance_total": 500.00,            // 总充值额
  "disabled_balance": 10.00,          // 冻结额度
  "average_credits": 45.67,           // 滚动平均每日消耗
  
  "window": {
    "requests": 1250,                 // 窗口内请求总数
    "ok_requests": 1200,              // 成功请求数
    "prompt_tokens": 50000,
    "completion_tokens": 15000,
    "total_tokens": 65000,
    "cached_tokens": 5000,            // 缓存读取的 token 数
    "credits": 123.45                 // 窗口内消耗积分
  },
  
  "cache": {
    "cached_tokens": 5000,            // 缓存节省的 tokens
    "prompt_tokens": 50000,           // 总提示词 tokens
    "miss_tokens": 45000,             // 缓存未命中的 tokens
    "rate": 0.0909,                   // 命中率（0.0909 = 9.09%）
    "samples": 1200,                  // 有缓存数据的请求数
    "by_platform": {                  // 各平台命中率
      "openai": { "rate": 0.15, "cached": 500, "total": 3333 },
      "deepseek": { "rate": 0.08, "cached": 4000, "total": 50000 }
    },
    "note": "缓存功能仅在部分模型/平台支持"
  },
  
  "monitor": {
    "active": 12,                     // 活跃账号数
    "active_with_balance": 10,        // 有余额的活跃账号
    "paused": 2,                      // 暂停中的账号
    "disabled": 1,                    // 已禁用的账号
    "details": [
      {
        "state": "active",
        "count": 12,
        "avg_balance": 10.5,
        "last_used": 1700000000        // Unix 时间戳
      },
      {
        "state": "paused",
        "count": 2,
        "avg_balance": 0,
        "reason": "主动暂停"
      },
      // ... 其他状态
    ]
  }
}
```

### 副接口

**获取平台列表**（切换平台时用）:
```http
GET /api/platforms
```
返回 `[{ "name": "openai", "label": "OpenAI", "region": "美国", "disabled": false }, ...]`

**获取告警状态**（横幅判定）:
```http
GET /api/sentinel/status?window=300
```
返回 `{ "breach": false, "tripped": false, "accounts_affected": 0, ... }`

**获取侧栏圆点**（账号状态指示）:
```http
GET /api/accounts/badges
```
返回各状态的账号数，用于侧栏状态指示器

---

## 三、数据展示规范

### 3.1 数字格式化

#### 缩写格式（`ovNum()`）
- 用于大数字简化显示
- 规则：
  - ≥ 1 亿（1e8）：显示为 B（亿），2 位小数，如 `1.23B`
  - ≥ 1 万（1e4）：显示为 M（万），2 位小数，如 `12.34M`  
  - ≥ 1 千（1e3）：显示为 K（千），1 位小数，如 `1.2K`
  - < 1 千：显示原数，四舍五入

#### 千分位格式（`ovInt()`）
- 用于金额、精确计数
- 规则：千分位分组，不缩写，如 `1,234,567`

#### 金额格式（`ovCredit(sign)`）
- 用于余额、消耗、积分
- 规则：千分位 + 固定 2 位小数，如 `1,234.56`
- 可选加正负号：`+123.45` / `-67.89`

#### 时间格式（`fmtTime(s, withSec)`）
- 输入：Unix 时间戳（秒）
- 输出：本地时间（UTC+8），格式为 `YYYY-MM-DD HH:mm:ss` 或 `HH:mm:ss`
- 规则：没有秒时省略秒字段

### 3.2 着色规则

#### 余额着色
- 绿色（`ok`）：余额 ≥ 平均日消耗 × 10 天
- 黄色（`warn`）：10 天 > 余额 ≥ 3 天
- 红色（`danger`）：余额 < 3 天的平均消耗
- 灰色（`disabled`）：账户已禁用

#### 成功率着色
- 绿色（`ok`）：≥ 98%
- 黄色（`warn`）：90% ≤ < 98%
- 红色（`danger`）：< 90%

#### 命中率着色
- 绿色（`ok`）：≥ 10%
- 黄色（`warn`）：5% ≤ < 10%
- 灰色（`neutral`）：< 5% 或无数据

---

## 四、交互与行为

### 4.1 页面加载

**首次进入**:
1. 显示加载状态
2. 调用 `GET /api/overview?days=1&sections=all`
3. 渲染所有卡片
4. 如果 `sections=all` 中包含 `cache`，展开缓存卡片；否则折叠

**时间粒度切换** (1天 ↔ 7天 ↔ 30天):
1. 更新 URL 参数（可选）或本地状态
2. 重新调用接口，`days` 参数更新
3. 平滑动画更新卡片数值

**平台切换**（通过平台上下文事件 `wb-platform-change`）:
1. 触发 `loadOverview(true, {scope: "usage"})`
2. 只刷新使用统计数据（不重新进页面）
3. 其他卡片平滑更新数值

### 4.2 操作

#### 刷新按钮
- 位置：页面底部右侧或卡片右上角
- 行为：`loadOverview(true, {scope: "all"})`
- 显示：最后刷新时间

#### 告警横幅交互
- 显示条件：`/api/sentinel/status` 返回 `breach: true` 或 `tripped: true`
- 内容：「风控熔断已触发」或「有账号余额不足」
- 操作：点击可导航到「风控哨兵」页（P2）或「账号管理」页（P13）
- 自动关闭：5 秒后消失（用户可手动关闭）

#### 缓存卡片折叠/展开
- 默认折叠（如果 `cache.note` 中有备注、或命中率为 0）
- 点击展开时显示表格：按平台列举命中率、缓存节省额度
- 展开后表格可排序（点击列头）

---

## 五、跨页联动

### 5.1 平台上下文
- 所有数据自动按 `X-Platform` 请求头过滤
- 切平台时 localStorage 同步更新，页面自动刷新数据
- CustomEvent `wb-platform-change` 由 platform-switch.js 发送，本页监听并重载

### 5.2 导航跳转
- 点击「账号监控」卡片中的某个状态标签 → 跳转到「账号管理」（P13）并按状态过滤
- 点击「告警横幅」→ 跳转到「风控哨兵」（P2）
- 点击「缓存统计」中的某平台行 → 跳转到「缓存分析」（P24，如有）

### 5.3 全局事件
- 账号状态变化时，侧栏会监听更新并发出事件，概览页收到后局部刷新「账号监控」卡片
- 平台被禁用时，概览页切换到其他平台（若当前平台被禁用）

---

## 六、边界情况与容错

### 6.1 数据缺失或异常

| 情况 | 处理 |
|------|------|
| 余额字段为 null | 显示 `—` 或 `0.00` |
| 窗口内无请求 | 卡片显示 `0`，不着色 |
| 缓存数据为空 | 缓存卡片折叠，显示「暂无缓存数据」 |
| 命中率计算分母为 0 | 显示 `—` 或 `0%` |
| 账号监控无数据 | 显示「暂无账号」 |
| 平台标签缺失 | 显示平台代码名 |

### 6.2 加载态与错误

**加载中**:
- 显示骨架屏或灰色占位符
- 卡片内容淡出
- 可选：显示「更新中…」文案

**请求失败**:
- 显示错误提示（Toast）：「数据加载失败，请重试」
- 保留上次成功的数据（若有）
- 提供「重试」按钮

**响应超时**（> 5 秒）:
- 显示降级视图（上次缓存数据或仅显示时间戳）
- 后台继续重试
- 收到响应后自动更新

### 6.3 空状态

| 场景 | 显示 |
|------|------|
| 账户被禁用 | 顶部显示横幅「账户已禁用」，所有卡片置灰 |
| 无平台可用 | 显示「没有可用平台，请联系管理员」 |
| 新账户（首次使用） | 余额为 0，无使用记录，提示「欢迎使用，请先充值或绑定 API Key」 |
| 账户余额为负 | 余额卡片显示负数（红色），且显示「欠费」标记 |

### 6.4 功能开关与平台差异

**平台只读字段**:
- 某些平台可能不支持「缓存」功能：`cache` 字段为 null 时，缓存卡片不显示
- 某些平台不支持「账号监控」：`monitor` 字段为 null 时，该卡片不显示

**端差异**（PC vs 移动）:
- **PC**: 卡片横向排列，缓存卡片展开为表格
- **移动**: 卡片纵向堆叠，缓存卡片展开为竖向列表

---

## 七、旧版有但本契约未涉及的功能

| 功能 | 旧版位置 | 新版建议位置 | 状态 |
|------|---------|------------|------|
| 实时告警条 | 概览页下方 | 保留（P2 风控哨兵） | ✓ 已规划 |
| 昨日/今日对比 | 概览页卡片 | 暂不实现（作为后期优化项） | ⏳ |
| 按账号分类统计 | 概览页底部 | 移到「账号分析」（P16） | ✓ |
| 模型TOP排行 | 概览页右侧 | 移到「数据看板」（P11） | ✓ |

---

## 八、实现要点

1. **缓存策略**：概览数据缓存 30 秒～1 分钟，用户点击刷新时 `force=true` 绕过缓存
2. **性能优化**：缓存卡片默认不加载详细数据，展开时才异步加载
3. **无障碍**：
   - 卡片用 `<article>` 或 `<section>` 语义标签，标题用 `<h3>`
   - 数字用 `<span aria-label="...">` 提供完整文字描述
   - 着色规则不单独依靠颜色，要有文字/图标补充
4. **国际化**：所有文案（「余额」「请求」等）需提取到 i18n 配置
5. **主题适应**：所有颜色变量用 CSS Custom Properties（`--c-ok` 等），支持亮/暗主题切换

---

## 实现对照

> 本节由 S21 实现时追加（原 1–307 行未改动）。上文是旧版概览的完整描述；
> 新版按设计稿 `06-pages-a.md` P1 实现，与上文不一致处逐行标注了原因。

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 页面骨架：状态行 + 英雄卡 + 用量条 + 流量带 / 账号状态 + 缓存卡 | `src/pages/OverviewPage.vue` | 已实现 |
| 时间范围 今日 / 7 天 / 30 天 + 刷新 + 更新时间 | `Segmented` + `?days=` + 状态行「更新于」 | 已实现 |
| 状态行：运行状态、Base URL（可复制）、平台数 / 模型数、当前平台、更新时间 | `StatusDot` + `IconBtn(Copy)` + `buildUsageItems` 之外的状态字段 | 已实现（运行状态 = 页面能取到数据，与旧版静态徽标同口径；没有独立健康检查接口） |
| 剩余额度英雄卡：`--fs-display` 大数字 + 今日 / N 天 / 户均 + Meter（<20% warn、<5% danger）+ 总额度与账号数 + 已禁用余额说明 | `buildBalanceHero` + `Meter` | 已实现 |
| 右侧用量条：请求 / 成功率 / 输入 Token / 输出 Token / Token 合计 | `buildUsageItems` + `StatStrip`（设计稿 P3 的五格口径） | 已实现 |
| 今日流量 RouteRibbon（点击线路 → `/platforms` 并选中该平台） | `buildLanes` + `RouteRibbon @select` + `platformStore.setCurrent` | 已实现（「选中」= 切换全局平台上下文；平台管理页尚未做行级选中，见下方接线说明） |
| 账号状态：5 行 Meter（活跃/冷却/熔断/额度耗尽/已禁用），0 值灰字，结论跳 `/accounts?status=cooldown` | `buildMonitorRows` / `monitorConclusion` | 已实现（目标页需读取 `status` query；账号页由 S25 实现，见接线说明） |
| 告警横幅：danger / warn soft + 「查看」 | `buildAlerts`（封控 → 平台暂停 → 账号冷却 → 额度不足 20%） | 已实现 |
| 缓存命中：Ring + 3 个 KV（输入命中/输入总量/样本请求）+ 说明 | `buildCache` + `Ring` + `KV` | 已实现 |
| 首次加载骨架 | `NSkeleton`（状态行 / 英雄 / 两列卡 / 缓存） | 已实现 |
| 刷新保留旧数据 | `Card loading` 转圈 + 页头按钮 loading；失败只 Toast 不清表 | 已实现 |
| 空状态 | 账号卡 `Empty`（无账号）+ 流量带自带「暂无流量」+ 各数值 0 / — | 已实现 |
| 错误状态 + 重试 | `ErrorBlock`（首屏失败替换内容区） | 已实现 |
| 平台上下文订阅 + 卸载取消 | `usePlatformStore().onChange`（selector 变化才重取） | 已实现（`/api/overview` 读 `X-Platform`，切平台数据确实变化） |
| 时间范围同步路由 query | `useTableQuery('overview')` 的 `days` filter | 已实现（刷新 / 前进后退 / 分享链接可还原） |
| 手机 375px 可用 | 英雄与两列卡 ≤1023px 单列；状态行换行；缓存卡纵向 | 已实现（设计稿要求「手机 2 列」，实现为 1023 以下单列 + StatStrip 自带 2 列网格，避免 375px 挤压） |
| 失败分类 / 模型 Top 排行 | — | 未做（旧版概览没有；P1 把它们放在数据看板 `/stats`，见 S17） |
| 旧版底部平台状态网格 | 改为今日流量 RouteRibbon | 已实现（有差异：P1 明确用线路带替代网格） |
| 旧版账号异常明细列表（最多 6 条 + 剩余冷却分钟） | — | 未做（P1 只要求 5 行 Meter + 一句结论；明细在账号池 `/accounts`） |
| 缓存卡「命中 / 未命中」构成条与分平台命中率 | — | 未做（P1 只要求 Ring + 3 KV + 说明；`cache.by_platform` / `miss_tokens` 接口已返回，后续可加） |
| 缓存卡折叠 / 展开（契约 §4.2） | — | 未做（P1 无折叠交互，卡片常显） |
| 数字滚动动效（旧版 countTo） | — | 未做（01-direction §6 明确禁止数字滚动动画） |
| 侧栏风控角标 | 外壳 `platform/shellBadges` | 不在本页（外壳统一维护） |
| 成功率着色阈值（上文 §3.2：98% / 90%） | `rateTone`（`src/api/stats.ts`） | 有差异：复用全站统一阈值 ≥99.5% ok / ≥97% warn（与 dashboard.js 一致），避免概览与数据看板两套颜色 |
| 命中率着色（≥10% ok / ≥5% warn） | `buildCache` | 已实现 |
| 单元测试（英雄卡 / 用量条 / 监测 / 告警 / 缓存 / 流量带） | `tests/page-overview.spec.ts`（19 个用例，用后端真实响应形状） | 已实现 |

### 口径核对（以 overview_router.py / sentinel.py 实际字段为准）

- `accounts` 段只统计 `status='active'` 的账号；`balance_remain` / `balance_total` 是整数，`used_ratio = 1 - 剩余/总额`（总额为 0 时 `null`）。
- `today` / `window` / `month` 的 `requests` 含失败，`ok_requests` 是成功数，`prompt_tokens` / `completion_tokens` / `total_tokens` / `cached_tokens` / `credits` 只累加成功请求。
- `window.days` 只有 `window` 段带（后端 `dict(window, days=days)`）；`month` 是最近 30 天（30 天口径下与 `window` 相同）。
- `cache.rate` 的分母只算「最早一条带缓存命中之后」的输入 token；无样本时 `rate = null`（页面显示 —，不显示 0%）。
- `monitor` 分桶优先级：`disabled` > `breaker` > `cooling` > `no_balance` > `active`，`total` 是账号总数。
- `/api/sentinel/status` 的 `accounts_affected` 只算真风控信号，`pause` 是 router 追加的「平台 -> 暂停信息」。
- 流量带平台维度来自 `/api/stats/dashboard.platforms`（按落库平台聚合，`trae` 覆盖 `trae-cn` / `trae-intl`），`success_rate` 无样本时为 `null`；`/api/platforms` 只用来取图标与禁用状态。
