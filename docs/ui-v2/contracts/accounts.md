# 功能契约：账号管理页（Accounts）

**页面标识**: P13（TABS 中「账号」）  
**入口函数**: `loadAccounts()`  
**路由**: 侧栏「账号」菜单 或 概览页账号监控卡片点击状态标签

---

## 一、页面整体结构

```
┌─ 平台上下文切换器（侧栏顶部）
│
├─ 页面操作栏
│  ├─ 搜索框（按账号名称、平台、UID 搜索）
│  ├─ 过滤器（状态、平台、关键字）
│  ├─ 右上方按钮组
│  │  ├─ 「添加账号」（新增单个）
│  │  └─ 「批量导入」（CSV / JSON / JSONL）
│  └─ 视图切换（列表 / 卡片，可选）
│
├─ 侧栏快速过滤（可选）
│  ├─ 按状态聚合计数
│  │  ├─ 活跃 / 暂停 / 已禁用 / 无余额 / 冷却中（点击过滤）
│  │  └─ 展示各状态的账号数
│  └─ 点击数字快速过滤
│
├─ 中间屏：账号表格
│  ├─ 表头（动态列名取决于平台）
│  │  ├─ ☑ 全选复选框
│  │  ├─ 账号名称（支持排序）
│  │  ├─ 状态（支持排序、色码标记）
│  │  ├─ 总额度 / 总 Token（平台决定）（支持排序）
│  │  ├─ 剩余 / 可用（支持排序）
│  │  ├─ 额度到期 / Token 到期（支持排序）
│  │  ├─ 入库时间（支持排序）
│  │  └─ 操作（编辑、暂停/启用、删除、…）
│  │
│  ├─ 行内容
│  │  ├─ 账号行：展示基础信息
│  │  ├─ 色码行为：
│  │  │  ├─ 绿色：余额充足（≥ 10 天平均消耗）
│  │  │  ├─ 黄色：余额不足（3～10 天平均消耗）
│  │  │  ├─ 红色：余额紧张（< 3 天平均消耗）
│  │  │  ├─ 灰色：已禁用 / 冷却中
│  │  │  └─ 橙色：即将过期（< 7 天）
│  │  └─ 点击行进入侧抽屉（详情 / 编辑）
│  │
│  ├─ 分页控制
│  │  ├─ 每页条数选择（10 / 25 / 50 / 100）
│  │  ├─ 前后翻页
│  │  └─ 总条数显示
│  │
│  └─ 表格操作栏（底部）
│     ├─ 选中项计数
│     ├─ 批量操作
│     │  ├─ 批量暂停 / 启用
│     │  ├─ 批量删除
│     │  ├─ 批量标记（标签，可选）
│     │  └─ 批量导出
│     └─ 清空选中
│
├─ 详情侧抽屉（右侧 overlay）
│  ├─ 账号基础信息
│  │  ├─ 名称、平台、UID、状态
│  │  └─ 创建时间、最后使用时间
│  │
│  ├─ 额度信息
│  │  ├─ 总额度 / 剩余额度
│  │  ├─ 额度到期日
│  │  ├─ 额度消耗趋势（小图表，可选）
│  │  └─ 冷却状态（若有）
│  │
│  ├─ 操作按钮组
│  │  ├─ 编辑
│  │  ├─ 连通性检查（测试 token / 模型）
│  │  ├─ 暂停 / 启用
│  │  ├─ 删除
│  │  ├─ 清除冷却（如适用）
│  │  └─ 查看使用记录
│  │
│  └─ 关闭按钮
│
└─ 底部：数据更新时间戳 + 刷新按钮
```

---

## 二、数据来源与接口

### 主接口

```http
GET /api/accounts?
  page={int}&
  per_page={10|25|50|100}&
  sort={field}:{asc|desc}&
  status={active|paused|disabled|no_balance|cooling}&
  keyword={string}&
  platform={string}
```

