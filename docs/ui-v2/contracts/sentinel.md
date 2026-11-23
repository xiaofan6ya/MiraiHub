# 功能契约：风控哨兵页（Sentinel）

**页面标识**: P2（TABS 第二个）  
**入口函数**: `loadSentinel(force)` + `sentinelInitTraffic(force)`  
**路由**: 点击导航栏「风控哨兵」，或从「概览」页告警横幅跳转

---

## 一、页面整体结构

```
┌─ 平台上下文切换器（侧栏顶部）
│
├─ 上半屏：熔断状态横幅 + 统计卡片
│  ├─ 【横幅】批次级封控状态
│  │  ├─ 未触发：「✓ 未检测到批次级封控」（绿底）
│  │  └─ 已触发：「! 检测到批次级封控」（红底）+ 「立即暂停止损」按钮
│  │
│  ├─ 【统计卡片组】6 个 KPI
│  │  ├─ 信号总数 / 涉及账号 / 原因种类 / 存储后端 / 最近信号 / 暂停中的平台
│  │
│  └─ 【受影响账号表】表格
│     └─ 平台、账号、最近信号、类型、次数、首次、最近（按最近信号排序）
│
├─ 中间屏：拓扑动画 + 右侧实时面板
│  ├─ 【左侧拓扑图】三层网络可视化
│  │  ├─ 客户端请求（左） → 粒子流 → 风控哨兵（中） → 粒子流 → 各平台（右）
│  │  ├─ 粒子流着色：绿（成功）/ 黄（告警）/ 红（失败/超时）
│  │  ├─ 节点脉冲动画：非 2xx 时红色脉冲
│  │  └─ 更新频率：4 秒轮询一次
│  │
│  └─ 【右侧数据区】
│     ├─ KPI 四格（请求数、成功率、失败/超时、存储后端）
│     ├─ 平台健康条（堆叠条：绿=成功、红=失败，按请求数排序）
│     └─ 最近请求实时流（时间 + 模型 + 平台 + 状态代码，自动刷新）
│
├─ 下半屏：信号明细表
│  └─ 时间、平台、账号、类型、码、模型、细节（最近 500 条，Redis 保留 15 分钟自动过期）
│
└─ 页面状态指示
   ├─ 「实时监听中（今日 X 个请求）」/ 「等待第一个请求…」
   └─ 刚收到 N 个新请求（实时更新文案）
```

---

## 二、数据来源与接口

### 2.1 主接口

#### 获取熔断状态与账号明细

```http
GET /api/sentinel/status?window=300
```

**参数**:
- `window`: 时间窗口（秒），通常为 300（5 分钟）

**返回字段**:

```json
{
  "tripped": false,                       // 是否已触发批次级封控
  "breach": false,                        // 旧版兼容字段（同 tripped）
  "threshold": 3,                         // 熔断阈值（N 个账号）
  "window_seconds": 300,                  // 判定窗口（秒）
  "suggested_pause_seconds": 600,         // 建议暂停时长（秒）
  
  "total_events": 42,                     // 窗口内信号总数
  "accounts_affected": 5,                 // 被风控的账号数
  "accounts": [                           // 受影响账号详情（按最近信号排序）
    {
      "account_id": 152,
      "platform": "workbuddy",
      "code": "11140",                    // 业务码
      "kind": "account_fault",            // 信号分类
      "label": "账号级授权风控",           // 人类可读原因
      "severity": "risk",                 // 四档：risk/throttle/auth/info
      "signal": "risk_control",           // 信号名
      "count": 3,                         // 窗口内出现次数
      "first_ts": 1700000000,             // 首次时间戳（秒）
      "last_ts": 1700000120               // 最近时间戳（秒）
    }
  ],
  
  "recent": [                             // 最近 500 条信号明细
    {
      "ts": 1700000120,                   // 时间戳（秒）
      "platform": "workbuddy",
      "account_id": 152,
      "code": "11140",
      "kind": "account_fault",
      "label": "账号级授权风控",
      "severity": "risk",
      "signal": "risk_control",
      "model": "gpt-4",
      "detail": "上游原始错误信息（可能为空）",
      "unmapped": false                   // 该码是否未在 PLATFORM_SIGNALS 收录
    }
  ],
  
  "by_code": {                            // 按原始码聚合的信号分类
    "11140": { "count": 10, "accounts": 3 },
    "11128": { "count": 8, "accounts": 2 }
  },
  
  "pause": {                              // 当前暂停中的平台与时长
    "workbuddy": 500,                     // 暂停剩余秒数（0 = 已过期，应清除）
    "all": 300                            // "all" = 全平台暂停
  },
  
  "redis": true,                          // 存储后端（true=Redis, false=内存）
  "signal_labels": {                      // 信号名 → 中文映射（前端缓存用）
    "risk_control": "账号/设备风控",
    "account_fault": "账号授权异常",
    "waf": "出口 IP 被拦",
    "quota_exceeded": "额度耗尽"
    // ...
  }
}
```

