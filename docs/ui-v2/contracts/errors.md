# 功能契约：错误分析页（Errors Analysis）

**页面标识**: P15（TABS 第十五个）  
**入口函数**: `loadErrors(opts={days, type})`  
**路由**: `/errors`

---

## 一、页面整体结构

```
错误分析                    时间窗口 [24h] [7天] [30天]  [导出] [刷新]
┌─ 错误统计卡片矩阵 ─────────────────────────────────────────────────────┐
│ 总错误数 · 错误率 · 影响账号数 · 高频错误 TOP 1 · 最后发生时间           │
│ [着色规则：超过阈值时标黄/红]                                           │
└─────────────────────────────────────────────────────────────────────────┘
┌─ 错误分类与分布 ──────────────────────────────────────────────────────┐
│ [柱状图或表格切换]                                                       │
│ 柱图视图：X=错误类型  Y=发生次数  着色=错误级别（rate_limit/timeout/…） │
│ 表格视图：错误类型 · 发生次数 · 影响账号数 · 最后发生时间 · 操作        │
│ [按发生次数 ↓/↑ 排序]  [按影响账号数 ↓/↑ 排序]                         │
└─────────────────────────────────────────────────────────────────────────┘
┌─ 按平台分布（可折叠）─────────────────────────────────────────────────┐
│ 平台 · 错误数 · 百分比 · 主要错误类型                                   │
│ [可展开查看该平台的详细错误列表]                                        │
└─────────────────────────────────────────────────────────────────────────┘
┌─ 告警规则管理 ────────────────────────────────────────────────────────┐
│ 规则名称 · 触发条件 · 通知方式 · 启用/禁用 · 最近触发 · 操作            │
│ ┌─ 规则 #1：rate_limit 过频 ─────────────────────────────────┐         │
│ │ 条件: 1 分钟内相同账号 rate_limit ≥ 5 次                    │ [启用]  │
│ │ 通知: Email · Slack  最近触发: 2 小时前                    │ [编辑]  │
│ │ [测试规则] [删除]                                           │ [⋯]    │
│ └─────────────────────────────────────────────────────────────┘         │
│ [+ 创建规则]                                                             │
└─────────────────────────────────────────────────────────────────────────┘
```

**整体布局说明**:
- 顶部统计卡片矩阵（快速浏览）
- 中间核心区域：错误分类统计（切换视图）
- 下拉区域：平台分布详情、告警规则列表
- 底部操作：导出、刷新、创建规则

---

## 二、数据来源与接口

### 2.1 错误统计接口

```http
GET /api/stats/errors?days={1|7|30}&platform=...
```

**查询参数**:
- `days` - 时间窗口（1、7、30 天）
- `platform` - 平台过滤（可选，查询参数或请求头 `X-Platform`）

**返回字段**:

```json
{
  "summary": {
    "total_errors": 342,              // 总错误次数
    "error_rate": 0.0234,             // 错误率（错误数/总请求数）
    "affected_accounts": 18,          // 有错误的账号数
    "top_error": {
      "type": "rate_limit",
      "count": 128,                   // 该错误发生次数
      "last_occurred_at": "2026-11-23T10:45:12Z"
    },
    "window_days": 1
  },
  
  "errors_by_type": [
    {
      "error_type": "rate_limit",     // 错误代码/分类
      "error_label": "限流",
      "count": 128,
      "percentage": 37.43,            // 占比百分比
      "affected_accounts": 8,
      "last_occurred_at": "2026-11-23T10:45:12Z",
      "severity": "warn"              // critical/warn/info
    },
    {
      "error_type": "timeout",
      "error_label": "超时",
      "count": 95,
      "percentage": 27.78,
      "affected_accounts": 12,
      "last_occurred_at": "2026-11-23T10:40:00Z",
      "severity": "warn"
    },
    {
      "error_type": "auth_error",
      "error_label": "鉴权失败",
      "count": 67,
      "percentage": 19.59,
      "affected_accounts": 5,
      "last_occurred_at": "2026-11-23T09:30:00Z",
      "severity": "critical"
    },
    {
      "error_type": "model_not_found",
      "error_label": "模型不存在",
      "count": 32,
      "percentage": 9.36,
      "affected_accounts": 3,
      "last_occurred_at": "2026-11-23T08:15:00Z",
      "severity": "info"
    },
    {
      "error_type": "other",
      "error_label": "其他错误",
      "count": 20,
      "percentage": 5.84,
      "affected_accounts": 4,
      "last_occurred_at": "2026-11-23T07:00:00Z",
      "severity": "warn"
    }
  ],
  
  "errors_by_platform": [
    {
      "platform": "openai",
      "platform_label": "OpenAI",
      "total_errors": 120,
      "percentage": 35.09,
      "top_errors": [
        { "type": "rate_limit", "count": 60 },
        { "type": "timeout", "count": 35 },
        { "type": "auth_error", "count": 25 }
      ]
    },
    {
      "platform": "deepseek",
      "platform_label": "DeepSeek",
      "total_errors": 180,
      "percentage": 52.63,
      "top_errors": [
        { "type": "timeout", "count": 85 },
        { "type": "rate_limit", "count": 60 },
        { "type": "model_not_found", "count": 32 }
      ]
    },
    {
      "platform": "claude",
      "platform_label": "Claude",
      "total_errors": 42,
      "percentage": 12.28,
      "top_errors": [
        { "type": "auth_error", "count": 25 },
        { "type": "rate_limit", "count": 10 }
      ]
    }
  ]
}
```

