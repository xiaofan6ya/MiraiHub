# 功能契约：分组管理页（Groups）

**页面 key**：`groups`　**路由**：`/groups`（hash：`/admin/v2/#/groups`）　**设计稿**：`06-pages-d.md` P16　**模板**：B（列表管理型）
**旧版位置**：`admin/console/index.html` `#panel-groups`（2031–2056 行）+ `console-app.js`（17174–17356 行：`loadGroups / renderGroups / openGroupEditor / renderGeModels / geToggle / geSelectAll / saveGroup / deleteGroup`）
**后端**：`admin/routers/groups.py`（`list_groups` / `create_group` / `update_group` / `delete_group`），模型候选来自 `admin/routers/models.py:list_configs`（`GET /api/models/configs`）。
**新版文件**：`src/pages/GroupsPage.vue`、`src/api/groups.ts`、`tests/page-groups.spec.ts`。

分组是「模型范围」的授权单位：API Key 不选分组 = 可用全部启用模型；选了分组 = 只能调用组内模型，越界返回 403 `model_not_in_group`（`admin/routers/groups.py` 模块文档串）。

---

## 一、区块（按旧版出现顺序）

| # | 旧版区块 | 旧版中文标题/文案 | 新版位置 |
|---|---|---|---|
| 1 | 工具栏左 | 「模型分组」 | `PageHeader` 标题「分组管理」（`PAGE_TITLES.groups`）+ 说明气泡 |
| 2 | 工具栏右 | 「新建分组」 | `PageHeader #actions`：`刷新`（quiet）+ `新建分组`（primary） |
| 3 | 页面说明 | 「分组用于限定 API Key 可用的模型范围。创建 Key 时选择分组即可；不选分组表示可用全部启用模型。绑定分组后该 Key 只能调用组内模型，调用组外模型返回 403（model_not_in_group）。」 | 收进 `PageHeader` 的 `help`（HelpTip），不占主视线（01-direction §8） |
| 4 | 表格 | 分组名称 / 模型数 / 绑定 Key / 备注 / 操作 | `Card flush` 内 `FilterBar` + `DataTable` |
| 5 | 编辑弹窗 | 「新建分组 / 编辑分组」+ 分组名称、备注、模型列表（搜索、「全选 / 清空」、按平台分段展示勾选框） | `FormDrawer size="lg"`：分组名、模型多选（`NTransfer`，源列表可搜索）、备注 |

> 设计稿 P16 未列旧版「模型数」列，改为「包含模型」列：chips 最多 5 个 + `+N`，chip 内容即模型 id；`model_count` / `effective_count` 的差额以 `Badge warn`「N 个已停用」表达（旧版同一语义的琥珀小标签）。

---

## 二、数据展示

### 2.1 表格列（`DataTable`）

| 列 | 中文标签 | 来源字段 | 格式 / 着色 | 排序 | 手机卡片位 |
|---|---|---|---|---|---|
| 1 | 分组名 | `items[].name` | 正文、500 字重；长名省略 + `title` 悬浮全文 | 升/降 | `title` |
| 2 | 包含模型 | `items[].models`（原始字符串数组） | `Chip`（等宽）最多 5 个 + `+N`（`+N` 的 `title` 列出剩余模型）；`staleCount = model_count - effective_count > 0` 时追加 `Badge tone="warn"`「N 个已停用」（`title`：部分模型已停用，实际可用 `effective_count` 个） | 按 `model_count` 升/降 | `meta` |
| 3 | Key 数 | `items[].key_count` | 整数千分位、右对齐、等宽；`0` 用 `--fg-faint`（是真实 0，不是无数据，不显示 `—`） | 升/降 | `meta` |
| 4 | 备注 | `items[].note` | 单行省略 + `title`；空值显示 `—`（`--fg-faint`） | — | `subtitle` |
| 5 | 操作 | — | `RowActions`：`编辑` 直接显示；`删除`（danger）收进「⋯」菜单，朱色、上方分隔线（`max=1`） | — | `actions` |

