# WPS 灵犀（Lingxi）桌面端「上游接口」取证报告

采集时间：2026-11-07
样本：`E:\lingxi-desktop`（Electron 5.24.0 / app-server build `bb60347a-20261105`）

---

## 0. 取证环境与一个必须先知道的事实

| 文件 | 状态 | 说明 |
|---|---|---|
| `E:\lingxi-desktop\resources\app.asar` | **被截断** | 物理大小 121,634,816 B（116 MiB），但头部目录声明的 packed 内容最大 `offset+size = 263,691,823`（≈251 MiB） |
| `E:\lingxi-desktop\resources\app.asar.unpacked\` | 空 | 187 个 `unpacked:true` 条目全部缺失（含 win32 原生模块） |
| `E:\lingxi-desktop\resources\app-server\app-server-windows-amd64.zip` | 完整 95 MB | **核心证据来源**：`service\lingxi.exe`（68.9 MB，Go 编译，含全部上游路由常量） |
| `E:\lingxi-desktop\resources\{kdocs-cli,wps-cli,node,koffi}\` | 空目录 | 只剩同名子目录，载荷缺失 |
| `E:\lingxi-desktop\resources\app-update.yml` | 占位 | `url: https://example.com/auto-updates` |

实测解包结论（node 手工解析 asar：`base = 8 + headerSize`，`headerSize=6,938,964`，`jsonSize=6,938,953`）：

- 目录条目 26,622，其中 packed 26,435；**可读**（`base+off+size <= 121,634,816`）的 packed 条目 **22,856**，共 105.6 MB。
- 可读的全是 `node_modules/**`；
- **`out/`（主进程/preload）与 `desktop-renderer-dist/`（渲染层）的 731 个 chunk 全部 0 个可读**，例如：
  - `out/main/index.js` off=238,918,726
  - `out/preload/index.js` off=240,925,935
  - `desktop-renderer-dist/assets/credits-Cf9JagTB.js` off=177,340,514
  - `desktop-renderer-dist/assets/aiofficeChat-DVVFcXVg.js` off=170,602,426
  - `desktop-renderer-dist/assets/useCoworkCreditDeductStatus-DPGkhjpr.js` off=232,867,117
  - `desktop-renderer-dist/assets/PCoworkChat-DmEANxbp.js` off=162,318,855

含义：**渲染层里那份最完整的 URL / header / setInterval 代码，本副本拿不到**。可交付的一手证据全部来自 `lingxi.exe` 的 Go 字符串表（它是真正的「上游转发层」，路由常量、请求头、JSON 字段都在里面）。

---

## 1. 域名与 base

`lingxi.exe` 中直接出现（偏移为文件字节偏移）：

```
@39909583  https://lingxi.wps.cn/api/public/v1/agent-runtime/chat/completions
@39775732  http://lingxi.wps.cn/api/aioffice/v1/web_search
@702138    ](https://lingxi.wps.cn/api/aioffice/v1/short_link/xxx)
@702369    ](https://lingxi.wps.cn/claw)
@689590    https://drive.wps.cn  https://www.kdocs.cn  DRIVE_DMC_ACCESS_KEY  http://lingxi.wps.cn
@689641    https://account.wps.cn  DRIVE_INTERNAL_GATEWAY
@689510    http://api.wps.cn  https://hd.wps.cn ... http://vas.wps.cn  http://kpp.wps.cn
@689511    ai-gateway.wps.cn  Client-Request-Id  /api/v3/responses  /api/v2/llm/video
@689665    http://api.inner.wps.cn  http://aibot-api.wps.cn
@689693    http://encs-inner.wps.cn
@689942    http://personal-ai-bus.internal.wps.cn
@689892    http://aigc-gateway-test.ksord.com
```

结论：