**参数**:
- `page`: 页码（从 1 开始），默认 1
- `per_page`: 每页条数，默认 25
- `sort`: 排序字段与方向，如 `created_at:desc`，默认 `-created_at`（最新优先）
- `status`: 按状态过滤（多选用逗号分隔，或不传 = 全部）
- `keyword`: 按账号名称、UID 子串搜索
- `platform`: 按平台过滤（来自 `X-Platform` 请求头，可选）

**返回字段**（`GET /api/accounts`）:

```json
{
  "items": [
    {
      "id": 123,
      "name": "Account Name",
      "platform": "workbuddy",
      "uid": "user_12345",
      "status": "active",             // active | paused | disabled | no_balance | cooling
      "balance_total": 100.00,        // 总额度 / 总 Token
      "balance_remain": 45.67,        // 剩余额度
      "credits_used": 54.33,          // 已消耗
      "last_used_at": 1700000000,     // Unix 时间戳（秒），最后使用时间
      "created_at": 1690000000,       // 创建时间
      "expires_at": 1800000000,       // 额度到期时间（可选）
      "cool_until": 1700003600,       // 冷却结束时间（可选，仅冷却中时有）
      "last_err_msg": "..."           // 最近错误（可选）
    },
    // ... 更多账号
  ],
  "summary": {
    "total": 150,                      // 总账号数（全库 or 按过滤条件）
    "active": 120,                     // 活跃账号数
    "paused": 15,                      // 暂停账号数
    "disabled": 10,                    // 禁用账号数
    "no_balance": 5,                   // 无余额账号数
    "cooling": 3,                      // 冷却中账号数
    "balance_remain": 5234.56,         // 启用账号的合计可用余额
    "balance_total": 10000.00,         // 启用账号的合计总额度
    "disabled_balance_remain": 100.00, // 禁用账号上冻结的余额
    "credits_expiring_soon": 234.56,   // 7 天内到期的总额度
    "expiring_accounts": 8,            // 7 天内有到期额度的账号数
    "soonest_expire_at": 1700000000    // 最紧迫的到期时间戳
  },
  "paginated": true,                   // 是否分页（服务端分页标记）
  "page": 1,
  "per_page": 25,
  "total_pages": 6
}
```

### 副接口

**获取账号详情**:
```http
GET /api/accounts/{id}
```
返回单个账号的完整信息（包括历史错误、冷却期详情等）

**创建账号**:
```http
POST /api/accounts
Content-Type: application/json

{
  "name": "My Account",         // 可选，默认从凭证取真实昵称
  "auth_json": "{...}"          // 凭证原文（加密格式）
}
```
返回 `{ "id": 123, ... }`

**编辑账号**:
```http
PUT /api/accounts/{id}
Content-Type: application/json

{
  "name": "New Name"            // 可编辑字段（部分平台可能有更多）
}
```

**暂停/启用账号**:
```http
POST /api/accounts/{id}/pause
POST /api/accounts/{id}/resume
```
返回 `{ "status": "paused" / "active", ... }`

**删除账号**:
```http
DELETE /api/accounts/{id}
```

**批量导入**:
```http
POST /api/accounts/batch-import
Content-Type: application/json

{
  "items": [
    { "name": "...", "auth_json": "..." },
    // ...
  ],
  "verify": true                // 是否做真实上游验证
}
```
返回 `{ "created": 5, "failed": 1, "details": [...] }`

**批量操作**:
```http
POST /api/accounts/batch
Content-Type: application/json

{
  "action": "pause" | "resume" | "delete" | "export",
  "account_ids": [1, 2, 3, ...],
  "options": { ... }            // 根据 action 有不同参数
}
```

**测试连通性**:
```http
POST /api/accounts/{id}/test
{
  "test_type": "token" | "model" | "chat" | "balance"
}
```
返回 `{ "ok": true, "message": "...", ... }`

**刷新余额**:
```http
POST /api/accounts/batch-refresh
{
  "account_ids": [1, 2, 3, ...]
}
```
异步刷新，返回任务 ID；后续轮询 `/api/jobs/{task_id}` 获取进度

**导出账号**:
```http
POST /api/accounts/export
{
  "ids": [1, 2, 3],             // 空 = 当前平台全部
  "format": "backup" | "client", // backup = 原文备份；client = 加密客户端格式
  "include_disabled": false
}
```
返回导出文件下载链接 或 token（异步导出）

