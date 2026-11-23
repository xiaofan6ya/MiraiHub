# 功能契约：客户端参数（S27 /client）

| 项 | 值 |
|---|---|
| 页面 | 客户端参数（模板 C） |
| 路由 | /client（hash：/admin/v2/#/client） |
| 设计稿 | docs/ui-v2/06-pages-d.md P18（54–64 行） |
| 旧版 | admin/console/index.html #panel-client（2339–2423）；console-app.js 13571–14318 / 14972–15040 |
| 后端 | admin/routers/client_profile.py（GET/PUT/detect/detect-all/reset/export/import）、admin/client_profile.py（effective/saved/sources/runtime_diagnostics/normalize/save/reset）、admin/routers/client_paths.py（手动路径覆盖） |
| 新版交付 | admin/ui/src/pages/ClientPage.vue、admin/ui/src/api/client.ts、admin/ui/tests/page-client.spec.ts |

## 一、功能（7 个区块，左侧锚点目录同名）

1. **扫描位置**（只读）：状态（取值策略 / 本机客户端 / 已保存字段数 / 扫描目录数）、WorkBuddy 数据目录列表、凭据位置（明文/加密 + 账号提示）、上游地址、探测提示与错误；卡片操作「重新探测」。
2. **运行时诊断**（只读 KV）：运行平台、设备令牌（可用/无缓存/失败缓存/不可用）、Node 运行时、Native helper；异常时标题旁 warn Badge「有异常项」；请求头字段组（client_profile / sdk_stainless / account_session / request_trace 的字段名）。
3. **生效中的 User-Agent**（只读 CodeBlock + 复制）：来自 payload.ua（或 detect.ua）；说明 UA 由版本号现拼、整体自定义用环境变量。
4. **版本与归属**（可编辑 12 项，按卡片保存）：desktop_version、cli_version、ide_name、ide_type、product、application_name、turing_channel_id、turing_product_name、commit、release_date_ms、node_version、stainless_package_version。每项带旧版说明；int 字段用数字输入。
5. **桌面事件指纹**（可编辑 9 项，按卡片保存）：fp_product、ext_name、os、arch、os_version、cpu_cores、memory_size、timezone、report_delay。
6. **风控 / 审计请求头**（只读表格：请求头 · 值 · 来源）：档案值（探测/保存/兜底）给出真实值，X-IDE-Name / X-IDE-Type / X-Product / X-IDE-Version 由生效档案派生；SDK / 账号会话 / 调用方链路头显示「—（运行时生成）」并标注来源。
7. **取值来源**（只读，可展开）：每个键当前取自哪一层（环境变量 / 安装包实测 / 本机系统 / 已保存 / 兜底默认 / 合并），默认显示前 10 项，超过后可「展开全部」。

页头操作：探测本机客户端、取值策略（优先现场探测 ↔ 钉住已保存值）、重置（清空保存值，确认框）、刷新。

## 二、数据来源与接口

### 2.1 GET /api/client-profile

后端 client_profile.py:_payload / get_profile。响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| effective | object | 合成后的生效档案（兜底 ← 探测/保存 ← 环境变量；UA 现拼） |
| saved | object | 后台保存的档案（可能为空） |
| detected | object | 本机现场探测值（只含真实读到的键） |
| detected_origins | object | 探测值来自「安装包」还是「本机系统」 |
| detected_package / detected_system | object | 探测明细拆分 |
| sources | object | 每个键当前取自哪一层（排障；含 source_mode） |
| source | auto / saved | 取值策略：优先现场探测 / 钉住已保存值 |
| defaults | object | 内置兜底值（协议兼容参考值，不是实测） |
| has_client | boolean | 本机是否探测到客户端（线上通常 false） |
| diff_saved_vs_effective | object | 保存值与生效值的差异 |
| runtime_diagnostics | object | 见 2.2 |
| dirs | string[] | WorkBuddy 平台上报的数据目录（扫描位置） |
| ua | string | 生效 UA |
| platform | string | 固定 workbuddy（档案是 WorkBuddy 的全局设置） |
| product_version | string | 探测到的桌面端版本 |

### 2.2 runtime_diagnostics（client_profile.py:runtime_diagnostics）

