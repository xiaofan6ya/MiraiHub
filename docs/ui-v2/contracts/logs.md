# 功能契约：使用日志页（P7）

**页面标识**: P7（TABS 第七个）  
**入口函数**: `loadLogs(opts={filter, page, sort})`  
**路由**: `/logs`

---

## 一、页面整体结构

```
使用日志                              [导出 ▾] [刷新]
┌─ 搜索与过滤栏 ────────────────────────────────────────────────────────────┐
│ Key [______]  账号 [▾ 选择/搜索]  模型 [______]  时间范围 [开始] - [结束]    │
│ 平台 [▾]  状态 [▾ 全部/成功/失败]  [清空所有筛选]                           │
└────────────────────────────────────────────────────────────────────────────┘
┌─ 新日志通知横幅（实时SSE推送）────────────────────────────────────┐
│ ⚡ 有 23 条新日志  [加载]                   [×]                   │
└────────────────────────────────────────────────────────────────────┘
┌── 日志表格（DataTable，可排序、可展开）──────────────────────────────────┐
│ 时间 ↑↓ · Key · 账号 · 模型 · Tokens · 耗时 · 状态 · 操作            │
├────────────────────────────────────────────────────────────────────────┤
│ 2026-11-23 10:45:12  workbuddy-v1  账号#123  gpt-4  12.3M/5.1K  1,234ms  ✓  [展开]
│ ► 详情：platform=workbuddy, ttfb=234ms, cached_tokens=0, ...        │
│                                                                        │
│ 2026-11-23 10:44:58  api-free      账户名    claude-opus  8.9M/2.3K   856ms  ✗  [展开]
│ ► 错误：error_kind=rate_limit, reason=账号超限, diagnostic=...       │
│ ...                                                                    │
├────────────────────────────────────────────────────────────────────────┤
│ 显示 1-20 / 共 15,284 条  ◀ [1] [2] [3] [▾] ▶  跳转第 [__] 页 ▶       │
└────────────────────────────────────────────────────────────────────────┘
```

**整体布局说明**:
- 顶部导出 + 刷新按钮（固定在页头）
- 多行筛选栏（FilterBar），可折叠/展开，记住用户上次设置
- 实时推送横幅（有新日志时）
- 核心表格：分页 + 可展开详情
- 底部分页器（支持跳转、前后翻页）

---

## 二、数据来源与接口

### 2.1 主接口

```http
GET /api/logs?page=1&page_size=20&sort=created_at:desc&key=...&account=...&model=...&start=...&end=...&result=...
```

**查询参数**:
- `page` / `page_size` - 传统分页（默认20条/页，最多500）
- `before_id` - keyset分页游标（深翻页时用，比OFFSET快）
- `after_id` - 增量轮询（新日志推送后获取）
- `sort` - 排序字段（支持 `created_at:desc`, `created_at:asc`, `latency_ms:desc` 等）
- `key` - 按Key名称模糊搜索
- `account` - 按账号名称/UID模糊搜索
- `model` - 按模型名精确或模糊匹配
- `client_ip` - 按客户端IP搜索
- `start` / `end` - 时间范围（ISO 8601）
- `result` - 过滤结果（`all` / `success` / `error`）
- `platform` - 平台过滤（查询参数，非请求头）
- `with_total` - 是否返回总数（默认true）

**返回字段（`items` 数组）**:

