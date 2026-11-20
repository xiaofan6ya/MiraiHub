---
name: case-krowork
description: KroWork（Kro）客户端拆包与网关接入实录。当需要接入 KroWork、还原它的三端协议（Chat Completions / Anthropic Messages / Responses）、理解 Keycloak id_token 与 30 分钟寿命、分 provider 端点前缀、槽位（slot）与后端模型（backend）两层目录，或排查「模型 authentication failed」「登录态 30 分钟就没了」「/v1/models 列出一堆调不通的模型」时使用。
---

# KroWork 接入实录（2026-11-19）

一次真实接入的完整结论：从 51 MB 的 Tauri 壳和 111 MB 的 Nuitka 服务里，
把三端协议、鉴权链、模型目录全部还原出来，并落到 `admin/platform/krowork/`。

> 与 [ONBOARDING.md](ONBOARDING.md) 的分工：那份讲通用六步，这份是**第七个实例**的
> 具体输出 + 五条只有 KroWork 才有的坑。

---

## 1. 组件画像

| 文件 | 大小 | 形态 | 职责 |
|---|---|---|---|
| `krow-app.exe` | 51 MB | **Tauri（Rust）** | 桌面壳；本地控制面 `127.0.0.1:17322` |
| `krow-server.exe` | 111 / 255 MB | **Nuitka（Python 3.12）** | agent 后端 `127.0.0.1:17323`（FastAPI，`/health` 免鉴权） |
| `krow-shim.exe` | 4 MB | Rust CLI | 外部进程入口，走 `17322` + `.runtime-token` |
| `resources/packages/*.whl` | 13 个 | Cython `.pyd` | `krow_openai`（模型/对话）、`krow_kro_protocol`（kro 入口协议） |

**没有 `app.asar`** —— 别按 Electron 的思路拆。Tauri 的前端资产直接嵌在 exe 里，
业务逻辑在 Rust + 那个 Python 服务里。

### 数据目录

```
%LOCALAPPDATA%\krow\
  config.json            {"projectsDir": "...", "region": "cn"}
  credentials.bin        加密的 access/refresh/id_token（3153 B，非 DPAPI、非明文）
  .krowd_token           64 hex，本地 daemon 管道握手指令
  .runtime-token         64 hex，本地 HTTP 控制面 Bearer（17322）
  storage-v2.db          SQLite：conversation / conversation_message / project
  model-map-cn.json      网关落盘的模型表缓存（本平台写入）
```

`storage-v2.db` 的 `conversation_message` 里有 `model_name = "krow-model-1:ds4-pro"`
和 pydantic-ai 风格的 usage —— 这是**最早认出槽位/后端两层结构**的线索。

---

## 2. 域名表

| 用途 | 国内（region=cn） | 国际 |
|---|---|---|
| LLM 网关 | `https://llm.krowork.com` | `https://llm.kro.work` |
| 业务 API | `https://api.krowork.com` | `https://api.kro.work` |
| Suite（应用商店） | `https://fuse.krowork.com` | `https://fuse.kro.work` |
| Keycloak | `https://auth.krowork.com/realms/krow/protocol/openid-connect` | `https://auth.kro.work/realms/krow/...` |

Keycloak realm `krow`，client **`krow-app`**，走 OAuth2 PKCE。
JWT payload 实测：`typ=ID`、`aud=krow-app`、`azp=krow-app`、`exp-iat=1800`。

---

## 3. 协议端点（核心产出）

**上游不是统一的 `/v1`** —— 每个模型带 `proxy_path`，对话路径按它拼。

| provider_type | 端点 | 协议 | 实测 |
|---|---|---|---|
| `openai-chat` | `{proxy_path}/chat/completions` | OpenAI 原生 | ✅ 200（`/deepseek/v2`、`/ks/v1`） |
| `dashscope` | `{proxy_path}/chat/completions` | OpenAI 兼容 | ✅ 200（`/qwen/compatible-mode/v1`、`/glm/v1`） |
| `anthropic` | `/anthropic/v1/messages` | **原生 Anthropic** | ✅ 200（`msg_` 前缀 + `stop_reason`） |
| `openai` | `/openai/v1/responses` | **原生 Responses** | ⚠️ 路径通，Azure 后端偶发 500/502 |
| `openai-image` / `dashscope-image` | `/ks/v1` · `/ali/api/v1` | 生图 | 未接 |
| `effect` | `/effect` | 特效 | `enabled=null`，跳过 |