- 行键：`String(items[].id)`（新建后 id 稳定）。
- 默认顺序 = 后端顺序（`ModelGroup.id DESC`，最新在前）；「排序」为空时保持后端顺序。
- 排序三态由 `DataTable` 表头 + `sortOrder` 受控列表达（升序 → 降序 → 取消），与旧版模型页一致。
- 时间字段 `created_at` 本页不展示（P16 未要求），保存在行数据里备后续「按时间排序」；契约不承诺 UI 呈现。

### 2.2 筛选栏（`FilterBar`）

| 控件 | 中文标签 | 路由 query | 取值 | 语义 |
|---|---|---|---|---|
| 搜索 | 搜索分组 / 模型 | `q` | 文本（300ms 防抖） | 大小写不敏感，匹配 `name`、`note`、`models[]` |
| 模型状态 | 模型 | `models` | `''`（全部）/ `stale` / `ready` | `stale`：`model_count > effective_count`（组内有已停用模型）；`ready`：`model_count > 0` 且无停用 |
| Key 绑定 | Key | `keys` | `''`（全部）/ `bound` / `unbound` | `bound`：`key_count > 0`；`unbound`：`key_count === 0` |

- 「已筛选」chips：每个生效项一个可关闭 chip（`搜索` / `模型` / `Key`）；`remove` 即清空该筛选项。
- 右侧统计文字：`共 N 个分组 · 覆盖 M 个模型 · 绑定 K 个 Key`（`N/M/K` 取**筛选后**集合；有筛选时前缀「筛选出 N / 共 T 个分组」）。
- 「重置」清空全部筛选项并回到第 1 页。
- 分页 / 排序 / 筛选全部经 `useTableQuery('groups')` 写入路由 query（`page`、`size`、`sort`、`order`、`q`、`models`、`keys`）；刷新、前进/后退、分享链接都还原。每页条数 20/50/100 另存 `localStorage['mh.pageSize.groups']`。

### 2.3 四种状态

| 状态 | 表现 |
|---|---|
| 首次加载 | `Card` 表格区渲染 6 行 `NSkeleton`（灰块），不闪屏；`aria-busy="true"` |
| 刷新中 | 保留旧数据；`DataTable` 顶部 2px 进度条；`刷新` 按钮转圈 |
| 空 | 无任何分组：`Empty`「还没有分组。新建分组，从已启用模型里挑几个。」+ `新建分组`；有筛选但无命中：`Empty`「没有符合条件的分组。」+ `清除筛选` |
| 错误 | 首次加载失败：`ErrorBlock`（标题「加载分组失败：<原因>」，`detail` 可展开，`重试`）；已有数据时刷新失败：保留旧数据 + 错误 Toast「刷新分组失败：<原因>」，不清空表格 |

---

## 三、交互

### 3.1 页头

| 动作 | 中文文案 | 行为 | 成功 / 失败 |
|---|---|---|---|
| 刷新 | `刷新` | `GET /api/groups`，保留筛选/分页/排序 | 失败：错误 Toast + 旧数据保留 |
| 新建 | `新建分组` | 打开空表单抽屉（模型目录按需拉取） | — |

### 3.2 行操作

| 动作 | 中文文案 | 行为 | 成功 / 失败 |
|---|---|---|---|
| 编辑 | `编辑` | 打开抽屉，回填 `name` / `note` / `models`（见 §3.4 的 id 归一） | 模型目录拉取失败时抽屉内 `ErrorBlock` + 「重试」 |
| 删除 | `删除` | `useConfirm`：标题「删除分组「X」？」；正文按 `key_count`：「删除后无法恢复；绑定该分组的 N 个 Key 会自动变为「不限制模型」。」/「删除后无法恢复。该分组当前没有绑定 Key。」；按钮 `取消` / `删除`（danger） | 成功 Toast：「已删除分组「X」」；`unbound_keys > 0` 时「已删除分组「X」，N 个 Key 已改为不限制模型」。失败 Toast：「删除分组失败：<原因>」 |

### 3.3 批量操作

多选（首列复选框）后表格上方出现 `BulkBar`：「已选 N 项」+ `批量删除`（danger）+ `取消选择`。