- **业务上游 = `https://lingxi.wps.cn`**（`lingxi.kdocs.cn` 在可读代码中**不存在**）
- LLM 网关 = `https://ai-gateway.wps.cn`（`/api/v3/chat/completions`、`/api/v3/responses`、`/api/v2/llm/*`、`/api/v2/aigc/*`、`/api/v2/asr/*`、`/api/v2/tts/*`）
- 账号/登录 = `https://account.wps.cn`；内网账号 = `http://api.wps.cn` / `http://api.inner.wps.cn`
- 云文档 = `https://drive.wps.cn`、`https://www.kdocs.cn`、`https://kdocs.cn`、`https://365.kdocs.cn`、`https://web.wps.cn`
- base 来自配置项 `base_url`（`json:"base_url"`，`*struct { BaseURL string "json:\"base_url\""; EnableRunBilling *bool "json:\"enable_run_billing\"" }`），未写死；但 `chat/completions` 与 `short_link` 两处是**硬编码完整 URL**。

---

## 2. 鉴权 / 必需请求头 / 硬编码常量

### 2.1 会话凭证：`wps_sid`

```
@32675610  ... CollectWithCountFloat  WPSSid  json:"wps_sid" ...
@32959687  ... WPSSid json:"wps_sid,omitempty" ... *serverapi.CreateSessionRequest ...
@39262606  ... LINGXI_WPS_SID  set_credential  account.status  account.logout ...
@39330128  ... account.login.start  wps_sid is required  ...
@32705476  ... *auth.WPSSidCredential ...
```

- 字段名 / Cookie 名均为 **`wps_sid`**
- 可用环境变量注入：**`LINGXI_WPS_SID`**
- 缺失时报错 `wps_sid is required`（错误码段在 `account.login.start` 之后）
- 未发现 `kdocs_sid` / `csrf` 参与业务请求（`csrf` 只在 `node_modules` 里 hono 框架自带中间件出现）

### 2.2 请求头（全部来自 lingxi.exe 字符串表）

| 头 | 证据偏移 |
|---|---|
| `X-Client-Type` | @39251536 |
| `X-Client-Lang` / `X-Client-Language` | @39251510 / @39301362 |
| `X-Kso-AccessToken` | @39301379 |
| `X-Cc-Agent-Type` | @39275344 |
| `X-Cc-Region` | @39228738 |
| `X-Cc-Run-ID` | @39228738 |
| `X-Cc-Session-ID` | @39276321 |
| `X-Request-Id` | @39240549 |
| `X-Session-Id` | @39242533 |
| `Idempotency-Key` / `X-Idempotency-Key` | @39275659 / @39301908 |
| `AI-Gateway-Product-Name` | @39390401 |
| `Client-Request-Id` | @689511 |
| `Authorization: Bearer %s` | @689339 附近（`Bearer %s`） |
| `Accept: text/event-stream` | @39301379 |

### 2.3 `X-Client-Type` 取值（客户端类型枚举，全部实测出现）

```
lingxi_claw   lingxi-claw   lingxi-app-server   lingxi_claw_we
pc_copilot    web_copilot   mac_copilot         ios_copilot
lingxi_ios    lingxi_ios_remote365   pad_android
wps-lingxi-web   wps_sidebar
```

（@39215374、@39226912、@39226516、@39299821、@39293655 一带）

### 2.4 响应封套

所有 public API 统一为：

```
{ <publicAPIResponse 状态字段>, "data": { ... } }
```

证据（@682661 起大量 `type:.eq.struct`）：

```
*struct { publicapi.publicAPIResponse; Data publicapi.CreditBalanceResp "json:\"data\"" }
*struct { publicapi.publicAPIResponse; Data publicapi.CreditTransactionListResp "json:\"data\"" }
*struct { publicapi.publicAPIResponse; Data publicapi.CreateSessionResp "json:\"data\"" }
*struct { publicapi.publicAPIResponse; Data publicapi.ListSessionsResp "json:\"data\"" }
*struct { publicapi.publicAPIResponse; Data publicapi.MessageListResp "json:\"data\"" }
*struct { publicapi.publicAPIResponse; Data publicapi.URLFetchResponse "json:\"data\"" }
...
```

状态字段含 `json:"code"`、`json:"message"` / `json:"msg"`（@641142/@644594/@654039），以及字符串常量 `is_success`、`business_code`、`err_code`（@39226494 / @39275344 区段）。

### 2.5 版本 / 构建 / 渠道

`resources\app-server\bundled-version.json`：

```json
{ "version": "5.24.0", "platforms": ["win","mac_arm","mac_intel"],
  "build_time": "2026-11-05T11:50:53.724Z", "commit_id": "bb60347a" }
```