### 三个路径坑

1. **Anthropic 有 `/v1`**：`/anthropic/v1/messages` 通，`/anthropic/messages` 是 404。
2. **`/anthropic/v1/messages/count_tokens` 不存在**（404）—— count_tokens 只能本地估。
3. **`provider_type=openai` 没有 chat**：`/openai/v1/chat/completions` 404，
   只有 `/openai/v1/responses`。所以 chat 入口遇到这类模型要**明确 400 报端点错**，
   而不是转一手协议去猜。

### 请求头（实测必需）

```
Authorization: Bearer <id_token>
x-model:       <backend id>          ← 必须与 body.model 一致
x-source:      krow-openai
x-app:         <app_name>            ← 归因用，网关自报 miraihub
x-user-id:     <jwt.sub>
x-device-id:   <64 hex>              ← MachineGuid 派生，**必须稳定**
x-extra:       {"source","app_name","client_version","lang"}
Content-Type:  application/json
```

鉴权头形状实测：

| 请求 | 响应 |
|---|---|
| 无 Authorization | `401 {"error":"missing token"}` |
| `Authorization: Bearer junk` | `401 {"error":"invalid token"}` |
| `x-api-key: junk` | `401 {"error":"missing token"}`（不认这个头） |

---

## 4. 模型目录是两层

```
slots[region][slot]  ──model_id──▶  models[backend]  ──proxy_path──▶  端点
krow-model-1                        ds4-pro             /deepseek/v2
```

`GET /config/model-map` 一次给全（61 个 backend + 分 region 的 slots + defaults）：

```json
{
  "version": 19, "min_agent_version": "1.4.0",
  "models": { "ds4-pro": {"provider_type":"openai-chat","proxy_path":"/deepseek/v2",
                          "max_output_tokens":32000,
                          "window":{"nominal":1000000,"max_input":1000000},
                          "capabilities":{"supports_tool_calling":true,
                                          "supports_parallel_tool_calls":true,
                                          "supports_thinking":true,
                                          "supports_json_schema":true},
                          "extra_body":{"thinking":{"type":"disabled"}}} },
  "slots":  { "cn": { "krow-model-1": {"model_id":"ds4-pro","display_name":"DeepSeek V4 Pro"} } },
  "defaults": { "default_slot_per_region": {"cn":"krow-model-1"} }
}
```

### cn 区 7 个槽位（免费账号实际能调的）

| slot | backend | provider | 上下文 | 最大输出 | 图片 | 思考 |
|---|---|---|---|---|---|---|
| `krow-model-1` | `ds4-pro` | openai-chat | 1,000,000 | 32,000 | — | ✅ |
| `krow-model-n` | `ds4-flash` | openai-chat | 1,000,000 | 32,000 | — | ✅ |
| `krow-model-i` | `qwen3.8-max-klink-check` | openai-chat | 1,000,000 | 64,000 | ✅ | ✅ |
| `krow-model-j` | `qwen3.8-flash-klink-check` | openai-chat | 1,000,000 | 64,000 | ✅ | ✅ |
| `krow-model-k` | `glm-5.3-klink-check` | openai-chat | 1,000,000 | 32,000 | — | ✅ |
| `krow-model-l` | `glm-5.3-flash-klink-check` | openai-chat | 1,000,000 | 32,000 | ✅ | ✅ |
| `krow-model-m` | `kimi-k3-klink-check` | openai-chat | 1,000,000 | 64,000 | ✅ | ✅ |

> **`/v1/models` 只许列这 7 个。** 61 个 backend 里有 11 个 anthropic、4 个 openai、
> 若干 `pa/` 变体 —— 这些对免费账号全是 502 `upstream authentication failed`。
> 全列出去 = 客户端选到一个必然失败的模型，而错误长得像「网关坏了」。