---

## 三、数据展示规范

### 3.1 账号状态标记

| 状态 | 徽章色 | 文案 | 触发条件 |
|------|------|------|---------|
| `active` | 🟢 绿 | 已启用 | 账号有效、有余额、未冷却 |
| `paused` | 🟡 黄 | 已暂停 | 用户主动暂停 |
| `disabled` | 🔴 红 | 已禁用 | 登录失败、凭证过期、风控禁用 |
| `no_balance` | 🔴 红 | 无余额 | 活跃但剩余额度 ≤ 0 |
| `cooling` | 🟠 橙 | 冷却中 | 触发熔断，恢复中 |

### 3.2 数字格式化（复用 overview.md 规则）

#### 金额格式（`fmtCredit(value, sign)`）
- 千分位 + 2 位小数，如 `1,234.56`
- 可选加正负号

#### 整数格式（`ovInt()`）
- 千分位分组，如 `1,234,567`

#### 时间格式（`fmtTime(s, withSec)`）
- 输入：Unix 时间戳（秒）
- 输出：`YYYY-MM-DD HH:mm` 或 `HH:mm:ss`

#### 相对时间（如「冷却倒计时」）
- 格式：`X 天 Y 小时` 或 `X 小时 Y 分钟`
- 若 < 1 分钟，显示 `即将恢复`

### 3.3 余额着色规则

依赖 `/api/overview` 返回的 `average_credits`（平均日消耗）：

| 余额 vs 平均消耗 | 颜色 | 说明 |
|-----------------|------|------|
| ≥ 10 天 | 🟢 绿 | `ok` 健康 |
| 3～10 天 | 🟡 黄 | `warn` 预警 |
| < 3 天 | 🔴 红 | `danger` 紧张 |
| 已禁用 | 🔘 灰 | `disabled` |

### 3.4 表格列名动态调整（平台相关）

**WorkBuddy / CodeBuddy**:
- 列名：「总积分」「剩余」「积分到期」

**Trae**:
- 列名：「总 Token」「剩余」「凭证到期」
- 额外列：「地区」「设备绑定」

**Qoder / Loomy**:
- 列名：「总额度」「剩余」「有效期」
- 格式因平台而异

---

## 四、交互与行为

### 4.1 页面加载

**首次进入**:
1. 显示加载状态（骨架屏）
2. 调用 `GET /api/accounts?page=1&per_page=25`
3. 渲染表格行、分页控制、统计卡片
4. 侧栏快速过滤按钮显示各状态计数

**搜索与过滤**:
1. 用户在搜索框输入关键字 → 防抖（300ms）
2. 调用 `GET /api/accounts?page=1&keyword=xxx&status=...`（重置到第 1 页）
3. 表格平滑更新

**排序**:
1. 点击表头列名 → 切换排序方向（无排序 → 升序 → 降序 → 无排序）
2. 调用 `GET /api/accounts?page=1&sort=field:asc`
3. 保留当前搜索与过滤条件

**分页**:
1. 点击前后按钮 或 选择页码
2. 调用 `GET /api/accounts?page=X&per_page=Y`
3. 表格滚动到顶部（平滑）

**侧栏状态过滤**:
1. 点击状态标签（「活跃」「暂停」等）→ 立即过滤
2. 若已有同类过滤，再次点击 = 清除该过滤
3. 多个状态可叠加（`status=active,paused`）

### 4.2 操作交互

#### 添加账号
- 点击「添加账号」→ 弹出对话框
  1. 输入账号名称（可选，默认从凭证取昵称）
  2. 粘贴凭证原文 或 点击「选择文件」上传 `.info` 文件
  3. 勾选「验证凭证可用性」（默认勾选）
  4. 点击「确定」
- 后端校验、验证、入库 → Toast 提示成功 或 错误原因
- 成功后重新加载表格

#### 批量导入
- 点击「批量导入」→ 弹出对话框
  1. 选择导入格式（JSON 数组 / JSONL / CSV）
  2. 粘贴或上传文件
  3. 可选：关闭「验证凭证可用性」（加快导入，仅做结构检查）
  4. 点击「开始导入」