`app-server\.version`：`{"build":"bb60347a-20261105","platform":"windows-amd64","version":"5.24.0"}`

其他可用常量：`lingxi_v2`、`lingxi.scene`、`lingxi: stdio`、`session_client_type`、`KLOG_FORMATTER`、`KDOCS_CLI_PATH=`、`BROWSER_RUN_ID=`、`LINGXI_GROUP_ID=`、`LINGXI_EXEC_ENV=`、`LINGXI_EMBED_NATIVE_HOST`、`LINGXI_SANDBOX_ALLOW_LOCAL_BINDING`。

**硬编码 appid：未找到**（`appid` / `app_id` 只作为 JSON 字段 `json:"appid"` / `json:"app_id"` 出现在 `AppID` 结构体上，无字面量数字）。

---

## 3. 五类接口

### 3.1 签到 / 领奖 —— **未找到**

可读代码中不存在 `checkin` / `check_in` / `sign_in` / `signin` / `benefit` / `welfare` / `daily reward` / `智点` 任一形态的 URL、路径或字段（对 asar 可读区 + lingxi.exe 双份检索，`签到`/`领取`/`奖励`/`智点` UTF-8 检索命中 0）。

能找到的**相邻能力**（不是签到，别当签到用）：

| 能力 | 证据 |
|---|---|
| 邀请码兑换查询 `GET /api/public/v1/invite_codes/redeemed` | @39644180、@39675594（`%s/api/public/v1/invite_codes/redeemed` / `GET_/api/public/v1/invite_codes/redeemed`） |
| 积分发放配置键 `ai_credits_grant` | @39290969，紧邻 `灵犀 v2 快速` / `灵犀 v2 思考` |
| 积分发放字段 `credits_issue` | @33004917 `CreditsIssue json:"credits_issue"` |
| 赠送/加购额度 `bonus_credits`、`total_additional`、`total_purchased` | @33010167 / @33133079 附近 |
| 每日额度耗尽错误码 `DailyQuotaExhausted` | @39330033 |

判断：签到逻辑要么在渲染层（本副本不可读），要么走 `kdocs.cn` 会员侧（`kdocs-cli` 载荷缺失）。**不能编造。**

### 3.2 保活 / 心跳 / 活跃度

**没有账号级心跳接口**（无 `heartbeat` / `presence` / `keepalive` / `last_active` 的 HTTP 路径；`keepalive` 全部命中 grpc / etcd / mcp 内部）。

会话级保活与活跃上报，实测存在：

| 接口 | Method | 作用 | 证据 |
|---|---|---|---|
| `/api/public/v1/sessions/:session_id/status` | PUT | 会话状态上报（`UpdateSessionStatus`） | @39762238 |
| `/api/public/v1/sessions/{id}/execution_status` | GET | 执行状态轮询（`ReportExecutionRunning`） | @690007、@39287081 |
| `/api/public/v1/sessions/{id}/events` | GET | **SSE 长连接**，对应 IPC 方法 `session/subscribe` | @39592222、@39300019 |
| `/api/public/v1/sessions/:session_id/pin` | POST | 会话置顶（心跳旁路会带） | @39592191 区段 |
| `/api/public/v1/sessions/{id}/messages/max_id` | GET | 增量拉取水位 | @39733749 |
| `/api/public/v1/credits/balance` | GET | 余额轮询（最接近「保活」的周期请求） | @39576026 |
| `/api/logs/v1/app-server/dw/events` | POST | 埋点上报（dw = data warehouse） | @689590 区段 |
| `/api/logs/v1/metrics` | - | 指标上报 | 同上 |
| `/trace/report`、`/api/aioffice/v1/trace/config` | POST / GET | 链路追踪上报与配置 | @39251862、@689590 区段 |

**轮询间隔：未找到。** 所有 `setInterval` / `setTimeout` 毫秒常量都在 `out/main` 与 `desktop-renderer-dist`（不可读）。Go 侧唯一时间常量是沙箱 `python-heartbeat` 的 **90s 超时**（`[python-heartbeat] timeout after 90s; requesting interrupt`，@690130），与账号活跃无关，不能当轮询间隔用。

### 3.3 用量详情（余额 / 明细 / 计划）

