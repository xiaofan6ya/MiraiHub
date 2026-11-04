# 案例：CodeArts（华为云 CodeArts Doer）

> 第 7 个平台，也是**鉴权最不像其它平台**的一个：既不是 Bearer token，
> 也不是明文会话，而是**华为云 AK/SK 请求签名 + STS 临时凭证**。
> 这份文档记录「怎么拆出来的」和「哪几个地方只回一句 `signature fail` 就没了」，
> 下一个接华为云系产品的平台可以直接照着做。

---

## 0. 一句话结论

```
客户端登录（OAuth2 + PKCE + DPoP）
  → STS 发临时 AK/SK/security_token（约 6 小时）
  → 之后每个业务请求都要现算 SDK-HMAC-SHA256 签名
  → 凭证加密存在 %APPDATA%/codearts/secretStorage/<sha256>.enc
```

网关侧要做的就是这三件事：**解出凭证**、**会签名**、**会续期**。

---

## 1. 安装布局

> 安装目录名固定为 `CodeArts`，但**盘符和用户新建的父文件夹是动态的**
> （本机实测在 `D:/CodeArts`，别处可能是 `E:\AI\CodeArts`）。网关侧
> `core/paths.py` 只存目录名，由 `admin.platform.install_paths` 定位。

```
<任意盘>/<任意父目录>/CodeArts/
  CodeArts.exe                       Electron 主程序
  resources/app.asar                 297 MB（主进程在 out/main/index.js，944 KB）
  resources/build/icon.png           512×512 RGBA —— 控制台图标取自这里
  resources/bin/agent-kernel.exe     122 MB Rust 内核（推理调用在它里面）
  resources/product.json             所有地址/客户端 id 都在这里
%APPDATA%/codearts/                 userData
  Local State                        os_crypt.encrypted_key（DPAPI 保护）
  secretStorage/<sha256(key)>.enc    authSession:cn-north-4
  app-settings.json                  客户端设置
```

**判据**：`resources/product.json` 里同时有 `auth.iamStsHost` 与
`kernel.envConfig.inferhubBaseUrl`，就说明走的是「IAM 换临时凭证 → 签名调 API」。

---

## 2. 凭证存在哪（这一节最能省时间）

客户端有一个 `FileSecretStorageService`（`out/main/index.js:1843`）：

```js
this.storageDir = path.join(electron.app.getPath("userData"), "secretStorage");
// 文件名 = sha256(key).hex + ".enc"
// 内容 = base64(electron.safeStorage.encryptString(value))
```

而 Electron 在 Windows 上的 `safeStorage` 就是 **Chromium OSCrypt**：

```
v10 | nonce(12 字节) | AES-256-GCM 密文（末尾 16 字节是 tag）
 ↑ 3 字节
```

AES 主密钥来自 `Local State` 的 `os_crypt.encrypted_key`（base64），
去掉前 5 字节 `DPAPI` 后再过一次 `CryptUnprotectData` 得到 32 字节。

Python 侧就两步（`core/session.py`）：DPAPI 解密钥 → AESGCM 解文件。
**注意**：`v10` 前缀必须去掉、nonce 是 12 字节，少一个字节就解不出来，
而且错误只是 `InvalidTag`，不会告诉你哪里错了。

解出来的结构：

```json
{"session": {"accessKey","secretKey","securityToken","refreshToken",
             "expiresAt"(毫秒),"userId","userName","domainId", ...},
 "loginContext": {"pkcePair": {"codeVerifier": "..."},
                  "dpopKeyPair": {"privateKey": "{JWK}", "publicKey": "{JWK}"}}}
```

`loginContext` 是**续期的钥匙**：华为云 STS 刷新要求 `DPoP` 头 + `code_verifier`。
两者都随登录态落盘，所以我们能独立续期（不像 Loomy 只能重登）。

---

## 3. 端点表（含踩过的 404）

| 用途 | 路径 | 备注 |
|---|---|---|
| 对话 | `POST /api/v2/chat/completions` | 与 OpenAI 完全同构，`stream: true` 是真 SSE |
| 模型目录 | `GET /v1/model/builtin` | **必须带 `Agent-Type: PromptCenter`** |
| 额度 | `GET /snap-manager/v1/statistics/plugin` | 积分包明细 |
| 身份 | `GET /v5/caller-identity` | 探活（签名通了就说明凭证活着） |
| 令牌 | `POST https://sts.cn-north-4.myhuaweicloud.com/v1/oauth2/tokens` | 刷新/换票 |

**404 陷阱**：`/api/v2/models` 看起来最对称，实测返回

```json
{"error_msg":"The API does not exist or has not been published in the environment",
 "error_code":"APIG.0101"}
```

不要根据「路径看起来对」去猜 —— 这是这类平台最常见的空转。

**400 陷阱**：`/v1/model/builtin` 少 `Agent-Type` 时回
`{"error_code":"TM.00001001","error_msg":"请求头Agent-Type为空"}` ——
它不会说「缺哪个头」，只会说「为空」。

---

## 4. 签名：两个只能靠逐字节对比才能发现的坑

算法本身是标准的华为云签名：