#### 获取实时流量数据

```http
GET /api/sentinel/traffic?window=3600
```

**参数**:
- `window`: 统计窗口（秒），通常为 3600（1 小时）

**返回字段**:

```json
{
  "totals": {
    "requests": 1200,                     // 窗口内总请求数
    "ok": 1150,                           // 成功请求数（HTTP 2xx/3xx）
    "error": 30,                          // 业务错误数（HTTP 4xx/5xx）
    "timeout": 20                         // 超时数
  },
  
  "platforms": [                          // 各平台流量统计（按请求数降序）
    {
      "platform": "workbuddy",
      "label": "WorkBuddy（国内版）",      // 展示名
      "region": "国内",
      "disabled": false,
      "accounts": 8,
      "requests": 800,
      "ok": 750,
      "error": 30,
      "timeout": 20,
      "last_at": 1700000900,              // 最后一次请求时间戳（秒）
      "last_status": 200                  // 最后一次状态码
    }
  ],
  
  "recent": [                             // 最近请求实时流（最近 40 条）
    {
      "ts": 1700000900,                   // 时间戳（秒）
      "platform": "workbuddy",
      "account_id": 152,
      "model": "gpt-4",
      "path": "/v1/chat/completions",
      "status": 200,                      // HTTP 状态码
      "outcome": "completed",             // 请求阶段：received/waiting/upstream/streaming/done/running/timeout
      "error": "",                        // 错误信息（如有）
      "elapsed_ms": 3200,                 // 耗时（毫秒）
      "key_label": "sk-xxx...（后 8 位）", // Key 标签
      "client_ip": "192.168.1.1"          // 客户端 IP
    }
  ],
  
  "storage": "redis"                      // 存储后端
}
```

### 2.2 控制接口

#### 暂停平台止损

```http
POST /api/sentinel/pause
```

**请求体**:

```json
{
  "platform": "workbuddy",                // 平台 id（空字符串 = 全平台）
  "seconds": 600,                         // 暂停时长（秒）
  "reason": "手动止损"                     // 操作原因（日志用）
}
```

**返回**:

```json
{
  "paused_until": 1700000900,             // 预期恢复的时间戳（秒）
  "reason": "手动止损"
}
```

#### 恢复平台

```http
POST /api/sentinel/resume?platform=workbuddy
```

**返回**:

```json
{
  "resumed": true,
  "platform": "workbuddy"
}
```

---

## 三、数据展示规范

### 3.1 数字格式化

#### 缩写格式（`fmtNum()` / `fmtK()`）
- ≥ 100 万（1e6）：显示为 M（百万），2 位小数，如 `1.23M`
- ≥ 1 万（1e4）：显示为 K（千），1 位小数，如 `12.3K`
- < 1 万：显示原数

#### 时间显示（`sentinelClock()`）
- 时间戳 → `HH:mm:ss` 格式（仅显示小时、分、秒）
- 用于最近请求表的「时间」列

#### 时间差（`sentinelLastSignalText()`）
- < 60 秒：「刚刚」
- < 3600 秒：「N 分钟前」
- 其他：「N 小时前」

#### 百分比（成功率）
- 格式：`XX.X%`，1 位小数
- 着色规则：≥98% 绿、≥90% 黄、<90% 红

### 3.2 着色规则

#### 请求状态着色（`sentinelToneOf()`）
- **绿色（`ok`）**：HTTP 2xx/3xx 且无业务错误
- **黄色（`warn`）**：HTTP 4xx
- **红色（`danger`）**：HTTP 5xx、超时、连接错误、业务错误代码

#### 信号严重程度着色
- **蓝色（risk）**：「风控」—— 真正的封控（WAF / 渠道 / 账号级）
- **黄色（throttle）**：「限流」—— 限速、限流、排队、额度耗尽
- **灰色（auth）**：「登录」—— 登录态失效、需重新登录
- **浅灰（info）**：「记录」—— 其他留痕