| 动作 | 中文文案 | 确认 | 成功 / 失败 |
|---|---|---|---|
| 批量删除 | `批量删除` | 标题「删除 N 个分组？」；正文说明不可恢复与 Key 自动解绑数量；`requireText: '删除'`（高风险批量操作，04-components §3） | 全部成功：「已删除 N 个分组」；部分失败：「已删除 A 个分组，B 个删除失败：<首个原因>」（后端只有单条 DELETE，逐条串行调用） |

删除成功后清空选择、重载列表；若当前页越界则回退到有效页。

### 3.4 表单抽屉（`FormDrawer size="lg"`，`FormField`）

| 字段 | 控件 | 必填 | 校验 / 说明 |
|---|---|---|---|
| 分组名 | `NInput`（placeholder「例如：便宜模型组」，maxlength 64） | 是 | 去空白后非空；与现有分组重名（大小写敏感、与后端 `name ==` 一致）时前端先报「分组「X」已存在」，后端仍会以 409 兜底；后端另有 `clean_display_name` 清洗 |
| 模型 | `NTransfer`（source filterable，左「全部启用模型」右「已选 N 个」） | 是 | 只列 `enabled` 的模型；`filter` 同时匹配平台名、平台 id、可读模型名、上游模型 id；选项 label 形如 `Qoder 中国版 / Qoder 国际版 · deepseek-flash-qoder · 免费`；按平台名 + 模型名排序 |
| 备注 | `NInput`（placeholder「可选」，maxlength 200） | 否 | 去空白后提交 |

- 底部固定操作栏：`取消` + `创建分组` / `保存`；提交中按钮 loading 且禁用。
- 校验失败：`FormField error` 朱色文案（分组名/模型下方），不发请求。
- 提交成功：Toast「已创建分组「X」」/「已保存分组「X」」，关闭抽屉，重载列表（新建后回第 1 页）。
- 提交失败：Toast「创建分组失败：<原因>」/「保存分组失败：<原因>」（原因取后端 `detail`，如 409「分组「X」已存在」），抽屉保持打开、已填内容不丢。
- 模型目录（`GET /api/models/configs`）：首次打开抽屉时加载，之后复用内存缓存；失败时抽屉内显示 `ErrorBlock` +「重试」，不阻塞分组名/备注输入。
- 抽屉内提示：「只列出已启用的模型。分组保存的是上游模型 id，停用模型会从可用范围自动排除。」；若选中项里存在「目录中找不到」或「已停用」的模型，显示 `Badge warn`「N 个模型不在启用目录」+ HelpTip 列出 id，保存时**原样保留**（见 §6 边界）。

### 3.5 模型 id 归一（与旧版的关键差异）

- 后端 `ModelGroupItem.model_id` 与 `ModelConfig.model_id`（`/api/models/configs` 响应中的 `upstream_id`）做**精确**集合匹配，并由 `_normalized_members()` 做「同平台同区、大小写不敏感」的归一（`admin/routers/groups.py:57`）。
- 旧版 `openGroupEditor` 提交的是 `items[].model_id`（**可读调用名**，如 `deepseek-flash-qoder`），与库里的上游 id（`dfmodel`）不同 → 分组可能整组失效（`effective_count = 0`，调用 403）。
- 新版：**提交 `upstream_id`**；显示 `model_id`（可读名）。编辑旧数据时按 `upstream_id` → `model_id`（精确，再退化为大小写不敏感）解析回上游 id；解析不到的原样保留并标注，避免「打开旧分组再保存」悄悄改坏授权范围。

---

## 四、接口

### 4.1 `GET /api/groups` — 分组列表

- 后端：`admin/routers/groups.py:list_groups`（`@router.get("")`）。
- 参数：无（不读 `X-Platform`；分组是全局的）。
- 调用时机：进入页面、点「刷新」、新建/编辑/删除成功后。
- 响应（`items` 按 `ModelGroup.id DESC`）：

```json
{
  "items": [
    {
      "id": 7,
      "name": "便宜模型组",
      "note": "只放免费模型",
      "models": ["glm-5.3", "hy3", "dfmodel"],
      "model_count": 3,
      "effective_count": 2,
      "key_count": 3,
      "created_at": "2026-11-20T09:12:00"
    }
  ]
}
```