```json
{
  "items": [
    {
      "id": 12345,
      "created_at": "2026-11-23T10:45:12Z",
      "key_id": 42,
      "key_name": "workbuddy-v1",
      "account_id": 123,
      "account_name": "账号名 / 未命名账号#123",
      "platform": "workbuddy",
      "model": "gpt-4",           // 可读名（已转译）
      "model_raw": "gpt-4",       // 落库原值（历史行可能是槽位名）
      
      "prompt_tokens": 12300000,
      "completion_tokens": 5100,
      "total_tokens": 12305100,
      "cached_tokens": 0,         // 缓存命中的 tokens
      "credits": 2.456,           // 消耗积分
      
      "ttfb_ms": 234,             // Time To First Byte（首字节延迟）
      "latency_ms": 1234,         // 总耗时（毫秒）
      "status": "ok / rate_limit / timeout / ...",  // error_kind 或 "ok"
      "error_kind": "rate_limit / timeout / network_error / ...",  // null表示成功
      
      "client_ip": "192.168.1.1",
      "use_case": "",             // 请求用途标记（可选）
      "reasoning_effort": "medium",  // 思考强度（Claude适用）
      "seq": 1,                   // 请求序列号
      
      "diagnostic": null          // 详情页面才返回
    },
    // ...
  ],
  "total": 15284,                 // 总记录数（with_total=true时）
  "page": 1,
  "page_size": 20,
  "has_more": true,
  "next_before_id": 12300,        // keyset分页游标
  "stats": {                       // 统计摘要（首次加载）
    "total": 15284,
    "success": 15100,
    "error": 184
  }
}
```

### 2.2 副接口

**获取账号下拉列表**（筛选用）:
```http
GET /api/logs/accounts?days=30&compact=false
```
返回 `{ items: [{id, name, uid, status, balance_remain, requests, credits, last_used_at}, ...], range_days: 30 }`
- 仅列出指定天数内**真实产生过请求**的账号
- 附带请求数 / 积分 / 最近使用时间，让用户选前有个参考

**获取模型下拉列表**（可选，从其他页面同步）:
```http
GET /api/models?enabled=true
```
返回 `[{id, name, platform, ...}, ...]`

**获取统计摘要**（装饰用）:
```http
GET /api/logs/summary?days=1&start=...&end=...
```
返回 `{ range_days, total_requests, ok_requests, failed_requests, account_count, total_credits, total_tokens, items: [{account_id, requests, credits, ...}] }`

**导出日志为CSV**:
```http
GET /api/logs/export?key=...&account=...&model=...&start=...&end=...&limit=50000&diagnostics=false
```
- 返回 `text/csv; charset=utf-8` 流（BOM头保证Excel识别中文）
- 列：ID · Key · 账号 · 模型 · 积分 · 提示Tokens · 补全Tokens · 缓存Tokens · 总Tokens · TTFB · 耗时 · 结果 · IP · 用途 · 思考强度 · 时间

**实时推送新日志**（SSE）:
```http
GET /api/logs/usage/stream?ticket=...
```
- 帧协议：`event: usage` + `data: {"usage_revision": N}` —— 有新日志时推送
- 空闲心跳：`: keepalive`（15秒一次）
- 重连信号：`event: reauth`（连接到期时）
- 前端收到 `usage` 事件后重新拉取当前页（保持页码）

---

## 三、交互与行为

### 3.1 页面加载与刷新

**首次进入**:
1. 显示加载骨架屏
2. 调用 `GET /api/logs?page=1&page_size=20`（默认按时间倒序）
3. 同时调用 `GET /api/logs/accounts?days=30&compact=false` 填充账号下拉
4. 渲染表格 + 分页器
5. 建立 SSE 连接 `/api/logs/usage/stream`，监听新日志

**用户点击「刷新」按钮**:
1. 清空当前页数据
2. 重新调用 `/api/logs?page=1&...` （若SSE推送过程中有新日志，改用 `/api/logs?after_id=...` 增量拉取）
3. 更新表格和总数
4. 显示 Toast「已更新」

**用户修改筛选条件**:
1. 重置到 `page=1`
2. 收集筛选参数，调用 `/api/logs?page=1&key=...&account=...&model=...&start=...&end=...`
3. 更新表格
4. 筛选条件存 localStorage（下次打开恢复）

**用户翻页**:
- **浅翻页**（1-10页）：用 `page` / `page_size` 传统分页
- **深翻页**（11页+）：自动切换到 `before_id` keyset分页（避免 `OFFSET 100000` 的全表扫）
- 分页器显示「当前第 N 页，共 M 条记录」

### 3.2 表格交互

