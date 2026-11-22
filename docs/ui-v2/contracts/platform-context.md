# 平台上下文契约（S03）

> 本文只记录**已经核实过**的事实：字段取自后端序列化函数，行为取自旧版
> `admin/console/platform-switch.js` 与 `console-app.js`。前端实现见
> `admin/ui/src/api/platforms.ts`、`admin/ui/src/stores/platform.ts`。

一句话：**切平台 = 换全局数据上下文**。不是"给账号列表加个筛选"——
每个后台请求都带 `X-Platform`，后端按账号归属收窄所有统计口径
（`admin/platform/scope.py` 文件头就是这个约定）。

---

## 1. 平台对象字段表（`GET /api/platforms` → `items[]`）

序列化函数：`admin/platform/router.py:56 _serialize(spec)`；
`spec` 是 `admin/platform/registry.py:35 class PlatformSpec`（含 `Realm`，
`registry.py:22`）。

| 字段 | 类型 | 含义 | 来源（后端函数 / 位置） |
|---|---|---|---|
| `id` | string | 对外平台 id，**同一平台的不同区是不同 item**（如 `trae-cn` / `trae-intl`） | `spec.id` → `_serialize()`，`router.py:57` |
| `label` | string | 界面显示的平台名（中文） | `spec.label`，`registry.py:46` |
| `enabled` | boolean | 平台是否启用（关闭后网关不再调度） | `spec.enabled` → `_serialize()` |
| `supports_local_scan` | boolean | 是否支持"扫描本机"导入 | `spec.supports_local_scan`，`registry.py:52` |
| `auth_mode` | string | 凭证获取方式：`local-scan` / `web-login` / `import-only`。前端据此决定"加号"入口，`import-only` 时**不要**注入授权登录 | `spec.effective_auth_mode()`，`registry.py:134` |
| `default_realm` | string | 该平台默认区域（多区平台回落用） | `spec.default_realm()`，`registry.py:148` |
| `notes` | string | 接入说明（旧版卡片 tooltip 用它） | `spec.notes`，`registry.py:67` |
| `icon` | string | 图标 key；PlatformLogo 在 `platform-icons.js` 的映射里取图，缺图用占位 | `spec.icon or spec.id` → `_serialize()` |
| `storage_platform` | string | **落库平台**：`trae-cn` / `trae-intl` 都落 `trae`。按平台 id 查中文名 / 做平台级判断一律用它 | `spec.storage()`，`registry.py:141` |
| `realm` | string | 单区平台的固定区域（`trae-cn` → `cn`）；多区平台为空串 | `spec.realm`，`registry.py:80` |
| `realm_label` | string | 固定区域的中文名（国内版 / 国际版），仅 `realm` 有值时非空 | `_serialize()` 内联映射 `{cn: 国内版, intl: 国际版}` |
| `realms` | `{key,label,default}[]` | 可切换的区域列表；长度 ≤1 时旧版不显示区域行 | `spec.realms`，`registry.py:48` / `:22` |
| `proxied` | boolean | 是否经本网关代理（与"直连上游"对应） | `bool(spec.gateway)`，`registry.py:57` |
| `rating` | `{tier,reason,scores,source}` | 平台自评等级（"平均级"徽标）；`tier` 只放行 `hang/elite/npc/bad`，未知档位后端直接丢弃（前端回落兜底表） | `_rating_of(spec)`，`router.py:33` |
| `extra` | object | 平台自定义扩展（`actions` / `quota_unit` 的原始来源） | `spec.extra`，`registry.py:132` |
| `managed` | boolean | 是否提供管理契约（账号池 / 任务 / 客户端探测 / 概览卡片）。false 时不显示"管理"入口，避免点进去 404 | `bool(spec.platform_gateway)`，`registry.py:65` |
| `actions` | string[] | 平台能做的动作：`scan` / `import` / `refresh-quota` / `usage` / `web-cookie` / `checkin` / `begin-auth`。**由平台声明，前端不写死** | `(spec.extra or {}).get("actions")`，`router.py:95` |
| `quota_unit` | string | 额度口径：`credits`（积分制，默认）/ `token`（令牌钱包，量级 10^7 要缩写） | `spec.extra.get("quota_unit") or "credits"`，`router.py:98` |
| `quota_unit_label` | string | 额度单位名（"积分" / "Token"）；空则由 `quota_unit` 推 | `spec.extra.get("quota_unit_label") or ""`，`router.py:99` |
| `account_count` | number | 账号数。按**落库平台 + 固定区域**统计：单区平台只算自己那个区，多区平台算全平台 | `list_platforms()` 统计段，`router.py:147-172` |
| `realm_counts` | `{realm: number}` | 每个区域的账号数（区域未知的老账号计入每个区） | `list_platforms()`，`router.py:164-171` |