| 字段 | 类型 | 含义 | 新版用途 |
|---|---|---|---|
| `id` | int | 主键 | 行键、编辑/删除路径参数 |
| `name` | string | 分组名 | 列 1、表单 |
| `note` | string | 备注（`NULL` 时为 `""`） | 列 4、表单 |
| `models` | string[] | 组内**原始**模型 id（`ModelGroupItem`，按 id 升序） | 列 2 chips；编辑时回填 |
| `model_count` | int | `len(models)` | 列 2 排序、`+N` 计算 |
| `effective_count` | int | 组内 ∩ 当前启用模型（已归一） | `Badge warn`「已停用」 |
| `key_count` | int | 绑定该分组的 `ApiKey` 数 | 列 3、删除确认文案 |
| `created_at` | string \| null | ISO 时间 | 行数据保留（本页不展示） |

> 后端**没有** `q / page / size / sort` 参数，也没有分页结构：一次返回全部。新版在客户端做筛选/排序/分页，路由 query 只表达视图状态，不发给后端（契约不承诺后端分页）。

### 4.2 `POST /api/groups` — 新建

- 后端：`admin/routers/groups.py:create_group`。
- 请求体（`GroupIn`）：`{ "name": string, "note": string, "models": string[] }`；`models` 去重保序。
- 响应：`{ "id": number, "name": string, "model_count": number, "ok": true }`。
- 错误：400「分组名称不能为空」/「请至少选择一个模型」；409「分组「X」已存在」。

### 4.3 `PUT /api/groups/{id}` — 编辑

- 后端：`admin/routers/groups.py:update_group`。`models` 为**全量覆盖**语义（传什么就是什么），因此新版必须回填并保留全部原值（含目录中已停用/找不到的 id）。
- 请求体同 4.2；响应 `{ "id": number, "ok": true }`。
- 错误：400 / 404「分组不存在」/ 409。

### 4.4 `DELETE /api/groups/{id}` — 删除

- 后端：`admin/routers/groups.py:delete_group`；绑定该分组的 Key 的 `group_id` 置 0（不限制模型），返回 `unbound_keys`。
- 响应：`{ "id": number, "ok": true, "unbound_keys": number }`。
- 错误：404「分组不存在」。

### 4.5 `GET /api/models/configs` — 模型候选（抽屉用）

- 后端：`admin/routers/models.py:list_configs`（`@router.get("/configs")`）。
- 参数：本页不传（要全量目录）；后端另支持 `level` / `platform` 过滤，本页不用。
- 响应（本页只消费 `items`，其余字段留给 S12 模型配置页）：

```json
{
  "official_total": 42,
  "platforms": [{ "platform": "qoder", "label": "Qoder 中国版", "count": 3, "enabled": 3 }],
  "items": [
    {
      "id": 2,
      "model_id": "deepseek-flash-qoder",
      "upstream_id": "dfmodel",
      "client_id": "deepseek-flash-qoder",
      "platform": "qoder",
      "platform_label": "Qoder 中国版",
      "realm": "cn",
      "enabled": true,
      "is_free": false,
      "credit_multiplier": 1.0
    }
  ]
}
```

| 字段 | 类型 | 含义 | 新版用途 |
|---|---|---|---|
| `model_id` | string | 可读调用名（`/v1/models` 口径） | 穿梭框 label |
| `upstream_id` | string | 上游原生 id（= `ModelConfig.model_id`） | **穿梭框 value / 提交值** |
| `platform` / `platform_label` | string | 区级平台 id / 平台名 | label 前缀、搜索 |
| `realm` | string | `cn` / `intl` / `""` | 平台名已含区域，本页不单列 |
| `enabled` | bool | 是否启用 | 过滤候选（只列启用；已选中的停用项保留） |
| `is_free` | bool | 本站不计费（倍率 = 0） | label 追加「免费」 |
| `credit_multiplier` | number \| null | 倍率（原样） | 本页不展示（留给 S12） |

- 调用时机：首次打开新建/编辑抽屉；缓存到页面内存（刷新页面或点抽屉内「重试」时重取）。

