# 功能契约：平台管理页（Platforms）

**页面标识**: P4（TABS 第四个）  
**入口函数**: `loadPlatformOverview()` / `loadPlatformBench()`  
**路由**: 点击导航栏「平台」或通过 URL 导航

---

## 一、页面整体结构

```
┌─ 平台上下文切换器（侧栏顶部）
│
├─ 上半屏：平台性能基准（Bench）
│  ├─ 排名卡片矩阵（3行）
│  │  ├─ 排名 / 平台名 / 成功率 / 延迟 / 抖动 / 操作
│  │  ├─ 着色规则：成功率 ≥98% (绿) / 90-98% (黄) / <90% (红)
│  │  ├─ 徽标：「最快」「最稳」「样本少」
│  │  └─ 操作按钮：「设为最高优先级」
│  └─ 统计提示：近 N 小时 · M 个平台有样本
│
├─ 中间屏：平台列表（主区域）
│  ├─ 左侧：平台列表面板（可搜索、可排序）
│  │  ├─ 搜索框（实时过滤）
│  │  ├─ 列表项（平台行）
│  │  │  ├─ 平台名 / 图标 / 标签
│  │  │  ├─ 状态指示（启用/禁用/维护）
│  │  │  ├─ 账号数 / 最后活跃时间
│  │  │  └─ 点击选中 → 右侧加载详情
│  │  └─ 底部：结果计数
│  │
│  └─ 右侧：选中平台的详情面板
│     ├─ 平台基本信息卡片
│     │  ├─ 平台代码 / 标签 / 图标
│     │  ├─ API 端点 / 是否代理
│     │  ├─ 描述 / 能力声明（扫描/导入/签到…）
│     │  ├─ 授权模式（local-scan / web-login / import-only）
│     │  └─ 评级与说明
│     │
│     ├─ 账号列表区
│     │  ├─ 表格列头
│     │  │  ├─ 账号名 / 状态 / 余额 / 剩余额度 / 到期时间 / 入库时间 / 操作
│     │  │  └─ 点击列头可排序
│     │  │
│     │  ├─ 分页器（若数据量大）
│     │  │  ├─ 上一页 / 下一页 / 页码输入 / 每页条数选择
│     │  │  └─ 位置：表格上方或下方
│     │  │
│     │  └─ 表格行
│     │     ├─ 账号名 / 状态徽标 / 总额度 / 剩余 / 到期 / 创建时间 / 操作按钮
│     │     ├─ 状态着色：active (绿) / paused (黄) / disabled (红) / no_balance (灰)
│     │     └─ 操作按钮：编辑 / 暂停/启用 / 删除 / 查看使用记录
│     │
│     └─ 操作按钮区（底部或右上）
│        ├─ 添加账号 → 弹出对话框
│        ├─ 批量导入 → 弹出导入向导
│        ├─ 编辑平台信息 → 弹出编辑对话框
│        └─ 删除平台（如允许）→ 确认对话框
│
└─ 底部：数据采集时间戳 + 刷新按钮
```

---

## 二、数据来源与接口

### 主接口

#### 2.1 平台性能基准

```http
GET /api/platform/speed?hours={24|7|1}
```

**参数**:
- `hours`: 时间窗口（1、7、24 小时）

**返回字段**:

```json
{
  "hours": 24,
  "fastest": "openai",              // 最快平台的 ID
  "steadiest": "deepseek",          // 最稳平台的 ID
  "items": [
    {
      "rank": 1,
      "platform": "openai",
      "label": "OpenAI",
      "requests": 1500,
      "success_rate": 99,           // 百分比整数
      "latency_p50": 245,           // 中位数延迟（毫秒）
      "jitter": 12,                 // 标准差（毫秒）
      "low_sample": false           // 样本是否不足（建议标记）
    },
    // ... 其他平台
  ]
}
```

**排序规则**:
- 以「延迟 + 抖动 + 失败率」综合排序
- 计算方式由后端决定，前端只负责展示

---

#### 2.2 平台列表（带元信息）

```http
GET /api/platforms
```

**返回字段**:

```json
[
  {
    "id": "openai",
    "label": "OpenAI",
    "icon": "openai",               // 图标 key（前端在 platform-icons.js 查表）
    "enabled": true,
    "storage_platform": "openai",   // 落库平台（实际在哪个数据库）
    "realm": null,                  // 单区平台为 null；多区为 "cn" / "intl"
    "realm_label": "",
    "realms": [                     // 多区信息（单区为空数组）
      { "key": "cn", "label": "国内版", "default": true },
      { "key": "intl", "label": "国际版", "default": false }
    ],
    "proxied": false,               // 是否通过网关代理
    "managed": true,                // 是否提供管理契约
    "auth_mode": "web-login",       // local-scan / web-login / import-only
    "actions": ["scan", "import", "refresh-quota", "usage", "checkin"],
    "quota_unit": "credits",        // credits / token
    "quota_unit_label": "积分",
    "rating": {
      "tier": "elite",              // hang / elite / npc / bad
      "reason": "速度快、稳定性好",
      "scores": {                   // 可选明细
        "speed": 5,
        "quota": 4,
        "stability": 5
      },
      "source": "platform"
    },
    "notes": "官方高质量平台",
    "extra": {}
  },
  // ... 其他平台
]
```

**数据含义**:
- `enabled`: 平台是否对用户可用（禁用的平台不显示在列表中，或显示为灰态）
- `managed`: 是否提供账号管理接口（管理页面据此决定要不要显示该平台）
- `auth_mode`: 控制「添加账号」时的入口方式
  - `local-scan`: 只支持扫描本机，不支持授权登录
  - `web-login`: 支持授权登录（如 OAuth）
  - `import-only`: 只支持粘贴导入，不支持扫描或登录
- `actions`: 平台支持的操作列表，前端据此决定显示哪些按钮

---

#### 2.3 平台详情 + 账号列表

```http
GET /api/platforms/{name}/accounts?page={1}&per_page={20}&sort_by={name|status|balance}&sort_dir={asc|desc}
```

**参数**:
- `page`: 分页页码（从 1 开始）
- `per_page`: 每页条数（默认 20）
- `sort_by`: 排序字段（name / status / balance / created_at）
- `sort_dir`: 排序方向（asc / desc）

**返回字段**:

```json
{
  "platform": {
    "id": "openai",
    "label": "OpenAI",
    "icon": "openai",
    "enabled": true,
    // ... 其他字段（同平台列表）
  },
  
  "summary": {
    "total": 25,                    // 该平台的账号总数
    "active": 20,                   // 启用中的账号
    "paused": 2,                    // 暂停中
    "disabled": 3,                  // 已禁用
    "balance_remain": 1234.56,      // 启用账号的合计余额
    "balance_total": 5000.00,       // 启用账号的合计充值额
    "disabled_balance_remain": 50.00, // 已禁用账号上的余额（未计入可用）
    "credits_expiring_soon": 123.45, // 7 天内到期的总额度
    "expiring_accounts": 3,         // 有 7 天内到期额度的账号数
    "soonest_expire_at": 1700123456  // 最紧迫的到期时间戳（秒）
  },

  "items": [
    {
      "id": 12345,
      "name": "my-openai-account",
      "status": "active",           // active / paused / disabled / no_balance / cooling
      "balance_total": 100.00,
      "balance_remain": 45.67,
      "expires_at": 1702512000,     // Unix 时间戳（秒）；null 表示长期有效
      "created_at": "2024-01-15T10:30:00Z",
      "last_used_at": 1700000000,
      "cool_until": null,           // 冷却截止时间（若在冷却中）
      "last_err_msg": null,         // 最近上游错误
      // 平台特定字段（由后端返回）
      "uid": "user-123",            // 平台返回的用户标识
      "realm": "cn",                // 区域（多区平台）
      "region": "beijing",          // 地域（Trae 返回）
      "device_id": "abc123def",     // 设备绑定标识
      "device_bound": true          // 是否已绑定
    },
    // ... 更多账号
  ],

  "paginated": true,                // 是否采用服务端分页
  "page": 1,
  "per_page": 20,
  "total": 25,
  "pages": 2
}
```

---

#### 2.4 添加账号