#### 拓扑粒子流着色（`sentinelToneColor()`）
- 绿色：`#3E6A99`（成功）
- 黄色：`#f59e0b`（告警）
- 红色：`#ef4444`（失败）

### 3.3 表格列

#### 受影响账号表
| 列 | 格式 | 说明 |
|---|------|------|
| 平台 | 彩色标签 | 平台 id + 人类名 |
| 账号 | `#152` | 账号 id（带 # 前缀） |
| 最近信号 | `11140 账号级授权风控` | 业务码 + 标签 |
| 类型 | 彩色 chip | 四档严重程度，带 tooltip |
| 次数 | 数字 | 窗口内出现次数 |
| 首次 / 最近 | `HH:mm:ss` | 时间戳转本地时间 |

#### 最近请求表（实时流）
| 列 | 格式 | 说明 |
|---|------|------|
| 时间 | `HH:mm:ss` | 仅时分秒 |
| 模型 | 模型名 / 路径 | 若无模型显示路径 |
| 平台 | 展示名 | 人类可读平台名 |
| 状态 | 状态码或 outcome | 200 / 进行中 / 超时 |

#### 信号明细表
| 列 | 格式 | 说明 |
|---|------|------|
| 时间 | `YYYY-MM-DD HH:mm:ss` | 完整时间戳 |
| 平台 / 账号 / 类型 / 码 | 同上 | - |
| 模型 | 模型名 | 若无显示 `—` |
| 细节 | 截断 90 字符 | 鼠标 hover 显示完整 |

---

## 四、交互与行为

### 4.1 页面加载

**首次进入**:
1. 显示加载状态
2. 并行调用：
   - `GET /api/sentinel/status?window=300` → 渲染横幅、账号表、信号明细表
   - 若页面可见且在拓扑区域：`sentinelInitTraffic()` → 初始化动画
3. 拓扑区初始化完成后启动轮询（4 秒一次）

**切换平台**（通过平台上下文事件 `wb-platform-change`）:
1. 重新调用 `GET /api/sentinel/status?window=300`
2. 重新渲染所有卡片、表格
3. 拓扑动画重新初始化（若当前在拓扑区）

### 4.2 轮询机制

#### 熔断状态轮询（`loadSentinel()`）
- **触发**：页面加载、平台切换、暂停/恢复操作完成
- **频率**：手动触发（非自动轮询）
- **停止条件**：用户离开页面

#### 实时流量轮询（`sentinelPollLogs()`）
- **频率**：4 秒一次（原来 2 秒，服务端已缓存 2 秒，前端再快无意义）
- **条件**：
  - 页面可见（`!document.hidden`）
  - 当前活跃标签为「风控哨兵」（`document.documentElement.dataset.activeTab === "gateway"` ？ **待确认**）
  - 拓扑已初始化（`_wbtSentinelTopology` 存在）
- **停止**：调用 `sentinelStopTraffic()` 或离开页面

#### 粒子流动画
- **请求到达**：每个新请求
  1. 粒子从「客户端请求」→ 「风控哨兵」（420ms）
  2. 随后从「风控哨兵」→ 对应平台（520ms）
  3. 目标平台节点出现脉冲（非成功时为红色脉冲）
- **着色**：根据 `sentinelToneOf(item)` 判定（绿 / 黄 / 红）
- **延迟**：批量请求逐个错开 200ms 播放，避免粒子堆叠

### 4.3 操作

#### 暂停止损
1. 显示确认对话：「暂停 {platform} 的上游请求 10 分钟？暂停期间请求会直接返回 503…」
2. 用户确认 → `POST /api/sentinel/pause`
3. 成功 → Toast 「已暂停，可随时恢复」
4. 刷新 `loadSentinel()` 更新按钮状态

#### 恢复平台
1. 点击 「恢复 {platform}」 按钮
2. `POST /api/sentinel/resume?platform={platform}`
3. 成功 → Toast 「已恢复」
4. 刷新 `loadSentinel()` 更新按钮状态

---

## 五、跨页联动

### 5.1 侧栏角标
- 读取 `/api/sentinel/status` 中的 `accounts_affected`
- 若 > 0，侧栏「风控哨兵」标签显示红点 + 数字
- Tooltip：「N 个账号近期被上游风控」

