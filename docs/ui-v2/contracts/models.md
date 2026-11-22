# 功能契约：模型管理页（Models）

**页面标识**: P8（配置组）  
**入口函数**: `loadModels()`、`renderModelPage(pageNum)`  
**路由**: 侧栏「配置」→「模型管理」，或点击导航栏相应标签

---

## 一、页面整体结构

```
┌─ 工具栏（上方）
│  ├─ 左侧操作按钮组
│  │  ├─ 「从后端同步」（同步所有平台或当前平台的模型）
│  │  ├─ 「全部启用」（批量启用系统级模型）
│  │  └─ 「全部禁用」（批量禁用系统级模型）
│  │
│  └─ 右侧显示控制
│     ├─ 聚合视图切换（同一模型跨平台合并）
│     ├─ 「显示倍率」复选框（在模型名中拼接 credit_multiplier）
│     └─ 「官方同显」复选框（只显示官方客户端可见的模型）
│
├─ 搜索与过滤栏
│  ├─ 搜索框（支持模型 ID / 备注 / 厂商中文名 / 平台 / 能力标签）
│  ├─ 清除按钮（仅搜索有输入时显示）
│  ├─ 结果计数（搜索时显示命中数）
│  └─ 平台过滤芯片（动态显示各平台及计数）
│
├─ 中间屏：模型表格
│  ├─ 表头（可排序）
│  │  ├─ 聚合视图：模型名 / 平台数 / 平均倍率 / 启用数 / 操作
│  │  └─ 平铺视图：模型 ID / 所属平台 / 定价倍数 / 启用状态 / 能力标签 / 备注 / 操作
│  │
│  ├─ 表体行
│  │  ├─ 聚合视图：
│  │  │  ├─ 点击模型名行展开悬停详情（各平台版本、id、倍率、启用状态）
│  │  │  └─ 操作：编辑、启用/禁用、删除
│  │  │
│  │  └─ 平铺视图：
│  │     ├─ 模型基本信息（ID、平台、倍率）
│  │     ├─ 状态指示（启用/禁用 toggle）
│  │     ├─ 能力标签展示（supports_images / supports_reasoning / 上下文等）
│  │     └─ 操作列（编辑、启用/禁用、删除、测速）
│  │
│  └─ 分页器（底部）
│
└─ 对话框
   ├─ 编辑模型配置（模型名、定价倍数、描述、能力覆盖）
   ├─ 确认删除（警告框）
   └─ 确认批量操作（启用/禁用全部）
```

---

## 二、数据来源与接口

### 主接口

#### 1. 获取模型配置列表
```http
GET /api/models/configs
```

**参数**: 无（带 `X-Platform` 请求头时返回该平台的配置；不带则返回全部平台）

**返回字段**:
```json
{
  "items": [
    {
      "id": 123,                              // 配置记录 ID（主键）
      "model_id": "deepseek-v4.1-flash",      // 调用时用的模型 ID
      "upstream_id": "deepseek-v4.1-flash",   // 上游原生 ID（可能不同）
      "name": "DeepSeek V4.1 Flash",          // 展示名
      "platform": "workbuddy",                // 对外平台 id（trae-cn / qoder-intl）
      "platform_label": "WorkBuddy",          // 平台展示名
      "credit_multiplier": 1.0,               // 定价倍数（相对基准）
      "enabled": true,                        // 是否启用
      "level": "system",                      // 模型层级（system / custom / beta）
      "description": "...",                   // 备注/描述
      "note": "...",                          // 内部标记
      "tags": ["reasoning", "vision"],        // 能力标签
      "upstream_metadata": "{...}",           // 上游元数据 JSON 字符串
      "official_visible": true,               // 官方前端是否显示
      "model_type": "text",                   // 模型类型（text / image / video）
      "created_at": "2026-11-01T12:00:00Z",
      "updated_at": "2026-11-23T10:30:00Z"
    },
    // ... 更多模型
  ],
  "official_total": 42        // 官方前端展示的模型总数（用于计算隐藏数）
}
```

#### 2. 获取显示偏好设置
```http
GET /api/models/display
```

**返回字段**:
```json
{
  "aggregate": false,                 // 是否开启聚合视图
  "show_multiplier": true,            // 是否在名称中显示倍率
  "official_only": false              // 是否只显示官方同显的模型
}
```

#### 3. 获取模型健康状态（性能指标）
```http
GET /api/models/model-health
```