```http
POST /api/platforms/{name}/accounts
Content-Type: application/json

{
  "account": "account-name",        // 必填：账号名称
  "data": { ... },                  // 平台特定字段（如凭证）
  "batch": false
}
```

**返回**:
```json
{
  "ok": true,
  "id": 12346,
  "message": "账号已添加"
}
```

---

#### 2.5 更新账号状态

```http
PUT /api/platforms/{name}/accounts/{id}/status
Content-Type: application/json

{
  "status": "paused"                // active / paused / disabled
}
```

---

#### 2.6 删除账号

```http
DELETE /api/platforms/{name}/accounts/{id}
```

---

#### 2.7 编辑平台信息

```http
PUT /api/platforms/{name}
Content-Type: application/json

{
  "label": "OpenAI Pro",            // 可选：显示名称
  "notes": "企业账户",              // 可选：备注
  "enabled": true                   // 可选：启用/禁用
}
```

---

#### 2.8 删除平台

```http
DELETE /api/platforms/{name}
```

**条件**: 平台下所有账号必须已删除或转移

---

### 副接口

**获取当前选中平台**（切换时用）:
```http
GET /api/platforms/current
```
返回 `{ "id": "openai", ... }`

**保存当前选择**:
```http
PUT /api/platforms/current
Content-Type: application/json

{ "id": "openai" }
```

**导入账号（批量）**:
```http
POST /api/platforms/{name}/accounts/batch-import
Content-Type: application/json

{
  "uids": ["user-id-1", "user-id-2", "user-id-3"],
  "mode": "overwrite"               // overwrite / skip / merge
}
```

---

## 三、数据展示规范

### 3.1 数字格式化

#### 额度格式（`fmtCredit()`）
- 用于余额、充值额、到期额
- 规则：千分位 + 固定 2 位小数，如 `1,234.56`
- 可选加正负号：`+123.45` / `-67.89`

#### 时间格式（`fmtTime()`）
- 输入：Unix 时间戳（秒）
- 输出：本地时间（UTC+8），格式为 `YYYY-MM-DD HH:mm:ss`
- 或简写为 `HH:mm:ss`（若当天）

#### 延迟格式（`fmtDuration()`）
- 输入：毫秒数
- 输出：如 `123ms` / `1.2s`
- 规则：≥1000ms 显示为秒，<1000ms 显示为毫秒

---

### 3.2 状态定义与着色

#### 平台状态
- **启用**（绿）: 平台对用户可用，可添加/编辑账号
- **禁用**（灰）: 平台已下线或维护中，账号列表置灰，操作按钮禁用
- **维护中**（黄）: 平台临时不可用，显示倒计时或「预计恢复时间」

#### 账号状态
- **active**（绿）: 账号启用且有余额
- **paused**（黄）: 用户主动暂停（不消耗额度，可恢复）
- **disabled**（红）: 账号已禁用（登录失效、被封禁等）
- **no_balance**（灰）: 账号启用但无余额（需充值）
- **cooling**（黄）: 账号处于冷却期（触发风控后，待冷却时间过去）

#### 性能基准着色
- **成功率**:
  - 绿色（`ok`）: ≥ 98%
  - 黄色（`warn`）: 90% ≤ < 98%
  - 红色（`danger`）: < 90%
- **延迟**:
  - 绿色（`ok`）: < 中位数的 70%
  - 黄色（`warn`）: 70% ≤ 延迟 < 150% 中位数
  - 红色（`danger`）: ≥ 150% 中位数
- **抖动**:
  - 绿色（`ok`）: < 20ms
  - 黄色（`warn`）: 20ms ≤ 抖动 < 50ms
  - 红色（`danger`）: ≥ 50ms

---

## 四、交互与行为

### 4.1 页面加载

**首次进入**:
1. 显示加载状态（骨架屏或灰色占位符）
2. 并行调用：
   - `GET /api/platform/speed?hours=24`（性能基准）
   - `GET /api/platforms`（平台列表）
   - `GET /api/platforms/current`（当前选中平台）
3. 渲染性能基准卡片
4. 渲染平台列表，自动选中当前平台
5. 加载当前平台的详情 + 账号列表（`GET /api/platforms/{name}/accounts`）