```
@39576026  %s/api/public/v1/credits/balance      GET_/api/public/v1/credits/balance
@39762303  GET_/api/public/v1/credits/transaction_summary   invalid since date %q, expected YYYY-MM-DD
@39775490  invalid before date %q, expected YYYY-MM-DD
@39540997  %s/api/public/v1/credits/balance
@39592222  POST_/api/public/v1/credits/consume
@39733833  %s/api/public/v1/credits/transaction_summary   POST_/api/public/v1/dev/credits/team_consume
@39610664  %s/api/public/v1/dev/credits/consume   %s/api/public/v1/dev/credits/balance   POST_/api/public/v1/dev/agents/batch
```

| 接口 | Method | 参数 | 返回结构 |
|---|---|---|---|
| `/api/public/v1/credits/balance` | GET | 无（凭证在 header / body） | `CreditBalanceResp{ CreditBalanceItem[] }` |
| `/api/public/v1/credits/transaction_summary` | GET | `CreditTransactionListOpts`：日期范围 **`since` / `before`，格式 `YYYY-MM-DD`**；分页 `page_size`（`page_size_too_large` / `negative_pagination` 错误码） | `CreditTransactionListResp{ []CreditTransactionItem }` |
| `/api/public/v1/credits/consume` | POST | `CreditConsumeReq` | `CreditConsumeResp` |
| `/api/public/v1/dev/credits/{balance,consume,team_balance,team_consume}` | GET/POST | 同上，开发态 | - |
| `/api/public/v1/dev/credits/skill_charges/finalize` | POST | `DMCSkillChargeFinalizeReq` | - |

**余额字段**（`CreditBalanceItem`，@33010167 / @33089383 / @33010202 / @32960649）：

```
total_balance      bonus_credits     task_consumed      income_cursor
credits_balance    credit_consumed   usage_available    usage_percent
subscription_id    expire_time       credits_issue      total_purchased
total_additional   total_period      total_groups       total_queries
total_page_num     credits_cost      image_price_cents  input_price_cents
```

**明细字段**（`CreditTransactionItem`，@33053288 / @32989702）：

```
expense_cursor   income_cursor   storage_source   already_exists
screenshot_url   blur_desc       tts_count        user_type   remark
```

→ 明细列表是**双向游标**：`income_cursor`（收入侧）+ `expense_cursor`（支出侧），配合 `since`/`before` 日期窗口。

**计划 / 套餐**：存在 `Plans json:"plans"` + `Models json:"models"`（`serverapi.Plan` / `serverapi.Model`，@687602、@32582519），但**没有独立的 `/plans` 或 `/subscription` 端点**；计划信息随会话/运行配置一起下发（`plan_path`、`subscription_id`）。

### 3.4 使用日志 / 调用记录

```
@39592191  %s/api/public/v1/agents/run_records
@39644180  %s/api/public/v1/agents/run_records   POST_/api/public/v1/agents/run_records
@758038    publicapi.(*Client).InsertRunRecord
```

| 接口 | Method | 说明 |
|---|---|---|
| `/api/public/v1/agents/run_records` | **POST**（`InsertRunRecord`，body=`AgentRunRecordInput`） | 客户端**上报**每次 run 的用量 |
| 同路径 `%s/api/public/v1/agents/run_records` | GET（推断） | 记录列表；`agents/` 前缀有 20 处、`sessions/` 有 38 处，说明是标准 CRUD 资源 |
| `/api/public/v1/sessions/{id}/messages/groups` / `group/{gid}` / `max_id` | GET | 会话历史分页 |
| `/api/public/v1/sessions/{id}/events` | GET | 事件流回放 |
| `/api/public/v1/session_list` | GET | 会话清单（轻量） |
| `/api/logs/v1/app-server/dw/events` | POST | 明细埋点（`dw.group_id`/`dw.scene`/`dw.ppt_mode`/`dw.trace_id`/`dw.exec_env`） |

**记录字段**（`AgentRunRecordInput` + 通用 token 结构，@664114/@660489/@664625-670431）：

```
run_id  session_id  model_name  client_request_id  request_id
input_tokens  output_tokens  total_tokens  cached_tokens  reasoning_tokens
duration_ms  created_at  finished_at  status / run_status  is_success
credits_cost  agent_type  skill_name  tool_names  channel_id  host_model
```