---

## 5. 凭证链（最麻烦的一段）

### 客户端怎么拿 token

对 `krow_openai._client` 做 `sys.addaudithook` 实测：

```
open  %LOCALAPPDATA%\krow\config.json          → 取 region
open  %LOCALAPPDATA%\krow\.krowd_token         → 64 hex 握手口令
open  \\\\.\\pipe\\krowd                        → 换 id_token（自定义二进制帧）
subprocess REG.exe QUERY ...MachineGuid        → 派生 x-device-id
```

**token 不进磁盘**（`credentials.bin` 是加密的且非 DPAPI），只活在 daemon 进程里。
管道帧格式在 Cython 里被压缩，静态读不出 —— 所以网关**不裸读管道**。

### 网关的四级来源链（`core/token.py`）

| 级 | 来源 | 场景 |
|---|---|---|
| 1 | `KROW_ID_TOKEN` 环境变量 | 容器 / 上线 |
| 2 | `%LOCALAPPDATA%\krow\id_token.txt` | 面板粘贴、脚本落盘 |
| 3 | **sidecar**：借 KroWork 自带的 Python 3.12 跑 `krow_openai`，用 `httpx.Client.send` 钩子取走 `Authorization` | 本机有客户端，全自动 |
| 4 | `refresh_token` + Keycloak `grant_type=refresh_token` | 客户端没开时的续命 |

**sidecar 为什么必须走子进程**：`krow_openai` 是 `cp312` 原生扩展，网关跑 3.10。
借 KroWork 自己的 3.12 环境 + 它内置的管道客户端，等于**免费继承官方续期逻辑**，
比自己逆管道稳得多。

### 30 分钟寿命带来的连锁反应

* `id_token` 只活 30 分钟 → **必须**有 `krowork_keepalive` 定时任务；
* 模型表是静态配置 → **必须落盘**（`model-map-cn.json`），
  否则「重启 + 客户端没登录」会让 `/v1/models` 直接空掉。

---

## 6. 三端怎么接（`gateway.py`）

| 入口 | 目标模型 | 走法 |
|---|---|---|
| `/v1/chat/completions` | openai-chat / dashscope | 原生透传 |
| `/v1/chat/completions` | anthropic | `openai_request_to_anthropic` → 调 `/anthropic/v1/messages` → `anthropic_message_to_openai` |
| `/v1/messages` | anthropic | **原生透传**（只改 `model` 字段） |
| `/v1/messages` | 其它 | `anthropic_to_openai` → chat → `openai_to_anthropic` |
| `/v1/responses` | openai | **原生透传** |
| `/v1/responses` | 其它 | `responses_to_openai` → chat → `openai_to_responses` |

原则：**能原生就原生**。每多一层转换，思考块 / 工具参数增量 / 缓存字段
就多一个静默丢失的机会。

流式一律 `StreamingResponse` + 逐帧 yield，收尾补 `data: [DONE]`。

---

## 7. 实测记录（2026-11-19，cn 区）

```
GET  /health                                200  "ok"（免鉴权探针）
GET  /config/model-map                      200  version=19 models=61 slots=7(чn)
POST /deepseek/v2/chat/completions          200  tools → finish=tool_calls
                                                tool_calls[0].function.arguments = {"city":"杭州"}
                                                reasoning_content + usage.cached_tokens 齐全
POST /ks/v1/chat/completions                200  qwen / glm / kimi 走这里
POST /qwen/compatible-mode/v1/chat/...      200
POST /glm/v1/chat/completions               200
POST /anthropic/v1/messages                 200  claude-sonnet-5-5-klink-check → "ok"
POST /openai/v1/responses                   200  gpt-6-sol / gpt-6-astra 可用
POST /openai/v1/chat/completions            404  provider=openai 只有 responses
POST /anthropic/messages                    404  少了 /v1
POST /anthropic/v1/messages/count_tokens    404  没有这个接口
GET  api.krowork.com/krow/api/app/check     200  免鉴权业务探针
POST /anthropic/v1/messages (pa/claude-*)   502  upstream authentication failed（套餐不通）
```