---

## 五、跨页联动

| 联动 | 说明 |
|---|---|
| API Keys（`/keys`，S14） | 创建/编辑 Key 时选择分组（`group_id`）；本页删除分组后，Key 自动变为「不限制模型」——删除成功 Toast 报出 `unbound_keys` |
| 模型配置（`/models`，S12） | 模型被禁用后，本页自动表现为 `effective_count < model_count`（`Badge warn`），无需手工清理分组（后端 `effective_group_models` 动态判定） |
| 平台上下文 | 本页**不随平台切换重载**（`router/pages.ts:PLATFORM_SCOPED_KEYS` 不含 `groups`；`contracts/platform-context.md` §8 同结论），因此不订阅 `usePlatformStore().onChange`；`GET /api/groups` 也不读 `X-Platform` |
| 命令面板 | 本页不在 `08-prompts-pages` 列出的命令面板注册项里（S14/S26 才注册「创建 Key / 添加账号」）；本页只提供页内「新建分组」 |
| 全局事件 | 无（不写 localStorage 业务态，只有表格密度/列显示/每页条数偏好） |

---

## 六、边界情况

1. **空数据**：`items: []` → `Empty` + 「新建分组」；后端允许存在 `model_count = 0` 的历史分组（创建/编辑时要求至少一个模型，但老数据可能为空）→ 该行「包含模型」显示 `—`，编辑保存时仍按后端规则要求至少选一个。
2. **加载 / 错误**：见 §2.3；401 由 `http` 层统一跳登录页（`api/auth.ts`），页面不重复处理。
3. **重名**：前端先查本地列表（大小写敏感，与后端一致），后端 409 兜底；两种来源都提示「分组「X」已存在」。
4. **模型 id 归一**：旧数据可能存可读名（`deepseek-flash-qoder`）或大小写不一致（`DeepSeek-V4.1-Flash`）；编辑时按 `upstream_id` → `model_id` → 大小写不敏感 依次解析；**解析不到的原样保留**（`PUT` 全量覆盖，不能悄悄删掉），UI 标注「未在目录」；「已停用但仍在目录里」的只可移除、不可新增。
5. **同名模型跨平台**：分组只存模型 id 字符串（不含平台），`upstream_id` 相同的跨平台行在穿梭框里**合并为一条**（label 列出全部平台）；这是后端数据模型限制，与旧版一致，不承诺按平台区分授权。
6. **后端无分页/搜索**：数据量大时（>100 分组）仍一次拉全量；筛选/排序/分页均在内存完成（`toGroupRows → filter → sort → paginate` 纯函数链）。
7. **删除竞态**：删除正在编辑的分组不会自动关抽屉（抽屉持有的是快照）；保存时报 404「分组不存在」，Toast 提示并保留表单，用户可改存为新分组。
8. **桌面/手机**：≤640px 由 `DataTable` 自动切卡片模式（列 `mobile` 标注见 §2.1）；抽屉在手机上占满宽度（`NDrawer` 480/640 超宽时退化为全宽），穿梭框高度固定 280px 内部滚动。
9. **深色 / 浅色**：只用 tokens.css 变量，无裸写颜色；状态色只用于状态（`Badge` / 数字着色）。
10. **减少动效**：不新增自发动画；沿用 `DataTable` 进度条与 NDrawer 动效（`prefers-reduced-motion` 已由 tokens.css 全局关闭）。

---

## 七、旧版有但设计稿没写到的功能，以及新版放置