```
CanonicalRequest = METHOD \n CanonicalURI \n CanonicalQuery \n
                   CanonicalHeaders \n SignedHeaders \n sha256(body)
StringToSign     = "SDK-HMAC-SHA256" \n X-Sdk-Date \n sha256(CanonicalRequest)
Signature        = HMAC-SHA256(secretKey, StringToSign)
```

**坑 1：规范 URI 要补尾斜杠。**
客户端是 `path.split('/').map(encodeURIComponent).join('/')` 再补 `/`，
所以 `/api/v2/chat/completions` 的规范 URI 是 `/api/v2/chat/completions/`。

**坑 2：拼接路径段必须用 `/`。**
写成 `''.join(segments)` 会得到 `apiv2chatcompletions/`，
签名必错，服务端仍然只回 `verify ak sk signature fail`。

这两条都不会有更具体的提示。**唯一可靠的排查方式是**：
把「规范请求串」整个打印出来，和客户端 JS 里同一份输入的结果逐字节 diff
（`core/signer.py` 的 `canonical_request()` 就是为此存在的，
`tests/test_codearts_platform.py::test_canonical_request_matches_client_algorithm`
把期望串固化了）。

---

## 5. 续期

```python
DPoP 头 = ES256 JWT，protected header 里内嵌公钥 JWK
          payload = {htm, htu, iat, jti}
POST sts/v1/oauth2/tokens
     Content-Type: application/x-www-form-urlencoded
     DPoP: <上面那个 JWT>
     client_id=codearts & grant_type=refresh_token
     & refresh_token=... & code_verifier=...
```

**坑 3：ES256 签名是 `r||s` 各 32 字节裸拼，不是 DER。**
`cryptography` 的 `sign()` 返回 DER，直接 base64 会得到「签名格式非法」。
必须 `decode_dss_signature` 拆开再拼。

**坑 4：响应字段名是 `access_key_id` / `secret_access_key` / `expiration`（ISO）。**
写成 `access_key` / `secret_key` / `expires_in` 会「续期成功但 AK 为空」，
下一次调用就 `signature fail`（看起来像「续期没生效」）。

---

## 6. 额度口径

```json
{"metrics": [
   {"name": "usageTotalPackageCredit",
    "package_credit_amount": 4600.0,
    "package_credit_used": 0.0,
    "package_credit_remain": 4600.0,
    "package_credit_expiring_amount": 100.0},
   {"name": "usageBasicPackageCredit",  "package_credit_remain": 500.0},
   {"name": "usageBonusPackageCredit",  "package_credit_remain": 4100.0}],
 "package": {"package_name_cn": "体验版", "spec_code": "codearts.agent.individual.trial"},
 "start_date": "2026-11-04", "end_date": "2026-12-05"}
```

* `usageTotalPackageCredit.package_credit_remain` = **可用积分**（含所有分包）
* 分包（basic / bonus / ondemand）留着给概览卡片做拆分展示
* `expiring` 是「即将过期」那部分，映射到 `credits_expiring_soon`

额度是**买断/赠送的积分包**，不像 Loomy 的当日额度每天重置 ——
所以 0 积分的号冷却 30 分钟即可，不必频繁重试。

---

## 7. 实现地图

```
admin/platform/codearts/
  core/signer.py          华为云签名（含 canonical_request 便于 diff）
  core/client.py          对话 / 模型 / 额度 / 身份 + 错误分类
  core/session.py         OSCrypt 解密 + DPoP 续期
  core/paths.py           安装与数据目录
  services/accounts.py    凭证、额度、导入导出、视图
  services/scan.py        本机扫描（不含 AK/SK）
  platform_gateway.py     11 项 AccountOps + ClientProbe + 概览卡片
  gateway.py              /v1/* 转发（真流式）
  routers/accounts.py     /api/codearts/*
```

注册只需两处：`admin/platform/loader.py` 的 `BUILTIN` 一行 +
`admin/server.py` 挂一次路由。控制台图标在
`admin/console/platform-icons.js` 的 `RASTER` 里登记。

---

## 8. 验证

```bash
# 1) 本机扫描（不解密给前端，只回 uid/到期）
curl -b cookie http://127.0.0.1:8790/api/codearts/scan-local

# 2) 导入（服务端读同一份登录态，顺带拉一次额度 + 模型数）
curl -b cookie -X POST http://127.0.0.1:8790/api/codearts/import-local

# 3) 分段健康检查（凭证 → 签名 → 模型 → 额度）
curl -b cookie -X POST http://127.0.0.1:8790/api/codearts/accounts/1/test

# 4) 对话（后缀路由 / 显式头都行）
curl -H "Authorization: Bearer sk-..." -H "Content-Type: application/json" \
     -d '{"model":"deepseek-v4.1-flash-codearts","messages":[{"role":"user","content":"hi"}]}' \
     http://127.0.0.1:8790/v1/chat/completions
```

判据：