### 5.2 概览页告警横幅
- 概览页在 `loadOverview()` 时并行调用 `GET /api/sentinel/status?window=300`
- 若 `tripped: true` 或 `accounts_affected > 0`，显示告警横幅
- 点击「浏览详情 →」跳转到本页（P2）
- 点击「查看 →」跳转到本页（若有暂停平台）

### 5.3 账号管理页反向链接
- 账号被风控时，可点击链接进入本页并高亮该账号的行

---

## 六、边界情况与容错

### 6.1 数据缺失或异常

| 情况 | 处理 |
|------|------|
| 熔断状态接口超时 | 显示「哨兵加载失败：…」错误提示（红色文本） |
| 无账号被风控 | 受影响账号表显示「最近没有账号被上游拦。」|
| 无最近请求 | 最近请求表显示「暂无明细。」 |
| 无实时流量 | 右侧面板显示「今天还没有请求。」/ 「等待第一个请求…」 |
| 存储后端为空 | 显示 `—`（em-dash） |
| 未映射的业务码 | 码字体显示 `is-unmapped` 样式，tooltip：「未收录的码…」|
| 账号 id 为空 | 显示 `—` |
| 时间戳为 0 | 显示 `—` |

### 6.2 加载态与错误

**加载中**:
- 拓扑区显示骨架屏
- 表格显示「加载中…」占位符
- 右侧面板数据淡出

**请求失败**:
- 顶部显示 Toast（`toast("数据加载失败，请重试", "err")`）
- 保留上次成功的数据（若有）
- 轮询继续，不中断

**响应超时**（> 5 秒）:
- 显示「哨兵加载失败」文本
- 后台继续轮询重试
- 收到响应后自动更新

### 6.3 空状态

| 场景 | 显示 |
|------|------|
| 首次进页面 | 拓扑等待初始化，表格显示「等待加载…」 |
| 实时流量无请求 | 「等待第一个请求…」→ 收到请求后变为「实时监听中」 |
| 平台被禁用 | 拓扑节点显示「已禁用」标签，该平台不参与流量统计 |
| 无可用平台 | 显示「没有可用平台」（同 overview 页） |

### 6.4 僵尸数据清理

**进行中请求永不消失的原因与修复**:
- 旧版：running 和 recent 都存在同一个 Redis 有序集合，只能按 score/rank 删除
- 新版：running 单独存在 hash，结束时 `hdel` 精确删除，不会残留
- 前端：每 4 秒轮询拉新数据，自动刷新过期的「进行中」标记

**缓冲满溢**:
- 最近请求保留 300 条
- 若超过限制，自动截断最旧的 100 条保留最新 200 条
- 防止内存无限增长

**去重**:
- 前端维护 `_wbtSentinelSeen` Set（请求的去重键：ts + platform + path）
- 防止同一请求被多次触发粒子动画

---

## 七、旧版有但本契约未涉及的功能

| 功能 | 旧版位置 | 新版建议位置 | 状态 |
|------|---------|------------|------|
| 熔断参数调整 | 风控哨兵页下方 | 配置页（P25） | ⏳ |
| 信号级别统计图表 | 侧栏折叠区 | 数据看板（P11） | ⏳ |
| 按天/周对比 | 风控哨兵页 | 分析页（后期优化） | ⏳ |
| 告警订阅（邮件/钉钉） | 通知中心 | 通知中心（P20） | ✓ |

---

## 八、实现要点

### 8.1 拓扑动画库集成

- 使用 `window.WBTraffic.Topology` 类（已定义在全局）
- 节点着色、粒子流、脉冲效果均由库提供
- 前端只需：构造 nodes/links 数据 → 新建实例 → 轮询调用 `fire()` / `pulse()`

### 8.2 轮询与页面可见性

```javascript
// 伪代码
if (document.hidden) return;              // 切到其他窗口停止
if (_activeTab !== "sentinel") return;    // 切到其他标签停止
await sentinelPollLogs();                 // 执行轮询
```

### 8.3 缓存策略

- 状态数据（横幅、账号表）：每次手动加载（无缓存）
- 流量数据（KPI、健康条、请求流）：4 秒轮询一次

### 8.4 无障碍

- 拓扑图加 `aria-hidden="true"`（纯装饰）
- 表格用语义化 `<thead>` / `<tbody>`
- 颜色着色需有文字/图标补充（不单独依靠颜色）
- 状态码、时间戳提供 `title` / `aria-label` 完整文字