**返回字段**:
```json
{
  "models": {
    "deepseek-v4.1-flash": {
      "busy": 0.45,                   // 繁忙度（0.0 ~ 1.0）
      "latency_p50_ms": 1200,         // 中位延迟
      "latency_p95_ms": 3400,         // 95 分位延迟
      "last_updated": 1700000000      // 更新时间戳
    },
    // ...
  }
}
```

#### 4. 获取模型描述文本
```http
GET /api/models/descriptions
```

**返回字段**:
```json
{
  "descriptions": {
    "deepseek-v4.1-flash": "高效推理模型，适合代码生成",
    // ...
  }
}
```

### 编辑接口

#### 编辑/启用/禁用单个模型
```http
PATCH /api/models/{id}
```

**请求体**:
```json
{
  "enabled": true,                    // 更新启用状态（可选）
  "credit_multiplier": 1.5,           // 更新倍率（可选）
  "name": "新显示名",                 // 更新显示名（可选）
  "description": "新描述",            // 更新描述（可选）
  "note": "内部标记",                 // 更新备注（可选）
  "upstream_metadata": "{...}"        // 更新上游元数据（可选）
}
```

**返回**:
```json
{
  "id": 123,
  "model_id": "...",
  // ... 更新后的完整字段
}
```

#### 删除模型配置
```http
DELETE /api/models/{id}
```

**返回**:
```json
{
  "deleted": true,
  "id": 123
}
```

#### 批量启用/禁用
```http
POST /api/models/batch-toggle
```

**请求体**:
```json
{
  "enabled": true,                    // true=启用，false=禁用
  "level": "system"                   // 目标层级（system / custom / beta）
}
```

**返回**:
```json
{
  "updated": 42,                      // 更新的模型数
  "failed": 0
}
```

#### 从后端同步模型
```http
POST /api/models/sync?platform={storage_platform}
```

**参数**:
- `platform`（可选）：只同步某一个平台（如 `workbuddy` / `trae` / `qoder`），不传则同步全部

**返回**:
```json
{
  "added": 5,                         // 新增数
  "updated": 3,                       // 更新数
  "total_in_db": 42,                  // 数据库内总数
  "platforms": {                      // 各平台同步结果
    "workbuddy": {"added": 2, "updated": 1},
    "trae": {"added": 2, "updated": 2},
    "qoder": {"added": 1, "updated": 0}
  },
  "problems": {                       // 未成功的平台
    "unknown-platform": "平台配置错误"
  }
}
```

#### 模型测速
```http
POST /api/models/speed-test
```

**请求体**:
```json
{
  "model": "deepseek-v4.1-flash"      // 模型 ID
}
```

**返回**:
```json
{
  "model": "deepseek-v4.1-flash",
  "account": "account-123",           // 使用的账号
  "ttft_ms": 450,                     // 首字延迟（Time to First Token）
  "speed_tps": 125.5,                 // 生成速度（Tokens Per Second）
  "prompt_tokens": 150,               // 输入 token 数
  "completion_tokens": 500,           // 输出 token 数
  "total_tokens": 650,
  "timestamp": 1700000000
}
```

#### 保存显示偏好
```http
POST /api/models/display
```

**请求体**:
```json
{
  "aggregate": true,                  // 聚合视图
  "show_multiplier": false,           // 显示倍率
  "official_only": true               // 官方同显
}
```

---

## 三、数据展示规范

### 3.1 数字格式化

#### 定价倍数（`fmtMultiplier(val)`）
- 规则：保留 2 位小数，显示为 `1.50x` 或 `0.80x`
- 着色：倍数 > 1.0 显示黄色（相对昂贵）；= 1.0 显示默认；< 1.0 显示绿色（优惠）

#### 繁忙度（`fmtBusy(ratio)`）
- 规则：百分比显示，0-100%，保留整数
- 着色：
  - 绿色（`ok`）：≤ 30%
  - 黄色（`warn`）：30% < ratio ≤ 70%
  - 红色（`danger`）：> 70%

#### 延迟（`fmtLatency(ms)`）
- 规则：保留 0 位小数，单位 ms，如 `1200 ms` 或 `1.2 s`（> 1000 ms 转秒）
- 着色：自定义阈值（待后端反馈）

#### Token 数（`fmtTokens(num)`）
- 规则：≥ 1000 时用缩写（如 `1.2K` / `12.3M`），否则显示原数

### 3.2 能力标签展示

从 `upstream_metadata` 中解析，显示为紧凑的文字/图标标签：