> `account_count` / `realm_counts` 是**上一次请求的缓存**：导入 / 删除 / 刷新账号后
> 必须重新拉 `/api/platforms`，否则卡片上的数字停在页面打开那一刻（旧版
> `loadCounts(true)` 就是干这个的，2026-10-31 实测踩过）。

## 2. 列表响应外层

| 字段 | 类型 | 含义 | 来源 |
|---|---|---|---|
| `items` | PlatformSpec[] | 平台清单，顺序即后端登记顺序（`DEFAULT_PLATFORM` 恒在首位） | `list_platforms()`，`router.py:131`；排序见 `registry.py:199` |
| `default` | string | 默认平台 id | `registry.DEFAULT_PLATFORM`，`registry.py:18`（= `workbuddy`） |
| `loader` | `{loaded:string[], failed:Record<string,string>}` | 平台模块装配结果；`failed` 非空说明某平台挂载失败（排障用） | `admin/platform/loader.py: status()` |

## 3. 当前平台（服务端记忆）

| 接口 | 方法 | 请求 | 响应 | 后端 |
|---|---|---|---|---|
| `/api/platforms/current` | GET | — | `{platform, realm, raw}`；读 `system_settings['platform:current']`，没有则回落默认平台，并用 `registry.resolve()` 归一化 | `get_current()`，`router.py:180`；`CURRENT_KEY`，`router.py:30` |
| `/api/platforms/current` | PUT | `{platform, realm}` | 成功 `{ok:true, platform, realm, raw}`；**未知平台回 `{ok:false, error}`（HTTP 仍是 200）** | `set_current()`，`router.py:193`；`spec.normalize_realm()`，`registry.py:156` |

注意：这是**按登录会话共享**的单值（落 `system_settings`），多设备会互相覆盖；
页面上的"当前平台"始终以本地 `localStorage` 为准，服务端只用于"下次打开时恢复"。

## 4. 持久化与读取顺序（前端 store）

```
读：localStorage['wb_platform_current'] = {platform, realm}
    → 没有则 GET /api/platforms/current（新版新增，03-shell §2.2 要求两处都读）
    → 再没有则 items[0]（后端保证是 workbuddy）
    校验：platform 必须存在于 items；realm 必须属于该 item 的 realms，否则归零为"整个平台"
写：每次用户切换 setCurrent(id, realm)
    → localStorage（旧版行为）
    → PUT /api/platforms/current（新版新增；失败只告警，不回滚）
```

- 键名 `wb_platform_current` 与旧版 `platform-switch.js:31 STORE_KEY` 完全一致，
  新旧前端共用同一条记录（旧版还在用，不能改名）。
- 旧版**只**写 localStorage；写后端是新版补的，目的是换设备/换浏览器后仍能恢复。
- 读取顺序里"localStorage 优先"是刻意的：它代表用户在这台机器上最后一次明确选择。

## 5. 请求头 `X-Platform`

- 每个后台请求都带，值为**选择器**：`workbuddy` / `trae` / `trae:cn`。
  旧版实现：`console-app.js:525 _apiRaw` + `platform-switch.js route()/header()`。
- 后端解析：`admin/platform/scope.py: from_request()` —— 先看头，再看
  `?platform=` 查询串；`parse()` 归一化成 `(落库平台, 区域)`，
  空值 = 不过滤（保持免登录脚本/curl 的旧行为）。
- 新版实现：`api/http.ts` 通过 `setHeaderProvider()` 向 store 取头；
  store 还没加载时回落直接读 localStorage（与旧版 `_apiRaw` 的兜底同源）。
- 后端真正按它收窄的模块：`overview_router`、`sentinel_router`、`accounts`、
  `credits`（含 `/expiry`、`/usage` 积分流水）、`logs`、`models`、
  `model_market_key`、`stats`；`maintenance` 只在历史回填动作里用它。