### 2.2 告警规则列表接口

```http
GET /api/alerts/rules?enabled={all|true|false}
```

**返回字段**:

```json
{
  "items": [
    {
      "id": 1,
      "name": "rate_limit 过频",
      "description": "1 分钟内相同账号 rate_limit ≥ 5 次",
      "enabled": true,
      
      "condition": {
        "error_type": "rate_limit",
        "window_minutes": 1,
        "threshold": 5,
        "scope": "account"              // account/platform/global
      },
      
      "notification": {
        "channels": ["email", "slack"],
        "recipients": ["admin@example.com", "slack://alert-channel"]
      },
      
      "created_at": "2026-11-01T12:00:00Z",
      "updated_at": "2026-11-23T10:00:00Z",
      "last_triggered_at": "2026-11-23T10:45:12Z",
      "trigger_count": 7                // 自创建以来触发次数
    },
    {
      "id": 2,
      "name": "timeout 预警",
      "description": "5 分钟内 timeout 错误数 ≥ 10",
      "enabled": true,
      
      "condition": {
        "error_type": "timeout",
        "window_minutes": 5,
        "threshold": 10,
        "scope": "global"
      },
      
      "notification": {
        "channels": ["email"],
        "recipients": ["ops@example.com"]
      },
      
      "created_at": "2026-11-02T14:30:00Z",
      "updated_at": "2026-11-20T15:00:00Z",
      "last_triggered_at": "2026-11-23T09:30:00Z",
      "trigger_count": 3
    },
    // ...
  ],
  "total": 12
}
```

### 2.3 创建/编辑规则接口

```http
POST /api/alerts/rules
PUT /api/alerts/rules/{id}
```

**请求体**:

```json
{
  "name": "rate_limit 过频",
  "description": "1 分钟内相同账号 rate_limit ≥ 5 次",
  "enabled": true,
  
  "condition": {
    "error_type": "rate_limit",       // 错误类型（必填）
    "window_minutes": 1,              // 时间窗口（必填）
    "threshold": 5,                   // 阈值（必填）
    "scope": "account"                // account/platform/global（必填）
  },
  
  "notification": {
    "channels": ["email", "slack"],
    "recipients": ["admin@example.com", "slack://alert-channel"]
  }
}
```

**返回**:

```json
{
  "id": 1,
  "name": "rate_limit 过频",
  "enabled": true,
  "created_at": "2026-11-01T12:00:00Z",
  "message": "规则已保存"
}
```

### 2.4 删除规则接口

```http
DELETE /api/alerts/rules/{id}
```

**返回**:

```json
{
  "message": "规则已删除",
  "id": 1
}
```

### 2.5 测试规则接口

```http
POST /api/alerts/rules/{id}/test
```

**请求体** (可选，用于模拟特定场景):

```json
{
  "error_type": "rate_limit",
  "account_id": 123,
  "count": 5
}
```

**返回**:

```json
{
  "rule_id": 1,
  "triggered": true,
  "matched_condition": {
    "error_type": "rate_limit",
    "window_minutes": 1,
    "threshold": 5,
    "scope": "account"
  },
  "notification_sent": {
    "email": ["admin@example.com"],
    "slack": ["slack://alert-channel"]
  },
  "message": "规则已触发，通知已发送"
}
```

### 2.6 导出错误日志

```http
GET /api/errors/export?days=...&error_type=...&platform=...&format={csv|json}
```

**返回**:
- CSV 文件流（Content-Type: `text/csv; charset=utf-8`）
- JSON 文件流（Content-Type: `application/json`）

**导出字段**: 时间 · 错误类型 · 错误代码 · 账号 · 平台 · 模型 · 诊断信息 · 错误消息

---

## 三、交互与行为

### 3.1 页面加载与时间窗口切换

**首次进入**:
1. 显示加载骨架屏
2. 调用 `GET /api/stats/errors?days=1`（默认显示 24 小时）
3. 同时调用 `GET /api/alerts/rules`
4. 渲染统计卡片、错误分类图表、规则列表

**时间窗口切换** (24h ↔ 7天 ↔ 30天):
1. 更新 URL 参数（可选）或本地状态
2. 重新调用接口，`days` 参数更新
3. 平滑动画更新卡片数值与图表

### 3.2 错误分类统计交互

#### 视图切换
- **柱状图视图**（默认）：
  - X 轴：错误类型
  - Y 轴：发生次数
  - 着色：按 severity（critical=红, warn=黄, info=灰）
  - 点击柱子可过滤到该错误类型的日志
  
- **表格视图**：
  - 列：错误类型 · 发生次数 · 影响账号数 · 百分比 · 最后发生时间 · 操作
  - 点击列头排序
  - 操作：「查看日志」→ 跳转到日志页并过滤该错误类型

#### 着色规则
- **Severity 等级**:
  - `critical` (红)：认证错误、系统错误等严重问题
  - `warn` (黄)：限流、超时等临时问题
  - `info` (灰)：模型不存在、已弃用等信息类

### 3.3 平台分布交互

**默认折叠**，点击展开时：
1. 显示各平台的错误数与百分比
2. 显示该平台的 TOP 3 错误类型
3. 可点击「查看详情」→ 跳转日志页并按平台过滤
4. 右侧「向下箭头」动画表示展开/收起

### 3.4 告警规则管理

#### 规则列表操作

| 操作 | 触发 | 行为 |
|------|------|------|
| 启用/禁用 | 规则行的开关 | 切换 `enabled` 字段，调用 `PUT /api/alerts/rules/{id}` |
| 编辑 | `[编辑]` 按钮 | 打开 FormDrawer，预填当前规则数据，提交时调用 `PUT /api/alerts/rules/{id}` |
| 删除 | `[⋯]` → 删除 | 显示确认框，确认后调用 `DELETE /api/alerts/rules/{id}` |
| 测试 | `[测试规则]` 按钮 | 调用 `POST /api/alerts/rules/{id}/test`，显示结果：「规则已触发」或「未匹配条件」，显示将发送的通知渠道 |

#### 创建规则

**入口**: 规则列表下方 `[+ 创建规则]` 按钮

**FormDrawer** (640px):

```
┌─ 基本信息 ─────────────────────────────────┐
│ 规则名称 [________________]                 │
│ 描述     [________________________________] │
│ 启用     [⚪ 开] (默认启用)                │
└────────────────────────────────────────────┘
┌─ 触发条件 ────────────────────────────────┐
│ 错误类型   [▾ 选择: rate_limit/timeout/…] │
│ 时间窗口   [1] 分钟                        │
│ 阈值      [5] 次/min                      │
│ 应用范围   [▾ 账号级 / 平台级 / 全局 ]   │
│ ⓘ 账号级: 检查单个账号的错误频率          │
│   平台级: 检查单个平台的错误总数          │
│   全局: 检查所有平台的错误总数            │
└────────────────────────────────────────────┘
┌─ 通知方式 ────────────────────────────────┐
│ ☑ Email   收件人: [______________]        │
│ ☐ Slack   频道:   [______________]        │
│ ☐ Webhook URL:    [______________]        │
│ ⓘ 一条规则可配置多个通知渠道              │
└────────────────────────────────────────────┘
      [取消]  [保存规则]
```