### 8.5 性能优化

- **粒子延迟播放**：批量请求错开 200ms，避免堆叠过多粒子
- **去重 Set**：每个请求的 `ts|platform|path` 组合，防止重复触发
- **缓冲裁剪**：`_wbtSentinelSeen` 超 800 条时自动保留最新 300 条
- **条件轮询**：检查 `document.hidden` 和 `_activeTab` 再执行 API 调用

### 8.6 国际化

所需 i18n key：
- 页面标题：「风控哨兵」
- 横幅文案：「检测到批次级封控」/ 「未检测到批次级封控」
- 按钮：「立即暂停止损」/ 「恢复」
- 表头：「平台」「账号」「最近信号」「类型」「次数」「首次」「最近」
- 提示：「暂停期间请求会直接返回 503…」/ 「已暂停，可随时恢复」

### 8.7 主题适应

- 拓扑背景色：CSS Custom Property `--bg-secondary`
- 粒子流颜色：已在代码中硬编码（绿 `#3E6A99` / 黄 `#f59e0b` / 红 `#ef4444`），**后期可改为 CSS 变量**
- 表格行悬停：`hover:bg-slate-100 dark:hover:bg-slate-800`
- 着色 chip：用 CSS class `.tone-ok` / `.tone-warn` / `.tone-danger`，值在 tokens.css 中定义


---

## 实现对照（S18 /sentinel 新版实现，2026-11-23）