## 6. 切换平台后需要重渲染的面板

旧版用 `document` 事件 `wb-platform-change` 通知（`platform-switch.js:221`，
detail = `{platform, realm, spec, selector}`）。监听器实据：

### 6.1 立即重载（`console-app.js:3314-3350`）

| 面板（旧版 tab） | 切平台后调用的函数 |
|---|---|
| 概览 `overview` | `loadOverview(true)`（每次都重拉：数字全带平台作用域） |
| 平台管理 `platforms` | `loadPlatformOverview()` + `loadPlatformBench()` + `loadJoinGate()` |
| 账号池 `accounts` | `renderIntakePlatformLogin()` + `loadAccounts()` |
| 使用记录 `usage` | `loadUsageStats()` |
| 用量日志 `logs` | `openLogsPanel()` |
| 积分到期 `credits` | `loadCreditStats()` |
| API Keys `keys` | `loadKeys()` + `loadShareInfo()` |
| 模型配置 `models` | `resetModelFilter()` + `loadModels()`（**重置**筛选，不能沿用上一个平台的条件） |
| 定时任务 `schedules` | `loadSchedules()` |
| 同步设置 `sync` | `loadSyncConfig()` |

### 6.2 另有独立监听器重画

| 位置 | 作用 |
|---|---|
| `console-app.js:6261` | 账号池顶部"添加与迁移账号"动作区（`renderIntakePlatformLogin()`） |
| `console-app.js:6284` | 账号池页头的平台下拉（`renderAccPlatformSwitch()`） |
| `console-app.js:10609` | 积分面板可见时重拉（`loadCreditStats()`） |

### 6.3 不立即重载、但数据随平台变化的面板

这些页面靠"重新进入页面"或"自己的轮询"取数（`api()` 已带新头），切换时**不会**
立刻刷，旧版就是这个行为：

- 积分流水 `journal`：重新进入时按新平台取（后端 `credits.py` 按平台收窄）。
- 风控哨兵 `sentinel`：自带轮询（`console-app.js:14463`），下一拍自然带上新头。
- 数据看板 `stats`、模型广场 `market`：后端按平台收窄，进入页面时取。

### 6.4 与平台无关的面板

平台网关 `gateway`、分组管理 `groups`、客户端参数 `client`、预警管理 `alerts`、
智能体 `agent`、公开注册入口 `openentry`、设置 `settings`、更新与备份 `maintenance`、
IP 管理 `ip`：切平台不需要重渲（旧版也不监听）。

## 7. 平台专属页面规则

旧版 `console-app.js:1366`：

```
const _PLATFORM_ONLY_TABS = { growth: "workbuddy" };
```

- 判定用**落库平台**（`storage_platform`），所以 `trae-cn` / `trae-intl` 都算 `trae`。
- 平台还没解析出来时（storage 为空）**不隐藏**，避免首屏整行闪一下。
- 停在不支持的页面时切平台：直接落到"账号池"（`console-app.js:3323-3328`，
  `name === 'growth' ? 'accounts' : 默认页`），不要停在永远空着的页面。
- 新前端对应：`usePlatformStore().supports(tabKey)` / `fallbackTab(tabKey)`。

## 8. 顶栏平台选择器的显示范围

03-shell §2.2：选择器只在"依赖平台上下文"的页面显示，其余位置显示面包屑。
据此把路由 `meta.platformScoped` 定为：

| platformScoped | 页面 |
|---|---|
| `true` | overview、sentinel、stats、platforms、accounts、credits、journal、growth、usage、logs、keys、models、market、schedules、sync |
| `false` | gateway、groups、client、alerts、agent、openentry、settings、maintenance、ip |

依据：6.1/6.3 的"数据随平台变化"清单 + 平台专属页面（growth）。
`groups` 虽然管理账号分组，但旧版不随平台刷新、后端也不按平台收窄，归为 false。

## 9. 前端实现映射（S03 交付）