**校验规则**:
- 规则名称：必填，最多 50 字
- 错误类型：必填
- 时间窗口：1-60 分钟，必填
- 阈值：1-10000，必填
- 至少配置一个通知渠道
- Email 和 Webhook 格式校验

**提交后**:
1. 调用 `POST /api/alerts/rules`
2. 成功：显示 Toast「规则已创建」，刷新规则列表，新规则显示在列表顶部
3. 失败：显示错误信息

### 3.5 导出

**右上角 `[导出 ▾]` 下拉菜单**:
- **导出 CSV** —— 调用 `GET /api/errors/export?days=...&format=csv` 下载 `errors.csv`
- **导出 JSON** —— 调用 `GET /api/errors/export?days=...&format=json` 下载 `errors.json`

导出范围：**当前时间窗口** + **所有错误类型/平台**（无额外筛选）

---

## 四、数据展示规范

### 4.1 数字格式化

#### 计数格式（`errCount()`）
- ≥ 1 万（1e4）：显示为 K（千），1 位小数，如 `12.3K`
- < 1 万：显示原数

#### 百分比格式（`errPercent()`）
- 两位小数，如 `37.43%`

#### 时间格式（`fmtTime(s)`）
- 表格行：`HH:mm:ss` 或 `MM-DD HH:mm` (超过 7 天前)
- 详情页：`YYYY-MM-DD HH:mm:ss`
- 时区：UTC+8（北京时间）

#### 错误率格式（`errRate()`）
- 百分比，2 位小数，如 `2.34%`

### 4.2 着色规则