- [ ] `/api/codearts/accounts` < 200ms（列表只读快照）
- [ ] `/v1/models` 返回 13 个模型，且 `context_window` 非 0
- [ ] 流式 `ttfb < latency`（`usage_logs` 里两列不相等）
- [ ] `usage_logs.platform='codearts'` 有记录，`account_id` 正确
- [ ] 把 `expires_at` 改成 1 分钟前，调一次刷新能自动续期成功
- [ ] 把 `secret_key` 改坏，错误是 `auth/signature_invalid` 且**可重试**（会换号）

---

## 9. 实测数据（2026-11-04，单账号 `hid_hqng...`）

### 9.1 速度（流式单发，`deepseek-v4.1-flash` 等，300 字左右的任务）

| 模型 | TTFT | 解码速度 | 样本 |
|---|---|---|---|
| `deepseek-v4.1-flash` | 3.45s | **146.8 t/s** | 416 tok / 2.8s |
| `glm-5.3-flash` | 1.99s | 51.2 t/s | 800 tok / 15.6s（撞 max_tokens 上限） |
| `kimi-k3` | 5.47s | 55.6 t/s | 800 tok / 14.4s（同上） |

口径：TTFT = 首个**内容帧**（含 `reasoning_content`）；t/s = `completion_tokens /
(total - ttft)`。拿总耗时当分母会把长思考模型算成「极慢」，这是老坑。

### 9.2 并发：直连 vs 经网关

| 场景 | 结果 |
|---|---|
| 直连上游 4 路并发 | **3/4** 成功，1 路 `400 TM.00001041`（第 4 个会话被拒） |
| 经网关 5 路并发 | 2 路立即成功，2 路排队 ~60s 后成功，1 路 **429**（等满 60s 仍无位） |

关键：**没有任何一路吃上游 400**。排队发生在网关内，错误信息也从
「没有可用账号」变成了 `429 session_window_full` + `Retry-After: 30`。

### 9.3 会话窗口的真实语义（15 个端点 + 时序实测）

* 上限 **3 个会话 / 账号**；只有**流式**建会话（非流式连发 6 次全 200）；
* 滞留 ~53~55 秒，**从会话空闲开始算**（流跑 5 秒 → 占位 60 秒）；
* **不是滑动窗口**（固定 TTL，到点即释放，可滚动使用）；
* **没有关闭接口**：`DELETE|POST /api/v2/{chat/,}{session,s}s[/<id>][/close]`、
  `/chat/close`、`GET /api/v2/sessions` 等 15 种写法全 404；
* 会话**不可复用**：把 chunk id 从 `session_id`/`conversation_id`/
  `previous_response_id`/`X-Session-Id` 回传，上游照样每次新建。

=> 单账号流式吞吐上限 ≈ `limit / (ttl + 平均流时长)` ≈ **2 请求/分钟出头**。
要更高并发只有加号（换号 = 换窗口）。这也解释了为什么网关要做
「按账号排队」而不是「换号重试」——单号池里换号没用。

### 9.4 思考档位（`reasoning_effort`）

```
非法值 insane -> 400 parameter check failed,
                 reasoning_effort must be none or low or medium or high or xhigh or max
```

* 枚举里**确实有 `xhigh` / `max`**，上游会解析（非法值直接 400）；
* 效果（同题 3 次，reasoning_tokens 中位数）：`max` **691** > `high` 272 >
  `xhigh` 189 —— `xhigh` 并不比 `high` 强（采样噪声），**要加力就用 `max`**；
* `reasoning_effort: none` **不等于关思考**（中位数 346，照样思考）；
  真正关闭要发 `thinking: {"type": "disabled"}`（reasoning_tokens 直接为 None）；
* `thinking.budget_tokens` 被接受但不体现为档位差异；
* `enable_thinking: true` 有效（与默认档接近）。

> 记账口径：日志记**客户端原始意图**（`xhigh` 就记 `xhigh`），
> 上游实际收到什么由各平台适配层决定；两者不一致时以日志为准排查。


---

## 10. 请求体契约（2026-11-04）：别因为「OpenAI 同构」就照抄

上游响应确实与 OpenAI 同构，但下面两件事仍是 **CodeArts 私有**，只能在本平台网关里维护：

| 项 | 实测行为 | 代码落点 |
|---|---|---|
| role 白名单 | 只认 `system/user/assistant/tool`；`developer` → **HTTP 200 的 SSE 错误帧** `error_code=InferHub.001001044.400 / Message role cannot empty`（无 choices、无 finish_reason） | `core/client.py::normalize_body()` |
| 错误帧形式 | `{"text":"[DONE]","error_code":…,"error_msg":…}`，不是 `{"error":{...}}`；不识别就会透传成「流无 finish_reason」，客户端无限重试 | `core/client.py::_inband_error()` + `gateway.py::_terminal_frame()` |

**不要把 WorkBuddy 的 `11128 / x-anthropic-billing-header` 净化词表搬过来**：
那是另一个上游的渠道校验规则，与本平台无关（反之亦然）。完整规范、
两条入口链路的同步要求与验证脚本见
[REQUEST-BODY-CONTRACT.md](REQUEST-BODY-CONTRACT.md)；本次故障记在
[FIXED-BUGS 五·补51](FIXED-BUGS.md)。