#### 列与排序
- **时间**（默认 ↓ 倒序）：点击切换升/降序
- **Key** / **账号** / **模型**：显示名称，点击可过滤
- **Tokens**：显示 `输入 / 输出 / 合计` 或简化为 `合计`（Tokens很大时用缩写：`2.3M`）
- **耗时**：毫秒单位，着色规则见下文
- **状态**：✓ 成功 / ✗ 失败，失败时显示错误代码/原因
- **操作**：`[展开]` 按钮

#### 展开详情
点击 `[展开]` 或表格行前的 `►` 展开该行详情面板，显示：
```
► 详情面板（该行下方插入）
  ┌─ Key#42 (workbuddy-v1) ─────────────────────────┐
  │ 账号: 账号#123 · 平台: workbuddy · 时间: 10:45  │
  │ 模型: gpt-4 · 耗时: 1234ms（TTFB 234ms）       │
  │ Token 分布:                                     │
  │   输入: 12.3M  输出: 5.1K  缓存: 0  合计: 12.3M │
  │ 消耗: 2.456 积分                                │
  │ 状态: ✓ 成功（无错误）                          │
  │ 诊断数据（JSON展开）:                           │
  │ { upstream_response: {...}, latency: ... }      │
  │ [复制诊断JSON] [下载完整记录(JSONL)]            │
  └─────────────────────────────────────────────────┘
```
- 调用 `GET /api/logs/{id}` 获取 `diagnostic` 字段
- 显示诊断JSON（可折叠）
- 提供 `[下载完整记录]` 按钮导出该条记录的完整信息（包括请求/响应体）

#### 着色规则
- **耗时**（latency_ms）:
  - 绿色（`ok`）：≤ 500ms
  - 黄色（`warn`）：500ms < x ≤ 2000ms
  - 红色（`danger`）：> 2000ms 或超时
- **状态**:
  - 绿色（✓）：error_kind 为空或 "success"
  - 红色（✗）：error_kind 为 "rate_limit" / "timeout" / "network_error" / ...
- **Tokens 着色**（若超过阈值）:
  - 灰色：没有token数据
  - 正常：< 100K
  - 黄色警告：100K ≤ x < 1M
  - 红色警告：≥ 1M

### 3.3 筛选栏

**默认隐藏**（用户可展开），包含：

#### 第一行
- **Key（文本输入）**：模糊搜索 Key 名称，支持拼音/部分匹配
- **账号（下拉+搜索）**：列举 `/api/logs/accounts` 返回的账号，支持搜索
- **模型（文本或下拉）**：模型名称，可从 `/api/models` 加载 or 自由输入
- **时间范围**：
  - 开始时间：日期选择器（默认「今天00:00」）
  - 结束时间：日期选择器（默认「现在」）
  - 或快捷按钮：「最近1小时」「今天」「7天」「30天」

#### 第二行
- **平台（下拉）**：从 `/api/platforms` 或 `/api/logs/accounts` 的 platform 字段获取
- **状态（单选）**：「全部」/ 「成功」/ 「失败」
- **客户端IP（文本输入）**：支持 IP 地址或 CIDR 段查询
- **[清空所有筛选]** 按钮

**筛选状态记忆**：
- 筛选条件（包括分页、排序）存 localStorage Key `miraihub_logs_filter`
- 下次打开页面自动恢复

### 3.4 导出

右上角 `[导出 ▾]` 下拉菜单：
- **导出 CSV** —— 调用 `GET /api/logs/export?...` 下载 `usage_logs.csv`
- **导出 JSON** —— 调用 `GET /api/logs/export?format=json&...` 下载 `usage_logs.json`（可选）
- **导出包含诊断** —— `...&diagnostics=true` 追加诊断 JSON 列（文件更大）

导出范围：**当前筛选条件 + 分页** ——
- 若用户在第 2 页，导出仅这一页的 20 条
- 若用户清空筛选看全部日志，导出可选「限制前 50000 条」确认（防止OOM）

---

## 四、实时推送与大屏幕同步

### 4.1 SSE 推送流程

**建立连接**:
1. 页面挂载时调用 `GET /api/logs/requests/ticket` 换一张2分钟有效的推送票据
2. 建立 `EventSource('/api/logs/usage/stream?ticket=...')` 长连接