**平台切换**:
1. 用户点击左侧平台行
2. 更新 URL 参数（可选）或本地状态
3. 右侧面板显示加载状态
4. 调用 `GET /api/platforms/{name}/accounts?page=1`
5. 刷新右侧详情面板，重置分页到第 1 页

**时间窗口切换**（性能基准中的 1h / 24h / 7h）:
1. 用户点击时间切换按钮
2. 调用 `GET /api/platform/speed?hours={...}`
3. 卡片以动画更新数值

---

### 4.2 操作

#### 搜索平台
- 位置：平台列表上方
- 行为：实时过滤（输入时立刻过滤，不需后端请求）
- 匹配范围：平台名、标签、描述
- 过滤结果为空时显示「没有匹配的平台」

#### 排序平台列表
- 可排序字段：平台名、账号数、最后活跃时间、平均成功率（可选）
- 点击列头切换升序/降序
- 当前排序字段显示箭头指示符

#### 搜索账号
- 位置：账号表格上方
- 行为：实时过滤本页数据，不重新请求
- 匹配范围：账号名、UID

#### 账号表排序
- 可排序字段：名称、状态、余额、创建时间
- 点击列头触发 `GET /api/platforms/{name}/accounts?sort_by=...&sort_dir=...`
- 重置分页到第 1 页

#### 账号表分页
- 位置：表格上方或下方
- 控件：«  < 1 2 3 > » + 「每页」下拉（20 / 50 / 100）
- 行为：点击页码或改变每页条数时调用 `GET /api/platforms/{name}/accounts?page=...&per_page=...`

---

### 4.3 添加账号

**入口**:
- 右上方「添加账号」按钮
- 或底部「+」浮窗

**流程**:
1. 点击「添加账号」→ 弹出对话框
2. 对话框根据 `auth_mode` 显示不同入口：
   - `local-scan`: 显示「扫描本机」按钮
   - `web-login`: 显示「授权登录」按钮
   - `import-only`: 显示「粘贴 JSON」文本框
3. 用户完成凭证获取后，显示「确认」按钮
4. 点「确认」→ 调用 `POST /api/platforms/{name}/accounts`
5. 成功后关闭对话框，刷新表格并显示 Toast「账号已添加」

---

### 4.4 编辑账号

**入口**:
- 账号行右侧「编辑」按钮

**流程**:
1. 点击「编辑」→ 弹出对话框，显示账号现有信息
2. 允许修改的字段（由后端决定）：账号名、备注、是否启用
3. 点「保存」→ 调用 `PUT /api/platforms/{name}/accounts/{id}`
4. 成功后关闭对话框，刷新该行数据

---

### 4.5 暂停/启用账号

**入口**:
- 账号行右侧「暂停」或「启用」按钮（根据当前状态）

**流程**:
1. 点击按钮 → 显示确认对话框（仅暂停需确认）
2. 确认后调用 `PUT /api/platforms/{name}/accounts/{id}/status`
3. 成功后刷新该行状态，更新摘要数据（active / paused 计数）

---

### 4.6 删除账号

**入口**:
- 账号行右侧「删除」按钮（通常在更多菜单中）

**流程**:
1. 点击「删除」→ 显示确认对话框
   - 文案：「确定删除该账号吗？此操作不可撤销。」
   - 若账号有余额，补充：「该账号还有 ¥XXX 余额。」
2. 确认后调用 `DELETE /api/platforms/{name}/accounts/{id}`
3. 成功后从表格移除，刷新摘要数据
4. 显示 Toast：「账号已删除」

---

### 4.7 查看使用记录

**入口**:
- 账号行右侧「日志」或「查看使用记录」按钮

**流程**:
1. 点击 → 导航到「日志」页面（P13），自动过滤该账号
2. 或在当前页弹出侧抽屉，显示该账号的用量趋势（简化版）

---

### 4.8 批量导入

**入口**:
- 右上方「批量导入」按钮

**流程**:
1. 点击「批量导入」→ 弹出导入向导
2. 第 1 步：选择导入模式
   - 「覆盖」: 相同 UID 的账号用新数据替换
   - 「跳过」: 相同 UID 的账号保留原数据
   - 「合并」: 合并字段（新建优先，冲突时保留原值）