- 显示进度条 + 实时反馈（已导入 X 个，失败 Y 个）
- 完成后展示汇总（成功数、失败数、错误详情）

#### 点击行进入详情抽屉
- 点击账号行 → 右侧展开抽屉，显示详情
- 在抽屉中可以编辑、暂停、删除、测试等

#### 编辑账号
- 在详情抽屉中点击「编辑」 → 弹出对话框
- 修改账号名称（平台、UID 等不可编辑）
- 点击「保存」→ PUT 请求 → 更新表格 → 关闭对话框

#### 暂停 / 启用账号
- 点击行的「暂停」 或 「启用」按钮
- 弹出确认对话框（仅暂停需要确认，启用直接操作）
- 确认 → POST 请求 → 状态徽章更新 → Toast 提示

#### 删除账号
- 点击行的「删除」按钮
- 弹出危险操作确认：「确定要删除账号 XXX 吗？此操作不可撤销」
- 确认 → DELETE 请求 → 表格重新加载 → Toast 提示

#### 批量操作（表格底部）
- 勾选多行 → 底部操作栏显示「已选 N 个」
- 点击「批量暂停」/ 「批量启用」/ 「批量删除」/ 「批量导出」
- 暂停/启用/删除：弹出确认 → POST 或 DELETE 批量请求 → 表格刷新
- 导出：POST 请求 → 触发下载 或 返回导出token

#### 测试连通性
- 点击行的「🧪 测试」或 详情抽屉中的「连通性检查」
- 弹出对话框，选择测试项（凭证、模型、真实对话、余额）
- 显示进度 → 返回结果（成功 ✓ 或 失败 ✗ 附带原因）

#### 清除冷却
- 若账号处于冷却中，详情抽屉显示「清除冷却」按钮
- 点击 → 确认 → POST 请求 → 状态立即更新为「已启用」

#### 查看使用记录
- 点击「📊 使用记录」→ 跳转到「日志」页（P7），按此账号过滤

### 4.3 刷新按钮
- 位置：页面底部右侧 或 表格上方
- 行为：重新加载账号列表（保留当前搜索、过滤、排序条件）
- 显示：最后刷新时间戳

### 4.4 平台切换
- 通过平台上下文事件 `wb-platform-change` 触发
- 自动刷新表格（调用 `loadAccounts()`）
- 保留搜索与过滤条件

---

## 五、跨页联动

### 5.1 平台上下文
- 所有 API 请求自动按 `X-Platform` 请求头过滤
- 切平台时 localStorage 同步，页面自动刷新

### 5.2 导航跳转
- 概览页「账号监控」卡片点击状态标签 → 此页并按状态过滤
- 此页「查看使用记录」→  跳转到「日志」页，按账号过滤
- 此页「查看配额」(若有) → 跳转到「配额管理」页

### 5.3 全局事件
- 账号状态变化后，全局事件 `wb-account-updated` 广播
- 其它页面（如 overview 页）收到后局部刷新
- 此页若处于前台，重新加载表格

---

## 六、边界情况与容错

### 6.1 数据缺失或异常

| 情况 | 处理 |
|------|------|
| 账号名称缺失 | 显示 `—` 或 UID（若有） |
| UID 缺失 | 显示 `—`（部分平台不提供） |
| 余额字段为 null | 显示 `—` |
| 到期时间超过 100 年 | 显示「长期有效」 |
| 冷却时间已过期 | 自动清除冷却标记，状态变回「已启用」 |
| 平台标签缺失 | 显示平台代码 |
| 最后错误文本超长 | 截断至 42 字符，tooltip 显示完整 |

### 6.2 加载态与错误

**加载中**:
- 显示骨架屏（整行灰色占位符）
- 保留上次成功的数据

**请求失败**:
- 显示 Toast 错误提示：「加载失败：[错误原因]」
- 保留上次成功的数据（若有）
- 提供「重试」按钮

**响应超时**（> 10 秒）:
- 显示降级视图（上次缓存数据 或 只显示时间戳）
- 后台继续重试