**收到推送**:
```javascript
// 有新日志时
event: usage
data: {"usage_revision": 12345}

// 空闲心跳（15秒）
: keepalive

// 连接到期（4分钟后）
event: reauth
data: {}
```

前端收到 `usage` 事件时：
1. 若当前在第1页 → 调用 `GET /api/logs?page=1&with_total=false` 刷新表格
2. 若当前在第2+页 → 显示黄色横幅「有 N 条新日志」+「[加载]」按钮，点击后回到第1页
3. 若当前不在日志页 → 顶部导航栏的「日志」标签显示红点提示

**大屏幕展示**（若有实时面板）:
- 左上角显示「今日日志 N 条」/ 「今日成功率 M%」更新 1~2 秒一次
- 下方实时流量带展示最新 50 条请求（来自 `/api/logs/requests/stream`）

### 4.2 连接管理

- **开标签页数量限制**：一条账户最多 16 个并发连接（防止被代理掐）
- **连接自动重连**：4 分钟后主动关闭，前端收到 `reauth` 事件后重新换票
- **标签页关闭**：浏览器自动 `abort()` 连接，服务端立刻停采样
- **标签页切换离开**：`visibility: hidden` 时仍保持连接但降低采样频率（后续优化）

---

## 五、数据展示规范

### 5.1 数字格式化

#### Tokens 格式（`logTokens()`）
- ≥ 100 万（1e6）：显示为 M（百万），1 位小数，如 `12.3M`
- ≥ 1 万（1e4）：显示为 K（千），1 位小数，如 `5.1K`
- < 1 万：显示原数

#### 积分格式（`logCredit()`）
- 千分位 + 2 位小数，如 `1,234.56`

#### 时间格式（`fmtTime(s, format)`）
- 表格行：`HH:mm:ss` 或 `MM-DD HH:mm` (超过7天前)
- 展开详情：`YYYY-MM-DD HH:mm:ss`
- 时区：UTC+8（北京时间）

#### 耗时格式（`logLatency()`）
- < 1 秒：`XXXms`
- ≥ 1 秒：`X.XXs`

### 5.2 状态与错误显示

**status 列**（状态）:
| 值 | 显示 | 颜色 |
|---|---|---|
| (空/null) | ✓ | 绿 |
| success | ✓ | 绿 |
| rate_limit | ✗ 限流 | 红 |
| timeout | ✗ 超时 | 红 |
| network_error | ✗ 网络错误 | 红 |
| auth_error | ✗ 鉴权失败 | 红 |
| model_not_found | ✗ 模型不存在 | 黄 |
| other | ✗ 其他错误 | 红 |

**展开详情时**，显示完整错误文案：
```
错误: rate_limit
账号超出速率限制（Tokens/min），请等待 2 分钟后重试
诊断: upstream_error=429 Too Many Requests
```

---

## 六、边界情况与容错

### 6.1 加载态与错误

**加载中**:
- 表格行显示 5~8 个灰色占位符（骨架屏）
- 分页器禁用

**请求失败**:
- 显示 Toast：「日志加载失败，请检查网络」
- 保留上次成功的数据（若有）
- 表格底部显示「重试」按钮

**SSE 连接断开**:
- 自动重连（指数退避：1s / 2s / 5s / 10s）
- 若一直无法连接，6 次后放弃，改为手动轮询（点刷新）

### 6.2 空状态

| 场景 | 显示 |
|------|------|
| 本周无日志 | 表格显示「无数据」+ 插图 + 「开始您的第一个请求」文案 |
| 筛选无结果 | 表格显示「未找到匹配的日志」+ 「清空筛选」按钮 |
| 账号列表空 | 下拉显示「暂无账号产生过请求」（需先配置账号） |
| 时间范围异常 | 验证错误提示：「结束时间需晚于开始时间」 |

### 6.3 数据缺失

| 字段 | 缺失时显示 |
|------|-----------|
| account_name | `—` 或 `未记录账号 / 未选号` |
| platform | `—`（可能是孤儿日志） |
| model | `—` |
| Tokens | `—` 或 `0` |
| client_ip | `—` |
| diagnostic | 详情页无诊断信息时显示「暂无诊断数据」 |