3. 第 2 步：选择导入方式
   - 「粘贴 JSON」: 用户粘贴 JSON 数组
   - 「上传 CSV」: 用户上传文件（若平台支持）
4. 第 3 步：预览 & 确认
   - 显示待导入的账号列表（表格形式）
   - 显示「将导入 N 个新账号，更新 M 个现有账号」
   - 允许用户取消或继续
5. 点「开始导入」→ 调用 `POST /api/platforms/{name}/accounts/batch-import`
6. 导入完成后显示结果：「成功导入 N 个账号，X 个失败」
7. 返回账号列表，新导入的账号高亮显示（可选）

---

### 4.9 设为最高优先级

**入口**:
- 性能基准卡片中各平台行右侧「设为最高优先级」按钮

**流程**:
1. 点击按钮 → 显示加载状态
2. 调用 `PUT /api/gateway/config` 更新平台优先级
3. 成功后显示 Toast：「已把 XXX 设为最高优先级」
4. 刷新性能基准卡片（可选）

---

### 4.10 编辑平台信息

**入口**:
- 右侧详情面板右上角「编辑」按钮

**流程**:
1. 点击「编辑」→ 弹出对话框
2. 显示可编辑字段：标签名、备注、是否启用
3. 点「保存」→ 调用 `PUT /api/platforms/{name}`
4. 成功后关闭对话框，刷新详情面板

---

### 4.11 刷新按钮

**位置**:
- 页面右上角或各卡片右上角

**行为**:
- 刷新性能基准：`GET /api/platform/speed?hours=...`
- 刷新当前平台详情：`GET /api/platforms/{name}/accounts?page=...`
- 显示最后刷新时间戳

---

## 五、跨页联动

### 5.1 平台上下文
- 所有数据自动按当前平台过滤
- 切换平台时，URL 中 `platform` 参数更新，右侧面板自动刷新
- CustomEvent `wb-platform-change` 由 platform-switch.js 发送

### 5.2 导航跳转
- 点击账号行 → 可跳转到「日志」（P13）查��该账号的使用记录
- 点击「性能基准」中的平台行 → 可跳转到「缓存分析」（P24，如支持）
- 右侧摘要中的「启用 / 禁用」数字 → 可跳转到「账号管理」（P13）

### 5.3 全局事件
- 添加/删除账号时，侧栏的「账号总数」标记需更新
- 平台被禁用时，页面自动切换到其他平台
- 平台被删除时，列表自动移除该平台行

---

## 六、边界情况与容错

### 6.1 数据缺失或异常

| 情况 | 处理 |
|------|------|
| 平台列表为空 | 显示「暂无可用平台」，右侧为空状态 |
| 平台已禁用 | 列表显示灰态，右侧数据置灰，所有操作按钮禁用 |
| 账号余额为 null | 显示 `—` 或 `0.00` |
| 账号到期时间为 null | 显示「长期有效」或 `—` |
| 账号无使用历史 | `last_used_at` 显示 `—` 或「从未使用」 |
| 性能基准无样本 | 显示「近 24 小时还没有足够样本」 |
| 平台支持的操作为空 | 添加账号按钮显示为禁用或隐藏，显示「该平台暂不支持手动添加」 |

### 6.2 加载态与错误

**加载中**:
- 显示骨架屏（左侧平台列表 + 右侧详情面板）
- 卡片内容淡出，显示灰色占位符
- 可选：显示「加载中…」文案

**请求失败**:
- 显示错误提示（Toast）：「加载失败，请重试」
- 保留上次成功的数据（若有）
- 提供「重试」按钮

**响应超时**（> 5 秒）:
- 显示降级视图（上次缓存数据或仅显示时间戳）
- 后台继续重试
- 收到响应后自动更新

### 6.3 空状态

| 场景 | 显示 |
|------|------|
| 账号列表为空 | 表格内显示「该平台还没有账号，点「添加账号」开始」 |
| 搜索无结果 | 表格内显示「没有匹配的账号，试试调整搜索条件」 |
| 平台无权限访问 | 显示「你没有权限访问该平台」 |
| 账号列表受限（仅显示自己的） | 底部显示「仅显示与你相关的账号」 |

### 6.4 权限与功能开关