- platform：服务器 sys.platform。
- device_token：{available, source, checked, node_available, helper_available, cached_failure, error}（不含令牌本身）。
- header_groups：{client_profile: string[], sdk_stainless: string[], account_session: string[], request_trace: string[]}。

### 2.3 POST /api/client-profile/detect?platform=

现场探测（只读、不写库）：{ok, platform, detected, ua, dirs, detected_origins, detected_package, detected_system, has_client, runtime_diagnostics, message, errors}。非 WorkBuddy 平台走 platform 自己的 client_probe（本页只展示状态/目录，不展示可编辑字段）。

### 2.4 PUT /api/client-profile

请求体 {profile: object, source?: auto|saved, merge?: boolean}（merge 默认 true：只覆盖传入的键；headers 逐键合并）。后端 normalize 校验：未知键忽略并报错；字符串非空且 ≤512；int 键必须非负整数；请求头名/值 ≤256 且不能含换行。响应 = {ok, errors[], source, ..._payload}。错误：400 detail（校验失败/空 profile 且无 source）。

### 2.5 POST /api/client-profile/reset

清空保存档案并把 source 恢复 auto；响应 {ok, ..._payload}。

### 2.6 admin/routers/client_paths.py（旧版手动路径覆盖）

GET /api/client-paths/{platform} 返回 saved/env_names/sources；PUT 保存（空串=清除该项）；DELETE 清空；POST /refresh 丢弃定位缓存。本页只读展示自动发现的目录；手动覆盖未做（见 §六）。

## 三、交互

| 控件 | 文案 | 调用 | 成功 | 失败 |
|---|---|---|---|---|
| 保存版本与归属 | 「保存版本与归属」 | PUT {profile, merge:true} | Toast「已保存，新值立即生效」；重填基线 | Toast「保存版本与归属失败：<原因>」；前端先校验 int/长度 |
| 保存桌面事件指纹 | 「保存桌面事件指纹」 | 同上 | 同上 | 同上 |
| 卡片取消 | 「取消」 | 无 | Toast「已恢复为当前生效值」 | — |
| 探测本机客户端 / 重新探测 | 「探测本机客户端」/「重新探测」 | POST /detect | 有客户端 → success Toast；无 → warning Toast（含后端 message）；随后刷新档案 | Toast「探测本机客户端失败：<原因>」 |
| 取值策略 | 「取值策略：优先现场探测 / 钉住已保存值」 | PUT {profile:{}, source} | 切换 Toast；重填表单 | Toast「切换取值策略失败：<原因>」 |
| 重置 | 「重置」 | POST /reset | 确认框（danger）→ Toast「已重置」 | Toast「重置客户端参数失败：<原因>」 |
| 刷新 | 「刷新」 | GET /api/client-profile | 保留旧数据、卡片转圈 | Toast「刷新客户端参数失败：<原因>」 |
| 展开取值来源 | 「展开全部（N 项）」/「收起」 | 无 | — | — |

保存按卡片：每张可编辑卡独立 dirty（SettingsCard 标题旁 Badge「未保存」），页面任一卡片脏时 SettingsLayout 显示「有未保存的修改」并在路由离开 / 关页时确认。

## 四、展示规则

- 数值：int 字段直接展示数字；空值统一 —（--fg-faint）。
- 来源徽标：环境变量 warn、安装包/本机系统/现场探测 ok、已保存 brand、兜底/合并 neutral；完整来源放 title。
- 运行时诊断异常（设备令牌不可用 / 无 Node / 无 Native helper）→ 标题旁 warn Badge「有异常项」，行内 Badge 用 warn。
- 请求头来源：档案值 brand、SDK 资料 ok、账号/链路头 neutral。
- 说明文字全部收进 HelpTip 或卡片底部 12px note（旧版大段灰字不占主视线）。

## 五、跨页联动与平台上下文

- 平台上下文：客户端档案是 **WorkBuddy 的全局设置**（GET 的 platform 参数目前不影响返回内容）。页面订阅 usePlatformStore().onChange 重取档案（卸载取消），并在「扫描位置」标注当前平台；不在平台切换时替换档案内容（与旧版可切平台只读探测有差异，见 §七）。
- 与 /sync：同步到线上时推送 client_profile.effective（可同步字段）；本页保存后 source 仍是 auto 的话，线上需要 saved 才能钉住 —— 页面用「取值策略」显式提示。
- 与 /accounts：本页不改账号；账号的 UA/版本由请求运行时使用本档案。