另外 `prompt_tokens` / `completion_tokens`（@39251536 区段）、`usage_percent`、`token_usage.updated` 事件名（@39330033）。

### 3.5 对话通路与鉴权（三个已知接口已全部核实）

| 接口 | 核实来源 |
|---|---|
| `POST https://lingxi.wps.cn/api/aioffice/v1/cowork/sessions` | @39535413 `... /api/public/v1/connector-skills/ /api/aioffice/v1/cowork/sessions ...`；另有 `cowork_session.create`、`cowork session create completed` |
| `POST https://lingxi.wps.cn/api/public/v1/credits/runs` | @39453332 `/api/public/v1/credits/runs`；IPC `credit_run.create`、`creating credit run` |
| `POST https://lingxi.wps.cn/api/public/v1/agent-runtime/chat/completions` | @39909583 **完整硬编码 URL** `https://lingxi.wps.cn/api/public/v1/agent-runtime/chat/completions` |

配套通路（同一会话体系）：

```
POST   /api/public/v1/sessions                          # 建会话（CreateSessionReq/Resp）
GET    /api/public/v1/sessions                          # 列表（ListSessionsOpts/Resp）
GET    /api/public/v1/sessions/{id}                     # 详情（SessionDetail）
PUT    /api/public/v1/sessions/{id}/title               # 改名
PUT    /api/public/v1/sessions/{id}/extra
PUT    /api/public/v1/sessions/{id}/status
POST   /api/public/v1/sessions/{id}/pin
POST   /api/public/v1/sessions/{id}/chunks              # 消息分块上传（UploadGroupToChunk）
POST   /api/public/v1/sessions/{id}/files
GET    /api/public/v1/sessions/{id}/files
GET    /api/public/v1/sessions/{id}/events              # SSE
POST   /api/aioffice/v1/sessions/{id}/messages           # aioffice 老通道
POST   /api/aioffice/v1/sessions/{id}/pin
POST   /api/aioffice/v1/sessions/plan
POST   /api/aioffice/v1/sessions/rollout
POST   /api/public/v1/agent-runtime/chat/completions     # 主对话（SSE）
POST   /api/public/v1/agent-runtime/chat/recommendations
GET    /api/aioffice/v1/trace/config
POST   /api/aioffice/v1/immemory/compact
POST   /api/aioffice/v1/agent/parallel-tools/policy
```

**鉴权复刻要点**（全部有实证）：

1. `Cookie: wps_sid=<sid>`（或 body 字段 `wps_sid`）；也接受 `LINGXI_WPS_SID` 环境变量
2. `X-Kso-AccessToken: <token>`（金山账号 access token）
3. `X-Client-Type: lingxi_claw | pc_copilot | wps-lingxi-web ...`
4. `X-Client-Lang: zh-CN`（或 `X-Client-Language`）
5. `X-Request-Id` / `X-Session-Id` / `X-Cc-Run-ID` / `X-Cc-Region` / `Idempotency-Key`
6. `Accept: text/event-stream`（对话必须，SSE 逐事件流）
7. AI 网关侧另加：`AI-Gateway-Product-Name`、`Client-Request-Id`、`anthropic-version`、`Authorization: Bearer <key>`
8. **无 csrf 头证据**——`csrf` 只在 `node_modules/hono` 框架代码里，业务请求不校验 `x-csrf-token`

---

## 4. 域名 URL 去重清单（按路径排序）