### 6.4 权限与可见性

- **平台隔离**：用户只能看本平台的日志（由 `X-Platform` 头自动过滤）
- **导出权限**：同权限模型，导出 50000 条限制可配置
- **诊断数据**：默认不返回（占用传输/存储），仅详情页按需加载
- **个人账号日志**：普通用户可看自己的 Key / 账号日志；管理员可看全部

---

## 七、跨页联动

### 7.1 导航跳转

- 点击日志行中的「Key 名称」→ 跳 `/api-keys` 并过滤该 Key
- 点击日志行中的「账号名」→ 跳 `/accounts` 并过滤该账号
- 点击日志行中的「模型名」→ 跳 `/models` 并定位该模型
- 点击日志行中的「平台名」→ 该页面自动过滤该平台（无需跳转）

### 7.2 全局事件

- **账号状态变化**：日志表格自动刷新（SSE 推送反应）
- **平台被禁用**：若当前平台被禁用，页面自动切换到其他可用平台

### 7.3 实时面板联动

- **概览页** (`/overview`)：「实时请求」区域来自 `/api/logs/requests/stream`，与本页使用同一条 SSE 连接但不同的数据源
- **数据看板** (`/stats`)：「今日请求数」等指标定时拉取 `/api/logs/summary`，可选订阅本页推送的 `usage_revision` 更新

---

## 八、实现要点

### 8.1 性能优化

1. **表格虚拟化**：显示 100+ 条记录时用虚拟滚动（`react-window` 等）
2. **缓存策略**：
   - 账号下拉列表缓存 5 分钟（后端已缓存）
   - 分页数据本地缓存当前 + 相邻 2 页（快速翻页）
3. **keyset 分页**：深翻页自动切换 `before_id` 避免 O(offset) 扫描
4. **SSE 单飞**：多标签页共用同一条推送连接（后端限流）
5. **懒加载详情**：展开详情时才调用 `/api/logs/{id}`，不预加载

### 8.2 无障碍与国际化

- 表格行用 `<tr aria-label="...">` 标记
- 状态图标配文字标签：`<span aria-label="成功">✓</span>`
- 所有文案提取到 i18n 配置（「日志」「成功」「失败」等）
- 颜色不单独依赖：≥ 90% 的信息用文字/图标表达，仅配色补充

### 8.3 主题与样式

- 所有颜色用 CSS Custom Properties（`--c-ok`, `--c-warn`, `--c-danger` 等）
- 支持亮/暗主题无缝切换
- 表格行 hover 时高亮背景
- 点击展开时用 150ms 淡入动画

### 8.4 测试

- 单元测试：格式化函数（tokens / credits / time）
- 集成测试：筛选 + 分页 + 排序的组合
- E2E 测试：导出流程、SSE 推送、详情展开
- 性能测试：1000+ 条记录渲染时间 < 2s

---

## 九、旧版有但本契约未直接涉及的功能

| 功能 | 旧版位置 | 新版建议位置 | 状态 |
|------|---------|------------|------|
| 批量操作（导出、删除标记） | 日志页左侧checkbox | 表格行级操作菜单 | ⏳ 后期 |
| 实时日志流式显示 | 页面底部「最新请求」 | 整合进 `/api/logs/requests/stream` | ✓ 已规划 |
| 按 IP 分布统计 | 统计面板 | 移到「数据看板」 | ✓ |
| 诊断数据详情 | 弹窗 | 展开详情面板（本契约涵盖） | ✓ |
| 下载原始请求/响应体 | 单条日志右键菜单 | `[下载完整记录]` 按钮 | ✓ |
| 告警触发器 | 预警页 | 保留（P20 预警管理） | ✓ 已规划 |

---

## 十、API 总结表