新版实现：`admin/ui/src/pages/SentinelPage.vue` + `admin/ui/src/api/sentinel.ts` + `admin/ui/tests/page-sentinel.spec.ts`。
结构按 P2（模板 A）：PageHeader（存储 / 暂停平台… / 刷新）+ 结论横幅 + 6 格 StatStrip + 左右分栏（实时流量 + 平台健康 ｜ 实时请求）+ 封控信号表格 + 暂停平台弹窗。
视图状态（tab/window/platform/page/size/sort/order）通过 `useTableQuery('sentinel')` 同步到路由 query。

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 结论横幅（是否触发 + 阈值） | 页面首屏横幅（ok-soft / danger-soft） | 已实现：文案沿用旧版「检测到批次级封控：最近 N 分钟内有 X 个账号被风控 / 未检测到批次级封控」，副行给阈值与建议暂停秒数 |
| 检测到时「暂停该平台」主按钮 | 横幅右侧 danger 按钮 | 已实现：取 `by_platform` 里 risk 最多的平台 + `suggested_pause_seconds`，点击进暂停确认 |
| 暂停中的平台「恢复 X」按钮 | 横幅右侧（每个暂停键一个） | 已实现：`pause` 里的键逐个渲染（`all` → 全部平台），过期项不显示 |
| 6 格 StatStrip | `StatStrip` | 已实现（按 P2 ASCII）：今日请求 / 成功率（着色 ≥98 ok、≥90 warn、其余 danger）/ 失败·超时 / 信号（脚注最近信号相对时间）/ 涉及账号（脚注原因种类）/ 暂停中的平台（脚注平台名） |
| 「存储后端 Redis」不再占统计位 | 页头「存储：Redis / 内存」小字 | 已实现（旧版第 4 格的位置让给「信号」，符合 P2） |
| 实时流量 RouteRibbon（按成功率着色） | 「实时流量」卡 | 已实现：`traffic.platforms` → lanes，线宽=请求占比、颜色用组件内 healthOf（≥90/60/其余，与设计稿一致），带成功率数字与 `HTTP last_status`；空数据走组件自带「暂无流量」 |
| 点击线路带平台 → 过滤信号表 | RouteRibbon `@select` → `?platform=` | 已实现（新版补充）：再点一次取消，筛选 chip 可移除 |
| 实时请求列表（2 秒轮询） | 右栏「实时请求」卡 | 已实现：`/api/sentinel/traffic?window=3600` 每 2 秒（任务书要求；旧版 4 秒），最多 50 行，新的在上 |
| 状态码着色（2xx ok / 429 warn / ≥400 danger） | 列表行 | 已实现：`requestTone` 按旧版 sentinelToneOf 口径（状态码优先；200+流式 interrupted 也算成功；running 用 brand 显示「进行中」；无状态码才看 timeout/error） |
| 新行插入时顶部淡入 150ms | CSS `sentinel-row-in` | 已实现（keyed 列表只在新增行触发；`prefers-reduced-motion` 由 tokens.css 全局关掉动画） |
| 页面不可见暂停轮询 | `usePolling`（status 10s / traffic 2s） | 已实现：05-patterns §3 的 visibility 暂停 |
| 连续失败 3 次暂停 + 「恢复」 | 各卡底部 warn 文案 + 恢复按钮 | 已实现：`usePolling` 的 pausedMessage + resume；横幅/表格仍显示旧数据 |
| 平台健康 StackMeter 两段 | 「平台健康」卡 | 已实现：每平台一行 = 名称 + 成功率（着色）+ 次数·失败 + 两段 StackMeter（成功 ok / 失败·超时 danger，total=今日请求） |
| 平台健康行「⋯」止损 | 行内 `RowActions` | 已实现：暂停该平台（danger）/ 恢复该平台（已暂停时），暂停走确认弹窗 |
| 封控信号 DataTable（时间 · 平台 · 账号 · 信号码 · 原因 · 操作） | 表格卡 | 已实现：时间（相对时间）、平台展示名、#账号、信号码（未收录显示「· 未归类」并 tooltip 说明）、原因（归一信号名 · 原始标签，tooltip 显示 detail）、模型列、操作（暂停/恢复该平台） |
| 受影响账号表（旧版） | 同一卡片的页签「受影响账号」 | 已实现：平台 / #账号 / 最近信号 / 类型（四档 Badge）/ 次数 / 首次 / 最近，按最近信号排序 |
| 四档严重程度展示 | 类型 Badge | 已实现：risk 风控（danger）/ throttle 限流（warn）/ auth 登录（neutral）/ info 记录（neutral），tooltip 给该档处置说明（仅 risk 参与熔断） |
| 信号窗口 | 表格卡「5 / 10 / 30 分钟」 | 已实现（新版补充）：写 `?window=`，重新请求 `/api/sentinel/status?window=`（后端默认 300s，范围 30–3600） |
| 暂停平台（止损）确认 | 「暂停平台…」弹窗 + 确认框 | 已实现：目标平台（全部/各平台）、时长（10 分钟 / 30 分钟 / 直到手动恢复）、原因；确认文案沿用旧版「暂停期间请求会直接返回 503，不消耗账号额度、也不会打到上游」；成功 Toast「已暂停：X」 |
| 恢复平台 | 横幅按钮 / 行操作 | 已实现；有差异：旧版恢复**无**确认框，新版按任务书要求补了确认「恢复 X 的上游请求？」 |
| 旧版拓扑粒子动画（traffic.js） | 由 RouteRibbon 取代 | 有差异（设计稿指定）：P2 明确「实时流量（RouteRibbon，按成功率着色）」，不再保留 cabling 粒子动画 |
| 旧版「今日概况」4 格 KPI | 合并进 StatStrip | 有差异（设计稿指定）：P2 要求把旧版 6 格信号指标与 KPI 一起收进一个 StatStrip |
| 旧版「受影响账号 / 信号明细」两块表 | 合并为一个表格卡两个页签 | 有差异：页面高度更可控，数据与列完全保留 |
| `/api/sentinel/reset`（清空信号） | — | 未提供入口：后端有该接口（排障用），但旧版面板没有按钮，P2 也未要求；如需可从命令面板或排障脚本调用 |
| `/api/sentinel/pressure`（并发压力） | — | 不在 P2 范围（概览/哨兵以外的排障数据），未展示 |
| `alert` 字段（熔断时后端推的预警） | — | 未单独展示：后端在 status 里返回最近一次通知结果，页面只用 `tripped` 判定；通知去重与投递在服务端完成 |
| 平台上下文 | — | 不适用：`/api/sentinel/*` 不读 `X-Platform`（`platform` 是显式的查询参数）；页面的「平台筛选」只筛本页表格，不切全局上下文，因此不订阅 `usePlatformStore().onChange` |

### 无法验证

- 真实后端联调（本环境无 8790 服务）：status/traffic/pause/resume 的行为只用单测 + 类型检查验证。
- 需要真实流量的部分：RouteRibbon 线宽/脉冲、平台健康条比例、实时请求 2 秒刷新与淡入、暂停后网关 503（`Retry-After`）。
- Redis 不可用时的降级（`storage=memory`、信号只存进程内存）与多 worker 下 running 记录的合并。
- 批次熔断的真实触发（需要 3 个账号在窗口内被上游真风控）与后端预警投递（邮件/机器人）。