### 6.3 空状态

| 场景 | 显示 |
|------|------|
| 无账号 | 表格显示「暂无账号，点击「添加账号」开始」 |
| 搜索无结果 | 表格显示「没有匹配的账号，试试调整搜索或筛选条件」 |
| 过滤后无结果 | 同上 |
| 账号池被禁用 | 顶部横幅「此平台的账号池已禁用，请联系管理员」，表格显示空列表 |
| 权限不足 | 显示「您没有权限访问此页面」 |

### 6.4 冲突与竞态

**批量操作中途失败**:
- 显示「部分操作失败」，列出失败的账号 ID 与原因
- 已成功的操作保留，用户可选择重试失败项

**账号被并发删除**:
- 页面显示 404，Toast 提示「账号已被删除」
- 自动刷新表格

**导入时检测到重复**:
- 按 UID 判重，显示「账号 XXX 已存在，将跳过」
- 继续处理其他账号

### 6.5 功能开关与平台差异

**平台只读字段**:
- 某些平台的账号名称不可编辑（凭证自带）→ 编辑对话框中该字段禁用

**平台差异的操作**:
- Trae：有「设备指纹」、「地区」、「凭证到期」等特有字段
- Qoder：有「套餐」、「资源包」等
- 侧抽屉和编辑对话框需动态显示/隐藏这些字段

**端差异**（PC vs 移动）:
- **PC**: 表格横向排列，侧抽屉固定右侧
- **移动**: 表格响应式缩列，侧抽屉全屏或从下方 slide up

---

## 七、旧版有但本契约未涉及的功能

| 功能 | 旧版位置 | 新版建议位置 | 状态 |
|------|---------|------------|------|
| 账号分析（消耗趋势、按账号排行） | 账号列表下方 | 移到「数据看板」（P11） | ✓ 已规划 |
| 配额管理（模型冷却、局部限制） | 账号详情页 | 新增独立页「配额管理」（P20） | ✓ |
| 账号分组 / 标签 | 无 | 后期功能，暂不实现 | ⏳ |
| 账号健康度评分 | 无 | 在列表中展示简单评分（可选） | ⏳ |

---

## 八、实现要点

1. **缓存策略**：账号列表缓存 10 秒，用户点击刷新时 `force=true` 绕过缓存

2. **性能优化**：
   - 大量账号时用虚拟滚动（Virtual Scroll）避免 DOM 爆炸
   - 搜索/过滤 300ms 防抖
   - 分页服务端分页，单页不超过 100 条
   - 侧抽屉详情数据延迟加载

3. **无障碍**:
   - 表格用 `<table>` 语义标签，行用 `<tr>`，单元格用 `<td>`
   - 状态徽章用 `aria-label` 提供完整文字描述，不单依赖颜色
   - 操作按钮用 `<button>` 且有 `aria-label`
   - 搜索框有 `<label>`，关联 `for` 和 `id`

4. **国际化**：所有文案（「添加账号」「已启用」等）提取到 i18n 配置

5. **主题适应**：所有颜色变量用 CSS Custom Properties（`--ok`、`--warn`、`--danger` 等），支持亮/暗主题切换

6. **实时刷新**（可选高级功能）:
   - 通过 SSE 或 WebSocket 实时推送账号状态变化
   - 页面自动更新行的状态徽章、余额等，无需手动刷新

7. **批量操作幂等性**：确保批量操作即使网络中断重试也不会重复操作（使用 idempotency key）

8. **导出安全**：
   - 导出的文件自动加密或仅含脱敏信息
   - 导出token 有 TTL（如 30 分钟），超期无法下载
---

## 实现对照