| 方法 | 路由 | 用途 | 返回 |
|------|------|------|------|
| GET | `/api/logs` | 分页日志列表 | `{items, total, stats, ...}` |
| GET | `/api/logs/{id}` | 日志详情 + 诊断 | `{...log, diagnostic, request_capture}` |
| GET | `/api/logs/{id}/capture/body` | 请求/响应体分页 | `{body_part, offset, ...}` |
| GET | `/api/logs/{id}/capture/download` | 下载完整记录 | JSONL 流 |
| GET | `/api/logs/export` | 导出 CSV | CSV 文件流 |
| GET | `/api/logs/accounts` | 账号下拉 | `{items: [{id, name, ...}]}` |
| GET | `/api/logs/summary` | 统计摘要 | `{total_requests, ok_requests, ...}` |
| GET | `/api/logs/requests` | 请求动态（轮询） | `{active, recent, usage_revision}` |
| GET | `/api/logs/requests/ticket` | 推送票据 | `{ticket, expires_in}` |
| GET | `/api/logs/requests/stream` | 请求流（SSE） | 事件流 |
| GET | `/api/logs/usage/stream` | 日志流（SSE） | 事件流 |

---

## 十一、UI 对标与旧版截图

旧版参考：`assets/screenshots/logs.png`

**新版改进**:
- ✓ 多行可折叠筛选栏（vs 旧版单行）
- ✓ 实时SSE推送（vs 旧版5秒轮询）
- ✓ 展开详情面板（vs 旧版弹窗）
- ✓ Keyset分页（vs 旧版OFFSET分页）
- ✓ 状态着色规则统一（vs 旧版零散）
- ✓ 诊断数据嵌入详情（vs 旧版另开弹窗）


---