**用法示例（OpenAI SDK）**

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8790/v1", api_key="<网关 Key>")
client.chat.completions.create(
    model="krow-model-1",                    # 槽位名，不是 ds4-pro
    messages=[{"role": "user", "content": "你好"}],
    tools=[{"type": "function", "function": {"name": "get_weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}],
)
```

**Anthropic 客户端**

```python
from anthropic import Anthropic
c = Anthropic(base_url="http://127.0.0.1:8790", api_key="<网关 Key>")
c.messages.create(model="krow-model-1", max_tokens=1024,
                  messages=[{"role": "user", "content": "你好"}])
```

---

## 8. 五条只有 KroWork 才有的坑

| # | 坑 | 现象 | 修法 |
|---|---|---|---|
| 1 | **模型目录两层** | 拿 `ds4-pro` 当 model 发，能通但日志/计费对不上；拿 61 个后端全列出去，客户端选到 502 | `resolve()` 双向解析；`/v1/models` 只出 slots |
| 2 | **凭证 30 分钟** | 「刚导入能用，过一会儿全 503」 | `krowork_keepalive` 定时续 + 401 时 `note_unauthorized()` 清缓存重取 |
| 3 | **Anthropic 路径带 `/v1`** | 按 `/anthropic/messages` 打，全 404 | `/anthropic/v1/messages` |
| 4 | **`provider=openai` 只有 responses** | chat 入口转一手协议去猜，静默丢工具参数 | chat 入口对这类模型直接 400 + `code=wrong_endpoint` |
| 5 | **`pa/` 前缀模型对免费号不可用** | 502 `upstream authentication failed`，看起来像网关 bug | 不列进目录；真要调时把 502 原文透给用户 |

---

## 9. 接入清单（本平台实际改的文件）

```
新增  admin/platform/krowork/{__init__,gateway,platform_gateway}.py
      admin/platform/krowork/core/{endpoints,client,token}.py
      admin/platform/krowork/services/accounts.py
      admin/platform/krowork/routers/accounts.py
改    admin/platform/loader.py              BUILTIN 加一行
改    admin/server.py                      include_router 加一段
```

验证：

```bash
python tests/test_gateway_contract.py      # 141 PASS（krowork 全绿）
python -c "import admin.platform.krowork"
curl -s localhost:8790/api/krowork/meta | jq .token
curl -s -H "Authorization: Bearer $K" localhost:8790/v1/models | jq '.data[].id'
```

管理接口：

```
GET  /api/krowork/meta              平台元信息 + 凭证状态（不含 token）
GET  /api/krowork/accounts          号池
GET  /api/krowork/scan-local        扫描本机（不返回 token）
POST /api/krowork/import-local      从本机导入（入库前先打模型表验证）
GET  /api/krowork/token             凭证状态（来源 / 剩余秒数 / sidecar 解释器）
POST /api/krowork/token             手动写 id_token（先验证再用）
POST /api/krowork/catalog/refresh   强制拉模型表
GET  /api/krowork/probe             上游探针
```

---

## 10. 手机号授权登录（PKCE + Keycloak SMS）

这是本平台**最重要的一条路**：sidecar 只能借客户端已登好的态，客户端一登出
网关就失明（`KrowAIError: No credentials available`）。走下面这条，
**网关自己拿票**，不依赖客户端是否登录。

### 完整流程（全部实测 2026-11-19）

```
1) GET  /realms/krow/protocol/openid-connect/auth
        ?response_type=code&client_id=krow-app&state=…
        &code_challenge=<S256(verifier)>&code_challenge_method=S256
        &redirect_uri=http://127.0.0.1:17321/callback&scope=openid
   → 200 登录页（title=KroWork），下发 AUTH_SESSION_ID / KC_AUTH_SESSION_HASH / KC_RESTART
   → 页面里有 <form id="kc-form-login" action="…/login-actions/authenticate?session_code=…">

2) POST /realms/krow/sms/send-code    {"phoneNumber":"138…","countryCode":"+86","scene":"LOGIN"}
   → 200 {"success":true}  |  400 {"success":false,"errorCode":"smsTooFast"|"userNotFound"|"invalidPhoneNumber"}

3) POST /realms/krow/sms/verify       {"phoneNumber","countryCode","code","scene":"LOGIN"}
   → {"success":true}   ← 只校验证码，**不建立会话**

