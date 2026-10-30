# 实战案例：Qoder（从拆包到三协议跑通）

这份文件是 `ONBOARDING.md` 的**完整实例**。六步流程的每一步都对应这里的
真实操作、真实输出、真实卡点。

> 为什么单独一份：`ONBOARDING.md` 讲方法，这份讲**一次完整走通长什么样**。
> 下次接新平台时，照着这份的顺序做，遇到反常现象就回来对一遍。

---

## 0. 结果先说

| 环节 | 状态 | 关键事实 |
|---|---|---|
| 定位安装 | ✅ | `D:\Qoder CN`（Launcher 128 KB 是引导器） |
| 拆包 | ✅ | `app.asar` 180 MB / 6420 文件 / 3321 个 js |
| 找接口 | ✅ | 业务在 `app.asar.unpacked` 里的 agent SDK |
| 鉴权 | ✅ | COSY 自签名（RSA + AES + MD5） |
| 模型目录 | ✅ | 10 个 chat 模型，`format` 全 `openai` |
| 对话 | ✅ | SSE，信封包一层，`reasoning_content` 分离 |
| 三协议 | ✅ | Chat Completions / Anthropic Messages / Responses |
| 签到 | ✅ | campaign 领取，实测领到 100 credits |
| 本机扫描 | ⚠️ | 能定位账号，**凭证加密读不出 token** |

---

## 1. 定位安装目录

```powershell
# 用户给的路径只有 Launcher
C:\Users\<u>\AppData\Local\Qoder CN\Qoder CN Launcher\
    Qoder CN Launcher.exe    128 KB      ← 引导器
    install.ini
```

**读 `install.ini`**：

```ini
[launcher]
installDir=D:\Qoder CN
appExecutable=Qoder CN.exe
```

真正的应用在 `D:\Qoder CN`，`Qoder CN.exe` 有 **215 MB**。

### 教训

> 128 KB 的 exe 装不下 IDE。**任何 < 1 MB 的「主程序」都是引导器**，
> 去同级或上级找 `install.ini` / `product.json` / `*.ini` 拿真实路径。

---

## 2. 拆包

### asar 结构

```
[8 字节 pickle 头][JSON 目录表][文件数据]
```

偏移 12 是 headerSize，**必须 4 字节对齐**：

```javascript
const headerSize = buf.readUInt32LE(12);
const aligned = Math.ceil(headerSize / 4) * 4;   // ← 少这一步解出乱码
const base = 8 + 8 + aligned;
```

### 结果

```
总文件 6420 | .js 3321

17410 KB  /out/main/electronMain.js              ← 主进程
14658 KB  /out/renderer/assets/Cux9x6wn.js       ← renderer
```

### ⚠️ 最大的坑：业务不在 asar 里

在 `electronMain.js`（17 MB）里搜：

```powershell
foreach ($kw in @('https://','chat/completions','v1/messages')) {
  ([regex]::Matches($c, [regex]::Escape($kw))).Count
}
```

结果：

```
https://            1        ← 几乎没有 URL
chat/completions    0
v1/messages         0
```

**这说明业务逻辑不在主进程。** 去 `app.asar.unpacked`：

```
resources/app.asar.unpacked/node_modules/@qoder-ai/
    qoder-cn-agent-sdk/dist/_worker/
        qoder-worker-runtime.obf.mjs     32.8 MB   ← 真正的 AI 运行时
```

在这里才搜到：

```
chat/completions    5
v1/messages         1
qoderapi          110
api.qoder          20
```

### 教训

> **主进程里搜不到接口，不代表没有接口。**
> Electron 应用的业务常被拆到 `app.asar.unpacked` 的独立 SDK 里
> （尤其是 AI/agent 这类频繁更新的模块，放 asar 外面便于热更）。
> 顺序应该是：`out/main` → `out/renderer` → **`app.asar.unpacked`**。

---

## 3. 找上游接口

### 3.1 追混淆代码里的 host

Qoder 的写法（压缩 + 三元）：