| 能力 | 标签 | 颜色 | 条件 |
|-----|------|------|------|
| 图片输入 | `🖼 图` / `image` | — | `supports_images: true` |
| 推理 | `💭 推理` / `reasoning` | 蓝色 | `supports_reasoning: true` |
| 工具调用 | `🔧 工具` / `tools` | — | `supports_tools: true` |
| 上下文 | `📚 256K` | — | 显示 `context_window` |
| 推理档位 | `low/med/high` | — | 显示 `reasoning_efforts` 中的可用档位 |

### 3.3 着色规则

#### 启用状态
- 绿色（`ok`）：已启用 (`enabled: true`)
- 灰色（`disabled`）：已禁用 (`enabled: false`)

#### 平台标签
- 各平台使用预定义颜色（WorkBuddy / Trae / Qoder / 等）
- 组件库 Platform Badge 维护颜色映射

#### 倍数着色（见 3.1）

---

## 四、交互与行为

### 4.1 页面加载

**首次进入**:
1. 显示加载状态（骨架屏）
2. 并行调用：`GET /api/models/configs`、`GET /api/models/display`
3. 读取 localStorage：`wb_model_page`（上次停留页码）、`wb_model_sort`（排序字段）
4. 根据 `display.aggregate` 决定渲染聚合/平铺视图
5. 异步加载 `model-health`、`descriptions`

**平台切换**（通过 `wb-platform-change` 事件）:
1. 触发 `loadModels()`
2. 表格重新过滤至当前平台（平铺视图）
3. 聚合视图维持跨平台展示

### 4.2 操作

#### 搜索与过滤

**搜索匹配范围**:
- 模型 ID（`model_id`、`upstream_id`、`modelClientId`）
- 平台（`platform`、`platform_label`）
- 备注（`note`、`description`）
- 能力标签（`tags` 数组、解析的元数据）
- 厂商中文名（豆包、通义、混元等）
- 能力名（如搜 "推理" 匹配 `supports_reasoning` 模型）

**行为**:
1. 输入时实时过滤（防抖 300ms）
2. 显示命中数（如 "12 个结果"）
3. 清除按钮自动出现/隐藏
4. 若同时有搜索和平台过滤，两者交集

#### 平台过滤芯片

**行为**:
1. 从表数据动态生成（仅平铺视图）
2. 点击切换过滤（单选）
3. 显示各平台模型计数
4. 聚合视图下不显示

#### 排序

**可排序列**（平铺视图）:
- 模型 ID
- 定价倍数
- 创建时间
- 更新时间
- 启用状态

**行为**:
1. 点击列头切换 asc / desc / default（无排序）
2. 记录到 localStorage（`wb_model_sort`）
3. 分页时保持排序

#### 分页

**每页行数**: 50 行（PS = 50）

**行为**:
1. 底部显示总数、当前页、总页数
2. 记录当前页到 localStorage（`wb_model_page`）
3. 翻页时保持搜索/过滤条件

#### 聚合/平铺视图切换

**切换时**:
1. 保存选择到 `display` 表（通过 `POST /api/models/display`）
2. 重新渲染表格（表头、数据行不同）
3. 聚合视图下隐藏平台过滤芯片和排序

**聚合视图悬停详情**:
```
DeepSeek V4.1 Flash（2 个平台）

✓ workbuddy: deepseek-v4.1-flash (1.0x, 已启用)
✗ trae-cn:   deepseek-v4.1-flash (1.2x, 已禁用)
```

#### 启用/禁用操作

**单个操作**:
1. 点击行的 toggle / 按钮
2. 调用 `PATCH /api/models/{id}` 更新 `enabled` 字段
3. 成功后立即刷新表格（调用 `loadModels()`）

**批量操作**:
1. 点击「全部启用」/ 「全部禁用」按钮
2. 显示确认对话框（内容提示危险等级）
3. 调用 `POST /api/models/batch-toggle` 指定 `level: "system"`
4. 显示操作结果（如 "已启用 42 个模型"）
5. 刷新表格

#### 编辑模型配置

**打开编辑对话框**:
1. 点击「编辑」按钮
2. 加载当前模型数据到表单
3. 显示字段：
   - 模型 ID（只读）
   - 显示名（可编辑）
   - 定价倍数（可编辑）
   - 描述（可编辑）
   - 能力覆盖：
     - 支持图片（checkbox）
     - 支持推理（checkbox）
     - 上下文窗口（数字输入，显示原生值）
     - 推理档位（多选或标签）

**保存**:
1. 调用 `PATCH /api/models/{id}` 提交改动
2. 验证倍率 ≥ 0.1 且 ≤ 10.0
3. 成功后关闭对话框，刷新表格