4) POST <form action>   （form-urlencoded，字段名逐字照抄登录页 JS）
        phoneNumber=+86138…&smsCode=123456&action=smsLogin
   → 302 Location: http://127.0.0.1:17321/callback?code=<code>&state=<state>

5) POST /realms/krow/protocol/openid-connect/token
        grant_type=authorization_code&client_id=krow-app
        &code=<code>&code_verifier=<verifier>
        &redirect_uri=http://127.0.0.1:17321/callback
   → {id_token, access_token, refresh_token, expires_in, …}
```

### 五个关键约束（踩过才知道）

| # | 约束 | 证据 |
|---|---|---|
| 1 | **`redirect_uri` 白名单只认 `127.0.0.1:17321`** | 17320 / 45123 / 8080 全 400，17321 才 200 |
| 2 | **17321 被客户端占着**（`krow-app.exe` 监听） | `Get-NetTCPConnection -LocalPort 17321` → 同一个 pid |
| 3 | **公开客户端**，不需要 `client_secret` | 假 code 换票返回 `invalid_grant: Code not valid`（不是 `invalid_client`） |
| 4 | **Device Code 流程被禁用** | `/auth/device` → `unauthorized_client … flow is disabled` |
| 5 | **`code_verifier` 长度 43~128** | 客户端里那条 Rust 断言 `len >= 43 && len <= 128` |

> 因为 1+2，网关**不能绑端口等回调**。做法：HTTP 客户端里拦 302，
> 从 `Location` 直接读 `code`（`core/webauth.py` 的 `_StopRedirect`）。

### 两种用法

**A. 全自动（短信）**

```bash
python -m admin.platform.krowork.core.webauth_cli --phone 13800138000
# 输入收到的验证码 → 自动换票 → 写 id_token.txt → 入号池
```

HTTP：

```
POST /api/krowork/auth/begin              → {auth_id, auth_url}
POST /api/krowork/auth/send-code          {"auth_id","phone"}
POST /api/krowork/auth/submit            {"auth_id","code","phone"}
GET  /api/krowork/auth/session?auth_id=…
POST /api/krowork/auth/cancel
```

**B. 手动兑换（已经用客户端登过了）**

```bash
# 1) 拿链接和 verifier
python -m admin.platform.krowork.core.webauth_cli --url-only
# 2) 浏览器打开 auth_url 登完，地址栏停在
#    http://127.0.0.1:17321/callback?code=xxxx&state=yyyy
# 3) 把整条 URL + verifier 一起换票
python -m admin.platform.krowork.core.webauth_cli --redeem "http://127.0.0.1:17321/callback?code=xxxx" --verifier <verifier>
```

对应 `POST /api/krowork/auth/redeem {"callback_url","verifier"}`。

### 短信接口的两个实测细节

* **错误是 400 + JSON**，不是 Keycloak 标准的 `error` 字段：
  `{"success":false,"errorCode":"invalidPhoneNumber","message":"Invalid phone number format"}`
  → 映射关系写在 `webauth.send_code` 的 `mapping` 里。
* **`send-code` 不需要登录会话**也能调（只校验手机号格式）——
  所以它能当「这个号有没有注册」的探针：`userNotFound` 就是没注册。

---

## 11. 积分 / 额度接口（2026-11-20 找到真路径）

**不是猜的** —— 从 web dashboard 的打包产物里扒出来的：

```
https://h4.static.yximgs.com/.../dashboard/assets/request-xghCvCNO.js
  → axios.create({ baseURL: "https://api.krowork.com/" })
  → 请求拦截器：Authorization: Bearer <同一个 Keycloak token>
credits-R7wPK0Le.js
  → 全部接口清单