```javascript
Os  = "cn" == "cn"                                    // 恒 true → CN 版
kOe = Os ? "qoder.com.cn" : "qoder.sh"
ZGA = Os ? "gateway.qoder.com.cn" : "api2-v2.qoder.sh"
Cel = { prod: ZGA, daily: XGA, test: zGA }

D1i() { return process.env.QODER_MODEL_SERVER_HOST || Cel[Dw()] }
btt() { return `https://${D1i()}/model/v1/chat/completions` }
```

追法：

```powershell
# 从变量名反查赋值
[regex]::Matches($c, '\bZGA\s*=\s*[^;,}]{2,120}')
# 抓三元表达式两个分支
[regex]::Matches($c, 'Os\s*\?\s*"([^"]+)"\s*:\s*"([^"]+)"')
```

### 3.2 拿到的完整表

```
openapi      https://openapi.qoder.com.cn     （国际版 openapi.qoder.sh）
网关         https://gateway.qoder.com.cn     （国际版 api3 / api2）

/api/v1/userinfo                    用户信息
/api/v2/user/plan                   套餐
/api/v2/quota/usage                 额度（两个桶）
/api/v1/jobToken/exchange           PAT → 作业令牌
/api/v1/jobToken/refresh            作业令牌续期
/api/v1/deviceToken/poll            设备流轮询
/api/v1/deviceToken/refresh         设备流续期
/sash/api/v1/me/campaigns           活动（签到入口）
/sash/api/v1/me/campaigns/{id}/claim 领取
/algo/api/v2/model/list             模型目录
/algo/api/v2/service/pro/sse/agent_chat_generation   对话 SSE
```

### 3.3 三族接口的鉴权口径完全不同

| 族 | 路径 | 鉴权 |
|---|---|---|
| openapi | `/api/v1/*`、`/api/v2/*` | Bearer + `Cosy-Version: 1.0.1` 等基础头 |
| sash | `/sash/api/v1/me/*` | Bearer + **`cosy-clienttype: 10`**，**不打签名** |
| 推理 | `/algo/*` | **COSY 自签名** |

> `cosy-clienttype` 用 5（IDE 形态）会返回 `showCampaign:false` + 空活动列表，
> 用 10（桌面端）才有完整活动包。**这个头在门控返回内容**，不是装饰。

---

## 4. 验证连通性

### 探针法：不带凭证先打一遍

```python
for url, method in (
    ("https://gateway.qoder.com.cn/algo/api/v1/ping", "POST"),
    ("https://openapi.qoder.com.cn/api/v2/quota/usage", "GET"),
):
    ...
```

真实输出：

```
[200] /algo/api/v1/ping                       pong
[401] /api/v2/quota/usage                     TOKEN_INVALID（missing authorization token）
[401] /algo/api/v5/service/region/endpoints   TOKEN_INVALID
[404] /model/v1/chat/completions              （空）
```

### 判读

| 观察 | 结论 |
|---|---|
| `ping` 返回 `pong` | **host 正确** |
| 两个 401 | 路径存在，只缺 token |
| `/model/v1/chat/completions` 404 | 该路径需 BYOK，**不是主路径** |
| `/algo/.../agent_chat_generation` | 主路径（对话走这里） |

> **这一步省了几小时**：先确认 host 和路径，再去搞凭证。
> 如果反过来（先搞凭证再试路径），401 和 404 会混在一起，无法定位。

---

## 5. 鉴权：COSY 自签名

`/algo/*` 族**不认普通 Bearer**，是一套自签名：

```
1. 组装身份 JSON（键序固定：uid / security_oauth_token / name / aid / email）
2. 生成 16 字符一次性 AES-128 密钥，加密身份 → info（**key 同时当 IV**）
3. 同一 AES 密钥用 RSA-1024 PKCS#1 v1.5 加密 → cosy_key
4. {version,requestId,info,cosyVersion,ideVersion} → base64 = payload
5. MD5(payload \n key \n ts \n body \n sigPath) = 签名
6. Authorization: Bearer COSY.<payload>.<签名>
```

### 三个必须照抄的细节

| 细节 | 写错的表现 |
|---|---|
| 签名路径**去掉 `/algo` 前缀、不含查询串** | `{"code":"101","message":"Signature invalid"}` |
| **请求体参与签名**，且是**编码后**的字节 | 同上，看不出是顺序问题 |
| 身份 JSON **键序固定** | 密文变了，理论上仍能解密，但对拍失败后无法定位 |

### 请求体编码（`Encode=1` 时）

```
base64 → 按「尾段/中段/首段」重排（各 ⌊n/3⌋，余数留中段）→ 字母表替换（= → $）
```

### Python 实现要点

```python
# AES-128-CBC，key 同时当 IV
cipher = Cipher(algorithms.AES(key), modes.CBC(key))

# RSA 用手写模幂（cryptography 没有可用的 NoPadding）
cipher = pow(int.from_bytes(encoded, "big"), 65537, modulus)

# PKCS#1 v1.5 的 PS 必须非零
for i, b in enumerate(ps):
    if b == 0:
        ps[i] = 1
```

### 国际版按令牌前缀分流

| 前缀 | 含义 | 推理主机 |
|---|---|---|
| `dt-` | 设备流令牌 | `api3.qoder.sh` |
| `jt-` | PAT 换来的作业令牌 | `api2.qoder.sh` |

> 把 `jt-` 打到 api3 会被判 `Login expired`（403），
> 而错误信息**不会告诉你是主机选错了**。

---

## 6. 模型目录

```python
GET {gateway}/algo/api/v2/model/list      # 需要 COSY 签名
```

返回是**按场景分组**的：

```
chat 10 · developer 10 · assistant 10 · inline 9 · quest 9
qwork 9 · experts 4 · qwake 10 · app 10 · byok_teams 0 · byok_enterprise 0
```

每个模型的关键字段：

```
key / display_name / format / is_reasoning / is_vl
max_input_tokens / price_factor / is_free / is_default / enable
```

### 重要发现

**`format` 全是 `openai`** —— 所以协议转换成本极低，OpenAI 那条路近乎直通。

10 个 chat 模型：

```
auto              Auto                 reasoning=True   vl=True  factor=0.5
qmodel_38max      Qwen3.8-Max          reasoning=True   vl=True  factor=0.5
qfmodel           Qwen3.8-Flash        reasoning=True   vl=True  factor=0.0
dmodel            DeepSeek-V4-Pro      reasoning=True   vl=True  factor=0.5
dfmodel           DeepSeek-Flash       reasoning=False  vl=True  factor=0.0
gmodel            GLM-5.3              reasoning=True   vl=True  factor=0.8
gfmodel           GLM-5.3-Flash        reasoning=True   vl=True  factor=0.1
kmodel_latest     Kimi-K3.1-Pro        reasoning=False  vl=True  factor=1.0
kmodel            Kimi-K2.8-Preview    reasoning=True   vl=True  factor=0.8
mmodel            MiniMax-M3.1-Flash   reasoning=False  vl=True  factor=0.1
```

---

## 7. 对话协议

### 上游要一个「固定信封」

```json
{
  "request_id": "<uuid>", "request_set_id": "<uuid>",
  "chat_record_id": "<uuid>", "session_id": "sess-<hex>",
  "stream": true, "chat_task": "common", "is_reply": true,
  "source": 1, "version": "3", "session_type": "agent", "agent_id": "agent",
  "messages": [...], "tools": [], "parameters": {"max_tokens": 512},
  "chat_context": {"text": "<最后一条用户输入>", "extra": {
    "modelConfig": {"key": "<模型 key>", "is_reasoning": true},
    "originalContent": "<最后一条用户输入>", "context": []}},
  "model_config": {"key": "...", "display_name": "...", ...}
}
```

### 响应多一层信封

```json
{"headers": {...}, "body": "<JSON 字符串>", "statusCodeValue": 200}
```

`body` 是**字符串**，要二次 `json.loads`。

### ⚠️ 业务错误不体现在 HTTP 状态码上

HTTP 永远 200，错误在 `statusCodeValue` 里。

> **不检查信封就会把「额度耗尽」渲染成「空回复」。**
> 这也是为什么「换号重试」不能只靠 HTTP 错误触发。

### `content` 与 `reasoning_content` 分开

```
帧2-13   reasoning_content: "The user is asking..."
帧14     content: "OK"
帧15     finish_reason: "stop"
帧16     usage: {prompt_tokens: 68, completion_tokens: 37,
                total_tokens: 105, credits: 0.0477,
                completion_tokens_details: {reasoning_tokens: 33}}
```

实测一个 `OK` 的回复：思考占 33 token，正文 4 token ——
**思考内容不能拼进正文**，否则会看到一大段内心独白。

---

## 8. 权益 / 签到

### 上游是「活动（campaign）」不是「签到」

```
GET  {openapi}/sash/api/v1/me/campaigns?forceRefresh=true
  → {uid, showCampaign, claimable, campaigns:[{
       campaignId, actionType, claimStatus,
       benefit:{kind:"CREDITS", amount:100,
                validity:{mode:"RELATIVE_DAYS", days:30}}}]}

POST {openapi}/sash/api/v1/me/campaigns/{campaignId}/claim  body {}
  → {status:"CLAIMED", replayed:false, benefit:{amount:100},
     claimedAt:"...", expiresAt:"..."}
```

### 四个必须照做的细节

| 细节 | 不照做的后果 |
|---|---|
| 只领 `actionType == "CLAIM_BENEFIT"` | 促销活动是 `VIEW_DETAILS`，领不了 |
| 只领 `claimStatus == "CLAIMABLE"` | 领完仍是 `CLAIMED`，重复领报错 |
| **顶层 `claimable` 不能当判据** | 领完之后它仍是 `true` |
| `cosy-clienttype: 10` | 用 5 返回空活动列表 |

### 额度落在哪个桶

```
领取前: userQuota {total:300, remaining:300}
        addOnQuota  （上游不返回该键）

领取后: userQuota {total:300, remaining:300}     ← 不变
        addOnQuota {total:100, remaining:100}    ← ★ 签到额度在这里
```

> **两个桶都要算**。只看 `userQuota` 会以为签到没生效。

### 不要碰 legacy

`GET /sash/api/v1/me/daily-check-in/status` 两个地区都已停用
（国际版 404、中国版恒 `DISABLED`）。它的 claim 兄弟端点对未领取的日子
也回 409 且不发积分。**一次都不碰。**

---

## 9. 本机扫描（这一步失败是上游的限制）

### 数据在哪

```
%APPDATA%\com.qodercn.app.stable      ← 客户端（★ 不是 "Qoder CN"）
~/.qoder-cn                            ← CLI
    .models/<uid>/catalog-v7           ← 模型目录（QMC 容器）
    .auth/machine_id
    .qoder-app-status.json
```

### ⚠️ 产品名 ≠ 数据目录名

我一开始找 `%APPDATA%\Qoder CN`，**全部落空**。真实位置是
`com.qodercn.app.stable`（Electron 的 appId 形态）。

**唯一可靠的方式**：从运行中的进程读命令行：

```powershell
Get-CimInstance Win32_Process -Filter "Name='Qoder CN.exe'" |
  Select-Object -ExpandProperty CommandLine
```

```
--user-data-dir="C:\Users\<u>\AppData\Roaming\com.qodercn.app.stable"
```

### 凭证读不出明文

```
auth.v1.dat    547 字节    开头 "v10" 版本标记
```

* 解密实现在**原生模块**里（JS bundle 搜不到 `auth.v1` / `safeStorage` / `keytar` 的实现）
* 进程内存扫描只抓到一个**过期的、别的系统的** JWT（`client_id=saber`）
* `.qoder-cn/.auth/` 里只有 `machine_id` 和空的事务标记

### 结论：扫描能给出什么、给不出什么

| 能给 | 给不出 |
|---|---|
| 本机登录过哪个区（`qoder:prod:cn:<hash>`） | token |
| 有哪些 uid（从 `catalog-v7` 目录名 + `default` 文件） | refresh_token |
| 客户端版本 / 机器 id | — |
| 凭证文件是否存在（`auth.v1.dat` 547 字节） | — |

所以 `import_local` **明确返回 `ok: false` + `reason: "credential_encrypted"`**，
不假装导入成功。要拿到可用 token，需要 PAT 或设备流授权。

### 扫描必须脱敏

```python
def mask(value, keep=4):
    return value[:keep] + "*" * min(8, len(value) - keep*2) + value[-keep:]
```

扫描结果会进日志和界面，**绝不能带出完整 uid / email / token**。

---

## 10. 落地：写平台模块时踩的坑

### 坑 1：`isinstance(gw, Gateway)` 判 False，但方法全在

```
[FAIL] qoder 满足 Gateway 协议
```

原因：`Gateway` Protocol 有 **`platform_id: str` 属性**。
方法齐了但缺这个属性，`isinstance` 就是 False，
而错误信息只写「不满足协议」，看不出是缺属性。

```python
class QoderGateway:
    platform_id = "qoder"      # ← 缺了它契约检查失败
```

### 坑 2：Protocol 的方法签名要**和实现完全一致**

```python
# Protocol
def chat_completions(self, request: Any, body: dict) -> Any: ...

# ❌ 有默认值 → isinstance False
def chat_completions(self, request, body=None): ...

# ✅ 必须一致
def chat_completions(self, request, body): ...
```

### 坑 3：`dispatch` 必须是 `async def`

`PlatformDispatchMiddleware` 的调用是 `await dispatch(request, path)`。

```
502 {"error":{"message":"object JSONResponse can't be used in 'await' expression"}}
```

改成 `async def`，内部 handler 也全部 `async`，请求体用 `await request.json()`。

### 坑 4：新平台按字典序抢走了默认路由

加完 `qoder` 后，测试报：

```
[FAIL] 默认平台仍由原网关鉴权   →  返回了 Qoder 的模型列表
```

原因：`priority` 策略下，无显式优先级时按 `(100, platform_id)` 排序，
`qoder` 字母序在 `workbuddy` 前面，于是**所有不带 `X-Platform` 的请求都被路由到 Qoder**。

修法：排序键加一层「是否默认平台」：

```python
return (priority.get(pid, 100),
        0 if pid == DEFAULT_PLATFORM else 1,   # ← 默认平台优先
        pid)
```

> 这是一个**真实的架构缺陷**：新增平台不该改变已有请求的去向。
> 任何「按名字排序」的默认选择器都有这个风险。

---

## 11. 三协议验证（最终结果）

```
=== /v1/chat/completions（非流式）===
  200 | content: CHAT_OK | usage: {prompt: 67, completion: 28, total: 95}

=== /v1/chat/completions（流式）===
  200 | 帧数 23 | 文本 "1 2 3" | usage: {prompt: 68, completion: 64, total: 132}

=== /v1/messages（Anthropic 非流式）===
  200 | type: message | stop: end_turn | text: MSG_OK
      | usage: {input_tokens: 66, output_tokens: 28}

=== /v1/messages（Anthropic 流式）===
  200 | 事件: message_start → content_block_start → content_block_delta×n
      → content_block_stop → message_delta → message_stop

=== /v1/responses ===
  200 | object: response | text: RESP_OK | usage: {prompt: 66, completion: 19}

=== /v1/models ===
  200 | 模型数 10
```

---

## 12. 这一次的可复用清单

写进 `ONBOARDING.md` 的新条目：

| 教训 | 属于哪一步 |
|---|---|
| < 1 MB 的「主程序」都是引导器，读 `install.ini` | ① 定位 |
| 主进程搜不到接口 → 业务在 `app.asar.unpacked` | ② 拆包 |
| asar 偏移要 4 字节对齐 | ② 拆包 |
| 先打探针（`ping`）确认 host，再搞凭证 | ④ 验证 |
| 401 = 端点存在缺凭证；404 = 路径错 | ④ 验证 |
| **产品名 ≠ 数据目录名**（`com.qodercn.app.stable`） | ⑥ 扫描 |
| 从进程命令行读 `--user-data-dir` | ⑥ 扫描 |
| 凭证可能在原生加密层，JS 搜不到就别死磕 | ⑥ 扫描 |
| Protocol 的 `platform_id` 属性容易漏 | 落地 |
| Protocol 方法签名不能有默认值差异 | 落地 |
| `dispatch` 必须 `async def` | 落地 |
| **默认平台不能按字典序被抢占** | 落地 |
| 上游业务错误可能藏在 SSE 信封里（HTTP 恒 200） | 协议 |
| `reasoning_content` 与 `content` 要分开处理 | 协议 |
| 额度可能有多个桶（套餐 / 资源包） | 权益 |

---

## 13. 一句话总结

> 接一个陌生客户端，**顺序比聪明重要**：
> 先确认 host 活着（探针），再确认路径对（401 vs 404），
> 再解决凭证，最后才写协议转换。
> 反过来做，会在「凭证」和「路径」两个未知量之间来回猜。