#### 删除操作

**流程**:
1. 点击「删除」按钮
2. 显示确认对话框（`kind: "danger"`）：「确认移除该模型配置？移除后该模型将被过滤。」
3. 确认后调用 `DELETE /api/models/{id}`
4. 成功提示 toast：「已移除」
5. 刷新表格（可能导致页码变化）

#### 测速

**流程**:
1. 点击「测速」按钮（模型行的快速操作）
2. 按钮显示加载动画，禁用
3. 调用 `POST /api/models/speed-test`
4. 成功显示 toast：`<b>deepseek-v4.1-flash</b> 测速：首字 450ms · 提示 150 / 补全 500 tok · 125.5 t/s · 账号 account-123`
5. 若 `model-health` 已加载，更新该模型的繁忙度/延迟显示

#### 从后端同步

**流程**:
1. 点击「从后端同步」
2. 显示 toast：「正在同步 WorkBuddy 的模型...」（若指定平台）或「正在同步全部平台的模型...」
3. 调用 `POST /api/models/sync?platform={storage_platform}`
4. 解析返回结果：
   - 成功：展示 `+5 个新增，~3 个更新，共 42 个`；若有问题平台显示「未取到：qoder-intl」
   - 失败：显示错误提示
5. 刷新表格
6. 触发 `loadShareInfo()` 更新 API Keys 页的可用模型列表

### 4.3 显示控制

#### 聚合视图切换
```
<label>
  <input id="modelAggregate" type="checkbox" onchange="toggleModelAggregate()" />
  聚合视图
</label>
```

**Tooltip**: 「把相同模型的不同平台版本合并显示」

#### 显示倍率
```
<label>
  <input id="modelShowMultiplier" type="checkbox" checked onchange="saveModelDisplay()" />
  显示倍率
</label>
```

**效果**: 模型名后显示倍数，如 "DeepSeek V4.1 Flash (1.0x)"

#### 官方同显
```
<label>
  <input id="modelOfficialOnly" type="checkbox" onchange="saveModelDisplay()" />
  官方同显
</label>
```

**效果**: 只显示 `official_visible: true` 的模型；隐藏的数量显示在表格下方

---

## 五、跨页联动

### 5.1 平台上下文
- 侧栏选择平台时触发 `wb-platform-change` 事件
- 本页监听并自动切换到该平台（平铺视图时）
- 聚合视图保持全平台显示

### 5.2 导航跳转
- 点击模型行的平台标签（平铺视图）→ 保持本页，只过滤该平台
- 表格操作后自动刷新，页码/排序保持

### 5.3 全局事件
- 模型启用状态变化时，侧栏或其他页面（API Keys 等）可能需要更新可用模型列表
- 通过 `loadShareInfo()` 刷新共享信息

---

## 六、边界情况与容错

### 6.1 数据缺失或异常

| 情况 | 处理 |
|------|------|
| 模型无上游元数据 | 能力标签为空，不显示；使用兜底值（128K 上下文）|
| 倍数为 0 或负数 | 显示异常警告，禁止编辑保存 |
| 平台标签缺失 | 显示平台代码名 |
| 能力字段为 null | 该能力标签不显示（无对应 checkbox） |
| 搜索结果为空 | 显示「没有匹配「xxx」的模型」 |
| 官方同显隐藏部分模型 | 表格下显示「已按官方同显隐藏 N 个旧模型」|
| 数据库无模型配置 | 显示「还没有模型配置，点击「从后端同步」拉取」 |

### 6.2 加载态与错误

**加载中**:
- 表格显示骨架屏（灰色占位符行）
- 工具栏按钮显示 loading 动画

**请求失败**:
- 显示 toast 错误提示（如「数据加载失败，请重试」）
- 保留上次成功的表格数据（若有）
- 表格底部显示「重试」按钮

**响应超时**（> 5 秒）:
- 显示降级视图（上次缓存数据或仅显示表头）
- 后台继续重试
- 收到响应后自动更新

### 6.3 空状态

| 场景 | 显示 |
|------|------|
| 账户被禁用 | 顶部显示横幅「账户已禁用」，表格不可操作 |
| 无平台可用 | 显示「没有可用平台，请联系管理员」 |
| 新账户 | 显示「暂无模型配置，点击「从后端同步」拉取」 |
| 搜索无结果 | 显示「没有匹配「xxx」的模型」 |
| 官方同显下无模型 | 显示「本页无模型」，显示隐藏数 |

### 6.4 功能开关与平台差异