```

| 用途 | 方法与路径 |
|---|---|
| **积分余额** | `GET /krow/commerce/credit/balance` |
| 积分流水 | `GET /krow/commerce/credit/getCreditStat?pageNum&pageSize` |
| 套餐清单 | `GET /krow/commerce/plan/info` |
| 我的订阅 | `GET /krow/commerce/plan/subscription` |
| 签到状态 | `GET /krow/commerce/checkin/status`（**必须带 `X-User-Timezone`**） |
| 签到领取 | `POST /krow/commerce/checkin/claim`（同上） |
| 邀请码 | `GET /krow/commerce/referral/myInfo` |

实测返回：

```json
{"status":200,"message":"成功","data":{
  "totalCredits":2961,
  "items":[{"planId":1,"creditType":"免费试用","creditTypeEn":"FREE",
            "credits":2961,"originCredits":3000,"expireTime":"2026-12-19 14:47:18"}],
  "total":1,"pageNum":0,"pageSize":10}}
```

### 三个必须知道的坑

1. **业务码在 body 里，HTTP 一律 200**。成功判断是 `body["status"] == 200`，
   不是 `resp.status_code`。看错就会把 `{"status":500,"message":"Missing
   request header 'X-User-Timezone'…"}` 当成成功数据吞下去。
2. **`checkin` 两个接口都强制要 `X-User-Timezone`**。少了它 HTTP 200 但
   `status:500`，而 `fetch_checkin_status` 若只看 HTTP 码会报「查不到签到状态」。
   取值用 `Asia/Shanghai` 即可。
3. **`totalCredits` 是可用总额，`sum(originCredits)` 才是发放总额**。
   写 `balance_remain` 用前者，`balance_total` 用后者 —— 否则界面显示的
   「已用」永远 0（免费试用发了 3000、剩 2953，已用其实是 47）。

### 积分包 → `credits_snapshot`（账号池「未采集」的修复点）

控制台账号池的「到期」列读 `accounts.credits_snapshot`，而
`admin.credits.normalize_packages()` 认的是 **WorkBuddy 的字段名**
（`CycleCapacitySize` / `CycleCapacityRemain` …）。把 KroWork 的
`credits` / `originCredits` / `expireTime` 喂进去会得到
`package_count:2` 但 `total_capacity:0` 的空快照 —— 界面就显示「未采集」。

修法：`services/accounts.credit_packages()` **直接产出已归一化结构**
（`total` / `remaining` / `used` / `expire_at`(ms) / `expired` /
`expiring_soon` / `days_left`），跳过 `normalize_packages` 那一层。
写回后界面显示：

```
每日签到  200  2026-11-27 11:25
免费试用  2953 / 3000  2026-12-19 14:47
```

---

## 12. 自动续签 / 保活（**免客户端**）

### 为什么原来续不上

`refresh_with()` 一直拿 `client_id=krow-app` 换票，但授权时 scope 只有
`openid` —— Keycloak 发的是**会话型** refresh_token，30 分钟后随 SSO 会话
一起作废，再拿它换票只会得到：

```
400 {"error":"invalid_grant","error_description":"Session doesn't have required client"}
```

实测四种情况：

| refresh_token 来源 | 换票结果 |
|---|---|
| scope 只写 `openid`（旧流程） | `invalid_grant: Session doesn't have required client` |
| scope 含 `offline_access`（新流程） | 正常换新票，**refresh 轮换**（旧的立刻作废） |
| 用 `client_id=krow-dashboard` | `Token client and authorized client don't match` |
| token-exchange / introspect | `Standard token exchange is not enabled` / `Client not allowed` |

### 修法

1. **授权入口 `SCOPE = "openid offline_access"`**（`webauth.py`）——
   实测该 client 允许（登录页无 scope 报错），拿到的是 offline 型
   refresh_token（`typ=Offline`，寿命 **7 天**，实测 `exp - iat = 604800`）。
2. **换票成功后必须写回新 refresh_token**（轮换，旧的立刻失效）。
3. **失败后 5 分钟冷却**（`refresh_blocked_until`）：会话型 token 换票必然
   失败，不冷却就是每个 `/v1` 请求打一次 Keycloak，把登录接口打出限流。

### 任务表

| 任务 | 周期 | 作用 |
|---|---|---|
| `krowork_keepalive` | **10 分钟** | 20 分钟窗口内续期；会话型 token 标注「需重新授权」 |
| `krowork_checkin` | 每日 | 签到 200 积分（实测到账） |
| `krowork_refresh_quota` | 60 分钟 | 积分写回 `balance_*` + `credits_snapshot` |
| `krowork_refresh_catalog` | 360 分钟 | 模型表 |

**全都不依赖客户端**：凭证走号池里的 refresh_token，sidecar（借客户端
3.12 环境取票）只是额外的新鲜来源。

---

## 13. 授权登录入库后「模型/额度都取不到」的根因

这是本平台最容易复发的一类 bug，根因有两层：

### 第一层：token 来源链没看数据库

`TokenStore._resolve()` 原来只有 `env → 文件 → sidecar`。
授权登录的票是写进 **`accounts` 表** 的，sidecar 只在客户端
正在运行且已登录时可用 —— 于是「账号入库、状态正常、模型和额度全空」。

修法：加一级 `from_accounts()`，**排在 sidecar 之前**：

```
env → 文件 → accounts（号池，含自动 refresh）→ sidecar → refresh
```

### 第二层：余额/积分没写回列

`/v1/balance` 和 `/v1/models` 也各自只读 `token_mod.current()`，
而卡片的 `balance_total=0` 是因为 `refresh()` 从来没写过这两列。

修法：
* `AccountOps.refresh()` 写 `balance_total` / `balance_remain` /
  `credits_snapshot` / `credits_*`；
* `/v1/balance` 与 `/v1/models` 都改成**从号池取票**（`ka.ensure_token`），
  进程级 token 只作兜底。

修完实测：

```
账号池  2217898929@qq.com | 3153 / 3153 | 每日签到 200 到期 11/27
卡片    credits 3,153（总额 3,153 · 到期 2026-11-27）
/v1/models   7 个模型
/v1/balance  balance 3153 · supported true
对话     krow-model-1 → "收到"（含工具调用 / 流式 / Anthropic / Responses 全通）
```

---

## 14. 接入清单（本平台实际改的文件）

```
新增  admin/platform/krowork/{__init__,gateway,platform_gateway}.py
      admin/platform/krowork/core/{endpoints,client,token,webauth,webauth_cli}.py
      admin/platform/krowork/services/accounts.py
      admin/platform/krowork/routers/accounts.py
      admin/console/logos/krowork-{32,64,256}.png
改    admin/platform/loader.py              BUILTIN 加一行
改    admin/server.py                      include_router 加一段
改    admin/scheduler.py                   _TASK_SCHEDULE_META 加三条
改    admin/console/platform-icons.js      图标 + ALIASES
改    admin/console/console-app.js         platformBeginAuth 加 krowork 分支
```

验证：

```bash
python tests/test_gateway_contract.py      # 141 PASS（krowork 全绿）
curl -s localhost:8790/api/krowork/diagnose | jq .stages
curl -s localhost:8790/api/krowork/overview | jq '.cards[]|{key,value,sub}'
```

管理接口（24 个）：

```
GET  /api/krowork/meta              平台元信息 + 凭证状态（不含 token）
GET  /api/krowork/accounts          号池（含余额与积分包到期）
GET  /api/krowork/diagnose          分段诊断：客户端 / 文件 / sidecar / token / 上游
POST /api/krowork/auth/begin        开始手机号授权（scope 含 offline_access）
POST /api/krowork/auth/send-code    发短信
POST /api/krowork/auth/submit       交码换票入池
POST /api/krowork/auth/submit-callback  粘贴回调地址换票（**推荐**）
POST /api/krowork/auth/redeem       手动兑换 code + verifier
GET  /api/krowork/token             凭证状态（来源 / 剩余秒数 / 是否需重新授权）
POST /api/krowork/token             手动写 id_token（先验证再用）
POST /api/krowork/catalog/refresh   强制拉模型表
GET  /api/krowork/probe             上游探针
```