## 六、边界情况

| 场景 | 表现 |
|---|---|
| 首次加载 | 骨架卡片（NSkeleton） |
| 加载失败（无数据） | ErrorBlock「加载客户端参数失败」+ detail + 重试 |
| 刷新失败（有旧数据） | 保留旧数据 + Toast |
| 本机未装客户端 | 扫描位置状态「未检测到客户端」；取值策略非 saved 时 HelpTip 已说明会退回兜底版本号 |
| 无扫描目录 / 无凭据位置 | 显示 — / 空列表；不隐藏区块 |
| 没有运行时诊断 | 请求头字段组 Empty；运行时行仍显示「不可用」 |
| 没有请求头字段组 | 请求头表 Empty |
| 表单留空 | 不提交该字段（后端拒绝空串）；保存后重取仍显示旧值 |
| int 字段非法 | 前端拦截并 Toast，不发请求 |
| 字符串超长 | 前端拦截（>512） |
| 保存时后端返回 errors（未知键等） | Toast warning 展示，不阻断已保存的部分 |
| 401 | http 层跳登录 |
| 手机 375px | 表单单列、KV 行上下堆叠、表格卡片模式、隐藏/展开按钮可用 |

## 七、旧版有但本次未做的功能（如实标注）

| 功能 | 旧版位置 | 说明 |
|---|---|---|
| 导出参数 / 导入并启用（JSON 文件） | WBProfileTransfer + /api/client-profile/export/import | 任务未列；后端接口保留，后续可加到页头 |
| 手动路径覆盖（安装/数据目录、config root、exe） | /api/client-paths/{platform} PUT/DELETE + renderClientPathCard | 任务只要求「路径列表 + 重新探测」；手动覆盖未做 |
| 请求头可编辑（增删行） | #clientHeaders（旧版输入框 + 添加/删除） | 设计稿改为「请求头 · 值 · 来源」表格；本页只读展示。旧版 merge 保存也无法删除已保存键，编辑入口建议走导入导出 |
| 平台切换 chip + 非 WorkBuddy 只读探测 | clientPlatformBar / renderClientPlatformFields | 档案是 WorkBuddy 全局设置；本页只在扫描位置标注当前平台。非 WorkBuddy 的通用探测（/detect?platform=）未接 |
| 独立「提示条」卡片 | #clientHint | 内容拆进扫描位置 KV + 页头 HelpTip |

## 实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 扫描位置：路径列表 + 重新探测 + 凭据位置 + 上游地址 + 错误 | ClientPage 扫描位置卡 + scanView | 已实现 |
| 运行时诊断 KV + 异常项 warn Badge + 请求头字段组 | runtimeView + Badge / Section | 已实现 |
| 生效中的 UA：CodeBlock + 复制 + 说明 HelpTip | CodeBlock + UA_HELP | 已实现 |
| 版本与归属：12 项可编辑、按卡片保存、未保存 Badge | VERSION_FIELDS + SettingsCard + saveCard('version') | 已实现 |
| 桌面事件指纹：9 项可编辑、按卡片保存 | FINGERPRINT_FIELDS + saveCard('fingerprint') | 已实现 |
| 风控 / 审计请求头：请求头 · 值 · 来源表格 | headerRows + DataTable + HelpTip | 已实现（只读；旧版可编辑已注明） |
| 取值来源：可展开说明 | sourceRows + 展开/收起 | 已实现 |
| 探测本机客户端 / 取值策略切换 / 重置 / 刷新 | runDetect / toggleSource / resetAll / loadProfile | 已实现 |
| 四态（首次/刷新/空/错误） | 骨架 + Card loading + Empty + ErrorBlock | 已实现 |
| 平台上下文订阅（卸载取消） | usePlatformStore().onChange | 已实现（档案全局，重取不改内容） |
| 手机 375px 单列 | .cl-grid 断点 + KV 行堆叠 + DataTable mobile | 已实现（未截图验证） |
| 导入 / 导出参数 | — | 未实现（任务未列；见 §七） |
| 手动路径覆盖 | — | 未实现（只读展示目录；见 §七） |
| 非 WorkBuddy 平台只读探测 | — | 未实现（档案为 WorkBuddy 全局设置；见 §七） |