**平台差异**:
- 某些平台可能不支持「推理」功能：对应模型不显示推理相关标签
- 某些平台的能力元数据不完整：使用兜底规则推断

**端差异**（PC vs 移动）:
- **PC**: 表格横向排列，所有列完整显示
- **移动**: 表格转为卡片形式（由 `refreshMobileCardsSoon()` 处理）
  - 每行变成一张卡片
  - 操作按钮纵向堆叠
  - 能力标签横向排列（可滚动）

---

## 七、实现要点

1. **缓存策略**
   - 模型配置缓存 10 秒（后端 `_TTL = 10.0`）
   - 本地 localStorage 记录：页码、排序、显示偏好
   - 切平台时不清缓存（允许多页签操作）

2. **性能优化**
   - 分页显示（PS = 50），表格不超过一屏
   - `model-health`、`descriptions` 异步加载，不阻塞主表
   - 聚合视图的平台明细存在内存，悬停时查询（无额外网络请求）

3. **搜索优化**
   - 搜索项构建：`model_id + modelClientId + upstream_id + note + platform + tags`
   - 匹配时不区分大小写
   - 支持厂商中文名（豆包/通义/混元等）对应英文搜索

4. **排序处理**
   - 聚合视图下由后端处理（前端不排序）
   - 平铺视图前端排序（已加载的分页内容）
   - 保存排序状态到 localStorage

5. **无障碍**:
   - 表头用 `<th scope="col">`，数据行用 `<td>`
   - 能力标签用 `aria-label` 提供完整文字（如 "支持推理")
   - 着色不单依靠颜色，要有文字/图标补充
   - 对话框用 `role="dialog"`，焦点管理正确

6. **国际化**:
   - 所有文案提取到 i18n 配置（「启用」「禁用」「编辑」等）
   - 模型能力标签名称国际化

7. **主题适应**:
   - 所有颜色变量用 CSS Custom Properties（`--c-ok`、`--c-warn` 等）
   - 支持亮/暗主题切换

---

## 八、旧版有但本契约未涉及的功能

| 功能 | 旧版位置 | 新版建议位置 | 状态 |
|------|---------|------------|------|
| 模型分组管理 | 控制台右侧 | 移到「模型分组」页面（P9） | ✓ 待规划 |
| 模型配额设置 | 账号详情页 | 账号额度管理（P14） | ✓ |
| 实时模型健康看板 | 底部滚动条 | 移到「数据看板」（P11） | ⏳ |
| 模型聊天测试 | 模型详情 | 专页工具（P22） | ⏳ |

---

## 九、实现步骤与优先级

### Phase 1（核心，必需）
- [ ] 表格基础（平铺视图、分页、基本字段）
- [ ] 搜索与平台过滤
- [ ] 单个启用/禁用（`PATCH /api/models/{id}`)
- [ ] 删除（`DELETE /api/models/{id}`)
- [ ] 编辑对话框（模型名、倍数、描述）

### Phase 2（扩展，重要）
- [ ] 聚合视图 + 切换
- [ ] 显示控制（倍率、官方同显）
- [ ] 批量启用/禁用
- [ ] 从后端同步（`POST /api/models/sync`)
- [ ] 模型健康状态（`GET /api/models/model-health`)

### Phase 3（优化）
- [ ] 测速功能（`POST /api/models/speed-test`)
- [ ] 能力标签展示与过��
- [ ] 排序持久化
- [ ] 移动端卡片适配
- [ ] 国际化

---

## 十、API 总结表

| 操作 | 端点 | 方法 | 触发 |
|-----|------|------|------|
| 加载表 | `/api/models/configs` | GET | 页面打开、切平台、搜索、过滤 |
| 加载显示设置 | `/api/models/display` | GET | 页面打开 |
| 加载健康状态 | `/api/models/model-health` | GET | 页面打开后（异步） |
| 加载描述 | `/api/models/descriptions` | GET | 页面打开后（异步） |
| 编辑模型 | `/api/models/{id}` | PATCH | 编辑对话框保存 |
| 删除模型 | `/api/models/{id}` | DELETE | 删除确认 |
| 批量启用/禁用 | `/api/models/batch-toggle` | POST | 「全部启用」「全部禁用」按钮 |
| 同步模型 | `/api/models/sync` | POST | 「从后端同步」按钮 |
| 测速 | `/api/models/speed-test` | POST | 「测速」按钮 |
| 保存显示偏好 | `/api/models/display` | POST | 聚合/倍率/官方同显 切换 |