```
https://ai-gateway.wps.cn
https://ai-gateway.wps.cn/api/v2/aigc/text2images/create
https://ai-gateway.wps.cn/api/v2/aigc/workflows
https://ai-gateway.wps.cn/api/v2/asr/audio2text
https://ai-gateway.wps.cn/api/v2/llm/chat
https://ai-gateway.wps.cn/api/v2/llm/embedding
https://ai-gateway.wps.cn/api/v2/llm/multimodal
https://ai-gateway.wps.cn/api/v2/llm/video
https://ai-gateway.wps.cn/api/v2/tts/base
https://ai-gateway.wps.cn/api/v3/chat/completions
https://ai-gateway.wps.cn/api/v3/responses
http://aigc-gateway-test.ksord.com
https://account.wps.cn
https://api.wps.cn
http://api.wps.cn
http://api.inner.wps.cn
http://aibot-api.wps.cn
https://drive.wps.cn
http://encs-inner.wps.cn
https://hd.wps.cn
http://kpp.wps.cn
https://lingxi.wps.cn
https://lingxi.wps.cn/api/aioffice/v1/short_link/{id}
http://lingxi.wps.cn/api/aioffice/v1/web_search
https://lingxi.wps.cn/api/public/v1/agent-runtime/chat/completions
https://lingxi.wps.cn/claw
http://personal-ai-bus.internal.wps.cn
http://vas.wps.cn
https://web.wps.cn
https://www.kdocs.cn
https://kdocs.cn
https://365.kdocs.cn
https://www.kdocs.cn/l/{id}
https://365.kdocs.cn/l/{id}
```

（另有 `https://ksogit.kingsoft.net/lingxi-ai/lingxi-app-server`、`.../lingxi-agents`、`.../lingxi-env` —— 源码仓库地址，不是 API。）

---

## 5. 明确「没找到」的部分

1. **签到 / 领取 / 每日奖励**：无任何 URL、路径、常量。`签到`、`领取`、`奖励`、`智点`、`checkin`、`sign_in`、`benefit`、`welfare` 全部 0 命中。
2. **轮询间隔毫秒数**：无。`setInterval`/`setTimeout` 全在不可读的 renderer chunk 里。
3. **账号级心跳 / 在线保活接口**：无。只有会话级 status/SSE 与 balance 轮询。
4. **`lingxi.kdocs.cn`**：不存在（只有 `kdocs.cn` / `www.kdocs.cn` / `365.kdocs.cn` 这些文档站）。
5. **`csrf` / `kdocs_sid` 参与业务请求**：无。
6. **硬编码 appid**：无字面量（`appid` 只是字段名）。
7. **签到类积分单位「智点」字符串**：无（积分统一叫 `credits`，额度叫 `quota`）。
8. **`/plans`、`/subscription` 独立端点**：无。

---

## 6. 给 Python 网关的落地建议

```python
BASE = "https://lingxi.wps.cn"
HEADERS = {
    "Cookie": f"wps_sid={WPS_SID}",
    "X-Client-Type": "lingxi_claw",
    "X-Client-Lang": "zh-CN",
    "X-Kso-AccessToken": KSO_TOKEN,
    "X-Request-Id": uuid4().hex,
    "Accept": "text/event-stream",
    "Content-Type": "application/json",
}

# 余额
GET  f"{BASE}/api/public/v1/credits/balance"
# 明细（日期窗口 + 双向游标）
GET  f"{BASE}/api/public/v1/credits/transaction_summary"
     params={"since": "2026-11-01", "before": "2026-11-08", "page_size": 20,
             "cursor": income_cursor_or_expense_cursor}
# 运行记录上报 / 列表
POST f"{BASE}/api/public/v1/agents/run_records"
GET  f"{BASE}/api/public/v1/agents/run_records"   # 方法推断，先试 GET 再回退 POST
# 建会话 → 拿 session_id → 建 credit run → SSE 对话
POST f"{BASE}/api/public/v1/sessions"
POST f"{BASE}/api/public/v1/credits/runs"
POST f"{BASE}/api/public/v1/agent-runtime/chat/completions"
# 事件流
GET  f"{BASE}/api/public/v1/sessions/{sid}/events"
```

成功判定：HTTP 2xx + 封套 `data` 存在；错误看封套 `code` / `message` 与业务码 `business_code`、`is_success`，以及 `DailyQuotaExhausted` / `credit_insufficient` / `TeamCreditInsufficient`。

---

## 7. 若要补全 renderer 侧证据

需要一份**未截断**的 `app.asar`（≥252 MiB）。补齐后可直接对
`desktop-renderer-dist/assets/*.js` 做 `rg -a`，重点文件（目录已列出）：
`credits-*.js`、`useCoworkCreditDeductStatus-*.js`、`aiofficeChat-*.js`、`PCoworkHome-*.js`、
`out/main/chunks/authCloudReporter-*.js`、`out/main/chunks/timerTaskScope-*.js`（这个文件名本身暗示定时器逻辑）。