**可选功能**:
- 某些平台可能不支持「批量导入」：`actions` 字段中无 `import` 时，导入按钮隐藏
- 某些平台不支持「扫描本机」：`auth_mode` 为 `web-login` 或 `import-only` 时，扫描按钮隐藏
- 某些平台不支持「删除」：后端返回 403 时，删除按钮禁用

**端差异**（PC vs 移动）:
- **PC**: 左右分栏布局，账号表左右滚动
- **移动**: 全宽纵向堆叠，账号表上下滚动，详情面板可滑动关闭

---

## 七、旧版有但本契约未涉及的功能

| 功能 | 旧版位置 | 新版建议位置 | 状态 |
|------|---------|------------|------|
| 平台性能排行 | 控制台多个位置 | 本页顶部（性能基准） | ✓ |
| 账号池多选 & 批量操作 | 账号面板 | 本页表格列头勾选 | ⏳ |
| 账号数据导出 | 账号面板 | 本页表格右上「导出」 | ⏳ |
| 平台接入向导 | 单独页面 | 新平台提示 + 链接到文档 | ⏳ |

---

## 八、实现要点

1. **缓存策略**：平台列表缓存 5 分钟，性能基准缓存 1 分钟，用户点击刷新时 `force=true` 绕过缓存
2. **性能优化**：
   - 平台列表采用虚拟滚动（若超过 100 个平台）
   - 账号表默认只显示前 20 条，分页加载其余
   - 搜索时防抖（300ms）
3. **无障碍**:
   - 表格行用 `<tr>` 且行可聚焦（`tabindex="0"`）
   - 状态用 `<span role="status">` 标记
   - 数字用 `aria-label` 提供完整文字描述
   - 按钮用 `aria-pressed` / `aria-expanded` 表示状态
4. **国际化**：所有文案（「启用」「暂停」等）提取到 i18n 配置
5. **主题适应**：所有颜色变量用 CSS Custom Properties（`--ok` / `--warn` / `--danger` 等），支持亮/暗主题切换
6. **错误边界**：各异步操作应包裹 try-catch，错误时显示用户友好的提示
7. **表单验证**：
   - 账号名非空且不超过 128 字符
   - 编辑时检测未改动时禁用「保存」按钮
8. **确认流程**：删除、导入等破坏性操作前必须显示确认对话框

---

## 实现对照

> 本节是 S20 的交付说明（原 1–672 行未改动）。实现落在
> `admin/ui/src/pages/PlatformsPage.vue` + `admin/ui/src/api/platforms-admin.ts`（+ `tests/page-platforms.spec.ts`）。
> 设计稿以 `06-pages-a.md` P4（卡片墙 + 跑分表）为准；本契约前半部分是早先按「列表 + 右侧详情」写的设想，
> 与 P4 不一致处按 P4 实现，并在下表如实标注差异与真实接口。

