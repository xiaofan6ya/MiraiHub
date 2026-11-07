# Freebuff 平台接入

把 **Freebuff 的 Developer API** 接成 OpenAI / Anthropic / Responses 三种协议，
并把「账号从哪来、怎么保活、怎么验证、用量看哪」一并补齐到与其它平台同一标准。

参考实现：[`chengyixu/magpie-freebuff`](https://github.com/chengyixu/magpie-freebuff)
（Magpie / OpenCode 插件）。本目录是在 MiraiHub 里**按同一协议重写**的 Python 版 ——
MiraiHub 的平台必须实现 `Gateway` / `AccountOps` / `ClientProbe` 三个契约，
与插件宿主接口不兼容。

---

## 一、上游事实（实测 2026-11-07）

| 用途 | 请求 | 实测 |
|---|---|---|
| 模型目录 | `GET https://freebuff.com/api/v1/paid/models` | **200，不需要鉴权**，280 个模型 |
| 对话 | `POST https://freebuff.com/api/v1/paid/chat/completions` | **401 `invalid_api_key`**（端点存在，缺真 key） |
| 其它 | `/usage` `/credits` `/me` `/balance` `/health` `/billing` `/models/{id}` | 全部 **404**（返回 HTML，端点不存在） |

两个结论直接决定了本平台的设计：

1. **没有余额接口**：`credits()` / `/v1/balance` 只报本地账本与 key 状态，不编数字。
2. **没有校验 key 的轻量端点**：拉目录成功证明不了 key 有效（免鉴权也能拉）。
   所以 `verify_state` 只在**真实对话**成功（`ok`）或被 401 拒绝（`rejected`）
   时翻转 —— 这也正是「每日活跃对话」存在的意义（见第五节）。

目录里每个模型只有 `capabilities.{chat,streaming,toolCalls}`，没有名字和上下文长度，
所以 `core/protocol.py::merge_catalog()` 用内置 17 条元数据补齐，对不上的按插件同一套正则推断。

### 280 个模型里只有 1 个支持流式

实测 `streaming=true` 的只有 `deepseek-v4.1-flash`，其余 279 个是 `false`。所以流式策略是：

```
stream=true + 模型支持流式  → 逐帧透传上游 SSE
stream=true + 模型不支持    → 上游走非流式拿完整答复，本地合成标准 OpenAI SSE 帧
stream=false               → 普通 JSON
```

客户端永远看到合法 SSE（`delta.content` / `finish_reason` / `usage` / `[DONE]` 齐全），
不必关心上游这个模型能不能流；响应头 `X-Freebuff-Stream: synthesized` 标明是本地合成的。

### 踩过的坑：dispatch 会把模型名改成别家的上游 id

MiraiHub 的 dispatch 中间件有一层「slug / 别名还原」，把 `/v1/models` 给客户端的
规范名换回它以为的上游 id。那张映射表是按 workbuddy / qoder 的写法建的 ——
`deepseek-v4-pro` 会被换成 `dmodel`（正是 qoder 的内部代号），而 **Freebuff 的上游 id
本来就是 `deepseek-v4-pro`**，照单全收就会把请求发成不存在的模型。

本平台的处理（`gateway.py::_resolve_model`）：读中间件存下的 `_wb_requested_model`，
并用**自己目录里的 id 集合**校验一遍，命中就用原名。测试里有一条专门锁这个行为。

---

## 二、凭证与授权登录

### 只有一种凭据：Developer API key（`fb_...`）

在 https://freebuff.com/account/api 创建。导入支持：裸 key / `Bearer fb_...` /
`Authorization: Bearer fb_...` / `api_key=...`。

**CLI/Desktop 的会话 token 用不了**：它是 UUID 形状，Freebuff 把免费额度绑定在
它自己那个经过校验的客户端上，第三方程序用会话 token 只会拿到
`410 freebuff_update_required`。本平台在**本地**就识别并拒绝（不浪费一次上游请求）。

### 授权登录：独立浏览器 + CDP 抓 key

`POST /api/freebuff/auth/start` 会用**独立的浏览器用户目录**拉起 Chrome/Edge，
打开账户页；用户正常登录（不用手抄 key），之后由 `GET /api/freebuff/auth/poll`
从浏览器里读出来并自动入库：

* `core/ws.py` —— 标准库实现的极简 WebSocket 客户端（不引入 `websocket-client` 依赖）；
* `core/cdp.py` —— `Network.getAllCookies` + `Runtime.evaluate` 两个能力；
* **为什么要走 CDP**：新版 Chromium 的 Cookie 用 App-Bound Encryption（`v20`），
  密钥绑在浏览器服务进程上，DPAPI + AES-GCM 解不开；CDP 由浏览器自己解密后回话；
* 抓取范围：页面正文 / 表单值与 `data-key` / `localStorage` / `sessionStorage` /
  `document.cookie` + 整页 HTML，正则 `fb_[A-Za-z0-9_\-]{20,}`；
* **打码的 key 不会被抓**（`fb_1a2b…9z8y` 这类）——抓不到就如实返回 `pending`
  并提示「点一次显示/复制」，绝不把半截当 key。

抓不到还有兜底路径：手动复制 → `POST /api/freebuff/import`。

---

## 三、能力清单

| 能力 | 状态 |
|---|---|
| `/v1/chat/completions` | ✅ 支持流式的模型透传；其余本地合成 SSE |
| `/v1/messages`（Anthropic） | ✅ 本地翻译，**含工具闭环** |
| `/v1/responses` | ✅ 本地翻译，**含 function_call 闭环** |
| `/v1/messages/count_tokens` | ✅ 粗估 |
| `/v1/models` | ✅ 上游目录（280 条），失败回落内置 17 条 |
| `/v1/balance` | ⚠️ 上游无此接口，只报本地账本 + key 状态 |
| 账号池 | ✅ 导入 / 批量导入 / 导出（默认脱敏）/ 探活 / 健康检查 / 用量 |
| 授权登录 | ✅ 独立 profile 拉起浏览器 + CDP 抓 key |
| 每日活跃对话 | ✅ 默认关闭，开启后保活 + **顺手验证 key** |
| 失效账号复检 | ✅ 被 401 禁用的 key 又活了就恢复 |
| 用量详情 / 使用日志 | ✅ 本地账本（上游没有用量接口，接口里显式 `supported: false`） |
| 本机客户端扫描 | ❌ 没有客户端登录态可扫（API key 是唯一入口） |
| 签到 | ❌ 上游没有签到接口 |
| Cookie / token 刷新 | ❌ key 是长期凭据，失效只能重新创建 |

### 三协议工具闭环（`core/protocol.py`）

上游是 OpenAI 兼容端点，工具是原生的 `tools` / `tool_calls`。Agent 侧把另外两种协议
翻成 OpenAI 就闭环：

| 方向 | 映射 |
|---|---|
| Anthropic → OpenAI | `tools[{name,input_schema}]` → `tools`；`input_schema` → `parameters`（压平）；`tool_choice: any` → `required`；assistant `tool_use` → `message.tool_calls`；user `tool_result` → `role="tool"` 消息 |
| OpenAI → Anthropic | `tool_calls` → `content[{type:tool_use}]`（`arguments` 字符串 → `input` 对象）；`finish_reason=tool_calls` → `stop_reason=tool_use`；`cached_tokens` → `cache_read_input_tokens` |
| Responses → OpenAI | 顶层平铺 `tools` → 嵌套；`input[{type:function_call}]` → assistant tool_calls；`function_call_output` → `role="tool"` |
| OpenAI → Responses | `tool_calls` → `output[{type:function_call}]`；`cached_tokens` → `input_tokens_details` |

三条硬约束：`parameters` 必须压平（`anyOf`/`$ref`/数组型 `type` 会让上游整轮失败）；
`arguments` 是字符串；`tool_calls` 与 tool 结果必须成对（别丢孤儿 `tool` 消息）。

---

## 四、接口

```
GET  /api/freebuff/meta
POST /api/freebuff/import                 {"key":"fb_...","label":"personal"}
POST /api/freebuff/import-batch           数组 / JSON / JSONL
GET  /api/freebuff/accounts
POST /api/freebuff/accounts/{id}/refresh  探活（目录可达 + key 状态）
POST /api/freebuff/accounts/{id}/health   分段健康检查
GET  /api/freebuff/accounts/{id}/usage    该账号最近请求
POST /api/freebuff/keepalive              全量探活
POST /api/freebuff/catalog/sync           强制刷新目录缓存
GET  /api/freebuff/auth/url               授权页地址 + 本机浏览器可用性
POST /api/freebuff/auth/start             拉起浏览器
GET  /api/freebuff/auth/poll?auth_id=     轮询（拿到 key 自动入库）
POST /api/freebuff/auth/cancel/{auth_id}
POST /api/freebuff/activity               手动跑一次每日活跃
POST /api/freebuff/ban-recheck            失效账号复检
GET  /api/freebuff/usage                  本地用量（按模型）
GET  /api/freebuff/usage/detail           平台视角 / 单账号明细
GET  /api/freebuff/usage/logs             使用日志（分页，可只看错误）
GET  /api/freebuff/export?reveal=0        导出（默认脱敏）
```

定时任务：`freebuff_keepalive`（探活）、`freebuff_activity`（每日活跃，默认不跑）、
`freebuff_ban_recheck`（失效复检）。

---

## 五、环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ADMIN_FREEBUFF_BASE` | `https://freebuff.com/api/v1/paid` | 上游 base（联调/回归） |
| `ADMIN_FREEBUFF_BROWSER` | 空 | 指定授权登录用的浏览器 exe |
| `ADMIN_FREEBUFF_ACTIVITY` | 空（关） | 置 `1` 才跑每日活跃对话（**会花额度**） |
| `ADMIN_FREEBUFF_ACTIVITY_PROB` | `0.7` | 每天每号执行概率 |
| `ADMIN_FREEBUFF_ACTIVITY_MAX_TOKENS` | `64` | 单次 max_tokens（够验证、少花钱） |
| `ADMIN_FREEBUFF_ACTIVITY_LIMIT` | `0`（全部） | 单次最多处理几个号 |

> 活跃对话默认关闭是刻意的：它是唯一能验证 key 的手段，但也会消耗额度。
> 打开后每个账号每天最多一次，且按概率跳过（真人不会天天来）。

---

## 六、验证记录（2026-11-07）

**上游连通性**（假 key 打真实上游）：

```
[200] GET  /api/v1/paid/models              280 个模型，capabilities 只有 chat/streaming/toolCalls
[401] POST /api/v1/paid/chat/completions    {"error":{"code":"invalid_api_key","message":"Invalid API key"}}
[404] GET  /api/v1/paid/{usage,credits,me,balance,health,billing}   全部 HTML 404
```

**结构验收**：`verify_platform.py --platform freebuff` → 11 项通过
（`overview_cards` 在本机因默认 MySQL 不可达失败，已用 SQLite + TestClient 覆盖；
`tests/gateway_contract_baseline.txt` 在 develop 分支不存在）。

**协议层单测**：63 项断言全通过 —— key 归一 / 会话 token 拒绝 / 目录合并 /
流式能力判定 / SSE 合成 / usage 归一 / 三协议工具闭环 / key 抓取正则 / 活跃开关。

**端到端冒烟**（SQLite + TestClient）：37 项全通过 ——
`/v1/models` 拉到 280 条真实目录、假 key → `401 auth`、会话 token 本地拒绝、
非流式模型返回合成 SSE（`X-Freebuff-Stream: synthesized`）、流式模型透传、
Anthropic 入口产出 `tool_use` 块且 `stop_reason=tool_use`、
请求侧 `anyOf` 被压平、授权入口可用、用量详情/日志可读、
活跃任务默认跳过、模拟上游下活跃对话把 `verify_state` 翻成 `ok`。

---

## 七、已知限制

* **没有余额口径**：上游不提供，界面只显示本地用量与 key 校验状态。
* **key 有效性延后确认**：`unverified` 的账号在首次真实对话后才会变成 `ok` /
  `rejected`；连续 2 次 401 才禁用（避免抖动误杀）。
* **合成 SSE 不是真流式**：模型不支持流式时，首字节要等上游整条答复生成完
  （`X-Freebuff-Stream: synthesized` 标明），TTFT 会比真流式高。
* **授权登录需要本机有 Chromium 系浏览器**；服务器/容器部署下只能粘贴导入。
* **活跃对话会花额度**，所以默认关闭。