> 本节由 S25（账号池·上）实现时追加（原 1–534 行未改动）。S26 会补齐导入/添加账号的完整表单；
> 本步只交付列表、行/批量操作、整池后台任务与入口壳。

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 页头：导入 / 扫描本机 / 导出 / 添加账号 ▾ | `AccountsPage.vue` PageHeader；`addAccountModes` 按平台声明生成下拉项 | 已实现（入口壳：导入/扫描/添加方式点击提示「S26 实现」；导出走真实异步导出任务） |
| 状态页签：全部 / 已启用 / 已禁用 / 冷却 / 熔断（+计数，`?status=`） | `Segmented` + `accountTabCounts` + `normalizeAccountStatus`（`cooldown`→`cooling`，兼容概览页链接） | 已实现（计数来自全量行，保证与筛选结果一致） |
| 搜索（名称 / UID / 域名 / 绑定 Key） | `FilterBar` + `filterAccountRows` | 已实现（后端只搜 name/uid；域名与绑定 Key 为客户端补足） |
| 积分区间筛选（≥ / ≤） | FilterBar 两个输入 + `filterAccountRows` | 已实现（与旧版 accMinCredits/accMaxCredits 对应） |
| 排序（ID / 名称 / 剩余积分 / 最近同步 / 最近使用） | 列 `sortable` + 受控 `sortOrder` + `sortAccountRows` | 已实现（客户端排序；旧版的 5 个 sort 值等价） |
| 分页（50/100/200） | `paginateAccountRows` + `useTableQuery` | 已实现（**有差异**：为保证冷却/熔断页签与计数正确，本步取全量后客户端分页；`listAccounts` 仍保留 page/page_size 参数，S26 可切服务端分页） |
| 账号列（名称 + UID 脱敏 + 域名 + 令牌天数） | 列 `name` / `renderAccount` / `maskAccountId` | 已实现 |
| 状态 Badge（活跃/冷却/熔断/降级/已禁用）+ 悬停原因与恢复时间 | `accountStatusMeta` + `Badge` | 已实现 |
| 平台扩展列（能力声明驱动） | `showTokenColumn` + `tokenColumn`：任一账号 `token_supported=true` 时追加「令牌」列（剩余天数 + 续期悬停，expired/expiring 着色） | 已实现（后端 `/api/platforms` 未声明「列」，故以账号能力字段驱动；更多扩展列待后端补声明） |
| 剩余额度（Meter + 剩余/总，<20% warn / <5% danger） | 列 `balance` / `renderBalance` | 已实现 |
| 今日消耗 | 列 `today`；数据来自 `GET /api/logs/summary?days=1` 一次聚合 | 已实现（旧版列表没有该列，设计稿 P6 新增；后端没有独立「今日消耗」字段，因此复用日志汇总） |
| 到期（`fmtExpiry`） | 列 `expire`，取 `credits_soonest_expire_at`（毫秒） | 已实现 |
| 最近使用（`fmtRelative`） | 列 `lastUsed` | 已实现 |
| 备注（内联编辑） | 列 `note` 展示绑定 Key；行操作「编辑」打开 `FormDrawer` | **有差异**：`Account` 没有 note 字段（PATCH 只支持 name/max_in_flight/status）；可编辑项改为账号名称与并发上限，备注列展示绑定的 API Key |
| 行操作：刷新额度 / 检测 / 启用禁用 / 编辑 / 详情 / 签到（能力声明）/ 删除 | `RowActions`（前 2 个直显，其余进 ⋯，删除朱色）+ `actionList` | 已实现（签到只在 `accountFeatures().checkin` 为 true 的平台出现；调用 `/api/accounts/{id}/task`，平台不支持时如实提示） |
| 详情抽屉（全部字段 / 额度明细 / 模型冷却） | `FormDrawer`（lg、隐藏底栏）+ `KV` + 积分包列表 + `model_limits` | 已实现（**有差异**：未接「最近请求」——需要按账号查日志，留给 S26/日志页联动；积分包来自 `GET /{id}/credit-details`） |
| 批量工具条：启用 / 禁用 / 刷新额度 / 检测 / 导出选中 / 删除 | `BulkBar` 插槽 + `POST /api/accounts/bulk` + `export-async` | 已实现（删除需输入「删除」确认；结果为 affected/errors，部分失败有 Toast） |
| 整池任务：刷新全部 | 工具栏「刷新全部」→ `POST /api/accounts/refresh-batch` → `useJob` 轮询 | 已实现（确认后启动；任务键带平台，重复点击复用同一任务） |
| 整池任务：检测全部 | 工具栏「检测全部」→ `POST /api/accounts/health-check-batch` | 已实现（新增入口：旧版在「管理操作」菜单里） |
| 模型额度与冷却 | 工具栏按钮 → `GET /api/accounts/model-limits` → 抽屉列表 | 已实现（只读列表：账号 × 模型 × 剩余时间） |
| 成长任务 | 工具栏（仅 WorkBuddy）→ `POST /api/growth/run-async` | 已实现（按 `accountFeatures().growth` 显示） |
| 猫猫旅行与领奖 | 工具栏（仅 WorkBuddy）→ `POST /api/accounts/cat-travel/batch-async` | 已实现（按 `accountFeatures().catTravel` 显示） |
| 后台任务进度：内联条 + 右下 JobProgress + 1500ms 轮询 | `useJob(undefined, { pollUrl: '/api/accounts/job/' })` + 表格上方内联条 + `JobProgress` | 已实现（与旧版 accountJobStatus/runJob 一致；任务详情抽屉列出逐条结果） |
| 导出（异步 + 完成后自动下载） | `POST /api/accounts/export-async` → 轮询 → `download('/api/accounts/export-download', {token})` | 已实现（导出当前筛选结果或选中项） |
| 平台能力声明驱动 | `accountFeatures()`（唯一判断点）+ `/api/platforms.actions` | 已实现（页面无 `if (platform === …)` 散落分支） |
| 平台切换重载 + 卸载取消 | `usePlatformStore().onChange`（`/api/accounts` 读 X-Platform） | 已实现 |
| 筛选/排序/分页同步路由 query | `useTableQuery('accounts')`（status/q/min/max + page/size/sort/order） | 已实现 |
| 四态：首次加载骨架 / 刷新保留旧数据 / 空 / 错误+重试 | NSkeleton / Card loading + DataTable 进度条 / Empty（区分筛选无命中）/ ErrorBlock | 已实现 |
| 手机 375px 卡片模式 | 列 `mobile` 标注（title / meta / subtitle / actions） | 已实现 |
| 添加账号各方式（手机号、PKCE、手动、导入预览） | — | **未做（S26）**：页头保留下拉入口并按平台能力列出可见项，点击提示「S26 实现」 |
| 扫描本机预览 / 导入本机 / 导入异步 | — | **未做（S26）**：页头保留入口 |
| 订阅账号状态变化自动刷新（契约 §5.3） | — | 未做：无 SSE/事件源；由手动刷新与操作后刷新覆盖（S26 若接 SSE 再补） |
| 单元测试（状态归一 / 行转换 / 页签筛选 / 排序分页 / 任务快照 / 能力声明） | `tests/page-accounts.spec.ts`（19 个用例，真实响应形状） | 已实现 |