#### 错误类型 severity 着色
| Severity | 颜色 | 含义 |
|----------|------|------|
| critical | 红 (#dc2626) | 严重错误（认证、系统故障） |
| warn | 黄 (#f59e0b) | 警告（限流、超时） |
| info | 灰 (#6b7280) | 信息（模型不存在、已弃用） |

#### 错误率着色
- 绿色（`ok`）：≤ 1%
- 黄色（`warn`）：1% < x ≤ 5%
- 红色（`danger`）：> 5%

#### 影响账号数着色
- 绿色（`ok`）：≤ 3
- 黄色（`warn`）：3 < x ≤ 10
- 红色（`danger`）：> 10

---

## 五、边界情况与容错

### 5.1 数据缺失或异常

| 情况 | 处理 |
|------|------|
| 窗口内无错误 | 统计卡片显示 `0`，不着色；图表显示「暂无错误数据」 |
| 错误率计算分母为 0 | 显示 `—` 或 `0%` |
| 平台分布为空 | 折叠区显示「本期无错误」 |
| 规则列表为空 | 显示「暂无告警规则」+「创建规则」按钮 |
| error_type 未知 | 显示原始代码，补充说明「未知错误类型」 |
| 通知渠道配置缺失 | 规则创建时验证错误提示「至少选择一个通知方式」 |

### 5.2 加载态与错误

**加载中**:
- 统计卡片显示灰色占位符
- 图表区域显示加载骨架屏
- 规则列表显示 3 个占位符行

**请求失败**:
- 显示错误提示 (Toast)：「数据加载失败，请重试」
- 保留上次成功的数据（若有）
- 提供「重试」按钮

**响应超时**（> 5 秒）:
- 显示降级视图（上次缓存数据或仅显示时间戳）
- 后台继续重试
- 收到响应后自动更新

### 5.3 空状态

| 场景 | 显示 |
|------|------|
| 所有平台禁用 | 顶部显示横幅「没有可用平台」 |
| 新账户（无错误数据） | 统计卡片全为 0，图表显示「暂无数据，请等待用户请求」 |
| 时间范围异常 | 验证错误提示（若有自定义范围选项） |
| 规则测试无匹配 | Toast 显示「条件不满足，规则未触发」 |

### 5.4 权限与可见性

- **平台隔离**：用户只能看本平台的错误统计（由 `X-Platform` 头自动过滤）
- **规则管理权限**：普通用户可看规则但不可编辑；管理员可全部操作
- **测试权限**：规则创建者或管理员可测试规则

---

## 六、跨页联动

### 6.1 导航跳转

- 点击错误类型卡片 / 表格行 → 跳转到「日志页」(P11) 并按 `error_type` 过滤
- 点击平台名 → 该页面自动按平台过滤（无需跳转）
- 规则列表「查看日志」→ 跳转到日志页并按 `error_type` 过滤
- 统计卡片中的「影响账号数」→ 跳转到「账号管理」(P6) 并按状态标记异常的账号

### 6.2 全局事件

- **实时错误通知**：当新错误发生且匹配某规则时，后台自动发送通知（邮件 / Slack）
- **规则被启用/禁用**：实时对新发生的错误生效（无需刷新页面）
- **平台被禁用**：页面自动切换到其他可用平台，重新拉取数据

---

## 七、实现要点

### 7.1 性能优化

1. **缓存策略**：错误统计数据缓存 1 分钟，用户点击刷新时 `force=true` 绕过缓存
2. **图表渲染**：使用 `react-chartjs-2` 或 `recharts` 库，限制数据点数 ≤ 20（超出时合并「其他」）
3. **规则列表**：列表不分页（通常 ≤ 50 条规则），若超出可考虑虚拟滚动

### 7.2 无障碍与国际化

- 统计卡片用 `<article>` 或 `<section>` 语义标签，标题用 `<h2>`
- 图表用 `aria-label` 提供完整描述：「柱状图：错误类型分布，rate_limit 128 次占比 37.43%」
- 着色规则不单独依赖颜色，要有文字/图标补充（如 severity 图标）
- 所有文案提取到 i18n 配置

### 7.3 主题与样式

- 所有颜色用 CSS Custom Properties（`--c-critical`, `--c-warn`, `--c-info` 等）
- 支持亮/暗主题无缝切换
- 表格行 hover 时高亮背景（`--bg-hover`）
- 图表背景自适应主题

### 7.4 测试

- **单元测试**：格式化函数（count / percent / time）
- **集成测试**：规则创建/编辑/删除、时间窗口切换、导出功能
- **E2E 测试**：规则测试流程、通知发送验证
- **性能测试**：1000+ 条错误记录渲染时间 < 2s

---

## 八、旧版有但本契约未直接涉及的功能

| 功能 | 旧版位置 | 新版建议位置 | 状态 |
|------|---------|------------|------|
| 实时错误流 (SSE) | 控制台实时面板 | 可选集成到本页下方实时区 | ⏳ 后期 |
| 错误追踪与溯源 | 单条错误详情 | 点击错误类型后的日志详情页 | ✓ 已规划 |
| 错误告警历史 | 告警中心（独立页） | 规则列表下方「触发历史」可折叠区 | ⏳ |
| 按账号/模型分类 | 数据看板 | 移到「数据看板」或此页下方筛选 | ✓ |
| 自定义错误代码 | 系统设置 | 系统管理页面 | ✓ |

---

## 九、API 总结表

| 方法 | 路由 | 用途 | 返回 |
|------|------|------|------|
| GET | `/api/stats/errors` | 错误统计概览 | `{summary, errors_by_type, errors_by_platform}` |
| GET | `/api/alerts/rules` | 告警规则列表 | `{items: [{id, name, condition, ...}], total}` |
| POST | `/api/alerts/rules` | 创建告警规则 | `{id, name, created_at, ...}` |
| PUT | `/api/alerts/rules/{id}` | 编辑告警规则 | `{id, updated_at, ...}` |
| DELETE | `/api/alerts/rules/{id}` | 删除告警规则 | `{message, id}` |
| POST | `/api/alerts/rules/{id}/test` | 测试告警规则 | `{triggered, notification_sent, ...}` |
| GET | `/api/errors/export` | 导出错误日志 | CSV / JSON 文件流 |

---

## 十、UI 对标与旧版截图参考

**参考文件**: `assets/screenshots/errors.png` (若有)

**新版改进**:
- ✓ 视图切换（柱图 ↔ 表格）vs 旧版单一表格展示
- ✓ 平台分布可折叠（vs 旧版混在表格中）
- ✓ 规则测试功能（vs 旧版无测试）
- ✓ 着色规则统一（critical/warn/info vs 旧版零散）
- ✓ 规则创建 FormDrawer（vs 旧版可能是弹窗）
- ✓ 错误率数字格式化统一