## 实现对照（S15，`admin/ui/src/pages/LogsPage.vue`）

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 请求详情记录：开关（PUT capture/settings） | `LogsPage.vue` 顶部卡片 + `api/logs.ts:setCaptureEnabled` | 已实现 |
| 状态行：已开启/关闭 · 缓存 N/200 条 · MiB · 单条 8 MiB · 60 分钟过期 · 重启清空 | `captureStatusText()`（`GET /api/logs/capture/settings` 的真实字段） | 已实现 |
| 刷新状态 / 清空详情缓存（确认） | `loadCapture()` / `clearCapture()`（`DELETE /api/logs/capture/cache`） | 已实现 |
| 说明 HelpTip（凭据脱敏、不写库、重启/过期/淘汰丢失） | 卡片 `#extra` 的 `HelpTip` | 已实现 |
| FilterBar：平台（/api/platforms，按落库平台去重） | 平台 `NSelect` | 已实现 |
| FilterBar：账号（`/api/logs/accounts?compact=true`，窗口内有记录的账号，选项带次数/积分） | 账号 `NSelect` | 已实现 |
| FilterBar：时间范围 今日/近 1 小时/近 1/3/7/30 天/不限 + 自定义起止日期 | `resolveTimeWindow()` + 两个 `NDatePicker` | 已实现 |
| FilterBar：模型 / Key / 调用结果 / 客户端 IP | `NInput` / `NSelect` | 已实现 |
| 快速翻页（不统计总数） | `fast` 过滤项 → `buildLogQuery` 不传 `with_total` | 已实现 |
| 重置 / 已筛选 chips（可单个移除） | `FilterBar` `chips` / `@remove` / `resetFilters` | 已实现 |
| 已筛选统计「当前筛选共 N 次 · 成功 X · 错误 Y」 | `summaryStatText()`（`/api/logs/summary` 的 total/ok/failed，跟筛选走、与分页无关） | 已实现 |
| 按账号汇总：可折叠（默认展开） | 卡片 `#extra` 按钮 + `summaryOpen` | 已实现 |
| 汇总列：账号 · 请求数 · 失败 · 积分 · Token · 命中率 · 平均耗时 · 最近使用 · 积分占比 Meter | `summaryColumns` + `toAccountSummaryRows()` | 已实现 |
| 汇总行点击 = 按该账号筛选 | `applyAccountFilter` → `account_id` 过滤项 | 已实现 |
| 未记录账号 / 未选号固定置底、文字弱化 | `toAccountSummaryRows()` 排序 + `unattributed` | 已实现 |
| 请求动态：近 15 分钟每分钟请求/错误小柱图 + 最近 5 条明细 | `buildLiveBars()` + `liveRecent`（`/api/logs/requests`） | 已实现（差异：旧版用哨兵 SSE 数据源，这里改用 `/api/logs/requests` 5 秒轮询，避免日志页再占一条 SSE；暂停/恢复/文档隐藏暂停/连续失败 3 次自动暂停都在） |
| 请求动态：暂停 / 恢复 | `usePolling` + `toggleLive()` | 已实现 |
| 文档隐藏时暂停 | `usePolling({ pauseWhenHidden: true })` | 已实现 |
| 连续失败 3 次停止并显示原因 + 恢复按钮 | `usePolling(maxFailures: 3)` + `pausedMessage` + ErrorBlock | 已实现 |
| 表格新行推送（SSE `/api/logs/usage/stream` + 票据 + reauth 重连） | `openUsageStream()` / `requestStreamTicket()` | 已实现 |
| SSE 不可用 / 连续 3 次断开 → 8 秒轮询兜底 | `usageFallback`（`GET /api/logs/requests?include_history=false` 比对 usage_revision） | 已实现 |
| 日志 DataTable 列：时间 · Key · 模型（含思考强度）· 平台/账号 · 端点 · 结果 Badge · Token 入/出/缓存 · 命中率 · 积分（warn）· 耗时 TTFT/总 + TPS · IP · 详情 | `columns` + `toLogRow()` | 已实现 |
| keyset 分页（before_id + cursors），第 1 页才统计总数 | `loadList()` + `buildLogQuery()` + 自定义分页条 | 已实现（深链接 page>1 无游标时退回 `page/page_size` 的 OFFSET） |
| 虚拟滚动 | DataTable `:virtual-scroll="true"` | 已实现 |
| 行点击 → DetailDrawer | `openDetail()` + `DetailDrawer` | 已实现 |
| 详情概要 KV（时间/平台/账号/Key/模型/端点/结果/Token/命中/积分/耗时/TPS/IP/seq/思考强度） | `detailKv` + `KV` | 已实现 |
| 详情错误与诊断（error_kind + diagnostic JSON） | 「错误与诊断」`Section` + `CodeBlock` | 已实现 |
| 详情请求正文 / 上游正文：按 64 KiB 分页读取、Headers + body CodeBlock | `loadIngress()` / `loadUpstream()` + `getCaptureBody()` | 已实现 |
| 复制 curl（method/url/未脱敏请求头/正文；跳过 [REDACTED]） | `buildCurl()` + `copyCurl()` | 已实现 |
| 下载完整 JSONL（NDJSON） | `downloadLogCapture()`（`download()`） | 已实现 |
| 导出 / 导出诊断（CSV，诊断最多最近 1000 条） | `exportLogs()` + `downloadLogsExport()` | 已实现 |
| 从其他页带 `model / key / result / account / platform / start / end` 进入 | useTableQuery 的 filters 同名键，地址栏直读 | 已实现 |
| 筛选 / 分页同步路由 query | `useTableQuery('logs')`（page/size + 11 个筛选键） | 已实现 |
| 四态：首次加载骨架 / 刷新保留旧数据 / 空（带清除筛选）/ 错误（ErrorBlock 重试） | 列表、汇总、请求动态、详情各自处理 | 已实现 |
| 手机 375px 卡片模式 | DataTable 列 `mobile` 标注 + ≤640px 筛选控件全宽 | 已实现 |
| 平台切换重取 | `usePlatformStore().onChange`（卸载取消） | 已实现 |
| 按账号汇总跟随平台筛选 | — | 有差异：后端 `log_summary` 不接 platform/realm（logs.py:1128），汇总按全网关口径；平台筛选只作用于列表。旧版同样如此，已在页面注释与契约 §2 注明。 |
| 详情抽屉内正文「展开全部」一次读完 | 分页按钮「继续读取」 | 有差异：按后端 64 KiB 分页逐页读取（避免一次把 8 MiB 正文塞进 DOM），不是一次性展开。 |