| 能力 | 位置 |
|---|---|
| 平台列表 / 当前平台 HTTP 封装与类型 | `admin/ui/src/api/platforms.ts` |
| 当前平台状态、切换、持久化、事件 | `admin/ui/src/stores/platform.ts` |
| `X-Platform` 注入 | `admin/ui/src/api/http.ts`（`setHeaderProvider`） |
| 401 → 回登录页 | `admin/ui/src/api/auth.ts`（`onUnauthorized` / `consumeNextHash`） |
| 服务状态心跳 | `admin/ui/src/stores/heartbeat.ts` |
| 单元测试 | `admin/ui/tests/platform-store.spec.ts`、`http.spec.ts`、`heartbeat.spec.ts`、`format.spec.ts`、`session.spec.ts`、`composables.spec.ts` |

store 暴露（与旧版 `window.WBPlatform` 对照）：

| store | 旧版 | 说明 |
|---|---|---|
| `platforms` | `list()` | 已加载的 items |
| `current` / `currentId` | `current()` | 返回 `{platform, realm}` / 平台 id |
| `setCurrent(id, realm?)` | `select(platform, realm)` | 返回是否真的切换；同时写 localStorage 与后端 |
| `onChange(cb)` | `onChange(fn)` | 返回取消函数；同时仍派发 `document` 事件供非 Pinia 代码订阅 |
| `reload()` | `load()` / `refreshCounts()` | 重拉 `/api/platforms` 并校正当前平台 |
| `supports(tabKey)` | `_visibleTabsOf / activateTab` 的判定 | 平台专属页面规则 |
| `specOf(id)` / `specFor(id)` | `specOf(id)` | 精确查 / 带回落（落库平台 id 也能查） |
| `selector` / `header()` | `route()` / `header()` | `trae:cn` / `{X-Platform: ...}` |

## 10. 格式化口径（`src/utils/format.ts`）

按 05-patterns §4 实现；与旧版函数逐项对照后的差异（已尽量对齐，剩下的按设计稿）：

| 函数 | 旧版 | 新版 | 说明 |
|---|---|---|---|
| `fmtCompact` | `fmtToken`：1e4 起 K、1 位小数去 `.0`；M/B 用 2 位 | 同阈值，K/M/B 统一 1 位小数 | 设计稿明确"保留 1 位小数"，示例 2.3M / 150.9K 两者一致；只有 2.35M 这类值会显示成 2.4M |
| `fmtInt` | 千分位取整 | 同 | — |
| `fmtCredit` | 汇总取整、单包两位（两个函数） | 一个函数：整数原样、小数最多 2 位 | 设计稿只列了一个 `fmtCredit`（9.55 / 789.31），汇总需要整数时套 `fmtInt` |
| `fmtPct` | 比率 ×100，1 位小数 | 同 | 入参是 0–1（后端 `success_rate` 口径） |
| `fmtDuration` | `845 ms` / `3.2s` / `2分15秒` | `845ms` / `3.95s` / `1m 30s` | 设计稿表格给的是新格式 |
| `fmtTime` | 恒 `YYYY-MM-DD HH:mm:ss`（+8） | 今天→`HH:mm:ss`；今年→`MM-DD HH:mm`；更早→`YYYY-MM-DD HH:mm`；完整时间另用 `fmtFullTime` | 设计稿 §4 明确分级；tooltip 用 `fmtFullTime` |
| `fmtRelative` | 无（旧版另有 `fmtRemaining` 讲"多久后"） | 刚刚 / N 分钟前 / N 小时前 / 昨天 / 日期 | — |
| `fmtExpiry` | `expiryBadge()` 返回 HTML 徽标 | 只返回文案，语义色由 `expiryTone()` 给出 | 组件化后不应该在格式化函数里拼 DOM |
| `maskSecret` | 无统一函数（各处 `slice(0,6)+"…"`） | `sk-mh3…9f2e` | — |

时区口径全站统一：后端存 UTC，展示固定 +8（旧版 `fmtTime` 的做法，与浏览器时区无关）。

## 11. 明确未做的部分

- **侧栏提示点**（账号组冷却/熔断数、哨兵批次封控）：旧版没有对应接口/数据源
  （顶栏 `topbarMode` 是静态文案，见 `stores/heartbeat.ts` 注释），本步不编造。
  等对应页面接入 `/api/accounts/badges`、哨兵接口后由外壳聚合。
- **心跳接口**：旧版没有周期检测；新版沿用旧版启动探针
  `GET /api/accounts/probe`（O(1)，只验凭据）作为 15 秒心跳。