### 口径核对（以 accounts.py / logs.py / jobrunner.py 实际字段为准）

- `GET /api/accounts` 不带 `page` 时返回全量 `{items, summary}`；带 `page` 时返回 `{items, summary, paginated, total, page, page_size}`。
- `status` 查询参数只认 `active|disabled`；冷却/熔断/降级由 `gateway_state` + `gateway_reason`（cooling/breaker/degraded）表达。
- `balance_total` / `balance_remain` 为整数；`credits_soonest_expire_at` 是**毫秒**时间戳。
- `bound_key_name` 是独占绑定的 Key 名；`model_limits[]` 是当前仍生效的模型冷却（`remaining_seconds`）。
- `POST /api/accounts/bulk` 同步执行（enable/disable/delete/refresh/check），返回 `{affected, skipped, errors}`；删除被 Key 独占绑定的账号会跳过并提示。
- `refresh-batch` / `health-check-batch` 返回 `jobrunner` 快照；轮询 `GET /api/accounts/job/{job_id}`，`status !== running` 即结束。
- 导出任务完成时 `result.file_token` 用于 `GET /api/accounts/export-download?token=…`，文件 30 分钟内可重复下载。
- `GET /api/logs/summary?days=1` 的 `items[].account_id/requests/credits` 是「今日消耗」列的数据源（请求数含失败、积分只算成功）。