| 旧版功能 | 新版处理 |
|---|---|
| 「模型数」列 + 「N 可用」琥珀标签（`effective_count < model_count`） | 合并进「包含模型」列：chips 后追加 `Badge warn`「N 个已停用」，`title` 给出实际可用数（P16 的列清单不含模型数） |
| 编辑弹窗「全选 / 清空」（仅作用于当前搜索结果） | 由 `NTransfer` 头部「全部勾选 / 清空」承担同等语义（筛选后勾选只影响筛选结果，naive-ui 原生行为）；不再单独做「全选」按钮，避免与穿梭框状态打架 |
| 模型按平台分段小标题 + 平台标签 + 区域标签 + 免费/倍率标签 | 平台前缀写进每个选项 label（`平台 · 模型 · 免费`），平台名已含「中国版/国际版」；倍率不在本页展示（S12 模型配置页负责） |
| 搜索同时匹配平台名 / 平台 id / 模型 id | 保留：`NTransfer` 自定义 `filter` 同时匹配 label、平台 id、可读名、上游 id |
| 页面底部大段说明文字 | 收进 `PageHeader` 的 `help`（HelpTip），文案原样保留 |
| 保存提示「分组已更新」/「分组「X」已创建」 | 统一为「已保存分组「X」」/「已创建分组「X」」（05-patterns §6：Toast 用过去式，动作同名） |
| 删除确认文案（uiConfirm，两行） | 标题问句 + 后果说明 + `danger` 按钮「删除」，并把 `key_count` 代入（§3.2） |
| 无批量操作 | 新增多选 + 批量删除（模板 B 的 BulkBar 规范，供 S10+ 复制），高风险需输入「删除」确认 |

---

## 实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 分组列表（分组名 / 包含模型 chips ≤5+N / Key 数 / 备注 / 操作） | `GroupsPage.vue` 列定义 `columns`；`api/groups.ts:toGroupRows` | 已实现 |
| 停用模型提示（`effective_count < model_count`） | `toGroupRows` 的 `staleCount` + 列 2 `Badge warn` | 已实现 |
| 搜索（名称 / 备注 / 模型，300ms 防抖） | `FilterBar` 搜索框 + `filterGroupRows` | 已实现 |
| 模型状态筛选（含已停用 / 全部可用） | `FilterBar` 「模型」下拉 + `filterGroupRows` | 已实现 |
| Key 绑定筛选（已绑定 / 未绑定） | `FilterBar` 「Key」下拉 + `filterGroupRows` | 已实现 |
| 排序（分组名 / 包含模型 / Key 数，升→降→默认） | 列 `sortable` + 受控 `sortOrder` + `sortGroupRows` | 已实现 |
| 分页（20/50/100，服务端无分页→客户端） | `paginateGroupRows` + `useTableQuery` | 已实现（差异：后端无分页参数，见 §4.1） |
| 筛选/排序/分页同步路由 query | `useTableQuery('groups')` | 已实现 |
| 首次加载骨架 / 刷新保留旧数据 / 空 / 错误 + 重试 | §2.3 四种状态 | 已实现 |
| 新建分组（分组名 + 模型 + 备注） | `FormDrawer` + `POST /api/groups` | 已实现 |
| 编辑分组（回填 + `PUT` 全量覆盖） | `openEdit` + `buildGroupEditorOptions` | 已实现 |
| 模型多选穿梭框（带搜索、按平台/模型/上游 id 匹配） | `NTransfer` + `transferFilter` | 已实现 |
| 已停用/未知模型原样保留（避免保存时丢授权） | `buildGroupEditorOptions` 的 `unknown`/禁用项保留 + 抽屉提示 | 已实现（旧版无声保留；新版显式标注） |
| 提交模型 id 用上游 id（修正旧版可读名导致分组失效） | `buildGroupEditorOptions` value = `upstream_id` | 已实现（有差异，优于旧版：旧版提交 `model_id`） |
| 删除单个分组（确认 + `unbound_keys` 文案） | `removeOne` + `useConfirm` | 已实现 |
| 批量删除（多选 + `requireText` 确认 + 逐条 DELETE） | `BulkBar` + `bulkDelete` | 已实现（新增能力，旧版无） |
| 失败 Toast「<动作>失败：<原因>」 | `failureText` 统一处理 | 已实现 |
| 平台切换重载 | 不适用（`groups` 不在 `PLATFORM_SCOPED_KEYS`） | 已实现（不订阅 `usePlatformStore`） |
| 手机卡片布局 | 列 `mobile` 标注 | 已实现 |
| 单元测试（行转换 / 筛选 / 排序 / 分页 / 统计 / 模型 id 归一） | `tests/page-groups.spec.ts` | 已实现 |