| 契约功能 | 新版位置 / 真实接口 | 状态 |
|---|---|---|
| 平台卡片墙（logo 28 / 名称 / 状态 Badge / 区域 / 优先级） | `PlatformsPage.vue` 卡片网格；数据 `GET /api/platforms`（`router.py:list_platforms`）+ `GET /api/gateway/config`（priority） | 已实现 |
| 账号可用数（3/3 可用） | `GET /api/accounts?all_platforms=1` → `aggregateAccounts`（只算 `status=active`，按落库平台聚合） | 已实现（多一次账号快照请求，失败降级为只显示账号数） |
| 剩余额度 Meter | 同上：active 账号 `balance_remain` 求和；`quota_unit=token` 用 `fmtCompact`，积分用 `fmtCredit`；Meter 以全平台最大值为刻度 | 已实现 |
| 今日请求 / 成功率 / TTFT / TPS | `GET /api/platform/speed?hours=24\|168`（`bench_router.py:platform_speed`，成功率是 0–100） | 已实现 |
| 状态页签 全部/已启用/已禁用 + 搜索 | FilterBar + Segmented（计数来自卡片）；状态与搜索同步路由 query（`state`/`q`） | 已实现 |
| 卡片操作「账号池」 | `platformStore.setCurrent` + 跳 `/accounts` | 已实现 |
| 卡片操作「设为当前」 | `platformStore.setCurrent`（写 localStorage + `PUT /api/platforms/current`） | 已实现 |
| `⋯` 启用 / 禁用 | `POST /api/gateway/disable\|enable`（`gateway_api.py`），禁用需确认；禁用后卡片 60% 透明 + 提示「不参与调度，账号仍可管理」 | 已实现 |
| `⋯` 扫描本机 | `runPlatformScan`：workbuddy 系列 `POST /api/accounts/import-local {all:true}`；qoder 只 `GET /api/qoder/scan-local`；其它平台 `GET+POST /api/<storage>/scan-local|import-local`（与旧版 platformScanAll 分支一致） | 已实现 |
| `⋯` 导出本平台 | `GET /api/platforms/export-all?platform=<落库名>&mode=backup` → JSON 下载 | 已实现 |
| `⋯` 平台特有动作（actions 声明原样移植） | `platformActionsOf` 移植 platform-actions.js（含 import-only / 不支持扫描的修正、同组只留一个主按钮）；`import` 打开导入抽屉，其余动作切到账号池执行（签到/刷新额度/授权登录等按账号执行，属 S25/S26） | 有差异（动作入口在，执行落在账号池） |
| `⋯` tier / 平均级 | `tierOf`：平台自报 `rating` 优先，其次 platform-tiers.js 兜底表；菜单里显示「平均级：夯/人上人/npc/拉完了」+ 原因 | 已实现 |
| 扫描全部平台 | 页头按钮，逐平台串行执行（旧版同样串行、无后端 job），结果表展示 扫描/入库/跳过/说明 | 已实现（客户端串行进度，未用 JobProgress） |
| 导入全部平台（FormDrawer：文件选择 + 预览 + 确认） | `parsePlatformBackup` 解析 `format=miraihub-all-platforms`；逐平台 `POST /api/accounts/import-async` → `GET /api/accounts/job/{id}`；`useJob`+`JobProgress` 右下角进度 | 已实现 |
| 导出全部平台 | FormDrawer：备份（含凭证）/ 清单（脱敏）+ 平台范围；`GET /api/platforms/export-all` → `miraihub-<scope>-<mode>-<时间戳>.json` | 已实现 |
| 平台速度与稳定性表（成功率 / TTFT / TPS / 总耗时 / 抖动 / 样本） | DataTable（`table-id=platform-bench`），窗口 24h/7d 同步 query `hours`；样本 <3 标「样本少」warn 色 | 已实现 |
| 四态（首次骨架 / 刷新保留 / 空 / 错误） | 卡片网格骨架、`Card :loading`、`Empty`、`ErrorBlock`+重试 | 已实现 |
| 手机 375px | 卡片网格 1 列、搜索框满宽、DataTable 走组件卡片模式 | 已实现 |
| 平台上下文订阅 | `platformStore.onChange`（选择真变了才重载），卸载取消 | 已实现 |
| 契约 §一「左侧列表 + 右侧详情面板 + 账号表格 + 分页」 | 未实现：P4 设计稿是卡片墙，账号明细在 `/accounts`（S25/S26） | 有差异（设计稿未要求） |
| 契约 §2.2.4 `PUT /api/platforms/{name}` 改平台元信息 | 后端不存在该接口（平台元信息由代码声明），未实现 | 有差异（后端无此接口） |
| 契约 §2.2.5 `DELETE /api/platforms/{name}` 删平台 | 后端不存在该接口，未实现 | 有差异（后端无此接口） |
| 契约 §2.3 `GET /api/platforms/{name}/accounts` 分页账号表 | 后端不存在该接口；账号数据走 `GET /api/accounts`（S25） | 有差异（后端无此接口） |
| 契约 §二「点击列头排序」 | 跑分表固定按后端 rank 排序；卡片无排序需求 | 有差异（P4 未要求） |
| 契约 §八.8「删除/导入前确认」 | 禁用平台、删除渠道类操作走 `useConfirm`；导入在抽屉内二次确认（开始导入） | 已实现 |
| 单元测试（真实响应形状 → 卡片/动作/扫描计划） | `admin/ui/tests/page-platforms.spec.ts`（14 例） | 已实现 |

